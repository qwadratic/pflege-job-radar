from pflege_jobs.classify import classify_employer, classify_role, employer_norm, fuzzy_key, department_hint, qualification_hint, enrich_description

def test_employer_clinic():
    assert classify_employer("Klinikum Nürnberg")[0] == "clinic"
    assert classify_employer("Universitätsklinikum Erlangen")[0] == "clinic"
    assert classify_employer("Bezirkskrankenhaus Bayreuth")[0] == "clinic"
    assert classify_employer("Sana Klinik München GmbH")[0] == "clinic"
    assert classify_employer("kbo-Isar-Amper-Klinikum gemeinnützige GmbH")[0] == "clinic"

def test_employer_non_clinic():
    assert classify_employer("AWO Seniorenzentrum Bamberg")[0] == "non_clinic"
    assert classify_employer("Ambulante Intensivpflege ape GmbH")[0] == "non_clinic"
    assert classify_employer("BRK KV Miesbach Personalabteilung")[0] == "non_clinic"
    assert classify_employer("Pflegedienst Sonnenschein GmbH")[0] == "non_clinic"

def test_employer_conflict_and_unknown():
    c, rule = classify_employer("Klinik-Seniorenresidenz XY")
    assert c == "unknown" and rule.startswith("conflict")
    assert classify_employer("Müller & Söhne")[0] == "unknown"

def test_employer_norm():
    assert employer_norm("Klinikum Nürnberg gGmbH") == employer_norm("Klinikum Nürnberg GmbH")
    assert employer_norm("Klinikum Nürnberg") != employer_norm("Klinikum Nürnberg Personalabteilung")

def test_roles():
    assert classify_role("Pflegefachkraft (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"
    assert classify_role("Stationsleitung (m/w/d)", "Stationsleiter/in - Kranken-/Alten-/Kinderkrankenpflege")[0] == "leitung"
    assert classify_role("Fachkrankenpfleger Intensiv (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "fachpflege"
    assert classify_role("Pflegefachkraft Intensivstation (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"
    assert classify_role("Pflegehelfer (m/w/d)", "Altenpflegehelfer/in")[0] == "pflegehelfer"
    assert classify_role("Hebamme (m/w/d)", "Hebamme/Entbindungspfleger")[0] == "hebamme"
    assert classify_role("OTA (m/w/d)", "Operationstechnische/r Assistent/in")[0] == "ota_ata"
    assert classify_role("Praxisanleiter (m/w/d)", "Praxisanleiter/in - Pflegeberufe")[0] == "praxisanleitung"
    assert classify_role("Ausbildung Pflegefachmann/-frau 2027", "Pflegefachmann/-frau (Ausbildung)", "AUSBILDUNG")[0] == "ausbildung"
    assert classify_role("Ausbildung Pflegefachkraft", "Pflegefachmann/-frau (Ausbildung)")[0] == "ausbildung"
    assert classify_role("Notfallsanitäter (m/w/d)", "Notfallsanitäter/in")[0] == "nicht_pflege"
    assert classify_role("MFA (m/w/d)", "Medizinische/r Fachangestellte/r")[0] == "nicht_pflege"
    assert classify_role("Pflegefachkraft Notaufnahme (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"
    assert classify_role("Werkstudent Pflege (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "werkstudent_praktikum"
    assert classify_role("Pflegedienstleitung (m/w/d)", "Pflegedienstleiter/in")[0] == "leitung"
    assert classify_role("Advanced Practice Nurse Onkologie", "Gesundheits- und Krankenpfleger/in")[0] == "apn_experte"

def test_hints():
    assert department_hint("Pflegefachkraft Intensivstation") == "Intensiv/IMC"
    assert department_hint("Pflegefachkraft OP (m/w/d)") == "OP"
    assert department_hint("Pflegefachkraft (m/w/d)") is None
    assert qualification_hint("x", "Gesundheits- und Kinderkrankenpfleger/in") == "GKiK"
    assert qualification_hint("x", "Pflegefachmann/-frau (Altenpflege)") == "Altenpflege"

def test_fuzzy_key_stable():
    a = fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg gGmbH", "Nürnberg")
    b = fuzzy_key("Pflegefachkraft (w/m/d) Vollzeit", "Klinikum Nürnberg GmbH", "nürnberg")
    assert a == b
    assert a != fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg", "Fürth")

def test_enrich():
    e = enrich_description("Wir bieten Personalwohnungen. Vergütung nach TVöD-K. Kontakt: pflegedirektion@klinik.de, B2 Deutsch erforderlich. Willkommensprämie 3000€")
    assert e["housing"] and e["tariff"] == "TVöD" and e["contact_emails"] == ["pflegedirektion@klinik.de"]
    assert e["bonus"] and e["language_req"]
    assert enrich_description("") == {}

def test_conflict_matrix():
    assert classify_employer("Malteser Waldkrankenhaus Erlangen gGmbH")[0] == "clinic"
    assert classify_employer("Caritas-Krankenhaus St. Josef Träger Caritasverband e.V.")[0] == "clinic"
    assert classify_employer("Kliniken Nordoberpfalz AG Akademie für Gesundheit")[0] == "clinic"
    assert classify_employer("Donau-Ries Kliniken und Seniorenheime gKU Seniorenheim Monheim")[0] == "unknown"
    assert classify_employer("Sozialstiftung Bamberg")[0] == "clinic"
    assert classify_employer("Schwesternschaft München vom B Roten Kreuz e. V.")[0] == "clinic"
    assert classify_employer("Deutschefachpflege Holding GmbH")[0] == "non_clinic"
    assert classify_employer("MVZ Praxisklinik Orthospine")[0] == "unknown"
    assert classify_employer("Deutsche Rentenversicherung Bund Reha-Zentrum Bad Brückenau Klinik Hartwald")[0] == "clinic"

def test_audit_fixes():
    assert classify_role("Alltagsbegleiter (m/w/d)", "Betreuungskraft / Alltagsbegleiter/in")[0] == "pflegehelfer"
    assert classify_role("Bildungsbegleiter (m/w/d) im Jugendbereich", "Pflegedienstleiter/in")[0] == "nicht_pflege"
    assert classify_role("Schulleiter/in", "Pflegedienstleiter/in")[0] == "nicht_pflege"
    assert classify_role("Stationsleitung – Anästhesiologische operative Intensivstation", "Wachleiter/in - Rettungsdienst")[0] == "leitung"
    assert classify_role("Pflegeüberleitung im Sozial- & Entlassmanagement (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"  # GuK role, not leadership
    assert classify_role("Leitung Restaurant (m/w/d), Medical Park Bad Rodach", "Pflegedienstleiter/in")[0] == "nicht_pflege"
    assert classify_role("Versorgungsassistenz (m/w/d) Neurologie Phase B", "Pflegefachassistent/in")[0] == "pflegehelfer"
    assert classify_role("Pflegeunterstützungskraft (w/m/d)", "Bürokaufmann/-frau")[0] == "pflegehelfer"
    assert classify_role("Mitarbeiter (m/w/d) im Hol- u. Bringedienst", "Schwestern-/Pflegediensthelfer/in")[0] == "nicht_pflege"
    assert classify_role("Stellvertretende AEMP-Leitung (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "nicht_pflege"
    assert classify_role("Pflege- oder Medizinpädagoge mit Bachelor-Abschluss (m/w/d)", "Lehrkraft - Schulen im Gesundheitswesen")[0] == "apn_experte"
    assert classify_role("Kommissarische Funktionsleitung Anästhesiepflege (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "leitung"
    assert classify_role("Leiter - Pflege (m/w/d)", "Pflegedienstleiter/in")[0] == "leitung"
    assert classify_employer("Thoraxzentrum Bezirk Unterfranken")[0] == "clinic"
    assert classify_employer("Oberberg GmbH Ost")[0] == "clinic"
    assert classify_employer("Vitolus Care GmbH")[0] == "non_clinic"
    assert classify_employer("Rummelsberger Diakonie e.V. Rummelsberger Dienste gAG")[0] == "non_clinic"

def test_physicians_are_not_nursing():
    assert classify_role("Facharzt (m/w/d) Anästhesie und Intensivmedizin", "Fachkinderkrankenpfleger/in - Intensivpflege/Anästhesie")[0] == "nicht_pflege"
    assert classify_role("Chefarzt (m/w/d) der Klinik für Anästhesiologie", "Fachkrankenpfleger/in - Intensivpflege/Anästhesie")[0] == "nicht_pflege"
    assert classify_role("Psychologe (m/w/d) - Psychosomatik", "Fachkinderkrankenpfleger/in - Psychiatrie")[0] == "nicht_pflege"
    assert classify_role("Pflegefachfrau / Pflegefachmann (m/w/d) für unsere psychotherapeutische Station", "Pflegefachmann/-frau")[0] == "pflegefachkraft"
    assert classify_role("Pflegefachkraft / Co-Therapeut für Psychosomatische Station (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"
    assert classify_role("Atmungstherapeuten (DGP) (w/m/d) oder Gesundheits- und Krankenpfleger (w/m/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"

def test_pay_grade_and_requirements():
    e = enrich_description("Ihr Profil: abgeschlossene Ausbildung als Pflegefachkraft, mindestens 2 Jahre Berufserfahrung auf Intensivstation. Wir bieten: Vergütung nach TVöD-K Entgeltgruppe P 8 sowie Personalwohnungen.")
    assert e["pay_grade"] == "P8" and e["housing"] and e["tariff"] == "TVöD"
    assert e["requirements"].startswith("abgeschlossene Ausbildung als Pflegefachkraft")
    assert "Berufserfahrung" in e["experience"] and "Vergütung nach TVöD-K" in e["pay_text"]
    assert enrich_description("Eingruppierung gemäß TV-L in KR 8.")["pay_grade"] == "KR8"
    assert enrich_description("Bus P 8 fährt zur Klinik. Vergütung nach Tarif.")["pay_grade"] is None

def test_ausbildung_requires_pflege_token():
    assert classify_role("Auszubildende zum Elektroniker (m/w/d)", "", "AUSBILDUNG")[0] == "nicht_pflege"
    assert classify_role("Auszubildende zum Medizinischen Fachangestellten MFA (m/w/d)", "", "AUSBILDUNG")[0] == "nicht_pflege"
    assert classify_role("Ausbildung Pflegefachmann/-frau 2027", "Pflegefachmann/-frau (Ausbildung)", "AUSBILDUNG")[0] == "ausbildung"
    assert classify_role("Auszubildende zum Anästhesietechnischen Assistenten ATA (m/w/d)", "", "AUSBILDUNG")[0] == "ausbildung"

def test_ward_leadership_without_pflege_token():
    assert classify_role("Organisatorische Teamleitung (m/w/d) für die orthopädische Station mit Schwerpunkt Akutgeriatrie", "")[0] == "leitung"
    assert classify_role("Organisatorische Teamleitung (m/w/d) für den OP-Bereich", "")[0] == "leitung"
    assert classify_role("Medizinische Fachangestellte (m/w/d)", "")[0] == "nicht_pflege"
    assert classify_role("Teamleitung Buchhaltung (m/w/d)", "")[0] == "nicht_pflege"

def test_canonical_ref():
    from pflege_jobs.cli import canonical_ref
    assert canonical_ref("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579-needle-nurse-m-w-d-in-teilzeit") == canonical_ref("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579")
    assert canonical_ref("https://x.de/job/1/?utm=a#top") == "https://x.de/job/1"
