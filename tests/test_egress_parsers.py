from crawlers.claude_egress import parse_stepstone, parse_jsonld_lines, parse_generic_links
MD = """[Hessing Stiftung Hessing Stiftung](https://www.stepstone.de/cmp/de/hessing-stiftung-179319/jobs)

## [Pflegefachkraft (m/w/d) für die Notaufnahme](https://www.stepstone.de/stellenangebote--Pflegefachkraft-m-w-d-fuer-die-Notaufnahme-Augsburg-Hessing-Stiftung--14429796-inline.html)

Hessing Stiftung

Augsburg

Gehalt anzeigen

[TUM Klinikum Rechts der Isar TUM Klinikum Rechts der Isar](https://www.stepstone.de/cmp/de/tum-88535/jobs)

## [Pflegefachkraft – Neonatologische Überwachungsstation](https://www.stepstone.de/stellenangebote--Pflegefachkraft-Neonatologische-Ueberwachungsstation-Muenchen-TUM--14418027-inline.html)

TUM Klinikum Rechts der Isar

München
"""
HEL = 'Text {"hiringOrganization":{"name":"Helios"},"jobLocation":{"address":{"addressCountry":"DE","streetAddress":"Steinerweg 5","addressLocality":"München","postalCode":"81241"}},"employmentType":"Vollzeit,Teilzeit","description":"in Voll- oder Teilzeit.","title":"Gesundheits- und Krankenpfleger / Pflegefachkraft mit Weiterbildung Notfallpflege (m/w/d)","datePosted":"2025-12-26"} end'
def test_stepstone():
    j = parse_stepstone(MD, "https://www.stepstone.de/jobs/pflegefachkraft/in-bayern?page=1")
    assert [x["org"] for x in j] == ["Hessing Stiftung", "TUM Klinikum Rechts der Isar"] and j[0]["loc"][0]["city"] == "Augsburg"
def test_jsonld_line():
    j = parse_jsonld_lines(HEL, "https://www.helios-gesundheit.de/karriere/job/x/")
    assert j[0]["loc"][0]["plz"] == "81241" and j[0]["title"].startswith("Gesundheits-") and j[0]["datePosted"] == "2025-12-26"
def test_generic_links():
    assert len(parse_generic_links("[Pflegefachhelfer (m/w/d)](https://x.de/karriere/job/abc/) and [Impressum](https://x.de/i)", "u")) == 1

def test_ats_fingerprint_from_external_links():
    from crawlers.claude_egress import parse_external_links, fingerprint_ats
    md = "[Jetzt bewerben](https://www.stepstone.de/apply/1) [Bewerben](https://hessing.softgarden.io/job/12345/Pflegefachkraft?l=de) [Impressum](https://www.hessing-kliniken.de/impressum)"
    links = parse_external_links(md)
    assert all("stepstone" not in u for u in links)
    assert fingerprint_ats(links) == ("softgarden", "https://hessing.softgarden.io/job/12345/Pflegefachkraft?l=de")
    assert fingerprint_ats(["https://jobs.b-ite.com/x", "https://a.de"])[0] == "bite" and fingerprint_ats(["https://a.de"]) == (None, None)

def test_exa_text_parsers():
    from crawlers.claude_egress import parse_stepstone_text, parse_stepstone_detail
    md = "# 69 Treffer für Pflegefachkraft Jobs in Regensburg\n\nRelevanz\n\n## Study Nurse / Study Nurse (m/w/d) in TZ 20 Std./Woche\n\nBarmherzige Brüder gemeinnützige Krankenhaus GmbH\n\nRegensburg\n\nGehalt anzeigen\n\nDas Krankenhaus ...\n\nmehr\n\nvor 4 Tagen\n\n## Pflegefachkraft (w/m/d)\n\nKorian Deutschland GmbH\n\nObertraubling\n\nGehalt anzeigen\n\nAnschreiben nicht erforderlich\n\ntext\n\nvor 4 Tagen\n"
    j = parse_stepstone_text(md, "https://www.stepstone.de/jobs/pflegefachkraft/in-regensburg")
    assert [x["org"] for x in j] == ["Barmherzige Brüder gemeinnützige Krankenhaus GmbH", "Korian Deutschland GmbH"] and j[0]["age"] == "vor 4 Tagen"
    d = parse_stepstone_detail("Pflegefachkraft ... Job in Augsburg\n\n# Pflegefachkraft (m/w/d) für die Notaufnahme\n\nGehalt anzeigen\n\n- Hessing Stiftung\n- Augsburg\n- Feste Anstellung\n- Vollzeit\n- Erschienen: vor 1 Woche\n\n#### Wir bieten\n\n- Günstige Betriebswohnungen\n", "https://www.stepstone.de/x")
    assert d["org"] == "Hessing Stiftung" and d["employmentType"] == "Vollzeit" and "Betriebswohnungen" in d["description"]
