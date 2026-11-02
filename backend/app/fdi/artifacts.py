"""Hugging Face artifact loading — the only module that touches the Hub.

Every artifact download goes through `hf_hub_download` here, and the twins
are extracted once per process into a temp directory with an allow-list
policy (only `<station>.joblib` for known stations). Missing twins are a
hard error — a partial twin set silently produces wrong features.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from functools import lru_cache

import pandas as pd

from app.fdi import config
from app.fdi.pandas_compat import safe_joblib_load
from app.fdi import detector_core as dc
from app.fdi.defender import FastDefender

logger = logging.getLogger("fdi.artifacts")

CACHE_ROOT = os.path.join(tempfile.gettempdir(), "fdi_v3")
TWINS_DIR  = os.path.join(CACHE_ROOT, "twins")
os.makedirs(TWINS_DIR, exist_ok=True)


def _hf_token() -> str | None:
    return config.resolve_hf_token()


def _download(filename: str, required: bool = True) -> str | None:
    from huggingface_hub import hf_hub_download
    try:
        return hf_hub_download(
            repo_id=config.HF_REPO_ID, filename=filename, token=_hf_token(),
        )
    except Exception as e:
        if required:
            logger.error("Required HF artifact %s failed: %s", filename, e)
            raise
        logger.warning("Optional HF artifact %s unavailable: %s", filename, e)
        return None


@lru_cache(maxsize=1)
def download_models() -> dict:
    return {
        "detector": _download(config.HF_DETECTOR_FILENAME, required=True),
        "type":     _download(config.HF_TYPE_FILENAME,     required=True),
        "witness":  _download(config.HF_WITNESS_FILENAME,  required=False),
    }


@lru_cache(maxsize=1)
def download_twins() -> str:
    expected = {f"{s}.joblib" for s in dc.STATIONS}
    have = {f for f in os.listdir(TWINS_DIR) if f in expected}
    if expected <= have:
        logger.info("Twins already extracted (%d/%d).", len(have), len(expected))
        return TWINS_DIR

    zpath = _download(config.HF_TWINS_ZIP_FILENAME, required=True)
    staging = tempfile.mkdtemp(prefix="twins_staging_", dir=CACHE_ROOT)
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(staging)

        found: dict[str, str] = {}
        for root, _, files in os.walk(staging):
            for f in files:
                if f in expected and f not in found:
                    found[f] = os.path.join(root, f)

        missing = expected - set(found)
        if missing:
            raise RuntimeError(
                f"twins.zip is missing expected station twins: {sorted(missing)}. "
                f"Refusing to continue with a partial twin set."
            )
        for fname, src in found.items():
            shutil.move(src, os.path.join(TWINS_DIR, fname))
        logger.info("Extracted %d twins into %s.", len(found), TWINS_DIR)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return TWINS_DIR


@lru_cache(maxsize=1)
def get_defender() -> FastDefender:
    paths = download_models()
    tdir = download_twins()
    return FastDefender(store_path=tdir, model_path=paths["detector"])


@lru_cache(maxsize=1)
def get_type_classifier() -> dict:
    T = safe_joblib_load(download_models()["type"])
    missing = {"model", "features", "classes"} - set(T.keys())
    if missing:
        raise RuntimeError(
            f"Malformed type_classifier.joblib — missing keys: {sorted(missing)}."
        )
    return T


@lru_cache(maxsize=1)
def get_witness_ref() -> dict | None:
    p = download_models().get("witness")
    if not p:
        return None
    try:
        W = safe_joblib_load(p)
    except Exception as e:
        logger.warning("witness_ref.joblib failed to load: %s", e)
        return None
    if isinstance(W, dict) and "wref" in W:
        return W["wref"]
    return W if isinstance(W, dict) else None


@lru_cache(maxsize=1)
def get_demo_parquet() -> str:
    return _download(config.HF_DEMO_PARQUET_NAME, required=True)


@lru_cache(maxsize=1)
def get_demo_labels() -> str:
    return _download(config.HF_DEMO_LABELS_NAME, required=True)