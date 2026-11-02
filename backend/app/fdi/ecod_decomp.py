"""ECOD decomposition — 10 features, upload-derived reference.

Mirrors `xai_engine._ecod_scores_and_contribs` EXACTLY, but exposes a
vectorized decompose() so a whole station block is decomposed in one pass.
Contributions are additive BY CONSTRUCTION:

    sum_j contrib_j / n_present == ecod_score

because ecod_score is the MEAN of contributions over non-NaN features, not
a sum. The startup check verifies this identity on demo rows; the panel must
not serve a decomposition that does not close.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from app.fdi.xai_engine import ECOD_FEATURES, ECOD_META

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = ["EcodDecomposer", "EcodMatrix", "startup_additivity_check"]


@dataclass
class EcodMatrix:
    contrib: np.ndarray   # (rows, 10), -log(p) >= 0
    signed: np.ndarray    # contrib * sign(x - median)
    p: np.ndarray         # tail prob, >= 1/n
    upper: np.ndarray     # True = value above reference median


class EcodDecomposer:
    """Vectorized mirror of `xai_engine._ecod_scores_and_contribs`."""

    def __init__(self, ecod_ref: dict):
        # ecod_ref is the dict returned by _build_reference_from_upload:
        #   {station: {"ref": {feature: sorted array}, "sigma": float, "n": int}}
        self.ref = ecod_ref
        self.keys = list(ECOD_FEATURES)

    def decompose_station(self, station: str, D_station: pd.DataFrame) -> EcodMatrix | None:
        """Decompose one station's rows. None if the station has no reference."""
        ref = self.ref.get(station)
        if not ref:
            return None
        ref = ref["ref"]
        n_rows = len(D_station)
        n_feat = len(self.keys)
        contrib = np.full((n_rows, n_feat), np.nan)
        p = np.full((n_rows, n_feat), np.nan)
        upper = np.zeros((n_rows, n_feat), dtype=bool)

        for j, f in enumerate(self.keys):
            arr = ref.get(f)
            if arr is None or arr.size < 2:
                continue
            x = D_station[f].to_numpy(dtype=float) if f in D_station else np.full(n_rows, np.nan)
            ok = np.isfinite(x)
            if not ok.any():
                continue
            n = arr.size
            left = np.searchsorted(arr, x[ok], side="right") / n
            right = 1.0 - np.searchsorted(arr, x[ok], side="left") / n
            pp = np.clip(np.minimum(left, right), 1.0 / n, 1.0)
            contrib[ok, j] = -np.log(pp)
            p[ok, j] = pp
            med = float(np.median(arr))
            upper[ok, j] = x[ok] > med

        signed = contrib * np.where(upper, 1.0, -1.0)
        return EcodMatrix(contrib=contrib, signed=signed, p=p, upper=upper)

    def score_from_matrix(self, m: EcodMatrix) -> np.ndarray:
        """Row-wise mean of non-NaN contributions — matches ecod_score exactly."""
        finite = np.isfinite(m.contrib)
        n = finite.sum(axis=1).astype(float)
        total = np.where(finite, m.contrib, 0.0).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, total / n, np.nan)


def startup_additivity_check(ecod_ref: dict | None, demo_D: pd.DataFrame,
                             max_rows: int = 20) -> float:
    """Assert sum(contrib)/n_present == ecod_score on demo rows.

    Called from main.py's lifespan. Returns the max abs residual and raises
    on failure — the panel must not serve a decomposition that does not close.
    """
    if not ecod_ref:
        print("[XAI] ecod additivity: no reference — skipped.")
        return 0.0

    dec = EcodDecomposer(ecod_ref)
    worst = 0.0
    n_rows = 0
    for st, sub in demo_D.groupby("station"):
        if "ecod_score" not in sub.columns:
            continue
        m = dec.decompose_station(str(st), sub)
        if m is None:
            continue
        score_vec = dec.score_from_matrix(m)
        stored = sub["ecod_score"].to_numpy(dtype=float)
        ok = np.isfinite(score_vec) & np.isfinite(stored)
        if not ok.any():
            continue
        resid = np.abs(score_vec[ok] - stored[ok])
        worst = max(worst, float(resid.max()))
        n_rows += int(ok.sum())
        if n_rows >= max_rows:
            break

    print(f"[XAI] ecod additivity max_abs = {worst:.6g} on {n_rows} rows "
          f"(threshold 1e-9)")
    assert worst <= 1e-9, (
        f"ECOD decomposition does not reproduce ecod_score: "
        f"max_abs={worst:.6g} > 1e-9."
    )
    return worst