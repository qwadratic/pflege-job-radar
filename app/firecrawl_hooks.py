"""Firecrawl agent webhook receiver (POST /api/firecrawl/webhook, docs/firecrawl.md).

Firecrawl posts here on agent.started / agent.action / agent.completed / agent.failed / agent.cancelled
(we ask for all five in webhook.events when submitting -- see pflege_jobs.sources.firecrawl_agent.run_agent's
`webhook=` and `check_webhook=` parameters). No signature header is documented for v2 webhooks, so the
caller authenticates with a shared secret it must echo back in webhook.headers['X-Pflege-Webhook-Secret'];
the secret is generated once (secrets.token_urlsafe) and stored under settings key 'firecrawl'.

Every event is stored verbatim in firecrawl_events (app/runs.py) regardless of what else happens, so the
poller in run_agent() can notice a webhook already answered a job and stop polling, and so the ONE
experiment run can prove (or disprove) that the webhook actually arrived -- pflege-board.exe.xyz is
login-gated unless the user has run `ssh exe.dev share set-public pflege-board`, which this VM cannot
check, so the webhook is best-effort and the polling fallback in run_agent() is what actually matters.

We always answer 200 within a couple hundred ms; anything that could be slow (running the adapter probe,
posting to Supabase) either isn't done here (spend_gate already ran before submission) or is cheap (posting
already-converted rows straight to the inbox)."""
import secrets

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from . import data as D
from . import runs as R

router = APIRouter()

TERMINAL_EVENTS = ("agent.completed", "agent.failed", "agent.cancelled")


def get_webhook_secret():
    """The shared secret to put in webhook.headers['X-Pflege-Webhook-Secret'] when submitting a job.
    Generated once (secrets.token_urlsafe(24)) and persisted under settings key 'firecrawl'."""
    fc = R.get_setting("firecrawl") or {}
    s = fc.get("webhook_secret")
    if not s:
        s = secrets.token_urlsafe(24)
        fc = dict(fc)
        fc["webhook_secret"] = s
        R.set_setting("firecrawl", fc)
    return s


def _first_data_dict(payload, items):
    """v2 webhook payloads carry the agent's answer as data: [{creditsUsed, data}, ...]; be tolerant of a
    bare data: {...} too, since that is what a synchronous /v2/agent response and our own tests use."""
    for it in items or []:
        d = (it or {}).get("data")
        if isinstance(d, dict):
            return d
    d = payload.get("data")
    return d if isinstance(d, dict) else {}


def _run_log(run_id, line):
    if run_id:
        try:
            R.log(int(run_id), line)
        except Exception:
            pass


def _handle_completed(payload, items, clinic_id, run_id, credits_used, job_id=None):
    from pflege_jobs.sources import firecrawl_agent as FA
    from . import crawl as CR
    data = _first_data_dict(payload, items)
    n_jobs = len((data or {}).get("jobs") or [])
    clinic = D.clinic(clinic_id) if clinic_id else None
    n_rows = 0
    if clinic and data:
        rows = FA.jobs_to_inbox_rows(data, clinic)
        n_rows = len(rows)
        if rows:
            CR._post_inbox(rows, log=(lambda *a: _run_log(run_id, " ".join(str(x) for x in a))))
    if run_id:                                 # keyed by job id: updates the row the poller's on_submit wrote instead of adding a second one
        R.add_usage("jobs" if clinic_id else "webhook", clinic_id, credits_used, int(run_id), job_id=job_id)
    _run_log(run_id, f"firecrawl webhook: agent.completed, {credits_used} credits, {n_jobs} jobs -> {n_rows} inbox rows"
                     + (f"; blocked_reason: {data.get('blocked_reason')[:200]}" if isinstance(data, dict) and data.get("blocked_reason") else ""))


def _handle_terminal_failure(event_type, payload, clinic_id, run_id, credits_used, job_id=None):
    if run_id:
        R.add_usage("jobs" if clinic_id else "webhook", clinic_id, credits_used, int(run_id), job_id=job_id)
    _run_log(run_id, f"firecrawl webhook: {event_type} error={str(payload.get('error'))[:250]}")


@router.post("/firecrawl/webhook")
async def firecrawl_webhook(request: Request, x_pflege_webhook_secret: str = Header(default=None)):
    expected = get_webhook_secret()
    if not x_pflege_webhook_secret or x_pflege_webhook_secret != expected:
        return JSONResponse({"error": "bad or missing X-Pflege-Webhook-Secret"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event_type = payload.get("type") or "unknown"
    job_id = payload.get("id")
    meta = payload.get("metadata") or {}
    clinic_id, run_id = meta.get("clinic_id"), meta.get("run_id")
    items = payload.get("data") if isinstance(payload.get("data"), list) else None
    credits_used = sum(int((it or {}).get("creditsUsed") or 0) for it in (items or [])) or int(payload.get("creditsUsed") or 0)
    R.add_firecrawl_event(event_type, job_id, clinic_id=clinic_id, run_id=run_id,
                           success=payload.get("success"), credits_used=credits_used, raw=payload)
    try:
        if event_type == "agent.completed":
            _handle_completed(payload, items, clinic_id, run_id, credits_used, job_id=job_id)
        elif event_type in ("agent.failed", "agent.cancelled"):
            _handle_terminal_failure(event_type, payload, clinic_id, run_id, credits_used, job_id=job_id)
        elif event_type == "agent.action":
            _run_log(run_id, f"firecrawl webhook: agent.action {str(payload.get('action') or payload.get('message') or '')[:200]}")
        elif event_type == "agent.started":
            _run_log(run_id, f"firecrawl webhook: agent.started job {job_id}")
    except Exception as e:                     # the ack must not depend on downstream processing succeeding
        _run_log(run_id, f"firecrawl webhook handling error ({event_type}): {type(e).__name__}: {str(e)[:200]}")
    return {"ok": True}
