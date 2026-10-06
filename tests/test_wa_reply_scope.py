"""The send-scope kill switches (Ivan, 2026-09-27): WA_REPLY_SCOPE (both rails) and WA_META_SCOPE
(Meta rail only) each default to 'all' (today's behaviour) and, set to 'test_only', mute every
send except to a store.is_test_thread number. Offline: SQLite in a temp dir, Meta faked, no network,
no live rail (tests/conftest.py scrubs WA_BRIDGE_*/WA_AUTOSEND/etc. from the environment already).
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa import transport as T
from tests.test_wa_transport import _import_config

PHONE = "491700000001"        # an ordinary candidate number
TEST_PHONE = "491700000099"   # a store.is_test_thread number


class _FakeClient:
    """Stands in for app.wa.meta.Client/app.wa.bridge.Client: records what would go out, never a
    real one -- T.rail_of_client() reads it as 'unknown', same as every other test's fake."""

    def __init__(self):
        self.sent = []
        self.n = 0

    def _next(self):
        self.n += 1
        return f"wamid.out.{self.n}"

    def send_text(self, to_e164, body):
        self.sent.append({"to": to_e164, "body": body})
        return self._next()

    def send_buttons(self, to_e164, body, buttons):
        self.sent.append({"to": to_e164, "body": body, "buttons": buttons})
        return self._next()

    def send_template(self, to_e164, template_name, language="de", params=None):
        self.sent.append({"to": to_e164, "template": template_name})
        return self._next()


def _sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")


# --- scope_refusal matrix -------------------------------------------------------------------------

def test_scope_refusal_allows_a_test_thread_on_either_rail_even_under_both_switches(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    monkeypatch.setattr(C, "META_SCOPE", "test_only")
    with ST.db() as c:
        ST.mark_test_thread(c, TEST_PHONE, True)
        assert T.scope_refusal(conn=c, phone=TEST_PHONE, rail="meta") is None
        assert T.scope_refusal(conn=c, phone=TEST_PHONE, rail="bridge") is None


def test_scope_refusal_refuses_a_non_test_thread_on_either_rail_under_reply_scope_test_only(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    with ST.db() as c:
        meta_reason = T.scope_refusal(conn=c, phone=PHONE, rail="meta")
        bridge_reason = T.scope_refusal(conn=c, phone=PHONE, rail="bridge")
    assert meta_reason is not None and "WA_REPLY_SCOPE" in meta_reason
    assert bridge_reason is not None and "WA_REPLY_SCOPE" in bridge_reason


def test_scope_refusal_meta_scope_test_only_mutes_meta_but_leaves_bridge_alone(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "META_SCOPE", "test_only")
    with ST.db() as c:
        meta_reason = T.scope_refusal(conn=c, phone=PHONE, rail="meta")
        bridge_reason = T.scope_refusal(conn=c, phone=PHONE, rail="bridge")
    assert meta_reason is not None and "WA_META_SCOPE" in meta_reason
    assert bridge_reason is None


def test_scope_refusal_allows_everyone_at_the_all_all_default(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    with ST.db() as c:
        assert T.scope_refusal(conn=c, phone=PHONE, rail="meta") is None
        assert T.scope_refusal(conn=c, phone=PHONE, rail="bridge") is None


def test_scope_refusal_without_a_connection_opens_its_own_like_rail_for(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    with ST.db() as c:
        ST.mark_test_thread(c, TEST_PHONE, True)
    assert T.scope_refusal(phone=TEST_PHONE, rail="meta") is None
    assert T.scope_refusal(phone=PHONE, rail="meta") is not None


def test_an_unknown_reply_scope_stops_the_process_at_import():
    """Same discipline as WA_TRANSPORT (tests/test_wa_transport.py): a typo must stop the process at
    import, never silently read as 'all'. A fresh interpreter, like that test -- config.py validates
    at import and reloading it in-process would rewrite the module for every other test in this
    session."""
    proc = _import_config(WA_REPLY_SCOPE="sometimes")
    assert proc.returncode != 0
    assert "WA_REPLY_SCOPE='sometimes' is not 'all' or 'test_only'" in proc.stderr


def test_an_unknown_meta_scope_stops_the_process_at_import():
    proc = _import_config(WA_META_SCOPE="sometimes")
    assert proc.returncode != 0
    assert "WA_META_SCOPE='sometimes' is not 'all' or 'test_only'" in proc.stderr


# --- _send: drafts under a scope refusal, really sends to a test thread ---------------------------

def test_send_stores_a_draft_with_scope_refusal_for_a_non_test_thread(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    fake = _FakeClient()
    t = {"phone": PHONE, "last_inbound_at": ST.now_iso()}
    with ST.db() as c:
        status = WAPI._send(c, t, ["Hallo"], [], client=fake, action="reply_now_conversational")
        rows = ST.messages_for(c, PHONE, direction="out")
    assert status == "draft"
    assert fake.sent == []
    assert len(rows) == 1
    assert rows[0]["kind"] == "draft" and rows[0]["wamid"] is None
    assert rows[0]["meta"]["action"] == "reply_now_conversational"
    assert "WA_REPLY_SCOPE" in rows[0]["meta"]["scope_refusal"]


def test_send_really_sends_to_a_test_thread_under_reply_scope_test_only(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    fake = _FakeClient()
    t = {"phone": TEST_PHONE, "last_inbound_at": ST.now_iso()}
    with ST.db() as c:
        ST.mark_test_thread(c, TEST_PHONE, True)
        status = WAPI._send(c, t, ["Hallo"], [], client=fake, action="reply_now_conversational")
        rows = ST.messages_for(c, TEST_PHONE, direction="out")
    assert status == "sent"
    assert fake.sent == [{"to": TEST_PHONE, "body": "Hallo"}]
    assert rows[0]["kind"] == "text" and "scope_refusal" not in rows[0]["meta"]


def test_send_drafts_under_meta_scope_test_only_for_a_meta_rail_send(tmp_path, monkeypatch):
    """No thread has ever sent, so rail_for falls back to C.TRANSPORT -- 'meta' by default -- and the
    fake client is 'unknown' to rail_of_client, so _send must fall back to that rail too."""
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "META_SCOPE", "test_only")
    monkeypatch.setattr(C, "TRANSPORT", "meta")
    fake = _FakeClient()
    t = {"phone": PHONE, "last_inbound_at": ST.now_iso()}
    with ST.db() as c:
        status = WAPI._send(c, t, ["Hallo"], [], client=fake, action="reply_now_conversational")
        rows = ST.messages_for(c, PHONE, direction="out")
    assert status == "draft"
    assert fake.sent == []
    assert "WA_META_SCOPE" in rows[0]["meta"]["scope_refusal"]


def test_send_still_sends_on_the_bridge_rail_under_meta_scope_test_only(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "META_SCOPE", "test_only")
    monkeypatch.setattr(C, "TRANSPORT", "bridge")
    fake = _FakeClient()
    t = {"phone": PHONE, "last_inbound_at": ST.now_iso()}
    with ST.db() as c:
        status = WAPI._send(c, t, ["Hallo"], [], client=fake, action="reply_now_conversational")
    assert status == "sent"
    assert fake.sent == [{"to": PHONE, "body": "Hallo"}]


# --- the reopen-template branch (_send_reopen_template) -------------------------------------------

def _stale():
    return (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0).isoformat()


def test_reopen_template_drafts_with_scope_refusal_for_a_non_test_thread(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    fake = _FakeClient()
    t = {"phone": PHONE, "last_inbound_at": _stale()}
    with ST.db() as c:
        status = WAPI._send(c, t, ["Text"], [], client=fake)
        rows = ST.messages_for(c, PHONE, direction="out")
    assert status == "draft_template"
    assert fake.sent == []
    assert rows[0]["kind"] == "draft_template"
    assert "WA_REPLY_SCOPE" in rows[0]["meta"]["scope_refusal"]


def test_reopen_template_still_sends_to_a_test_thread(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    fake = _FakeClient()
    t = {"phone": TEST_PHONE, "last_inbound_at": _stale()}
    with ST.db() as c:
        ST.mark_test_thread(c, TEST_PHONE, True)
        status = WAPI._send(c, t, ["Text"], [], client=fake)
    assert status == "sent_template"
    assert fake.sent == [{"to": TEST_PHONE, "template": "candidate_reopen_v1"}]


# --- the tools-server subprocess env passthrough (luna_brain._mcp_config_path) --------------------

def test_mcp_config_env_carries_both_scope_switches(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(LB.BV, "vocabulary_lines", lambda: {})
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")

    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    monkeypatch.setattr(C, "META_SCOPE", "all")
    env_on = json.loads(LB._mcp_config_path(tmp_path / "ready1.json")
                        .read_text(encoding="utf-8"))["mcpServers"][LB.MCP_SERVER_NAME]["env"]
    assert env_on["WA_REPLY_SCOPE"] == "test_only"
    assert env_on["WA_META_SCOPE"] == "all"

    monkeypatch.setattr(C, "REPLY_SCOPE", "all")
    monkeypatch.setattr(C, "META_SCOPE", "test_only")
    env_off = json.loads(LB._mcp_config_path(tmp_path / "ready2.json")
                         .read_text(encoding="utf-8"))["mcpServers"][LB.MCP_SERVER_NAME]["env"]
    assert env_off["WA_REPLY_SCOPE"] == "all"
    assert env_off["WA_META_SCOPE"] == "test_only"


# --- tools_server.py's own gate (show_clinic_photos, send_updated_cv) -----------------------------

def test_show_clinic_photos_refuses_under_reply_scope_test_only(tmp_path, monkeypatch):
    from app.wa.luna import tools_server as TS
    from tests.test_wa_luna_tools import _with_photos, board
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    monkeypatch.setenv("WA_LUNA_PHONE", PHONE)
    _with_photos(monkeypatch)

    def _must_not_construct():
        raise AssertionError("BR.Client() must not be constructed under a scope refusal")
    monkeypatch.setattr(TS.BR, "Client", _must_not_construct)

    out = TS.show_clinic_photos("c1")
    assert out["sent"] is False and "WA_REPLY_SCOPE" in out["reason"]


def test_send_updated_cv_refuses_under_reply_scope_test_only(tmp_path, monkeypatch):
    from app.wa.luna import tools_server as TS
    from tests.test_wa_luna_tools import board
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    monkeypatch.setenv("WA_LUNA_PHONE", PHONE)

    def _must_not_construct():
        raise AssertionError("BR.Client() must not be constructed under a scope refusal")
    monkeypatch.setattr(TS.BR, "Client", _must_not_construct)

    out = TS.send_updated_cv("updated body")
    assert out["sent"] is False and "WA_REPLY_SCOPE" in out["reason"]


# --- campaign.py's --send fail-fast (a campaign blasts many real phones) --------------------------

def _minimal_leads(tmp_path):
    path = tmp_path / "leads.csv"
    path.write_text("phone,body.1\n+491700000001,Frau Test\n")
    return path


def test_campaign_send_refuses_when_reply_scope_is_test_only(tmp_path, monkeypatch):
    from app.wa.luna import campaign as CAMP
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "REPLY_SCOPE", "test_only")
    report = tmp_path / "report.json"
    code = CAMP.main(["--campaign-id", "x", "--template-id", "t", "--leads", str(_minimal_leads(tmp_path)),
                      "--send", "--report", str(report), "--override-no-history-source"])
    assert code == CAMP.EXIT_CONFIG
    assert "WA_REPLY_SCOPE" in json.loads(report.read_text())["error"]


def test_campaign_send_refuses_when_meta_scope_is_test_only(tmp_path, monkeypatch):
    from app.wa.luna import campaign as CAMP
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "META_SCOPE", "test_only")
    report = tmp_path / "report.json"
    code = CAMP.main(["--campaign-id", "x", "--template-id", "t", "--leads", str(_minimal_leads(tmp_path)),
                      "--send", "--report", str(report), "--override-no-history-source"])
    assert code == CAMP.EXIT_CONFIG
    assert "WA_META_SCOPE" in json.loads(report.read_text())["error"]
