"""execute()'s same-run board retry (2026-09-16, Ivan: some errors just need a restart to clear)."""
import time

import pytest

from app import config as A, data as D, runs as R
from pflege_jobs import inbox_db as IB
import app.crawl as CR

CLINIC = {"clinic_id": "1", "name": "Test Clinic", "town": "X", "status": "Plan-KH", "routable": True, "walled": False}


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setattr(A, "CRAWL_OUT", tmp_path / "crawl_output")
    R.init()
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [CLINIC], "by_clinic": {"1": CLINIC},
                    "facets": {}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "jobs", lambda: [])
    monkeypatch.setattr(D, "towns", lambda: set())
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(IB, "PATH", str(tmp_path / "inbox.sqlite"))
    monkeypatch.setattr(CR, "_cli", lambda args, log, timeout=1800: 0)
    monkeypatch.setattr(R, "mirror_to_supabase", lambda run: None)
    monkeypatch.setattr(CR, "plan_for", lambda *a, **k: {
        "clinics": [CLINIC], "adapter": [CLINIC], "firecrawl": [], "skipped": [], "boards": 1, "walled": 0, "credits_needed": 0, "credits_left": 0})
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": board})
    monkeypatch.setattr(CR.time, "sleep", lambda *a: None)   # real code sleeps between attempts; tests don't need to
    yield


def test_board_recovers_within_3_attempts_no_issue_recorded(fresh, monkeypatch):
    calls = {"n": 0}

    def flaky(b, c, session, log, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return [{"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}]

    monkeypatch.setattr(CR, "_vendor_rows", flaky)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    assert calls["n"] == 3                     # failed, failed, succeeded -- exactly the 3-attempt ladder
    assert R.list_crawl_issues() == []         # recovered -- nothing goes in the daily report
    assert R.get_run(rid, with_log=False)["status"] == "done"


def test_board_still_failing_after_3_attempts_is_recorded(fresh, monkeypatch):
    def always_fails(b, c, session, log, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(CR, "_vendor_rows", always_fails)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1
    assert issues[0]["board_url"] == "https://x.example/board"
    assert issues[0]["clinic_ids"] == ["1"]
    assert "boom" in issues[0]["error"]
    assert issues[0]["run_id"] == rid


def test_group_portal_board_gets_the_empty_check_on_its_own_first_fetch(fresh, monkeypatch):
    """TASK-72 AC#1, a group-portal edge case found while implementing it: _vendor_rows marks the
    group cache the instant it runs a shared board's real fetch, so checking "already fetched"
    AFTER that call (the old code) was always true right after a group board's own FIRST real
    fetch too -- the empty/failure checks below could never fire for kbo.de/karriere.barmherzige.net
    at all. Checked before the fetch now."""
    from crawlers import vendor_adapters as VA
    kbo_clinic = {"clinic_id": "1", "name": "kbo-Test Klinik", "town": "X", "status": "Plan-KH",
                  "routable": True, "walled": False, "careers_url": ""}
    board = {"kind": "vendor", "vendor": "kbo", "clinics": [kbo_clinic]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://kbo.de/karriere/jobboerse": board})
    monkeypatch.setattr(CR, "plan_for", lambda *a, **k: {
        "clinics": [kbo_clinic], "adapter": [kbo_clinic], "firecrawl": [], "skipped": [], "boards": 1, "walled": 0, "credits_needed": 0, "credits_left": 0})
    monkeypatch.setattr(VA, "crawl_group_portal", lambda c, g, session=None, towns=None: [])
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1 and issues[0]["kind"] == "empty"


def test_zero_rows_with_a_working_session_is_recorded_as_crawl_issue_kind_empty(fresh, monkeypatch):
    """TASK-72 AC#1 clause B: 0 rows but the shared session DID see successful requests -- the board
    was genuinely read and genuinely has nothing right now. Recorded directly as its own crawl_issue
    kind='empty' (not the retry ladder: there is nothing transient here to retry into), replacing
    what used to be a log-only WARNING nobody ever saw again."""
    def genuinely_empty(b, c, session, log, **kw):
        session._attempts = getattr(session, "_attempts", 0) + 3
        session._ok = getattr(session, "_ok", 0) + 3
        return []

    monkeypatch.setattr(CR, "_vendor_rows", genuinely_empty)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1
    assert issues[0]["kind"] == "empty" and issues[0]["board_url"] == "https://x.example/board"
    assert R.get_run(rid, with_log=False)["status"] == "done"   # a genuinely empty board is not a run error


def test_zero_rows_with_no_successful_request_is_a_transport_failure_not_success(fresh, monkeypatch):
    """TASK-72 AC#1 clause A: 0 rows AND every request the shared session made for this board
    failed -- a real network problem, indistinguishable from a raised exception, so it must enter
    the same 3-attempt retry ladder (recorded under the board's own kind, not kind='empty')."""
    def every_request_fails(b, c, session, log, **kw):
        session._attempts = getattr(session, "_attempts", 0) + 4
        return []

    monkeypatch.setattr(CR, "_vendor_rows", every_request_fails)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1
    assert issues[0]["kind"] == "vendor"          # the board's own kind -- the retry-ladder path, not kind=empty
    assert "transport failure" in issues[0]["error"]


def test_seeded_adapter_zero_observations_with_no_error_is_recorded_as_crawl_issue_kind_empty(fresh, monkeypatch):
    """TASK-72 AC#1, the seeded-adapter (non-'vendor' kind, e.g. bite/softgarden/umantis) side of
    the same fix: _seed_obs returning no observations and no 'error' in its stats used to be a
    log-only WARNING -- now recorded directly as its own crawl_issue kind='empty', same as the
    vendor-adapter path."""
    seeded_board = {"kind": "seeded", "vendor": "bite", "clinics": [CLINIC]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": seeded_board})
    monkeypatch.setattr(CR, "_seed_obs", lambda b, c, towns, log: ([], {"job_links_found": 0}))
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1
    assert issues[0]["kind"] == "empty" and issues[0]["board_url"] == "https://x.example/board"
    assert R.get_run(rid, with_log=False)["status"] == "done"   # a genuinely empty board is not a run error


def test_seeded_adapter_error_in_stats_enters_the_retry_ladder(fresh, monkeypatch):
    """The seeded-adapter counterpart of the transport-failure case: _seed_obs reporting its own
    'error' in stats (e.g. 'no umantis instance found on careers page') is a real failure and must
    enter the same 3-attempt retry ladder, recorded under the board's own kind -- not kind=empty."""
    seeded_board = {"kind": "seeded", "vendor": "umantis", "clinics": [CLINIC]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": seeded_board})
    monkeypatch.setattr(CR, "_seed_obs", lambda b, c, towns, log: ([], {"error": "no umantis instance found on careers page"}))
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1
    assert issues[0]["kind"] == "seeded"
    assert "no umantis instance" in issues[0]["error"]


def test_cli_inbox_nonzero_exit_is_recorded_and_fails_the_run(fresh, monkeypatch):
    """TASK-72 AC#4: _cli(["inbox"])'s return code used to be discarded outright -- a failed drain
    left the queue stranded with no trace in either crawl_issues or the run's own status."""
    monkeypatch.setattr(CR, "_vendor_rows", lambda b, c, session, log, **kw: [
        {"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])
    monkeypatch.setattr(CR, "_cli", lambda args, log, timeout=1800: 1 if args == ["inbox"] else 0)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = [i for i in R.list_crawl_issues() if i["kind"] == "intake"]
    assert len(issues) == 1 and "inbox" in issues[0]["error"]
    assert R.get_run(rid, with_log=False)["status"] == "failed"   # errors>0 fails the run even though rows > 0


def test_status_stays_done_when_one_board_errors_but_the_run_otherwise_completes(fresh, monkeypatch):
    """A board that fails outright (exhausting the retry ladder) is recorded in crawl_issues/run_log
    and no longer flips the WHOLE run to "failed" -- that used to conflate normal partial-coverage
    noise (one board out of hundreds) with a genuinely broken run. Confirmed live 2026-09-22: run 120
    (509 new postings, 13796 rows, 1 unrelated board still failing) and run 122 (4 new postings landed,
    a different clinic's Firecrawl agent hit its own cap) both reported status=failed despite real,
    useful work completing. Only a fatal intake-pipeline error (cli inbox/link-cross exit code, or an
    exception in the intake block itself -- TASK-72 AC#4) still fails the run."""
    calls = {"n": 0}

    def one_board_ok_one_board_dead(b, c, session, log, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}]
        raise RuntimeError("dead board")

    board_ok = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}
    board_dead = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board-ok": board_ok, "https://x.example/board-dead": board_dead})
    monkeypatch.setattr(CR, "_vendor_rows", one_board_ok_one_board_dead)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    run = R.get_run(rid, with_log=False)
    assert run["n_rows"] == 1 and run["status"] == "done" and "1 issue" in (run.get("error") or "")


def test_status_is_failed_when_the_intake_pipeline_itself_blows_up(fresh, monkeypatch):
    """TASK-72 AC#4: the intake block (inbox post/drain/link-cross/verify) blowing up must still fail
    the run -- unlike a single board fetch error, this is not per-item noise, it silently strands or
    loses rows that already reached the queue."""
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board-ok": {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}})
    monkeypatch.setattr(CR, "_vendor_rows", lambda b, c, session, log, **kw: [
        {"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])
    monkeypatch.setattr(CR, "_cli", lambda args, log=None: 1)   # every cli invocation "fails" (non-zero exit)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    run = R.get_run(rid, with_log=False)
    assert run["status"] == "failed"


def test_verify_ids_receives_every_touched_posting_not_capped_at_200(fresh, monkeypatch):
    """TASK-72 AC#2: the fallback list(set(ids.values()))[:200] used to silently re-verify only 200
    of the touched-but-not-new postings, in an arbitrary set() order, with no truncated flag."""
    refs = [f"https://x.example/job/{i}" for i in range(250)]
    posting_ids = {ref: i for i, ref in enumerate(refs)}
    monkeypatch.setattr(D, "jobs", lambda: [{"posting_id": i} for i in range(250)])   # all "already existed"
    monkeypatch.setattr(CR, "_vendor_rows", lambda b, c, session, log, **kw: [
        {"kind": "jobposting", "payload": {"url": "https://x.example/dummy"}, "source_url": "https://x.example/dummy"}])
    monkeypatch.setattr(IB, "loaded_refs", lambda run_id, path=None: list(refs))
    monkeypatch.setattr(CR, "_posting_ids_for_refs", lambda rs: dict(posting_ids))
    seen = {}
    monkeypatch.setattr(CR, "_verify_ids", lambda ids_, log: seen.setdefault("ids", set(ids_)))

    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)

    assert seen["ids"] == set(posting_ids.values())      # all 250, not capped to 200


def test_verify_mode_pushes_verdicts_and_records_city_mismatch(fresh, monkeypatch):
    """mode='verify' re-checks postings instead of crawling; a page that names a different city than
    the stored one is recorded for review (this process cannot patch a posting's city itself)."""
    from app import data as D
    import pflege_jobs.verify as V

    D._snap["jobs"] = [{"posting_id": 7, "title": "Pflegefachkraft", "clinic_id": "1", "city": "Neuburg",
                        "status": "open", "external_url": "https://x.example/job/7"}]
    monkeypatch.setattr(D, "jobs", lambda: D._snap["jobs"])
    monkeypatch.setattr(V, "verify_all", lambda rows, **kw: [
        {"posting_id": 7, "verify_status": "live", "verify_http": 200, "verified_at": "2026-09-16T00:00:00+00:00",
         "verify_note": "title tokens 2/2", "method": "http", "city": "Oberhausen", "plz": "46045",
         "loc_source": "jsonld", "final_url": "https://x.example/job/7"}])
    pushed = {}
    monkeypatch.setattr("pflege_jobs.sinks.EdgeSink._post", lambda self, body: pushed.update(body) or {"verify": 1})

    rid = R.create_run("clinic", "1", "verify")
    CR.execute(rid)

    assert [r["posting_id"] for r in pushed["verify"]] == [7]
    assert set(pushed["verify"][0]) == set(V.VERIFY_FIELDS)      # extras stripped before the ingest op
    issues = R.list_crawl_issues()
    assert len(issues) == 1 and issues[0]["kind"] == "city"
    assert "Oberhausen" in issues[0]["error"] and "Neuburg" in issues[0]["error"]
    assert R.get_run(rid, with_log=False)["status"] == "done"


def test_verify_scope_all_still_reaches_postings_with_no_clinic_id(fresh, monkeypatch):
    """2026-09-18 crawler review: _run_verify filtered by clinic_id in the scope's clinic set, so a
    posting the registry Matcher left unattributed (clinic_id null) was excluded from every
    scheduled re-verification, including the daily scope='all' run, and kept its last status/city
    forever (confirmed live: 198 of 2676 open postings, 7.4%)."""
    from app import data as D
    import pflege_jobs.verify as V

    D._snap["jobs"] = [
        {"posting_id": 7, "title": "Pflegefachkraft", "clinic_id": "1", "city": "X",
         "status": "open", "external_url": "https://x.example/job/7"},
        {"posting_id": 8, "title": "Pflegefachkraft", "clinic_id": None, "city": "Y",
         "status": "open", "external_url": "https://x.example/job/8"},
    ]
    monkeypatch.setattr(D, "jobs", lambda: D._snap["jobs"])
    seen = {}
    monkeypatch.setattr(V, "verify_all", lambda rows, **kw: seen.setdefault("ids", {r["posting_id"] for r in rows}) and [])
    monkeypatch.setattr("pflege_jobs.sinks.EdgeSink._post", lambda self, body: {"verify": 0})

    rid = R.create_run("all", "", "verify")
    CR.execute(rid)

    assert seen["ids"] == {7, 8}, "posting 8 (clinic_id=None) must be included when scope is 'all'"


def test_verify_scope_clinic_still_excludes_postings_with_no_clinic_id(fresh, monkeypatch):
    """The null-clinic-id inclusion is scoped to scope='all' only -- a narrower scope (one clinic,
    a city, a board) must not pull in every unattributed posting in the whole registry."""
    from app import data as D
    import pflege_jobs.verify as V

    D._snap["jobs"] = [
        {"posting_id": 7, "title": "Pflegefachkraft", "clinic_id": "1", "city": "X",
         "status": "open", "external_url": "https://x.example/job/7"},
        {"posting_id": 8, "title": "Pflegefachkraft", "clinic_id": None, "city": "Y",
         "status": "open", "external_url": "https://x.example/job/8"},
    ]
    monkeypatch.setattr(D, "jobs", lambda: D._snap["jobs"])
    seen = {}
    monkeypatch.setattr(V, "verify_all", lambda rows, **kw: seen.setdefault("ids", {r["posting_id"] for r in rows}) and [])
    monkeypatch.setattr("pflege_jobs.sinks.EdgeSink._post", lambda self, body: {"verify": 0})

    rid = R.create_run("clinic", "1", "verify")
    CR.execute(rid)

    assert seen["ids"] == {7}


def test_post_inbox_stores_every_row_it_is_given(monkeypatch):   # no `fresh`: it stubs the REST calls itself
    """TASK-95: _post_inbox used to run classify_role before the insert and drop everything that was
    not an experienced nursing role. That filter existed only to survive the server-side write cap,
    and it decided at crawl time what is worth keeping. Nothing filters at the queue any more --
    what is kept is decided when the queue is processed."""
    from app import config as A
    posted = []
    monkeypatch.setattr(A, "rest_post", lambda path, body, **kw: posted.extend(body))
    monkeypatch.setattr(A, "rest_get", lambda *a, **kw: [])
    rows = [{"kind": "jobposting", "source_url": f"https://x/{i}", "payload": {"title": t}} for i, t in enumerate([
        "Pflegefachkraft (m/w/d) Intensiv", "Oberarzt (m/w/d) Kardiologie", "Küchenhilfe (m/w/d)",
        "Ausbildung zur Pflegefachfrau (m/w/d)", "Gesundheits- und Krankenpfleger (m/w/d)"])]

    CR._post_inbox(rows, lambda *_: None)

    assert [p["source_url"] for p in posted] == [f"https://x/{i}" for i in range(5)]


def test_post_inbox_never_sends_a_rest_get_dedupe_batch_over_50_urls(monkeypatch):
    """A 50-URL in.() filter is _post_inbox's own chunk size for the already-in-the-inbox dedupe
    lookup (confirmed live 2026-09-16: 200 real URLs at ~22KB got a flat gateway 400 in front of
    PostgREST; the same 150 at ~16KB succeeded). Both existing fixtures that reach the real
    rest_get either discard `params` entirely (test_post_inbox_drops_non_nursing_before_the_insert,
    above) or hand back every row unconditionally (tests/test_agent_api.py's `inbox` fixture) --
    neither would catch a regression that widened or dropped the chunk. This one records the params
    rest_get actually received and inspects the real in.() filter string."""
    import re

    from app import config as A
    calls = []

    def fake_rest_get(path, params=None, **kw):
        calls.append(params)
        return []   # nothing already in the inbox -- every row below is "new"

    posted = []
    monkeypatch.setattr(A, "rest_post", lambda path, body, **kw: posted.extend(body))
    monkeypatch.setattr(A, "rest_get", fake_rest_get)
    rows = [{"kind": "jobposting", "source_url": f"https://x.example/job/{i}",
             "payload": {"title": "Pflegefachkraft (m/w/d)"}} for i in range(120)]

    CR._post_inbox(rows, lambda *_: None)

    assert len(calls) == 3   # 120 urls / 50-per-batch = 50 + 50 + 20
    batch_sizes = [len(re.findall(r'"([^"]*)"', c["source_url"])) for c in calls]
    assert batch_sizes == [50, 50, 20]
    assert all(n <= 50 for n in batch_sizes), batch_sizes
    assert len(posted) == 120   # every row still gets inserted -- only the dedupe lookup is chunked


def test_post_inbox_raises_instead_of_posting_an_unchecked_batch(monkeypatch):
    """TASK-60, the half of the bug the chunk=50 fix did not cover. The dedupe GET can also fail for
    a reason no batch size changes -- the server-side inbox write quota, which 400s every request
    from that client for the rest of the day (run 105, 2026-09-20: 17 lookups failed inside 10s,
    then the insert failed with "inbox: daily limit reached for this client"). Swallowing that made
    every URL look unseen, so the whole run was re-inserted as new: a tripped quota turned straight
    into a duplicate flood, and the run still reported done. It must raise, like its sibling
    _unseen_source_urls already does."""
    from app import config as A
    posted = []

    def quota_tripped(path, params=None, **kw):
        raise RuntimeError('PostgREST 400: {"message":"inbox: daily limit reached for this client"}')

    monkeypatch.setattr(A, "rest_post", lambda path, body, **kw: posted.extend(body))
    monkeypatch.setattr(A, "rest_get", quota_tripped)
    rows = [{"kind": "jobposting", "source_url": f"https://x.example/job/{i}",
             "payload": {"title": "Pflegefachkraft (m/w/d)"}} for i in range(60)]

    with pytest.raises(RuntimeError, match="daily limit reached"):
        CR._post_inbox(rows, lambda *_: None)
    assert posted == []   # nothing written unchecked


def test_a_truncated_seeded_read_is_recorded_as_crawl_issue_kind_truncated(fresh, monkeypatch):
    """TASK-14 AC#2: a walk stopped by its own safety ceiling rather than by the board's end of
    pagination has NOT read the board in full. stats["truncated"] existed but only reached the run
    log, where a board short by an unknown number of postings looked exactly like a complete one on
    every later read."""
    seeded_board = {"kind": "seeded", "vendor": "umantis", "clinics": [CLINIC]}
    obs = [{"source_ref": "https://x.example/1", "title": "Pflegefachkraft (m/w/d)"}]
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": seeded_board})
    monkeypatch.setattr(CR, "_seed_obs", lambda b, c, towns, log: (
        obs, {"truncated": True, "job_links_found": 180, "list_pages": 500, "job_pages": 150}))
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)

    issues = [i for i in R.list_crawl_issues() if i["kind"] == "truncated"]
    assert len(issues) == 1 and issues[0]["board_url"] == "https://x.example/board"
    assert "180" in issues[0]["error"]   # the count reached, not just the fact of the stop


def test_a_complete_seeded_read_records_no_truncated_issue(fresh, monkeypatch):
    """The other side of the same check: a board that stopped at its own end of pagination must not
    be reported as truncated, or the flag means nothing."""
    seeded_board = {"kind": "seeded", "vendor": "umantis", "clinics": [CLINIC]}
    obs = [{"source_ref": "https://x.example/1", "title": "Pflegefachkraft (m/w/d)"}]
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": seeded_board})
    monkeypatch.setattr(CR, "_seed_obs", lambda b, c, towns, log: (
        obs, {"truncated": False, "job_links_found": 12, "list_pages": 2, "job_pages": 12}))
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)

    assert [i for i in R.list_crawl_issues() if i["kind"] == "truncated"] == []


def test_a_degraded_vendor_read_is_recorded_as_crawl_issue_kind_degraded(fresh, monkeypatch):
    """TASK-85 AC#1: crawl_wp_jobs tags its own return value .degraded when its primary discovery
    path (sitemap + wp-json CPT fallback) came up empty and it fell through to a lower-confidence
    path -- orthogonal to whether the fallback then found rows (AMEOS: degraded, 734 real rows via
    the hr4you fallback). Uses the real _BoardTotalRows carrier, same as production code."""
    from crawlers import vendor_adapters as VA

    def degraded_but_not_empty(b, c, session, log, **kw):
        out = VA._BoardTotalRows([{"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])
        out.degraded = "sitemap_and_wp_json_empty"
        return out

    monkeypatch.setattr(CR, "_vendor_rows", degraded_but_not_empty)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = [i for i in R.list_crawl_issues() if i["kind"] == "degraded"]
    assert len(issues) == 1 and issues[0]["board_url"] == "https://x.example/board"
    assert "sitemap_and_wp_json_empty" in issues[0]["error"]


def test_a_normal_vendor_read_records_no_degraded_issue(fresh, monkeypatch):
    from crawlers import vendor_adapters as VA

    def normal(b, c, session, log, **kw):
        return VA._BoardTotalRows([{"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])

    monkeypatch.setattr(CR, "_vendor_rows", normal)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    assert [i for i in R.list_crawl_issues() if i["kind"] == "degraded"] == []


def test_an_under_read_vendor_board_is_recorded_as_crawl_issue_kind_incomplete(fresh, monkeypatch):
    """TASK-88 AC#2: an adapter (crawl_erecruiter, directly or via crawl_wp_jobs' delegate loop)
    read the board's own self-reported total and it exceeds the row count actually returned."""
    from crawlers import vendor_adapters as VA

    def under_read(b, c, session, log, **kw):
        out = VA._BoardTotalRows([{"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])
        out.board_total = 57
        out.board_paginated = True
        return out

    monkeypatch.setattr(CR, "_vendor_rows", under_read)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = [i for i in R.list_crawl_issues() if i["kind"] == "incomplete"]
    assert len(issues) == 1 and issues[0]["board_url"] == "https://x.example/board"
    assert "57" in issues[0]["error"] and "1" in issues[0]["error"]


def test_a_board_matching_its_own_declared_total_records_no_incomplete_issue(fresh, monkeypatch):
    from crawlers import vendor_adapters as VA

    def full_read(b, c, session, log, **kw):
        out = VA._BoardTotalRows([{"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])
        out.board_total = 1
        return out

    monkeypatch.setattr(CR, "_vendor_rows", full_read)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    assert [i for i in R.list_crawl_issues() if i["kind"] == "incomplete"] == []


def test_a_plain_list_return_value_triggers_neither_new_check(fresh, monkeypatch):
    """~18 of ~19 vendor adapters still return a plain list with no .degraded/.board_total attribute
    at all -- getattr's default must keep both new checks a no-op for them, not an AttributeError
    that a bare `except Exception` would then misrecord as a fake board failure."""
    monkeypatch.setattr(CR, "_vendor_rows", lambda b, c, session, log, **kw: [
        {"kind": "jobposting", "payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}])
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    assert R.list_crawl_issues() == []
    assert R.get_run(rid, with_log=False)["status"] == "done"


def test_a_board_already_fetched_reaches_the_local_queue_even_if_the_run_never_finishes(fresh, monkeypatch, tmp_path):
    """Live 2026-09-22, run 118: a systemd restart mid-run killed the process after 188 of 220
    boards had already been fetched and logged. All 188 were lost -- data/inbox.sqlite had not
    changed since the day before -- because execute() held every board's rows in memory and wrote
    the local queue once, after the LAST board. Fixed via _flush_board, called right after each
    board's own fetch instead of at the end of the run.

    This test proves the fix the way the incident actually happened: raise partway through
    execute(), after one board's rows would already have been fetched, and check the queue anyway."""
    board2 = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {
        "https://x.example/board1": {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]},
        "https://x.example/board2": board2,
    })

    def two_boards(c, session=None):
        return [{"kind": "jobposting", "payload": {"title": "Pflegefachkraft", "page": "https://x.example/board1/1"}}]

    # VENDORS is a dict built at import time holding a direct function reference -- patching the
    # module attribute crawl_wp_jobs does not change what VENDORS["wp_jobs"] points at.
    import crawlers.vendor_adapters as VA
    monkeypatch.setitem(VA.VENDORS, "wp_jobs", two_boards)

    # _fetch_board catches its own vendor-call exceptions (that is its retry mechanism, not a bug),
    # so a real process kill -- which does not discriminate -- has to be simulated between boards,
    # at the one point execute() calls out to _flush_board directly. The first board's flush runs
    # for real and must already be durable by the time the second one blows up.
    real_flush = CR._flush_board
    state = {"n": 0}

    def flush_then_die(rows, obs, run_id, log):
        state["n"] += 1
        n = real_flush(rows, obs, run_id, log)
        if state["n"] == 1:
            raise RuntimeError("process killed right after the first board's flush completed")
        return n

    monkeypatch.setattr(CR, "_flush_board", flush_then_die)

    run_id = R.create_run("all", "", "adapter", {"max_credits": 0}, ["1"], trigger="test")
    with pytest.raises(RuntimeError):
        CR.execute(run_id)

    # The board that finished before the crash must already be on disk -- not lost with the
    # in-memory accumulator the old code relied on.
    queued = IB.pending(limit=10, path=str(tmp_path / "inbox.sqlite"))
    assert len(queued) == 1
    assert queued[0]["payload"]["title"] == "Pflegefachkraft"

    archive = tmp_path / "crawl_output" / f"run_{run_id}.jsonl"
    assert archive.exists()
    assert "Pflegefachkraft" in archive.read_text(encoding="utf-8")
