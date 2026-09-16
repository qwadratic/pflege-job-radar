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


def test_no_source_hardcodes_a_bavarian_region_or_flag():
    offenders = []
    for p in SRC:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'"region"\s*:\s*"BAYERN"', line) or re.search(r'"in_bavaria"\s*:\s*True\b', line):
                offenders.append(f"{p}:{i}: {line.strip()[:90]}")
    assert not offenders, "a region/in_bavaria may only come from the source:\n" + "\n".join(offenders)


@pytest.mark.parametrize("city, plz, expected", [
    ("Oberhausen", None, False),                      # NON_BAV_CITIES
    (None, "31592", False),                           # Stolzenau, Lower Saxony
    ("Coburg, Bayern, Deutschland", None, True),      # the source states the Land in the city string
    ("Sonneberg, Thüringen, Deutschland", None, False),
    ("Bad Bayersoien", None, None),                   # must not read as "Bayern"
    ("34537 Bad Wildungen", None, False),             # PLZ carried inside the city string
    ("München", None, True),
])
def test_in_bavaria_decides_on_evidence(city, plz, expected):
    assert in_bavaria(city, plz, None, TOWNS) is expected
