"""Feature labels for the v3 panel.

The labels for the 10 ECOD features come directly from `xai_engine.ECOD_META`.
SHAP feature labels are handled by a small dictionary; any unknown feature
falls back to its raw name.
"""

from __future__ import annotations

from app.fdi.xai_engine import ECOD_FEATURES, ECOD_META

__all__ = ["ECOD_LABELS", "FeatureLabelCoverageError", "assert_label_coverage"]

#: ECOD labels derived from the single source of truth
ECOD_LABELS: dict[str, str] = {k: ECOD_META[k]["label"] for k in ECOD_FEATURES}

#: SHAP labels for the most common features (fallback is the raw key)
SHAP_LABELS: dict[str, str] = {
    "tu5_z":            "5 cm differs from untouched sensors (z-score)",
    "tu5_cdev":         "5 cm vs untouched sensors (deviation from 30-day median)",
    "tu5_m24":          "5 cm vs untouched sensors (24 h mean)",
    "tw5_z":            "5 cm vs 10 cm twin (z-score)",
    "tw5_cdev":         "5 cm vs 10 cm twin (deviation)",
    "jump_ex":          "1 h change of sm5 not matched by sm10",
    "jz":               "paired-step z-score",
    "jz_max24":         "max paired-step z-score (24 h)",
    "ratio_dev":        "log(sm5/sm10) deviation vs 14-day median",
    "ratio1020_dev":    "10/20 cm depth-relationship deviation",
    "inc5_24":          "24 h change of sm5 not explained by witnesses",
    "inc10_24":         "24 h change of sm10 not explained by witnesses",
    "rise_nosupport72": "72 h wetting without plausible rain",
    "rain_implaus24":   "rain recorded, weather disagrees",
    "rain_missing24":   "weather says rain, sensor shows none",
    "dup_later_m24":    "exact-increment replay fingerprint",
    "raindup_m24":      "exact rain replay fingerprint",
    "tE5_cdev":         "5 cm vs ERA5 reanalysis",
    "box_tu5_absmax":   "5 cm level shifted vs surrounding days",
    "pair5_24":         "unexplained rise then unexplained fall",
}


class FeatureLabelCoverageError(RuntimeError):
    pass


def assert_label_coverage() -> None:
    """Fail fast if any ECOD feature lacks a label."""
    missing = [k for k in ECOD_FEATURES if k not in ECOD_LABELS]
    if missing:
        raise FeatureLabelCoverageError(
            f"ECOD features without labels: {missing}. "
            f"Add them to xai_engine.ECOD_META or feature_labels.ECOD_LABELS."
        )