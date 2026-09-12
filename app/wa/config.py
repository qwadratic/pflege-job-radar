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

# Claude model + reasoning effort for the luna brain. Chat-style turns don't need the highest
# effort tier by default; raise via env if quality on hard turns (ambiguous German, edge-case
# qualification) doesn't hold at this level.
LUNA_MODEL = os.environ.get("WA_LUNA_MODEL", "claude-opus-5").strip() or "claude-opus-5"
LUNA_EFFORT = os.environ.get("WA_LUNA_EFFORT", "medium").strip() or "medium"
# The luna brain calls the `claude` CLI (subprocess), not the Anthropic Python SDK -- it rides
# whatever auth that CLI already has on this host (OAuth session, API key, or apiKeyHelper),
# so this harness needs no ANTHROPIC_API_KEY of its own. Override the binary name/path only if
# `claude` is not the right one to invoke on PATH.
LUNA_CLAUDE_BIN = os.environ.get("WA_LUNA_CLAUDE_BIN", "claude").strip() or "claude"
LUNA_TIMEOUT_SEC = int(os.environ.get("WA_LUNA_TIMEOUT_SEC", "60") or "60")


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
