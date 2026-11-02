"""
Integration entrypoint for the frozen V3 pipeline (INTEGRATION ONLY).

    python run_v3.py --input-dir <DIR> [--output out.parquet] [--artifact <joblib>]

Pipeline (the frozen module is used verbatim, nothing reimplemented):

    D   = detecteur_v3.build(DIR)          # DIR/detector_input.parquet + DIR/row_labels.parquet
    M   = joblib.load(artifact)            # YOUR_HF_ACCOUNT/mirsaad-models/detecteur_v3.joblib
    out = detecteur_v3.predict(D, M)       # adds proba / alerte, drops tu5_z-NaN rows

Fails loudly on missing artifact keys, missing F1/F2 columns everywhere they
are consumed, or missing input files. Output carries at least
station, time, proba, alerte (plus every D column).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen detecteur_v3 pipeline end-to-end.")
    parser.add_argument("--input-dir", required=True, help="DIR holding detector_input.parquet + row_labels.parquet")
    parser.add_argument("--output", default="v3_predictions.parquet", help="Output .parquet or .csv")
    parser.add_argument("--data", default=str(BACKEND_ROOT / "Data"), help="CIS_DATA tree (ERA5_USCRN + Voisins)")
    parser.add_argument(
        "--artifact",
        default=str(BACKEND_ROOT / "models" / "detecteur_v3.joblib"),
        help="Artifact path (local copy preferred; HF fallback below)",
    )
    args = parser.parse_args()

    # CIS_DATA is resolved at IMPORT time by detecteur_v3 — set it first.
    os.environ.setdefault("CIS_DATA", args.data)
    sys.path.insert(0, str(BACKEND_ROOT))

    # .env BEFORE app.fdi.config is imported — config snapshots the env, and
    # the HF fallback below needs a valid token.
    from dotenv import load_dotenv

    load_dotenv(BACKEND_ROOT / ".env")

    from app.fdi import config

    # Resolve the artifact: local copy first, canonical HF repo as fallback.
    artifact = Path(args.artifact)
    if not artifact.is_file():
        from huggingface_hub import hf_hub_download

        artifact = Path(
            hf_hub_download(
                config.HF_REPO_ID,
                config.FDI_V3_FILENAME,
                token=config.resolve_hf_token(),
            )
        )
        print(f"[artifact] local copy missing — downloaded from HF: {artifact}")

    import joblib
    import pandas as pd

    from app.fdi import detecteur_v3 as v3

    dir_in = Path(args.input_dir)
    for required in ("detector_input.parquet", "row_labels.parquet"):
        if not (dir_in / required).is_file():
            sys.exit(f"[input] missing {required} in {dir_in}")
    data_tree = Path(os.environ["CIS_DATA"])
    for required in (
        data_tree / "ERA5_USCRN" / "era5_uscrn_hourly_2022_2025.csv",
    ):
        if not required.is_file():
            sys.exit(f"[input] missing external data file: {required}")

    print(f"[artifact] {artifact}")
    model = joblib.load(artifact)
    missing = [k for k in ("M1", "M2", "F1", "F2", "thr") if k not in model]
    if missing:
        sys.exit(f"[artifact] missing keys: {missing}")
    thr = model["thr"]
    if not (isinstance(thr, float) and 0.0 <= thr <= 1.0):
        sys.exit(f"[artifact] thr must be a float in [0,1], got {thr!r}")

    print(f"[build] {dir_in} (CIS_DATA={data_tree}) — twin featurisation, be patient")
    di = pd.read_parquet(dir_in / "detector_input.parquet")
    # build() featurises ALL of v3.STATIONS and crashes on stations the frame
    # does not carry — scope the loop to the input's own stations via the
    # wrapper's context manager (restores the module constant on exit).
    from app.fdi.attack_classifier import _stations_present_in

    with _stations_present_in(di):
        D = v3.build(dir_in)

    bad_f1 = [c for c in model["F1"] if c not in D.columns]
    if bad_f1:
        sys.exit(f"[contract] {len(bad_f1)} F1 feature(s) absent from build output; first: {bad_f1[:5]}")

    # F2 names must exist in build output or among the frozen stage-2 products
    # (p_m*/p_max*/p_q25_* are derived from the stage-1 probability inside
    # predict(), which does not return them — so they are validated against
    # stage2_features' own output, not against predict()'s frame).
    probe = v3.stage2_features(D.copy(), 0.5)
    bad_f2 = [c for c in model["F2"] if c not in probe.columns]
    if bad_f2:
        sys.exit(f"[contract] {len(bad_f2)} F2 feature(s) unexplained by stage2_features; first: {bad_f2[:5]}")

    out = v3.predict(D, model)

    keep = ["station", "time", "proba", "alerte"]
    missing_out = [c for c in keep if c not in out.columns]
    if missing_out:
        sys.exit(f"[output] predict() did not produce {missing_out}")

    dest = Path(args.output)
    if dest.suffix.lower() == ".csv":
        out[keep].to_csv(dest, index=False)
    else:
        out[keep].to_parquet(dest, index=False)

    n_alert = int(pd.Series(out["alerte"]).astype(bool).sum())
    print(f"[done] rows={len(out)} alerts={n_alert} thr={thr:.4f} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
