"""Where the models live and how far a request may go.

Environment overrides:
    FDI_HF_REPO_ID               default YOUR_HF_ACCOUNT/mirsaad-models
    FDI_DETECTOR_FILENAME        default detecteur_v3_final.joblib
    FDI_TYPE_FILENAME            default type_classifier.joblib
    FDI_WITNESS_FILENAME         default witness_ref.joblib
    FDI_TWINS_ZIP_FILENAME       default twins.zip
    FDI_DEMO_PARQUET_FILENAME    default demo_detector_input.parquet
    FDI_DEMO_LABELS_FILENAME     default demo_row_labels.parquet
    HF_TOKEN / HUGGING_FACE_HUB_TOKEN
    FDI_MODEL_PATH               optional local override for the detector

The token is read from the environment only — never from source.
"""

from __future__ import annotations

import os
from pathlib import Path

HF_REPO_ID            = os.getenv("FDI_HF_REPO_ID", "YOUR_HF_ACCOUNT/mirsaad-models")
HF_DETECTOR_FILENAME  = os.getenv("FDI_DETECTOR_FILENAME", "detecteur_v3_final.joblib")
HF_TYPE_FILENAME      = os.getenv("FDI_TYPE_FILENAME", "type_classifier.joblib")
HF_WITNESS_FILENAME   = os.getenv("FDI_WITNESS_FILENAME", "witness_ref.joblib")
HF_TWINS_ZIP_FILENAME = os.getenv("FDI_TWINS_ZIP_FILENAME", "twins.zip")
HF_DEMO_PARQUET_NAME  = os.getenv("FDI_DEMO_PARQUET_FILENAME", "demo_detector_input.parquet")
HF_DEMO_LABELS_NAME   = os.getenv("FDI_DEMO_LABELS_FILENAME", "demo_row_labels.parquet")

LOCAL_MODEL_PATH = os.getenv("FDI_MODEL_PATH", "").strip()

# --- request limits -------------------------------------------------------
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB (you uploaded ~210k rows)
MAX_ROWS         = 500_000

# --- run store ------------------------------------------------------------
RUN_TTL_SECONDS = 30 * 60
MAX_RUNS        = 12

# --- XAI budgets ----------------------------------------------------------
SHAP_BACKGROUND_ROWS = 100
TEMPORAL_DEFAULT_SPAN = 96
TEMPORAL_MIN_SPAN     = 24
TEMPORAL_MAX_SPAN     = 336

# Robust envelope half-width, in MADs, drawn around the v3 twin's expected
# value (residual_tu). The old `ENVELOPE_MAD_K` is reused verbatim.
ENVELOPE_MAD_K = 3.0


def resolve_hf_token() -> str | None:
    """HF_TOKEN or HUGGING_FACE_HUB_TOKEN from the environment, else None."""
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def local_model_path() -> Path | None:
    if not LOCAL_MODEL_PATH:
        return None
    path = Path(LOCAL_MODEL_PATH)
    return path if path.is_file() else None