"""TASK-100: Matcher._match_board's R0_board_name/_town/_tokens refused outright on any tie (len(hit)>1),
even when the tied candidates are a real Plan-KH/Vertrags-KH twin pair sharing one operator (confirmed
live 2026-09-22: Schön Klinik Roseneck 18716/18776 in Prien am Chiemsee both satisfy R0_board_town, real
beds 234 vs 179 -- _match_content already resolves this exact shape via _pick_site, decision-4/TASK-58A,
_match_board had no equivalent). The tie-break must stay gated on the tied candidates sharing one
normalized name/operator -- two genuinely different sites in the same town (München Harlaching vs
Schwabing, different operators) must stay unmatched, not get force-picked by bed count."""
from pflege_jobs.registry import Matcher

ROSENECK_A = {"clinic_id": "18716", "name": "Schön Klinik Roseneck", "operator": "Schön Klinik Roseneck SE & Co. KG",
              "town": "Prien am Chiemsee", "beds": 234}
ROSENECK_B = {"clinic_id": "18776", "name": "Schön Klinik Roseneck", "operator": "Schön Klinik Roseneck SE & Co. KG",
              "town": "Prien am Chiemsee", "beds": 179}
HARLACHING = {"clinic_id": "16209", "name": "Schön Klinik München Harlaching",
              "operator": "Schön Klinik München Harlaching SE & Co. KG", "town": "München", "beds": 148}
SCHWABING = {"clinic_id": "16224", "name": "Schön Klinik München Schwabing",
             "operator": "Schön Klinik München Schwabing SE & Co. KG", "town": "München", "beds": 165}


def test_a_real_twin_pair_tie_resolves_to_the_higher_bed_site_via_board_town():
    m = Matcher([dict(ROSENECK_A), dict(ROSENECK_B), dict(HARLACHING), dict(SCHWABING)])
    res = m.match("Schön Klinik Karriere Jobportal", "Prien am Chiemsee", board=["18716", "18776"])
    assert res == ("18716", "R0_board_town_bestsite", 0.75)


def test_two_genuinely_different_sites_in_the_same_town_stay_unmatched():
    m = Matcher([dict(ROSENECK_A), dict(ROSENECK_B), dict(HARLACHING), dict(SCHWABING)])
    assert m.match("Schön Klinik Karriere Jobportal", "München", board=["16209", "16224"]) is None


def test_end_to_end_match_call_with_a_real_board_pool():
    """Same as production's own call shape: Matcher.match(employer, city, board=[clinic_id, ...])."""
    m = Matcher([dict(ROSENECK_A), dict(ROSENECK_B), dict(HARLACHING), dict(SCHWABING)])
    res = m.match("Schön Klinik Karriere Jobportal", "Prien am Chiemsee", board=["18716", "18776"])
    assert res[0] == "18716"
