"""Inbound WhatsApp harness (docs/whatsapp.md): webhook trust boundary, the question ladder, the
board filters behind it, and the opt-out. No network: the registry snapshot is stubbed like every
other backend test (tests/test_app_api.py) and Meta is a fake client that records calls.
"""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import brain as B
from app.wa import config as C
from app.wa import meta as M
from app.wa import slots as SL
from app.wa import store as ST

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
PHONE_ID = "111222333"
LEAD = "+491701234567"

# Three towns, three departments, both working-time values, housing on a quarter of the rows: enough
# for the gain function to have something to choose between, small enough to assert exact counts.
CITIES = (("München", "Oberbayern"), ("Würzburg", "Unterfranken"), ("Augsburg", "Schwaben"))
DEPTS = ("Intensiv/IMC", "OP", "Notaufnahme")
ROLES = ("pflegefachkraft",) * 7 + ("fachpflege", "ota_ata", "leitung")


def _jobs():
    rows = []
    for i in range(60):
        city, bezirk = CITIES[i % 3]
        dept = DEPTS[(i // 3) % 3]
        rows.append({"posting_id": i + 1, "title": f"{dept} Stelle {i + 1}", "role_class": ROLES[i % 10],
                     "department_hint": dept, "qualification_hint": "generalistisch", "city": city,
                     "clinic_town": city, "regierungsbezirk": bezirk, "clinic_id": f"9000{i % 5}",
                     "clinic_name": f"Klinikum {city} {i % 5}", "employer": f"Klinikum {city}",
                     "employment_types": ["vollzeit"] if i % 2 else ["teilzeit"],
                     "enr_housing": bool(i % 4 == 0), "verify_status": "live", "status": "open",
                     "first_published": "2026-09-0%d" % (i % 9 + 1), "fresh": True,
                     "source_url": f"https://example.org/job/{i + 1}"})
    return rows


class FakeMeta:
    """Stands in for app.wa.meta.Client: records what would go to Meta, hands back a wamid."""

    def __init__(self):
        self.sent = []
        self.sent_templates = []
        self.n = 0

    def _next(self):
        self.n += 1
        return f"wamid.out.{self.n}"

    def send_text(self, to_e164, body):
        self.sent.append({"to": to_e164, "body": body, "buttons": None})
        return self._next()

    def send_buttons(self, to_e164, body, buttons):
        self.sent.append({"to": to_e164, "body": body, "buttons": buttons})
        return self._next()

    def send_template(self, to_e164, template_name, language="de", params=None):
        self.sent_templates.append({"to": to_e164, "template": template_name, "language": language, "params": params})
        return self._next()


class FailingMeta(FakeMeta):
    def send_text(self, to_e164, body):
        raise M.MetaError("Meta HTTP 400", status_code=400, payload={"error": {"code": 131030}})


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    """Snapshot stubbed, SQLite in a temp dir, credentials set, autosend on, Meta faked."""
    jobs = _jobs()
    D._snap.update({"at": time.time(), "jobs": jobs, "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "APP_SECRET", APP_SECRET)
    monkeypatch.setattr(C, "VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    return FakeMeta()


def payload(text=None, wamid="wamid.1", phone="491701234567", button=None, kind="text",
            phone_number_id=PHONE_ID):
    if button is not None:
        message = {"id": wamid, "from": phone, "type": "interactive",
                   "interactive": {"type": "button_reply", "button_reply": button}}
    elif kind != "text":
        message = {"id": wamid, "from": phone, "type": kind, kind: {"id": "media-1", "mime_type": "application/pdf"}}
    else:
        message = {"id": wamid, "from": phone, "type": "text", "text": {"body": text}}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_number_id},
                "messages": [message]}}]}]}


def post(wa, body, signature=None):
    raw = json.dumps(body).encode("utf-8")
    sig = signature if signature is not None else "sha256=" + hmac.new(
        APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return WAPI.handle_payload(body, client=wa) if sig is None else _route(wa, raw, sig)


def _route(wa, raw, sig):
    """Through the real route, so the signature check and the JSON parse are exercised too."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(WAPI.router, prefix="/api")

    def _client_dep():
        return wa

    with TestClient(app) as client:
        import app.wa.api as mod
        real = mod.M.Client
        mod.M.Client = lambda *a, **k: wa
        try:
            return client.post("/api/wa/webhook", content=raw,
                               headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
        finally:
            mod.M.Client = real


# --- the trust boundary --------------------------------------------------------------------------

def test_verification_echoes_the_challenge_only_for_the_right_token(wa):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(WAPI.router, prefix="/api")
    with TestClient(app) as client:
        ok = client.get("/api/wa/webhook", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                   "hub.verify_token": VERIFY_TOKEN})
        assert ok.status_code == 200 and ok.text == "42"
        for bad in ({"hub.mode": "subscribe", "hub.challenge": "42", "hub.verify_token": "wrong"},
                    {"hub.mode": "unsubscribe", "hub.challenge": "42", "hub.verify_token": VERIFY_TOKEN},
                    {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN}):
            assert client.get("/api/wa/webhook", params=bad).status_code == 403


def test_a_wrong_signature_is_rejected_and_nothing_is_sent(wa):
    r = _route(wa, json.dumps(payload("Hallo")).encode(), "sha256=" + "0" * 64)
    assert r.status_code == 403
    assert wa.sent == []


def test_a_missing_signature_header_is_rejected(wa):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(WAPI.router, prefix="/api")
    with TestClient(app) as client:
        assert client.post("/api/wa/webhook", json=payload("Hallo")).status_code == 403


def test_a_valid_signature_over_the_raw_body_is_accepted(wa):
    r = _route(wa, json.dumps(payload("Hallo")).encode(), "sha256=" + hmac.new(
        APP_SECRET.encode(), json.dumps(payload("Hallo")).encode(), hashlib.sha256).hexdigest())
    assert r.status_code == 200 and r.json()["handled"] == 1
    assert wa.sent, "a first contact must be answered"


def test_a_payload_for_another_whatsapp_number_is_skipped(wa):
    out = WAPI.handle_payload(payload("Hallo", phone_number_id="999"), client=wa)
    assert out["handled"] == 0 and out["skipped"] == 1
    assert wa.sent == []


# --- inbound parsing -----------------------------------------------------------------------------

def test_parse_reads_text_buttons_and_media(wa):
    msgs, _ = WAPI.inbound_messages(payload("Guten Tag"))
    assert msgs[0]["text"] == "Guten Tag" and msgs[0]["phone"] == LEAD and msgs[0]["button_id"] is None
    msgs, _ = WAPI.inbound_messages(payload(button={"id": "dept:OP", "title": "OP"}))
    assert msgs[0]["button_id"] == "dept:OP" and msgs[0]["text"] == "OP"
    msgs, _ = WAPI.inbound_messages(payload(kind="document"))
    assert msgs[0]["kind"] == "document" and msgs[0]["text"] == ""
    assert WAPI.parse_message({"id": "x", "from": "49170", "type": "reaction"}) is None
    assert WAPI.parse_message({"from": "49170", "type": "text", "text": {"body": "hi"}}) is None


def test_parse_captures_media_id_mime_type_and_filename(wa):
    """TASK-67: document/image/audio/video used to drop the media entirely (media_id/mime_type
    both None, no filename) -- these are what app/wa/meta.py's media download needs, captured
    straight from Meta's own `{"<type>": {"id":..., "mime_type":..., "filename":...}}` field."""
    for kind in ("document", "image", "audio", "video"):
        m = {"id": "wamid.m", "from": "491701234567", "type": kind,
             kind: {"id": "media-42", "mime_type": "application/pdf", "filename": "lebenslauf.pdf"}}
        parsed = WAPI.parse_message(m)
        assert parsed["media_id"] == "media-42"
        assert parsed["media_mime_type"] == "application/pdf"
        assert parsed["media_filename"] == "lebenslauf.pdf"
    # No filename (Meta does not always send one, e.g. for a photo): None, not a crash or "None" string.
    m = {"id": "wamid.i", "from": "491701234567", "type": "image",
         "image": {"id": "media-7", "mime_type": "image/jpeg"}}
    parsed = WAPI.parse_message(m)
    assert parsed["media_id"] == "media-7" and parsed["media_filename"] is None


def test_a_status_only_payload_answers_nothing(wa):
    body = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": PHONE_ID},
                                              "statuses": [{"id": "wamid.1", "status": "read"}]}}]}]}
    out = WAPI.handle_payload(body, client=wa)
    assert out["handled"] == 0 and wa.sent == []


def test_a_redelivered_webhook_is_answered_once(wa):
    first = WAPI.handle_payload(payload("Hallo", wamid="wamid.dup"), client=wa)
    again = WAPI.handle_payload(payload("Hallo", wamid="wamid.dup"), client=wa)
    assert first["results"][0]["status"] == "sent"
    assert again["results"][0]["status"] == "duplicate"
    assert len(wa.sent) == 2, "greeting + question once, not twice"


def test_media_is_acknowledged_not_silently_dropped(wa):
    out = WAPI.handle_payload(payload(kind="document", wamid="wamid.pdf"), client=wa)
    assert out["results"][0]["action"] == "media_ack"
    assert wa.sent[-1]["body"] == WAPI.MEDIA_REPLY


# --- the conversation ----------------------------------------------------------------------------

def test_first_contact_greets_and_asks_exactly_one_question(wa):
    WAPI.handle_payload(payload("Hallo", wamid="wamid.a"), client=wa)
    bodies = [s["body"] for s in wa.sent]
    assert len(bodies) == 2
    assert bodies[0] == B.GREETING
    assert sum(b.count("?") for b in bodies) == 1
    assert wa.sent[-1]["buttons"], "a question offers the live top values as reply buttons"


def test_every_turn_asks_at_most_one_question_in_short_bubbles(wa):
    for i, text in enumerate(["Hallo", "Intensivpflege", "München", "Vollzeit", "Urkunde habe ich"]):
        wa.sent.clear()
        WAPI.handle_payload(payload(text, wamid=f"wamid.t{i}"), client=wa)
        bodies = [s["body"] for s in wa.sent]
        assert len(bodies) <= B.MAX_BUBBLES
        assert sum(b.count("?") for b in bodies) <= 1
        for b in bodies:
            assert b.strip() and (len(b) <= B.MAX_BUBBLE_CHARS or "http" in b)


def test_the_next_question_is_the_one_that_narrows_the_list_most(wa):
    rows = B.jobs_for({})
    # Every row states a town and a department; role is 70 % one value, so it splits worse.
    assert B._gain(rows, "where") > B._gain(rows, "role")
    assert B.next_question(rows, {}, [])["slot"] == "where"
    # Once the town is known, the department is the useful question; the town is not re-asked.
    rows = B.jobs_for({"city": "München"})
    assert B.next_question(rows, {"city": "München"}, ["where"])["slot"] == "department"


def test_a_question_nobody_can_answer_differently_is_not_asked(wa):
    """Every Notaufnahme posting in München is the same role class, so asking the role buys nothing."""
    rows = B.jobs_for({"city": "München", "department": "Notaufnahme"})
    assert rows and {r["role_class"] for r in rows} == {"pflegefachkraft"}
    assert B._gain(rows, "role") == 0
    slots = {"city": "München", "department": "Notaufnahme"}
    assert B.next_question(rows, slots, ["where", "department"])["slot"] != "role"


def test_an_unanswered_question_is_repeated_once_then_dropped(wa):
    """A skipped question stays on the list at a discount, and is gone after two tries."""
    slots = {"city": "München", "role": "pflegefachkraft", "hours": "vollzeit", "housing": True}
    rows = B.jobs_for({"city": "München"})
    assert B.next_question(rows, slots, ["where"])["slot"] == "department"
    assert B.next_question(rows, slots, ["where", "department"])["slot"] == "department", \
        "asked once and unanswered: still the only thing left worth asking"
    assert B.next_question(rows, slots, ["where", "department", "department"]) is None, \
        "asked twice is enough; the harness stops nagging and shows what it has"
    # And the discount is real: a slot asked once loses to a fresh slot of similar value.
    assert B._gain(rows, "department") - B.ASK_AGAIN_PENALTY < B._gain(rows, "hours")


def test_one_message_can_fill_several_slots(wa):
    out = WAPI.handle_payload(payload("Intensiv in Würzburg, Teilzeit", wamid="wamid.m"), client=wa)
    assert out["results"][0]["slots"]["department"] == "Intensiv/IMC"
    assert out["results"][0]["slots"]["city"] == "Würzburg"
    assert out["results"][0]["slots"]["hours"] == "teilzeit"


def test_a_later_town_corrects_the_earlier_one(wa):
    WAPI.handle_payload(payload("München", wamid="wamid.c1"), client=wa)
    out = WAPI.handle_payload(payload("eigentlich lieber Augsburg", wamid="wamid.c2"), client=wa)
    assert out["results"][0]["slots"]["city"] == "Augsburg"


def test_a_bare_yes_answers_the_question_just_asked_and_not_an_older_one(wa):
    """'ja bitte' after the handover question must not also flip the housing slot from four turns ago."""
    t = {"slots": {}, "asked": ["housing", "urkunde", "handover"], "last_outbound_at": "x"}
    found = B.read_answer("ja bitte", t["slots"], t["asked"], [])
    assert found == {"handover": True}


def test_matches_name_real_postings_with_clinic_town_and_link(wa):
    wa.sent.clear()
    out = WAPI.handle_payload(payload("Intensiv in München", wamid="wamid.match"), client=wa)
    ids = out["results"][0]["matches"]
    assert ids, "a narrow enough search hands over postings"
    expected = B.jobs_for({"department": "Intensiv/IMC", "city": "München"})[:B.MATCH_LIMIT]
    assert ids == [r["posting_id"] for r in expected]
    bubble = wa.sent[0]["body"]
    for r in expected:
        assert r["clinic_name"][:20] in bubble and r["source_url"] in bubble
    assert "München" in bubble


def test_the_filters_are_the_ones_the_jobs_api_takes(wa):
    p = SL.filters({"role": "fachpflege", "city": "München", "department": "Intensiv/IMC",
                    "hours": "teilzeit", "housing": True, "urkunde": "urkunde"})
    assert p == {"verify": "live", "sort": "-first_published", "role_class": "fachpflege",
                 "city": "München", "department_hint": "Intensiv/IMC",
                 "employment_types": "teilzeit", "housing": "1"}
    assert D.filter_jobs(p) == [j for j in D.jobs()
                                if j["role_class"] == "fachpflege" and j["city"] == "München"
                                and j["department_hint"] == "Intensiv/IMC"
                                and "teilzeit" in j["employment_types"] and j["enr_housing"]]


def test_nothing_open_widens_the_search_and_says_what_it_dropped(wa):
    """Every Vollzeit posting in the fixture is on a row without housing, so this asks for both."""
    narrow = {"department": "OP", "city": "Augsburg", "hours": "vollzeit", "housing": True}
    assert B.jobs_for(narrow) == []
    assert B.jobs_for({k: v for k, v in narrow.items() if k != "housing"}), "dropping one wish is enough"
    wa.sent.clear()
    out = WAPI.handle_payload(payload("OP in Augsburg, Vollzeit, mit Wohnung", wamid="wamid.w"), client=wa)
    assert out["results"][0]["action"] == "widened:housing"
    assert "Wohnungs-Wunsch" in wa.sent[0]["body"] and "nichts offen" in wa.sent[0]["body"]
    assert out["results"][0]["matches"], "the widened search is shown, not an apology"


def test_nothing_open_at_all_is_said_plainly(wa):
    wa.sent.clear()
    out = WAPI.handle_payload(payload("Hebamme in Würzburg", wamid="wamid.none"), client=wa)
    assert B.jobs_for({"role": "hebamme", "city": "Würzburg"}) == []
    assert out["results"][0]["action"] == "no_matches"
    assert "nichts offen" in wa.sent[0]["body"] and out["results"][0]["matches"] == []


def test_the_urkunde_question_gates_the_handover(wa):
    WAPI.handle_payload(payload("Intensiv in München", wamid="wamid.u1"), client=wa)
    wa.sent.clear()
    out = WAPI.handle_payload(payload("Urkunde habe ich", wamid="wamid.u2"), client=wa)
    assert out["results"][0]["action"].endswith("handover")
    assert "Pflegedirektion" in wa.sent[-1]["body"]


def test_without_an_accepted_qualification_no_clinic_is_promised(wa):
    WAPI.handle_payload(payload("Intensiv in München", wamid="wamid.b1"), client=wa)
    wa.sent.clear()
    out = WAPI.handle_payload(payload("Kenntnisprüfung nicht bestanden", wamid="wamid.b2"), client=wa)
    assert out["results"][0]["action"] == "blocked_qualification"
    bodies = " ".join(s["body"] for s in wa.sent)
    assert "noch nicht vorstellen" in bodies and "Pflegedirektion" not in bodies


def test_a_failed_exam_counts_even_when_nobody_asked(wa):
    assert SL.read_disqualifier("bin leider durchgefallen") == "keine"
    assert SL.read_disqualifier("Urkunde ist da") is None


# --- opt-out -------------------------------------------------------------------------------------

def test_stop_ends_the_conversation_and_nothing_more_is_sent(wa):
    WAPI.handle_payload(payload("Hallo", wamid="wamid.s1"), client=wa)
    wa.sent.clear()
    out = WAPI.handle_payload(payload("STOP", wamid="wamid.s2"), client=wa)
    assert out["results"][0]["status"] == "stopped" and wa.sent == []
    after = WAPI.handle_payload(payload("doch noch Intensiv in München", wamid="wamid.s3"), client=wa)
    assert after["results"][0]["status"] == "stopped" and wa.sent == []
    with ST.db() as c:
        assert ST.thread(c, LEAD)["stopped"] is True


@pytest.mark.parametrize("text,stops", [("STOP", True), ("stopp", True), ("bitte abmelden", True),
                                        ("Daten löschen", True), ("Stopfen Sie das nicht", False),
                                        ("Intensivstation", False)])
def test_opt_out_words_do_not_fire_on_ordinary_german(text, stops):
    assert SL.is_stop(text) is stops


# --- the send path -------------------------------------------------------------------------------

def test_a_meta_failure_is_not_recorded_as_sent(wa, monkeypatch):
    with pytest.raises(M.MetaError):
        WAPI.handle_payload(payload("Hallo", wamid="wamid.f"), client=FailingMeta())
    with ST.db() as c:
        rows = ST.history(c, LEAD)
    assert [r["direction"] for r in rows] == ["in"], "the inbound is kept, no outbound is claimed"


def test_an_accepted_call_without_a_message_id_is_an_error(wa):
    class NoId:
        def __init__(self):
            self.calls = 0

        def transport(self, **kw):
            self.calls += 1
            return {"messages": [{}]}

    stub = NoId()
    client = M.Client(transport=lambda **kw: stub.transport(**kw), access_token="t", phone_number_id="p")
    with pytest.raises(M.MetaError, match="no message id"):
        client.send_text(LEAD, "hallo")


def test_no_credentials_raises_rather_than_pretending(wa):
    client = M.Client(transport=lambda **kw: {"messages": [{"id": "x"}]}, access_token="", phone_number_id="")
    with pytest.raises(M.MetaError, match="not set"):
        client.send_text(LEAD, "hallo")


def test_autosend_off_stores_drafts_and_sends_nothing(wa, monkeypatch):
    monkeypatch.setattr(C, "AUTOSEND", False)
    out = WAPI.handle_payload(payload("Hallo", wamid="wamid.d"), client=wa)
    assert out["results"][0]["status"] == "draft" and wa.sent == []
    with ST.db() as c:
        kinds = [r["kind"] for r in ST.history(c, LEAD)]
    assert kinds.count("draft") == 2


def test_media_url_fetches_the_lookup_json_with_a_bearer_token(wa):
    calls = []

    def transport(method, url, headers=None, data=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers})
        return {"url": "https://cdn.example/abc", "mime_type": "application/pdf", "id": "media-1"}

    client = M.Client(transport=transport, access_token="tok", phone_number_id="p")
    out = client.media_url("media-1")
    assert out["url"] == "https://cdn.example/abc"
    assert calls[0]["method"] == "GET" and calls[0]["url"].endswith("/media-1")
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_media_url_raises_without_a_url_in_the_response(wa):
    client = M.Client(transport=lambda **kw: {"mime_type": "application/pdf"}, access_token="tok", phone_number_id="p")
    with pytest.raises(M.MetaError, match="no url"):
        client.media_url("media-1")


def test_media_url_raises_without_an_access_token(wa):
    client = M.Client(transport=lambda **kw: {"url": "x"}, access_token="", phone_number_id="p")
    with pytest.raises(M.MetaError, match="not set"):
        client.media_url("media-1")


def test_download_media_returns_raw_bytes_not_parsed_json(wa):
    """The whole reason download_media needs its own transport: _default_transport always returns
    a parsed dict (JSON) or text decoded with errors='replace', either of which would corrupt real
    binary media. This asserts the exact bytes come back untouched, with the same bearer token."""
    raw = b"\xff\xd8\xff\xe0not-really-a-jpeg-but-binary\x00\x01\x02"
    calls = []

    def media_transport(method, url, headers=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers})
        return raw

    client = M.Client(media_transport=media_transport, access_token="tok", phone_number_id="p")
    out = client.download_media("https://cdn.example/abc")
    assert out == raw
    assert calls[0]["method"] == "GET" and calls[0]["url"] == "https://cdn.example/abc"
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_download_media_raises_without_an_access_token(wa):
    client = M.Client(media_transport=lambda **kw: b"x", access_token="", phone_number_id="p")
    with pytest.raises(M.MetaError, match="not set"):
        client.download_media("https://cdn.example/abc")


def test_buttons_respect_meta_limits(wa):
    client = M.Client(transport=lambda **kw: {"messages": [{"id": "x"}]}, access_token="t", phone_number_id="p")
    assert client.send_buttons(LEAD, "frage?", [{"id": "a", "title": "Ja"}]) == "x"
    with pytest.raises(M.MetaError, match="max is 3|3 reply buttons"):
        client.send_buttons(LEAD, "frage?", [{"id": str(i), "title": "x"} for i in range(4)])
    with pytest.raises(M.MetaError, match="over 20 chars"):
        client.send_buttons(LEAD, "frage?", [{"id": "a", "title": "x" * 21}])
    for q in (B.URKUNDE_QUESTION, B.HANDOVER_QUESTION):
        assert len(q["buttons"]) <= 3
        assert all(len(b["title"]) <= 20 for b in q["buttons"])


def test_every_generated_button_title_fits_meta(wa):
    rows = B.jobs_for({})
    for slot in B.LADDER:
        q = B._question(slot, rows)
        assert len(q["buttons"]) <= 3
        for b in q["buttons"]:
            assert 0 < len(b["title"]) <= 20 and b["id"]
            assert B.button_answer(b["id"]), f"{b['id']} must map back to a slot"


# --- storage -------------------------------------------------------------------------------------

def test_the_thread_keeps_slots_and_the_full_transcript(wa):
    for i, text in enumerate(["Hallo", "Intensiv in München", "Urkunde habe ich"]):
        WAPI.handle_payload(payload(text, wamid=f"wamid.k{i}"), client=wa)
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        rows = ST.history(c, LEAD)
    assert t["slots"]["city"] == "München" and t["slots"]["urkunde"] == "urkunde"
    assert t["turns"] == 3 and t["matches_sent_at"]
    assert [r["direction"] for r in rows][:2] == ["in", "out"]
    assert sum(1 for r in rows if r["direction"] == "in") == 3


def test_phone_numbers_become_one_identity(wa):
    assert M.sender_e164("491701234567") == LEAD
    assert M.canonicalize_phone("0170 1234567") == LEAD
    assert M.canonicalize_phone("0049-170-1234567") == LEAD
    assert M.canonicalize_phone("+49 170 1234567") == LEAD
    assert M.canonicalize_phone("") == ""


def test_health_reports_readiness_without_secrets(wa):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(WAPI.router, prefix="/api")
    with TestClient(app) as client:
        body = client.get("/api/wa/health").json()
    assert body["webhook_ready"] and body["outbound_ready"] and body["autosend"] is True
    assert APP_SECRET not in json.dumps(body) and "test-token" not in json.dumps(body)


# --- TASK-70: the 24h free-form window --------------------------------------------------------
# _handle_one() always stamps last_inbound_at to "now" for a live inbound turn, so the window can
# never be closed there by construction -- these test _send()/_freeform_window_open() directly,
# the way a future catch-up/dry-run tool (operating on a possibly-stale stored thread) would.

def test_window_is_open_for_a_thread_that_just_wrote(wa):
    t = {"phone": LEAD, "last_inbound_at": ST.now_iso()}
    assert WAPI._freeform_window_open(t) is True


def test_window_is_open_when_last_inbound_at_was_never_set(wa):
    assert WAPI._freeform_window_open({"phone": LEAD}) is True


def test_window_is_closed_after_the_configured_hours(wa, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(C, "FREEFORM_WINDOW_HOURS", 24)
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(microsecond=0).isoformat()
    assert WAPI._freeform_window_open({"phone": LEAD, "last_inbound_at": stale}) is False


def test_send_uses_the_reopen_template_when_the_window_is_closed(wa, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0).isoformat()
    t = {"phone": LEAD, "last_inbound_at": stale}
    with ST.db() as c:
        status = WAPI._send(c, t, ["Diese freie Nachricht darf es gar nicht bis zu Meta schaffen"], [], client=wa)
    assert status == "sent_template"
    assert wa.sent == [], "no free-form text call must reach Meta once the window is closed"


def test_a_sent_reopen_template_flips_ownership_to_us(wa, monkeypatch):
    """TASK-75: this harness reopening an old conversation with its own template is the one
    explicit act that hands that phone's ownership to us, regardless of whatever it was before."""
    from datetime import datetime, timedelta, timezone

    from app.wa import routing as R
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0).isoformat()
    t = {"phone": LEAD, "last_inbound_at": stale}
    with ST.db() as c:
        WAPI._send(c, t, ["Text"], [], client=wa)
    with R.db() as rc:
        assert R.route_decision(rc, LEAD) == "us"


def test_send_drafts_the_reopen_template_when_autosend_is_off(wa, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(C, "AUTOSEND", False)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0).isoformat()
    t = {"phone": LEAD, "last_inbound_at": stale}
    with ST.db() as c:
        status = WAPI._send(c, t, ["Text"], [], client=wa)
        kinds = [r["kind"] for r in ST.history(c, LEAD)]
    assert status == "draft_template"
    assert "draft_template" in kinds


def test_send_raises_loudly_when_window_closed_and_no_template_configured(wa, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "")
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0).isoformat()
    t = {"phone": LEAD, "last_inbound_at": stale}
    with ST.db() as c:
        with pytest.raises(RuntimeError, match="no reopen template is configured"):
            WAPI._send(c, t, ["Text"], [], client=wa)


def test_send_template_calls_the_real_meta_shape(wa):
    calls = []

    def transport(method, url, headers=None, data=None, timeout=None):
        calls.append(json.loads(data))
        return {"messages": [{"id": "wamid.tpl.1"}]}

    cl = M.Client(transport=transport, access_token="t", phone_number_id="1")
    wamid = cl.send_template(LEAD, "candidate_reopen_v1", "de")
    assert wamid == "wamid.tpl.1"
    assert calls[0]["type"] == "template"
    assert calls[0]["template"] == {"name": "candidate_reopen_v1", "language": {"code": "de"}}


def test_send_template_with_params_fills_body_components(wa):
    calls = []

    def transport(method, url, headers=None, data=None, timeout=None):
        calls.append(json.loads(data))
        return {"messages": [{"id": "wamid.tpl.2"}]}

    cl = M.Client(transport=transport, access_token="t", phone_number_id="1")
    cl.send_template(LEAD, "candidate_reopen_v1", "de", params=["Ionel"])
    assert calls[0]["template"]["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Ionel"}]}]


# --- template discovery: what is already approved on this WABA (docs/whatsapp.md) --------------

def test_phone_number_info_fetches_the_parent_waba_id(wa):
    calls = []

    def transport(method, url, headers=None, data=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers})
        return {"whatsapp_business_account": {"id": "waba-1"}, "display_phone_number": "+49 170 0000000"}

    cl = M.Client(transport=transport, access_token="tok", phone_number_id="p1")
    out = cl.phone_number_info()
    assert out["whatsapp_business_account"]["id"] == "waba-1"
    assert calls[0]["method"] == "GET" and "/p1?fields=" in calls[0]["url"]
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_list_message_templates_returns_the_flat_list(wa):
    def transport(method, url, headers=None, data=None, timeout=None):
        return {"data": [{"name": "candidate_reopen_v1", "status": "APPROVED", "language": "de"},
                         {"name": "candidate_reopen_v1", "status": "APPROVED", "language": "en"}]}

    cl = M.Client(transport=transport, access_token="tok", phone_number_id="p1")
    templates = cl.list_message_templates("waba-1")
    assert len(templates) == 2
    assert templates[0]["name"] == "candidate_reopen_v1"


def test_list_message_templates_follows_pagination(wa):
    pages = [
        {"data": [{"name": "tpl_a"}], "paging": {"next": "https://graph.facebook.com/next-page"}},
        {"data": [{"name": "tpl_b"}]},
    ]
    calls = []

    def transport(method, url, headers=None, data=None, timeout=None):
        calls.append(url)
        return pages.pop(0)

    cl = M.Client(transport=transport, access_token="tok", phone_number_id="p1")
    templates = cl.list_message_templates("waba-1")
    assert [t["name"] for t in templates] == ["tpl_a", "tpl_b"]
    assert len(calls) == 2 and calls[1] == "https://graph.facebook.com/next-page"


def test_list_message_templates_raises_without_an_access_token(wa):
    cl = M.Client(transport=lambda **kw: {"data": []}, access_token="", phone_number_id="p1")
    with pytest.raises(M.MetaError, match="not set"):
        cl.list_message_templates("waba-1")
