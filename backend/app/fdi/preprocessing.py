"""CSV in, scored rows out — delegates to `pipeline.run_pipeline` + XAI.

Two responsibilities, kept apart on purpose:
    prepare_df(raw)          schema validation + normalisation
    run_predictions(det,df)  pipeline.run_pipeline + xai_engine.enrich
"""

from __future__ import annotations

import pandas as pd

from app.fdi import config
from app.fdi.artifacts import get_defender, get_type_classifier, get_witness_ref
from app.fdi.pipeline import REQUIRED_SENSOR_COLS, run_pipeline
from app.fdi.xai_engine import enrich


class SchemaError(ValueError):
    pass


def prepare_df(raw: pd.DataFrame) -> pd.DataFrame:
    if len(raw) == 0:
        raise SchemaError("The file contains no rows.")
    if len(raw) > config.MAX_ROWS:
        raise SchemaError(
            f"The file has {len(raw):,} rows; the limit is {config.MAX_ROWS:,}."
        )
    if "time" not in raw.columns or "station" not in raw.columns:
        raise SchemaError("Columns `time` and `station` are required.")
    missing = [c for c in REQUIRED_SENSOR_COLS if c not in raw.columns]
    if missing:
        raise SchemaError(f"Missing required sensor columns: {missing}")

    df = raw.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.tz_localize(None)
    if df["time"].isna().any():
        raise SchemaError("The 'time' column contains unreadable values.")
    df["station"] = df["station"].astype(str)
    if df["station"].str.strip().eq("").any():
        raise SchemaError("The 'station' column contains empty values.")
    return df.sort_values(["station", "time"]).reset_index(drop=True)


def renamed_columns(raw: pd.DataFrame) -> dict[str, str]:
    return {}


def run_predictions(df: pd.DataFrame):
    """Score + explain. Returns (out, H) where H is the enriched frame.

    `out` is a lightweight projection (station, time, proba, alerte,
    type_pred, type_conf, reason, niveau); `H` is the full enriched frame
    (all witnesses, ECOD, SHAP, residual_tu, etc.). They share a RangeIndex.
    """
    defender = get_defender()
    T = get_type_classifier()
    witness_ref = get_witness_ref()

    enriched = run_pipeline(df, T, defender=defender)
    enriched = enrich(enriched, defender, witness_ref, with_shap=True)
    enriched = enriched.reset_index(drop=True)

    out = pd.DataFrame({
        "station":   enriched["station"].astype(str),
        "time":      enriched["time"],
        "proba":     enriched["proba"].astype(float),
        "alerte":    enriched["alerte"].astype(bool),
        "type_pred": enriched["type_pred"] if "type_pred" in enriched else None,
        "type_conf": enriched["type_conf"].astype(float) if "type_conf" in enriched else float("nan"),
        "reason":    enriched["reason"] if "reason" in enriched else None,
        "score":     enriched["proba"].astype(float),
    })
    return out, enriched