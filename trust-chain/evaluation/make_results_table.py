"""Fill the results tables of evaluation/README.md from the JSON outputs. Nothing is typed by hand."""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAMILIES = ("spike", "bias", "drift", "replay", "stealth")


def main():
    realtime = json.loads((HERE / "results/realtime/causal_detector_results.json").read_text("utf-8"))
    delayed = json.loads((HERE / "results/delayed/causal_detector_results.json").read_text("utf-8"))
    rows = [
        ("Rules", "real-time", realtime["results"]["test"]["R"]),
        ("Model", "real-time", realtime["results"]["test"]["M"]),
        ("Model + ERA5", "delayed confirmation", delayed["results"]["test"]["M"]),
    ]
    main_table = [
        "| Detector | Mode | Precision | Recall | F1 | False-alarm rate | PR-AUC |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, mode, m in rows:
        main_table.append(
            f"| {name} | {mode} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} "
            f"| {100 * m['false_alarm_rate']:.2f} % | {m['pr_auc']:.3f} |"
        )
    family_table = [
        "| Detector | " + " | ".join(f.capitalize() for f in FAMILIES) + " |",
        "|---|" + "---:|" * len(FAMILIES),
    ]
    for name, _, m in rows:
        cells = []
        for f in FAMILIES:
            p = m["per_family"][f]
            detected = round(p["events_detected"] * p["n_events"])
            cells.append(f"{p['hourly_recall']:.3f} / {detected} of {p['n_events']}")
        family_table.append(f"| {name} | " + " | ".join(cells) + " |")
    test = realtime["results"]["test"]
    facts = [
        f"Test rows scored: {test['rows']:,}, of which {test['attacked_rows']:,} attacked.",
    ]

    path = HERE / "README.md"
    text = path.read_bytes().decode("utf-8")
    for marker, lines in (("MAIN", main_table), ("FAMILY", family_table), ("FACTS", facts)):
        block = f"<!-- {marker}:START -->\n" + "\n".join(lines) + f"\n<!-- {marker}:END -->"
        text, count = re.subn(rf"<!-- {marker}:START -->.*?<!-- {marker}:END -->", block, text, flags=re.S)
        if count != 1:
            raise SystemExit(f"marker {marker} not found exactly once in README.md")
    path.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
