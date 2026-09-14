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
# WhatsApp Business Account id -- needed to list/manage templates (Client.list_message_templates),
# a different Meta identifier from PHONE_NUMBER_ID. Not derivable via the phone-number lookup with
# every access token (observed live: Graph API rejects the whatsapp_business_account field as
# "nonexisting" for a messaging-scoped token) -- the real reference bridge configures this
# directly as its own env var (META_WHATSAPP_WABA_ID) rather than looking it up, so this does too.
WABA_ID = os.environ.get("META_WHATSAPP_WABA_ID", "").strip()
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

# Backstop against a runaway/abusive loop burning real claude CLI cost, not a conversational
# throttle (TASK-76, parity with the real system's CATCHUP_MODEL_RUNS_PER_HOUR): a normal
# back-and-forth never gets near this. Hitting it skips the brain call for that turn without
# losing the inbound message -- the catch-up driver (TASK-78) backfills the reply shortly after.
# 0 disables the cap entirely.
LUNA_MAX_CALLS_PER_HOUR = int(os.environ.get("WA_LUNA_MAX_CALLS_PER_HOUR", "20") or "20")

# A thread whose ball has been on us longer than this is flagged "stuck_reply" on GET /wa/threads
# (TASK-79) -- no invented notification channel (no email/Telegram integration exists in this
# repo), just a durable, discoverable flag a human or the catch-up driver can act on.
STUCK_REPLY_HOURS = float(os.environ.get("WA_STUCK_REPLY_HOURS", "2") or "2")

# Conversation ownership (TASK-75, app/wa/routing.py): a plain, newline-delimited, operator-
# produced export of phone numbers already known to the real production system -- unset means
# routing a brand-new phone cannot be decided at all (see routing._is_known_to_real_system), not
# that everything defaults one way or the other.
REAL_SYSTEM_PHONES_FILE = os.environ.get("WA_REAL_SYSTEM_PHONES_FILE", "").strip()

# Webhook router (TASK-84, app/wa/router.py): where to forward a 'them'-owned message. Empty
# means router.route_webhook() raises loudly on any 'them' message rather than silently dropping
# a real candidate's reply -- this is not registered as Meta's actual webhook URL by anything in
# this repo, that is a separate, explicitly-confirmed production change.
REAL_SYSTEM_WEBHOOK_URL = os.environ.get("WA_REAL_SYSTEM_WEBHOOK_URL", "").strip()

# Local-only internal webhook receiver (TASK-86, app/wa/router.py) -- for the alternative
# architecture where the REAL system stays Meta's primary webhook and forwards a brand-new lead
# to this harness over a same-host, loopback-only call instead of us receiving Meta directly.
# Off by default: a fresh/misconfigured deployment must opt in explicitly, since this endpoint
# accepts a payload with no Meta signature check at all (see _is_local_caller in router.py for
# the network-trust boundary that replaces it).
INTERNAL_WEBHOOK_ENABLED = os.environ.get("WA_INTERNAL_WEBHOOK_ENABLED", "").strip() in ("1", "true", "yes")

# Proactive follow-up nudges (TASK-85, app/wa/luna/followups.py) -- a scaled-down version of the
# real system's own tiered (15m/1h/4h) re-engagement. Fixed, reviewable text, not a model call --
# an unprompted, system-initiated message is not what luna_brain.turn()'s "the candidate just
# said X" contract was built for.
FOLLOWUP_TIER_MINUTES = [int(x) for x in os.environ.get("WA_FOLLOWUP_TIERS_MINUTES", "15,60,240").split(",") if x.strip()]
MAX_FOLLOWUPS_PER_STREAK = int(os.environ.get("WA_MAX_FOLLOWUPS_PER_STREAK", "4") or "4")
FOLLOWUP_NUDGE_DE = os.environ.get("WA_FOLLOWUP_NUDGE_DE", "").strip() or (
    "Nur zur Sicherheit nachgefragt – sind Sie noch da? Ich helfe gerne weiter, sobald Sie Zeit haben 🙂")

# Quiet hours for follow-up nudges only (TASK-92): the real reference system never sends its own
# proactive nudge during a candidate's likely sleep window -- this scaled-down version lacked that
# entirely until now. One fixed local-time window in one timezone, not per-candidate, since this
# board has no per-candidate timezone data (it is Bavaria-only, same reasoning as the rest of this
# harness). Hours are 0-23; START > END means the window wraps past midnight (default 21 -> 9).
# Deliberately NOT applied to catchup.py (TASK-78) -- a reply owed to something the candidate
# already said is never proactive, so it is never delayed by this.
QUIET_HOURS_START = int(os.environ.get("WA_QUIET_HOURS_START", "21") or "21")
QUIET_HOURS_END = int(os.environ.get("WA_QUIET_HOURS_END", "9") or "9")
QUIET_HOURS_TZ = os.environ.get("WA_QUIET_HOURS_TZ", "Europe/Berlin").strip() or "Europe/Berlin"


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
