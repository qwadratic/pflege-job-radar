"""Tests for pflege_jobs.geo -- the municipality/PLZ -> Bundesland labeller that replaces
career_crawl.in_bavaria's polluted-towns-set + overreaching regex guess. See pflege_jobs/geo.py's
module docstring for the two bugs being fixed and the numbers behind every claim below.

Style matches tests/test_mech_bavaria_filter.py and tests/test_sinks.py: plain pytest, no fixtures
unless needed, a comment above any test that encodes a real incident.
"""
import csv

import pytest
import requests

from pflege_jobs import geo
from pflege_jobs.geo import resolve, UNKNOWN, LAND_NAMES
from pflege_jobs.sources.career_crawl import in_bavaria


# ==================== 1. the five polluted-towns-set false positives (career_crawl bug #2) =========

def test_neustadt_an_der_weinstrasse_is_rheinland_pfalz_not_bavaria():
    # Today in_bavaria('Neustadt an der Weinstrasse', ...) returns True because "neustadt" sits in
    # the PDF-parse-polluted towns set (cli.py:370). The real Neustadt an der Weinstrasse is RP.
    assert resolve(city="Neustadt an der Weinstrasse").land == "RP"


def test_landau_in_der_pfalz_is_rheinland_pfalz_not_bavaria():
    assert resolve(city="Landau in der Pfalz").land == "RP"


def test_friedberg_hessen_is_hessen_not_bavaria():
    assert resolve(city="Friedberg (Hessen)").land == "HE"


def test_weilheim_an_der_teck_is_baden_wuerttemberg_not_bavaria():
    assert resolve(city="Weilheim an der Teck").land == "BW"


def test_hof_westfalen_is_not_bavaria():
    # Verified against both sources geo.py is built from before trusting a specific Land here:
    # GeoNames DE.txt has exactly two "Hof" rows (BY 95028/95030/95032, RP 56472) and Wikipedia's
    # "Liste der Orte namens Hof" lists NRW's only two Hof-named places as Ortsteile of Hennef and
    # Wipperfuerth (both Rhineland, not Westfalen) -- neither source has a resolvable NW "Hof" at
    # the Gemeinde/PLZ granularity this module operates at, and normalize_name() drops the ", ..."
    # tail (same as Destatis' own ", Stadt" convention) so "Westfalen" never reaches the lookup as a
    # region signal either. "Hof" alone is name_ambiguous BY|RP, so UNKNOWN is the correct, honest
    # answer -- same acceptable-UNKNOWN bar as Klinik Hohe Mark Oberursel below. What must not
    # regress is the old detector's false "True".
    assert resolve(city="Hof, Westfalen").land != "BY"


def test_klinik_hohe_mark_oberursel_is_not_bavaria():
    # Matches today only because "klinik" itself is in the polluted towns set. The real town
    # (Oberursel) is Hessen; UNKNOWN is fine here, a wrong Land is not.
    assert resolve(city="Klinik Hohe Mark Oberursel").land != "BY"


# ==================== 2. BAV_PLZ regex overreach clusters (career_crawl bug #1) =====================

NON_BAVARIAN_OVERREACH_PLZ = (
    # 89xxx cluster -> Baden-Wuerttemberg (Heidenheim an der Brenz and neighbours; Klinikum
    # Heidenheim is a real hospital in this range, so this is not a hypothetical false positive).
    "89516 89517 89518 89520 89522 89537 89542 89547 89551 89555 89558 89561 89564 89567 89568 "
    "89582 89584 89597 "
    # 97xxx cluster -> Baden-Wuerttemberg (Wertheim/Bad Mergentheim and neighbours; Bad Mergentheim
    # is a real clinic town).
    "97877 97896 97900 97922 97941 97944 97947 97950 97953 97956 97957 97959 97980 97990 97993 "
    "97996 97999 "
    # 965xx cluster -> Thueringen (Sonneberg and neighbours).
    "96515 96523 96524 96528 "
    # single outlier -> Baden-Wuerttemberg (Achberg).
    "88147"
).split()


@pytest.mark.parametrize("plz", NON_BAVARIAN_OVERREACH_PLZ)
def test_bav_plz_overreach_cluster_plz_are_not_bavaria(plz):
    assert resolve(plz=plz).land != "BY"


def test_jungholz_87491_is_austrian_not_bavaria():
    # 87491 is a German PLZ assigned to Jungholz, an Austrian Tyrolean exclave reachable by road only
    # through Bavaria -- BAV_PLZ's regex swallows it as a false "True" today. geo.py carries a
    # dedicated rare-case entry for it precisely so the refusal is auditable, not a bare "not found".
    g = resolve(plz="87491")
    assert g.land != "BY"
    assert g.rule == "rare_case:jungholz_austrian_exclave"


# ==================== 3. genuinely Bavarian cases -- must NOT regress =============================

BAVARIAN_PLZ = {
    "89231": "Neu-Ulm", "88131": "Lindau", "63739": "Aschaffenburg", "97080": "Wuerzburg",
    "97318": "Kitzingen", "94405": "Landau a.d.Isar", "86316": "Friedberg", "82362": "Weilheim i.OB",
    "94327": "Bogen", "82335": "Berg (Kreis Starnberg)", "95180": "Berg (Kreis Hof)",
}


@pytest.mark.parametrize("plz,town", BAVARIAN_PLZ.items(), ids=list(BAVARIAN_PLZ))
def test_genuinely_bavarian_plz_still_resolve_to_bavaria(plz, town):
    g = resolve(plz=plz)
    assert g.land == "BY", f"{plz} ({town}) should be BY, got {g}"


# ==================== 4. ambiguity refusal -- must not guess ========================================

def test_bare_neustadt_refuses_and_names_all_eight_laender():
    # Neustadt exists in 8 Bundeslaender (measured). Bare, unqualified "Neustadt" carries no PLZ or
    # region signal, so resolve() must refuse rather than guess -- this is exactly the branch that
    # replaces today's polluted first-token match.
    g = resolve(city="Neustadt")
    assert g.land == UNKNOWN
    assert "ambiguous" in g.rule
    for code in ("BB", "BY", "HE", "NI", "RP", "SH", "SN", "TH"):
        assert code in g.rule


@pytest.mark.parametrize("name,lands", [
    ("Berg", {"BW", "BY", "RP"}),
    ("Bergen", {"BY", "NI", "RP", "SN"}),
    ("Neunkirchen", {"BW", "BY", "NW", "RP", "SL"}),
    ("Reichenbach", {"BY", "RP", "TH"}),
])
def test_bare_ambiguous_stems_refuse_and_name_the_laender(name, lands):
    g = resolve(city=name)
    assert g.land == UNKNOWN
    assert "ambiguous" in g.rule
    for code in lands:
        assert code in g.rule


def test_bare_sulzbach_refuses_after_the_weiden_precedence_fix():
    # Superseded finding: this test used to assert bare "Sulzbach" resolves confidently to RP,
    # reasoning that RP's own two municipalities are officially named exactly "Sulzbach" with no
    # qualifier, while every OTHER Land's Sulzbach carries a qualifier (e.g. "Sulzbach (Taunus)" in
    # Hessen), so name_exact (checked before the stem branch) never collided.
    #
    # That reasoning is exactly the shape of a real wrong-Land bug found live in the same precedence
    # (see resolve()'s "Weiden" comment): "Weiden" is RP's own bare, unqualified name too, yet the
    # honest answer is UNKNOWN because Bavaria's "Weiden i.d.OPf." shares the same stem once its
    # qualifier is stripped -- resolve() now refuses whenever the QUERY itself carries no qualifier
    # and that bare stem is ambiguous, which is exactly bare "Sulzbach"'s shape (Destatis has
    # qualified "Sulzbach"-stem municipalities in BW/BY/HE/SL too). Sulzbach is also one of the task
    # brief's own named 5-Land ambiguous stems, so refusing here matches the brief's framing of it as
    # ambiguous, not the previous test's narrower "RP happens to be unqualified" argument. Demoting
    # this one case from confident-RP to safe-UNKNOWN is the accepted cost of fixing the Weiden bug
    # via the same general mechanism -- see the "confirmed fixes"/"trade-off" notes for the sibling
    # cases below.
    g = resolve(city="Sulzbach")
    assert g.land == UNKNOWN
    assert g.rule == "name_stem_ambiguous:sulzbach:BW|BY|HE|RP|SL"


# ==================== 5. region-string precedence ====================================================

@pytest.mark.parametrize("region", ["Bayern", "BY", "by", "Bavaria", "Freistaat Bayern"])
def test_region_string_variants_all_resolve_bavaria(region):
    g = resolve(region=region)
    assert g.land == "BY"
    assert g.rule.startswith("region_exact:BY")


def test_region_beats_a_wrong_city():
    # Region is checked before city/PLZ (near-total ground truth when it decodes) -- a contradicting
    # city string must not override it.
    g = resolve(city="Frankfurt", region="Bayern")
    assert g.land == "BY"
    g2 = resolve(city="Tutzing", region="NW")
    assert g2.land == "NW"


# ==================== 6. malformed-input defenses ====================================================

def test_one_item_lists_instead_of_scalars_do_not_crash():
    # Live 2026-09-09, inbox_id 13576: a malformed upstream row carried {"plz": ["97318"],
    # "city": ["Kitzingen"]} -- the same incident test_sinks.py and test_mech_bavaria_filter.py cover
    # for in_bavaria() must also not crash resolve().
    g = resolve(city=["Kitzingen"], plz=["97318"])
    assert g.land == "BY"
    assert resolve(city=["Berlin"], plz=None, region=["NW"]).land == "NW"
    assert resolve(city=[], plz=[], region=[]).land == UNKNOWN


def test_int_plz_does_not_crash():
    assert resolve(plz=97318).land == "BY"


@pytest.mark.parametrize("val", ["", "   ", None])
def test_empty_and_whitespace_inputs_give_no_signal(val):
    g = resolve(city=val, plz=val, region=val)
    assert g.land == UNKNOWN
    assert g.rule == "no_signal"


def test_four_digit_plz_does_not_crash_and_does_not_falsely_match():
    # A 4-digit string is not a valid German PLZ. _clean_plz zero-pads it to 5 digits, which must
    # not accidentally collide with a real municipality's PLZ.
    g = resolve(plz="9738")
    assert g.land != "BY"


def test_four_digit_plz_zero_pad_recovers_a_real_dropped_leading_zero():
    # The zero-pad in _clean_plz exists for a real, legitimate case: a leading "0" silently dropped
    # by an upstream export (e.g. "1067" for Dresden's real PLZ "01067"), not just "must not crash".
    g = resolve(plz="1067")
    assert g.land == "SN"
    assert g.rule == "plz_exact:01067"


def test_three_digit_plz_is_rejected_not_zero_padded():
    # Regression (LENS regression, severity LOW): _clean_plz used to zero-pad ANY string of length
    # <= 5, so a truncated 3-digit PLZ (seen live: "603" for a Frankfurt am Main posting, likely an
    # upstream string-slicing bug) silently became a fake, plausible-looking "00603" instead of being
    # rejected. A 3-digit string is not a dropped-leading-zero case (that's only ever a single zero,
    # i.e. exactly 4 digits) -- it is truncated/malformed input, and must carry NO PLZ signal, not a
    # manufactured one. Paired with a real city so the case exercises resolve() end-to-end, not just
    # _clean_plz in isolation.
    g = resolve(city="Frankfurt am Main", plz="603")
    assert g.land == "HE"  # via city name_exact, PLZ correctly contributed nothing
    assert g.rule == "name_exact:frankfurt am main"
    assert resolve(plz="603").land == UNKNOWN
    assert resolve(plz="603").rule == "no_signal"  # no usable signal at all, not "not_found"


# ==================== 6b. name_exact vs ambiguous-bare-stem precedence (LENS wrong-label BLOCKER) ===

# Root cause (see resolve()'s inline comment for the full incident): name_exact used to return the
# instant it found a unique-Land hit, even when the query's OWN bare name (no qualifier at all) also
# doubles as another Land's stem once THEIR qualifier is stripped. Fixed by refusing whenever the
# query itself carries no qualifier (name == bare_stem(name)) and that bare stem is ambiguous.
# Confirmed live against data/geo/gemeinden_de.csv for every case below before writing the assertion.

@pytest.mark.parametrize("city,expected_rule_prefix", [
    ("Weiden", "name_stem_ambiguous:weiden:"),                    # RP's bare name vs BY's "Weiden i.d.OPf."
    ("Weiden, Bayern, Deutschland", "name_stem_ambiguous:weiden:"),  # comma-cut drops "Bayern" too -- same bug
    ("Heidenheim", "name_stem_ambiguous:heidenheim:"),            # BY's bare name vs BW's "Heidenheim an der Brenz"
    ("Rothenburg", "stem_ambiguous:rothenburg:"),                 # SN's bare stem vs BY's "Rothenburg ob der Tauber"
])
def test_name_exact_no_longer_confidently_wrong_on_ambiguous_bare_names(city, expected_rule_prefix):
    g = resolve(city=city)
    assert g.land == UNKNOWN, f"{city!r} must refuse, not guess: got {g}"
    assert g.rule.startswith(expected_rule_prefix), g.rule
    assert "BY" in g.rule  # Bavaria is always one of the genuine candidates in these cases


@pytest.mark.parametrize("city,expected_land", [
    ("Weiden i.d.OPf.", "BY"),           # the explicit qualifier IS real disambiguating evidence
    ("Heidenheim an der Brenz", "BW"),
    ("Rothenburg ob der Tauber", "BY"),  # QUALIFIER_RE gained "ob" for exactly this case
    ("Rothenburg/O.L.", "SN"),
])
def test_name_exact_still_trusts_a_query_that_supplies_its_own_qualifier(city, expected_land):
    # The fix must not become a blanket "any ambiguous stem is UNKNOWN" -- when the CALLER already
    # gave the disambiguating qualifier, that is real evidence and resolve() must use it, not discard
    # it. This is also what keeps "Frankfurt am Main" (below) from wrongly regressing into UNKNOWN.
    g = resolve(city=city)
    assert g.land == expected_land, f"{city!r} should resolve {expected_land}, got {g}"
    assert g.rule.startswith("name_exact:")


def test_frankfurt_am_main_does_not_regress_into_ambiguous_with_frankfurt_oder():
    # Guard for the fix above over-firing: stripping "am Main" off "Frankfurt am Main" collides with
    # Frankfurt (Oder)'s bare stem "frankfurt" -- but the query itself already supplied the
    # disambiguating qualifier, so this must stay a confident, correct HE, not become UNKNOWN.
    g = resolve(city="Frankfurt am Main")
    assert g.land == "HE"
    assert g.rule == "name_exact:frankfurt am main"
    # Bare "Frankfurt" (no qualifier at all) is genuinely ambiguous and correctly refuses.
    g2 = resolve(city="Frankfurt")
    assert g2.land == UNKNOWN
    assert "BB" in g2.rule and "HE" in g2.rule


# ==================== 6c. real-world recall trade-offs (LENS regression) -- documented, not "fixed" ==
#
# These are cases a lens's live-data regression run found where the OLD in_bavaria() detector
# happened to guess correctly (often by luck, from real-world knowledge no dataset row encodes) and
# the NEW resolve() safely refuses instead of guessing, because the qualifying detail a human would
# use ("am Chiemsee" names an exclusively-Bavarian lake, "Oberfranken" is a Bavaria-only
# Regierungsbezirk, "München Sued" is an informal district suffix) is either stripped by
# normalize_name/bare_stem as if it were disambiguating noise, or was never a matchable dataset row
# in the first place. This is the accepted cost of never guessing -- adding a synonym/gazetteer table
# to recover them would reintroduce the exact kind of "guess from vocabulary" mechanism this module
# replaces, and is out of scope here (deliberately not fixed, not a false alarm: these ARE real
# Bavarian postings the site would lose if UNKNOWN were ever wired to mean "exclude").

@pytest.mark.parametrize("city", [
    "Bernau am Chiemsee",       # lake is exclusively Bavarian, but "am Chiemsee" is stripped as a qualifier
    "Forchheim, Oberfranken",   # comma-cut drops the Bavaria-only Regierungsbezirk name
    "Lauf an der Pegnitz",      # Pegnitz river is Bavarian; qualifier stripped, stem ambiguous BW|BY
    "Neuburg/Donau",            # "/..." stripped; stem ambiguous BY|MV|RP
    "München Flughafen",        # informal site-suffix, no matching dataset row at all
    "München Süd",
    "München Mitte",
])
def test_real_bavaria_postings_safely_refuse_rather_than_guess(city):
    g = resolve(city=city)
    assert g.land != "BY"  # must not be wrong either -- these correctly land on UNKNOWN, not another Land
    assert g.land == UNKNOWN


# ==================== 7. umlaut / eszett folding =====================================================

def test_nuernberg_ascii_and_umlaut_spellings_agree():
    assert resolve(city="Nürnberg").land == "BY"
    assert resolve(city="Nuernberg").land == "BY"
    # "Nurnberg" (umlaut dropped outright, not transliterated) is not how normalize_name folds --
    # it must not silently match; UNKNOWN/not-Bavaria is the safe answer, not a guess.
    assert resolve(city="Nurnberg").land != "BY"


def test_weissenburg_ascii_and_eszett_spellings_agree():
    assert resolve(city="Weissenburg").land == "BY"
    assert resolve(city="Weißenburg").land == "BY"


# ==================== 7b. dataset hygiene (LENS data findings) =======================================

def test_grossempfaenger_named_bamberg_examples_still_absent():
    # The task brief's 4 named Grossempfaenger PLZ (all really Bamberg, Bayern, tagged wrong Land by
    # raw GeoNames) must stay absent from the merged CSV -- and Bamberg's own real PLZ must resolve
    # correctly, proving the filter isn't just dropping Bamberg outright.
    for plz in ("96039", "96076", "96035", "96063"):
        g = resolve(plz=plz)
        assert g.land != "NW" and g.land != "BE" and g.land != "HE", f"{plz}: {g}"
    assert resolve(plz="96047").land == "BY"  # Bamberg's real Destatis PLZ


def test_grossempfaenger_suffix_compound_institutional_names_are_dropped():
    # LENS-data HIGH: the original GROSSEMPFAENGER_RE required a word boundary BEFORE the
    # institutional noun too, so it missed German's habit of compounding onto a prefix with no space
    # ("Finanzamt", "Landesbank", "Stadtwerke", "Kreissparkasse"). Fixed for suffix compounds (a
    # second, right-anchored-only alternative). Spot-check a few of the lens's own named examples.
    with open("data/geo/gemeinden_de.csv", newline="", encoding="utf-8") as f:
        names = {row["gemeindename"] for row in csv.DictReader(f) if row["source"] == "geonames"}
    for junk in ("BayernLB Bayerische Landesbank", "Landesamt für Finanzen Dienststelle Regensburg"):
        assert junk not in names, f"{junk!r} should have been dropped as Grossempfaenger-like"


def test_no_double_utf8_mojibake_survives_for_a_real_land_answer():
    # LENS-data MEDIUM: a handful of GeoNames rows are double-UTF-8-encoded upstream (e.g. "KÃ¶ln"
    # for "Köln") -- confirmed present byte-identical in the raw source, not introduced by this
    # builder. Not fully fixable without correcting upstream data, so this test only pins down what
    # actually matters: no mojibake row's `land` value is wrong, and the count of surviving mojibake
    # rows doesn't silently grow (all residual cases are institutional junk anyway, not real
    # municipality answers resolve() would ever need for a Land verdict).
    with open("data/geo/gemeinden_de.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    mojibake_rows = [r for r in rows if "Ã" in r["gemeindename"]]
    assert len(mojibake_rows) <= 3, f"mojibake rows grew unexpectedly: {mojibake_rows}"
    for r in mojibake_rows:
        assert r["land"] in LAND_NAMES  # still a real, plausible Land code, not garbage


# ==================== 8. whole-dataset property: PLZ must resolve to its own row's Land =============

def test_every_csv_row_plz_resolves_to_its_own_land():
    # The check that catches a builder bug: for every row in data/geo/gemeinden_de.csv, looking that
    # row's own PLZ up through resolve() must return that row's own Land -- except for the six PLZ
    # that Destatis itself splits across two Laender (plz_ambiguous), where resolve() must instead
    # name that row's Land among the candidates rather than silently picking the other one.
    # Runs over EVERY row (13762), not a sample.
    with open("data/geo/gemeinden_de.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 13000  # sanity: didn't accidentally load an empty/truncated file
    checked = 0
    for row in rows:
        plz, land = row["plz"], row["land"]
        assert land in LAND_NAMES, f"unknown Land code {land!r} in row {row}"
        g = resolve(plz=plz)
        if g.land == UNKNOWN and g.rule.startswith("plz_ambiguous:"):
            assert land in g.rule.split(":", 2)[2].split("|"), f"{plz}: {land} missing from {g.rule}"
        else:
            assert g.land == land, f"plz {plz}: row says {land}, resolve() says {g.land} ({g.rule})"
        checked += 1
    assert checked == len(rows)


# ==================== 9. live-data regression against career_crawl.in_bavaria =======================

def _load_towns():
    # Same construction as pflege_jobs/cli.py:370, so the OLD detector is exercised exactly as it
    # runs in production (polluted towns set and all) -- reproducing the second half of the bug this
    # module fixes, not a cleaned-up strawman.
    from pflege_jobs.classify import norm_text
    with open("data/registry/clinics.csv", newline="", encoding="utf-8") as f:
        return {norm_text(c["town"]) for c in csv.DictReader(f) if c.get("town")}


def _fetch_live_city_plz_pairs():
    url = "https://supabase.int.exe.xyz/rest/v1/posting_observations"
    headers = {"Accept-Profile": "pflege_jobs"}
    rows, offset = [], 0
    while True:
        r = requests.get(url, headers=headers, params={"select": "city,plz", "limit": 1000, "offset": offset}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        offset += 1000
        if len(batch) < 1000:
            break
    return rows


def _own_land_for_plz(csv_plz_to_lands, plz):
    """Independent ground truth for a PLZ: what data/geo/gemeinden_de.csv itself says, unambiguously.
    Returns None (no independent verdict) if the PLZ is absent or straddles >1 Land."""
    if not plz:
        return None
    lands = csv_plz_to_lands.get(plz)
    if not lands or len(lands) != 1:
        return None
    return next(iter(lands))


@pytest.mark.network
def test_live_posting_observations_no_regression_vs_old_detector():
    """Pull every (city, plz) pair the live pipeline has actually seen, run OLD in_bavaria and NEW
    resolve() over each, print the full disagreement table, and assert only the one thing that
    matters for a safe rollout: no row the OLD detector got demonstrably RIGHT (city/plz whose
    Destatis PLZ is unambiguously Bavaria) becomes non-Bavarian under the NEW labeller. Disagreements
    in the other direction (OLD wrongly said True, or OLD had no opinion) are exactly what this
    module is FOR -- they are printed, not asserted against.

    Needs network (keyless Supabase read proxy); deselect offline with `-m "not network"`.
    """
    try:
        rows = _fetch_live_city_plz_pairs()
    except requests.exceptions.RequestException as e:
        pytest.skip(f"no network access to the Supabase read proxy: {e}")

    with open("data/geo/gemeinden_de.csv", newline="", encoding="utf-8") as f:
        csv_plz_to_lands = {}
        for r in csv.DictReader(f):
            csv_plz_to_lands.setdefault(r["plz"], set()).add(r["land"])

    towns = _load_towns()
    pairs = {(r.get("city"), r.get("plz")) for r in rows}

    disagreements = []
    regressions = []
    for city, plz in sorted(pairs, key=lambda p: (p[0] or "", p[1] or "")):
        old = in_bavaria(city, plz, None, towns)
        new_geo = resolve(city=city, plz=plz)
        new = True if new_geo.land == "BY" else (False if new_geo.land != UNKNOWN else None)
        if old != new:
            disagreements.append((city, plz, old, new, new_geo.rule))
        own_land = _own_land_for_plz(csv_plz_to_lands, plz)
        if own_land == "BY" and old is True and new is not True:
            regressions.append((city, plz, old, new, new_geo.rule))

    print(f"\n{len(pairs)} distinct (city, plz) live pairs; {len(disagreements)} old/new disagreements:")
    print(f"{'city':40} {'plz':6} {'old':6} {'new':6} rule")
    for city, plz, old, new, rule in disagreements:
        print(f"{(city or ''):40.40} {(plz or ''):6} {str(old):6} {str(new):6} {rule}")

    assert not regressions, f"OLD detector was RIGHT (Bavaria by PLZ) but NEW disagrees: {regressions}"
