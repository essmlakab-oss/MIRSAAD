"""Print and save the demo summary. It reads test labels for this purpose only.

Nothing here feeds back into a model, threshold or decision: trust_decisions.parquet already
exists when this runs, and the output is a report. Exits 1 if the demo's checks fail.

Usage: demo_summary.py --trust <trust_decisions.parquet> --truth <ground_truth.parquet>
           --gate-summary <json> --out <json>
"""

import argparse
import json
from pathlib import Path

import pandas as pd

import causal_detector_eval as ev


def counts(series):
    return {str(k): int(v) for k, v in series.value_counts().sort_index().items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trust", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--gate-summary", required=True)
    ap.add_argument("--era5-manifest", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    trust = pd.read_parquet(a.trust)
    accepted = trust[trust["time"].notna()]
    truth = ev.read_truth(a.truth, "test")
    joined = accepted.merge(truth, on=["station", "time"], how="left", validate="1:1")
    if joined["is_attacked"].isna().any():
        raise SystemExit("accepted telemetry outside the test partition")
    joined["is_attacked"] = joined["is_attacked"].astype(bool)
    attacked = joined[joined["is_attacked"]]
    clean = joined[~joined["is_attacked"]]
    quarantined = trust[trust["decision"] == "QUARANTINE"]
    flagged = attacked["decision"].isin(["SUSPECT", "ALERT"])
    era5_threshold = json.loads(Path(a.era5_manifest).read_text("utf-8"))["threshold"]
    missed = attacked[(attacked["decision"] == "ACCEPT") & (attacked["era5_score"] > era5_threshold)]

    report = {
        "window": {"first": str(accepted["time"].min()), "last": str(accepted["time"].max())},
        "stations": int(accepted["station"].nunique()),
        "gate": json.loads(Path(a.gate_summary).read_text("utf-8")),
        "decisions_total": len(trust),
        "decision_counts": counts(trust["decision"]),
        "confirmed_decision_counts": counts(trust["confirmed_decision"]),
        "quarantined_codes": counts(quarantined["gate_code"]),
        "attacked_hours_accepted_by_gate": len(attacked),
        "attacked_hours_flagged_suspect_or_alert": int(flagged.sum()),
        "attacked_hours_accepted_with_era5_alarm": len(missed),
        "attacked_hours": {
            "decision": counts(attacked["decision"]),
            "confirmed_decision": counts(attacked["confirmed_decision"]),
        },
        "clean_hours": {
            "decision": counts(clean["decision"]),
            "confirmed_decision": counts(clean["confirmed_decision"]),
        },
        "attacked_by_family": {
            family: {
                "hours": len(group),
                "events": int(group["attack_id"].nunique()),
                "decision": counts(group["decision"]),
                "confirmed_decision": counts(group["confirmed_decision"]),
            }
            for family, group in attacked.groupby("attack_type")
        },
        "confirmation_delay_h": int(trust["confirmation_delay_h"].max()),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: report[k] for k in report if k != "attacked_by_family"}, indent=2))
    print("\nDecisions on attacked hours accepted by the gate (validly signed false data):")
    columns = ["station", "time", "attack_type", "ai_score", "decision", "trust_score", "confirmed_decision"]
    with pd.option_context("display.max_rows", None, "display.width", 140):
        print(attacked.sort_values(["time", "station"])[columns].to_string(index=False))

    if report["quarantined_codes"].get("E_BAD_SIG", 0) < 1:
        raise SystemExit("demo check failed: the tampered packet was not quarantined")
    if not flagged.any():
        raise SystemExit("demo check failed: no attacked hour accepted by the gate was flagged")


if __name__ == "__main__":
    main()
