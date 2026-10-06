"""Adapter-specific completeness tests for beesite + hr4you (TASK-40), on top of the shared harness
in tests/adapter_harness.py, run against the local mirror of the two boards (TASK-197).

What these cases request beyond the standard board recording (the beesite fingerprint page, the generic sitemap walk) is
recorded under their own scope the first time:
    MIRROR_RECORD=1 .venv/bin/python -m pytest tests/test_completeness_beesite_hr4you.py

Both boards are registry-labelled "self_hosted" (no beesite/hr4you vendor fingerprint exists in the
census -- TASK-40 AC#3: the family is chosen by probed capability, not a new registry label), so the
shared harness's own board ids are "self_hosted__jobs.bezirkskliniken-mfr.de" and
"self_hosted__atos-karriere.de" -- `-k beesite`/`-k hr4you` selects NOTHING there, and every
row-dependent shared check (field completeness, public url, round trip) auto-passes on zero rows, so
the shared suite cannot red on "the adapter silently returns nothing" for these two boards at all
(confirmed live 2026-09-10: crawlers.vendor_adapters.find_job_urls() returns [] for both hosts even
though their sitemaps ARE reachable -- neither board's job url shape matches the generic JOB_PATH
regex). These tests close that gap directly.
"""
import os
import re
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import adapter_harness as H  # noqa: E402
from tests import mirror as M  # noqa: E402

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror
from crawlers.vendor_adapters import crawl_wp_jobs, find_job_urls  # noqa: E402
from pflege_jobs.sources.beesite import crawl_beesite, is_beesite  # noqa: E402
from pflege_jobs.sources.hr4you import crawl_hr4you  # noqa: E402

SCOPE = "beesite_hr4you"


class _Board:
    """One of the two boards of the mirror, found when first used (a missing mirror fails the case, not the collection)."""

    def __init__(self, host):
        self.host = host

    @property
    def board(self):
        return H.board_with(self.host)

    @property
    def clinic(self):
        return self.board["clinics"][0]

    def __getitem__(self, k):
        return self.board[k]

    def replay(self):
        return M.mirror_board(self.board["board_id"], SCOPE)


_BEESITE_BOARD = _Board("bezirkskliniken-mfr.de")
_HR4YOU_BOARD = _Board("atos-karriere.de")


# ---------------------------------------------------------------------------------------------
# root cause (RED before any of this file's reader code existed): the generic sitemap/page-link
# walk every unlabelled self_hosted board otherwise gets sees the sitemap fine but finds no
# job-shaped url in it, on both boards.
# ---------------------------------------------------------------------------------------------
@pytest.mark.completeness
def test_generic_walk_finds_no_job_urls_on_either_board():
    with _BEESITE_BOARD.replay():
        assert find_job_urls("https://jobs.bezirkskliniken-mfr.de") == []
    with _HR4YOU_BOARD.replay():
        assert find_job_urls("https://www.atos-karriere.de") == []


# ---------------------------------------------------------------------------------------------
# probed capability (AC#3): neither clinic carries a beesite/hr4you registry label, and the
# GENERIC dispatcher (crawl_wp_jobs -- what every other unlabelled self_hosted board actually
# runs through) must still reach every row by recognising the vendor at runtime.
# ---------------------------------------------------------------------------------------------
@pytest.mark.completeness
def test_no_new_registry_label():
    assert (_BEESITE_BOARD.clinic.get("ats_type") or "") != "beesite"
    assert (_HR4YOU_BOARD.clinic.get("ats_type") or "") != "hr4you"


@pytest.mark.completeness
def test_crawl_wp_jobs_delegates_to_beesite_by_capability():
    with _BEESITE_BOARD.replay():
        rows = crawl_wp_jobs(_BEESITE_BOARD.clinic, session=requests.Session())
    # 30, not the ~40 seen live 2026-09-10: the board changes day to day, this only needs to prove
    # "the full board" rather than 0 (what the generic pre-TASK-40 walk returned).
    assert len(rows) >= 30, f"expected close to the board's full posting count via probed delegation, got {len(rows)}"
    assert all(r["collector"] == "vendor-beesite-v1" for r in rows)


@pytest.mark.completeness
def test_crawl_wp_jobs_delegates_to_hr4you_by_capability():
    with _HR4YOU_BOARD.replay():
        rows = crawl_wp_jobs(_HR4YOU_BOARD.clinic, session=requests.Session())
    # 50, not the ~67 seen live 2026-09-10: the board changes day to day, this only needs to prove
    # "the full board" rather than 0 (what the generic pre-TASK-40 walk returned).
    assert len(rows) >= 50, f"expected close to the board's full posting count via probed delegation, got {len(rows)}"
    assert all(r["collector"] == "vendor-hr4you-v1" for r in rows)


# ---------------------------------------------------------------------------------------------
# fingerprint: the free (no extra request) capability probe crawl_wp_jobs calls.
# ---------------------------------------------------------------------------------------------
@pytest.mark.completeness
def test_beesite_fingerprint():
    with _BEESITE_BOARD.replay():
        r = requests.get("https://jobs.bezirkskliniken-mfr.de/index.php?ac=start", timeout=25)
    assert is_beesite(r)
    with _HR4YOU_BOARD.replay():
        r2 = requests.get("https://www.atos-karriere.de", timeout=25)
    assert not is_beesite(r2)


# ---------------------------------------------------------------------------------------------
# hr4you tenant fan-out: one board, many tenant subdomains -- every job must actually be on its
# own tenant host, not silently collapsed onto one.
# ---------------------------------------------------------------------------------------------
@pytest.mark.completeness
def test_hr4you_spans_multiple_tenants():
    with _HR4YOU_BOARD.replay():
        rows = crawl_hr4you(_HR4YOU_BOARD.clinic, session=requests.Session())
    hosts = {r["source_host"] for r in rows}
    assert len(hosts) >= 10, f"expected postings spread across many hr4you tenants, saw {sorted(hosts)}"


# ---------------------------------------------------------------------------------------------
# regression: beesite's JSON-LD description is double HTML-encoded ("&lt;ul&gt;...", not "<ul>...")
# -- a strip-tags-then-unescape order (the shape every other vendor's html needs) leaves the
# decoded tags behind unstripped. Caught live on id=332 during this task; pinned here.
# ---------------------------------------------------------------------------------------------
@pytest.mark.completeness
def test_beesite_description_has_no_leftover_markup():
    with _BEESITE_BOARD.replay():
        rows = crawl_beesite(_BEESITE_BOARD.clinic, session=requests.Session())
    assert rows
    bad = [r["source_url"] for r in rows
           if r["payload"].get("description") and re.search(r"&lt;|&gt;|<[a-z/][^>]*>", r["payload"]["description"], re.I)]
    assert not bad, f"{len(bad)} rows still carry raw/escaped markup in description, e.g. {bad[:3]}"


# ---------------------------------------------------------------------------------------------
# mutations (TASK-40 step 3): same vocabulary as tests.adapter_contract.MUTATIONS, applied at each
# adapter's OWN seam. Neither adapter routes its HTTP through crawlers.vendor_adapters.get (see the
# module docstrings on beesite.py/hr4you.py -- both are plain requests/session calls instead), so
# the shared suite's vendor-kind mutation appliers (which only patch that one seam) would never
# actually reach either adapter even on the rare chance one of these boards were ever picked as its
# family's mutation representative -- these tests patch the real seam directly instead.
#
# cap_first_page has no target on either board: beesite's job ids and every hr4you tenant's job
# list each come from ONE unpaginated sitemap fetch, not a walked/paged listing -- there is no
# "first page" to cap. (The shared harness's own _no_observable_effect skips this identical shape
# elsewhere for the same reason -- documented here as a deliberate skip, not a silent gap.)
# ---------------------------------------------------------------------------------------------
import pflege_jobs.sources.beesite as _beesite_mod  # noqa: E402
import pflege_jobs.sources.hr4you as _hr4you_mod  # noqa: E402
check_field_completeness, check_public_url = H.check_field_completeness, H.check_public_url


def _mutation_msg(name, board_url, check, detail):
    return f"beesite-hr4you mutation {name} @ {board_url} :: {check} :: {detail}"


def test_mutation_cap_first_page_not_applicable():
    pytest.skip("beesite/hr4you job discovery is one unpaginated sitemap fetch per board/tenant -- "
                "no 'first page' exists to cap (see module docstring above)")


@pytest.mark.mutation
def test_mutation_drop_description_beesite(monkeypatch):
    orig = _beesite_mod._detail

    def mutated(html, url, org):
        j = orig(html, url, org)
        if j:
            j["description"] = None
        return j

    monkeypatch.setattr(_beesite_mod, "_detail", mutated)
    with _BEESITE_BOARD.replay():
        rows = crawl_beesite(_BEESITE_BOARD.clinic, session=requests.Session())
    ok, detail = check_field_completeness(_BEESITE_BOARD.board, rows)
    assert not ok, _mutation_msg("drop_description", _BEESITE_BOARD["url"], "field_completeness",
                                  "expected this check to go red, it stayed green")


@pytest.mark.mutation
def test_mutation_api_self_link_beesite(monkeypatch):
    orig = _beesite_mod._detail

    def mutated(html, url, org):
        j = orig(html, url, org)
        if j:
            j["url"] = j["page"] = "https://mutated-vendor.invalid/api/v1/jobPublication/xyz"
        return j

    monkeypatch.setattr(_beesite_mod, "_detail", mutated)
    with _BEESITE_BOARD.replay():
        rows = crawl_beesite(_BEESITE_BOARD.clinic, session=requests.Session())
    for r in rows:
        r["source_url"] = r["payload"]["url"]
    ok, detail = check_public_url(_BEESITE_BOARD.board, rows)
    assert not ok, _mutation_msg("api_self_link", _BEESITE_BOARD["url"], "public_url",
                                  "expected this check to go red, it stayed green")


@pytest.mark.mutation
def test_mutation_skip_detail_beesite(monkeypatch):
    orig_get = _beesite_mod._get

    def blocked(u, session=None):
        return None if "ac=jobad" in u else orig_get(u, session=session)

    monkeypatch.setattr(_beesite_mod, "_get", blocked)
    with _BEESITE_BOARD.replay():
        rows = crawl_beesite(_BEESITE_BOARD.clinic, session=requests.Session())
    # The shared harness's own read-path-coverage oracle is blind here (the registered careers_url
    # never shows this board's real listing over plain HTTP -- see module docstring), so the
    # board's own known-nonzero posting count is the project-specific oracle this mutation must
    # still turn red: detail fetches are the only source of a title, so blocking them must drop
    # every row, not just some.
    assert rows == [], _mutation_msg("skip_detail", _BEESITE_BOARD["url"], "read_path_coverage",
                                      f"expected 0 rows once every detail fetch is blocked, got {len(rows)}")


@pytest.mark.mutation
def test_mutation_drop_description_hr4you(monkeypatch):
    orig = _hr4you_mod._detail

    def mutated(html, url, org):
        j = orig(html, url, org)
        if j:
            j["description"] = None
        return j

    monkeypatch.setattr(_hr4you_mod, "_detail", mutated)
    with _HR4YOU_BOARD.replay():
        rows = crawl_hr4you(_HR4YOU_BOARD.clinic, session=requests.Session())
    ok, detail = check_field_completeness(_HR4YOU_BOARD.board, rows)
    assert not ok, _mutation_msg("drop_description", _HR4YOU_BOARD["url"], "field_completeness",
                                  "expected this check to go red, it stayed green")


@pytest.mark.mutation
def test_mutation_api_self_link_hr4you(monkeypatch):
    orig = _hr4you_mod._detail

    def mutated(html, url, org):
        j = orig(html, url, org)
        if j:
            j["url"] = j["page"] = "https://mutated-vendor.invalid/api/v1/jobPublication/xyz"
        return j

    monkeypatch.setattr(_hr4you_mod, "_detail", mutated)
    with _HR4YOU_BOARD.replay():
        rows = crawl_hr4you(_HR4YOU_BOARD.clinic, session=requests.Session())
    for r in rows:
        r["source_url"] = r["payload"]["url"]
    ok, detail = check_public_url(_HR4YOU_BOARD.board, rows)
    assert not ok, _mutation_msg("api_self_link", _HR4YOU_BOARD["url"], "public_url",
                                  "expected this check to go red, it stayed green")


@pytest.mark.mutation
def test_mutation_skip_detail_hr4you(monkeypatch):
    orig_get = _hr4you_mod._get

    def blocked(u, session=None):
        return None if "/job/view/" in u else orig_get(u, session=session)

    monkeypatch.setattr(_hr4you_mod, "_get", blocked)
    with _HR4YOU_BOARD.replay():
        rows = crawl_hr4you(_HR4YOU_BOARD.clinic, session=requests.Session())
    assert rows == [], _mutation_msg("skip_detail", _HR4YOU_BOARD["url"], "read_path_coverage",
                                      f"expected 0 rows once every detail fetch is blocked, got {len(rows)}")
