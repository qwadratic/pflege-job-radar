# Regression test for the ats-discovery probe branch in pflege_jobs.cli._drain_once: a candidate
# careers_url that names one specific job posting must never overwrite clinics.careers_url with a
# board-shaped URL expected there -- see plan finding on cli.py's probe branch (2026-09-06).
import argparse
from urllib.parse import urlparse

import requests

from pflege_jobs import cli
from pflege_jobs.cli import JOB_DETAIL_RX

# Real payload.careers_url / apply_url values measured live in pflege_jobs.inbox process_note like
# 'ats set:%' on 2026-09-06 -- job-DETAIL pages that must be rejected.
JOB_DETAIL_URLS = [
    "https://mvt-zentrum.de/job/leitung-finanzen-medizincontrolling-m-w-d/",
    "https://www.fachklinik-osterhofen.de/stellenangebot/facharzt-innere-medizin-als-oberarzt-m-w-vollzeit/",
    "https://www.helios-gesundheit.de/karriere/job/3bc89d91-7c4e-485d-ba7f-260fc7a5a378/",
    "https://gkg-bamberg.de/job/stationshilfen-m-w-in-teilzeit-oder-auf-minijob-basis/",
    "https://www.komm-ins-klinikland.de/stelle/famulatur-innere-abteilung-klinik-kitzinger-land/",
    "https://www.deutsches-herzzentrum-muenchen.de/stellenangebot/anlagenmechaniker-m-w-d-sanitaer-heizungs-und-klimatechnik--job-muenchen-107406.html",
    "https://www.josephinum.de/stellenangebot/gesundheits-und-krankenpfleger-w-m-d-fuer-schichtdienst-in-vollzeit-teilzeit/",
    "https://referral-portal-staging.lmu-klinikum.de/stellenanzeigen/personalreferent-arztliche-direktion/fa941ce576c36153",
    "https://dongku.de/stellenangebote/gku-donau-ries-kliniken-und-seniorenheime/gesundheits-und-krankenpfleger-kinderkrankenpfleger-altenpfleger-m-w-d/",
    "https://tagesklinik-westend.de/unsere-klinik/stellenangebote/assistenzarzt/",
    "https://example.de/jobs/1234-j5678.html",
    "https://example.de/Job/98765",
]

# Same live snapshot -- sane listing/board pages that must keep passing through untouched.
LISTING_URLS = [
    "https://kbo-iak.de/kbo-karriere/stellenangebote-pflege",
    "https://www.krankenhaus-st-camillus.de/stellenangebote",
    "https://www.klinik-bad-trissl.de/karriere/",
    "https://www.kreisklinik-woerth.de/stellenangebote/",
    "https://www.klinik-fraenkische-schweiz.de/herz/ueber_uns/stellenangebote",
    "https://www.klinik-angermuehle.de/jobs/",
    "https://www.waldhausklinik.de/stellenangebote-der-klinik",
    "https://www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung",
    "https://www.muenchen-klinik.de/stellenmarkt/aktuelles-stellenangebot/",
]


def test_job_detail_urls_are_flagged():
    for url in JOB_DETAIL_URLS:
        assert JOB_DETAIL_RX.search(urlparse(url).path), f"should flag as single-posting: {url}"


def test_listing_urls_are_not_flagged():
    for url in LISTING_URLS:
        assert not JOB_DETAIL_RX.search(urlparse(url).path), f"should NOT flag as single-posting: {url}"


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


class _FakeSink:
    """Captures every EdgeSink._post body instead of hitting the network -- used to pin what
    pflege_jobs.cli._drain_once posts (TASK-73 AC1/AC2)."""
    posted = []
    written = []

    def __init__(self, *a, batch=200, **kw):
        self.batch = batch

    def write(self, obs, **kw):
        _FakeSink.written.extend(obs)
        return {"observations": len(obs)}

    def write_clinics(self, rows, log=print):
        # Same shape as the real pflege_jobs.sinks.EdgeSink.write_clinics -- exercised here instead
        # of stubbed out, so a test on the caller side still pins real batch-size behaviour.
        n = 0
        for i in range(0, len(rows), self.batch):
            n += self._post({"clinics": rows[i:i + self.batch]}).get("clinics", 0)
        return n

    def _post(self, body):
        _FakeSink.posted.append(body)
        return {k: (len(v) if isinstance(v, list) else 1) for k, v in body.items()}


class _FakeMatcher:
    def match(self, *a, **kw):
        return ("77402", "exact")


def test_drain_once_never_fabricates_a_verify_stamp(monkeypatch):
    """TASK-73 AC1: no collector writing into pflege_jobs.inbox is an actual browser that fetched the
    stored URL -- _drain_once must not stamp verify_status=live/200 itself; that is left to
    app/crawl.py:_verify_ids or the next `cli verify` sweep."""
    def fake_jobposting_to_obs(row, towns):
        return {"source_id": 20, "source_ref": row["source_url"], "role_class": "pflegefachkraft",
                "in_bavaria": True, "employer_name": "Klinikum X", "city": "München",
                "employer_class_rule": "keyword_rule", "observed_at": "2026-09-17T03:00:00Z"}
    monkeypatch.setattr("pflege_jobs.sources.inbox.jobposting_to_obs", fake_jobposting_to_obs)

    def fake_get(u, params=None, headers=None, timeout=None):
        if "/rest/v1/inbox?" in u:
            return _Resp([{"inbox_id": 1, "kind": "jobposting", "source_url": "https://x.de/job/1", "payload": {}}])
        if u.endswith("/rest/v1/posting_observations"):
            return _Resp([{"posting_id": 999, "source_id": 20, "source_ref": "https://x.de/job/1"}])
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)
    _FakeSink.posted = []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)

    n = cli._drain_once(argparse.Namespace(no_ack=False), "https://db", {}, _FakeMatcher(), set())
    assert n == 1
    assert not any("verify" in body for body in _FakeSink.posted), _FakeSink.posted
    assert any("clinic_links" in body for body in _FakeSink.posted)          # the real link still happens
    assert any("inbox_ack" in body for body in _FakeSink.posted)


def test_probe_branch_uses_params_and_acks_a_bad_clinic_id_instead_of_raising(monkeypatch):
    """TASK-73 AC2: clinic_id is payload data, not a literal -- a value containing '&' must reach
    PostgREST via params= (URL-encoded) rather than split the filter string, and a lookup failure
    must ack the row with an error note instead of raising SystemExit and wedging the whole drain."""
    poison_cid = "77402&x=1"
    get_calls = []

    def fake_get(u, params=None, headers=None, timeout=None):
        get_calls.append((u, params))
        if "/rest/v1/inbox?" in u:
            return _Resp([{"inbox_id": 7, "kind": "probe",
                           "payload": {"probe": "ats_discovery", "clinic_id": poison_cid, "ats": "softgarden",
                                       "careers_url": "https://x.de/de/vacancies"}}])
        if u.endswith("/rest/v1/clinics"):
            assert params == {"select": "*", "clinic_id": f"eq.{poison_cid}"}   # via params=, never interpolated
            return _Resp({"code": "PGRST100", "message": "failed to parse filter"})
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)
    _FakeSink.posted = []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)

    n = cli._drain_once(argparse.Namespace(no_ack=False), "https://db", {}, _FakeMatcher(), set())
    assert n == 1
    acks = [row for body in _FakeSink.posted for row in body.get("inbox_ack", [])]
    assert acks and acks[0]["inbox_id"] == 7 and "failed" in acks[0]["note"]
    assert any(p == {"select": "*", "clinic_id": f"eq.{poison_cid}"} for _, p in get_calls)


def test_probes_post_is_batched_via_write_clinics_not_one_unbounded_call(monkeypatch):
    """TASK-73 AC2: the probes POST at the end of _drain_once batches like every other EdgeSink
    caller -- via sinks.EdgeSink.write_clinics, the same helper crawl.py's clinic updates use --
    instead of one raw {"clinics": [...]} call with every probe row."""
    rows = [{"inbox_id": i, "kind": "probe",
             "payload": {"probe": "ats_discovery", "clinic_id": str(i), "ats": "softgarden",
                         "careers_url": f"https://x{i}.de/de/vacancies"}} for i in range(1, 851)]   # > 2*sink.batch

    def fake_get(u, params=None, headers=None, timeout=None):
        if "/rest/v1/inbox?" in u:
            return _Resp(rows)
        if u.endswith("/rest/v1/clinics"):
            cid = params["clinic_id"].split(".", 1)[1]
            return _Resp([{"clinic_id": cid, "ats_type": None, "careers_url": None}])
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)
    _FakeSink.posted = []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)

    n = cli._drain_once(argparse.Namespace(no_ack=False), "https://db", {}, _FakeMatcher(), set())
    assert n == 850
    clinics_batches = [len(body["clinics"]) for body in _FakeSink.posted if "clinics" in body]
    assert clinics_batches == [200, 200, 200, 200, 50] and sum(clinics_batches) == 850


def _jobposting_row(inbox_id, source_url, city, region):
    return {"inbox_id": inbox_id, "kind": "jobposting", "source_url": source_url,
            "source_host": urlparse(source_url).netloc, "collector": "vendor-wp_jobs-v1",
            "payload": {"title": "Pflegefachkraft (m/w/d)", "org": "Klinikum X", "url": source_url,
                        "description": "", "loc": [{"city": city, "plz": None, "region": region}]}}


def test_drain_once_pins_the_non_prod_host_and_in_bavaria_false_drop_gates(monkeypatch):
    """2026-09-18 crawler review: deleting either gate at cli.py:311 (NON_PROD_HOST) or cli.py:316
    (in_bavaria is False) leaves the suite green and re-admits the two incidents they were added
    for (77 staging LMU rows, the inherited-city family). Real pflege_jobs.sources.inbox.
    jobposting_to_obs runs here (not stubbed) so the in_bavaria gate is the real one, on an
    in-memory 3-row inbox: one Bavarian row, one non-Bavarian row, one staging-host row."""
    bavarian = _jobposting_row(1, "https://www.klinikum-x.de/stelle/1-pflegefachkraft", "München", "Bayern")
    non_bavarian = _jobposting_row(2, "https://www.klinikum-y.de/stelle/2-pflegefachkraft", "Frankfurt", "Hessen")
    staging = _jobposting_row(3, "https://referral-portal-staging.lmu-klinikum.de/stellenanzeigen/3", None, None)
    rows = [bavarian, non_bavarian, staging]

    def fake_get(u, params=None, headers=None, timeout=None):
        if "/rest/v1/inbox?" in u:
            return _Resp(rows)
        if u.endswith("/rest/v1/posting_observations"):
            return _Resp([])
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)
    _FakeSink.posted, _FakeSink.written = [], []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)

    n = cli._drain_once(argparse.Namespace(no_ack=False), "https://db", {}, _FakeMatcher(), set())
    assert n == 3
    written_urls = {o["source_url"] for o in _FakeSink.written}
    assert written_urls == {bavarian["source_url"]}          # only the Bavarian row reached the sink

    acks = [row for body in _FakeSink.posted for row in body.get("inbox_ack", [])]
    notes = {a["inbox_id"]: a["note"] for a in acks}
    assert notes[2] == "skipped: outside Bavaria"
    assert notes[3] == "skipped: non-production host (staging/preview)"


def test_load_observations_pins_the_non_prod_host_and_in_bavaria_false_drop_gates(monkeypatch):
    """Same in_bavaria-is-False drop gate, the app/crawl.py:_load_observations side (seeded adapters
    -- career_crawl, bite, pi_asp, ... -- never pass through cli.py's inbox at all, so this is a
    second, independent place the same 2026-09-18 review found the gate needed pinning). An
    in-memory 3-row batch: Bavarian, non-Bavarian (in_bavaria False), and unknown (in_bavaria None,
    e.g. a page with no placeable city at all) -- only the Bavarian row must reach the sink."""
    import app.crawl as CR
    from app import config as A
    from app import data as D
    import pflege_jobs.registry as registry_mod
    import pflege_jobs.sinks as sinks_mod

    written = []

    class _FakeSink2:
        def __init__(self, *a, **kw):
            pass

        def write(self, obs, **kw):
            written.extend(obs)
            return {"observations": len(obs)}

        def _post(self, body):
            return {}

    class _NoopMatcher:
        def match(self, *a, **kw):
            return None

    monkeypatch.setattr(D, "clinics", lambda: [{"clinic_id": "0", "name": "x", "town": "x"}])
    monkeypatch.setattr(registry_mod, "Matcher", lambda clinics: _NoopMatcher())
    monkeypatch.setattr(sinks_mod, "EdgeSink", _FakeSink2)
    monkeypatch.setattr(A, "rest_get", lambda *a, **k: [])   # _posting_ids_for_refs: no network

    def make(source_ref, in_bavaria, role_class="pflegefachkraft"):
        return {"source_ref": source_ref, "employer_name": "Klinikum X", "city": "X",
                "role_class": role_class, "in_bavaria": in_bavaria, "employer_class_rule": "r"}

    obs = [make("bav-1", True), make("non-bav-2", False), make("unknown-3", None)]
    ids, kept = CR._load_observations(obs, {}, log=lambda *_: None)

    # the gate is specifically "in_bavaria is False", not "is not True" -- an unplaceable/unknown
    # location (None) is not evidence of being outside Bavaria and must not be dropped either.
    assert {o["source_ref"] for o in kept} == {"bav-1", "unknown-3"}
    assert {o["source_ref"] for o in written} == {"bav-1", "unknown-3"}
