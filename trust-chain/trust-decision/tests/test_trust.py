"""Decision table, confirmation column, hash pinning, log chain, real-slice smoke test."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from trust_decision.chain import verify_log, write_log
from trust_decision.cli import app
from trust_decision.pipeline import hourly_frame, load_scores, read_gate, rule_hits
from trust_decision.table import Evidence, Verdict, confirm, decide

REPO = Path(__file__).resolve().parents[2]
THRESHOLD = 0.5


def evidence(**kwargs) -> Evidence:
    return Evidence(**{"gate_code": "OK", "ai_score": 0.1, "threshold": THRESHOLD, **kwargs})


# ------------------------------------------------------------- one test per table row


def test_gate_rejected_is_quarantine() -> None:
    verdict = decide(evidence(gate_code="E_BAD_SIG", ai_score=0.99, rule_hits=("r",)))
    assert (verdict.decision, verdict.trust_score) == ("QUARANTINE", 0.0)
    assert "E_BAD_SIG" in verdict.reason


def test_alarm_and_rule_is_alert() -> None:
    verdict = decide(evidence(ai_score=0.9, rule_hits=("sp_replay_match",)))
    assert (verdict.decision, verdict.trust_score) == ("ALERT", 0.05)
    assert "sp_replay_match" in verdict.reason


def test_alarm_only_is_suspect() -> None:
    verdict = decide(evidence(ai_score=0.9))
    assert (verdict.decision, verdict.trust_score) == ("SUSPECT", 0.20)


def test_rule_only_is_review() -> None:
    verdict = decide(evidence(rule_hits=("sp_cusum_5",)))
    assert (verdict.decision, verdict.trust_score) == ("REVIEW", 0.60)


@pytest.mark.parametrize(
    "kwargs", [{"features_available": False}, {"ai_score": None}, {"ai_score": float("nan")}]
)
def test_features_unavailable_is_review(kwargs) -> None:
    verdict = decide(evidence(**kwargs))
    assert (verdict.decision, verdict.trust_score) == ("REVIEW", 0.50)


def test_otherwise_accept_with_one_minus_score() -> None:
    verdict = decide(evidence(ai_score=0.25))
    assert verdict.decision == "ACCEPT"
    assert verdict.trust_score == pytest.approx(0.75)


def test_first_match_wins_and_all_scores_in_unit_interval() -> None:
    assert decide(evidence(gate_code="E_STALE", ai_score=0.9)).decision == "QUARANTINE"
    assert decide(evidence(ai_score=0.9, features_available=False)).decision == "SUSPECT"
    for score in (0.0, 0.5, 1.0):
        assert 0.0 <= decide(evidence(ai_score=score)).trust_score <= 1.0


def test_seq_gap_warning_is_kept_in_the_reason() -> None:
    verdict = decide(evidence(warnings=("W_SEQ_GAP",)))
    assert verdict.decision == "ACCEPT"
    assert "W_SEQ_GAP" in verdict.reason


def test_rule_hits_use_thresholds_and_ignore_nan() -> None:
    rules = {"sp_innov_z_5": 5.0, "sp_innov_z_10": 5.0, "sp_depth_gap_z": 5.0}
    row = {
        "sp_innov_z_5": -6.0,
        "sp_innov_z_10": float("nan"),
        "sp_depth_gap_z": 1.0,
        "sp_cusum_5": 3.0,
        "sp_cusum_10": 1.0,
        "sp_replay_match": 1,
    }
    hits = rule_hits(row, rules, {"5": 3.0, "10": 3.0})
    assert len(hits) == 3
    assert any("sp_innov_z_5" in h for h in hits)
    assert any("sp_cusum_5" in h for h in hits)
    assert "sp_replay_match" in hits


# ------------------------------------------------------------- confirmation


@pytest.mark.parametrize("immediate", ["SUSPECT", "REVIEW"])
def test_confirmation_escalates_or_clears(immediate) -> None:
    first = Verdict(immediate, 0.5, "x")
    assert confirm(first, 0.9, THRESHOLD).decision == "ALERT"
    assert confirm(first, 0.9, THRESHOLD).trust_score == 0.05
    assert confirm(first, 0.2, THRESHOLD).decision == "CLEARED"
    assert confirm(first, 0.2, THRESHOLD).trust_score == pytest.approx(0.8)


@pytest.mark.parametrize("immediate", ["ACCEPT", "ALERT", "QUARANTINE"])
def test_confirmation_leaves_other_decisions(immediate) -> None:
    first = Verdict(immediate, 0.5, "x")
    assert confirm(first, 0.9, THRESHOLD) == first
    assert confirm(first, 0.1, THRESHOLD) == first


def test_confirmation_without_era5_score_keeps_verdict() -> None:
    first = Verdict("SUSPECT", 0.2, "x")
    assert confirm(first, None, THRESHOLD) == first
    assert confirm(first, float("nan"), THRESHOLD) == first


# ------------------------------------------------------------- pinning and the log


def write_scores(folder: Path, frame: pd.DataFrame, **extra) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ai_scores.parquet"
    frame.to_parquet(path, index=False)
    manifest = {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "threshold": THRESHOLD,
        "model_version": "test",
        "attack_set_id": "test",
        "decision_delay_h": 0,
        "rule_thresholds": {"sp_innov_z_5": 1e9, "sp_innov_z_10": 1e9, "sp_depth_gap_z": 1e9},
        **extra,
    }
    target = folder / "manifest.json"
    target.write_text(json.dumps(manifest), encoding="utf-8")
    return target


def score_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station": ["A"],
            "time": pd.to_datetime(["2025-06-01 01:00"]),
            "ai_score": [0.1],
            "ai_reason": ["r"],
        }
    )


def test_tampered_score_file_is_refused(tmp_path: Path) -> None:
    manifest = write_scores(tmp_path, score_frame())
    assert len(load_scores(manifest).frame) == 1
    tampered = score_frame().assign(ai_score=0.0)
    tampered.to_parquet(tmp_path / "ai_scores.parquet", index=False)
    with pytest.raises(ValueError, match="does not match manifest"):
        load_scores(manifest)


def test_manifest_pin_is_enforced(tmp_path: Path) -> None:
    manifest = write_scores(tmp_path, score_frame())
    with pytest.raises(ValueError, match="not the pinned"):
        load_scores(manifest, "0" * 64)


def test_manifest_pointing_at_labels_is_refused(tmp_path: Path) -> None:
    manifest = write_scores(tmp_path, score_frame())
    document = json.loads(manifest.read_text("utf-8"))
    document["file"] = "ground_truth.parquet"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="label file"):
        load_scores(manifest)


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "decision_log.jsonl"
    write_log(
        [{"decision": "ACCEPT", "n": i, "confirmed_decision": "ACCEPT"} for i in range(5)], path
    )
    return path


def test_intact_log_verifies(log: Path) -> None:
    assert verify_log(log)[0]
    assert runner_exit("verify-log", str(log)) == 0


def test_every_line_carries_the_previous_hash(log: Path) -> None:
    lines = log.read_bytes().splitlines()
    for before, after in itertools.pairwise(lines):
        expected = hashlib.sha256(before).hexdigest()
        assert json.loads(after)["prev_sha256"] == expected


@pytest.mark.parametrize("target", [0, 2, 4])
def test_edited_line_is_detected(log: Path, target: int) -> None:
    lines = log.read_bytes().splitlines()
    lines[target] = lines[target].replace(b'"ACCEPT"', b'"ALERT"', 1)
    log.write_bytes(b"\n".join(lines) + b"\n")
    assert not verify_log(log)[0]
    assert runner_exit("verify-log", str(log)) == 1


def test_removed_and_reordered_and_truncated_lines_are_detected(log: Path) -> None:
    original = log.read_bytes().splitlines()
    for mutated in (
        original[:2] + original[3:],
        [original[1], original[0], *original[2:]],
        original[:-1],
    ):
        log.write_bytes(b"\n".join(mutated) + b"\n")
        assert not verify_log(log)[0]


def runner_exit(*args: str) -> int:
    return CliRunner().invoke(app, list(args)).exit_code


# ------------------------------------------------------------- gate reader and smoke test


def make_gate(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE decisions (decision_id INTEGER PRIMARY KEY, timestamp TEXT, sensor_id TEXT,
        station TEXT, session_id TEXT, sequence INTEGER, decision TEXT, code TEXT, reason TEXT,
        packet_digest TEXT, digest_scope TEXT, warnings TEXT, sequence_gap INTEGER);
        CREATE TABLE accepted_observations (
            decision_id INTEGER PRIMARY KEY, telemetry_json TEXT);"""
    )
    for i, (station, time, code, warnings, values) in enumerate(rows, 1):
        accepted = code == "OK"
        connection.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "2026-01-01T00:00:00+00:00", "s", station, "u", i,
             "ACCEPT" if accepted else "REJECT", code, "r", "d", "body", json.dumps(warnings), 0),
        )  # fmt: skip
        if accepted:
            telemetry = {"station": station, "observation_time": time, "measurements": values}
            connection.execute(
                "INSERT INTO accepted_observations VALUES(?,?)", (i, json.dumps(telemetry))
            )
    connection.commit()
    connection.close()


def test_gate_is_opened_read_only(tmp_path: Path) -> None:
    db = tmp_path / "gate.sqlite3"
    make_gate(db, [("A", None, "E_BAD_SIG", [], None)])
    before = db.read_bytes()
    decisions, accepted = read_gate(db)
    assert len(decisions) == 1 and accepted.empty
    assert db.read_bytes() == before


def test_hourly_frame_keeps_telemetry_and_null(tmp_path: Path) -> None:
    db = tmp_path / "gate.sqlite3"
    values = {"SOIL_MOISTURE_5": None, "SOIL_MOISTURE_10": 0.2, "P_CALC": 0.0}
    make_gate(db, [("A", "2025-06-01T01:00:00Z", "OK", [], values)])
    frame = hourly_frame(read_gate(db)[1])
    assert frame.loc[0, "time"] == pd.Timestamp("2025-06-01 01:00")
    assert pd.isna(frame.loc[0, "SOIL_MOISTURE_5"])
    assert frame.loc[0, "SOIL_MOISTURE_10"] == 0.2


REAL_PARQUET = REPO / "USCRN_livraison/data/uscrn_clean.parquet"
REAL_PARAMS = REPO / "signal-processing/params/frozen_params.json"


def test_smoke_on_a_small_real_slice(tmp_path: Path) -> None:
    """Real clean USCRN observations, one week of one station, through the whole command."""
    source = pd.read_parquet(
        REAL_PARQUET, columns=["station", "time", "SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "P_CALC"]
    )
    week = source[
        (source["station"] == "CA_Fallbrook_5_NE")
        & (source["time"] > "2025-03-01")
        & (source["time"] <= "2025-03-08")
    ].reset_index(drop=True)
    rows = []
    for record in week.to_dict("records"):
        stamp = record["time"].isoformat() + "Z"
        values = {
            k: (None if pd.isna(record[k]) else record[k])
            for k in ("SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "P_CALC")
        }
        warnings = ["W_SEQ_GAP"] if len(rows) == 30 else []
        rows.append(("CA_Fallbrook_5_NE", stamp, "OK", warnings, values))
    rows.append(("CA_Fallbrook_5_NE", None, "E_BAD_SIG", [], None))
    db = tmp_path / "gate.sqlite3"
    make_gate(db, rows)
    scores = week[["station", "time"]].assign(ai_score=0.1, ai_reason="r")
    scores.loc[50, "ai_score"] = 0.9
    realtime = write_scores(tmp_path / "rt", scores)
    delayed_scores = scores.assign(ai_score=0.1)
    delayed = write_scores(tmp_path / "era5", delayed_scores, decision_delay_h=120)
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        ["run", "--db", str(db), "--scores", str(realtime), "--features-params", str(REAL_PARAMS),
         "--out", str(out), "--era5-scores", str(delayed)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    table = pd.read_parquet(out / "trust_decisions.parquet")
    assert len(table) == len(rows)
    assert table["trust_score"].between(0, 1).all()
    assert table.iloc[-1]["decision"] == "QUARANTINE"
    assert table.iloc[-1]["confirmed_decision"] == "QUARANTINE"
    assert table.iloc[0]["decision"] == "REVIEW"  # the first hour has no history
    assert table.iloc[50]["decision"] in {"SUSPECT", "ALERT"}
    assert "W_SEQ_GAP" in table.iloc[30]["reason"]
    if table.iloc[50]["decision"] == "SUSPECT":
        assert table.iloc[50]["confirmed_decision"] == "CLEARED"
    assert verify_log(out / "decision_log.jsonl")[0]
    assert "confirmed_decision" in (out / "decision_log.jsonl").read_text("utf-8").splitlines()[0]
    # Tampered score file makes the same command fail.
    pd.read_parquet(tmp_path / "rt/ai_scores.parquet").assign(ai_score=0.0).to_parquet(
        tmp_path / "rt/ai_scores.parquet", index=False
    )
    refused = CliRunner().invoke(
        app,
        ["run", "--db", str(db), "--scores", str(realtime), "--features-params", str(REAL_PARAMS),
         "--out", str(tmp_path / "out2")],
    )  # fmt: skip
    assert refused.exit_code == 1
    assert not (tmp_path / "out2").exists()
