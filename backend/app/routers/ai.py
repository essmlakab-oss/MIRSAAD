"""
AI copilot endpoints.

One question at a time: a turn of the in-app analyst. It reads the FDI run
currently loaded in the server-side run store (app/fdi/store.py), so "how many
attacks are there?" and "why was this hour flagged?" are answered from the
detector's own verdict rather than from whatever the browser decides to ship.

Nothing here is polled. A question is answered when it is asked, and the
numbers it quotes were already computed once, at ingest.

Every endpoint requires a signed-in user: the Groq key is a billed resource
and these are the only routes that spend it, so an anonymous caller is a
billing hole rather than a privacy one. The interpret-xai route adds a hard
per-user rate limit on top of the auth gate.
"""

import math
import time
from collections import deque
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.ai import assistant, interpreter
from app.ai.client import AIUnavailable
from app.authentification.dependencies import require_user
from app.authentification.models import UserAccount
from app.fdi.schemas import InterpretXaiRequest, InterpretXaiResponse

router = APIRouter(prefix="/ai", tags=["ai"])

# ---------------------------------------------------------------------------
# interpret-xai rate limit (frozen contract F2): 10 calls / hour / user,
# in-memory sliding window {user_id: deque[unix_ts]}. State resets when the
# process restarts — it is a billing guard, not an audit trail.
#
# On the 11th call within the window the reply is 429 with
#   Retry-After = ceil(oldest_timestamp_in_window + 3600 - now)
# i.e. the exact number of seconds until the oldest observed call leaves the
# sliding window. Computed with math.ceil against the SAME epoch clock the
# deque stores, so the header's countdown matches the window's expiry to the
# second.
# ---------------------------------------------------------------------------
RATE_LIMIT_PER_HOUR = 10
RATE_WINDOW_SECONDS = 3600.0

_rate_deques: dict[str, deque[float]] = {}


def _check_rate_limit(user_id: str) -> None:
    """Slide, count, and either admit or raise the 429 + Retry-After."""

    now = time.time()
    bucket = _rate_deques.setdefault(user_id, deque())
    while bucket and bucket[0] <= now - RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_HOUR:
        # Oldest admitted call leaves the window at its timestamp + 3600 s;
        # ceil() rounds the wait UP so the client never retries too early.
        retry_after = int(math.ceil(bucket[0] + RATE_WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail=(
                f"interpret-xai rate limit: {RATE_LIMIT_PER_HOUR} calls per hour "
                "per user. Retry after the value of the Retry-After header."
            ),
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    bucket.append(now)


@router.post("/assistant")
async def ask_assistant(request: Request, user: UserAccount = Depends(require_user)):
    """
    One turn of the in-app copilot.

    Body: {"messages": [{"role": "user"|"assistant", "content": "..."}],
           "current_route": "/dashboard/fdi",
           "context": {"run_id": "...", "row": 123}}

    `context` is optional: it names the run and the hour the operator has
    selected on the timeline, so the copilot can answer about the exact point
    on screen ("what is the score of this hour?").

    Returns the reply plus, when the question is about something on screen, a
    `target` the frontend navigates to and highlights. The target is resolved
    from a fixed table on this side, so a confused model can pick the wrong
    panel but never an invalid route.
    """

    body = await request.json()

    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise HTTPException(status_code=400, detail="Expected {messages: [...]}")

    context = body.get("context")

    return await _run(
        assistant.ask,
        body["messages"],
        body.get("current_route"),
        context if isinstance(context, dict) else None,
    )


@router.get("/status")
def get_status(user: UserAccount = Depends(require_user)):
    """Whether the copilot is configured, and what it is pointed at."""

    return assistant.stats()


@router.post("/interpret-xai", response_model=InterpretXaiResponse)
async def interpret_xai(
    body: InterpretXaiRequest,
    user: UserAccount = Depends(require_user),
):
    """One validated prose turn over the frozen /api/xai payload.

    The request body carries the XAI panel's own response object
    (``{"payload": {...}}``); it re-validates against the frozen XaiResponse
    schema so a tampered browser cannot push unvalidated "facts" into the
    prompt. The reply is validated against the strict §1.9 output schema with
    one retry, then 502.

    422 on a payload the schema rejects, 429 over the per-user limit
    (Retry-After header), 503 when Groq is not configured/reachable, 502
    after two invalid model replies.
    """

    _check_rate_limit(str(user.id))

    try:
        validated = body.validated_payload()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"payload rejected by the XAI schema: {e}")

    result = await _run(partial(interpreter.interpret, user_id=str(user.id)), validated)
    return InterpretXaiResponse(**result)


async def _run(function, *args):
    """Threadpool wrapper that maps client/interpreter failures to HTTP."""

    try:
        return await run_in_threadpool(function, *args)
    except interpreter.InterpreterError as e:
        # Two invalid model replies: do not quote unvalidated prose.
        raise HTTPException(status_code=502, detail=str(e))
    except AIUnavailable as e:
        # 503, not 500: the dashboard works without this, and the copilot panel
        # should say it is unavailable rather than show an error.
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        # e.g. the last message was not from the user.
        raise HTTPException(status_code=409, detail=str(e))
