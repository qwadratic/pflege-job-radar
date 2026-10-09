"""TASK-457 (Ivan, 2026-10-09): Jev (TypeSafe AI) -- typed decisions with probabilities, over a
compact state.

WHY THIS EXISTS. The two decision gates on this rail (closing_gate.py -- judged before EVERY
reply a candidate sees; refusal.py -- the decline branch) each paid a ``claude -p`` haiku
subprocess per call: process spawn plus model inference on a 30-90 s timeout, a text model
bent into answering one binary question. Jev is a decision model: it does not generate prose,
it returns typed answers with probabilities (Choice / Noul / Score) off a compact ``state``,
in tens of milliseconds on the wire with input-only billing. So it takes over exactly the
work that IS a decision, and the haiku tier keeps what is text (expose_shrink.py's
clinic-description rewrite stays on the CLI).

WHAT DOES NOT COME WITH IT. Jev does no tool calling and keeps no session. That is why it sits
on the narrow stateless gates and never on the main turn: the main brain
(luna_brain.Client) is a tool-using agent resumed across a card's session, which is a
different shape of work (see tools_server.py).

CONTRACT, mirroring refusal._run_cli on purpose. decide() returns the raw ``decision`` object
and raises DecisionError on anything that is not a usable answer: missing key, HTTP failure,
a body without a decision. The caller (a gate's ``_live_transport``) converts that into the
gate's own safe-direction Verdict -- closes=True send-unchecked / is_refusal=False keep-talking
-- and logs it. This module never decides for itself what a failure means for the
conversation, the same asymmetry both gates document.
"""
import logging

import requests

from .. import config as C

log = logging.getLogger(__name__)

# The OpenRouter alpha endpoint that serves the TypeSafe decision models (probed 2026-10-09):
# POST {model, state, questions} -> {"decision": {name: value}}. /v1/chat/completions does not
# know this model, so this is a constant of the API, not configurable plumbing.
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class DecisionError(RuntimeError):
    """Anything that is not a usable Jev answer. No secret ever enters a message: the key goes
    out in a header only."""


def decide(state, questions, *, model=None, timeout_sec=None, transport=None, what="jev"):
    """One stateless Jev decision over ``state``.

    ``questions`` maps decision names to their spec -- ``{"question": str, "type": "noul" |
    "choice", "choices": [...], "threshold": float}`` -- passed to the API unchanged, so a
    spec shape the API accepts later needs no client change. ``state`` is the compact context
    the question may read; keep it small (Jev's window is 32k, and the gates' payloads are a
    sentence each anyway).

    Returns the parsed ``decision`` object (name -> value: a float for noul, a
    ``{"value", "confidence"}`` object for choice). Raises DecisionError on a missing key, an
    HTTP failure, or a body without a usable decision; the caller turns every raise into its
    own safe-direction verdict, never a propagation. ``transport`` is the HTTP seam tests
    inject: ``transport(url, payload, key, timeout) -> parsed JSON body`` -- the same
    ``_default_transport`` pattern app/wa/stt.py uses for its ASR call.
    """
    key = C.OPENROUTER_API_KEY
    if not key:
        raise DecisionError(f"{what}: WA_OPENROUTER_API_KEY is not set -- Jev decisions cannot run")
    call = transport or _live_post
    payload = {"model": model or C.JEV_MODEL, "state": state, "questions": questions}
    try:
        body = call(DECISIONS_URL, payload, key, timeout=timeout_sec or C.JEV_TIMEOUT_SEC)
    except DecisionError:
        raise
    except Exception as exc:  # DNS, connect/read timeout, transport's own errors -- one shape
        raise DecisionError(f"{what}: Jev call failed: {exc.__class__.__name__}: "
                            f"{str(exc)[:200]}") from exc
    if not isinstance(body, dict):
        raise DecisionError(f"{what}: Jev answer was not a JSON object: {str(body)[:200]!r}")
    decision = body.get("decision")
    if not isinstance(decision, dict) or not decision:
        raise DecisionError(f"{what}: Jev answer carried no decision: {str(body)[:300]!r}")
    return decision


def _live_post(url, payload, key, timeout):
    """The production HTTP seam: one POST, Bearer key in a header, parsed JSON back. Raises
    DecisionError on a non-2xx or a non-JSON body. The body preview is truncated -- it may
    quote the request state (candidate text), which is fine to log, but never the key."""
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code // 100 != 2:
        raise DecisionError(f"jev: HTTP {resp.status_code}: {resp.text[:200]!r}")
    try:
        return resp.json()
    except ValueError as exc:
        raise DecisionError(f"jev: response was not JSON: {resp.text[:200]!r}") from exc
