"""TASK-100/101 through the real claude CLI: replies to our campaign template, identity, declines, already
placed, and a "Ja" after a template or a follow-up nudge on a stalled session; TASK-105: a decline imported from the
old system; TASK-107: a campaign reply spoken as a voice note (fake transcription transport). Marked ``llm`` (real
model, real cost, several seconds per turn); every test runs twice (``run``).

Each message goes the production way: a signed-shape webhook payload into ``api.handle_payload`` with
WA_BRAIN=luna, WA_AUTOSEND=1 and a fake Meta client (nothing leaves the process), a tmp_path SQLite, and the
persona board served to the MCP tools. Campaign sends use ``store.record_campaign_send``, nudges the same
``api.send_and_record(action="followup")`` call followups.py makes, documents a stored wa_documents row
already on the card (what ``api._ingest_media`` leaves) plus the media message itself. Fictional persona
and phone number only.

Run: ``pytest -q -m llm tests/test_wa_luna_campaign_personas.py -s``.
"""
import json
import re
import shutil

import pytest

from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST
from app.wa.luna import reporting as REP
from tests.test_wa_luna_personas import board  # noqa: F401  (fixture: persona board, tools included)

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not shutil.which(C.LUNA_CLAUDE_BIN),
                       reason=f"{C.LUNA_CLAUDE_BIN!r} is not on PATH -- WA_BRAIN=luna needs it installed"),
]

PHONE_ID = "555000222"
LEAD = "+4915550000042"
RUNS = pytest.mark.parametrize("run", [1, 2])

# The approved campaign template (recruitment_bayern_stellen_interesse_de, components as the read-only Graph lookup
# returned them 2026-09-14) with the values the sender sends: body only, no button payload -- so a quick-reply tap
# arrives with its label as payload (repair 2026-09-14: the tests used a synthetic body and synthetic payloads).
YES_LABEL, NO_LABEL = "Ja, ich habe Interesse", "Nein, kein Interesse"
TEMPLATE = {"name": "recruitment_bayern_stellen_interesse_de", "language": "de", "parameter_format": "POSITIONAL",
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": "Neue Stellen in Bayern für Pflegekräfte"},
                {"type": "BODY", "text": "Hallo, {{1}}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben "
                                         "wir viele neue Stellen in Bayern. Haben Sie noch Interesse?",
                 "example": {"body_text": [["Frau Müller"]]}},
                {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": YES_LABEL},
                                                {"type": "QUICK_REPLY", "text": NO_LABEL}]}]}
PARAMS = {"body": ["Frau Kowalska"]}

BRANDS_RE = re.compile(r"pflege[-_ ]?(job[-_ ]?radar|board)|job[-_ ]?radar", re.I)
NEW_LEAD_OPENER_RE = re.compile(r"danke(schön)? für ihre (nachricht|anfrage|bewerbung)|schön, dass sie sich (melden|gemeldet)|"
                                r"willkommen|\b\d+\s+(offene\s+)?(pflege)?stellen\b", re.I)
# A region yes/no ("Suchen Sie eine Stelle in Bayern?"), not an open city question ("Welche Stadt in Bayern?").
BAYERN_QUESTION_RE = re.compile(r"\b(?:suchen sie|sind sie|interessier\w*|möchten sie|wollen sie|kommt|käme|wäre)\b"
                                r"[^.!?]*\bbayern\b[^.!?]*\?", re.I)
QUALIFICATION_RE = re.compile(r"urkunde|anerkenn", re.I)
# Review 2026-09-14 (R5): the first model reply on a campaign thread names Valentina and NDT Group once.
INTRO_RE = re.compile(r"valentina[^.!?]*ndt|ndt[^.!?]*valentina", re.I)
# Repair 2026-09-14: live, a nudge 'sind Sie noch da?' answered 'Ja' got 'Alles gut, ich bin noch da'.
SELF_THERE_RE = re.compile(r"\bich bin (?:\w+ ){0,2}(?:da|hier)\b|\bbin (?:\w+ ){0,2}für sie da\b", re.I)
# Repair 2026-09-14: live, an already placed candidate was offered 'dass ich Ihnen ab und zu passende Stellen zeige';
# nothing here writes to such a thread later.
PROMISE_LATER_RE = re.compile(
    r"ab und zu|von zeit zu zeit|regelmäßig|auf dem laufenden|benachrichtig|"
    r"\b(?:ich|wir)\b[^.!?]*\b(?:melde|melden|schicke|schicken|sende|senden|informiere|informieren|zeige|zeigen)\b"
    r"[^.!?]*\b(?:später|künftig|zukünftig|wieder|sobald)\b|"
    r"\b(?:sobald|falls|wenn)\b[^.!?]*\b(?:ergibt|ergeben|auftut|frei wird|etwas neues|etwas passendes)\b", re.I)


class FakeMeta:
    def __init__(self):
        self.sent, self.n = [], 0

    def send_text(self, to_e164, body):
        self.n += 1
        self.sent.append(body)
        return f"wamid.out.{self.n}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)

    def media_url(self, media_id):
        return {"url": f"https://media.example/{media_id}", "mime_type": "audio/ogg"}

    def download_media(self, url):
        return b"OggS synthetic voice note " + url.encode()


class Chat:
    """One candidate's WhatsApp thread, driven through the webhook pipeline. ``transcript`` keeps who said what."""

    def __init__(self):
        self.meta, self.n, self.transcript, self.campaign_wamid = FakeMeta(), 0, [], None

    def _deliver(self, message, shown):
        self.n += 1
        message = {"id": f"wamid.in.{self.n}", "from": LEAD[1:], **message}
        before = len(self.meta.sent)
        body = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages",
                "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
                          "messages": [message]}}]}]}
        result = WAPI.handle_payload(body, client=self.meta)["results"][0]
        bubbles = self.meta.sent[before:]
        self.transcript += [("candidate", shown), ("luna", bubbles)]
        return bubbles, result

    def campaign(self):
        self.campaign_wamid = f"wamid.campaign.{self.n}"
        with ST.db() as c:
            campaign = ST.record_campaign_send(c, LEAD, self.campaign_wamid, M.render_template(TEMPLATE, PARAMS),
                                               "bayern-test")
        self.transcript.append(("template", campaign["rendered_text"]))

    def say(self, text):
        return self._deliver({"type": "text", "text": {"body": text}}, text)

    def tap(self, label):
        """A quick-reply tap on the template; without a payload in the send, Meta's webhook carries the label."""
        return self._deliver({"type": "button", "button": {"text": label, "payload": label},
                              "context": {"from": PHONE_ID, "id": self.campaign_wamid}}, f"[tap: {label}]")

    def react(self, emoji):
        return self._deliver({"type": "reaction", "reaction": {"message_id": self.campaign_wamid, "emoji": emoji}},
                             f"[reaction {emoji} on the template]")

    def voice(self, transcript, monkeypatch):
        """A voice note (TASK-107): the real webhook path downloads and transcribes it; the transcription transport
        answers ``transcript`` (tests/test_wa_voice_notes.py:use_openai)."""
        from tests.test_wa_voice_notes import use_openai
        use_openai(monkeypatch, {"text": transcript})
        return self._deliver({"type": "audio", "audio": {"id": f"media-voice-{self.n + 1}",
                                                         "mime_type": "audio/ogg; codecs=opus"}},
                             f"[voice note: {transcript}]")

    def consent_tap(self, button_id, label):
        return self._deliver({"type": "interactive", "interactive": {"type": "button_reply",
                                                                     "button_reply": {"id": button_id, "title": label}}},
                             f"[tap: {label}]")

    def set_facts(self, **facts):
        with ST.db() as c:
            t = ST.thread(c, LEAD)
            t["slots"].update(facts)
            ST.save_thread(c, t)

    def nudge(self):
        with ST.db() as c:
            t = ST.thread(c, LEAD)
            WAPI.send_and_record(c, t, [C.FOLLOWUP_NUDGE_DE], [], client=self.meta, action="followup")
            ST.record_followup_sent(c, LEAD, 0)
            ST.save_thread(c, t)
        self.transcript.append(("nudge", C.FOLLOWUP_NUDGE_DE))

    def upload(self, document_type, certificate_level, text):
        """A classified document already on the card (api._ingest_media's result), then its media message."""
        wamid = f"wamid.in.{self.n + 1}"
        with ST.db() as c:
            doc_id = ST.record_document(c, LEAD, wamid, f"media-{wamid}", "document", "application/pdf",
                                        f"{document_type}.pdf", f"/nonexistent/{wamid}.pdf", "0" * 64, 1)
            ST.set_document_text(c, doc_id, text)
            t = ST.thread(c, LEAD)
            summary = {"id": doc_id, "document_type": document_type, "certificate_level": certificate_level}
            key = WAPI._CARD_TEXT_KEY.get(document_type)
            if key:
                t["slots"][key] = "\n\n".join(filter(None, (t["slots"].get(key), text)))
            t["slots"].update(document_type=document_type, certificate_level=certificate_level)
            t["slots"]["documents"] = [*t["slots"].get("documents", []), summary]
            t["slots"]["_documents_just_received"] = [*t["slots"].get("_documents_just_received", []), summary]
            ST.save_thread(c, t)
        return self._deliver({"type": "document", "document": {"id": f"media-{wamid}", "mime_type": "application/pdf",
                                                             "filename": f"{document_type}.pdf"}},
                             f"[{document_type}.pdf]")

    def card(self):
        with ST.db() as c:
            return ST.thread(c, LEAD)["slots"]

    def dump(self):
        card = {k: v for k, v in self.card().items() if not k.endswith("_text") and k != "campaign"}
        return json.dumps({"transcript": self.transcript, "card": card}, ensure_ascii=False, indent=1)


@pytest.fixture()
def chat(board, monkeypatch):  # noqa: F811
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    chat = Chat()
    yield chat
    print(chat.dump())


def _text(bubbles):
    return " ".join(bubbles)


def _assert_campaign_yes_answer(chat, bubbles):
    said, dump = _text(bubbles), chat.dump()
    assert bubbles, dump
    assert chat.card().get("region") == "Bayern", dump
    assert not NEW_LEAD_OPENER_RE.search(said), f"new-lead opener after our template: {dump}"
    assert not BAYERN_QUESTION_RE.search(said), f"Bayern asked again after a yes to the Bayern template: {dump}"
    assert QUALIFICATION_RE.search(said), f"the next open gate (Urkunde) was not asked: {dump}"
    assert chat.card().get("qualification_path") in (None, "unknown"), dump
    assert not BRANDS_RE.search(said), dump
    assert INTRO_RE.search(said), f"no self-introduction in the first reply after the template: {dump}"


@RUNS
def test_campaign_typed_ja_continues_with_the_urkunde_question(chat, run):
    chat.campaign()
    bubbles, _ = chat.say("Ja")
    _assert_campaign_yes_answer(chat, bubbles)


@RUNS
def test_campaign_yes_button_continues_with_the_urkunde_question(chat, run):
    chat.campaign()
    bubbles, _ = chat.tap(YES_LABEL)
    _assert_campaign_yes_answer(chat, bubbles)


@RUNS
def test_who_are_you_and_where_is_my_number_from(chat, run):
    chat.campaign()
    bubbles, _ = chat.say("Wer sind Sie? Woher haben Sie meine Nummer?")
    said, dump = _text(bubbles).lower(), chat.dump()
    assert "valentina" in said and "ndt" in said, dump
    assert re.search(r"gemeldet|kontakt|geschrieben|beworben", said), f"not honest about the earlier contact: {dump}"
    assert "stopp" in said, f"Stopp not mentioned: {dump}"
    assert re.search(r"mensch|kolleg|mitarbeiter|ansprechpart|manager", said), f"no human handoff offered: {dump}"
    assert not BRANDS_RE.search(said), dump
    assert "assistentin" not in said, f"not the old bot's 'ein digitaler Assistent': {dump}"
    assert not chat.card().get("declined"), dump


@RUNS
def test_decline_by_the_no_button_gets_the_fixed_ack_once(chat, run):
    chat.campaign()
    bubbles, result = chat.tap(NO_LABEL)
    dump = chat.dump()
    assert bubbles == [LB.P.DECLINE_ACK_DE] and result["action"] == "decline_ack", dump
    assert chat.card().get("declined") is True, dump
    assert chat.card().get("region") is None, f"a no to the Bayern template recorded region: {dump}"


@RUNS
def test_decline_by_text_then_ok_danke_stays_silent(chat, run):
    chat.campaign()
    first, _ = chat.say("Nein danke, habe schon eine Stelle")
    second, result = chat.say("ok danke")
    dump = chat.dump()
    assert first == [LB.P.DECLINE_ACK_DE], dump
    assert second == [] and result["status"] == "nothing_to_send", f"a reply after the decline ack: {dump}"
    card = chat.card()
    assert card.get("declined") is True and REP.stage_for(card) == "declined", dump
    assert card.get("region") is None, f"a decline recorded region from the template: {dump}"
    with ST.db() as c:
        assert REP.ball_for(c, LEAD) == "silent", dump


@RUNS
def test_re_engagement_after_a_decline_resumes_the_funnel(chat, run):
    chat.campaign()
    first, _ = chat.say("Nein, kein Interesse")
    second, _ = chat.say("Moment, ich habe es mir überlegt: doch, ich hätte Interesse an einer Stelle in Bayern.")
    dump = chat.dump()
    assert first == [LB.P.DECLINE_ACK_DE], dump
    assert second and second != [LB.P.DECLINE_ACK_DE], f"no reply to a clear re-engagement: {dump}"
    card = chat.card()
    assert card.get("declined") is False and card.get("re_engaged_at"), dump
    assert QUALIFICATION_RE.search(_text(second)), f"the funnel did not resume at the Urkunde question: {dump}"
    assert INTRO_RE.search(_text(second)), f"no self-introduction after the decline ack: {dump}"


def _import_old_opt_out(tmp_path):
    """TASK-105: the old system closed this candidate as declined_opt_out months ago; the history import (example
    queries, synthetic old-system database) declines the card. No campaign: the sender skips such a phone."""
    import sqlite3
    from app.wa.luna import import_history as IH
    from tests.test_wa_luna_import_history import OLD_SCHEMA, QUERIES

    db = tmp_path / "old" / "sales_brain.sqlite"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.executescript(OLD_SCHEMA)
    meta = {"phone": LEAD, "lifecycle": {"status": "closed", "reason": "declined_opt_out",
                                         "closed_at": "2026-06-02T10:00:00+00:00", "source": "sync_portfolio_lifecycle"}}
    c.execute("insert into candidates (id, workspace_id, metadata_json) values (1, 1, ?)", (json.dumps(meta),))
    c.execute("insert into candidate_whatsapp_messages (workspace_id, candidate_id, phone_e164, wamid, direction, "
              "message_type, body, occurred_at) values (1, 1, ?, 'wamid.old.1', 'inbound', 'text', "
              "'Nein danke, kein Interesse mehr', '2026-06-02T09:58:00+00:00')", (LEAD,))
    c.commit()
    c.close()
    with IH.Source.open(db, QUERIES, "old-system-persona") as source:
        return IH.import_phone(source, LEAD, apply=True)


@RUNS
def test_an_imported_old_system_opt_out_stays_silent_until_the_candidate_re_engages(chat, run, tmp_path):
    report = _import_old_opt_out(tmp_path)
    assert report["decline_import"]["marked"] is True and chat.card().get("declined") is True, report
    first, result = chat.say("Ok, danke.")
    second, _ = chat.say("Hallo, ich habe es mir anders überlegt: ich suche jetzt doch eine Stelle als "
                         "Pflegefachfrau in Bayern.")
    dump = chat.dump()
    assert first == [] and result["status"] == "nothing_to_send", f"a reply after an imported opt-out: {dump}"
    assert second and second != [LB.P.DECLINE_ACK_DE], f"no reply to a clear re-engagement: {dump}"
    card = chat.card()
    assert card.get("declined") is False and card.get("re_engaged_at"), dump
    assert not BRANDS_RE.search(_text(second)), dump


@RUNS
def test_already_placed_is_congratulated_and_asked_one_yes_no(chat, run):
    chat.campaign()
    bubbles, _ = chat.say("Ich habe inzwischen eine neue Stelle gefunden.")
    said, dump = _text(bubbles), chat.dump()
    card = chat.card()
    assert card.get("already_placed") is True and not card.get("declined"), dump
    assert re.search(r"glückwunsch|gratul|freut|schön", said, re.I), f"no congratulation: {dump}"
    questions = re.findall(r"[^.!?]*\?", said)
    # The question is the part after the last dash/colon: a lead-in clause may carry 'oder' (live: "Falls sich mal
    # etwas ändert oder ... – wären Sie grundsätzlich offen, ...?").
    asked = re.split(r"[–—:]", questions[0])[-1] if questions else ""
    assert len(questions) == 1 and not re.search(r"\boder\b", asked, re.I), f"not one plain yes/no: {dump}"
    assert not QUALIFICATION_RE.search(said), f"funnel question to an already placed candidate: {dump}"
    assert not PROMISE_LATER_RE.search(said), f"promised later job offers nothing here sends: {dump}"
    assert not card.get("anonymous_send_consent") and REP.stage_for(card) == "already_placed", dump
    assert INTRO_RE.search(said), f"no self-introduction in the first reply after the template: {dump}"


def _stall_on_the_urkunde_question(chat):
    bubbles, _ = chat.say("Hallo, ich bin Krankenschwester und suche eine Stelle in Bayern.")
    assert QUALIFICATION_RE.search(_text(bubbles)), f"setup: Luna did not ask the Urkunde question: {chat.dump()}"
    assert chat.card().get("qualification_path") in (None, "unknown"), chat.dump()


def _assert_ja_set_no_qualification(chat, bubbles):
    card, dump = chat.card(), chat.dump()
    assert card.get("qualification_path") in (None, "unknown"), f"a Ja to our message set the Urkunde: {dump}"
    assert card.get("qualification_ok") is not True and not card.get("urkunde_status"), dump
    assert bubbles and QUALIFICATION_RE.search(_text(bubbles)), f"the open Urkunde question was not re-asked: {dump}"


@RUNS
def test_existing_session_stalled_on_urkunde_then_template_then_ja(chat, run):
    _stall_on_the_urkunde_question(chat)
    chat.campaign()
    bubbles, _ = chat.say("Ja")
    _assert_ja_set_no_qualification(chat, bubbles)


@RUNS
def test_follow_up_nudge_then_ja_is_not_an_urkunde_answer(chat, run):
    _stall_on_the_urkunde_question(chat)
    chat.nudge()
    bubbles, _ = chat.say("Ja")
    _assert_ja_set_no_qualification(chat, bubbles)
    assert not SELF_THERE_RE.search(_text(bubbles)), f"answered the nudge as if the candidate had asked: {chat.dump()}"


_BOARD_CLINICS = ("Klinikum München", "Klinikum Augsburg", "Klinikum Würzburg", "Klinikum Regensburg",
                  "Klinikum Bayreuth")


def _full_funnel_to_the_shortlist(chat, city_or_department_answer):
    """Answers whatever gate the scoreboard has open, uploads CV and Urkunde when documents are open, until a
    shortlist clinic is named. 12 candidate turns are the test's own budget, reported with the transcript."""
    chat.campaign()
    bubbles, _ = chat.say("Ja")
    _assert_campaign_yes_answer(chat, bubbles)
    answers = {"region": "Ja, Bayern.", "qualification": "Ja, ich habe die deutsche Urkunde, volle Anerkennung.",
               "city_or_department": city_or_department_answer, "housing": "Nur ich, eine Person."}
    for _ in range(12):
        board_state = LB.requirement_scoreboard(chat.card())
        if any(c in _text(bubbles) for c in _BOARD_CLINICS) and LB.market_snapshot(chat.card())["shortlist"]:
            break
        gate = next((g for g in ("region", "qualification", "city_or_department", "housing") if board_state[g] == "open"),
                    None)
        if gate:
            bubbles, _ = chat.say(answers[gate])
        elif board_state["cv_document"] == "open":
            bubbles, _ = chat.upload("lebenslauf", "unknown", "Lebenslauf Anna Kowalska, Pflegefachfrau, 2012-2026 "
                                                              "Innere Medizin, Klinikum (fiktiv)")
        elif board_state["qualification_document"] == "open":
            bubbles, _ = chat.upload("urkunde", "fachkraft", "Urkunde über die Erlaubnis zum Führen der "
                                                            "Berufsbezeichnung Pflegefachfrau (fiktiv)")
        else:
            bubbles, _ = chat.say("Ok")
    dump = chat.dump()
    assert LB.market_snapshot(chat.card())["shortlist"], f"no shortlist within the budget: {dump}"
    assert any(c in _text(bubbles) for c in _BOARD_CLINICS), f"the shortlist was not named: {dump}"
    luna = " ".join(b for who, said in chat.transcript if who == "luna" for b in said)
    assert len(BAYERN_QUESTION_RE.findall(luna)) == 0, f"Bayern asked after the campaign yes: {dump}"
    return dump


@RUNS
def test_full_funnel_after_campaign_ja_reaches_the_shortlist(chat, run):
    dump = _full_funnel_to_the_shortlist(chat, "Am liebsten München.")
    # TASK-104: the candidate never names a department (live: department_pref "flexibel" emptied the shortlist; the
    # CV's "Innere Medizin" or a tool result's department must not become one either).
    assert chat.card().get("department_pref") is None, f"department_pref the candidate never named: {dump}"


@RUNS
def test_full_funnel_flexible_on_city_and_department_reaches_the_shortlist(chat, run):
    """TASK-104: a flexible answer settles the city/department gate and filters nothing."""
    dump = _full_funnel_to_the_shortlist(chat, "Das ist mir egal, ich bin da ganz flexibel.")
    department_filter = LB.market_snapshot(chat.card())["department_filter"]
    assert department_filter and department_filter["status"] == "flexible", dump


@RUNS
def test_full_funnel_unknown_department_reaches_the_shortlist(chat, run):
    """TASK-104: a department the board has no department for (Urologie) filters nothing; the snapshot says so."""
    dump = _full_funnel_to_the_shortlist(chat, "Am liebsten in München, auf der Urologie.")
    department_filter = LB.market_snapshot(chat.card())["department_filter"]
    assert department_filter and department_filter["status"] == "unmatched", dump
    # Honest: told at least once that the search cannot be narrowed to Urologie, and no sentence presents a Urologie
    # match (live 3/3 told it at the city answer; 1 of 3 repeated it at the shortlist).
    luna = " ".join(b for who, said in chat.transcript if who == "luna" for b in said)
    urologie = [s for s in re.split(r"[.!?]", luna) if re.search(r"urolog", s, re.I)]
    assert urologie, f"never said the search is not narrowed to Urologie: {dump}"
    assert all(re.search(r"\b(?:nicht|kein\w*)\b", s, re.I) for s in urologie), f"a Urologie match claimed: {dump}"


@RUNS
def test_campaign_thumbs_up_reaction_continues_with_the_urkunde_question(chat, run):
    """Review 2026-09-14 (F3/R4): a 👍 on the template was stored raw only; now it is a reply."""
    chat.campaign()
    bubbles, _ = chat.react("👍")
    _assert_campaign_yes_answer(chat, bubbles)


# TASK-107: never a claim that voice messages cannot be heard, never a request to type it instead.
CANNOT_LISTEN_RE = re.compile(r"(?:sprachnachricht|audio|voice)\w*[^.!?]*\b(?:nicht|kein\w*)\b[^.!?]*"
                              r"(?:anhören|abspielen|hören|öffnen|lesen)|(?:schreiben|tippen) sie[^.!?]*"
                              r"(?:als text|schriftlich|kurz auf)|schriftlich", re.I)


@RUNS
def test_campaign_reply_as_a_voice_note_is_answered_from_its_transcript(chat, run, monkeypatch, tmp_path):
    """TASK-107: a campaign yes spoken as a voice note used to get the flat MEDIA_REPLY and stall. The transcript
    carries three facts; Luna records them and asks the next open gate, never the Urkunde or the city again."""
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    chat.campaign()
    bubbles, result = chat.voice("Ja hallo, ich habe Interesse. Ich habe die deutsche Urkunde als Pflegefachfrau "
                                 "schon und würde gern in Augsburg arbeiten.", monkeypatch)
    said, card, dump = _text(bubbles), chat.card(), chat.dump()
    assert result["status"] == "sent" and bubbles and WAPI.MEDIA_REPLY not in bubbles, dump
    assert card.get("region") == "Bayern" and card.get("city") == "Augsburg", dump
    assert card.get("qualification_path") == "urkunde", dump
    assert "_unread_media" not in card and not card.get("_escalated"), dump
    assert not CANNOT_LISTEN_RE.search(said), f"claimed it cannot hear the voice note: {dump}"
    assert not re.search(r"urkunde[^.!?]*\?", said, re.I), f"the Urkunde asked again: {dump}"
    assert not re.search(r"(?:welche|in welcher) stadt|wo möchten sie", said, re.I), f"the city asked again: {dump}"
    assert INTRO_RE.search(said), f"no self-introduction in the first reply after the template: {dump}"
    assert not NEW_LEAD_OPENER_RE.search(said), f"new-lead opener after our template: {dump}"


@RUNS
def test_a_misheard_town_in_a_voice_note_is_asked_back_not_recorded(chat, run, monkeypatch, tmp_path):
    """TASK-107 prompt VOICE NOTE: a transcript can mishear a town ('Augsbuch'); the city filter is an exact match, so a
    misheard town recorded as city would empty the shortlist. Luna asks back instead of recording it."""
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    chat.campaign()
    chat.say("Ja")
    chat.set_facts(region="Bayern", qualification_path="urkunde", qualification_ok=True, urkunde_status="present")
    bubbles, _ = chat.voice("Also ich würde am liebsten in Augsbuch arbeiten.", monkeypatch)
    said, card, dump = _text(bubbles), chat.card(), chat.dump()
    assert card.get("city") in (None, "Augsburg"), f"a misheard town recorded as city: {dump}"
    if card.get("city") is None:
        assert re.search(r"augsburg[^.!?]*\?|\?[^?]*augsburg", said, re.I), f"not asked back about Augsburg: {dump}"
    assert not CANNOT_LISTEN_RE.search(said), f"claimed it cannot hear the voice note: {dump}"


@RUNS
def test_consent_no_tap_is_not_a_decline(chat, run):
    """Review 2026-09-14 (R1, live 2/2 before the fix): the consent button 'Nein danke' after the close became the
    decline ack and a declined card. The facts the funnel collects are seeded; both documents arrive as uploads;
    Luna runs the two close turns; then the candidate taps Nein danke."""
    chat.campaign()
    chat.say("Ja")
    chat.set_facts(region="Bayern", qualification_path="urkunde", qualification_ok=True, urkunde_status="yes",
                   city="München", housing_known=True, people_count=1)
    chat.upload("lebenslauf", "unknown", "Lebenslauf Anna Kowalska, Pflegefachfrau, 2012-2026 Innere Medizin (fiktiv)")
    bubbles, _ = chat.upload("urkunde", "fachkraft", "Urkunde über die Erlaubnis zum Führen der Berufsbezeichnung "
                                                    "Pflegefachfrau (fiktiv)")
    for _ in range(4):
        if chat.card().get("anonymous_send_offered"):
            break
        bubbles, _ = chat.say("Ok, klingt gut.")
    assert chat.card().get("anonymous_send_offered"), f"setup: no consent question within the budget: {chat.dump()}"
    bubbles, result = chat.consent_tap(LB.CONSENT_NO_ID, "Nein danke")
    card, dump = chat.card(), chat.dump()
    assert bubbles and bubbles != [LB.P.DECLINE_ACK_DE] and result["action"] != "decline_ack", dump
    assert card.get("anonymous_send_consent") is False and not card.get("declined"), dump
    assert REP.stage_for(card) != "declined", dump
