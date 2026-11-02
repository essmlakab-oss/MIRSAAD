"""Temporal explanations — expected vs observed around the selected hour.

`expected = sm5 − residual_tu` where `residual_tu = sm5 − exp5_tu`.
That is *exactly* the v3 twin's `exp5_tu` (identity in detector_core).
The envelope is the per-station robust half-width of `residual_tu` on clean
rows, in m3/m3, computed once at run creation (store.Run.envelope).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.fdi.pipeline import REQUIRED_SENSOR_COLS
from app.fdi.store import Run, _finite, _round


class TemporalError(ValueError):
    pass


MIRROR_SENSORS = ("sm5", "sm10", "st5", "t", "rain", "solar")


@dataclass
class TemporalSeries:
    span: int
    envelope: float
    sensors: dict[str, float | None]
    points: list[dict]
    note: str | None = None


def resolve_target(df: pd.DataFrame, target: str | None) -> str:
    if not target:
        return "sm5"
    t = str(target).strip().lower()
    if t in df.columns:
        return t
    raise TemporalError(f"Unknown target sensor: {target!r}.")


def build_temporal(run: Run, row: int, span: int | None = None,
                   target: str | None = None) -> TemporalSeries:
    df, out, H = run.df, run.out, run.H
    target_col = resolve_target(df, target)

    if row not in out.index:
        raise TemporalError(f"Unknown row {row} (0..{len(out) - 1}).")

    span = int(span or 96)
    span = max(24, min(span, 336))
    lo = max(0, int(row) - span)
    hi = min(len(out), int(row) + span + 1)

    dfw = df.iloc[lo:hi]
    outw = out.iloc[lo:hi]
    hw = H.iloc[lo:hi]

    if "residual_tu" not in hw.columns:
        raise TemporalError("This run has no `residual_tu` (v3 XAI missing?).")

    observed = np.asarray(dfw[target_col], dtype=float) if target_col in dfw else np.full(len(dfw), np.nan)
    residual = np.asarray(hw["residual_tu"], dtype=float)
    expected = observed - residual
    scores = np.asarray(outw["proba"], dtype=float) if "proba" in outw else np.asarray(outw["score"], dtype=float)
    levels = [("ATTAQUE" if a else "normal") for a in outw["alerte"].astype(bool).tolist()]
    times = outw["time"]

    envelope = run.envelope_for(str(out.at[row, "station"]))

    points = []
    for j in range(len(outw)):
        band = envelope if np.isfinite(expected[j]) else None
        points.append({
            "i": int(lo + j),
            "t": str(times.iloc[j]),
            "actual": _round(_finite(observed[j])),
            "expected": _round(_finite(expected[j])),
            "error": _round(_finite(observed[j] - expected[j])),
            "score": _round(_finite(scores[j])),
            "level": levels[j],
            "band_low": _round(_finite(expected[j] - band)) if band is not None else None,
            "band_high": _round(_finite(expected[j] + band)) if band is not None else None,
        })

    sensors: dict[str, float | None] = {}
    for name in MIRROR_SENSORS:
        if name in dfw.columns:
            sensors[name] = _round(_finite(dfw[name].iloc[row - lo]))
    sensors[target_col] = _round(_finite(dfw[target_col].iloc[row - lo]))

    return TemporalSeries(
        span=span, envelope=float(envelope),
        sensors=sensors, points=points, note=None,
    )


__all__ = ["TemporalError", "TemporalSeries", "build_temporal", "resolve_target"]