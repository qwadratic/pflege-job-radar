"""TASK-185: a posting is attached to a registry clinic only on evidence that it belongs there (2026-10-01).

404 of 2,794 judged open postings sat under a clinic whose town or operator their own text contradicts. In every wrong
row the crawler's place was the seed clinic's own town copied onto the posting (city_source 'seed') and the employer
often the seed clinic's own name (org_source 'seed'); a single-clinic board pool then attached anything, and the
nightly link-clinics stage re-created what the drain had refused. The rows below are frozen from the real raw rows and
pages of run 225 (2026-10-01), cut to the lines that matter; the registry rows are the live ones, reduced to what the
Matcher reads. No network: the sinks and lookups of cmd_inbox are stubbed, the local queue, the drain,
jobposting_to_obs and the Matcher are real."""
import argparse
import json
from pathlib import Path

import pytest

from pflege_jobs import cli, inbox_db as IB
from pflege_jobs.registry import Matcher, _pick_site, city_key


def clinic(cid, name, town, operator=None, beds=None):
    return {"clinic_id": cid, "name": name, "town": town, "operator": operator, "beds": beds}


NEUBURG = clinic("18501", "AMEOS Klinikum St. Elisabeth Neuburg", "Neuburg/Donau", "AMEOS Krankenhausgesellschaft Neuburg mbH", 298)
PARSBERG = clinic("37304", "Klinik für Psychosomatische Medizin und Psychotherapie", "Parsberg", "Medizinische Einrichtungen des Bezirks Oberpfalz - KU (AöR)", 0)
ZIEGELBERG = clinic("66103", "Klinik am Ziegelberg Frauenklinik Aschaffenburg", "Aschaffenburg", "Betreiber GmbH", 30)
ROTTAL = [clinic("27701", "Psychosomatische Fachklinik Simbach am Inn", "Simbach am Inn", "KU Rottal-Inn-Kliniken", 190),
          clinic("27702", "Kreiskrankenhaus Pfarrkirchen", "Pfarrkirchen", "KU Rottal-Inn-Kliniken", 80),
          clinic("27705", "Kreiskrankenhaus Eggenfelden", "Eggenfelden", "KU Rottal-Inn-Kliniken", 275),
          clinic("RH2718", "Geriatrische Rehabilitation am Kreiskrankenhaus Pfarrkirchen", "Pfarrkirchen", "Rottal-Inn Kliniken Kommunalunternehmen", 52)]
STRAUBING = clinic("26301", "Barmherzige Brüder Klinikum St. Elisabeth, Straubing", "Straubing", "Barmherzige Brüder Klinikum St. Elisabeth Straubing GmbH", 475)
REGENSBURG = clinic("36201", "Krankenhaus Barmherzige Brüder", "Regensburg", "Barmherzige Brüder gemeinnützige Krankenhaus GmbH", 985)
MUENCHEN = [clinic("16201", "München Klinik Schwabing", "München", "München Klinik gGmbH", 521),
            clinic("16202", "München Klinik Harlaching", "München", "München Klinik gGmbH", 660),
            clinic("16203", "München Klinik Neuperlach", "München", "München Klinik gGmbH", 545),
            clinic("16204", "München Klinik Thalkirchner Straße", "München", "München Klinik gGmbH", 150),
            clinic("16205", "München Klinik Bogenhausen", "München", "München Klinik gGmbH", 1020)]
NUERNBERG = [clinic("56401", "Klinikum Nürnberg - Betriebsstätte Nord", "Nürnberg", "KU Klinikum Nürnberg", 1276),
             clinic("56410", "Klinikum Nürnberg - Betriebsstätte Süd", "Nürnberg", "KU Klinikum Nürnberg", 957)]
BAMBERG = [clinic("46101", "Klinikum Bamberg - Betriebsstätte am Bruderwald", "Bamberg", "Sozialstiftung Bamberg", 911),
           clinic("46170", "Klinikum Bamberg - Betriebsstätte am Bruderwald", "Bamberg", "Sozialstiftung Bamberg", 0)]
ALLGAEU = [clinic("76301", "Klinikum Kempten", "Kempten", "Klinikverbund Allgäu gGmbH", 510),
           clinic("78001", "Klinik Immenstadt", "Immenstadt", "Klinikverbund Allgäu gGmbH", 190),
           clinic("78002", "Klinik Oberstdorf", "Oberstdorf", "Klinikverbund Allgäu gGmbH", 70)]
ANREGIOMED = [clinic("56101", "ANregiomed Klinikum Ansbach", "Ansbach", "ANregiomed gKU, AöR des Landkreises Ansbach und der Stadt Ansbach", 360),
              clinic("57103", "ANregiomed Klinik Rothenburg o.d.T.", "Rothenburg o.d. Tauber", "ANregiomed gKU, AöR des Landkreises Ansbach und der Stadt Ansbach", 145)]
GKG = [clinic("47101", "Juraklinik Scheßlitz", "Scheßlitz", "Gem. Krankenhausgesellschaft des Landkreises Bamberg mbH", 130),
       clinic("47102", "Steigerwaldklinik Burgebrach", "Burgebrach", "Gem. Krankenhausgesellschaft des Landkreises Bamberg mbH", 128)]
DIAKONIE = clinic("66310", "Tagesklinik für KJP Würzburg", "Würzburg", "Diakonisches Werk Würzburg e.V.", 0)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(IB, "PATH", str(tmp_path / "inbox.sqlite"))


class _Sink:
    links = []
    written = []
    def __init__(self, *a, **kw): pass
    def write(self, obs, **kw):
        _Sink.written.extend(obs)
        return {"observations": len(obs)}
    def write_clinics(self, rows, log=print): return len(rows)
    def _post(self, body):
        _Sink.links.extend(body.get("clinic_links") or [])
        return {}


def drain(monkeypatch, registry, rows, stored=True):
    """Real cmd_inbox over a local queue of `rows`. -> ({posting url: [clinic_id or None of every pushed link]},
    {url: process_note}). stored: every posting carries a link already, so an unmatched one pushes a clearing link."""
    _Sink.links, _Sink.written = [], []
    refs = {}
    monkeypatch.setattr(cli, "_live_clinics", lambda url, H: registry)
    monkeypatch.setattr(cli, "EdgeSink", _Sink)
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)
    monkeypatch.setattr(cli, "lookup_posting_ids", lambda get, url, H, obs: {
        (o["source_id"], o["source_ref"]): refs.setdefault(o["source_ref"], len(refs) + 1) for o in obs})
    monkeypatch.setattr(cli, "manual_posting_ids", lambda get, url, H, ids: set())
    monkeypatch.setattr(cli, "linked_posting_ids", lambda get, url, H, ids: set(ids) if stored else set())
    IB.enqueue(rows, run_id=1)
    cli.cmd_inbox(argparse.Namespace(no_ack=False, max_batches=10, inbox_db=None, reprocess_run=None, reprocess_all=False))
    by_ref = {pid: ref for ref, pid in refs.items()}
    out = {}
    for l in _Sink.links:
        out.setdefault(by_ref[l["posting_id"]], []).append(l["clinic_id"])
    notes = {u: n for u, n in IB.connect().execute("select source_url, process_note from inbox")}
    return out, notes


def seeded(host, board_url, url, title, org, city, board, description="", **payload):
    """A vendor-wp_jobs row as app/crawl.py queues it for a seeded board: employer and place are the seed clinic's own."""
    return {"kind": "jobposting", "collector": "vendor-wp_jobs-v1", "source_host": host, "source_url": url,
            "payload": {"title": title, "org": org, "org_source": "seed", "loc": [{"city": city, "plz": None, "region": None}],
                        "url": url, "page": url, "employmentType": "Teilzeit", "datePosted": "2026-09-30", "city_source": "seed",
                        "section_labels": [], "board_url": board_url, "board_clinic_ids": board, "description": description, **payload}}


# --- AMEOS (karriere.ameos.eu): one board of a nationwide group, its registry pool collapsed to Neuburg ----------------
AMEOS = "https://karriere.ameos.eu/offene-stellen/stelle/"
# The real urls end in "-in-kiel", "-in-sta%C3%9Ffurt", "-in-osnabr%C3%BCck": the adapters read that place now and the Bavaria gate drops
# such an ad before the Matcher (test_an_ameos_ad_whose_url_names_a_place_outside_bavaria_never_reaches_the_matcher). These are the same
# pages with the place left out of the url, the way run 225 stamped them: the crawler's place is the seed's copy and only the text names
# the site -- what the Matcher still has to refuse on a board that names other places.
KIEL = AMEOS + "11716-pflegefachkr%C3%A4fte"
STASSFURT = AMEOS + "12048-pflegefachkraft-ambulante-pflege"
OSNABRUECK = AMEOS + "11862-pflegefachkraft"
HILDESHEIM = AMEOS + "11533-pflegefachkraft-gerontopsychiatrie-in-hildesheim"


def ameos(url, title, description=""):
    return seeded("karriere.ameos.eu", "https://karriere.ameos.eu/offene-stellen/", url, title,
                  "AMEOS Klinikum St. Elisabeth Neuburg", "Neuburg/Donau", ["18501"], description)


AMEOS_ADS = [ameos(KIEL, "Pflegefachkräfte (m/w/d)", "Für das AMEOS Klinikum Kiel suchen wir Pflegefachkräfte (m/w/d) in Voll- oder Teilzeit"),
             ameos(STASSFURT, "Pflegefachkraft (m/w/d) Ambulante Pflege",
                   "Dann sind Sie bei der AMEOS Pflege und Eingliederung am Standort Staßfurt genau richtig"),
             ameos(OSNABRUECK, "Pflegefachkraft (m/w/d)", "Das AMEOS Klinikum Osnabrück ist ein modernes Fachkrankenhaus für Psychiatrie, Psychotherapie und psychosomatische Medizin.")]


def test_ameos_ads_of_other_sites_are_not_filed_under_the_seed_clinic(monkeypatch):
    # The Hildesheim ad names its place in its url, is outside Bavaria and never loaded -- but it shows the board serves
    # more than Neuburg. The three ads name no place of their own (Neuburg is the seed's copy): they stay unmatched, the
    # link run 225 left on them (R0_board) is cleared, and the note says why.
    links, notes = drain(monkeypatch, [NEUBURG], AMEOS_ADS + [ameos(HILDESHEIM, "Pflegefachkraft (m/w/d) Gerontopsychiatrie")])
    assert links == {KIEL: [None], STASSFURT: [None], OSNABRUECK: [None]}
    assert "R0_board refused" in notes[KIEL] and "no site match" in notes[KIEL]
    assert notes[HILDESHEIM] == "skipped: outside Bavaria"


def test_an_ameos_ad_whose_url_names_a_place_outside_bavaria_never_reaches_the_matcher(monkeypatch):
    real = [AMEOS + "11716-pflegefachkr%C3%A4fte-in-kiel", AMEOS + "12048-pflegefachkraft-ambulante-pflege-in-sta%C3%9Ffurt",
            AMEOS + "11862-pflegefachkraft-in-osnabr%C3%BCck"]
    links, notes = drain(monkeypatch, [NEUBURG], [ameos(u, "Pflegefachkraft (m/w/d)") for u in real])
    assert links == {} and {notes[u] for u in real} == {"skipped: outside Bavaria"}


def test_a_single_site_board_that_names_no_other_place_keeps_attaching(monkeypatch):
    # The common own board (csj.de, klinikum-memmingen.de ...): none of its postings names a place, so nothing says it
    # serves more than its one clinic -- same rows, no Hildesheim ad.
    links, _ = drain(monkeypatch, [NEUBURG], AMEOS_ADS)
    assert links == {KIEL: ["18501"], STASSFURT: ["18501"], OSNABRUECK: ["18501"]}


def test_an_ad_that_names_the_clinics_own_town_attaches_although_the_board_names_other_places(monkeypatch):
    own = ameos(AMEOS + "5707-pflegefachkraft-in-neuburg-an-der-donau", "Pflegefachkraft (m/w/d)")
    own["payload"].pop("city_source")          # the page itself states the town: not the seed's copy
    own["payload"]["loc"] = [{"city": "Neuburg an der Donau", "plz": None, "region": None}]
    links, _ = drain(monkeypatch, [NEUBURG], [own, ameos(HILDESHEIM, "Pflegefachkraft (m/w/d) Gerontopsychiatrie")])
    assert links == {own["source_url"]: ["18501"]}


def test_an_unmatched_posting_without_a_stored_link_pushes_nothing(monkeypatch):
    links, _ = drain(monkeypatch, [NEUBURG], AMEOS_ADS + [ameos(HILDESHEIM, "Pflegefachkraft (m/w/d) Gerontopsychiatrie")], stored=False)
    assert links == {}


# --- krankenpflegejobs24.de: an aggregator registered as 66103's careers_url ------------------------------------------
KPJ = "https://www.krankenpflegejobs24.de/"


def kpj(path, title, description, **payload):
    return seeded("krankenpflegejobs24.de", KPJ + "klinik-am-ziegelberg-frauenklinik-aschaffenburg", KPJ + path, title,
                  "Klinik am Ziegelberg Frauenklinik Aschaffenburg", "Aschaffenburg", ["66103"], description, **payload)


def test_krankenpflegejobs24_postings_of_other_towns_are_not_filed_under_the_seed_clinic(monkeypatch):
    """All 201 rows of this board in run 197 carried only the seed's town; the 181 postings still open sit under 66103
    (R0_board), 173 of them judged to be for a home in Hessen or elsewhere. Once an adapter reads the page's Arbeitsort
    (row 1: Bad Orb, outside Bavaria, never loaded) the board is known to serve other places, and the ad that still
    carries only the seed's town (row 2) has no evidence left."""
    bad_orb = kpj("bad-orb/krankenpflege/caritas-altenpflegeheim-st-martin/pflegefachkraft-in-bad-orb-100", "Pflegefachkraft (m/w/d) in Bad Orb",
                  "Arbeitsort Caritas-Altenpflegeheim St. Martin 63619 Bad Orb, Main-Kinzig-Kreis Hessen",
                  loc=[{"city": "Bad Orb", "plz": "63619", "region": "Hessen"}], city_source="page")
    frankfurt = kpj("frankfurt-am-main/krankenpflege/alloheim-senioren-residenzen-se-9/pflegefachkraft-im-springerpool-102",
                    "Pflegefachkraft (m/w/d) im Springerpool", "Arbeitsort Alloheim Senioren-Residenzen SE 60326 Frankfurt am Main, Hessen")
    links, _ = drain(monkeypatch, [ZIEGELBERG], [bad_orb, frankfurt])
    assert links == {frankfurt["source_url"]: [None]}


# --- Rottal-Inn Kliniken: four sites share one board; the ad is for Eggenfelden, the seed is Simbach -----------------
def test_rottal_inn_ad_for_eggenfelden_is_not_filed_under_the_seed_clinic(monkeypatch):
    url = "https://karriere.rottalinnkliniken.de/job/pflegefachkraft-urologie-m-w-d/"
    row = seeded("karriere.rottalinnkliniken.de", "https://karriere.rottalinnkliniken.de/offene-stellen/", url,
                 "Pflegefachkraft Urologie (m/w/d)", "Psychosomatische Fachklinik Simbach am Inn", "Simbach am Inn",
                 ["27701", "27702", "27705", "RH2718"],
                 "Wir suchen Sie zum nächstmöglichen Zeitpunkt in Eggenfelden als Pflegefachkraft Urologie (m/w/d)")
    links, notes = drain(monkeypatch, ROTTAL, [row])
    assert links == {url: [None]} and "no site match" in notes[url]


def test_link_clinics_does_not_recreate_the_link_the_drain_refused(monkeypatch, tmp_path):
    """cmd_link_clinics (nightly stage `link`, after the drain) matches the stored employer + city; both are the seed's
    copy, so it re-created the R1_exact link the drain had just refused (Rottal-Inn 12, AMEOS, Straubing)."""
    import requests
    from pflege_jobs.sinks import EdgeSink
    monkeypatch.setattr(cli, "_live_clinics", lambda url, H: ROTTAL)
    stamps = {"inbox": {"inbox_id": 1}, "city_source": "seed", "employer_source": "seed"}
    posting = lambda pid, payload: {"posting_id": pid, "title": "Pflegefachkraft Urologie (m/w/d)", "city": "Simbach am Inn",
                                    "clinic_match_rule": None, "posting_observations": [{"payload": payload}],
                                    "employers": {"name_display": "Psychosomatische Fachklinik Simbach am Inn", "employer_class": "clinic"}}
    page = [posting(1, json.dumps(stamps)), posting(2, stamps), posting(3, json.dumps({"inbox": {"inbox_id": 3}, "city_source": "page", "employer_source": "page"}))]

    class _Resp:
        def json(self): return page
    monkeypatch.setattr(requests, "get", lambda u, params=None, headers=None, timeout=None: _Resp())
    posted = []
    monkeypatch.setattr(EdgeSink, "_post", lambda self, body: (posted.append(body), {"clinic_links": len(body["clinic_links"])})[1])
    cli.cmd_link_clinics(argparse.Namespace(dry_run=False, out=str(tmp_path / "links.json")))
    # stamped (as a JSON string, as stored by jobposting_to_obs, or as a dict): nothing; unstamped: the exact match stands
    assert [(l["posting_id"], l["clinic_id"], l["clinic_match_rule"]) for b in posted for l in b["clinic_links"]] == [(3, "27701", "R1_exact")]


# --- Straubing: the portal of the Straubing hospital also lists the Regensburg hospital's ads -----------------------------
def straubing(title, description):
    return dict(employer="Barmherzige Brüder Klinikum St. Elisabeth, Straubing", city="Straubing", employer_inherited=True,
                city_inherited=True, board=["26301"], title=title, description=description)


def test_straubing_portal_ad_that_names_the_regensburg_hospital_is_filed_there():
    m = Matcher([dict(STRAUBING), dict(REGENSBURG)])
    ad = straubing("Hygienefachkraft (m/w/d)", "Das Krankenhaus Barmherzige Brüder Regensburg ... Hygieneteam aus 6 Hygienefachkräften")
    assert m.match(**ad) == ("36201", "R_jd_text", 0.65)


def test_straubing_portal_ad_that_names_no_site_is_unmatched():
    m = Matcher([dict(STRAUBING), dict(REGENSBURG)])
    ad = straubing("Pflegefachkraft (m/w/d)", "Gesundheits- und Kinderkrankenpfleger/in Intensivstation")
    notes = []
    assert m.match(**ad, foreign={"regensburg"}, note=notes) is None
    assert notes and "R0_board refused" in notes[0]


# --- R_jd_text: AMEOS Osnabrück would match the Parsberg clinic by its specialty words -----------------------------------
OSNABRUECK_TEXT = "Das AMEOS Klinikum Osnabrück ist ein modernes Fachkrankenhaus für Psychiatrie, Psychotherapie und psychosomatische Medizin."


def test_a_posting_that_stays_unmatched_says_why_in_its_note():
    m = Matcher([dict(NEUBURG), dict(PARSBERG)])
    notes = []
    assert m.match("AMEOS Klinikum St. Elisabeth Neuburg", "Neuburg/Donau", employer_inherited=True, city_inherited=True, board=["18501"],
                   foreign={"kiel"}, note=notes) is None
    assert len(notes) == 1 and notes[0].startswith("R0_board refused")                      # a refusal already says why: not overwritten
    for kwargs, why in (({"employer_inherited": True, "city_inherited": True}, "no evidence: its place and employer are the seed clinic's own copy, nothing else names a site"),
                        ({"city_inherited": True}, "no evidence: its place is the seed clinic's own copy, nothing else names a site"),
                        ({}, "no rule names a registry site for this employer and place")):
        notes = []
        assert m.match("Unbekannte Praxis GmbH", "Berlin", note=notes, **kwargs) is None
        assert notes == [why]


def test_r_jd_text_needs_the_clinics_town_in_the_text_too():
    m = Matcher([dict(NEUBURG), dict(PARSBERG)])
    assert m.match(None, None, description=OSNABRUECK_TEXT) is None          # was R_jd_text -> 37304 (cf_ameos: 5 new wrong matches)
    named = OSNABRUECK_TEXT + " Die Klinik in Parsberg ist ein Haus der Medbo."
    assert m.match(None, None, description=named) == ("37304", "R_jd_text", 0.65)


KHDW = [dict(clinic_id="77301", name="Kreisklinik St. Elisabeth Dillingen", town="Dillingen a.d.Donau", operator="Kreiskliniken Dillingen-Wertingen gGmbH", beds=182, parse_quality="ok"),
        dict(clinic_id="77302", name="Kreisklinik Wertingen", town="Wertingen", operator="Kreiskliniken Dillingen-Wertingen gGmbH", beds=117, parse_quality="ok")]
KHDW_TEXT = ("Pflegefachkraft (m/w/d) für den Standort Dillingen - Kreiskliniken Dillingen Wertingen Zum Inhalt springen Wir suchen Pflegefachkraft (m/w/d) "
             "für den Standort Dillingen Jetzt bewerben An der Kreisklinik St. Elisabeth Dillingen suchen wir zum nächstmöglichen Zeitpunkt eine "
             "Pflegefachkraft (m/w/d) für unsere Bettenstationen in Vollzeit oder Teilzeit (unbefristet)")


def test_an_unqualified_town_in_the_text_names_the_qualified_registry_town_unless_other_towns_share_it():
    m = Matcher([dict(c) for c in KHDW])
    # the group's boilerplate names both sites ("Kreiskliniken Dillingen Wertingen"): "Dillingen" must count as naming Dillingen a.d.Donau,
    # or Wertingen alone passes the town gate and every Dillingen ad is filed there
    assert m.match(None, None, description=KHDW_TEXT) is None
    assert m.match(None, None, description="An der Kreisklinik St. Elisabeth Dillingen suchen wir eine Pflegefachkraft (m/w/d).") == ("77301", "R_jd_text", 0.65)
    # "Bad" alone names none of the Bad towns
    bad = [dict(clinic_id="1", name="Rhön Fachklinik Alpha", town="Bad Windsheim", operator=None, beds=50, parse_quality="ok"),
           dict(clinic_id="2", name="Fachklinik Beta", town="Bad Kissingen", operator=None, beds=50, parse_quality="ok")]
    assert Matcher(bad).match(None, None, description="Die Rhön Fachklinik Alpha liegt im Kurort Bad Kissingen.") is None


# --- R6: which of several same-operator sites the posting is -------------------------------------------------------------
MUENCHEN_TEXT = ("Die München Klinik gGmbH ist eine Klinik mit fünf Standorten in Bogenhausen, Harlaching, Neuperlach, Schwabing sowie der "
                 "Fachklinik für Dermatologie und Allergologie in der Thalkirchner Straße. Pflegefachkraft Gynäkologie und Wochenbett (w|m|d) "
                 "Werden Sie Teil unserer Stationen für operative Gynäkologie und Wochenbett in Harlaching.")


def test_r6_does_not_pick_by_beds_when_the_text_names_several_sites():
    m = Matcher([dict(c) for c in MUENCHEN])
    notes = []
    # posting 44: the group boilerplate lists all five sites, the ad names Harlaching -- until 2026-10-01 the biggest, Bogenhausen, won
    assert m.match("München Klinik gGmbH", "München", description=MUENCHEN_TEXT, note=notes) is None
    assert notes == ["R6 refused: sites 16201,16202,16203,16204,16205 tie and the text names several of them"]


def test_a_refused_r6_tie_is_not_rescued_by_the_board_the_ad_came_from():
    m = Matcher([dict(c) for c in MUENCHEN])
    # 16205's registered board is the group's whole job market: it cannot say which site an ad that names several is for
    assert m.match("München Klinik gGmbH", "München", board=["16205"], description=MUENCHEN_TEXT) is None


def test_a_refusal_is_noted_once_however_many_rungs_reach_it():
    m = Matcher([dict(c) for c in NUERNBERG])
    notes = []
    for _ in range(2):
        _pick_site(m.clinics, ("Klinikum Nürnberg",), notes)
    assert notes == ["R6 refused: sites 56401,56410 tie and the text names none of them"]


def test_r6_takes_the_one_site_the_text_names():
    m = Matcher([dict(c) for c in MUENCHEN])
    text = "Pflegefachkraft Gynäkologie und Wochenbett (w|m|d) Werden Sie Teil unserer Stationen für operative Gynäkologie und Wochenbett in Harlaching."
    assert m.match("München Klinik gGmbH", "München", description=text) == ("16202", "R6_ambiguous_sites:16201,16202,16203,16204,16205", 0.5)


def test_r6_the_listing_of_the_ads_locations_names_the_site_not_the_boilerplate():
    m = Matcher([dict(c) for c in MUENCHEN])
    # München Klinik's own list of the places an ad is for (allJobs[i].locations[].title): the group boilerplate in the
    # text names every site, the listing says which ones this ad is for
    assert m.match("München Klinik gGmbH", "München", description=MUENCHEN_TEXT, sites=["München Klinik Neuperlach"]) \
        == ("16203", "R6_ambiguous_sites:16201,16202,16203,16204,16205", 0.5)
    notes = []
    assert m.match("München Klinik gGmbH", "München", description=MUENCHEN_TEXT, note=notes,
                   sites=["München Klinik Schwabing", "München Klinik Harlaching"]) is None
    assert notes == ["R6 refused: sites 16201,16202,16203,16204,16205 tie and the text names several of them"]
    notes = []
    assert m.match("München Klinik gGmbH", "München", description=MUENCHEN_TEXT, note=notes, sites=["Alle Standorte"]) is None
    assert notes == ["R6 refused: sites 16201,16202,16203,16204,16205 tie and the text names none of them"]


MK_ROWS = json.loads((Path(__file__).resolve().parent / "fixtures" / "board_samples" / "muenchen_klinik_rows_sample.json").read_text(encoding="utf-8"))
mk = lambda num, **payload: (lambda r: {**r, "payload": {**r["payload"], **payload}})(next(r for r in MK_ROWS if r["source_url"].rstrip("/").endswith("-" + num)))


def test_real_muenchen_klinik_rows_attach_where_the_listing_names_one_site_and_stay_unmatched_where_it_names_several(monkeypatch):
    # Three nursing rows exactly as crawl_muenchen_klinik queued them on 2026-10-01 (A2's adapters; contact sentence removed): the same
    # boilerplate names all five sites in every text, the `sites` listing of the posting is what differs -- Harlaching alone (the
    # adapter then names it as the employer too), Schwabing + Harlaching, and all five.
    links, notes = drain(monkeypatch, MUENCHEN, MK_ROWS)
    one, two, five = (mk(n)["source_url"] for n in ("43515", "43495", "43483"))
    assert links == {one: ["16202"], two: [None], five: [None]}
    assert "R6 refused: sites 16201,16202,16203,16204,16205 tie and the text names several of them" in notes[two] == notes[five]


def test_the_listing_of_a_real_muenchen_klinik_row_reaches_the_matcher_through_jobposting_to_obs(monkeypatch):
    # jobposting_to_obs hands the listing over as the private key _sites; _process_rows pops it next to _board. The same real row with
    # a listing that names one clinic and one place that is none (the adapter keeps the employer as the gGmbH then) is attached to that
    # clinic and not to the bigger Bogenhausen; without a listing the boilerplate names all five and it is refused.
    url = mk("43495")["source_url"]
    narrowed = mk("43495", sites=["München Klinik Harlaching", "Bildungscampus"])
    assert drain(monkeypatch, MUENCHEN, [narrowed])[0] == {url: ["16202"]}
    # the stored observation keeps the listing (the private keys _sites and _board are popped), where the nightly link stage reads it back
    stored = [o for o in _Sink.written if o["source_url"] == url]
    assert [json.loads(o["payload"])["sites"] for o in stored] == [["München Klinik Harlaching", "Bildungscampus"]]
    assert not any("_sites" in o or "_board" in o for o in stored)
    unlisted = mk("43495"); unlisted["payload"].pop("sites")
    assert drain(monkeypatch, MUENCHEN, [unlisted])[0] == {url: [None]}


def test_the_listing_of_locations_reaches_the_matcher_in_the_drain_and_in_the_link_stage(monkeypatch, tmp_path):
    url = "https://www.muenchen-klinik.de/karriere/jobs/pflegefachkraft-neuperlach"
    ad = {"kind": "observation", "collector": "vendor-muenchen_klinik-v1", "source_host": "www.muenchen-klinik.de", "source_url": url,
          "payload": {"source_id": 20, "source_ref": "muenchen_klinik:1", "source_url": url, "title": "Pflegefachkraft (w|m|d)",
                      "employer_name": "München Klinik gGmbH", "employer_class_rule": "x", "role_class": "pflegefachkraft",
                      "in_bavaria": True, "city": "München", "description": MUENCHEN_TEXT, "sites": ["München Klinik Neuperlach"]}}
    links, _ = drain(monkeypatch, MUENCHEN, [ad])
    assert links == {"muenchen_klinik:1": ["16203"]}
    # the nightly link stage reads the stored payload (a JSON string or a dict) and must not disagree with the drain
    import requests
    from pflege_jobs.sinks import EdgeSink
    posting = lambda pid, payload, title: {"posting_id": pid, "title": title, "city": "München", "clinic_match_rule": None,
                                           "posting_observations": [{"payload": payload}],
                                           "employers": {"name_display": "München Klinik gGmbH", "employer_class": "clinic"}}
    page = [posting(1, json.dumps({"sites": ["München Klinik Neuperlach"]}), "Pflegefachkraft (w|m|d)"),
            posting(2, {"sites": ["Alle Standorte"]}, "Pflegefachkraft Schwabing (w|m|d)")]

    class _Resp:
        def json(self): return page
    monkeypatch.setattr(requests, "get", lambda u, params=None, headers=None, timeout=None: _Resp())
    posted = []
    monkeypatch.setattr(EdgeSink, "_post", lambda self, body: (posted.append(body), {"clinic_links": len(body["clinic_links"])})[1])
    cli.cmd_link_clinics(argparse.Namespace(dry_run=False, out=str(tmp_path / "links.json")))
    # posting 2's title names Schwabing, its listing says "all sites": the listing is what the ad is for
    assert [(l["posting_id"], l["clinic_id"]) for b in posted for l in b["clinic_links"]] == [(1, "16203")]


def test_r6_nuernberg_campus_sued_ads_go_to_the_sued_site():
    m = Matcher([dict(c) for c in NUERNBERG])
    title = "Pflegefachkraft (m/w/d) Springerpool - Chirurgie, Innere Medizin, Intensivmedizin, Pädiatrie Campus Süd"
    assert m.match("Klinikum Nürnberg", "Nürnberg", title=title) == ("56410", "R6_ambiguous_sites:56401,56410", 0.5)   # was 56401: the bigger
    notes = []
    assert m.match("Klinikum Nürnberg", "Nürnberg", title="Pflegefachkraft (m/w/d)", note=notes) is None
    assert notes == ["R6 refused: sites 56401,56410 tie and the text names none of them"]


def test_r6_one_site_entered_twice_still_stands_for_the_bigger_row():
    # Plan-KH row and its Vertrags-KH placeholder (decision-4): no text can tell them apart
    m = Matcher([dict(c) for c in BAMBERG])
    assert m.match("Sozialstiftung Bamberg", "Bamberg") == ("46101", "R6_ambiguous_sites:46101,46170", 0.5)


# --- the stamp markers, wherever the adapter puts them ------------------------------------------------------------------
def test_a_stamp_marker_is_read_from_the_row_the_payload_dict_or_the_payload_string():
    seed = {"city_source": "seed"}
    assert cli._city_inherited(dict(seed))                                         # a seeded adapter's observation: top-level key
    assert cli._city_inherited({"payload": dict(seed)})                            # stored posting_observations row
    assert cli._city_inherited({"payload": json.dumps(seed)})                      # jobposting_to_obs: the serialized payload
    assert not cli._city_inherited({"payload": json.dumps({"city_source": "page"})}) and not cli._city_inherited({})
    assert cli._marker({"payload": json.dumps({"employer_source": "seed"})}, "employer_source") == "seed"
    assert cli._marker({"payload": "not json"}, "employer_source") is None


def observation(url, title, employer, city, description, board, **extra):
    """A finished observation of a seeded umantis / TYPO3 adapter as app/crawl.py queues it (_obs_row)."""
    return {"kind": "observation", "collector": "seed-20", "source_host": url.split("/")[2], "source_url": url,
            "payload": {"source_id": 20, "source_ref": url, "source_url": url, "title": title, "employer_name": employer,
                        "employer_class_rule": "clinic:klinik", "role_class": "pflegefachkraft", "in_bavaria": True, "city": city,
                        "description": description, "_board": board, **extra}}


def test_allgaeu_ad_for_oberstdorf_and_immenstadt_is_not_filed_under_the_seed_city(monkeypatch):
    url = ("https://karriere.klinikverbund-allgaeu.de/karriere-detail/Immenstadt-Oberstdorf/"
           "Pflegefachkraft-mwd-Notfallsanitter-mwd-und-Ansthesietechnische-Assistenz-ATA-fr-die-Ansthesie/856")
    row = observation(url, "Pflegefachkraft (m/w/d), Notfallsanitäter (m/w/d) und Anästhesietechnische Assistenz (ATA) für die Anästhesie",
                      "Klinikverbund Allgäu gGmbH", "Kempten", "flexible Einsatz in den Kliniken Oberstdorf und Immenstadt sind Voraussetzung",
                      ["76301", "78001", "78002"])
    assert drain(monkeypatch, ALLGAEU, [row])[0] == {url: ["76301"]}              # no marker (today's rows): the operator + Kempten match
    row["payload"]["city_source"] = "seed"                                        # what the adapter sets once it knows the town is the seed's
    assert drain(monkeypatch, ALLGAEU, [row])[0] == {url: [None]}


def test_anregiomed_ad_for_rothenburg_follows_the_place_the_adapter_reads(monkeypatch):
    url = "https://recruitingapp-5511.de.umantis.com/Vacancies/653/Description/1?lang=ger"
    text = "Pflegefachmann/-frau für die Intensivpflege (m/w/d) Klinik Rothenburg Voll- oder Teilzeit EG P8-P9 TVöD-k"
    seed = observation(url, "Pflegefachmann/-frau für die Intensivpflege (m/w/d)", "ANregiomed Klinikum Ansbach", "Ansbach", text,
                       ["56101", "57103"], city_source="seed", _emp_inherited=True)
    assert drain(monkeypatch, ANREGIOMED, [seed])[0] == {url: [None]}              # Ansbach is the seed's copy, not the posting's place
    own = observation(url, "Pflegefachmann/-frau für die Intensivpflege (m/w/d)", "ANregiomed Klinikum Ansbach", "Rothenburg o.d. Tauber",
                      text, ["56101", "57103"], _emp_inherited=True)         # spelled as the registry does: city_key("Rothenburg ob der Tauber") differs
    assert drain(monkeypatch, ANREGIOMED, [own])[0] == {url: ["57103"]}


def test_a_seeded_observation_on_a_board_that_names_other_places_attaches_no_better_than_a_raw_row(monkeypatch):
    stamped = "https://karriere.rottal.example/stellen/1"
    own = observation(stamped, "Pflegefachkraft (m/w/d)", "Kreiskrankenhaus Eggenfelden", "Eggenfelden", "", ["27705"], city_source="seed", _emp_inherited=True)
    other = observation("https://karriere.rottal.example/stellen/2", "Pflegefachkraft (m/w/d) Simbach", "Kreiskrankenhaus Eggenfelden",
                        "Simbach am Inn", "", ["27705"], _emp_inherited=True)
    assert drain(monkeypatch, ROTTAL, [own])[0] == {stamped: ["27705"]}              # nothing on the board names another place
    assert drain(monkeypatch, ROTTAL, [own, other])[0][stamped] == [None]           # an ad on it names Simbach: not Eggenfelden's own board


def test_the_markers_are_stored_with_the_observation_for_the_nightly_link_stage(monkeypatch):
    o = {"employer_name": "Klinikverbund Allgäu gGmbH", "payload": {"x": 1}, "city_source": "seed"}
    cli._persist_markers(o, employer_inherited=True, city_inherited=True)
    assert o["payload"] == {"x": 1, "employer_source": "seed", "city_source": "seed"}
    o = {"payload": json.dumps({"x": 1})}
    cli._persist_markers(o, employer_inherited=False, city_inherited=False)
    assert json.loads(o["payload"]) == {"x": 1, "employer_source": "page"}
    # and the drain does it to every seeded observation it writes
    url = "https://recruitingapp-5511.de.umantis.com/Vacancies/653/Description/1?lang=ger"
    drain(monkeypatch, ANREGIOMED, [observation(url, "Pflegefachkraft (m/w/d)", "ANregiomed Klinikum Ansbach", "Ansbach", "", ["56101", "57103"],
                                                  city_source="seed", _emp_inherited=True)])
    assert json.loads(_Sink.written[0]["payload"]) == {"employer_source": "seed", "city_source": "seed"}


# --- gkg-bamberg.de: one posting, two copies (one per clinic board), each carrying its own seed's stamps ------------------
def test_gkg_copies_from_two_boards_agree_on_no_clinic_and_clear_the_stored_link(monkeypatch):
    url = "https://gkg-bamberg.de/job/pflegefachkraefte-m-w-in-vollzeit-oder-teilzeit/"
    text = "Die Stelle kann in einem unserer elf Seniorenzentren angetreten werden: St. Vitus Burgebrach, St. Elisabeth Scheßlitz, St. Bernhard Ebrach."
    copy = lambda cid, name, town, board_url: seeded("gkg-bamberg.de", board_url, url, "Pflegefachkraft (m/w/d) (in Vollzeit oder Teilzeit)",
                                                      name, town, [cid], text)
    links, _ = drain(monkeypatch, GKG, [copy("47101", "Juraklinik Scheßlitz", "Scheßlitz", "https://gkg-bamberg.de/offene-stellen/"),
                                        copy("47102", "Steigerwaldklinik Burgebrach", "Burgebrach", "https://gkg-bamberg.de/job/stationshilfen/")])
    # run 225: each copy matched its own seed (R3_tokens) -> CONFLICT, the stored 47101 stayed. Now neither copy has evidence.
    assert links == {url: [None]}


def test_a_copy_without_evidence_abstains_it_neither_conflicts_with_nor_undoes_a_copy_that_has_some(monkeypatch):
    url = "https://gkg-bamberg.de/job/pflegefachkraefte-m-w-in-vollzeit-oder-teilzeit/"
    stated = seeded("gkg-bamberg.de", "https://gkg-bamberg.de/offene-stellen/", url, "Pflegefachkraft (m/w/d)", "Juraklinik Scheßlitz",
                    "Scheßlitz", ["47101"], "Die Stelle kann in einem unserer elf Seniorenzentren angetreten werden")
    for stamp in ("org_source", "city_source"):
        stated["payload"].pop(stamp)               # this copy's page states employer and town itself
    stamped = seeded("gkg-bamberg.de", "https://gkg-bamberg.de/job/stationshilfen/", url, "Pflegefachkraft (m/w/d)",
                     "Steigerwaldklinik Burgebrach", "Burgebrach", ["47102"], "Die Stelle kann in einem unserer elf Seniorenzentren angetreten werden")
    assert drain(monkeypatch, GKG, [stated, stamped])[0] == {url: ["47101"]}


# --- diakonie-wuerzburg.de: the limit of the data-driven rule, pinned ----------------------------------------------------------
def test_diakonie_wuerzburg_ads_still_attach_because_nothing_on_the_board_names_another_place(monkeypatch):
    """Every row of this board carries only the seed's town (Würzburg); the ad itself is for the Evang. Wohnstift St. Paul in
    Würzburg-Heidingsfeld, an elderly-care home, not the clinic -- a different facility in the same town. No place, hence
    no counter-evidence: R0_board stays. Only an adapter that reads the facility off the page can separate it; if this
    test fails because the pipeline now does, that is the improvement."""
    url = "https://diakonie-wuerzburg.de/diakonisches-werk-wuerzburg/arbeiten-in-der-diakonie/aktuelle-jobs/pflegefachkraft-stp.html"
    row = seeded("diakonie-wuerzburg.de", "https://diakonie-wuerzburg.de/therapie-behandlung/tagesklinische-behandlung/stellenangebote/", url,
                 "Pflegefachkraft (m/w/d) - Diakonisches Werk Würzburg", "Tagesklinik für KJP Würzburg", "Würzburg", ["66310"],
                 "Pflegefachkraft (m/w/d) Evang. Wohnstift St. Paul, Würzburg-Heidingsfeld Vollzeit, Teilzeit")
    assert drain(monkeypatch, [DIAKONIE], [row])[0] == {url: ["66310"]}


# --- Matcher.foreign_places -------------------------------------------------------------------------------------------------
def test_foreign_places_are_the_places_beyond_the_towns_of_the_pools_clinics():
    m = Matcher([dict(NEUBURG)] + [dict(c) for c in ROTTAL])
    keys = lambda *places: {city_key(p) for p in places}
    assert m.foreign_places(["18501"], keys("Neuburg an der Donau", "Kiel", "Staßfurt")) == keys("Kiel", "Staßfurt")
    assert m.foreign_places(["27701", "27702", "27705"], keys("Eggenfelden", "Pfarrkirchen", "Simbach am Inn")) == set()
    assert m.foreign_places(["27702"], keys("Eggenfelden", "Pfarrkirchen")) == keys("Eggenfelden")
