"""Server-side run store.

An upload produces a `run`: the validated df, the v3 output frame, and the
enriched H frame (with ECOD/SHAP/reason). Browsers only receive JSON
projections; the frames stay server-side.

Eviction: sliding 30-minute TTL, MAX_RUNS LRU cap.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.fdi import config

LEVEL_PRIORITY = {"ATTAQUE": 0, "SUSPECT": 1, "normal": 2, "historique insuffisant": 3}
MAX_CANDIDATES = 120


def _finite(v) -> float | None:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if np.isfinite(n) else None


def _round(v, digits: int = 6) -> float | None:
    return None if v is None else round(v, digits)


@dataclass
class Run:
    run_id: str
    created_at: float
    source: str
    filename: str | None
    df: pd.DataFrame
    out: pd.DataFrame
    H: pd.DataFrame
    model_info: dict[str, Any]
    dataset: dict[str, Any]
    summary: dict[str, Any]
    points: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    envelope: dict[str, float]
    attacks: dict[str, tuple[str, str]] = field(default_factory=dict)
    last_used: float = 0.0
    caches: dict[tuple, Any] = field(default_factory=dict)

    @property
    def twin_features(self) -> list[str]:
        return list(self.model_info.get("twin_features") or [])

    def envelope_for(self, station: str) -> float:
        return float(self.envelope.get(station, 0.01))


_runs: "OrderedDict[str, Run]" = OrderedDict()
_lock = threading.Lock()


def _prune_locked(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [
        run_id for run_id, run in _runs.items()
        if now - (run.last_used or run.created_at) > config.RUN_TTL_SECONDS
    ]
    for run_id in expired:
        del _runs[run_id]
    while len(_runs) > config.MAX_RUNS:
        _runs.popitem(last=False)


def get_run(run_id: str | None) -> Run | None:
    if not run_id:
        return None
    with _lock:
        _prune_locked()
        run = _runs.get(run_id)
        if run is None:
            return None
        run.last_used = time.time()
        _runs.move_to_end(run_id)
        return run


def touch(run: Run) -> None:
    with _lock:
        run.last_used = time.time()
        _runs.move_to_end(run.run_id)


def latest_run() -> Run | None:
    with _lock:
        _prune_locked()
        if not _runs:
            return None
        run = _runs[next(reversed(_runs))]
        run.last_used = time.time()
        return run


def put_run(run: Run) -> Run:
    with _lock:
        _prune_locked()
        run.last_used = run.last_used or time.time()
        _runs[run.run_id] = run
        _runs.move_to_end(run.run_id)
    return run


def create_run(
    df: pd.DataFrame,
    enriched: pd.DataFrame,
    *,
    source: str,
    filename: str | None = None,
    renamed: dict[str, str] | None = None,
    attacks: dict[str, tuple[str, str]] | None = None,
    threshold: float = 0.5,
    twin_features: list[str] | None = None,
) -> Run:
    """Build the run from the enriched frame (output of `xai_engine.enrich`).

    `enriched` carries proba, alerte, type_pred/type_conf, ecod_*, shap_*,
    reason, residual_tu, exp5_tu, sm5, etc. This function splits it into
    `out` (verdict table) and `H` (features + XAI columns), computes the
    per-station envelope, and registers the run.
    """
    enriched = enriched.reset_index(drop=True)

    out = pd.DataFrame({
        "station":   enriched["station"].astype(str),
        "time":      enriched["time"],
        "score":     enriched["proba"].astype(float) if "proba" in enriched else np.nan,
        "proba":     enriched["proba"].astype(float) if "proba" in enriched else np.nan,
        "alerte":    enriched["alerte"].astype(bool) if "alerte" in enriched else False,
        "type_pred": enriched["type_pred"] if "type_pred" in enriched else None,
        "type_conf": enriched["type_conf"].astype(float) if "type_conf" in enriched else np.nan,
        "reason":    enriched["reason"] if "reason" in enriched else None,
    })
    H = enriched
    # Preserve enrich()'s attrs (m1_base, ecod_ref_summary, ...) on H.
    H.attrs.update(enriched.attrs)

    out["niveau"] = np.where(
        out["alerte"], "ATTAQUE",
        np.where(out["proba"].fillna(0) >= threshold * 0.75, "SUSPECT", "normal"),
    )
    out.loc[out["proba"].isna(), "niveau"] = "historique insuffisant"

    envelope: dict[str, float] = {}
    for st, grp in enriched.groupby("station"):
        residual = (
            grp["residual_tu"].dropna().to_numpy(dtype=float)
            if "residual_tu" in grp else np.array([])
        )
        if residual.size == 0:
            envelope[str(st)] = 0.01
            continue
        med = float(np.median(residual))
        mad = float(np.median(np.abs(residual - med)))
        sigma = 1.4826 * mad
        if not np.isfinite(sigma) or sigma <= 0:
            sigma = float(np.std(residual)) or 0.01
        envelope[str(st)] = round(config.ENVELOPE_MAD_K * sigma, 6)

    model_info = {
        "ready": True,
        "framework": "lightgbm (joblib)",
        "repo_id": config.HF_REPO_ID,
        "filename": config.HF_DETECTOR_FILENAME,
        "stations": sorted(out["station"].unique().tolist()),
        "twin_features": list(twin_features or []),
        "inputs": list(df.columns),
        "threshold": float(threshold),
        "suspect_threshold": float(threshold * 0.75),
        "min_history_days": 18,
    }
    dataset = {
        "rows": int(len(out)),
        "scored": int(out["proba"].notna().sum()),
        "insufficient_history": int((out["niveau"] == "historique insuffisant").sum()),
        "stations": sorted(out["station"].unique().tolist()),
        "start": _iso(out["time"].min()),
        "end": _iso(out["time"].max()),
        "renamed_columns": dict(renamed or {}),
        "source": source,
        "filename": filename,
    }

    finite_scores = out["proba"].dropna().to_numpy(dtype=float)
    summary = {
        "hours": int(len(out)),
        "attaque": int((out["niveau"] == "ATTAQUE").sum()),
        "suspect": int((out["niveau"] == "SUSPECT").sum()),
        "normal":  int((out["niveau"] == "normal").sum()),
        "insufficient_history": dataset["insufficient_history"],
        "alerts": int(out["alerte"].sum()),
        "max_score": _round(float(finite_scores.max()) if finite_scores.size else None),
        "mean_score": _round(float(finite_scores.mean()) if finite_scores.size else None),
        "threshold": float(threshold),
    }

    points = [
        {
            "i": int(i),
            "t": _iso(out.at[i, "time"]),
            "station": str(out.at[i, "station"]),
            "score": _round(_finite(out.at[i, "score"])),
            "level": str(out.at[i, "niveau"]),
            "alert": int(bool(out.at[i, "alerte"])),
            "sm5": _round(_finite(H.at[i, "sm5"])) if "sm5" in H.columns else None,
        }
        for i in out.index
    ]

    ranked = out.assign(_p=out["niveau"].map(LEVEL_PRIORITY).fillna(4)).sort_values(
        ["_p", "score"], ascending=[True, False], na_position="last",
    )
    candidates = [
        {
            "i": int(i),
            "t": _iso(out.at[i, "time"]),
            "station": str(out.at[i, "station"]),
            "score": _round(_finite(out.at[i, "score"])),
            "level": str(out.at[i, "niveau"]),
            "rule": None,
        }
        for i in ranked.index
        if out.at[i, "niveau"] != "historique insuffisant"
    ][:MAX_CANDIDATES]

    return put_run(Run(
        run_id=uuid.uuid4().hex,
        created_at=time.time(),
        source=source,
        filename=filename,
        df=df.reset_index(drop=True),
        out=out,
        H=H,
        model_info=model_info,
        dataset=dataset,
        summary=summary,
        points=points,
        candidates=candidates,
        envelope=envelope,
        attacks=attacks or {},
    ))


def _iso(value) -> str | None:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def cache_get(run: Run, key: tuple):
    with _lock:
        return run.caches.get(key)


def cache_put(run: Run, key: tuple, value) -> None:
    with _lock:
        run.caches[key] = value


def target_for(run: Run, row: int) -> dict[str, Any]:
    out, H = run.out, run.H
    if row not in out.index:
        raise KeyError(row)
    actual = _finite(H.at[row, "sm5"]) if "sm5" in H.columns else None
    residual = _finite(H.at[row, "residual_tu"]) if "residual_tu" in H.columns else None
    expected = None if actual is None or residual is None else _round(actual - residual)
    niveau = str(out.at[row, "niveau"])
    reason = str(out.at[row, "reason"]) if "reason" in out and out.at[row, "reason"] is not None else ""
    return {
        "actual": _round(actual),
        "expected": expected,
        "error": _round(residual),
        "score": _round(_finite(out.at[row, "score"])),
        "level": niveau,
        "alert": int(bool(out.at[row, "alerte"])),
        "rule": None,
        "reason": reason or None,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()