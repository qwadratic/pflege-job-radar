"""The deferred-candidates register (backlog doc "Deferred candidates register") keeps measured findings that are not adopted now,
each with a comment date and a next-review date. tools/deferred_due.py lists the rows whose review has come. Offline."""
import datetime
import pathlib

import pytest

from tools import deferred_due as D

HEAD = "| id | candidate | source | comment and impact | comment date | by | next review | adopt when |\n|---|---|---|---|---|---|---|---|\n"


def _row(i, review, commented="2026-10-06"):
    return f"| D{i} | cand {i} | TASK-1 | impact {i} | {commented} | pflege-clawl | {review} | when {i} |\n"


def test_rows_due_on_or_before_the_day():
    text = HEAD + _row(1, "2026-11-06") + _row(2, "2026-12-06") + _row(3, "2026-10-20")
    got = D.due(D.rows(text), datetime.date(2026, 11, 6))
    assert [r["id"] for r in got] == ["D1", "D3"]
    assert got[0]["candidate"] == "cand 1" and got[0]["next_review"] == "2026-11-06"


def test_a_malformed_row_or_date_fails_loudly():
    with pytest.raises(SystemExit, match="D1"):
        D.rows(HEAD + "| D1 | only | three |\n")
    with pytest.raises(ValueError):
        D.rows(HEAD + _row(1, "next month"))


def test_the_register_in_the_backlog_is_well_formed():
    rows = D.rows(D.register_path().read_text(encoding="utf-8"))
    assert len(rows) >= 10 and [r["id"] for r in rows] == [f"D{i}" for i in range(1, len(rows) + 1)]
    assert all(r["next_review"] > r["comment_date"] for r in rows)
    assert all(r["comment"] and r["adopt_when"] for r in rows)
