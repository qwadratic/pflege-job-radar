"""Offline tests for the phone rail's outbound client (TASK-120).

No network: every client here is built with an injected ``transport=``, the same seam
``meta.Client`` has. The tests are written against what a caller observes -- what lands in
``wa_messages.wamid``, what ``campaign.py`` classifies the raise as, whether the executor was called
once or twice -- because the rail's whole value is that those outcomes cannot lie.
"""
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

from app.wa import bridge as BR
from app.wa import bridge_ids as BI
from app.wa import config as C
from app.wa import meta as M

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAD = "+491701234567"
OTHER = "+491709999999"
TURN = "wamid.HBgNNDkxNzAxMjM0NTY3FQIAEhgg"
BASE = "http://127.0.0.1:8793"


class FakeExecutor:
    """Stands in for the executor on the remote machine. Records every call and answers from a
    queue; an exception in the queue is raised instead of returned. An unexpected call fails the
    test rather than reaching the network."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {},
                           "body": json.loads(data) if data else None, "timeout": timeout})
        if not self.answers:
            raise AssertionError(f"unexpected call to the bridge: {method} {url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def sends(self):
        return [c for c in self.calls if c["url"].endswith(BR.MESSAGES_PATH)]


def sent(client_msg_id, tick="Zugestellt", **extra):
    """A terminal 200 body: the only answer that may become a `sent` row."""
    return 200, {"ok": True, "state": "sent", "client_msg_id": client_msg_id,
                 "verified": {"tick": tick, "read_at_send": True},
                 "sent_at": "2026-09-21T10:00:00.000Z", "replayed": False, **extra}


def build(*answers, media_transport=None, **kw):
    """-> (client, fake executor)."""
    fake = FakeExecutor(*answers)
    return BR.Client(transport=fake, media_transport=media_transport, base_url=BASE, token="tok", **kw), fake


def reply_client(*answers, phone=LEAD, action="reply", **kw):
    cl, fake = build(*answers, **kw)
    cl.begin_turn(phone, TURN, action)
    return cl, fake


def campaign_verdict(exc):
    """app/wa/luna/campaign.py:675-678, verbatim -- the classification BridgeError must leave
    byte-identical. Mirrored here rather than imported because it is three lines inside a function
    that needs a claimed campaign row and a live database; the test below pins the mirror to the
    original."""
    status_code = exc.status_code
    rejected = isinstance(exc, M.TemplateParamsError) or (status_code is not None and 400 <= status_code < 500)
    return "failed" if rejected else "uncertain"


def test_the_classification_mirrored_in_this_file_is_still_campaigns_own():
    src = (ROOT / "app" / "wa" / "luna" / "campaign.py").read_text(encoding="utf-8")
    assert ("rejected = isinstance(exc, M.TemplateParamsError) or "
            "(status_code is not None and 400 <= status_code < 500)") in src


# --- capabilities ------------------------------------------------------------------------------

def test_the_capability_flags_say_what_this_rail_can_do():
    """api.py's window gate reads requires_freeform_window through getattr with a Meta default, so a
    thread whose 24h Cloud-API window closed is answerable here -- the rail's whole v1 value."""
    cl, _ = build()
    assert cl.requires_freeform_window is False
    assert cl.supports_buttons is False
    assert cl.wants_idempotency_key is True
    # the Meta client carries none of them, which is what keeps the getattr default honest
    assert getattr(M.Client, "requires_freeform_window", True) is True
    assert getattr(M.Client, "wants_idempotency_key", False) is False


# --- a send that really went out ------------------------------------------------------------------

def test_a_verified_tick_returns_the_id_the_thread_will_remember():
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    cl, fake = reply_client(sent(key))
    assert cl.send_text(LEAD, "Guten Tag!") == key
    assert cl.last_send["verified"]["tick"] == "Zugestellt"


def test_the_injected_transport_is_used_verbatim_and_carries_the_whole_contract():
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    cl, fake = reply_client(sent(key))
    cl.send_text(LEAD, "Guten Tag!")
    call = fake.sends[0]
    assert cl.transport is fake
    assert call["method"] == "POST" and call["url"] == BASE + BR.MESSAGES_PATH
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["timeout"] == cl.send_timeout("Guten Tag!") > C.BRIDGE_TIMEOUT_SEC
    # trace.action is the executor's PACING class, not Luna's slug -- the slug rides as intent.
    assert call["body"] == {"client_msg_id": key, "to": LEAD, "kind": "text", "body": "Guten Tag!",
                            "trace": {"action": "reply", "intent": "reply", "turn_key": TURN,
                                      "bubble_index": 0}}


@pytest.mark.parametrize("tick", BR.VERIFIED_TICKS)
def test_each_tick_the_driver_can_read_counts_as_delivered(tick):
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    cl, _ = reply_client(sent(key, tick=tick))
    assert cl.send_text(LEAD, "Guten Tag!") == key


# --- a send that did not, or may not have -----------------------------------------------------------

@pytest.mark.parametrize("verified", [
    {"tick": "unverified"},                       # their "pressed send, never found the bubble" sentinel
    {"tick": ""},                                 # pressed send, no tick on the bubble yet
    {"tick": None},
    {},                                           # a 200 with no verified block at all
])
def test_a_200_without_a_verified_tick_is_never_a_send(verified):
    """Their own lane records this as sent (it fired on 2 of its 23 live sends). Refusing it here is
    half the reason we own this layer."""
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    body = {"ok": True, "state": "sent", "client_msg_id": key, "verified": verified}
    cl, fake = reply_client((200, body))
    with pytest.raises(BR.BridgeError) as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert err.value.status_code == BR.UNCERTAIN_STATUS
    assert campaign_verdict(err.value) == "uncertain"
    assert len(fake.sends) == 1, "an uncertain send is never retried by the client itself"


def test_the_unverified_refusal_names_the_failure_it_is_refusing():
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    cl, _ = reply_client((200, {"ok": True, "state": "sent", "client_msg_id": key,
                                "verified": {"tick": "unverified"}}))
    with pytest.raises(BR.BridgeError) as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert "unverified" in str(err.value) and "never auto-resent" in str(err.value)


def test_an_accepted_202_is_uncertain_and_keeps_the_key_for_reconciliation():
    """202-first is the normal answer on a paced rail. It must never read as sent, and the id has to
    survive the raise or bridge_sync (TASK-125) has nothing to resolve."""
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    cl, fake = reply_client((202, {"ok": True, "state": "queued", "client_msg_id": key,
                                   "scheduled_at": "2026-09-21T10:05:00Z", "reason": "min_gap"}))
    with pytest.raises(BR.BridgeAccepted) as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert isinstance(err.value, M.MetaError)
    assert err.value.status_code == BR.UNCERTAIN_STATUS and campaign_verdict(err.value) == "uncertain"
    assert err.value.client_msg_id == key and err.value.payload["state"] == "queued"
    assert cl.last_send is None and len(fake.sends) == 1


def test_an_answer_for_a_different_key_is_not_a_send():
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    cl, _ = reply_client(sent("wab.o.somebodyelse"))
    with pytest.raises(BR.BridgeError, match="we do not know what went out") as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert err.value.status_code == BR.UNCERTAIN_STATUS
    assert err.value.client_msg_id == key and err.value.payload["client_msg_id"] == "wab.o.somebodyelse"


@pytest.mark.parametrize("status,verdict", [(400, "failed"), (401, "failed"), (409, "failed"),
                                            (422, "failed"), (429, "failed"),
                                            (500, "uncertain"), (503, "uncertain"), (504, "uncertain")])
def test_the_bridges_own_status_reaches_campaigns_classification(status, verdict):
    """TASK-120 AC#1/#2: BridgeError is a MetaError carrying .status_code, so campaign.py needs no
    change to tell "nothing went out, restore ownership" from "we do not know"."""
    exc = BR.BridgeError(f"bridge HTTP {status}", status_code=status, payload={"error": {"code": "x"}})
    cl, fake = reply_client(exc)
    with pytest.raises(BR.BridgeError) as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert isinstance(err.value, M.MetaError)
    assert err.value.status_code == status and campaign_verdict(err.value) == verdict
    assert len(fake.sends) == 1


# --- the unreachable-bridge asymmetry ---------------------------------------------------------------

def test_an_unreachable_bridge_fails_a_send_as_4xx():
    """Nothing went out and we know it: campaign.py records `failed`, ownership is restored and the
    lead is retryable -- not `uncertain`, which nobody may safely re-drive by hand."""
    cl, fake = reply_client(BR.BridgeUnreachable("tunnel down"))
    with pytest.raises(BR.BridgeError) as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert err.value.status_code == BR.UNREACHABLE_SEND_STATUS == 424
    assert campaign_verdict(err.value) == "failed"
    assert err.value.client_msg_id == BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    assert len(fake.sends) == 1


def test_an_unreachable_bridge_aborts_an_import_run_instead_of_skipping_a_document():
    """The deliberate other half: import_history.py:538-539 treats a None status as "not an answer
    about this media" and re-raises, which ends the run. A 4xx there would record the candidate's
    Urkunde as permanently unrecoverable because a tunnel was down for a second."""
    from app.wa.luna import import_history as IH

    cl, _ = build(BR.BridgeUnreachable("tunnel down"))
    doc = {"path": "/nonexistent/urkunde.pdf", "media_id": "wab.m.5c8e21aa7f0349bd6e12", "mime_type": None}
    with pytest.raises(BR.BridgeUnreachable) as err:
        IH._obtain(types.SimpleNamespace(media_roots=()), doc, cl)
    assert err.value.status_code is None


def test_a_media_lookup_returns_the_url_and_mime_type_the_importer_reads():
    cl, fake = build((200, {"url": BASE + "/v1/media/wab.m.abc", "mime_type": "application/pdf"}),
                     media_transport=lambda **kw: b"%PDF-1.4 bytes")
    info = cl.media_url("wab.m.abc")
    assert info["url"].endswith("/v1/media/wab.m.abc") and info["mime_type"] == "application/pdf"
    assert fake.calls[0]["method"] == "GET"
    assert cl.download_media(info["url"]) == b"%PDF-1.4 bytes"


def test_a_media_lookup_with_no_url_also_aborts_the_run():
    cl, _ = build((200, {"mime_type": "application/pdf"}))
    with pytest.raises(BR.BridgeError) as err:
        cl.media_url("wab.m.abc")
    assert err.value.status_code is None


def test_a_relative_media_url_is_resolved_against_the_clients_own_base_url():
    """TASK-131: the executor cannot know which local port our ssh tunnel maps it to, so it answers
    with a path and this client resolves it -- the same base every other route on this rail uses."""
    cl, _ = build((200, {"url": "/v1/media/wab.m.abc/raw", "mime_type": "application/pdf"}))
    info = cl.media_url("wab.m.abc")
    assert info["url"] == BASE + "/v1/media/wab.m.abc/raw"


def test_an_already_absolute_media_url_is_passed_through_untouched():
    cl, _ = build((200, {"url": "http://127.0.0.1:9999/elsewhere", "mime_type": "image/jpeg"}))
    assert cl.media_url("wab.m.abc")["url"] == "http://127.0.0.1:9999/elsewhere"


def test_a_media_lookup_that_definitely_refuses_is_a_404_not_an_unanswered_question():
    """The other half of the None-versus-raise distinction import_history.py depends on: a bridge
    that DID answer, and said no (the file was never pulled), must not read like an unreachable
    bridge -- that would abort the whole import run over one recoverable-elsewhere document."""
    cl, _ = build((404, {"error": {"code": "media_not_found", "message": "no media pulled"}}))
    with pytest.raises(BR.BridgeError) as err:
        cl.media_url("wab.m.never-pulled")
    assert err.value.status_code == 404


def test_download_media_never_goes_through_the_json_transport():
    """Same reason meta.py carries a second transport: a parsed body corrupts binary content."""
    seen = {}

    def media_transport(method, url, headers=None, timeout=None):
        seen.update(method=method, url=url, headers=headers)
        return b"\x89PNG\r\n\x1a\n"

    cl, fake = build(media_transport=media_transport)
    assert cl.download_media(BASE + "/v1/media/wab.m.abc") == b"\x89PNG\r\n\x1a\n"
    assert seen["headers"]["Authorization"] == "Bearer tok" and fake.calls == []


# --- one turn, many bubbles, deterministic keys (TASK-114 at the client) ----------------------------

def test_the_bubbles_of_one_turn_are_separate_calls_with_separate_keys():
    keys = [BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=i) for i in range(2)]
    cl, fake = reply_client(sent(keys[0]), sent(keys[1]))
    assert [cl.send_text(LEAD, b) for b in ("Guten Tag!", "Passt Ihnen Augsburg?")] == keys
    assert [c["body"]["trace"]["bubble_index"] for c in fake.sends] == [0, 1]


def test_a_regenerated_turn_re_posts_the_same_key_for_a_bubble_that_already_went_out():
    """The failure chain in bridge_ids.py's docstring, at this layer: bubble 0 is delivered, bubble 1
    raises, the claim is reclaimable, the catch-up timer re-drives the turn and the brain writes new
    text. Bubble 0's key is unchanged, so the executor's ledger replays it and the candidate is not
    told the same thing twice."""
    key0 = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=0)
    key1 = BI.reply_key(phone=LEAD, turn_key=TURN, action="reply", bubble_index=1)
    cl, fake = reply_client(sent(key0), BR.BridgeUnreachable("tunnel down"),
                            sent(key0, replayed=True, body_mismatch=True), sent(key1))
    assert cl.send_text(LEAD, "Guten Tag!") == key0
    with pytest.raises(BR.BridgeError):
        cl.send_text(LEAD, "Passt Ihnen Augsburg?")

    cl.begin_turn(LEAD, TURN, "reply")                       # the catch-up run, new bubbles
    assert cl.send_text(LEAD, "Guten Tag, hier noch einmal!") == key0
    # first body wins on the executor: the candidate keeps the bubble they already read, and the
    # replay flags reach the caller untouched rather than being smoothed into a fresh send.
    assert cl.last_send["replayed"] is True and cl.last_send["body_mismatch"] is True
    assert cl.send_text(LEAD, "Waere Augsburg etwas?") == key1
    assert [c["body"]["client_msg_id"] for c in fake.sends] == [key0, key1, key0, key1]


def test_a_send_with_no_turn_open_never_reaches_the_bridge():
    cl, fake = build()
    with pytest.raises(BR.BridgeError, match="no turn is open") as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert err.value.status_code == 400 and fake.calls == []


def test_a_send_to_somebody_other_than_the_turns_phone_is_refused():
    cl, fake = reply_client()
    with pytest.raises(BR.BridgeError, match="turn was opened for"):
        cl.send_text(OTHER, "Guten Tag!")
    assert fake.calls == []


def test_a_campaign_attempt_keys_on_the_campaign_and_not_on_the_turn():
    definition = {"name": "erstkontakt", "language": "de", "status": "APPROVED",
                  "parameter_format": "POSITIONAL",
                  "components": [{"type": "BODY", "text": "Guten Tag {{1}}, drei Stellen in Augsburg."}]}
    key = BI.campaign_key(campaign_id="pflege-okt-2026", phone=LEAD, attempt=1)
    cl, fake = build(sent(key))
    cl.begin_campaign_attempt("pflege-okt-2026", LEAD, 1)
    assert cl.send_template(LEAD, definition=definition, params={"body": ["Frau Meier"]}) == key
    body = fake.sends[0]["body"]
    assert body["body"] == "Guten Tag Frau Meier, drei Stellen in Augsburg."
    # A campaign attempt is cold contact, and trace.action is what the governor paces on.
    assert body["trace"] == {"action": "first_touch", "campaign_id": "pflege-okt-2026", "attempt": 1}


# --- what this rail cannot do, said loudly ----------------------------------------------------------

def test_send_buttons_renders_the_titles_as_numbered_text():
    """There is no tappable button on a phone rail, so the candidate gets something they can type
    an answer to. Raising instead made the funnel's close step unreachable on the only rail Ivan
    runs, and a bare NotImplementedError is not a MetaError, so nothing classified the failure and
    catch-up re-drove the turn forever (TASK-146)."""
    key = BI.reply_key(phone=LEAD, turn_key=TURN, action="ask_consent", bubble_index=0)
    cl, fake = build(sent(key))
    cl.begin_turn(LEAD, TURN, "ask_consent")
    assert cl.send_buttons(LEAD, "Soll ich Ihr Profil schicken?",
                           [{"id": "consent:yes", "title": "Ja, gerne"},
                            {"id": "consent:no", "title": "Nein danke"}]) == key
    assert fake.sends[0]["body"]["body"] == (
        "Soll ich Ihr Profil schicken?\n\n1. Ja, gerne\n2. Nein danke")


def test_send_buttons_with_no_title_to_render_is_a_4xx_not_a_bare_exception():
    cl, fake = reply_client()
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_buttons(LEAD, "Soll ich Ihr Profil schicken?", [{"id": "consent:yes", "title": " "}])
    assert caught.value.status_code == BR.CONTRACT_STATUS
    assert fake.calls == []


def test_send_photos_posts_the_phone_and_local_paths_and_returns_the_body():
    """TASK-131 round 7 (Ivan, 2026-09-22): mechanism proof, no idempotency key -- unlike send_text/
    send_buttons this does not go through begin_turn/begin_campaign_attempt at all, matching the
    executor route it calls (no client_msg_id there either)."""
    cl, fake = build((200, {"ok": True, "at": "2026-09-22T21:00:00.000Z",
                            "sent": [{"clock": "21:00", "tick": "Gesendet"},
                                    {"clock": "21:01", "tick": "Gesendet"}]}))
    result = cl.send_photos(LEAD, ["/tmp/a.jpg", "/tmp/b.jpg"])
    assert result["sent"] == [{"clock": "21:00", "tick": "Gesendet"},
                              {"clock": "21:01", "tick": "Gesendet"}]
    [call] = fake.calls
    assert call["url"] == BASE + BR.PHOTOS_PATH
    assert call["body"] == {"phone": LEAD, "local_paths": ["/tmp/a.jpg", "/tmp/b.jpg"]}


def test_send_photos_with_no_files_is_a_4xx_not_a_bare_exception():
    cl, fake = build()
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_photos(LEAD, [])
    assert caught.value.status_code == BR.CONTRACT_STATUS
    assert fake.calls == []


def test_send_photos_surfaces_a_non_200_as_a_bridge_error():
    cl, fake = build((500, {"error": {"code": "executor_error", "detail": "boom"}}))
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_photos(LEAD, ["/tmp/a.jpg"])
    assert caught.value.status_code == 500


def test_send_gallery_posts_the_phone_local_paths_and_caption_and_returns_the_body():
    """TASK-131 round 7 gallery redesign (Ivan, 2026-09-22): one message, several photos, a shared
    caption -- same mechanism-proof shape as send_photos, its own route."""
    cl, fake = build((200, {"ok": True, "at": "2026-09-23T02:00:00.000Z",
                            "clock": "02:00", "tick": "Gesendet"}))
    result = cl.send_gallery(LEAD, ["/tmp/a.jpg", "/tmp/b.jpg"], caption="Unsere Klinik")
    assert (result["clock"], result["tick"]) == ("02:00", "Gesendet")
    [call] = fake.calls
    assert call["url"] == BASE + BR.GALLERY_PATH
    assert call["body"] == {"phone": LEAD, "local_paths": ["/tmp/a.jpg", "/tmp/b.jpg"],
                            "caption": "Unsere Klinik"}


def test_send_gallery_omits_caption_from_the_body_when_none_is_given():
    cl, fake = build((200, {"ok": True, "at": "x", "clock": "02:00", "tick": "Gesendet"}))
    cl.send_gallery(LEAD, ["/tmp/a.jpg"])
    [call] = fake.calls
    assert call["body"] == {"phone": LEAD, "local_paths": ["/tmp/a.jpg"]}


def test_send_gallery_with_no_files_is_a_4xx_not_a_bare_exception():
    cl, fake = build()
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_gallery(LEAD, [])
    assert caught.value.status_code == BR.CONTRACT_STATUS
    assert fake.calls == []


def test_send_gallery_surfaces_a_non_200_as_a_bridge_error():
    cl, fake = build((500, {"error": {"code": "executor_error", "detail": "boom"}}))
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_gallery(LEAD, ["/tmp/a.jpg"])
    assert caught.value.status_code == 500


def test_send_document_posts_the_phone_local_path_and_caption_and_returns_the_body():
    """TASK-131 round 7 (Ivan, 2026-09-23): a file, any type -- same mechanism-proof shape as
    send_photos/send_gallery, its own route."""
    cl, fake = build((200, {"ok": True, "at": "2026-09-23T02:50:00.000Z",
                            "clock": "02:50", "tick": "Gesendet"}))
    result = cl.send_document(LEAD, "/tmp/Lebenslauf.pdf", caption="Bitte pruefen")
    assert (result["clock"], result["tick"]) == ("02:50", "Gesendet")
    [call] = fake.calls
    assert call["url"] == BASE + BR.DOCUMENT_PATH
    assert call["body"] == {"phone": LEAD, "local_path": "/tmp/Lebenslauf.pdf",
                            "caption": "Bitte pruefen"}


def test_send_document_omits_caption_from_the_body_when_none_is_given():
    cl, fake = build((200, {"ok": True, "at": "x", "clock": "02:50", "tick": "Gesendet"}))
    cl.send_document(LEAD, "/tmp/Lebenslauf.pdf")
    [call] = fake.calls
    assert call["body"] == {"phone": LEAD, "local_path": "/tmp/Lebenslauf.pdf"}


def test_send_document_with_no_file_is_a_4xx_not_a_bare_exception():
    cl, fake = build()
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_document(LEAD, "")
    assert caught.value.status_code == BR.CONTRACT_STATUS
    assert fake.calls == []


def test_send_document_surfaces_a_non_200_as_a_bridge_error():
    cl, fake = build((500, {"error": {"code": "executor_error", "detail": "boom"}}))
    with pytest.raises(BR.BridgeError) as caught:
        cl.send_document(LEAD, "/tmp/Lebenslauf.pdf")
    assert caught.value.status_code == 500


def test_a_template_with_buttons_is_refused_not_flattened():
    definition = {"name": "erstkontakt", "language": "de", "status": "APPROVED",
                  "parameter_format": "POSITIONAL",
                  "components": [{"type": "BODY", "text": "Guten Tag."},
                                 {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ja"}]}]}
    cl, fake = build()
    cl.begin_campaign_attempt("c1", LEAD, 1)
    with pytest.raises(BR.BridgeError, match="TASK-121") as err:
        cl.send_template(LEAD, definition=definition)
    assert campaign_verdict(err.value) == "failed" and fake.calls == []


def test_template_parameter_validation_is_the_meta_one_unchanged():
    """campaign.py depends on a missing variable raising TemplateParamsError before any POST -- and
    on that being a `failed`, not an `uncertain`."""
    definition = {"name": "erstkontakt", "language": "de", "status": "APPROVED",
                  "parameter_format": "POSITIONAL",
                  "components": [{"type": "BODY", "text": "Guten Tag {{1}}."}]}
    cl, fake = build()
    cl.begin_campaign_attempt("c1", LEAD, 1)
    with pytest.raises(M.TemplateParamsError) as err:
        cl.send_template(LEAD, definition=definition, params={})
    assert campaign_verdict(err.value) == "failed" and fake.calls == []


def test_send_template_without_a_definition_says_why_there_is_no_registry():
    cl, fake = build()
    cl.begin_campaign_attempt("c1", LEAD, 1)
    with pytest.raises(BR.BridgeError, match="TASK-124"):
        cl.send_template(LEAD, template_name="erstkontakt", language="de")
    assert fake.calls == []


def test_get_template_refuses_instead_of_inventing_an_approved_definition():
    cl, fake = build()
    with pytest.raises(BR.BridgeError, match="TASK-124"):
        cl.get_template("1234567890")
    assert fake.calls == []


# --- configuration ------------------------------------------------------------------------------

def _run(code, **env):
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, **env},
                          capture_output=True, text=True)
    return proc


def test_an_unconfigured_rail_fails_the_send_loudly_and_posts_nothing():
    cl = BR.Client(transport=FakeExecutor(), base_url="", token="")
    cl.begin_turn(LEAD, TURN, "reply")
    with pytest.raises(BR.BridgeError, match="WA_BRIDGE_URL") as err:
        cl.send_text(LEAD, "Guten Tag!")
    assert campaign_verdict(err.value) == "failed", "nothing was posted, so ownership goes back"


def test_health_reports_whether_the_bridge_could_send():
    code = "import json;from app.wa import config as C;print(json.dumps(C.readiness()))"
    ready = json.loads(_run(code, WA_BRIDGE_URL=BASE, WA_BRIDGE_TOKEN="tok").stdout)
    assert ready["bridge_ready"] is True and ready["checks"]["bridge_url"] is True
    half = json.loads(_run(code, WA_BRIDGE_URL=BASE, WA_BRIDGE_TOKEN="").stdout)
    assert half["bridge_ready"] is False and half["checks"]["bridge_token"] is False
    assert half["transport"] == "meta", "the Meta rail keeps reporting itself while it carries traffic"


def test_a_bridge_url_that_is_not_http_stops_the_process_at_import():
    proc = _run("import app.wa.config", WA_BRIDGE_URL="127.0.0.1:8793")
    assert proc.returncode != 0 and "WA_BRIDGE_URL='127.0.0.1:8793'" in proc.stderr


def test_selecting_the_bridge_rail_does_not_require_the_rail_to_be_configured_yet():
    """WA_TRANSPORT=bridge must stay importable on a host that has not been wired up -- the loud
    failure belongs at send time, and tests/test_wa_transport.py asserts that import stays green."""
    assert _run("import app.wa.config", WA_TRANSPORT="bridge", WA_BRIDGE_URL="",
                WA_BRIDGE_TOKEN="").returncode == 0
