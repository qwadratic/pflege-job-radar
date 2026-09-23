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
