"""TASK-100/101 offline: campaign context on the card, outbound Luna did not write reaching the model,
template button payloads and reply context, the decline acknowledgement and silence, a recorded no_send,
stages, follow-ups and the free-form window. The model is a fake luna_brain.Client, Meta a fake client;
synthetic phones only.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import asgi
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST
from app.wa.luna import catchup as CU
from app.wa.luna import followups as FU
from app.wa.luna import reporting as REP
from app.wa.luna import shadow_run as SR

PHONE_ID = "555000111"
LEAD = "+4915550000001"
LEAD_DIGITS = LEAD[1:]

TEMPLATE = {"name": "bayern_interesse_test", "language": "de", "parameter_format": "POSITIONAL",
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": "Neue Pflegestellen in Bayern"},
                {"type": "BODY", "text": "Guten Tag {{1}}, Sie hatten sich früher bei uns gemeldet. Wir haben neue "
                                         "Stellen für Pflegefachkräfte in Bayern. Ist das für Sie interessant?",
                 "example": {"body_text": [["Frau X"]]}},
                {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ja, interessiert"},
                                                {"type": "QUICK_REPLY", "text": "Nein, danke"}]}]}
PARAMS = {"body": ["Frau Test"], "buttons": {"0": {"payload": "bayern_yes"}, "1": {"payload": "bayern_no"}}}


class FakeMeta:
    def __init__(self):
        self.sent, self.n = [], 0

    def send_text(self, to_e164, body):
        self.n += 1
        self.sent.append(body)
        return f"wamid.out.{self.n}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(C, "QUIET_HOURS_START", 0)
    monkeypatch.setattr(C, "QUIET_HOURS_END", 0)
    return FakeMeta()


def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Danke! Haben Sie die deutsche Urkunde schon?"],
            "rationale": "", "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


class Model:
    """Stands in for the claude CLI: answers with the queued outputs in order, records every payload."""

    def __init__(self, monkeypatch, *outs):
        self.outs, self.payloads = list(outs), []
        real = LB.Client
        monkeypatch.setattr(LB, "Client", lambda: real(reply=self._reply))

    def _reply(self, system, user, session_id):
        self.payloads.append(json.loads(user))
        return self.outs.pop(0), session_id or "session-1"


def _message(wamid, kind="text", text="Ja", payload=None, context_id=None):
    m = {"id": wamid, "from": LEAD_DIGITS, "type": kind}
    if kind == "text":
        m["text"] = {"body": text}
    elif kind == "button":
        m["button"] = {"text": text, "payload": payload}
    elif kind == "interactive":
        m["interactive"] = {"type": "button_reply", "button_reply": {"id": payload, "title": text}}
    if context_id:
        m["context"] = {"from": "4915550009999", "id": context_id}
    return m


def _deliver(wa, *messages):
    body = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID}, "messages": list(messages)}}]}]}
    return WAPI.handle_payload(body, client=wa)["results"]


def _campaign(wamid="wamid.campaign.1"):
    with ST.db() as c:
        return ST.record_campaign_send(c, LEAD, wamid, M.render_template(TEMPLATE, PARAMS), "bayern-2026-09")


def _thread():
    with ST.db() as c:
        return ST.thread(c, LEAD)


def _rows():
    with ST.db() as c:
        return ST.messages_for(c, LEAD)


# --- parse_message (TASK-100) --------------------------------------------------------------------

def test_a_template_button_tap_keeps_its_payload_namespaced_and_its_label():
    parsed = WAPI.parse_message(_message("wamid.b", kind="button", text="Ja, interessiert", payload="bayern_yes",
                                         context_id="wamid.campaign.1"))
    assert parsed["kind"] == "button" and parsed["text"] == "Ja, interessiert"
    assert parsed["button_id"] == "tpl:bayern_yes"
    assert parsed["reply_to_wamid"] == "wamid.campaign.1"


@pytest.mark.parametrize("payload", ["consent:yes", "yes", LB.CONSENT_YES_ID, LB.CONSENT_NO_ID])
def test_a_template_payload_can_never_equal_a_consent_button_id(payload):
    parsed = WAPI.parse_message(_message("wamid.b", kind="button", text="Ja", payload=payload))
    assert parsed["button_id"] not in (LB.CONSENT_YES_ID, LB.CONSENT_NO_ID)


@pytest.mark.parametrize("kind", ["text", "button", "interactive", "image"])
def test_every_inbound_kind_keeps_the_replied_to_wamid(kind):
    m = _message("wamid.x", kind=kind, text="Ja", payload="p", context_id="wamid.ours.7")
    if kind == "image":
        m["image"] = {"id": "media-1", "mime_type": "image/jpeg"}
    assert WAPI.parse_message(m)["reply_to_wamid"] == "wamid.ours.7"
    assert WAPI.inbound_meta(WAPI.parse_message(m))["context"]["id"] == "wamid.ours.7"


@pytest.mark.parametrize("kind, body, text, reply_to", [
    ("reaction", {"message_id": "wamid.campaign.1", "emoji": "👍"}, "👍", "wamid.campaign.1"),
    ("reaction", {"message_id": "wamid.campaign.1"}, "[reaction removed]", "wamid.campaign.1"),
    ("sticker", {"id": "sticker-1", "mime_type": "image/webp", "animated": False}, "[sticker]", None),
    ("location", {"latitude": 48.14, "longitude": 11.58, "name": "Klinik (fiktiv)", "address": "Musterstr. 1, München"},
     "[location: Klinik (fiktiv), Musterstr. 1, München (48.14, 11.58)]", None),
    ("contacts", [{"name": {"formatted_name": "Erika Muster"}, "phones": [{"phone": "+49 155 5000 0099"}]}],
     "[contact card: Erika Muster +49 155 5000 0099]", None),
])
def test_reactions_stickers_locations_and_contact_cards_are_answered_from_a_summary(kind, body, text, reply_to):
    """Review 2026-09-14: these were kept raw only -- a 👍 on the template got no turn and counted as no reply."""
    parsed = WAPI.parse_message({"id": "wamid.k", "from": LEAD_DIGITS, "type": kind, kind: body})
    assert (parsed["kind"], parsed["text"], parsed["button_id"], parsed.get("reply_to_wamid")) == \
        (kind, text, None, reply_to)
    assert WAPI.inbound_meta(parsed)[kind] == body


def test_an_unsupported_message_is_answered_and_keeps_its_errors():
    errors = [{"code": 131051, "title": "Message type unknown"}]
    parsed = WAPI.parse_message({"id": "wamid.u", "from": LEAD_DIGITS, "type": "unsupported", "errors": errors})
    assert parsed["text"] == "[unsupported message]" and WAPI.inbound_meta(parsed)["errors"] == errors


def test_a_reaction_on_the_campaign_reaches_the_model_as_a_reply_to_the_template(wa, monkeypatch):
    _campaign()
    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    [r] = _deliver(wa, {"id": "wamid.in.react", "from": LEAD_DIGITS, "type": "reaction",
                        "reaction": {"message_id": "wamid.campaign.1", "emoji": "👍"}})
    assert r["status"] == "sent" and wa.sent == ["Danke! Haben Sie die deutsche Urkunde schon?"]
    [p] = model.payloads
    assert p["latest_inbound"] == "👍" and p["reply_context"]["kind"] == "reaction"
    assert p["reply_context"]["replies_to"]["action"] == "campaign" and p["reply_context"]["replies_to"]["found"]
    assert [(m["direction"], m["kind"]) for m in _rows()] == [("out", "template"), ("in", "reaction"), ("out", "text")]
    with ST.db() as c:
        assert ST.has_inbound(c, LEAD) and REP.ball_for(c, LEAD) == "them"


def test_a_location_naming_another_land_is_no_region_answer(wa, monkeypatch):
    model = Model(monkeypatch, _out())
    [r] = _deliver(wa, {"id": "wamid.in.loc", "from": LEAD_DIGITS, "type": "location",
                        "location": {"latitude": 52.52, "longitude": 13.4, "address": "Alexanderplatz, Berlin"}})
    assert r["action"] == "reply_now_conversational" and len(model.payloads) == 1
    assert "region" not in _thread()["slots"]


# --- campaign seed (TASK-100 contract for TASK-103) ------------------------------------------------

def test_record_campaign_send_seeds_the_card_contract_and_the_outbound_row(wa):
    campaign = _campaign()
    t = _thread()
    assert t["slots"]["campaign"] == campaign
    assert set(campaign) == {"campaign_id", "template_name", "language", "rendered_text", "buttons", "sent_at",
                             "wamid"}
    assert campaign["template_name"] == "bayern_interesse_test" and campaign["language"] == "de"
    assert "Guten Tag Frau Test" in campaign["rendered_text"] and "[Nein, danke]" in campaign["rendered_text"]
    assert [b["payload"] for b in campaign["buttons"]] == ["bayern_yes", "bayern_no"]
    assert t["last_outbound_at"] == campaign["sent_at"] and t["last_inbound_at"] is None
    [row] = _rows()
    assert (row["direction"], row["kind"], row["wamid"], row["body"]) == \
        ("out", "template", "wamid.campaign.1", campaign["rendered_text"])
    assert row["meta"]["action"] == "campaign" and row["meta"]["campaign_id"] == "bayern-2026-09"


# --- the payload: campaign, template button, reply context (TASK-100) -----------------------------

def test_a_button_tap_on_the_campaign_reaches_the_model_with_the_template_and_reply_context(wa, monkeypatch):
    _campaign()
    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    [result] = _deliver(wa, _message("wamid.in.1", kind="button", text="Ja, interessiert", payload="bayern_yes",
                                     context_id="wamid.campaign.1"))
    assert result["status"] == "sent"
    [p] = model.payloads
    assert p["fresh_session"] is True
    assert p["card"]["campaign"]["template_name"] == "bayern_interesse_test"
    assert [(o["kind"], o["action"], o["wamid"]) for o in p["outbound_since_last_turn"]] == \
        [("template", "campaign", "wamid.campaign.1")]
    assert p["last_turn_at"] is None
    rc = p["reply_context"]
    assert rc["is_template_button"] is True and rc["template_button_payload"] == "bayern_yes"
    assert rc["replies_to"]["found"] is True and rc["replies_to"]["action"] == "campaign"
    assert p["is_button_reply"] is False, "a template tap is not a consent-style button reply"
    assert _thread()["slots"]["region"] == "Bayern"


def test_a_reply_to_a_message_we_do_not_hold_says_so(wa, monkeypatch):
    model = Model(monkeypatch, _out())
    _deliver(wa, _message("wamid.in.1", text="Wer sind Sie?", context_id="wamid.old.system.9"))
    rc = model.payloads[0]["reply_context"]
    assert rc["replies_to"] == {"wamid": "wamid.old.system.9", "found": False}
    assert rc["is_template_button"] is False and rc["template_button_payload"] is None


def test_luna_own_bubbles_are_not_repeated_but_a_later_nudge_and_template_are(wa, monkeypatch):
    model = Model(monkeypatch,
                  _out(bubbles=["Haben Sie die deutsche Urkunde schon?"], card_patch={"region": "Bayern"}),
                  _out(bubbles=["Schön! Haben Sie die deutsche Urkunde schon?"]))
    _deliver(wa, _message("wamid.in.1", text="Hallo, ich suche in Bayern"))
    marker = _thread()["slots"][LB.LAST_TURN_KEY]
    assert marker["own_message_ids"] and marker["at"]
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        ST.record_outbound(c, LEAD, "wamid.nudge.1", C.FOLLOWUP_NUDGE_DE, meta={"action": "followup"})
    _campaign("wamid.campaign.2")
    _deliver(wa, _message("wamid.in.2", text="Ja"))
    second = model.payloads[1]
    assert [o["action"] for o in second["outbound_since_last_turn"]] == ["followup", "campaign"]
    assert second["outbound_since_last_turn"][0]["text"] == C.FOLLOWUP_NUDGE_DE
    assert second["last_turn_at"] == marker["at"]
    assert "Haben Sie die deutsche Urkunde schon?" not in [o["text"] for o in second["outbound_since_last_turn"]]
    assert LB.LAST_TURN_KEY not in second["card"]


def test_a_session_from_before_the_marker_sees_only_what_came_after_its_last_turn_row(wa, monkeypatch):
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.in.0", "Hallo")
        ST.record_outbound(c, LEAD, "wamid.out.0", "Haben Sie die Urkunde?", meta={"action": "ask_qualification"})
        ST.record_outbound(c, LEAD, "wamid.nudge.0", C.FOLLOWUP_NUDGE_DE, meta={"action": "followup"})
        t = ST.thread(c, LEAD)
        t["slots"] = {"_session_id": "legacy-session", "region": "Bayern"}
        ST.save_thread(c, t)
    model = Model(monkeypatch, _out())
    _deliver(wa, _message("wamid.in.1", text="Ja"))
    assert [o["wamid"] for o in model.payloads[0]["outbound_since_last_turn"]] == ["wamid.nudge.0"]


def test_a_locked_text_sent_instead_of_the_model_bubbles_reaches_the_next_payload(wa, monkeypatch):
    model = Model(monkeypatch, _out(bubbles=["Schade."], card_patch={"qualification_ok": False,
                                                                     "qualification_path": "reject"}),
                  _out(no_send=True, bubbles=[]))
    _deliver(wa, _message("wamid.in.1", text="Ich bin Pflegehelferin"))
    _deliver(wa, _message("wamid.in.2", text="ok"))
    assert [o["text"] for o in model.payloads[1]["outbound_since_last_turn"]] == [LB.P.REJECT_BODY_DE]


def _meta_status(wamid, status, ts, **extra):
    with ST.db() as c:
        ST.record_message_status(c, LEAD, {"id": wamid, "status": status, "timestamp": ts, "recipient_id": LEAD_DIGITS,
                                           **extra})


def test_a_template_meta_reported_undelivered_stays_out_of_the_model_payload(wa, monkeypatch):
    """Review 2026-09-14: after a failed 131049 status the template still reached the model as seen, and card.campaign
    made Luna tell a candidate who never got it 'that is why we wrote'."""
    _campaign()
    _meta_status("wamid.campaign.1", "sent", "1789400000")
    _meta_status("wamid.campaign.1", "failed", "1789400005", errors=[{"code": 131049, "title": "Marketing limit"}])
    model = Model(monkeypatch, _out())
    _deliver(wa, _message("wamid.in.1", text="Hallo, ich suche Arbeit"))
    [p] = model.payloads
    assert p["outbound_since_last_turn"] == [] and "campaign" not in p["card"]
    assert _thread()["slots"]["campaign"]["wamid"] == "wamid.campaign.1", "the card keeps the record"


def test_a_delivered_template_carries_its_delivery_status(wa, monkeypatch):
    _campaign()
    _meta_status("wamid.campaign.1", "delivered", "1789400005")
    model = Model(monkeypatch, _out())
    _deliver(wa, _message("wamid.in.1", kind="button", text="Ja, interessiert", payload="bayern_yes",
                          context_id="wamid.campaign.1"))
    [p] = model.payloads
    assert [o["delivery"] for o in p["outbound_since_last_turn"]] == [{"status": "delivered", "error_codes": []}]
    assert p["reply_context"]["replies_to"]["delivery"] == {"status": "delivered", "error_codes": []}
    assert p["card"]["campaign"]["wamid"] == "wamid.campaign.1"


def test_introduced_follows_the_old_bot_greeting_check_not_the_session(wa, monkeypatch):
    """Review 2026-09-14: the decline turn started a session whose bubbles became the fixed ack, so fresh_session was
    false on the re-engagement and Valentina never introduced herself."""
    _campaign()
    model = Model(monkeypatch, _out(decline=True, bubbles=[]),
                  _out(re_engaged=True, bubbles=["Schön! Ich bin Valentina von der NDT Group. Haben Sie die Urkunde?"]),
                  _out(bubbles=["Super."]))
    _deliver(wa, _message("wamid.in.1", text="Nein, kein Interesse"))
    _deliver(wa, _message("wamid.in.2", text="Doch, ich habe jetzt Interesse"))
    _deliver(wa, _message("wamid.in.3", text="Ja"))
    assert [p["introduced"] for p in model.payloads] == [False, False, True]
    assert [p["fresh_session"] for p in model.payloads] == [True, False, False]


@pytest.mark.parametrize("kind, body, expected", [
    ("text", "Danke! Ich bin Valentina von der NDT Group.", True),
    ("buttons", "Ich bin Valentina — ein digitaler Assistent der NDT Group.", True),
    ("text", "Hallo Frau Test, schön von Ihnen zu hören.", True),
    ("text", "Alles klar, vielen Dank für die Rückmeldung.", False),
    ("template", "Hallo Frau Test. Ich bin Valentina von der NDT Group.", False),
])
def test_introduced_reads_free_form_outbound_like_the_old_bot(wa, kind, body, expected):
    with ST.db() as c:
        ST.record_outbound(c, LEAD, "wamid.out.x", body, kind=kind)
        assert LB.introduced(c, LEAD) is expected


def test_turn_context_raises_for_a_turn_key_that_is_not_a_stored_inbound(wa):
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        with pytest.raises(RuntimeError, match="not a stored inbound message"):
            LB.turn_context(c, t, "wamid.nowhere")


def test_shadow_run_hands_the_model_the_same_context(wa, monkeypatch):
    _campaign()
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.in.1", "Ja")
    seen = []
    client = LB.Client(reply=lambda s, u, sid: (seen.append(json.loads(u)) or _out(), sid))
    SR.shadow_turn(ST.db(), LEAD, client=client)
    assert [o["action"] for o in seen[0]["outbound_since_last_turn"]] == ["campaign"]


# --- decline (TASK-101) ----------------------------------------------------------------------------

def test_a_decline_sends_the_fixed_ack_once_marks_the_card_and_then_stays_silent(wa, monkeypatch):
    _campaign()
    model = Model(monkeypatch,
                  _out(decline=True, decline_reason="no interest", bubbles=["Schade, alles Gute!"]),
                  _out(bubbles=["Gern geschehen!"]),
                  _out(decline=True, bubbles=["Alles klar."]))
    [r1] = _deliver(wa, _message("wamid.in.1", kind="button", text="Nein, danke", payload="bayern_no"))
    assert r1["status"] == "sent" and r1["action"] == "decline_ack"
    assert wa.sent == [LB.P.DECLINE_ACK_DE]
    assert wa.sent[0] == "Alles klar, vielen Dank für die Rückmeldung. Falls sich das ändert, schreiben Sie mir gern."
    card = _thread()["slots"]
    assert card["declined"] is True and card["declined_reason"] == "no interest" and card["declined_at"]
    assert _rows()[-1]["meta"]["action"] == "decline_ack"

    [r2] = _deliver(wa, _message("wamid.in.2", text="ok danke"))
    [r3] = _deliver(wa, _message("wamid.in.3", text="Nein"))
    assert (r2["status"], r3["status"]) == ("nothing_to_send", "nothing_to_send")
    assert wa.sent == [LB.P.DECLINE_ACK_DE], "exactly one acknowledgement, silence after"
    assert [o["action"] for o in model.payloads[1]["outbound_since_last_turn"]] == ["decline_ack"]
    with ST.db() as c:
        assert REP.stage_for(ST.thread(c, LEAD)["slots"]) == "declined"
        assert REP.ball_for(c, LEAD) == "silent"


def test_a_clear_re_engagement_after_a_decline_resumes_the_funnel(wa, monkeypatch):
    Model(monkeypatch, _out(decline=True, bubbles=[]),
          _out(re_engaged=True, bubbles=["Schön! Haben Sie die deutsche Urkunde schon?"], card_patch={"region": "Bayern"}))
    _deliver(wa, _message("wamid.in.1", text="Nein danke, kein Interesse"))
    [r] = _deliver(wa, _message("wamid.in.2", text="Doch, ich habe jetzt Interesse"))
    assert r["status"] == "sent"
    assert wa.sent[-1] == "Schön! Haben Sie die deutsche Urkunde schon?"
    card = _thread()["slots"]
    assert card["declined"] is False and card["re_engaged_at"] and card["region"] == "Bayern"
    assert REP.stage_for(card) != "declined"


def test_the_model_cannot_write_code_owned_card_keys(wa):
    thread = {"slots": {}, "asked": []}
    client = LB.Client(reply=lambda s, u, sid: (_out(card_patch={"declined": True, "campaign": {"x": 1},
                                                               LB.LAST_TURN_KEY: {"seen_through_id": 99}}), sid))
    d = LB.turn("Hallo", thread, client=client)
    assert not {"declined", "campaign", LB.LAST_TURN_KEY} & set(d["slots"])


def test_a_consent_no_tap_is_never_a_decline(wa, monkeypatch):
    """Review 2026-09-14 (live 2/2): the consent button 'Nein danke' became the fixed decline ack and silence for a
    candidate ready to close. The model's decline flag is ignored for that tap; its reply goes out."""
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["slots"].update(region="Bayern", qualification_path="urkunde", qualification_ok=True, city="München",
                          housing_known=True, anonymous_send_offered=True, _session_id="session-1")
        ST.save_thread(c, t)
    reply = "Alles klar, ohne Ihre Zustimmung leite ich nichts weiter. Schreiben Sie mir gern, falls sich das ändert."
    Model(monkeypatch, _out(decline=True, decline_reason="declined consent", bubbles=[reply]))
    [r] = _deliver(wa, _message("wamid.in.1", kind="interactive", text="Nein danke", payload=LB.CONSENT_NO_ID))
    assert (r["status"], r["action"]) == ("sent", "reply_now_conversational")
    assert wa.sent == [reply]
    card = _thread()["slots"]
    assert card["anonymous_send_consent"] is False and not card.get("declined") and "declined_at" not in card
    assert REP.stage_for(card) != "declined"


def test_a_decline_naming_another_land_on_a_campaign_thread_is_a_decline_not_the_region_question(wa, monkeypatch):
    """Review 2026-09-14: the out-of-scope shortcut ran before the model on a campaign thread (no region yet), sent
    'käme Bayern für Sie infrage?' to a decliner and left the thread open for follow-up nudges."""
    _campaign()
    model = Model(monkeypatch, _out(decline=True, decline_reason="has a job in Hessen", bubbles=[]))
    [r] = _deliver(wa, _message("wamid.in.1", text="Nein danke, habe schon eine Stelle in Hessen"))
    assert r["action"] == "decline_ack" and wa.sent == [LB.P.DECLINE_ACK_DE] and len(model.payloads) == 1
    card = _thread()["slots"]
    assert card["declined"] is True and "region" not in card
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["last_outbound_at"] = _ago(300)
        ST.save_thread(c, t)
    assert FU.run(client=wa) == [] and wa.sent == [LB.P.DECLINE_ACK_DE]


def test_a_land_named_on_a_declined_thread_gets_no_reply(wa, monkeypatch):
    _campaign()
    model = Model(monkeypatch, _out(decline=True, bubbles=[]), _out(no_send=True, bubbles=[]))
    _deliver(wa, _message("wamid.in.1", kind="button", text="Nein, danke", payload="bayern_no"))
    [r] = _deliver(wa, _message("wamid.in.2", text="Ich wohne jetzt sowieso in Berlin, danke"))
    assert r["status"] == "nothing_to_send" and wa.sent == [LB.P.DECLINE_ACK_DE] and len(model.payloads) == 2
    assert "region" not in _thread()["slots"]


def test_a_campaign_yes_from_someone_living_elsewhere_reaches_the_model(wa, monkeypatch):
    _campaign()
    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    [r] = _deliver(wa, _message("wamid.in.1", text="Ja, wohne aber in NRW"))
    assert r["action"] == "reply_now_conversational" and len(model.payloads) == 1
    assert wa.sent == ["Danke! Haben Sie die deutsche Urkunde schon?"]
    assert _thread()["slots"]["region"] == "Bayern"


def test_a_land_typed_on_an_ordinary_thread_still_gets_the_locked_text(wa, monkeypatch):
    model = Model(monkeypatch)
    [r] = _deliver(wa, _message("wamid.in.1", text="Ich suche eine Stelle in Hessen"))
    assert r["action"] == "out_of_scope_region" and wa.sent == [LB.P.OUT_OF_SCOPE_REGION_DE] and model.payloads == []


class MediaMeta(FakeMeta):
    """FakeMeta plus Meta's two-step media download, every media id served as ``mime_type``."""

    def __init__(self, mime_type="video/mp4"):
        super().__init__()
        self.mime_type = mime_type

    def media_url(self, media_id):
        return {"url": f"https://media.example/{media_id}", "mime_type": self.mime_type}

    def download_media(self, url):
        return b"synthetic media " + url.encode()


def _media(wamid, kind="video", mime_type="video/mp4"):
    return {"id": wamid, "from": LEAD_DIGITS, "type": kind, kind: {"id": f"media-{wamid}", "mime_type": mime_type}}


def test_a_video_on_a_declined_thread_gets_no_reply_and_is_flagged_for_a_human(wa, monkeypatch, tmp_path):
    """Review 2026-09-14: after the decline ack a voice note got MEDIA_REPLY ('Ein Kollege schaut sie sich an.'),
    breaking the silence, and nothing flagged it. Since TASK-107 a voice note is transcribed (next test); a video still
    goes this way."""
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    _campaign()
    model = Model(monkeypatch, _out(decline=True, bubbles=[]))
    meta = MediaMeta()
    _deliver(meta, _message("wamid.in.1", text="Nein, kein Interesse"))
    [r] = _deliver(meta, _media("wamid.in.video"))
    assert (r["status"], r["action"]) == ("nothing_to_send", "declined_no_send")
    assert meta.sent == [LB.P.DECLINE_ACK_DE] and len(model.payloads) == 1
    card = _thread()["slots"]
    [unread] = card["_unread_media"]
    assert (unread["wamid"], unread["kind"]) == ("wamid.in.video", "video") and unread["document_id"]
    assert card["_escalated"] is True and card["declined"] is True
    with ST.db() as c:
        assert ST.reply_turn_claim_state(c, LEAD, "wamid.in.video") == ST.NO_SEND_STATE
        assert REP.ball_for(c, LEAD) == "silent" and ST.pending_inbound(c, LEAD) == []


def test_a_voice_note_on_a_declined_thread_is_a_model_turn_on_its_transcript_silent_unless_it_re_engages(
        wa, monkeypatch, tmp_path):
    """TASK-107: the transcript is read like typed text on a declined card (DECLINE): silence, or a re-engagement."""
    from tests.test_wa_voice_notes import use_openai
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    openai = use_openai(monkeypatch, {"text": "Okay, danke."}, {"text": "Ich suche jetzt doch eine Stelle in Bayern."})
    _campaign()
    model = Model(monkeypatch, _out(decline=True, bubbles=[]), _out(bubbles=[], no_send=True),
                  _out(re_engaged=True, bubbles=["Schön, dass Sie sich melden! Haben Sie die deutsche Urkunde schon?"]))
    meta = MediaMeta("audio/ogg")
    _deliver(meta, _message("wamid.in.1", text="Nein, kein Interesse"))
    [silent] = _deliver(meta, _media("wamid.in.voice1", "audio", "audio/ogg"))
    [back] = _deliver(meta, _media("wamid.in.voice2", "audio", "audio/ogg"))
    assert (silent["status"], silent["action"]) == ("nothing_to_send", "declined_no_send")
    assert back["status"] == "sent" and len(openai.requests) == 2
    assert meta.sent == [LB.P.DECLINE_ACK_DE, "Schön, dass Sie sich melden! Haben Sie die deutsche Urkunde schon?"]
    assert [(p["latest_inbound"], p["voice_note"]) for p in model.payloads[1:]] == \
        [("Okay, danke.", True), ("Ich suche jetzt doch eine Stelle in Bayern.", True)]
    card = _thread()["slots"]
    assert card["declined"] is False and card["re_engaged_at"] and "_unread_media" not in card
    assert "_escalated" not in card
    with ST.db() as c:
        assert ST.reply_turn_claim_state(c, LEAD, "wamid.in.voice1") == ST.NO_SEND_STATE


def test_a_video_reply_to_the_campaign_is_flagged_for_a_human_and_never_nudged(wa, monkeypatch, tmp_path):
    """Review 2026-09-14 (then a voice note): MEDIA_REPLY promised a colleague, nobody was flagged, and 20 minutes later
    the follow-up timer asked 'sind Sie noch da?'."""
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    _campaign()
    meta = MediaMeta()
    [r] = _deliver(meta, _media("wamid.in.video"))
    assert r["action"] == "media_ack" and meta.sent == [WAPI.MEDIA_REPLY]
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["last_outbound_at"] = _ago(20)
        ST.save_thread(c, t)
    assert FU.run(client=meta) == [] and meta.sent == [WAPI.MEDIA_REPLY]
    with TestClient(asgi.app) as client:
        (row,) = client.get("/api/wa/threads").json()["rows"]
    assert [u["wamid"] for u in row["unread_media"]] == ["wamid.in.video"] and row["slots"]["_escalated"] is True

    Model(monkeypatch, _out())
    _deliver(meta, _message("wamid.in.2", text="Haben Sie mein Video bekommen?"))
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["last_outbound_at"] = _ago(20)
        ST.save_thread(c, t)
    assert [n["tier"] for n in FU.run(client=meta)] == [0], "a typed message after it is an ordinary reply again"


def test_a_voice_note_reply_to_the_campaign_is_answered_from_its_transcript(wa, monkeypatch, tmp_path):
    """TASK-107: a campaign reply spoken as a voice note no longer stalls on MEDIA_REPLY: Luna answers the transcript
    against the template, and the thread is an ordinary conversation after it (a follow-up may nudge)."""
    from tests.test_wa_voice_notes import use_openai
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    use_openai(monkeypatch, {"text": "Ja, ich habe Interesse an Bayern."})
    _campaign()
    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    meta = MediaMeta("audio/ogg")
    [r] = _deliver(meta, _media("wamid.in.voice", "audio", "audio/ogg"))
    assert r["status"] == "sent" and meta.sent == ["Danke! Haben Sie die deutsche Urkunde schon?"]
    (payload,) = model.payloads
    assert (payload["latest_inbound"], payload["voice_note"]) == ("Ja, ich habe Interesse an Bayern.", True)
    assert [o["action"] for o in payload["outbound_since_last_turn"]] == ["campaign"]
    card = _thread()["slots"]
    assert card["region"] == "Bayern" and "_unread_media" not in card and "_escalated" not in card
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["last_outbound_at"] = _ago(20)
        ST.save_thread(c, t)
    assert [n["tier"] for n in FU.run(client=meta)] == [0]


def test_stop_on_a_declined_thread_stops_without_any_ack(wa, monkeypatch):
    Model(monkeypatch, _out(decline=True, bubbles=[]))
    _deliver(wa, _message("wamid.in.1", text="kein Interesse"))
    [r] = _deliver(wa, _message("wamid.in.2", text="Stopp"))
    assert r["status"] == "stopped" and wa.sent == [LB.P.DECLINE_ACK_DE]


# --- recorded no_send (TASK-101) -------------------------------------------------------------------

def test_a_no_send_is_never_re_run_by_catch_up(wa, monkeypatch):
    model = Model(monkeypatch, _out(no_send=True, bubbles=[]))
    [r] = _deliver(wa, _message("wamid.in.1", text="Danke"))
    assert r["status"] == "nothing_to_send"
    with ST.db() as c:
        assert ST.reply_turn_claim_state(c, LEAD, "wamid.in.1") == ST.NO_SEND_STATE
        assert REP.ball_for(c, LEAD) == "silent"
        assert SR.phones_owed_a_reply(c) == []
        assert ST.pending_inbound(c, LEAD) == []
    assert CU.run(client=wa) == []
    assert len(model.payloads) == 1, "catch-up must not call the model again"
    assert CU.run(client=wa, phones=[LEAD])[0]["status"] == "no_send_recorded"
    assert len(model.payloads) == 1


def test_a_new_message_after_a_recorded_no_send_is_owed_again(wa, monkeypatch):
    Model(monkeypatch, _out(no_send=True, bubbles=[]))
    _deliver(wa, _message("wamid.in.1", text="Danke"))
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.in.2", "Noch eine Frage")
        assert REP.ball_for(c, LEAD) == "us" and SR.phones_owed_a_reply(c) == [LEAD]


# --- stages, follow-ups, window (TASK-101) ---------------------------------------------------------

@pytest.mark.parametrize("card, stage", [
    ({"declined": True, "anonymous_send_consent": True}, "declined"),
    ({"already_placed": True}, "already_placed"),
    ({"already_placed": True, "open_to_new_position": False}, "already_placed"),
    ({"already_placed": True, "open_to_new_position": True, "qualification_path": "urkunde"}, "qualifying"),
    ({"declined": False, "re_engaged_at": "2026-09-14T10:00:00+00:00"}, "new_lead"),
])
def test_declined_and_already_placed_stages(card, stage):
    assert REP.stage_for(card) == stage


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


@pytest.mark.parametrize("slots", [{"declined": True}, {"already_placed": True}], ids=["declined", "already_placed"])
def test_follow_ups_skip_declined_and_already_placed_threads(wa, slots):
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.in.1", "Nein danke")
        ST.record_outbound(c, LEAD, "wamid.out.1", LB.P.DECLINE_ACK_DE, meta={"action": "decline_ack"})
        t = ST.thread(c, LEAD)
        t.update(slots=slots, last_inbound_at=_ago(300), last_outbound_at=_ago(299))
        ST.save_thread(c, t)
    assert FU.run(client=wa) == [] and wa.sent == []
    assert set(FU.TERMINAL_STAGES) >= {"declined", "already_placed"}


def test_follow_ups_never_nudge_a_campaign_recipient_who_never_replied(wa):
    with ST.db() as c:
        ST.record_campaign_send(c, LEAD, "wamid.campaign.1", M.render_template(TEMPLATE, PARAMS), "bayern-2026-09",
                                sent_at=_ago(600))
    assert FU.run(client=wa) == [] and wa.sent == []


def test_the_free_form_window_is_closed_for_a_thread_that_never_wrote(wa):
    _campaign()
    assert WAPI._freeform_window_open(_thread()) is False
    with pytest.raises(RuntimeError, match="no reopen template is configured"):
        with ST.db() as c:
            WAPI._send(c, _thread(), ["Hallo?"], [], client=wa)
    assert wa.sent == []


# --- prompt (TASK-100/101) --------------------------------------------------------------------------

def _rule(prefix):
    return next(r for r in LB.P.RULES if r.startswith(prefix))


def test_the_prompt_carries_the_old_bot_identity_and_no_invented_brand():
    system = LB.P.system_prompt(LB._CONSTITUTION_TEXT, LB._QUALIFICATION_TEXT)
    for brand in ("pflege-job-radar", "pflege-board", "pflege_board", "job-radar"):
        assert brand not in system.lower()
        assert all(brand not in name for name in LB.MCP_TOOL_NAMES)
    identity = _rule("IDENTITY")
    assert "Ich bin Valentina von der NDT Group." in identity and "ein digitaler Assistent der NDT Group" in identity
    assert "Stopp" in identity and "human colleague" in identity and "contacted NDT Group on this WhatsApp number" in identity
    # Repair 2026-09-14: the old bot says 'ein digitaler Assistent der NDT Group', never 'Assistentin'.
    assert "Du bist Valentina, ein digitaler Assistent der NDT Group" in LB.P.GOAL
    assert "digitale assistentin" not in system.lower() and "never 'Assistentin'" in identity


def test_the_prompt_campaign_outbound_template_button_decline_and_already_placed_rules():
    think1 = LB.P.THINK_ORDER[0]
    assert "EXCEPTION: a thread opened by our template (card.campaign) is never first contact" in think1
    assert "market_snapshot.open_jobs stays unsaid in the reply to the template" in think1
    assert "outbound_since_last_turn holds a message sent after your last turn" in LB.P.THINK_ORDER[3]
    campaign = _rule("CAMPAIGN (TASK-100)")
    assert "set region=Bayern" in campaign and "never ask" in campaign and "no open-jobs count" in campaign
    outbound = _rule("OUR OUTBOUND (TASK-100)")
    assert "means the candidate is still there" in outbound and "never a card fact" in outbound
    assert "never a word about yourself being there ('ich bin (noch) da/hier'" in outbound
    assert "is_template_button=true" in _rule("TEMPLATE BUTTON (TASK-100)")
    decline = _rule("DECLINE (TASK-101)")
    assert "A Nein to one of your gate questions" in decline and "re_engaged=true" in decline
    assert "consent button 'Nein danke'" in decline and "is not a decline either" in decline
    assert "After 'Nein danke'" in _rule("CONSENT IS A BUTTON TAP")
    assert "gets no locked out-of-scope text from the harness on this thread" in campaign
    assert "When introduced is false" in campaign and "fresh_session" not in campaign
    placed = _rule("ALREADY PLACED (TASK-100)")
    assert "ONE plain yes/no" in placed and "never consent" in placed
    assert "look at the positions open in Bayern now" in placed and "Never a later or conditional frame" in placed
    for key in ("decline", "re_engaged", "already_placed", "open_to_new_position"):
        assert key in LB.P.OUTPUT_INSTRUCTION


def test_the_deterministic_brain_reads_a_template_tap_as_its_label(wa, monkeypatch):
    from app.wa import brain as B
    monkeypatch.setattr(C, "BRAIN", "deterministic")
    seen = []
    real = B.turn
    monkeypatch.setattr(B, "turn", lambda text, thread, button_id=None: seen.append((text, button_id)) or
                        real(text, thread, button_id=button_id))
    _deliver(wa, _message("wamid.in.1", kind="button", text="Ja, interessiert", payload="bayern_yes"))
    assert seen == [("Ja, interessiert", None)]
