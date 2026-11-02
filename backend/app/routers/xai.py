"""The /api/xai endpoints — payload builders only.

All heavy lifting (ECOD, SHAP, percentiles, reference building) happens in
`xai_engine.enrich` and is cached in the Run's `H` frame. This module
projects those columns onto the frozen `XaiResponse` contract.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from app.authentification.dependencies import require_user
from app.authentification.models import UserAccount
from app.fdi.attack_classifier import classify_attack
from app.fdi.feature_labels import ECOD_LABELS
from app.fdi.schemas import AttackClassificationResponse, XaiResponse, XaiSeries
from app.fdi.store import Run, cache_get, cache_put, get_run
from app.fdi.xai_engine import ECOD_FEATURES, ECOD_META

router = APIRouter(prefix="/xai", tags=["fdi-xai"])

FEATURE_VERSION = "v3-lightgbm+ecod-upload-ref"
TEMPORAL_RADIUS_H = 12
WINDOW_HOURS = 24


def _finite(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _iso_z(ts) -> str:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _parse_hour(hour_iso: str) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(hour_iso.strip())
    except Exception as exc:
        raise HTTPException(422, f"hour_iso is not parseable: {hour_iso!r}") from exc
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _require_run(run_id: str) -> Run:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(
            404,
            "Unknown or expired run_id. Uploads live 30 minutes — re-upload.",
        )
    return run


def _row_for(run: Run, station: str, ts: pd.Timestamp) -> int:
    out = run.out
    if station not in set(out["station"]):
        raise HTTPException(
            404, f"station {station!r} has no hours in this run "
                 f"(stations: {sorted(set(out['station']))}).",
        )
    masked = out.index[(out["station"] == station) & (out["time"] == ts)]
    if masked.empty:
        hits = out.index[out["station"] == station]
        rng = f"{_iso_z(out.at[hits.min(), 'time'])} .. {_iso_z(out.at[hits.max(), 'time'])}"
        raise HTTPException(404, f"No hour {_iso_z(ts)} for {station} (range: {rng}).")
    return int(masked[0])


# --- panel builders ------------------------------------------------------

def _twin_panel(run: Run, row: int) -> dict:
    """SHAP on M1 — kept under the schema key 'twin_sm5' for backward compat."""
    h = run.H
    shap_cols = [c for c in h.columns if c.startswith("shap_")]
    base = float(h.attrs.get("m1_base", 0.0))
    if not shap_cols:
        return {"target": "twin_sm5", "base_value": base, "features": []}

    shap_vals: dict[str, float] = {}
    for c in shap_cols:
        v = _finite(h.at[row, c])
        if v is not None:
            shap_vals[c[5:]] = v
    order = sorted(shap_vals.items(), key=lambda kv: -abs(kv[1]))
    features = [
        {
            "key": k,
            "label": k,
            "value": _finite(h.at[row, k]) if k in h.columns else None,
            "shap": float(v),
            "contribution_sign_ok": True,
            "rank": i + 1,
        }
        for i, (k, v) in enumerate(order)
    ]
    return {"target": "twin_sm5", "base_value": base, "features": features}


def _ecod_panel(run: Run, row: int, station: str) -> dict:
    h = run.H
    if "ecod_score" not in h.columns:
        return {"score": None, "threshold": 1.0, "suspect": False,
                "features": [], "families": []}
    score = _finite(h.at[row, "ecod_score"])
    if score is None:
        return {"score": None, "threshold": 1.0, "suspect": False,
                "features": [], "families": []}

    contribs = []
    for f in ECOD_FEATURES:
        col = f"ecod_contrib_{f}"
        if col not in h.columns:
            continue
        v = _finite(h.at[row, col])
        if v is None:
            continue
        raw = _finite(h.at[row, f]) if f in h.columns else None
        contribs.append({
            "key": f,
            "label": ECOD_LABELS.get(f, f),
            "family": ECOD_META[f]["family"],
            "value": raw,
            "tail": "upper",
            "p": float(np.exp(-v)),
            "contribution": float(v),
            "signed_contribution": float(v),
            "rank": 0,
        })
    contribs.sort(key=lambda c: -c["contribution"])
    for i, c in enumerate(contribs, start=1):
        c["rank"] = i

    total = sum(c["contribution"] for c in contribs) or 1.0
    fam_agg: dict[str, dict] = {}
    for c in contribs:
        a = fam_agg.setdefault(
            c["family"],
            {"family": c["family"], "signed_contribution": 0.0,
             "contribution_sum": 0.0, "share_pct": 0.0},
        )
        a["signed_contribution"] += c["signed_contribution"]
        a["contribution_sum"] += c["contribution"]
    families = sorted(fam_agg.values(), key=lambda a: (-a["contribution_sum"], a["family"]))
    for a in families:
        a["share_pct"] = 100.0 * a["contribution_sum"] / total

    return {
        "score": float(score),
        "threshold": 1.0,
        "suspect": False,
        "features": contribs,
        "families": families,
    }


def _temporal_panel(run: Run, station: str, ts: pd.Timestamp) -> dict:
    h = run.H
    sub = h.loc[h["station"] == station, ["time", "sm5", "residual_tu"]]
    sub = sub.set_index("time")[["sm5", "residual_tu"]].sort_index()
    rng = pd.date_range(
        ts - pd.Timedelta(hours=TEMPORAL_RADIUS_H),
        periods=2 * TEMPORAL_RADIUS_H + 1, freq="h",
    )
    table = sub.reindex(sub.index.union(pd.DatetimeIndex(rng))).reindex(rng)
    points = []
    for t, row_values in table.iterrows():
        observed = _finite(row_values["sm5"])
        residual = _finite(row_values["residual_tu"])
        expected = None if observed is None or residual is None else observed - residual
        points.append({"t": _iso_z(t), "expected": expected, "observed": observed})
    return {"window_hours": WINDOW_HOURS, "points": points}


def _ground_truth(run: Run, ts: pd.Timestamp) -> str | None:
    if run.source != "demo":
        return None
    for _key, (start, end) in (run.attacks or {}).items():
        if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
            return "attack"
    return "normal"


def _niveau_for(row: pd.Series, threshold: float) -> str:
    if bool(row.get("alerte")):
        return "ATTAQUE"
    proba = _finite(row.get("proba"))
    if proba is not None and proba >= threshold * 0.75:
        return "SUSPECT"
    return "normal"


def _rule_key_for(row: pd.Series) -> str:
    if row.get("niveau") == "historique insuffisant":
        return "no_hist"
    type_pred = row.get("type_pred")
    if isinstance(type_pred, str):
        if type_pred == "replay":
            return "replay"
        if type_pred in ("spike", "bias"):
            return "marches"
        if type_pred == "drift":
            return "ecod"
    return "ecod" if bool(row.get("alerte")) else "aucune"


def _recommended_action(fired: str) -> str:
    return {
        "marches": "Reject / quarantine the reading — confirmed attack.",
        "replay":  "Reject / quarantine the reading — confirmed attack.",
        "ecod":    "Investigate — suspect, often a drift the raw rules miss.",
        "no_hist": "Provide more history upstream of the period to analyse.",
        "aucune":  "Accept the data.",
    }.get(fired, "Investigate.")


def build_xai_payload(run: Run, row: int, threshold: float = 0.5) -> dict:
    det_threshold = float(run.model_info.get("threshold", threshold))
    h = run.H
    out = run.out
    station = str(out.at[row, "station"])
    ts = pd.Timestamp(out.at[row, "time"])
    niveau = _niveau_for(out.loc[row], det_threshold)
    alerte = bool(out.at[row, "alerte"])

    observed = _finite(h.at[row, "sm5"])
    residual = _finite(h.at[row, "residual_tu"])
    expected = None if observed is None or residual is None else observed - residual
    dev_pct = None
    if expected is not None and abs(expected) >= 1e-12:
        dev_pct = 100.0 * (observed - expected) / abs(expected)

    twin_sigma = _finite(h.at[row, "residual_tu_sigma"]) if "residual_tu_sigma" in h.columns else None
    twin_z = None
    if residual is not None and twin_sigma and twin_sigma > 0:
        twin_z = residual / twin_sigma

    fired = _rule_key_for(pd.Series({
        "niveau": niveau,
        "type_pred": out.at[row, "type_pred"] if "type_pred" in out.columns else None,
        "alerte": alerte,
    }))
    details: dict[str, Any] = {}

    attack_classification = (
        run.caches.get(("attack_classification",)) if niveau == "ATTAQUE" else None
    )

    return {
        "meta": {
            "station": station,
            "hour_iso": _iso_z(ts),
            "niveau": niveau,
            "score": _finite(out.at[row, "proba"]) if "proba" in out.columns else _finite(out.at[row, "score"]),
            "proba": _finite(out.at[row, "proba"]) if "proba" in out.columns else None,
            "alert": alerte,
            "model_version": "detecteur_v3_final",
            "feature_version": FEATURE_VERSION,
            "threshold": det_threshold,
            "ground_truth": _ground_truth(run, ts),
        },
        "summary": {
            "expected_sm5": expected,
            "observed_sm5": observed,
            "deviation_pct": dev_pct,
            "twin_residual_z": twin_z,
            "verdict": "ALERT" if niveau in ("SUSPECT", "ATTAQUE") else "NORMAL",
        },
        "rules": {
            "fired": fired,
            "reason_text": str(out.at[row, "reason"]) if "reason" in out.columns else None,
            "details": details,
            "recommended_action": _recommended_action(fired),
            "attack_classification": attack_classification,
        },
        "ecod": _ecod_panel(run, row, station),
        "shap_twin": _twin_panel(run, row),
        "temporal": _temporal_panel(run, station, ts),
    }


def build_series_payload(run: Run, station: str) -> dict:
    if station not in set(run.out["station"]):
        raise HTTPException(404, f"station {station!r} has no hours in this run.")
    h, out = run.H, run.out
    mask = h["station"] == station
    sub = h.loc[mask].sort_values("time", kind="mergesort")
    out_sub = out.loc[sub.index]

    scores = sub["ecod_score"].to_numpy(dtype=float) if "ecod_score" in sub else np.full(len(sub), np.nan)
    if "residual_tu_sigma" in sub and "residual_tu" in sub:
        twin_z = (sub["residual_tu"] / sub["residual_tu_sigma"]).to_numpy(dtype=float)
    else:
        twin_z = np.full(len(sub), np.nan)
    top_fam = sub["top_ecod_family"].to_numpy() if "top_ecod_family" in sub else [None] * len(sub)
    threshold = float(run.model_info.get("threshold", 0.5))
    niveau_arr = [_niveau_for(out_sub.loc[i], threshold) for i in sub.index]

    hours = [
        {
            "t": _iso_z(t),
            "niveau": niveau_arr[pos],
            "alert": bool(out_sub.at[idx, "alerte"]),
            "ecod_score": _finite(scores[pos]),
            "twin_residual_z": _finite(twin_z[pos]),
            "top_ecod_family": top_fam[pos] if top_fam[pos] else None,
        }
        for pos, (idx, t) in enumerate(sub["time"].items())
    ]
    return {"station": station, "hours": hours}


# --- routes --------------------------------------------------------------

@router.get("/{run_id}/series/{station}", response_model=XaiSeries)
def get_xai_series(run_id: str, station: str,
                   user: UserAccount = Depends(require_user)):
    run = _require_run(run_id)
    payload = cache_get(run, ("xai_series", station))
    if payload is None:
        payload = build_series_payload(run, station)
        cache_put(run, ("xai_series", station), payload)
    return payload


@router.get("/{run_id}/{station}/{hour_iso}", response_model=XaiResponse)
def get_xai_hour(run_id: str, station: str, hour_iso: str,
                 user: UserAccount = Depends(require_user)):
    run = _require_run(run_id)
    ts = _parse_hour(hour_iso)
    row = _row_for(run, station, ts)
    key = ("xai", station, ts)
    payload = cache_get(run, key)
    if payload is None:
        payload = build_xai_payload(run, row)
        cache_put(run, key, payload)
    return payload


@router.post(
    "/{run_id}/{station}/{hour_iso}/classify",
    response_model=AttackClassificationResponse,
)
def classify_xai_hour(run_id: str, station: str, hour_iso: str,
                      user: UserAccount = Depends(require_user)):
    run = _require_run(run_id)
    ts = _parse_hour(hour_iso)
    row = _row_for(run, station, ts)
    niveau = _niveau_for(run.out.loc[row], float(run.model_info.get("threshold", 0.5)))
    if niveau != "ATTAQUE":
        raise HTTPException(409, "Model 2 classifies ATTAQUE hours only.")

    key = ("attack_classification",)
    if key not in run.caches:
        run.caches[key] = classify_attack(run.H, run.run_id)
    return {"attack_classification": run.caches[key]}