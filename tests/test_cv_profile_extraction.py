"""app/cv.py's deterministic extraction, on the shape of CV this board's candidates actually send.

Requirements audit 2026-09-21, section 2 ("Analyse a CV and match vacancies"): two extractions were
measured wrong against the live board, and both are the NORMAL shape of a foreign-trained nurse's CV
rather than an edge case -- the desired town written without umlauts was dropped entirely, and the
German level was lost whenever a mother tongue was listed before it. The German level is the single
most decisive datum for placement, and the desired town is what ranks the shortlist.

Offline: the board is a fixture snapshot, exactly as tests/test_wa_luna_tools.py builds one. No network,
no LLM -- profile_from_text() is the regex path, analyse_llm() is a different path and a different test.
Every CV text here is synthetic; no candidate's data is in this repo.
"""
import time

import pytest

from app import cv as CV
from app import data as D

# The board's own spellings, the awkward ones included: a town name regularly carries an administrative
# qualifier ('Lohr a. Main') that no candidate types, and a town regularly has two board spellings.
_BOARD_TOWNS = ["Nürnberg", "München", "Erlangen", "Würzburg", "Lohr a. Main", "Neumarkt i.d.OPf.",
                "Neumarkt in der Oberpfalz", "Weißenburg i.Bay.", "Neustadt an der Aisch",
                "Neustadt bei Coburg", "Bad Tölz", "Berg"]
_BEZIRK = {"Nürnberg": "Mittelfranken", "München": "Oberbayern", "Erlangen": "Mittelfranken",
           "Würzburg": "Unterfranken", "Lohr a. Main": "Unterfranken",
           "Neumarkt i.d.OPf.": "Oberpfalz", "Neumarkt in der Oberpfalz": "Oberpfalz",
           "Weißenburg i.Bay.": "Mittelfranken", "Neustadt an der Aisch": "Mittelfranken",
           "Neustadt bei Coburg": "Oberfranken", "Bad Tölz": "Oberbayern", "Berg": "Oberbayern"}


@pytest.fixture(autouse=True)
def _board(monkeypatch):
    jobs = [{"posting_id": i + 1, "title": "Pflegefachkraft Intensivstation", "role_class": "pflegefachkraft",
             "role_label": "Pflegefachkraft", "department_hint": "Intensiv/IMC", "city": town,
             "clinic_town": town, "regierungsbezirk": _BEZIRK[town], "clinic_id": f"c{i}",
             "clinic_name": f"Klinikum {town}", "employer": f"Klinikum {town}", "status": "open",
             "verify_status": "live", "first_published": "2026-09-01", "fresh": True}
            for i, town in enumerate(_BOARD_TOWNS)]
    clinics = [{"clinic_id": f"c{i}", "name": f"Klinikum {town}", "town": town,
                "regierungsbezirk": _BEZIRK[town], "jobs_open": 1, "jobs_fresh": 1, "jobs_live": 1}
               for i, town in enumerate(_BOARD_TOWNS)]
    D._snap.update({"at": time.time(), "jobs": jobs, "clinics": clinics,
                    "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)


def _cv(where="", languages="Sprachen: Deutsch B2, Englisch A2"):
    """A realistic, synthetic CV of the population this board serves: a nurse trained abroad, German
    recognition under way, writing in German with an ASCII keyboard."""
    return ("Lebenslauf\n"
            "Olena Ivanivna\n"
            "Gesundheits- und Krankenpflegerin (Anerkennung beantragt)\n"
            "2016 - 2024 Intensivstation, Universitaetsklinik Kyiv\n"
            "Beatmung, Stroke Unit, 8 Jahre Berufserfahrung\n"
            f"{languages}\n"
            f"{where}\n")


# --- the desired town, written the way a candidate writes it ------------------------------------
# 'Gewuenschter Arbeitsort: Nuernberg.' -> cities=[] live on 2026-09-21, while 'Nürnberg' worked: the
# fold dropped the umlaut ('nurnberg') where the candidate had expanded it ('nuernberg').

@pytest.mark.parametrize("written, expect", [
    ("Gewuenschter Arbeitsort: Nuernberg.", "Nürnberg"),
    ("Gewünschter Arbeitsort: Nürnberg.", "Nürnberg"),
    ("Wunschort: Nurnberg", "Nürnberg"),
    ("Arbeitsort: NUERNBERG", "Nürnberg"),
    ("Ich suche eine Stelle in Wuerzburg.", "Würzburg"),
    ("Umzug nach Muenchen geplant", "München"),
    ("Derzeit wohnhaft in Bad Toelz", "Bad Tölz"),
    # the board's own qualifier, which no candidate types
    ("Ich moechte in Lohr am Main arbeiten", "Lohr a. Main"),
    ("Gewuenschter Arbeitsort: Weissenburg", "Weißenburg i.Bay."),
    ("Bevorzugt: Neustadt an der Aisch", "Neustadt an der Aisch"),
])
def test_the_desired_town_is_read_however_the_candidate_spells_it(written, expect):
    assert CV.profile_from_text(_cv(where=written))["cities"] == [expect], written


def test_a_town_the_board_spells_two_ways_takes_one_place_not_two():
    """'Neumarkt i.d.OPf.' and 'Neumarkt in der Oberpfalz' are one town. Listing both would spend two
    of the profile's eight city slots on it and double-count it in the ranking."""
    cities = CV.profile_from_text(_cv(where="Gewuenschter Arbeitsort: Neumarkt"))["cities"]
    assert len(cities) == 1 and cities[0].startswith("Neumarkt"), cities


def test_a_town_name_two_different_towns_share_is_not_guessed_at():
    """The board has a Neustadt an der Aisch and a Neustadt bei Coburg. A bare 'Neustadt' does not say
    which, and putting the wrong one into the ranking is worse than putting none."""
    assert CV.profile_from_text(_cv(where="Ich wohne in Neustadt."))["cities"] == []


def test_the_town_carries_its_regierungsbezirk_into_the_profile():
    """The bezirk is what match() falls back on when no posting is in the town itself, so losing the
    town silently lost the whole region too."""
    profile = CV.profile_from_text(_cv(where="Gewuenschter Arbeitsort: Nuernberg."))
    assert profile["regierungsbezirke"] == ["Mittelfranken"]


def test_a_cv_that_names_no_town_still_reads_as_no_town():
    assert CV.profile_from_text(_cv())["cities"] == []


@pytest.mark.parametrize("written", ["Nuernberg", "Nürnberg", "Nurnberg"])
def test_the_umlaut_free_town_ranks_postings_exactly_like_the_umlaut_one(written):
    """The bug was silent: the town simply stopped scoring, so a candidate who wants Nürnberg was ranked
    as if they had said nothing about where."""
    ranked = CV.match(CV.profile_from_text(_cv(where=f"Gewuenschter Arbeitsort: {written}.")))
    assert ranked[0]["city"] == "Nürnberg", written
    assert "Nürnberg" in ranked[0]["why"], ranked[0]["why"]


# --- the German level, however the CV orders its languages --------------------------------------
# 'Sprachen: Russisch, Deutsch B2, Englisch A2.' -> ['Englisch A2'] live on 2026-09-21: patterns.json's
# pattern pairs ANY language word with the next level within 40 characters, so 'Russisch' swallowed
# 'Deutsch B2' whole. Every CV that names a mother tongue first lost its German level.

@pytest.mark.parametrize("written, expect", [
    ("Sprachen: Deutsch B2, Englisch A2.", ["Deutsch B2", "Englisch A2"]),
    ("Sprachen: Russisch, Deutsch B2, Englisch A2.", ["Deutsch B2", "Englisch A2"]),
    ("Sprachen: Ukrainisch, Russisch, Deutsch B1", ["Deutsch B1"]),
    ("Sprachen: Russisch (Muttersprache), Deutsch B2, Englisch A2", ["Deutsch B2", "Englisch A2"]),
    ("Sprachen: Tagalog, Englisch C1, Deutsch A2", ["Englisch C1", "Deutsch A2"]),
    ("Sprachen: Rumaenisch - Muttersprache; Deutsch - C1", ["Deutsch C1"]),
    ("Sprachen\nRussisch – Muttersprache\nDeutsch – B2\nEnglisch – A2",
     ["Deutsch B2", "Englisch A2"]),
    ("Deutschkenntnisse: B2", ["Deutsch B2"]),
    ("Sprachniveau Deutsch B2, Englisch A2", ["Deutsch B2", "Englisch A2"]),
    ("B2 Deutsch, Muttersprache Polnisch", ["Deutsch B2"]),
    ("Languages: German B2, English C1", ["Deutsch B2", "Englisch C1"]),
    # a level the board has no CEFR code for still names the language, and never invents a code
    ("Sprachen: Deutsch fließend, Englisch B1", ["Deutsch", "Englisch B1"]),
    # no level stated anywhere -> nothing claimed
    ("Sprachen: Russisch, Ukrainisch", []),
])
def test_the_german_level_survives_whatever_comes_before_it(written, expect):
    assert CV.profile_from_text(_cv(languages=written))["languages"] == expect, written


def test_a_level_belongs_to_the_language_next_to_it_not_the_first_one_in_the_line():
    """The other half of the same bug, and the one that would have put a false claim in front of a
    clinic: 'Deutsch, Englisch B2' is a B2 in English, not in German."""
    assert CV.profile_from_text(_cv(languages="Sprachen: Deutsch, Englisch B2"))["languages"] == \
        ["Englisch B2"]


def test_the_rest_of_the_profile_is_untouched_by_either_fix():
    """Both fixes are inside one extraction each; the qualification, role, ward and years a CV states
    are what the funnel gates on and must read exactly as before."""
    profile = CV.profile_from_text(_cv(where="Gewuenschter Arbeitsort: Nuernberg."))
    assert profile["qualifications"] == ["GuK", "Anerkennung"]
    assert profile["roles"] == ["pflegefachkraft"]
    assert "Intensiv/IMC" in profile["departments"] and "Intensiv" in profile["skills"]
    assert profile["experience_years"] == 8
