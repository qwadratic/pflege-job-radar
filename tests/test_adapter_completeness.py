"""Adapter-vs-board completeness harness (TASK-26/27). Read tests/adapter_contract.py in full first --
this file only wires its contract (checks/mutations) onto the live board registry and onto the exact
code path app/crawl.py runs (app.crawl._vendor_rows / app.crawl._seed_obs, group_portal_for first).

The board is the oracle, never our own parser. Five checks per (adapter family, board):
  read-path coverage     -- crawlers.vendor_adapters/requests calls the adapter made cover every
                             API-shaped read path the board's own client uses (missing_read_paths).
  declared-total parity  -- rows returned == the board's own claimed count (declared_total).
  field completeness     -- description/city/datePosted/employmentType populated on at least one row
                             (a field entirely absent across every row is a dropped field, not a
                             source limitation -- see adapter_contract.py's field completeness note).
  public url              -- every stored url opens as a posting, never an API self-link.
  round trip               -- up to 5 stored urls resolve live with a title match; snapshotted.

Plus one mutation meta-test per tests.adapter_contract.MUTATIONS key, run on ONE representative board
per adapter family (the family's biggest board), asserting the matching check goes red and nothing
else does. The oracle fetch (client_read_paths, the api_json probe, round-trip fetches) always goes
through urllib directly -- never through requests/crawlers.vendor_adapters.get -- so a mutation that
patches the adapter's own HTTP seam can never also corrupt the ground truth it is checked against.

    .venv/bin/python -m pytest tests/test_adapter_completeness.py -m completeness -q          # everything
    .venv/bin/python -m pytest tests/test_adapter_completeness.py -k smartrecruiters -q       # one family
    .venv/bin/python -m pytest tests/test_adapter_completeness.py -k "kbo.de" -q              # one board
    .venv/bin/python -m pytest tests/test_adapter_completeness.py -m mutation -q              # meta-tests only
"""
import json
import os
import sys
import urllib.request
from urllib.parse import urlparse

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import adapter_contract as AC  # noqa: E402
from crawlers import vendor_adapters as VA  # noqa: E402
import app.crawl as AppCrawl  # noqa: E402
from app import data as D  # noqa: E402


def _offline_reason():
    if AC.offline():
        return "PFLEGE_TESTS_OFFLINE=1"
    try:
        req = urllib.request.Request(AC.PROXY + "/clinics?select=clinic_id&limit=1", headers=AC.HDR)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        return f"registry proxy unreachable: {type(e).__name__}: {e}"
    return None


_SKIP_REASON = _offline_reason()
if _SKIP_REASON:
    pytest.skip(f"adapter-completeness harness offline: {_SKIP_REASON}", allow_module_level=True)

# --- board registry, at collection time (requirement 1) ----------------------------------------
_BOARDS_BY_URL = AC.boards()
_BOARD_LIST = sorted(_BOARDS_BY_URL.values(), key=lambda b: -len(b["clinics"]))


def _family_of(board):
    """The actual code path this board runs through, one bucket per adapter -- not per ats_type
    label (concludis/typo3_jobs/talention/self_hosted/wp_jobs all share crawl_wp_jobs, one family)."""
    if board["kind"] != "vendor":
        return "seeded:" + board["vendor"]
    if VA.group_portal_for(board["clinics"][0]):
        return "group_portal"
    return board.get("adapter") or board["vendor"]


def _board_host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def _board_id(board):
    return f"{board['vendor']}__{_board_host(board['url'])}"


_seen, _BOARD_IDS = {}, []
for _b in _BOARD_LIST:
    _base = _board_id(_b)
    _n = _seen.get(_base, 0)
    _seen[_base] = _n + 1
    _BOARD_IDS.append(_base if _n == 0 else f"{_base}-{_n}")

# boards per family (biggest first), for the mutation meta-tests
_FAMILY_BOARDS = {}
for _b in _BOARD_LIST:
    _FAMILY_BOARDS.setdefault(_family_of(_b), []).append(_b)


# ---------------------------------------------------------------------------------------------
# run_adapter: the SAME code path app/crawl.py uses, dispatch by board["kind"] exactly as
# app.crawl.execute() does (== "vendor" -> _vendor_rows, everything else -> _seed_obs). Looked up
# through the AppCrawl module object (not imported by name) so a mutation test's monkeypatch on
# AppCrawl._vendor_rows / AppCrawl._seed_obs is actually exercised.
# ---------------------------------------------------------------------------------------------
def run_adapter(board):
    c = board["clinics"][0]
    log = lambda *a: None  # noqa: E731
    with AC.RecordCalls() as rec:
        if board["kind"] == "vendor":
            rows = AppCrawl._vendor_rows(board, c, requests.Session(), log)
        else:
            rows, _st = AppCrawl._seed_obs(board, c, D.towns(), log)
    return rows, rec.urls


@pytest.fixture(scope="module", params=_BOARD_LIST, ids=_BOARD_IDS)
def board(request):
    return request.param


@pytest.fixture(scope="module")
def adapter_result(board):
    """One fetch per board, shared by every check function that asks for it (requirement 2)."""
    return run_adapter(board)


# client_read_paths (+ one api-total probe) memoised in a plain dict, not a fixture -- it must
# survive being asked for again from inside a mutation test (which explicitly warms it BEFORE
# patching anything), regardless of which test happens to run first for a given board.
_CLIENT_CACHE = {}


def _client_for(board):
    if board["url"] in _CLIENT_CACHE:
        return _CLIENT_CACHE[board["url"]]
    c = AC.client_read_paths(board["url"])
    api_json = None
    if not c.get("error") and c.get("api_urls"):
        u = sorted(c["api_urls"])[0]
        try:
            req = urllib.request.Request(u, headers={"User-Agent": AC.UA, "Accept-Language": "de-DE,de;q=0.9"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8", "replace")
            AC.save(board["url"], u, body, resp.status, "application/json")
            api_json = json.loads(body)
        except Exception:
            api_json = None
    c["api_json"] = api_json
    _CLIENT_CACHE[board["url"]] = c
    return c


# ---------------------------------------------------------------------------------------------
# field accessors: vendor rows are {payload: {title, loc:[{city}], description, datePosted,
# employmentType, url}}; seeded rows are the flat observation shape (career_crawl/bite/pi_asp) with
# title/city/description/first_published/employment_types/source_url directly on the row.
# ---------------------------------------------------------------------------------------------
def _field(kind, row, name):
    if kind == "vendor":
        p = row.get("payload") or {}
        if name == "title":
            return p.get("title")
        if name == "description":
            return p.get("description")
        if name == "city":
            return ((p.get("loc") or [{}])[0] or {}).get("city")
        if name == "datePosted":
            return p.get("datePosted")
        if name == "employmentType":
            return p.get("employmentType")
    else:
        if name == "datePosted":
            return row.get("first_published")
        if name == "employmentType":
            return row.get("employment_types") or None
        return row.get(name)
    return None


def _url_of(row):
    return row.get("source_url")


# ---------------------------------------------------------------------------------------------
# the five checks -- pure functions, (ok, detail) -- so both the live tests and the mutation
# meta-test can call them and inspect every check's outcome, not just assert one.
# ---------------------------------------------------------------------------------------------
def check_read_path_coverage(board, calls, client):
    if client.get("error"):
        return True, f"client page unavailable ({client['error']}) -- nothing to compare read paths against"
    missing = AC.missing_read_paths(calls, client)
    if missing:
        return False, f"{len(missing)} client read path(s) never called by the adapter: {sorted(missing)[:3]}"
    return True, "ok"


def check_declared_total_parity(board, rows, client):
    if client.get("error"):
        return True, f"client page unavailable ({client['error']}) -- no declared total to compare"
    total = AC.declared_total(client.get("html"), client.get("api_json"))
    if total is None:
        return True, "board declares no parseable total (no totalFound/numberOfItems/'N Stellen') -- nothing to compare"
    if len(rows) != total:
        return False, f"board declares {total}, adapter returned {len(rows)}"
    return True, "ok"


def check_field_completeness(board, rows):
    if not rows:
        return True, "no rows returned (the count check already covers the zero-row case)"
    missing = [name for name in ("description", "city", "datePosted", "employmentType")
               if not any(_field(board["kind"], r, name) for r in rows)]
    if missing:
        return False, f"{', '.join(missing)} populated on 0/{len(rows)} rows"
    return True, "ok"


def check_public_url(board, rows):
    if not rows:
        return True, "no rows returned"
    bad = [u for u in (_url_of(r) for r in rows) if not AC.is_public_url(u)]
    if bad:
        return False, f"{len(bad)}/{len(rows)} stored urls look like an API self-link, e.g. {bad[0]!r}"
    return True, "ok"


def _title_resolves(title, html):
    import re
    core = re.sub(r"\(.*?\)", " ", title or "")
    words = [w for w in re.sub(r"[^\wäöüÄÖÜß ]", " ", core).lower().split() if len(w) > 3][:4]
    if not words:
        return True
    page = html.lower()
    return sum(1 for w in words if w in page) >= max(1, len(words) - 1)


def check_round_trip(board, rows):
    public = [r for r in rows if AC.is_public_url(_url_of(r))][:5]
    if not public:
        return True, "no public urls to sample"
    bad = []
    for r in public:
        u = _url_of(r)
        try:
            req = urllib.request.Request(u, headers={"User-Agent": AC.UA, "Accept-Language": "de-DE,de;q=0.9"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8", "replace")
                status = resp.status
        except Exception as e:
            bad.append(f"{u} ({type(e).__name__})")
            continue
        AC.save(board["url"], u, body, status)
        title = _field(board["kind"], r, "title")
        if title and not _title_resolves(title, body):
            bad.append(f"{u} (title {title!r} not found on the live page)")
    if bad:
        return False, f"{len(bad)}/{len(public)} sampled urls failed round trip: {bad[:3]}"
    return True, "ok"


def _msg(board, check, detail):
    return f"{board['vendor']} @ {board['url']} :: {check} :: {detail}"


# ---------------------------------------------------------------------------------------------
# live checks -- one pytest id per (adapter_family/board_host via the `board` fixture id, check)
# ---------------------------------------------------------------------------------------------
@pytest.mark.network
@pytest.mark.completeness
def test_read_path_coverage(board, adapter_result):
    _rows, calls = adapter_result
    client = _client_for(board)
    ok, detail = check_read_path_coverage(board, calls, client)
    assert ok, _msg(board, "read-path coverage", detail)


@pytest.mark.network
@pytest.mark.completeness
def test_declared_total_parity(board, adapter_result):
    rows, _calls = adapter_result
    client = _client_for(board)
    ok, detail = check_declared_total_parity(board, rows, client)
    assert ok, _msg(board, "declared-total parity", detail)


@pytest.mark.network
@pytest.mark.completeness
def test_field_completeness(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = check_field_completeness(board, rows)
    assert ok, _msg(board, "field completeness", detail)


@pytest.mark.network
@pytest.mark.completeness
def test_public_url(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = check_public_url(board, rows)
    assert ok, _msg(board, "public url", detail)


@pytest.mark.network
@pytest.mark.completeness
def test_round_trip(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = check_round_trip(board, rows)
    assert ok, _msg(board, "round trip", detail)


# ---------------------------------------------------------------------------------------------
# mutations (TASK-27): apply each MUTATIONS breakage on one representative board per family and
# prove exactly the matching check goes red. Mutations patch the exact seam app/crawl.py itself
# calls (AppCrawl._vendor_rows / AppCrawl._seed_obs for description/url mutations) or the shared
# HTTP entrypoint every adapter funnels through (crawlers.vendor_adapters.get for vendor kind,
# requests.Session.request for seeded kind, since career_crawl/bite/pi_asp all sit on a
# requests.Session) for the two fetch-shaped mutations -- never a hand-picked per-family internal,
# so the same four appliers cover every family without per-family plumbing.
# ---------------------------------------------------------------------------------------------
class _FailResp:
    ok = False
    status_code = 404

    def __init__(self, url=""):
        self.url, self.text = url, ""

    def json(self):
        raise ValueError("mutation: blocked, no json body")

    def raise_for_status(self):
        raise requests.HTTPError("mutation: blocked")


def _apply_drop_description(monkeypatch, board):
    def null_desc(row):
        if board["kind"] == "vendor":
            (row.get("payload") or {})["description"] = None
        else:
            row["description"] = None
        return row

    _wrap_rows(monkeypatch, board, null_desc)


def _apply_api_self_link(monkeypatch, board):
    import hashlib

    def fake_url(row):
        old = _url_of(row) or ""
        fake = "https://mutated-vendor.invalid/api/v1/jobPublication/" + hashlib.sha1(old.encode()).hexdigest()[:12]
        row["source_url"] = fake
        if board["kind"] == "vendor":
            (row.get("payload") or {})["url"] = fake
        else:
            row["source_ref"] = row["external_url"] = fake
        return row

    _wrap_rows(monkeypatch, board, fake_url)


def _wrap_rows(monkeypatch, board, mutate_row):
    """Wrap AppCrawl._vendor_rows / _seed_obs (exactly what run_adapter calls) so every row it
    returns is run through `mutate_row` -- the same chokepoint app/crawl.py itself calls, for
    either board kind, with no per-family branch needed."""
    target = "_vendor_rows" if board["kind"] == "vendor" else "_seed_obs"
    orig = getattr(AppCrawl, target)

    if target == "_vendor_rows":
        def wrapped(*a, **k):
            return [mutate_row(r) for r in orig(*a, **k)]
    else:
        def wrapped(*a, **k):
            rows, st = orig(*a, **k)
            return [mutate_row(r) for r in rows], st

    monkeypatch.setattr(AppCrawl, target, wrapped)


def _apply_cap_first_page(monkeypatch, board):
    """Cap every read-path endpoint (by shape, see adapter_contract.endpoint_key) to one successful
    call -- whichever pagination shape the adapter loops with (offset=, page=, tx_solr[page]=,
    /Jobs/<n>, ...), the second hit on the same listing endpoint now fails, forcing that loop to
    stop after page 1. Detail-page fetches keep their own distinct key per posting and are
    unaffected."""
    seen = set()

    def capped(url):
        key = AC.endpoint_key(url)
        if key in seen:
            return True
        seen.add(key)
        return False

    _patch_http(monkeypatch, board, capped)


def _apply_skip_detail(monkeypatch, board):
    """Block every URL that shapes like one of the board's own posting-list/detail API read paths
    (adapter_contract.API_HINTS) -- the adapter never gets to fetch the detail/listing data those
    endpoints carry."""
    import re
    patterns = [re.compile(rx, re.I) for _, rx in AC.API_HINTS]

    def blocked(url):
        return any(p.search(url) for p in patterns)

    _patch_http(monkeypatch, board, blocked)


def _patch_http(monkeypatch, board, should_fail):
    """Patch whichever HTTP seam this board's kind actually calls -- crawlers.vendor_adapters.get
    for vendor kind, requests.Session.request for seeded kind (career_crawl/bite/pi_asp's requests
    sessions, and the transient session requests.get() makes internally, all funnel through it)."""
    if board["kind"] == "vendor":
        orig = VA.get

        def patched(u, timeout=30, session=None):
            return _FailResp(u) if should_fail(u) else orig(u, timeout=timeout, session=session)

        monkeypatch.setattr(VA, "get", patched)
    else:
        orig = requests.Session.request

        def patched(self_, method, url, *a, **k):
            return _FailResp(url) if should_fail(url) else orig(self_, method, url, *a, **k)

        monkeypatch.setattr(requests.Session, "request", patched)


_MUTATION_APPLIERS = {
    "cap_first_page": _apply_cap_first_page,
    "drop_description": _apply_drop_description,
    "api_self_link": _apply_api_self_link,
    "skip_detail": _apply_skip_detail,
}
_TARGET_CHECK = {
    "cap_first_page": "declared_total_parity",
    "drop_description": "field_completeness",
    "api_self_link": "public_url",
    "skip_detail": "read_path_coverage",
}


def _no_observable_effect(mutation_name, board, client, baseline_rows, baseline_calls, rows, calls):
    """True when this family's adapter (or this particular board) has nothing this mutation could
    break -- either the mutation changed nothing observable (e.g. cap_first_page on a board with no
    second listing page to cap), or the check it targets has no oracle to react to on this board at
    all (e.g. declared_total_parity when the board declares no parseable total). Skip rather than
    assert a red that structurally cannot happen, instead of hard-coding which families paginate or
    expose an API read path."""
    if mutation_name == "cap_first_page":
        total = None if client.get("error") else AC.declared_total(client.get("html"), client.get("api_json"))
        return total is None or len(rows) == len(baseline_rows)
    if mutation_name == "drop_description":
        return not any(_field(board["kind"], r, "description") for r in baseline_rows)
    if mutation_name == "api_self_link":
        return not baseline_rows
    if mutation_name == "skip_detail":
        # Raw call-URL equality under-detects: an adapter with only ONE api_url shape in its whole
        # flow (e.g. bite's single postings/search call) still "calls" that blocked endpoint -- the
        # url is recorded by RecordCalls before the request fails -- so missing_read_paths never
        # sees a gap there even though the board now returns zero rows. Comparing the actual set of
        # missing read paths (what check_read_path_coverage itself asserts on) instead of the raw
        # call list is the precise question: did blocking leave any client api_url newly uncovered.
        return (not client.get("api_urls")
                or AC.missing_read_paths(calls, client) == AC.missing_read_paths(baseline_calls, client))
    return False


# The board with the most clinics is the default representative, but a handful of real boards
# (see crawl_personio's own docstring on ProSomno) are known to yield 0 rows for their labelled
# vendor -- a bad representative for a mutation test that needs real rows to break. Try up to 5
# candidates per family, biggest first, and keep the first that actually returns rows (falling back
# to the biggest if none do); cached once per family and reused across all 4 mutation_names.
_FAMILY_BASELINE_CACHE = {}


def _representative_and_baseline(family):
    if family in _FAMILY_BASELINE_CACHE:
        return _FAMILY_BASELINE_CACHE[family]
    chosen = rows = calls = None
    for cand in _FAMILY_BOARDS[family][:5]:
        r, c = run_adapter(cand)
        chosen, rows, calls = cand, r, c
        if r:
            break
    client = _client_for(chosen)
    # baseline outcome of the 4 non-round-trip checks -- a check already red before any mutation
    # (a real, separate finding the live suite already reports) must not be blamed as "collateral
    # damage" from this mutation.
    baseline_checks = {
        "read_path_coverage": check_read_path_coverage(chosen, calls, client)[0],
        "declared_total_parity": check_declared_total_parity(chosen, rows, client)[0],
        "field_completeness": check_field_completeness(chosen, rows)[0],
        "public_url": check_public_url(chosen, rows)[0],
    }
    _FAMILY_BASELINE_CACHE[family] = (chosen, rows, calls, baseline_checks)
    return _FAMILY_BASELINE_CACHE[family]


@pytest.mark.mutation
@pytest.mark.network
@pytest.mark.parametrize("mutation_name", sorted(AC.MUTATIONS))
@pytest.mark.parametrize("family", sorted(_FAMILY_BOARDS))
def test_mutation(monkeypatch, family, mutation_name):
    board, baseline_rows, baseline_calls, baseline_checks = _representative_and_baseline(family)
    client = _client_for(board)  # warm the oracle cache BEFORE any patch is applied

    _MUTATION_APPLIERS[mutation_name](monkeypatch, board)
    rows, calls = run_adapter(board)

    if _no_observable_effect(mutation_name, board, client, baseline_rows, baseline_calls, rows, calls):
        pytest.skip(_msg(board, f"mutation {mutation_name}", "adapter shape has nothing this mutation can break here"))

    checks = {
        "read_path_coverage": check_read_path_coverage(board, calls, client),
        "declared_total_parity": check_declared_total_parity(board, rows, client),
        "field_completeness": check_field_completeness(board, rows),
        "public_url": check_public_url(board, rows),
    }
    target = _TARGET_CHECK[mutation_name]
    ok, detail = checks[target]
    assert not ok, _msg(board, f"mutation {mutation_name} -> {target}", "expected this check to go red, it stayed green")
    for name, (ok2, detail2) in checks.items():
        if name == target or not baseline_checks[name]:
            continue  # already red before the mutation -- a pre-existing finding, not collateral damage
        assert ok2, _msg(board, f"mutation {mutation_name} -> collateral {name}", detail2)
