"""Offline tests for the transport seam (TASK-116) and the phone helpers it left behind (TASK-115).

No network: the only client ever built here is app.wa.meta.Client with no token, which is never
called. The one test that needs a different WA_TRANSPORT in the environment runs a fresh
interpreter -- app.wa.config validates the value at import, and reloading it in-process would
hand every other test in this session a rewritten config module.
"""
import ast
import os
import pathlib
import subprocess
import sys

import pytest

from app.wa import api as WAPI
from app.wa import bridge as BR
from app.wa import config as C
from app.wa import meta as M
from app.wa import phones as P
from app.wa import store as ST
from app.wa import transport as T

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAD = "+491701234567"


def _import_config(**env):
    """Import app.wa.config in a fresh interpreter with env applied. -> the CompletedProcess."""
    return subprocess.run([sys.executable, "-c", "import app.wa.config"], cwd=ROOT,
                          env={**os.environ, **env}, capture_output=True, text=True)


# --- get_client: the injection seam --------------------------------------------------------------

def test_an_injected_client_is_handed_back_untouched():
    """``cl = client or M.Client()`` became ``T.get_client(client=client)``: every FakeMeta in this
    suite, and every real client a caller already holds, must still flow straight through."""
    fake = object()
    assert T.get_client(client=fake) is fake
    assert T.get_client(phone=LEAD, client=fake) is fake


def test_an_injected_client_is_never_replaced_by_a_live_one(monkeypatch):
    monkeypatch.setattr(M, "Client", lambda *a, **k: pytest.fail("an injected client must be used as given"))
    fake = object()
    assert T.get_client(client=fake) is fake


# --- get_client: which transport ------------------------------------------------------------------

def test_without_a_client_the_default_transport_builds_a_meta_client():
    assert C.TRANSPORT == "meta", "WA_TRANSPORT unset must mean the Cloud API"
    assert isinstance(T.get_client(), M.Client)


def test_the_meta_client_is_looked_up_on_the_meta_module_at_call_time(monkeypatch):
    """The existing tests patch app.wa.meta.Client (tests/test_wa_harness.py, test_wa_voice_notes.py,
    test_wa_router_internal.py). Building it through the seam must still go through that patch."""
    sentinel = object()
    monkeypatch.setattr(M, "Client", lambda *a, **k: sentinel)
    assert T.get_client() is sentinel


def test_constructor_kwargs_reach_the_transport():
    cl = T.get_client(access_token="tok", phone_number_id="pn1")
    assert cl.access_token == "tok" and cl.phone_number_id == "pn1"


def test_the_bridge_transport_builds_the_phone_rail_client(monkeypatch):
    """WA_TRANSPORT=bridge now resolves to app/wa/bridge.Client -- never to Meta, which would answer
    a candidate from a number they have never seen."""
    monkeypatch.setattr(C, "TRANSPORT", "bridge")
    assert isinstance(T.get_client(), BR.Client)
    assert isinstance(T.get_client(phone=LEAD), BR.Client)


def test_an_unknown_rail_is_never_built(monkeypatch):
    """A rail nobody implements must stop the send, not pick one. (config.py refuses an unknown
    WA_TRANSPORT at import; this is the same refusal one layer down, for a pinned column.)"""
    monkeypatch.setattr(C, "TRANSPORT", "carrier-pigeon")
    with pytest.raises(RuntimeError, match="is not a WhatsApp rail"):
        T.get_client()


def test_bridge_still_accepts_an_injected_client(monkeypatch):
    """The raise is about building one, not about using the one the caller brought."""
    monkeypatch.setattr(C, "TRANSPORT", "bridge")
    fake = object()
    assert T.get_client(client=fake) is fake


# --- config: the value is validated at import, not at send time -----------------------------------

def test_an_unknown_transport_stops_the_process_at_import():
    proc = _import_config(WA_TRANSPORT="carrier-pigeon")
    assert proc.returncode != 0
    assert "WA_TRANSPORT='carrier-pigeon' is not 'meta' or 'bridge'" in proc.stderr


def test_an_empty_transport_is_not_a_silent_default():
    proc = _import_config(WA_TRANSPORT="")
    assert proc.returncode != 0 and "WA_TRANSPORT" in proc.stderr


@pytest.mark.parametrize("value", ["meta", "bridge", "META", " meta "])
def test_the_two_known_values_import_in_any_case_or_padding(value):
    assert _import_config(WA_TRANSPORT=value).returncode == 0


def test_readiness_reports_the_active_transport():
    """GET /api/wa/health must say which rail is live, so nobody has to read .env to find out."""
    assert C.readiness()["transport"] == C.TRANSPORT == "meta"


# --- the seam is the only door: no call site builds a transport client itself ---------------------

def _transport_client_calls(path):
    """-> the line numbers in ``path`` that construct a transport client (``M.Client()`` /
    ``meta.Client()``). AST, not grep: a docstring naming ``M.Client()`` is prose, not a call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Client"
            and isinstance(n.func.value, ast.Name) and n.func.value.id in ("M", "meta")]


def test_only_transport_py_builds_a_transport_client():
    """TASK-116 AC#1. Every other test in the suite injects a FakeMeta, so a call site that went back
    to ``M.Client()`` would stay green everywhere else and only surface as a real Cloud API send on a
    deployment configured for the bridge."""
    found = {str(p.relative_to(ROOT)): lines
             for p in sorted((ROOT / "app" / "wa").rglob("*.py"))
             for lines in [_transport_client_calls(p)] if lines}
    assert list(found) == ["app/wa/transport.py"], f"transport client built outside the seam: {found}"
    assert len(found["app/wa/transport.py"]) == 1, f"the seam builds one client, not {found}"


def test_process_phones_resolves_one_client_per_phone(monkeypatch, tmp_path):
    """TASK-116 AC#4. One webhook payload can name several candidates, and the rail is per-thread
    (TASK-117), so the client must be resolved inside the loop with the phone it will answer -- not
    once by ``submit_accepted``, which runs before any phone has been read."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    seen = []

    def recording_get_client(phone=None, client=None, **kw):
        seen.append(phone)
        return client if client is not None else object()

    monkeypatch.setattr(WAPI.T, "get_client", recording_get_client)
    WAPI.process_phones([LEAD, "+491709999999"])
    assert seen == [LEAD, "+491709999999"]


def test_submit_accepted_builds_no_client_of_its_own(monkeypatch):
    """The other half of AC#4: nothing may be built before the first phone is known."""
    monkeypatch.setattr(M, "Client", lambda *a, **k: pytest.fail("submit_accepted must build no client"))
    monkeypatch.setattr(WAPI, "_process_in_background", lambda phones, client: (phones, client))
    future = WAPI.submit_accepted({"phones": [LEAD]})
    assert future.result(30) == ([LEAD], None)


def test_a_send_without_a_client_resolves_the_rail_and_fails_loudly_when_it_is_unconfigured(monkeypatch, tmp_path):
    """The seam has to be reachable from a real send path, not just callable in isolation: ``_send``
    with no injected client resolves one for this thread's phone, so WA_TRANSPORT=bridge reaches the
    phone rail's own client -- which refuses a send on a host with no executor configured instead of
    quietly answering from the WABA number."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "TRANSPORT", "bridge")
    monkeypatch.setattr(C, "BRIDGE_URL", "")
    with ST.db() as c:
        thread = ST.thread(c, LEAD)
        thread["last_inbound_at"] = ST.now_iso()
        with pytest.raises(BR.BridgeError, match="WA_BRIDGE_URL"):
            WAPI._send(c, thread, ["Guten Tag!"], [], action="reply", turn_key="wamid.in.1")
        assert ST.rail_of(c, LEAD) is None, "a failed send pins no rail"


# --- phones.py: same behaviour as the meta.py originals (TASK-115) --------------------------------

def test_meta_still_exports_the_helpers_it_moved_out():
    assert M.sender_e164 is P.sender_e164
    assert M.canonicalize_phone is P.canonicalize_phone


@pytest.mark.parametrize("raw,expected", [
    ("491701234567", LEAD),          # tests/test_wa_harness.py: Meta's bare-digit wa_id
    ("+491701234567", LEAD),
    ("", ""),
    (None, ""),
])
def test_sender_e164(raw, expected):
    assert P.sender_e164(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("0170 1234567", LEAD),          # tests/test_wa_harness.py:533-536, the four shapes one human has
    ("0049-170-1234567", LEAD),
    ("+49 170 1234567", LEAD),
    ("491701234567", LEAD),
    ("", ""),
    (None, ""),
    # tests/test_wa_luna_migrate_candidates.py / test_wa_luna_export_known_phones.py: garbage keeps
    # returning a truthy-but-bogus "+49" -- the callers' MIN_PHONE_DIGITS check depends on it.
    ("not-a-phone-number", "+49"),
])
def test_canonicalize_phone(raw, expected):
    assert P.canonicalize_phone(raw) == expected


def test_canonicalize_phone_takes_a_default_country_code():
    assert P.canonicalize_phone("0170 1234567", default_country_code="+43") == "+431701234567"
