"""Offline tests for the per-thread rail and the 24 h window gate (TASK-117, TASK-118).

Two rules meet in ``api._send`` and this file is where they are held apart.

The **window** is Meta's rule, so it is now the client's rule and not a process-wide setting: the
client is built first and the gate reads ``requires_freeform_window`` off it. A consumer chat has no
24 h window, so a thread the Cloud API would only let us reopen with an approved template is
answerable with ordinary text on the phone rail -- while a Meta thread keeps today's behaviour
exactly, including "the candidate never wrote, so there is no window at all".

The **rail** is a sender number, so it is a column on the thread and not that setting either: pinned
on the first send that actually went out, never changed afterwards, and read back before the next
client is built. What these tests assert about the pin is mostly what it refuses to do.

No network: a fake executor transport answers the phone rail, a FakeMeta answers the Cloud one.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import bridge as BR
from app.wa import config as C
from app.wa import meta as M
from app.wa import store as ST
from app.wa import transport as T
from app.wa.asgi import app

LEAD = "+491701234567"
OTHER = "+491709999999"
TURN = "wab.i.0001"


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")


class FakeMeta:
    """A Cloud API double: no capability flags at all, which is the point -- every existing test
    double in this suite looks like this and must keep Meta semantics for free."""

    def __init__(self):
        self.sent, self.templates = [], []

    def send_text(self, to_e164, body):
        self.sent.append({"to": to_e164, "body": body})
        return f"wamid.out.{len(self.sent)}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)

    def send_template(self, to_e164, template_name=None, language=None, params=None, **kw):
        self.templates.append({"to": to_e164, "template": template_name})
        return f"wamid.tpl.{len(self.templates)}"


class FakeBridge:
    """The phone rail's capability surface, as ``bridge.Client`` declares it."""

    requires_freeform_window = False
    supports_buttons = False
    wants_idempotency_key = True

    def __init__(self):
        self.sent, self.turns = [], []

    def begin_turn(self, phone, turn_key, action):
        self.turns.append({"phone": phone, "turn_key": turn_key, "action": action})

    def send_text(self, to_e164, body):
        if not self.turns:
            raise AssertionError("send_text before begin_turn: the rail would have no idempotency key")
        self.sent.append({"to": to_e164, "body": body})
        return f"wab.o.{len(self.sent)}"


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _thread(c, phone=LEAD, *, last_inbound_hours=48, rail=None):
    t = ST.thread(c, phone)
    t["last_inbound_at"] = None if last_inbound_hours is None else _ago(last_inbound_hours)
    ST.save_thread(c, t)
    if rail:
        ST.pin_rail(c, phone, rail)
    return t


# --- the gate is the client's, not the config's (TASK-118) -----------------------------------------

def test_a_bridge_thread_past_the_window_sends_free_text(wa, monkeypatch):
    """The phone rail's entire v1 value: a thread Meta would refuse becomes answerable again. The
    reopen template is deliberately left unconfigured -- if the window path were taken at all, this
    would raise instead of sending."""
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "")
    cl = FakeBridge()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=72, rail="bridge")
        status = WAPI._send(c, t, ["Guten Tag!"], [], client=cl, action="reply", turn_key=TURN)
    assert status == "sent"
    assert [m["body"] for m in cl.sent] == ["Guten Tag!"]
    assert cl.turns == [{"phone": LEAD, "turn_key": TURN, "action": "reply"}]


def test_a_meta_thread_past_the_window_still_takes_the_reopen_path(wa):
    cl = FakeMeta()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=48, rail="meta")
        status = WAPI._send(c, t, ["Diese freie Nachricht darf Meta nie erreichen"], [], client=cl,
                            action="reply", turn_key=TURN)
    assert status == "sent_template"
    assert cl.sent == [] and [x["template"] for x in cl.templates] == ["candidate_reopen_v1"]


def test_a_meta_thread_that_never_wrote_still_has_no_window_at_all(wa, monkeypatch):
    """TASK-101's hard return, unchanged: no inbound message ever means no window, so the bubbles
    are not deliverable and an unconfigured template is still a loud failure."""
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "")
    cl = FakeMeta()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=None, rail="meta")
        with pytest.raises(RuntimeError, match="no reopen template is configured"):
            WAPI._send(c, t, ["Hallo?"], [], client=cl, action="reply", turn_key=TURN)
    assert cl.sent == []


def test_a_bridge_thread_that_never_wrote_is_answerable(wa, monkeypatch):
    """The same state on the other rail: a consumer chat has no window to have missed."""
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "")
    cl = FakeBridge()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=None, rail="bridge")
        assert WAPI._send(c, t, ["Guten Tag!"], [], client=cl, action="reply", turn_key=TURN) == "sent"
    assert len(cl.sent) == 1


def test_two_threads_on_different_rails_are_gated_independently(wa):
    """One process, one moment, two stale threads: the gate is read per client, so a single
    process-wide switch could not produce these two answers."""
    bridge, meta = FakeBridge(), FakeMeta()
    with ST.db() as c:
        on_bridge = _thread(c, LEAD, last_inbound_hours=72, rail="bridge")
        on_meta = _thread(c, OTHER, last_inbound_hours=72, rail="meta")
        assert WAPI._send(c, on_bridge, ["Text"], [], client=bridge, action="reply", turn_key=TURN) == "sent"
        assert WAPI._send(c, on_meta, ["Text"], [], client=meta, action="reply", turn_key=TURN) == "sent_template"
    assert len(bridge.sent) == 1 and meta.sent == [] and len(meta.templates) == 1


def test_a_client_without_the_flag_keeps_meta_semantics(wa):
    """AC#5: the default is True, so no existing test double and no existing test file had to change."""
    assert not hasattr(FakeMeta(), "requires_freeform_window")
    cl = FakeMeta()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=48)
        assert WAPI._send(c, t, ["Text"], [], client=cl, action="reply", turn_key=TURN) == "sent_template"


def test_inside_the_window_both_rails_send_the_bubbles(wa):
    bridge, meta = FakeBridge(), FakeMeta()
    with ST.db() as c:
        on_bridge = _thread(c, LEAD, last_inbound_hours=1, rail="bridge")
        on_meta = _thread(c, OTHER, last_inbound_hours=1, rail="meta")
        assert WAPI._send(c, on_bridge, ["A"], [], client=bridge, action="reply", turn_key=TURN) == "sent"
        assert WAPI._send(c, on_meta, ["A"], [], client=meta, action="reply", turn_key=TURN) == "sent"
    assert len(bridge.sent) == 1 and len(meta.sent) == 1


# --- the turn the key is derived from (TASK-114's call site) ---------------------------------------

def test_a_send_with_no_turn_key_is_refused_on_a_rail_that_needs_one(wa):
    """A client that mints its own message ids needs to know which turn it is sending, or a
    catch-up re-drive delivers the same reply twice. Loud, not a random key."""
    cl = FakeBridge()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=1, rail="bridge")
        with pytest.raises(RuntimeError, match="carries no turn_key"):
            WAPI._send(c, t, ["Text"], [], client=cl, action="reply")
    assert cl.sent == []


def test_the_media_ack_names_its_own_turn(wa):
    """``_media_ack`` sends outside ``process_owed_turn``; on this rail it still has to name the
    message it acknowledges, or its key collides with the reply turn's."""
    cl = FakeBridge()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=1, rail="bridge")
        ST.record_inbound_pending(c, LEAD, "wab.i.media", "", kind="video", meta={})
        result = WAPI._media_ack(c, t, {"wamid": "wab.i.media", "kind": "video", "phone": LEAD}, cl)
    assert result["status"] == "sent"
    assert cl.turns == [{"phone": LEAD, "turn_key": "wab.i.media", "action": "media_ack"}]


# --- the pin (TASK-117) -----------------------------------------------------------------------------

def test_the_rail_is_pinned_on_the_first_send_and_read_back_by_the_seam(wa, monkeypatch):
    monkeypatch.setattr(C, "TRANSPORT", "bridge")
    cl = FakeBridge()
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=1)
        assert ST.rail_of(c, LEAD) is None, "nothing has gone out yet"
        WAPI._send(c, t, ["Guten Tag!"], [], client=cl, action="reply", turn_key=TURN)
        assert ST.rail_of(c, LEAD) == "bridge"
        assert ST.thread(c, LEAD)["rail"] == "bridge", "_thread_row exposes it"


def test_a_pinned_thread_is_not_moved_by_flipping_the_transport(wa, monkeypatch):
    """The kill switch moves new conversations only. A live thread following WA_TRANSPORT would
    answer the candidate from a number they have never seen."""
    monkeypatch.setattr(C, "TRANSPORT", "meta")
    with ST.db() as c:
        _thread(c, last_inbound_hours=1, rail="bridge")
        assert T.rail_for(c, LEAD) == "bridge"
        assert isinstance(T.get_client(phone=LEAD, conn=c), BR.Client)
        assert isinstance(T.get_client(phone=OTHER, conn=c), M.Client), "an unpinned phone follows the config"


def test_the_pin_is_read_without_a_connection_too(wa, monkeypatch):
    """``process_phones`` resolves a client before it opens the turn's connection."""
    monkeypatch.setattr(C, "TRANSPORT", "meta")
    with ST.db() as c:
        _thread(c, last_inbound_hours=1, rail="bridge")
    assert T.rail_for(phone=LEAD) == "bridge"
    assert isinstance(T.get_client(phone=LEAD), BR.Client)


def test_a_second_pin_to_a_different_rail_fails_loudly(wa):
    with ST.db() as c:
        _thread(c, last_inbound_hours=1, rail="bridge")
        assert ST.pin_rail(c, LEAD, "bridge") == "bridge", "the same rail again is a no-op"
        with pytest.raises(RuntimeError, match="pinned to the 'bridge' rail"):
            ST.pin_rail(c, LEAD, "meta")
        assert ST.rail_of(c, LEAD) == "bridge", "and nothing was overwritten"


def test_an_unknown_rail_is_never_pinned(wa):
    with ST.db() as c:
        _thread(c, last_inbound_hours=1)
        with pytest.raises(ValueError, match="not one of meta, bridge"):
            ST.pin_rail(c, LEAD, "carrier-pigeon")
        assert ST.rail_of(c, LEAD) is None


def test_a_card_save_cannot_change_the_rail(wa):
    """Only pin_rail writes the column: ``_update_thread`` rewrites the whole card on every turn and
    must not carry a rail with it."""
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=1, rail="bridge")
        t["rail"] = "meta"
        t["slots"]["city"] = "Augsburg"
        ST.save_thread(c, t)
        assert ST.rail_of(c, LEAD) == "bridge"
        assert ST.thread(c, LEAD)["slots"]["city"] == "Augsburg", "the card itself did save"


def test_a_draft_pins_nothing(wa, monkeypatch):
    """WA_AUTOSEND off: the bubbles are stored for a human to read, nothing left this machine, so the
    thread is still free to start on whichever rail its first real send resolves."""
    monkeypatch.setattr(C, "AUTOSEND", False)
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=1)
        assert WAPI._send(c, t, ["Text"], [], client=FakeMeta(), action="reply", turn_key=TURN) == "draft"
        assert ST.rail_of(c, LEAD) is None


def test_a_failed_send_pins_nothing(wa):
    class Broken(FakeBridge):
        def send_text(self, to_e164, body):
            raise BR.BridgeError("bridge unreachable on send", status_code=424)

    with ST.db() as c:
        t = _thread(c, last_inbound_hours=1)
        with pytest.raises(BR.BridgeError):
            WAPI._send(c, t, ["Text"], [], client=Broken(), action="reply", turn_key=TURN)
        assert ST.rail_of(c, LEAD) is None


def test_a_reopen_template_pins_the_rail_it_went_out_on(wa):
    with ST.db() as c:
        t = _thread(c, last_inbound_hours=48)
        assert WAPI._send(c, t, ["Text"], [], client=FakeMeta(), action="reply", turn_key=TURN) == "sent_template"
        assert ST.rail_of(c, LEAD) == "meta"


def test_the_rails_are_reported_by_health_and_by_the_thread_read(wa):
    with ST.db() as c:
        _thread(c, LEAD, last_inbound_hours=1, rail="bridge")
        _thread(c, OTHER, last_inbound_hours=1)
    with TestClient(app, client=("127.0.0.1", 41234)) as client:
        health = client.get("/api/wa/health").json()
        rows = client.get("/api/wa/threads").json()["rows"]
    assert health["rails"] == {"bridge": 1, "unpinned": 1}
    assert {r["phone"]: r["rail"] for r in rows} == {LEAD: "bridge", OTHER: None}


# --- the whole rail, end to end ----------------------------------------------------------------------

def test_a_pinned_bridge_thread_answers_a_turn_through_the_real_client(wa, monkeypatch):
    """No injected client anywhere: the worker resolves the rail from the thread, builds
    ``bridge.Client`` itself, opens the turn and posts one bubble per message to the executor. The
    only fake is the HTTP transport at the very edge."""
    monkeypatch.setattr(C, "TRANSPORT", "meta")     # the pin wins over the configured default
    monkeypatch.setattr(C, "BRIDGE_URL", "http://127.0.0.1:8793")
    monkeypatch.setattr(C, "BRIDGE_TOKEN", "tok")
    posted = []

    def fake_executor(method, url, headers=None, data=None, timeout=None):
        body = json.loads(data)
        posted.append(body)
        return 200, {"ok": True, "state": "sent", "client_msg_id": body["client_msg_id"],
                     "verified": {"tick": "Zugestellt", "read_at_send": True}, "replayed": False}

    monkeypatch.setattr(BR, "_default_transport", fake_executor)
    with ST.db() as c:
        _thread(c, last_inbound_hours=1, rail="bridge")
        ST.record_inbound_pending(c, LEAD, TURN, "Hallo, ich suche eine Stelle", kind="text", meta={})
    WAPI.process_phones([LEAD])

    assert [p["to"] for p in posted] == [LEAD] * len(posted) and posted, "the executor was called"
    assert all(p["client_msg_id"].startswith("wab.o.") for p in posted)
    assert [p["trace"]["turn_key"] for p in posted] == [TURN] * len(posted)
    with ST.db() as c:
        out = [m for m in ST.history(c, LEAD) if m["direction"] == "out"]
        assert ST.rail_of(c, LEAD) == "bridge"
        assert ST.pending_inbound_summary(c, LEAD) is None, "the inbound message is finished"
    assert len(out) == len(posted)
