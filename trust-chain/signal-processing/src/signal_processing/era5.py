"""Optional ERA5 features: an independent reference the sensor attacker does not control.

Same guarantees as the core features: parameters are fitted on train rows only, a
value at hour t uses only observations and ERA5 values at hours <= t, no labels are
read, and NaN stays NaN. ERA5 ``time`` needs no shift against USCRN (see era5/README.md).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from signal_processing.features import (
    Grid,
    build_grid,
    lag,
    robust_location_scale,
    trailing_sum,
)
from signal_processing.fit import huber_regression
from signal_processing.io import FORBIDDEN_FILE_STEMS, epoch_hours, sha256_file
from signal_processing.params import Era5Constants, Era5Params, Era5StationParams, FrozenParams
from signal_processing.partition import PartitionRule, assign_partitions

ERA5_FEATURE_COLUMNS = (
    "sp_era5_sm_anom",
    "sp_era5_sm_anom_z",
    "sp_era5_rain_mismatch",
    "sp_era5_dsm_vs_rain",
)
ERA5_COLUMNS = ("time", "station", "tp_mm", "swvl1")
# The ERA5 file has one series per grid cell; both Goodwell stations share one cell.
STATION_MAP = {"OK_Goodwell_2_E": "OK_Goodwell", "OK_Goodwell_2_SE": "OK_Goodwell"}


def era5_station(station: str) -> str:
    return STATION_MAP.get(station, station)


def read_era5(path: Path) -> pd.DataFrame:
    """Read the ERA5 columns used here. Raises ``ValueError`` on a malformed file."""
    if path.stem in FORBIDDEN_FILE_STEMS:
        raise ValueError(f"refusing to read label/evaluator file: {path.name}")
    header = pd.read_csv(path, nrows=0).columns
    missing = [c for c in ERA5_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"{path.name} is missing required columns: {missing}")
    frame = pd.read_csv(path, usecols=list(ERA5_COLUMNS), parse_dates=["time"])
    if frame.duplicated(["station", "time"]).any():
        raise ValueError("duplicate (station, time) keys in ERA5 input")
    return frame


def _align(
    era5: pd.DataFrame, station: str, start: int, size: int
) -> tuple[np.ndarray, np.ndarray]:
    """ERA5 (swvl1, tp_mm) of one station on its grid; hours without ERA5 stay NaN."""
    series = era5[era5["station"] == era5_station(station)]
    if series.empty:
        raise ValueError(f"no ERA5 series for station {station}")
    positions = epoch_hours(series["time"]) - start
    keep = (positions >= 0) & (positions < size)
    swvl1, rain = np.full(size, np.nan), np.full(size, np.nan)
    swvl1[positions[keep]] = series["swvl1"].to_numpy(dtype=float)[keep]
    rain[positions[keep]] = series["tp_mm"].to_numpy(dtype=float)[keep]
    return swvl1, rain


def _rain_difference(grid: Grid, era5_rain: np.ndarray, c: Era5Constants) -> np.ndarray:
    return trailing_sum(grid.rain, c.rain_window_hours) - trailing_sum(
        era5_rain, c.rain_window_hours
    )


def fit_era5(
    frame: pd.DataFrame,
    era5: pd.DataFrame,
    era5_name: str,
    era5_sha256: str,
    rules: tuple[PartitionRule, ...],
    huber_c: float,
    scale_floor: float,
) -> Era5Params:
    """Fit per-station SM5 ~ swvl1 mapping and robust scales on train rows only."""
    c = Era5Constants()
    hours = epoch_hours(frame["time"])
    is_train = assign_partitions(hours, rules) == "train"
    train = frame.loc[is_train].reset_index(drop=True)
    train_hours = hours[is_train]
    stations: dict[str, Era5StationParams] = {}
    for station, rows in train.groupby("station", sort=True).indices.items():
        station_hours = train_hours[rows]
        grid = build_grid(station_hours, train.iloc[rows])
        swvl1, era5_rain = _align(era5, station, int(station_hours.min()), len(grid.rain))
        sm5 = grid.moisture["5"]
        ok = np.isfinite(sm5) & np.isfinite(swvl1)
        if int(ok.sum()) < c.min_fit_pairs:
            raise ValueError(f"only {int(ok.sum())} train pairs to fit ERA5 at {station}")
        design = np.column_stack((np.ones(int(ok.sum())), swvl1[ok]))
        intercept, slope = huber_regression(design, sm5[ok], huber_c)
        anom = sm5 - (intercept + slope * swvl1)
        anom_median, anom_scale, n_anom = robust_location_scale(anom, scale_floor)
        rain_median, rain_scale, _ = robust_location_scale(
            _rain_difference(grid, era5_rain, c), c.rain_scale_floor_mm
        )
        stations[station] = Era5StationParams(
            era5_station=era5_station(station),
            intercept=float(intercept),
            slope=float(slope),
            anom_median=anom_median,
            anom_scale=anom_scale,
            n_anom=n_anom,
            rain_diff_median=rain_median,
            rain_diff_scale=rain_scale,
        )
    return Era5Params(file=era5_name, sha256=era5_sha256, constants=c, stations=stations)


def station_era5_features(
    grid: Grid, swvl1: np.ndarray, era5_rain: np.ndarray, sp: Era5StationParams, c: Era5Constants
) -> dict[str, np.ndarray]:
    sm5 = grid.moisture["5"]
    anom = sm5 - (sp.intercept + sp.slope * swvl1)
    mismatch = (_rain_difference(grid, era5_rain, c) - sp.rain_diff_median) / sp.rain_diff_scale
    rise = sm5 - lag(sm5, c.rain_window_hours)
    era5_window = trailing_sum(era5_rain, c.rain_window_hours)
    with np.errstate(invalid="ignore"):
        unexplained = np.where((rise > 0) & (era5_window < c.no_rain_threshold_mm), rise, 0.0)
    unexplained[~(np.isfinite(rise) & np.isfinite(era5_window))] = np.nan
    return {
        "sp_era5_sm_anom": anom,
        "sp_era5_sm_anom_z": (anom - sp.anom_median) / sp.anom_scale,
        "sp_era5_rain_mismatch": mismatch,
        "sp_era5_dsm_vs_rain": unexplained,
    }


def add_era5_features(
    features: pd.DataFrame, frame: pd.DataFrame, era5: pd.DataFrame, params: FrozenParams
) -> pd.DataFrame:
    """Append the ERA5 columns to ``features``; rows and order are those of ``frame``."""
    if params.era5 is None:
        raise ValueError("params hold no ERA5 block; refit with --era5")
    hours = epoch_hours(frame["time"])
    columns = {name: np.full(len(frame), np.nan) for name in ERA5_FEATURE_COLUMNS}
    for station, rows in frame.groupby("station", sort=True).indices.items():
        if station not in params.era5.stations:
            raise ValueError(f"station without ERA5 params: {station}")
        station_hours = hours[rows]
        grid = build_grid(station_hours, frame.iloc[rows])
        swvl1, era5_rain = _align(era5, station, int(station_hours.min()), len(grid.rain))
        values = station_era5_features(
            grid, swvl1, era5_rain, params.era5.stations[station], params.era5.constants
        )
        for name in ERA5_FEATURE_COLUMNS:
            columns[name][rows] = values[name][grid.positions]
    result = features.copy()
    for name in ERA5_FEATURE_COLUMNS:
        result[name] = columns[name]
    return result


def verified_era5(path: Path, params: FrozenParams) -> pd.DataFrame:
    """Read ERA5 for a transform, refusing a file other than the one fitted on."""
    if params.era5 is None:
        raise ValueError("params hold no ERA5 block; refit with --era5")
    digest = sha256_file(path)
    if digest != params.era5.sha256:
        raise ValueError(f"{path.name} sha256 {digest} differs from the fitted ERA5 file")
    return read_era5(path)
