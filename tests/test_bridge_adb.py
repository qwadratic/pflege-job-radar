"""Offline proof for our own adb driver (TASK-371). No adb binary, no handset, no ssh.

The driver is the layer that cannot be integration-tested without messaging a real person, so what
is testable about it is tested here: the parsing, the thread guard, the keyboard restore and the
refusal to call an unmatched bubble a send. A scripted screen stands in for the phone.
"""
import os
import time
from datetime import datetime, timezone

import pytest

from bridge import adb_driver as AD
from bridge import driver as D

PHONE = "+4915216678689"
OTHER = "+491701112233"
WIDTH = 1080


def node(rid, text="", *, bounds=(57, 100, 900, 180), desc="", clickable=False, pkg="com.whatsapp",
        cls="android.widget.TextView"):
    return AD.Node(cls=cls, rid=f"com.whatsapp:id/{rid}", text=text,
                   desc=desc, bounds=bounds, clickable=clickable, pkg=pkg)


def incoming(text, clock="10:04", y=300):
    return [node("message_text", text, bounds=(57, y, 905, y + 80)),
            node("date", clock, bounds=(700, y + 82, 905, y + 110))]


def outgoing(text, clock="10:05", tick="Gesendet", y=500):
    return [node("message_text", text, bounds=(300, y, 1017, y + 80)),
            node("date", clock, bounds=(800, y + 82, 1017, y + 110)),
            node("status", desc=tick, bounds=(960, y + 82, 1017, y + 110))]


def voice_note_incoming(duration, clock="11:02", y=300):
    """A voice-note bubble as this module's own docstring says it is verified to draw: a ``date``
    node (every bubble has one) and NO ``message_text`` node at all -- the duration is on some other
    node's content-desc, shape unverified (module docstring), stood in here as a play-button node."""
    return [node("audio_play_pause", desc=f"Sprachnachricht, {duration}",
                bounds=(57, y, 300, y + 80), clickable=True),
            node("date", clock, bounds=(700, y + 82, 905, y + 110))]


def document_incoming(filename, size_text, clock="11:05", y=500):
    return [node("document_name", filename, bounds=(57, y, 905, y + 40)),
            node("document_size", size_text, bounds=(57, y + 42, 400, y + 70)),
            node("date", clock, bounds=(700, y + 82, 905, y + 110))]


def conversation(header, bubbles=(), composer=""):
    screen = [node("conversation_contact_name", header, bounds=(150, 60, 800, 130)),
              node("entry", composer, bounds=(60, 2100, 900, 2200), clickable=True),
              node("send", bounds=(920, 2100, 1020, 2200), clickable=True)]
    for bubble in bubbles:
        screen.extend(bubble)
    return screen


class FakeProc:
    """Stands in for the ``Popen`` a real ``spawn_shell`` would hand back (TASK-228) -- nothing is
    ever really started in this offline lane."""

    def __init__(self):
        self.waited = 0

    def wait(self, timeout=None):
        self.waited += 1
        return 0


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
        # --- media (TASK-360) ---------------------------------------------------------------
        self.media_listing = ""     # what the find+stat shell command answers, scripted per test
        self.pulls = []             # (remote, local) pairs this test's driver was asked to pull
        self.fail_pull = None       # a remote path whose pull answers rc != 0
        # --- outbound media: photos (TASK-360 round 7) ----------------------------------------
        self.pushes = []            # (local, remote) pairs this test's driver was asked to push
        self.fail_push = None       # a local path whose push answers rc != 0
        # --- outbound media: one gallery message (TASK-360 round 7 gallery redesign) ----------
        self.device_date = "202609230200.00"   # what "date +%Y%m%d%H%M.%S" answers, scripted per test
        # --- debug capture (TASK-228) -----------------------------------------------------------
        self.spawned = []           # commands spawn_shell was asked to start, in call order
        self.downscaled = []        # paths downscale was asked to shrink, in call order
        self.next_proc = FakeProc()  # what spawn_shell hands back -- one test script sets a fresh
                                     # one per call if it needs to tell two recordings apart
        # --- PhoneDoctor's orphaned-recordings sweep (TASK-315 review point 10) -----------------
        # what the `find /sdcard ... -mmin +5` LISTING call answers -- empty by default, same
        # "script it or nothing happens" shape as media_listing above.
        self.orphaned_recording_listing = ""
        # --- PhoneDoctor's screen_size() (TASK-315 review point 3) ------------------------------
        self.wm_size_output = "Physical size: 1080x2260\n"

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
        if "find" in cmd and "stat" in cmd:
            return self.media_listing
        if cmd == "date +%Y%m%d%H%M.%S":
            return self.device_date
        if cmd.startswith("find /sdcard") and "-mmin +5" in cmd and "-delete" not in cmd:
            return self.orphaned_recording_listing
        if cmd == "wm size":
            return self.wm_size_output
        return ""

    def pull(self, remote, local, *, timeout=120):
        import pathlib
        import subprocess
        self.pulls.append((remote, local))
        if remote == self.fail_pull:
            return subprocess.CompletedProcess(["adb", "pull"], returncode=1,
                                               stdout="", stderr="remote object not found")
        pathlib.Path(local).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(local).write_bytes(b"scripted bytes for " + remote.encode())
        return subprocess.CompletedProcess(["adb", "pull"], returncode=0, stdout="1 file pulled",
                                           stderr="")

    def push(self, local, remote, *, timeout=120):
        import subprocess
        self.pushes.append((local, remote))
        if local == self.fail_push:
            return subprocess.CompletedProcess(["adb", "push"], returncode=1,
                                               stdout="", stderr="no such file or directory")
        return subprocess.CompletedProcess(["adb", "push"], returncode=0, stdout="1 file pushed",
                                           stderr="")

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

    # --- debug capture (TASK-228) -----------------------------------------------------------
    def spawn_shell(self, cmd):
        self.commands.append(cmd)
        self.spawned.append(cmd)
        return self.next_proc

    def downscale(self, path, *, max_dim=800):
        self.downscaled.append(str(path))


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


# --- media bubble evidence (TASK-360 round 6): reading past message_text ------------------------
def test_a_voice_note_bubble_with_no_message_text_node_is_still_found_by_its_date():
    """The bug this round exists to fix: _placed_bubbles anchors on message_text, which a voice
    note never draws. read_media_evidence anchors on the date node every bubble has instead."""
    driver, _ = build([conversation("+49 152 1667 8689", [voice_note_incoming("0:07")])])
    [entry] = driver.read_media_evidence()
    assert entry["clock"] == "11:02"
    assert any("0:07" in e for e in entry["evidence"])


def test_a_document_bubbles_filename_and_size_are_both_in_the_evidence_pool():
    driver, _ = build([conversation("+49 152 1667 8689",
                                    [document_incoming("Lebenslauf.pdf", "165 KB")])])
    [entry] = driver.read_media_evidence()
    assert "Lebenslauf.pdf" in entry["evidence"]
    assert "165 KB" in entry["evidence"]


def test_evidence_is_only_the_other_partys_bubbles_never_our_own():
    driver, _ = build([conversation("+49 152 1667 8689",
                                    [voice_note_incoming("0:07"), outgoing("Danke")])])
    entries = driver.read_media_evidence()
    assert len(entries) == 1 and entries[0]["clock"] == "11:02"


def test_two_media_bubbles_in_one_minute_are_two_separate_evidence_bands():
    driver, _ = build([conversation("+49 152 1667 8689", [
        document_incoming("Anna-CV.pdf", "165 KB", clock="12:00", y=300),
        document_incoming("Bernd-CV.pdf", "900 KB", clock="12:00", y=500)])])
    entries = driver.read_media_evidence()
    assert len(entries) == 2
    assert "Anna-CV.pdf" in entries[0]["evidence"] and "Anna-CV.pdf" not in entries[1]["evidence"]
    assert "Bernd-CV.pdf" in entries[1]["evidence"] and "Bernd-CV.pdf" not in entries[0]["evidence"]


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


# --- TASK-375: the lies this driver used to be able to tell ------------------------------------
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


# --- the cold-read day anchor (TASK-231): read_open_thread's derivation leans on our own just-sent
# bubble proving the bottom of the thread is today; a cold read (Operations.read_thread,
# Executor._read_evidence_for) has no such bubble and must not borrow that assumption.
def test_a_cold_read_places_only_what_is_below_a_divider_that_names_today():
    today = [node("date", "HEUTE", bounds=(450, 200, 630, 250))]
    screen = conversation("+49 152 1667 8689",
                          [incoming("Ja", clock="20:40", y=100), today,
                           incoming("Guten Morgen", clock="09:05", y=400)])
    driver, _ = build([screen])
    driver._open_phone = PHONE
    placed, unresolved = driver.read_cold_thread(PHONE)
    assert [m.text for m in placed] == ["Guten Morgen"]
    assert len(unresolved) == 1


def test_a_cold_read_does_not_read_gestern_as_today_the_way_the_anchored_read_would():
    """The defect this guards: read_open_thread treats the LOWEST divider on screen as the cut
    into today, whatever it says -- sound only because its own just-sent bubble proves the bottom
    of the thread really is today. A cold read has no such bubble, so a 'GESTERN' divider with
    nothing else on screen must not be read as 'so the bubble below it is today' -- unlike
    read_open_thread, which (wrongly, for a caller with no anchor) would place it."""
    separator = [node("date", "GESTERN", bounds=(450, 200, 630, 250))]
    screen = conversation("+49 152 1667 8689",
                          [incoming("Ja", clock="20:40", y=100), separator,
                           incoming("Guten Morgen", clock="09:05", y=400)])
    driver, _ = build([screen])
    driver._open_phone = PHONE
    placed, unresolved = driver.read_cold_thread(PHONE)
    assert placed == []
    assert len(unresolved) == 2


def test_a_cold_read_with_no_divider_at_all_mints_nothing_rather_than_guess():
    """The other half of the same defect: read_open_thread reads 'no divider visible' as 'no day
    change in the visible window, so all of it is today' -- true only because it knows the bottom
    bubble is seconds-old. A cold read cannot tell a thread that is entirely today's from one that
    is entirely some other day's without a divider to look at, so it places nothing -- the KNOWN
    RESIDUAL read_cold_thread's own docstring states rather than papers over."""
    screen = conversation("+49 152 1667 8689", [incoming("Guten Morgen", clock="09:05", y=400)])
    driver, _ = build([screen])
    driver._open_phone = PHONE
    placed, unresolved = driver.read_cold_thread(PHONE)
    assert placed == []
    assert len(unresolved) == 1


def test_two_contacts_with_one_display_name_are_unresolvable_rather_than_a_coin_toss():
    """Guessing keyed the inbound on the wrong human, and every opt-out on this rail is keyed on
    the human rather than on the row."""
    rows = "Row: 0 display_name=Anna, data1=+49 170 1111111\nRow: 1 display_name=Anna, data1=+49 170 2222222\n"
    driver, _ = build([[]], contacts=rows)
    with pytest.raises(AD.I.Unresolvable):
        driver.resolve_counterparty("Anna")
    assert driver.resolve_counterparty("+49 170 1111111") == "+491701111111"


# --- the idle self-check's own identity read (TASK-234): unlike open_chat, which is GIVEN the
# phone and only verifies the header against it, this reads whatever header is on screen with no
# expectation of its own and resolves it the same way a notification title is.
def test_current_chat_phone_resolves_an_unsaved_contacts_own_number_header():
    screen = conversation("+49 152 1667 8689")
    driver, _ = build([screen])
    assert driver.current_chat_phone() == PHONE


def test_current_chat_phone_resolves_a_saved_contacts_display_name_via_the_address_book():
    rows = "Row: 0 display_name=Anna, data1=+49 170 1111111\n"
    screen = conversation("Anna")
    driver, _ = build([screen], contacts=rows)
    assert driver.current_chat_phone() == "+491701111111"


def test_current_chat_phone_is_none_with_no_header_on_screen():
    """Reachable when the idle check's own ``focus()`` read raced a screen change -- a 'some
    Conversation is open' focus string proves nothing about what dump() catches an instant later."""
    driver, _ = build([chat_list()])
    assert driver.current_chat_phone() is None


def test_current_chat_phone_is_none_for_a_display_name_two_contacts_share():
    """Same refusal as everywhere else on this rail: two contacts sharing a display name is
    unresolvable, not a coin toss, so this has nothing sound to key a read against."""
    rows = "Row: 0 display_name=Anna, data1=+49 170 1111111\nRow: 1 display_name=Anna, data1=+49 170 2222222\n"
    screen = conversation("Anna")
    driver, _ = build([screen], contacts=rows)
    assert driver.current_chat_phone() is None


# --- the chat list and the two destructive verbs (TASK-376) -------------------------------------
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


# --- inbound media (TASK-360) --------------------------------------------------------------------
def test_list_media_parses_the_find_stat_listing_and_scopes_the_shell_command():
    driver, adb = build([[]])
    adb.media_listing = ("1234 1758534000 WhatsApp Documents/Lebenslauf.pdf\n"
                         "222 1758534010 WhatsApp Images/IMG-20260922-WA0007.jpg\n")
    listing = driver.list_media()
    assert listing == {"WhatsApp Documents/Lebenslauf.pdf": (1234, 1758534000),
                       "WhatsApp Images/IMG-20260922-WA0007.jpg": (222, 1758534010)}
    cmd = next(c for c in adb.commands if "find" in c)
    assert "WhatsApp Documents" in cmd and "WhatsApp Images" in cmd
    assert "*/Sent/*" in cmd, "outgoing copies of our own sends are excluded"


def test_pull_media_writes_the_file_and_returns_its_path(tmp_path):
    driver, adb = build([[]])
    dest = tmp_path / "incoming" / "a.pdf"
    got = driver.pull_media("WhatsApp Documents/Lebenslauf.pdf", dest)
    assert got == dest and dest.exists()
    assert adb.pulls == [("/sdcard/WhatsApp/Media/WhatsApp Documents/Lebenslauf.pdf", str(dest))]


def test_a_failed_pull_is_a_driver_error_and_writes_nothing_useful(tmp_path):
    driver, adb = build([[]])
    remote = "/sdcard/WhatsApp/Media/WhatsApp Documents/gone.pdf"
    adb.fail_pull = remote
    dest = tmp_path / "gone.pdf"
    with pytest.raises(D.DriverError) as caught:
        driver.pull_media("WhatsApp Documents/gone.pdf", dest)
    assert "adb pull" in str(caught.value)


# --- outbound media: photos (TASK-360 round 7, Ivan 2026-09-22) ---------------------------------
def share_picker(*row_texts):
    """WhatsApp's own share-target screen (ExternalShareAlias), one row per candidate text --
    verified live, this handset, 2026-09-22."""
    return [node("contactpicker_row_name", text, bounds=(216, 813 + i * 90, 608, 878 + i * 90))
           for i, text in enumerate(row_texts)]


def photo_compose(recipient_text):
    """The screen WhatsApp opens once exactly one share-picker row is tapped: a recipients line
    (what it read back) and the send button, verified live the same session."""
    return [node("recipients", recipient_text, bounds=(48, 2063, 457, 2107)),
           node("send", bounds=(912, 2020, 1056, 2107), clickable=True)]


def outgoing_photo(clock="10:06", tick="Gesendet", y=600):
    """An image bubble draws no message_text node at all (this module's own docstring) -- just a
    date and a status, banded the way _outgoing_bubble_count/_newest_outgoing_tick read them."""
    return [node("date", clock, bounds=(800, y + 82, 1017, y + 110)),
           node("status", desc=tick, bounds=(960, y + 82, 1017, y + 110))]


def test_send_photo_pushes_shares_and_verifies_a_new_outgoing_bubble(tmp_path):
    header = PHONE
    local = tmp_path / "clinic.jpg"
    local.write_bytes(b"not a real jpeg, this test only cares about the path")
    driver, adb = build([
        conversation(header, []),           # before-count read
        share_picker(header),               # WhatsApp's own picker, our row present
        photo_compose(header),              # compose screen, recipient line reads back correctly
        conversation(header, []),           # open_chat's own re-verification after sending
        conversation(header, [outgoing_photo()]),   # the new bubble, read back for the tick
    ])
    driver._open_phone = PHONE
    clock, tick = driver.send_photo(PHONE, str(local))
    assert (clock, tick) == ("10:06", "Gesendet")
    assert adb.pushes == [(str(local), f"/sdcard/Pictures/{AD.OUTBOUND_MEDIA_DIR}/clinic.jpg")]
    assert any("android.intent.action.SEND" in c and "image/jpeg" in c for c in adb.commands)


def test_send_photo_without_a_verified_open_chat_refuses(tmp_path):
    local = tmp_path / "clinic.jpg"
    local.write_bytes(b"x")
    driver, adb = build([[]])
    with pytest.raises(D.DriverError) as caught:
        driver.send_photo(PHONE, str(local))
    assert "without a verified open chat" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_photo_refuses_a_local_file_that_does_not_exist():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_photo(PHONE, "/tmp/definitely-not-here-9f8e7d.jpg")
    assert "does not exist" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_photo_refuses_when_no_picker_row_matches_the_phone(tmp_path):
    local = tmp_path / "clinic.jpg"
    local.write_bytes(b"x")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(OTHER),          # WhatsApp's own picker, but not our number
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_photo(PHONE, str(local))
    assert "no row in WhatsApp's own share picker matches" in str(caught.value)


def test_send_photo_refuses_two_ambiguous_picker_rows_rather_than_guess(tmp_path):
    local = tmp_path / "clinic.jpg"
    local.write_bytes(b"x")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(PHONE, PHONE),   # the same number drawn twice -- refuse, never guess
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_photo(PHONE, str(local))
    assert "refusing to guess" in str(caught.value)


def test_send_photo_refuses_when_the_compose_screens_recipient_line_disagrees(tmp_path):
    local = tmp_path / "clinic.jpg"
    local.write_bytes(b"x")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(PHONE),
        photo_compose(OTHER),   # picked the right row, but the compose screen reads back wrong
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_photo(PHONE, str(local))
    assert "does not read back" in str(caught.value)


# --- outbound media: one gallery message (TASK-360 round 7 gallery redesign, 2026-09-23) -------
def attach_button():
    """The paperclip on the conversation screen itself, verified live this handset, 2026-09-23."""
    return [node("input_attach_button", desc="Anhängen", bounds=(596, 2031, 740, 2107), clickable=True)]


def attach_menu():
    """The bottom-sheet a tap on the paperclip opens; only the row this driver needs is scripted."""
    return [node("pickfiletype_gallery_holder", text="Galerie", bounds=(0, 1357, 270, 1524),
                 clickable=True)]


def gallery_item(day, month_name, year, hh, mm, *, y=1129):
    """One 'Letzte' thumbnail, dated the way WhatsApp's own content-desc draws it -- verified live
    this handset, 2026-09-23: '‎Foto, Datum: 22. September 2026 23:52'."""
    text = f"Foto, Datum: {day}. {month_name} {year} {hh:02d}:{mm:02d}"
    return node("media_item_view", desc=text, bounds=(0, y, 265, y + 266), clickable=True)


def gallery_screen(items, *, selected_count=None, caption_text=""):
    """The gallery grid screen; once ``selected_count`` is set, the counter/caption/send nodes a
    real selection draws are included too (this scripted phone does not simulate a tap's effect on
    the screen, so a test that wants to read them back after "tapping" scripts them present from
    the start, same convention as ``photo_compose``)."""
    screen = list(items)
    if selected_count is not None:
        screen.append(node("send_media_counter", str(selected_count),
                           bounds=(1002, 2008, 1074, 2080)))
        screen.append(node("send_media_btn", desc=f"{selected_count} Medienobjekte senden",
                           bounds=(912, 2020, 1056, 2107), clickable=True))
        screen.append(node("caption", caption_text, desc="Bildunterschrift hinzufügen",
                           bounds=(228, 1980, 744, 2107), clickable=True,
                           cls="android.widget.EditText"))
    return screen


def sent_gallery_bubble_caption(text, *, y=446):
    """The SAME resource-id, drawn read-only under an already-sent gallery bubble further up the
    same conversation -- verified live, this handset, 2026-09-23 (module docstring in
    send_gallery): a TextView, not the picker's own EditText, and its bounds sit outside the
    picker sheet -- tapping it (picked blind by rid alone) lands on the conversation behind the
    sheet instead."""
    return node("caption", text, bounds=(251, y, 737, y + 83), cls="android.widget.TextView")


def test_send_gallery_pushes_stamps_selects_by_timestamp_captions_and_sends(tmp_path):
    header = PHONE
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    items = [gallery_item(23, "September", 2026, 1, 59), gallery_item(23, "September", 2026, 2, 0)]
    driver, adb = build([
        conversation(header, []),                                          # before-count
        conversation(header, []) + attach_button(),                        # attach button wait
        attach_menu(),                                                     # "Galerie" tapped
        gallery_screen(items, selected_count=2),                           # matched by timestamp
        gallery_screen(items, selected_count=2),                           # counter re-read
        gallery_screen(items, selected_count=2, caption_text="Test caption"),  # send button, post-caption
        conversation(header, []),                                          # open_chat re-verification
        conversation(header, [outgoing_photo(clock="10:06", tick="Gesendet")]),  # the new bubble
    ])
    adb.device_date = "202609230200.00"   # device "now" = 2026-09-23 02:00 -- the DST-compensated
                                            # touch lands at 01:00, the two files stamp 01:59/02:00
    driver._open_phone = PHONE
    clock, tick = driver.send_gallery(PHONE, [str(a), str(b)], caption="Test caption")
    assert (clock, tick) == ("10:06", "Gesendet")
    assert len(adb.pushes) == 2
    assert any(c.startswith("touch -t") for c in adb.commands)
    assert any("MEDIA_SCANNER_SCAN_FILE" in c for c in adb.commands)
    assert "Test caption" in "".join(t for t in adb.typed if t != "<clear>"), (
        "type_human chunks words randomly -- join before asserting, never assume a chunk boundary")


def test_send_gallery_types_into_the_live_caption_field_not_a_sent_bubbles_readonly_one(tmp_path):
    """The regression this fix exists for (found live, 2026-09-23): rid=RID_CAPTION alone matches
    BOTH the picker's own live caption box (an EditText) and the read-only caption TextView
    WhatsApp draws under an already-sent gallery bubble higher up the same conversation -- present
    in every dump once such a bubble is on screen, and sorted FIRST here on purpose. Taking the
    first rid match blind grabbed the read-only one at least once live, typed the caption into the
    ordinary chat composer behind the sheet instead, and read back "no send button" next. This
    proves the class filter reaches the editable one regardless of node order."""
    header = PHONE
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    items = [gallery_item(23, "September", 2026, 2, 0)]
    screen_with_old_bubble = ([sent_gallery_bubble_caption("Test gallery caption")]
                              + gallery_screen(items, selected_count=1, caption_text="New caption"))
    driver, adb = build([
        conversation(header, []),
        conversation(header, []) + attach_button(),
        attach_menu(),
        screen_with_old_bubble,
        screen_with_old_bubble,
        screen_with_old_bubble,
        conversation(header, []),
        conversation(header, [outgoing_photo(clock="10:06", tick="Gesendet")]),
    ])
    adb.device_date = "202609230200.00"
    driver._open_phone = PHONE
    driver.send_gallery(PHONE, [str(a)], caption="New caption")
    assert "New caption" in "".join(t for t in adb.typed if t != "<clear>")
    live_caption_center = (228 + 744) // 2, (1980 + 2107) // 2
    stale_bubble_center = (251 + 737) // 2, (446 + 529) // 2
    assert live_caption_center in adb.taps
    assert stale_bubble_center not in adb.taps


def test_send_gallery_refuses_without_a_verified_open_chat(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    driver, adb = build([[]])
    with pytest.raises(D.DriverError) as caught:
        driver.send_gallery(PHONE, [str(a)])
    assert "without a verified open chat" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_gallery_refuses_a_local_file_that_does_not_exist():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_gallery(PHONE, ["/tmp/definitely-not-here-9f8e7d.jpg"])
    assert "does not exist" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_gallery_refuses_more_than_the_cap():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_gallery(PHONE, [f"/tmp/{i}.jpg" for i in range(D.MAX_PHOTOS_PER_SEND + 1)])
    assert "at most" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_gallery_refuses_an_empty_list():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError):
        driver.send_gallery(PHONE, [])


def test_send_gallery_refuses_when_a_stamp_has_no_match(tmp_path):
    """One of the two staged files' own minute is simply not in the pool -- MediaStore/the picker
    has not caught up yet, or the touch failed silently. Never fall back to selecting something
    close; a real candidate's own photo could be the nearest match."""
    header = PHONE
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    only_second = [gallery_item(23, "September", 2026, 2, 0)]
    driver, adb = build([
        conversation(header, []),
        conversation(header, []) + attach_button(),
        attach_menu(),
        gallery_screen(only_second, selected_count=1),
    ])
    adb.device_date = "202609230200.00"
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_gallery(PHONE, [str(a), str(b)])
    assert "refusing to guess" in str(caught.value)


def test_send_gallery_refuses_when_a_stamp_matches_more_than_one_item(tmp_path):
    """A real candidate's photo (or an earlier leftover) happens to share a staged file's own
    minute -- refuse rather than pick either one blind."""
    header = PHONE
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    duplicated = [gallery_item(23, "September", 2026, 1, 59), gallery_item(23, "September", 2026, 1, 59),
                 gallery_item(23, "September", 2026, 2, 0)]
    driver, adb = build([
        conversation(header, []),
        conversation(header, []) + attach_button(),
        attach_menu(),
        gallery_screen(duplicated, selected_count=2),
    ])
    adb.device_date = "202609230200.00"
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_gallery(PHONE, [str(a), str(b)])
    assert "refusing to guess" in str(caught.value)


def test_send_gallery_refuses_when_the_selection_counter_disagrees(tmp_path):
    header = PHONE
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    items = [gallery_item(23, "September", 2026, 1, 59), gallery_item(23, "September", 2026, 2, 0)]
    driver, adb = build([
        conversation(header, []),
        conversation(header, []) + attach_button(),
        attach_menu(),
        gallery_screen(items, selected_count=1),   # both matched, but the counter reads "1" not "2"
    ])
    adb.device_date = "202609230200.00"
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_gallery(PHONE, [str(a), str(b)])
    assert "selection counter" in str(caught.value)


# --- outbound media: one document (TASK-360 round 7, Ivan 2026-09-23) ---------------------------
def document_recipient_confirm():
    """The intermediate "N ausgewählt" recipient-confirm screen a document share -- unlike a
    photo share -- lands on first: its own confirm control shares the plain send button's own
    resource id (RID_SEND), verified live, this handset, 2026-09-23."""
    return [node("send", desc="Senden", bounds=(912, 2020, 1056, 2107), clickable=True)]


def document_compose(basename, *, caption_text=""):
    """WhatsApp's own DocumentPreviewActivity, opened once the share picker's matched row (and,
    for a document, the recipient-confirm screen past that) is tapped -- verified live, this
    handset, 2026-09-23."""
    return [
        node("document_file_name", basename, bounds=(0, 995, 1080, 1177)),
        node("caption", caption_text, desc="Bildunterschrift hinzufügen",
             bounds=(84, 1831, 1014, 1969), clickable=True, cls="android.widget.EditText"),
        node("send", desc="Senden", bounds=(912, 2020, 1056, 2107), clickable=True),
    ]


def test_send_document_pushes_shares_confirms_captions_and_sends(tmp_path):
    """The full flow as observed live, 2026-09-23: unlike a photo, a document share lands on an
    intermediate recipient-confirm screen before the real compose screen."""
    header = PHONE
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    driver, adb = build([
        conversation(header, []),                          # before-count
        share_picker(header),                               # WhatsApp's own picker, our row present
        document_recipient_confirm(),                        # "N ausgewählt" -- one more tap
        document_compose("Lebenslauf.pdf"),                  # filename verified, caption present
        document_compose("Lebenslauf.pdf", caption_text="New caption"),  # send, post-caption
        conversation(header, []),                            # open_chat re-verification
        conversation(header, [outgoing_photo(clock="10:06", tick="Gesendet")]),  # the new bubble
    ])
    driver._open_phone = PHONE
    clock, tick = driver.send_document(PHONE, str(a), caption="New caption")
    assert (clock, tick) == ("10:06", "Gesendet")
    assert adb.pushes == [(str(a), f"/sdcard/Pictures/{AD.OUTBOUND_MEDIA_DIR}/Lebenslauf.pdf")]
    assert any("android.intent.action.SEND" in c and "application/pdf" in c for c in adb.commands)
    assert "New caption" in "".join(t for t in adb.typed if t != "<clear>")


def test_send_document_skips_the_confirm_screen_when_the_share_lands_on_compose_directly(tmp_path):
    """Not assumed fixed at exactly one extra tap: if some WhatsApp build (or content type) lands
    directly on the compose screen the way a photo share does, this must still work without ever
    tapping a "send" that turns out to belong to a screen already left behind."""
    header = PHONE
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    driver, adb = build([
        conversation(header, []),
        share_picker(header),
        document_compose("Lebenslauf.pdf"),   # no intermediate screen this time
        conversation(header, []),
        conversation(header, [outgoing_photo(clock="10:06", tick="Gesendet")]),
    ])
    driver._open_phone = PHONE
    clock, tick = driver.send_document(PHONE, str(a))
    assert (clock, tick) == ("10:06", "Gesendet")


def test_send_document_refuses_without_a_verified_open_chat(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"a")
    driver, adb = build([[]])
    with pytest.raises(D.DriverError) as caught:
        driver.send_document(PHONE, str(a))
    assert "without a verified open chat" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_document_refuses_a_local_file_that_does_not_exist():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_document(PHONE, "/tmp/definitely-not-here-9f8e7d.pdf")
    assert "does not exist" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_document_refuses_when_no_picker_row_matches_the_phone(tmp_path):
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(OTHER),
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_document(PHONE, str(a))
    assert "no row in WhatsApp's own share picker matches" in str(caught.value)


def test_send_document_refuses_two_ambiguous_picker_rows_rather_than_guess(tmp_path):
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(PHONE, PHONE),
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_document(PHONE, str(a))
    assert "refusing to guess" in str(caught.value)


def test_send_document_refuses_when_the_share_flow_lands_nowhere_recognised(tmp_path):
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(PHONE),
        [],   # neither the confirm screen nor the compose screen
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_document(PHONE, str(a))
    assert "neither the compose screen nor a recipient-confirm step" in str(caught.value)


def test_send_document_refuses_when_whatsapps_own_compose_screen_shows_a_different_file(tmp_path):
    """A defensive re-check, not a redundant one: this is the SAME real bug class the caption
    class-filter fix exists for (module docstring, send_gallery) -- WhatsApp's own compose screen
    is the truth about what will actually send, checked again rather than assumed from having
    matched the intended recipient in the share picker."""
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    driver, adb = build([
        conversation(PHONE, []),
        share_picker(PHONE),
        document_compose("a_totally_different_file.pdf"),
    ])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_document(PHONE, str(a))
    assert "reads back" in str(caught.value)


def test_send_photos_refuses_more_than_the_cap():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_photos(PHONE, [f"/tmp/{i}.jpg" for i in range(D.MAX_PHOTOS_PER_SEND + 1)])
    assert "at most" in str(caught.value)
    assert adb.pushes == [], "refused before anything was pushed to the handset"


def test_send_photos_refuses_an_empty_list():
    driver, adb = build([[]])
    driver._open_phone = PHONE
    with pytest.raises(D.DriverError) as caught:
        driver.send_photos(PHONE, [])
    assert "no files" in str(caught.value)


# --- debug capture (TASK-228) ---------------------------------------------------------------

def build_capture(tmp_path, screens=([],)):
    adb = ScriptedAdb(list(screens))
    driver = AD.AdbDriver(shots_dir=str(tmp_path / "shots"),
                          recordings_dir=str(tmp_path / "recordings"), adb=adb)
    return driver, adb


def test_debug_shot_names_the_file_after_the_op_and_downscales_it(tmp_path):
    driver, adb = build_capture(tmp_path)
    path = driver.debug_shot("op.abc123", "00_pre")
    assert path == str(tmp_path / "shots" / "op.abc123_00_pre.png")
    assert adb.downscaled == [path]


def test_start_recording_spawns_a_sized_time_limited_screenrecord(tmp_path):
    driver, adb = build_capture(tmp_path)
    driver.start_recording("op.abc123")
    assert adb.spawned == ["screenrecord --size 720x1280 --time-limit 180 /sdcard/op.abc123.mp4"]


def test_stop_recording_signals_sigint_pulls_and_cleans_the_device_copy(tmp_path):
    driver, adb = build_capture(tmp_path)
    driver.start_recording("op.abc123")
    path = driver.stop_recording("op.abc123")
    assert "killall -2 screenrecord" in adb.commands
    assert adb.pulls == [("/sdcard/op.abc123.mp4", str(tmp_path / "recordings" / "op.abc123.mp4"))]
    assert "rm -f /sdcard/op.abc123.mp4" in adb.commands
    assert path == str(tmp_path / "recordings" / "op.abc123.mp4")
    assert (tmp_path / "recordings" / "op.abc123.mp4").exists()


def test_stop_recording_with_nothing_started_is_a_noop(tmp_path):
    driver, adb = build_capture(tmp_path)
    assert driver.stop_recording("op.never-started") is None
    assert adb.commands == [] and adb.pulls == []


def test_stop_recording_returns_none_when_the_pull_fails(tmp_path):
    driver, adb = build_capture(tmp_path)
    driver.start_recording("op.abc123")
    adb.fail_pull = "/sdcard/op.abc123.mp4"
    assert driver.stop_recording("op.abc123") is None


def test_sweep_orphaned_recordings_removes_whatever_op_mp4s_are_still_on_the_device(tmp_path):
    """TASK-275: the only bookkeeping start_recording/stop_recording keep is the in-memory
    self._recordings dict, so a process restart between the two leaves the mp4 on /sdcard with
    nothing left to name it. A fresh driver -- no recording could possibly be in flight yet --
    finds it via `find` the same way a fresh Ledger fails a stuck-running row.

    TASK-315 review point 10: only files find's own ``-mmin +5`` already calls old enough are ever
    listed or removed -- never a bare ``rm -f`` glob, which could not express that age floor at
    all (screenrecord does not even exist as a binary on this Huawei, so this stays minimal)."""
    driver, adb = build_capture(tmp_path)
    adb.orphaned_recording_listing = "/sdcard/op.abc123.mp4\n"
    removed = driver.sweep_orphaned_recordings()
    assert removed == 1
    find_calls = [c for c in adb.commands if c.startswith("find /sdcard") and "-mmin +5" in c]
    assert any("-delete" not in c for c in find_calls)          # the listing call
    assert any(c.endswith("-delete 2>/dev/null") for c in find_calls)  # the delete call, only
                                                                        # because something was found


def test_sweep_orphaned_recordings_finds_nothing_deletes_nothing(tmp_path):
    driver, adb = build_capture(tmp_path)
    removed = driver.sweep_orphaned_recordings()
    assert removed == 0
    assert not any("-delete" in c for c in adb.commands)


def test_list_screenshot_candidates_names_only_what_is_past_retention(tmp_path):
    driver, adb = build_capture(tmp_path)
    (tmp_path / "shots").mkdir()
    old = tmp_path / "shots" / "op.old_00_pre.png"
    fresh = tmp_path / "shots" / "op.fresh_00_pre.png"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old_mtime = time.time() - 20 * 86400
    os.utime(old, (old_mtime, old_mtime))
    candidates = driver.list_screenshot_candidates(datetime.now(timezone.utc), days=14)
    assert candidates == [old]
    assert old.exists() and fresh.exists()


def test_list_recording_candidates_names_only_what_is_past_retention(tmp_path):
    driver, adb = build_capture(tmp_path)
    (tmp_path / "recordings").mkdir()
    old = tmp_path / "recordings" / "op.old.mp4"
    fresh = tmp_path / "recordings" / "op.fresh.mp4"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old_mtime = time.time() - 20 * 86400
    os.utime(old, (old_mtime, old_mtime))
    candidates = driver.list_recording_candidates(datetime.now(timezone.utc), days=14)
    assert candidates == [old], "listing only names candidates -- nothing is deleted yet"
    assert old.exists() and fresh.exists()


def test_delete_paths_removes_exactly_what_it_is_given_and_tolerates_a_missing_file(tmp_path):
    driver, adb = build_capture(tmp_path)
    (tmp_path / "recordings").mkdir()
    a = tmp_path / "recordings" / "op.a.mp4"
    b = tmp_path / "recordings" / "op.b.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    gone = tmp_path / "recordings" / "op.gone.mp4"   # never created -- already-swept-elsewhere case
    removed = driver.delete_paths([a, gone])
    assert removed == 1
    assert not a.exists() and b.exists()


# --- the whole class of "the abstract base says it exists, the real driver never wrote it" --------

def test_the_real_driver_implements_every_verb_the_base_only_declares():
    """AdbDriver is the one layer with no unit test of its own (bridge/driver.py says so in its own
    header), so a verb added to PhoneDriver and to FakeDriver but never to AdbDriver passes every
    test in this repo and then raises NotImplementedError on the handset. That is exactly what
    happened to focus() between TASK-226 and 2026-09-23: it only stayed invisible because the mini
    was still running a pre-TASK-226 executor, and it would have killed every phone op and the
    inbound watcher thread on the next deploy. This test is the guard, not that one fix."""
    missing = [name for name in dir(D.PhoneDriver)
               if not name.startswith("_")
               and callable(getattr(D.PhoneDriver, name, None))
               and getattr(AD.AdbDriver, name, None) is getattr(D.PhoneDriver, name, None)]
    assert missing == [], f"AdbDriver never overrides {missing} -- they raise NotImplementedError on a real phone"


# --- Adb.screen_size() (TASK-315 review point 3): the REAL display, never the dump's own extent ---

def test_screen_size_parses_physical_size(tmp_path):
    driver, adb = build_capture(tmp_path)
    adb.wm_size_output = "Physical size: 1080x2260\n"
    assert adb.screen_size() == (1080, 2260)


def test_screen_size_prefers_an_active_override(tmp_path):
    driver, adb = build_capture(tmp_path)
    adb.wm_size_output = "Physical size: 1080x2260\nOverride size: 720x1440\n"
    assert adb.screen_size() == (720, 1440)


def test_screen_size_raises_on_unparseable_output(tmp_path):
    driver, adb = build_capture(tmp_path)
    adb.wm_size_output = "nonsense\n"
    with pytest.raises(D.DriverError):
        adb.screen_size()


# --- _outside_frame_tap (TASK-315 review point 3, Opus reject 2026-09-29) --------------------------
# Opus's own reject: the old fallback computed its "below the frame" gap against the DUMP's own
# extent (max(node bounds)), not the real screen -- for a dialog-only window whose dump never
# reaches the true bottom of the screen, that collapsed the gap and the fallback point landed
# inside the lowest button. These tests use the REAL, documented reproduction shape: a dialog-only
# window, EINLADEN/SMS stacked vertically, and a title/message node sitting right at the top.

def _stacked_dialog_nodes():
    """A dialog-only dump: a title node with NO gap above it (bounds start at y=0 -- 'top node
    near y=0'), a message node, and two buttons stacked vertically at the bottom -- EINLADEN then
    SMS, exactly the shape named in the review."""
    return [
        AD.Node(cls="android.widget.TextView", rid="", text="WhatsApp Einladung", desc="",
               bounds=(40, 0, 1040, 120), clickable=False),
        AD.Node(cls="android.widget.TextView", rid="", text="Diese Nummer ist nicht bei WhatsApp",
               desc="", bounds=(40, 130, 1040, 400), clickable=False),
        AD.Node(cls="android.widget.Button", rid="", text="EINLADEN", desc="",
               bounds=(100, 1900, 980, 2000), clickable=True),
        AD.Node(cls="android.widget.Button", rid="", text="SMS", desc="",
               bounds=(100, 2010, 980, 2110), clickable=True),
    ]


def test_outside_frame_tap_gives_up_rather_than_land_inside_the_lowest_button():
    """The exact reproduction: the real screen height equals the dump's own extent (no genuine gap
    below the lowest button at all -- a dialog-only window drawn all the way to the true bottom of
    the screen) and the top node sits at y=0 (no gap above either). The OLD code's fallback would
    have computed a point inside the SMS button here (min(screen_h-20, y2) lands 20px above y2,
    well inside a 100px-tall button); the fix must return None instead."""
    nodes = _stacked_dialog_nodes()
    result = AD._outside_frame_tap(nodes, screen_w=1080, screen_h=2110)
    assert result is None


def test_outside_frame_tap_never_lands_inside_any_button_even_when_it_finds_a_point():
    """Same stacked-buttons, top-near-y=0 shape, but now with the REAL screen taller than the
    dump's own extent (the genuinely common case: a dialog window's dump does not draw all the way
    to the true screen edge). A safe point below the buttons must exist and must clear every
    labelled/clickable node's bounds, jitter and margin included."""
    nodes = _stacked_dialog_nodes()
    result = AD._outside_frame_tap(nodes, screen_w=1080, screen_h=2260)
    assert result is not None
    x, y = result
    assert 0 <= x < 1080 and 0 <= y < 2260
    margin_x = AD._TAP_SAFETY_MARGIN_PX + AD._TAP_JITTER_X
    margin_y = AD._TAP_SAFETY_MARGIN_PX + AD._TAP_JITTER_Y
    for n in nodes:
        assert AD._clears(result, n, margin_x=margin_x, margin_y=margin_y), \
            f"{result} does not clear {n.text!r} bounds {n.bounds}"


def test_outside_frame_tap_normal_dump_yields_a_point_outside_the_frame():
    """A more ordinary dialog: a short title well below the top of the screen, one message, one
    button -- plenty of clear space above the frame. The point returned must lie above the frame's
    own top edge, on screen, and clear of every node."""
    nodes = [
        AD.Node(cls="android.widget.TextView", rid="", text="USB-Nutzung", desc="",
               bounds=(60, 900, 1020, 1000), clickable=False),
        AD.Node(cls="android.widget.TextView", rid="", text="Dieses Gerät als Kamera verwenden?",
               desc="", bounds=(60, 1010, 1020, 1200), clickable=False),
        AD.Node(cls="android.widget.Button", rid="", text="ABBRECHEN", desc="",
               bounds=(700, 1250, 1020, 1350), clickable=True),
    ]
    result = AD._outside_frame_tap(nodes, screen_w=1080, screen_h=2260)
    assert result is not None
    x, y = result
    assert y < 900   # strictly above the frame's own top edge
    assert 0 <= x < 1080 and 0 <= y < 2260
    margin_x = AD._TAP_SAFETY_MARGIN_PX + AD._TAP_JITTER_X
    margin_y = AD._TAP_SAFETY_MARGIN_PX + AD._TAP_JITTER_Y
    for n in nodes:
        assert AD._clears(result, n, margin_x=margin_x, margin_y=margin_y)


def test_outside_frame_tap_returns_none_with_no_labelled_nodes():
    nodes = [AD.Node(cls="android.view.View", rid="", text="", desc="", bounds=(0, 0, 1080, 2260),
                     clickable=False)]
    assert AD._outside_frame_tap(nodes, screen_w=1080, screen_h=2260) is None
