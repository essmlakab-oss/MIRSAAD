#!/usr/bin/env python3
"""End-to-end demo: signed replay through the Crypto Gate, one tampered packet, trust decisions.

Usage: run_demo.py --detector-dir <combined/detector> --truth <combined/ground_truth.parquet>

Standard library only; each step runs in its own project's environment. Keys, certificates and
the gate database go to a disposable Git-ignored directory. Passwords are random, held in child
environments only, and never printed.

The window was fixed once from attack_manifest.csv as the 7-day test window holding the most
attack families, using event counts only and no detector output.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
CYBER = REPO / "cyber-pipeline"
START, END = "2025-06-01T00:00:00Z", "2025-06-08T00:00:00Z"


def uv(project: Path, *args: str, env: dict[str, str] | None = None, capture: bool = False):
    command = ["uv", "run", "--project", str(project), *args]
    print(f"$ {' '.join(a for a in ('uv run', project.name, *args))}", flush=True)
    return subprocess.run(
        command, cwd=project, env=env or os.environ.copy(), check=True,
        capture_output=capture, text=True,
    )  # fmt: skip


def wait_for_gate(base: str, ca: Path, process: subprocess.Popen, seconds: float = 60) -> None:
    context = ssl.create_default_context(cafile=str(ca))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Crypto Gate stopped during startup")
        try:
            with urllib.request.urlopen(base + "/health", context=context, timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError("Crypto Gate did not start in time")


def stop_tree(process: subprocess.Popen) -> None:
    """Stop the server and any interpreter child; a venv python.exe on Windows is a launcher."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        process.terminate()
    process.wait(timeout=20)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector-dir", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    args = parser.parse_args()
    release, truth = args.detector_dir.resolve(), args.truth.resolve()

    security = CYBER / ".sensorlab" / f"demo-{uuid4().hex[:8]}"
    out = REPO / "results" / "demo"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    security.mkdir(parents=True)
    identity, tls, database = security / "identity", security / "tls", security / "gate.sqlite3"
    signing_env = {**os.environ, "SENSORLAB_KEY_PASSWORD": secrets.token_urlsafe(32)}
    tls_env = {**os.environ, "SENSORLAB_TLS_PASSWORD": secrets.token_urlsafe(32)}
    tls_env.pop("SENSORLAB_KEY_PASSWORD", None)

    uv(CYBER, "sensorlab", "identities", "provision", "--directory", str(identity), env=signing_env)
    uv(CYBER, "sensorlab", "gate", "dev-cert", "--directory", str(tls), env=tls_env)
    session = str(uuid4())
    expires = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    registry = identity / "registry.json"
    uv(CYBER, "sensorlab", "gate", "authorize", session, "--expires-at", expires,
       "--database", str(database), "--registry", str(registry))  # fmt: skip

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"https://127.0.0.1:{port}"
    print(f"$ sensorlab gate serve --port {port} ...", flush=True)
    # The venv interpreter directly, so stop_tree reaches the server and not a `uv run` wrapper.
    venv = CYBER / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    server = subprocess.Popen(
        [str(venv), "-m", "sensorlab.cli", "gate", "serve", "--port", str(port),
         "--registry", str(registry), "--database", str(database),
         "--cert", str(tls / "server.pem"), "--key", str(tls / "server.private.pem")],
        cwd=CYBER, env=tls_env,
    )  # fmt: skip
    try:
        wait_for_gate(base, tls / "ca.pem", server)
        signed = security / "signed.jsonl"
        uv(CYBER, "sensorlab", "replay", "--signed", "--identity-dir", str(identity),
           "--session-id", session, "--speed", "0", "--start", START, "--end", END,
           "--release", str(release), "--endpoint", base + "/v1/telemetry",
           "--ca-cert", str(tls / "ca.pem"), "--output", str(signed), env=signing_env)  # fmt: skip
        tamper = uv(CYBER, "python", "scripts/tamper_packet.py", "--signed", str(signed),
                    "--endpoint", base + "/v1/telemetry", "--ca-cert", str(tls / "ca.pem"),
                    capture=True)  # fmt: skip
        print(tamper.stdout, flush=True)
        status = uv(CYBER, "sensorlab", "gate", "status", "--database", str(database), capture=True)
    finally:
        stop_tree(server)
    gate_status = json.loads(status.stdout)
    gate_summary = {
        "accepted": gate_status["accepted"],
        "rejected": gate_status["rejected"],
        "codes": gate_status["codes"],
        "outbox_count": gate_status["outbox_count"],
        "tampered_packet": json.loads(tamper.stdout),
    }
    (out / "gate_summary.json").write_text(json.dumps(gate_summary, indent=2) + "\n", "utf-8")

    evaluation = REPO / "evaluation"
    trust = REPO / "trust-decision"
    uv(trust, "trust-decision", "run", "--db", str(database),
       "--scores", str(evaluation / "outputs/realtime/manifest.json"),
       "--era5-scores", str(evaluation / "outputs/delayed/manifest.json"),
       "--features-params", str(REPO / "signal-processing/params/frozen_params.json"),
       "--out", str(out / "trust"))  # fmt: skip
    uv(trust, "trust-decision", "verify-log", str(out / "trust/decision_log.jsonl"))
    uv(evaluation, "python", "demo_summary.py", "--trust", str(out / "trust/trust_decisions.parquet"),
       "--truth", str(truth), "--gate-summary", str(out / "gate_summary.json"),
       "--era5-manifest", str(evaluation / "outputs/delayed/manifest.json"),
       "--out", str(REPO / "results/demo_summary.json"))  # fmt: skip
    shutil.rmtree(security, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
