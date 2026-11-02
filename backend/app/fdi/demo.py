"""Demo data — loads the shipped real demo parquet from Hugging Face.

The old synthetic generator is retired: v3's twins are cross-fitted on
2022-2024, and a 2025-only synthetic set produces no `tu5_z` (the reference-
year guard rejects it). Shipping a real, curated 30-day / year-crossing
slice of `detector_input.parquet` gives the frontend a demo whose alerts,
types, ECOD and SHAP all reflect actual v3 behaviour on real data.

Two HF artifacts are required:
    demo_detector_input.parquet    (the frame)
    demo_row_labels.parquet        (is_attacked / attack_type / attack_id)

Both live in the same HF repo as the models (see config.py).
"""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

from app.fdi.artifacts import get_demo_labels, get_demo_parquet

logger = logging.getLogger("fdi.demo")


@lru_cache(maxsize=1)
def _load_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    data = pd.read_parquet(get_demo_parquet())
    labels = pd.read_parquet(get_demo_labels())
    # Align dtypes
    data["time"] = pd.to_datetime(data["time"]).dt.tz_localize(None)
    labels["time"] = pd.to_datetime(labels["time"]).dt.tz_localize(None)
    data["station"] = data["station"].astype(str)
    labels["station"] = labels["station"].astype(str)
    return data, labels


def _attack_windows(labels: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """(start_iso, end_iso) per attack_id, from the row labels."""
    out: dict[str, tuple[str, str]] = {}
    atk = labels[labels["is_attacked"].fillna(False).astype(bool)].copy()
    if atk.empty:
        return out
    for attack_id, grp in atk.groupby("attack_id", dropna=False):
        if pd.isna(attack_id):
            continue
        grp = grp.sort_values("time")
        out[str(attack_id)] = (
            str(grp["time"].iloc[0]),
            str(grp["time"].iloc[-1]),
        )
    return out


def make_sample_data(
    seed: int | None = None,
    days: int | None = None,
    stations: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, tuple[str, str]]]:
    """Return `(data, attacks)` for the shipped demo.

    Signature preserved for backward compatibility with `fdi.post_demo`.
    `seed`, `days`, `stations` are ignored — the demo is a fixed real slice.
    """
    data, labels = _load_frames()
    df = data.copy()
    attacks = _attack_windows(labels)
    logger.info(
        "Demo loaded: %d rows, %d station(s), %d attack window(s).",
        len(df), df["station"].nunique(), len(attacks),
    )
    return df, attacks