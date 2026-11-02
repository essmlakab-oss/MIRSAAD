"""The decision table. Pure functions: no I/O, first matching row wins."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

QUARANTINE = "QUARANTINE"
ALERT = "ALERT"
SUSPECT = "SUSPECT"
REVIEW = "REVIEW"
ACCEPT = "ACCEPT"
CLEARED = "CLEARED"

TRUST_QUARANTINE = 0.00
TRUST_ALERT = 0.05
TRUST_SUSPECT = 0.20
TRUST_REVIEW_RULE = 0.60
TRUST_REVIEW_UNAVAILABLE = 0.50


@dataclass(frozen=True)
class Evidence:
    """What is known about one gate decision."""

    gate_code: str
    warnings: tuple[str, ...] = ()
    ai_score: float | None = None
    threshold: float | None = None
    rule_hits: tuple[str, ...] = ()
    features_available: bool = True
    notes: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Verdict:
    decision: str
    trust_score: float
    reason: str


def _with_notes(reason: str, evidence: Evidence) -> str:
    return "; ".join((reason, *evidence.warnings, *evidence.notes))


def _is_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def decide(evidence: Evidence) -> Verdict:
    """Apply the decision table to one row."""
    if evidence.gate_code.startswith("E_"):
        return Verdict(QUARANTINE, TRUST_QUARANTINE, f"gate rejected: {evidence.gate_code}")
    scored = _is_finite(evidence.ai_score) and evidence.threshold is not None
    alarm = scored and evidence.ai_score > evidence.threshold
    rules = ", ".join(evidence.rule_hits)
    if alarm and evidence.rule_hits:
        reason = f"detector alarm {evidence.ai_score:.3f} and rule: {rules}"
        return Verdict(ALERT, TRUST_ALERT, _with_notes(reason, evidence))
    if alarm:
        reason = f"detector alarm {evidence.ai_score:.3f}, no rule beyond threshold"
        return Verdict(SUSPECT, TRUST_SUSPECT, _with_notes(reason, evidence))
    if evidence.rule_hits:
        return Verdict(REVIEW, TRUST_REVIEW_RULE, _with_notes(f"rule only: {rules}", evidence))
    if not scored or not evidence.features_available:
        reason = "features unavailable (insufficient history or NaN)"
        return Verdict(REVIEW, TRUST_REVIEW_UNAVAILABLE, _with_notes(reason, evidence))
    trust = min(max(1.0 - evidence.ai_score, 0.0), 1.0)
    reason = f"no alarm, detector score {evidence.ai_score:.3f}"
    return Verdict(ACCEPT, trust, _with_notes(reason, evidence))


def confirm(immediate: Verdict, era5_score: float | None, era5_threshold: float) -> Verdict:
    """Second-stage verdict once ERA5 is published.

    Only SUSPECT and REVIEW can change. Without a usable ERA5 score the verdict stands.
    """
    if immediate.decision not in (SUSPECT, REVIEW) or not _is_finite(era5_score):
        return immediate
    if era5_score > era5_threshold:
        reason = f"ERA5 detector alarm {era5_score:.3f} confirms: {immediate.reason}"
        return Verdict(ALERT, TRUST_ALERT, reason)
    trust = min(max(1.0 - era5_score, 0.0), 1.0)
    reason = f"ERA5 detector clear {era5_score:.3f}, was: {immediate.reason}"
    return Verdict(CLEARED, trust, reason)
