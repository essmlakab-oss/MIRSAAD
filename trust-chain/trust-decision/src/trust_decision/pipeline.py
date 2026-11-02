"""Read gate state, rebuild hourly telemetry, compute features, decide, write outputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from trust_decision.chain import write_log
from trust_decision.table import Evidence, confirm, decide

REQUIRED_FEATURES = ("sp_innov_z_5", "sp_innov_z_10", "sp_depth_gap_z", "sp_cusum_5", "sp_cusum_10")
ABS_RULES = ("sp_innov_z_5", "sp_innov_z_10", "sp_depth_gap_z")
FORBIDDEN_STEMS = {
    "ground_truth",
    "ground_truth_cells",
    "row_labels",
    "cell_labels",
    "attack_manifest",
}


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Scores:
    frame: pd.DataFrame
    manifest: dict[str, Any]
    manifest_sha256: str


def load_scores(manifest_path: Path, expected_manifest_sha256: str | None = None) -> Scores:
    """Hash-pinned loader: refuse a score file whose SHA-256 differs from its manifest."""
    manifest_sha = sha256_file(manifest_path)
    if expected_manifest_sha256 and manifest_sha != expected_manifest_sha256:
        raise ValueError(
            f"{manifest_path.name}: manifest sha256 {manifest_sha} is not the pinned one"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = manifest_path.parent / Path(manifest["file"]).name
    if path.stem in FORBIDDEN_STEMS:
        raise ValueError(f"refusing to read label file: {path.name}")
    actual = sha256_file(path)
    if actual != manifest["sha256"]:
        raise ValueError(
            f"{path.name}: sha256 {actual} does not match manifest {manifest['sha256']}"
        )
    frame = pd.read_parquet(path, columns=["station", "time", "ai_score", "ai_reason"])
    return Scores(frame, manifest, manifest_sha)


def read_gate(database: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every decision and every accepted telemetry row, through a read-only connection."""
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        decisions = pd.read_sql_query(
            """SELECT decision_id, timestamp, sensor_id, station, session_id, sequence, decision,
                      code, reason, warnings, sequence_gap FROM decisions ORDER BY decision_id""",
            connection,
        )
        accepted = pd.read_sql_query(
            "SELECT decision_id, telemetry_json FROM accepted_observations ORDER BY decision_id",
            connection,
        )
    finally:
        connection.close()
    return decisions, accepted


def hourly_frame(accepted: pd.DataFrame) -> pd.DataFrame:
    """Hourly rows from verified telemetry: decision_id, station, time, all measurements."""
    rows = []
    for decision_id, text in zip(accepted["decision_id"], accepted["telemetry_json"], strict=True):
        telemetry = json.loads(text)
        rows.append(
            {
                "decision_id": int(decision_id),
                "station": telemetry["station"],
                "time": telemetry["observation_time"],
                **telemetry["measurements"],
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    times = pd.to_datetime(frame["time"], utc=True)
    frame["time"] = times.dt.tz_localize(None).astype("datetime64[us]")
    return frame


def run_transform(
    frame: pd.DataFrame, params: Path, signal_processing: Path, workdir: Path
) -> pd.DataFrame:
    """Run the signal-processing transform through its CLI, in its own environment."""
    source = workdir / "accepted_telemetry.parquet"
    frame.drop(columns="decision_id").to_parquet(source, index=False)
    output = workdir / "features"
    command = [
        "uv", "run", "--project", str(signal_processing), "signal-processing", "transform",
        "--input", str(source), "--params", str(params.resolve()), "--output", str(output),
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"signal-processing transform failed: {result.stderr.strip()}")
    return pd.read_parquet(output / "signal_features.parquet")


def rule_hits(row: dict[str, Any], rules: dict[str, float], cusum_h: dict[str, float]) -> list[str]:
    hits = [
        f"|{name}| beyond {rules[name]:.2f}"
        for name in ABS_RULES
        if np.isfinite(row[name]) and abs(row[name]) > rules[name]
    ]
    for depth, threshold in cusum_h.items():
        value = row[f"sp_cusum_{depth}"]
        if np.isfinite(value) and value >= threshold:
            hits.append(f"sp_cusum_{depth} at or above h {threshold:.2f}")
    if row["sp_replay_match"]:
        hits.append("sp_replay_match")
    return hits


def build_records(
    decisions: pd.DataFrame,
    frame: pd.DataFrame,
    features: pd.DataFrame | None,
    scores: Scores,
    params: dict[str, Any],
    era5: Scores | None,
) -> list[dict[str, Any]]:
    """One record per gate decision, in decision order."""
    by_decision: dict[int, dict[str, Any]] = {}
    if features is not None and not frame.empty:
        merged = pd.concat(
            [frame[["decision_id"]].reset_index(drop=True), features.reset_index(drop=True)], axis=1
        )
        merged = merged.merge(
            scores.frame[["station", "time", "ai_score"]], on=["station", "time"], how="left"
        )
        if era5 is not None:
            merged = merged.merge(
                era5.frame[["station", "time", "ai_score"]].rename(
                    columns={"ai_score": "era5_score"}
                ),
                on=["station", "time"],
                how="left",
            )
        by_decision = {int(r["decision_id"]): r for r in merged.to_dict("records")}
    threshold = float(scores.manifest["threshold"])
    rules = scores.manifest["rule_thresholds"]
    records = []
    for gate in decisions.to_dict("records"):
        warnings = tuple(json.loads(gate["warnings"]))
        row = by_decision.get(int(gate["decision_id"]))
        hits: list[str] = []
        ai_score = era5_score = None
        time = None
        if row is not None:
            cusum = {
                depth: dp["cusum_h"]
                for depth, dp in params["stations"][row["station"]]["depths"].items()
            }
            hits = rule_hits(row, rules, cusum)
            value = row["ai_score"]
            ai_score = None if pd.isna(value) else float(value)
            if era5 is not None and not pd.isna(row["era5_score"]):
                era5_score = float(row["era5_score"])
            time = row["time"].isoformat() + "Z"
        available = row is not None and all(np.isfinite(row[c]) for c in REQUIRED_FEATURES)
        verdict = decide(
            Evidence(
                gate_code=gate["code"],
                warnings=warnings,
                ai_score=ai_score,
                threshold=threshold,
                rule_hits=tuple(hits),
                features_available=available,
            )
        )
        record: dict[str, Any] = {
            "decision_id": int(gate["decision_id"]),
            "gate_timestamp": gate["timestamp"],
            "sensor_id": gate["sensor_id"],
            "station": gate["station"],
            "session_id": gate["session_id"],
            "sequence": None if pd.isna(gate["sequence"]) else int(gate["sequence"]),
            "time": time,
            "gate_decision": gate["decision"],
            "gate_code": gate["code"],
            "gate_warnings": list(warnings),
            "ai_score": ai_score,
            "rule_hits": hits,
            "decision": verdict.decision,
            "trust_score": round(verdict.trust_score, 6),
            "reason": verdict.reason,
        }
        if era5 is not None:
            second = confirm(verdict, era5_score, float(era5.manifest["threshold"]))
            record.update(
                era5_score=era5_score,
                confirmed_decision=second.decision,
                confirmed_trust_score=round(second.trust_score, 6),
                confirmed_reason=second.reason,
                confirmation_delay_h=int(era5.manifest["decision_delay_h"]),
            )
        records.append(record)
    return records


def write_outputs(records: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(records)
    frame["time"] = pd.to_datetime(frame["time"], utc=True).dt.tz_localize(None)
    frame["time"] = frame["time"].astype("datetime64[us]")
    parquet = out / "trust_decisions.parquet"
    table = pa.Table.from_pandas(frame, preserve_index=False).replace_schema_metadata(None)
    pq.write_table(table, parquet, compression="zstd", compression_level=3)
    with (out / "trust_decisions.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    lines, last = write_log(records, out / "decision_log.jsonl")
    summary: dict[str, Any] = {
        "decisions": len(records),
        "counts": dict(sorted(Counter(r["decision"] for r in records).items())),
        "log_lines": lines,
        "log_last_sha256": last,
    }
    if records and "confirmed_decision" in records[0]:
        summary["confirmed_counts"] = dict(
            sorted(Counter(r["confirmed_decision"] for r in records).items())
        )
    return summary


def run(
    database: Path,
    scores_manifest: Path,
    params_path: Path,
    out: Path,
    signal_processing: Path,
    era5_manifest: Path | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    scores = load_scores(scores_manifest, expected_manifest_sha256)
    era5 = load_scores(era5_manifest) if era5_manifest else None
    params = json.loads(params_path.read_text(encoding="utf-8"))
    decisions, accepted = read_gate(database)
    frame = hourly_frame(accepted)
    notes = []
    features = None
    if not frame.empty:
        duplicated = frame.duplicated(["station", "time"], keep="first")
        if duplicated.any():
            notes.append(f"{int(duplicated.sum())} accepted rows repeat a (station, time)")
        with tempfile.TemporaryDirectory() as tmp:
            unique = frame[~duplicated].reset_index(drop=True)
            features = run_transform(unique, params_path, signal_processing, Path(tmp))
        frame = unique
    records = build_records(decisions, frame, features, scores, params, era5)
    summary = write_outputs(records, out)
    summary.update(
        scores={
            "real_time_sha256": scores.manifest["sha256"],
            "real_time_manifest_sha256": scores.manifest_sha256,
            "decision_delay_h": scores.manifest["decision_delay_h"],
            "attack_set_id": scores.manifest["attack_set_id"],
        },
        notes=notes,
    )
    if era5 is not None:
        summary["scores"]["era5_sha256"] = era5.manifest["sha256"]
        summary["scores"]["era5_decision_delay_h"] = era5.manifest["decision_delay_h"]
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
