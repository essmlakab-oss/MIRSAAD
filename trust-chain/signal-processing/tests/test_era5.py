"""ERA5 features: optional, train-only, causal, label-free, NaN-preserving, deterministic."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from signal_processing.cli import app
from signal_processing.era5 import (
    ERA5_FEATURE_COLUMNS,
    STATION_MAP,
    add_era5_features,
    fit_era5,
    read_era5,
)
from signal_processing.features import FEATURE_COLUMNS, transform
from signal_processing.io import base_params_sha256, dumps_json, sha256_file
from signal_processing.partition import DEFAULT_RULES

runner = CliRunner()
COMMITTED_FEATURES_SHA256 = "6b636698dee2ef66358975b94f3a00c0e4f3ceadbbc87f36a5843fb58c7cfdc3"
BASE_PARAMS_SHA256 = "960fefa7a0922dc0729dca0814ba3083c6c2eaa66791df610906509011d43ac7"
REPO = Path(__file__).resolve().parents[2]


def make_era5(frame: pd.DataFrame) -> pd.DataFrame:
    """Synthetic reference, loosely tied to the synthetic sensors, for unit tests only."""
    parts = []
    for i, (station, rows) in enumerate(frame.groupby("station", sort=True)):
        rng = np.random.default_rng(100 + i)
        sm = rows["SOIL_MOISTURE_5"].ffill().bfill().to_numpy()
        rain = rows["P_CALC"].fillna(0.0).to_numpy()
        parts.append(
            pd.DataFrame(
                {
                    "time": rows["time"].to_numpy(),
                    "station": station,
                    "tp_mm": np.abs(rain * 0.8 + rng.normal(0, 0.05, len(rows))),
                    "swvl1": 0.05 + 0.7 * sm + rng.normal(0, 0.005, len(rows)),
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def fitted(frame, params, era5):
    block = fit_era5(frame, era5, "e.csv", "0" * 64, DEFAULT_RULES, 1.345, 0.0005)
    return params.model_copy(update={"era5": block})


@pytest.fixture(scope="module")
def era5(frame: pd.DataFrame) -> pd.DataFrame:
    return make_era5(frame)


@pytest.fixture(scope="module")
def era5_params(frame, params, era5):
    return fitted(frame, params, era5)


@pytest.fixture(scope="module")
def result(frame, era5_params, era5) -> pd.DataFrame:
    return add_era5_features(transform(frame, era5_params), frame, era5, era5_params)


def test_goodwell_stations_share_one_series() -> None:
    assert STATION_MAP == {"OK_Goodwell_2_E": "OK_Goodwell", "OK_Goodwell_2_SE": "OK_Goodwell"}


def test_mapping_recovers_linear_relation(era5_params) -> None:
    for sp in era5_params.era5.stations.values():
        assert 0.8 < sp.slope < 2.0
        assert sp.anom_scale >= 0.0005


def test_columns_rows_and_core_features_unchanged(frame, params, era5_params, result) -> None:
    assert list(result.columns) == ["station", "time", *FEATURE_COLUMNS, *ERA5_FEATURE_COLUMNS]
    assert len(result) == len(frame)
    pd.testing.assert_frame_equal(
        result[["station", "time", *FEATURE_COLUMNS]], transform(frame, params)
    )


def test_params_ignore_validation_and_test_rows(frame, params, era5) -> None:
    baseline = fit_era5(frame, era5, "e", "0" * 64, DEFAULT_RULES, 1.345, 0.0005)
    late = (frame["time"] - pd.Timedelta(hours=1)) >= "2024-01-01"
    changed_frame = frame.copy()
    changed_frame.loc[late, "SOIL_MOISTURE_5"] = 0.9
    changed_era5 = era5.copy()
    changed_era5.loc[(era5["time"] - pd.Timedelta(hours=1)) >= "2024-01-01", "swvl1"] = 0.01
    assert (
        fit_era5(changed_frame, changed_era5, "e", "0" * 64, DEFAULT_RULES, 1.345, 0.0005)
        == baseline
    )


def test_causality_future_perturbation_leaves_past_unchanged(
    frame, era5_params, era5, result
) -> None:
    cut = frame["time"].sort_values().iloc[len(frame) // 2]
    future_frame = frame.copy()
    future_frame.loc[frame["time"] > cut, ["SOIL_MOISTURE_5", "P_CALC"]] = 0.5
    future_era5 = era5.copy()
    future_era5.loc[era5["time"] > cut, ["swvl1", "tp_mm"]] = 3.0
    changed = add_era5_features(
        transform(future_frame, era5_params), future_frame, future_era5, era5_params
    )
    past = frame["time"] <= cut
    pd.testing.assert_frame_equal(
        changed.loc[past, list(ERA5_FEATURE_COLUMNS)], result.loc[past, list(ERA5_FEATURE_COLUMNS)]
    )


def test_nan_stays_nan(frame, era5_params, era5, result) -> None:
    missing = frame["SOIL_MOISTURE_5"].isna().to_numpy()
    assert missing.any()
    for name in ("sp_era5_sm_anom", "sp_era5_sm_anom_z"):
        assert result.loc[missing, name].isna().all()
    no_era5 = era5.copy()
    no_era5.loc[no_era5.index[100:110], "swvl1"] = np.nan
    no_era5.loc[no_era5.index[200:205], "tp_mm"] = np.nan
    out = add_era5_features(transform(frame, era5_params), frame, no_era5, era5_params)
    assert out["sp_era5_sm_anom"].isna().sum() > result["sp_era5_sm_anom"].isna().sum()
    assert out["sp_era5_rain_mismatch"].isna().sum() > result["sp_era5_rain_mismatch"].isna().sum()


def test_hours_absent_from_era5_are_nan(frame, era5_params, era5) -> None:
    last = era5["time"].max()
    out = add_era5_features(
        transform(frame, era5_params), frame, era5[era5["time"] < last], era5_params
    )
    assert out.loc[frame["time"] == last, "sp_era5_sm_anom"].isna().all()


def test_fake_rain_raises_mismatch(frame, era5_params, era5, result) -> None:
    rows = frame.index[frame["station"] == "ST_A"][3000:3006]
    attacked = frame.copy()
    attacked.loc[rows, "P_CALC"] = 8.0
    out = add_era5_features(transform(attacked, era5_params), attacked, era5, era5_params)
    assert out.loc[rows[-1], "sp_era5_rain_mismatch"] > (
        result.loc[rows[-1], "sp_era5_rain_mismatch"] + 10
    )


def test_unexplained_rise_flagged_only_without_era5_rain(frame, era5_params, era5) -> None:
    dry_era5 = era5.assign(tp_mm=0.0)
    wet_era5 = era5.assign(tp_mm=2.0)
    dry = add_era5_features(transform(frame, era5_params), frame, dry_era5, era5_params)
    wet = add_era5_features(transform(frame, era5_params), frame, wet_era5, era5_params)
    assert (dry["sp_era5_dsm_vs_rain"] > 0).any()
    assert (wet["sp_era5_dsm_vs_rain"].dropna() == 0).all()


# ---------------------------------------------------------------- CLI


@pytest.fixture
def files(tmp_path: Path, frame: pd.DataFrame, era5: pd.DataFrame) -> tuple[Path, Path]:
    measured = frame["time"] - pd.Timedelta(hours=1)
    detector = tmp_path / "detector_input.parquet"
    frame.assign(
        partition=(measured >= "2024-01-01").map({True: "validation", False: "train"})
    ).to_parquet(detector)
    csv = tmp_path / "era5.csv"
    era5.to_csv(csv, index=False)
    return detector, csv


def _invoke(*args: object) -> None:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output


def test_cli_with_and_without_era5(files, tmp_path: Path) -> None:
    detector, csv = files
    plain_params, era5_params = tmp_path / "plain.json", tmp_path / "era5.json"
    _invoke("fit", "--input", detector, "--output", plain_params)
    _invoke("fit", "--input", detector, "--output", era5_params, "--era5", csv)
    assert "era5" not in json.loads(plain_params.read_text("utf-8"))

    _invoke("transform", "--input", detector, "--params", plain_params, "--output", tmp_path / "a")
    _invoke("transform", "--input", detector, "--params", era5_params, "--output", tmp_path / "b")
    _invoke(
        "transform",
        "--input",
        detector,
        "--params",
        era5_params,
        "--output",
        tmp_path / "c",
        "--era5",
        csv,
    )

    # Without the flag the features and contract are byte-identical, whichever params file is used.
    for name in ("signal_features.parquet", "feature_contract.json"):
        assert sha256_file(tmp_path / "a" / name) == sha256_file(tmp_path / "b" / name)
    with_era5 = pd.read_parquet(tmp_path / "c" / "signal_features.parquet")
    assert list(with_era5.columns)[-4:] == list(ERA5_FEATURE_COLUMNS)
    contract = json.loads((tmp_path / "c" / "feature_contract.json").read_text("utf-8"))
    assert set(ERA5_FEATURE_COLUMNS) <= set(contract["columns"])
    assert contract["era5"]["station_map"]
    provenance = json.loads((tmp_path / "c" / "run_provenance.json").read_text("utf-8"))
    assert provenance["era5"]["sha256"] == sha256_file(csv)


def test_cli_transform_refuses_other_era5_file(files, tmp_path: Path) -> None:
    detector, csv = files
    params = tmp_path / "p.json"
    _invoke("fit", "--input", detector, "--output", params, "--era5", csv)
    other = tmp_path / "other.csv"
    other.write_text(csv.read_text("utf-8") + "\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "transform",
            "--input",
            str(detector),
            "--params",
            str(params),
            "--output",
            str(tmp_path / "o"),
            "--era5",
            str(other),
        ],
    )
    assert result.exit_code == 2


def test_cli_era5_requires_era5_params(files, tmp_path: Path) -> None:
    detector, csv = files
    params = tmp_path / "p.json"
    _invoke("fit", "--input", detector, "--output", params)
    result = runner.invoke(
        app,
        [
            "transform",
            "--input",
            str(detector),
            "--params",
            str(params),
            "--output",
            str(tmp_path / "o"),
            "--era5",
            str(csv),
        ],
    )
    assert result.exit_code == 2


def test_read_era5_rejects_missing_column_and_label_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("time,station\n2024-01-01 00:00:00,A\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        read_era5(bad)
    labelled = tmp_path / "ground_truth.csv"
    labelled.write_text("time,station,tp_mm,swvl1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        read_era5(labelled)


def test_era5_block_does_not_change_base_identity(frame, params, era5_params) -> None:
    assert base_params_sha256(params) == base_params_sha256(era5_params)
    payload = dumps_json(params.model_dump(mode="json", exclude_none=True))
    assert "era5" not in json.loads(payload)


@pytest.mark.skipif(
    not (REPO / "signal-processing/params/frozen_params.json").is_file(),
    reason="no committed params",
)
def test_committed_params_keep_their_base_identity() -> None:
    from signal_processing.params import FrozenParams

    committed = REPO / "signal-processing/params/frozen_params.json"
    params = FrozenParams.model_validate_json(committed.read_bytes())
    assert base_params_sha256(params) == BASE_PARAMS_SHA256


def _release() -> Path | None:
    generated = REPO / "attack-engine/datasets/generated"
    target = "ff478b38f2f0996b5301a48f93f0a4b0a40b7bc321440c69f8e85ff31c6eb50a"
    for path in sorted(generated.glob("**/detector/detector_input.parquet")):
        if sha256_file(path) == target:
            return path
    return None


def test_real_release_reproduces_committed_features(tmp_path: Path) -> None:
    release = _release()
    if release is None:
        pytest.skip("combined detector release is unavailable")
    params = REPO / "signal-processing/params/frozen_params.json"
    _invoke("transform", "--input", release, "--params", params, "--output", tmp_path)
    assert sha256_file(tmp_path / "signal_features.parquet") == COMMITTED_FEATURES_SHA256
