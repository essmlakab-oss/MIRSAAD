"""HTTPS end-to-end benchmark of the Crypto Gate, per station, over keep-alive connections.

For each run and each of the six stations: open one keep-alive TLS connection, send a short
unmeasured warm-up, then measure --records signed records. Every record carries the current time,
a new increasing sequence and a valid session, built by SensorLab's own sensor and signing code.

Against a gate that is already running (the session must be authorized), with
SENSORLAB_KEY_PASSWORD set:

    uv run --with psutil python scripts/bench_gate_e2e.py
        --endpoint https://127.0.0.1:8443/v1/telemetry --ca-cert <ca.pem> --identity-dir <dir>
        --session-id <uuid> --gate-pid <pid> --database <gate.sqlite3>
        --release <detector dir> --output <json>

Or let the script host a disposable gate and also measure the baseline (same HTTPS and storage,
signature verification off), with a generated password that is never written:

    uv run --with psutil python scripts/bench_gate_e2e.py --self-host
        --release <dir> --output <json>

The JSON records no host name and no user path.
"""

import argparse
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
import threading
import time
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psutil

import sensorlab.gate.service as gate_service
from sensorlab.dataset import DetectorDataset, parse_utc
from sensorlab.gate.repository import SQLiteGateRepository
from sensorlab.gate.tls import development_certificates
from sensorlab.identity import TrustedRegistry, provision
from sensorlab.registry import STATION_SENSORS
from sensorlab.sensor import VirtualSensor
from sensorlab.signing import SigningService

START, END = "2022-01-01T01:00:00Z", "2026-01-01T01:00:00Z"
PACKAGES = ("cryptography", "fastapi", "httpx", "pydantic", "uvicorn", "psutil")
REQUIRED_WITHOUT_SELF_HOST = (
    "endpoint",
    "ca_cert",
    "identity_dir",
    "session_id",
    "gate_pid",
    "database",
)


def percentile(ordered: list[float], fraction: float) -> float:
    return ordered[min(int(fraction * len(ordered)), len(ordered) - 1)]


def latency_summary(values_s: list[float]) -> dict:
    ordered = sorted(v * 1000 for v in values_s)
    return {
        "requests": len(ordered),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "p50_ms": round(percentile(ordered, 0.50), 3),
        "p95_ms": round(percentile(ordered, 0.95), 3),
        "p99_ms": round(percentile(ordered, 0.99), 3),
        "throughput_requests_per_s": round(len(ordered) / sum(values_s), 1),
    }


class MemoryWatch:
    """Peak RSS of the gate process: OS peak where available, plus sampling."""

    def __init__(self, pid: int):
        self.process = psutil.Process(pid)
        self.peak = 0
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _read(self) -> None:
        info = self.process.memory_info()
        self.peak = max(self.peak, info.rss, getattr(info, "peak_wset", 0))

    def _loop(self) -> None:
        while not self.stopping.wait(0.5):
            self._read()

    def __enter__(self):
        self._read()
        self.baseline_mib = round(self.process.memory_info().rss / 2**20, 1)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self._read()
        self.stopping.set()
        self.thread.join()


def load_observations(release: Path, needed: int) -> dict[str, list]:
    selected = DetectorDataset.load(release).select(start=parse_utc(START), end=parse_utc(END))
    by_station: dict[str, list] = {station: [] for station in STATION_SENSORS}
    for observation in selected.observations():
        by_station[observation.station].append(observation)
    for station, rows in by_station.items():
        if len(rows) < needed:
            raise ValueError(f"{station}: {len(rows)} observations, {needed} needed")
    return by_station


def run_benchmark(args, *, identity: Path, session: UUID, endpoint: str, ca: Path, pid: int,
                  database: Path, password: bytes, runs: int) -> dict:  # fmt: skip
    needed = runs * (args.records + args.warmup)
    observations = load_observations(args.release, needed)
    positions = dict.fromkeys(STATION_SENSORS, 0)
    sensors = {station: VirtualSensor(station, session) for station in STATION_SENSORS}
    service = SigningService.unlock(identity, password)
    context = ssl.create_default_context(cafile=str(ca))
    sent = accepted = 0
    raw_bytes, envelope_bytes = [], []

    def send(client: httpx.Client, station: str, connections: set) -> float:
        nonlocal sent, accepted
        observation = observations[station][positions[station]]
        positions[station] += 1
        sensor = sensors[station]
        message = sensor.prepare(observation, datetime.now(UTC))
        sensor.commit(message)
        payload = service.sign(message).model_dump_json().encode("utf-8")
        raw_bytes.append(len(message.model_dump_json().encode("utf-8")))
        envelope_bytes.append(len(payload))
        began = time.perf_counter()
        response = client.post(
            endpoint, content=payload, headers={"content-type": "application/json"}
        )
        elapsed = time.perf_counter() - began
        sent += 1
        connections.add(response.extensions.get("network_stream"))
        if response.status_code == 202:
            accepted += 1
        else:
            raise RuntimeError(f"{station}: refused with {response.json().get('code')}")
        return elapsed

    results, everything = [], []
    with MemoryWatch(pid) as memory:
        for run_number in range(1, runs + 1):
            run_latencies, per_station = [], {}
            run_began = time.perf_counter()
            for station in STATION_SENSORS:
                connections: set = set()
                with httpx.Client(
                    verify=context, trust_env=False, timeout=30,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                ) as client:  # fmt: skip
                    for _ in range(args.warmup):
                        send(client, station, connections)
                    latencies = [send(client, station, connections) for _ in range(args.records)]
                if len(connections) != 1:
                    raise RuntimeError(f"{station}: used {len(connections)} TLS connections")
                per_station[station] = latency_summary(latencies)
                run_latencies += latencies
            results.append(
                {
                    "run": run_number,
                    "overall": latency_summary(run_latencies),
                    "wall_seconds_including_signing_and_warmup": round(
                        time.perf_counter() - run_began, 1
                    ),
                    "per_station": per_station,
                }
            )
            everything += run_latencies
            print(f"run {run_number}: {results[-1]['overall']}", flush=True)

    status = SQLiteGateRepository(database).status()
    return {
        "stations": len(STATION_SENSORS),
        "runs": runs,
        "records_per_station_per_run": args.records,
        "warmup_per_connection_not_measured": args.warmup,
        "connection": "one keep-alive TLS connection per station per run, sequential, loopback",
        "latency_measures": "client.post round trip; signing happens before the timer starts",
        "overall": latency_summary(everything),
        "by_run": results,
        "sent_including_warmup": sent,
        "accepted_http_202": accepted,
        "accepted_percent": round(100 * accepted / sent, 3),
        "gate_status": {
            "accepted": status["accepted"],
            "rejected": status["rejected"],
            "outbox_count": status["outbox_count"],
            "codes": status["codes"],
        },
        "gate_process": {
            "rss_at_start_mib": memory.baseline_mib,
            "peak_rss_mib": round(memory.peak / 2**20, 1),
        },
        "envelope": {
            "raw_record_mean_bytes": round(statistics.fmean(raw_bytes), 1),
            "signed_envelope_mean_bytes": round(statistics.fmean(envelope_bytes), 1),
            "added_bytes_per_envelope": round(
                statistics.fmean(envelope_bytes) - statistics.fmean(raw_bytes), 1
            ),
            "scope": "JSON body only; HTTP headers and TLS records are not counted",
        },
    }


def serve(arguments: list[str], tls_password: str, signature_check: bool) -> None:
    """Child process entry: a real process, so its PID is the gate's own."""
    os.environ.pop("SENSORLAB_KEY_PASSWORD", None)
    os.environ["SENSORLAB_TLS_PASSWORD"] = tls_password
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115, PTH123
    if not signature_check:
        gate_service.verify_envelope = lambda envelope, _registry: envelope.telemetry
    from sensorlab.cli import app

    app(arguments)


def hosted(args, work: Path, identity: Path, password: bytes, *, signature_check: bool, runs: int):
    tls_password = secrets.token_urlsafe(32)
    tls = work / "tls"
    if not tls.exists():
        development_certificates(tls, tls_password.encode())
        (work / "tls.password").write_text(tls_password, encoding="utf-8")
    tls_password = (work / "tls.password").read_text(encoding="utf-8")
    registry = identity / "registry.json"
    database = work / f"gate-{uuid4().hex[:8]}.sqlite3"
    session = uuid4()
    now = datetime.now(UTC)
    SQLiteGateRepository(database).authorize(
        session, list(TrustedRegistry.load(registry).sensors), now, now, now + timedelta(hours=6)
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = multiprocessing.Process(
        target=serve,
        args=(
            ["gate", "serve", "--port", str(port), "--registry", str(registry),
             "--database", str(database), "--cert", str(tls / "server.pem"),
             "--key", str(tls / "server.private.pem")],
            tls_password,
            signature_check,
        ),
        daemon=True,
    )  # fmt: skip
    server.start()
    base = f"https://127.0.0.1:{port}"
    try:
        context = ssl.create_default_context(cafile=str(tls / "ca.pem"))
        with httpx.Client(verify=context, trust_env=False, timeout=5) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    if client.get(base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.2)
        return run_benchmark(
            args, identity=identity, session=session, endpoint=base + "/v1/telemetry",
            ca=tls / "ca.pem", pid=server.pid, database=database, password=password, runs=runs,
        )  # fmt: skip
    finally:
        server.terminate()
        server.join(timeout=15)


def environment() -> dict:
    memory = psutil.virtual_memory().total
    return {
        "machine": platform.machine(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "ram_gib": round(memory / 2**30, 1),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "packages": {name: metadata.version(name) for name in PACKAGES},
        "database": "SQLite WAL, synchronous=FULL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--records", type=int, default=3000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--self-host", action="store_true")
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--endpoint")
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument("--identity-dir", type=Path)
    parser.add_argument("--session-id", type=UUID)
    parser.add_argument("--gate-pid", type=int)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()

    report = {"data": f"detector-release observations from {START}", "environment": environment()}
    if args.self_host:
        password = secrets.token_urlsafe(32).encode()
        work = Path(tempfile.mkdtemp(prefix="bench-gate-e2e-"))
        try:
            identity = work / "identity"
            provision(identity, password)
            secured = hosted(args, work, identity, password, signature_check=True, runs=args.runs)
            report["gate"] = secured
            if not args.no_baseline:
                print("baseline: signature verification off", flush=True)
                baseline = hosted(args, work, identity, password, signature_check=False, runs=1)
                first = secured["by_run"][0]["overall"]["mean_ms"]
                second = baseline["overall"]["mean_ms"]
                report["baseline_without_signature_verification"] = {
                    "what_is_removed": "Ed25519 verification only; HTTPS, schema, key, session, "
                    "sequence, freshness and SQLite storage all stay on",
                    "overall": baseline["overall"],
                    "accepted_percent": baseline["accepted_percent"],
                    "compared_with": "run 1 of the gate, same fresh-database starting point",
                    "added_mean_latency_ms": round(first - second, 3),
                }
        finally:
            shutil.rmtree(work, ignore_errors=True)
    else:
        missing = [
            name
            for name in REQUIRED_WITHOUT_SELF_HOST
            if getattr(args, name) is None
        ]
        if missing:
            parser.error(f"without --self-host these are required: {missing}")
        password = os.environ["SENSORLAB_KEY_PASSWORD"].encode()
        report["gate"] = run_benchmark(
            args, identity=args.identity_dir, session=args.session_id, endpoint=args.endpoint,
            ca=args.ca_cert, pid=args.gate_pid, database=args.database, password=password,
            runs=args.runs,
        )  # fmt: skip
    gate = report["gate"]
    if gate["accepted_percent"] != 100.0 or gate["gate_status"]["rejected"]:
        print("NOT ALL RECORDS WERE ACCEPTED", file=sys.stderr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in gate.items() if k != "by_run"}, indent=2))
    return 0 if gate["accepted_percent"] == 100.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
