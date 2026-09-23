"""Adapter-specific completeness regressions for group_portal (TASK-36) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live run found that the generic checks can't exercise offline (mocked HTTP via
crawlers.vendor_adapters.get, no network):

  career-facts fallback   karriere.barmherzige.net's job pages carry no JobPosting JSON-LD at all
                          (only a generic WebSite/Organization graph) -- parse_job_page's non-JSON-LD
                          fallback took title/description but dropped employmentType/city even though
                          the page states them plainly in a <ul class="career-facts"> icon+label list
                          -- verified missing live 2026-09-10 (0/84 rows on barmherzige-regensburg.de,
                          klinikum-straubing.de, barmherzige-bieten-zukunft.de before the fix).
  pagination cap          GROUP_PORTALS carried a "pages": 12 cap on both kbo.de and
                          karriere.barmherzige.net -- a self-invented stop instead of the board's own
                          end signal (an empty page). Removed; the loop now only stops there.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402


def test_group_portal_reads_employment_type_and_city_from_career_facts_when_no_jsonld(monkeypatch):
    g = {"match": "barmherzige", "list": "https://karriere.barmherzige.net/jobs/",
         "page_param": "c_page",
         "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"}
    c = {"name": "St. Barbara Krankenhaus Schwandorf",
         "careers_url": "https://www.barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse"}
    detail_url = "https://karriere.barmherzige.net/jobs/pflegefachkraft-mwd-1"
    list_page = _R('<a href="%s">x</a>' % detail_url, url=g["list"], ok=True)
    detail = _R(
        '<h2>Pflegefachkraft (m/w/d)</h2>'
        '<ul class="career-facts">'
        '<li><img src="/icons/schedule.svg" class="career-icon" /><span class="fact">Vollzeit</span></li>'
        '<li><img src="/icons/location.svg" class="career-icon" /><span class="fact">Schwandorf</span></li>'
        '</ul><p>Beschreibung des Jobs.</p>', url=detail_url, ok=True)
    monkeypatch.setattr(va, "get", _router({g["list"]: list_page, detail_url: detail}))
    rows = va.crawl_group_portal(c, g)
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["employmentType"] == "Vollzeit"
    assert p["loc"][0]["city"] == "Schwandorf"


KBO_G = {"match": r"kbo-|kbo\.de", "list": "https://kbo.de/karriere/jobboerse", "page_param": "tx_solr[page]",
         "job_rx": r"https://kbo\.de/karriere/jobs/[^\"'\s>]+", "host": "kbo.de", "hq_location_untrusted": True,
         "title_city_rx": va.GROUP_PORTALS[0]["title_city_rx"]}
KBO_TOWNS = {"haar", "münchen"}


def _kbo_detail(title, jsonld_city="München", jsonld_plz="80538", body_extra=""):
    jsonld = ('<script type="application/ld+json">{"@type":"JobPosting","title":"%s",'
              '"jobLocation":{"address":{"addressLocality":"%s","postalCode":"%s"}}}</script>'
              % (title, jsonld_city, jsonld_plz))
    return jsonld + "<h1>%s</h1>%s" % (title, body_extra)


def test_kbo_never_carries_the_group_hq_address_through_as_the_posting_city(monkeypatch):
    """2026-09-18 crawler review: kbo.de's JSON-LD jobLocation is always the group's Munich HQ, on
    every posting, regardless of the real site -- crawl_group_portal must never treat it as this
    posting's own location."""
    list_url = KBO_G["list"]
    title_named = "Pflegefachhelfer (m/w/d) in Haar"
    detail_named = "https://kbo.de/karriere/jobs/1"
    title_einsatzort = "Pflegefachkraft (m/w/d)"
    detail_einsatzort = "https://kbo.de/karriere/jobs/2"
    title_unknown = "Pflegehelfer (m/w/d)"
    detail_unknown = "https://kbo.de/karriere/jobs/3"

    list_page = _R('<a href="%s"></a><a href="%s"></a><a href="%s"></a>' % (detail_named, detail_einsatzort, detail_unknown),
                   url=list_url, ok=True)
    named_page = _R(_kbo_detail(title_named), url=detail_named, ok=True)
    einsatzort_page = _R(_kbo_detail(title_einsatzort, body_extra="<p>Einsatzort: Haar</p>"), url=detail_einsatzort, ok=True)
    unknown_page = _R(_kbo_detail(title_unknown), url=detail_unknown, ok=True)
    monkeypatch.setattr(va, "get", _router({list_url: list_page, detail_named: named_page,
                                            detail_einsatzort: einsatzort_page, detail_unknown: unknown_page}))
    rows = va.crawl_group_portal({"name": "kbo-Heckscher-Klinikum", "careers_url": "https://kbo-heckscher-klinikum.de"},
                                  KBO_G, towns=KBO_TOWNS)
    by_title = {r["payload"]["title"]: r["payload"]["loc"][0] for r in rows}
    assert by_title[title_named] == {"city": "Haar", "plz": None, "region": None}          # from the title
    assert by_title[title_einsatzort] == {"city": "Haar", "plz": None, "region": None}     # from Einsatzort text
    assert by_title[title_unknown] == {"city": None, "plz": None, "region": None}          # honest unknown
    # none of the three carry the group HQ's Munich address/80538 postcode through
    for loc in by_title.values():
        assert loc["plz"] != "80538"
        assert loc["city"] != "München" or loc["plz"] is None


def test_group_portal_for_does_not_reroute_a_clinic_with_its_own_working_board():
    """2026-09-18 crawler review: GROUP_PORTALS' "barmherzige" match fired on the clinic's own NAME
    (an unrelated Barmherzige Bruder order) and even on a same-word-but-distinct microsite
    (barmherzige-bieten-zukunft.de), silently un-fetching each clinic's own board and re-attributing
    the real karriere.barmherzige.net board's postings to it instead. Only a clinic whose OWN
    careers_url is empty, or already names the group host, may still be routed to the group."""
    bruder_regensburg = {"name": "Krankenhaus Barmherzige Brüder", "careers_url": "https://www.barmherzige-regensburg.de/karriere/offene-stellen-bewerbung.html"}
    bruder_strassburg = {"name": "Barmherzige Brüder Klinikum St. Elisabeth, Straubing", "careers_url": "https://www.klinikum-straubing.de/karriere/offene-stellen.html"}
    bruder_muenchen = {"name": "Krankenhaus Barmherzige Brüder München", "careers_url": "https://karriere-barmherzige-muenchen.de/stellenangebote"}
    st_barbara_schwandorf = {"name": "St. Barbara Krankenhaus Schwandorf",
                              "careers_url": "https://www.barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse"}
    for c in (bruder_regensburg, bruder_strassburg, bruder_muenchen, st_barbara_schwandorf):
        assert va.group_portal_for(c) is None, c["name"]

    # The two real Barmherzige Schwestern members, whose own careers_url already names the group's
    # host with a per-clinic filter, must still be routed there.
    schwestern_neuwittelsbach = {"name": "Krankenhaus Neuwittelsbach",
                                  "careers_url": "https://karriere.barmherzige.net/jobs/?filter[company][]=Krankenhaus+Neuwittelsbach"}
    g = va.group_portal_for(schwestern_neuwittelsbach)
    assert g is not None and g["host"] == "karriere.barmherzige.net"

    # A clinic with no careers_url of its own (blank -- never reaches this function in production
    # since crawlers.routing.plan() marks it unroutable first, but group_portal_for itself must not
    # refuse it either) still matches on name alone.
    kbo_blank = {"name": "kbo-Heckscher-Klinikum Ingolstadt, Transitionspsychiatrie", "careers_url": ""}
    assert va.group_portal_for(kbo_blank) is not None

    # A kbo satellite domain that merely redirects into the shared board (not its own real content)
    # must still be routed to the group -- kbo's "own_ok" falls back to its broad match pattern.
    kbo_satellite = {"name": "kbo-Heckscher-Klinikum München", "careers_url": "https://kbo-heckscher-klinikum.de"}
    g2 = va.group_portal_for(kbo_satellite)
    assert g2 is not None and g2["host"] == "kbo.de"


def test_group_list_url_keys_a_filtered_member_separately_from_the_bare_group_list():
    g = va.group_portal_for({"name": "Krankenhaus Neuwittelsbach",
                              "careers_url": "https://karriere.barmherzige.net/jobs/?filter[company][]=Krankenhaus+Neuwittelsbach"})
    filtered = va._group_list_url({"careers_url": "https://karriere.barmherzige.net/jobs/?filter[company][]=Krankenhaus+Neuwittelsbach"}, g)
    other_filtered = va._group_list_url({"careers_url": "https://karriere.barmherzige.net/jobs/?filter[company][]=Maria-Theresia-Klinik"}, g)
    bare = va._group_list_url({"careers_url": ""}, g)
    assert filtered != other_filtered and filtered != bare and bare == g["list"]


def test_group_portal_paginates_past_the_old_page_cap_to_the_boards_own_end(monkeypatch):
    g = {"match": "barmherzige", "list": "https://karriere.barmherzige.net/jobs/",
         "page_param": "c_page",
         # TASK-14: crawl_group_portal read a per-board page ceiling off this dict
         # (`range(1, g.get("pages", 100_000) + 1)`). No registry entry sets it, but the code path
         # was still live -- a registry edit could re-cap a free board with no truncated flag.
         "pages": 3,
         "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"}
    c = {"name": "St. Barbara Krankenhaus Schwandorf",
         "careers_url": "https://www.barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse"}
    # 15 pages of one fresh job each -- one more than the old hardcoded 12-page cap -- then an empty
    # page 16, the board's own end signal.
    mapping = {}
    for i in range(1, 16):
        url = g["list"] if i == 1 else "%s?c_page=%d" % (g["list"], i)
        job_url = "https://karriere.barmherzige.net/jobs/job-%d" % i
        mapping[url] = _R('<a href="%s">x</a>' % job_url, url=url, ok=True)
        mapping[job_url] = _R("<h2>Job Nr. %d (m/w/d)</h2><p>desc</p>" % i, url=job_url, ok=True)
    end_url = "%s?c_page=16" % g["list"]
    mapping[end_url] = _R("<html></html>", url=end_url, ok=True)
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_group_portal(c, g)
    assert len(rows) == 15


def _kbo_detail_with_site_block(title, site_name, street, plz_city):
    """A real kbo.de detail page: group-wide hiringOrganization + group HQ jobLocation in the
    JSON-LD, and the page's own "Einsatzort" block naming the actual site and its address."""
    jsonld = ('<script type="application/ld+json">{"@type":"JobPosting","title":"%s",'
              '"hiringOrganization":{"name":"Kliniken des Bezirks Oberbayern \\u2013 Kommunalunternehmen"},'
              '"jobLocation":{"address":{"addressLocality":"M\\u00fcnchen","postalCode":"80538"}}}</script>'
              % title)
    block = ('<div class="job__related-site"><header class="job__related-site-header"><h3>Einsatzort'
             '<h2 class="list-teaser__item-text-headline">%s</h2></h3></header>'
             '<p class="job__related-site-description">%s<br />%s</p></div>' % (site_name, street, plz_city))
    return jsonld + "<h1>%s</h1>%s" % (title, block)


def test_kbo_takes_site_name_and_address_from_the_pages_own_einsatzort_block(monkeypatch):
    """TASK-57 AC#1: kbo.de's JSON-LD names the group on every posting (hiringOrganization = the
    Kommunalunternehmen, jobLocation = the Munich HQ), so nothing in it tells two registry sister
    sites in the SAME town apart -- Lech-Mangfall and Heckscher both run a Landsberg am Lech site
    on this board. The detail page's own "Einsatzort" block does: it states the site by name and
    gives its street address. Confirmed live 2026-09-21 on all 108 job pages of the board."""
    list_url = va.GROUP_PORTALS[0]["list"]
    pages = {
        "https://kbo.de/karriere/jobs/1": _kbo_detail_with_site_block(
            "Pflegefachkraft (m/w/d) Psychiatrie", "kbo-Lech-Mangfall-Klinik Landsberg am Lech",
            "Bürgermeister-Dr.-Hartmann-Straße 50 - 52", "86899 Landsberg am Lech"),
        "https://kbo.de/karriere/jobs/2": _kbo_detail_with_site_block(
            "Gesundheits- und Kinderkrankenpfleger (m/w/d)", "kbo-Heckscher-Klinikum Landsberg am Lech",
            "Bürgermeister-Dr.-Hartmann-Straße 52", "86899 Landsberg am Lech"),
        # The title names Munich, the block names Haar: the block is the site of work and wins.
        "https://kbo.de/karriere/jobs/3": _kbo_detail_with_site_block(
            "Pflegefachhelfer (m/w/d) in München", "kbo-Isar-Amper-Klinikum | Haar, Pflegedirektion",
            "Vockestraße 72", "85540 Haar"),
    }
    mapping = {list_url: _R("".join('<a href="%s"></a>' % u for u in pages), url=list_url, ok=True)}
    mapping.update({u: _R(h, url=u, ok=True) for u, h in pages.items()})
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_group_portal({"name": "kbo-Heckscher-Klinikum", "careers_url": "https://kbo-heckscher-klinikum.de"},
                                  va.GROUP_PORTALS[0], towns={"landsberg am lech", "haar", "münchen"})
    by_url = {r["payload"]["url"]: r["payload"] for r in rows}
    assert len(by_url) == 3
    a = by_url["https://kbo.de/karriere/jobs/1"]
    b = by_url["https://kbo.de/karriere/jobs/2"]
    c = by_url["https://kbo.de/karriere/jobs/3"]
    # the two same-town sister sites are told apart by name, not collapsed onto the group's
    assert a["org"] == "kbo-Lech-Mangfall-Klinik Landsberg am Lech"
    assert b["org"] == "kbo-Heckscher-Klinikum Landsberg am Lech"
    assert a["org_source"] is None and b["org_source"] is None     # read off the page, not inherited
    assert a["loc"] == [{"city": "Landsberg am Lech", "plz": "86899", "region": None}]
    assert b["loc"] == [{"city": "Landsberg am Lech", "plz": "86899", "region": None}]
    assert c["org"] == "kbo-Isar-Amper-Klinikum | Haar, Pflegedirektion"
    assert c["loc"] == [{"city": "Haar", "plz": "85540", "region": None}]   # block beats the title
    for p in (a, b, c):
        assert p["loc"][0]["plz"] != "80538"


def test_kbo_site_name_resolves_two_same_town_sister_sites_to_different_registry_rows():
    """The point of reading the site name: Matcher.match() can now name the right one of the two
    Landsberg am Lech kbo sites from content alone, with no board-pool guess."""
    from pflege_jobs.registry import Matcher
    clinics = [{"clinic_id": "18103", "name": "kbo-Lech-Mangfall-Klinik Landsberg am Lech",
                "town": "Landsberg am Lech", "operator": "kbo-Lech-Mangfall-Kliniken gGmbH", "beds": 92},
               {"clinic_id": "18104", "name": "kbo-Heckscher-Klinikum Landsberg am Lech",
                "town": "Landsberg am Lech", "operator": "kbo-Heckscher-Klinikum gGmbH", "beds": 0}]
    m = Matcher(clinics)
    assert m.match("kbo-Lech-Mangfall-Klinik Landsberg am Lech", "Landsberg am Lech")[0] == "18103"
    assert m.match("kbo-Heckscher-Klinikum Landsberg am Lech", "Landsberg am Lech")[0] == "18104"
    # the group name alone, which is what every posting's JSON-LD carries, still decides nothing
    assert m.match("Kliniken des Bezirks Oberbayern – Kommunalunternehmen", "Landsberg am Lech") is None
