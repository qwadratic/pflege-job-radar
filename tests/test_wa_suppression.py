"""TASK-113/TASK-137 offline: the cross-thread, cross-lane suppression store (app/wa/suppression.py).

What is asserted is what the harness does, not how it stores it: the reply that never goes out, the
campaign template that is never claimed, the Stopp that outlives the thread it was typed in, and the
untouched number next to it. The model is never called for real (a Stopp returns from luna_brain
before any model call; every other turn fakes ``luna_brain.turn``), Meta is a recording fake, and every
number is synthetic.
"""
from datetime import datetime, timezone

import pytest

from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST
from app.wa import suppression as SUP
from app.wa.luna import campaign as CAMP

PHONE_ID = "113000111"
LEAD = "+4915550113001"
OTHER = "+4915550113002"
CAMPAIGN = "suppression-test-2026-09"
DEFINITION = {"id": "1130000000000001", "name": "synthetic_bayern_interesse_de", "language": "de"}
BERLIN = "Europe/Berlin"


class FakeMeta:
    """Records what would have gone out. Every assertion about "nothing was sent" reads ``sent``/``posts``."""

    def __init__(self):
        self.sent, self.posts = [], []

    def send_text(self, to_e164, body):
        self.sent.append(to_e164)
        return f"wamid.out.{len(self.sent)}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)

    def send_template(self, to_e164, template_name=None, language=None, params=None, *, definition=None):
        self.posts.append(to_e164)
        return f"wamid.camp.{len(self.posts)}"


class RefusingTransport(FakeMeta):
    """A rail that refuses the recipient itself -- what the phone rail does once its executor holds the
    same list (TASK-120). Proves campaign.send_one classifies the refusal, not that it produces it."""

    def __init__(self, record):
        super().__init__()
        self.record = record

    def send_template(self, to_e164, template_name=None, language=None, params=None, *, definition=None):
        raise SUP.SuppressedRecipient(self.record)


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    return FakeMeta()


def _arrived(c, phone, wamid, text):
    """The thread as finish_inbound hands it to process_owed_turn: the inbound row is stored and the
    free-form window is open."""
    ST.record_inbound(c, phone, wamid, text)
    t = ST.thread(c, phone)
    t["last_inbound_at"] = ST.now_iso()
    return t


def _reply(monkeypatch, bubbles=("Guten Tag!",)):
    """Make the brain want to say something, so a test that sends nothing sent nothing on purpose."""
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: {
        "bubbles": list(bubbles), "buttons": [], "slots": dict(thread.get("slots") or {}), "asked": [],
        "stopped": False, "matches": [], "action": "reply_now_conversational"})


def _lead(phone):
    return {"phone": phone, "params": {"body": ["Frau Test"]},
            "rendered": {"name": DEFINITION["name"], "language": DEFINITION["language"],
                         "text": "Hallo, Frau Test.", "buttons": []}}


def _send_one(phone, client):
    return CAMP.send_one(CAMPAIGN, DEFINITION, _lead(phone), client, lambda: datetime.now(timezone.utc),
                         CAMP.Retry(), CAMP.Window("00-24", BERLIN))


def _attempts(phone):
    with CAMP.live_db() as c:
        return ST.campaign_attempts(c, CAMPAIGN, phone)


# --- the detector writes the list -----------------------------------------------------------------

# Inbound messages slots.is_stop (the one matcher, reused -- no second vocabulary here) reads as a refusal.
# The real brain runs for these: it returns on the stop check before any model call.
STOP_TEXTS = ["Stopp", "STOP", "Bitte abmelden", "keine nachrichten mehr bitte", "Löschen Sie meine Daten",
              "unsubscribe"]
# Messages that are not a refusal, including the whole-word trap ('stopfen' is not 'stop').
ORDINARY_TEXTS = ["Ja, gerne!", "Ich stopfe gerade Socken", "Wo genau ist die Stelle?"]


@pytest.mark.parametrize("text", STOP_TEXTS)
def test_an_inbound_stop_token_suppresses_the_number_for_every_rail(wa, text):
    with ST.db() as c:
        t = _arrived(c, LEAD, "wamid.stop", text)
        result = WAPI.process_owed_turn(c, t, text, None, "wamid.stop", client=wa)
        assert SUP.is_suppressed(c, LEAD) is True
    assert result["status"] == "stopped" and wa.sent == []


@pytest.mark.parametrize("text", ORDINARY_TEXTS)
def test_an_ordinary_message_suppresses_nothing(wa, monkeypatch, text):
    _reply(monkeypatch)   # these rows are about what is NOT written; the model itself is out of scope
    with ST.db() as c:
        t = _arrived(c, LEAD, "wamid.ok", text)
        result = WAPI.process_owed_turn(c, t, text, None, "wamid.ok", client=wa)
        assert SUP.is_suppressed(c, LEAD) is False
    assert result["status"] == "sent"


def test_the_suppression_records_the_words_the_candidate_typed_and_the_rail_that_heard_them(wa, monkeypatch):
    monkeypatch.setattr(C, "TRANSPORT", "bridge")
    with ST.db() as c:
        t = _arrived(c, LEAD, "wamid.stop", "Stopp, bitte nicht mehr schreiben")
        WAPI.process_owed_turn(c, t, "Stopp, bitte nicht mehr schreiben", None, "wamid.stop", client=wa)
        record = SUP.suppression(c, LEAD)
    assert record["trigger_text"] == "Stopp, bitte nicht mehr schreiben"
    assert (record["reason"], record["lane"]) == (SUP.REASON_STOP, "bridge")
    assert record["at"] and record["phone"] == LEAD


def test_a_stop_also_stops_the_thread_as_before(wa):
    """The thread-scoped opt-out (wa_threads.stopped) is untouched: the list is added next to it, not instead."""
    with ST.db() as c:
        t = _arrived(c, LEAD, "wamid.stop", "Stopp")
        WAPI.process_owed_turn(c, t, "Stopp", None, "wamid.stop", client=wa)
        ST.save_thread(c, t)
        assert (ST.thread(c, LEAD)["stopped"], ST.thread(c, LEAD)["stopped_reason"]) == (True, ST.STOPPED)


# --- the list is keyed on the human, not the thread ------------------------------------------------

# The four spellings of one German mobile number that reach us from a webhook, a CSV and a handset.
SPELLINGS = ["+49170113001", "0049170113001", "49170113001", "0170113001"]


@pytest.mark.parametrize("written_as", SPELLINGS)
def test_one_human_is_one_identity_however_the_number_is_written(wa, written_as):
    with ST.db() as c:
        SUP.suppress(c, "0170113001", SUP.REASON_STOP, "bridge", trigger_text="Stopp")
        assert SUP.is_suppressed(c, written_as) is True


def test_a_number_nobody_suppressed_is_not_on_the_list(wa):
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "meta", trigger_text="Stopp")
        assert SUP.is_suppressed(c, OTHER) is False


def test_suppressing_twice_keeps_the_first_refusal(wa):
    """Idempotent and first-write-wins: a redelivered webhook or a second Stopp must not rewrite when the
    candidate first refused, or with which words."""
    with ST.db() as c:
        first = SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp",
                             at="2026-09-01T10:00:00+00:00")
        again = SUP.suppress(c, "0" + LEAD[3:], "operator entry", "meta", trigger_text="noch mal",
                             at="2026-09-20T10:00:00+00:00")
        assert again == first
        assert SUP.suppressions(c) == [first]


def test_a_suppression_outlives_the_thread_it_was_typed_in(wa, monkeypatch):
    """A new thread for the same number -- the row purged, or a first message arriving after a wipe -- starts
    with stopped=0. The refusal must survive that; wa_threads alone cannot carry it."""
    _reply(monkeypatch)
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp")
        c.execute("delete from wa_threads where phone=?", (LEAD,))
        c.commit()
        t = _arrived(c, LEAD, "wamid.new", "Hallo, ich bin wieder da")
        assert t["stopped"] is False, "a fresh thread knows nothing about the old one"
        with pytest.raises(SUP.SuppressedRecipient):
            WAPI.process_owed_turn(c, t, "Hallo, ich bin wieder da", None, "wamid.new", client=wa)
    assert wa.sent == []


# --- no send path may reach a suppressed number ----------------------------------------------------

def test_a_suppressed_number_gets_no_reply(wa, monkeypatch):
    _reply(monkeypatch)
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp")
        t = _arrived(c, LEAD, "wamid.1", "Und was verdient man da?")
        with pytest.raises(SUP.SuppressedRecipient):
            WAPI.process_owed_turn(c, t, "Und was verdient man da?", None, "wamid.1", client=wa)
    assert wa.sent == [], "the brain had bubbles ready; the send path refused the recipient"


def test_the_number_next_to_it_is_answered_normally(wa, monkeypatch):
    _reply(monkeypatch)
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp")
        t = _arrived(c, OTHER, "wamid.2", "Und was verdient man da?")
        result = WAPI.process_owed_turn(c, t, "Und was verdient man da?", None, "wamid.2", client=wa)
    assert result["status"] == "sent" and wa.sent == [OTHER]


def test_the_refusal_is_recorded_where_an_operator_reads_it(wa, monkeypatch):
    """Not a silent skip: the same trace any other undeliverable thread leaves (GET /wa/threads
    last_send_error)."""
    _reply(monkeypatch)
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp")
        t = _arrived(c, LEAD, "wamid.1", "Hallo")
        with pytest.raises(SUP.SuppressedRecipient):
            WAPI.process_owed_turn(c, t, "Hallo", None, "wamid.1", client=wa)
        failure = ST.recent_send_failure(c, LEAD)
    assert "suppressed recipient" in failure["error"] and "bridge" in failure["error"]
    assert LEAD not in failure["error"] and "Stopp" not in failure["error"], "no number, no candidate words"


def test_the_follow_up_pool_never_offers_a_suppressed_number(wa):
    """luna.followups sweeps store.candidate_phones; a suppressed number is filtered there so one refusal
    cannot end the sweep for every other thread."""
    with ST.db() as c:
        ST.thread(c, LEAD)
        ST.thread(c, OTHER)
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp")
        assert ST.candidate_phones(c) == [OTHER]


# --- campaigns ---------------------------------------------------------------------------------------

def test_a_campaign_neither_claims_nor_posts_to_a_suppressed_number(wa):
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp")
    result = _send_one(LEAD, wa)
    assert result["status"] == "skip_suppressed"
    assert "bridge" in result["reason"]
    assert wa.posts == [] and _attempts(LEAD) == []


def test_a_campaign_sends_to_a_number_that_is_not_on_the_list(wa):
    result = _send_one(OTHER, wa)
    assert result["status"] == "sent" and wa.posts == [OTHER]


def test_a_rail_that_refuses_the_recipient_fails_the_attempt_permanently(wa):
    """The uncertain/failed split decides whether --retry-uncertain may post it again. A refused recipient
    is the one thing that must never be retried."""
    record = {"phone": OTHER, "reason": SUP.REASON_STOP, "lane": "bridge", "trigger_text": "Stopp",
              "at": "2026-09-20T10:00:00+00:00"}
    result = _send_one(OTHER, RefusingTransport(record))
    assert result["status"] == "failed", "uncertain would authorise a resend"
    assert result["error_code"] == SUP.ERROR_CODE and result["http_status"] == 403
    assert [a["state"] for a in _attempts(OTHER)] == ["failed"]


def test_the_refusal_is_a_meta_error_so_one_classifier_covers_both_rails(wa):
    """campaign.send_one classifies retryability in exactly one place (a MetaError with a 4xx is permanent).
    This is the contract SuppressedRecipient rides instead of a second branch."""
    record = {"phone": LEAD, "reason": SUP.REASON_STOP, "lane": "meta", "trigger_text": "Stopp",
              "at": "2026-09-20T10:00:00+00:00"}
    exc = SUP.SuppressedRecipient(record)
    assert isinstance(exc, M.MetaError) and 400 <= exc.status_code < 500


# --- reporting ----------------------------------------------------------------------------------------

def test_the_read_helper_lists_the_whole_list_newest_first(wa):
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "bridge", trigger_text="Stopp", at="2026-09-01T10:00:00+00:00")
        SUP.suppress(c, OTHER, SUP.REASON_STOP, "meta", trigger_text="abmelden", at="2026-09-20T10:00:00+00:00")
        rows = SUP.suppressions(c)
    assert [r["phone"] for r in rows] == [OTHER, LEAD]
    assert [r["lane"] for r in rows] == ["meta", "bridge"]


def test_a_number_that_canonicalizes_to_nothing_fails_loudly(wa):
    with ST.db() as c:
        with pytest.raises(ValueError):
            SUP.suppress(c, "", SUP.REASON_STOP, "meta")
