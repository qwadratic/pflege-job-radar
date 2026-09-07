"""classify.classify_role's nursing_section_confirmed relaxation + section.job_confirmed_nursing,
the strict per-job signal that feeds it. Every title/department-label pair below is a REAL example
from the 2026-09 survey of live vendor boards (see task notes), not an invented case.

Design, grounded in what the survey actually found:
  - Only step 1 (the pflege_gate token requirement) is relaxed. Real dept-tagged titles that failed
    ONLY that gate: dvinci "Hygienefachkraft (m/w/d)" (dept "Pflege- und Funktionsdienst"),
    dvinci "Advanced Practice Nurses (m/w/d)" and "Gerontofachkraft (w/m/d)" (dept "02 Pflegedienst"),
    smartrecruiters "Dauernachtwache (m/w/d)" (dept "Pflegedienst").
  - Step 2 (the nicht_pflege/strong_pflege check) is left unchanged and must keep firing even inside
    a confirmed section: the survey found zero real examples of it wrongly excluding a genuinely-
    nursing section-confirmed title, and real examples of it correctly excluding a competing
    occupation even inside a confirmed nursing bucket -- rexx "Medizinische Fachangestellte (m/w/d)
    für den OP in München" and "Kodierfachkraft (m/w/d) für DRG/PEPP", both dept "Pflege,
    Patientenmanagement & Dokumentation". No regression test was needed for "step 2 wrongly fires
    inside a confirmed section" beyond these two -- no real example of that failure mode existed in
    the survey at all, so nothing else was changed there.
  - Steps 3/4 (offer_kind AUSBILDUNG/PRAKTIKUM_TRAINEE, the pflegehelfer/ausbildung ROLE_RULES) never
    relax: real examples that must stay excluded even inside a confirmed section: smartrecruiters
    "Ausbildung Pflegefachmann/-frau (m/w/d)" (offer_kind AUSBILDUNG) and dvinci "Betreuungskraft
    §43b (m/w/d) stationär" (pflegehelfer rule).
"""
from pflege_jobs.classify import classify_role
from pflege_jobs.section import job_confirmed_nursing


# --- job_confirmed_nursing: the strict per-job signal ------------------------------------------

def test_confirms_on_a_real_nursing_department_label():
    assert job_confirmed_nursing("Pflegedienst") is True
    assert job_confirmed_nursing("Pflege- und Funktionsdienst") is True
    assert job_confirmed_nursing(["02 Pflegedienst"]) is True


def test_does_not_confirm_a_non_nursing_or_missing_label():
    assert job_confirmed_nursing("Verwaltung") is False
    assert job_confirmed_nursing("Chirurgie") is False
    assert job_confirmed_nursing(None) is False
    assert job_confirmed_nursing([]) is False
    assert job_confirmed_nursing("") is False


# --- classify_role: gate relaxation, real dept-tagged titles that failed only the gate ----------

def test_confirmed_section_recovers_titles_that_only_failed_the_pflege_gate():
    cases = [
        ("Dauernachtwache (m/w/d)", "pflegefachkraft"),                 # smartrecruiters, dept "Pflegedienst"
        ("Hygienefachkraft (m/w/d)", "apn_experte"),                    # dvinci, dept "Pflege- und Funktionsdienst"
        ("Advanced Practice Nurses (m/w/d)", "apn_experte"),            # dvinci, dept "02 Pflegedienst"
        ("Gerontofachkraft (w/m/d)", "sonstige_pflege"),                # dvinci, dept "02 Pflegedienst" -- fallback, still kept
    ]
    for title, want in cases:
        assert classify_role(title, "", "", nursing_section_confirmed=True)[0] == want, title


def test_without_confirmation_the_same_titles_are_still_dropped_today():
    # unchanged default behaviour (nursing_section_confirmed=False) -- proves the relaxation is opt-in
    for title in ("Dauernachtwache (m/w/d)", "Hygienefachkraft (m/w/d)", "Advanced Practice Nurses (m/w/d)"):
        role, rule = classify_role(title, "")
        assert (role, rule) == ("nicht_pflege", "no_pflege_token"), title


# --- classify_role: step 2 (nicht_pflege/strong_pflege) still runs inside a confirmed section ----

def test_competing_occupations_stay_excluded_even_inside_a_confirmed_section():
    # rexx, dept "Pflege, Patientenmanagement & Dokumentation" -- a broad HR bucket that also
    # carries MFAs and Kodierfachkräfte; step 2 is what must still catch these.
    cases = [
        "Medizinische Fachangestellte (m/w/d) für den OP in München",
        "Kodierfachkraft (m/w/d) für DRG/PEPP",
    ]
    for title in cases:
        assert classify_role(title, "", "", nursing_section_confirmed=True)[0] == "nicht_pflege", title


# --- classify_role: helpers/learners still excluded even inside a confirmed section (steps 3/4) --

def test_pflegehelfer_and_ausbildung_still_excluded_inside_a_confirmed_section():
    # dvinci, dept "02 Pflegedienst"
    role, rule = classify_role("Betreuungskraft §43b (m/w/d) stationär", "", "", nursing_section_confirmed=True)
    assert role == "pflegehelfer"
    # smartrecruiters, dept "Pflegedienst"
    role, rule = classify_role("Ausbildung Pflegefachmann/-frau (m/w/d)", "", "AUSBILDUNG", nursing_section_confirmed=True)
    assert role == "ausbildung"
