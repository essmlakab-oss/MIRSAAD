# trust-decision

Stage 2: the consumer of the Crypto Gate's `accepted_observations` outbox. For every gate decision it
produces a verdict, a reason and a trust score in [0, 1]. It is its own uv project and imports nothing
from the others; signal features come from running the `signal-processing` CLI as a subprocess.

It reads no labels. It opens the gate database read-only.

## Decision table

First match wins. `gate rejected` covers any `E_*` code.

| Condition | Decision | Trust score |
|---|---|---:|
| Gate rejected | `QUARANTINE` | 0.00 |
| Detector alarm and a rule feature beyond its threshold | `ALERT` | 0.05 |
| Detector alarm only | `SUSPECT` | 0.20 |
| Rule only | `REVIEW` | 0.60 |
| Features unavailable (insufficient history or NaN) | `REVIEW` | 0.50 |
| Otherwise | `ACCEPT` | 1 − score |

Rule features are `sp_cusum_{5,10}` at or above the station's `h` from the frozen params,
`|sp_innov_z_{5,10}|` or `|sp_depth_gap_z|` beyond the validation thresholds in the score manifest, and
`sp_replay_match`. `W_SEQ_GAP` marks an accepted packet: the warning stays in its reason.

## Two verdicts

`decision` is immediate and uses the real-time scores (`decision_delay_h` 0). With `--era5-scores`, a
second verdict `confirmed_decision` is added, available once ERA5 is published (`decision_delay_h` 120):

| Immediate decision | ERA5 model | `confirmed_decision` | Trust score |
|---|---|---|---:|
| `SUSPECT` or `REVIEW` | Alarm | `ALERT` | 0.05 |
| `SUSPECT` or `REVIEW` | No alarm | `CLEARED` | 1 − ERA5 score |
| `SUSPECT` or `REVIEW` | No score | Unchanged | Unchanged |
| `ACCEPT`, `ALERT` or `QUARANTINE` | Any | Unchanged | Unchanged |

Both columns are kept in `trust_decisions.parquet`, `trust_decisions.jsonl` and the log.
Confirmation deliberately cannot escalate `ACCEPT`. The demo summary counts the attacked hours this
leaves unflagged though the ERA5 model alarmed.

## Integrity

- **Score files.** A score file whose SHA-256 differs from its manifest is refused. `--manifest-sha256`
  also pins the manifest itself.
- **Decision log.** Each line of `decision_log.jsonl` carries the SHA-256 of the previous line.
  `decision_log.jsonl.head` records the line count and last hash, so truncation is detected too.
  `trust-decision verify-log <file>` exits 1 on any change. The chain is tamper-evident only against
  someone who cannot rewrite both files: neither is signed or externally anchored.

## End-to-end test

`tests/test_end_to_end.py` starts a real gate, replays two days of the 2025 attacked release with
signed telemetry, sends a tampered, a replayed and a revoked-key packet, and runs this stage. It checks
that every replayed (station, hour) has exactly one decision and that the three bad packets are
`QUARANTINE`. It needs a `run_all` export and score files, so a fresh clone skips it.

## Limitations

- Rejected packets have no trusted observation time, so their `time` is empty.
- Features are computed from the accepted telemetry only. In a short window the CUSUM starts at 0, the
  first hours have no history, and a replay of data from before the window cannot match. The detector
  scores come from a score file computed on the full year.
- If a `(station, time)` is accepted twice, only the first is used for features and `summary.json` notes it.

## Usage

```bash
uv sync --locked
uv run trust-decision run --db <gate.sqlite3> --scores <realtime/manifest.json> --era5-scores <delayed/manifest.json> --features-params ../signal-processing/params/frozen_params.json --out <dir>
uv run trust-decision verify-log <dir>/decision_log.jsonl
uv run pytest -q
```

## End-to-end demo

`scripts/run_demo.py` runs these steps with random passwords and a disposable Git-ignored directory:

```bash
python scripts/run_demo.py --detector-dir <combined/detector> --truth <combined/ground_truth.parquet>
```

The same steps by hand, from `cyber-pipeline/`. `S` is a new `.sensorlab/` subdirectory, and
`SENSORLAB_KEY_PASSWORD` and `SENSORLAB_TLS_PASSWORD` are set in the environment:

```bash
# 1. Keys and a development TLS certificate
uv run sensorlab identities provision --directory $S/identity
uv run sensorlab gate dev-cert --directory $S/tls

# 2. Authorise a session (expires in the future, ISO-8601 with timezone)
uv run sensorlab gate authorize $SESSION --expires-at $EXPIRES --database $S/gate.sqlite3 --registry $S/identity/registry.json

# 3. Start the gate, in a second terminal
uv run sensorlab gate serve --port 8443 --registry $S/identity/registry.json --database $S/gate.sqlite3 --cert $S/tls/server.pem --key $S/tls/server.private.pem

# 4. Signed replay: 7 days, all 6 sensors, over the combined detector release
uv run sensorlab replay --signed --identity-dir $S/identity --session-id $SESSION --speed 0 --start 2025-06-01T00:00:00Z --end 2025-06-08T00:00:00Z --release $DETECTOR --endpoint https://127.0.0.1:8443/v1/telemetry --ca-cert $S/tls/ca.pem --output $S/signed.jsonl

# 5. Tamper one packet in transit; the gate must answer E_BAD_SIG
uv run python scripts/tamper_packet.py --signed $S/signed.jsonl --endpoint https://127.0.0.1:8443/v1/telemetry --ca-cert $S/tls/ca.pem
```

Stop the gate, then from `trust-decision/`:

```bash
# 6. Decisions and log check
uv run trust-decision run --db ../cyber-pipeline/$S/gate.sqlite3 --scores ../evaluation/outputs/realtime/manifest.json --era5-scores ../evaluation/outputs/delayed/manifest.json --features-params ../signal-processing/params/frozen_params.json --out ../results/demo/trust
uv run trust-decision verify-log ../results/demo/trust/decision_log.jsonl
```

Then from `evaluation/`:

```bash
# 7. Print decision counts and the decisions on attacked hours (the only step that reads labels)
uv run python demo_summary.py --trust ../results/demo/trust/trust_decisions.parquet --truth $BUNDLE/ground_truth.parquet --gate-summary ../results/demo/gate_summary.json --era5-manifest outputs/delayed/manifest.json --out ../results/demo_summary.json
```

The driver writes `gate_summary.json` from `sensorlab gate status`. The window was fixed once from
`attack_manifest.csv` as the 7-day test window with the most attack families, using event counts and no
detector output. Results are in [results/demo_summary.json](../results/demo_summary.json).
