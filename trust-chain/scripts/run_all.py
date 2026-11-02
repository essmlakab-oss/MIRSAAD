#!/usr/bin/env python3
"""The whole pipeline from a fresh clone. Standard library only; any failing step stops the run."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "attack-engine"
CYBER = REPO / "cyber-pipeline"
SIGNAL = REPO / "signal-processing"
EVALUATION = REPO / "evaluation"
TRUST = REPO / "trust-decision"
PROJECTS = (ENGINE, CYBER, SIGNAL, EVALUATION, TRUST)
GENERATED = ENGINE / "datasets/generated"
ERA5 = REPO / "era5/era5_uscrn_hourly_2022_2025.csv"
DETECTOR_SHA256 = "ff478b38f2f0996b5301a48f93f0a4b0a40b7bc321440c69f8e85ff31c6eb50a"
FEATURES_SHA256 = "6b636698dee2ef66358975b94f3a00c0e4f3ceadbbc87f36a5843fb58c7cfdc3"
ATTACK_SET = "combined-seed14-ff478b38"

steps: list[dict] = []
warnings: list[str] = []


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _public(argument: str) -> str:
    """Step label for the report: no absolute path may reach a committed file."""
    if argument == sys.executable:
        return "python"
    return argument.replace(str(REPO) + os.sep, "").replace("\\", "/")


def run(cwd: Path, *command: object) -> None:
    text = [str(c) for c in command]
    print(f"\n=== [{cwd.name}] {' '.join(text)}", flush=True)
    began = time.monotonic()
    result = subprocess.run(text, cwd=cwd, check=False)
    steps.append(
        {
            "project": cwd.name,
            "step": " ".join(_public(t) for t in text[:5]),
            "seconds": round(time.monotonic() - began, 1),
        }
    )
    if result.returncode:
        raise SystemExit(f"FAILED ({result.returncode}) in {cwd.name}: {' '.join(text)}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def export_campaign() -> Path:
    before = set(GENERATED.iterdir()) if GENERATED.is_dir() else set()
    run(ENGINE, "uv", "run", "attack-engine", "export",
        "--input", "../USCRN_livraison/data/uscrn_clean.parquet",
        "--config", "configs/dataset.yaml",
        "--attack-config", "configs/campaigns/combined/scheduling.yaml",
        "--attack-params", "configs/campaigns/combined/attacks.yaml",
        "--output", "datasets/generated")  # fmt: skip
    created = [p for p in set(GENERATED.iterdir()) - before if p.is_dir()]
    require(len(created) == 1, f"expected one new export, found {len(created)}")
    bundle = created[0]
    run(REPO, sys.executable, "scripts/anonymize_paths.py", bundle)
    digest = sha256(bundle / "detector/detector_input.parquet")
    require(digest == DETECTOR_SHA256, f"detector_input.parquet sha256 {digest}")
    print(f"detector_input.parquet sha256 OK: {digest}")
    return bundle


def signal_processing(detector: Path) -> None:
    source = detector / "detector_input.parquet"
    base_params = SIGNAL / "outputs/run/params_no_era5.json"
    base_params.unlink(missing_ok=True)
    refit = SIGNAL / "outputs/run/params_era5.json"
    refit.unlink(missing_ok=True)
    frozen = SIGNAL / "params/frozen_params.json"
    run(SIGNAL, "uv", "run", "signal-processing", "fit", "--input", source, "--output", base_params)
    run(SIGNAL, "uv", "run", "signal-processing", "fit", "--input", source, "--output", refit,
        "--era5", ERA5)  # fmt: skip
    if sha256(refit) != sha256(frozen):
        warnings.append("refit params differ from the committed ones; committed params were used")
    run(SIGNAL, "uv", "run", "signal-processing", "transform", "--input", source,
        "--params", frozen, "--output", "outputs/combined")  # fmt: skip
    run(SIGNAL, "uv", "run", "signal-processing", "transform", "--input", source,
        "--params", frozen, "--output", "outputs/combined_era5", "--era5", ERA5)  # fmt: skip
    digest = sha256(SIGNAL / "outputs/combined/signal_features.parquet")
    if digest != FEATURES_SHA256:
        warnings.append(f"no-ERA5 signal_features.parquet sha256 {digest} differs from reference")
    run(SIGNAL, "uv", "run", "python", "scripts/era5_leak_check.py", "--input", source,
        "--era5", ERA5, "--params", frozen, "--output", "outputs/report/era5_leak_check.json")  # fmt: skip
    run(SIGNAL, "uv", "run", "signal-processing", "bench", "--input", source,
        "--params", frozen, "--output", "outputs/bench.json")  # fmt: skip


def evaluate(bundle: Path) -> None:
    frozen = SIGNAL / "params/frozen_params.json"
    modes = (
        ("realtime", "combined", "causal_gbm", "real-time", 0),
        ("delayed", "combined_era5", "causal_gbm_era5", "delayed-confirmation", 120),
    )
    sums = EVALUATION / "models/models.sha256"
    previous = sums.read_text(encoding="utf-8") if sums.is_file() else ""
    for folder, features, model, _, _ in modes:
        run(EVALUATION, "uv", "run", "python", "causal_detector_eval.py", "evaluate",
            "--features", SIGNAL / "outputs" / features / "signal_features.parquet",
            "--truth", bundle / "ground_truth.parquet", "--params", frozen,
            "--out", f"results/{folder}", "--model-out", f"models/{model}.joblib")  # fmt: skip
    current = "".join(
        f"{sha256(EVALUATION / 'models' / f'{model}.joblib')}  {model}.joblib\n"
        for _, _, model, _, _ in modes
    )
    if previous and previous.replace("\r\n", "\n") != current:
        warnings.append("retrained models differ from the pinned hashes; models.sha256 was updated")
    sums.parent.mkdir(exist_ok=True)
    sums.write_bytes(current.encode("utf-8"))
    for folder, features, model, mode, delay in modes:
        run(EVALUATION, "uv", "run", "python", "causal_detector_eval.py", "score",
            "--features", SIGNAL / "outputs" / features / "signal_features.parquet",
            "--model", f"models/{model}.joblib", "--sums", "models/models.sha256",
            "--out", f"outputs/{folder}", "--mode", mode, "--attack-set-id", ATTACK_SET,
            "--decision-delay-h", delay)  # fmt: skip
    run(EVALUATION, "uv", "run", "python", "make_results_table.py")


def main() -> int:
    began = time.monotonic()
    for project in PROJECTS:
        run(project, "uv", "sync", "--locked", "--all-groups")
    bundle = export_campaign()
    detector = bundle / "detector"
    signal_processing(detector)
    evaluate(bundle)
    run(REPO, sys.executable, "scripts/run_demo.py",
        "--detector-dir", detector, "--truth", bundle / "ground_truth.parquet")  # fmt: skip
    run(CYBER, "uv", "run", "python", "scripts/bench_gate.py", "--release", detector,
        "--output", REPO / "results/gate_benchmark.json")  # fmt: skip
    run(REPO, sys.executable, "scripts/collect_metrics.py")
    report = {
        "wall_seconds": round(time.monotonic() - began, 1),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "detector_input_sha256": DETECTOR_SHA256,
        "warnings": warnings,
        "steps": steps,
    }
    (REPO / "results/run_all.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(f"\nrun_all finished in {report['wall_seconds']} s; warnings: {warnings or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
