from pflege_jobs.classify import qualification_hint
from pflege_jobs.mechanics import get


def test_from_hauptberuf():
    assert qualification_hint("x", "Gesundheits- und Kinderkrankenpfleger/in") == "GKiK"
    assert qualification_hint("x", "Pflegefachmann/-frau (Altenpflege)") == "Altenpflege"
    assert qualification_hint("x", "Gesundheits- und Krankenpfleger/in") == "GuK"


def test_from_title_only():
    assert qualification_hint("Gesundheits- und Krankenpfleger (m/w/d) Intensiv") == "GuK"
    assert qualification_hint("Pflegefachfrau / Pflegefachmann (m/w/d)") in ("generalistisch", "GuK", None)


def test_null_means_not_stated():
    assert qualification_hint("Stationsleitung (m/w/d)") is None
    assert get("qualification").run({"title": "Stationsleitung (m/w/d)", "hauptberuf": ""})["result"]["qualification_hint"] is None


# --- TASK-104: qualification_hint shared department_hint's pre-TASK-97 defect -- title (+ hauptberuf,
# an Arbeitsagentur occupation code most sources never populate) only, never the posting's own body.
# A generic title ("Pflegefachkräfte (m/w/d)") whose required licence is stated only in its Aufgaben/
# Profil text used to come back null -- and app/autopilot/matching.py's _quali_ok() auto-passes every
# candidate against a null job_q, so the qualification gate silently did nothing for that posting.
# qualification_hint(title, hauptberuf, desc) now also reads the posting's own TASKS (Aufgaben/
# Tätigkeiten) and PROFIL (Ihr Profil) sections via extract_section() -- the identical reused helper
# and section boundaries department_hint() already scans (TASK-97) -- and nothing else in the body.
def test_qualification_named_only_in_the_tasks_section_is_found():
    desc = ("Wir suchen Verstärkung für unser Team. Ihre Aufgaben: examinierte Gesundheits- und "
            "Krankenpfleger (m/w/d) übernehmen die Grund- und Behandlungspflege auf der Station. "
            "Ihr Profil: Teamfähigkeit, Zuverlässigkeit.")
    assert qualification_hint("Pflegekraft (m/w/d)", "", desc) == "GuK"


def test_qualification_named_only_in_the_profil_section_is_found():
    desc = ("Ihre Aufgaben: allgemeine Stationsarbeit nach Absprache. Ihr Profil: abgeschlossene "
            "Ausbildung zur Altenpflegerin oder zum Altenpfleger, Freude am Umgang mit Menschen.")
    assert qualification_hint("Pflegekraft (m/w/d)", "", desc) == "Altenpflege"


def test_title_only_misses_a_qualification_stated_in_the_body_live_example():
    # Live 2026-09-24, posting_id 5023 (title alone and title+hauptberuf carry no licence at all --
    # "Chest Pain Unit" is a department name, not a qualification). Trimmed excerpt of the real "Ihr
    # Profil" section, which does state it.
    title = "Pflegefachkräfte (m/w/d) für die Aufnahmestation/Chest Pain Unit"
    desc = ("Ihr Profil Sie haben eine erfolgreich abgeschlossene Berufsausbildung als Pflegefachkraft "
            "(m/w/d) oder Gesundheits- und Krankenpfleger (m/w/d). Sie verfügen über sehr gutes "
            "Fachwissen. Wir bieten: Vergütung nach TVöD.")
    assert qualification_hint(title, "") is None           # RED under the old title(+hauptberuf)-only contract
    assert qualification_hint(title, "", desc) == "GuK"    # GREEN once the Profil section is scanned too


def test_text_past_the_section_boundary_produces_no_label():
    # extract_section()'s stop-boundary guarantee (TASK-97) applies here unchanged: a qualification word
    # sitting past "Wir bieten" (a Kontakt/Sekretariat-style tail, not this posting's own Aufgaben text)
    # must not be scanned, the same way department_hint() ignores page-tail phone directories.
    desc = ("Ihre Aufgaben: Verwaltungstätigkeiten im Sekretariat. Wir bieten: gute Bezahlung. Kontakt: "
            "unser Pflegeteam besteht aus erfahrenen Gesundheits- und Krankenpflegern.")
    assert qualification_hint("Verwaltungsassistenz (m/w/d)", "", desc) is None


def test_settings_page_try_it_wiring_passes_description_through():
    # The "qualification" mechanic (Settings page) gained a "description" input alongside
    # title/hauptberuf (mirrors the "department" mechanic's TASK-97 change) -- proves _try_qualification
    # actually forwards it to qualification_hint()'s new 3rd argument, not dead wiring.
    desc = "Ihre Aufgaben: Stationsarbeit. Ihr Profil: examinierte Altenpflegerin oder Altenpfleger."
    out = get("qualification").run({"title": "Pflegekraft (m/w/d)", "hauptberuf": "", "description": desc})
    assert out["result"]["qualification_hint"] == "Altenpflege"
