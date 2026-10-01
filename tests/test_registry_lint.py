"""Registry lint: careers_url must not be a single job-detail page (TASK-86), an aggregator page or a
posting's own page (TASK-185)."""
import re
from pathlib import Path

from pflege_jobs.registry_lint import check_careers_url, lint_rows


# --- the real junk rows the lint exists to have caught -------------------------------------------
def test_flags_job_uuid_shape_that_produced_47601_67201_67601():
    # Same dead Helios URL on all three clinics live (TASK-86 review, 2026-09-22 sweep).
    url = "https://www.helios-gesundheit.de/karriere/job/3bc89d91-7c4e-485d-ba7f-260fc7a5a378/"
    assert check_careers_url(url) == "job-slug"


def test_flags_stellenangebote_slug_slug_shape_that_produced_77901():
    url = ("https://dongku.de/stellenangebote/gku-donau-ries-kliniken-und-seniorenheime/"
           "gesundheits-und-krankenpfleger-kinderkrankenpfleger-altenpfleger-m-w-d/")
    assert check_careers_url(url) == "stellenangebote-slug-slug"


def test_flags_job_human_slug_shape_missed_by_a_uuid_only_pattern():
    # 16268 and 47102, live on the pflege_jobs.clinics table -- the original job-uuid-only
    # pattern missed both because neither slug is a UUID (TASK-86 review finding #7).
    assert check_careers_url("https://mvt-zentrum.de/job/leitung-finanzen-medizincontrolling-m-w-d/") == "job-slug"
    assert check_careers_url("https://gkg-bamberg.de/job/stationshilfen-m-w-in-teilzeit-oder-auf-minijob-basis/") == "job-slug"


# --- the corrected replacement for 77901 must NOT re-trip the same lint -----------------------
def test_corrected_77901_board_url_is_clean():
    assert check_careers_url("https://dongku.de/stellenangebote/") is None


# --- real board URLs already live in the registry must not false-positive ----------------------
def test_real_board_urls_are_not_flagged():
    boards = [
        "https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote",   # one slug only
        "https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote?selection3=3",
        "https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15",
        "https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobSite%3Afoo",
        "https://recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=22&Reset=G",
        "https://jobs.klinikum-ab-alz.de/Jobs",
        "https://jobs.bezirkskliniken-schwaben.de/Jobs",  # plural "/Jobs" board, not "/job/<x>/"
    ]
    for url in boards:
        assert check_careers_url(url) is None, url


def test_empty_or_missing_careers_url_is_not_flagged():
    assert check_careers_url("") is None
    assert check_careers_url(None) is None


# --- row-level API -------------------------------------------------------------------------------
def test_lint_rows_reports_clinic_id_and_shape():
    rows = [
        {"clinic_id": "16201", "careers_url": "https://www.muenchen-klinik.de/stellenmarkt/"},
        {"clinic_id": "47601", "careers_url": "https://www.helios-gesundheit.de/karriere/job/"
                                               "3bc89d91-7c4e-485d-ba7f-260fc7a5a378/"},
    ]
    findings = lint_rows(rows)
    assert [f.clinic_id for f in findings] == ["47601"]
    assert findings[0].shape == "job-slug"


# --- TASK-185: a careers_url on an aggregator host, or equal to a posting's own url, is not a board. Synthetic
# rows only; the real offenders are 66103 (krankenpflegejobs24.de) and 77902/77903 (one dongku job page). -----
def test_lint_flags_an_aggregator_careers_url_and_names_the_clinic():
    rows = [{"clinic_id": "66103", "name": "Synthetic Frauenklinik", "careers_url": "https://www.krankenpflegejobs24.de/synthetic-frauenklinik"},
            {"clinic_id": "1", "name": "A", "careers_url": "https://de.indeed.com/cmp/synthetic"},
            {"clinic_id": "2", "name": "B", "careers_url": "https://www.stepstone.de/cmp/synthetic"},
            {"clinic_id": "3", "name": "C", "careers_url": "https://www.meinestadt.de/synthetic/stellenangebote"},
            {"clinic_id": "4", "name": "D", "careers_url": "https://klinik-d.example/karriere/"}]
    findings = lint_rows(rows)
    assert [(f.clinic_id, f.shape) for f in findings] == [(c, "aggregator-host") for c in ("66103", "1", "2", "3")]
    assert "66103" in str(findings[0]) and "Synthetic Frauenklinik" in str(findings[0]) and "krankenpflegejobs24.de" in str(findings[0])


def test_a_host_that_merely_contains_an_aggregator_name_is_not_one():
    rows = [{"clinic_id": "5", "careers_url": "https://www.notindeed.com/karriere"},
            {"clinic_id": "6", "careers_url": "https://karriere.meinestadt-klinik.example/"},
            {"clinic_id": "7", "careers_url": "https://klinik-d.example/stepstone.de/jobs"}]
    assert lint_rows(rows) == []


def test_every_host_the_purge_script_retires_is_linted():
    # AGGREGATOR_HOSTS is not a second list: the hosts data/purge_retired_sources.py purges must all be in it.
    text = (Path(__file__).resolve().parents[1] / "data" / "purge_retired_sources.py").read_text(encoding="utf-8")
    hosts = re.findall(r"source_host\.ilike\.\*([^*]+)\*", text)
    assert hosts
    for h in hosts:
        assert [f.shape for f in lint_rows([{"clinic_id": "x", "careers_url": f"https://www.{h}/jobs"}])] == ["aggregator-host"], h


def test_a_careers_url_equal_to_a_posting_url_is_that_postings_page():
    url = "https://klinik-x.example/stellenangebot/pflegefachkraft-m-w-d"
    rows = [{"clinic_id": "8", "name": "X", "careers_url": url},
            {"clinic_id": "9", "name": "Y", "careers_url": "https://klinik-y.example/karriere/"}]
    assert [(f.clinic_id, f.shape) for f in lint_rows(rows, {url})] == [("8", "posting-url")]
    assert lint_rows(rows) == []                      # the posting urls are what it compares with


# --- wiring: the lint must run inside every real funnel a discovered/probed careers_url passes
# through: EdgeSink.write_clinics (pflege_jobs/sinks.py) -- career_discover_exa.py's Exa write-back and
# cli.py's ats-probe path post through it directly (cmd_link_clinics's CSV push, the third funnel, is
# gone since TASK-175). A lint module nothing imports cannot reject anything. --------------------------
def test_write_clinics_rejects_a_job_detail_careers_url_before_posting():
    from pflege_jobs.sinks import EdgeSink

    sink = EdgeSink(url="http://x", anon_key="k", secret="s")
    posted = []
    sink._post = lambda body: (posted.append(body), {"clinics": len(body.get("clinics", []))})[1]
    n = sink.write_clinics([{"clinic_id": "67201",
                              "careers_url": "https://www.helios-gesundheit.de/karriere/job/"
                                             "3bc89d91-7c4e-485d-ba7f-260fc7a5a378/"}])
    # Refused and sent "" (coalesces server-side to whatever is already stored), not written as-is --
    # and not aborted either: see finding #1 below for why an all-or-nothing raise is wrong here.
    assert n == 1
    assert posted[0]["clinics"][0] == {"clinic_id": "67201", "careers_url": ""}


def test_write_clinics_still_posts_clean_rows():
    from pflege_jobs.sinks import EdgeSink

    sink = EdgeSink(url="http://x", anon_key="k", secret="s")
    posted = []
    sink._post = lambda body: (posted.append(body), {"clinics": len(body.get("clinics", []))})[1]
    n = sink.write_clinics([{"clinic_id": "16215", "careers_url": "https://example.de/stellenangebote/"}])
    assert n == 1 and posted[0]["clinics"][0]["clinic_id"] == "16215"


# --- TASK-86 review round 2, finding #1: both real callers (career_discover_exa.py's Exa write-back,
# cli.py's ats-probe drain) build the outgoing row from a full live-clinic snapshot and only
# conditionally overwrite careers_url -- a row proposing an unrelated correction (e.g. ats_type only)
# routinely CARRIES THROUGH an already-bad, unchanged careers_url. The old all-or-nothing raise
# refused the whole batch for that (reproduced: 200 clean rows + 1 carried-over row -> ValueError, 0
# written), which also re-opened the TASK-73 AC2 "one bad batch wedges the whole call" wedge in
# cli.py's inbox drain (the raise lands before ack_fn). Fix: scrub only the offending careers_url
# value (safe -- the edge upsert's coalesce leaves the already-stored value untouched when sent ""),
# and still write the rest of that row plus every other row in the batch. --------------------------
def test_write_clinics_scrubs_only_the_bad_careers_url_not_the_whole_batch():
    from pflege_jobs.sinks import EdgeSink

    sink = EdgeSink(url="http://x", anon_key="k", secret="s")
    posted = []
    sink._post = lambda body: (posted.append(body), {"clinics": len(body.get("clinics", []))})[1]
    clean = [{"clinic_id": str(i), "careers_url": "https://example.de/stellenangebote/"} for i in range(200)]
    # 16268: real live clinic whose careers_url is already the job-slug shape (TASK-86 review); this
    # row is only here to carry an unrelated ats_type correction, exactly like career_discover_exa.py's
    # build_write_back and cli.py's ats-probe branch construct it.
    carried_over = {"clinic_id": "16268", "ats_type": "wp_jobs",
                     "careers_url": "https://mvt-zentrum.de/job/leitung-finanzen-medizincontrolling-m-w-d/"}
    n = sink.write_clinics(clean + [carried_over])
    sent = [r for b in posted for r in b.get("clinics", [])]
    assert n == 201 and len(sent) == 201                      # nothing dropped from the batch
    by_id = {r["clinic_id"]: r for r in sent}
    assert by_id["16268"]["careers_url"] == ""                # bad value refused, sent blank (coalesces to stored)
    assert by_id["16268"]["ats_type"] == "wp_jobs"             # the intended, unrelated correction still went through
    assert by_id["0"]["careers_url"] == "https://example.de/stellenangebote/"   # clean rows untouched


# --- TASK-175: cmd_link_clinics (`link-clinics`, orchestrate.py's nightly 'link' stage) used to load
# data/registry/clinics.csv and push every row of it back into the clinics table, reverting any fix made in
# the DB since the CSV was last written. It now matches against the live table and writes clinic_links only.
def test_cmd_link_clinics_matches_against_the_live_table_and_writes_no_clinics(monkeypatch, tmp_path):
    import argparse

    import requests

    from pflege_jobs import cli
    from pflege_jobs.sinks import EdgeSink

    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    monkeypatch.setenv("PFLEGE_INGEST_URL", "http://ingest.example")
    monkeypatch.setenv("PFLEGE_INGEST_SECRET", "s")
    live = [{"clinic_id": "16201", "name": "München Klinik Schwabing", "town": "München",
             "operator": "München Klinik gGmbH", "beds": 700}]
    posting = {"posting_id": 1, "title": "Pflegefachkraft (m/w/d)", "city": "München", "clinic_match_rule": None,
               "employers": {"name_display": "München Klinik Schwabing", "employer_class": "clinic"},
               "posting_observations": []}

    class _FakeResp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    def fake_get(u, params=None, headers=None, timeout=None):
        if "/rest/v1/clinics?" in u:
            return _FakeResp(live)
        if "/rest/v1/postings?" in u:
            return _FakeResp([posting])
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)

    posted = []
    monkeypatch.setattr(EdgeSink, "_post",
                         lambda self, body: (posted.append(body), {k: len(v) for k, v in body.items()})[1])

    cli.cmd_link_clinics(argparse.Namespace(dry_run=False, out=str(tmp_path / "links.json")))

    assert [list(b) for b in posted] == [["clinic_links"]]           # links only: no clinics push at all
    assert posted[0]["clinic_links"][0]["clinic_id"] == "16201"      # matched against the live row
