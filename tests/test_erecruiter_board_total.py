"""crawl_erecruiter reads its board's own TotalJobsCount / Pagination.IsPagination (TASK-88 AC#1
worked example). Stashed on the shared session, mirroring get()'s _attempts/_ok side channel
(TASK-72 AC#1): crawl_erecruiter returns a plain row list whose shape app/crawl.py's generic
vendor-adapter caller depends on, so board-level metadata has no return-value channel of its own.
No network -- va.get is monkeypatched, same convention as tests/test_completeness_js_widget_boards.py."""
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


def test_reads_total_jobs_count_and_pagination_flag_onto_the_session(monkeypatch):
    jobs = [_job(1), _job(2)]
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs, total=2, is_paginated=False), url=ER_CU)}))
    sess = _Session()
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=sess)
    assert len(rows) == 2
    assert sess._board_total == 2
    assert sess._board_paginated is False


def test_flags_a_tenant_that_under_reads_its_own_declared_total(monkeypatch):
    """The concrete gap TASK-88 names: a third tenant whose TotalJobsCount exceeds what this page
    embeds (server-side pagination this adapter does not walk) must be visible as total > rows,
    not silently read as a clean success."""
    jobs = [_job(1)]                                    # page 1 only; the board claims 57 exist
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs, total=57, is_paginated=True), url=ER_CU)}))
    sess = _Session()
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=sess)
    assert len(rows) == 1
    assert sess._board_total == 57 and sess._board_total > len(rows)
    assert sess._board_paginated is True


def test_no_session_no_crash(monkeypatch):
    """session=None is the norm in this test suite (every other erecruiter test omits it) and in
    any direct call outside app/crawl.py's shared-session fetch loop."""
    jobs = [_job(1)]
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs), url=ER_CU)}))
    rows = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU})
    assert len(rows) == 1


def test_missing_total_jobs_count_is_none_not_a_false_match(monkeypatch):
    jobs = [_job(1)]
    page = _erecruiter_page(jobs).replace('"TotalJobsCount": 1', '"TotalJobsCount": null')
    sess = _Session()
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(page, url=ER_CU)}))
    va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU}, session=sess)
    assert sess._board_total is None
