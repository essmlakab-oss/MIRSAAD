"""Tamper one signed packet in transit and report the Crypto Gate's answer.

Takes the last envelope of a signed NDJSON file, changes one measurement without re-signing,
and posts it over verified HTTPS. Exits 1 unless the gate answers E_BAD_SIG.
"""

import argparse
import json
import ssl
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signed", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--ca-cert", type=Path, required=True)
    args = parser.parse_args()

    lines = args.signed.read_text(encoding="utf-8").splitlines()
    envelope = json.loads(lines[-1])
    measurements = envelope["telemetry"]["measurements"]
    column = next(
        c
        for c in ("SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "T_HR_AVG")
        if measurements[c] is not None
    )
    measurements[column] = round(measurements[column] + 0.1, 3)
    context = ssl.create_default_context(cafile=str(args.ca_cert))
    with httpx.Client(verify=context, trust_env=False, timeout=10) as client:
        response = client.post(
            args.endpoint,
            content=json.dumps(envelope),
            headers={"content-type": "application/json"},
        )
    code = response.json().get("code")
    print(
        json.dumps(
            {
                "tampered_column": column,
                "sensor_id": envelope["telemetry"]["sensor_id"],
                "sequence": envelope["telemetry"]["sequence"],
                "http_status": response.status_code,
                "gate_code": code,
            }
        )
    )
    return 0 if code == "E_BAD_SIG" else 1


if __name__ == "__main__":
    raise SystemExit(main())
