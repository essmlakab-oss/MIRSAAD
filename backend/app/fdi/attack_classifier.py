"""Model 2 — attack-type classification for flagged hours.

Calls the shipped `type_classifier.joblib` directly on the run's already-
enriched frame — no re-featurisation, no V3 build(), no 2022-2024 history
needed here because the pipeline already ran the detector. This is the
fast path: one predict_proba on the gated rows.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from app.fdi.artifacts import get_type_classifier

logger = logging.getLogger("fdi.attack_classifier")


def classify_attack(D: pd.DataFrame, run_id: str) -> dict[str, Any] | None:
    """The type classifier's answer for this run's alerts.

    Returns a dict with the v3 pipeline's type predictions (already computed
    inside `pipeline.run_pipeline`) projected onto the schema the route
    expects:

        {"detected": bool, "confidence": float, "type": str | None,
         "type_conf": float | None}

    `detected` = any alert exists in the run; `confidence` = the top
    alert's `proba`; `type`/`type_conf` = the top alert's type prediction.

    Never raises — degrades to None with a logged warning.
    """
    try:
        T = get_type_classifier()
        if "alerte" not in D.columns or "proba" not in D.columns:
            logger.warning(
                "[type_clf] run %s has no alerte/proba — skipping.", run_id,
            )
            return None

        alerts = D[D["alerte"].fillna(False).astype(bool)]
        if alerts.empty:
            return {
                "detected": False,
                "confidence": 0.0,
                "type": None,
                "type_conf": None,
            }

        top = alerts.sort_values("proba", ascending=False).iloc[0]
        type_pred = top.get("type_pred")
        type_conf = top.get("type_conf")

        return {
            "detected": True,
            "confidence": round(float(top["proba"]), 4),
            "type": str(type_pred) if isinstance(type_pred, str) else None,
            "type_conf": round(float(type_conf), 4) if pd.notna(type_conf) else None,
        }
    except Exception as e:  # noqa: BLE001 — never break the panel
        logger.warning("[type_clf] run %s failed: %s", run_id, e)
        return None