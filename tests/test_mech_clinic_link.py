from pflege_jobs.registry import Matcher, _town_match, city_key
from pflege_jobs.mechanics import get

CL = [{"clinic_id": "16101", "name": "Klinikum Ingolstadt", "town": "Ingolstadt", "operator": "Klinikum Ingolstadt GmbH"},
      {"clinic_id": "16201", "name": "München Klinik Schwabing", "town": "München", "operator": "München Klinik gGmbH"},
      {"clinic_id": "16202", "name": "München Klinik Harlaching", "town": "München", "operator": "München Klinik gGmbH"},
      {"clinic_id": "58101", "name": "Klinikum Fürth", "town": "Fürth", "operator": "Klinikum Fürth"},
      {"clinic_id": "18701", "name": "Kliniken Südostbayern Klinikum Traunstein", "town": "Traunstein", "operator": "Kliniken Südostbayern AG"},
      {"clinic_id": "18702", "name": "Kliniken Südostbayern Kreisklinik Bad Reichenhall", "town": "Bad Reichenhall", "operator": "Kliniken Südostbayern AG"}]


def test_r2_operator_town_no_longer_collapses_bad_towns():
    """2026-09-18 crawler review: city_key's old .split()[0] fallback collapsed every "Bad *" town
    into one bucket, so a Heiligenfeld-shaped operator with sites in two different "Bad *" towns
    matched the wrong one via R2_operator_town."""
    cl = [{"clinic_id": "67208", "name": "Fachklinik Heiligenfeld", "town": "Bad Kissingen", "operator": "Heiligenfeld Kliniken GmbH"},
          {"clinic_id": "18601", "name": "Klinik Waldmuenster", "town": "Bad Woerishofen", "operator": "Heiligenfeld Kliniken GmbH"}]
    m = Matcher(cl)
    r = m.match("Heiligenfeld Kliniken GmbH", "Bad Woerishofen")
    assert r == ("18601", "R2_operator_town", 0.9)   # not the Bad Kissingen clinic


def test_r0_board_single_clinic_pool_refuses_a_disagreeing_known_city():
    """2026-09-18 crawler review: a single-clinic board pool used to win with no city check at all
    -- decision-5's "no match beats a wrong match" now applies to R0_board too."""
    cl = [{"clinic_id": "18105", "name": "Psychosomatische Klinik Kloster Diessen", "town": "Dießen am Ammersee", "operator": None}]
    m = Matcher(cl)
    assert m.match("Some Other Org GmbH", "Dießen am Ammersee", board=["18105"]) == ("18105", "R0_board", 0.9)
    assert m.match("Some Other Org GmbH", "Tutzing", board=["18105"]) is None   # known, disagreeing city -> refused
    assert m.match("Some Other Org GmbH", None, board=["18105"]) == ("18105", "R0_board", 0.9)   # unknown city -> unchanged


def test_rules_r1_r2_r6():
    m = Matcher(CL)
    assert m.match("Klinikum Ingolstadt GmbH", "Ingolstadt")[1] in ("R1_exact", "R2_operator")
    assert m.match("Klinikum Fürth", "Fürth")[0] == "58101"
    assert m.match("Kliniken Südostbayern AG", "Traunstein") == ("18701", "R2_operator_town", 0.9)
    r = m.match("München Klinik gGmbH", "München")
    assert r[1].startswith("R6_ambiguous_sites:16201,16202")


def test_never_links_ambiguous_or_non_clinic():
    m = Matcher(CL)
    assert m.match("Kliniken Südostbayern AG", "Rosenheim") is None          # ambiguous operator, unknown town
    assert m.match("AWO Seniorenzentrum Fürth", "Fürth") is None


def test_no_false_match_across_city_key_bad_collapse():
    # city_key() reduces every "Bad X" town to the single key "bad" (Bad Reichenhall, Bad Windsheim,
    # Bad Steben, ... all collapse together) -- a real out-of-state employer whose own name carries
    # no token overlapping any Bavaria "Bad *" site must not fall through to a match just because it
    # shares that collapsed town bucket. Found live 2026-09-11: 'Klinik Reinhardshöhe GmbH' (Bad
    # Wildungen, Hesse) false-matched to 'Kreisklinik Bad Reichenhall' this way.
    m = Matcher(CL)
    assert m.match("Klinik Reinhardshöhe GmbH", "Bad Wildungen") is None


def test_tokens_and_stopwords():
    m = Matcher(CL)
    assert m.match("Klinikum Fürth Personalabteilung", "Fürth")[0] == "58101"


def test_prefers_real_site_over_beds_less_duplicate():
    # decision-4: the Bayern Krankenhausplan lists a real Plan-KH site (real beds) alongside a
    # near-duplicate placeholder entry for the same building (Vertrags-KH, or a beds-less satellite
    # day-clinic under a DIFFERENT operator) -- both token-tie against a generic employer string.
    # Found live 2026-09-11: Klinikum Bamberg-Bruderwald (46101, 911 beds) vs. its Vertrags-KH twin
    # (46170, 0 beds, same operator) vs. a beds-less KJP day-clinic sharing the building name under a
    # third operator (46110) -- three-way token tie, only one candidate has real capacity.
    m = Matcher([
        {"clinic_id": "46101", "name": "Klinikum Bamberg - Betriebsstätte am Bruderwald-", "town": "Bamberg",
         "operator": "Sozialstiftung Bamberg", "beds": 911},
        {"clinic_id": "46110", "name": "Tagesklinik für KJP am Klinikum Bamberg - Betriebsstätte am Bruderwald-",
         "town": "Bamberg", "operator": "KU Gesundheitseinrichtungen des Bezirks Oberfranken (GeBO)", "beds": 0},
        {"clinic_id": "46170", "name": "Klinikum Bamberg - Betriebsstätte am Bruderwald", "town": "Bamberg",
         "operator": "Sozialstiftung Bamberg", "beds": 0},
    ])
    r = m.match("Klinikum Bamberg (Bruderwald)", "Bamberg")
    assert r[0] == "46101" and r[1].endswith("_realsite")


def test_board_name_match_rejects_disagreeing_city():
    # TASK-59a: a crawler's own org-defaulting bug can make employer_name IDENTICAL for every
    # posting on a shared multi-site board regardless of the real site (found live 2026-09-11:
    # karriere.ameos.eu's crawl_wp_jobs sets every row's employer_name to whichever clinic seeded
    # the crawl -- R0_board_name then silently matched postings for towns nowhere near Bavaria to
    # that one seed clinic just because they shared its board pool). The employer name here is
    # deliberately ambiguous GLOBALLY (two same-named AMEOS sites, one in Bavaria, one not) so
    # _match_content's own R1_exact can't resolve it -- only board-pool R0_board_name can, and it
    # must not do so when the posting's own city disagrees with every pool candidate's town.
    ameos = [{"clinic_id": "A1", "name": "AMEOS Klinikum Neuburg", "town": "Neuburg", "operator": "AMEOS Gruppe"},
             {"clinic_id": "A2", "name": "AMEOS Klinikum Neuburg", "town": "Halberstadt", "operator": "AMEOS Gruppe"},
             {"clinic_id": "A3", "name": "AMEOS Klinikum Inntal", "town": "Haag in Oberbayern", "operator": "AMEOS Gruppe"}]
    m = Matcher(ameos)
    assert m.match("AMEOS Klinikum Neuburg", "Haldensleben", board=["A1", "A3"]) is None
    # Same board, a city that DOES agree with the seed candidate still resolves correctly -- via
    # _match_content's own R1_exact_town before board fallback is even reached.
    assert m.match("AMEOS Klinikum Neuburg", "Neuburg", board=["A1", "A3"]) == ("A1", "R1_exact_town", 0.98)
    # No known city at all (ck falsy) -- the new guard only rejects a city that actively disagrees.
    assert m.match("AMEOS Klinikum Neuburg", None, board=["A1", "A3"]) == ("A1", "R0_board_name", 0.9)


def test_uni_aliases():
    m = Matcher([{"clinic_id": "56290", "name": "Klinikum der Friedrich-Alexander-Universität Erlangen-Nürnberg", "town": "Erlangen", "operator": "Freistaat Bayern", "beds": 1400},
                 {"clinic_id": "16290", "name": "Klinikum der Ludwig-Maximilians-Universität München", "town": "München", "operator": "Freistaat Bayern", "beds": 2000},
                 {"clinic_id": "16291", "name": "Klinikum der Technischen Universität München (TUM) - Klinikum rechts der Isar", "town": "München", "operator": "Freistaat Bayern", "beds": 1100}])
    assert m.match("Universitätsklinikum Erlangen AöR", "Erlangen")[0] == "56290"
    assert m.match("LMU Klinikum (Campus Großhadern)", "München")[0] == "16290"
    assert m.match("Klinikum rechts der Isar der Technischen Universität München", "München")[0] == "16291"
    assert m.match("TUM Klinikum Rechts der Isar", "München")[0] == "16291"


def test_city_key():
    # Full canonical town, not truncated to one word (2026-09-18): a registry town keeps its
    # geographic qualifier as a real disambiguating token (city_key.__doc__), so "Bad Kissingen"
    # and "Bad Wörishofen" -- or "Neuburg an der Donau" and any other "Neuburg *" -- no longer
    # collapse onto the same bare stem. _town_match's prefix rule still lets a posting that just
    # says "Neuburg" earn the qualified registry town.
    assert city_key("82467 Garmisch-Partenkirchen") == "garmisch partenkirchen"
    assert city_key("Landshut, Isar") == "landshut" and city_key("Neuburg an der Donau") == "neuburg donau" and city_key("Muenchen") == "münchen"
    assert city_key("Bad Kissingen") != city_key("Bad Wörishofen")


def test_town_match_is_prefix_aware_but_not_over_permissive():
    assert _town_match("neuburg donau", "neuburg")           # bare posting city still earns the qualified registry town
    assert _town_match("neuburg", "neuburg donau")            # symmetric
    assert _town_match("bad kissingen", "bad kissingen")
    assert not _town_match("bad kissingen", "bad wörishofen")  # distinct towns, both start with "bad" -- must not match
    assert not _town_match("neuburg donau", "neu")             # not a whitespace-delimited prefix
    assert not _town_match("", "neuburg") and not _town_match("neuburg", "")


def test_mechanic_try_uses_real_registry():
    r = get("clinic_link").run({"employer": "Klinikum Fürth Personalabteilung", "city": "Fürth"})
    assert r["result"]["clinic_id"] and r["rule"].startswith("R")
    assert get("clinic_link").run({"employer": "AWO Seniorenzentrum", "city": "Fürth"})["result"]["clinic_id"] is None
