"""Env and paths for the WhatsApp harness.

The META_WHATSAPP_* names are the ones the production bridge on tasker-dispatcher-01 already
uses (apps/connectors/meta_whatsapp_cloud.py), so one Meta app and one .env fit both.
Nothing here has a default that could pass for a configured value: an unset token stays empty and
the send path raises rather than pretend (CLAUDE.md, "No safety nets").
"""
import json
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
# goes through (TASK-414). 24 is Meta's actual rule -- overridable only for testing.
FREEFORM_WINDOW_HOURS = float(os.environ.get("WA_FREEFORM_WINDOW_HOURS", "24") or "24")
# Must already be approved in Meta Business Manager -- this repo cannot create one. Empty means
# "not configured yet": a thread whose window has closed then fails loudly (app/wa/api.py) rather
# than silently sending free-form text Meta would reject, or silently doing nothing.
WA_REOPEN_TEMPLATE_NAME = os.environ.get("WA_REOPEN_TEMPLATE_NAME", "").strip()
WA_REOPEN_TEMPLATE_LANG = os.environ.get("WA_REOPEN_TEMPLATE_LANG", "de").strip() or "de"

# The harness keeps its own SQLite file: the board's app.sqlite is rebuilt by crawl/run bookkeeping,
# and a conversation must outlive that.
SQLITE_PATH = A.DATA_DIR / "wa.sqlite"
# Inbound media originals (TASK-426, app/wa/api.py:_store_original): one owner-only subdirectory
# per phone, one file per inbound media message, each linked by a wa_documents row. Candidate PII:
# gitignored, never commit it.
DOCUMENTS_DIR = pathlib.Path(os.environ.get("WA_DOCUMENTS_DIR", "").strip() or A.DATA_DIR / "wa_documents")

# Off by default. With WA_AUTOSEND unset the harness still parses, stores and decides -- it just does
# not hand anything to Meta, so a webhook can be pointed at a fresh deployment without messaging anyone.
AUTOSEND = os.environ.get("WA_AUTOSEND", "").strip() in ("1", "true", "yes")

# Which transport carries an outbound message (TASK-349, app/wa/transport.py): "meta" is the Cloud
# API (app/wa/meta.py), "bridge" is the phone rail on the remote machine (app/wa/bridge.py). This is
# the rail a NEW thread starts on: an existing thread keeps the rail pinned on wa_threads (TASK-220),
# so flipping this variable never moves a live conversation to a different sender number. Same
# discipline as WA_BRAIN below: an unknown value stops the process at import, it never becomes a
# default that quietly sends.
TRANSPORT = os.environ.get("WA_TRANSPORT", "meta").strip().lower()
if TRANSPORT not in ("meta", "bridge"):
    raise RuntimeError(f"WA_TRANSPORT={TRANSPORT!r} is not 'meta' or 'bridge'")

# Send-scope kill switches (Ivan, 2026-09-27): a real candidate wrote to the old Meta/WABA number and
# got answered by this harness via the Cloud API (WA_OWN_ALL_CHATS routes every Meta inbound to us).
# Both gate through app/wa/transport.py:scope_refusal, which every outbound path crosses -- a test
# thread (store.is_test_thread) is always allowed regardless of either switch. Same discipline as
# WA_TRANSPORT above: an unrecognized value stops the process at import.
# WA_REPLY_SCOPE: both rails. "test_only" mutes every non-test thread everywhere -- the global switch.
REPLY_SCOPE = os.environ.get("WA_REPLY_SCOPE", "all").strip().lower()
if REPLY_SCOPE not in ("all", "test_only"):
    raise RuntimeError(f"WA_REPLY_SCOPE={REPLY_SCOPE!r} is not 'all' or 'test_only'")
# WA_META_SCOPE: the Meta rail only -- "the Meta channel muted but re-activatable" from Ivan's ask.
# "test_only" mutes Meta sends to a non-test thread while leaving the bridge (phone) rail alone.
META_SCOPE = os.environ.get("WA_META_SCOPE", "all").strip().lower()
if META_SCOPE not in ("all", "test_only"):
    raise RuntimeError(f"WA_META_SCOPE={META_SCOPE!r} is not 'all' or 'test_only'")

# The phone rail's executor (TASK-350, app/wa/bridge.py). WA_BRIDGE_URL is the server-side end of the
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
# POST /api/wa/bridge-webhook) is a separate secret, TASK-352: a leak in one direction must not grant
# the other.
BRIDGE_TOKEN = os.environ.get("WA_BRIDGE_TOKEN", "").strip()
# 90s, not HTTP_TIMEOUT_SEC's 30: a send here is a human-paced sequence of UI actions on a real
# handset -- open the chat, type at 3-5 characters per second, press send, then poll the bubble for a
# delivery tick -- and their own verify loop alone runs up to 30s (whatsapp.py:123). Plan §6.
BRIDGE_TIMEOUT_SEC = int(os.environ.get("WA_BRIDGE_TIMEOUT_SEC", "90") or "90")
# The other direction's secret (TASK-352): the executor pushes inbound to POST /api/wa/bridge-webhook
# with this one in X-Pflege-Bridge-Token. Separate from BRIDGE_TOKEN above on purpose -- a leak in one
# direction must not grant the other. Empty means the door is shut: app/wa/bridge_api.py answers 403
# rather than accept an unauthenticated payload that would enter a real conversation.
BRIDGE_INBOUND_TOKEN = os.environ.get("WA_BRIDGE_INBOUND_TOKEN", "").strip()
# metadata.phone_number_id in the envelope the executor pushes. The phone rail has no Meta
# phone-number id, so it carries its own name (e.g. "pflege-bridge-01") and api._number_matches
# accepts either that or PHONE_NUMBER_ID. META_WHATSAPP_PHONE_NUMBER_ID stays SET: blanking it would
# make _number_matches accept every number's payload instead of ours.
BRIDGE_PHONE_NUMBER_ID = os.environ.get("WA_BRIDGE_PHONE_NUMBER_ID", "").strip()

# Where show_clinic_photos (app/wa/luna/tools_server.py) stages a downloaded clinic photo before
# BR.Client().send_gallery can reach it -- an ssh alias + a directory on the SAME handset machine
# BRIDGE_URL above talks to over HTTP, but reached directly over ssh/scp instead, so it is its own
# pair rather than reusing BRIDGE_URL (a different leg, a different protocol) or
# WA_BRIDGE_SSH_HOST (bridge/relay_pull.py's own alias for the inbound pull tunnel, read from a
# different env file). TASK-272: these were bare string literals in tools_server.py with no env
# override and no readiness() entry, so a host-alias or username change turned every photo send
# into a ToolError with nothing in the health view showing why. Defaults match the old literals, so
# an unconfigured deploy is unchanged.
LUNA_MEDIA_HOST = os.environ.get("WA_LUNA_MEDIA_HOST", "").strip() or "macmini"
LUNA_MEDIA_DIR = os.environ.get("WA_LUNA_MEDIA_DIR", "").strip() or "/home/cursorworker1/wa_luna_media"

# TASK-351: on the phone rail there is no button to tap at all, so app/wa/luna/choices.py (TASK-224)
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

# Claude model + reasoning effort for the luna brain. Raised to Opus 5 at "max" effort, Ivan
# 2026-09-24, in direct response to Valentyn's live CONVERGE incident (his test thread:
# "Wo ist die Klinik?" asked twice, three non-converging replies, empty card -- see
# luna_brain.py::_checked_reply's CONVERGE check, added the same day). Ivan's own words: "повысь
# до опуса 5. повысь thinking." -- an explicit, later override of TASK-287's "high"/Sonnet choice
# (which was about first-turn latency, a different tradeoff); this one is about reply quality on
# the dialog-rules gates (CONVERGE, grounding, style) actually holding under real conversation
# pressure. Known cost: TASK-287's own profiling showed cli_duration_ms scales with num_turns and
# reasoning depth, so this raises tail latency on cold, multi-tool-call turns -- accepted
# knowingly, not a regression.
# 2026-10-05, Ivan: Opus 5.5 at effort "high" ("модель можно на opus 5.5 обновить?" -> effort "high"). Until then
# production ran Sonnet 5/high through a WA_LUNA_MODEL/WA_LUNA_EFFORT override in .env while the llm tests ran
# this default (Opus 5/max) -- the tests checked a model production did not use. The default is now the one
# production runs; the .env override is dropped at deploy.
LUNA_MODEL = os.environ.get("WA_LUNA_MODEL", "claude-opus-5-5").strip() or "claude-opus-5-5"
# See LUNA_MODEL's comment just above -- same 2026-09-24 CONVERGE-incident decision, same
# explicit override of TASK-287's "high". Accepted values are low/medium/high/xhigh/max
# (`claude -p --help`).
LUNA_EFFORT = os.environ.get("WA_LUNA_EFFORT", "high").strip() or "high"
# The luna brain calls the `claude` CLI (subprocess), not the Anthropic Python SDK -- it rides
# whatever auth that CLI already has on this host (OAuth session, API key, or apiKeyHelper),
# so this harness needs no ANTHROPIC_API_KEY of its own. Override the binary name/path only if
# `claude` is not the right one to invoke on PATH.
LUNA_CLAUDE_BIN = os.environ.get("WA_LUNA_CLAUDE_BIN", "claude").strip() or "claude"
# 60s (this repo's original default) started timing out for real once TASK-323 added a tool
# call in the loop and bumped effort to "high" -- both add real latency on top of the base
# reply time, observed live during TASK-328's E2E run (subprocess.TimeoutExpired at 60s on an
# otherwise-ordinary turn). 120s gave that room for an ordinary turn, but TASK-271 found nobody
# had ever checked it against the one tool call that costs the most: show_clinic_photos
# (app/wa/luna/tools_server.py) downloads and stages up to 5 photos and then makes ONE
# send_gallery call that is, on its own, already budgeted at GALLERY_BUDGET_SEC = 187s
# (app/wa/bridge.py) for an EMPTY caption -- more than the whole 120s this subprocess.run() gave
# the CLI to finish the entire turn in. A killed subprocess.run() does not stop that send either:
# it kills the direct `claude` child only (no process group), so the MCP tools_server.py
# grandchild the CLI spawned via --mcp-config keeps running the call to completion, including its
# own wa_messages row, orphaned and unaware the turn that started it was declared failed.
# Derived the same way DESTROY_BUDGET_SEC is (app/wa/bridge.py), term by term, not a round number
# picked apart from the tool that runs inside it. This module cannot import app/wa/bridge.py to
# read GALLERY_BUDGET_SEC directly -- bridge.py itself imports this module first, and config.py
# is meant to be the leaf everything else depends on -- so the terms are literal copies of
# figures bridge.py/tools_server.py already publish in their own comments, and
# tests/test_wa_luna_brain.py cross-checks them against bridge.py's own constants so a future
# change to either side cannot drift out of sync silently the way this one did:
#   the clinic lookup        tools_server.py _fetch_clinic_expose's own urlopen timeout, 15s
#   worst-case photo count   tools_server.py show_clinic_photos slices photo_paths[:5] -- 5
#   per photo, download+stage  _download_to_temp timeout 20s + _stage_on_mini's ssh mkdir
#                            timeout 20s + scp timeout 30s = 70s, paid once per photo, before any
#                            send starts
#   the one gallery send     GALLERY_BUDGET_SEC = 187s (app/wa/bridge.py), empty caption
# 15 + 5*70 + 187 = 552s. This adds nothing for the model's own generation time before or after
# the tool call -- LUNA_TIMEOUT_SEC wraps the whole `claude -p` subprocess, not just one tool --
# because unlike the figures above there is no measured number for that yet; if it turns out to
# matter, measure it live the way TASK-328 measured the original 60s overrun, do not guess it
# here. Real cost of raising this floor: an ordinary, genuinely hung turn that never calls
# show_clinic_photos at all now also waits up to 552s before this call gives up on it, where it
# used to wait 120s -- unavoidable as long as one constant covers every turn shape.
_LUNA_PHOTO_EXPOSE_FETCH_SEC = 15
_LUNA_PHOTO_MAX_COUNT = 5
_LUNA_PHOTO_DOWNLOAD_STAGE_SEC = 70
_LUNA_PHOTO_GALLERY_BUDGET_SEC = 187   # app/wa/bridge.py GALLERY_BUDGET_SEC, copied not imported
_LUNA_TIMEOUT_DEFAULT_SEC = (_LUNA_PHOTO_EXPOSE_FETCH_SEC
                             + _LUNA_PHOTO_MAX_COUNT * _LUNA_PHOTO_DOWNLOAD_STAGE_SEC
                             + _LUNA_PHOTO_GALLERY_BUDGET_SEC)
LUNA_TIMEOUT_SEC = int(os.environ.get("WA_LUNA_TIMEOUT_SEC", str(_LUNA_TIMEOUT_DEFAULT_SEC))
                      or str(_LUNA_TIMEOUT_DEFAULT_SEC))

# The refusal classifier (app/wa/luna/refusal.py, TASK-384, Ivan 2026-09-22): a small-model second
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
# reasons, so this began at 20s. Measured on this host (TASK-386 verification, 2026-09-22): 2 of 6
# probes exceeded 20s and every one of them completed inside 90s. "Not a refusal" is the safe
# direction for a WRONG answer, but a TIMEOUT is not free: the decline is then not honoured, so a
# candidate who typed a terse "Nein" stays in the follow-up nudge population -- the exact harm
# TASK-386 closed, reached through latency instead. The call runs only on the decline path, which is
# rare, so waiting costs almost nothing and finishing is worth much more than finishing fast.
_REFUSAL_TIMEOUT_RAW = os.environ.get("WA_REFUSAL_TIMEOUT_SEC", "90").strip() or "90"
try:
    REFUSAL_TIMEOUT_SEC = int(_REFUSAL_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_REFUSAL_TIMEOUT_SEC={_REFUSAL_TIMEOUT_RAW!r} is not an integer")
if REFUSAL_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_REFUSAL_TIMEOUT_SEC={REFUSAL_TIMEOUT_SEC} must be a positive number of seconds")

# The exposé shrinker (app/wa/luna/expose_shrink.py, Ivan 2026-09-24): a small-model rewrite of the
# board's own Firecrawl-researched clinic paragraph -- shorter, less enumeration, selling the vacancy
# -- run once per show_clinic_photos call, on the caption a candidate actually receives (whether sent
# raw as a photo's caption or handed to the main brain to write in its own words). Same reasoning as
# REFUSAL_MODEL above: a narrow, isolated rewrite, not the conversation itself, so its own cheap Haiku
# constant rather than LUNA_MODEL. Unlike the refusal classifier this runs on the funnel's CLIMAX
# moment, not a rare decline path (TASK-287 is literally about turn latency) -- the timeout starts
# tight on purpose, and a timeout or any other failure falls back to the ORIGINAL, unshortened text
# rather than blocking or failing the send (see expose_shrink.py's own docstring for the asymmetry).
EXPOSE_SHRINK_MODEL = os.environ.get("WA_EXPOSE_SHRINK_MODEL", "claude-haiku-4-5").strip()
if not EXPOSE_SHRINK_MODEL:
    raise RuntimeError("WA_EXPOSE_SHRINK_MODEL is set but empty -- unset it for the default "
                       "(claude-haiku-4-5) or give it a real model id")
_EXPOSE_SHRINK_TIMEOUT_RAW = os.environ.get("WA_EXPOSE_SHRINK_TIMEOUT_SEC", "15").strip() or "15"
try:
    EXPOSE_SHRINK_TIMEOUT_SEC = int(_EXPOSE_SHRINK_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_EXPOSE_SHRINK_TIMEOUT_SEC={_EXPOSE_SHRINK_TIMEOUT_RAW!r} is not an integer")
if EXPOSE_SHRINK_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_EXPOSE_SHRINK_TIMEOUT_SEC={EXPOSE_SHRINK_TIMEOUT_SEC} must be a positive "
                       "number of seconds")

# The operator-inbox gate (app/wa/luna/agent_note_gate.py, Ivan 2026-09-24). Its own cheap constant for
# the same reason REFUSAL_MODEL has one: a one-shot, tool-less, history-less classification of a single
# message, never the conversation itself -- the Haiku tier, never LUNA_MODEL. Same `claude` CLI as
# LUNA_CLAUDE_BIN; no second model-calling mechanism.
AGENT_NOTE_MODEL = os.environ.get("WA_AGENT_NOTE_MODEL", "claude-haiku-4-5").strip()
if not AGENT_NOTE_MODEL:
    raise RuntimeError("WA_AGENT_NOTE_MODEL is set but empty -- unset it for the default "
                       "(claude-haiku-4-5) or name a real model id")
# 30s, not REFUSAL_TIMEOUT_SEC's 90: this call sits in FRONT of a turn someone is waiting on, so its
# timeout is added to reply latency. It only runs on a test thread whose message actually contains
# Cyrillic, so no real candidate turn ever pays it, and a timeout falls through to the ordinary turn
# (the safe direction -- see agent_note_gate.py's own asymmetry note).
_AGENT_NOTE_TIMEOUT_RAW = os.environ.get("WA_AGENT_NOTE_TIMEOUT_SEC", "30").strip() or "30"
try:
    AGENT_NOTE_TIMEOUT_SEC = int(_AGENT_NOTE_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_AGENT_NOTE_TIMEOUT_SEC={_AGENT_NOTE_TIMEOUT_RAW!r} is not an integer")
if AGENT_NOTE_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_AGENT_NOTE_TIMEOUT_SEC={AGENT_NOTE_TIMEOUT_SEC} must be a positive number "
                       "of seconds")

# The operator-note WORKER (app/wa/luna/agent_note_worker.py, TASK-303, Ivan 2026-09-24/25): the two
# claude -p calls it makes per note, decode and hand-off. Model/effort are Ivan's own crystallised
# choice from the card, not this repo's usual cheap-classifier default -- decode restates a whole note
# plus conversation/card context into a structured object (more work than AGENT_NOTE_MODEL's single
# boolean), and the hand-off's ListAgents/SendMessage tool use is the one place this feature can affect
# another live session, so both get the bigger model. Env-overridable like every model choice in this
# file, though neither is expected to live in .env -- these are the worker's own knobs, not part of the
# service's checked-in EnvironmentFile (app/wa/envfile.py).
#
# NO DOLLAR-BUDGET KNOB (TASK-303, round-1 review, item E; Ivan 2026-09-25: "лимит бюджета снять" --
# the CLI has no token cap to use instead). AGENT_NOTE_DECODE_BUDGET_USD/AGENT_NOTE_HANDOFF_BUDGET_USD
# and the --max-budget-usd flag they fed agent_note_worker.py's two claude -p calls are REMOVED, not
# just unused: CLAUDE.md's "no safety nets" -- a cap nobody asked for is exactly the thing that rule
# exists to keep out, and Ivan asked for the opposite of one. What replaces it is visibility, not a
# limit: every call's real token usage and cost (input, output, cache read/create, total_cost_usd)
# is written to the note's own progress trail and to worker.log by agent_note_worker.py's own
# usage-logging helpers -- see that module's WHY comment on _usage_from_stdout for the exact envelope
# fields this reads.
AGENT_NOTE_DECODE_MODEL = os.environ.get("WA_AGENT_NOTE_DECODE_MODEL", "sonnet").strip() or "sonnet"
AGENT_NOTE_DECODE_EFFORT = os.environ.get("WA_AGENT_NOTE_DECODE_EFFORT", "medium").strip() or "medium"
# 120s: a decode prompt carries up to 20 messages, a compact card and up to 5 earlier notes on top of
# the note itself -- more input than AGENT_NOTE_TIMEOUT_SEC's single-message classification budgets
# for, at "medium" effort rather than "low". Measured live 2026-09-25 (TASK-303 build): a real decode
# call of this shape completed in ~3.3s, so this is headroom, not the expected case.
_AGENT_NOTE_DECODE_TIMEOUT_RAW = os.environ.get("WA_AGENT_NOTE_DECODE_TIMEOUT_SEC", "120").strip() or "120"
try:
    AGENT_NOTE_DECODE_TIMEOUT_SEC = int(_AGENT_NOTE_DECODE_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_AGENT_NOTE_DECODE_TIMEOUT_SEC={_AGENT_NOTE_DECODE_TIMEOUT_RAW!r} is not an "
                       "integer")
if AGENT_NOTE_DECODE_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_AGENT_NOTE_DECODE_TIMEOUT_SEC={AGENT_NOTE_DECODE_TIMEOUT_SEC} must be a "
                       "positive number of seconds")

AGENT_NOTE_HANDOFF_MODEL = os.environ.get("WA_AGENT_NOTE_HANDOFF_MODEL", "sonnet").strip() or "sonnet"
AGENT_NOTE_HANDOFF_EFFORT = os.environ.get("WA_AGENT_NOTE_HANDOFF_EFFORT", "low").strip() or "low"
# 60s: a ListAgents + (at most one) SendMessage round trip, nothing else -- measured live 2026-09-25
# (TASK-303 build): a real ListAgents-only probe of this exact shape completed in ~2.3s.
_AGENT_NOTE_HANDOFF_TIMEOUT_RAW = os.environ.get("WA_AGENT_NOTE_HANDOFF_TIMEOUT_SEC", "60").strip() or "60"
try:
    AGENT_NOTE_HANDOFF_TIMEOUT_SEC = int(_AGENT_NOTE_HANDOFF_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_AGENT_NOTE_HANDOFF_TIMEOUT_SEC={_AGENT_NOTE_HANDOFF_TIMEOUT_RAW!r} is not an "
                       "integer")
if AGENT_NOTE_HANDOFF_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_AGENT_NOTE_HANDOFF_TIMEOUT_SEC={AGENT_NOTE_HANDOFF_TIMEOUT_SEC} must be a "
                       "positive number of seconds")

# The exact session name the hand-off addresses (TASK-303 card, AC#5): pinned with `claude -n
# wa-harness` on the working session, resolved fresh through ListAgents at send time -- never a
# pattern, because a restarted session's OLD process can linger under a different display name (see
# the card's own 2026-09-25 note: 'Pflege Hire: WA Harness' vs the pinned 'wa-harness'). Zero or
# several exact matches is a failed hand-off, never a guess.
AGENT_NOTE_TARGET = os.environ.get("WA_AGENT_NOTE_TARGET", "wa-harness").strip() or "wa-harness"

# After this many attempts (claim_agent_note's own counter) a note that still cannot get through the
# worker's pipeline is closed as blocked rather than retried forever -- AC#8's "after 5 attempts".
_AGENT_NOTE_ATTEMPTS_CAP_RAW = os.environ.get("WA_AGENT_NOTE_ATTEMPTS_CAP", "5").strip() or "5"
try:
    AGENT_NOTE_ATTEMPTS_CAP = int(_AGENT_NOTE_ATTEMPTS_CAP_RAW)
except ValueError:
    raise RuntimeError(f"WA_AGENT_NOTE_ATTEMPTS_CAP={_AGENT_NOTE_ATTEMPTS_CAP_RAW!r} is not an integer")
if AGENT_NOTE_ATTEMPTS_CAP <= 0:
    raise RuntimeError(f"WA_AGENT_NOTE_ATTEMPTS_CAP={AGENT_NOTE_ATTEMPTS_CAP} must be a positive count")

# Where the worker writes health.json and nothing else touches (tools/agent_note_cron.sh writes here
# too, directly, only for a failure so early python never ran -- see that script's own comment). Not
# under DATA_DIR: this is process/health state, not harness data, and (unlike DATA_DIR) it must exist
# before the database does on a brand new box, since the shell wrapper's own guards write here first.
AGENT_NOTE_STATE_DIR = pathlib.Path(os.environ.get("WA_AGENT_NOTE_STATE_DIR", "").strip()
                                    or "/home/claude/.local/state/pflege-wa-agent-notes")

# The closing-bubble gate (app/wa/luna/closing_gate.py, Ivan 2026-09-24). Same tier and the same
# reasoning as AGENT_NOTE_MODEL: one boolean about one short reply, no tools, no history.
CLOSING_GATE_MODEL = os.environ.get("WA_CLOSING_GATE_MODEL", "claude-haiku-4-5").strip()
if not CLOSING_GATE_MODEL:
    raise RuntimeError("WA_CLOSING_GATE_MODEL is set but empty -- unset it for the default "
                       "(claude-haiku-4-5) or name a real model id")
# The same 30s as the agent-note gate, for the same reason and one more: this gate runs on EVERY
# candidate reply, not only on a test thread, so its timeout is added to every candidate's wait. A
# timeout sends the reply unchecked (closing_gate.py's asymmetry) rather than holding the turn.
_CLOSING_GATE_TIMEOUT_RAW = os.environ.get("WA_CLOSING_GATE_TIMEOUT_SEC", "30").strip() or "30"
try:
    CLOSING_GATE_TIMEOUT_SEC = int(_CLOSING_GATE_TIMEOUT_RAW)
except ValueError:
    raise RuntimeError(f"WA_CLOSING_GATE_TIMEOUT_SEC={_CLOSING_GATE_TIMEOUT_RAW!r} is not an integer")
if CLOSING_GATE_TIMEOUT_SEC <= 0:
    raise RuntimeError(f"WA_CLOSING_GATE_TIMEOUT_SEC={CLOSING_GATE_TIMEOUT_SEC} must be a positive "
                       "number of seconds")

# Claude Code keys a resumable session by session id *and* the working directory it was started
# in (session transcripts live under a path derived from cwd). Every luna turn for every phone
# number must run from this exact directory, or `--resume <id>` from a later turn silently looks
# in the wrong place and starts a fresh, memory-less session instead of continuing the real one.
LUNA_SESSION_DIR = A.DATA_DIR / "wa_luna_sessions"
# Where the Claude Code CLI keeps those sessions' transcripts: <store>/<a directory name derived from the
# cwd>/<session id>.jsonl. Not ours to write -- app/wa/luna/purge_test_history.py (TASK-212) only needs to
# find and delete a test number's transcript, which holds the whole conversation in plain text. Default and
# env var are the CLI's own (CLAUDE_CONFIG_DIR, else ~/.claude); it must be the one the service user runs
# with, or the purge finds nothing to delete and says so.
LUNA_SESSION_STORE = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
                                  or pathlib.Path.home() / ".claude") / "projects"

# Backstop against a runaway/abusive loop burning real claude CLI cost, not a conversational
# throttle (TASK-410, parity with the real system's CATCHUP_MODEL_RUNS_PER_HOUR): a normal
# back-and-forth never gets near this. Hitting it skips the brain call for that turn without
# losing the inbound message -- the catch-up driver (TASK-332) backfills the reply shortly after.
# 0 disables the cap entirely.
LUNA_MAX_CALLS_PER_HOUR = int(os.environ.get("WA_LUNA_MAX_CALLS_PER_HOUR", "20") or "20")

# A thread whose ball has been on us longer than this is flagged "stuck_reply" on GET /wa/threads
# (TASK-412) -- no invented notification channel (no email/Telegram integration exists in this
# repo), just a durable, discoverable flag a human or the catch-up driver can act on.
STUCK_REPLY_HOURS = float(os.environ.get("WA_STUCK_REPLY_HOURS", "2") or "2")

# TASK-302, Ivan's design update (2026-09-25): the warming turn's radius widening, when the
# candidate's own city has no matching posting. The knob IS the feature, not a safety cap -- how
# far "nearby" reaches for a candidate who would otherwise get no warming turn at all
# (app/wa/luna_brain.py:_warming_candidates).
LUNA_WARMING_RADIUS_KM = float(os.environ.get("WA_LUNA_WARMING_RADIUS_KM", "30") or "30")

# Conversation ownership (TASK-331, app/wa/routing.py): a plain, newline-delimited, operator-
# produced export of phone numbers already known to the real production system -- unset means
# routing a brand-new phone cannot be decided at all (see routing._is_known_to_real_system), not
# that everything defaults one way or the other.
REAL_SYSTEM_PHONES_FILE = os.environ.get("WA_REAL_SYSTEM_PHONES_FILE", "").strip()

# Ivan, 2026-09-23: "с этого момента чужих чатов нету -- все чаты наши, все кандидаты наши. Мы
# переезжаем." The split above exists because two systems shared one WhatsApp number and a wrong
# guess meant an existing customer treated as a cold lead. That is over: this harness now owns every
# conversation on the rail. Explicit and configured rather than a new default, because routing
# refuses to guess on principle (app/wa/routing.py's own docstring) -- and because the day the split
# comes back, this is the one line to turn off. It also removes the hard dependency on
# REAL_SYSTEM_PHONES_FILE: with this on, that export is never consulted.
OWN_ALL_CHATS = os.environ.get("WA_OWN_ALL_CHATS", "").strip().lower() in ("1", "true", "yes")

# Webhook router (TASK-419, app/wa/router.py): where to forward a 'them'-owned message. Empty
# means router.route_webhook() raises loudly on any 'them' message rather than silently dropping
# a real candidate's reply -- this is not registered as Meta's actual webhook URL by anything in
# this repo, that is a separate, explicitly-confirmed production change.
REAL_SYSTEM_WEBHOOK_URL = os.environ.get("WA_REAL_SYSTEM_WEBHOOK_URL", "").strip()

# Local-only internal webhook receiver (TASK-335, app/wa/router.py) -- for the alternative
# architecture where the REAL system stays Meta's primary webhook and forwards a brand-new lead
# to this harness over a same-host, loopback-only call instead of us receiving Meta directly.
# Off by default: a fresh/misconfigured deployment must opt in explicitly, since this endpoint
# accepts a payload with no Meta signature check at all (see _is_local_caller in router.py for
# the network-trust boundary that replaces it).
INTERNAL_WEBHOOK_ENABLED = os.environ.get("WA_INTERNAL_WEBHOOK_ENABLED", "").strip() in ("1", "true", "yes")

# Proactive follow-up nudges (TASK-420, app/wa/luna/followups.py) -- a scaled-down version of the
# real system's own tiered (15m/1h/4h) re-engagement. Fixed, reviewable text, not a model call --
# an unprompted, system-initiated message is not what luna_brain.turn()'s "the candidate just
# said X" contract was built for.
FOLLOWUP_TIER_MINUTES = [int(x) for x in os.environ.get("WA_FOLLOWUP_TIERS_MINUTES", "15,60,240").split(",") if x.strip()]
MAX_FOLLOWUPS_PER_STREAK = int(os.environ.get("WA_MAX_FOLLOWUPS_PER_STREAK", "4") or "4")
FOLLOWUP_NUDGE_DE = os.environ.get("WA_FOLLOWUP_NUDGE_DE", "").strip() or (
    "Nur zur Sicherheit nachgefragt – sind Sie noch da? Ich helfe gerne weiter, sobald Sie Zeit haben 🙂")

# Quiet hours for follow-up nudges only (TASK-425): the real reference system never sends its own
# proactive nudge during a candidate's likely sleep window -- this scaled-down version lacked that
# entirely until now. One fixed local-time window in one timezone, not per-candidate, since this
# board has no per-candidate timezone data (it is Bavaria-only, same reasoning as the rest of this
# harness). Hours are 0-23; START > END means the window wraps past midnight (default 21 -> 8).
# END is 8, not 9: Ivan widened the active window by an hour on 2026-09-23 ("давай начинать с
# восьми") -- Pflege shift handover is early and a candidate reading at 08:00 is awake, not asleep.
# Deliberately NOT applied to catchup.py (TASK-332) -- a reply owed to something the candidate
# already said is never proactive, so it is never delayed by this.
QUIET_HOURS_START = int(os.environ.get("WA_QUIET_HOURS_START", "21") or "21")
QUIET_HOURS_END = int(os.environ.get("WA_QUIET_HOURS_END", "8") or "8")
QUIET_HOURS_TZ = os.environ.get("WA_QUIET_HOURS_TZ", "Europe/Berlin").strip() or "Europe/Berlin"

# Voice-note transcription (TASK-210, app/wa/stt.py), WA_BRAIN=luna only. The old system's setup
# (apps/connectors/candidate_audio_stt.py): OpenAI's transcription endpoint, model whisper-1, key from
# OPENAI_API_KEY. Unset key: every voice note fails loudly and waits for catch-up, never a flat reply.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
STT_MODEL = os.environ.get("WA_STT_MODEL", "whisper-1").strip() or "whisper-1"
STT_TIMEOUT_SEC = int(os.environ.get("WA_STT_TIMEOUT_SEC", "120") or "120")


# --- Pro API (TASK-395/396, Ivan 2026-09-29/30, topology B) ---------------------------------------
# Read at REQUEST time, not frozen into a module constant like every env var above -- app/wa/pro_api.py
# checks these on every call, so a token can be rotated (or a test can flip it with monkeypatch.setenv)
# without restarting/reloading this module. Empty means "not configured": the caller fails closed
# (503), never open -- see pro_api.py's own auth helper.

def pro_api_token():
    """WA_API_TOKEN: the bearer every GET under /api/wa/pro/* accepts."""
    return os.environ.get("WA_API_TOKEN", "").strip()


def pro_api_write_token():
    """WA_API_WRITE_TOKEN: the bearer POST /api/wa/pro/handoffs requires. A read token
    (pro_api_token above) may never use this path; this one may also read (pro_api.py's auth
    helper checks it on every GET too)."""
    return os.environ.get("WA_API_WRITE_TOKEN", "").strip()


def sales_brain_path():
    """The colleague's CRM sqlite (TASK-396, Daria's leads read) -- read-only, never ours to write.
    Env-overridable for a test's synthetic fixture; the default is the real path on this host."""
    return os.environ.get("WA_SALES_BRAIN_PATH", "").strip() or "/opt/clinic-dispatcher/var/sales_brain.sqlite"


# --- Client identity (TASK-162: this repo is public, the client's name/domains are never committed
# here) -----------------------------------------------------------------------------------------
# config/wa-client.json is the real file (gitignored); config/wa-client.example.json is the committed,
# synthetic stand-in with the same shape. Every place that used to hard-code the client's name or mail
# domains (app/wa/luna/prompts.py, constitution.json, app/wa/luna_brain.py's greeting check,
# tools/daria_desk.py's ANSWER_SYSTEM, the email tools' own-domain sets) now reads it from here.
# "No safety nets" (CLAUDE.md): a missing file, unreadable JSON, a missing/empty "name" or a domain key
# that is not a list of strings raises loudly naming the path -- never a default that could pass for a
# real client identity.
_CLIENT_CACHE = {}


def client():
    """-> the parsed client-identity dict ({"name", "own_mail_domains", "partner_mail_domains"}).
    Path: WA_CLIENT_CONFIG if set, else <repo root>/config/wa-client.json. Cached per path -- a test
    that points WA_CLIENT_CONFIG elsewhere and monkeypatch.setenv's back gets a fresh read for the
    new path, not a stale cache entry from the first one."""
    path = os.environ.get("WA_CLIENT_CONFIG", "").strip() or str(A.ROOT / "config" / "wa-client.json")
    if path in _CLIENT_CACHE:
        return _CLIENT_CACHE[path]
    p = pathlib.Path(path)
    if not p.exists():
        raise RuntimeError(
            f"client config not found at {path!r} -- create it (config/wa-client.example.json shows "
            f"the shape) or set WA_CLIENT_CONFIG to point at it")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"client config at {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not str(data.get("name", "")).strip():
        raise RuntimeError(f"client config at {path!r} has no non-empty \"name\" key")
    for key in ("own_mail_domains", "partner_mail_domains"):
        domains = data.get(key)
        if not isinstance(domains, list) or not all(isinstance(d, str) and d.strip() for d in domains):
            raise RuntimeError(f"client config at {path!r}: \"{key}\" must be a list of domain strings")
    _CLIENT_CACHE[path] = data
    return data


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
           "reply_scope": REPLY_SCOPE, "meta_scope": META_SCOPE,
           # Which rail could send right now, independently of which one is selected: an operator
           # flipping WA_TRANSPORT must be able to see beforehand that the other rail is configured,
           # and afterwards that the live one is (TASK-350).
           "bridge_ready": checks["bridge_url"] and checks["bridge_token"],
           # The inbound door is a separate readiness: the rail can send while the executor still
           # cannot push a reply back (TASK-352), and an operator has to see which half is missing.
           "bridge_inbound_ready": checks["bridge_inbound_token"],
           "bridge_phone_number_id": BRIDGE_PHONE_NUMBER_ID,
           # Neither is a secret (a host alias, a directory path) -- shown directly, the same way
           # bridge_phone_number_id is above, so an operator can see what show_clinic_photos will
           # actually ssh/scp to without reading tools_server.py (TASK-272).
           "luna_media_host": LUNA_MEDIA_HOST, "luna_media_dir": LUNA_MEDIA_DIR,
           "stt_ready": checks["openai_api_key"], "stt_model": STT_MODEL}
    if BRAIN == "luna":
        out["luna_model"] = LUNA_MODEL
        out["luna_ready"] = bool(shutil.which(LUNA_CLAUDE_BIN))
        out["refusal_model"] = REFUSAL_MODEL
    return out
