"""Env and paths for the WhatsApp harness.

The META_WHATSAPP_* names are the ones the production bridge on tasker-dispatcher-01 already
uses (apps/connectors/meta_whatsapp_cloud.py), so one Meta app and one .env fit both.
Nothing here has a default that could pass for a configured value: an unset token stays empty and
the send path raises rather than pretend (CLAUDE.md, "No safety nets").
"""
import os

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


def readiness():
    """Non-secret view of what is configured, for GET /api/wa/health and the webhook's own log."""
    checks = {"access_token": bool(ACCESS_TOKEN), "app_secret": bool(APP_SECRET),
              "verify_token": bool(VERIFY_TOKEN), "phone_number_id": bool(PHONE_NUMBER_ID)}
    return {"checks": checks,
            "webhook_ready": checks["app_secret"] and checks["verify_token"],
            "outbound_ready": checks["access_token"] and checks["phone_number_id"],
            "autosend": AUTOSEND, "graph_api_version": GRAPH_API_VERSION}
