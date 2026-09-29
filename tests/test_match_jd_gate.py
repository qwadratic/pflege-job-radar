"""TASK-101: Matcher._match_jd (rule R_jd_text) never ran in production -- pflege_jobs/cli.py never
passed description= to Matcher.match(). Wiring it in naively reintroduces the single-generic-token
false-positive class TASK-51/decision-5 already fixed for R3/R4: a clinic whose name/operator reduces
to ONE token after town-stripping (e.g. 'Artemed Fachklinik München' -> {'artemed'}, 'Augenklinik
Rosenheim' -> {'augenklinik'}) is not a unique registry-wide signal, it's a generic word many
descriptions mention in passing. _match_jd runs registry-wide (unlike R3/R4's same-town scoping), so
the risk is worse, not equal -- confirmed live 2026-09-22 replaying description= into production data:
5 currently-correct clinic_id matches would have flipped to a wrong site purely on a one-word
coincidence."""
from pflege_jobs.registry import Matcher

# town="München" strips "münchen"/"münchener" tokens; "Fachklinik" is itself a STOP word --
# the whole name collapses to the single generic token {"artemed"}.
ARTEMED_MUENCHEN = {"clinic_id": "16235", "name": "Artemed Fachklinik München", "operator": "Artemed SE", "town": "München"}
# board-matched Artemed sibling site the posting should actually resolve to (via board fallback,
# not JD text) -- present so a same-registry token collision is exercised, not just a lone clinic.
ARTEMED_TUTZING = {"clinic_id": "18802", "name": "Artemed Fachklinik Tutzing", "operator": "Artemed SE", "town": "Tutzing"}
AUGENKLINIK_ROSENHEIM = {"clinic_id": "16307", "name": "Augenklinik Rosenheim", "operator": "", "town": "Rosenheim"}
BARMHERZIGE_REGENSBURG = {"clinic_id": "36201", "name": "Barmherzige Brüder Regensburg", "operator": "Barmherzige Brüder gGmbH", "town": "Regensburg"}
ST_JOSEF_REGENSBURG = {"clinic_id": "36202", "name": "Krankenhaus St. Josef", "operator": "St. Josef Stiftung", "town": "Regensburg"}
# Real registry shape, clinics.csv line 143/382 (TASK-131): 18813 is the clean row, 18872 is a
# parse_quality='partial' Krankenhausplan-PDF-extraction twin whose town field is garbage ("Co. KG",
# not a real town) -- so 'feldafing' never gets stripped from its operator tokens, and it out-competes
# its own clean twin for real Feldafing postings purely because its bad data dodges the town-strip.
FELDAFING_CLEAN = {"clinic_id": "18813", "name": "Benedictus Krankenhaus Feldafing", "town": "Feldafing",
                    "operator": "Benedictus Krankenhaus Feldafing GmbH & Co. KG", "parse_quality": "ok"}
FELDAFING_CORRUPT_TWIN = {"clinic_id": "18872", "name": "Benedictus Krankenhaus", "town": "Co. KG",
                           "operator": "Feldafing Feldafing Benedictus Krankenhaus Feldafing GmbH &", "parse_quality": "partial"}


def test_a_single_generic_token_name_is_not_unique_evidence_registry_wide():
    """Artemed Fachklinik München's own name/operator both collapse to {'artemed'} -- a description
    that merely mentions the Artemed group in passing (the real TASK-101 Tutzing/Berg repro shape)
    must not JD-match to it."""
    m = Matcher([dict(ARTEMED_MUENCHEN)])
    desc = "Wir sind Teil der Artemed Gruppe und suchen Verstärkung für unser Team am Standort Tutzing."
    assert m.match("", None, description=desc) is None


def test_a_single_generic_token_operator_is_not_unique_evidence_either():
    desc = "Die Augenklinik Bremen sucht examinierte Pflegefachkräfte für den OP-Bereich."
    m = Matcher([dict(AUGENKLINIK_ROSENHEIM)])
    assert m.match("", None, description=desc) is None


def test_a_genuine_two_token_name_still_matches_uniquely():
    """'Barmherzige Brüder Regensburg' reduces to {'barmherzige','brüder'} once the town is stripped --
    two real, distinguishing tokens, not a stray generic word. A description that actually names the
    site should still resolve via R_jd_text, same as before this fix."""
    m = Matcher([dict(BARMHERZIGE_REGENSBURG), dict(ST_JOSEF_REGENSBURG)])
    desc = "Wir gehören zum Krankenhaus Barmherzige Brüder Regensburg und suchen eine Pflegefachkraft (m/w/d)."
    assert m.match("", None, description=desc) == ("36201", "R_jd_text", 0.65)


def test_two_single_token_clinics_both_mentioned_stay_unmatched_even_without_the_gate():
    """Sanity check the pre-existing len(hits)==1 uniqueness rule still holds: even a clinic that DID
    clear the new token-count floor must not win if the description mentions more than one candidate."""
    m = Matcher([dict(BARMHERZIGE_REGENSBURG), dict(ST_JOSEF_REGENSBURG)])
    desc = "Sowohl die Barmherzigen Brüder Regensburg als auch das Krankenhaus St. Josef kooperieren hier."
    assert m.match("", None, description=desc) is None


def test_a_parse_quality_partial_twin_does_not_steal_a_match_from_its_clean_sibling():
    """The real TASK-101/TASK-131 repro: without the parse_quality guard, 18872's corrupted town field
    lets 'feldafing' survive in its operator tokens (2 real tokens, clears the >=2 gate) while the
    CLEAN row 18813 correctly strips 'feldafing' from its own tokens (down to 1, blocked) -- so the
    bad-data row would win registry-wide over the real one. Excluding parse_quality='partial' entirely
    must leave neither candidate eligible, so the caller (board fallback) resolves it instead."""
    m = Matcher([dict(FELDAFING_CLEAN), dict(FELDAFING_CORRUPT_TWIN)])
    desc = ("Die Benedictus Krankenhaus Feldafing GmbH & Co. KG sucht Pflegefachkräfte am Starnberger "
            "See. Wir suchen Sie fuer unser Team in Feldafing.")
    assert m.match("Artemed SE", None, description=desc) is None


def test_board_fallback_still_recovers_the_correct_site_when_jd_text_declines():
    """The real TASK-101 shape end-to-end: employer text is generic/inherited (nothing for
    _match_content to use), description only mentions the parent group in passing (blocked by the new
    gate), but the crawler's own board pool still know which site actually served the page."""
    m = Matcher([dict(ARTEMED_MUENCHEN), dict(ARTEMED_TUTZING)])
    desc = "Wir sind Teil der Artemed Gruppe."
    res = m.match("", "Tutzing", board=["16235", "18802"], description=desc)
    assert res == ("18802", "R0_board_town", 0.85)
