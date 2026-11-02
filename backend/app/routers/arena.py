"""
Attack Arena routes — attacker vs defender, behind the same sign-in as /fdi.

    GET  /api/arena/status                  loading | ready | unavailable (+ why)
    GET  /api/arena/meta                    stations, attack families, defaults and limits, threshold
    POST /api/arena/simulate                one attack -> every frame the Duel view animates
    POST /api/arena/batch                   many attacks, run in the background -> job id
    GET  /api/arena/jobs/{job_id}           progress, then results + summary
    GET  /api/arena/scoreboard              per-family detection over every recorded run
    GET  /api/arena/frontier                detection rate vs attack size, per family
    GET  /api/arena/replay/{job}/{attack}   replays one batch attack exactly (Duel view)

The engine holds the digital twins of five stations in memory, so it is loaded
once, in a background thread, on the first call — the console asks /status and
shows "warming up" instead of hanging a request for 20 s.
"""

from __future__ import annotations

import json
import os
import threading
import traceback

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.arena.config import ASSETS
from app.authentification.dependencies import require_user
from app.authentification.models import UserAccount

router = APIRouter(prefix="/arena", tags=["arena"])

MODEL_CARD = dict(name="Detector v3.1", f1_cyber=0.754, f1_ours=0.640, precision=0.83, false_alarms=0.021)
SHAPES = ["abrupt", "smooth", "ramp", "triangle", "follow_era5", "fade"]

_state: dict = {"status": "idle", "detail": None, "arena": None}
_lock = threading.Lock()


def _load() -> None:
    try:
        missing = [p for p in ("base/detector_input.parquet", "twins", "detecteur_v3_final.joblib", "type_classifier.joblib")
                   if not (ASSETS / p).exists()]
        if missing:
            raise FileNotFoundError(f"arena assets missing in {ASSETS}: {', '.join(missing)}")
        from app.arena.engine import Arena  # heavy import: LightGBM + twins

        _state.update(arena=Arena(), status="ready", detail=None)
    except Exception as e:  # reported by /status, never raised into a request
        traceback.print_exc()
        _state.update(status="unavailable", detail=f"{type(e).__name__}: {e}")


def _ensure_loading() -> None:
    with _lock:
        if _state["status"] in ("idle",):
            _state["status"] = "loading"
            threading.Thread(target=_load, daemon=True).start()


def _arena():
    _ensure_loading()
    if _state["status"] != "ready":
        raise HTTPException(503, _state["detail"] or "The arena engine is warming up — retry in a few seconds.")
    return _state["arena"]


class Scenario(BaseModel):
    family: str
    station: str
    start: str
    amplitude: float | None = None
    duration_h: int | None = None
    shape: str | None = None
    sensors: str | None = None
    params: dict = {}


class Batch(BaseModel):
    scenarios: list[dict] | None = None
    # families, stations, amplitudes, durations, shapes, sensors, seed, max_n
    generate: dict | None = None


@router.get("/status")
def status(user: UserAccount = Depends(require_user)):
    _ensure_loading()
    return dict(status=_state["status"], detail=_state["detail"], assets=str(ASSETS))


@router.get("/meta")
def meta(user: UserAccount = Depends(require_user)):
    A = _arena()
    from app.arena import detector_core as dc
    from app.arena.engine import FAM_LABEL
    from app.arena.simulator import DEFAULTS, FAMILIES, LIMITS, SENSORS

    return dict(
        stations=dc.STATIONS,
        families=[dict(key=f, label=FAM_LABEL[f], default=dict(zip(("sensors", "shape", "duration_h", "amplitude"), DEFAULTS[f])),
                       limits=dict(zip(("min_h", "max_h", "max_amplitude"), LIMITS[f]))) for f in FAMILIES],
        sensors=list(SENSORS), shapes=SHAPES, threshold=float(A.D.thr), model=MODEL_CARD,
    )


@router.post("/simulate")
def simulate(s: Scenario, user: UserAccount = Depends(require_user)):
    A = _arena()
    sc = {k: v for k, v in s.model_dump().items() if v is not None}
    return JSONResponse(A.simulate_one(sc))


@router.post("/batch")
def batch(b: Batch, user: UserAccount = Depends(require_user)):
    A = _arena()
    sc = b.scenarios or A.generate(**(b.generate or {}))
    if not sc:
        raise HTTPException(400, "No scenario to run.")
    if len(sc) > 400:
        raise HTTPException(400, "At most 400 attacks per batch.")
    for i, x in enumerate(sc):
        x.setdefault("round", 0)
        x.setdefault("id", f"U-{i:04d}-{x.get('family')}")
    return dict(job_id=A.run_batch_async(sc), n=len(sc))


@router.get("/jobs/{job_id}")
def job(job_id: str, user: UserAccount = Depends(require_user)):
    A = _arena()
    J = A.jobs.get(job_id)
    if J is None:
        raise HTTPException(404, "Unknown job.")
    return JSONResponse({k: v for k, v in J.items() if k != "started"})


@router.get("/scoreboard")
def scoreboard(user: UserAccount = Depends(require_user)):
    return JSONResponse(_arena().scoreboard())


@router.get("/frontier")
def frontier(user: UserAccount = Depends(require_user)):
    return JSONResponse(_arena().frontier())


@router.get("/runs")
def runs(user: UserAccount = Depends(require_user)):
    """Recorded batches, newest first, with their results (for the Launcher's history)."""
    A = _arena()
    from app.arena.engine import RUNS

    out = []
    for d in sorted(os.listdir(RUNS), key=lambda x: -os.path.getmtime(os.path.join(RUNS, x))):
        p = os.path.join(RUNS, d, "results.json")
        if os.path.isfile(p):
            out.append(dict(job_id=d, results=A.rescore(__import__("pandas").read_json(p)).pipe(
                lambda R: json.loads(R.drop(columns=[c for c in ("params", "type_probs", "changed") if c in R]).to_json(orient="records")))))
    return JSONResponse(out[:5])


@router.get("/replay/{job_id}/{attack_id}")
def replay(job_id: str, attack_id: str, user: UserAccount = Depends(require_user)):
    A = _arena()
    from app.arena.engine import RUNS

    p = os.path.join(RUNS, os.path.basename(job_id), "scenarios.json")
    if not os.path.exists(p):
        raise HTTPException(404, "Unknown job.")
    sc = [s for s in json.load(open(p)) if s.get("id") == attack_id]
    if not sc:
        raise HTTPException(404, "Unknown attack.")
    s = dict(sc[0])
    s.pop("round", None)
    return JSONResponse(A.simulate_one(s))
