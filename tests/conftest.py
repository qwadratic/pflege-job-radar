"""Shared test defaults. AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the
existing API tests keep calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test."""
import importlib
import os
import sys

import pytest

os.environ.setdefault("AUTH_DISABLED", "1")


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
    """app.wa.api finishes webhook turns in a background thread (TASK-99). Wait for it before this test's
    monkeypatches (SQLite path, Meta client, tokens) are undone, so no job runs against the real config."""
    yield
    api = sys.modules.get("app.wa.api")
    if api is not None:
        api.wait_for_background(timeout=120)
