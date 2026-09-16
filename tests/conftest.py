"""Shared test defaults. AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the
existing API tests keep calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test."""
import os
import sys

import pytest

os.environ.setdefault("AUTH_DISABLED", "1")


@pytest.fixture(autouse=True)
def _wa_background_idle(monkeypatch):
    """app.wa.api finishes webhook turns in a background thread (TASK-99). Wait for it before this test's
    monkeypatches (SQLite path, Meta client, tokens) are undone, so no job runs against the real config."""
    yield
    api = sys.modules.get("app.wa.api")
    if api is not None:
        api.wait_for_background(timeout=120)
