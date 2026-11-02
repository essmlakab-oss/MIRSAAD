"""Blind protocol: 2025 cannot influence the detector, and pinned artifacts are enforced."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import causal_detector_eval as ev

STATIONS = ("ST_A", "ST_B")
SP = ["sp_innov_z_5", "sp_innov_z_10", "sp_depth_gap_z", "sp_cusum_5", "sp_cusum_10", "sp_replay_match"]


def synthetic(start: str, end: str, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic unit-test frames; attacked hours get larger feature values."""
    rng = np.random.default_rng(seed)
    times = pd.date_range(start, end, freq="h")
    feats, truth = [], []
    for station in STATIONS:
        n = len(times)
        y = np.zeros(n, bool)
        for s in rng.integers(0, n - 30, max(n // 300, 8)):
            y[s : s + 12] = True
        frame = pd.DataFrame({c: rng.normal(0, 1, n) + y * 6 for c in SP[:3]})
        frame["sp_cusum_5"] = rng.random(n) + y * 3
        frame["sp_cusum_10"] = rng.random(n) + y * 3
        frame["sp_replay_match"] = np.zeros(n, "int8")
        frame.insert(0, "station", station)
        frame.insert(1, "time", times)
        feats.append(frame)
        year = (times - pd.Timedelta(hours=1)).year
        truth.append(
            pd.DataFrame(
                {
                    "station": station,
                    "time": times,
                    "partition": np.where(year == 2024, "validation", "test"),
                    "is_attacked": y,
                    "attack_type": np.where(y, "bias", None),
                    "attack_id": [
                        f"bias-{station}-{i}" if flag else None
                        for i, flag in zip(np.cumsum(np.diff(y, prepend=False)), y, strict=True)
                    ],
                    "is_evaluable": True,
                }
            )
        )
    return pd.concat(feats, ignore_index=True), pd.concat(truth, ignore_index=True)


@pytest.fixture
def setup(tmp_path: Path):
    f24, t24 = synthetic("2024-01-01 01:00", "2025-01-01 00:00", 1)
    f25, t25 = synthetic("2025-01-01 01:00", "2025-03-01 00:00", 2)
    features = tmp_path / "f.parquet"
    pd.concat([f24, f25], ignore_index=True).to_parquet(features)
    truth = tmp_path / "ground_truth.parquet"
    pd.concat([t24, t25], ignore_index=True).to_parquet(truth)
    params = tmp_path / "p.json"
    params.write_text(
        json.dumps({"stations": {s: {"depths": {"5": {"cusum_h": 2.0}, "10": {"cusum_h": 2.0}}} for s in STATIONS}}),
        encoding="utf-8",
    )
    return features, truth, params, tmp_path


def run_eval(features, truth, params, out: Path):
    ev.main(
        ["--features", str(features), "--truth", str(truth), "--params", str(params),
         "--out", str(out), "--model-out", str(out / "m.joblib")]
    )  # fmt: skip
    return json.loads((out / "causal_detector_results.json").read_text("utf-8"))


def test_build_detector_refuses_non_validation_rows(setup) -> None:
    features, truth, params, _ = setup
    feats = pd.read_parquet(features)
    all_rows = feats.merge(pd.read_parquet(truth), on=["station", "time"])
    with pytest.raises(ValueError, match="expected only 2024"):
        ev.build_detector(all_rows, json.loads(params.read_text("utf-8")), 0.01)


def test_read_truth_loads_one_partition_only(setup) -> None:
    _, truth, _, _ = setup
    assert set(ev.read_truth(truth, "validation")["partition"]) == {"validation"}


def pinned_model(path: Path):
    sums = path.with_name("models.sha256")
    sums.write_text(f"{ev.sha256_file(path)}  {path.name}\n", encoding="utf-8")
    return ev.load_pinned(path, sums)["model"]


def test_2025_features_and_labels_cannot_change_the_detector(setup, tmp_path: Path) -> None:
    features, truth, params, base = setup
    first = run_eval(features, truth, params, base / "a")
    feats = pd.read_parquet(features)
    late = ev.measured_year(feats["time"]) == 2025
    numeric = [c for c in feats.columns if c.startswith("sp_") and c != "sp_replay_match"]
    feats.loc[late, numeric] = 99.0
    other_features = base / "f2.parquet"
    feats.to_parquet(other_features)
    t = pd.read_parquet(truth)
    t.loc[t["partition"] == "test", "is_attacked"] = ~t.loc[t["partition"] == "test", "is_attacked"]
    other_truth = base / "ground_truth2.parquet"
    t.to_parquet(other_truth)
    second = run_eval(other_features, other_truth, params, base / "b")
    assert first["thresholds"] == second["thresholds"]
    assert first["rule_thresholds"] == second["rule_thresholds"]
    assert first["results"]["validation"] == second["results"]["validation"]
    a = pinned_model(base / "a" / "m.joblib")
    b = pinned_model(base / "b" / "m.joblib")
    probe = np.random.default_rng(0).normal(0, 3, (200, len(SP) + 1))
    probe[:, -1] = 0
    assert np.array_equal(a.predict_proba(probe), b.predict_proba(probe))


def test_label_columns_in_features_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    pd.DataFrame({"station": ["A"], "time": [pd.Timestamp("2025-01-01")], "is_attacked": [True]}).to_parquet(path)
    with pytest.raises(ValueError, match="label columns"):
        ev.load_features([path])


def test_multiple_feature_files_are_joined(tmp_path: Path) -> None:
    key = {"station": ["A", "A"], "time": pd.to_datetime(["2025-01-01 01:00", "2025-01-01 02:00"])}
    pd.DataFrame({**key, "sp_a": [1.0, 2.0]}).to_parquet(tmp_path / "a.parquet")
    pd.DataFrame({**key, "sp_a": [9.0, 9.0], "sp_b": [3.0, 4.0]}).to_parquet(tmp_path / "b.parquet")
    merged = ev.load_features([tmp_path / "a.parquet", tmp_path / "b.parquet"])
    assert merged["sp_a"].tolist() == [1.0, 2.0] and merged["sp_b"].tolist() == [3.0, 4.0]


def _score_args(features, out, model, sums):
    return ["score", "--features", str(features), "--model", str(model), "--sums", str(sums), "--out", str(out),
            "--mode", "real-time", "--attack-set-id", "set", "--decision-delay-h", "0"]  # fmt: skip


def test_score_writes_pinned_output_and_manifest(setup) -> None:
    features, truth, params, base = setup
    run_eval(features, truth, params, base / "a")
    model = base / "a" / "m.joblib"
    sums = base / "sums"
    sums.write_text(f"{ev.sha256_file(model)}  {model.name}\n", encoding="utf-8")
    ev.main(_score_args(features, base / "s1", model, sums))
    ev.main(_score_args(features, base / "s2", model, sums))
    manifest = json.loads((base / "s1" / "manifest.json").read_text("utf-8"))
    assert manifest["sha256"] == ev.sha256_file(base / "s1" / "ai_scores.parquet")
    assert manifest["decision_delay_h"] == 0
    assert {"file", "threshold", "model_version", "attack_set_id"} <= set(manifest)
    assert pq.read_schema(base / "s1" / "ai_scores.parquet").names == ["station", "time", "ai_score", "ai_reason"]
    # Byte-deterministic, and only 2025 rows are scored.
    assert ev.sha256_file(base / "s1" / "ai_scores.parquet") == ev.sha256_file(base / "s2" / "ai_scores.parquet")
    scores = pd.read_parquet(base / "s1" / "ai_scores.parquet")
    assert set(ev.measured_year(scores["time"])) == {2025}


def test_score_refuses_a_model_with_the_wrong_hash(setup) -> None:
    features, truth, params, base = setup
    run_eval(features, truth, params, base / "a")
    model = base / "a" / "m.joblib"
    sums = base / "sums"
    sums.write_text(f"{'0' * 64}  {model.name}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match pinned"):
        ev.main(_score_args(features, base / "s", model, sums))
