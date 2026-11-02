"""Crypto Gate benchmark on real detector-release observations.

Measures in-process verify latency per path, HTTPS round-trip latency and throughput, peak RSS,
envelope overhead, and the absolute cost of signature verification. Records no host name and no
user path. Keys, certificates and the database are disposable and removed afterwards.

Usage: bench_gate.py --release <detector dir> --output <json> [--https-packets 3000]
"""

import argparse
import ctypes
import json
import multiprocessing
import os
import platform
import secrets
import shutil
import socket
import ssl
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from uuid import uuid4

import httpx

import sensorlab.gate.service as gate_service
from sensorlab.clock import SystemClock
from sensorlab.dataset import DetectorDataset, parse_utc
from sensorlab.gate.models import GateConfig
from sensorlab.gate.repository import SQLiteGateRepository
from sensorlab.gate.service import CryptoGate
from sensorlab.gate.tls import development_certificates
from sensorlab.identity import TrustedRegistry, provision, revoke
from sensorlab.registry import STATION_SENSORS
from sensorlab.sensor import VirtualSensor
from sensorlab.signing import SigningService

START = "2025-01-01T01:00:00Z"
REJECT_SAMPLES = 200
ACCEPT_SAMPLES = 1000
PACKAGES = ("cryptography", "fastapi", "httpx", "pydantic", "uvicorn")


def summary(values_s: list[float]) -> dict:
    values = sorted(v * 1000 for v in values_s)
    return {
        "n": len(values),
        "mean_ms": round(statistics.fmean(values), 4),
        "p95_ms": round(values[min(int(0.95 * len(values)), len(values) - 1)], 4),
    }


def serve_gate(arguments: list[str], tls_password: str) -> None:
    """Child process entry. A real process, unlike the launcher a venv python.exe starts."""
    os.environ.pop("SENSORLAB_KEY_PASSWORD", None)
    os.environ["SENSORLAB_TLS_PASSWORD"] = tls_password
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115, PTH123
    from sensorlab.cli import app

    app(arguments)


def peak_rss_mib(process: multiprocessing.Process | None = None) -> float:
    """Peak resident memory of a child, or of this process, with no extra dependency."""
    if os.name == "nt":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        get_current = ctypes.windll.kernel32.GetCurrentProcess
        get_current.restype = ctypes.c_void_p
        handle = int(process.sentinel) if process else get_current()
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if not query(handle, ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return round(counters.PeakWorkingSetSize / 2**20, 1)
    pid = process.pid if process else os.getpid()
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    raise OSError("VmHWM not available")


class Packets:
    """Fresh telemetry from real observations, one session, sequences as a sensor would send."""

    def __init__(self, release: Path, count: int, session_id):
        hours = count // 6 + 2
        start = parse_utc(START)
        selected = DetectorDataset.load(release).select(
            start=start, end=start + timedelta(hours=hours)
        )
        self.observations = iter(selected.observations())
        self.sensors = {s: VirtualSensor(s, session_id) for s in STATION_SENSORS}

    def next(self):
        observation = next(self.observations)
        sensor = self.sensors[observation.station]
        message = sensor.prepare(observation, datetime.now(UTC))
        sensor.commit(message)
        return message


def body(service: SigningService, message) -> bytes:
    return service.sign(message).model_dump_json().encode("utf-8")


def fresh_gate(registry: Path, database: Path, session_id) -> CryptoGate:
    repository = SQLiteGateRepository(database)
    trusted = TrustedRegistry.load(registry)
    now = datetime.now(UTC)
    repository.authorize(session_id, list(trusted.sensors), now, now, now + timedelta(hours=2))
    return CryptoGate(GateConfig(registry=registry, database=database), repository, SystemClock())


def timed(gate: CryptoGate, payload: bytes, expected: str) -> float:
    began = time.perf_counter()
    decision = gate.handle(payload, datetime.now(UTC))
    elapsed = time.perf_counter() - began
    if decision.code != expected:
        raise RuntimeError(f"expected {expected}, gate answered {decision.code}")
    return elapsed


def in_process(release: Path, identity: Path, password: bytes, work: Path) -> dict:
    service = SigningService.unlock(identity, password)
    registry = identity / "registry.json"

    # Accept path with and without signature verification, alternating to cancel disk drift.
    session_a, session_b = uuid4(), uuid4()
    with_sig = fresh_gate(registry, work / "a.sqlite3", session_a)
    without_sig = fresh_gate(registry, work / "b.sqlite3", session_b)
    packets_a = Packets(release, ACCEPT_SAMPLES, session_a)
    packets_b = Packets(release, ACCEPT_SAMPLES, session_b)
    original_verify = gate_service.verify_envelope
    accept, no_signature, ed25519 = [], [], []
    for _ in range(ACCEPT_SAMPLES):
        accept.append(timed(with_sig, body(service, packets_a.next()), "OK"))
        payload = body(service, packets_b.next())
        gate_service.verify_envelope = lambda envelope, _registry: envelope.telemetry
        try:
            no_signature.append(timed(without_sig, payload, "OK"))
        finally:
            gate_service.verify_envelope = original_verify
    trusted = TrustedRegistry.load(registry)
    sample = service.sign(Packets(release, 12, uuid4()).next())
    for _ in range(ACCEPT_SAMPLES):
        began = time.perf_counter()
        original_verify(sample, trusted)
        ed25519.append(time.perf_counter() - began)

    # Reject paths, each built as the gate would really meet it.
    session = uuid4()
    gate = fresh_gate(registry, work / "c.sqlite3", session)
    packets = Packets(release, 2 * REJECT_SAMPLES * 8, session)
    paths: dict[str, list[float]] = {}

    def tampered(message) -> bytes:
        document = json.loads(body(service, message))
        document["telemetry"]["measurements"]["LATITUDE"] += 1.0
        return json.dumps(document).encode()

    def unknown_key(message) -> bytes:
        document = json.loads(body(service, message))
        current = document["key_id"]
        document["key_id"] = next(e.key_id for e in trusted.sensors if e.key_id != current)
        return json.dumps(document).encode()

    foreign_session = Packets(release, REJECT_SAMPLES + 12, uuid4())

    def unauthorized(_message) -> bytes:
        return body(service, foreign_session.next())

    def stale(message) -> bytes:
        return body(
            service, message.model_copy(update={"sent_at": datetime.now(UTC) - timedelta(hours=1)})
        )

    builders = {
        "E_SCHEMA": lambda m: b'{"telemetry": {}, "unexpected": true}',
        "E_UNKNOWN_KEY": unknown_key,
        "E_BAD_SIG": tampered,
        "E_UNAUTHORIZED_SESSION": unauthorized,
        "E_STALE": stale,
    }
    for code, build in builders.items():
        paths[code] = [timed(gate, build(packets.next()), code) for _ in range(REJECT_SAMPLES)]
    replayed = body(service, packets.next())
    timed(gate, replayed, "OK")
    paths["E_REPLAY_SEQ"] = [timed(gate, replayed, "E_REPLAY_SEQ") for _ in range(REJECT_SAMPLES)]

    # Revocation last: it changes the registry this gate reloads on each request.
    message = packets.next()
    held = body(service, message)
    revoke(registry, message.sensor_id)
    paths["E_REVOKED_KEY"] = [timed(gate, held, "E_REVOKED_KEY") for _ in range(REJECT_SAMPLES)]

    accept_summary, baseline = summary(accept), summary(no_signature)
    return {
        "accept": accept_summary,
        "accept_without_signature_verification": baseline,
        "reject": {code: summary(values) for code, values in sorted(paths.items())},
        "ed25519_verify_envelope": summary(ed25519),
        "security_overhead_ms_per_packet": round(
            accept_summary["mean_ms"] - baseline["mean_ms"], 4
        ),
        "peak_rss_mib": peak_rss_mib(),
    }


def https(release: Path, identity: Path, password: bytes, work: Path, count: int) -> dict:
    tls_password = secrets.token_urlsafe(32)
    tls = work / "tls"
    development_certificates(tls, tls_password.encode())
    registry = identity / "registry.json"
    database = work / "https.sqlite3"
    session = uuid4()
    fresh_gate(registry, database, session)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = multiprocessing.Process(
        target=serve_gate,
        args=(
            [
                "gate",
                "serve",
                "--port",
                str(port),
                "--registry",
                str(registry),
                "--database",
                str(database),
                "--cert",
                str(tls / "server.pem"),
                "--key",
                str(tls / "server.private.pem"),
            ],
            tls_password,
        ),
        daemon=True,
    )
    server.start()
    base = f"https://127.0.0.1:{port}"
    service = SigningService.unlock(identity, password)
    packets = Packets(release, count, session)
    try:
        context = ssl.create_default_context(cafile=str(tls / "ca.pem"))
        with httpx.Client(verify=context, trust_env=False, timeout=10) as client:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if client.get(base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)
            latencies, signing = [], []
            for _ in range(count):
                began = time.perf_counter()
                payload = body(service, packets.next())
                signing.append(time.perf_counter() - began)
                began = time.perf_counter()
                response = client.post(
                    base + "/v1/telemetry",
                    content=payload,
                    headers={"content-type": "application/json"},
                )
                latencies.append(time.perf_counter() - began)
                if response.status_code != 202:
                    raise RuntimeError(f"packet refused: {response.json().get('code')}")
        total = sum(latencies)
        result = {
            "packets": count,
            "connection": "one keep-alive TLS connection, sequential",
            "round_trip": summary(latencies),
            "client_build_and_sign": summary(signing),
            "throughput_packets_per_s": round(count / total, 1),
            "server_peak_rss_mib": peak_rss_mib(server),
        }
    finally:
        server.terminate()
        server.join(timeout=15)
    status = SQLiteGateRepository(database).status()
    if status["accepted"] != count or status["outbox_count"] != count or status["rejected"]:
        raise RuntimeError("gate accounting does not match the packets sent")
    return result


def overhead(release: Path, identity: Path, password: bytes) -> dict:
    service = SigningService.unlock(identity, password)
    packets = Packets(release, 60, uuid4())
    signed, unsigned = [], []
    for _ in range(60):
        message = packets.next()
        unsigned.append(len(message.model_dump_json().encode("utf-8")))
        signed.append(len(service.sign(message).model_dump_json().encode("utf-8")))
    return {
        "unsigned_telemetry_mean_bytes": round(statistics.fmean(unsigned), 1),
        "signed_envelope_mean_bytes": round(statistics.fmean(signed), 1),
        "added_bytes_per_envelope": round(statistics.fmean(signed) - statistics.fmean(unsigned), 1),
    }


def environment_record() -> dict:
    return {
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "packages": {name: metadata.version(name) for name in PACKAGES},
        "database": "SQLite WAL, synchronous=FULL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--https-packets", type=int, default=3000)
    args = parser.parse_args()
    if args.https_packets < 3000:
        parser.error("--https-packets must be at least 3000")
    password = secrets.token_urlsafe(32).encode()
    work = Path(tempfile.mkdtemp(prefix="bench-gate-"))
    try:
        identity = work / "identity"
        provision(identity, password)
        https_result = https(args.release, identity, password, work, args.https_packets)
        envelope = overhead(args.release, identity, password)
        in_process_result = in_process(args.release, identity, password, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report = {
        "data": "real detector-release observations from " + START,
        "environment": environment_record(),
        "in_process": in_process_result,
        "https": https_result,
        "envelope": envelope,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
