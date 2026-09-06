from pflege_jobs.classify import classify_role
from pflege_jobs.config import EXCLUDED_ROLE_CLASSES
from pflege_jobs.mechanics import get


def test_roles():
    cases = [("Pflegefachkraft (m/w/d)", "Gesundheits- und Krankenpfleger/in", "pflegefachkraft"),
             ("Stationsleitung (m/w/d)", "Stationsleiter/in - Kranken-/Alten-/Kinderkrankenpflege", "leitung"),
             ("Fachkrankenpfleger Intensiv (m/w/d)", "Gesundheits- und Krankenpfleger/in", "fachpflege"),
             ("Pflegefachkraft Intensivstation (m/w/d)", "Gesundheits- und Krankenpfleger/in", "pflegefachkraft"),
             ("Pflegehelfer (m/w/d)", "Altenpflegehelfer/in", "pflegehelfer"),
             ("Hebamme (m/w/d)", "Hebamme/Entbindungspfleger", "hebamme"),
             ("OTA (m/w/d)", "Operationstechnische/r Assistent/in", "ota_ata"),
             ("Praxisanleiter (m/w/d)", "Praxisanleiter/in - Pflegeberufe", "praxisanleitung"),
             ("Ausbildung Pflegefachkraft", "Pflegefachmann/-frau (Ausbildung)", "ausbildung"),
             ("Notfallsanitäter (m/w/d)", "Notfallsanitäter/in", "nicht_pflege"),
             ("MFA (m/w/d)", "Medizinische/r Fachangestellte/r", "nicht_pflege"),
             ("Pflegefachkraft Notaufnahme (m/w/d)", "Gesundheits- und Krankenpfleger/in", "pflegefachkraft"),
             ("Werkstudent Pflege (m/w/d)", "Gesundheits- und Krankenpfleger/in", "werkstudent_praktikum"),
             ("Pflegedienstleitung (m/w/d)", "Pflegedienstleiter/in", "leitung"),
             ("Advanced Practice Nurse Onkologie", "Gesundheits- und Krankenpfleger/in", "apn_experte")]
    for title, hb, want in cases:
        assert classify_role(title, hb)[0] == want, title


def test_offer_kind_forces_training_but_needs_pflege_token():
    assert classify_role("Ausbildung Pflegefachmann/-frau 2027", "Pflegefachmann/-frau (Ausbildung)", "AUSBILDUNG")[0] == "ausbildung"
    assert classify_role("Auszubildende zum Anästhesietechnischen Assistenten ATA (m/w/d)", "", "AUSBILDUNG")[0] == "ausbildung"
    assert classify_role("Auszubildende zum Elektroniker (m/w/d)", "", "AUSBILDUNG")[0] == "nicht_pflege"
    assert classify_role("Auszubildende zum Medizinischen Fachangestellten MFA (m/w/d)", "", "AUSBILDUNG")[0] == "nicht_pflege"


def test_audit_fixes():
    cases = [("Alltagsbegleiter (m/w/d)", "Betreuungskraft / Alltagsbegleiter/in", "pflegehelfer"),
             ("Bildungsbegleiter (m/w/d) im Jugendbereich", "Pflegedienstleiter/in", "nicht_pflege"),
             ("Schulleiter/in", "Pflegedienstleiter/in", "nicht_pflege"),
             ("Stationsleitung – Anästhesiologische operative Intensivstation", "Wachleiter/in - Rettungsdienst", "leitung"),
             ("Pflegeüberleitung im Sozial- & Entlassmanagement (m/w/d)", "Gesundheits- und Krankenpfleger/in", "pflegefachkraft"),
             ("Leitung Restaurant (m/w/d), Medical Park Bad Rodach", "Pflegedienstleiter/in", "nicht_pflege"),
             ("Versorgungsassistenz (m/w/d) Neurologie Phase B", "Pflegefachassistent/in", "pflegehelfer"),
             ("Pflegeunterstützungskraft (w/m/d)", "Bürokaufmann/-frau", "pflegehelfer"),
             ("Mitarbeiter (m/w/d) im Hol- u. Bringedienst", "Schwestern-/Pflegediensthelfer/in", "nicht_pflege"),
             ("Stellvertretende AEMP-Leitung (m/w/d)", "Gesundheits- und Krankenpfleger/in", "nicht_pflege"),
             ("Pflege- oder Medizinpädagoge mit Bachelor-Abschluss (m/w/d)", "Lehrkraft - Schulen im Gesundheitswesen", "apn_experte"),
             ("Kommissarische Funktionsleitung Anästhesiepflege (m/w/d)", "Gesundheits- und Krankenpfleger/in", "leitung"),
             ("Leiter - Pflege (m/w/d)", "Pflegedienstleiter/in", "leitung")]
    for title, hb, want in cases:
        assert classify_role(title, hb)[0] == want, title


def test_physicians_are_not_nursing():
    assert classify_role("Facharzt (m/w/d) Anästhesie und Intensivmedizin", "Fachkinderkrankenpfleger/in - Intensivpflege/Anästhesie")[0] == "nicht_pflege"
    assert classify_role("Chefarzt (m/w/d) der Klinik für Anästhesiologie", "Fachkrankenpfleger/in - Intensivpflege/Anästhesie")[0] == "nicht_pflege"
    assert classify_role("Psychologe (m/w/d) - Psychosomatik", "Fachkinderkrankenpfleger/in - Psychiatrie")[0] == "nicht_pflege"
    assert classify_role("Pflegefachfrau / Pflegefachmann (m/w/d) für unsere psychotherapeutische Station", "Pflegefachmann/-frau")[0] == "pflegefachkraft"
    assert classify_role("Pflegefachkraft / Co-Therapeut für Psychosomatische Station (m/w/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"
    assert classify_role("Atmungstherapeuten (DGP) (w/m/d) oder Gesundheits- und Krankenpfleger (w/m/d)", "Gesundheits- und Krankenpfleger/in")[0] == "pflegefachkraft"


def test_ward_leadership_without_pflege_token():
    assert classify_role("Organisatorische Teamleitung (m/w/d) für die orthopädische Station mit Schwerpunkt Akutgeriatrie", "")[0] == "leitung"
    assert classify_role("Organisatorische Teamleitung (m/w/d) für den OP-Bereich", "")[0] == "leitung"
    assert classify_role("Medizinische Fachangestellte (m/w/d)", "")[0] == "nicht_pflege"
    assert classify_role("Teamleitung Buchhaltung (m/w/d)", "")[0] == "nicht_pflege"


# OP-Fachkraft is OP nursing written without the word "Pflege"; neighbouring titles (MFA, Stationsassistenz) stay excluded.
def test_op_fachkraft_is_nursing_neighbours_are_not():
    assert classify_role("OP-Fachkraft (m/w/d) für unser Flexteam-OP")[0] == "fachpflege"
    assert classify_role("OP-Fachkräfte (m/w/d) für das AOZ Holzkirchen")[0] == "fachpflege"
    for t in ["Medizinische Fachangestellte (m/w/d) für die Endoskopie", "Stationsassistenz (m/w/d) der Geriatrie in Teilzeit", "Facharzt (m/w/d) Innere Medizin"]:
        assert classify_role(t)[0] in EXCLUDED_ROLE_CLASSES, t


def test_mechanic_try_flags_excluded():
    r = get("role_class").run({"title": "MFA (m/w/d)", "hauptberuf": "", "offer_kind": ""})
    assert r["result"] == {"role_class": "nicht_pflege", "excluded": True}
