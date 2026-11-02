"""Prove that the committed ERA5 parameters use no data after the train partition.

Refits the ERA5 block on inputs from which every row measured from 2024 onwards has been
physically removed, then requires exact equality with the block in the frozen params.
Reads no labels. Writes a small JSON report.

Usage: era5_leak_check.py --input <parquet> --era5 <csv> --params <json> --output <json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from signal_processing.era5 import fit_era5, read_era5
from signal_processing.io import dumps_json, read_detector_input, sha256_file
from signal_processing.params import FrozenParams
from signal_processing.partition import DEFAULT_RULES

CUT = pd.Timestamp("2024-01-01")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--era5", type=Path, required=True)
    parser.add_argument("--params", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    params = FrozenParams.model_validate_json(args.params.read_bytes())
    if params.era5 is None:
        raise SystemExit("params hold no ERA5 block")
    frame = read_detector_input(args.input)
    era5 = read_era5(args.era5)
    frame_train = frame[frame["time"] - pd.Timedelta(hours=1) < CUT].reset_index(drop=True)
    era5_train = era5[era5["time"] - pd.Timedelta(hours=1) < CUT].reset_index(drop=True)
    refit = fit_era5(
        frame_train,
        era5_train,
        params.era5.file,
        params.era5.sha256,
        DEFAULT_RULES,
        params.constants.huber_c,
        params.constants.scale_floor,
    )
    identical = refit.model_dump(mode="json") == params.era5.model_dump(mode="json")
    report = {
        "check": "ERA5 block refit with all rows measured from 2024 onwards removed",
        "params_sha256": sha256_file(args.params),
        "era5_sha256": params.era5.sha256,
        "detector_rows_kept": len(frame_train),
        "era5_rows_kept": len(era5_train),
        "latest_detector_time_kept": str(frame_train["time"].max()),
        "latest_era5_time_kept": str(era5_train["time"].max()),
        "identical_to_frozen_params": identical,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(dumps_json(report))
    print(json.dumps(report, indent=2))
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
