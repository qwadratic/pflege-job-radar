from pflege_jobs.classify import department_hint, extract_section
from pflege_jobs.mechanics import get


def test_common_departments():
    assert department_hint("Pflegefachkraft Intensivstation") == "Intensiv/IMC"
    assert department_hint("Pflegefachkraft OP (m/w/d)") == "OP"
    assert department_hint("Pflegefachkraft Notaufnahme (m/w/d)") == "Notaufnahme"
    assert department_hint("Pflegefachkraft (m/w/d) für die Anästhesie") == "Anästhesie"


def test_multiple_distinct_departments_all_reach_the_facet():
    # TASK-97: department_hint used to be next((n for n, r in _DEPT if r.search(s)), None) -- first
    # match only. A posting for Intensiv AND Anästhesie lost one of the two. Now every distinct match
    # is kept, "|"-joined (app/data.py splits this back into a list for the facet/filter/search layer).
    assert department_hint("Pflegefachkraft (m/w/d) für Intensivstation und Anästhesie") == "Intensiv/IMC|Anästhesie"


def test_order_decides_on_multiple_hits():
    # Both patterns genuinely match this title (Intensivstation, Innere Medizin) -- both are kept, in
    # patterns.json's declared department order, not just the first ("Intensiv/IMC" alone, the pre-
    # TASK-97 behaviour this test used to assert).
    assert department_hint("Pflegefachkraft Intensivstation Innere Medizin") == "Intensiv/IMC|Innere Medizin"


def test_null_when_not_stated():
    assert department_hint("Pflegefachkraft (m/w/d)") is None
    assert get("department").run({"title": "Pflegefachkraft (m/w/d)"}) == {"result": {"department_hint": None}, "rule": None}


# --- TASK-97: title alone misses ~43% of postings whose specialty is only in the body; naively
# scanning the whole description picks up department phone directories, "verfügt über ..."
# hospital-wide boilerplate and site menus instead. department_hint(title, desc) now also reads the
# posting's own TASKS (Aufgaben/Tätigkeiten) and PROFIL (Ihr Profil) sections, via extract_section() --
# the same section-boundary approach enrich_description() already used for "Ihr Profil" -- and nothing
# else in the body. Fixtures below are trimmed, de-identified excerpts of the exact shapes confirmed
# live 2026-09-24 (Klinikum Fürstenfeldbruck posting_id 5979's page-wide "Fachbereiche" contact list;
# Ilmtalklinik posting_id 10767's "verfügt über" intro paragraph) that the task named by name.
def test_department_named_only_in_the_tasks_section_is_found():
    desc = ("Wir suchen Verstärkung für unser Team. Ihre Aufgaben: Grund- und Behandlungspflege von "
            "Patienten auf der interdisziplinären Intensivstation, Assistenz bei diagnostischen und "
            "therapeutischen Maßnahmen. Ihr Profil: examinierte Pflegefachkraft, Teamfähigkeit.")
    assert department_hint("Pflegefachkraft (m/w/d)", desc) == "Intensiv/IMC"


def test_department_named_only_in_the_profil_section_is_found():
    desc = ("Ihre Aufgaben: allgemeine Stationsarbeit nach Absprache. Ihr Profil: Erfahrung in der "
            "Anästhesie und im Aufwachraum wünschenswert, examinierte Pflegefachkraft.")
    assert department_hint("Pflegefachkraft (m/w/d)", desc) == "Anästhesie"


def test_department_phone_directory_line_produces_no_label():
    # Live 2026-09-24 (Klinikum Fürstenfeldbruck, posting_id 5979): a page-wide "Fachbereiche" contact
    # block, not this posting's own section -- classifying on it would file this Notaufnahme posting
    # under every department the hospital happens to have a secretariat phone number for.
    desc = ("Fachbereiche Anästhesie & operative Intensivmedizin Sekretariat 08141/99-3001 "
            "anaesthesie@klinikum-ffb.de Neurologie & Stroke Unit Sekretariat 08141/99-6103 "
            "neurologie@klinikum-ffb.de Montag - Freitag: 8.30 bis 14.00 Uhr")
    assert department_hint("Pflegefachkraft für unsere zentrale Notaufnahme", desc) == "Notaufnahme"


def test_hospital_wide_verfuegt_ueber_boilerplate_produces_no_label():
    # Live 2026-09-24 (Ilmtalklinik, posting_id 10767): a hospital-wide "about us" sentence outside
    # both the Aufgaben and Profil sections of a Gynäkologie/Geburtshilfe posting -- "Stroke Unit" here
    # describes the hospital, not this posting, and must not add Neurologie to its facet membership.
    desc = ("Als Haus der Grund- und Regelversorgung verfügen wir neben den klassischen Fachabteilungen "
            "auch über eine Chest Pain Unit, eine Stroke Unit, eine Akutgeriatrie und ein "
            "Endoprothetik-Zentrum. Das bieten wir Dir: Vergütung nach TVöD-K. Deine Aufgaben bei uns: "
            "Unterstützung der Stationsleitung bei der fachlichen Mitarbeiterführung. Dein Profil: "
            "Abgeschlossene Ausbildung zur Pflegefachkraft.")
    assert department_hint("Stellvertretende Stationsleitung für Gynäkologie und Geburtshilfe", desc) == "Geburtshilfe"


# --- TASK-156: patterns.json's department list carried 3 unanchored substrings that swallow an
# unrelated German word whenever a prefix attaches directly in front with no boundary -- "operations"
# inside "Kooperationspartner"/"Kooperationsbereitschaft", "sucht" inside the ubiquitous recruiting verb
# form "gesucht" ("... Pflegefachkraft gesucht!"), and "unfall" inside "Unfallvorschriften" (workplace
# safety regulations, not the Unfallchirurgie/trauma-surgery department). Live-measured 2026-09-24
# against all 3848 open postings' title+Aufgaben+Profil scanned text (the exact department_hint() scan
# surface): 70 postings' department_hint value changed, 42 of them losing their ONLY label entirely
# (a pure false positive with no real department stated at all) -- 1954 -> 1912 labeled postings
# (-2.1% of the previously-labeled set), 2927 -> 2857 total department tag-instances (-2.4%).
# Fixed with narrow, live-evidence-driven anchoring -- NOT a blanket \b on every alternative, which
# would break real compounds these same patterns must keep matching (Operationsdienst, Kinderintensiv-
# station, Suchterkrankungen/Suchtmedizin, Unfallchirurgie): "operations" gets a left \b (only blocks an
# unrelated PREFIX attaching before it; every legitimate live form -- Operationsdienst, Operations-
# bereich, Operationssäle, Operationstechnische -- already starts the word, so the fix costs nothing);
# "sucht" gets negative lookbehinds for the specific German verb-conjugation prefixes that produce a
# false hit (ge-/be-/er-/ver-/unter-, covering gesucht/besucht/ersucht/versucht/untersucht) while still
# matching every live Sucht-noun compound (Suchterkrankungen, Suchtmedizin, Suchtpsychiatrie -- these
# start the word with no prefix at all, so the lookbehinds never trigger on them); "unfall" gets a
# narrow negative lookahead for the one confirmed false continuation ("vorschrift"), leaving
# Unfallchirurgie/Unfallchirurgische etc. (unfall + a DIFFERENT continuation) untouched.
def test_kooperationspartner_does_not_trigger_op():
    desc = ("Ihre Aufgaben: Zusammenarbeit mit unserem Kooperationspartner und Kooperationsbereitschaft "
            "im interdisziplinären Team. Ihr Profil: Teamfähigkeit und Kooperationsfähigkeit.")
    assert department_hint("Pflegefachkraft (m/w/d)", desc) is None


def test_gesucht_does_not_trigger_psychiatrie():
    desc = ("Ihre Aufgaben: Allgemeine Stationsarbeit nach Absprache. Ihr Profil: Examinierte "
            "Pflegefachkraft gesucht, Berufserfahrung wünschenswert.")
    assert department_hint("Pflegefachkraft (m/w/d) gesucht", desc) is None


def test_unfallvorschriften_does_not_trigger_chirurgie():
    desc = ("Ihre Aufgaben: Einhaltung der Hygiene- und Unfallvorschriften. Ihr Profil: "
            "Zuverlässigkeit und Teamfähigkeit.")
    assert department_hint("Pflegefachkraft (m/w/d)", desc) is None


def test_legitimate_op_compounds_still_match_after_the_anchoring_fix():
    desc = "Ihre Aufgaben: Mitarbeit im Operationsdienst und im Zentral-OP. Ihr Profil: OTA-Ausbildung."
    assert department_hint("Pflegefachkraft (m/w/d)", desc) == "OP"


def test_legitimate_sucht_compounds_still_match_after_the_anchoring_fix():
    desc = ("Ihre Aufgaben: Betreuung von Patienten mit Suchterkrankungen. Ihr Profil: Erfahrung in "
            "der Suchtmedizin und Suchtpsychiatrie.")
    assert department_hint("Pflegefachkraft (m/w/d)", desc) == "Psychiatrie"


def test_legitimate_unfallchirurgie_still_matches_after_the_anchoring_fix():
    desc = "Ihre Aufgaben: Versorgung von Patienten in der Unfallchirurgie. Ihr Profil: Teamfähigkeit."
    assert department_hint("Pflegefachkraft (m/w/d)", desc) == "Chirurgie/Orthopädie"


def test_extract_section_is_the_shared_reusable_helper():
    """extract_section(desc, head_rx, stop_rx) is what both enrich_description()'s "Ihr Profil"
    extraction and department_hint()'s Aufgaben/Profil extraction call -- TASK-104/TASK-107 reuse it
    directly instead of re-implementing the same boundary slicing a third and fourth time."""
    import re
    head, stop = re.compile("ihre aufgaben"), re.compile("ihr profil")
    assert extract_section("", head, stop) is None                         # empty desc -> None
    assert extract_section("kein Treffer hier", head, stop) is None        # head never matches -> None
    text = extract_section("Ihre Aufgaben: Pflege auf der Station. Ihr Profil: examiniert.", head, stop)
    assert text == "Pflege auf der Station."
