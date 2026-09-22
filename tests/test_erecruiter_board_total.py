"""crawl_erecruiter reads its board's own TotalJobsCount / Pagination.IsPagination (TASK-88 AC#1
worked example). Carried on the RETURNED row list's own .board_total/.board_paginated attributes,
not on the shared session: session is one per whole run (app/crawl.py:655) and reused board after
board, so a value written there needs an explicit per-board reset that only app/crawl.py's
_fetch_board (not this file) can add next to its existing session._attempts/_ok reset -- a value
carried on THIS call's own returned list instead needs no reset, because nothing else ever holds a
reference to that exact list (2026-09-22 review fix; the first cut used session._board_total and
went stale across boards, see _BoardTotalRows' docstring). No network -- va.get is monkeypatched,
same convention as tests/test_completeness_js_widget_boards.py."""
import json

from crawlers import vendor_adapters as va
from tests.test_vendor_adapters import _R, _router  # noqa: E402

ER_CU = "https://jobs.bezirkskliniken-schwaben.de/Jobs"


def _erecruiter_page(jobs, total=None, is_paginated=False):
    return ('<html><body>'
            '<script id="jobListTemplate" type="text/template">'
            '<a href="/Job/{{Id}}">{{Title}}</a></script>'
            '<script>jQuery(function ($) { window.jobList = new JobList('
            '$("#jobListPlaceholder"), $("#jobListTemplate"), %s); });</script>'
            '</body></html>' % json.dumps({"TotalJobsCount": len(jobs) if total is None else total, "Jobs": jobs,
                                           "Pagination": {"IsPagination": is_paginated}}))


class _Session:
    """A bare object to hang side-channel attributes off of -- crawl_erecruiter never calls .get()
    on it directly here (va.get is monkeypatched wholesale), only reads/writes attributes."""


def _job(i):
    return {"Id": i, "Title": "Pflegefachkraft (m/w/d) %d" % i, "SubTitle": "", "Location": "Günzburg", "Date": "21.09.2026"}


def test_reads_total_jobs_count_and_pagination_flag_onto_the_returned_rows(monkeypatch):
    jobs = [_job(1), _job(2)]
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs, total=2, is_paginated=False), url=ER_CU)}))
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=_Session())
    assert len(rows) == 2
    assert rows.board_total == 2
    assert rows.board_paginated is False


def test_flags_a_tenant_that_under_reads_its_own_declared_total(monkeypatch):
    """The concrete gap TASK-88 names: a third tenant whose TotalJobsCount exceeds what this page
    embeds (server-side pagination this adapter does not walk) must be visible as total > rows,
    not silently read as a clean success."""
    jobs = [_job(1)]                                    # page 1 only; the board claims 57 exist
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs, total=57, is_paginated=True), url=ER_CU)}))
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=_Session())
    assert len(rows) == 1
    assert rows.board_total == 57 and rows.board_total > len(rows)
    assert rows.board_paginated is True


def test_no_session_no_crash(monkeypatch):
    """session=None is the norm in this test suite (every other erecruiter test omits it) and in
    any direct call outside app/crawl.py's shared-session fetch loop."""
    jobs = [_job(1)]
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs), url=ER_CU)}))
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU})
    assert len(rows) == 1
    assert rows.board_total == 1


def test_missing_total_jobs_count_is_none_not_a_false_match(monkeypatch):
    jobs = [_job(1)]
    page = _erecruiter_page(jobs).replace('"TotalJobsCount": 1', '"TotalJobsCount": null')
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(page, url=ER_CU)}))
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=_Session())
    assert rows.board_total is None


def test_a_failed_board_fetch_still_returns_something_with_a_readable_board_total(monkeypatch):
    """A bare [] here has no .board_total attribute -- app/crawl.py's pending AC#2 wiring reads that
    attribute unconditionally, so a plain list on this path would AttributeError instead of just
    reporting an unknown total (review finding, 2026-09-22)."""
    monkeypatch.setattr(va, "get", _router({}))              # ER_CU unmapped -> _router's own 404
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=_Session())
    assert len(rows) == 0
    assert rows.board_total is None                          # readable, not an AttributeError


def test_crawl_wp_jobs_keeps_the_board_total_when_erecruiter_parses_zero_rows(monkeypatch):
    """The mechanism's most severe case (review finding, 2026-09-22): a board this delegate loop DOES
    recognise as eRecruiter (its own TotalJobsCount is right there in the embedded JSON) but which
    yields zero parsed rows must stay tagged with that total, not fall through as an indistinguishable
    plain empty list. `if rows: return rows` used to treat 0-rows-with-a-real-total exactly like
    'not eRecruiter at all' -- the one shape the board-total signal exists to catch, since the live
    registry has zero clinics labelled ats_type=erecruiter, so this delegate loop is the ONLY path
    production reaches this adapter through."""
    page = _erecruiter_page([], total=57, is_paginated=True)  # board claims 57, ships an empty Jobs array
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(page, url=ER_CU)}))
    rows = va.crawl_wp_jobs({"name": "seed", "careers_url": ER_CU})
    assert len(rows) == 0
    assert rows.board_total == 57                             # not discarded as "not this vendor"


def test_a_stale_total_from_a_prior_board_never_leaks_onto_the_next_board(monkeypatch):
    """The review-flagged bug: the first cut stashed the total on the shared `session`, which
    app/crawl.py reuses across every board in one run -- a later board's own return value (a plain
    row list, no total field) had no way to overwrite it, so whatever read the session after
    crawl_erecruiter ran for board A kept seeing board A's number while looking at board B. Same
    session object reused for two calls here, second one with a smaller/no total, proves each
    call's returned rows carry only their OWN board's signal."""
    big_board = _erecruiter_page([_job(1), _job(2)], total=57, is_paginated=True)
    small_board = _erecruiter_page([_job(9)], total=None, is_paginated=False)
    shared_session = _Session()

    router = _router({ER_CU: _R(big_board, url=ER_CU)})
    monkeypatch.setattr(va, "get", router)
    rows_a = va.crawl_erecruiter({"name": "board A", "careers_url": ER_CU}, session=shared_session)
    assert rows_a.board_total == 57

    monkeypatch.setattr(va, "get", _router({ER_CU: _R(small_board, url=ER_CU)}))
    rows_b = va.crawl_erecruiter({"name": "board B", "careers_url": ER_CU}, session=shared_session)
    assert rows_b.board_total == 1                      # board B's own total, not board A's stale 57
    assert rows_a.board_total == 57                      # and board A's own return value is untouched
