"""Second-pass ATS discovery: fingerprinting and the name matcher. Pure functions -> no network."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawlers"))

from ats_discover2 import _match_name, base_of, fingerprint   # noqa: E402


def test_fingerprint_prefers_url_over_body():
    """A vendor host in the URL is stronger proof than whatever the page body happens to contain."""
    ats, _ = fingerprint('<a href="https://klinik.jobs.personio.de/">Stellen</a>',
                         "https://foo.softgarden.io/vacancies")
    assert ats == "softgarden"                     # URL is checked first and wins


def test_fingerprint_needs_a_domain_not_a_passing_mention():
    """"We use personio" in prose must not label the site — only a real vendor domain counts."""
    assert fingerprint("<html>we also mention personio somewhere</html>", "") == (None, None)
    assert fingerprint('<a href="https://klinik.jobs.personio.de/">x</a>', "")[0] == "personio"


def test_fingerprint_detects_common_vendors():
    cases = {
        "softgarden": '<iframe src="https://josef.softgarden.io/job/1/">',
        "umantis": '<iframe src="https://recruitingapp-5556.de.umantis.com/Jobs/1">',
        "dvinci": '<a href="https://klinik.dvinci-hr.com/de/jobs">Stellen</a>',
        "mein-check-in": '<a href="https://www.mein-check-in.de/klinik/overview">',
        "oracle": '<a href="https://x.oraclecloud.com/hcmUI/CandidateExperience/de/sites/CX">',
        "personio": '<a href="https://klinik.jobs.personio.de/">',
        "interamt": '<a href="https://www.interamt.de/koop/app/stelle?id=1">',
        "concludis": '<a href="https://ukr.concludis.de/jobs">',
    }
    for want, html in cases.items():
        got, ev = fingerprint(html, "")
        assert got == want, "%s -> %s" % (want, got)
        assert ev                                   # evidence is always recorded for audit


def test_fingerprint_returns_none_for_plain_cms():
    assert fingerprint("<html><body><h1>Karriere</h1><p>Bewerbung per Post</p></body></html>", "") == (None, None)


def test_base_of():
    assert base_of("https://x.de/karriere/stellen?a=1") == "https://x.de"
    assert base_of("") is None
    assert base_of("not-a-url") is None


def test_match_name_ignores_legal_forms_and_generic_words():
    # "Klinikum"/"GmbH" are stripped, so matching rests on the distinctive tokens
    assert _match_name("Klinikum Bayreuth GmbH", "Klinikum Bayreuth")
    assert _match_name("Krankenhaus St. Josef Schweinfurt", "St. Josef Schweinfurt gGmbH")
    assert not _match_name("Klinikum Bayreuth", "Klinikum Augsburg")
    # two clinics that share only the generic words must not match
    assert not _match_name("Klinik am See", "Krankenhaus der Stadt")
