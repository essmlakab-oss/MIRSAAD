"""Full backend test — v3 pipeline + XAI + AI interpretation.

Location:   backend/app/test_fdi.py
Run from:   backend/
Command:    python -m app.test_fdi

Uses:
    backend/.env             (Groq key, HF token)
    backend/Data/            (ERA5 + SCAN — the feature-builder input)

Every layer calls the same function the production route calls.
Exit code 0 = full stack verified.  Non-zero = the printed ✗ names the layer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

# ---------------------------------------------------------------------------
# 0. sys.path + env — BEFORE any app.* import.
# ---------------------------------------------------------------------------
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# Load backend/.env so HF_TOKEN and groq_model are available.
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(_BACKEND_ROOT, ".env")
    if os.path.isfile(_env_path):
        load_dotenv(_env_path, override=True)
except Exception:
    pass

# CIS_DATA — explicit, since your tree lives at backend/Data/.
# Only set if the environment hasn't already provided it.
if not os.getenv("CIS_DATA"):
    _data_candidate = os.path.join(_BACKEND_ROOT, "Data")
    if os.path.isdir(_data_candidate):
        os.environ["CIS_DATA"] = _data_candidate
print(f"  CIS_DATA = {os.environ.get('CIS_DATA', '<unset>')}")
print(f"  HF_TOKEN = {'set' if os.getenv('HF_TOKEN') else '<unset>'}")

_era5 = os.path.join(os.environ.get("CIS_DATA", ""), "ERA5_USCRN",
                     "era5_uscrn_hourly_2022_2025.csv")
_scan  = os.path.join(os.environ.get("CIS_DATA", ""), "Voisins", "scan")
print(f"  ERA5 CSV: {'OK' if os.path.isfile(_era5) else 'MISSING'}  {_era5}")
print(f"  SCAN dir: {'OK' if os.path.isdir(_scan) else 'MISSING'}  {_scan}")


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_FAILS: list[str] = []


def step(name: str):
    class _Step:
        def __enter__(self):
            print(f"\n▶ {name}")
            return self
        def __exit__(self, et, ev, tb):
            global _PASS, _FAIL
            if et is None:
                _PASS += 1
                print(f"  ✓ {name}")
            else:
                _FAIL += 1
                _FAILS.append(name)
                print(f"  ✗ {name}")
                print(f"    {et.__name__}: {ev}")
                traceback.print_exc(limit=3)
            return True
    return _Step()


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — Groq client
# ═══════════════════════════════════════════════════════════════════════════
with step("Groq client is configured"):
    from app.ai.client import API_KEY, MODEL, status

    if not API_KEY:
        raise AssertionError(
            "No Groq key. Set `groq_model` (or GROQ_API_KEY) in backend/.env."
        )
    print(f"  model = {MODEL}")
    st = status()
    check(st["available"], f"client not available: {st.get('error')}")
    print(f"  reasoning_effort = {st['reasoning_effort']}")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — demo + prepare
# ═══════════════════════════════════════════════════════════════════════════
DEMO_PATH = os.getenv("DEMO_PATH")

with step("Load demo (or DEMO_PATH) and prepare"):
    from app.fdi.demo import make_sample_data
    from app.fdi.preprocessing import prepare_df

    if DEMO_PATH and os.path.isfile(DEMO_PATH):
        import pandas as pd
        print(f"  using local file: {DEMO_PATH}")
        data = pd.read_parquet(DEMO_PATH)
        attacks = {}
    else:
        data, attacks = make_sample_data()

    check(len(data) > 0, "empty frame")
    df = prepare_df(data)
    years = set(df["time"].dt.year)
    print(f"  rows={len(df):,}, stations={df['station'].nunique()}, "
          f"years={sorted(years)}")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 3 — v3 pipeline
# ═══════════════════════════════════════════════════════════════════════════
with step("run_predictions — v3 detection + type classification"):
    from app.fdi.preprocessing import run_predictions
    out, H = run_predictions(df)
    check(len(out) == len(H), f"out/H length mismatch ({len(out)} vs {len(H)})")
    for col in ("proba", "alerte", "reason"):
        check(col in out.columns, f"out missing {col}")
    n_alert = int(out["alerte"].sum())
    print(f"  scored={len(out):,}, alerts={n_alert}")
    check(n_alert >= 1, "no alerts — demo may be empty")


with step("create_run — server-side store"):
    from app.fdi.store import create_run
    from app.fdi.artifacts import get_defender
    defender = get_defender()
    run = create_run(
        df, H,
        source="demo", filename=None, renamed={}, attacks=attacks,
        threshold=float(defender.thr), twin_features=None,
    )
    print(f"  run_id={run.run_id[:12]}…, "
          f"points={len(run.points)}, candidates={len(run.candidates)}")
    check(len(run.out) == len(out), "run.out length mismatch")
    check(len(run.candidates) > 0, "no candidates in run")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 4 — real XaiResponse payload
# ═══════════════════════════════════════════════════════════════════════════
with step("build_xai_payload on a real alert hour"):
    from app.routers.xai import build_xai_payload, _niveau_for

    threshold = float(run.model_info["threshold"])
    alert_rows = [
        i for i in run.out.index
        if _niveau_for(run.out.loc[i], threshold) == "ATTAQUE"
    ]
    check(len(alert_rows) > 0, "no ATTAQUE rows in this run")
    row = alert_rows[0]
    payload = build_xai_payload(run, row)

    check(payload["meta"]["niveau"] == "ATTAQUE", "niveau must be ATTAQUE")
    check(payload["meta"]["score"] is not None, "meta.score null")
    check(payload["summary"]["verdict"] == "ALERT", "verdict must be ALERT")
    check(payload["summary"]["expected_sm5"] is not None, "expected_sm5 null")
    check(payload["summary"]["twin_residual_z"] is not None,
          "twin_residual_z null — patch 4b not applied?")
    check(payload["ecod"]["score"] is not None, "ecod.score null")
    check(len(payload["ecod"]["features"]) == 10,
          f"ecod.features = {len(payload['ecod']['features'])} (expected 10)")
    check(len(payload["shap_twin"]["features"]) > 0,
          "no SHAP features (pip install shap?)")
    check(len(payload["temporal"]["points"]) == 25,
          f"temporal.points = {len(payload['temporal']['points'])} (expected 25)")

    print(f"  row={row}, station={payload['meta']['station']}, "
          f"hour={payload['meta']['hour_iso']}")
    print(f"  score={payload['meta']['score']:.3f}, "
          f"ecod={payload['ecod']['score']:.2f}, "
          f"fired={payload['rules']['fired']}")
    print(f"  top ecod family = {payload['ecod']['families'][0]['family']}")

    cache_path = os.path.join(_BACKEND_ROOT, "xai_payload.json")
    with open(cache_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"  payload cached at {cache_path}")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 5 — schema round-trip
# ═══════════════════════════════════════════════════════════════════════════
with step("InterpretXaiRequest.validated_payload — schema round-trip"):
    from app.fdi.schemas import InterpretXaiRequest

    req = InterpretXaiRequest(payload=payload)
    validated = req.validated_payload()

    for k in ("meta", "summary", "rules", "ecod", "shap_twin", "temporal"):
        check(k in validated, f"schema dropped {k}")
    check(validated["summary"]["verdict"] == payload["summary"]["verdict"],
          "schema mutated summary.verdict")

    print(f"  schema round-trip OK, "
          f"verdict={validated['summary']['verdict']}, "
          f"n_features={len(validated['ecod']['features'])}")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 6 — the real Groq call
# ═══════════════════════════════════════════════════════════════════════════
with step("interpreter.interpret on the real payload"):
    from app.ai.interpreter import interpret

    t0 = time.time()
    result = interpret(validated, user_id="test_ai_interp")
    elapsed = time.time() - t0

    for k in ("resume", "verdict", "what_happened", "key_factors",
              "what_it_means", "recommended_action", "confidence",
              "attempt", "request_id"):
        check(k in result, f"missing {k} in output")
    check(result["verdict"] == validated["summary"]["verdict"],
          f"verdict was flipped: {result['verdict']} != "
          f"{validated['summary']['verdict']}")
    check(result["confidence"] in ("low", "medium", "high"),
          f"confidence invalid: {result['confidence']}")
    check(1 <= len(result["key_factors"]) <= 4,
          f"key_factors out of range: {len(result['key_factors'])}")

    print(f"  elapsed: {elapsed:.1f}s  attempt={result['attempt']}  "
          f"request_id={result['request_id']}")

    print("\n  ── INTERPRETATION ──")
    print(f"  verdict:            {result['verdict']}")
    print(f"  confidence:         {result['confidence']}")
    print(f"  resume:             {result['resume']}")
    print(f"  what_happened:      {result['what_happened']}")
    for i, f in enumerate(result["key_factors"], 1):
        print(f"  key_factor[{i}]:     {f}")
    print(f"  what_it_means:      {result['what_it_means']}")
    print(f"  recommended_action: {result['recommended_action']}")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 7 — prompt sanity (warns, does not fail)
# ═══════════════════════════════════════════════════════════════════════════
with step("interpretation does not describe the OLD model"):
    from app.ai.interpreter import SYSTEM_PROMPT

    stale = ("hybrid detector", "unsupervised anomaly score", "LIME")
    for phrase in stale:
        if phrase.lower() in SYSTEM_PROMPT.lower():
            print(f"  ⚠️  prompt still mentions: {phrase!r}")
            print(f"     (cosmetic — interpretation may describe the old model)")
        else:
            print(f"  ✓  prompt does not mention {phrase!r}")


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════
parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true",
                    help="Print the browser snippet to hit the live route")
args, _ = parser.parse_known_args()

if args.live:
    with step("Live route reproduction"):
        cache_path = os.path.join(_BACKEND_ROOT, "xai_payload.json")
        print("  To reproduce /api/ai/interpret-xai in the browser dev console:")
        print("  1. Sign in to the dashboard.")
        print("  2. In dev console, paste (adjust the URL if the API isn't on :8000):")
        print("")
        print("     const payload = await (await fetch('/xai_payload.json')).json();")
        print("     fetch('/api/ai/interpret-xai', {")
        print("       method: 'POST',")
        print("       credentials: 'include',")
        print("       headers: {'Content-Type': 'application/json'},")
        print("       body: JSON.stringify({ payload })")
        print("     }).then(r => r.json()).then(console.log)")
        print("")
        print(f"  (payload file: {cache_path})")


print("\n" + "=" * 72)
print(f"RESULT: {_PASS} passed, {_FAIL} failed")
if _FAILS:
    print("Failed steps:")
    for n in _FAILS:
        print(f"  ✗ {n}")
    print("\nSend me the traceback from the failing step.")
    sys.exit(1)
print("Backend + XAI + AI interpretation verified against the real pipeline ✓")
sys.exit(0)