"""Section-first fetching in crawlers/vendor_adapters.py. Mocked HTTP (via the module's own `get`
helper), no network. Each vendor gets one test proving it narrows to the nursing section when a
signal is present, and one proving today's full-fetch behaviour survives when it isn't."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402


class _R:
    def __init__(self, text="", url="", ok=True, json_data=None):
        self.text = text
        self.url = url
        self.ok = ok
        self.encoding = None
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


def test_personio_narrows_to_department_when_tenant_tags_it(monkeypatch):
    xml = _personio_xml([
        ("1", "Pflegefachkraft (m/w/d)", "Pflege"),
        ("2", "Verwaltungsfachkraft (m/w/d)", "Verwaltung"),
    ])
    monkeypatch.setattr(va, "get", _router({"https://acme.jobs.personio.de/xml": _R(xml, ok=True)}))
    rows = va.crawl_personio({"name": "Acme Klinik", "careers_url": "https://acme.jobs.personio.de/"})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (m/w/d)"]


def test_personio_keeps_everything_when_no_department_is_nursing(monkeypatch):
    xml = _personio_xml([
        ("1", "Augenarzt (m/w/d)", "Augenheilkunde"),
        ("2", "Verwaltungsfachkraft (m/w/d)", "Verwaltung"),
    ])
    monkeypatch.setattr(va, "get", _router({"https://acme.jobs.personio.de/xml": _R(xml, ok=True)}))
    rows = va.crawl_personio({"name": "Acme Klinik", "careers_url": "https://acme.jobs.personio.de/"})
    assert len(rows) == 2


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


# ---------------------------------------------------------------------------
# helix
# ---------------------------------------------------------------------------
def test_helix_narrows_via_the_berufsfeld_filter(monkeypatch):
    listing = _R(
        '<label for="category_e99edf">Pflege (1 Treffer)</label>'
        '<label for="category_abc123">Küche (3 Treffer)</label>'
        '<a href="/okh/jobad?prj=A1">Pflegefachkraft (m/w/d)</a>'
        '<a href="/okh/jobad?prj=B2">Koch (m/w/d)</a>',
        url="https://tenant.helixjobs.com/okh/joblist", ok=True)
    narrowed = _R('<a href="/okh/jobad?prj=A1">Pflegefachkraft (m/w/d)</a>',
                  url="https://tenant.helixjobs.com/okh/joblist?category%5B%5D=e99edf", ok=True)
    calls = []
    mapping = {
        "https://tenant.helixjobs.com/okh/joblist": listing,
        "https://tenant.helixjobs.com/okh/joblist?category%5B%5D=e99edf": narrowed,
    }
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_helix({"name": "X", "careers_url": "https://tenant.helixjobs.com/okh/joblist"})
    assert len(rows) == 1 and "prj=A1" in rows[0]["payload"]["url"]
    assert any("category%5B%5D=e99edf" in u for u in calls)


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
def test_mein_check_in_only_processes_positions_in_the_nursing_sidebar_group(monkeypatch):
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
    assert {r["payload"]["title"] for r in rows} == {"Pflegefachkraft Notaufnahme", "Gesundheits-/Krankenpfleger Intensivstation"}
    assert not any("position-500000" in u for u in calls)


def test_mein_check_in_keeps_every_position_when_the_listing_has_no_sidebar_groups(monkeypatch):
    cu = "https://www.mein-check-in.de/flat-tenant/overview"
    listing = _R(
        '<a href="/flat-tenant/position-1">Pflegefachkraft</a>'
        '<a href="/flat-tenant/position-2">Verwaltungsfachkraft</a>', ok=True)
    monkeypatch.setattr(va, "get", _router({cu: listing}))
    rows = va.crawl_mein_check_in({"name": "Flat Tenant", "careers_url": cu})
    assert len(rows) == 2


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
