import pytest

from pflege_jobs.classify import classify_role
from pflege_jobs.config import EXCLUDED_ROLE_CLASSES
from pflege_jobs.mechanics import get


def test_roles():
    cases = [("Pflegefachkraft (m/w/d)", "Gesundheits- und Krankenpfleger/in", "pflegefachkraft"),
             ("Stationsleitung (m/w/d)", "Stationsleiter/in - Kranken-/Alten-/Kinderkrankenpflege", "leitung"),
             ("Fachkrankenpfleger Intensiv (m/w/d)", "Gesundheits- und Krankenpfleger/in", "fachpflege"),
             ("Pflegefachkraft Intensivstation (m/w/d)", "Gesundheits- und Krankenpfleger/in", "pflegefachkraft"),
             ("Pflegehelfer (m/w/d)", "Altenpflegehelfer/in", "pflegehelfer"),
             # 2026-09-09: Firecrawl (compare_adapter_fc.py, clinic 46101) found this plural slipping through
             # as sonstige_pflege -- "pflegehilfskraft" didn't match the umlaut plural "pflegehilfskräfte".
             ("Pflegehilfskräfte (m/w) für ambulant, teilstationär und stationär", "", "pflegehelfer"),
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
             # TASK-186: teaching staff is not nursing (this row said apn_experte until 2026-09-30)
             ("Pflege- oder Medizinpädagoge mit Bachelor-Abschluss (m/w/d)", "Lehrkraft - Schulen im Gesundheitswesen", "nicht_pflege"),
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
    # TASK-89 M8: "OP Leitung" carries no gate token (no "pfleg", no hyphenated "op-bereich") even
    # though the _ROLES leitung rule already recognises standalone "Leitung" as its own word -- the
    # gate blocked it before the role rule ever ran. Fixed with a gate token scoped to "op leit(ung|er)"
    # rather than a bare standalone "leitung"/"leiter" (that bare form was tried first and reverted:
    # it made the gate self-admitting -- match leitung to pass the gate, match leitung again in _ROLES
    # -- so ANY "X Leitung" title got stored, 41 of them real non-nursing roles when replayed against
    # crawl_output/run_*.jsonl's ~528 distinct 'leit*' titles; see the negative pins below, which are
    # real titles from that replay and go red against the bare-word gate).
    assert classify_role("OP Leitung (m/w/d)", "")[0] == "leitung"
    assert classify_role("OP-Leitung (m/w/d)", "")[0] == "leitung"
    # Negative pins: a standalone "Leitung"/"Leiter" with no OP/nursing context must NOT put a title
    # in policy on its own -- "leitung" is not in excluded_role_classes, so a false admission here is
    # stored and served as an open nursing posting. All four are real titles (crawl_output/run_*.jsonl,
    # 2026-09-22 replay); the pre-existing "Leitung Restaurant"/"AEMP-Leitung" pins elsewhere in this
    # file do NOT cover this -- both are caught by nicht_pflege keywords ("restaurant"/"aemp"), not by
    # the gate, so they stayed green even while the gate itself was too permissive.
    assert classify_role("Ärztliche Leitung (m/w/d)", "")[0] == "nicht_pflege"          # Bad Reichenhall: a physician role
    assert classify_role("Leiter (m/w/d) Technik Region AMEOS Süd", "")[0] == "nicht_pflege"
    assert classify_role("Leitung Recht (m/w/d)", "")[0] == "nicht_pflege"
    assert classify_role("Leiter des Klinikums hört auf", "")[0] == "nicht_pflege"       # Klinikum Memmingen news headline, not a posting


def test_medizinische_fachangestellte_inflected_forms_stay_excluded():
    # TASK-89 M8/M9: the pattern was "medizinische/?r? fachangestellte" -- an exact "medizinische"
    # immediately followed by " fachangestellte", so the common dative/accusative inflection
    # ("Medizinischen Fachangestellten") never matched at all. Harmless while the pflege_gate also
    # rejects the title outright (as here), but a real leak wherever something else already gets the
    # title past the gate -- clinic 47701's live posting title below contains "Station 11", which
    # passes the gate on its own, and the row was stored as sonstige_pflege (kept) before this fix.
    assert classify_role("Medizinischen Fachangestellten (m/w/d)", "")[0] == "nicht_pflege"
    assert classify_role(
        "Medizinischen Fachangestellten (m/w/d) für den ambulanten OP/Station 11 in Voll-/Teilzeit", ""
    ) == ("nicht_pflege", "nicht_pflege:medizinischen fachangestellten")
    # section-confirmed path (dept label rescues the gate, same as TASK-89 M8's "Onkologische
    # Fachkraft" case) must still exclude an MFA on the inflected form, exactly like the un-inflected
    # form already does in test_classify_section.py's competing-occupations test.
    assert classify_role("Medizinischen Fachangestellten (m/w/d)", "", nursing_section_confirmed=True)[0] == "nicht_pflege"


# OP-Fachkraft is OP nursing written without the word "Pflege"; neighbouring titles (MFA, Stationsassistenz) stay excluded.
def test_op_fachkraft_is_nursing_neighbours_are_not():
    assert classify_role("OP-Fachkraft (m/w/d) für unser Flexteam-OP")[0] == "fachpflege"
    assert classify_role("OP-Fachkräfte (m/w/d) für das AOZ Holzkirchen")[0] == "fachpflege"
    for t in ["Medizinische Fachangestellte (m/w/d) für die Endoskopie", "Stationsassistenz (m/w/d) der Geriatrie in Teilzeit", "Facharzt (m/w/d) Innere Medizin"]:
        assert classify_role(t)[0] in EXCLUDED_ROLE_CLASSES, t


# 2026-09-18 crawler review: pflege_gate missing tokens its own kept rules matched on, dropping
# real nursing postings before they ever reached the _ROLES loop.
def test_pflege_gate_recognises_the_previously_missing_tokens():
    cases = [("Hygienefachkraft (m/w/d)", "apn_experte"),
             ("Hygienebeauftragte (m/w/d) Pflege", "apn_experte"),
             ("Dauernachtwache (m/w/d)", "pflegefachkraft"),
             ("Nachtwache (m/w/d) gesucht", "pflegefachkraft"),
             ("Advanced Practice Nurse (m/w/d) Onkologie", "apn_experte"),
             ("Betreuungskräfte (m/w/d) gesucht", "sonstige_pflege"),
             ("Fachkraft mit Fachweiterbildung Intensivpflege (m/w/d)", "fachpflege")]
    for title, want in cases:
        assert classify_role(title, "")[0] == want, title


# 2026-09-18 crawler review: patterns.json's first-match-wins rules had pflegehelfer before
# pflegefachkraft, so a title naming both qualifications was excluded as a helper.
def test_a_title_offering_both_qualifications_is_classified_by_the_higher_one():
    cases = ["Pflegefachkraft (m/w/d) oder Pflegefachhelfer (m/w/d)",
             "Gesundheits- und Krankenpfleger/in oder Pflegehelfer/in (m/w/d)",
             "Pflegefachkraft / Pflegehelfer (m/w/d) für die Station"]
    for title in cases:
        assert classify_role(title, "")[0] == "pflegefachkraft", title
    # a title naming ONLY the helper qualification is still classified as a helper
    assert classify_role("Pflegehelfer (m/w/d)", "")[0] == "pflegehelfer"


# 2026-09-18 crawler review: strong_pflege led with the bare substring "pfleg", so a facility name
# containing "Pflege" (not a role) vetoed the whole nicht_pflege list for any title at that site.
def test_strong_pflege_no_longer_overrides_on_a_facility_name_alone():
    cases = ["Hauswirtschaftshilfe für das Pflegezentrum Bürgerheim Nördlingen (m/w/d)",
             "Reinigungskraft (m/w/d) im Pflegeheim",
             "Referent Pflegebuchhaltung (m/w/d)",
             "Teamleitung Controlling im Pflegebereich (m/w/d)"]
    for title in cases:
        assert classify_role(title, "")[0] == "nicht_pflege", title
    # the override itself still fires for a real dual-qualification title
    assert classify_role("MFA oder Pflegefachkraft (m/w/d) für die Ambulanz", "")[0] == "pflegefachkraft"


def test_mechanic_try_flags_excluded():
    r = get("role_class").run({"title": "MFA (m/w/d)", "hauptberuf": "", "offer_kind": ""})
    assert r["result"] == {"role_class": "nicht_pflege", "excluded": True}


# TASK-84 AC3: sonstige_pflege's fallback stops being a catch-all for any bare pflege_gate token --
# it now also requires the title to carry a posting-shaped signal (a gender marker). Decision: NOT
# adding sonstige_pflege to excluded_role_classes, because that would also drop the real, already-kept
# "Betreuungskräfte (m/w/d) gesucht" and section-confirmed "Gerontofachkraft (w/m/d)" cases just above
# (both still assert sonstige_pflege, unchanged) -- narrowing the fallback itself keeps those while
# dropping a bare mention of "pflegen" that carries no job-title shape at all.
def test_sonstige_pflege_fallback_requires_a_posting_shaped_title():
    # Klinikum Memmingen: classify_role('PFLEGEN KÖNNEN.') used to return sonstige_pflege/fallback,
    # which is how 31 news headlines on that board became open postings (confirmed live 2026-09-21).
    assert classify_role("PFLEGEN KÖNNEN.", "") == ("nicht_pflege", "fallback_no_posting_signal")
    # München Klinik (clinic 16201, confirmed live 2026-09-22): division-landing-page and marketing
    # <h1> headlines heuristically read as job titles by career_crawl -- none carry a gender marker.
    for title in ("Intensivpflege MACHEN KÖNNEN.", "IMC-Station: Wir sagen, wie es läuft", "#BildderPflege"):
        role, rule = classify_role(title, "")
        assert role == "nicht_pflege" and rule == "fallback_no_posting_signal", title
    # a real title that only reaches the fallback because of the pflegehelfer umlaut-plural gap (see
    # test_pflege_gate_recognises_the_previously_missing_tokens above) still keeps its marker and is
    # still kept -- this narrowing must not regress it.
    assert classify_role("Betreuungskräfte (m/w/d) gesucht", "")[0] == "sonstige_pflege"


# TASK-126: a speculative-application category link ("Initiativbewerbung <Rolle>", "Blitzbewerbung
# <Rolle>") is never a genuine open posting, no matter which role name it carries -- confirmed live
# 2026-09-23 that kbo-iak.de/kbo-lmk.de's own "Blitzbewerbung Pflegefachkräfte (m/w/d)" would otherwise
# match the plain pflegefachkraft substring rule exactly like a real posting.
def test_speculative_application_titles_never_classify_as_a_real_role():
    for title in (
        "Blitzbewerbung Pflegefachkräfte (m/w/d)",
        "Initiativbewerbung Pflegefachkraft (VZ/TZ)",
        "Initiativbewerbungen Assistenzärzte (m/w/d)",
        "Initiativbewerbung",
    ):
        assert classify_role(title, "") == ("nicht_pflege", "speculative_application"), title
    # a genuine posting that merely happens to carry the role token stays classified normally --
    # this check must not swallow real postings, only speculative-application titles.
    assert classify_role("Pflegefachkraft (m/w/d)", "")[0] == "pflegefachkraft"
    assert classify_role("Pflegefachkraft Intensivstation (m/w/d)", "")[0] == "pflegefachkraft"


# TASK-177 (Ivan, 2026-09-29): Kinderpfleger/-in (Kita), Heilerziehungspfleger/-in (HEP) and Landschafts-/
# Garten-/Parkpfleger are not nursing. strong_pflege's "pfleger\b|pflegerin\b|pflegerisch" matched inside
# those very words, so their nicht_pflege hit was overridden and _ROLES filed them as pflegefachkraft /
# apn_experte / leitung -- 110 open postings on 2026-09-29. Every title below is a real live posting title.
def test_hep_kinderpfleger_and_gardening_pfleger_titles_are_not_nursing():
    for title, rule in [
        ("Heilerziehungspfleger (m/w/d)", "nicht_pflege:heilerziehung"),                                 # 7274
        ("Heilerziehungspflegerin (m/w/d)", "nicht_pflege:heilerziehung"),                               # 7011
        ("Heilerziehungspflegerinnen und -pfleger (m/w/d)", "nicht_pflege:heilerziehung"),               # 14978
        ("Kinderpfleger (m/w/d) - Nürnberg", "nicht_pflege:kinderpfleger"),                              # 7063
        ("Kinderpfleger/in (m/w/d)", "nicht_pflege:kinderpfleger"),                                      # 14543
        ("Kinderpflegerin (m/w/d) für unsere neue Kinderwohngruppe", "nicht_pflege:kinderpfleger"),      # 14150
        ("Erzieher, Kinderpfleger, Sozialpädagoge (o. ä. Abschlüsse) (m/w/d)", "nicht_pflege:erzieher"),  # 13592, was apn_experte
        ("Heilerziehungspfleger / Erzieher / Heilpädagoge als Gruppenleitung Tagesförderstätte m/w/d",
         "nicht_pflege:heilerziehung"),                                                                  # 14355, was leitung
        ("Garten-und Landschaftspfleger (m/w/d)", "nicht_pflege:landschaftspflege"),                     # 14947
    ]:
        assert classify_role(title, "") == ("nicht_pflege", rule), title
    # Only helper roles left once "Kinderpfleger" no longer reads as pflegefachkraft (13937).
    assert classify_role("Heilerziehungspflegehelfer / Kinderpfleger / Altenpflegehelfer (m/w/d) für unsere "
                         "Wohneinrichtung Kloster Holzen", "") == ("pflegehelfer", "pflegehelfer:altenpflegehelfer")


def test_real_nursing_titles_next_to_hep_or_kinder_words_stay_nursing():
    for title, want in [
        ("Gesundheits- und Kinderkrankenpfleger (w/m/d)", "pflegefachkraft"),
        ("Kinderkrankenschwester (m/w/d)", "pflegefachkraft"),
        ("Kinderintensivpfleger (m/w/d)", "fachpflege"),
        ("Kinderkrankenpflegerinnen und -pfleger (m/w/d)", "pflegefachkraft"),                            # 14970
        ("Krankenschwester (m/w/d) Kinderpflege", "pflegefachkraft"),
        ("Altenpfleger (m/w/d)", "pflegefachkraft"),
        ("Pflegefachkraft (m/w/d)", "pflegefachkraft"),
        ("Pflegehelfer (m/w/d)", "pflegehelfer"),
        # a posting that also takes nurses stays a nursing posting (7091, 14989, 14011)
        ("Pflegefachkraft (m/w/d) / Heilerziehungspfleger (m/w/d) auf geringfügiger Basis - Wohnen Rothenburg",
         "pflegefachkraft"),
        ("Heilerziehungspfleger, Erzieher, Gesundheits- und Krankenpfleger, Altenpfleger oder Pflegefachmann*",
         "pflegefachkraft"),
        ("Sozial- oder Sonderpädagoge, Pfleger mit Schwerpunkt Psychiatrie, Heilerziehungspfleger (m/w/d)",
         "apn_experte"),
    ]:
        assert classify_role(title, "")[0] == want, title


# TASK-177 extension ("nursing jobs only"): catering, grounds, animal-keeper, cleaning, foot-care/cosmetics and
# childminder titles that the "pfleg" gate let in and the -pfleger precedence or the fallback kept. Live titles.
def test_catering_grounds_animal_cleaning_cosmetic_and_childminder_titles_are_not_nursing():
    for title, rule in [
        ("Servicemitarbeiter (m/w/d) in unserer Patientenverpflegung", "nicht_pflege:verpflegung"),           # 15315
        ("Betriebsassistenz (m/w/d) für Patientenbeherbergung und -verpflegung", "nicht_pflege:verpflegung"),  # 15316
        ("Beschäftigung im Zuverdienst – Garten- und Anlagepflege (w/m/d)", "nicht_pflege:anlagepflege"),      # 15317
        ("Tierpfleger (m/w/d) für die Keimfrei-Tierhaltung", "nicht_pflege:tierpfleg"),                        # 12297
        ("Examinierter Tierpfleger für die Forschung (m/w/d)", "nicht_pflege:tierpfleg"),                      # 12814
        ("Raumpfleger (m/w/d)", "nicht_pflege:raumpfleg"),                                                     # 12186
        ("Zimmermädchen / Roomboy / Raumpfleger (m/w/d)", "nicht_pflege:raumpfleg"),                           # 14880
        ("Kosmetiker und Fußpfleger (m/w/d) - Ganzjahresstelle", "nicht_pflege:kosmetik"),                     # 14963
        ("Kosmetikerin (m/w/d) medizinische Fußpflege und medizinische Kosmetik", "nicht_pflege:kosmetik"),    # 15071
        ("Qualifizierte Tagespflegeperson (m/w/d) für unsere nach Kneipp zertifizierte Kita in Kombination "
         "mit unserer Manufaktur", "nicht_pflege:tagespflegeperson"),                                          # 14946
    ]:
        assert classify_role(title, "") == ("nicht_pflege", rule), title
    # a nursing-section label skips the gate, the nicht_pflege terms still decide (live inbox titles)
    assert classify_role("Fußpflege - Klinikum Main-Spessart", "", nursing_section_confirmed=True) == ("nicht_pflege", "nicht_pflege:fußpfleg")
    assert classify_role("Podologe (m/w/d)", "", nursing_section_confirmed=True) == ("nicht_pflege", "nicht_pflege:podolog")
    # elderly day care ("Tagespflege") is nursing work and stays in its class
    for title, want in [("Pflegefachkraft (m/w/d) Tagespflege", "pflegefachkraft"),
                        ("Krankenpfleger in Stockstadt am Main, Tagespflege Am Hübnerwald", "pflegefachkraft"),
                        ("Pflegedienstleitung für die Tagespflege (m/w/d) als Krankheitsvertretung", "leitung"),
                        ("Betreuungskraft (m/w/d) Tagespflege", "pflegehelfer")]:
        assert classify_role(title, "")[0] == want, title


# TASK-186 problem 1 (judge verdicts 2026-09-30): an Ausbildung offered under a plain staff title. The title
# says "Operationstechnische Assistenten (m/w/d)"; only the body says it is a training place, so the title-only
# classifier filed it as a kept class (ota_ata, pflegefachkraft) and the board listed it as a job. The markers
# are what a training-place page states and a staff posting does not: the school-leaving certificate a school
# leaver needs, and a training start date. Each excerpt is the real body text of a live posting (posting id in
# the comment), abridged to the one passage that carries the marker, names and contact data dropped.
_TRAINING_PAGE_EXCERPTS = [
    ("ausbildungsbeginn", "Operationstechnischer Assistent (m/w/d)",                                # 12990
     "Ausbildungsbeginn Die Ausbildung beginnt immer am zweiten Dienstag im September und die Ausbildungszeit "
     "beträgt drei Jahre. Wir bieten… Eine abwechslungsreiche und umfassende Ausbildung Ein angenehmes "
     "Arbeitsklima in einem aufgeschlossenen Team"),
    ("hauptschulabschluss", "Operationstechnische Assistenten (m/w/d)",                             # 7376
     "Ihre Voraussetzungen für die Ausbildung Sie haben einen Hauptschulabschluss (oder gleichwertig) zusammen "
     "mit: einer erfolgreich abgeschlossenen, mindestens zweijährigen Berufsausbildung - oder- der Erlaubnis zur "
     "Führung der Berufsbezeichnung Krankenpflegehelfer/-in"),
    ("realschulabschluss", "Operationstechnischer Assistent (OTA) (m/w/d)",                         # 12327
     "Das solltest Du mitbringen: Realschulabschluss oder eine gleichwertige Schulbildung Belastbarkeit und "
     "Teamfähigkeit, um den Anforderungen im OP-Saal gerecht zu werden Interesse für den medizinischen Bereich"),
    ("mittelschulabschluss", "Pflegefachkraft (m/w/d)",                                             # 12646
     "Zulassungsvoraussetzungen Mindestalter 17 Jahre oder Mittelschulabschluss mit einer erfolgreich "
     "abgeschlossenen zweijährigen Berufsausbildung bzw. mit einjähriger Ausbildung zur staatlich anerkannten "
     "Pflegefachhilfe Gesundheitliche Eignung"),
    ("mittlere reife", "Pflegefachmann/-frau (m/d/w)",                                              # 12641
     "Persönliche Voraussetzungen Abgeschlossene 10-jährige Schulbildung (Mittlere Reife, Fachschulhochreife)"),
    ("mittlerer schulabschluss", "Ausbildungsplätze zur Pflegefachkraft (m/w/d)",                   # 15347
     "Aufnahmevoraussetzungen Gesundheitliche Eignung zur Ausübung des Pflegeberufes Mittlerer Schulabschluss "
     "oder eine andere gleichwertige, abgeschlossene Schulbildung"),
]

# Staff postings and boilerplate that mention "Ausbildung" without being one (all real, posting id in the comment).
_STAFF_PAGE_EXCERPTS = [
    ("profil", "Pflegefachkraft (m/w/d) oder Pflegefachhelfer (m/w/d) für Pflegestation",              # 12953
     "Ihr Profil Abgeschlossene Ausbildung zum Gesundheits- und Krankenpfleger (m/w/d) oder zur "
     "Pflegefachkraft (m/w/d) Hohe Sozialkompetenz und Kommunikationsfähigkeit Zuverlässigkeit und Teamfähigkeit"),
    ("ota_profil", "Operationstechnische Assistenz (OTA), OP-Kraft oder MFA mit Weiterbildung",       # 15244
     "Ihr Profil Abgeschlossene Ausbildung zur OTA, OP-Schwester/OP-Pfleger oder MFA mit entsprechender "
     "Weiterbildung oder Erfahrung im OP-Dienst Idealerweise einige Jahre Berufserfahrung"),
    ("praxisanleitung", "Freigestellte Praxisanleitung (m/w/d)",                                      # 5959
     "Ab 01.09.2026 ist mit Beginn des neuen Ausbildungsjahres eine Teilzeitstelle (30 Std./Woche) als "
     "Freigestellte Praxisanleitung (m/w/d) zu besetzen. Bitte bewerben Sie sich über den untenstehenden Link."),
    ("arbeitgeber", "Examinierte Pflegefachkraft (m/w/d) - Erlangen",                                 # 7036
     "Bei uns wird Ausbildung und Weiterbildung großgeschrieben: aktuell haben wir 22 Schüler*innen in drei "
     "Ausbildungsjahren. Für kostenlose Getränke (Wasser) ist natürlich jederzeit gesorgt."),
    ("hochschulabschluss", "Advanced Practice Nurse – (Endo-)Vaskuläre Chirurgie",                    # 6140
     "eine abgeschlossene dreijährige Pflegeausbildung Engagement und Begeisterung für die Pflege und deren "
     "Weiterentwicklung Dem Hochschulabschluss entsprechende gute Fach-, Methoden- und Sozialkompetenzen"),
    ("navigation", "Operationstechnischen Assistenten (m/w/d) sowie OP Fachkraft",                    # 6620
     "Jobs-mit Herz – Pflegekraft Initiativbewerbung Initiativbewerbung Ausbildungsplatz Kontakt Impressum"),
    ("bildungsinstitut", "Pflegefachkraft (m/w/d) für unsere interdisziplinäre Intensivstation",      # 6610
     "Das zugehörige Bildungsinstitut für Gesundheitsberufe bietet 120 Ausbildungsplätze für junge Menschen an."),
]


@pytest.mark.parametrize("marker, title, body", _TRAINING_PAGE_EXCERPTS, ids=[x[0] for x in _TRAINING_PAGE_EXCERPTS])
def test_an_ausbildung_under_a_staff_title_is_read_from_the_body(marker, title, body):
    assert classify_role(title, "")[0] != "ausbildung"                    # the title alone does not give it away
    role, rule = classify_role(title, "", desc=body)
    assert role == "ausbildung" and rule.startswith("ausbildung_body:"), (role, rule)


@pytest.mark.parametrize("why, title, body", _STAFF_PAGE_EXCERPTS, ids=[x[0] for x in _STAFF_PAGE_EXCERPTS])
def test_a_staff_posting_that_mentions_ausbildung_stays_what_its_title_says(why, title, body):
    role, rule = classify_role(title, "", desc=body)
    assert (role, rule) == classify_role(title, ""), (role, rule)


def test_without_a_body_the_title_alone_decides_as_before():
    assert classify_role("Operationstechnische Assistenten (m/w/d)", "")[0] == "ota_ata"
    assert classify_role("Operationstechnische Assistenten (m/w/d)", "", desc="")[0] == "ota_ata"
    assert classify_role("Operationstechnische Assistenten (m/w/d)", "", desc=None)[0] == "ota_ata"


def test_mechanic_try_reads_the_ad_text():
    r = get("role_class").run({"title": "Operationstechnische Assistenten (m/w/d)", "hauptberuf": "", "offer_kind": "",
                               "description": "Ihre Voraussetzungen für die Ausbildung Sie haben einen Hauptschulabschluss"})
    assert r["result"] == {"role_class": "ausbildung", "excluded": True}
    assert r["rule"].startswith("ausbildung_body:")


# TASK-186 problem 2 (judge verdicts 2026-09-30, "nonnursing_job"): clear non-nursing clusters that sat on the
# board as nursing roles because their title carries a nursing token ("Pflegepädagoge", "Pflege", "ATA", "Station")
# or a leadership word the pflege_gate accepts ("Bereichsleitung"). Every title is a real live posting title (posting
# id in the comment); a (title, hauptberuf) pair is given where the Arbeitsagentur occupation took part.
_NOT_NURSING_TITLES = [
    # teaching staff and school administration: patterns.json role.rules, ahead of ota_ata / hebamme / apn_experte
    ("teach_pflegepaedagoge", "Pflegepädagoge (m/w/d)", ""),                                                     # 10140
    ("teach_pflegepaedagoge_lehrkraft", "Pflegepädagoge / Lehrkraft für Pflegeberufe (m/w/d)", ""),               # 15087
    ("teach_lehrkraft", "Lehrkraft (m/w/d) für die Berufsfachschule für Pflege / Qualifikationsebene 3", ""),     # 10517
    ("teach_lehrkraft_ota", "Lehrkraft für OTA: Pflegepädagoge/ Medizinpädagoge/ Berufspädagoge (m/w/d)", ""),    # 12331
    ("teach_lehrkraft_ata", "Lehrkraft Berufsfachschule ATA-OTA - Bereich OTA (m/w/d)", ""),                     # 15358
    ("teach_medizinpaedagoge", "Pflege-/Medizinpädagoge (m/w/d)", ""),                                           # 6748
    ("teach_pflegepaedagoge_oder_lehrer", "Pflegepädagoge oder Lehrer für Pflegeberufe (w/m/d)", ""),            # 7331
    # the shape the brief names; no live posting carries it without a Pflegepädagoge next to it yet
    ("teach_lehrer_alone", "Lehrer für Pflegeberufe (m/w/d)", ""),
    ("teach_dozent", "Dozent:in (m/w/d) Basis Pflege", ""),                                                      # 15073
    ("teach_hauptberuf", "Pflege- oder Medizinpädagoge mit Bachelor-Abschluss (m/w/d)",
     "Lehrkraft - Schulen im Gesundheitswesen"),                                                                 # 10707
    ("school_lehrsekretariat", "Lehrsekretariat Hebammenwissenschaft", ""),                                      # 10519
    ("school_teamassistenz", "Teamassistenz ATA-OTA-Berufsfachschule", ""),                                      # 12834
    ("school_verwaltungskraft", "Verwaltungskraft (m/w/d) für die Berufsfachschule der Evangelischen PflegeAkademie", ""),  # 14911
    ("school_stundenplan", "Koordinator Unterrichtsplanung / Zentrale Stundenplanung Berufsfachschule Pflege (m/w/d)", ""),  # 6847
    ("school_leitung_der_akademie", "Leitung (m/w/d) der Akademie - Dienstleistungszentrum Bildung / Pflegeschulen", ""),     # 10503
    ("school_teamassistenz_akademie", "Teamassistenz der Akademieleitung für Gesundheits- und Pflegeberufe (m/w/d)", ""),   # 15243
    # medical assistants in the spellings the old pattern missed, physicians, doctors' secretaries
    ("mfa_slash_dash", "Medizinische/-r Fachangestellte/-r (m/w/d) für die Zentrale Notaufnahme", ""),            # 10446
    ("mfa_abbreviated", "Med. Fachangestellte (w/m/d) - für den Stützpunkt Notaufnahme", ""),                     # 10124
    ("mfa_funktionsdienst", "med. Fachangestellte/r im Funktionsdienst", ""),                                    # 14769
    ("arzt_gender_colon", "Ärzt:in in Weiterbildung - für die Station", ""),                                     # 12311
    ("arzt_leitung", "Stellvertretende ärztliche Leitung (m/w/d) für die Zentrale Notaufnahme", ""),              # 10440
    ("arzt_bereichsleitung", "Ärztliche Bereichsleitung MVZ Rheumatologie", ""),                                 # 14819
    ("arztsekretaer", "Arztsekretär (m/w/d)  Vorzimmer Station 34", ""),                                         # 12718
    # IT / EDV
    ("it_anwendungsbetreuer", "ORBIS Anwendungsbetreuer (m/w/d) - Schnittstelle Pflege & IT", ""),               # 10441
    ("it_edv", "Mitarbeiter in der Stabstelle EDV Pflege (m/w/d)", ""),                                          # 13636
    # DKG Fachweiterbildung course pages (the title is the course name, not a job)
    ("course_dkg_op", "Fachweiterbildung (DKG) Pflege im Operationsdienst", ""),                                 # 6931
    ("course_dkg_onko", "Fachweiterbildung (DKG) Pflege in der Onkologie", ""),                                  # 6932
    ("course_intensiv", "Fachweiterbildung für Intensiv- und Anästhesiepflege", ""),                             # 6635
    # not hospital work at all: aggregator boards, laboratory and sales titles that only pass the gate on a
    # leadership or ATA/OTA token
    ("retail_frischetheke", "Bereichsleiter Frischetheke (m/w/d)", ""),                                          # 15006
    ("retail_frischetheke_bereichsleitung", "Bereichsleitung Frischetheke (m/w/d)", ""),                         # 15000
    ("industry_steine_erden", "Bereichsleiter (m/w/d) Steine & Erden", ""),                                      # 14984
    ("industry_standortmanager", "Bereichsleiter Produktion / Standortmanager (m/w/d)", ""),                     # 14986
    ("sales_beratung_verkauf", "Fachbereichsleitung Beratung & Verkauf", ""),                                    # 15020
    ("petrol_station", "Verkäufer Stationsleitung (m/w/d) Tankstelle", ""),                                      # 14942
    ("radiology_ct", "Bereichsleitung Computertomographie (m/w/d)", ""),                                         # 6740
    ("radiology_mrt", "Bereichsleitung MRT (m/w/d)", ""),                                                        # 6741
    ("laboratory_blutbank", "MTL / Bereichsleitung Blutbank (m/w/d)", ""),                                       # 6864
    ("laboratory_laborant", "Laborant - Milchwirtschaftlicher Laborant, CTA, ATA, MTLA, PTA (m/w/d)", ""),       # 14944
    ("pharma_berater", "PTA, BTA, CTA, OTA oder MTLA als Pharmaberater / Pharmareferent im Innendienst (m/w/x)", ""),  # 14493
]

# Neighbours that name the same words and are nursing (or at least not part of the clusters above).
_STILL_NURSING_TITLES = [
    ("praxisanleiter_paedagogisch", "Praxisanleiter (m/w/d) - Pädagogisches Kompetenzzentrum", "praxisanleitung"),            # 7379
    ("practical_instruction_nurse", "Pflegefachkraft (m/w/d) für den Schwerpunkt Praxisbegleitung und fachpraktischem Unterricht",
     "pflegefachkraft"),                                                                                                       # 6428
    ("ward_team_assistant", "Teamassistentin (m/w/d) der Station 3 mit den Fachbereichen Geburtshilfe und Gynäkologie",
     "sonstige_pflege"),                                                                                                       # 13004
    # an office clerk outside a school: not part of the school cluster, left as it was for the owner (no body text)
    ("clerk_in_nursing_service", "Verwaltungskraft im Pflegedienst (m/w/d)", "sonstige_pflege"),                              # 14307
    ("job_with_fachweiterbildung", "Pflegefachkraft (m/w/d) mit Fachweiterbildung in der Psychiatrie, Psychosomatik und Psychotherapie",
     "fachpflege"),                                                                                                            # 11299
    ("job_with_fachweiterbildung_notfall", "Gesundheits- und Krankenpfleger (m/w/d) mit Fachweiterbildung Notfallpflege", "fachpflege"),  # 6082
    ("nursing_bereichsleitung", "Stellvertretende Bereichsleitung 5/6 (w/d/m)", "leitung"),                                   # 7326
    ("wohnbereichsleitung", "Wohnbereichsleitung (m/w/d)", "leitung"),                                                        # 13595
    ("nurse_or_mtra", "Gesundheits- und Krankenpfleger (m/w/d) oder MTRA (m/w/d) im Herzkatheter-Labor in Voll- oder Teilzeit",
     "pflegefachkraft"),                                                                                                       # 5858
    ("mfa_or_nurse", "MFA oder Pflegefachkraft (m/w/d) für die Ambulanz", "pflegefachkraft"),                                 # TASK-89
    ("hygienefachkraft", "Hygienefachkraft (m/w/d)", "apn_experte"),                                                          # 13200, owner decision
    ("stationsleitung", "Stationsleitung (m/w/d) Dialyse", "leitung"),                                                        # 6064
]


@pytest.mark.parametrize("why, title, hauptberuf", _NOT_NURSING_TITLES, ids=[x[0] for x in _NOT_NURSING_TITLES])
def test_clear_non_nursing_titles_are_not_nursing(why, title, hauptberuf):
    role, rule = classify_role(title, hauptberuf)
    assert role == "nicht_pflege" and rule.startswith("nicht_pflege:"), (role, rule)


@pytest.mark.parametrize("why, title, want", _STILL_NURSING_TITLES, ids=[x[0] for x in _STILL_NURSING_TITLES])
def test_neighbours_of_the_non_nursing_clusters_stay_nursing(why, title, want):
    assert classify_role(title, "")[0] == want, title
