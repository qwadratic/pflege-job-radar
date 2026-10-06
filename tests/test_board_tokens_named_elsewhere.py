"""R0_board_tokens: an employer text that names ANOTHER registry site does not attach to a pool clinic that only shares a generic token (TASK-431.9).

Stored: 18 postings of "Helios Amper-Klinikum Dachau" (17) and "Helios Amper-Klinik Indersdorf" (1) sat under clinic 67601, HELIOS Klinik
Erlenbach a. Main, rule R0_board_tokens, score 1.0. Where they come from: every one is a row of the 2026-09-05 snapshot import
(observation payload "_source": "pflege-board.exe.xyz snapshot"), linked by an older matcher; the rung writes 0.7 today, and since
7023b37 (2026-09-14) it needs the posting's own town to agree. With the city the stored rows carry the current ladder links all 18
correctly (R1_exact 17401 for Dachau, R6 for Indersdorf), so those 18 are stale data, not a live link.

What is live is the rung's own cause. 67601's distinguishing tokens (its name minus the town) are {'helios'}: 201 of 651 registry clinics
have one such token, and overlap() is |A and B| / min(|A|, |B|), so ANY employer text that contains 'helios' covers 100 percent of 67601.
Once a posting has no place of its own (the city is a seed stamp and no longer evidence, or the page states none) nothing stops the rung:
"Helios Amper-Klinik Indersdorf" is filed under Erlenbach although the registry has the clinic that carries its other tokens (17402).
The mirror (403 boards, 3 682 retained rows) holds no row on which the rung fires today, so the red case is the stored shape with the
city taken away. Registry rows below are the live ones; no network, no mirror page.
"""
from pflege_jobs.registry import Matcher

REGISTRY = [  # (clinic_id, name, town, operator, beds)
    ("67601", "HELIOS Klinik Erlenbach a. Main", "Erlenbach a. Main", "Kliniken Miltenberg-Erlenbach GmbH", 267),
    ("47601", "HELIOS Frankenwaldklinik Kronach", "Kronach", "Frankenwaldklinik Kronach GmbH", 282),
    ("67201", "HELIOS St. Elisabeth-Krankenhaus Bad Kissingen", "Bad Kissingen", "St. Elisabeth-Krankenhaus GmbH", 175),
    ("17401", "HELIOS Amper-Klinikum Dachau", "Dachau", "Amper Kliniken AG", 435),
    ("17402", "HELIOS Amper-Klinik Indersdorf", "Markt Indersdorf", "Amper Kliniken AG", 35),
    ("RH2544", "Helios Amper-Klinik Indersdorf", "Markt Indersdorf", "Amper Kliniken AG", 67),
]
POOL = ["67601", "47601", "67201"]            # the clinics of the careers page helios-gesundheit.de/karriere/job/..., none of them in Dachau


def _matcher():
    return Matcher([{"clinic_id": i, "name": n, "town": t, "operator": o, "beds": b, "parse_quality": "ok"} for i, n, t, o, b in REGISTRY])


def test_the_stored_shape_with_its_city_never_reached_the_rung():
    m = _matcher()
    assert m.match("Helios Amper-Klinikum Dachau", "Dachau", board=POOL)[:2] == ("17401", "R1_exact")
    assert m.match("Helios Amper-Klinik Indersdorf", "Markt Indersdorf", board=POOL)[0] in ("17402", "RH2544")


def test_an_employer_naming_another_registry_site_is_not_filed_under_a_clinic_sharing_one_generic_token():
    m = _matcher()
    for employer in ("Helios Amper-Klinikum Dachau", "Helios Amper-Klinik Indersdorf"):
        got = m.match(employer, None, board=POOL)
        assert got is None or got[0] != "67601", f"{employer!r} without a place of its own was filed under 67601 Erlenbach: {got}"


def test_the_rung_still_attaches_an_employer_that_names_the_pool_clinic():
    m = _matcher()
    assert m.match("HELIOS Klinik Erlenbach a. Main", None, board=POOL)[0] == "67601"          # by name
    assert m.match("Helios Klinikum Erlenbach", None, board=POOL)[:2] == ("67601", "R0_board_tokens")   # by tokens: nothing else named
