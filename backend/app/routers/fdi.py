"""FDI detection endpoints.

    POST /fdi/predict          CSV upload     -> scored run
    POST /fdi/demo             shipped demo   -> same, with injected attacks
    GET  /fdi/status           is the model even loaded?
    GET  /fdi/xai/shap         DEPRECATED     -> use /api/xai/{run_id}/{station}/{hour_iso}
    GET  /fdi/xai/temporal     DEPRECATED     -> use /api/xai/{run_id}/{station}/{hour_iso}
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from app.authentification.dependencies import require_user
from app.authentification.models import UserAccount
from app.fdi import config
from app.fdi.artifacts import get_defender
from app.fdi.demo import make_sample_data
from app.fdi.preprocessing import SchemaError, prepare_df, renamed_columns, run_predictions
from app.fdi.schemas import (
    DemoResponse, PredictResponse, ShapResponse, StatusResponse, TemporalResponse,
)
from app.fdi.store import cache_get, cache_put, create_run, get_run, target_for
from app.fdi.temporal import TemporalError, build_temporal
from app.fdi.xai_engine import enrich
from app.fdi.pipeline import NEIGHBOUR_WITNESS, REQUIRED_SENSOR_COLS
from app.routers.xai import build_xai_payload

router = APIRouter(prefix="/fdi", tags=["fdi"])


def _require_model():
    try:
        return get_defender()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


def _require_run(run_id: str | None):
    run = get_run(run_id)
    if run is None:
        raise HTTPException(
            404,
            "Unknown or expired run_id. Uploads live 30 minutes — re-upload.",
        )
    return run


def _require_row(run, row: int):
    if row < 0 or row >= len(run.out):
        raise HTTPException(
            422, f"row={row} is out of range for this run (0..{len(run.out) - 1}).",
        )
    return row


@router.get("/status", response_model=StatusResponse)
def get_status(user: UserAccount = Depends(require_user)):
    try:
        det = get_defender()
    except Exception as e:
        return StatusResponse(available=False, detail=str(e))
    th = float(det.thr)
    return StatusResponse(
        available=True,
        repo_id=config.HF_REPO_ID,
        filename=config.HF_DETECTOR_FILENAME,
        stations=list(det.store.keys()),
        threshold=th,
        suspect_threshold=th * 0.75,
        min_history_days=18,
    )


def _read_csv(upload: UploadFile) -> pd.DataFrame:
    raw_bytes = upload.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    if not raw_bytes:
        raise HTTPException(422, "The uploaded file is empty.")
    try:
        return pd.read_csv(io.BytesIO(raw_bytes))
    except UnicodeDecodeError:
        raise HTTPException(422, "The file is not UTF-8 text.")
    except Exception as e:
        raise HTTPException(422, f"Could not parse the CSV: {e}")


def _score_frame(det, df: pd.DataFrame, *, source: str,
                 filename: str | None, renamed: dict[str, str], attacks=None):
    # The neighbour witness station (OK_Goodwell_2_E) is consumed by
    # run_pipeline to build nbE5/nbE10/nbErain features for OK_Goodwell_2_SE,
    # but is never scored as its own station. Exclude it from the unknown-
    # station guard so a 6-station input (5 real + 1 neighbour) is accepted.
    unknown = set(df["station"].unique()) - set(det.store.keys()) - NEIGHBOUR_WITNESS
    if unknown:
        raise HTTPException(
            422, f"Stations without a trained twin: {sorted(unknown)}. "
                 f"Available: {sorted(det.store.keys())} "
                 f"(plus neighbour witness {sorted(NEIGHBOUR_WITNESS)}).",
        )
    try:
        out, H = run_predictions(df)
    except ValueError as e:
        raise HTTPException(422, f"Prediction failed: {e}")

    run = create_run(
        df, H,
        source=source, filename=filename, renamed=renamed, attacks=attacks,
        threshold=float(det.thr), twin_features=None,
    )
    return run


@router.post("/predict", response_model=PredictResponse)
def post_predict(
    file: UploadFile = File(..., description="CSV — time, station, sm5…e_stl1"),
    user: UserAccount = Depends(require_user),
):
    det = _require_model()
    raw = _read_csv(file)
    renamed = renamed_columns(raw)
    try:
        df = prepare_df(raw)
    except SchemaError as e:
        raise HTTPException(422, str(e))
    run = _score_frame(det, df, source="upload", filename=file.filename, renamed=renamed)
    return _predict_payload(run)


@router.post("/demo", response_model=DemoResponse)
def post_demo(user: UserAccount = Depends(require_user)):
    det = _require_model()
    data, attacks = make_sample_data()
    df = prepare_df(data)
    run = _score_frame(det, df, source="demo", filename=None, renamed={}, attacks=attacks)
    payload = _predict_payload(run)
    return DemoResponse(
        **payload,
        injected_attacks={k: [v[0], v[1]] for k, v in (attacks or {}).items()},
    )


def _predict_payload(run) -> dict:
    return {
        "run_id": run.run_id,
        "created_at": datetime.fromtimestamp(run.created_at, tz=timezone.utc).isoformat(),
        "model": run.model_info,
        "dataset": run.dataset,
        "summary": run.summary,
        "points": run.points,
        "candidates": run.candidates,
    }


# --- deprecated legacy XAI routes ----------------------------------------

@router.get("/xai/shap", response_model=ShapResponse)
def get_shap(
    run_id: str = Query(...), row: int = Query(..., ge=0),
    response: Response = None, user: UserAccount = Depends(require_user),
):
    if response is not None:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Sun, 31 Jan 2027 23:59:59 GMT"
    run = _require_run(run_id)
    _require_row(run, row)
    payload = build_xai_payload(run, row)
    twins = payload["shap_twin"]
    names = [f["key"] for f in twins["features"]]
    values = [f["shap"] for f in twins["features"]]
    return ShapResponse(
        run_id=run.run_id, row=row,
        t=str(run.out.at[row, "time"]),
        station=str(run.out.at[row, "station"]),
        target=target_for(run, row),
        method="shap.TreeExplainer on M1",
        base=float(twins["base_value"]),
        pred=float(run.out.at[row, "proba"]),
        names=names, values=values,
        note="SHAP on the stage-1 LightGBM booster (M1).",
        elapsed_ms=0.0,
    )


@router.get("/xai/temporal", response_model=TemporalResponse)
def get_temporal(
    run_id: str = Query(...), row: int = Query(..., ge=0),
    target: str = Query("sm5"),
    span: int = Query(config.TEMPORAL_DEFAULT_SPAN,
                      ge=config.TEMPORAL_MIN_SPAN, le=config.TEMPORAL_MAX_SPAN),
    response: Response = None, user: UserAccount = Depends(require_user),
):
    if response is not None:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Sun, 31 Jan 2027 23:59:59 GMT"
    run = _require_run(run_id)
    _require_row(run, row)
    try:
        series = build_temporal(run, row, span=span, target=target)
    except TemporalError as e:
        raise HTTPException(422, str(e))
    station = str(run.out.at[row, "station"])
    return TemporalResponse(
        run_id=run.run_id, row=row,
        t=str(run.out.at[row, "time"]), station=station,
        span=series.span, envelope=series.envelope,
        score_threshold=float(run.model_info["threshold"]),
        target={"sensors": series.sensors},
        points=series.points, note=series.note,
    )