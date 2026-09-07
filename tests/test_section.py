"""pick_nursing_category / pick_nursing_link against real label/link sets collected in the 2026-09
vendor survey (see the survey report for the live URLs each set came from)."""
from pflege_jobs.section import NURSING_SECTION_RX, pick_nursing_category, pick_nursing_link


# --- personio: maximilians-augenklinik-ggmbh.jobs.personio.de/xml <department>/<recruitingCategory> ---
def test_personio_department_categories():
    cats = ["MVZ Erlenstegen", "MVZ Röthenbach", "MVZ Stein", "Pflege"]
    assert pick_nursing_category(cats) == "Pflege"


def test_personio_tenant_with_no_nursing_department():
    # bergmanclinics.jobs.personio.de: an eye-clinic tenant whose <department> values never include
    # a nursing label at all -- has_section_signal is genuinely "sometimes" per tenant.
    cats = ["Abrechnung", "Anästhesie", "Augenheilkunde", "Empfang", "Management", "Ärztlicher Dienst"]
    assert pick_nursing_category(cats) is None


# --- smartrecruiters: api.smartrecruiters.com/v1/companies/ArtemedSE/postings[].department.label ---
def test_smartrecruiters_department_label():
    assert pick_nursing_category(["Pflegedienst"]) == "Pflegedienst"


# --- dvinci: romed-jobs.de/jobPublication/list.json jobOpening.categories[].name ---
def test_dvinci_categories():
    cats = ["Ärztlicher Dienst", "Medizinisch-technischer Dienst", "Pflege- und Funktionsdienst",
            "Verwaltung", "IT und Technik"]
    assert pick_nursing_category(cats) == "Pflege- und Funktionsdienst"


# --- concludis (TYPO3+Solr "nxmamajobs" variant): karriere.martha-maria.de jobgroup facet ---
def test_concludis_jobgroup_facet():
    cats = ["Ausbildung", "Medizinisch-technischer Dienst", "Pflege", "Service", "Sonstiges",
            "Studentische Hilfskräfte", "Verwaltung", "Ärztinnen und Ärzte"]
    assert pick_nursing_category(cats) == "Pflege"


# --- talention: jobs.ebel-kliniken.com POST talention/api/3.2/job campaignProperties[bereich] ---
def test_talention_bereich_property():
    cats = ["Therapeutisches Personal", "Technisches Personal", "Medizinisches Personal", "Sonstiges",
            "Pflegepersonal"]
    assert pick_nursing_category(cats) == "Pflegepersonal"


# --- rexx: jobs.schoen-klinik.de listing "Fachbereich" tag shown per job ---
def test_rexx_fachbereich_tag():
    assert pick_nursing_category(["Chirurgie"]) is None
    assert pick_nursing_category(["Pflege, Patientenmanagement & Dokumentation"]) == \
        "Pflege, Patientenmanagement & Dokumentation"


# --- bite: augustinum tenant custom.taetigkeitsfelder vs. naturheilweisen tenant (no such field) ---
def test_bite_taetigkeitsfelder_values():
    assert pick_nursing_category(["Pflegefachkraft"]) == "Pflegefachkraft"
    assert pick_nursing_category(["Pflegehelfer*in"]) == "Pflegehelfer*in"
    assert pick_nursing_category(["Verwaltung", "Sonstiges", "Haustechnik"]) is None


# --- mein-check-in: kna-online /overview sidebar <li id="pg-12880"><span>Pflegedienst</span> ---
def test_mein_check_in_position_group_heading():
    assert pick_nursing_category(["Ärztlicher Dienst", "Pflegedienst", "Verwaltungsdienst"]) == "Pflegedienst"


# --- generic wp_jobs fallback: muenchen-klinik.de/jobs/ "Berufsgruppe" footer <select> ---
def test_wp_jobs_nav_link():
    links = [
        ("Ausbildung", "https://www.muenchen-klinik.de/ausbildung/"),
        ("Ärzte", "https://www.muenchen-klinik.de/jobs/arzt/"),
        ("Bau & Technik", "https://www.muenchen-klinik.de/jobs/ingenieur-techniker-servicekraft/"),
        ("Funktionsdienste / Sonstige Dienste", "https://www.muenchen-klinik.de/jobs/funktionsdienst/"),
        ("Medizinisch-Technischer Dienst", "https://www.muenchen-klinik.de/jobs/mta/"),
        ("Pflegedienst", "https://www.muenchen-klinik.de/jobs/pflege/"),
        ("Verwaltung & Management", "https://www.muenchen-klinik.de/jobs/verwaltung-management/"),
    ]
    assert pick_nursing_link(links) == "https://www.muenchen-klinik.de/jobs/pflege/"


def test_no_link_matches_returns_none():
    links = [("Ärzte", "/arzt/"), ("Verwaltung", "/verwaltung/")]
    assert pick_nursing_link(links) is None


def test_empty_inputs():
    assert pick_nursing_category([]) is None
    assert pick_nursing_category(None) is None
    assert pick_nursing_link([]) is None
    assert pick_nursing_link(None) is None


# --- word-boundary discipline: no live board in the 2026-09 survey ever used "pflege" as a substring
# for something other than nursing, but the regex must still reject it if one ever does (a facilities
# department labelled for grounds/vehicle/textile "care") rather than matching on bare substring. ---
def test_word_boundary_rejects_unrelated_compound():
    assert NURSING_SECTION_RX.search("Grünflächenpflege") is None
    assert pick_nursing_category(["Grünflächenpflege", "Fahrzeugpflege"]) is None
    assert pick_nursing_link([("Grünflächenpflege", "/facility/grounds/")]) is None


def test_word_boundary_still_matches_real_labels():
    assert NURSING_SECTION_RX.search("Pflege") is not None
    assert NURSING_SECTION_RX.search("Pflegedienst") is not None
    assert NURSING_SECTION_RX.search("Gesundheits- und Krankenpflege") is not None
    assert NURSING_SECTION_RX.search("Pflege- und Funktionsdienst") is not None
    assert NURSING_SECTION_RX.search("Nursing") is not None
