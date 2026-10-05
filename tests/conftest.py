"""Shared test defaults. AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the
existing API tests keep calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test."""
import copy
import importlib
import json
import os
import pathlib
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
# only reads the public board, and `_registry_rest_tripwire` below means an empty/wrong value never
# reaches PostgREST for the WA lane anyway -- it raises loudly instead (see that fixture's docstring).
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


# WA lane test set (plans/2026-10-05-merge-gate-fixtures.md): the files the fixture below guards.
# Exactly the glob the merge-gate task itself runs, so this never touches a board/crawler test.
_WA_LANE_PREFIXES = ("test_wa_", "test_bridge_")
_WA_LANE_EXTRA = ("test_app_wa_proxy.py", "test_auth.py", "test_app_api.py", "test_sip_guard_watch.py")

_REGISTRY_FIXTURE_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "wa_registry" / "registry_snapshot.json"
_registry_fixture_cache = None


def _registry_fixture():
    global _registry_fixture_cache
    if _registry_fixture_cache is None:
        with open(_REGISTRY_FIXTURE_PATH, encoding="utf-8") as f:
            _registry_fixture_cache = json.load(f)
    return _registry_fixture_cache


def _is_wa_lane_file(name):
    return name.startswith(_WA_LANE_PREFIXES) or name in _WA_LANE_EXTRA


@pytest.fixture(autouse=True)
def _registry_rest_tripwire(request, monkeypatch):
    """LOUD tripwire for the WA lane (2026-10-05 fix pass, M1 -- Ivan agreed the silent version below
    was itself the bug, CLAUDE.md "No safety nets"). If a test has NOT stubbed app.data._snap/_refresh
    itself (the `luna`/`wa`/`env`/... fixtures most WA-lane tests already request) and app/data.py's
    _build()/_housing_evidence() genuinely reach app.config.rest_get_all, that call now RAISES instead
    of quietly being served real-but-trimmed data.

    2026-10-05: an earlier version of this fixture served tools/wa_registry_fixture.py's committed
    snapshot here instead -- meant as a safety net for the 28 tests that then failed with
    HTTPException 503 (SUPABASE_ANON_KEY empty). The actual bug in every one of those 28 was a missing
    fixture parameter in the test itself (fixed directly, see the report), and an instrumented full
    lane run afterwards logged ZERO calls into that safety net across 2634 tests -- it was pure dead
    code, silently waiting to hide the NEXT test that makes the same mistake instead of failing it
    loudly. A test that genuinely wants real-shaped registry data now has to say so: request the
    `registry_snapshot` fixture below, which is this same stub, opt-in. A test that patches
    app.config.rest_get_all itself (several already do, e.g. tests/test_app_api.py's `env`) overrides
    this fixture by running later, same as any other monkeypatch layering -- autouse fixtures apply
    before a test's own requested ones. Skipped outside the WA lane (see _is_wa_lane_file) and for a
    `network`-marked test (the drift guard, tests/test_wa_registry_fixture_generated.py), which needs
    the real PostgREST response."""
    if request.node.get_closest_marker("network") or not _is_wa_lane_file(request.node.path.name):
        yield
        return

    from app import config as A

    def _raise(path, params=None, page=1000, timeout=120):
        raise RuntimeError(
            f"this offline WA test reached PostgREST for {path!r}: request the luna/wa fixture that "
            f"stubs app.data._snap/_refresh, or request registry_snapshot for real-shaped data"
        )

    monkeypatch.setattr(A, "rest_get_all", _raise)
    yield


def _registry_rows_served(rows, params):
    """The rows PostgREST would answer with for `params`: app/data.py._build() asks v_postings for
    role_class=not.in.(<EXCLUDED_ROLE_CLASSES>), and the fixture (a trimmed live read without that
    filter) carries rows of excluded classes that production never loads. Any other filter param is
    one this fake does not understand -- fail loudly rather than answer as if it were applied."""
    rows = list(rows)
    for key, value in (params or {}).items():
        if key in ("select", "order", "status"):
            continue  # the fixture is select=*, posting_id-ordered, open rows only
        if key == "role_class" and value.startswith("not.in.(") and value.endswith(")"):
            excluded = set(value[len("not.in.("):-1].split(","))
            rows = [r for r in rows if r.get("role_class") not in excluded]
            continue
        raise RuntimeError(f"registry_snapshot fake does not implement the PostgREST filter {key}={value!r}")
    return rows


@pytest.fixture()
def registry_snapshot(monkeypatch):
    """Opt-in: serves tools/wa_registry_fixture.py's committed snapshot through
    app.config.rest_get_all -- exactly what _registry_rest_tripwire above used to install
    automatically, before that became a loud failure instead (see its docstring for why). A test
    that wants app/data.py's real loader path to run against real-shaped data without reaching
    Supabase requests this fixture by name; it returns the snapshot dict itself, in case the test
    wants to assert against the rows it just made rest_get_all answer with (e.g.
    tests/test_wa_registry_snapshot.py).

    Returns a deep copy of each table on every call: app/data.py._build() mutates job/clinic dicts
    IN PLACE (adds "fresh", turns "department_hint" from a "|"-string into a list, ...) exactly as it
    would mutate a fresh list from a real PostgREST response -- a real client never hands back the
    same objects twice, so neither does this one. Without the copy, the SECOND test in a session to
    request this fixture inherited the FIRST test's already-_build()-mutated rows from the module-
    level cache (_registry_fixture()) and crashed on ``'list' object has no attribute 'split'``;
    found by tests/test_wa_registry_snapshot.py, the first real consumer of this stub (see M1)."""
    from app import config as A

    fixture = _registry_fixture()

    def _fake_rest_get_all(path, params=None, page=1000, timeout=120):
        if path == "v_postings":
            return copy.deepcopy(_registry_rows_served(fixture["v_postings"], params))
        if path == "clinics":
            return copy.deepcopy(fixture["clinics"])
        if path == "postings":
            return copy.deepcopy(fixture["postings_housing_evidence"])
        raise RuntimeError(f"registry_snapshot fixture has no rows for rest_get_all({path!r}) -- "
                           f"extend tools/wa_registry_fixture.py if a new table is needed")

    monkeypatch.setattr(A, "rest_get_all", _fake_rest_get_all)
    return fixture
