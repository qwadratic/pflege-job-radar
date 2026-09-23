"""crawlers/vendor_adapters.py: every adapter fetches the whole board and tags each job with its
own vendor-native label (department/category/section) as data -- it never narrows the fetch or
drops a posting on that label. Mocked HTTP (via the module's own `get` helper), no network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402


class _R:
    def __init__(self, text="", url="", ok=True, json_data=None, headers=None):
        self.text = text
        self.url = url
        self.ok = ok
        self.encoding = None
        self.headers = headers or {}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


def _router(mapping, calls=None):
    """mapping: {url: _R}. Any url not in it comes back as a 404. `calls` (if given) records every
    url requested, so a test can assert a URL was -- or was not -- fetched at all."""
    def fake_get(u, timeout=30, session=None):
        if calls is not None:
            calls.append(u)
        return mapping.get(u, _R(ok=False, url=u))
    return fake_get


# ---------------------------------------------------------------------------
# get(): the shared session attempt/ok tally app/crawl.py's _fetch_board reads (TASK-72 AC#1)
# ---------------------------------------------------------------------------
class _FakeSession:
    def __init__(self, answers):
        self._answers = list(answers)

    def get(self, u, **kw):
        item = self._answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_get_tallies_attempts_and_oks_on_the_shared_session():
    s = _FakeSession([_R(ok=True), _R(ok=False), RuntimeError("boom")])
    r1 = va.get("https://x/1", session=s)
    r2 = va.get("https://x/2", session=s)
    r3 = va.get("https://x/3", session=s)
    assert r1.ok and not r2.ok and r3 is None
    assert s._attempts == 3 and s._ok == 1


def test_get_follows_an_immediate_meta_refresh_but_not_a_delayed_one():
    """psychiatrie-werneck.de shape (confirmed live 2026-09-22): careers_url is nothing but a
    "content=0;url=..." redirect stub to the real board -- requests' own allow_redirects never
    follows an HTML-level refresh, only an HTTP 3xx. A DELAYED refresh (content="30;...") is left
    alone -- that shape is usually a session-timeout/please-wait notice for a human, not "this page
    IS the redirect", and auto-following it would silently skip whatever real content that page has."""
    stub = _R('<meta http-equiv="refresh" content="0;url=https://x/real">', url="https://x/stub", ok=True)
    real = _R("real board content", url="https://x/real", ok=True)
    r = va.get("https://x/stub", session=_FakeSession([stub, real]))
    assert r.text == "real board content" and r.url == "https://x/real"

    slow = _R('<meta http-equiv="refresh" content="30;url=https://x/other">', url="https://x/slow", ok=True)
    r2 = va.get("https://x/slow", session=_FakeSession([slow]))
    assert r2.url == "https://x/slow"  # not followed


def test_get_meta_refresh_loop_terminates_instead_of_spinning_forever():
    a = _R('<meta http-equiv="refresh" content="0;url=https://x/b">', url="https://x/a", ok=True)
    b = _R('<meta http-equiv="refresh" content="0;url=https://x/a">', url="https://x/b", ok=True)
    r = va.get("https://x/a", session=_FakeSession([a, b, a, b]))
    assert r is not None and r.url in ("https://x/a", "https://x/b")


# ---------------------------------------------------------------------------
# personio
# ---------------------------------------------------------------------------
def _personio_xml(entries):
    body = "".join(
        "<position><id>%s</id><name>%s</name><office>Standort</office><subcompany>Klinik GmbH</subcompany>"
        "<department>%s</department><recruitingCategory>%s</recruitingCategory>"
        "<employmentType>full</employmentType><createdAt>2026-01-01T00:00:00Z</createdAt>"
        "<jobDescriptions><jobDescription><value>desc</value></jobDescription></jobDescriptions></position>"
        % (pid, name, dept, dept) for pid, name, dept in entries)
    return "<workzag-jobs>%s</workzag-jobs>" % body


def test_personio_keeps_every_job_and_tags_each_ones_own_department(monkeypatch):
    xml = _personio_xml([
        ("1", "Pflegefachkraft (m/w/d)", "Pflege"),
        ("2", "Verwaltungsfachkraft (m/w/d)", "Verwaltung"),
    ])
    monkeypatch.setattr(va, "get", _router({"https://acme.jobs.personio.de/xml": _R(xml, ok=True)}))
    rows = va.crawl_personio({"name": "Acme Klinik", "careers_url": "https://acme.jobs.personio.de/"})
    assert {r["payload"]["title"] for r in rows} == {"Pflegefachkraft (m/w/d)", "Verwaltungsfachkraft (m/w/d)"}
    by_title = {r["payload"]["title"]: r["payload"] for r in rows}
    assert by_title["Pflegefachkraft (m/w/d)"]["section_labels"] == ["Pflege"]
    assert by_title["Verwaltungsfachkraft (m/w/d)"]["section_labels"] == ["Verwaltung"]


def test_personio_keeps_everything_when_no_department_is_nursing(monkeypatch):
    xml = _personio_xml([
        ("1", "Augenarzt (m/w/d)", "Augenheilkunde"),
        ("2", "Verwaltungsfachkraft (m/w/d)", "Verwaltung"),
    ])
    monkeypatch.setattr(va, "get", _router({"https://acme.jobs.personio.de/xml": _R(xml, ok=True)}))
    rows = va.crawl_personio({"name": "Acme Klinik", "careers_url": "https://acme.jobs.personio.de/"})
    assert len(rows) == 2


def test_personio_keeps_the_tenants_own_com_tld_instead_of_forcing_de(monkeypatch):
    """A careers page can link only the .com form of its tenant (munich-airport-clinic.com does) --
    the adapter must fetch and record that same host, not silently rewrite it to .de."""
    xml = _personio_xml([("1", "Pflegefachkraft (m/w/d)", "Pflege")])
    monkeypatch.setattr(va, "get", _router({"https://acme.jobs.personio.com/xml": _R(xml, ok=True)}))
    rows = va.crawl_personio({"name": "Acme Klinik", "careers_url": "https://acme.jobs.personio.com/"})
    assert rows and rows[0]["source_host"] == "acme.jobs.personio.com"
    assert rows[0]["payload"]["url"].startswith("https://acme.jobs.personio.com/")


def test_personio_falls_back_to_the_wordpress_plugin_rest_api(monkeypatch):
    """Sites running the 'Personio Integration Light' WP plugin (ProSomno) have no
    <slug>.jobs.personio.* tenant at all -- jobs live at wp-json/wp/v2/personioposition."""
    careers_url = "https://prosomno.de/ueber-uns/jobs/"
    posts = [{
        "title": {"rendered": "Pflegefachkraft (m/w/d)"},
        "excerpt": {"rendered": "<h3>Festangestellte • Vollzeit • München</h3>"},
        "content": {"rendered": "<p>Aufgaben...</p>"},
        "date": "2026-03-01T00:00:00",
        "link": "https://prosomno.de/stelle/pflegefachkraft-mwd/",
    }]
    calls = []
    monkeypatch.setattr(va, "get", _router({
        careers_url: _R(ok=False),  # no <slug>.jobs.personio.* anywhere on the page
        "https://prosomno.de/wp-json/wp/v2/personioposition?per_page=100": _R(ok=True, json_data=posts),
    }, calls))
    rows = va.crawl_personio({"name": "ProSomno", "careers_url": careers_url})
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["title"] == "Pflegefachkraft (m/w/d)"
    assert p["loc"][0]["city"] == "München"
    assert p["employmentType"] == "Festangestellte"
    assert p["datePosted"] == "2026-03-01"
    assert p["url"] == "https://prosomno.de/stelle/pflegefachkraft-mwd/"


# ---------------------------------------------------------------------------
# smartrecruiters
# ---------------------------------------------------------------------------
def _sr_posting(pid, name, dept_id, dept_label):
    return {"id": pid, "name": name, "ref": "https://jobs.smartrecruiters.com/ArtemedSE/%s" % pid,
            "company": {"name": "Artemed", "identifier": "ArtemedSE"}, "location": {},
            "department": {"id": dept_id, "label": dept_label}}


def test_smartrecruiters_walks_the_full_board_and_tags_each_jobs_own_department(monkeypatch):
    # 2026-09 coverage-loss fix: a hard &department= re-fetch used to drop genuine certified-nursing
    # postings filed under a non-"Pflegedienst" bucket (real Artemed example: "Funktionsdienst"
    # OTA/Anästhesiepflege leads). This API returns full posting data with no per-job detail fetch,
    # so a full walk costs no more than a filtered one -- fetch everything, and thread each job's own
    # department label into payload["section_labels"] instead of using it to narrow the fetch.
    page = _R(json_data={"totalFound": 2, "content": [
        _sr_posting("1", "Pflegefachkraft (m/w/d)", "3484565", "Pflegedienst"),
        _sr_posting("2", "Buchhalter (m/w/d)", "999", "Verwaltung"),
    ]})
    base = "https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings"
    calls = []
    mapping = {"%s?limit=100&offset=0" % base: page}
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_smartrecruiters({"name": "Artemed", "careers_url": "https://www.smartrecruiters.com/ArtemedSE"})
    assert {r["payload"]["title"] for r in rows} == {"Pflegefachkraft (m/w/d)", "Buchhalter (m/w/d)"}
    assert not any("department=" in u for u in calls)
    by_title = {r["payload"]["title"]: r["payload"] for r in rows}
    assert by_title["Pflegefachkraft (m/w/d)"]["section_labels"] == ["Pflegedienst"]
    assert by_title["Buchhalter (m/w/d)"]["section_labels"] == ["Verwaltung"]


def test_smartrecruiters_keeps_full_walk_when_no_department_field(monkeypatch):
    page = _R(json_data={"totalFound": 1, "content": [
        {"id": "1", "name": "Pflegefachkraft (m/w/d)", "ref": "https://jobs.smartrecruiters.com/X/1",
         "company": {"name": "X", "identifier": "X"}, "location": {}},
    ]})
    base = "https://api.smartrecruiters.com/v1/companies/X/postings"
    calls = []
    monkeypatch.setattr(va, "get", _router({"%s?limit=100&offset=0" % base: page}, calls))
    rows = va.crawl_smartrecruiters({"name": "X", "careers_url": "https://www.smartrecruiters.com/X"})
    assert len(rows) == 1
    assert not any("department=" in u for u in calls)


def test_smartrecruiters_carries_the_boards_own_total_found_as_board_total(monkeypatch):
    """TASK-88 AC#1: totalFound was already read to drive the offset walk (the "No offset ceiling"
    comment in crawl_smartrecruiters) -- carrying it as board_total lets app/crawl.py's existing
    consumer (TASK-88 AC#2) catch a genuine under-read the same way it already does for crawl_erecruiter."""
    page = _R(json_data={"totalFound": 2, "content": [
        _sr_posting("1", "Pflegefachkraft (m/w/d)", "1", "Pflege"),
        _sr_posting("2", "Buchhalter (m/w/d)", "2", "Verwaltung"),
    ]})
    base = "https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings"
    monkeypatch.setattr(va, "get", _router({"%s?limit=100&offset=0" % base: page}))
    rows = va.crawl_smartrecruiters({"name": "Artemed", "careers_url": "https://www.smartrecruiters.com/ArtemedSE"})
    assert len(rows) == 2
    assert rows.board_total == 2


def test_smartrecruiters_finds_tenant_from_company_code_on_the_listing_page_itself(monkeypatch):
    # Klinik Vincentinum-shaped board: the careers_url IS the listing page (no smartrecruiters.com
    # in the url, no category subpages to crawl) -- the tenant only shows up as the widget's own
    # company_code JSON, never as a jobs.smartrecruiters.com/<tenant>/<id> link on that page.
    listing_page = _R('<div data-widget=\'widget({"company_code": "ArtemedSE", "api_url": "x"})\'></div>',
                       url="https://www.klinik-vincentinum.de/karriere/stellenangebote", ok=True)
    page = _R(json_data={"totalFound": 1, "content": [_sr_posting("1", "Pflegefachkraft (m/w/d)", "1", "Pflege")]})
    base = "https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings"
    mapping = {"https://www.klinik-vincentinum.de/karriere/stellenangebote": listing_page,
               "%s?limit=100&offset=0" % base: page}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_smartrecruiters({"name": "Klinik Vincentinum",
                                      "careers_url": "https://www.klinik-vincentinum.de/karriere/stellenangebote"})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (m/w/d)"]


def test_smartrecruiters_fetches_detail_for_description_and_the_slugged_url(monkeypatch):
    page = _R(json_data={"totalFound": 1, "content": [_sr_posting("1", "Pflegefachkraft (m/w/d)", "1", "Pflege")]})
    base = "https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings"
    detail = _R(json_data={"postingUrl": "https://jobs.smartrecruiters.com/ArtemedSE/1-pflegefachkraft-m-w-d-",
                            "jobAd": {"sections": {"jobDescription": {"text": "<p>Wir suchen</p>"},
                                                    "qualifications": {"text": "Examen"}}}})
    calls = []
    mapping = {"%s?limit=100&offset=0" % base: page, "%s/1" % base: detail}
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_smartrecruiters({"name": "Artemed", "careers_url": "https://www.smartrecruiters.com/ArtemedSE"})
    assert "%s/1" % base in calls
    assert rows[0]["payload"]["description"] == "Wir suchen Examen"
    assert rows[0]["payload"]["url"] == "https://jobs.smartrecruiters.com/ArtemedSE/1-pflegefachkraft-m-w-d-"
    assert rows[0]["source_url"] == "https://jobs.smartrecruiters.com/ArtemedSE/1-pflegefachkraft-m-w-d-"


# ---------------------------------------------------------------------------
# helix
# ---------------------------------------------------------------------------
def test_helix_keeps_every_job_when_a_berufsfeld_fieldset_is_present(monkeypatch):
    listing = _R(
        '<label for="category_e99edf">Pflege (1 Treffer)</label>'
        '<label for="category_abc123">Küche (3 Treffer)</label>'
        '<a href="/okh/jobad?prj=A1">Pflegefachkraft (m/w/d)</a>'
        '<a href="/okh/jobad?prj=B2">Koch (m/w/d)</a>',
        url="https://tenant.helixjobs.com/okh/joblist", ok=True)
    calls = []
    mapping = {"https://tenant.helixjobs.com/okh/joblist": listing}
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_helix({"name": "X", "careers_url": "https://tenant.helixjobs.com/okh/joblist"})
    assert len(rows) == 2
    assert not any("category%5B%5D" in u for u in calls)


def test_helix_full_fetch_when_no_berufsfeld_fieldset(monkeypatch):
    listing = _R(
        '<a href="/okh/jobad?prj=A1">Pflegefachkraft (m/w/d)</a>'
        '<a href="/okh/jobad?prj=B2">Koch (m/w/d)</a>',
        url="https://tenant.helixjobs.com/okh/joblist", ok=True)
    monkeypatch.setattr(va, "get", _router({"https://tenant.helixjobs.com/okh/joblist": listing}))
    rows = va.crawl_helix({"name": "X", "careers_url": "https://tenant.helixjobs.com/okh/joblist"})
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# wp_jobs (generic sitemap/link-walk fallback)
# ---------------------------------------------------------------------------
def _jsonld_job(title):
    return ('<script type="application/ld+json">{"@type": "JobPosting", "title": "%s"}</script>' % title)


def test_wp_jobs_fetches_the_nursing_nav_link_first_and_tags_it(monkeypatch):
    cu = "https://www.muenchen-klinik.de/jobs/"
    cu_page = _R('<a href="https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/">Pflegedienst</a>'
                 '<a href="https://www.muenchen-klinik.de/jobs/andere/stellenangebote/">Andere Bereiche</a>',
                 url=cu, ok=True)
    section_page = _R('<a href="https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/pflegefachkraft-1">x</a>',
                       url="https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/", ok=True)
    detail = _R(_jsonld_job("Pflegefachkraft (m/w/d)"),
                url="https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/pflegefachkraft-1", ok=True)
    calls = []
    mapping = {
        cu: cu_page,
        "https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/": section_page,
        "https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/pflegefachkraft-1": detail,
    }
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_wp_jobs({"name": "München Klinik", "careers_url": cu})
    assert len(rows) == 1 and rows[0]["payload"]["title"] == "Pflegefachkraft (m/w/d)"
    assert rows[0]["payload"]["section_labels"] == ["Pflegedienst"]


def test_wp_jobs_resolves_relative_nav_link_against_base_href_and_skips_hidden_h1(monkeypatch):
    # 2026-09 KWM fix: a <base href> page (Contao and similar German-clinic CMSs always emit one)
    # makes urljoin(page_url, relative_href) double up the path and 404 unless <base> is honoured.
    # The detail page also carries a site-wide a11y <h1 class="visuallyhidden"> that is never the
    # job title -- the real title sits in a plain <h3> instead.
    cu = "https://www.kwm-example.de/beruf-chancen/stellenanzeigen/"
    cu_page = _R(
        '<base href="https://www.kwm-example.de/">'
        '<a href="beruf-chancen/stellenanzeigen/pflege-und-funktionsdienst/">Pflege- &amp; Funktionsdienst</a>',
        url=cu, ok=True)
    section_url = "https://www.kwm-example.de/beruf-chancen/stellenanzeigen/pflege-und-funktionsdienst/"
    section_page = _R(
        '<base href="https://www.kwm-example.de/">'
        '<a href="/beruf-chancen/stellenanzeigen/pflege-und-funktionsdienst/details/?job=1">x</a>',
        url=section_url, ok=True)
    detail_url = "https://www.kwm-example.de/beruf-chancen/stellenanzeigen/pflege-und-funktionsdienst/details/?job=1"
    detail = _R(
        '<h1 class="visuallyhidden">Klinikum Beispiel gGmbH</h1>'
        "<h3>Pflegefachkraft (m/w/d) für die Station</h3>",
        url=detail_url, ok=True)
    calls = []
    mapping = {cu: cu_page, section_url: section_page, detail_url: detail}
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_wp_jobs({"name": "Klinikum Beispiel", "careers_url": cu})
    assert section_url in calls  # <base>-resolved, not the doubled-up 404 URL
    assert len(rows) == 1
    assert rows[0]["payload"]["title"] == "Pflegefachkraft (m/w/d) für die Station"
    assert rows[0]["payload"]["section_labels"] == ["Pflege- & Funktionsdienst"]


def test_wp_jobs_recovers_a_job_filed_outside_the_nursing_nav_bucket(monkeypatch):
    # 2026-09 coverage-loss fix: stopping as soon as the confirmed nursing nav subtree yielded any
    # rows used to mean the full-board sitemap walk never ran at all -- silently missing a real
    # Klinikum Nürnberg posting ("OTA / Pflegefachkraft OP (m/w/d) Zentral-OP") filed under a
    # different, non-nursing-labelled "Jobwelt". Now both the confirmed section AND the rest of the
    # sitemap are fetched (deduped, budget-capped), so that posting is recovered too.
    cu = "https://karriere.klinikum-nuernberg.de/freie-stellen"
    cu_page = _R('<a href="https://karriere.klinikum-nuernberg.de/jobs/pflege/">Jobwelt Pflege und Funktionsdienst</a>',
                 url=cu, ok=True)
    section_page = _R('<a href="https://karriere.klinikum-nuernberg.de/jobs/pflege/pflegefachkraft-1">x</a>',
                       url="https://karriere.klinikum-nuernberg.de/jobs/pflege/", ok=True)
    section_detail = _R(_jsonld_job("Pflegefachkraft (m/w/d) Station 3"),
                         url="https://karriere.klinikum-nuernberg.de/jobs/pflege/pflegefachkraft-1", ok=True)
    sitemap = _R(
        "<urlset>"
        "<url><loc>https://karriere.klinikum-nuernberg.de/jobs/pflege/pflegefachkraft-1</loc></url>"
        "<url><loc>https://karriere.klinikum-nuernberg.de/jobs/medizinisch-technisch/ota-op-2</loc></url>"
        "</urlset>", ok=True)
    other_detail = _R(_jsonld_job("OTA / Pflegefachkraft OP (m/w/d) Zentral-OP"),
                       url="https://karriere.klinikum-nuernberg.de/jobs/medizinisch-technisch/ota-op-2", ok=True)
    mapping = {
        cu: cu_page,
        "https://karriere.klinikum-nuernberg.de/jobs/pflege/": section_page,
        "https://karriere.klinikum-nuernberg.de/jobs/pflege/pflegefachkraft-1": section_detail,
        "https://karriere.klinikum-nuernberg.de/sitemap.xml": sitemap,
        "https://karriere.klinikum-nuernberg.de/jobs/medizinisch-technisch/ota-op-2": other_detail,
    }
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_wp_jobs({"name": "Klinikum Nürnberg", "careers_url": cu})
    titles = {r["payload"]["title"] for r in rows}
    assert titles == {"Pflegefachkraft (m/w/d) Station 3", "OTA / Pflegefachkraft OP (m/w/d) Zentral-OP"}
    by_title = {r["payload"]["title"]: r["payload"] for r in rows}
    assert by_title["Pflegefachkraft (m/w/d) Station 3"]["section_labels"] == ["Jobwelt Pflege und Funktionsdienst"]
    # the OTA posting was recovered via the full sitemap walk, not the confirmed nursing bucket, so
    # it carries no section label -- classify_role still keeps it on its own title's pflege_gate token
    assert by_title["OTA / Pflegefachkraft OP (m/w/d) Zentral-OP"]["section_labels"] == []


def test_wp_jobs_falls_back_to_the_sitemap_walk_when_no_nursing_nav_exists(monkeypatch):
    cu = "https://klinik-menterschwaige.de/karriere/"
    cu_page = _R('<a href="/impressum/">Impressum</a><a href="/datenschutz/">Datenschutz</a>', url=cu, ok=True)
    sitemap = _R(
        "<urlset><url><loc>https://klinik-menterschwaige.de/stellen/pflegefachkraft-1</loc></url>"
        "<url><loc>https://klinik-menterschwaige.de/stellen/koch-2</loc></url></urlset>", ok=True)
    d1 = _R(_jsonld_job("Pflegefachkraft (m/w/d)"), url="https://klinik-menterschwaige.de/stellen/pflegefachkraft-1", ok=True)
    d2 = _R(_jsonld_job("Koch (m/w/d)"), url="https://klinik-menterschwaige.de/stellen/koch-2", ok=True)
    mapping = {
        cu: cu_page,
        "https://klinik-menterschwaige.de/sitemap.xml": sitemap,
        "https://klinik-menterschwaige.de/stellen/pflegefachkraft-1": d1,
        "https://klinik-menterschwaige.de/stellen/koch-2": d2,
    }
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_wp_jobs({"name": "Klinik Menterschwaige", "careers_url": cu})
    assert {r["payload"]["title"] for r in rows} == {"Pflegefachkraft (m/w/d)", "Koch (m/w/d)"}


def test_crawl_wp_jobs_flags_the_board_degraded_when_sitemap_and_wp_json_both_come_up_empty(monkeypatch):
    """TASK-85 AC#1: this used to be a stderr-only print with the crawl still reporting a clean,
    nonzero-row success off whatever the homepage-link fallback below happened to find (confirmed
    live: karriere.ge-passau.de, "adapter: 2 rows" read as a clean result while the real board sat
    behind an untried sitemap variant, see AC#2's tests below). A caller can now read rows.degraded
    instead of never learning the count it got was not the real board."""
    cu = "https://example.de/karriere/"
    cu_page = _R('<a href="/stellen/hausmeister-1">Hausmeister (m/w/d)</a>', url=cu, ok=True)
    detail = _R(_jsonld_job("Hausmeister (m/w/d)"), url="https://example.de/stellen/hausmeister-1", ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page, "https://example.de/stellen/hausmeister-1": detail}))
    rows = va.crawl_wp_jobs({"name": "X", "careers_url": cu})
    assert len(rows) == 1
    assert rows.degraded == "sitemap_and_wp_json_empty"


def test_crawl_wp_jobs_keeps_the_degraded_tag_when_the_hr4you_fallback_also_finds_nothing(monkeypatch):
    """Review finding 2026-09-22: `out = crawl_hr4you(...)` reassigned `out` from the _BoardTotalRows
    instance carrying .degraded to hr4you's own plain list, silently dropping the tag exactly in the
    worst case it exists for -- a board whose sitemap and wp-json are both empty AND whose hr4you
    fallback also reads 0 rows. The AC#1 test above only exercises the 1-row homepage-fallback path,
    which never reaches this reassignment."""
    cu = "https://example.de/karriere/"
    cu_page = _R("<html><body>no job-shaped links here</body></html>", url=cu, ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page}))
    import pflege_jobs.sources.hr4you as hr4you
    monkeypatch.setattr(hr4you, "crawl_hr4you", lambda c, session=None: [])
    rows = va.crawl_wp_jobs({"name": "X", "careers_url": cu})
    assert list(rows) == []
    assert rows.degraded == "sitemap_and_wp_json_empty"


def test_find_job_urls_tries_the_hyphenated_sitemap_index_variant(monkeypatch):
    """TASK-85 AC#2: karriere.ge-passau.de serves /sitemap-index.xml (HYPHEN) while /sitemap.xml,
    /sitemap_index.xml (underscore) and /robots.txt all 404 -- every job on the board was invisible
    to this candidate list before."""
    base = "https://karriere.ge-passau.de"
    index = _R("<sitemapindex><sitemap><loc>%s/sitemap-0.xml</loc></sitemap></sitemapindex>" % base,
               url=base + "/sitemap-index.xml", ok=True)
    detail_url = base + "/stellen/pflegefachkraft-1"
    leaf = _R("<urlset><url><loc>%s</loc></url></urlset>" % detail_url, url=base + "/sitemap-0.xml", ok=True)
    mapping = {base + "/sitemap-index.xml": index, base + "/sitemap-0.xml": leaf}
    monkeypatch.setattr(va, "get", _router(mapping))
    assert va.find_job_urls(base) == [detail_url]


def test_wp_json_cpt_job_urls_prefers_the_shorter_slug_over_an_old_archive_variant(monkeypatch):
    """TASK-85 AC#2: karriere.klinikum-altmuehlfranken.de excludes its 'stellenangebote' CPT (53 open
    postings) from robots.txt and every sitemap candidate, but still answers its own WP REST API for
    it. A second, longer-named CPT ('stellenangebote_old') also matches the job vocabulary and must
    lose the tiebreak to the shorter, live one."""
    base = "https://karriere.klinikum-altmuehlfranken.de"
    types = {
        "post": {"name": "Beiträge", "rest_base": "posts"},
        "stellenangebote_old": {"name": "Stellenangebote (Old)", "rest_base": "stellenangebote_old"},
        "stellenangebote": {"name": "Stellenangebote", "rest_base": "stellenangebote"},
    }
    items = [{"link": base + "/stellenangebote/ergotherapeut-1/"}]
    mapping = {
        base + "/wp-json/wp/v2/types": _R(json_data=types, ok=True),
        base + "/wp-json/wp/v2/stellenangebote?per_page=100&page=1": _R(json_data=items, ok=True),
    }
    calls = []
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    found = va.find_job_urls(base)
    assert found == [base + "/stellenangebote/ergotherapeut-1/"]
    assert not any("stellenangebote_old" in u for u in calls)


def test_wp_json_cpt_job_urls_pages_until_the_sites_own_totalpages_header(monkeypatch):
    """No hardcoded page cap -- read on until the board's own X-WP-TotalPages says stop."""
    base = "https://example.de"
    types = {"stellenangebote": {"name": "Stellenangebote", "rest_base": "stellenangebote"}}
    page1 = _R(json_data=[{"link": base + "/s/1"}], ok=True, headers={"x-wp-totalpages": "2"})
    page2 = _R(json_data=[{"link": base + "/s/2"}], ok=True, headers={"x-wp-totalpages": "2"})
    mapping = {
        base + "/wp-json/wp/v2/types": _R(json_data=types, ok=True),
        base + "/wp-json/wp/v2/stellenangebote?per_page=100&page=1": page1,
        base + "/wp-json/wp/v2/stellenangebote?per_page=100&page=2": page2,
    }
    monkeypatch.setattr(va, "get", _router(mapping))
    assert va._wp_json_cpt_job_urls(base) == [base + "/s/1", base + "/s/2"]


def test_wp_json_cpt_job_urls_keeps_paging_when_totalpages_header_is_missing(monkeypatch):
    """No-safety-nets rule (CLAUDE.md): the source's own end signal is `not items` / a failed fetch,
    never a missing header. Review finding 2026-09-22: `if not total_pages or ...: break` silently
    ceilinged every board at per_page=100 the moment a host omitted X-WP-TotalPages -- inside the very
    fix for 'near-complete read reported as success'."""
    base = "https://example.de"
    types = {"stellenangebote": {"name": "Stellenangebote", "rest_base": "stellenangebote"}}
    page1 = _R(json_data=[{"link": base + "/s/1"}], ok=True)  # no headers -> no X-WP-TotalPages
    page2 = _R(json_data=[{"link": base + "/s/2"}], ok=True)  # no headers -> no X-WP-TotalPages
    mapping = {
        base + "/wp-json/wp/v2/types": _R(json_data=types, ok=True),
        base + "/wp-json/wp/v2/stellenangebote?per_page=100&page=1": page1,
        base + "/wp-json/wp/v2/stellenangebote?per_page=100&page=2": page2,
        # page 3 deliberately absent from mapping -> _router's default 404 is the board's own
        # end-of-pagination signal (not rr.ok), the only thing allowed to stop this loop.
    }
    monkeypatch.setattr(va, "get", _router(mapping))
    assert va._wp_json_cpt_job_urls(base) == [base + "/s/1", base + "/s/2"]


def test_find_job_urls_visits_every_unnamed_sitemap_index_child_not_just_the_first_3(monkeypatch):
    """TASK-72 AC#2: locs[:3] used to permanently drop children 4+ of a sitemap index whose own
    names carry no job-ish word at all (confirmed live: klinikum-ab-alz.de's wp-sitemap.xml)."""
    base = "https://example.de"
    index = _R("<sitemapindex>" + "".join(
        "<sitemap><loc>%s/wp-sitemap-posts-page-%d.xml</loc></sitemap>" % (base, i) for i in range(1, 6))
        + "</sitemapindex>", url=base + "/sitemap.xml", ok=True)
    mapping = {base + "/robots.txt": _R(ok=False), base + "/sitemap.xml": index}
    job_urls = set()
    for i in range(1, 6):
        u = "%s/wp-sitemap-posts-page-%d.xml" % (base, i)
        job_url = "%s/stellen/job-%d" % (base, i)
        job_urls.add(job_url)
        mapping[u] = _R("<urlset><url><loc>%s</loc></url></urlset>" % job_url, url=u, ok=True)
    monkeypatch.setattr(va, "get", _router(mapping))
    assert set(va.find_job_urls(base)) == job_urls


# ---------------------------------------------------------------------------
# rexx
# ---------------------------------------------------------------------------
def test_rexx_fetches_every_tagged_detail_page_and_carries_its_own_fachbereich(monkeypatch):
    # 2026-09 coverage-loss fix: hard-filtering fetch_urls to only the nursing Fachbereich tag used
    # to drop genuine certified-nursing postings filed under a different Fachbereich entirely (real
    # Schön Klinik examples: "OP Pfleger oder Operationstechnischer Assistent", "MFA/MTRA/
    # Pflegefachkraft Herzkatheterlabor..."). `urls` is already capped at max_jobs during pagination,
    # so fetching every one of them costs no more requests than the existing cap allowed -- fetch
    # them all, and thread each job's own Fachbereich tag into payload["section_labels"] instead.
    cu = "https://jobs.schoen-klinik.de/stellenangebote.html"
    listing = _R(
        '<div class="joboffer_container"><span class="job_details_first">Pflege, Patientenmanagement &amp; Dokumentation</span>'
        '<a href="/gesundheits-und-krankenpfleger-de-j111.html">Gesundheits- und Krankenpfleger (m/w/d)</a></div>'
        '<div class="joboffer_container"><span class="job_details_first">Chirurgie</span>'
        '<a href="/facharzt-de-j222.html">Facharzt (m/w/d) Gefäßchirurgie</a></div>',
        url=cu, ok=True)
    detail1 = _R("<h1>Gesundheits- und Krankenpfleger (m/w/d)</h1>",
                url="https://jobs.schoen-klinik.de/gesundheits-und-krankenpfleger-de-j111.html", ok=True)
    detail2 = _R("<h1>Facharzt (m/w/d) Gefäßchirurgie</h1>",
                url="https://jobs.schoen-klinik.de/facharzt-de-j222.html", ok=True)
    calls = []
    mapping = {cu: listing,
               "https://jobs.schoen-klinik.de/gesundheits-und-krankenpfleger-de-j111.html": detail1,
               "https://jobs.schoen-klinik.de/facharzt-de-j222.html": detail2}
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_rexx({"name": "Schön Klinik", "careers_url": cu})
    assert len(rows) == 2
    assert any("j222" in u for u in calls)   # both detail pages now fetched, not just the tagged one
    by_title = {r["payload"]["title"]: r["payload"] for r in rows}
    assert by_title["Gesundheits- und Krankenpfleger (m/w/d)"]["section_labels"] == ["Pflege, Patientenmanagement & Dokumentation"]
    assert by_title["Facharzt (m/w/d) Gefäßchirurgie"]["section_labels"] == ["Chirurgie"]


def test_rexx_fetches_every_detail_page_when_no_fachbereich_tag_is_rendered(monkeypatch):
    cu = "https://jobs.example-klinik.de/stellenangebote.html"
    listing = _R(
        '<div class="joboffer_container"><a href="/pflegefachkraft-de-j111.html">Pflegefachkraft (m/w/d)</a></div>'
        '<div class="joboffer_container"><a href="/koch-de-j222.html">Koch (m/w/d)</a></div>',
        url=cu, ok=True)
    d1 = _R("<h1>Pflegefachkraft (m/w/d)</h1>", url="https://jobs.example-klinik.de/pflegefachkraft-de-j111.html", ok=True)
    d2 = _R("<h1>Koch (m/w/d)</h1>", url="https://jobs.example-klinik.de/koch-de-j222.html", ok=True)
    mapping = {cu: listing,
               "https://jobs.example-klinik.de/pflegefachkraft-de-j111.html": d1,
               "https://jobs.example-klinik.de/koch-de-j222.html": d2}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_rexx({"name": "Example Klinik", "careers_url": cu})
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# mein-check-in
# ---------------------------------------------------------------------------
def test_mein_check_in_keeps_every_position_and_tags_each_ones_own_sidebar_group(monkeypatch):
    cu = "https://www.mein-check-in.de/kna-online/overview"
    listing = _R(
        '<li id="pg-12880"><span>Pflegedienst</span><ul>'
        '<a href="/kna-online/position-130492">Pflegefachkraft Notaufnahme</a>'
        '<a href="/kna-online/position-147515">Gesundheits-/Krankenpfleger Intensivstation</a>'
        '</ul></li>'
        '<li id="pg-99999"><span>Verwaltung</span><ul>'
        '<a href="/kna-online/position-500000">Verwaltungsfachkraft</a>'
        '</ul></li>', ok=True)
    calls = []
    monkeypatch.setattr(va, "get", _router({cu: listing}, calls))
    rows = va.crawl_mein_check_in({"name": "KNA", "careers_url": cu})
    assert {r["payload"]["title"] for r in rows} == {
        "Pflegefachkraft Notaufnahme", "Gesundheits-/Krankenpfleger Intensivstation", "Verwaltungsfachkraft"}
    assert any("position-500000" in u for u in calls)
    by_title = {r["payload"]["title"]: r["payload"] for r in rows}
    assert by_title["Pflegefachkraft Notaufnahme"]["section_labels"] == ["Pflegedienst"]
    assert by_title["Verwaltungsfachkraft"]["section_labels"] == ["Verwaltung"]


def test_mein_check_in_keeps_every_position_when_the_listing_has_no_sidebar_groups(monkeypatch):
    cu = "https://www.mein-check-in.de/flat-tenant/overview"
    listing = _R(
        '<a href="/flat-tenant/position-1">Pflegefachkraft</a>'
        '<a href="/flat-tenant/position-2">Verwaltungsfachkraft</a>', ok=True)
    monkeypatch.setattr(va, "get", _router({cu: listing}))
    rows = va.crawl_mein_check_in({"name": "Flat Tenant", "careers_url": cu})
    assert len(rows) == 2


def test_mein_check_in_reads_datePosted_employmentType_and_per_job_address_from_detail_microdata(monkeypatch):
    # Real mein-check-in detail pages carry a schema.org JobPosting as microdata spans (never
    # JSON-LD), tagging each job with its own branch address -- distinct from the clinic's town.
    cu = "https://www.mein-check-in.de/kna-online/overview"
    listing = _R('<a href="/kna-online/position-1">Pflegefachkraft</a>', ok=True)
    detail = _R(
        '<div itemscope itemtype="http://schema.org/JobPosting">'
        '<span itemprop="datePosted">2026-03-31T07:44:25+02:00</span>'
        '<span itemprop="employmentType">stellenangebote</span>'
        '<span itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">'
        '<span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">'
        '<span itemprop="addressLocality">Eichstätt</span>'
        '<span itemprop="postalCode">85072</span>'
        '<span itemprop="addressRegion">bavaria</span>'
        '</span></span></div>', ok=True, url="https://www.mein-check-in.de/kna-online/position-1")
    monkeypatch.setattr(va, "get", _router({cu: listing, "https://www.mein-check-in.de/kna-online/position-1": detail}))
    rows = va.crawl_mein_check_in({"name": "KNA", "town": "Kösching", "careers_url": cu})
    p = rows[0]["payload"]
    assert p["datePosted"] == "2026-03-31"
    assert p["employmentType"] == "stellenangebote"
    assert p["loc"] == [{"city": "Eichstätt", "plz": "85072", "region": "bavaria"}]


def test_mein_check_in_has_no_max_jobs_cap(monkeypatch):
    """TASK-72 AC#2: VENDOR_MAX_JOBS=300 used to silently truncate any board past 300 positions --
    removed (crawl_rexx already dropped its own copy after it lost 30 real postings the same way)."""
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)   # 305 rows * the real 0.2s politeness delay would be slow
    cu = "https://www.mein-check-in.de/big-tenant/overview"
    listing = _R("".join('<a href="/big-tenant/position-%d">Pflegefachkraft %d</a>' % (i, i) for i in range(305)), ok=True)
    monkeypatch.setattr(va, "get", _router({cu: listing}))
    rows = va.crawl_mein_check_in({"name": "Big Tenant", "careers_url": cu})
    assert len(rows) == 305


# ---------------------------------------------------------------------------
# dvinci
# ---------------------------------------------------------------------------
def _dvinci_job(position, category, town="Rosenheim"):
    return {"position": position, "jobPublicationURL": "https://romed-jobs.de/de/jobs/%s" % position,
            "jobOpening": {"location": town, "categories": ([{"name": category}] if category else [])}}


def test_dvinci_keeps_every_job_and_tags_each_ones_own_category(monkeypatch):
    # 2026-09 coverage-loss fix: hard-narrowing `jobs` to only the nursing category used to drop
    # genuine certified-nursing postings filed under other categories entirely (real RoMed/Bamberg
    # examples: "Berufsfachschule für Pflege", "B-PD-ANAE"/"OTK" functional-unit codes). Every job
    # here already came from the ONE list.json request made above (no per-job detail fetch), so
    # narrowing saved no network cost -- keep every job, and thread each one's own category name(s)
    # into payload["section_labels"] instead of using it to filter the fetch.
    cu = "https://romed-jobs.de/de/jobs"
    jobs = [_dvinci_job("Pflegefachkraft (m/w/d)", "Pflege- und Funktionsdienst"),
            _dvinci_job("Buchhalter (m/w/d)", "Verwaltung")]
    monkeypatch.setattr(va, "get", _router({"https://romed-jobs.de/jobPublication/list.json": _R(json_data=jobs, ok=True)}))
    rows = va.crawl_dvinci({"name": "RoMed", "careers_url": cu})
    assert {r["payload"]["title"] for r in rows} == {"Pflegefachkraft (m/w/d)", "Buchhalter (m/w/d)"}
    by_title = {r["payload"]["title"]: r["payload"] for r in rows}
    assert by_title["Pflegefachkraft (m/w/d)"]["section_labels"] == ["Pflege- und Funktionsdienst"]
    assert by_title["Buchhalter (m/w/d)"]["section_labels"] == ["Verwaltung"]


def test_dvinci_keeps_everything_when_no_category_is_nursing(monkeypatch):
    cu = "https://romed-jobs.de/de/jobs"
    jobs = [_dvinci_job("Facharzt (m/w/d)", "Ärztlicher Dienst"),
            _dvinci_job("Buchhalter (m/w/d)", "Verwaltung")]
    monkeypatch.setattr(va, "get", _router({"https://romed-jobs.de/jobPublication/list.json": _R(json_data=jobs, ok=True)}))
    rows = va.crawl_dvinci({"name": "RoMed", "careers_url": cu})
    assert len(rows) == 2


# TASK-83: jobPublicationURL sometimes carries a trailing /<slug> and sometimes doesn't for the SAME
# job (confirmed live 2026-09-22: Bamberg/Fuerth/Neumarkt each stored the same job twice, once per
# shape, 5 duplicate ids each) -- parse_dvinci normalizes both to the id-only form so they collapse
# onto the same source_ref (unique(source_id, source_ref), sql/001_schema.sql:112) instead of two rows.
def test_parse_dvinci_normalizes_id_slug_url_to_the_bare_id_form():
    j_with_slug = {"position": "Pflegefachkraft", "jobOpening": {},
                   "jobPublicationURL": "https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs/52664/pflegefachkraft-mwd-dialyse"}
    j_bare = {"position": "Pflegefachkraft", "jobOpening": {},
              "jobPublicationURL": "https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs/52664"}
    p_slug = va.parse_dvinci(j_with_slug, "Org", "https://x/list.json")
    p_bare = va.parse_dvinci(j_bare, "Org", "https://x/list.json")
    assert p_slug["url"] == p_bare["url"] == "https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs/52664"


# ---------------------------------------------------------------------------
# oracle: prefer the tenant's own jobs.feed.json (softgarden-fronted, e.g. St. Josef), else fall
# back to crawl_wp_jobs and backfill the fields that fallback's own parser never sets (TASK-31)
# ---------------------------------------------------------------------------
def test_oracle_prefers_the_jobs_feed_json_when_present(monkeypatch):
    feed = {"dataFeedElement": [{"item": {
        "@type": "JobPosting", "title": "Pflegefachkraft (m/w/d)",
        "url": "https://karriere.example.de/jobs/1/Pflegefachkraft/",
        "datePosted": "2026-07-01", "employmentType": "FULL_TIME",
    }}]}
    monkeypatch.setattr(va, "get", _router({"https://karriere.example.de/jobs.feed.json": _R(json_data=feed, ok=True)}))
    rows = va.crawl_oracle({"name": "St. Josef", "careers_url": "https://karriere.example.de/"})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (m/w/d)"]
    assert rows[0]["payload"]["datePosted"] == "2026-07-01"
    assert rows.board_total is None   # `feed` above carries no numberOfItems field


def test_oracle_carries_the_feeds_own_number_of_items_as_board_total(monkeypatch):
    """TASK-88 AC#1: same schema.org DataFeed shape softgarden.fetch_feed reads numberOfItems from."""
    feed = {"numberOfItems": 1, "dataFeedElement": [{"item": {
        "@type": "JobPosting", "title": "Pflegefachkraft (m/w/d)",
        "url": "https://karriere.example.de/jobs/1/Pflegefachkraft/"}}]}
    monkeypatch.setattr(va, "get", _router({"https://karriere.example.de/jobs.feed.json": _R(json_data=feed, ok=True)}))
    rows = va.crawl_oracle({"name": "St. Josef", "careers_url": "https://karriere.example.de/"})
    assert rows.board_total == 1


def test_oracle_falls_back_to_wp_jobs_and_backfills_missing_fields(monkeypatch):
    # no jobs.feed.json on this tenant (404, e.g. Klinikum FFB/Altmühlfranken) -- falls through to
    # the generic WordPress walk, whose own rows never carry employmentType/datePosted (parse_job_page
    # has no such extraction, see crawl_wp_jobs's docstring); crawl_oracle must backfill both.
    monkeypatch.setattr(va, "get", _router({}))  # feed probe 404s; any fallback GET the enrichment makes 404s too
    fallback_rows = [va.row("klinikum.example.de", "https://klinikum.example.de/stellenangebote/a/",
                             {"title": "Pflegefachkraft (m/w/d)", "url": "https://klinikum.example.de/stellenangebote/a/",
                              "description": "Wir suchen Sie in Vollzeit."}, "wp_jobs")]
    monkeypatch.setattr(va, "crawl_wp_jobs", lambda c, session=None: fallback_rows)
    rows = va.crawl_oracle({"name": "Klinikum", "careers_url": "https://klinikum.example.de/stellenangebote/"})
    assert rows[0]["payload"]["employmentType"] == "Vollzeit"


# ---------------------------------------------------------------------------
# _wp_job_rows: an ungendered <h1>/<title> is parse_job_page's own weak fallback (real, if rare, for
# an odd-titled posting) -- but the same weak fallback is how a department/category INDEX page got
# stored as a fake posting under its own <h1> label (2026-09-22 remediation round, reviewer finding
# #2). Confirmed live, clinic 36202/csj.de: .../stellenangebote/alle-stellenangebote stored as a
# posting titled 'Stellenangebote', so its own 20+ real nursing postings one hop behind it were
# never read (17 rows, 10 of them index pages -> fixed: 32 rows, all real postings).
# ---------------------------------------------------------------------------
def test_wp_job_rows_walks_an_ungendered_index_page_instead_of_storing_it_as_a_fake_posting(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    index_url = "https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote"
    job1 = "https://csj.de/beruf-und-karriere/berufsfelder/alle-stellenangebote/pflegedienst/notaufnahme"
    job2 = "https://csj.de/beruf-und-karriere/berufsfelder/alle-stellenangebote/pflegedienst/allgemeinstation"
    index_html = ('<h1>Stellenangebote</h1>'
                  f'<a href="{job1}">Pflegefachkraft (m/w/d) fuer die Notaufnahme</a>'
                  f'<a href="{job2}">Pflegefachkraft (m/w/d) fuer die Allgemeinstation</a>')
    job1_html = '<h1>Pflegefachkraft (m/w/d) fuer die Notaufnahme</h1><p>Jetzt bewerben.</p>'
    job2_html = '<h1>Pflegefachkraft (m/w/d) fuer die Allgemeinstation</h1><p>Jetzt bewerben.</p>'
    mapping = {index_url: _R(index_html, index_url), job1: _R(job1_html, job1), job2: _R(job2_html, job2)}
    monkeypatch.setattr(va, "get", _router(mapping))

    rows = va._wp_job_rows([index_url], {"name": "St. Josef Regensburg"}, "csj.de", None)

    titles = sorted(r["payload"]["title"] for r in rows)
    assert titles == ["Pflegefachkraft (m/w/d) fuer die Allgemeinstation", "Pflegefachkraft (m/w/d) fuer die Notaufnahme"]
    assert "Stellenangebote" not in titles     # the index page itself never becomes a row


def test_wp_job_rows_still_keeps_a_genuine_ungendered_title_with_no_further_job_links(monkeypatch):
    """The other half of the same fix: an ungendered title is only reinterpreted as a listing when the
    page actually links MORE THAN ONE further job-shaped page of its own -- a genuine (if oddly
    titled) single posting with no such links, or only one incidental related-job link, still keeps
    its own row instead of being silently dropped."""
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    url = "https://klinikum.example.de/karriere/jobs/quereinstieg"
    html = '<h1>Quereinstieg Pflege</h1><p>Jetzt bewerben.</p>'
    monkeypatch.setattr(va, "get", _router({url: _R(html, url)}))

    rows = va._wp_job_rows([url], {"name": "Klinikum"}, "klinikum.example.de", None)

    assert [r["payload"]["title"] for r in rows] == ["Quereinstieg Pflege"]


def test_enrich_wp_fallback_fields_does_not_refetch_a_row_whose_date_parse_job_page_already_found(monkeypatch):
    """TASK-85 AC#6: parse_job_page's non-JSON-LD branch used to leave datePosted empty, so this
    function re-fetched the SAME detail page a second time on every single row just to read the one
    meta tag it could have read on the first fetch -- confirmed live: karriere.ameos.eu, 0/766 rows
    had a date before this fix, ~doubling total requests on a 778-job board (one run killed at 17
    minutes, still running)."""
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    detail = _R('<h1>Pflegefachkraft (m/w/d)</h1><p>Bewerben. Vollzeit.</p>'
                '<meta itemprop="datePosted" content="2026-08-15">',
                url="https://karriere.ameos.eu/stelle/1", ok=True)
    calls = []
    monkeypatch.setattr(va, "get", _router({"https://karriere.ameos.eu/stelle/1": detail}, calls))
    rows = va._wp_job_rows(["https://karriere.ameos.eu/stelle/1"], {"name": "AMEOS"}, "karriere.ameos.eu", None)
    assert calls == ["https://karriere.ameos.eu/stelle/1"]                    # exactly one fetch so far
    out = va._enrich_wp_fallback_fields(rows)
    assert out[0]["payload"]["datePosted"] == "2026-08-15"
    assert calls == ["https://karriere.ameos.eu/stelle/1"]                    # _enrich must not refetch it


# ---------------------------------------------------------------------------
# _sane_date / parse_job_page: a JSON-LD epoch placeholder must not freeze a posting as ancient
# forever (TASK-73 AC7). Confirmed live 2026-09-18: 9 kbo.de + 2 frg-kliniken.de open postings.
# ---------------------------------------------------------------------------
def test_sane_date_rejects_the_unix_epoch_placeholder():
    assert va._sane_date("1970-01-01T00:00:00Z") is None
    assert va._sane_date("1969-12-31") is None


def test_sane_date_keeps_a_real_date():
    assert va._sane_date("2026-07-01T08:00:00+02:00") == "2026-07-01"


def test_sane_date_keeps_absent_and_malformed_values_as_before():
    assert va._sane_date(None) is None
    assert va._sane_date("") is None
    assert va._sane_date("not-a-date") == "not-a-date"   # unchanged from the pre-fix slice-only behaviour


def test_parse_job_page_drops_an_epoch_placeholder_datepostet_instead_of_freezing_freshness():
    html = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Pflegefachkraft (m/w/d)",'
            '"datePosted": "1970-01-01T00:00:00.000Z", "description": "Wir suchen."}</script>')
    j = va.parse_job_page(html, "https://kbo.de/karriere/jobs/1", "kbo")
    assert j["datePosted"] is None


def test_parse_job_page_keeps_a_real_dateposted():
    html = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Pflegefachkraft (m/w/d)",'
            '"datePosted": "2026-05-04", "description": "Wir suchen."}</script>')
    j = va.parse_job_page(html, "https://klinik.example.de/jobs/1", "Klinik")
    assert j["datePosted"] == "2026-05-04"


def test_parse_job_page_reads_dateposted_from_itemprop_when_no_jsonld():
    """TASK-85 AC#6: this is the branch a JSON-LD-less TYPO3 board (AMEOS: bare itemprop meta, no
    JobPosting block at all) always takes -- reading the date here, off the SAME already-fetched
    response, is what lets _enrich_wp_fallback_fields skip re-fetching every row a second time just
    to find it (see the end-to-end test next to _enrich_wp_fallback_fields below)."""
    html = ('<h1>Pflegefachkraft (m/w/d)</h1><p>Bewerben Sie sich jetzt.</p>'
            '<meta itemprop="datePosted" content="2026-08-15">')
    j = va.parse_job_page(html, "https://karriere.ameos.eu/stelle/1", "AMEOS")
    assert j["datePosted"] == "2026-08-15"


# ---------------------------------------------------------------------------
# NOT_JOB_PATH: a TYPO3 news/press/blog/event single-record view uses the exact same bare
# /detail/<id> shape as a job posting (TASK-73 AC9). Confirmed live 2026-09-18:
# klinikum-memmingen.de's /aktuelles/detail/ news archive, 31 fake nursing postings.
# ---------------------------------------------------------------------------
def test_not_job_path_excludes_typo3_news_press_blog_event_detail_pages():
    for path in ["/aktuelles/detail/1234-jubilaeum.html", "/presse/detail/99-pressemitteilung",
                 "/blog/detail/5-ratgeber", "/veranstaltungen/detail/7-tag-der-offenen-tuer",
                 "/termine/detail/3", "/news/detail/42"]:
        assert va.JOB_PATH.search(path), f"should still look job-shaped by URL alone: {path}"
        assert va.NOT_JOB_PATH.search(path), f"should be excluded as a non-job detail page: {path}"


def test_not_job_path_still_allows_real_karriere_detail_pages():
    for path in ["/karriere-detail/Ottobeuren/Pflegefachkraft-mwd/2612", "/karriere/detail/42-pflegefachkraft",
                 "/stellenangebote/detail/7"]:
        assert va.JOB_PATH.search(path) and not va.NOT_JOB_PATH.search(path), path


# TASK-84 AC2: a medical-glossary entry is the same TYPO3 /detail/ shape one folder over from the
# news/press/blog cases above -- confirmed live 2026-09-21, klinikum-msp.de's own
# /patienten-besucher/glossar/detail/fusspflege stored as an open nursing posting.
def test_not_job_path_excludes_glossary_detail_pages():
    path = "/patienten-besucher/glossar/detail/fusspflege"
    assert va.JOB_PATH.search(path), "should still look job-shaped by URL alone"
    assert va.NOT_JOB_PATH.search(path), "should be excluded as a non-job detail page"


# Regression (2026-09-22 remediation round, reviewer finding #5): the excluded word does not have to
# sit immediately before /detail/ -- a news section can nest its own /detail/ view one folder deeper
# still. Confirmed live: anregiomed.de postings 10453/10454/10455/10746 (clinic 56101), four real
# press releases ("15.000 Euro zur Foerderung...", "Mediroth spendet Reanimationspuppe", ...) stored
# as open nursing postings, all under /aktuelles/neuigkeiten/detail/... -- "neuigkeiten" sits
# immediately before /detail/, "aktuelles" one folder further out, which the old adjacency-only
# pattern never saw.
def test_not_job_path_excludes_a_news_detail_page_nested_a_folder_deeper_than_the_section_word():
    for path in ["/aktuelles/neuigkeiten/detail/15000-euro-zur-foerderung-des-klinikums-ansbach/",
                 "/aktuelles/neuigkeiten/detail/mediroth-spendet-reanimationspuppe/",
                 "/aktuelles/neuigkeiten/detail/foerderverein-spendet-transportstuehle/",
                 "/aktuelles/neuigkeiten/detail/kleine-implantate-grosse-wirkung/"]:
        assert va.JOB_PATH.search(path), f"should still look job-shaped by URL alone: {path}"
        assert va.NOT_JOB_PATH.search(path), f"should be excluded as a non-job detail page: {path}"


def test_find_job_urls_drops_typo3_news_detail_pages_confirmed_klinikum_memmingen(monkeypatch):
    base = "https://www.klinikum-memmingen.de"
    sitemap = _R(
        "<urlset>"
        "<url><loc>https://www.klinikum-memmingen.de/karriere/stellenangebote/detail/1-pflegefachkraft</loc></url>"
        "<url><loc>https://www.klinikum-memmingen.de/aktuelles/detail/99-neubau-eroeffnet</loc></url>"
        "</urlset>", ok=True)
    monkeypatch.setattr(va, "get", _router({base + "/robots.txt": _R(ok=False),
                                            base + "/sitemap.xml": sitemap}))
    found = va.find_job_urls(base)
    assert found == ["https://www.klinikum-memmingen.de/karriere/stellenangebote/detail/1-pflegefachkraft"]


# ---------------------------------------------------------------------------
# _job_link_pairs / _widget_endpoint_job_links: a job-looking link embedded in one board's own HTML
# must not be followed onto an unrelated site (TASK-73 AC10). Confirmed live 2026-09-18:
# psychiatrie-werneck.de's own career page linking 8 koenig-ludwig-haus.de rows.
# ---------------------------------------------------------------------------
def test_job_link_pairs_drops_a_link_to_an_unrelated_site():
    html = ('<a href="https://psychiatrie-werneck.de/karriere/stellenangebote/1">Pflegefachkraft (m/w/d) bei uns</a>'
            '<a href="https://koenig-ludwig-haus.de/karriere/stellenangebote/9">Pflegefachkraft (m/w/d) dort</a>')
    pairs = va._job_link_pairs(html, "https://psychiatrie-werneck.de/karriere/")
    assert list(pairs) == ["https://psychiatrie-werneck.de/karriere/stellenangebote/1"]


def test_job_link_pairs_follows_an_off_board_host_linked_repeatedly():
    # TASK-85 AC#3, confirmed live 2026-09-21: waldkrankenhaus.de's own careers page links
    # jobs.malteser.de 31 times (13 of them nursing) -- _same_board alone dropped every one, the
    # exact same guard that (correctly) drops the test above's single stray link to a DIFFERENT,
    # unrelated hospital's own site. The two are told apart by repetition: a host linked only once is
    # a stray cross-reference, a host linked repeatedly for distinct postings is where this board's
    # own vacancies actually live.
    html = "".join(
        '<a href="https://jobs.malteser.de/de/job-offer-list/job-detail/Job-%d.html">Pflegefachkraft (m/w/d) %d</a>' % (i, i)
        for i in range(3))
    pairs = va._job_link_pairs(html, "https://www.waldkrankenhaus.de/karriere/unsere-stellenangebote.html")
    assert len(pairs) == 3
    assert all(u.startswith("https://jobs.malteser.de/") for u in pairs)


def test_job_link_pairs_keeps_a_link_reached_via_the_boards_own_redirect():
    # base already reflects the post-redirect host (e.g. a vanity domain that 302s onto the ATS'
    # own host) -- a same-host link found there is not "off-board".
    html = '<a href="https://tenant.softgarden.io/job/1">Pflegefachkraft (m/w/d)</a>'
    pairs = va._job_link_pairs(html, "https://tenant.softgarden.io/de/vacancies")
    assert list(pairs) == ["https://tenant.softgarden.io/job/1"]


def test_widget_endpoint_job_links_drops_off_board_links(monkeypatch):
    cu_resp = _R('<div data-url="/ajax/joblist">x</div>', url="https://psychiatrie-werneck.de/karriere/", ok=True)
    widget = _R('<a href="https://psychiatrie-werneck.de/karriere/stellenangebote/1">a</a>'
                '<a href="https://koenig-ludwig-haus.de/karriere/stellenangebote/9">b</a>',
                url="https://psychiatrie-werneck.de/ajax/joblist", ok=True)
    monkeypatch.setattr(va, "get", _router({"https://psychiatrie-werneck.de/ajax/joblist": widget}))
    out = va._widget_endpoint_job_links(cu_resp)
    assert out == ["https://psychiatrie-werneck.de/karriere/stellenangebote/1"]


# --- TASK-55: the widget config lives inside an HTML attribute, so it is entity-escaped ----------

def _feldafing_sample():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples",
                     "klinik_feldafing_stellenangebote_sample.html")
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_smartrecruiters_finds_tenant_when_the_widget_config_is_html_escaped(monkeypatch):
    """Benedictus Krankenhaus Feldafing (18813) and its two Artemed siblings (16228, 16235) put the
    same widget JSON in a data-widget ATTRIBUTE, so every quote arrives as &quot; and the plain
    company_code regex saw nothing. The only other b-ite/SmartRecruiters marker on the page is a
    b-ite loader mount for 'artemed-8:niiid' -- the BITE recruiting-assistant chatbot, which ships
    createClient({key:""}) and has no postings API at all, so nothing else can rescue the board."""
    cu = "https://www.klinik-feldafing.de/karriere/stellenangebote"
    base = "https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings"
    page = _R(json_data={"totalFound": 1, "content": [_sr_posting("1", "Pflegefachkraft (m/w/d)", "1", "Pflegedienst")]})
    mapping = {cu: _R(_feldafing_sample(), url=cu, ok=True), "%s?limit=100&offset=0" % base: page}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_smartrecruiters({"name": "Benedictus Krankenhaus Feldafing", "careers_url": cu})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (m/w/d)"]


def test_smartrecruiters_ident_reads_the_escaped_and_the_plain_form():
    assert va._smartrecruiters_ident(_feldafing_sample()) == "ArtemedSE"
    assert va._smartrecruiters_ident('<div data-widget=\'widget({"company_code": "X1"})\'>') == "X1"
    assert va._smartrecruiters_ident("<p>no widget here</p>") is None


def test_smartrecruiters_pages_past_the_old_1000_offset_ceiling(monkeypatch):
    """TASK-14: crawl_smartrecruiters carried `ceiling = 1000` and looped `while offset < ceiling`,
    so a tenant past 1000 postings stopped on that constant and reported the partial board as a
    normal success. The board's own totalFound (or a short page) is the only stop now."""
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)   # 1204 detail fetches * the real politeness delay
    base = "https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings"
    total = 1204
    posts = [_sr_posting(str(i), "Pflegefachkraft (m/w/d) %d" % i, "3484565", "Pflegedienst") for i in range(total)]
    mapping = {"%s?limit=100&offset=%d" % (base, off): _R(json_data={"totalFound": total, "content": posts[off:off + 100]})
               for off in range(0, total, 100)}
    for p in posts:                                  # per-posting detail fetch (description + real postingUrl)
        mapping["%s/%s" % (base, p["id"])] = _R(json_data={"postingUrl": p["ref"], "jobAd": {"sections": {}}})
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_smartrecruiters({"name": "Big Tenant", "careers_url": "https://jobs.smartrecruiters.com/ArtemedSE"})
    assert len(rows) == total


def test_paginated_job_links_walks_past_the_old_200_page_ceiling(monkeypatch):
    """TASK-14: _paginated_job_links carried max_pages=200 that no caller ever set. A board with
    more pages than someone's guess was read short and returned as a complete list -- the site's own
    "no next page" link is the only stop."""
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    pages = 260
    mapping = {}
    for i in range(pages):
        nxt = '<a rel="next" href="/list?p=%d">weiter</a>' % (i + 1) if i + 1 < pages else ""
        mapping["https://x.example/list?p=%d" % i] = _R(
            '<a href="/stellenangebote/job-%d">Pflegefachkraft (m/w/d) %d</a>%s' % (i, i, nxt),
            url="https://x.example/list?p=%d" % i, ok=True)
    monkeypatch.setattr(va, "get", _router(mapping))
    urls = va._paginated_job_links("https://x.example/list?p=0")
    assert len(urls) == pages


def test_group_portal_and_wp_jobs_take_no_item_ceiling_argument():
    """TASK-14 AC#1/#2: crawl_group_portal and crawl_wp_jobs both carried a max_jobs=100_000 item
    ceiling that broke the walk and sliced the result with no truncated signal anywhere. No caller
    ever passed it, so the only thing it could do was silently shorten a board."""
    import inspect
    for fn in (va.crawl_group_portal, va.crawl_wp_jobs, va._wp_job_rows, va._paginated_job_links):
        assert "max_jobs" not in inspect.signature(fn).parameters, fn.__name__
        assert "max_pages" not in inspect.signature(fn).parameters, fn.__name__


def test_txt_handles_the_non_string_json_ld_shapes_that_aborted_whole_board_walks():
    """Live 2026-09-21, scheduled run 108: muenchen-klinik.de emits "postalCode":81545 as a JSON
    number and komm-ins-klinikland.de emits a field as an array. Both raised TypeError inside
    _txt's re.sub on the FIRST posting page, which aborted the entire board walk -- three retry
    passes, three identical failures, both Munich clinics left holding zero real vacancies.

    JSON-LD permits all three shapes (bare scalar, array, typed {"@value": ...}), so reading them
    is correct parsing, not a defensive guard."""
    assert va._txt(81545) == "81545"                            # the live int crash
    assert va._txt(["Vollzeit", "Teilzeit"]) == "Vollzeit, Teilzeit"   # the live list crash
    assert va._txt({"@value": "München"}) == "München"
    assert va._txt({"@type": "Thing"}) is None                  # an object is not text
    assert va._txt([]) is None
    assert va._txt(None) is None
    assert va._txt("<b>Pflege</b>&nbsp;kraft") == "Pflege kraft"  # unchanged for the normal case
    assert va._txt(0) == "0"                                    # falsy but real


# ---------------------------------------------------------------------------
# crawl_erecruiter: the registered careers_url is sometimes only a wrapper page linking out to the
# real board on a same-domain "jobs." subdomain (TASK-85 AC#4/AC#5)
# ---------------------------------------------------------------------------
def test_crawl_erecruiter_follows_a_same_domain_jobs_subdomain_link_from_a_wrapper_page(monkeypatch):
    """TASK-85 AC#4/AC#5: klinikum-ab-alz.de/karriere/ links jobs.klinikum-ab-alz.de/Jobs (63
    postings; the audit misread this as a Knockout SPA needing a render rung -- it is this same
    eRecruiter engine one hop away). bezirkskliniken-schwaben.de's own careers page does the same
    thing for jobs.bezirkskliniken-schwaben.de/Jobs (56 postings, the audit's "inline JSON model").
    Neither link matches JOB_PATH (a bare "/Jobs", no trailing slash) or carries a gender-marked
    anchor, so the generic job-link scan never follows either on its own."""
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    wrapper = "https://klinikum-ab-alz.de/karriere/"
    board = "https://jobs.klinikum-ab-alz.de/Jobs"
    wrapper_page = _R('<a href="%s">Offene Stellen</a><a href="/impressum/">Impressum</a>' % board, url=wrapper, ok=True)
    payload = ('{"TotalJobsCount": 1, "Jobs": [{"Id": 9, "Title": "Pflegefachkraft (m/w/d)", "SubTitle": "",'
               ' "Location": "Aschaffenburg", "Date": "01.09.2026"}], "Pagination": {"IsPagination": false}}')
    board_page = _R('<script>window.jobList = new JobList($a, $b, %s);</script>' % payload, url=board, ok=True)
    mapping = {wrapper: wrapper_page, board: board_page, "https://jobs.klinikum-ab-alz.de/Job/9": _R(ok=False)}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_erecruiter({"name": "Klinikum AB-ALZ", "careers_url": wrapper})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (m/w/d)"]
    assert rows.board_total == 1


def test_crawl_erecruiter_does_not_chase_a_same_domain_link_when_its_own_page_already_has_the_board():
    """No wasted probe when the landing page already carries the JobList JSON itself (the common
    case, e.g. tests/test_erecruiter_board_total.py's fixtures) -- _erecruiter_host_resp must return
    the same response object unchanged, not fetch anything else."""
    payload = '{"TotalJobsCount": 0, "Jobs": [], "Pagination": {"IsPagination": false}}'
    cu_resp = _R('<script>window.jobList = new JobList($a, $b, %s);</script>' % payload,
                 url="https://jobs.example.de/Jobs", ok=True)
    assert va._erecruiter_host_resp(cu_resp) is cu_resp


# --- TASK-102: talention's own jobLocation.addressLocality mixes clean towns with facility labels ------

def test_clean_talention_city_extracts_the_one_pool_town_it_names():
    pool = ["Weiden", "Tirschenreuth", "Kemnath"]
    assert va.clean_talention_city("Klinikum Weiden Zentrale Notaufnahme", pool) == "Weiden"
    assert va.clean_talention_city("Krankenhaus Tirschenreuth | Innere Medizin", pool) == "Tirschenreuth"
    assert va.clean_talention_city("Weiden, Bayern, Deutschland", pool) == "Weiden"  # already clean, unchanged shape


def test_clean_talention_city_leaves_ambiguous_or_out_of_pool_strings_alone():
    pool = ["Weiden", "Tirschenreuth", "Kemnath"]
    # Names TWO pool towns -- no match beats a wrong match (decision-5).
    assert va.clean_talention_city("Krankenhaus Tirschenreuth und Klinikum Weiden", pool) == "Krankenhaus Tirschenreuth und Klinikum Weiden"
    # Names no pool town at all.
    assert va.clean_talention_city("Steinwaldklinik Erbendorf | Geriatrische Rehabilitation", pool) == "Steinwaldklinik Erbendorf | Geriatrische Rehabilitation"
    assert va.clean_talention_city("Kliniken Nordoberpfalz AG", pool) == "Kliniken Nordoberpfalz AG"
    # "Weidenberg" must not false-positive on "Weiden" -- a real, different Bavarian town.
    assert va.clean_talention_city("Klinikum Weidenberg", pool) == "Klinikum Weidenberg"
    assert va.clean_talention_city(None, pool) is None


def test_extract_standort_city_reads_a_real_town_from_plain_prose():
    # TASK-118: meinkrankenhaus2030.de has no JSON-LD and no icon-fact location at all -- the real
    # work site is only ever stated in plain body prose.
    towns = {"weilheim", "schongau", "münchen"}
    desc = ("Für unsere OP-Abteilung am Standort Weilheim suchen wir zum nächstmöglichen "
            "Zeitpunkt eine/n Operations-Technischen-Assistenten (w/m/d).")
    assert va.extract_standort_city(desc, towns) == "Weilheim"


def test_extract_standort_city_rejects_a_standort_that_names_no_real_town():
    towns = {"weilheim", "schongau"}
    assert va.extract_standort_city("An unserem Standort Teamgeist suchen wir Verstärkung.", towns) is None
    assert va.extract_standort_city("Ohne jede Standortangabe.", towns) is None
    assert va.extract_standort_city(None, towns) is None
    assert va.extract_standort_city("am Standort Weilheim", None) is None
    assert va.extract_standort_city("am Standort Weilheim", set()) is None
