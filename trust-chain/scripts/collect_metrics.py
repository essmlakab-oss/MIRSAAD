#!/usr/bin/env python3
"""Build results/metrics.json and the root README metrics table from the JSON outputs only."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCES = {
    "gate": "results/gate_benchmark.json",
    "signal": "signal-processing/outputs/bench.json",
    "eval_realtime": "evaluation/results/realtime/causal_detector_results.json",
    "eval_delayed": "evaluation/results/delayed/causal_detector_results.json",
    "demo": "results/demo_summary.json",
}


def load(relative: str) -> dict:
    return json.loads((REPO / relative).read_text(encoding="utf-8"))


def detector(results: dict, name: str, delay_h: int) -> dict:
    m = results["results"]["test"][name]
    return {
        "decision_delay_h": delay_h,
        "precision": m["precision"],
        "recall": m["recall"],
        "f1": m["f1"],
        "false_alarm_rate": m["false_alarm_rate"],
        "pr_auc": m["pr_auc"],
        "per_family": m["per_family"],
    }


def main() -> int:
    data = {key: load(path) for key, path in SOURCES.items()}
    gate, signal, demo = data["gate"], data["signal"], data["demo"]
    delay = demo["confirmation_delay_h"]
    metrics = {
        "sources": SOURCES,
        "gate": {
            "accept_verify_mean_ms": gate["in_process"]["accept"]["mean_ms"],
            "accept_verify_p95_ms": gate["in_process"]["accept"]["p95_ms"],
            "reject_verify": gate["in_process"]["reject"],
            "security_overhead_ms_per_packet": gate["in_process"]["security_overhead_ms_per_packet"],
            "https_packets": gate["https"]["packets"],
            "https_round_trip_mean_ms": gate["https"]["round_trip"]["mean_ms"],
            "https_round_trip_p95_ms": gate["https"]["round_trip"]["p95_ms"],
            "https_throughput_packets_per_s": gate["https"]["throughput_packets_per_s"],
            "server_peak_rss_mib": gate["https"]["server_peak_rss_mib"],
            "added_bytes_per_envelope": gate["envelope"]["added_bytes_per_envelope"],
            "environment": gate["environment"],
        },
        "signal_processing": {
            "per_row_median_us": signal["per_row_us"]["median"],
            "rows_per_second": signal["rows_per_second"],
            "peak_memory_mib": signal["peak_memory_mib_tracemalloc"],
        },
        "detectors": {
            "rules": detector(data["eval_realtime"], "R", 0),
            "model": detector(data["eval_realtime"], "M", 0),
            "model_era5": detector(data["eval_delayed"], "M", delay),
        },
        "demo": {
            "decisions": demo["decisions_total"],
            "decision_counts": demo["decision_counts"],
            "confirmed_decision_counts": demo["confirmed_decision_counts"],
            "quarantined_codes": demo["quarantined_codes"],
            "attacked_hours_accepted_by_gate": demo["attacked_hours_accepted_by_gate"],
            "attacked_hours_flagged_suspect_or_alert": demo["attacked_hours_flagged_suspect_or_alert"],
            "attacked_hours_accepted_with_era5_alarm": demo["attacked_hours_accepted_with_era5_alarm"],
        },
    }
    target = REPO / "results/metrics.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    g, s, d, x = metrics["gate"], metrics["signal_processing"], metrics["detectors"], metrics["demo"]
    rows = [
        ("Gate verify, accept path", f"{g['accept_verify_mean_ms']:.2f} ms mean, {g['accept_verify_p95_ms']:.2f} ms p95"),
        ("Gate security overhead (signature verification)", f"{g['security_overhead_ms_per_packet']:.3f} ms per packet"),
        (f"HTTPS round trip, {g['https_packets']:,} packets", f"{g['https_round_trip_mean_ms']:.2f} ms mean, {g['https_round_trip_p95_ms']:.2f} ms p95"),
        ("HTTPS throughput, one sequential connection", f"{g['https_throughput_packets_per_s']:.0f} packets/s"),
        ("Gate server peak RSS", f"{g['server_peak_rss_mib']:.0f} MiB"),
        ("Bytes added per signed envelope", f"{g['added_bytes_per_envelope']:.0f}"),
        ("Signal features, median per row", f"{s['per_row_median_us']:.2f} µs"),
    ]  # fmt: skip
    for label, key in (
        ("Rules, real-time", "rules"),
        ("Model, real-time", "model"),
        (f"Model + ERA5, {delay} h delayed", "model_era5"),
    ):
        m = d[key]
        rows.append(
            (
                label,
                f"F1 {m['f1']:.3f}, precision {m['precision']:.3f}, recall {m['recall']:.3f}, "
                f"false alarms {100 * m['false_alarm_rate']:.2f} %, PR-AUC {m['pr_auc']:.3f}",
            )
        )
    rows += [
        ("Demo: tampered packets quarantined", str(x["quarantined_codes"].get("E_BAD_SIG", 0))),
        (
            "Demo: attacked hours the gate accepted, flagged SUSPECT or ALERT",
            f"{x['attacked_hours_flagged_suspect_or_alert']} of {x['attacked_hours_accepted_by_gate']}",
        ),
        (
            "Demo: attacked hours left ACCEPT that the ERA5 model would flag",
            f"{x['attacked_hours_accepted_with_era5_alarm']}",
        ),
    ]
    lines = ["| Metric | Value |", "|---|---|", *[f"| {a} | {b} |" for a, b in rows]]
    readme = REPO / "README.md"
    raw = readme.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    block = newline.join(["<!-- METRICS:START -->", *lines, "<!-- METRICS:END -->"])
    raw, count = re.subn(
        r"<!-- METRICS:START -->.*?<!-- METRICS:END -->", lambda _: block, raw, flags=re.S
    )
    if count != 1:
        raise SystemExit("metrics marker not found exactly once in README.md")
    readme.write_bytes(raw.encode("utf-8"))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
