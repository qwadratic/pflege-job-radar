"""A crawler may not assert a region it did not read (root cause of the 2026-09-16 cleanup).

in_bavaria() checks `region` FIRST and returns True on "BAYERN" without looking at the PLZ or the
city. Stamping region="BAYERN" onto a row whose city was inherited from the seed clinic therefore
short-circuited the intake gate (pflege_jobs/cli.py only drops in_bavaria is False), which is how
986 non-Bavarian postings -- Oberhausen, Stolzenau, Bremen -- entered the table as Bavarian.
"""
import pathlib
import re

import pytest

from pflege_jobs.sources.career_crawl import in_bavaria

SRC = [p for p in (list(pathlib.Path("crawlers").rglob("*.py")) + list(pathlib.Path("pflege_jobs").rglob("*.py"))
                   + list(pathlib.Path("app").rglob("*.py")))]
TOWNS = {"münchen", "coburg", "passau", "neuburg"}


# Widening this guard to the value position (below) surfaced exactly one pre-existing, already-
# reviewed exception: app/crawl.py's softgarden branch sets j["in_bavaria"] = True ONLY when the
# value was still undecided (None) AND the feed's own operator is a known Bavaria-only operator --
# a documented label derived from a real domain fact (see the line's own "label, not a filter"
# comment), not a guess fabricated with no evidence. Any OTHER hit here is the real bug this test
# exists to catch; nothing else may be added to this set without the same level of justification.
_ALLOWED = {("app/crawl.py", 'j["in_bavaria"] = True')}


def test_no_source_hardcodes_a_bavarian_region_or_flag():
    # Widened to the VALUE position, not just the dict-literal shape "region": "BAYERN" -- a
    # fallback written at runtime ('region': meta.get(...) or "BAYERN") has the literal string
    # sitting well past the key, which the narrower key:literal pattern missed entirely (confirmed
    # live 2026-09-18: crawl_mein_check_in's "or 'BAYERN'" fallback survived the 2026-09-16
    # cleanup this way). Matching the bare string "BAYERN" anywhere catches any such fallback,
    # wherever it sits in the expression.
    offenders = []
    for p in SRC:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'"BAYERN"', line) or re.search(r'\["in_bavaria"\]\s*=\s*True\b', line) or re.search(r'"in_bavaria"\s*:\s*True\b', line):
                stripped = line.strip()
                if (str(p), stripped) in _ALLOWED:
                    continue
                offenders.append(f"{p}:{i}: {stripped[:90]}")
    assert not offenders, "a region/in_bavaria may only come from the source:\n" + "\n".join(offenders)


@pytest.mark.parametrize("city, plz, expected", [
    ("Oberhausen", None, False),                      # NON_BAV_CITIES
    (None, "31592", False),                           # Stolzenau, Lower Saxony
    ("Coburg, Bayern, Deutschland", None, True),      # the source states the Land in the city string
    ("Sonneberg, Thüringen, Deutschland", None, False),
    ("Bad Bayersoien", None, None),                   # must not read as "Bayern"
    ("34537 Bad Wildungen", None, False),             # PLZ carried inside the city string
    ("München", None, True),
    # TASK-185 F7c: a municipality the repo's own table (data/geo, pflege_jobs.geo) places in another Land is not Bavaria, which
    # the hand list NON_BAV_CITIES did not know (26 AMEOS slug cities, 213 rows)
    ("Osnabrück", None, False),
    ("Neustadt in Holstein", None, False),
    ("Stralsund", None, False),
    ("Giengen", None, False),                         # BW, the PLZ range 89xxx overreaches into it but a bare name does not
    # ...and only that direction: the table never makes a city Bavarian here, and an ambiguous name stays undecided
    ("Brunnen", None, None),                          # a Bavarian Gemeinde AND Brunnen SZ (AMEOS Switzerland, 8 rows)
    ("Petershagen", None, None),                      # BB and NW
])
def test_in_bavaria_decides_on_evidence(city, plz, expected):
    assert in_bavaria(city, plz, None, TOWNS) is expected


def test_a_registry_town_stays_bavarian_even_when_another_land_has_a_municipality_of_that_name():
    # the table is consulted last: a registry clinic town (towns) decides before it
    assert in_bavaria("Osnabrück", None, None, TOWNS | {"osnabrück"}) is True
