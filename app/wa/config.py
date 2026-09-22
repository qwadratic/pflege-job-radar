"""Env and paths for the WhatsApp harness.

The META_WHATSAPP_* names are the ones the production bridge on tasker-dispatcher-01 already
uses (apps/connectors/meta_whatsapp_cloud.py), so one Meta app and one .env fit both.
Nothing here has a default that could pass for a configured value: an unset token stays empty and
the send path raises rather than pretend (CLAUDE.md, "No safety nets").
"""
import os
import pathlib
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
# Inbound media originals (TASK-95, app/wa/api.py:_store_original): one owner-only subdirectory
# per phone, one file per inbound media message, each linked by a wa_documents row. Candidate PII:
# gitignored, never commit it.
DOCUMENTS_DIR = pathlib.Path(os.environ.get("WA_DOCUMENTS_DIR", "").strip() or A.DATA_DIR / "wa_documents")

# Off by default. With WA_AUTOSEND unset the harness still parses, stores and decides -- it just does
# not hand anything to Meta, so a webhook can be pointed at a fresh deployment without messaging anyone.
AUTOSEND = os.environ.get("WA_AUTOSEND", "").strip() in ("1", "true", "yes")

# Which transport carries an outbound message (TASK-116, app/wa/transport.py): "meta" is the Cloud
# API (app/wa/meta.py), "bridge" is the phone rail on the remote machine (app/wa/bridge.py). This is
# the rail a NEW thread starts on: an existing thread keeps the rail pinned on wa_threads (TASK-117),
# so flipping this variable never moves a live conversation to a different sender number. Same
# discipline as WA_BRAIN below: an unknown value stops the process at import, it never becomes a
# default that quietly sends.
TRANSPORT = os.environ.get("WA_TRANSPORT", "meta").strip().lower()
if TRANSPORT not in ("meta", "bridge"):
    raise RuntimeError(f"WA_TRANSPORT={TRANSPORT!r} is not 'meta' or 'bridge'")

# The phone rail's executor (TASK-120, app/wa/bridge.py). WA_BRIDGE_URL is the server-side end of the
# one ssh -R leg -- the executor listens on the remote machine and the tunnel presents it on loopback
# here, which is also why no secret of Meta's ever travels: the WhatsApp account lives on the handset
# and this rail holds no credential of its own beyond these two tokens.
# Empty is "not configured", never a default that could pass for one: app/wa/bridge.py raises at send
# time (and only at send time, like meta.Client) rather than at import, because WA_TRANSPORT=bridge is
# a valid value on a host that has not been wired up yet.
BRIDGE_URL = os.environ.get("WA_BRIDGE_URL", "").strip()
if BRIDGE_URL and not BRIDGE_URL.startswith(("http://", "https://")):
    raise RuntimeError(f"WA_BRIDGE_URL={BRIDGE_URL!r} is not an http(s) URL")
# Bearer on every /v1 call to the executor. Its counterpart for the other direction (executor ->
# POST /api/wa/bridge-webhook) is a separate secret, TASK-123: a leak in one direction must not grant
# the other.
BRIDGE_TOKEN = os.environ.get("WA_BRIDGE_TOKEN", "").strip()
# 90s, not HTTP_TIMEOUT_SEC's 30: a send here is a human-paced sequence of UI actions on a real
# handset -- open the chat, type at 3-5 characters per second, press send, then poll the bubble for a
# delivery tick -- and their own verify loop alone runs up to 30s (whatsapp.py:123). Plan §6.
BRIDGE_TIMEOUT_SEC = int(os.environ.get("WA_BRIDGE_TIMEOUT_SEC", "90") or "90")
# The other direction's secret (TASK-123): the executor pushes inbound to POST /api/wa/bridge-webhook
# with this one in X-Pflege-Bridge-Token. Separate from BRIDGE_TOKEN above on purpose -- a leak in one
# direction must not grant the other. Empty means the door is shut: app/wa/bridge_api.py answers 403
# rather than accept an unauthenticated payload that would enter a real conversation.
BRIDGE_INBOUND_TOKEN = os.environ.get("WA_BRIDGE_INBOUND_TOKEN", "").strip()
# metadata.phone_number_id in the envelope the executor pushes. The phone rail has no Meta
# phone-number id, so it carries its own name (e.g. "pflege-bridge-01") and api._number_matches
# accepts either that or PHONE_NUMBER_ID. META_WHATSAPP_PHONE_NUMBER_ID stays SET: blanking it would
# make _number_matches accept every number's payload instead of ours.
BRIDGE_PHONE_NUMBER_ID = os.environ.get("WA_BRIDGE_PHONE_NUMBER_ID", "").strip()

# TASK-122: on the phone rail there is no button to tap at all, so app/wa/luna/choices.py (TASK-121)
# may recover a typed "ja"/"1" into app/wa/luna_brain.CONSENT_YES_ID -- the one place a typed reply
# stands in for a tap luna_brain.py otherwise requires. Plan ADDENDUM item 5 (Ivan, 2026-09-21):
# ships ON. Unlike AUTOSEND/INTERNAL_WEBHOOK_ENABLED above, an unrecognized value raises at import
# rather than silently reading as off -- this flag decides whether a candidate's own typed words can
# produce a documented consent record, so a typo in the env file must stop the process, not quietly
# change what consent means. Taking it back to tap-only needs no code change, only this var set off.
_SYNTHETIC_CONSENT_RAW = os.environ.get("WA_BRIDGE_SYNTHETIC_CONSENT", "").strip().lower()
if _SYNTHETIC_CONSENT_RAW in ("", "1", "true", "yes", "on"):
    SYNTHETIC_CONSENT = True
elif _SYNTHETIC_CONSENT_RAW in ("0", "false", "no", "off"):
    SYNTHETIC_CONSENT = False
else:
    raise RuntimeError(f"WA_BRIDGE_SYNTHETIC_CONSENT={_SYNTHETIC_CONSENT_RAW!r} is not a recognized "
                       f"boolean (1/true/yes/on, 0/false/no/off, or unset for the default ON)")

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

# The refusal classifier (app/wa/luna/refusal.py, TASK-155, Ivan 2026-09-22): a small-model second
# opinion on whether the candidate's own text is an unambiguous refusal to continue the conversation,
# called only at the one moment the brain is about to end a thread on a decline -- not on every turn,
# so this stays cheap where it matters. Its own model constant rather than reusing LUNA_MODEL: this is
# a narrow yes/no classification, not the conversation itself, so it defaults straight to the Haiku
# tier LUNA_MODEL's own comment already names as this repo's cheap option, never to Sonnet. Rides the
# same `claude` CLI as LUNA_CLAUDE_BIN above -- no second model-calling mechanism.
REFUSAL_MODEL = os.environ.get("WA_REFUSAL_MODEL", "claude-haiku-4-5").strip()
if not REFUSAL_MODEL:
    raise RuntimeError("WA_REFUSAL_MODEL is set but empty -- unset it for the default (claude-haiku-4-5) "
                       "or give it a real model id")
# A one-shot classification with no tools and no session needs none of LUNA_TIMEOUT_SEC's 120s
# reasons, so this began at 20s. Measured on this host (TASK-157 verification, 2026-09-22): 2 of 6
# probes exceeded 20s and every one of them completed inside 90s. "Not a refusal" is the safe
# direction for a WRONG answer, but a TIMEOUT is not free: the decline is then not honoured, so a
# candidate who typed a terse "Nein" stays in the follow-up nudge population -- the exact harm
# TASK-157 closed, reached through latency instead. The call runs only on the decline path, which is
# rare, so waiting costs almost nothing and finishing is worth much more than finishing fast.
_REFUSAL_TIMEOUT_RAW = os.environ.get("WA_REFUSAL_TIMEOUT_SEC", "90").strip() or "90"
try:
    REFUSAL_TIMEOUT_SEC = int(_REFUSAL_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_REFUSAL_TIMEOUT_SEC={_REFUSAL_TIMEOUT_RAW!r} is not an integer")
if REFUSAL_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_REFUSAL_TIMEOUT_SEC={REFUSAL_TIMEOUT_SEC} must be a positive number of seconds")

# Claude Code keys a resumable session by session id *and* the working directory it was started
# in (session transcripts live under a path derived from cwd). Every luna turn for every phone
# number must run from this exact directory, or `--resume <id>` from a later turn silently looks
# in the wrong place and starts a fresh, memory-less session instead of continuing the real one.
LUNA_SESSION_DIR = A.DATA_DIR / "wa_luna_sessions"
# Where the Claude Code CLI keeps those sessions' transcripts: <store>/<a directory name derived from the
# cwd>/<session id>.jsonl. Not ours to write -- app/wa/luna/purge_test_history.py (TASK-109) only needs to
# find and delete a test number's transcript, which holds the whole conversation in plain text. Default and
# env var are the CLI's own (CLAUDE_CONFIG_DIR, else ~/.claude); it must be the one the service user runs
# with, or the purge finds nothing to delete and says so.
LUNA_SESSION_STORE = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
                                  or pathlib.Path.home() / ".claude") / "projects"

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

# Voice-note transcription (TASK-107, app/wa/stt.py), WA_BRAIN=luna only. The old system's setup
# (apps/connectors/candidate_audio_stt.py): OpenAI's transcription endpoint, model whisper-1, key from
# OPENAI_API_KEY. Unset key: every voice note fails loudly and waits for catch-up, never a flat reply.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
STT_MODEL = os.environ.get("WA_STT_MODEL", "whisper-1").strip() or "whisper-1"
STT_TIMEOUT_SEC = int(os.environ.get("WA_STT_TIMEOUT_SEC", "120") or "120")


def readiness():
    """Non-secret view of what is configured, for GET /api/wa/health and the webhook's own log."""
    checks = {"access_token": bool(ACCESS_TOKEN), "app_secret": bool(APP_SECRET),
              "verify_token": bool(VERIFY_TOKEN), "phone_number_id": bool(PHONE_NUMBER_ID),
              "openai_api_key": bool(OPENAI_API_KEY),
              "bridge_url": bool(BRIDGE_URL), "bridge_token": bool(BRIDGE_TOKEN),
              "bridge_inbound_token": bool(BRIDGE_INBOUND_TOKEN)}
    out = {"checks": checks,
           "webhook_ready": checks["app_secret"] and checks["verify_token"],
           "outbound_ready": checks["access_token"] and checks["phone_number_id"],
           "autosend": AUTOSEND, "graph_api_version": GRAPH_API_VERSION, "brain": BRAIN,
           "transport": TRANSPORT,
           # Which rail could send right now, independently of which one is selected: an operator
           # flipping WA_TRANSPORT must be able to see beforehand that the other rail is configured,
           # and afterwards that the live one is (TASK-120).
           "bridge_ready": checks["bridge_url"] and checks["bridge_token"],
           # The inbound door is a separate readiness: the rail can send while the executor still
           # cannot push a reply back (TASK-123), and an operator has to see which half is missing.
           "bridge_inbound_ready": checks["bridge_inbound_token"],
           "bridge_phone_number_id": BRIDGE_PHONE_NUMBER_ID,
           "stt_ready": checks["openai_api_key"], "stt_model": STT_MODEL}
    if BRAIN == "luna":
        out["luna_model"] = LUNA_MODEL
        out["luna_ready"] = bool(shutil.which(LUNA_CLAUDE_BIN))
        out["refusal_model"] = REFUSAL_MODEL
    return out
