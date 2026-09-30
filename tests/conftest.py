"""Shared test defaults. AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the
existing API tests keep calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test."""
import importlib
import os
import sys

import pytest

os.environ.setdefault("AUTH_DISABLED", "1")

# No test may reach a live system through credentials it merely inherited (Ivan, 2026-09-25). The
# wa-harness session was started from a shell with the phone rail's rail.env loaded, so every pytest
# it ran saw WA_TRANSPORT=bridge and a real WA_BRIDGE_URL/WA_BRIDGE_TOKEN: tests that do not pin their
# transport posted to the live executor on the Mac mini and got HTTP 422 back (nothing was sent that
# day -- sent_today stayed 0 -- but only because the executor refused the fake numbers). Ivan asked for
# the caution to live in the tests rather than in how he starts the session, so it lives here: popped
# at import time, before any app module is imported, because app/wa/config.py freezes os.environ into
# module constants on first import and a later monkeypatch.delenv would come too late. A test that
# needs one of these sets its own fake value. SUPABASE_ANON_KEY is deliberately not on the list: it
# only reads the public board, and without it 27 tests fail on "PostgREST 401".
_LIVE_CREDENTIALS = (
    "WA_TRANSPORT", "WA_BRIDGE_URL", "WA_BRIDGE_TOKEN", "WA_BRIDGE_INBOUND_TOKEN", "WA_BRIDGE_PHONE_NUMBER_ID",
    "WA_AUTOSEND", "WA_REAL_SYSTEM_WEBHOOK_URL",
    "META_WHATSAPP_ACCESS_TOKEN", "META_WHATSAPP_APP_SECRET", "META_WHATSAPP_PHONE_NUMBER_ID",
    "META_WHATSAPP_VERIFY_TOKEN", "OPENAI_API_KEY",
)
for _name in _LIVE_CREDENTIALS:
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def _closing_gate_offline(monkeypatch):
    """app/wa/luna/closing_gate.py runs a `claude -p` subprocess on EVERY composed reply (Ivan's
    closing-bubble invariant, 2026-09-24). No offline test may spawn one, and none should depend on a
    live model's judgement either, so here the gate always answers "closes". Its transport is what is
    replaced, not closes_the_turn(), so the parse/verdict path still runs exactly as in production. A
    test that wants a rejection passes its own ``transport=`` (tests/test_wa_closing_gate.py) or
    patches this attribute itself -- a later monkeypatch.setattr in the test body wins over this one."""
    closing_gate = importlib.import_module("app.wa.luna.closing_gate")
    monkeypatch.setattr(closing_gate, "_live_transport", lambda payload_text: '{"closes": true}')


@pytest.fixture(autouse=True)
def _wa_background_idle(monkeypatch):
    """app.wa.api finishes webhook turns in a background thread (TASK-341). Wait for it before this test's
    monkeypatches (SQLite path, Meta client, tokens) are undone, so no job runs against the real config."""
    yield
    api = sys.modules.get("app.wa.api")
    if api is not None:
        api.wait_for_background(timeout=120)
