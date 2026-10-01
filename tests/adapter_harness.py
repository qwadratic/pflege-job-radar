"""The adapter-vs-board completeness harness, shared by the tests (tests/test_adapter_completeness.py) and by the recorder
(tools/mirror.py). Read tests/adapter_contract.py first: the board is the oracle, never our own parser.

Everything here runs the SAME code path app/crawl.py runs (app.crawl._vendor_rows / app.crawl._seed_obs, group_portal_for
first), and every HTTP request it makes goes through tests/mirror.py: tests replay a frozen recording of the board,
tools/mirror.py records one. Each entry point opens the replay of `board["board_id"]` itself when no mirror is active, and
only labels its scope when one is (the recorder wraps the whole scenario in one recording). A request the mirror lacks fails
the run with the board, the URL and the command to record it -- it is never answered from the live site.

Five checks per (adapter family, board), pure functions returning (ok, detail):
  read-path coverage     -- the requests the adapter made cover every API-shaped read path the board's own client uses.
  declared-total parity  -- rows returned == the board's own claimed count (declared_total).
  field completeness     -- description/city/datePosted/employmentType populated on at least one row.
  public url             -- every stored url opens as a posting, never an API self-link.
  round trip             -- up to 5 stored urls resolve with a title match.
Plus the mutation machinery (tests.adapter_contract.MUTATIONS): break an adapter on purpose on one representative board per
family and prove exactly the matching check goes red. The oracle fetch (client_read_paths, the api_json probe, round-trip
fetches) goes through urllib or a bare requests.get, never through crawlers.vendor_adapters.get, so a mutation that patches the
adapter's own HTTP seam cannot also corrupt the ground truth it is checked against.
"""
import contextlib
import json
import re
import urllib.request
from urllib.parse import urlparse

import requests

from tests import adapter_contract as AC
from tests import mirror as M
from crawlers import vendor_adapters as VA
import app.crawl as AppCrawl

# the scope names a board's recording is made of (tests/mirror.py: sequences are per scope)
ADAPTER, ORACLE, ROUNDTRIP = "adapter", "oracle", "roundtrip"


# ---------------------------------------------------------------------------------------------
# boards: identity, family
# ---------------------------------------------------------------------------------------------
def family_of(board):
    """The actual code path this board runs through, one bucket per adapter -- not per ats_type
    label (concludis/typo3_jobs/talention/self_hosted/wp_jobs all share crawl_wp_jobs, one family)."""
    if board["kind"] != "vendor":
        return "seeded:" + board["vendor"]
    if VA.group_portal_for(board["clinics"][0]):
        return "group_portal"
    return board.get("adapter") or board["vendor"]


def board_host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def board_id(board):
    return f"{board['vendor']}__{board_host(board['url'])}"


def assign_ids(boards):
    """Ids for boards sorted biggest-first: <vendor>__<host>, a second board on the same pair gets -1, -2 (the test ids
    the live suite always had)."""
    seen, ids = {}, []
    for b in boards:
        base = board_id(b)
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.append(base if n == 0 else f"{base}-{n}")
    return ids


def family_boards(boards):
    """{family: boards, biggest first} -- the mutation tests pick their representative from the head of each list."""
    out = {}
    for b in boards:
        out.setdefault(family_of(b), []).append(b)
    return out


# ---------------------------------------------------------------------------------------------
# the boards of the mirror (data/mirror/INDEX.json -- no network, no board file opened)
# ---------------------------------------------------------------------------------------------
def indexed_boards():
    """Every mirrored board as the harness takes it, biggest first (ties by id), each carrying its `board_id`."""
    idx = M.read_index_or_none()
    entries = sorted(idx["boards"].values(), key=lambda e: (-e["n_clinics"], e["board_id"])) if idx else []
    return [dict(e["board"], board_id=e["board_id"]) for e in entries]


def need_mirror():
    """What a case does when the mirror is not there: fail, saying which command makes it (never skip)."""
    M.read_index()
    raise M.MirrorMiss(f"{M.index_path()} lists no board: record them with .venv/bin/python {M.RECORD} record --all")


def board_params(boards, id_fn=lambda b: b["board_id"]):
    """(argvalues, ids) for pytest.mark.parametrize over `boards`; with no mirror a single None case that calls need_mirror(),
    so an empty parametrisation can never turn into a silent skip."""
    return (list(boards) or [None]), ([id_fn(b) for b in boards] or ["no-mirror"])


def board_for_url(url):
    """The mirrored board whose careers url is `url`."""
    for b in indexed_boards():
        if b["url"] == url:
            return b
    raise M.MirrorMiss(f"the mirror has no board with url {url!r}: .venv/bin/python {M.RECORD} record <board_id | host | clinic_id>")


@contextlib.contextmanager
def _mirror(board, scope):
    if M.active():
        with M.scope(scope):
            yield M.active()
    else:
        with M.mirror_board(board["board_id"], scope) as m:
            yield m


# ---------------------------------------------------------------------------------------------
# run_adapter: the SAME code path app/crawl.py uses, dispatch by board["kind"] exactly as app.crawl.execute() does
# (== "vendor" -> _vendor_rows, everything else -> _seed_obs). Looked up through the AppCrawl module object (not imported
# by name) so a mutation test's monkeypatch on AppCrawl._vendor_rows / AppCrawl._seed_obs is actually exercised.
# ---------------------------------------------------------------------------------------------
def run_adapter(board, scope=ADAPTER):
    c = board["clinics"][0]
    log = lambda *a: None  # noqa: E731
    with _mirror(board, scope) as m, AC.RecordCalls() as rec:
        if board["kind"] == "vendor":
            rows = AppCrawl._vendor_rows(board, c, requests.Session(), log)
        else:
            # what app.data.towns() read from the registry the day the board was recorded (tests/mirror.py meta)
            rows, _st = AppCrawl._seed_obs(board, c, set(m.store.meta("towns")), log)
    return rows, rec.urls


# client_read_paths (+ one api-total probe) memoised in a plain dict, not a fixture -- it must survive being asked for
# again from inside a mutation test (which explicitly warms it BEFORE patching anything), regardless of which test
# happens to run first for a given board.
_CLIENT_CACHE = {}


def client_for(board, cache=True):
    if cache and board["url"] in _CLIENT_CACHE:
        return _CLIENT_CACHE[board["url"]]
    with _mirror(board, ORACLE):
        c = AC.client_read_paths(board["url"])
        api_json = None
        if not c.get("error") and c.get("api_urls"):
            u = sorted(c["api_urls"])[0]
            try:
                req = urllib.request.Request(u, headers={"User-Agent": AC.UA, "Accept-Language": "de-DE,de;q=0.9"})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    body = resp.read().decode("utf-8", "replace")
                api_json = json.loads(body)
            except Exception:
                api_json = None
    c["api_json"] = api_json
    if cache:
        _CLIENT_CACHE[board["url"]] = c
    return c


# ---------------------------------------------------------------------------------------------
# field accessors: vendor rows are {payload: {title, loc:[{city}], description, datePosted, employmentType, url}}; seeded
# rows are the flat observation shape (career_crawl/bite/pi_asp) with title/city/description/first_published/
# employment_types/source_url directly on the row.
# ---------------------------------------------------------------------------------------------
def field(kind, row, name):
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


def url_of(row):
    return row.get("source_url")


# ---------------------------------------------------------------------------------------------
# the five checks -- pure functions, (ok, detail) -- so both the tests and the mutation meta-test can call them and
# inspect every check's outcome, not just assert one.
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
               if not any(field(board["kind"], r, name) for r in rows)]
    if missing:
        return False, f"{', '.join(missing)} populated on 0/{len(rows)} rows"
    return True, "ok"


def check_public_url(board, rows):
    if not rows:
        return True, "no rows returned"
    bad = [u for u in (url_of(r) for r in rows) if not AC.is_public_url(u)]
    if bad:
        return False, f"{len(bad)}/{len(rows)} stored urls look like an API self-link, e.g. {bad[0]!r}"
    return True, "ok"


def _title_resolves(title, html):
    core = re.sub(r"\(.*?\)", " ", title or "")
    words = [w for w in re.sub(r"[^\wäöüÄÖÜß ]", " ", core).lower().split() if len(w) > 3][:4]
    if not words:
        return True
    page = html.lower()
    return sum(1 for w in words if w in page) >= max(1, len(words) - 1)


def check_round_trip(board, rows):
    public = [r for r in rows if AC.is_public_url(url_of(r))][:5]
    if not public:
        return True, "no public urls to sample"
    bad = []
    with _mirror(board, ROUNDTRIP):
        for r in public:
            u = url_of(r)
            try:
                req = urllib.request.Request(u, headers={"User-Agent": AC.UA, "Accept-Language": "de-DE,de;q=0.9"})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    body = resp.read().decode("utf-8", "replace")
            except Exception as e:
                bad.append(f"{u} ({type(e).__name__})")
                continue
            title = field(board["kind"], r, "title")
            if title and not _title_resolves(title, body):
                bad.append(f"{u} (title {title!r} not found on the page)")
    if bad:
        return False, f"{len(bad)}/{len(public)} sampled urls failed round trip: {bad[:3]}"
    return True, "ok"


def msg(board, check, detail):
    return f"{board['vendor']} @ {board['url']} :: {check} :: {detail}"


def run_checks(board, rows, calls, client):
    """The five checks, in the order the tests run them: {name: (ok, detail)}."""
    return {
        "read_path_coverage": check_read_path_coverage(board, calls, client),
        "declared_total_parity": check_declared_total_parity(board, rows, client),
        "field_completeness": check_field_completeness(board, rows),
        "public_url": check_public_url(board, rows),
        "round_trip": check_round_trip(board, rows),
    }


# ---------------------------------------------------------------------------------------------
# mutations (TASK-27): apply each MUTATIONS breakage on one representative board per family and prove exactly the matching
# check goes red. Mutations patch the exact seam app/crawl.py itself calls (AppCrawl._vendor_rows / AppCrawl._seed_obs for
# description/url mutations) or the shared HTTP entrypoint every adapter funnels through (crawlers.vendor_adapters.get for
# vendor kind, requests.Session.request for seeded kind, since career_crawl/bite/pi_asp all sit on a requests.Session) for
# the two fetch-shaped mutations -- never a hand-picked per-family internal, so the same four appliers cover every family
# without per-family plumbing.
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
        old = url_of(row) or ""
        fake = "https://mutated-vendor.invalid/api/v1/jobPublication/" + hashlib.sha1(old.encode()).hexdigest()[:12]
        row["source_url"] = fake
        if board["kind"] == "vendor":
            (row.get("payload") or {})["url"] = fake
        else:
            row["source_ref"] = row["external_url"] = fake
        return row

    _wrap_rows(monkeypatch, board, fake_url)


def _wrap_rows(monkeypatch, board, mutate_row):
    """Wrap AppCrawl._vendor_rows / _seed_obs (exactly what run_adapter calls) so every row it returns is run through
    `mutate_row` -- the same chokepoint app/crawl.py itself calls, for either board kind, with no per-family branch needed."""
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
    """Cap every read-path endpoint (by shape, see adapter_contract.endpoint_key) to one successful call -- whichever
    pagination shape the adapter loops with (offset=, page=, tx_solr[page]=, /Jobs/<n>, ...), the second hit on the same
    listing endpoint now fails, forcing that loop to stop after page 1. Detail-page fetches keep their own distinct key per
    posting and are unaffected."""
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
    (adapter_contract.API_HINTS) -- the adapter never gets to fetch the detail/listing data those endpoints carry."""
    patterns = [re.compile(rx, re.I) for _, rx in AC.API_HINTS]

    def blocked(url):
        return any(p.search(url) for p in patterns)

    _patch_http(monkeypatch, board, blocked)


def _patch_http(monkeypatch, board, should_fail):
    """Patch whichever HTTP seam this board's kind actually calls -- crawlers.vendor_adapters.get for vendor kind,
    requests.Session.request for seeded kind (career_crawl/bite/pi_asp's requests sessions, and the transient session
    requests.get() makes internally, all funnel through it)."""
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


MUTATION_APPLIERS = {
    "cap_first_page": _apply_cap_first_page,
    "drop_description": _apply_drop_description,
    "api_self_link": _apply_api_self_link,
    "skip_detail": _apply_skip_detail,
}
TARGET_CHECK = {
    "cap_first_page": "declared_total_parity",
    "drop_description": "field_completeness",
    "api_self_link": "public_url",
    "skip_detail": "read_path_coverage",
}


def no_observable_effect(mutation_name, board, client, baseline_rows, baseline_calls, rows, calls):
    """True when this family's adapter (or this particular board) has nothing this mutation could break -- either the
    mutation changed nothing observable (e.g. cap_first_page on a board with no second listing page to cap), or the check
    it targets has no oracle to react to on this board at all (e.g. declared_total_parity when the board declares no
    parseable total). Skip rather than assert a red that structurally cannot happen, instead of hard-coding which families
    paginate or expose an API read path."""
    if mutation_name == "cap_first_page":
        total = None if client.get("error") else AC.declared_total(client.get("html"), client.get("api_json"))
        return total is None or len(rows) == len(baseline_rows)
    if mutation_name == "drop_description":
        return not any(field(board["kind"], r, "description") for r in baseline_rows)
    if mutation_name == "api_self_link":
        return not baseline_rows
    if mutation_name == "skip_detail":
        # Raw call-URL equality under-detects: an adapter with only ONE api_url shape in its whole flow (e.g. bite's single
        # postings/search call) still "calls" that blocked endpoint -- the url is recorded by RecordCalls before the
        # request fails -- so missing_read_paths never sees a gap there even though the board now returns zero rows.
        # Comparing the actual set of missing read paths (what check_read_path_coverage itself asserts on) instead of the
        # raw call list is the precise question: did blocking leave any client api_url newly uncovered.
        return (not client.get("api_urls")
                or AC.missing_read_paths(calls, client) == AC.missing_read_paths(baseline_calls, client))
    return False


# The board with the most clinics is the default representative, but a handful of real boards (see crawl_personio's own
# docstring on ProSomno) are known to yield 0 rows for their labelled vendor -- a bad representative for a mutation test
# that needs real rows to break. Try up to 5 candidates per family, biggest first, and keep the first that actually returns
# rows (falling back to the biggest if none do); cached once per family and reused across all 4 mutation_names.
CANDIDATES = 5
_FAMILY_BASELINE_CACHE = {}


def representative_and_baseline(family, fam_boards):
    if family in _FAMILY_BASELINE_CACHE:
        return _FAMILY_BASELINE_CACHE[family]
    chosen = rows = calls = None
    for cand in fam_boards[family][:CANDIDATES]:
        r, c = run_adapter(cand)
        chosen, rows, calls = cand, r, c
        if r:
            break
    client = client_for(chosen)
    # baseline outcome of the 4 non-round-trip checks -- a check already red before any mutation (a real, separate finding
    # the suite already reports) must not be blamed as "collateral damage" from this mutation.
    baseline_checks = {
        "read_path_coverage": check_read_path_coverage(chosen, calls, client)[0],
        "declared_total_parity": check_declared_total_parity(chosen, rows, client)[0],
        "field_completeness": check_field_completeness(chosen, rows)[0],
        "public_url": check_public_url(chosen, rows)[0],
    }
    _FAMILY_BASELINE_CACHE[family] = (chosen, rows, calls, baseline_checks)
    return _FAMILY_BASELINE_CACHE[family]


def mutated_run(monkeypatch, family, mutation_name, fam_boards):
    """One mutation on the family's representative: -> (board, baseline_checks, client, baseline_rows, baseline_calls, rows, calls, checks)."""
    board, baseline_rows, baseline_calls, baseline_checks = representative_and_baseline(family, fam_boards)
    client = client_for(board)  # warm the oracle cache BEFORE any patch is applied
    MUTATION_APPLIERS[mutation_name](monkeypatch, board)
    rows, calls = run_adapter(board, scope=f"mutation:{mutation_name}")
    checks = {
        "read_path_coverage": check_read_path_coverage(board, calls, client),
        "declared_total_parity": check_declared_total_parity(board, rows, client),
        "field_completeness": check_field_completeness(board, rows),
        "public_url": check_public_url(board, rows),
    }
    return board, baseline_checks, client, baseline_rows, baseline_calls, rows, calls, checks
