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

from pflege_jobs.classify import fuzzy_key   # noqa: E402
from pflege_jobs.sources.bite import _has_label, find_nursing_taxonomy, crawl, to_observation   # noqa: E402


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


# --- to_observation: external_url must prefer the real ad page over a broken apply-form host -----
# 2026-09-28, clinic 56404/56406 (Diakoneo): applyUrl pointed at a JS-only application form with no
# server-rendered content, while url (the same field source_url/source_ref already trust) was the
# real, content-bearing ad -- every posting on this tenant got a dead link as its external_url.

def test_external_url_prefers_url_over_a_diverging_apply_url():
    jp = _jp("Pflegefachkraft (m/w/d)", url="https://jobs.diakoneo.de/jobposting/abc123",
             applyUrl="https://jobs.diakoneo.de/de/jobposting/abc1230/apply")
    seed = {"name": "Klinik Hallerwiese Nürnberg", "kez": "K1", "career": "https://example.de/karriere"}
    obs = to_observation(jp, seed, {"nürnberg"})
    assert obs["external_url"] == "https://jobs.diakoneo.de/jobposting/abc123"


def test_external_url_falls_back_to_apply_url_when_url_is_empty():
    jp = _jp("Pflegefachkraft (m/w/d)", url="", applyUrl="https://jobs.example.de/apply/1")
    seed = {"name": "Klinik Hallerwiese Nürnberg", "kez": "K1", "career": "https://example.de/karriere"}
    obs = to_observation(jp, seed, {"nürnberg"})
    assert obs["external_url"] == "https://jobs.example.de/apply/1"


# --- TASK-186: the ad text reaches classify_role ------------------------------------------------------------
# An Ausbildung posted as "Operationstechnische Assistenten (m/w/d)" says so only in its body (real excerpt of a
# live posting). to_observation runs a second time with the fetched detail page, and that pass must read it.

def test_the_ad_text_reaches_the_role_classifier():
    jp = _jp("Operationstechnische Assistenten (m/w/d)")
    seed = {"name": "Klinik Hallerwiese Nürnberg", "kez": "K1", "career": "https://example.de/karriere"}
    assert to_observation(jp, seed, {"nürnberg"})["role_class"] == "ota_ata"           # list pass: title only
    html = "<p>Ihre Voraussetzungen für die Ausbildung Sie haben einen Hauptschulabschluss (oder gleichwertig)</p>"
    obs = to_observation(jp, seed, {"nürnberg"}, html)
    assert obs["role_class"] == "ausbildung" and obs["role_rule"].startswith("ausbildung_body:")


# --- TASK-184: a tenant whose address is the employer's seat, not the posting's place -----------------
# Arberland Kliniken (bewerbung.arberlandkliniken.de, live 2026-10-01, 41 postings): address.city is the seat Viechtach on
# 35 of them (5 more carry the MVZ's own address in Regen, 1 none), the site the posting is for is custom.ort[] (a list of
# lower-case place slugs), custom.einrichtung[] names the facility. Diakoneo's tenant names its place `custom.standort`
# and its address IS the posting's.

ARBERLAND_SEAT = {"street": "Karl-Gareis-Straße", "houseNumber": "31", "postCode": "94234", "city": "Viechtach",
                  "country": "de", "latitude": 49.0852209, "longitude": 12.875395}
ARBERLAND_MVZ = {"street": "Zwieseler Straße", "postCode": "94209", "city": "Regen", "country": "de"}


def _arberland_jp(title, ort, einrichtung, address=ARBERLAND_SEAT):
    jp = {"title": title, "url": f"https://bewerbung.arberlandkliniken.de/jobposting/{title}",
          "employer": {"name": "Arberland Kliniken"}, "address": dict(address), "employmentType": ["full_time"],
          "custom": {"kontakt_text": "sadipscing elitr", "einrichtung": einrichtung, "art": ["vollzeit"], "abteilung": ["pflege"]}}
    if ort is not None:
        jp["custom"]["ort"] = ort
    return jp


ARBERLAND_OTA = _arberland_jp("Operationstechnischer Assistent (m/w/d)", ["zwiesel"], ["arberlandklinik_zwiesel"])
ARBERLAND_VIECHTACH = _arberland_jp("Ausbildung zum Koch (m/w/d)", ["viechtach"], ["arberlandklinik_viechtach"])
ARBERLAND_SPRINGER = _arberland_jp("Pflegefachmann/-frau (m/w/d) für unseren Springerpool", ["viechtach", "zwiesel"],
                                   ["arberlandklinik_zwiesel", "arberlandklinik_viechtach"])
ARBERLAND_MVZ_ORT = _arberland_jp("Facharzt (m/w/d) Neurologie", ["viechtach", "zwiesel"], ["mvz_arberland"], ARBERLAND_MVZ)
ARBERLAND_MVZ_NO_ORT = _arberland_jp("Facharzt (m/w/d) Kinder- und Jugendmedizin", None, ["mvz_arberland"], ARBERLAND_MVZ)
ARBERLAND_NO_ADDRESS = _arberland_jp("Hausarzt (m/w/d) für die Marktgemeinde Bodenmais", None, None, {})
ARBERLAND_SEED = {"name": "Arberlandklinik Zwiesel", "kez": "27601", "career": "https://jobs.arberlandkliniken.de/",
                  "customer": "arberland-kliniken", "listing": "main-listing", "place_field": "ort"}
TOWNS = {"zwiesel", "viechtach"}


def test_a_place_field_names_the_posting_own_site_not_the_tenant_seat():
    obs = to_observation(ARBERLAND_OTA, ARBERLAND_SEED, TOWNS)
    assert (obs["city"], obs["plz"], obs["lat"], obs["lon"]) == ("Zwiesel", None, None, None)   # the seat's PLZ/coordinates are not Zwiesel's
    assert obs["in_bavaria"] is True
    assert (obs["n_locations"], json.loads(obs["locations"])) == (1, [{"adresse": {"ort": "Zwiesel", "plz": None}}])
    assert obs["fuzzy_key"] == fuzzy_key("Operationstechnischer Assistent (m/w/d)", "Arberland Kliniken", "Zwiesel")
    assert json.loads(obs["payload"])["bite_custom"]["ort"] == ["zwiesel"]                      # the raw field stays in the payload


def test_a_posting_naming_the_address_city_reads_exactly_as_it_did_before():
    # the seat IS Viechtach's address: PLZ, coordinates and everything derived stay (13 of the 41 live postings)
    with_field = to_observation(ARBERLAND_VIECHTACH, ARBERLAND_SEED, TOWNS)
    without = to_observation(ARBERLAND_VIECHTACH, {k: v for k, v in ARBERLAND_SEED.items() if k != "place_field"}, TOWNS)
    assert (with_field["city"], with_field["plz"], with_field["lat"], with_field["lon"], with_field["in_bavaria"]) == ("Viechtach", "94234", 49.0852209, 12.875395, True)
    assert {k: v for k, v in with_field.items() if k not in ("observed_at", "details_fetched_at")} == {k: v for k, v in without.items() if k not in ("observed_at", "details_fetched_at")}


def test_without_a_place_field_the_address_is_read_as_before():
    # the address is the posting's own field on a tenant that does not say otherwise (Diakoneo)
    obs = to_observation(ARBERLAND_OTA, {k: v for k, v in ARBERLAND_SEED.items() if k != "place_field"}, TOWNS)
    assert (obs["city"], obs["plz"], obs["lat"], obs["lon"]) == ("Viechtach", "94234", 49.0852209, 12.875395)
    assert (obs["n_locations"], json.loads(obs["locations"])) == (1, [{"adresse": {"ort": "Viechtach", "plz": "94234"}}])


def test_a_posting_naming_two_sites_lists_both_and_leaves_the_pick_to_the_matcher():
    obs = to_observation(ARBERLAND_SPRINGER, ARBERLAND_SEED, TOWNS)
    assert obs["n_locations"] == 2
    assert json.loads(obs["locations"]) == [{"adresse": {"ort": "Viechtach", "plz": "94234"}}, {"adresse": {"ort": "Zwiesel", "plz": None}}]
    assert (obs["city"], obs["plz"]) == ("Viechtach", "94234")        # the first place it names, as it lists them


def test_the_address_of_another_facility_is_dropped_when_the_posting_names_other_places():
    # MVZ postings carry their own address (Regen) but name the hospitals they work in
    obs = to_observation(ARBERLAND_MVZ_ORT, ARBERLAND_SEED, TOWNS)
    assert (obs["city"], obs["plz"], obs["lat"], obs["lon"]) == ("Viechtach", None, None, None)


def test_a_posting_without_the_place_field_keeps_reading_its_own_address():
    obs = to_observation(ARBERLAND_MVZ_NO_ORT, ARBERLAND_SEED, TOWNS)
    assert (obs["city"], obs["plz"], obs["in_bavaria"]) == ("Regen", "94209", True)
    obs = to_observation(ARBERLAND_NO_ADDRESS, ARBERLAND_SEED, TOWNS)
    assert (obs["city"], obs["plz"], obs["in_bavaria"]) == (None, None, None)


def test_the_arberland_seeds_name_the_place_field_and_no_other_tenant_does():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seeds = json.load(open(os.path.join(root, "data", "registry", "bite_seeds.json"), encoding="utf-8"))
    assert {s["kez"]: s.get("place_field") for s in seeds if s["customer"] == "arberland-kliniken"} == {"27601": "ort", "27602": "ort"}
    assert [s["customer"] for s in seeds if s.get("place_field")] == ["arberland-kliniken", "arberland-kliniken"]


def test_the_crawl_hands_the_seeds_place_field_to_the_adapter(monkeypatch):
    import app.crawl as CR
    seen = []
    monkeypatch.setattr("pflege_jobs.sources.bite.crawl", lambda seed, towns, log=print: (seen.append(seed), ([], {}))[1])
    for kez in ("27601", "56404"):
        c = {"clinic_id": kez, "name": "Test Klinik", "careers_url": "https://example.de/karriere", "town": "Zwiesel"}
        CR._seed_obs({"vendor": "bite"}, c, set(), lambda *_: None)
    assert [(s["kez"], s.get("place_field")) for s in seen] == [("27601", "ort"), ("56404", None)]
