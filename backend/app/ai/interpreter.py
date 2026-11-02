"""The XAI interpreter: one Groq call that turns a flagged hour into prose.

Unlike the copilot (assistant.py), this is not a conversation. The dashboard
sends the EXACT payload the new /api/xai endpoint returned (validated here
against the frozen XaiResponse schema — the server re-checks every field, so
no client arithmetic can leak into the prose), the prompt quotes the
payload's own numbers verbatim, and the reply is validated against the strict
output schema.

Phase 3 (business-level rewrite): the reader is an operations manager or
auditor, not a data scientist. The schema leads with an executive `resume`,
follows with a plain-language `what_happened`, lists the `key_factors` that
pushed the verdict (what impacted what, in words and numbers), states the business
consequence, and ends with a recommended action.

Honesty contract enforced server-side, regardless of what the model says:
  * no raw engineering key (res_dev, sm10_d6, ...) may appear in output
    text, only the operator labels;
  * banned over-certainty words (definitely, certainly, obviously, ...)
    fail validation;
  * verdict must agree with payload.summary.verdict (the model cannot
    upgrade or downgrade a detection);
  * confidence is an enum, never a number.
A non-conforming reply is retried ONCE with the same messages; a second
failure raises InterpreterError and the router turns it into a 502.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel

from app.ai import client

# Input caps: the panel sends exactly one XaiResponse; the temporal array is
# truncated to a few pivots server-side so the prompt stays short.
MAX_TEMPORAL_POINTS = 6

#: Over-certainty words the prompt itself bans; a reply containing one is
#: invalid output (retry once, then 502), not a soft warning.
BANNED_WORDS = (
    "definitely", "certainly", "obviously", "clearly", "undoubtedly", "of course",
)

ABS_EPSILON = 1e-12  # phase 0 deviation guard, interpreter.py:52-64 semantics

#: Fields stripped from the model-visible payload copy: the raw ``key`` is
#: UI metadata; the analyst must work from labels (see _build_user_content).
FROZEN_STRIP_KEYS = frozenset({"key"})


class InterpreterError(RuntimeError):
    """Two validation failures in a row — the router maps this to 502."""


class _OutputRejected(Exception):
    """Internal: one rejected model reply (triggers the single retry)."""


class InterpretOutput(BaseModel):
    """Strict Phase 3 output schema — business-level rewrite."""

    resume: str
    verdict: str
    what_happened: str
    key_factors: list[str]
    what_it_means: str
    recommended_action: str
    confidence: str


# ---------------------------------------------------------------------------
# Phase 3 prompt: business-level rewrite.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are the business-level XAI interpreter for a False Data Injection (FDI)\n"
    "detector running on 5 cm soil-moisture sensors. A supervised two-stage\n"
    "LightGBM detector plus a per-station digital twin, with a 7-class attack-\n"
    "type classifier, has already reached a verdict on ONE hour at ONE station.\n"
    "Your job is to translate that verdict and its technical explanation into\n"
    "language a non-technical operations manager or auditor can read in thirty\n"
    "seconds and act on.\n"
    "\n"
    "You receive a single JSON payload containing:\n"
    "  - meta (niveau, score, alert, verdict, ground_truth)\n"
    "  - summary (expected/observed sm5, deviation_pct, twin_residual_z)\n"
    "  - rules (fired rule + numeric trigger values + recommended_action)\n"
    "  - ecod (per-feature and per-family tail contributions, suspect flag)\n"
    "  - shap_twin (which witnesses pushed the stage-1 booster's score, in\n"
    "    sm5 units)\n"
    "  - temporal (expected-vs-observed pivots around the hour)\n"
    "\n"
    "Your reply is a single JSON object with EXACTLY these 7 keys:\n"
    '  "resume": 1-2 sentences, <=250 chars. The executive summary. State the\n'
    "     verdict and the single most important reason, in plain words. A reader\n"
    "     who stops here must know whether to care.\n"
    '  "verdict": "ALERT" or "NORMAL". Must equal payload.summary.verdict.\n'
    '  "what_happened": 2-4 sentences, <=350 chars. Plain-language narrative of\n'
    "     what was observed at that hour and why the detector reacted. No\n"
    "     engineering keys; translate labels into words a layperson knows\n"
    "     ('the 10 cm probe', 'the rain witness', 'the seasonal baseline').\n"
    '  "key_factors": 1-4 short strings, each <=180 chars. For each factor, say\n'
    "     WHAT it is (plain-language part of its label — the text after '—'),\n"
    "     include its exact NUMERIC VALUE from the payload if available, and\n"
    "     state WHAT IT DID (e.g., 'Twin gap (value: 0.46) — pushed the anomaly\n"
    "     score up'). Order by importance (top SHAP and ECOD contributions first).\n"
    '  "what_it_means": 1-2 sentences, <=250 chars. The business consequence:\n'
    "     what could go wrong if this signal is real, and how serious it is.\n"
    "     Fold in caveats here: if ground_truth is null say 'unconfirmed'; if any\n"
    "     rain-related family contributes positively, mention that real storms\n"
    "     can produce look-alike alerts (fake_storm risk); if\n"
    "     |twin_residual_z| >= 3, note that the residual is unusually large.\n"
    '  "recommended_action": 1 sentence, <=180 chars, imperative. What the\n'
    "     operator should do next.\n"
    '  "confidence": "low" | "medium" | "high".\n'
    "\n"
    "RULES (enforced server-side):\n"
    "  - Use ONLY the numbers, labels, families and rules in the payload. Never\n"
    "    invent a figure. If the payload does not contain a number, do not quote\n"
    "    one.\n"
    "  - Distinguish ALERT (the detector raised an alert) from ATTAQUE (a\n"
    "    confirmed attack). SUSPECT means borderline — say 'suspicious', never\n"
    "    'attacked'. 'historique insuffisant' means there was not enough prior\n"
    "    data to score the station — say so plainly.\n"
    "  - Refer to features by the plain-language part of their label (after\n"
    "    '—'), never by the raw engineering key. You may name the ECOD family\n"
    "    when one dominates.\n"
    "  - Tone: neutral, precise, hedged where uncertain, business-facing. No\n"
    "    hype, no marketing adjectives, no emojis.\n"
    "\n"
    "BANNED WORDS: definitely, certainly, obviously, clearly, undoubtedly, of course.\n"
)

# Addendum lives in the user payload, not the system prompt — the system
# prompt stays focused on register, while the schema enumeration rides at the
# head of the facts so the model cannot miss the exact key set.
SCHEMA_ADDENDUM = (
    "SCHEMA — the reply object, exactly these 7 keys, no others:\n"
    '  "resume": string <=250 chars (executive summary, 1-2 sentences)\n'
    '  "verdict": "ALERT" | "NORMAL" (must equal payload.summary.verdict)\n'
    '  "what_happened": string <=350 chars (2-4 plain-language sentences)\n'
    '  "key_factors": array of 1-4 strings, each <=180 chars (factor + numeric value + effect)\n'
    '  "what_it_means": string <=250 chars (business consequence, caveats folded in)\n'
    '  "recommended_action": string <=180 chars (imperative)\n'
    '  "confidence": "low" | "medium" | "high"'
)


def _payload_feature_keys(payload: dict) -> list[str]:
    """Every raw engineering key the output prose must NOT leak.

    Only opaque engineering identifiers are banned — those whose names contain
    an underscore, e.g. ``twin_dev``, ``res_dev6``, ``sm10_d24``, ``rain_72``,
    ``jump_excess``, ``ratio_dev``, ``doy_s``, ``e_t2m``. Short keys that are
    ordinary English words or plain column names — ``jump``, ``rain``, ``t``,
    ``solar``, ``hr``, ``sm5``, ``sm10``, ``st5`` — are deliberately ALLOWED
    in prose: the model is writing English, not echoing a key.
    """

    keys: list[str] = []
    keys += [f.get("key") for f in payload.get("ecod", {}).get("features", [])]
    keys += [f.get("key") for f in payload.get("shap_twin", {}).get("features", [])]
    return [k for k in keys if isinstance(k, str) and k and "_" in k]


def _compact_temporal(payload: dict) -> list[dict]:
    """First/last/peaked pivots only — the prompt does not need 25 rows."""

    points = payload.get("temporal", {}).get("points", [])
    if len(points) <= MAX_TEMPORAL_POINTS:
        return list(points)
    scored = [p for p in points if p.get("expected") and p.get("observed")]
    if not scored:
        return points[:3] + points[-3:]
    peak = max(
        range(len(points)),
        key=lambda idx: (abs(float(points[idx]["observed"]) - float(points[idx]["expected"]))
                         if points[idx].get("expected") and points[idx].get("observed")
                         else -1.0),
    )
    picks = sorted({0, peak, len(points) - 1})
    return [points[idx] for idx in picks]


def _build_user_content(payload: dict) -> str:
    """The fact line: payload JSON in, nothing invented, temporal trimmed.

    The raw ``key`` fields are deliberately stripped: the model should reason
    from operator labels only, and a key sitting in its context is a standing
    temptation to echo it (observed live: gpt-oss leaked 'twin_dev' verbatim).
    """
    def strip_keys(rows):
        return [{k: v for k, v in f.items() if k not in FROZEN_STRIP_KEYS}
                for f in rows]

    slim = {
        "meta": payload.get("meta", {}),
        "summary": payload.get("summary", {}),
        "rules": payload.get("rules", {}),
        "ecod": {
            "score": payload.get("ecod", {}).get("score"),
            "threshold": payload.get("ecod", {}).get("threshold"),
            "suspect": payload.get("ecod", {}).get("suspect"),
            "features": strip_keys(payload.get("ecod", {}).get("features", []))[:9],
            "families": payload.get("ecod", {}).get("families", []),
        },
        "shap_twin": {
            "base_value": payload.get("shap_twin", {}).get("base_value"),
            "features": strip_keys(payload.get("shap_twin", {}).get("features", []))[:9],
        },
        "temporal": {"window_hours": payload.get("temporal", {}).get("window_hours"),
                     "points": _compact_temporal(payload)},
    }
    # SCHEMA_ADDENDUM rides here — injected at the head of the user payload,
    # ahead of the facts — so the system prompt stays focused on register
    # while the schema caps keep full model attention.
    return SCHEMA_ADDENDUM + "\n\nPAYLOAD:\n" + json.dumps(slim, ensure_ascii=False)


def _clean(obj: Any) -> Any:
    """JSON has no NaN: convert float('nan') -> None at the seam."""

    import math

    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def validate_output(raw_content: str, payload: dict) -> dict:
    """One model reply -> validated strict dict, or _OutputRejected.

    Checks, in order: JSON object; exact Phase 3 key set; text-length caps;
    banned words; raw-key leakage; verdict equality with the payload;
    confidence enum.
    """

    import re

    try:
        obj = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _OutputRejected(f"reply is not JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise _OutputRejected("reply is not a JSON object")

    required = {
        "resume", "verdict", "what_happened", "key_factors",
        "what_it_means", "recommended_action", "confidence",
    }
    if not required.issubset(set(obj)):
        raise _OutputRejected(f"missing keys: {sorted(required - set(obj))}")

    def _cap(name: str, n: int) -> str:
        v = obj.get(name)
        if not isinstance(v, str) or not v.strip() or len(v) > n:
            raise _OutputRejected(f"{name} must be a non-empty string <= {n}")
        return v.strip()

    resume = _cap("resume", 250)
    what_happened = _cap("what_happened", 350)
    what_it_means = _cap("what_it_means", 250)
    action = _cap("recommended_action", 180)

    key_factors = obj.get("key_factors")
    if not isinstance(key_factors, list) or not (1 <= len(key_factors) <= 4):
        raise _OutputRejected("key_factors must be 1-4 items")
    key_factors = [str(k).strip() for k in key_factors]
    if any(not k or len(k) > 180 for k in key_factors):
        raise _OutputRejected("each key factor must be <=180 chars")

    verdict = obj.get("verdict")
    payload_verdict = payload.get("summary", {}).get("verdict")
    if verdict not in ("ALERT", "NORMAL"):
        raise _OutputRejected(f"verdict {verdict!r} not in ALERT|NORMAL")
    if verdict != payload_verdict:
        raise _OutputRejected(
            f"model verdict {verdict!r} contradicts the payload {payload_verdict!r}"
        )

    confidence = obj.get("confidence")
    if confidence not in ("low", "medium", "high"):
        raise _OutputRejected(f"confidence {confidence!r} not in low|medium|high")

    texts = [resume, what_happened, what_it_means, action, *key_factors]
    for word in BANNED_WORDS:
        if any(word.lower() in t.lower() for t in texts):
            raise _OutputRejected(f"banned word {word!r} in output")

    # Raw engineering keys (only the opaque ones, filtered by the underscore
    # rule) must never surface in prose as bare tokens.
    for key in _payload_feature_keys(payload):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])"
        for t in texts:
            cleaned = t.replace(f"{key} —", "")  # quoted operator label
            if re.search(pattern, cleaned):
                raise _OutputRejected(f"raw feature key {key!r} in output prose")

    return {
        "resume": resume,
        "verdict": verdict,
        "what_happened": what_happened,
        "key_factors": key_factors,
        "what_it_means": what_it_means,
        "recommended_action": action,
        "confidence": confidence,
    }


def interpret(payload: dict, *, user_id: str = "unknown") -> dict:
    """One validated Groq interpretation for one XaiResponse payload."""

    request_id = uuid.uuid4().hex[:12]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_content(payload)},
    ]

    attempts = 0
    last_error: _OutputRejected | None = None
    while attempts < 2:
        attempts += 1
        call = client.complete_json(messages, temperature=0.2)
        try:
            validated = validate_output(call["content"], _clean(payload))
        except _OutputRejected as e:
            last_error = e
            print(
                f"[AI] interpret {request_id} user={user_id} model={call['model']} "
                f"attempt={attempts} usage={call['usage']} "
                f"latency_ms={call['latency_ms']:.0f} REJECTED: {e}"
            )
            continue
        print(
            f"[AI] interpret {request_id} user={user_id} model={call['model']} "
            f"attempt={attempts} usage={call['usage']} "
            f"latency_ms={call['latency_ms']:.0f} OK"
        )
        validated["attempt"] = attempts
        validated["request_id"] = request_id
        return validated

    raise InterpreterError(
        f"Groq returned a non-conforming reply twice ({last_error}). "
        "The panel stays silent rather than quoting unvalidated text."
    )