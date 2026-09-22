"""Offline proof for our own adb driver (TASK-142). No adb binary, no handset, no ssh.

The driver is the layer that cannot be integration-tested without messaging a real person, so what
is testable about it is tested here: the parsing, the thread guard, the keyboard restore and the
refusal to call an unmatched bubble a send. A scripted screen stands in for the phone.
"""
import pytest

from bridge import adb_driver as AD
from bridge import driver as D

PHONE = "+4915216678689"
OTHER = "+491701112233"
WIDTH = 1080


def node(rid, text="", *, bounds=(57, 100, 900, 180), desc="", clickable=False, pkg="com.whatsapp"):
    return AD.Node(cls="android.widget.TextView", rid=f"com.whatsapp:id/{rid}", text=text,
                   desc=desc, bounds=bounds, clickable=clickable, pkg=pkg)


def incoming(text, clock="10:04", y=300):
    return [node("message_text", text, bounds=(57, y, 905, y + 80)),
            node("date", clock, bounds=(700, y + 82, 905, y + 110))]


def outgoing(text, clock="10:05", tick="Gesendet", y=500):
    return [node("message_text", text, bounds=(300, y, 1017, y + 80)),
            node("date", clock, bounds=(800, y + 82, 1017, y + 110)),
            node("status", desc=tick, bounds=(960, y + 82, 1017, y + 110))]


def conversation(header, bubbles=(), composer=""):
    screen = [node("conversation_contact_name", header, bounds=(150, 60, 800, 130)),
              node("entry", composer, bounds=(60, 2100, 900, 2200), clickable=True),
              node("send", bounds=(920, 2100, 1020, 2200), clickable=True)]
    for bubble in bubbles:
        screen.extend(bubble)
    return screen


class ScriptedAdb(AD.Adb):
    """A phone made of a list of screens. Every shell command is recorded, nothing is executed."""

    def __init__(self, screens, *, ime="com.touchtype.swiftkey/com.touchtype.KeyboardService",
                 contacts=""):
        super().__init__(AD.SERIAL, adb="/nonexistent/adb")
        self.screens = [list(s) for s in screens] or [[]]
        self.ime = ime
        self.contacts = contacts
        self.commands = []
        self.typed = []
        self.taps = []
        self.keys = []
        self.ime_sets = []

    # --- what the driver asks of a phone -------------------------------------------------------
    def connected(self):
        return True

    def awake(self):
        return True

    def wake(self):
        return None

    def focus(self):
        return "com.whatsapp/.home.ui.HomeActivity"

    def dump(self, *, tries=3, required=True):
        nodes = self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)
        if not nodes and required:
            raise AD.AdbUnavailable(f"uiautomator returned nothing readable {tries} times in a row")
        return nodes

    def shell(self, cmd, *, timeout=40):
        self.commands.append(cmd)
        if "content query" in cmd:
            return self.contacts
        if "ime list" in cmd:
            return AD.ADB_IME
        if "default_input_method" in cmd:
            return self.ime
        return ""

    def tap(self, x, y):
        self.taps.append((x, y))

    def key(self, name):
        self.keys.append(name)

    def current_ime(self):
        return self.ime

    def set_ime(self, ime):
        self.ime = ime
        self.ime_sets.append(ime)
        return True

    def type_verbatim(self, chunk):
        self.typed.append(chunk)

    def clear_composer(self):
        self.typed.append("<clear>")

    def screenshot(self, path):
        return str(path)


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """No wall clock in here. ``sleep`` is a no-op and ``monotonic`` runs fast, so a driver loop
    that waits 12 s for a screen that never comes fails in milliseconds and still fails for the
    real reason -- the deadline -- rather than because a test shortened it."""
    monkeypatch.setattr(AD.time, "sleep", lambda _s: None)
    ticker = iter(range(0, 10_000_000, 5))
    monkeypatch.setattr(AD.time, "monotonic", lambda: float(next(ticker)))


def build(screens, **kw):
    adb = ScriptedAdb(screens, **kw)
    return AD.AdbDriver(shots_dir="/tmp/pflege-bridge-test-shots", adb=adb), adb


# --- the pinned handset -------------------------------------------------------------------------
def test_the_chatgpt_farm_handset_is_refused_before_adb_is_called():
    with pytest.raises(D.DriverError) as caught:
        AD.Adb("L2N4C19B14054035")
    assert "ChatGPT farm" in str(caught.value)


def test_any_other_serial_is_refused():
    with pytest.raises(D.DriverError):
        AD.Adb("SOMEOTHERSERIAL")


# --- parsing ------------------------------------------------------------------------------------
def test_ui_xml_parses_into_nodes():
    xml = ('<?xml version="1.0"?><hierarchy><node class="android.widget.TextView" '
           'resource-id="com.whatsapp:id/message_text" text="Hallo" content-desc="" '
           'bounds="[57,300][905,380]" clickable="false" package="com.whatsapp"/></hierarchy>')
    nodes = AD.parse_ui_xml(xml)
    assert [(n.short_rid, n.text, n.bounds) for n in nodes] == \
        [("message_text", "Hallo", (57, 300, 905, 380))]


def test_a_truncated_dump_parses_to_nothing_rather_than_half_a_screen():
    assert AD.parse_ui_xml("<?xml version=\"1.0\"?><hierarchy><node bounds=\"[0,0]") == []
    assert AD.parse_ui_xml("device offline") == []


def test_contacts_rows_become_number_and_name_pairs():
    rows = "Row: 0 display_name=Ivan Kotelnikov, data1=+49 170 1112233\nRow: 1 display_name=x, data1=7\n"
    assert AD.parse_contacts(rows) == [("491701112233", "Ivan Kotelnikov")]


def test_bubble_direction_comes_from_the_x_edge_and_the_tick_from_the_content_desc():
    driver, _ = build([conversation("+49 152 1667 8689",
                                    [incoming("Hallo"), outgoing("Guten Tag", tick="Zugestellt")])])
    bubbles = driver.read_bubbles()
    assert [(b.direction, b.text, b.clock, b.tick) for b in bubbles] == \
        [("in", "Hallo", "10:04", ""), ("out", "Guten Tag", "10:05", "Zugestellt")]
    assert bubbles[1].tick_state == "delivered"


# --- the thread guard ---------------------------------------------------------------------------
def test_a_number_header_for_our_number_opens():
    driver, adb = build([conversation("+49 152 1667 8689")])
    assert driver.open_chat(PHONE) == "+49 152 1667 8689"
    assert any("smsto:" in c and "com.whatsapp" in c for c in adb.commands)


def test_a_number_header_for_someone_else_refuses_and_types_nothing():
    driver, adb = build([conversation("+49 170 111 2233")])
    with pytest.raises(D.DriverError) as caught:
        driver.open_chat(PHONE)
    assert "wrong thread" in str(caught.value)
    assert adb.typed == []


def test_a_name_header_the_address_book_does_not_tie_to_this_number_refuses(monkeypatch):
    """The hole in the reference implementation: a name has no digits, so its guard was skipped and
    it would have typed into whichever chat happened to be on screen."""
    driver, adb = build([conversation("Oma")], contacts="Row: 0 display_name=Oma, data1=+49 30 1234567\n")
    with pytest.raises(D.DriverError) as caught:
        driver.open_chat(PHONE)
    assert "the address book does not tie" in str(caught.value)
    assert adb.typed == []


def test_a_name_header_the_address_book_does_tie_to_this_number_opens():
    driver, _ = build([conversation("Ivan")],
                      contacts="Row: 0 display_name=Ivan, data1=+49 1521 667 8689\n")
    assert driver.open_chat(PHONE) == "Ivan"


def test_a_number_too_short_to_identify_a_thread_refuses():
    driver, adb = build([conversation("+49")])
    with pytest.raises(D.DriverError):
        driver.open_chat("+4915")
    assert adb.commands == []


# --- typing, and the keyboard that is borrowed property -------------------------------------------
def test_the_previous_ime_is_restored_after_a_successful_send():
    screens = [conversation("+49 152 1667 8689"),                 # open_chat
               conversation("+49 152 1667 8689"),                 # send_bubble: composer
               conversation("+49 152 1667 8689"),                 # send_bubble: send button
               conversation("+49 152 1667 8689", [outgoing("Guten Tag")])]
    driver, adb = build(screens)
    driver.open_chat(PHONE)
    bubble = driver.send_bubble("Guten Tag")
    assert bubble.tick == "Gesendet"
    assert adb.ime_sets == [AD.ADB_IME, "com.touchtype.swiftkey/com.touchtype.KeyboardService"]
    assert adb.ime == "com.touchtype.swiftkey/com.touchtype.KeyboardService"
    assert "".join(adb.typed) == "Guten Tag"


def test_the_previous_ime_is_restored_even_when_the_send_button_is_missing():
    no_button = [n for n in conversation("+49 152 1667 8689") if n.short_rid != "send"]
    driver, adb = build([conversation("+49 152 1667 8689"), conversation("+49 152 1667 8689"),
                         no_button])
    driver.open_chat(PHONE)
    with pytest.raises(D.DriverError):
        driver.send_bubble("Guten Tag")
    assert adb.ime == "com.touchtype.swiftkey/com.touchtype.KeyboardService"


def test_a_leftover_composer_is_cleared_before_typing():
    screens = [conversation("+49 152 1667 8689"),
               conversation("+49 152 1667 8689", composer="halber Satz"),
               conversation("+49 152 1667 8689"),
               conversation("+49 152 1667 8689", [outgoing("Guten Tag")])]
    driver, adb = build(screens)
    driver.open_chat(PHONE)
    driver.send_bubble("Guten Tag")
    assert adb.typed[0] == "<clear>"


def test_sending_without_a_verified_open_chat_refuses():
    driver, adb = build([conversation("+49 152 1667 8689")])
    with pytest.raises(D.DriverError) as caught:
        driver.send_bubble("Guten Tag")
    assert "verified open chat" in str(caught.value)
    assert adb.typed == []


# --- the invariant: an unmatched bubble is an error, never a send ----------------------------------
def test_a_bubble_that_never_appears_raises_instead_of_reporting_unverified(monkeypatch):
    """The reference implementation returns Bubble(status='unverified') here and its caller stores
    that as sent. It fired on 2 of 23 live sends. Here it is a DriverError, which the executor
    turns into a 504 that is never auto-resent."""
    monkeypatch.setattr(AD, "BUBBLE_APPEAR_SEC", 0.0)
    driver, adb = build([conversation("+49 152 1667 8689")])
    driver.open_chat(PHONE)
    with pytest.raises(D.DriverError) as caught:
        driver.send_bubble("Guten Tag")
    assert "was not on the thread" in str(caught.value)
    assert D.UNVERIFIED not in str(caught.value)
    assert adb.ime == "com.touchtype.swiftkey/com.touchtype.KeyboardService"


def test_the_read_back_matches_on_whitespace_normalised_text():
    """WhatsApp redraws a wrapped line with different whitespace than we typed it."""
    screens = [conversation("+49 152 1667 8689"), conversation("+49 152 1667 8689"),
               conversation("+49 152 1667 8689"),
               conversation("+49 152 1667 8689", [outgoing("Guten  Tag\nIvan")])]
    driver, _ = build(screens)
    driver.open_chat(PHONE)
    assert driver.send_bubble("Guten Tag Ivan").direction == "out"


def test_the_keyboard_is_hidden_once_before_giving_up_on_the_bubble(monkeypatch):
    """A fresh bubble is drawn at the bottom of the list, which is exactly where the IME covers."""
    monkeypatch.setattr(AD, "BUBBLE_APPEAR_SEC", 0.0)
    driver, adb = build([conversation("+49 152 1667 8689")])
    driver.open_chat(PHONE)
    with pytest.raises(D.DriverError):
        driver.send_bubble("Guten Tag")
    assert adb.keys.count("KEYCODE_BACK") == 1


# --- housekeeping ----------------------------------------------------------------------------------
def test_park_leaves_the_conversation_and_forgets_the_open_chat():
    driver, adb = build([conversation("+49 152 1667 8689")])
    driver.open_chat(PHONE)
    driver.park()
    assert driver._open_phone is None
    assert adb.keys[-1] == "KEYCODE_HOME"


def test_describe_names_our_own_file_and_no_third_party():
    driver, _ = build([[]])
    described = driver.describe()
    assert described["kind"] == "adb"
    assert described["serial"] == AD.SERIAL
    assert set(described["modules"]) == {"adb_driver.py"}


# --- TASK-146: the lies this driver used to be able to tell ------------------------------------
def test_an_identical_bubble_from_an_earlier_turn_is_not_proof_of_this_send(monkeypatch):
    """Several send bodies are constants on this rail (the media acknowledgement, the follow-up
    nudge), so "a bubble with this body is on the thread" was never evidence that OUR bubble is.
    The old bubble already carries a tick, so the executor wrote `sent` with a clock from the
    earlier turn -- exactly the class of lie this module exists to refuse."""
    monkeypatch.setattr(AD, "BUBBLE_APPEAR_SEC", 0.0)
    old = outgoing("Sind Sie noch da?", clock="09:12", tick="Gelesen", y=300)
    screens = [conversation("+49 152 1667 8689", [old])] * 6
    driver, _ = build(screens)
    driver.open_chat(PHONE)
    with pytest.raises(D.DriverError) as caught:
        driver.send_bubble("Sind Sie noch da?")
    assert "was not on the thread" in str(caught.value)


def test_a_second_identical_bubble_that_really_was_drawn_is_accepted():
    old = outgoing("Sind Sie noch da?", clock="09:12", tick="Gelesen", y=300)
    new = outgoing("Sind Sie noch da?", clock="10:31", tick="Gesendet", y=700)
    screens = [conversation("+49 152 1667 8689", [old]),        # open_chat
               conversation("+49 152 1667 8689", [old]),        # pre-send count
               conversation("+49 152 1667 8689", [old]),        # the send button dump
               conversation("+49 152 1667 8689", [old, new])]   # _verify
    driver, _ = build(screens)
    driver.open_chat(PHONE)
    bubble = driver.send_bubble("Sind Sie noch da?")
    assert (bubble.clock, bubble.tick) == ("10:31", "Gesendet")


def test_an_unreadable_dump_is_an_error_and_not_a_quiet_chat():
    """`uiautomator` failing and a chat with nothing in it both parsed to []. On the inbound path
    those two are a candidate's message being lost versus a quiet chat."""
    assert AD.parse_ui_xml("ERROR: could not get idle state.") == []
    driver, _ = build([[]])
    with pytest.raises(AD.AdbUnavailable):
        driver.read_bubbles()


def test_bubbles_above_a_day_separator_are_not_stamped_with_today():
    separator = [node("date", "GESTERN", bounds=(450, 200, 630, 250))]
    screen = conversation("+49 152 1667 8689",
                          [incoming("Ja", clock="20:40", y=100), separator,
                           incoming("Guten Morgen", clock="09:05", y=400)])
    driver, _ = build([screen])
    driver._open_phone = PHONE
    placed, unresolved = driver.read_open_thread(PHONE)
    assert [m.text for m in placed] == ["Guten Morgen"]
    assert len(unresolved) == 1


def test_a_day_separator_is_never_mistaken_for_a_bubble_clock():
    assert AD._is_clock("09:05") and AD._is_clock("9:05")
    assert not AD._is_clock("GESTERN") and not AD._is_clock("20. September")


def test_two_contacts_with_one_display_name_are_unresolvable_rather_than_a_coin_toss():
    """Guessing keyed the inbound on the wrong human, and every opt-out on this rail is keyed on
    the human rather than on the row."""
    rows = "Row: 0 display_name=Anna, data1=+49 170 1111111\nRow: 1 display_name=Anna, data1=+49 170 2222222\n"
    driver, _ = build([[]], contacts=rows)
    with pytest.raises(AD.I.Unresolvable):
        driver.resolve_counterparty("Anna")
    assert driver.resolve_counterparty("+49 170 1111111") == "+491701111111"


# --- the chat list and the two destructive verbs (TASK-147) -------------------------------------
# The screens below are the handset's own, read off L2N4C19B14054874 with WhatsApp 2.26.36.74 on
# 2026-09-21: the ids, the German labels and the geometry are what the phone drew, not a guess.
def chat_row(title, y, *, unread=0, stamp="13:36", preview=True):
    row = [AD.Node(cls="android.widget.LinearLayout", rid="com.whatsapp:id/contact_row_container",
                   text="", desc="", bounds=(0, y, 1080, y + 228), clickable=True,
                   pkg="com.whatsapp"),
           node("conversations_row_contact_name", title, bounds=(216, y + 45, 621, y + 110)),
           node("conversations_row_date", stamp, bounds=(933, y + 53, 1032, y + 102))]
    if preview:
        row.append(node("single_msg_tv", "letzte Nachricht", bounds=(281, y + 116, 960, y + 183)))
    if unread:
        plural = "Nachricht" if unread == 1 else "Nachrichten"
        row.append(node("conversations_row_message_count", desc=f"‎{unread} ungelesene {plural}",
                        bounds=(972, y + 119, 1032, y + 179)))
    return row


def chat_list(*rows, archive=True):
    screen = [node("fab", desc="Neuer Chat", bounds=(864, 1730, 1032, 1898), clickable=True),
              node("menuitem_overflow", desc="Weitere Optionen", bounds=(960, 93, 1080, 237),
                   clickable=True)]
    if archive:
        screen += [node("conversations_archive_header", bounds=(0, 453, 1080, 597), clickable=True),
                   node("archived_row", "Archiviert", bounds=(216, 453, 440, 597))]
    for row in rows:
        screen.extend(row)
    return screen


def selection_bar(*rows):
    return chat_list(*rows) + [
        node("action_mode_close_button", desc="Zurück", bounds=(0, 93, 168, 237), clickable=True),
        node("menuitem_conversations_delete", desc="Chat löschen", bounds=(528, 93, 672, 237),
             clickable=True),
        node("menuitem_conversations_archive", desc="Chat archivieren", bounds=(816, 93, 960, 237),
             clickable=True)]


def overflow_menu():
    return [node("title", "Als ungelesen markieren", bounds=(351, 457, 1020, 522)),
            node("title", "Chat sperren", bounds=(351, 745, 1020, 810)),
            node("title", "Chat leeren", bounds=(351, 1177, 1020, 1242))]


def clear_sheet(*, scope_checked=True, starred_checked=False):
    return [node("title", "Chat leeren", bounds=(216, 1109, 864, 1206)),
            AD.Node(cls="android.widget.RadioButton",
                    rid="com.whatsapp:id/dialog_clear_messages_all_text", text="Alle Nachrichten",
                    desc="", bounds=(30, 1278, 1032, 1422), clickable=True, pkg="com.whatsapp",
                    checked=scope_checked),
            AD.Node(cls="android.widget.RadioButton",
                    rid="com.whatsapp:id/dialog_clear_messages_media_text",
                    text="Nur Mediendateien", desc="", bounds=(30, 1422, 960, 1566),
                    clickable=True, pkg="com.whatsapp", checked=False),
            AD.Node(cls="android.widget.CheckBox",
                    rid="com.whatsapp:id/media_clear_chats_bottom_sheet_dialog_item_layout_checkbox",
                    text="", desc="Mit Stern markierte Nachrichten löschen",
                    bounds=(6, 1617, 150, 1761), clickable=True, pkg="com.whatsapp",
                    checked=starred_checked),
            node("primary_button", "CHAT LEEREN (2,0 MB)", bounds=(30, 1996, 1050, 2107),
                 clickable=True)]


def delete_dialog():
    return [node("alertTitle", "Diesen Chat löschen?", bounds=(171, 999, 867, 1096)),
            node("button2", "Abbrechen", bounds=(208, 1138, 538, 1282), clickable=True),
            node("button1", "Chat löschen", bounds=(538, 1138, 909, 1282), clickable=True)]


def test_the_chat_list_parses_into_rows_with_their_badges():
    screen = chat_list(chat_row("Ivan Test", 597, unread=1),
                       chat_row("+49 170 0000002", 825, unread=11, stamp="09:10"),
                       chat_row("Soak Rail", 1053, stamp="18.08.26", preview=False))
    rows = [row for _node, row in AD.parse_chat_rows(screen)]
    assert [(r.title, r.unread, r.stamp) for r in rows] == [
        ("Ivan Test", 1, "13:36"), ("+49 170 0000002", 11, "09:10"), ("Soak Rail", 0, "18.08.26")]
    assert [r.has_preview for r in rows] == [True, True, False]
    assert all(r.archived is False for r in rows)


def test_the_archive_folder_is_not_a_chat():
    """Its row carries no contact name, and deleting it would not mean anything anyway."""
    assert AD.parse_chat_rows(chat_list()) == []


def test_list_chats_reads_the_rows_off_the_handset():
    screen = chat_list(chat_row("Ivan Test", 597, unread=1), chat_row("Soak Rail", 825))
    driver, _adb = build([screen])
    assert [r.title for r in driver.list_chats(include_archived=False)] == ["Ivan Test", "Soak Rail"]


def test_a_chat_list_that_never_draws_is_a_loud_failure_not_an_empty_list():
    """An unreadable dump and a handset with no chats both parse to zero rows. Only one of them
    is an answer."""
    driver, _adb = build([[], [], [], [], []])
    with pytest.raises(D.DriverError) as caught:
        driver.list_chats()
    assert "would not come to the front" in str(caught.value)


def test_deleting_a_chat_walks_the_real_menu_and_confirms_it():
    rows = [chat_row("Ivan Test", 597), chat_row("Soak Rail", 825)]
    driver, adb = build([chat_list(*rows), chat_list(*rows), selection_bar(*rows),
                         chat_list(*rows) + delete_dialog(), chat_list(*rows)])

    out = driver.delete_chat_row("Soak Rail")

    assert out["confirmed_with"] == "Chat löschen"
    assert out["prompt"] == "Diesen Chat löschen?"
    # The long press landed on the row that was named, not on the one above it.
    assert any(f"motionevent DOWN 540 {825 + 114}" in c for c in adb.commands)
    assert (723, 1210) in [(x, y) for x, y in adb.taps][-1:]      # button1 of the dialog


def test_a_delete_whose_confirmation_never_appears_types_nothing_further():
    rows = [chat_row("Ivan Test", 597)]
    driver, adb = build([chat_list(*rows), chat_list(*rows), selection_bar(*rows),
                         chat_list(*rows), chat_list(*rows)])
    with pytest.raises(D.DriverError) as caught:
        driver.delete_chat_row("Ivan Test")
    assert "delete confirmation did not open" in str(caught.value)
    # One tap, and it is the action bar's delete button. Nothing was confirmed.
    assert len(adb.taps) == 1


def test_clearing_a_chat_chooses_all_messages_and_ticks_the_starred_box():
    rows = [chat_row("Ivan Test", 597)]
    sheet = chat_list(*rows) + clear_sheet(scope_checked=True, starred_checked=False)
    ticked = chat_list(*rows) + clear_sheet(scope_checked=True, starred_checked=True)
    driver, adb = build([chat_list(*rows), chat_list(*rows), selection_bar(*rows),
                         chat_list(*rows) + overflow_menu(), sheet, ticked, ticked,
                         chat_list(*rows)])

    out = driver.clear_chat_history("Ivan Test")

    assert out == {"menu_item": "Chat leeren", "scope": "all_messages",
                   "starred_included": True, "confirmed_with": "CHAT LEEREN (2,0 MB)"}


def test_a_clear_sheet_without_an_all_messages_scope_is_refused():
    """"Nur Mediendateien" would leave every word of the conversation on the phone and still look
    like it worked."""
    rows = [chat_row("Ivan Test", 597)]
    sheet = chat_list(*rows) + [n for n in clear_sheet() if n.short_rid != "dialog_clear_messages_all_text"]
    driver, _adb = build([chat_list(*rows), chat_list(*rows), selection_bar(*rows),
                          chat_list(*rows) + overflow_menu(), sheet, sheet, sheet, sheet])
    with pytest.raises(D.DriverError) as caught:
        driver.clear_chat_history("Ivan Test")
    assert "no 'all messages' scope" in str(caught.value)


def test_a_long_press_that_raises_no_selection_bar_stops_there():
    rows = [chat_row("Ivan Test", 597)]
    driver, _adb = build([chat_list(*rows)])
    with pytest.raises(D.DriverError) as caught:
        driver.delete_chat_row("Ivan Test")
    assert "no selection action bar" in str(caught.value)


def test_two_rows_with_one_title_refuse_before_anything_is_pressed():
    rows = [chat_row("Anna", 597), chat_row("Anna", 825, stamp="09:10")]
    driver, adb = build([chat_list(*rows)])
    with pytest.raises(D.DriverError) as caught:
        driver.delete_chat_row("Anna")
    assert "not guessable" in str(caught.value)
    assert not [c for c in adb.commands if "motionevent" in c]


def test_opening_a_chat_by_its_row_leaves_it_unsendable():
    """A chat opened by name has no number to check its header against, so it is readable only."""
    rows = [chat_row("Ivan Test", 597)]
    opened = conversation("Ivan Test", [incoming("Hallo")])
    driver, _adb = build([chat_list(*rows), chat_list(*rows), opened])
    assert driver.open_chat_row("Ivan Test") == "Ivan Test"
    assert driver._open_phone is None
    with pytest.raises(D.DriverError) as caught:
        driver.send_bubble("Guten Tag")
    assert "without a verified open chat" in str(caught.value)


def test_a_long_press_that_lands_on_a_neighbour_is_refused_before_the_menu():
    """The press is aimed with bounds from a dump taken a moment ago, and one inbound message moves
    a chat to the top and every row below it down by 228 px. Neither the selection action bar nor
    "Diesen Chat löschen?" names a chat, so nothing further down the menu could have noticed."""
    named = [chat_row("Ivan Test", 597), chat_row("Soak Rail", 825, stamp="18.08.26")]
    answered = [chat_row("Soak Rail", 597, unread=1), chat_row("Ivan Test", 825)]
    driver, adb = build([chat_list(*named), chat_list(*named), selection_bar(*answered),
                         selection_bar(*answered), chat_list(*answered)])

    with pytest.raises(D.DriverError) as caught:
        driver.delete_chat_row("Ivan Test")

    assert "did not land on the row that was named" in str(caught.value)
    assert any("motionevent DOWN 540 711" in c for c in adb.commands)   # the press was aimed there
    assert adb.taps == [], "nothing after the press: no delete button, no confirmation"
    assert adb.keys[-1] == "KEYCODE_BACK", "the selection is backed out, not left on the handset"


def test_two_chats_that_share_a_name_and_a_date_stay_two_rows_across_a_scroll():
    """(title, date) is what every row older than today shares -- "GESTERN", "18.08.26" -- so it
    cannot be a chat's identity. The pages are joined on their overlap instead, and the pair
    survives, which is what Operations._match_row refuses on."""
    day = "GESTERN"
    page1 = chat_list(chat_row("Anna", 597, stamp=day), chat_row("Boris", 825, stamp=day),
                      chat_row("Clara", 1053, stamp=day), archive=False)
    page2 = chat_list(chat_row("Boris", 597, stamp=day), chat_row("Clara", 825, stamp=day),
                      chat_row("Anna", 1053, stamp=day), archive=False)
    page3 = chat_list(chat_row("Clara", 597, stamp=day), chat_row("Anna", 825, stamp=day),
                      archive=False)
    driver, _adb = build([page1, page1, page2, page3, page3])

    assert [r.title for r in driver.list_chats()] == ["Anna", "Boris", "Clara", "Anna"]


def test_a_chat_list_whose_pages_do_not_overlap_is_a_loud_failure():
    """A swipe moves less than a screen. Pages that share nothing mean the list moved under us and
    which rows have already been read is a guess."""
    page1 = chat_list(chat_row("Anna", 597), chat_row("Boris", 825), archive=False)
    elsewhere = chat_list(chat_row("Xenia", 597), chat_row("Yara", 825), archive=False)
    driver, _adb = build([page1, page1, elsewhere, elsewhere])

    with pytest.raises(D.DriverError) as caught:
        driver.list_chats()
    assert "do not overlap" in str(caught.value)


def test_the_archive_is_walked_and_its_rows_come_back_marked_archived():
    """The archive folder is a second list behind its own header row, and ``archived`` on a row is
    what every destructive call has to assert before it can reach one."""
    main = chat_list(chat_row("Ivan Test", 597))
    archive = chat_list(chat_row("Alte Bewerberin", 597, stamp="18.08.26"), archive=False)
    # main list, its scroll-to-top and its scroll-through; then the same again for the second
    # visit that enters the archive; then the archive folder itself.
    driver, adb = build([main, main, main, main, main, archive, archive])

    rows = driver.list_chats(include_archived=True)

    assert [(r.title, r.archived) for r in rows] == [("Ivan Test", False),
                                                     ("Alte Bewerberin", True)]
    assert (540, 525) in adb.taps, "the archive header was tapped to get in"
    assert adb.keys[-1] == "KEYCODE_BACK", "and the list was left the way it was found"


def test_an_archive_folder_that_draws_no_rows_is_not_an_empty_archive():
    main = chat_list(chat_row("Ivan Test", 597))
    driver, _adb = build([main, main, main, main, main, chat_list(archive=False)])
    with pytest.raises(D.DriverError) as caught:
        driver.list_chats(include_archived=True)
    assert "drew no rows" in str(caught.value)


def test_an_adb_that_never_returns_is_a_driver_error_not_a_raw_timeout():
    """A TimeoutExpired escaping the driver reached the server's generic handler as a 500. On the
    send path a timeout typing a message is send_unconfirmed, and on the destructive path park()
    and the rescan both run after a conversation has already been destroyed."""
    adb = AD.Adb(AD.SERIAL, adb="/nonexistent/adb")

    def never_returns(*a, **kw):
        raise AD.subprocess.TimeoutExpired(cmd="adb", timeout=40)
    adb_run, AD.subprocess.run = AD.subprocess.run, never_returns
    try:
        with pytest.raises(D.DriverError) as caught:
            adb.shell("input text hallo")
        assert "did not return within" in str(caught.value)
        with pytest.raises(AD.AdbUnavailable):
            adb.connected()
    finally:
        AD.subprocess.run = adb_run
