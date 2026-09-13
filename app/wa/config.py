"""Env and paths for the WhatsApp harness.

The META_WHATSAPP_* names are the ones the production bridge on tasker-dispatcher-01 already
uses (apps/connectors/meta_whatsapp_cloud.py), so one Meta app and one .env fit both.
Nothing here has a default that could pass for a configured value: an unset token stays empty and
the send path raises rather than pretend (CLAUDE.md, "No safety nets").
"""
import os
import shutil

from .. import config as A

GRAPH_API_VERSION = os.environ.get("META_WHATSAPP_GRAPH_API_VERSION", "v25.0").strip() or "v25.0"
ACCESS_TOKEN = os.environ.get("META_WHATSAPP_ACCESS_TOKEN", "").strip()
APP_SECRET = os.environ.get("META_WHATSAPP_APP_SECRET", "").strip()
VERIFY_TOKEN = os.environ.get("META_WHATSAPP_VERIFY_TOKEN", "").strip()
PHONE_NUMBER_ID = os.environ.get("META_WHATSAPP_PHONE_NUMBER_ID", "").strip()
DEFAULT_COUNTRY_CODE = os.environ.get("META_WHATSAPP_DEFAULT_COUNTRY_CODE", "49").strip() or "49"
HTTP_TIMEOUT_SEC = 30

# WhatsApp Cloud API policy (not this repo's choice): free-form text is only allowed within this
# many hours of the candidate's last message; outside it, only a pre-approved template message
# goes through (TASK-70). 24 is Meta's actual rule -- overridable only for testing.
FREEFORM_WINDOW_HOURS = float(os.environ.get("WA_FREEFORM_WINDOW_HOURS", "24") or "24")
# Must already be approved in Meta Business Manager -- this repo cannot create one. Empty means
# "not configured yet": a thread whose window has closed then fails loudly (app/wa/api.py) rather
# than silently sending free-form text Meta would reject, or silently doing nothing.
WA_REOPEN_TEMPLATE_NAME = os.environ.get("WA_REOPEN_TEMPLATE_NAME", "").strip()
WA_REOPEN_TEMPLATE_LANG = os.environ.get("WA_REOPEN_TEMPLATE_LANG", "de").strip() or "de"

# The harness keeps its own SQLite file: the board's app.sqlite is rebuilt by crawl/run bookkeeping,
# and a conversation must outlive that.
SQLITE_PATH = A.DATA_DIR / "wa.sqlite"

# Off by default. With WA_AUTOSEND unset the harness still parses, stores and decides -- it just does
# not hand anything to Meta, so a webhook can be pointed at a fresh deployment without messaging anyone.
AUTOSEND = os.environ.get("WA_AUTOSEND", "").strip() in ("1", "true", "yes")

# Which brain answers a turn: "deterministic" (app/wa/brain.py, the board-filter question ladder,
# no LLM) or "luna" (app/wa/luna_brain.py, same persona/rules/gates as the reference this is
# adapted from -- app/wa/luna/VENDORED.md -- but calling Claude to decide the action and wording).
BRAIN = os.environ.get("WA_BRAIN", "deterministic").strip().lower()
if BRAIN not in ("deterministic", "luna"):
    raise RuntimeError(f"WA_BRAIN={BRAIN!r} is not 'deterministic' or 'luna'")

# Claude model + reasoning effort for the luna brain. A per-turn WhatsApp reply is a chat-shaped
# workload, not a hard reasoning one, so this defaults to Sonnet rather than Opus -- Haiku is the
# cheaper/faster option (WA_LUNA_MODEL=claude-haiku-4-5) if quality on the turns this repo's own
# gates already carry (qualification, region) holds up at that tier; raise back to Opus if a
# quality regression shows up on ambiguous German instead.
LUNA_MODEL = os.environ.get("WA_LUNA_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"
# "high" rather than "medium": once tools_server.py is wired in (--mcp-config), deciding whether a
# turn needs a live lookup, which tool, and with what arguments is real planning work, not just
# wording a reply -- accepted values are low/medium/high/xhigh/max (`claude -p --help`); "high" is
# the deliberate middle ground between that and the added latency a WhatsApp reply can tolerate.
LUNA_EFFORT = os.environ.get("WA_LUNA_EFFORT", "high").strip() or "high"
# The luna brain calls the `claude` CLI (subprocess), not the Anthropic Python SDK -- it rides
# whatever auth that CLI already has on this host (OAuth session, API key, or apiKeyHelper),
# so this harness needs no ANTHROPIC_API_KEY of its own. Override the binary name/path only if
# `claude` is not the right one to invoke on PATH.
LUNA_CLAUDE_BIN = os.environ.get("WA_LUNA_CLAUDE_BIN", "claude").strip() or "claude"
# 60s (this repo's original default) started timing out for real once TASK-62 added a tool
# call in the loop and bumped effort to "high" -- both add real latency on top of the base
# reply time, observed live during TASK-68's E2E run (subprocess.TimeoutExpired at 60s on an
# otherwise-ordinary turn). 120s gives that room without hiding a genuinely stuck process forever.
LUNA_TIMEOUT_SEC = int(os.environ.get("WA_LUNA_TIMEOUT_SEC", "120") or "120")
# Claude Code keys a resumable session by session id *and* the working directory it was started
# in (session transcripts live under a path derived from cwd). Every luna turn for every phone
# number must run from this exact directory, or `--resume <id>` from a later turn silently looks
# in the wrong place and starts a fresh, memory-less session instead of continuing the real one.
LUNA_SESSION_DIR = A.DATA_DIR / "wa_luna_sessions"


def readiness():
    """Non-secret view of what is configured, for GET /api/wa/health and the webhook's own log."""
    checks = {"access_token": bool(ACCESS_TOKEN), "app_secret": bool(APP_SECRET),
              "verify_token": bool(VERIFY_TOKEN), "phone_number_id": bool(PHONE_NUMBER_ID)}
    out = {"checks": checks,
           "webhook_ready": checks["app_secret"] and checks["verify_token"],
           "outbound_ready": checks["access_token"] and checks["phone_number_id"],
           "autosend": AUTOSEND, "graph_api_version": GRAPH_API_VERSION, "brain": BRAIN}
    if BRAIN == "luna":
        out["luna_model"] = LUNA_MODEL
        out["luna_ready"] = bool(shutil.which(LUNA_CLAUDE_BIN))
    return out
