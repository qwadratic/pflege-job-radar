"""pflege_jobs.verify.board_absent_gone -- TASK-87 AC#1: retire a posting absent from a board walk
that SUCCEEDED, distinct from a posting whose URL 404s (pflege_jobs/verify.py's existing decide()
path), and never retire anything from a walk that failed or was truncated (TASK-73 AC#6 / TASK-14
AC#2). Pure-function tests, no network."""
from pflege_jobs.verify import VERIFY_FIELDS, board_absent_gone

ROWS = [{"posting_id": 1, "external_url": "https://x.de/job/1"},
        {"posting_id": 2, "external_url": "https://x.de/job/2"},
        {"posting_id": 3, "external_url": "https://x.de/job/3"}]


def test_retires_only_the_row_absent_from_the_walk():
    out = board_absent_gone(ROWS, board_urls=["https://x.de/job/1", "https://x.de/job/3"], walk_ok=True)
    assert [r["posting_id"] for r in out] == [2]
    assert out[0]["verify_status"] == "gone"
    assert out[0]["verify_http"] is None
    assert "board" in out[0]["verify_note"]
    assert set(out[0]) == set(VERIFY_FIELDS)               # feeds EdgeSink's verify op as-is


def test_failed_walk_never_retires_anything():
    # the hard constraint: even a board_urls set that lists NOTHING must not retire a single row
    # when the walk itself did not complete.
    assert board_absent_gone(ROWS, board_urls=[], walk_ok=False) == []


def test_truncated_walk_never_retires_anything():
    assert board_absent_gone(ROWS, board_urls=["https://x.de/job/1"], walk_ok=False) == []


def test_successful_empty_board_retires_everything_when_the_caller_says_so():
    # walk_ok is the caller's own judgment call (a genuinely empty board vs. a wrong registry url
    # both read as "0 rows" from here) -- once the caller asserts walk_ok=True, an empty board_urls
    # set is a real signal and every open row on it retires (TASK-87 M7: "when an adapter silently
    # drops to 0 rows, nothing expires" is exactly the gap this closes).
    out = board_absent_gone(ROWS, board_urls=[], walk_ok=True)
    assert [r["posting_id"] for r in out] == [1, 2, 3]


def test_falls_back_to_source_url_when_external_url_is_unset():
    rows = [{"posting_id": 9, "source_url": "https://x.de/job/9"}]
    assert board_absent_gone(rows, board_urls=[], walk_ok=True)[0]["posting_id"] == 9


def test_a_row_present_on_the_board_is_left_alone():
    assert board_absent_gone(ROWS, board_urls=[r["external_url"] for r in ROWS], walk_ok=True) == []


# --- key= param: the default exact-URL match false-positives on cosmetic URL drift (reviewer finding
# on the TASK-87 dry run, Asklepios/helix) -- a caller that can derive a vendor's own stable job id
# needs an escape hatch instead of reimplementing this whole function.
def test_default_key_false_positives_on_cosmetic_url_drift():
    # same shape as the live Asklepios/helix finding: same vendor job id, slug text changed --
    # documents why the default is unsafe for a real write, not just a hypothetical.
    row = [{"posting_id": 18811, "external_url": "https://x.de/jobs/12345-alte-slug"}]
    out = board_absent_gone(row, board_urls=["https://x.de/jobs/12345-neue-slug"], walk_ok=True)
    assert [r["posting_id"] for r in out] == [18811]        # false positive: still on the board really


def test_custom_key_on_vendor_job_id_avoids_the_false_positive():
    import re
    vendor_id = lambda u: (re.match(r"https://x\.de/jobs/(\d+)-", u or "") or [None, None])[1]
    row = [{"posting_id": 18811, "external_url": "https://x.de/jobs/12345-alte-slug"}]
    out = board_absent_gone(row, board_urls=["https://x.de/jobs/12345-neue-slug"], walk_ok=True, key=vendor_id)
    assert out == []                                        # same vendor id -> correctly left alone


def test_custom_key_still_retires_a_genuinely_absent_vendor_id():
    import re
    vendor_id = lambda u: (re.match(r"https://x\.de/jobs/(\d+)-", u or "") or [None, None])[1]
    row = [{"posting_id": 18811, "external_url": "https://x.de/jobs/12345-alte-slug"}]
    out = board_absent_gone(row, board_urls=["https://x.de/jobs/99999-other"], walk_ok=True, key=vendor_id)
    assert [r["posting_id"] for r in out] == [18811]
