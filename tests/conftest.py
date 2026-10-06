"""Shared test defaults.

AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the existing API tests keep
calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test.

THE NETWORK GUARD (TASK-197): a test never talks to a clinic site, and never to our own infrastructure either. Any socket
connect or DNS lookup to a host that is not loopback is refused, recorded, and fails the test that made it -- even when the code
under test swallows the exception (crawlers.vendor_adapters.get() returns None on any Exception, and a swallowed refusal
would otherwise look like "the board answered nothing"). Hits during collection stop the session. Chromium is a separate
process the socket patch cannot see, so every Playwright launch gets a resolver that knows nothing but localhost.

  clinic sites          read the local mirror (tests/mirror.py; tools/mirror.py records it, on purpose, outside pytest)
  Supabase, ingest, Firecrawl, LLM APIs    fake them: a monkeypatched client or a local sqlite, never the real service

`MIRROR_RECORD=1` lifts the guard (and only that): a person re-recording what a mirror lacks, see tests/mirror.py.
"""
import copy
import importlib
import json
import os
import pathlib
import socket
import sys

import pytest

os.environ.setdefault("AUTH_DISABLED", "1")

_RECORDING = os.environ.get("MIRROR_RECORD") == "1"
_LOCAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", "", None}
_hits = []  # (kind, host, port) in the order they happened; a phase fails when its own slice is not empty
_real = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex, "getaddrinfo": socket.getaddrinfo,
         "gethostbyname": socket.gethostbyname, "gethostbyname_ex": socket.gethostbyname_ex}


def _local(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return host in _LOCAL or str(host).startswith("127.")


def _refuse(kind, host, port):
    _hits.append((kind, str(host), port))
    return host, port


def _connect(self, address):
    if isinstance(address, tuple) and address and not _local(address[0]):
        _refuse("connect", *address[:2])
        raise OSError(f"network guard: a test may not connect to {address[0]}:{address[1]}")
    return _real["connect"](self, address)


def _connect_ex(self, address):
    if isinstance(address, tuple) and address and not _local(address[0]):
        _refuse("connect", *address[:2])
        return 111  # ECONNREFUSED
    return _real["connect_ex"](self, address)


def _is_ip(host):
    import ipaddress
    try:
        ipaddress.ip_address(str(host).split("%")[0])
        return True
    except ValueError:
        return False


def _resolver(name):
    def resolve(host, *a, **k):
        # an IP literal resolves to itself, no DNS happens; the connect is what gets refused
        if not _local(host) and not _is_ip(host):
            _refuse("dns", host, a[0] if a else None)
            raise socket.gaierror(socket.EAI_NONAME, f"network guard: a test may not resolve {host}")
        return _real[name](host, *a, **k)
    resolve._network_guard = True
    return resolve


_connect._network_guard = _connect_ex._network_guard = True
if not _RECORDING:
    socket.socket.connect, socket.socket.connect_ex = _connect, _connect_ex
    socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex = (_resolver(n) for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"))
    try:  # a Chromium started by any test (or by a mirror replay) cannot resolve anything but localhost
        from playwright.sync_api import BrowserType

        _launch = BrowserType.launch

        def _guarded_launch(self, *a, **k):
            k["args"] = [*(k.get("args") or []), "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1 , EXCLUDE [::1]"]
            return _launch(self, *a, **k)

        BrowserType.launch = _guarded_launch
    except ImportError:
        pass

try:  # the web fonts our own pages load come from the mirror too (tests/mirror.py web_fonts), in both modes: a recording run fills it
    from playwright.sync_api import Browser

    _new_context, _new_page = Browser.new_context, Browser.new_page

    def _font_miss(url):
        _refuse("font (not in the mirror; MIRROR_RECORD=1 pytest <this web test> records it)", url, None)

    def _context_with_fonts(self, *a, **k):
        from tests import mirror as M
        ctx = _new_context(self, *a, **k)
        M.route_web_fonts(ctx, _font_miss)
        return ctx

    def _page_with_fonts(self, *a, **k):
        from tests import mirror as M
        page = _new_page(self, *a, **k)
        M.route_web_fonts(page.context, _font_miss)
        return page

    Browser.new_context, Browser.new_page = _context_with_fonts, _page_with_fonts
except ImportError:
    pass

_ADVICE = ("a test never talks to a clinic site or to our own infrastructure. Clinic sites: replay the mirror "
           "(tests/mirror.py; record it with tools/mirror.py). Supabase, the ingest endpoint, Firecrawl, LLM APIs: fake them.")


def _failure(mark, where):
    hits = _hits[mark:]
    if not hits:
        return None
    what = ", ".join(dict.fromkeys(f"{k} {h}:{p}" if k == "connect" else f"{k} {h}" for k, h, p in hits))
    return f"network guard: {where} reached {len(set(h for _, h, _ in hits))} non-local host(s): {what}\n  {_ADVICE}"


def _phase(item, name):
    mark = len(_hits)
    try:
        out = yield
    except BaseException as e:
        msg = _failure(mark, f"{item.nodeid} ({name})")
        if msg:
            raise AssertionError(msg) from e
        raise
    msg = _failure(mark, f"{item.nodeid} ({name})")
    if msg:
        raise AssertionError(msg)
    return out


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    return (yield from _phase(item, "setup"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from _phase(item, "call"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    return (yield from _phase(item, "teardown"))


def pytest_collection_finish(session):
    msg = _failure(0, "collection")
    if msg:
        pytest.exit(msg, returncode=2)


def pytest_collection_modifyitems(items):
    """The named gaps of the completeness cases (TASK-197): a check that was already red when its board was recorded is an explicit
    xfail with that reason (visible with -rx), not a hidden skip and not a red suite; the same check going red on a board where it
    was green is a regression and fails. Non-strict: a fix that turns a gap green shows as XPASS until the board is re-recorded."""
    gaps = [it for it in items if getattr(it, "originalname", None) and "board" in getattr(getattr(it, "callspec", None), "params", {})]
    if not gaps:
        return
    from tests import adapter_harness as H
    for it in gaps:
        board = it.callspec.params["board"]
        why = H.recorded_gap(it.originalname, board["board_id"]) if isinstance(board, dict) and "board_id" in board else None
        if why:
            it.add_marker(pytest.mark.xfail(reason=why, strict=False))


def pytest_report_header(config):
    return "network guard: " + ("OFF (MIRROR_RECORD=1)" if _RECORDING else "ON (a non-local socket or DNS lookup in a test is an error)")


def pytest_sessionfinish(session, exitstatus):
    if _RECORDING:
        from tests import mirror as M
        M.save_web_fonts()
    else:
        print(f"\nnetwork guard hits: {len(_hits)}")


# TASK-162: the client's real identity is gitignored (config/wa-client.json); every test runs against
# the committed, synthetic config/wa-client.example.json instead, unless a test (or the environment
# running pytest) already set WA_CLIENT_CONFIG itself. Set here, at collection time, before any test
# module's own "from app.wa import ..." imports app/wa/luna/prompts.py or luna_brain.py -- both read
# client() at import time (same reasoning _LIVE_CREDENTIALS below has for popping its own vars this
# early). Absolute path from this file's own location, not the cwd pytest happened to be run from.
os.environ.setdefault(
    "WA_CLIENT_CONFIG",
    str(pathlib.Path(__file__).resolve().parent.parent / "config" / "wa-client.example.json"),
)

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
