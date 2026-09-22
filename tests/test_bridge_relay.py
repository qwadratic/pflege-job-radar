"""Offline proof for the inbound id, the Meta envelope and the pull relay (TASK-143).

No phone, no ssh, no webhook. What is asserted here is what a candidate would feel if it were wrong:
a message that never gets answered because its id collided with someone else's, a message answered
twice because two doors minted two ids for it, and a message the relay walked past.
"""
from zoneinfo import ZoneInfo

import pytest

from bridge import driver as D
from bridge import envelope as EV
from bridge import inbound as I
from bridge import relay_pull as RP

BERLIN = ZoneInfo("Europe/Berlin")
ANNA = "+491700000001"
BERND = "+491700000002"

# Real records as `dumpsys notification --noredact` draws them on this handset (WhatsApp 2.26.36),
# with the numbers replaced. The four-space indent before NotificationRecord is load-bearing, and so
# is the shape: WhatsApp posts a MessagingStyle record for the message AND a plain group-summary
# record carrying the same android.text. Both are here on purpose.
SUMMARY_RECORD = """    NotificationRecord(0x0: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null key=0|com.whatsapp|1|null|10148appImportanceLocked=false: Notification(channel=individual_chat_defaults_1 category=msg groupKey=group_key_messages)
        tickerText=Nachricht von +49 170 0000001
        android.title=String (+49 170 0000001)
        android.text=String (Ja, gerne)
        android.showWhen=Boolean (true)
        mCreationTimeMs=1789312501000
"""
DUMP = "Current Notification List:\n" + SUMMARY_RECORD + """    NotificationRecord(0x1: pkg=com.whatsapp user=UserHandle{0} id=1 tag=abc key=0|com.whatsapp|1|null|10148appImportanceLocked=false: Notification(channel=individual_chat_defaults)
        android.title=String (+49 170 0000001)
        android.template=String (android.app.Notification$MessagingStyle)
        android.text=String (Ja, gerne)
        [0] Bundle[{extras=Bundle[{}], sender_person=android.app.Person@1, sender=+49 170 0000001, text=Ja, gerne, time=1789312500000}]
        android.messagingStyleUser=Bundle (Bundle[mParcelledData.dataSize=100])
        android.isGroupConversation=Boolean (false)
        mCreationTimeMs=1789312501000
    NotificationRecord(0x2: pkg=com.whatsapp user=UserHandle{0} id=2 tag=def key=0|com.whatsapp|2|null|10148appImportanceLocked=false: Notification(channel=individual_chat_defaults)
        android.title=String (+49 170 0000002)
        android.template=String (android.app.Notification$MessagingStyle)
        android.text=String (Ja, gerne)
        [0] Bundle[{extras=Bundle[{}], sender_person=android.app.Person@2, sender=+49 170 0000002, text=Ja, gerne, time=1789312500000}]
        android.isGroupConversation=Boolean (false)
        mCreationTimeMs=1789312501000
    NotificationRecord(0x3: pkg=com.android.systemui user=UserHandle{0} id=3 tag=ghi key=0|x|3|null|1000appImportanceLocked=false: Notification(channel=other)
        android.title=String (USB-Debugging aktiviert)
        mCreationTimeMs=1789312501000
"""


def resolver(title):
    return "+" + I.digits(title) if I.looks_like_number(title) else ""


def parse(dump=DUMP, **kw):
    return I.notification_messages(dump, tz=BERLIN, resolve=resolver, **kw)


# --- the id -------------------------------------------------------------------------------------
def test_two_people_answering_ja_in_the_same_minute_get_two_ids():
    """The reference fingerprint is sha1(direction|HH:MM|text): both of these collapse into one row,
    wa_messages.wamid is UNIQUE, and one of the two candidates is never answered."""
    messages, unresolved = parse()
    assert unresolved == []
    assert [m.counterparty for m in messages] == [ANNA, BERND]
    assert [m.text for m in messages] == ["Ja, gerne", "Ja, gerne"]
    assert messages[0].clock == messages[1].clock
    assert messages[0].inbound_id != messages[1].inbound_id


def test_the_same_notification_on_the_next_poll_is_the_same_id():
    first, _ = parse()
    second, _ = parse()
    assert [m.inbound_id for m in first] == [m.inbound_id for m in second]


def test_the_same_text_twice_in_one_minute_from_one_number_is_two_ids():
    repeated = DUMP.replace("sender=+49 170 0000002, text=Ja, gerne",
                            "sender=+49 170 0000001, text=Ja, gerne") \
                   .replace("android.title=String (+49 170 0000002)",
                            "android.title=String (+49 170 0000001)")
    messages, _ = I.notification_messages(repeated, tz=BERLIN, resolve=resolver)
    assert [m.occurrence for m in messages] == [0, 1]
    assert len({m.inbound_id for m in messages}) == 2


def test_the_same_text_on_two_days_is_two_ids():
    day_two = DUMP.replace("time=1789312500000", "time=1789398900000")
    first, _ = parse()
    second, _ = I.notification_messages(day_two, tz=BERLIN, resolve=resolver)
    assert first[0].local_date != second[0].local_date
    assert first[0].inbound_id != second[0].inbound_id


def test_the_shade_and_the_open_thread_mint_the_same_id_for_one_message():
    """Opening a chat to reply clears its notification, so the same message reaches us through both
    doors. Two ids would mean the candidate is answered twice."""
    from_shade, _ = parse()
    anna = from_shade[0]
    from_thread, unresolved = I.thread_messages([D.BubbleView("in", "Ja, gerne", anna.clock, "")],
                                                counterparty=ANNA, local_date=anna.local_date)
    assert from_thread[0].inbound_id == anna.inbound_id
    assert unresolved == []


def test_a_bubble_from_an_earlier_day_is_unresolved_rather_than_stamped_with_today():
    """WhatsApp draws only HH:MM on a bubble. Stamping yesterday's bubble with today's date minted
    an id the shade never minted, so it passed the UNIQUE column and was answered as a new message
    -- on the first send of day two, once per still-visible message from day one (TASK-146)."""
    yesterday = D.BubbleView("in", "Ja", "20:40", "")
    today = D.BubbleView("in", "Und noch etwas", "09:05", "")
    placed, unresolved = I.thread_messages([yesterday, today], counterparty=ANNA,
                                           local_date="2026-09-21", older=1)
    assert [m.text for m in placed] == ["Und noch etwas"]
    assert len(unresolved) == 1 and "day separator" in unresolved[0][1]


def test_an_id_cannot_be_minted_without_a_counterparty():
    with pytest.raises(ValueError):
        I.mint(I.InboundMessage(counterparty="", title="Oma", text="Ja",
                                local_date="2026-09-21", clock="10:04", source="notification"))


# --- what the shade is allowed to produce ----------------------------------------------------------
def test_other_packages_and_groups_and_our_own_echo_are_not_inbound():
    grouped = DUMP.replace("android.isGroupConversation=Boolean (false)",
                           "android.isGroupConversation=Boolean (true)", 1)
    messages, _ = I.notification_messages(grouped, tz=BERLIN, resolve=resolver)
    assert [m.counterparty for m in messages] == [BERND]

    # Our own bubble is echoed into the shade as sender="Du". It must not come back as inbound --
    # and its summary record must not resurrect it either.
    echoed = DUMP.replace("sender=+49 170 0000001", "sender=Du")
    messages, _ = I.notification_messages(echoed, tz=BERLIN, resolve=resolver)
    assert [m.counterparty for m in messages] == [BERND]


def test_a_collapsed_count_is_a_summary_and_never_a_message():
    collapsed = ("    NotificationRecord(0x9: pkg=com.whatsapp user=UserHandle{0} id=9 tag=z "
                 "key=0|com.whatsapp|9|null|10148appImportanceLocked=false: Notification(x)\n"
                 "        android.title=String (+49 170 0000001)\n"
                 "        android.text=String (2 neue Nachrichten)\n"
                 "        android.isGroupConversation=Boolean (false)\n")
    assert I.notification_messages(collapsed, tz=BERLIN, resolve=resolver)[0] == []


def test_one_message_with_a_summary_record_beside_it_is_one_message():
    """Observed live: WhatsApp posted a MessagingStyle record AND a group-summary record for one
    inbound "tst". Reading the summary's android.text as a message answers the candidate twice."""
    messages, unresolved = parse()
    assert len(messages) == 2                       # Anna and Bernd, not Anna twice
    assert [m.counterparty for m in messages] == [ANNA, BERND]
    assert [m.occurrence for m in messages] == [0, 0]
    assert unresolved == []


def test_a_first_message_with_no_messagingstyle_record_anywhere_still_arrives():
    """Some builds post the first message of a brand-new thread as android.text only. Dropping it
    would lose the opening message of every new conversation."""
    messages, _ = I.notification_messages("Current Notification List:\n" + SUMMARY_RECORD,
                                          tz=BERLIN, resolve=resolver)
    assert [(m.counterparty, m.text) for m in messages] == [(ANNA, "Ja, gerne")]
    assert messages[0].time_ms == 1789312501000     # the record's own creation time, not "now"


def test_a_record_with_no_timestamp_is_reported_rather_than_stamped_with_now():
    """An id keyed on the local minute plus a wall clock read at parse time would mint a new id
    every minute and re-answer the same candidate for as long as the notification sits there."""
    undated = ("Current Notification List:\n"
               + SUMMARY_RECORD.replace("        mCreationTimeMs=1789312501000\n", ""))
    messages, unresolved = I.notification_messages(undated, tz=BERLIN, resolve=resolver)
    assert messages == []
    assert unresolved == [("+49 170 0000001", "notification record carries no timestamp")]


def test_a_title_the_handset_cannot_name_is_reported_and_not_minted():
    named = DUMP.replace("android.title=String (+49 170 0000001)", "android.title=String (Oma)")
    messages, unresolved = I.notification_messages(named, tz=BERLIN, resolve=resolver)
    assert [m.counterparty for m in messages] == [BERND]
    assert unresolved == [("Oma", "address book has no number for this title")]


def test_a_media_placeholder_keeps_its_kind():
    photo = DUMP.replace("text=Ja, gerne, time", "text=\U0001f4f7 Foto, time")
    messages, _ = I.notification_messages(photo, tz=BERLIN, resolve=resolver)
    assert messages[0].media == "image"


# --- the envelope ----------------------------------------------------------------------------------
def envelope_for(message):
    return EV.meta_envelope(message.payload(), phone_number_id="pflege-bridge-01",
                            display_phone_number="+4915216678689", waba_id="pflege-wa-bridge")


def test_the_envelope_is_the_shape_the_live_webhook_already_parses():
    """Shape fidelity is the whole trick: app/wa/api.py must read this without one line of change."""
    from app.wa import api as API

    messages, _ = parse()
    payload = envelope_for(messages[0])
    parsed, skipped = API.inbound_messages(payload)
    assert skipped == 0
    assert len(parsed) == 1
    assert parsed[0]["phone"] == ANNA
    assert parsed[0]["text"] == "Ja, gerne"
    assert parsed[0]["wamid"] == messages[0].inbound_id


def test_the_envelope_carries_our_own_id_because_this_rail_has_no_provider_id():
    messages, _ = parse()
    message = envelope_for(messages[0])["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["id"].startswith(I.INBOUND_PREFIX)
    assert message["from"] == "491700000001"
    assert message["timestamp"] == "1789312500"
    assert message["type"] == "text"


def test_an_envelope_is_refused_rather_than_invented_when_the_id_is_missing():
    messages, _ = parse()
    payload = messages[0].payload()
    payload["inbound_id"] = ""
    with pytest.raises(ValueError):
        EV.meta_envelope(payload, phone_number_id="x", display_phone_number="y", waba_id="z")


def test_a_number_as_its_own_profile_name_is_not_a_name():
    messages, _ = parse()
    contacts = envelope_for(messages[0])["entry"][0]["changes"][0]["value"]["contacts"]
    assert contacts[0]["profile"]["name"] == ""


# --- the envelope, once media is linked (TASK-131) --------------------------------------------------
def test_a_linked_document_arrives_as_the_meta_shape_the_webhook_already_reads():
    """Shape fidelity again, now for the branch TASK-131 adds: app/wa/api.py must read this exactly
    like a real Cloud API document message without one line of change."""
    from app.wa import api as API

    photo = DUMP.replace("text=Ja, gerne, time", "text=\U0001f4c4 Dokument, time")
    messages, _ = I.notification_messages(photo, tz=BERLIN, resolve=resolver)
    payload = messages[0].payload()
    payload["media_id"] = "wab.m.aaaaaaaaaaaaaaaaaaaa"
    payload["media_mime_type"] = "application/pdf"
    payload["media_filename"] = "Lebenslauf.pdf"

    envelope = EV.meta_envelope(payload, phone_number_id="pflege-bridge-01",
                                display_phone_number="+4915216678689", waba_id="pflege-wa-bridge")
    message = envelope["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["type"] == "document"
    assert message["document"] == {"id": "wab.m.aaaaaaaaaaaaaaaaaaaa", "mime_type": "application/pdf",
                                   "filename": "Lebenslauf.pdf"}

    parsed, skipped = API.inbound_messages(envelope)
    assert skipped == 0 and len(parsed) == 1
    assert parsed[0]["kind"] == "document"
    assert parsed[0]["media_id"] == "wab.m.aaaaaaaaaaaaaaaaaaaa"
    assert parsed[0]["media_mime_type"] == "application/pdf"
    assert parsed[0]["media_filename"] == "Lebenslauf.pdf"


def test_a_weakly_attributed_documents_strength_reaches_the_parsed_message():
    """TASK-131 round 6: the one field that gates a weak document's text from the model
    (app/wa/api.py) travels the same wire everything else about a linked file already does."""
    from app.wa import api as API

    photo = DUMP.replace("text=Ja, gerne, time", "text=\U0001f4c4 Dokument, time")
    messages, _ = I.notification_messages(photo, tz=BERLIN, resolve=resolver)
    payload = messages[0].payload()
    payload["media_id"] = "wab.m.aaaaaaaaaaaaaaaaaaaa"
    payload["media_filename"] = "Lebenslauf.pdf"
    payload["media_link_strength"] = "weak"

    envelope = EV.meta_envelope(payload, phone_number_id="pflege-bridge-01",
                                display_phone_number="+4915216678689", waba_id="pflege-wa-bridge")
    assert envelope["entry"][0]["changes"][0]["value"]["messages"][0]["document"]["link_strength"] \
        == "weak"

    parsed, skipped = API.inbound_messages(envelope)
    assert skipped == 0
    assert parsed[0]["media_link_strength"] == "weak"


def test_a_strongly_attributed_documents_strength_also_reaches_the_parsed_message():
    from app.wa import api as API

    photo = DUMP.replace("text=Ja, gerne, time", "text=\U0001f4c4 Dokument, time")
    messages, _ = I.notification_messages(photo, tz=BERLIN, resolve=resolver)
    payload = messages[0].payload()
    payload["media_id"] = "wab.m.bbbbbbbbbbbbbbbbbbbb"
    payload["media_filename"] = "Lebenslauf.pdf"
    payload["media_link_strength"] = "strong"

    envelope = EV.meta_envelope(payload, phone_number_id="pflege-bridge-01",
                                display_phone_number="+4915216678689", waba_id="pflege-wa-bridge")
    parsed, skipped = API.inbound_messages(envelope)
    assert skipped == 0
    assert parsed[0]["media_link_strength"] == "strong"


def test_no_strength_at_all_is_omitted_not_sent_as_a_value():
    """A human attach (or a legacy link migrated before this column existed) carries no strength --
    omitted, never defaulted to 'strong', so a reader cannot mistake silence for a claim."""
    from app.wa import api as API

    photo = DUMP.replace("text=Ja, gerne, time", "text=\U0001f4c4 Dokument, time")
    messages, _ = I.notification_messages(photo, tz=BERLIN, resolve=resolver)
    payload = messages[0].payload()
    payload["media_id"] = "wab.m.cccccccccccccccccccc"
    payload["media_filename"] = "Lebenslauf.pdf"

    envelope = EV.meta_envelope(payload, phone_number_id="pflege-bridge-01",
                                display_phone_number="+4915216678689", waba_id="pflege-wa-bridge")
    assert "link_strength" not in envelope["entry"][0]["changes"][0]["value"]["messages"][0]["document"]
    parsed, _ = API.inbound_messages(envelope)
    assert parsed[0]["media_link_strength"] is None


def test_an_unlinked_media_message_still_falls_back_to_the_placeholder_text():
    photo = DUMP.replace("text=Ja, gerne, time", "text=\U0001f4f7 Foto, time")
    messages, _ = I.notification_messages(photo, tz=BERLIN, resolve=resolver)
    message = envelope_for(messages[0])["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["type"] == "text"
    assert message["text"]["body"] == "\U0001f4f7 Foto"


def test_a_location_placeholder_never_becomes_a_media_message_even_with_an_id_on_it():
    """Not a downloadable kind (bridge/media.py:DOWNLOADABLE_KINDS): a location or a contact card
    is never a file on disk, so an id here would be invented, not pulled."""
    payload = parse()[0][0].payload()
    payload["media_kind"] = "location"
    payload["media_id"] = "wab.m.whatever"
    message = EV.meta_envelope(payload, phone_number_id="x", display_phone_number="y",
                               waba_id="z")["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["type"] == "text"


# --- the relay cursor -------------------------------------------------------------------------------
class FakeRelay(RP.Relay):
    """A relay with the ssh leg and the webhook replaced by lists."""

    def __init__(self, cursor, items, answers):
        super().__init__(host="nowhere", token="t", webhook_url="http://127.0.0.1:8502/x",
                         inbound_token="i", cursor=cursor, phone_number_id="pflege-bridge-01",
                         display_phone_number="+49", waba_id="w", log=lambda _m: None)
        self.items = items
        self.answers = list(answers)
        self.posted = []

    def fetch(self):
        return [item for item in self.items if item["id"] > self.cursor.position()]

    def deliver(self, item):
        self.posted.append(item["id"])
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def outbox_item(number, message):
    return {"id": number, "received_at": "2026-09-21T10:00:00.000Z", "payload": message.payload()}


@pytest.fixture
def cursor(tmp_path):
    handle = RP.Cursor(tmp_path / "relay.sqlite")
    yield handle
    handle.close()


def test_the_cursor_advances_only_over_items_the_server_accepted(cursor):
    messages, _ = parse()
    items = [outbox_item(1, messages[0]), outbox_item(2, messages[1])]
    relay = FakeRelay(cursor, items, ["accepted", RP.RelayError("webhook answered 502")])
    with pytest.raises(RP.RelayError):
        relay.drain_once()
    assert cursor.position() == 1

    # the next pass redelivers item 2 and nothing before it
    relay.answers = ["accepted"]
    assert relay.drain_once() == 1
    assert relay.posted == [1, 2, 2]
    assert cursor.position() == 2


def test_a_redelivery_is_recorded_as_a_duplicate_and_still_advances(cursor):
    messages, _ = parse()
    relay = FakeRelay(cursor, [outbox_item(1, messages[0])], ["duplicate"])
    assert relay.drain_once() == 1
    assert (relay.delivered, relay.duplicates) == (0, 1)
    assert cursor.position() == 1


def test_a_cursor_survives_the_process(tmp_path):
    messages, _ = parse()
    first = RP.Cursor(tmp_path / "relay.sqlite")
    FakeRelay(first, [outbox_item(7, messages[0])], ["accepted"]).drain_once()
    first.close()
    second = RP.Cursor(tmp_path / "relay.sqlite")
    assert second.position() == 7
    second.close()


def test_a_webhook_that_handled_nothing_stops_the_relay_rather_than_walking_past_it(cursor,
                                                                                    monkeypatch):
    """api._number_matches skips a payload for an unknown phone_number_id silently. Advancing over
    that would lose a candidate's question with a 200 in the log."""
    messages, _ = parse()
    monkeypatch.setattr(RP, "http_json",
                        lambda *a, **kw: (200, {"ok": True, "handled": 0, "skipped": 1,
                                                "results": []}, 0.01))
    relay = RP.Relay(host="nowhere", token="t", webhook_url="http://127.0.0.1:8502/x",
                     inbound_token="i", cursor=cursor, phone_number_id="wrong",
                     display_phone_number="+49", waba_id="w", log=lambda _m: None)
    with pytest.raises(RP.RelayError) as caught:
        relay.deliver(outbox_item(1, messages[0]))
    assert "WA_BRIDGE_PHONE_NUMBER_ID" in str(caught.value)
    assert cursor.position() == 0


# --- TASK-146: the relay's own configuration and its log ----------------------------------------
def test_a_misconfigured_relay_names_every_missing_variable_at_once(monkeypatch):
    """This unit reads three EnvironmentFiles. Stopping at the first missing name meant finding out
    about WA_BRIDGE_TOKEN, then WA_BRIDGE_INBOUND_TOKEN, then WA_BRIDGE_PHONE_NUMBER_ID one restart
    at a time, and WA_BRIDGE_PHONE_NUMBER_ID existed in no file on the host at all."""
    for name in RP.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RP.RelayError) as caught:
        RP.check_env()
    for name in RP.REQUIRED_ENV:
        assert name in str(caught.value)
    assert "rail.env" in str(caught.value), "and where each one belongs"


def test_a_configured_relay_passes_the_check(monkeypatch):
    for name in RP.REQUIRED_ENV:
        monkeypatch.setenv(name, "x")
    assert RP.check_env() == sorted(RP.REQUIRED_ENV)


def test_a_borrowed_tunnel_is_announced_once_and_not_every_three_seconds():
    """`up()` had no branch for a forward we did not start, so every drain went through close() --
    which resets `borrowed` -- and re-announced it on the next line. At a 3 s interval that is a
    line every 3 s forever, which is how a journal stops being readable when someone needs it."""
    lines = []
    forward = RP.SshForward("macmini", log=lines.append)
    forward._port_open = lambda: True          # a dedicated tunnel unit already owns the port

    for _ in range(5):
        forward.open()
    assert lines == ["tunnel borrowed: 127.0.0.1:18793 is already forwarded"]
    assert forward.borrowed and forward.proc is None, "and no child of ours was started"
