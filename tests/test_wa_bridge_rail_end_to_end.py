"""The two halves of the phone rail, wired to each other (TASK-146).

``tests/test_wa_bridge_client.py`` proves the VPS client against a fake executor and
``tests/test_bridge_executor.py`` proves the executor against a fake client. Both passed while the
rail could not carry a single message: the client put Luna's action slug in ``trace.action`` and the
executor's governor paces on that field, so every outbound was refused 400 before it reached the
phone. Neither suite could see it, because neither one ever let the real payload meet the real
parser.

So this file has no fake wire in it. ``app.wa.bridge.Client``'s transport IS
``bridge.executor.Executor.send``, and what a test asserts is what a candidate would have received.
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.wa import bridge as BR
from app.wa import bridge_ids as BI
from bridge import driver as D
from bridge import executor as X
from bridge import governor as G
from bridge import ledger as L

BERLIN = ZoneInfo("Europe/Berlin")
LEAD = "+491701234567"
TURN = "wamid.HBgNNDkxNzAxMjM0NTY3FQIAEhgg"
BASE = "http://127.0.0.1:8793"


class Rail:
    """One executor, reached the way the live client reaches it: JSON in, (status, body) out."""

    def __init__(self, tmp_path, *, moment=None, per_number_cap=30, driver=None):
        self.now = moment or datetime(2026, 9, 23, 10, 0, tzinfo=BERLIN).astimezone(timezone.utc)
        self.ledger = L.Ledger(tmp_path / "ledger.sqlite")
        self.driver = driver or D.FakeDriver()
        self.executor = X.Executor(
            ledger=self.ledger, governor=G.Governor(self.ledger, per_number_daily_cap=per_number_cap),
            driver=self.driver, rail_number=None, clock=lambda: self.now, tick_wait_sec=0.0,
            sleep=lambda _s: None, monotonic=lambda: 10_000.0)
        self.requests = []
        self.slept = []

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        """The wire. Serialises through JSON on purpose: a tuple the client passes by reference
        would hide a payload the real HTTP handler could not have parsed."""
        payload = json.loads(data)
        self.requests.append(payload)
        try:
            return self.executor.send(payload)
        except Exception as refusal:                      # the server's envelope, as HTTP sees it
            return refusal.status_code, refusal.envelope()

    def client(self):
        """The real client, with the clock and the sleep injected: waiting out the rail's own
        inter-bubble gap is the behaviour under test, sitting through it is not."""
        return BR.Client(transport=self, base_url=BASE, token="tok",
                         sleep=self.advance, now=lambda: self.now)

    def advance(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def rail(tmp_path):
    return Rail(tmp_path)


# --- a conversational turn -------------------------------------------------------------------
@pytest.mark.parametrize("action", ["ask_question", "media_ack", "ask_consent", "followup",
                                    "reply", "matches"])
def test_every_action_slug_luna_produces_reaches_the_phone(rail, action):
    """Luna names its own actions and the fuse paces in its own vocabulary; the wire has to carry
    both. Before TASK-146 the slug went into trace.action and the governor answered
    "trace.action must be one of ('first_touch', 'reply')" to every single one of these."""
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, action)
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action=action, bubble_index=0)

    assert cl.send_text(LEAD, "Guten Tag, hier ist Valentina.") == key
    assert rail.driver.sent == ["Guten Tag, hier ist Valentina."]
    trace = rail.requests[0]["trace"]
    assert trace["action"] == "reply", "an answer to an inbound message is paced as a reply"
    assert trace["intent"] == action, "the slug still travels, for the ledger and the audit"


def test_a_campaign_attempt_is_paced_as_cold_contact(rail):
    cl = rail.client()
    cl.begin_campaign_attempt("pflege-okt-2026", LEAD, 1)
    definition = {"name": "erstkontakt", "language": "de", "status": "APPROVED",
                  "parameter_format": "POSITIONAL",
                  "components": [{"type": "BODY", "text": "Guten Tag, drei Stellen in Augsburg."}]}

    assert cl.send_template(LEAD, definition=definition) == BI.campaign_key(
        campaign_id="pflege-okt-2026", phone=LEAD, attempt=1)
    assert rail.requests[0]["trace"]["action"] == "first_touch"


def test_a_two_bubble_turn_waits_out_the_rails_own_gap_instead_of_being_refused(rail):
    """A Luna reply is routinely two or three bubbles and the handset's floor is 4 s between
    bubbles to one person. The client waits for the ``next_slot_at`` the fuse published in the
    previous 200 -- without it the second bubble was a 429, the turn raised, and the candidate got
    half an answer now and the other half from a second brain call three minutes later."""
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, "ask_question")
    cl.send_text(LEAD, "Erste Blase.")
    cl.send_text(LEAD, "Zweite Blase.")
    assert rail.driver.sent == ["Erste Blase.", "Zweite Blase."]
    assert [r["trace"]["bubble_index"] for r in rail.requests] == [0, 1]
    assert len({r["client_msg_id"] for r in rail.requests}) == 2
    assert rail.slept and min(rail.slept) >= 4.0, "the gap is the rail's own, not zero"


def test_buttons_arrive_as_numbered_text_the_candidate_can_answer(rail):
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, "ask_consent")
    cl.send_buttons(LEAD, "Darf ich Sie anonym vorstellen?",
                    [{"id": "consent:yes", "title": "Ja, gerne"},
                     {"id": "consent:no", "title": "Nein danke"}])
    assert rail.driver.sent == [
        "Darf ich Sie anonym vorstellen?\n\n1. Ja, gerne\n2. Nein danke"]


# --- the same turn, driven twice ---------------------------------------------------------------
def test_the_catch_up_re_drive_of_a_turn_replays_instead_of_sending_twice(rail):
    """The failure chain app/wa/bridge_ids.py documents, end to end: the turn is re-driven three
    minutes later with the same turn_key, and the candidate must not read the same reply twice."""
    for _ in range(2):
        cl = rail.client()
        cl.begin_turn(LEAD, TURN, "ask_question")
        assert cl.send_text(LEAD, "Wo moechten Sie arbeiten?")
    assert rail.driver.sent == ["Wo moechten Sie arbeiten?"], "one bubble reached the phone"
    assert len(rail.requests) == 2, "and the second request really was made"


def test_a_regenerated_reply_under_a_live_key_sends_nothing(rail):
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, "ask_question")
    cl.send_text(LEAD, "Wo moechten Sie arbeiten?")

    again = rail.client()
    again.begin_turn(LEAD, TURN, "ask_question")
    assert again.send_text(LEAD, "Ein anderer Satz.") == BI.reply_key(
        phone=LEAD, turn_key=TURN, action="ask_question", bubble_index=0)
    assert rail.driver.sent == ["Wo moechten Sie arbeiten?"], "first body wins"


# --- the refusals, as the VPS classifies them ----------------------------------------------------
def test_a_busy_handset_is_retryable_and_leaves_the_key_usable(rail):
    """The other lane holds the same flock across its own brain call, so this is the ordinary case.
    Nothing was typed, so the deterministic key has to survive it -- a row left ``attempting`` here
    answered 504 to every later attempt on that key, forever, and nothing reconciles it yet."""
    rail.driver.busy = True
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, "ask_question")
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_text(LEAD, "Wo moechten Sie arbeiten?")
    assert caught.value.status_code == 503
    assert rail.driver.sent == []
    assert rail.ledger.get(BI.reply_key(phone=LEAD, turn_key=TURN, action="ask_question",
                                        bubble_index=0)) is None, "no row to reconcile"

    # and the retry, once the other lane lets go, really sends
    rail.driver.busy = False
    retry = rail.client()
    retry.begin_turn(LEAD, TURN, "ask_question")
    retry.send_text(LEAD, "Wo moechten Sie arbeiten?")
    assert rail.driver.sent == ["Wo moechten Sie arbeiten?"]


def test_quiet_hours_refuse_a_reply_and_say_when_to_come_back(tmp_path):
    rail = Rail(tmp_path, moment=datetime(2026, 9, 23, 7, 53, tzinfo=BERLIN).astimezone(timezone.utc))
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, "ask_question")
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_text(LEAD, "Guten Morgen.")
    assert caught.value.status_code == 429
    assert rail.driver.sent == []


def test_an_unverified_bubble_is_never_recorded_as_sent(tmp_path):
    rail = Rail(tmp_path, driver=D.FakeDriver(ticks=[D.UNVERIFIED]))
    cl = rail.client()
    cl.begin_turn(LEAD, TURN, "ask_question")
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_text(LEAD, "Guten Tag.")
    assert caught.value.status_code == BR.UNCERTAIN_STATUS


def test_the_read_timeout_the_client_allows_covers_what_the_phone_needs(rail):
    """A 219-character reply is 41-67 s of typing at the handset's own pace before the verify and
    tick waits begin. The old fixed 90 s expired while the executor was still working."""
    cl = rail.client()
    body = "x" * 219
    assert cl.send_timeout(body) > BR.EXECUTOR_FIXED_BUDGET_SEC + 60
    cl.begin_turn(LEAD, TURN, "ask_question")
    cl.send_text(LEAD, body)
    assert rail.driver.sent == [body]
