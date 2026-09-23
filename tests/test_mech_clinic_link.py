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


def test_r1_exact_town_gate_survives_an_abbreviated_registry_qualifier():
    """Found live 2026-09-21 while validating the R1_exact town gate above on real production data:
    clinic 37301's registry town "Neumarkt i.d.OPf." abbreviates the Regierungsbezirk qualifier too
    (not just "in der" -> "i.d."), which city_key/_canon_town had no reason to expand -- the gate
    would have refused every real Klinikum Neumarkt posting stating the spelled-out city."""
    cl = [{"clinic_id": "37301", "name": "Klinikum Neumarkt", "town": "Neumarkt i.d.OPf.", "operator": None}]
    m = Matcher(cl)
    assert m.match("Klinikum Neumarkt", "Neumarkt in der Oberpfalz") == ("37301", "R1_exact", 1.0)


def test_board_town_gate_survives_a_leading_civic_qualifier():
    """Reviewer-found live regression (2026-09-21), same bug class as the abbreviated-qualifier test
    above but the mirror shape: clinic 17402's registry town carries a LEADING civic-status qualifier
    ('Markt Indersdorf') a posting's own city never repeats ('Indersdorf'). _town_match's prefix rule
    only covers a TRAILING qualifier -- fixed generally in city_key() (strip a leading 'markt', the
    only such qualifier measured across all 407 live clinics), not with another CITY_ALIASES entry,
    so a future 'Markt X' clinic is covered too. Live posting 10255, Helios Amper-Klinik Indersdorf,
    clinic 17402. Exercised here on the BOARD path (R0_board_town's positive town selection) rather
    than the single-candidate R1_exact gate, because R1_exact's own 'refuse only on a proven OTHER
    town' fix (below) would otherwise paper over this specific bug in a registry with no second
    'Indersdorf'-shaped candidate to prove disagreement with -- R0_board_town has no such fallback,
    it must actually SELECT the right town, so it isolates the city_key fix on its own."""
    cl = [{"clinic_id": "17402", "name": "Helios Amper-Klinik Indersdorf", "town": "Markt Indersdorf", "operator": "Helios Kliniken GmbH"},
          {"clinic_id": "99999", "name": "Helios Klinik Woanders", "town": "Woanders", "operator": "Andere Kliniken GmbH"}]
    m = Matcher(cl)
    # Employer text a content-side rule (name/operator/token overlap) cannot resolve at all --
    # isolates the board rules; only R0_board_town can place this posting.
    assert m.match("Generic Staffing Agency GmbH", "Indersdorf", board=["17402", "99999"]) == ("17402", "R0_board_town", 0.85)


def test_city_aliases_neumarkt_survives_on_the_board_path_too():
    """Reviewer-found overclaim (2026-09-21): the R1_exact test above no longer mutation-tests the
    CITY_ALIASES entry it names, under the *current* (post-reviewer-fix) code -- R1_exact's own
    'refuse only on a proven OTHER town' fallback (below) independently rescues clinic 37301 even
    with the alias value emptied, in any registry with no other 'Neumarkt'-shaped town to disagree
    with (verified against both this file's small fixture and the real 407-clinic live registry).
    R0_board_town does positive town selection, not gating, so it has no such fallback and genuinely
    needs the alias -- isolates it the same way the leading-qualifier test above isolates the
    'markt' strip."""
    cl = [{"clinic_id": "37301", "name": "Klinikum Neumarkt", "town": "Neumarkt i.d.OPf.", "operator": "Kliniken Nordoberpfalz AG"},
          {"clinic_id": "88888", "name": "Klinikum Anderswo", "town": "Anderswo", "operator": "Andere Kliniken GmbH"}]
    m = Matcher(cl)
    assert m.match("Generic Staffing Agency GmbH", "Neumarkt in der Oberpfalz",
                    board=["37301", "88888"]) == ("37301", "R0_board_town", 0.85)


def test_r1_exact_falls_through_on_a_known_disagreeing_city():
    """TASK-81 mechanism #2: R1_exact returned on a UNIQUE employer-name hit with NO town check at
    all, unlike its own R1_exact_town sibling two lines below -- a unique-in-the-registry employer
    name (e.g. a nationwide operator with exactly one Bavarian site) matched every one of that
    employer's postings, anywhere in the country, to the one Bavarian site.

    Refusal requires REAL contradicting evidence -- the posting's city naming some OTHER registry
    town (here X2, Hamburg) -- not just any known city string. Reviewer-found live regression
    (2026-09-21): the first cut refused on ANY known, non-agreeing city, which cost 17 of 2560 open
    postings their R1_exact match, 4 of them on a city ('RoMed Verbund', 'Titting', 'Petershausen')
    that is not any registry town at all -- not real evidence of a wrong site, just data the
    registry has no opinion on. 'Bielefeld' below is that case: a real city, known, disagreeing with
    X1's own town, but naming no OTHER registry site either -- still trusted."""
    cl = [{"clinic_id": "X1", "name": "Sonnenklinik Fernost GmbH", "town": "Rosenheim", "operator": None},
          {"clinic_id": "X2", "name": "Nordsee Fachklinik gGmbH", "town": "Hamburg", "operator": None}]
    m = Matcher(cl)
    assert m.match("Sonnenklinik Fernost GmbH", "Rosenheim") == ("X1", "R1_exact", 1.0)   # agreeing city still matches
    assert m.match("Sonnenklinik Fernost GmbH", "Hamburg") is None                         # names a DIFFERENT registry site -> refused
    assert m.match("Sonnenklinik Fernost GmbH", "Bielefeld") == ("X1", "R1_exact", 1.0)    # known city, no registry town at all -> not evidence
    assert m.match("Sonnenklinik Fernost GmbH", None) == ("X1", "R1_exact", 1.0)           # unknown city -> unchanged


def test_r1_exact_ignores_a_named_city_unreliable_employer():
    """TASK-96: KJF Klinik Hochried (clinic 18006, Murnau) shares josefinum.softgarden.io with its
    sibling Fachklinik KJF Josefinum (76110, Augsburg) -- every Hochried-employer posting on that
    board states city=Augsburg (the Diözese Augsburg's registered address) regardless of Hochried
    being the real employer, so the ordinary other_town_disagrees gate would refuse a genuinely
    correct, uniquely-named R1_exact match. registry.CITY_UNRELIABLE_EMPLOYERS is a named,
    single-employer exception (not a blanket relaxation -- see its own module comment for why a
    general fix was rejected after live-scanning 25 real refusals) that skips the gate ONLY for an
    employer_norm on that list."""
    from pflege_jobs.registry import CITY_UNRELIABLE_EMPLOYERS
    assert "kjf klinik hochried" in CITY_UNRELIABLE_EMPLOYERS
    cl = [{"clinic_id": "18006", "name": "KJF Klinik Hochried", "town": "Murnau", "operator": "Katholische Jugendfürsorge der Diözese Augsburg e.V."},
          {"clinic_id": "76110", "name": "Fachklinik KJF Josefinum", "town": "Augsburg", "operator": "KJF Klinik Josefinum gGmbH"}]
    m = Matcher(cl)
    assert m.match("KJF Klinik Hochried", "Augsburg") == ("18006", "R1_exact", 1.0)     # named exception -> still matches despite the disagreeing city
    assert m.match("KJF Klinik Hochried", "Murnau") == ("18006", "R1_exact", 1.0)       # agreeing city, unaffected either way
    assert m.match("Fachklinik KJF Josefinum", "Augsburg") == ("76110", "R1_exact", 1.0)  # sibling's own name, unaffected


def test_city_inherited_cannot_fabricate_agreement_with_the_seed_via_board_town():
    """TASK-81 mechanism #3 (city half): when a job page names no location at all, the crawler
    substitutes the seed clinic's own registry town (pflege_jobs/sources/inbox.py city_source='seed')
    -- R0_board_town then "agrees" with the seed by construction, filing every such row on whichever
    clinic seeded the crawl regardless of which site on the shared board it actually belongs to.
    Employer here is genuinely read off the page and matches neither site, so only the board rules
    are in play."""
    seed = {"clinic_id": "S1", "name": "Seed Klinikum", "town": "Seedstadt", "operator": None}
    sibling = {"clinic_id": "S2", "name": "Sibling Klinikum", "town": "Siblingstadt", "operator": None}
    m = Matcher([seed, sibling])
    assert m.match("Some Real Employer GmbH", "Seedstadt", board=["S1", "S2"], city_inherited=True) is None
    assert m.match("Some Real Employer GmbH", "Seedstadt", board=["S1", "S2"], city_inherited=False) == ("S1", "R0_board_town", 0.85)


def test_employer_inherited_cannot_fabricate_agreement_with_the_seed_via_board_name():
    """TASK-81 mechanism #3 (name half): employer_inherited already blocked R1_exact/R2_operator
    (_match_content), but the board-fallback's own en/et used to be built from the inherited
    (seed-echoed) name regardless -- R0_board_name then "agreed" with the seed purely because the
    employer text IS the seed's own registry name, copied there by the crawler. A same-named clinic
    OUTSIDE this board (S3) makes the name genuinely ambiguous content-side (by_name has 2 entries,
    neither town known -> R1_exact/R1_exact_town both decline), so this isolates the board rule."""
    seed = {"clinic_id": "S1", "name": "Seed Klinikum", "town": None, "operator": None}
    sibling = {"clinic_id": "S2", "name": "Sibling Klinikum", "town": None, "operator": None}
    elsewhere = {"clinic_id": "S3", "name": "Seed Klinikum", "town": None, "operator": None}
    m = Matcher([seed, sibling, elsewhere])
    assert m.match("Seed Klinikum", None, board=["S1", "S2"], employer_inherited=True) is None
    assert m.match("Seed Klinikum", None, board=["S1", "S2"], employer_inherited=False) == ("S1", "R0_board_name", 0.9)


def test_city_inherited_helper_reads_the_nested_jobposting_payload_marker():
    """pflege_jobs/sources/inbox.py buries city_source='seed' inside the jobposting branch's
    serialized payload (not a top-level key) -- pflege_jobs.cli._city_inherited must look there; a
    seeded-adapter observation setting it top-level (same convention as _emp_inherited) is read too."""
    import json
    from pflege_jobs import cli
    assert cli._city_inherited({"payload": json.dumps({"city_source": "seed"})}) is True
    assert cli._city_inherited({"payload": json.dumps({"city_source": "page"})}) is False
    assert cli._city_inherited({"city_source": "seed"}) is True
    assert cli._city_inherited({}) is False


def test_process_rows_wires_city_inherited_through_to_the_real_matcher(monkeypatch):
    """Integration pin for TASK-81 mechanism #3: a real jobposting row on a shared board, whose page
    named no location so a vendor adapter substituted the seed clinic's own town (city_source='seed'),
    must not resolve back to that seed via R0_board_town. Runs the real jobposting_to_obs and the
    real Matcher -- only EdgeSink is stubbed, to prove the cli.py wiring itself (not just the Matcher
    rule pinned separately above)."""
    import argparse
    from pflege_jobs import cli

    seed = {"clinic_id": "S1", "name": "Seed Klinikum", "town": "Seedstadt", "operator": None}
    sibling = {"clinic_id": "S2", "name": "Sibling Klinikum", "town": "Siblingstadt", "operator": None}
    m = Matcher([seed, sibling])
    row = {"inbox_id": 1, "kind": "jobposting", "source_url": "https://x.example/job/1", "source_host": "x.example",
           "payload": {"title": "Pflegefachkraft (m/w/d)", "org": "Some Real Employer GmbH",
                       "url": "https://x.example/job/1", "description": "",
                       "loc": [{"city": "Seedstadt", "plz": None, "region": None}],
                       "board_clinic_ids": ["S1", "S2"], "city_source": "seed"}}

    posted = []

    class _FakeSink:
        def __init__(self, *a, **kw): pass

        def write(self, obs, **kw):
            posted.extend(obs); return {"observations": len(obs)}
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)

    a = argparse.Namespace(no_ack=True)
    cli._process_rows([row], a, "https://db", {}, m, {"seedstadt", "siblingstadt"}, lambda acks: len(acks))

    assert len(posted) == 1 and posted[0]["city"] == "Seedstadt"
    assert posted[0]["_kez"] is None   # not fabricated back to the seed via the echoed town


def test_cli_inbox_default_reads_live_clinics_not_the_csv(monkeypatch):
    """TASK-80: data/registry/clinics.csv had drifted -- 117 of 399 active clinics missing
    careers_url, 30 more differing -- because ATS/career discovery (pflege_jobs/cli.py's own probe
    branch, pflege_jobs/mechanics.py) writes discovered careers_url straight to the live clinics
    table and never back to the CSV. app/crawl.py's production call (`_cli(["inbox"])`) passes no
    --clinics, so pflege_jobs/cli.py's default used to silently hand the Matcher that stale copy,
    where board rules (R0_board*) keyed on a clinic missing here could never fire. Pin: with no
    --clinics, cmd_inbox's Matcher registry comes from the live table (stubbed requests.get, no CSV
    file involved at all) and carries a clinic's real careers_url."""
    import argparse
    import requests
    from pflege_jobs import cli

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    live_row = {"clinic_id": "26108", "name": "LA-Regio Kliniken Landshut", "town": "Landshut",
                "operator": None, "beds": 862, "careers_url": "https://example.de/jobs"}

    class _Resp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    def fake_get(u, params=None, headers=None, timeout=None):
        assert "/rest/v1/clinics" in u, f"expected a live clinics table read, got {u}"
        return _Resp([live_row])
    monkeypatch.setattr(requests, "get", fake_get)

    seen = {}
    monkeypatch.setattr(cli, "_drain_local_once", lambda a, url, H, m, towns, **kw: seen.setdefault("m", m) and 0)
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)

    a = argparse.Namespace(clinics=None, no_ack=False, max_batches=1, inbox_db=None,
                           reprocess_run=None, reprocess_all=False)
    cli.cmd_inbox(a)

    assert seen["m"].by_id["26108"]["careers_url"] == "https://example.de/jobs"


def test_operator_tie_prefers_the_site_the_employer_text_actually_names():
    """TASK-57: kbo.de's per-posting Einsatzort block says "kbo-Kinderzentrum München" -- 16211's
    own name. But the Krankenhausplan lists that same gGmbH as the OPERATOR of both 16211 and
    16212, so the operator rung tied and _pick_site handed all 13 of those postings to 16212
    (kbo-Heckscher-Klinikum München) on bed count alone, which is blind to what the posting says."""
    cl = [{"clinic_id": "16211", "name": "kbo-Kinderzentrum München, Fachklinik für Sozialpädiatrie",
           "town": "München", "operator": "kbo-Kinderzentrum München gGmbH", "beds": 60},
          {"clinic_id": "16212", "name": "kbo-Heckscher-Klinikum München", "town": "München",
           "operator": "kbo-Kinderzentrum München gGmbH", "beds": 78}]
    m = Matcher(cl)
    r = m.match("kbo-Kinderzentrum München", "München")
    assert r[0] == "16211" and r[1] == "R2_operator_town_bestj"
    # the sister site is still reachable when the posting names IT
    assert m.match("kbo-Heckscher-Klinikum München", "München")[0] == "16212"
    # A genuine tie -- an operator name that names neither site any better than the other -- still
    # falls through to _pick_site/R6 (test_rules_r1_r2_r6 pins that path on München Klinik gGmbH).
