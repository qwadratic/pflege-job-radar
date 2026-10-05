"""Offline proof for the inbound id, the Meta envelope and the pull relay (TASK-372).

No phone, no ssh, no webhook. What is asserted here is what a candidate would feel if it were wrong:
a message that never gets answered because its id collided with someone else's, a message answered
twice because two doors minted two ids for it, and a message the relay walked past.
"""
import json
from datetime import datetime, timedelta, timezone
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
    -- on the first send of day two, once per still-visible message from day one (TASK-375)."""
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
    messages, unresolved = I.notification_messages(collapsed, tz=BERLIN, resolve=resolver)
    assert messages == []
    # A suppressed summary is not silence: it has to land in the journal and the health counter,
    # not evaporate the way an empty `unresolved` here would (TASK-254).
    assert unresolved == [("+49 170 0000001", "coalesced summary text, no individual message available")]


def test_a_collapsed_count_of_exactly_one_is_still_a_summary_and_never_a_message():
    """German always inflects the noun for count=1 ("1 neue Nachricht"), not "1 neue Nachrichten" --
    and a thread's very first inbound message is, by construction, a count of exactly one. Before
    TASK-254's fix, this singular text missed _SUMMARY and fell through the fallback at :235,
    minting an id and storing "1 neue Nachricht" as if the candidate had typed it."""
    collapsed = ("    NotificationRecord(0x9: pkg=com.whatsapp user=UserHandle{0} id=9 tag=z "
                 "key=0|com.whatsapp|9|null|10148appImportanceLocked=false: Notification(x)\n"
                 "        android.title=String (+49 170 0000001)\n"
                 "        android.text=String (1 neue Nachricht)\n"
                 "        android.isGroupConversation=Boolean (false)\n")
    messages, unresolved = I.notification_messages(collapsed, tz=BERLIN, resolve=resolver)
    assert messages == []
    assert unresolved == [("+49 170 0000001", "coalesced summary text, no individual message available")]


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


# --- the envelope, once media is linked (TASK-360) --------------------------------------------------
def test_a_linked_document_arrives_as_the_meta_shape_the_webhook_already_reads():
    """Shape fidelity again, now for the branch TASK-360 adds: app/wa/api.py must read this exactly
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
    """TASK-360 round 6: the one field that gates a weak document's text from the model
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
    assert "outbox #1" in str(caught.value), "an operator stuck on this alarm needs the item, not just the cause"
    assert messages[0].inbound_id in str(caught.value)
    assert cursor.position() == 0


# --- TASK-375: the relay's own configuration and its log ----------------------------------------
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


# --- TASK-255: nothing consumed the watcher heartbeat / /v1/health ------------------------------
def _health_body(*, watcher_alive=True, watcher_last_ok_at, oldest_unacked_at=None):
    return {"watcher": {"alive": watcher_alive, "last_ok_at": watcher_last_ok_at},
            "inbound": {"oldest_unacked_at": oldest_unacked_at}}


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_check_watcher_alarm_is_silent_on_a_healthy_body(cursor):
    relay = FakeRelay(cursor, [], [])
    lines = []
    relay.log = lines.append
    relay.health = lambda: (_health_body(watcher_last_ok_at=_ago(1)), 0.01)
    assert relay.check_watcher_alarm() == []
    assert lines == []


def test_check_watcher_alarm_fires_on_a_stale_last_ok_at(cursor):
    relay = FakeRelay(cursor, [], [])
    lines = []
    relay.log = lines.append
    stale = _ago(RP.WATCHER_STALE_SEC + 30)
    relay.health = lambda: (_health_body(watcher_last_ok_at=stale), 0.01)
    problems = relay.check_watcher_alarm()
    assert problems and "last_ok_at" in problems[0]
    assert any(line.startswith("ALARM:") for line in lines)


def test_check_watcher_alarm_fires_when_alive_is_false(cursor):
    relay = FakeRelay(cursor, [], [])
    lines = []
    relay.log = lines.append
    relay.health = lambda: (_health_body(watcher_alive=False, watcher_last_ok_at=_ago(1)), 0.01)
    problems = relay.check_watcher_alarm()
    assert any("alive" in p for p in problems)
    assert any(line.startswith("ALARM:") for line in lines)


def test_check_watcher_alarm_fires_on_an_old_inbound_backlog(cursor):
    relay = FakeRelay(cursor, [], [])
    lines = []
    relay.log = lines.append
    old = _ago(RP.INBOUND_BACKLOG_STALE_SEC + 30)
    relay.health = lambda: (
        _health_body(watcher_last_ok_at=_ago(1), oldest_unacked_at=old), 0.01)
    problems = relay.check_watcher_alarm()
    assert any("unacked" in p for p in problems)
    assert any(line.startswith("ALARM:") for line in lines)


def test_check_watcher_alarm_never_raises_when_health_itself_fails(cursor):
    """A stuck executor (the exact class of failure this alarm exists to catch) must not take the
    check itself out -- it has to report the failure, not crash the drain loop that calls it."""
    relay = FakeRelay(cursor, [], [])
    lines = []
    relay.log = lines.append

    def broken_health():
        raise RP.RelayError("executor health is 500: {}")

    relay.health = broken_health
    problems = relay.check_watcher_alarm()
    assert problems
    assert any(line.startswith("ALARM:") for line in lines)


class _StopAfterOnePass:
    """A fake ``stop`` event that lets Relay.run()'s while-loop body execute exactly once."""

    def __init__(self):
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > 1


def test_relay_run_asks_the_watcher_alarm_check_on_its_own_cadence(cursor, monkeypatch):
    """Before TASK-255, Relay.run() called drain_once() every pass and never health() -- the
    reachable gap the task names. This drives run() for exactly one loop body and proves the new
    call is actually wired in, not just defined."""
    monkeypatch.setattr(RP.time, "sleep", lambda _seconds: None)
    relay = FakeRelay(cursor, [], [])
    checks = []
    relay.check_watcher_alarm = lambda: checks.append(1)
    relay.run(stop=_StopAfterOnePass())
    assert checks == [1]


# --- rail freshness heartbeat (review item 13, 2026-09-30) -----------------------------------------
# sync_writer is opt-in (None by default, see Relay.__init__): every test above builds a FakeRelay
# without one, so none of them ever touch a database for this. These tests pass an explicit fake
# writer to prove run() actually calls it, on both outcomes, without pulling in app.wa.store at all.

def test_relay_run_marks_synced_on_a_successful_pass(cursor, monkeypatch):
    monkeypatch.setattr(RP.time, "sleep", lambda _seconds: None)
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    calls = []
    relay.sync_writer = lambda ok, error=None: calls.append((ok, error))
    relay.run(stop=_StopAfterOnePass())
    assert calls == [(True, None)]


def test_relay_run_marks_sync_error_on_a_relay_error_without_losing_the_cursor(cursor, monkeypatch):
    monkeypatch.setattr(RP.time, "sleep", lambda _seconds: None)
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    relay.fetch = lambda: (_ for _ in ()).throw(RP.RelayError("webhook answered 502"))
    calls = []
    relay.sync_writer = lambda ok, error=None: calls.append((ok, error))
    relay.run(stop=_StopAfterOnePass())
    assert len(calls) == 1
    assert calls[0][0] is False
    assert "502" in calls[0][1]
    assert cursor.position() == 0   # the cursor never moved for a failed pass


def test_a_broken_sync_writer_never_takes_down_the_drain_loop(cursor, monkeypatch):
    """The heartbeat is a supplement, never load-bearing: a sync_writer that itself raises must only
    be logged, not propagate out of run() and kill the relay process over a disk-full/locked-db
    heartbeat write."""
    monkeypatch.setattr(RP.time, "sleep", lambda _seconds: None)
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    lines = []
    relay.log = lines.append

    def _boom(ok, error=None):
        raise RuntimeError("disk full")

    relay.sync_writer = _boom
    relay.run(stop=_StopAfterOnePass())   # must not raise
    assert any("disk full" in line for line in lines)


def test_production_relay_wires_the_real_rail_sync_writer(monkeypatch):
    """from_env is the only caller that may build a Relay with a real (non-None) sync_writer --
    every direct Relay()/FakeRelay() construction in this file defaults to None (review item 13)."""
    for name, where in RP.REQUIRED_ENV.items():
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("WA_BRIDGE_RELAY_STATE", "/dev/shm/pflege-wa-test-relay-cursor.sqlite")
    relay = RP.from_env()
    try:
        assert relay.sync_writer is RP._write_rail_sync
    finally:
        relay.close()
        relay.cursor.close()


# --- TASK-283.7: health whitelist / phone-state derivation --------------------------------------

def test_trim_health_drops_the_rails_own_phone_number_and_the_debug_only_driver_fields():
    body = {"ok": True, "version": "x", "at": _ago(0),
            "rail": {"number": "+491700000099", "msisdn_verified": True, "note": None,
                     "driver": {"connected": True, "kind": "adb", "serial": "ABC123",
                               "whatsapp_version": "2.26.1",
                               "modules": {"adb_driver.py": {"sha256": "deadbeef"}}}},
            "watcher": {"alive": True}, "doctor": {"blocked_by_stuck_op": False},
            "journal_recent": {"dirty_state_recovered": 0, "idle_dirty_recovered": 0},
            "audit": {"destructions": 3}}
    trimmed = RP._trim_health(body)
    assert trimmed["driver"] == {"connected": True, "kind": "adb"}
    assert "audit" not in trimmed and "rail" not in trimmed and "ok" not in trimmed
    assert "+491700000099" not in json.dumps(trimmed)
    assert "ABC123" not in json.dumps(trimmed) and "deadbeef" not in json.dumps(trimmed)
    assert trimmed["watcher"] == {"alive": True}


def test_trim_health_strips_a_phone_from_free_text_last_error_fields():
    """NIT-1, 10-05 review: watcher/dispatcher/broadcast-runner/retention last_error fields are all
    built from an uncaught exception's own str(exc), which can carry a raw phone (review finding 2's
    own class of leak). ``_trim_health`` must never let that text through, while still keeping the
    *fact* that an error happened (a boolean) and the timestamp, since
    pro_api._broadcasts_job_summary derives broadcast_runner_error off exactly that presence check."""
    leaky = f"ConnectionError: could not reach the chat for {BERND} (window closed)"
    body = {"watcher": {"alive": True, "last_error": leaky, "last_error_at": _ago(0)},
            "ops_dispatcher": {"errors": 2, "last_error": leaky, "last_error_at": _ago(0)},
            "retention": {"last_ok_at": _ago(0), "errors": 0, "last_error": leaky,
                          "last_error_at": _ago(0), "result": {"purged": 1}},
            "broadcast": {"runs_open": 1,
                          "runner": {"attempted": 5, "errors": 1, "last_error": leaky,
                                     "last_error_at": _ago(0), "last_item_at": None}}}
    trimmed = RP._trim_health(body)
    dumped = json.dumps(trimmed)
    assert BERND not in dumped
    assert "window closed" not in dumped
    # the presence of an error, and when it happened, both survive as non-free-text values
    assert trimmed["watcher"]["last_error"] is True
    assert trimmed["watcher"]["last_error_at"] == body["watcher"]["last_error_at"]
    assert trimmed["ops_dispatcher"]["last_error"] is True
    assert trimmed["retention"]["last_error"] is True
    assert trimmed["retention"]["errors"] == 0           # a plain count, untouched
    assert trimmed["broadcast"]["runner"]["last_error"] is True
    assert trimmed["broadcast"]["runner"]["last_error_at"] == body["broadcast"]["runner"]["last_error_at"]


def test_trim_health_leaves_a_clean_last_error_null():
    """The common case (no error on this pass): last_error is None, not stripped into True."""
    body = {"watcher": {"alive": True, "last_error": None, "last_error_at": None}}
    trimmed = RP._trim_health(body)
    assert trimmed["watcher"]["last_error"] is None


def test_derive_phone_state_priority_order():
    assert RP._derive_phone_state({"driver": {"connected": None}}) == "unknown"
    assert RP._derive_phone_state({"driver": {}}) == "unknown"
    assert RP._derive_phone_state({"driver": {"connected": False}}) == "disconnected"
    # disconnected wins even when the doctor also reports a stuck op
    assert RP._derive_phone_state({"driver": {"connected": False},
                                   "doctor": {"blocked_by_stuck_op": True}}) == "disconnected"
    assert RP._derive_phone_state({"driver": {"connected": True},
                                   "doctor": {"blocked_by_stuck_op": True}}) == "blocked"
    assert RP._derive_phone_state(
        {"driver": {"connected": True}, "doctor": {"blocked_by_stuck_op": False},
         "journal_recent": {"dirty_state_recovered": 1, "idle_dirty_recovered": 0}}) == "recovering"
    assert RP._derive_phone_state(
        {"driver": {"connected": True}, "doctor": {"blocked_by_stuck_op": False},
         "journal_recent": {"dirty_state_recovered": 0, "idle_dirty_recovered": 2}}) == "recovering"
    assert RP._derive_phone_state(
        {"driver": {"connected": True}, "doctor": {"blocked_by_stuck_op": False},
         "journal_recent": {"dirty_state_recovered": 0, "idle_dirty_recovered": 0}}) == "ready"


# --- TASK-283.7: check_watcher_alarm's new snapshot_writer side effect ---------------------------

def test_check_watcher_alarm_writes_an_ok_snapshot_on_a_healthy_body(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.health = lambda: (_health_body(watcher_last_ok_at=_ago(1)), 0.01)
    relay.tunnel.up = lambda: True
    calls = []
    relay.snapshot_writer = lambda **kw: calls.append(kw)
    relay.check_watcher_alarm()
    assert len(calls) == 1
    assert calls[0]["ok"] is True
    assert calls[0]["tunnel_up"] is True
    assert calls[0]["phone_state"] == "unknown"   # _health_body carries no "rail"/"driver" key at all
    assert calls[0]["health"]["watcher"]["alive"] is True


def test_check_watcher_alarm_writes_a_failed_snapshot_when_health_itself_fails(cursor):
    relay = FakeRelay(cursor, [], [])

    def broken_health():
        raise RP.RelayError("executor health is 500: {}")

    relay.health = broken_health
    relay.tunnel.up = lambda: False
    calls = []
    relay.snapshot_writer = lambda **kw: calls.append(kw)
    relay.check_watcher_alarm()
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert calls[0]["tunnel_up"] is False
    assert calls[0]["error_code"] == "RelayError"
    assert calls[0]["health"] is None


def test_a_broken_snapshot_writer_never_takes_down_check_watcher_alarm(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.health = lambda: (_health_body(watcher_last_ok_at=_ago(1)), 0.01)
    lines = []
    relay.log = lines.append

    def _boom(**kw):
        raise RuntimeError("disk full")

    relay.snapshot_writer = _boom
    problems = relay.check_watcher_alarm()   # must not raise
    assert problems == []
    assert any("disk full" in line for line in lines)


def test_check_watcher_alarm_writes_nothing_without_a_snapshot_writer(cursor):
    """Default None -- same opt-in discipline as sync_writer: every test above this section that
    never set snapshot_writer proved nothing by way of this, since _write_snapshot is a no-op."""
    relay = FakeRelay(cursor, [], [])
    relay.health = lambda: (_health_body(watcher_last_ok_at=_ago(1)), 0.01)
    assert relay.snapshot_writer is None
    relay.check_watcher_alarm()   # must not raise


# --- TASK-283.7: Relay.ops() -----------------------------------------------------------------------

def _plain_relay(cursor):
    relay = RP.Relay(host="nowhere", token="t", webhook_url="http://127.0.0.1:8502/x",
                     inbound_token="i", cursor=cursor, phone_number_id="p",
                     display_phone_number="+49", waba_id="w", log=lambda _m: None)
    relay.tunnel._port_open = lambda: True   # a forward is already up (borrowed) -- no real ssh
    return relay


def test_relay_ops_raises_ops_route_missing_on_404(cursor, monkeypatch):
    monkeypatch.setattr(RP, "http_json", lambda *a, **kw: (404, {"detail": "not found"}, 0.01))
    relay = _plain_relay(cursor)
    with pytest.raises(RP.OpsRouteMissing):
        relay.ops()


def test_relay_ops_raises_relay_error_on_any_other_bad_status(cursor, monkeypatch):
    monkeypatch.setattr(RP, "http_json", lambda *a, **kw: (500, {"detail": "boom"}, 0.01))
    relay = _plain_relay(cursor)
    with pytest.raises(RP.RelayError):
        relay.ops()


def test_relay_ops_builds_the_query_string(cursor, monkeypatch):
    seen = {}

    def fake_http_json(method, url, **kw):
        seen["url"] = url
        return 200, {"ok": True, "ops": [], "counts": {}, "next_after_position": None}, 0.01

    monkeypatch.setattr(RP, "http_json", fake_http_json)
    relay = _plain_relay(cursor)
    relay.ops(after_position=5, limit=10, active=True, ids=["a", "b"])
    assert "after_position=5" in seen["url"]
    assert "limit=10" in seen["url"]
    assert "active=1" in seen["url"]
    assert "ids=a,b" in seen["url"]


def test_relay_ops_with_no_arguments_builds_a_bare_query(cursor, monkeypatch):
    seen = {}

    def fake_http_json(method, url, **kw):
        seen["url"] = url
        return 200, {"ok": True, "ops": [], "counts": {}, "next_after_position": None}, 0.01

    monkeypatch.setattr(RP, "http_json", fake_http_json)
    relay = _plain_relay(cursor)
    relay.ops()
    assert seen["url"].endswith("/v1/ops")


def test_relay_ops_passes_its_own_shorter_timeout_not_the_default_30s(cursor, monkeypatch):
    seen = {}

    def fake_http_json(method, url, **kw):
        seen["timeout"] = kw.get("timeout")
        return 200, {"ok": True, "ops": [], "counts": {}, "next_after_position": None}, 0.01

    monkeypatch.setattr(RP, "http_json", fake_http_json)
    relay = _plain_relay(cursor)
    relay.ops()
    assert seen["timeout"] == RP.OPS_TIMEOUT_SEC
    assert seen["timeout"] != 30.0   # executor()'s own default, review finding 8 (MINOR)


def test_relay_ops_treats_an_old_bridges_400_invalid_request_as_route_missing(cursor, monkeypatch):
    """Review finding 4 (MAJOR): an executor build that predates /v1/ops does not actually answer
    404 for it -- bridge/server.py's own catch-all for an unmatched path (``_no_route``) answers
    400 invalid_request with the EXACT message "no route /v1/ops" (NIT-3, 10-05 review narrowed the
    fold to this exact message, not every 400 invalid_request -- see the next two tests), so it is
    folded into OpsRouteMissing exactly like the 404 case."""
    monkeypatch.setattr(RP, "http_json",
                        lambda *a, **kw: (400, {"error": {"code": "invalid_request",
                                                          "message": "no route /v1/ops"}}, 0.01))
    relay = _plain_relay(cursor)
    with pytest.raises(RP.OpsRouteMissing):
        relay.ops()


def test_relay_ops_a_400_with_some_other_code_is_still_a_plain_relay_error(cursor, monkeypatch):
    """The 400-is-route-missing fold is specifically for invalid_request -- a 400 this module's own
    well-formed query could never trigger for a genuinely different reason must still stop the
    drain loop as a real RelayError, not be swallowed as "route missing"."""
    monkeypatch.setattr(RP, "http_json",
                        lambda *a, **kw: (400, {"error": {"code": "something_else"}}, 0.01))
    relay = _plain_relay(cursor)
    with pytest.raises(RP.RelayError):
        relay.ops()


def test_relay_ops_a_400_invalid_request_with_a_different_message_is_a_real_error(cursor, monkeypatch):
    """NIT-3, 10-05 review: the OLD fold matched ANY 400 invalid_request, regardless of message --
    too broad, since this module could in principle build a malformed query (a real bug) that also
    comes back as a 400 invalid_request with some OTHER message, and that must not be silently
    read as "the route doesn't exist yet". Only the exact _no_route message for THIS path counts."""
    monkeypatch.setattr(RP, "http_json",
                        lambda *a, **kw: (400, {"error": {"code": "invalid_request",
                                                          "message": "after_position must be an integer"}}, 0.01))
    relay = _plain_relay(cursor)
    with pytest.raises(RP.RelayError):
        relay.ops()


# --- TASK-283.7: Relay.mirror_ops() -----------------------------------------------------------------

class FakeOpsMirror:
    """Same shape as the production _OpsMirror (max_position/open_ids/write), backed by a plain
    dict instead of app.wa.store -- so these tests never touch a database."""

    def __init__(self, seed=()):
        self.rows = {op["op_id"]: op for op in seed}

    def max_position(self):
        return max((op["position"] for op in self.rows.values()), default=0)

    def open_ids(self):
        return [op_id for op_id, op in self.rows.items() if op["state"] not in ("done", "failed")]

    def write(self, ops):
        for op in ops:
            self.rows[op["op_id"]] = op

    def reset(self):
        self.rows = {}


def _mirror_op(op_id, position, *, state="queued"):
    return {"op_id": op_id, "position": position, "kind": "send", "origin": "luna", "state": state,
            "priority": 0, "created_at": _ago(10), "started_at": None, "finished_at": None,
            "resolved_at": None, "budget_sec": 60, "phone": "+491700000099",
            "error_code": None, "error_text": None}


def test_mirror_ops_is_a_no_op_without_a_mirror_writer(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.ops = lambda **kw: (_ for _ in ()).throw(AssertionError("ops() must not be called"))
    relay.mirror_ops()   # must not raise, and must not even call ops()


def test_mirror_ops_pages_after_position_until_next_after_position_is_null(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []   # new ops WILL appear -- keep this test off the real one
    mirror = FakeOpsMirror()
    relay.mirror_writer = mirror
    pages = [
        {"ops": [_mirror_op("a", 1), _mirror_op("b", 2)], "next_after_position": 2},
        {"ops": [_mirror_op("c", 3)], "next_after_position": None},
    ]
    position_calls = []

    def fake_ops(*, after_position=None, limit=None, active=None, ids=None):
        if active:
            return {"ops": []}
        if ids is not None:
            return {"ops": []}
        position_calls.append(after_position)
        return pages.pop(0)

    relay.ops = fake_ops
    relay.mirror_ops()
    assert set(mirror.rows) == {"a", "b", "c"}
    assert position_calls == [0, 2]


def test_mirror_ops_refreshes_open_ids_and_active_on_top_of_the_position_page(cursor):
    relay = FakeRelay(cursor, [], [])
    mirror = FakeOpsMirror(seed=[_mirror_op("z", 9, state="running")])
    relay.mirror_writer = mirror
    seen_ids = []

    def fake_ops(*, after_position=None, limit=None, active=None, ids=None):
        if ids is not None:
            seen_ids.append(ids)
            return {"ops": [_mirror_op("z", 9, state="done")]}
        if active:
            return {"ops": [_mirror_op("z", 9, state="done")]}
        return {"ops": [], "next_after_position": None}

    relay.ops = fake_ops
    relay.mirror_ops()
    assert seen_ids == [["z"]]
    assert mirror.rows["z"]["state"] == "done"


def test_mirror_ops_tolerates_a_missing_v1_ops_route(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.mirror_writer = FakeOpsMirror()
    lines = []
    relay.log = lines.append
    relay.ops = lambda **kw: (_ for _ in ()).throw(RP.OpsRouteMissing("no /v1/ops on this executor"))
    relay.mirror_ops()   # must not raise
    assert any("no /v1/ops" in line for line in lines)


def test_mirror_ops_tolerates_any_other_relay_error_too(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.mirror_writer = FakeOpsMirror()
    lines = []
    relay.log = lines.append
    relay.ops = lambda **kw: (_ for _ in ()).throw(RP.RelayError("executor ops is 500: {}"))
    relay.mirror_ops()   # must not raise
    assert any("ops mirror failed" in line for line in lines)


def test_mirror_ops_logs_other_relay_errors_only_on_a_state_change(cursor):
    """NIT-2, 10-05 review: a persistent 500 used to log every single ~3s pass (~1200 lines/hour at
    that cadence). Only the first failure, and a later CHANGE of error type, should log -- the same
    discipline OpsRouteMissing already had (review finding 4)."""
    relay = FakeRelay(cursor, [], [])
    relay.mirror_writer = FakeOpsMirror()
    lines = []
    relay.log = lines.append
    relay.ops = lambda **kw: (_ for _ in ()).throw(RP.RelayError("executor ops is 500: {}"))

    relay.mirror_ops()
    relay.mirror_ops()
    relay.mirror_ops()
    assert sum("ops mirror failed" in line for line in lines) == 1

    # a DIFFERENT error type logs again (it is new information, not the same stuck failure)
    relay.ops = lambda **kw: (_ for _ in ()).throw(RP.RelayError("executor ops is 503: {}"))
    relay.mirror_ops()
    assert sum("ops mirror failed" in line for line in lines) == 2

    # and recovering logs once, not on every later healthy pass
    relay.ops = lambda **kw: {"ops": [], "next_after_position": None}
    relay.mirror_ops()
    relay.mirror_ops()
    assert sum("recovered" in line for line in lines) == 1


def test_mirror_ops_triggers_an_immediate_alarm_check_when_new_ops_appeared(cursor):
    relay = FakeRelay(cursor, [], [])
    mirror = FakeOpsMirror()
    relay.mirror_writer = mirror
    checks = []
    relay.check_watcher_alarm = lambda: checks.append(1)

    def fake_ops(*, after_position=None, limit=None, active=None, ids=None):
        if active or ids is not None:
            return {"ops": []}
        return {"ops": [_mirror_op("a", 1)], "next_after_position": None}

    relay.ops = fake_ops
    relay.mirror_ops()
    assert checks == [1]


def test_mirror_ops_does_not_trigger_an_alarm_check_when_nothing_new_appeared(cursor):
    relay = FakeRelay(cursor, [], [])
    mirror = FakeOpsMirror(seed=[_mirror_op("z", 1, state="running")])
    relay.mirror_writer = mirror
    checks = []
    relay.check_watcher_alarm = lambda: checks.append(1)
    relay.ops = lambda **kw: {"ops": [], "next_after_position": None}
    relay.mirror_ops()
    assert checks == []


def test_relay_run_calls_mirror_ops_every_cycle(cursor, monkeypatch):
    monkeypatch.setattr(RP.time, "sleep", lambda _seconds: None)
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    calls = []
    relay.mirror_ops = lambda: calls.append(1)
    relay.run(stop=_StopAfterOnePass())
    assert calls == [1]


def test_relay_run_skips_mirror_ops_on_a_cycle_whose_drain_failed(cursor, monkeypatch):
    """Review finding 8 (MINOR): drain_once's own failure already closed the tunnel and is backing
    off -- calling mirror_ops() right after would reopen it immediately for a second, unrelated
    purpose, adding a whole extra ssh attempt on top of the failure the drain loop is already
    backing off from."""
    monkeypatch.setattr(RP.time, "sleep", lambda _seconds: None)
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    relay.fetch = lambda: (_ for _ in ()).throw(RP.RelayError("webhook answered 502"))
    calls = []
    relay.mirror_ops = lambda: calls.append(1)
    relay.run(stop=_StopAfterOnePass())
    assert calls == []


def test_mirror_ops_defers_an_active_row_above_this_pass_high_water_then_catches_up(cursor):
    """Review finding 3 (MAJOR), its own A/B repro: op A is enqueued right after this pass's
    position page and finishes so fast it is already terminal (and so invisible to the active=1
    fetch too) by the time this pass reads it; op B is enqueued after A and is still queued when
    active=1 is read. Writing B this pass (position above the page's own high-water mark) would
    raise the mirror's max position straight past A, skipping it forever -- the fix defers B
    instead, so the mirror's max position does not move past it. The NEXT pass's position page
    (which starts from that same, unmoved max position) then picks up both A and B by position,
    regardless of state -- nothing lost."""
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    mirror = FakeOpsMirror()
    relay.mirror_writer = mirror
    pass_n = {"n": 1}

    def fake_ops(*, after_position=None, limit=None, active=None, ids=None):
        if ids is not None:
            return {"ops": []}
        if active:
            return {"ops": [_mirror_op("B", 105, state="queued")]}
        if pass_n["n"] == 1:
            return {"ops": [_mirror_op("p1", 100, state="done")], "next_after_position": None}
        return {"ops": [_mirror_op("A", 102, state="done"), _mirror_op("B", 105, state="queued")],
                "next_after_position": None}

    relay.ops = fake_ops

    relay.mirror_ops()
    assert set(mirror.rows) == {"p1"}            # B deferred -- not written this pass
    assert mirror.max_position() == 100          # the cursor must not jump straight to B

    pass_n["n"] = 2
    relay.mirror_ops()
    assert set(mirror.rows) == {"p1", "A", "B"}  # the next pass's position page catches both


def test_mirror_ops_writes_an_ok_heartbeat_on_a_clean_pass(cursor):
    """Review finding 4 (MAJOR): a heartbeat on every pass that reaches a real outcome, so a caller
    can tell a frozen mirror from a healthy, idle one (queue.as_of / OpsEnvelope.mirrored_at)."""
    relay = FakeRelay(cursor, [], [])
    relay.check_watcher_alarm = lambda: []
    relay.mirror_writer = FakeOpsMirror()
    relay.ops = lambda **kw: {"ops": [], "next_after_position": None}
    calls = []
    relay.mirror_sync_writer = lambda ok, error_code=None: calls.append((ok, error_code))
    relay.mirror_ops()
    assert calls == [(True, None)]


def test_mirror_ops_writes_a_failed_heartbeat_with_the_route_missing_code(cursor):
    relay = FakeRelay(cursor, [], [])
    relay.mirror_writer = FakeOpsMirror()
    relay.ops = lambda **kw: (_ for _ in ()).throw(RP.OpsRouteMissing("no /v1/ops on this executor"))
    calls = []
    relay.mirror_sync_writer = lambda ok, error_code=None: calls.append((ok, error_code))
    relay.mirror_ops()
    assert calls == [(False, RP.OpsRouteMissing.code)]


def test_mirror_ops_logs_route_missing_once_on_state_change_not_every_pass(cursor):
    """Review finding 4 (MAJOR): this used to log every single pass, a line every ~3s forever at
    the drain cadence -- now only on the transition into, and back out of, "route missing"."""
    relay = FakeRelay(cursor, [], [])
    mirror = FakeOpsMirror()
    relay.mirror_writer = mirror
    lines = []
    relay.log = lines.append
    relay.ops = lambda **kw: (_ for _ in ()).throw(RP.OpsRouteMissing("no /v1/ops on this executor"))

    relay.mirror_ops()
    relay.mirror_ops()
    relay.mirror_ops()
    missing_lines = [l for l in lines if "no /v1/ops" in l]
    assert len(missing_lines) == 1   # not three

    relay.ops = lambda **kw: {"ops": [], "next_after_position": None}
    relay.mirror_ops()
    resumed_lines = [l for l in lines if "reachable again" in l]
    assert len(resumed_lines) == 1


def test_mirror_ops_detects_a_ledger_position_reset_and_resets_the_mirror_once(cursor):
    """Review finding 11 (NIT): the one false-positive-free signal available through this
    interface -- every op_id this mirror still has open vanishing from the bridge AT ONCE (never
    just one stale straggler retention-swept on its own) -- wipes the mirror and reports it as a
    distinct error_code, rather than staying permanently blind with stale, now-wrong positions."""
    relay = FakeRelay(cursor, [], [])
    mirror = FakeOpsMirror(seed=[_mirror_op("z", 9, state="running")])
    relay.mirror_writer = mirror
    lines = []
    relay.log = lines.append
    heartbeats = []
    relay.mirror_sync_writer = lambda ok, error_code=None: heartbeats.append((ok, error_code))

    def fake_ops(*, after_position=None, limit=None, active=None, ids=None):
        if ids is not None:
            return {"ops": []}             # every previously-open id vanished at once
        if active:
            return {"ops": []}
        return {"ops": [], "next_after_position": None}

    relay.ops = fake_ops
    relay.mirror_ops()
    assert mirror.rows == {}               # wiped
    assert heartbeats == [(False, "ledger_position_reset")]
    assert any("ledger position reset" in l for l in lines)

    # vacuously false right after the wipe (open_ids is now empty) -- no second wipe, no repeat log
    lines.clear()
    relay.mirror_ops()
    assert not any("ledger position reset" in l for l in lines)


def test_production_relay_wires_the_real_mirror_and_snapshot_writers(monkeypatch):
    for name, where in RP.REQUIRED_ENV.items():
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("WA_BRIDGE_RELAY_STATE", "/dev/shm/pflege-wa-test-relay-cursor-283-7.sqlite")
    relay = RP.from_env()
    try:
        assert isinstance(relay.mirror_writer, RP._OpsMirror)
        assert relay.snapshot_writer is RP._write_rail_snapshot
    finally:
        relay.close()
        relay.cursor.close()
