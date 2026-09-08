# Regressions for the two 2026-09-08 findings in pflege_jobs/cli.py:
#  1. link-cross pass 1 folded every job of a board whose ATS keeps the job id in the query string or
#     fragment into one posting (canonical_ref dropped `?...`/`#...`): postings 7416 (27 Helios jobs),
#     5625 (10 Bezirkskliniken jobs), 7543 (9 helixjobs jobs).
#  2. cmd_inbox's re-read of the just-written observations interpolated refs into the URL; a ref with
#     `&` split the PostgREST filter (400 PGRST100) and the whole batch lost its clinic links / verify
#     marks (Firecrawl postings 10201-10204 left clinic_id NULL despite 'loaded -> 56202').
from pflege_jobs.cli import canonical_ref, same_source_variant_pairs, lookup_posting_ids

# Real source_refs from the collapsed postings -- each line is a DISTINCT job on its board.
DISTINCT_JOBS = [
    ("https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=613", "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=935"),
    ("https://bezirk-unterfranken.helixjobs.com/bkhwerneck/jobad?prj=2618P932", "https://bezirk-unterfranken.helixjobs.com/bkhwerneck/jobad?prj=2618P900"),
    ("https://www.koenig-ludwig-haus.de/karriere/jobs-im-klh/index.html?detID=185", "https://www.koenig-ludwig-haus.de/karriere/jobs-im-klh/index.html?detID=149"),
    ("https://www.reisach-kliniken.de/position?id=e9ab29ae-4a90-40ca-8828-1a96e8c3a189", "https://www.reisach-kliniken.de/position?id=7aee700e-a911-4e73-9079-0b09fa372c20"),
    ("https://gebo-med.softgarden.io/job/66872540/x?l=de", "https://gebo-med.softgarden.io/job/66872541/x?l=de"),
    ("https://x.de/jobs?ref=12345", "https://x.de/jobs?ref=12346"),                                   # numeric ref = job reference, not a referrer
    ("https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/pflege-und-funktionsdienst/details/?job=ebdc3501-e772", "https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/pflege-und-funktionsdienst/details/?job=a0c87a45-d5b8"),
    ("https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1135#position,id=73", "https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1135#position,id=ac"),
    ("https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1130#position,id=1f", "https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1135#position,id=1f"),
    ("https://www.rheuma-kinderklinik.de/karriere-ausbildung/stellenangebote/detail-stellen?tx_news_pi1%5Bnews%5D=437&cHash=c57222",
     "https://www.rheuma-kinderklinik.de/karriere-ausbildung/stellenangebote/detail-stellen?tx_news_pi1%5Bnews%5D=497&cHash=876a63"),
]

# Genuine variants of ONE page that must still fold.
SAME_JOB = [
    ("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579-needle-nurse-m-w-d-in-teilzeit", "https://jobs.smartrecruiters.com/ArtemedSE/744000143844579"),
    ("https://karriere-im.klinikverbund-allgaeu.de/karriere-detail/Ottobeuren/Pflegefachkraft-mwd/2612?cHash=37815cb736b43797f2cf05262e36feab",
     "https://karriere-im.klinikverbund-allgaeu.de/karriere-detail/Ottobeuren/Pflegefachkraft-mwd/2612"),
    ("https://x.de/job/1/?utm_source=indeed&utm_medium=feed", "https://x.de/job/1"),
    ("https://gebo-med.softgarden.io/job/66872540/Mitarbeiter-m-w-d-f%C3%BCr-die-Klinik?jobDbPVId=282405335&amp%3Bl=de&l=de", "https://gebo-med.softgarden.io/job/66872540/Mitarbeiter-m-w-d-f%C3%BCr-die-Klinik?l=de"),
    ("https://x.career.softgarden.de/jobs/54545203/Pflegefachkraft-m-w-d-/", "https://x.career.softgarden.de/jobs/54545203/"),
    ("https://x.de/job/1#top", "https://X.de/job/1/"),
    ("https://karriere.asklepios.com/Pflegefachkraft-wmd-fuer-Anaesthesie-de-j25622.html", "https://karriere.asklepios.com/pflegefachkraft-wmd-fuer-anaesthesie-de-j25622.html"),
    ("https://www.klinikum-ffb.de/stellenangebote/examinierte-pflegefachkraft-m-w-d/?portfolioCats=20", "https://www.klinikum-ffb.de/stellenangebote/examinierte-pflegefachkraft-m-w-d/"),
    ("https://karriere.klinikum-landsberg.de/jobposting/41878a755e99?ref=homepage", "https://karriere.klinikum-landsberg.de/jobposting/41878a755e99"),
    ("https://jobs.bezirkskliniken-mfr.de/index.php?id=613&ac=jobad&fbclid=abc", "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=613"),
]


def test_canonical_ref_keeps_query_and_fragment_job_ids():
    for a, b in DISTINCT_JOBS:
        assert canonical_ref(a) != canonical_ref(b), f"folded two different jobs: {a} | {b}"


def test_canonical_ref_still_folds_true_variants():
    for a, b in SAME_JOB:
        assert canonical_ref(a) == canonical_ref(b), f"did not fold: {a} | {b}"


def test_same_source_pairs_never_fold_query_ids_and_merge_all_into_min():
    obs = [{"posting_id": 5625, "source_id": 20, "source_ref": "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=613"},
           {"posting_id": 5626, "source_id": 20, "source_ref": "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=935"},
           {"posting_id": 5627, "source_id": 20, "source_ref": "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=932"},
           # a real variant group of three postings folds in ONE run (previously only max->min per run)
           {"posting_id": 9, "source_id": 20, "source_ref": "https://x.de/job/1/"},
           {"posting_id": 7, "source_id": 20, "source_ref": "https://x.de/job/1?utm_source=a"},
           {"posting_id": 8, "source_id": 20, "source_ref": "https://x.de/job/1"},
           # same canonical url from ANOTHER source is not a same-source variant
           {"posting_id": 3, "source_id": 25, "source_ref": "https://x.de/job/1"},
           {"posting_id": None, "source_id": 20, "source_ref": "https://x.de/job/1"}]
    assert same_source_variant_pairs(obs) == [{"src": 8, "dst": 7}, {"src": 9, "dst": 7}]


class _Resp:
    def __init__(self, data): self._d = data
    def json(self): return self._d


def test_lookup_posting_ids_encodes_refs_and_keys_by_source():
    ref745 = "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=745"
    ref968 = "https://jobs.bezirkskliniken-mfr.de/index.php?ac=jobad&id=968"
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        assert url.endswith("/rest/v1/posting_observations")
        assert params["source_id"] == "eq.25"                       # filtered per source
        assert f'"{ref745}"' in params["source_ref"]                # ref passed whole, via params (encoded by requests)
        # the DB holds the same ref from employer_ats (20) on another posting; the per-source filter hides it
        return _Resp([{"posting_id": 10201, "source_id": 25, "source_ref": ref745},
                      {"posting_id": 10205, "source_id": 25, "source_ref": ref968}])

    obs = [{"source_id": 25, "source_ref": ref745}, {"source_id": 25, "source_ref": ref968}]
    ids = lookup_posting_ids(fake_get, "https://db", {"apikey": "x"}, obs, log=lambda *_: None)
    assert ids == {(25, ref745): 10201, (25, ref968): 10205}
    assert len(calls) == 1
    assert "source_ref=in.(" not in calls[0][0]                      # nothing interpolated into the URL string


def test_lookup_posting_ids_skips_error_objects():
    def bad_get(url, params=None, headers=None, timeout=None):
        return _Resp({"code": "PGRST100", "message": "failed to parse filter"})
    msgs = []
    ids = lookup_posting_ids(bad_get, "https://db", {}, [{"source_id": 20, "source_ref": "https://a.de/1"}], log=msgs.append)
    assert ids == {} and msgs and "ref lookup error" in msgs[0]
