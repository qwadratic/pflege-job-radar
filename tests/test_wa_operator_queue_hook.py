"""tools/operator_queue_hook.py (TASK-303 rewrite): reads wa_agent_notes and the worker's health.json
through a read-only URI, never app.wa.store (this hook must never be what turns an absent database
into an accidentally-created one, nor what a webhook process waits behind). Offline only: every test
builds its own tiny sqlite file and its own health.json; nothing here touches the real
data/wa.sqlite or the real state dir, and importing this module spawns nothing.
"""
import importlib
import json
import sqlite3
import sys

import pytest

sys.path.insert(0, "tools")
import operator_queue_hook as H  # noqa: E402  (tools/ has no __init__.py; path insert is required)


NOW = H.datetime(2026, 9, 25, 7, 35, 0, tzinfo=H.timezone.utc)


def _db(tmp_path, rows=()):
    path = tmp_path / "wa.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("""create table wa_agent_notes (
        id integer primary key, wamid text, phone text, kind text, body text, status text,
        created_at text, acked_at text, claimed_at text, attempts integer default 0, progress text,
        outcome_done text, outcome_not_done text, outcome_needed text, blocked_reason text,
        finished_at text, notified_at text, task_id text, handed_off_at text)""")
    for r in rows:
        conn.execute("""insert into wa_agent_notes
            (id, wamid, phone, kind, body, status, created_at, notified_at, task_id, finished_at)
            values (:id,:wamid,:phone,:kind,:body,:status,:created_at,:notified_at,:task_id,:finished_at)""",
            {"wamid": f"w{r['id']}", "phone": "+491", "kind": "text", "notified_at": None,
             "task_id": None, "finished_at": None, **r})
    conn.commit()
    conn.close()
    return path


def _health_file(tmp_path, **overrides):
    payload = {"ok": False, "at": NOW.isoformat(), "problem": "something broke", "note_id": 1,
              "card_id": None, **overrides}
    path = tmp_path / "health.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- silence when there is nothing to show ---------------------------------------------------

def test_no_db_no_health_prints_nothing(tmp_path):
    assert H.render(H.open_notes(db_path=tmp_path / "nope.sqlite", now=NOW),
                    H.health_problem(health_path=tmp_path / "nope.json", now=NOW)) == ""


def test_an_empty_but_real_database_prints_nothing(tmp_path):
    db_path = _db(tmp_path)
    assert H.open_notes(db_path=db_path, now=NOW) == []
    assert H.render([], None) == ""


def test_main_prints_nothing_and_returns_0_with_nothing_to_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(H, "DB_PATH", str(tmp_path / "nope.sqlite"))
    monkeypatch.setattr(H, "HEALTH_PATH", str(tmp_path / "nope.json"))
    assert H.main() == 0
    assert capsys.readouterr().out == ""


# --- silent + exit 0 on a missing or corrupt database ----------------------------------------

def test_a_missing_database_yields_no_notes_not_an_exception(tmp_path):
    assert H.open_notes(db_path=tmp_path / "does-not-exist.sqlite", now=NOW) == []


def test_a_corrupt_database_file_yields_no_notes_not_an_exception(tmp_path):
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"this is not a sqlite file at all, just bytes")
    assert H.open_notes(db_path=junk, now=NOW) == []


def test_a_database_missing_the_table_yields_no_notes(tmp_path):
    path = tmp_path / "no_table.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("create table something_else (x int)")
    conn.commit()
    conn.close()
    assert H.open_notes(db_path=path, now=NOW) == []


def test_main_never_raises_even_when_everything_underneath_is_broken(tmp_path, monkeypatch, capsys):
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"garbage")
    monkeypatch.setattr(H, "DB_PATH", str(junk))
    monkeypatch.setattr(H, "HEALTH_PATH", str(tmp_path / "also-garbage.json"))
    (tmp_path / "also-garbage.json").write_text("{not json", encoding="utf-8")
    assert H.main() == 0
    assert capsys.readouterr().out == ""


# --- the compact listing itself ----------------------------------------------------------------

def test_pending_in_progress_and_handed_off_are_all_listed(tmp_path):
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "pending", "body": "note one", "created_at": "2026-09-25T07:00:00+00:00"},
        {"id": 2, "status": "in_progress", "body": "note two", "created_at": "2026-09-25T07:10:00+00:00"},
        {"id": 3, "status": "handed_off", "body": "note three", "created_at": "2026-09-25T07:20:00+00:00",
         "task_id": "TASK-1"},
    ])
    notes = H.open_notes(db_path=db_path, now=NOW)
    assert [n["id"] for n in notes] == [1, 2, 3]   # oldest created_at first
    assert notes[2]["card"] == "TASK-1"
    assert notes[0]["card"] == "-"


def test_a_finished_undelivered_note_is_listed_regardless_of_age(tmp_path):
    old = "2026-09-01T00:00:00+00:00"
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "done", "body": "old but never notified", "created_at": old,
         "notified_at": None, "finished_at": old},
    ])
    ids = [n["id"] for n in H.open_notes(db_path=db_path, now=NOW)]
    assert ids == [1]


def test_a_delivered_finished_note_is_not_listed(tmp_path):
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "done", "body": "finished and notified", "created_at": "2026-09-25T07:00:00+00:00",
         "notified_at": "2026-09-25T07:01:00+00:00", "finished_at": "2026-09-25T07:01:00+00:00"},
    ])
    assert H.open_notes(db_path=db_path, now=NOW) == []


def test_a_recently_blocked_note_is_listed_even_though_it_was_delivered(tmp_path):
    recent = (NOW - H.timedelta(hours=1)).isoformat()
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "blocked", "body": "blocked an hour ago", "created_at": recent,
         "notified_at": recent, "finished_at": recent},
    ])
    ids = [n["id"] for n in H.open_notes(db_path=db_path, now=NOW)]
    assert ids == [1]


def test_a_stale_blocked_note_past_the_recent_window_is_not_listed(tmp_path):
    old = (NOW - H.timedelta(hours=H.BLOCKED_RECENT_HOURS + 1)).isoformat()
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "blocked", "body": "blocked days ago", "created_at": old,
         "notified_at": old, "finished_at": old},
    ])
    assert H.open_notes(db_path=db_path, now=NOW) == []


def test_body_preview_is_truncated_and_newlines_are_flattened(tmp_path):
    long_body = ("line one\nline two " + "x" * 200)
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "pending", "body": long_body, "created_at": "2026-09-25T07:00:00+00:00"},
    ])
    preview = H.open_notes(db_path=db_path, now=NOW)[0]["body"]
    assert "\n" not in preview
    assert len(preview) <= H.BODY_PREVIEW_CHARS
    assert preview.endswith("…")


def test_age_is_a_short_single_unit_string():
    assert H._age("2026-09-25T07:34:30+00:00", NOW) == "30s"
    assert H._age("2026-09-25T07:00:00+00:00", NOW) == "35m"
    assert H._age("2026-09-25T05:35:00+00:00", NOW) == "2h"
    assert H._age("2026-09-20T07:35:00+00:00", NOW) == "5d"
    assert H._age("not a timestamp", NOW) == "?"


def test_age_of_a_timezone_naive_timestamp_is_a_question_mark_not_a_raise(tmp_path):
    """Round-1 review, nonblocking, fixed as a plain bug: 'now - dt' used to sit OUTSIDE _age's own
    try, so a valid-but-naive isoformat string (parses fine, then TypeErrors on the subtraction against
    an aware 'now') raised one level up instead of returning '?' like every other malformed value."""
    assert H._age("2026-09-25T07:00:00", NOW) == "?"   # no offset -- naive, not malformed syntax


def test_a_naive_created_at_on_one_row_does_not_blank_the_whole_listing(tmp_path):
    """The actual failure mode this fixes: before, one bad row raised inside open_notes()'s own loop
    (also unwrapped), and main()'s broad except turned that into the WHOLE hook printing nothing --
    hiding every other, perfectly good note along with the one bad row."""
    db_path = _db(tmp_path, rows=[
        {"id": 1, "status": "pending", "body": "well-formed", "created_at": "2026-09-25T07:00:00+00:00"},
        {"id": 2, "status": "pending", "body": "naive timestamp", "created_at": "2026-09-25T07:10:00"},
    ])
    notes = H.open_notes(db_path=db_path, now=NOW)
    assert [n["id"] for n in notes] == [1, 2]
    assert notes[0]["age"] == "35m"
    assert notes[1]["age"] == "?"
    out = H.render(notes, None)
    assert "well-formed" in out and "naive timestamp" in out


def test_render_lists_every_note_and_a_one_line_legend(tmp_path):
    db_path = _db(tmp_path, rows=[
        {"id": 7, "status": "pending", "body": "hello", "created_at": "2026-09-25T07:00:00+00:00"},
    ])
    out = H.render(H.open_notes(db_path=db_path, now=NOW), None)
    assert "#7 pending text age=35m card=- \"hello\"" in out
    assert "1 open note(s)" in out
    assert out.endswith("\n")


# --- health problem surfacing -------------------------------------------------------------------

def test_a_recent_ok_false_health_problem_is_surfaced(tmp_path):
    path = _health_file(tmp_path, at=(NOW - H.timedelta(minutes=5)).isoformat(), problem="decode failed: timeout")
    problem = H.health_problem(health_path=path, now=NOW)
    assert problem == ("decode failed: timeout", "5m")


def test_a_stale_health_problem_past_the_recent_window_is_not_surfaced(tmp_path):
    stale_at = (NOW - H.timedelta(minutes=H.HEALTH_RECENT_MIN + 5)).isoformat()
    path = _health_file(tmp_path, at=stale_at)
    assert H.health_problem(health_path=path, now=NOW) is None


def test_an_ok_true_health_file_is_never_surfaced_as_a_problem(tmp_path):
    path = _health_file(tmp_path, ok=True, problem=None)
    assert H.health_problem(health_path=path, now=NOW) is None


def test_a_missing_health_file_is_not_a_problem(tmp_path):
    assert H.health_problem(health_path=tmp_path / "nope.json", now=NOW) is None


def test_a_corrupt_health_file_is_not_a_problem(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json at all", encoding="utf-8")
    assert H.health_problem(health_path=path, now=NOW) is None


def test_render_includes_the_health_problem_line_even_with_no_open_notes(tmp_path):
    path = _health_file(tmp_path, at=NOW.isoformat(), problem="handoff failed: no exact match")
    problem = H.health_problem(health_path=path, now=NOW)
    out = H.render([], problem)
    assert "handoff failed: no exact match" in out
    assert "0s ago" in out


# --- env-driven path overrides (module-level defaults, mirrored by agent_note_cron.sh) -----------

def test_default_paths_match_the_live_service_layout():
    importlib.reload(H)   # picks up any stray env override a prior test in this process may have left
    assert H.DB_PATH.endswith("/data/wa.sqlite")
    assert H.HEALTH_PATH.endswith("/pflege-wa-agent-notes/health.json")
