"""run_pipeline — the v3 detector + type classifier, exactly as app.py runs
it, minus the Streamlit skin.

Verbatim behaviour of app.py's run_pipeline and _rehydrate_raw_columns;
the only difference is that errors raise instead of calling st.error.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from app.fdi import detector_core as dc
from app.fdi.artifacts import get_defender
from app.fdi.defender import FastDefender

logger = logging.getLogger("fdi.pipeline")

REQUIRED_SENSOR_COLS = [
    "SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "SOIL_TEMP_5",
    "T_HR_AVG", "P_CALC", "SOLARAD", "RH_HR_AVG",
]
DEFAULT_REFERENCE_YEARS = (2022, 2023, 2024)
NEIGHBOUR_WITNESS       = {"OK_Goodwell_2_E"}

_RAW_COLS_NEEDED: tuple[tuple[str, str], ...] = (
    ("SOIL_MOISTURE_5",  "sm5"),
    ("SOIL_MOISTURE_10", "sm10"),
    ("SOIL_MOISTURE_20", "sm20"),
    ("SOIL_MOISTURE_50", "sm50"),
    ("SOIL_TEMP_5",      "st5"),
    ("SOIL_TEMP_10",     "st10"),
    ("SOIL_TEMP_20",     "st20"),
    ("T_HR_AVG",         "t"),
    ("RH_HR_AVG",        "rh"),
    ("SOLARAD",          "solar"),
    ("SUR_TEMP",         "sur"),
    ("P_CALC",           "rain"),
)
_XAI_RAW_COLUMNS   = [dst for _, dst in _RAW_COLS_NEEDED]
_XAI_TWIN_COLUMNS  = ["exp5_tu", "exp10_tu", "exp5_tw", "exp5_xu"]


def reference_years(defender: FastDefender) -> tuple[int, ...]:
    M = getattr(defender, "M", None)
    yrs = M.get("ref_years") if isinstance(M, dict) else None
    if yrs:
        try:
            return tuple(sorted(int(y) for y in yrs))
        except Exception as e:
            logger.warning("Model declares malformed ref_years=%r: %s", yrs, e)
    return DEFAULT_REFERENCE_YEARS


def _rehydrate_raw_columns(D: pd.DataFrame, df_raw: pd.DataFrame) -> pd.DataFrame:
    if D.empty:
        return D
    raw_cols_src = [src for src, _ in _RAW_COLS_NEEDED if src in df_raw.columns]
    if not raw_cols_src:
        logger.warning("None of the raw XAI columns are in the upload.")
        return D
    raw = df_raw[["station", "time"] + raw_cols_src].copy()
    raw["station"] = raw["station"].astype(str)
    raw["time"] = pd.to_datetime(raw["time"]).dt.tz_localize(None)
    rename_map = {src: dst for src, dst in _RAW_COLS_NEEDED if src in raw.columns}
    raw = raw.rename(columns=rename_map)
    overlap = [c for c in raw.columns if c in D.columns and c not in ("station", "time")]
    if overlap:
        raw = raw.drop(columns=overlap)
    D = D.copy()
    D["station"] = D["station"].astype(str)
    D["time"] = pd.to_datetime(D["time"]).dt.tz_localize(None)
    D = D.merge(raw, on=["station", "time"], how="left", suffixes=("", "_raw"))
    missing = [c for c in _XAI_RAW_COLUMNS if c not in D.columns]
    for c in missing:
        D[c] = np.nan
    if missing:
        logger.warning("Raw XAI columns still missing after rehydration: %s", missing)
    return D


def validate_upload(df: pd.DataFrame, defender: FastDefender) -> list[str]:
    problems = []
    if "station" not in df.columns or "time" not in df.columns:
        return ["Columns `station` and `time` are required."]
    missing = [c for c in REQUIRED_SENSOR_COLS if c not in df.columns]
    if missing:
        problems.append(f"Missing required sensor columns: {missing}")
    unknown = sorted(set(df.station.astype(str)) - set(defender.store) - NEIGHBOUR_WITNESS)
    if unknown:
        problems.append(
            f"Stations without a pre-trained twin: {unknown}. "
            f"Available: {sorted(defender.store)}"
        )
    try:
        years = set(pd.to_datetime(df.time).dt.year.unique().tolist())
    except Exception as e:
        return problems + [f"`time` unparseable: {e}"]
    ref = reference_years(defender)
    if not (years & set(ref)):
        problems.append(
            f"Upload contains none of the reference years {ref}. "
            f"Found: {sorted(years)}."
        )
    return problems


def run_pipeline(df: pd.DataFrame, T: dict, defender: FastDefender | None = None) -> pd.DataFrame:
    """The full v3 pipeline: build witnesses, score, classify type.

    Returns the enriched frame (proba, alerte, type_pred, type_conf,
    type_<class> for each class, residual_tu, the raw physical columns,
    and the exp*_* twin expected values). XAI columns are added by
    `xai_engine.enrich` afterwards.
    """
    if defender is None:
        defender = get_defender()

    stations = sorted(set(df.station.astype(str)) & set(defender.store))
    df_known = df[df.station.astype(str).isin(set(stations) | NEIGHBOUR_WITNESS)].copy()
    if not stations:
        raise ValueError("Upload contains none of the trained stations.")

    D = defender.features(df_known, stations=stations)
    if D.empty:
        raise ValueError(
            "Feature builder returned 0 rows. Check USCRN/ERA5 column names "
            "and that the upload covers a reference year."
        )

    D = _rehydrate_raw_columns(D, df_known)

    if "tu5_z" not in D.columns:
        raise ValueError("Feature frame has no `tu5_z`.")
    D = D[D["tu5_z"].notna()].reset_index(drop=True)
    if D.empty:
        raise ValueError("All rows dropped because tu5_z is NaN.")

    required_for_xai = {"sm5", "exp5_tu"}
    missing = sorted(required_for_xai - set(D.columns))
    if missing:
        raise ValueError(
            f"Missing {missing} after rehydration — upload lacks the raw "
            f"USCRN columns needed to reconstruct sm5 or exp5_tu."
        )

    D = dc.predict(D, defender.M)

    if "exp5_tu" in D.columns and "residual_tu" not in D.columns:
        D["residual_tu"] = D["sm5"] - D["exp5_tu"]

    classes = list(T["classes"])
    for c in classes:
        D[f"type_{c}"] = np.nan
    D["type_pred"] = None
    D["type_conf"] = np.nan

    missing_feats = [f for f in T["features"] if f not in D.columns]
    if missing_feats:
        raise ValueError(
            f"type_classifier expects missing features: {missing_feats[:5]}…"
        )

    gate = D["alerte"].fillna(False).to_numpy().astype(bool)
    if gate.any():
        Xt = D.loc[gate, T["features"]].to_numpy(np.float32)
        tp = T["model"].predict_proba(Xt)
        for j, c in enumerate(classes):
            D.loc[gate, f"type_{c}"] = tp[:, j]
        best = tp.argmax(axis=1)
        D.loc[gate, "type_pred"] = [classes[i] for i in best]
        D.loc[gate, "type_conf"] = tp[np.arange(len(best)), best]

    return D.sort_values(["station", "time"]).reset_index(drop=True)