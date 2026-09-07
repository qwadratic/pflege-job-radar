"""Exa career-site discovery: name/town cleanup, aggregator blocklist, ranking, accept(). Pure
functions -> no network (no Exa calls happen in this file)."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "crawlers"))

from career_discover_exa import (  # noqa: E402
    accept, affinity, build_write_back, clean_name, is_aggregator, is_true_prefix, tag_context, town_ok,
)


def test_clean_name_cuts_back_to_the_repeated_prefix():
    # the 51. Fortschreibung parse glued the site label + address + operator back onto the name
    assert clean_name("Schön Klinik Roseneck Prien am Chiemsee Schön Klinik Roseneck SE & Co.") == "Schön Klinik Roseneck"


def test_clean_name_leaves_a_clean_name_alone():
    assert clean_name("Klinikum Bayreuth") == "Klinikum Bayreuth"
    assert clean_name("") == ""


def test_town_ok_rejects_a_legal_form_fragment():
    assert town_ok("Co.") is False


def test_town_ok_accepts_a_real_town_and_the_haus_substring_does_not_trip_it():
    # Burghausen must not be rejected by some naive "contains -haus-" building-word filter.
    # "InnKlinikum Burghausen" is the clinic's own name -- the fast path: town is right there.
    assert town_ok("Burghausen", name="InnKlinikum Burghausen", registry_town_counts={"Burghausen": 1}) is True


def test_town_ok_needs_either_a_name_match_or_a_repeat_count():
    assert town_ok("Ammersee", name="Psychosomatische Klinik Windach", registry_town_counts={"Ammersee": 1}) is False
    assert town_ok("München", name="", registry_town_counts={"München": 54}) is True


def test_aggregator_blocklist():
    assert is_aggregator("https://www.stepstone.de/stellenangebote--Pflege--123.html")
    assert is_aggregator("https://de.indeed.com/jobs?q=pflege")
    assert not is_aggregator("https://www.klinikum-bayreuth.de/karriere")


def test_affinity_is_umlaut_aware():
    # "Schön" -> "schoen" must match the transliterated host, not just the raw umlaut
    on_own_domain = affinity("Schön Klinik Roseneck", "https://www.schoen-klinik.de/roseneck/karriere")
    on_unrelated_domain = affinity("Schön Klinik Roseneck", "https://www.stepstone.de/jobs/123")
    assert on_own_domain > on_unrelated_domain


def test_twin_match_prefix_needs_a_true_prefix_not_a_coincidental_one():
    # "Kreiskrankenhaus Wegscheid" and "Kreiskrankenhaus Schrobenhausen" share their first 12+
    # cleaned characters ("kreiskrankenhaus") purely because both are Kreiskrankenhaeuser
    assert not is_true_prefix("kreiskrankenhauswegscheid", "kreiskrankenhausschrobenhausen")
    # a genuine prefix relationship: the shorter cleaned name is a true prefix of the longer one
    assert is_true_prefix("adulaklinikoberstdorf", "adulaklinikoberstdorf")


def test_accept_true_for_a_bite_loader_page_even_with_zero_job_links():
    # b-ite career pages are a JS loader shell: no <a href="/job/..."> links in the raw HTML at
    # all, but the vendor host fingerprints instantly and the clinic's own name is on the page
    html = "<html><head><title>Karriere bei Klinikum Bayreuth</title></head><body>Lade Stellenangebote...</body></html>"
    ok, ats, ev = accept("https://jobs.b-ite.com/klinikum-bayreuth/de", html, "Klinikum Bayreuth", "Bayreuth")
    assert ok is True
    assert ats == "bite"


def test_accept_false_for_plain_marketing_copy_with_no_vendor_and_no_listing():
    html = "<html><body><h1>Karriere</h1><p>Wir freuen uns auf Ihre Bewerbung per Post.</p></body></html>"
    ok, ats, ev = accept("https://www.klinik-x.de/karriere", html, "Klinik X", "Musterstadt")
    assert ok is False


# ---------------------------------------------------------------------------
# write-back guard: careers_url-only-if-blank, ats_type gated on vendor confirmation
# ---------------------------------------------------------------------------
def test_tag_context_distinguishes_script_and_iframe_from_a_bare_href():
    html = '<p>before</p><script>var x = "concludis.de";</script><p>after href="concludis.de" here</p>'
    pos_in_script = html.index("concludis.de")
    assert tag_context(html, pos_in_script) == "script"

    html2 = '<iframe src="https://x.softgarden.io/widgets/jobs"></iframe>'
    pos_in_iframe = html2.index("softgarden.io")
    assert tag_context(html2, pos_in_iframe) == "iframe"

    html3 = '<p>Siehe <a href="https://waldze.pi-asp.de/bewerber-web/">Stellenangebote</a></p>'
    pos_in_href = html3.index("pi-asp.de")
    assert tag_context(html3, pos_in_href) is None


def _probe_row(clinic_id, ats, careers_url, evidence=None):
    return {"kind": "probe", "payload": {"probe": "ats_discovery", "clinic_id": clinic_id, "ats": ats,
                                          "careers_url": careers_url, "evidence": evidence, "angle": "exa:A"}}


def test_build_write_back_writes_careers_url_only_when_stored_is_blank():
    live = {"1": {"clinic_id": "1", "name": "Klinik Eins", "careers_url": "", "ats_type": ""}}
    rows = [_probe_row("1", None, "https://klinik-eins.de/karriere")]
    out, stats = build_write_back(rows, live, log=lambda *a, **k: None)
    assert stats["careers_url_written"] == 1
    assert len(out) == 1
    assert out[0]["careers_url"] == "https://klinik-eins.de/karriere"
    assert out[0]["clinic_id"] == "1"


def test_build_write_back_never_overwrites_a_non_empty_stored_ats_type():
    # clinic 66303's real shape: stored softgarden, a (weaker) discovered label must not win
    live = {"1": {"clinic_id": "1", "name": "X", "careers_url": "https://x.de/jobs", "ats_type": "softgarden"}}
    rows = [_probe_row("1", "concludis", "https://x.de/jobs", evidence="<script>concludis.de</script>")]
    out, stats = build_write_back(rows, live, log=lambda *a, **k: None)
    assert stats["ats_conflict_kept_stored"] == 1
    assert out == []                                     # nothing changed: careers_url already stored, ats_type protected


def test_build_write_back_rejects_a_vendor_label_seen_only_in_a_bare_href(monkeypatch):
    import career_discover_exa as cde

    class FakeResp:
        ok = True
        url = "https://www.sana.de/karriere/coburg/"
        text = ('<p>Bewerben Sie sich hier: <a href="https://logaallin.regiomed-kliniken.de/'
                'bewerber-web/?companyEid=%2a">Stellenangebote</a></p>')
        status_code = 200

    monkeypatch.setattr(cde, "get", lambda url, session=None: FakeResp())
    live = {"1": {"clinic_id": "1", "name": "Sana Klinikum Coburg", "careers_url": "", "ats_type": ""}}
    rows = [_probe_row("1", "pi_asp", "https://www.sana.de/karriere/coburg/", evidence="...bewerber-web...")]
    out, stats = build_write_back(rows, live, log=lambda *a, **k: None)
    assert stats["ats_rejected_bare_evidence"] == 1
    assert out[0]["ats_type"] == ""                       # careers_url still written -- only the label is gated
    assert out[0]["careers_url"] == "https://www.sana.de/karriere/coburg/"


def test_build_write_back_accepts_a_vendor_label_confirmed_inside_a_script_tag(monkeypatch):
    import career_discover_exa as cde

    class FakeResp:
        ok = True
        url = "https://rotkreuzklinikum-muenchen.de/stellenangebote/"
        text = "<script>(function(w,d,s,id,url){})(window,document,'script','concludis','swmbrk.concludis.de');</script>"
        status_code = 200

    monkeypatch.setattr(cde, "get", lambda url, session=None: FakeResp())
    live = {"1": {"clinic_id": "1", "name": "Rotkreuzklinikum", "careers_url": "", "ats_type": ""}}
    rows = [_probe_row("1", "concludis", "https://rotkreuzklinikum-muenchen.de/stellenangebote/")]
    out, stats = build_write_back(rows, live, log=lambda *a, **k: None)
    assert stats["ats_type_written"] == 1
    assert out[0]["ats_type"] == "concludis"
