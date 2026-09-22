"""app/runs.py: crawl_issues keying (TASK-72 AC#5). SQLite in a temp dir, no network."""
import sqlite3

import pytest

from app import config as A
from app import runs as R


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    R.init()
    yield


def test_same_board_and_day_different_kind_do_not_overwrite_each_other(fresh):
    """A board-fetch failure and a same-day per-posting verify/city issue for the SAME url used to
    collide on the old (board_url, day) key -- confirmed live 2026-09-17 erasing 883 of the day's
    crawl_issues rows. Keying on (kind, board_url, day) lets both survive."""
    url, day = "https://x.example/board", "2026-09-18"
    R.record_crawl_issue(url, day, "vendor", "wp_jobs", ["1"], "transport failure: 0/3 succeeded", 101)
    R.record_crawl_issue(url, day, "city", "jsonld", ["1"], "page says 'Oberhausen', stored 'Regensburg'", 102)

    issues = {i["kind"]: i for i in R.list_crawl_issues(day=day) if i["board_url"] == url}
    assert set(issues) == {"vendor", "city"}
    assert issues["vendor"]["error"] == "transport failure: 0/3 succeeded" and issues["vendor"]["run_id"] == 101
    assert issues["city"]["error"] == "page says 'Oberhausen', stored 'Regensburg'" and issues["city"]["run_id"] == 102


def test_same_kind_board_and_day_still_upserts(fresh):
    """A board failing again later the same day (e.g. a retry pass on a later run) still updates the
    one row for that (kind, board_url, day), rather than accumulating duplicates."""
    url, day = "https://x.example/board", "2026-09-18"
    R.record_crawl_issue(url, day, "vendor", "wp_jobs", ["1"], "first failure", 1)
    R.record_crawl_issue(url, day, "vendor", "wp_jobs", ["1"], "second failure", 2)

    issues = [i for i in R.list_crawl_issues(day=day) if i["board_url"] == url]
    assert len(issues) == 1
    assert issues[0]["error"] == "second failure" and issues[0]["run_id"] == 2


# --- board_walk_ok: TASK-87 AC#1's walk_ok signal for pflege_jobs.verify.board_absent_gone -----
def test_board_walk_ok_true_with_no_recorded_issue(fresh):
    assert R.board_walk_ok("https://x.example/board", "2026-09-21") is True


def test_board_walk_ok_false_on_vendor_kind(fresh):
    # kind='vendor' is what app/crawl.py:786 actually records for a board still transport-failing
    # after 3 attempts (b["kind"] is crawlers.routing.ADAPTERS' calling-convention tag -- there is no
    # literal 'error' kind; see board_walk_ok's docstring). Isolated on its own row/day so a filter
    # mutated down to e.g. kind in ('seeded','truncated') would redden this specific test.
    url, day = "https://x.example/board", "2026-09-21"
    R.record_crawl_issue(url, day, "vendor", "wp_jobs", ["1"], "transport failure: 0/3 succeeded", 1)
    assert R.board_walk_ok(url, day) is False


def test_board_walk_ok_false_on_seeded_kind(fresh):
    url, day = "https://x.example/board", "2026-09-21"
    R.record_crawl_issue(url, day, "seeded", "softgarden", ["1"], "transport failure: 0/3 succeeded", 1)
    assert R.board_walk_ok(url, day) is False


def test_board_walk_ok_false_on_truncated_kind(fresh):
    # isolated with NO 'vendor'/'seeded' row for this board/day -- the old test inserted 'error' first
    # and never cleared it, so this case was never actually exercised on its own (mutation-confirmed:
    # narrowing the filter to kind in ('error') alone left the old test green).
    url, day = "https://x.example/board", "2026-09-21"
    R.record_crawl_issue(url, day, "truncated", "wp_jobs", ["1"], "safety ceiling stopped the walk", 1)
    assert R.board_walk_ok(url, day) is False


def test_board_walk_ok_true_on_kinds_unrelated_to_a_board_walk(fresh):
    # 'city'/'posting' are per-posting verify issues for this exact url, recorded on a day the board
    # walk itself otherwise succeeded fine -- guards against a filter widened the wrong way (e.g.
    # `kind != 'empty'`) mistaking them for the walk itself having failed.
    url, day = "https://x.example/board", "2026-09-21"
    R.record_crawl_issue(url, day, "city", "jsonld", ["1"], "page says 'Oberhausen', stored 'Regensburg'", 1)
    R.record_crawl_issue(url, day, "posting", "jsonld", ["1"], "unrelated single-posting issue", 2)
    assert R.board_walk_ok(url, day) is True


def test_board_walk_ok_true_on_a_merely_empty_walk(fresh):
    # 'empty' (0 rows, no transport error) is deliberately NOT one of the three kinds that flip this
    # false -- board_absent_gone's own docstring explains why a plain 0-rows success must stay the
    # caller's judgment call, not a blanket ok/not-ok baked in here.
    url, day = "https://x.example/board", "2026-09-21"
    R.record_crawl_issue(url, day, "empty", "wp_jobs", ["1"], "0 rows, no transport error", 1)
    assert R.board_walk_ok(url, day) is True


def test_board_walk_ok_ignores_other_days_and_other_boards(fresh):
    # kind='vendor' here on purpose (a real matched kind): with the never-recorded 'error' this test
    # used before the fix, it passed regardless of whether the day/board_url scoping worked at all.
    url, day = "https://x.example/board", "2026-09-21"
    R.record_crawl_issue(url, "2026-09-20", "vendor", "wp_jobs", ["1"], "yesterday's failure", 1)
    R.record_crawl_issue("https://other.example/board", day, "vendor", "wp_jobs", ["2"], "a different board", 2)
    assert R.board_walk_ok(url, day) is True


def test_init_migrates_a_pre_task72_database_without_losing_rows(tmp_path, monkeypatch):
    """A database file created before this fix has crawl_issues with primary key (board_url, day)
    only -- init() must recreate it with the new key and keep whatever rows already existed."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "old.sqlite")
    con = sqlite3.connect(str(A.SQLITE_PATH))
    con.execute("""create table crawl_issues (
        board_url text, day text, kind text, vendor text, clinic_ids text default '[]',
        error text, run_id integer, created_at text, primary key(board_url, day))""")
    con.execute("insert into crawl_issues(board_url,day,kind,vendor,clinic_ids,error,run_id,created_at) "
                "values('https://old.example/board','2026-09-17','vendor','wp_jobs','[\"9\"]','old failure',5,'2026-09-17T00:00:00+00:00')")
    con.commit(); con.close()

    R.init()   # must not raise, and must upgrade the schema in place

    old_rows = [i for i in R.list_crawl_issues(day="2026-09-17") if i["board_url"] == "https://old.example/board"]
    assert len(old_rows) == 1 and old_rows[0]["error"] == "old failure"

    # the new key now lets a same-day, different-kind issue for that same url coexist
    R.record_crawl_issue("https://old.example/board", "2026-09-17", "empty", "wp_jobs", ["9"], "new issue", 6)
    rows = [i for i in R.list_crawl_issues(day="2026-09-17") if i["board_url"] == "https://old.example/board"]
    assert {i["kind"] for i in rows} == {"vendor", "empty"}
