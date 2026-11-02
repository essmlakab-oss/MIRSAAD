"""Real gate, real signed replay of the 2025 attacked release, three bad packets, real stage.

Needs what a `run_all` leaves behind: the combined export and the evaluation score files. Those are
Git-ignored, so a fresh clone skips this test.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from trust_decision.chain import verify_log
from trust_decision.pipeline import run

REPO = Path(__file__).resolve().parents[2]
CYBER = REPO / "cyber-pipeline"
SCORES = REPO / "evaluation/outputs/realtime/manifest.json"
PARAMS = REPO / "signal-processing/params/frozen_params.json"
DETECTOR_SHA256 = "ff478b38f2f0996b5301a48f93f0a4b0a40b7bc321440c69f8e85ff31c6eb50a"
START, END = "2025-06-01T00:00:00Z", "2025-06-03T00:00:00Z"

sys.path.insert(0, str(REPO / "scripts"))
from run_demo import stop_tree, wait_for_gate  # noqa: E402


def attacked_release() -> Path | None:
    for candidate in sorted((REPO / "attack-engine/datasets/generated").glob("*/**/detector")):
        source = candidate / "detector_input.parquet"
        if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == DETECTOR_SHA256:
            return candidate
    return None


def post(endpoint: str, ca: Path, body: bytes) -> tuple[int, str]:
    context = ssl.create_default_context(cafile=str(ca))
    request = urllib.request.Request(
        endpoint, data=body, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=10) as reply:
            return reply.status, json.load(reply)["code"]
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)["code"]


def test_every_hour_has_one_decision_and_bad_packets_are_quarantined(tmp_path: Path) -> None:
    release = attacked_release()
    venv = CYBER / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if release is None or not SCORES.is_file() or not venv.is_file() or not shutil.which("uv"):
        pytest.skip("needs uv, the cyber-pipeline environment, and a run_all export and scores")

    security = CYBER / ".sensorlab" / f"e2e-{uuid4().hex[:8]}"  # Git-ignored; removed afterwards
    identity, tls, database = security / "identity", security / "tls", security / "gate.sqlite3"
    registry = identity / "registry.json"
    signing_env = {**os.environ, "SENSORLAB_KEY_PASSWORD": secrets.token_urlsafe(32)}
    tls_env = {**os.environ, "SENSORLAB_TLS_PASSWORD": secrets.token_urlsafe(32)}
    tls_env.pop("SENSORLAB_KEY_PASSWORD", None)

    def sensorlab(*args: str, env: dict[str, str] | None = None) -> None:
        subprocess.run(
            [str(venv), "-m", "sensorlab.cli", *args],
            cwd=CYBER, env=env or os.environ.copy(), check=True, capture_output=True,
        )  # fmt: skip

    security.mkdir(parents=True)
    server = None
    try:
        sensorlab("identities", "provision", "--directory", str(identity), env=signing_env)
        sensorlab("gate", "dev-cert", "--directory", str(tls), env=tls_env)
        session = str(uuid4())
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        sensorlab("gate", "authorize", session, "--expires-at", expires,
                  "--database", str(database), "--registry", str(registry))  # fmt: skip
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"https://127.0.0.1:{port}"
        endpoint, ca = base + "/v1/telemetry", tls / "ca.pem"
        server = subprocess.Popen(
            [str(venv), "-m", "sensorlab.cli", "gate", "serve", "--port", str(port),
             "--registry", str(registry), "--database", str(database),
             "--cert", str(tls / "server.pem"), "--key", str(tls / "server.private.pem")],
            cwd=CYBER, env=tls_env,
        )  # fmt: skip
        wait_for_gate(base, ca, server)

        signed = security / "signed.jsonl"
        sensorlab("replay", "--signed", "--identity-dir", str(identity), "--session-id", session,
                  "--speed", "0", "--start", START, "--end", END, "--release", str(release),
                  "--endpoint", endpoint, "--ca-cert", str(ca), "--output", str(signed),
                  env=signing_env)  # fmt: skip
        lines = signed.read_text(encoding="utf-8").splitlines()
        envelopes = [json.loads(line) for line in lines]
        hours = {
            (e["telemetry"]["station"], pd.Timestamp(e["telemetry"]["observation_time"]))
            for e in envelopes
        }

        tampered = json.loads(lines[-1])
        measurements = tampered["telemetry"]["measurements"]
        column = next(c for c in ("SOIL_MOISTURE_5", "T_HR_AVG") if measurements[c] is not None)
        measurements[column] = round(measurements[column] + 0.1, 3)
        assert post(endpoint, ca, json.dumps(tampered).encode()) == (403, "E_BAD_SIG")

        status, code = post(endpoint, ca, lines[0].encode())
        assert code == "E_REPLAY_SEQ" and status >= 400

        # Validly signed before revocation; the registry is re-read on each request.
        sensorlab("identities", "revoke", envelopes[-1]["telemetry"]["sensor_id"],
                  "--registry", str(registry))  # fmt: skip
        assert post(endpoint, ca, lines[-1].encode()) == (403, "E_REVOKED_KEY")
    finally:
        if server is not None:
            stop_tree(server)

    try:
        out = tmp_path / "trust"
        summary = run(database, SCORES, PARAMS, out, REPO / "signal-processing")
        table = pd.read_parquet(out / "trust_decisions.parquet")
    finally:
        shutil.rmtree(security, ignore_errors=True)

    accepted = table[table["gate_decision"] == "ACCEPT"]
    assert len(accepted) == len(envelopes)
    assert not accepted.duplicated(["station", "time"]).any()
    decided = {
        (s, pd.Timestamp(t, tz="UTC"))
        for s, t in zip(accepted["station"], accepted["time"], strict=True)
    }
    assert decided == {(s, t if t.tzinfo else t.tz_localize("UTC")) for s, t in hours}
    assert set(accepted["decision"]) <= {"ACCEPT", "REVIEW", "SUSPECT", "ALERT"}

    bad = table[table["decision"] == "QUARANTINE"]
    assert sorted(bad["gate_code"]) == ["E_BAD_SIG", "E_REPLAY_SEQ", "E_REVOKED_KEY"]
    assert all(c in r for c, r in zip(bad["gate_code"], bad["reason"], strict=True))
    assert bad["time"].isna().all()  # a rejected packet has no trusted observation time
    assert len(table) == len(envelopes) + 3 == summary["decisions"]
    assert verify_log(out / "decision_log.jsonl")[0]
