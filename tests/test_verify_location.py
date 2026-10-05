"""pflege_jobs.verify: location extraction off a posting's own page (TASK-73 AC13).

  extract_location  a JSON-LD jobLocation list with more than one DISTINCT (city, plz) is a real
                     posting open at several sites at once (karriere.ge-passau.de) -- taking the
                     list's first entry silently attributed the wrong site to a posting that names
                     a different one.
  _clean_city        a JSON-LD addressLocality sometimes carries "PLZ City" as one field
                     (jobs.klinikum-gap.de) -- the PLZ must not ride along as part of the city name.
  _EINSATZORT        a bare (no colon/dash) "zum/zur/unsere/weitere/alle/andere Standort X" is
                     site-directory prose, not this posting's own location (klinikum-x.de: "zum
                     Standort Bremen" read as if it were the posting's own einsatzort).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.verify import extract_location, _clean_city  # noqa: E402


def _jsonld(body):
    return f'<html><script type="application/ld+json">{body}</script></html>'


def test_extract_location_rejects_a_jobposting_open_at_several_distinct_sites():
    html = _jsonld('{"@type": "JobPosting", "title": "x", "jobLocation": ['
                   '{"address": {"addressLocality": "Passau", "postalCode": "94032"}},'
                   '{"address": {"addressLocality": "Freyung", "postalCode": "94078"}}]}')
    assert extract_location(html) == (None, None, None)


def test_extract_location_keeps_a_jobposting_with_one_real_site_repeated():
    html = _jsonld('{"@type": "JobPosting", "title": "x", "jobLocation": ['
                   '{"address": {"addressLocality": "Passau", "postalCode": "94032"}},'
                   '{"address": {"addressLocality": "Passau", "postalCode": "94032"}}]}')
    assert extract_location(html) == ("Passau", "94032", "jsonld")


def test_extract_location_keeps_the_one_real_site_when_a_blank_entry_precedes_it():
    # A blank placeholder jobLocation (empty address object) ahead of the real one in the list must
    # not make the real address ambiguous, nor must it win over the real one by sitting at index 0.
    html = _jsonld('{"@type": "JobPosting", "title": "x", "jobLocation": ['
                   '{"address": {}}, {"address": {"addressLocality": "Passau", "postalCode": "94032"}}]}')
    assert extract_location(html) == ("Passau", "94032", "jsonld")


def test_extract_location_keeps_a_single_site_jobposting():
    html = _jsonld('{"@type": "JobPosting", "title": "x", '
                   '"jobLocation": {"address": {"addressLocality": "München", "postalCode": "80331"}}}')
    assert extract_location(html) == ("München", "80331", "jsonld")


def test_clean_city_strips_a_leading_plz_out_of_addresslocality():
    assert _clean_city("82467 Garmisch-Partenkirchen") == "Garmisch-Partenkirchen"


def test_clean_city_unaffected_when_there_is_no_leading_plz():
    assert _clean_city("Garmisch-Partenkirchen") == "Garmisch-Partenkirchen"


def test_extract_location_splits_a_plz_prefixed_addresslocality_via_jsonld():
    html = _jsonld('{"@type": "JobPosting", "title": "x", '
                   '"jobLocation": {"address": {"addressLocality": "82467 Garmisch-Partenkirchen", "postalCode": "82467"}}}')
    assert extract_location(html) == ("Garmisch-Partenkirchen", "82467", "jsonld")


def test_einsatzort_bare_site_directory_idiom_is_not_the_postings_own_location():
    html = "<p>Wir sind an vielen Orten fuer Sie da, z.B. zum Standort Bremen oder zur Standort Hamburg.</p>"
    assert extract_location(html) == (None, None, None)


def test_einsatzort_with_a_colon_label_still_matches():
    html = "<p>Einsatzort: Bremen</p>"
    assert extract_location(html) == ("Bremen", None, "einsatzort")


def test_einsatzort_bare_label_without_an_idiom_word_still_matches():
    html = "<p>Standort Bremen sucht Verstärkung.</p>"
    assert extract_location(html) == ("Bremen", None, "einsatzort")


def test_einsatzort_idiom_word_must_be_immediately_before_the_label_to_exclude_it():
    # "andere" sits three words back, not immediately before "Standort" -- this is an ordinary
    # sentence naming a real location, not the "andere Standort X" idiom shape.
    html = "<p>Sie arbeiten in einer anderen Abteilung am Standort Kiel.</p>"
    assert extract_location(html) == ("Kiel", None, "einsatzort")


# --- TASK-185: the posting's own place, read as the source states it -----------------------------------------
# Frozen real page slices (tests/fixtures/board_samples/README.md). Before this, extract_location read only
# JSON-LD and a case-sensitive, whitespace-flattened "Einsatzort" label: AMEOS and krankenpflegejobs24.de state
# the place as schema.org microdata (no JSON-LD JobPosting), kliniken-gz-kru.de writes "EINSATZORT:" in capitals
# with the value in the next block, and flattening the markup glued the next label onto the value
# ("Krumbach TÄTIGKEITSBEGINN", "Rödental Einrichtung").
def _fx(name):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples", name), encoding="utf-8") as f:
        return f.read()


def test_extract_location_reads_a_flat_microdata_joblocation_meta():
    # karriere.ameos.eu: <meta itemprop="jobLocation" content="Osnabrück" /> on every posting page
    assert extract_location(_fx("ameos_stelle_osnabrueck_sample.html")) == ("Osnabrück", None, "microdata")


def test_extract_location_reads_a_nested_microdata_postal_address():
    # krankenpflegejobs24.de: itemprop="jobLocation" > PostalAddress > postalCode / addressLocality
    assert extract_location(_fx("krankenpflegejobs24_bad_orb_sample.html")) == ("Bad Orb", "63619", "microdata")


def test_microdata_address_outside_the_joblocation_is_not_the_postings_place():
    # a ContactPoint / Organization address elsewhere on the page names the employer's office, not the job's place
    html = ('<div itemprop="jobLocation" itemscope><meta itemprop="name" content="Station 3"></div>'
            '<div itemprop="hiringOrganization"><address itemprop="address"><span itemprop="addressLocality">Fulda</span></address></div>')
    assert extract_location(html) == (None, None, None)


def test_einsatzort_label_matches_in_capitals_and_stops_at_the_end_of_its_own_block():
    # kliniken-gz-kru.de: <p class="fw-bold">EINSATZORT:</p><p>Krumbach</p> then the TÄTIGKEITSBEGINN block
    html = _fx("kliniken_gz_kru_einsatzort_sample.html") + "<p>TÄTIGKEITSBEGINN:</p><p>zum nächstmöglichen Zeitpunkt</p>"
    assert extract_location(html) == ("Krumbach", None, "einsatzort")


def test_einsatzort_value_in_the_next_sibling_block_is_read_up_to_its_slash():
    # innklinikum.de: <div class="offer-heading">Einsatzort:</div><div class="offer-text">Mühldorf am Inn / Bayern</div>
    assert extract_location(_fx("innklinikum_einsatzort_sample.html")) == ("Mühldorf am Inn", None, "einsatzort")


def test_einsatzort_value_does_not_run_into_the_next_list_item():
    # awo-omf.de: <li>Einsatzort: Rödental</li><li>Einrichtung: AWO Seniorenzentrum Rödental</li>
    assert extract_location(_fx("awo_omf_einsatzort_sample.html")) == ("Rödental", None, "einsatzort")


def test_a_label_word_inside_a_longer_word_is_not_a_label():
    # kbo.de, real prose: "...Klinik mit 305 Betten am Hauptstandort Ingolstadt, tagesklinische Plätze an den Standorten
    # Ingolstadt und Eichstätt..." must not make Ingolstadt the posting's place; the page's own label is the strong tag below it
    assert extract_location(_fx("kbo_hauptstandort_prose_sample.html")) == ("Taufkirchen", None, "einsatzort")


def test_einsatzort_value_ends_at_the_period_that_ends_its_sentence():
    # karriere.rottalinnkliniken.de, real prose: "...Räumlichkeiten am Standort Eggenfelden. Sie sind verantwortlich..." was read
    # as the place "Eggenfelden Sie" (the pronoun is the next sentence's first word)
    assert extract_location(_fx("rottal_standort_prose_sample.html")) == ("Eggenfelden", None, "einsatzort")


def test_a_place_name_may_continue_after_the_abbreviations_real_place_names_contain():
    # "St." and the one-letter "a." / "b." / "d." / "i." / "v." are the period-ended words inside the 13,7k real names of
    # data/geo/gemeinden_de.csv ("St. Englmar", "Heuchelheim a. d. Lahn", "Schülp b. Rendsburg"); a capitalised word after
    # any other period starts a new sentence
    assert extract_location("<p>Einsatzort: St. Englmar</p>") == ("St Englmar", None, "einsatzort")
    assert extract_location("<p>Einsatzort: Bad Windsheim. Wir bieten Ihnen</p>") == ("Bad Windsheim", None, "einsatzort")
