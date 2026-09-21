"""Offline regressions for the three "JS widget" engines whose listing plain HTTP used to miss
entirely (TASK-49/50/77). Mocked HTTP via crawlers.vendor_adapters.get / .post_json, no network.

Each of the three boards returned 0 rows through the generic sitemap/anchor walk before this, and
reported that as a clean empty success:

  asklepios   www.asklepios.com/karriere/jobs is a Next.js shell; the only job source is a
              same-origin POST /api/search whose per-tenant search id sits in the page's own HTML.
              Live 2026-09-21: 1398 postings, 90 of them at the 7 Bavarian Asklepios sites.
  eRecruiter  jobs.<clinic>/Jobs ships the whole board as JSON inside `new JobList(...)`, but the
              markup around it is a handlebars template whose plain-HTML form still carries the
              literal "/Job/{{Id}}" placeholder -- so not one anchor matches JOB_PATH. Live:
              jobs.bezirkskliniken-schwaben.de 57, jobs.klinikum-ab-alz.de 62.
  concludis   a real concludis tenant embeds the vendor's widget: the clinic page ships an empty
              container plus a loader naming the tenant host and board id. Live:
              swmbrk.concludis.de board 36, 18 postings.

Plus the JSON-LD entity-decoding fix the eRecruiter board exposed (see the last test).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402


def _poster(pages, calls=None):
    """Fake post_json: `pages` is the list of /api/search bodies to answer with, in order."""
    seq = list(pages)

    def fake_post(u, payload, timeout=30, session=None):
        if calls is not None:
            calls.append((u, payload))
        return _R(url=u, json_data=seq.pop(0)) if seq else _R(url=u, ok=False)
    return fake_post


# --- asklepios --------------------------------------------------------------------------------

ASKL_CU = "https://www.asklepios.com/karriere/jobs"
ASKL_SHELL = ('<html><body><div id="__next"></div>'
              '<script>{"restEndpoint":"/.rest/search/job/de/4bc0fdbd","documentType":"job"}</script>'
              '</body></html>')


def _askl_item(i, **extra):
    d = {"id": str(i), "title": "Pflegefachkraft (w/m/d) %d" % i, "location": "Gauting", "plz": "82131",
         "company": "Asklepios Lungenklinik Gauting", "qualifications": "Examen",
         "publicationDate": "18.09.26, 00:00", "workingTime": "Vollzeit/Teilzeit",
         "workareas": ["Pflege- und Funktionsdienst"],
         "jobLink": "https://karriere.asklepios.com/pflegefachkraft-de-j%d.html" % i}
    d.update(extra)
    return d


def test_asklepios_walks_every_page_of_its_own_search_api(monkeypatch):
    calls = []
    monkeypatch.setattr(va, "get", _router({ASKL_CU: _R(ASKL_SHELL, url=ASKL_CU)}))
    monkeypatch.setattr(va, "post_json", _poster([
        {"count": 3, "items": [_askl_item(1), _askl_item(2)]},
        {"count": 3, "items": [_askl_item(3)]},
    ], calls))
    rows = va.crawl_asklepios({"name": "Asklepios Lungenklinik Gauting", "careers_url": ASKL_CU})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (w/m/d) 1",
                                                     "Pflegefachkraft (w/m/d) 2",
                                                     "Pflegefachkraft (w/m/d) 3"]
    assert [p["o"] for _, p in calls] == [0, 2]          # offset follows what the board returned
    assert calls[0][1]["searchEndpoint"] == "/.rest/search/job/de/4bc0fdbd"
    assert calls[0][0] == "https://www.asklepios.com/api/search"


def test_asklepios_row_carries_city_plz_date_and_the_boards_own_workarea(monkeypatch):
    monkeypatch.setattr(va, "get", _router({ASKL_CU: _R(ASKL_SHELL, url=ASKL_CU)}))
    monkeypatch.setattr(va, "post_json", _poster([{"count": 1, "items": [_askl_item(7)]}]))
    p = va.crawl_asklepios({"name": "seed", "careers_url": ASKL_CU})[0]["payload"]
    assert p["loc"] == [{"city": "Gauting", "plz": "82131", "region": None}]
    assert p["datePosted"] == "2026-09-18"               # from "18.09.26, 00:00", not ISO
    assert p["employmentType"] == "Vollzeit/Teilzeit"
    assert p["section_labels"] == ["Pflege- und Funktionsdienst"]
    assert p["org"] == "Asklepios Lungenklinik Gauting" and p["org_source"] is None
    assert p["url"] == "https://karriere.asklepios.com/pflegefachkraft-de-j7.html"


def test_asklepios_stops_when_a_page_repeats_ids_instead_of_looping(monkeypatch):
    """The stated count is one end signal; a page that brings no new id is the other. Neither is a
    ceiling -- without the second, a board whose `o` parameter is ignored would page forever."""
    monkeypatch.setattr(va, "get", _router({ASKL_CU: _R(ASKL_SHELL, url=ASKL_CU)}))
    monkeypatch.setattr(va, "post_json", _poster([{"count": 99, "items": [_askl_item(1)]}] * 4))
    rows = va.crawl_asklepios({"name": "seed", "careers_url": ASKL_CU})
    assert len(rows) == 1


def test_asklepios_returns_nothing_when_the_page_names_no_search_endpoint(monkeypatch):
    monkeypatch.setattr(va, "get", _router({ASKL_CU: _R("<html>no widget here</html>", url=ASKL_CU)}))
    monkeypatch.setattr(va, "post_json", _poster([{"count": 1, "items": [_askl_item(1)]}]))
    assert va.crawl_asklepios({"name": "seed", "careers_url": ASKL_CU}) == []


# --- eRecruiter -------------------------------------------------------------------------------

ER_CU = "https://jobs.bezirkskliniken-schwaben.de/Jobs"


def _erecruiter_page(jobs):
    """The shape the engine really ships: a handlebars template with the literal /Job/{{Id}}
    placeholder (so no anchor is a job link) plus the whole board as the JobList constructor's
    third argument."""
    return ('<html><body>'
            '<script id="jobListTemplate" type="text/template">'
            '<a href="/Job/{{Id}}">{{Title}}</a></script>'
            '<script>jQuery(function ($) { window.jobList = new JobList('
            '$("#jobListPlaceholder"), $("#jobListTemplate"), %s); });</script>'
            '</body></html>' % json.dumps({"TotalJobsCount": len(jobs), "Jobs": jobs,
                                           "Pagination": {"IsPagination": False}}))


def _erecruiter_detail(title, locality, **extra):
    d = {"@context": "https://schema.org/", "@type": "JobPosting", "title": title,
         "datePosted": "2026-08-26", "employmentType": "1",
         "hiringOrganization": {"@type": "Organization", "name": "Bezirkskliniken Schwaben"},
         "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress",
                                                        "addressLocality": locality,
                                                        "postalCode": "89312"}}]}
    d.update(extra)
    return '<script type="application/ld+json">%s</script>' % json.dumps(d)


def test_erecruiter_reads_the_board_out_of_its_own_embedded_json(monkeypatch):
    jobs = [{"Id": 270777, "Title": "Pflegefachkraft (m/w/d)", "SubTitle": "in Vollzeit",
             "Location": "Günzburg", "Date": "21.09.2026"},
            {"Id": 271398, "Title": "Pflegefachhelfer (m/w/d)", "SubTitle": "",
             "Location": "Kaufbeuren", "Date": "01.08.2026"}]
    mapping = {ER_CU: _R(_erecruiter_page(jobs), url=ER_CU)}
    for j in jobs:
        u = "https://jobs.bezirkskliniken-schwaben.de/Job/%d" % j["Id"]
        mapping[u] = _R(_erecruiter_detail(j["Title"], "G&#252;nzburg"), url=u)
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_erecruiter({"name": "Bezirkskrankenhaus Augsburg", "careers_url": ER_CU})
    assert [r["payload"]["title"] for r in rows] == ["Pflegefachkraft (m/w/d)", "Pflegefachhelfer (m/w/d)"]
    assert [r["source_url"] for r in rows] == ["https://jobs.bezirkskliniken-schwaben.de/Job/270777",
                                               "https://jobs.bezirkskliniken-schwaben.de/Job/271398"]
    assert [r["payload"]["loc"][0] for r in rows] == [{"city": "Günzburg", "plz": "89312", "region": None}] * 2


def test_erecruiter_drops_the_engines_numeric_contract_code_from_employment_type(monkeypatch):
    """schema.org employmentType on this engine is its own internal id ("1"), meaningless outside
    its database -- the real Voll-/Teilzeit wording only exists in the board's own text."""
    jobs = [{"Id": 1, "Title": "Pflegefachkraft (m/w/d)", "SubTitle": "in Vollzeit",
             "Location": "Günzburg", "Date": "21.09.2026"}]
    mapping = {ER_CU: _R(_erecruiter_page(jobs), url=ER_CU),
               "https://jobs.bezirkskliniken-schwaben.de/Job/1":
                   _R(_erecruiter_detail("Pflegefachkraft (m/w/d)", "Günzburg"),
                      url="https://jobs.bezirkskliniken-schwaben.de/Job/1")}
    monkeypatch.setattr(va, "get", _router(mapping))
    p = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU})[0]["payload"]
    assert p["employmentType"] == "Vollzeit"
    assert p["datePosted"] == "2026-08-26"


def test_erecruiter_falls_back_to_the_list_row_when_a_detail_page_is_gone(monkeypatch):
    jobs = [{"Id": 5, "Title": "Pflegefachkraft (m/w/d)", "SubTitle": "in Teilzeit",
             "Location": "Kaufbeuren", "Date": "21.09.2026"}]
    monkeypatch.setattr(va, "get", _router({ER_CU: _R(_erecruiter_page(jobs), url=ER_CU)}))
    p = va.crawl_erecruiter({"name": "seed", "careers_url": ER_CU})[0]["payload"]
    assert p["loc"][0]["city"] == "Kaufbeuren"
    assert p["datePosted"] == "2026-09-21"               # from the list row's "21.09.2026"


def test_crawl_wp_jobs_delegates_to_erecruiter_by_capability(monkeypatch):
    """No registry label exists for this engine -- the generic dispatcher must recognise it on the
    careers page it has already fetched, the same contract beesite/hr4you use (TASK-40 AC#3)."""
    jobs = [{"Id": 9, "Title": "Pflegefachkraft (m/w/d)", "SubTitle": "", "Location": "Aschaffenburg",
             "Date": "21.09.2026"}]
    cu = "https://jobs.klinikum-ab-alz.de/Jobs"
    monkeypatch.setattr(va, "get", _router({cu: _R(_erecruiter_page(jobs), url=cu)}))
    rows = va.crawl_wp_jobs({"name": "Klinikum Aschaffenburg-Alzenau", "careers_url": cu})
    assert [r["collector"] for r in rows] == ["vendor-erecruiter-v1"]


# --- concludis widget -------------------------------------------------------------------------

CO_CU = "https://www.schwesternschaft-muenchen.de/stellenangebote/index.php"
CO_LIST = "https://swmbrk.concludis.de/prj/lst/?b=36&lang=de_DE&jsinclude=1"
CO_JOB = "https://swmbrk.concludis.de/prj/shw/abc_0/9950/Pflegefachkraft_m_w_d.htm?b=36"
CO_SHELL = ("<div id='concludis_swmbrk_stellenangebote'>Stellenangebote werden geladen ...</div>"
            "<script>(function(a,b,c,d,e,f,g){})(window,document,'script','concludis','swmbrk.concludis.de');"
            "concludis('setMainContainer', 'concludis_swmbrk_stellenangebote');"
            "concludis('setJobBoard', '36');concludis('setLanguage', 'de_DE');</script>")


def test_concludis_widget_reads_the_tenant_board_the_clinic_page_names(monkeypatch):
    listing = ('<div class="stellensum">1 Stellen gefunden</div>'
               '<div onclick="cJobboard.openJob(\'%s\');" id="line_9950">'
               '<span class="headerlink stellenlink">Pflegefachkraft (m/w/d)</span>'
               '<span class="kurzb">Umfang: Vollzeit</span></div>' % CO_JOB)
    detail = ('<script type="application/ld+json">%s</script>'
              % json.dumps({"@context": "http://schema.org", "@type": "JobPosting",
                            "title": "Pflegefachkraft als Hygienebeauftragter (m/w/d)",
                            "datePosted": "2026-09-16", "description": "<p>Willkommen</p>",
                            "jobLocation": {"@type": "Place",
                                            "address": {"addressLocality": "Grünwald",
                                                        "postalCode": "82031"}}}))
    calls = []
    monkeypatch.setattr(va, "get", _router({CO_CU: _R(CO_SHELL, url=CO_CU),
                                            CO_LIST: _R(listing, url=CO_LIST),
                                            CO_JOB + "&jsinclude=1": _R(detail, url=CO_JOB)}, calls))
    rows = va.crawl_concludis_widget({"name": "Rotkreuzklinikum München", "careers_url": CO_CU})
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["title"] == "Pflegefachkraft als Hygienebeauftragter (m/w/d)"
    assert p["loc"] == [{"city": "Grünwald", "plz": "82031", "region": None}]
    assert p["datePosted"] == "2026-09-16"
    assert CO_LIST in calls                              # host+board came off the clinic's own page
    # Without the jsinclude fragment the tenant 302s away from the posting (live 2026-09-21).
    assert CO_JOB + "&jsinclude=1" in calls


def test_concludis_widget_ignores_a_page_with_no_loader(monkeypatch):
    monkeypatch.setattr(va, "get", _router({CO_CU: _R("<html>plain career page</html>", url=CO_CU)}))
    assert va.crawl_concludis_widget({"name": "seed", "careers_url": CO_CU}) == []


def test_crawl_wp_jobs_delegates_to_the_concludis_widget_by_capability(monkeypatch):
    listing = ('<div onclick="cJobboard.openJob(\'%s\');">'
               '<span class="headerlink stellenlink">Pflegefachkraft (m/w/d)</span></div>' % CO_JOB)
    monkeypatch.setattr(va, "get", _router({CO_CU: _R(CO_SHELL, url=CO_CU),
                                            CO_LIST: _R(listing, url=CO_LIST)}))
    rows = va.crawl_wp_jobs({"name": "Rotkreuzklinikum München", "careers_url": CO_CU})
    assert [r["collector"] for r in rows] == ["vendor-concludis-widget-v1"]


# --- shared JSON-LD fix the eRecruiter board exposed --------------------------------------------

def test_jsonld_address_is_entity_decoded():
    """A board can HTML-escape inside the JSON string itself (live: jobs.bezirkskliniken-schwaben.de
    ships addressLocality "G&#252;nzburg"). Taken verbatim, no posting on such a board ever matches
    a registry town, so every one of them loses its location."""
    j = va.parse_job_page(_erecruiter_detail("Pflegefachkraft (m/w/d)", "G&#252;nzburg"),
                          "https://jobs.bezirkskliniken-schwaben.de/Job/1", "seed")
    assert j["loc"][0]["city"] == "Günzburg"
