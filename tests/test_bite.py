"""B-ITE adapter: section-first taxonomy detection (find_nursing_taxonomy) and its wiring into
crawl(). Field shapes below mirror real tenants surveyed/verified 2026-09 (augustinum's list-valued
custom_field1 including "pflege"; donauisar's scalar-valued api_bereich including
"pflege_funktionsdienst"; krankenhaus-naturheilweisen's "vorschaubild" thumbnail-image slug that
coincidentally takes the value "pflegekraft_blutdruckmessung" on some unrelated postings -- a false
positive the field-name denylist + min-distinct-buckets heuristic must reject)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources.bite import _has_label, find_nursing_taxonomy, crawl   # noqa: E402


def _jp(title, custom=None, city="München", plz="80331", **extra):
    jp = {"title": title, "url": f"https://jobs.b-ite.com/{title}", "custom": custom or {},
          "address": {"city": city, "postCode": plz, "latitude": None, "longitude": None},
          "employmentType": ["full_time"]}
    jp.update(extra)
    return jp


# --- find_nursing_taxonomy: real-shaped fixtures -----------------------------------------------

def test_finds_list_valued_taxonomy_field():
    # augustinum-shaped: custom_field1 is a list per posting, "pflege" among several department buckets
    jps = ([_jp("Pflegefachkraft", {"custom_field1": ["pflege", "seniorenresidenzen"]}) for _ in range(6)]
           + [_jp("Koch", {"custom_field1": ["gastronomie"]}) for _ in range(6)]
           + [_jp("Gärtner", {"custom_field1": ["dienstleistungen"]}) for _ in range(6)])
    key, label = find_nursing_taxonomy(jps)
    assert (key, label) == ("custom_field1", "pflege")


def test_finds_scalar_valued_taxonomy_field():
    # donauisar-shaped: api_bereich is a single string per posting
    jps = ([_jp("Pflegefachkraft", {"api_bereich": "pflege_funktionsdienst"}) for _ in range(4)]
           + [_jp("Arzt", {"api_bereich": "aerztlicher-dienst"}) for _ in range(4)]
           + [_jp("MTA", {"api_bereich": "medizinisch-technische-berufe"}) for _ in range(4)])
    key, label = find_nursing_taxonomy(jps)
    assert (key, label) == ("api_bereich", "pflege_funktionsdienst")


def test_rejects_image_slug_field_even_if_short_and_repeated():
    # krankenhaus-naturheilweisen-shaped: "vorschaubild" happens to be short/low-cardinality but is a
    # stock-photo filename, not a department -- must not be mistaken for a taxonomy signal.
    jps = [
        _jp("Pflegefachhelfer", {"vorschaubild": "pflegekraft_blutdruckmessung"}),
        _jp("Physiotherapeut", {"vorschaubild": "pflegekraft_blutdruckmessung"}),
        _jp("Mitarbeiter Patientenmanagement", {"vorschaubild": "verwaltung"}),
        _jp("Stationsleitung", {}),   # the genuine nursing posting carries no vorschaubild at all
    ]
    assert find_nursing_taxonomy(jps) == (None, None)


def test_ignores_freeform_description_field_even_if_it_mentions_pflege():
    # a long, near-unique-per-posting field (e.g. "aufgaben") must not be treated as a category enum
    # just because one posting's prose happens to contain the word "Pflege".
    jps = [
        _jp("Pflegefachkraft", {"aufgaben": "Sie übernehmen die Pflege unserer Bewohner in einem warmherzigen Team " + str(i)})
        for i in range(5)
    ] + [_jp("Koch", {"aufgaben": "Sie kochen leckere Gerichte für unsere Gäste " + str(i)}) for i in range(5)]
    assert find_nursing_taxonomy(jps) == (None, None)


def test_no_taxonomy_field_at_all():
    jps = [_jp("Pflegefachkraft", {"umfang": "Vollzeit", "befristung": "unbefristet"})]
    assert find_nursing_taxonomy(jps) == (None, None)


def test_taxonomy_field_present_but_no_nursing_bucket():
    # bergmanclinics-shaped: a real department enum exists, none of the buckets are nursing
    jps = ([_jp("Augenarzt", {"custom_field1": ["augenheilkunde"]}) for _ in range(4)]
           + [_jp("Empfang", {"custom_field1": ["verwaltung"]}) for _ in range(4)]
           + [_jp("OP-Assistenz", {"custom_field1": ["op"]}) for _ in range(4)])
    assert find_nursing_taxonomy(jps) == (None, None)


def test_has_label_handles_list_and_scalar():
    assert _has_label(_jp("x", {"custom_field1": ["pflege", "klinik"]}), "custom_field1", "pflege") is True
    assert _has_label(_jp("x", {"custom_field1": ["klinik"]}), "custom_field1", "pflege") is False
    assert _has_label(_jp("x", {"api_bereich": "pflege_funktionsdienst"}), "api_bereich", "pflege_funktionsdienst") is True
    assert _has_label(_jp("x", {}), "api_bereich", "pflege_funktionsdienst") is False


# --- crawl(): full wiring against a fake session (no network) ----------------------------------

class FakeResp:
    def __init__(self, text="", status_code=200, json_data=None):
        self.text, self.status_code = text, status_code
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class FakeSession:
    """Stubs the two HTTP calls crawl() makes: GET the customer bundle (for the API key), POST the
    search endpoint (for jobPostings)."""
    def __init__(self, key, job_postings):
        self.key, self.job_postings = key, job_postings

    def get(self, url, headers=None, timeout=None):
        if "cs-assets.b-ite.com" in url:
            return FakeResp(text=f'var x = createClient({{key:"{self.key}"}});')
        return FakeResp(status_code=404)

    def post(self, url, headers=None, json=None, timeout=None):
        assert json["key"] == self.key
        return FakeResp(json_data={"jobPostings": self.job_postings})


def test_crawl_uses_section_signal_to_narrow_candidates(monkeypatch):
    key = "a" * 40
    jps = ([_jp("Pflegefachkraft (m/w/d)", {"custom_field1": ["pflege"]}) for _ in range(3)]
           + [_jp("Koch (m/w/d)", {"custom_field1": ["gastronomie"]}) for _ in range(3)]
           + [_jp("Gärtner (m/w/d)", {"custom_field1": ["dienstleistungen"]}) for _ in range(2)])
    fake = FakeSession(key, jps)
    monkeypatch.setattr("pflege_jobs.sources.bite.requests.Session", lambda: fake)
    seed = {"name": "Test Klinik", "kez": "K1", "career": "https://example.de/karriere", "customer": "test", "listing": "main"}
    rows, stats = crawl(seed, {"münchen"}, with_descriptions=False)
    assert stats["section_field"] == "custom_field1"
    assert stats["section_label"] == "pflege"
    assert stats["section_matched"] == 3      # narrowed from 8 total to the 3 "pflege"-tagged postings
    assert stats["total"] == 8
    # every listed posting is kept and labelled; the section signal is data, not a drop
    assert len(rows) == 8
    assert sum(r["role_class"] == "nicht_pflege" for r in rows) == 5


def test_crawl_recovers_a_real_nursing_posting_filed_outside_the_matched_bucket(monkeypatch):
    # 2026-09 coverage-loss fix: `candidates` used to be hard-restricted to only the matched taxonomy
    # bucket -- but on the real Augustinum board that dropped a genuine certified-nursing posting
    # filed under a different custom_field1 bucket ("paedagogische_einrichtungen" containing
    # "Pflegefachfrau (m/w/d)"). classify_role() is a cheap title-only check that already runs before
    # any per-job detail fetch, so there's no cost reason to pre-filter -- every posting is now
    # processed, and "Pflegefachfrau" survives on its own title token regardless of its bucket.
    key = "c" * 40
    jps = ([_jp("Pflegefachkraft (m/w/d)", {"custom_field1": ["pflege"]}) for _ in range(3)]
           + [_jp("Pflegefachfrau (m/w/d)", {"custom_field1": ["paedagogische_einrichtungen"]})]
           + [_jp("Koch (m/w/d)", {"custom_field1": ["gastronomie"]}) for _ in range(3)])
    fake = FakeSession(key, jps)
    monkeypatch.setattr("pflege_jobs.sources.bite.requests.Session", lambda: fake)
    seed = {"name": "Augustinum", "kez": "K3", "career": "https://example.de/karriere", "customer": "test", "listing": "main"}
    rows, stats = crawl(seed, {"münchen"}, with_descriptions=False)
    assert stats["section_field"] == "custom_field1"
    assert stats["section_label"] == "pflege"
    assert stats["section_matched"] == 3          # unchanged: still counts only the matched bucket
    titles = {r["title"] for r in rows}
    assert "Pflegefachfrau (m/w/d)" in titles      # recovered -- would have been dropped before the fix
    assert "Koch (m/w/d)" in titles                # kept too, carrying role_class=nicht_pflege as a label
    assert {r["role_class"] for r in rows if r["title"] == "Koch (m/w/d)"} == {"nicht_pflege"}


def test_crawl_falls_back_to_full_list_when_no_taxonomy_signal(monkeypatch):
    key = "b" * 40
    jps = [_jp("Pflegefachkraft (m/w/d)", {"umfang": "Vollzeit"}), _jp("Koch (m/w/d)", {"umfang": "Teilzeit"})]
    fake = FakeSession(key, jps)
    monkeypatch.setattr("pflege_jobs.sources.bite.requests.Session", lambda: fake)
    seed = {"name": "Test Klinik 2", "kez": "K2", "career": "https://example.de/karriere", "customer": "test", "listing": "main"}
    rows, stats = crawl(seed, {"münchen"}, with_descriptions=False)
    assert stats["section_field"] is None
    assert stats["section_matched"] is None
    assert stats["total"] == 2
    assert len(rows) == 2   # both kept; classify_role only labels
    assert sorted(r["role_class"] for r in rows) == ["nicht_pflege", "pflegefachkraft"]
