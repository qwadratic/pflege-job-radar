"""TASK-431.9 experiment: place match with a degree of confidence (pflege_jobs/place_conf.py). Offline, no mirror, no DB, no network:
the gazetteer is data/geo/gemeinden_de.csv (Destatis, one seat PLZ per municipality) and every expected value below is a literal
(the 12-digit AGS and the seat PLZ of the municipality as the Destatis table writes them). The two redacted slices (kbo.de board,
a one-site board) carry only places, no names, e-mails or phone numbers.

Nothing in the repo imports pflege_jobs.place_conf: this file is the only reader."""
import pytest

from pflege_jobs import place_conf as P

G = P.Gazetteer()

# a redacted slice of GeoNames' public postal-code file (CC BY 4.0): PLZ, place, Kreis key. Only places, so a test reads PLZ -> Kreis
# without the 2.3 MB file, which is never committed.
MINI_GEONAMES = "\n".join("\t".join(["DE", plz, place, "Bayern", "BY", "Upper Bavaria", "09", kreis_name, kreis, "48.0", "11.0", "4"]) for plz, place, kreis_name, kreis in [
    ("86156", "Augsburg", "Augsburg", "09761"), ("86159", "Augsburg", "Augsburg", "09761"), ("96049", "Bamberg", "Bamberg", "09461"),
    ("80538", "München", "Kreisfreie Stadt München", "09162"), ("81377", "München", "Kreisfreie Stadt München", "09162"),
    ("85540", "Haar", "München", "09184"), ("85049", "Ingolstadt", "Kreisfreie Stadt Ingolstadt", "09161"),
    ("84416", "Taufkirchen (Vils)", "Erding", "09177"), ("82467", "Garmisch-Partenkirchen", "Garmisch-Partenkirchen", "09180"),
    ("83734", "Hausham", "Miesbach", "09182"), ("83435", "Bad Reichenhall", "Berchtesgadener Land", "09172"),
    ("95111", "Rehau", "Hof", "09475"), ("96250", "Ebensfeld", "Lichtenfels", "09478"), ("96231", "Bad Staffelstein", "Lichtenfels", "09478"),
    ("94234", "Viechtach", "Regen", "09276")]) + "\n"


@pytest.fixture(scope="module")
def GG(tmp_path_factory):
    f = tmp_path_factory.mktemp("geonames") / "DE.txt"
    f.write_text(MINI_GEONAMES, encoding="utf-8")
    return P.Gazetteer(geonames=f)

MUENCHEN = "091620000000"


# ---- C. a city in any spelling ends at one municipality and its PLZ ---------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "München", "Muenchen", "MÜNCHEN", "muenchen", "München, Bayern", "München Bayern", "München, Deutschland",
    "München-Pasing", "München (Aubing)", "München Süd", "Munchen",
])
def test_munich_in_every_spelling_is_one_municipality_with_the_seat_plz(raw):
    r = G.resolve(raw)
    assert [m.ars for m in r.munis] == [MUENCHEN], (raw, r)
    assert r.status == "unique" and r.plz_main == "80313"


@pytest.mark.parametrize("raw,ars", [
    ("Neustadt a. d. Waldnaab, Bayern, Deutschland", "093740139139"),
    ("Neustadt an der Waldnaab", "093740139139"),
    ("Neustadt a.d.Waldnaab", "093740139139"),
    ("Kempten (Allgäu)", "097630000000"),
    ("KEMPTEN ALLGÄU", "097630000000"),
    ("Kempten", "097630000000"),
    ("Bad Reichenhall", "091720114114"),
    ("Bad-Reichenhall", "091720114114"),
    ("BAD REICHENHALL", "091720114114"),
    ("Garmisch Partenkirchen", "091800117117"),
    ("Garmisch-Partenkirchen", "091800117117"),
    ("Neuburg/Donau", "091850149149"),
    ("Neuburg an der Donau", "091850149149"),
    ("Sankt Englmar", "092780184184"),
    ("Weiden in der Oberpfalz", "093630000000"),
    ("Weiden i.d.OPf.", "093630000000"),
    ("Rothenburg ob der Tauber", "095710193193"),
    ("Würzburg", "096630000000"),
    ("Wuerzburg", "096630000000"),
    ("Lindau (Bodensee)", "097760116116"),
    ("Pfaffenhofen an der Ilm", "091860143143"),
])
def test_spelling_variants_end_at_the_same_municipality(raw, ars):
    r = G.resolve(raw)
    assert [m.ars for m in r.munis] == [ars], (raw, r)


def test_a_bare_name_with_one_bavarian_candidate_is_read_as_the_bavarian_one_and_says_so():
    # Weiden exists in Rheinland-Pfalz too (071345005091). The corpus is the Bavarian registry, so the rule is named, never silent.
    r = G.resolve("Weiden")
    assert [m.ars for m in r.munis] == ["093630000000"] and r.rule == "bavaria_prior"


def test_a_bare_name_with_several_bavarian_candidates_stays_ambiguous_and_the_plz_picks_one():
    r = G.resolve("Neustadt")
    assert r.status == "ambiguous" and len(r.munis) > 2 and {m.land for m in r.munis} == {"BY"}
    # 91413 is the seat PLZ of Neustadt a.d.Aisch
    assert [m.ars for m in G.resolve("Neustadt", plz="91413").munis] == ["095750153153"]


@pytest.mark.parametrize("raw,land", [
    ("Oberursel (Taunus)", "HE"),
    ("Neustadt an der Weinstraße", "RP"),
    ("Frankfurt am Main", "HE"),
    ("Klinik Hohe Mark Oberursel", "HE"),
])
def test_a_place_outside_bavaria_is_named_as_such_never_forced_into_a_bavarian_town(raw, land):
    r = G.resolve(raw)
    assert r.status == "outside_bavaria" and {m.land for m in r.munis} == {land}, (raw, r)


@pytest.mark.parametrize("raw", ["Deutschland", "bundesweit", "Deutschlandweit", "", None, "-"])
def test_a_value_that_is_no_place_is_not_resolved(raw):
    r = G.resolve(raw)
    assert r.status == "non_place" and r.munis == ()


def test_several_places_in_one_string_stay_several():
    r = G.resolve("Günzburg, Krumbach")
    assert r.status == "multi" and sorted(m.name for m in r.munis) == ["Günzburg, GKSt", "Krumbach (Schwaben), St"]


def test_a_string_that_names_nothing_is_unresolved_not_guessed():
    assert G.resolve("Im Kamp 6/1230").status == "unresolved"


def test_a_plz_is_valid_only_with_five_digits_and_known_to_the_table():
    assert P.clean_plz("86156") == "86156"
    assert P.clean_plz(" 83093 ") == "83093"
    assert P.clean_plz("-") is None and P.clean_plz("0") is None and P.clean_plz("86") is None
    assert P.clean_plz("01844, 01844") is None          # two values in one field is a list, not a PLZ
    assert P.clean_plz(["97318"]) == "97318"            # the one-item list some JSON-LD ships
    assert G.plz_info("83435").kreise == {"09172"}      # Bad Reichenhall: Landkreis Berchtesgadener Land


# ---- D. weights are named, pinned and carry a reason ------------------------------------------------------------------

def test_the_weights_are_pinned_and_each_has_a_one_line_reason():
    assert P.POSTING_WEIGHT == {
        "jsonld_address": 0.90, "structured_plz_city": 0.85, "structured_city": 0.65, "text_einsatzort": 0.60,
        "text_plz_ort": 0.50, "title_or_url_city": 0.35, "text_mention": 0.20, "seed_stamp": 0.10, "board_stamp": 0.05}
    assert P.CLINIC_WEIGHT == {
        "imprint_plz": 0.95, "rhv_id": 0.95, "khv_domain": 0.90, "dk_source": 0.90, "khv_only_site": 0.85, "khv_one_plz": 0.70,
        "klinikradar": 0.60, "khv_name_overlap": 0.60, "posting_modal": 0.50, "registry_town": 0.80, "registry_landkreis": 0.90,
        "khv_other_site": 0.40}
    assert P.LEVEL_FACTOR == {"plz": 1.0, "municipality": 0.8, "kreis": 0.35, "none": 0.0}
    assert set(P.POSTING_WHY) == set(P.POSTING_WEIGHT) and set(P.CLINIC_WHY) == set(P.CLINIC_WEIGHT)
    assert all(len(w) > 10 and "\n" not in w for w in (*P.POSTING_WHY.values(), *P.CLINIC_WHY.values()))
    assert P.STAMP_KINDS == {"seed_stamp", "board_stamp"} and P.CONFIRM_ONLY == {"text_plz_ort", "text_mention"}
    assert P.RULE_FACTOR == {"exact": 1.0, "ascii_fold": 0.95, "qualifier": 0.95, "stem": 0.90, "bavaria_prior": 0.90, "district_of": 0.85,
                             "geonames_place": 0.80, "head_token": 0.60, "tail_token": 0.60, "multi": 0.50, "plz": 1.0}


# ---- D. the board stamp: a value that repeats on ONE board whatever each posting's own place is -----------------------------

def _post(plz, city, *own):
    return {"plz": plz, "city": city, "own": [{"city": c, "plz": p} for c, p in own]}


def test_kbo_de_munich_head_office_on_every_posting_is_a_board_stamp(GG):
    # TASK-68, redacted slice of the kbo.de group portal: JSON-LD says 80538 Muenchen on every posting; the page text names the real site.
    board = [
        _post("80538", "München", ("Haar", "85540")),
        _post("80538", "München", ("Ingolstadt", "85049")),
        _post("80538", "München", ("Taufkirchen", "84416")),
        _post("80538", "München", ("Garmisch-Partenkirchen", None)),
        _post("80538", "München", ("München", "81377")),
        _post("80538", "München", ("Hausham", "83734")),
    ]
    assert P.board_stamps(GG, board) == {"80538": "stamp"}


def test_a_one_site_board_that_repeats_its_own_plz_is_not_a_stamp(GG):
    board = [_post("94234", "Viechtach", ("Viechtach", None)), _post("94234", "Viechtach"), _post("94234", "Viechtach", ("Viechtach", "94234"))]
    assert P.board_stamps(GG, board) == {}


def test_one_value_on_a_board_of_several_municipalities_with_no_other_signal_is_only_suspect(GG):
    board = [_post("80538", "München"), _post("80538", "München"), _post("80538", "München")]
    assert P.board_stamps(GG, board, n_board_municipalities=3) == {"80538": "suspect"}
    assert P.board_stamps(GG, board, n_board_municipalities=1) == {}


def test_a_value_on_a_single_posting_is_never_a_board_value(GG):
    assert P.board_stamps(GG, [_post("80538", "München", ("Haar", "85540"))]) == {}


# ---- E. the confidence of a (posting, clinic) pair ---------------------------------------------------------------------------

def test_same_plz_on_both_sides_is_the_top_level_and_multiplies_the_two_weights(GG):
    posting = [P.Claim("structured_plz_city", "83435", "Bad Reichenhall")]
    clinic = [P.Claim("imprint_plz", "83435", None)]
    m = P.match(GG, posting, clinic)
    assert (m.category, m.level, round(m.confidence, 4)) == ("agree", "plz", round(0.85 * 0.95, 4))


def test_two_plz_of_one_city_are_the_municipality_level_not_a_disagreement(GG):
    # 76114 Augsburg: the imprint says 86156, four of five postings say 86159, both are Augsburg.
    m = P.match(GG, [P.Claim("structured_plz_city", "86159", "Augsburg")], [P.Claim("imprint_plz", "86156", None)])
    assert (m.category, m.level) == ("agree", "municipality")


def test_a_job_in_another_kreis_is_a_disagreement_with_the_clinic_it_is_linked_to(GG):
    # 46110: jobs in Rehau (Landkreis Hof) linked to a Bamberg day clinic
    m = P.match(GG, [P.Claim("structured_city", None, "Rehau")], [P.Claim("imprint_plz", "96049", None), P.Claim("registry_town", None, "Bamberg")])
    assert (m.category, m.level, m.confidence) == ("disagree", "none", 0.0)


def test_the_kreis_alone_is_a_weak_agreement(GG):
    m = P.match(GG, [P.Claim("structured_city", None, "Ebensfeld")], [P.Claim("registry_town", None, "Bad Staffelstein")])
    assert (m.category, m.level) == ("agree_weak", "kreis")


def test_a_posting_whose_only_place_is_a_stamp_is_unknown_never_agree_or_disagree(GG):
    m = P.match(GG, [P.Claim("seed_stamp", None, "Bamberg")], [P.Claim("registry_town", None, "Bamberg")])
    assert m.category == "unknown"
    assert P.match(GG, [], [P.Claim("registry_town", None, "Bamberg")]).category == "unknown"
    assert P.match(GG, [P.Claim("structured_city", None, "Bamberg")], []).category == "unknown"


def test_two_equally_strong_readings_that_disagree_halve_the_confidence_and_say_so(GG):
    # Sozialstiftung Bamberg board: the stored city says Bamberg, the adapter replayed from the mirror says Forchheim
    one = P.match(GG, [P.Claim("structured_city", None, "Bamberg")], [P.Claim("registry_town", None, "Bamberg")])
    two = P.match(GG, [P.Claim("structured_city", None, "Bamberg"), P.Claim("structured_city", None, "Forchheim")], [P.Claim("registry_town", None, "Bamberg")])
    assert (one.category, one.conflict) == ("agree", False)
    assert (two.category, two.level, two.conflict) == ("agree", "municipality", True)
    assert two.confidence == pytest.approx(one.confidence / 2)


def test_a_typo_plz_is_no_evidence_and_the_city_decides(GG):
    # 17205: postings carry 83453 (a typo, there is no such PLZ in the table) next to the city Bad Reichenhall
    m = P.match(GG, [P.Claim("structured_plz_city", "83453", "Bad Reichenhall")], [P.Claim("imprint_plz", "83435", None)])
    assert (m.category, m.level) == ("agree", "municipality")


def test_an_ambiguous_city_name_is_discounted_by_its_number_of_candidates(GG):
    one = P.match(GG, [P.Claim("structured_city", None, "Rehau")], [P.Claim("registry_town", None, "Rehau")])
    many = P.match(GG, [P.Claim("structured_city", None, "Neustadt")], [P.Claim("registry_town", None, "Neustadt a.d.Aisch")])
    assert one.confidence == pytest.approx(0.65 * 0.8 * 0.8)
    assert many.level == "municipality" and many.confidence < one.confidence / 5


def test_an_unlabelled_address_in_the_text_never_decides_against_a_place(GG):
    # LMU postings: the seed town stamp plus a '72581 Bewerbungsformat' picked off the page. The PLZ is real, the place is not the job's.
    m = P.match(GG, [P.Claim("seed_stamp", None, "München"), P.Claim("text_plz_ort", "72581", "Bewerbungsformat Bitte")],
                [P.Claim("imprint_plz", "81377", None), P.Claim("registry_town", None, "München")])
    assert (m.category, m.level) == ("unknown", "unknown")


def test_an_unlabelled_address_that_agrees_is_only_weak_support(GG):
    m = P.match(GG, [P.Claim("text_plz_ort", "86156", "Augsburg")], [P.Claim("imprint_plz", "86156", None)])
    assert (m.category, m.level, round(m.confidence, 4)) == ("agree_weak", "plz", round(0.50 * 0.95, 4))


def test_a_labelled_workplace_in_another_land_decides_against_the_seed_clinic(GG):
    # Aschaffenburg board, R0_board: 'Einsatzort: Seligenstadt' (Hessen) under the seed-town stamp
    m = P.match(GG, [P.Claim("seed_stamp", None, "Aschaffenburg"), P.Claim("text_einsatzort", None, "Seligenstadt")],
                [P.Claim("registry_town", None, "Aschaffenburg")])
    assert (m.category, m.level, m.confidence) == ("disagree", "none", 0.0)


def test_one_posting_naming_another_place_does_not_make_the_board_value_a_stamp(GG):
    # Sozialstiftung Bamberg board: 29 postings in Bamberg, one of them names a place in another Kreis in its text
    board = [_post("96049", "Bamberg", ("Bamberg", None))] * 4 + [_post("96049", "Bamberg", ("Rehau", "95111"))]
    assert P.board_stamps(GG, board) == {}
    assert P.board_stamps(GG, board[:2] + [_post("96049", "Bamberg", ("Rehau", "95111"))] * 3) == {"96049": "stamp"}


def test_a_place_read_by_a_token_scan_does_not_make_a_board_value_a_stamp(GG):
    # 'Standort Landsberg' reads as a place only by its last token (and 'Landsberg' alone is ambiguous): no evidence against 86899
    board = [_post("86899", "Landsberg am Lech", ("Standort Landsberg", None)), _post("86899", "Landsberg am Lech", ("Standort Landsberg", None))]
    assert P.board_stamps(GG, board) == {}
