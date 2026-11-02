"""ECOD families — 5 families across the 10 v3 witnesses.

Presentation layer only; never feeds the detector.
Family assignment mirrors `xai_engine.ECOD_META`'s `family` field so the
per-feature panel and the family roll-up can never disagree.
"""

from __future__ import annotations

from app.fdi.xai_engine import ECOD_FEATURES, ECOD_META

__all__ = ["ECOD_FAMILIES", "FamilyCoverageError", "family_of", "assert_family_coverage"]

#: Family name -> member feature keys, in panel display order.
ECOD_FAMILIES: dict[str, list[str]] = {
    "Paired-step fingerprint": ["jump_ex", "jz"],
    "Depth-relationship":      ["ratio_dev", "ratio1020_dev"],
    "Unresolved increment":    ["inc5_24", "inc10_24"],
    "Rain plausibility":       ["rise_nosupport72", "rain_implaus24", "rain_missing24"],
    "Replay":                  ["dup_later_m24"],
}

_REVERSE: dict[str, str] = {
    key: fam for fam, keys in ECOD_FAMILIES.items() for key in keys
}


class FamilyCoverageError(RuntimeError):
    pass


def family_of(key: str) -> str:
    try:
        return _REVERSE[key]
    except KeyError:
        raise FamilyCoverageError(
            f"ECOD feature {key!r} is not in ECOD_FAMILIES."
        ) from None


def assert_family_coverage() -> None:
    keys = set(ECOD_FEATURES)
    orphans = sorted(keys - set(_REVERSE))
    unknown = sorted(set(_REVERSE) - keys)
    if orphans or unknown:
        raise FamilyCoverageError(
            f"family mismatch — unmapped: {orphans}, mapped-but-absent: {unknown}."
        )
    missing_labels = [k for k in keys if k not in ECOD_META]
    if missing_labels:
        raise FamilyCoverageError(f"features without labels: {missing_labels}")