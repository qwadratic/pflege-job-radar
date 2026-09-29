"""pflege_jobs/verify.py's escalation ladder -- 2026-09-18 crawler review found several mechanisms
that each made a wrong live/gone verdict permanent. Pure-function regression tests, no network
(pflege_jobs/verify.py's own docstring: "a status set without a real request does not count")."""
from pflege_jobs.verify import FRAGMENT_URL, GONE_MARKERS, IS_PDF, _bounced_to_list, decide, firecrawl_fetch, verify_one


# --- GONE_MARKERS: a bare "404" must not fire off page furniture -----------------------------

def test_gone_markers_no_longer_fires_on_a_bare_404_substring():
    for noise in ("<div id=\"widget-404\">", "class=\"error-404-icon\"",
                  "Tel: 089 404 5566", "asset-404a1b2c.js"):
        assert not GONE_MARKERS.search(noise), noise


def test_gone_markers_still_fires_on_a_real_404_phrase():
    for phrase in ("Error 404", "404 - Seite nicht gefunden", "404: Not Found", "Fehler 404 nicht gefunden"):
        assert GONE_MARKERS.search(phrase), phrase


def test_gone_markers_still_fires_on_the_german_phrases():
    assert GONE_MARKERS.search("Diese Stelle ist leider nicht mehr verfügbar.")
    assert GONE_MARKERS.search("Position has been filled")


# --- decide(): an empty title-token list is zero evidence, not confirmation -------------------
# TASK-150: the old unconditional "error" here (before this fix) never even looked at the body,
# so a title made entirely of generic words ("Pflegefachkraft (m/w/d)") could NEVER resolve to
# anything but 'error', no matter what escalation rung fetched it -- 89/3624 open postings stuck
# permanently. GONE_MARKERS is now checked against the body even with toks=[].

def test_decide_empty_toks_with_gone_marker_is_gone():
    for title in ("Pflegefachkraft (m/w/d)", "Gesundheits- und Krankenpfleger (m/w/d)"):
        assert decide(200, "Diese Stelle ist leider nicht mehr verfügbar.", title) == (
            "gone", 200, "200, no title token to confirm, but a gone-marker matched")


def test_decide_empty_toks_without_gone_marker_is_live_with_low_confidence_note():
    for title in ("Pflegefachkraft (m/w/d)", "Gesundheits- und Krankenpfleger (m/w/d)"):
        assert decide(200, "irrelevant body text", title) == (
            "live", 200, "200, no title token to confirm, no gone-marker either")


def test_decide_with_a_real_token_is_unaffected():
    t = "Pflegefachkraft (m/w/d) Intensivstation Nürnberg"
    assert decide(200, "Willkommen auf der Intensivstation", t)[0] == "live"
    assert decide(200, "Diese Stelle ist nicht mehr verfügbar", t)[0] == "gone"


# --- decide(): a 200 answered by the bot wall's OWN refusal page is "blocked", not a fake verdict --

def test_decide_treats_a_wall_marker_page_as_blocked_not_a_live_or_gone_verdict():
    # every www.helios-gesundheit.de posting answers 200 with an Akamai refusal page -- reading the
    # title-match miss on THAT page as "error: 200 but title not found" hid a refusal as a broken
    # adapter instead of the honest "we were refused".
    t = "Pflegefachkraft (m/w/d) Intensivstation Nürnberg"
    assert decide(200, "<h1>Access Denied</h1>You don't have permission to access this page.", t) == (
        "blocked", 200, "bot wall (200 with a refusal page)")
    assert decide(200, "Just a moment... cf-browser-verification", t)[0] == "blocked"


# --- _bounced_to_list: decided from the URL shape alone, st never gates it -------------------

def test_bounced_to_list_fires_even_when_the_caller_says_live():
    # LMU's referral portal: a dead posting slug 302s to the generic /jobs list, which still
    # contains one of the posting's own title tokens often enough to read as "live".
    url = "https://referral-portal-staging.lmu-klinikum.de/stellenanzeigen/pflegefachkraft-12345"
    final = "https://referral-portal-staging.lmu-klinikum.de/jobs"
    assert _bounced_to_list(url, final, "live") is True
    assert _bounced_to_list(url, final) is True          # st omitted entirely -- same result
    assert _bounced_to_list(url, final, "gone") is True   # st already agreed -- unaffected


def test_bounced_to_list_medbo_shape():
    url = "https://www.medbo.de/karriere/jobsmedbo/detail/dead-slug-a"
    final = "https://www.medbo.de/test-jobs"
    assert _bounced_to_list(url, final, "live") is True


def test_bounced_to_list_does_not_fire_on_an_ordinary_redirect():
    # locale/canonical redirect: the slug is still a substring of the final URL
    url = "https://x.example/stellenangebot/pflegefachkraft-station-a"
    final = "https://x.example/de/stellenangebot/pflegefachkraft-station-a/"
    assert _bounced_to_list(url, final, "live") is False
    assert _bounced_to_list(url, url, "live") is False    # no redirect at all
    assert _bounced_to_list(url, None, "live") is False   # no final_url known


# --- FRAGMENT_URL: pi_asp's "#position,id=<pid>" fragment must force escalation --------------

def test_fragment_url_matches_the_pi_asp_position_id_shape():
    assert FRAGMENT_URL.search("https://helios-gesundheit.pi-asp.de/bewerber-web/#position,id=47601")
    assert FRAGMENT_URL.search("https://x.example/stellen#title=pflegefachkraft-m-w-d")


def test_fragment_url_does_not_fire_on_a_plain_in_page_anchor():
    assert not FRAGMENT_URL.search("https://x.example/jobs#content")


# --- IS_PDF: a posting that IS a PDF has no title/DOM -- the file answering 200 is the whole ------
# liveness question (Klinikum Passau, stadtklinik-diako, augenklinik-muenchen, dongku).

class _PDFSession:
    """GET always answers with a real Klinikum Passau attachment url's own shape: 200, an
    application/pdf content-type, no HTML body to match a title against."""
    def __init__(self, status=200):
        self.status = status

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        return _PDFResp(url, self.status)


class _PDFResp:
    def __init__(self, url, status):
        self.status_code = status
        self.url = url
        self.text = ""
        self.headers = {"content-type": "application/pdf"}


_PDF_URL = "https://bewerbung.klinikum-passau.de/dateiablage/stellen/2763/sapflegedienstdialyse202609web.pdf"


def test_verify_one_reads_a_reachable_pdf_as_live_without_a_title_match():
    out = verify_one(_PDFSession(200), _PDF_URL, "Pflegefachkraft (m/w/d) Dialyse", rungs=("http",))
    assert out["verify_status"] == "live" and out["verify_http"] == 200 and out["verify_note"] == "pdf reachable"
    assert out["method"] == "http"


def test_verify_one_reads_a_dead_pdf_link_as_gone_not_live():
    out = verify_one(_PDFSession(404), _PDF_URL, "Pflegefachkraft (m/w/d) Dialyse", rungs=("http",))
    assert out["verify_status"] == "gone" and out["verify_http"] == 404


def test_is_pdf_matches_a_real_attachment_url_and_not_an_ordinary_detail_page():
    assert IS_PDF.search(_PDF_URL)
    assert not IS_PDF.search("https://www.klinikum-passau.de/jobs/?jobid=2763&type=2500")


# --- firecrawl_fetch: the Firecrawl API's own 200 is not the ORIGIN's status ------------------

def test_firecrawl_fetch_reports_the_origins_404_not_the_apis_own_200(monkeypatch):
    import pflege_jobs.verify as V

    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")

    class _FCResp:
        status_code = 200

        def json(self):
            return {"data": {"metadata": {"statusCode": 404}, "html": "<html>WordPress 404</html>"}}

    monkeypatch.setattr(V.requests, "post", lambda *a, **k: _FCResp())
    code, html = firecrawl_fetch("https://dongku.de/stellen/dead.pdf")
    assert (code, html) == (404, "<html>WordPress 404</html>")


def test_firecrawl_fetch_defaults_to_200_when_no_statuscode_metadata_is_present(monkeypatch):
    import pflege_jobs.verify as V

    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")

    class _FCResp:
        status_code = 200

        def json(self):
            return {"data": {"metadata": {}, "html": "<html>ok</html>"}}

    monkeypatch.setattr(V.requests, "post", lambda *a, **k: _FCResp())
    assert firecrawl_fetch("https://x.example/job/1") == (200, "<html>ok</html>")


# --- verify_all: a "gone" the http rung already decided must not be re-escalated to render ---

class _FakeSession:
    """Minimal requests.Session stand-in: GET always answers 200 with a page carrying an explicit
    gone marker and no matching title token -- decide() must call this "gone" on the http rung."""
    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        return _FakeResp(url)


class _FakeResp:
    def __init__(self, url):
        self.status_code = 200
        self.url = url
        self.text = "<h1>Stellenangebote</h1><p>Diese Stelle ist leider nicht mehr verfügbar.</p>"
        self.headers = {}


def test_verify_all_treats_an_http_gone_marker_as_final_no_render_escalation(monkeypatch):
    import pflege_jobs.verify as V
    monkeypatch.setattr(V, "requests", type("R", (), {"Session": lambda: _FakeSession()}))

    def _boom(*a, **kw):
        raise AssertionError("render() must not be called -- the http rung already decided gone")
    monkeypatch.setattr(V, "render", _boom)

    rows = [{"posting_id": 1, "source_url": "https://x.example/job/1", "external_url": None,
             "title": "Pflegefachkraft (m/w/d) Intensivstation Nürnberg"}]
    res = V.verify_all(rows, workers=1, log=lambda *a, **k: None, render=True, firecrawl=False)
    assert len(res) == 1 and res[0]["verify_status"] == "gone"


# --- board_titles(): a caught exception must not poison the per-process cache ----------------

def test_board_titles_does_not_cache_a_result_from_a_caught_exception(monkeypatch):
    import pflege_jobs.verify as V
    V._BOARD_TITLES.clear()
    calls = {"n": 0}

    def _boom(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("second sync_playwright would have raised here")
    monkeypatch.setattr("crawlers.portals.fetch_page", _boom)

    url = "https://logaallin.regiomed-kliniken.de/bewerber-web/?companyEid=%2a"
    first = V.board_titles(url)
    second = V.board_titles(url)
    assert first == set() and second == set()
    assert calls["n"] == 2, "a failed render must be retried, not cached as 'this board has 0 postings'"


def test_reset_board_titles_cache_clears_between_runs(monkeypatch):
    import pflege_jobs.verify as V
    V._BOARD_TITLES["https://x.example/bewerber-web/"] = {"stale title"}
    V.reset_board_titles_cache()
    assert V._BOARD_TITLES == {}


# --- the P&I LOGA board is the ONLY rung allowed to answer for a /bewerber-web/ URL -------------
# These boards have no detail page, so every other rung fetches the board LIST -- which still
# carries every title, removed ones included. Falling through to it answers "live" forever
# (confirmed live 2026-09-18: 51 Helios rows in the table carry "title tokens 3/3 [rendered]").

_PI_URL = "https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1134#position,id=4760199aa1c2d3e4f5a6"
_PI_TITLE = "Pflegerische Bereichsleitung Neurologie (m/w/d)"


class _BoardListSession:
    """Any GET answers with the rendered board LIST: it contains the posting's own title tokens,
    so decide() reads it as 'live' -- which is exactly the wrong verdict for a removed posting."""
    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        return _FakeResp2(url, "<html><body>Stellenmarkt: Pflegerische Bereichsleitung Neurologie (m/w/d)</body></html>")


class _FakeResp2:
    def __init__(self, url, text):
        self.status_code, self.url, self.text, self.headers = 200, url, text, {}


def test_pi_loga_title_still_listed_is_live_via_the_board_list_rung(monkeypatch):
    import pflege_jobs.verify as V
    monkeypatch.setattr(V, "board_titles", lambda url: {"pflegerische bereichsleitung neurologie (m/w/d)", "apotheker (m/w/d)"})
    out = V.verify_one(_BoardListSession(), _PI_URL, _PI_TITLE, rungs=("http", "render"))
    assert out["verify_status"] == "live" and out["method"] == "board_list"


def test_pi_loga_title_no_longer_listed_is_gone_not_live_off_the_list_page(monkeypatch):
    import pflege_jobs.verify as V
    monkeypatch.setattr(V, "board_titles", lambda url: {"apotheker (m/w/d)"})
    out = V.verify_one(_BoardListSession(), _PI_URL, _PI_TITLE, rungs=("http", "render"))
    assert out["verify_status"] == "gone" and out["method"] == "board_list"


def test_pi_loga_unreadable_board_is_undecided_never_live_off_the_board_list(monkeypatch):
    """The drift case: the vendor changes the list markup, _list_rows matches nothing. The whole
    board must go to crawl_issues as 'error', not read 'live' off the page the rungs below fetch."""
    import pflege_jobs.verify as V
    monkeypatch.setattr(V, "board_titles", lambda url: set())

    def _no_render(*a, **kw):
        raise AssertionError("render() must not judge a P&I LOGA board list as if it were the posting")
    monkeypatch.setattr(V, "render", _no_render)

    def _no_firecrawl(*a, **kw):
        raise AssertionError("firecrawl must not judge a P&I LOGA board list either -- and must not be billed for it")
    monkeypatch.setattr(V, "firecrawl_fetch", _no_firecrawl)

    out = V.verify_one(_BoardListSession(), _PI_URL, _PI_TITLE, rungs=("http", "render", "firecrawl"))
    assert out["verify_status"] == "error" and out["method"] == "board_list"
    assert "listed no titles" in out["verify_note"]


def test_pi_loga_on_a_rung_set_without_render_is_undecided_and_does_no_http_get(monkeypatch):
    """verify_all's threaded http pass and its firecrawl pass both call verify_one without the
    render rung -- neither may fetch the board list and answer off it."""
    import pflege_jobs.verify as V

    class _NoGet:
        def get(self, *a, **kw):
            raise AssertionError("no rung below board_list may fetch a P&I LOGA URL")

    for rungs in (("http",), ("firecrawl",)):
        out = V.verify_one(_NoGet(), _PI_URL, _PI_TITLE, rungs=rungs)
        assert out["verify_status"] == "error" and out["method"] == "board_list", rungs


def test_a_non_pi_url_is_unaffected_and_still_uses_the_http_rung():
    import pflege_jobs.verify as V
    out = V.verify_one(_BoardListSession(), "https://x.example/stellen/pflegerische-bereichsleitung-neurologie",
                       _PI_TITLE, rungs=("http",))
    assert out["verify_status"] == "live" and out["method"] == "http"


# --- a JSON-LD address field that arrives as a one-item list ----------------------------------
# www.komm-ins-klinikland.de ships {"postalCode": ["97318"], "addressLocality": ["Kitzingen"]}.
# The list landed inside the (city, plz) tuple _walk_jsonld hashes, and the TypeError propagated
# out of as_completed().result() and killed the whole pass: run 109 (2026-09-21) re-checked none of
# the 2560 open postings, and every verdict in the table stayed three days old.

_LIST_VALUED_JSONLD = """<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Pflegefachkraft", "jobLocation": {"@type": "Place",
 "address": {"@type": "PostalAddress", "streetAddress": ["Keltenstra\\u00dfe 67"],
             "postalCode": ["97318"], "addressLocality": ["Kitzingen"]}}}
</script></head><body>OP-Schwester OTA Kitzingen</body></html>"""


def test_extract_location_reads_a_list_valued_jsonld_address_instead_of_crashing():
    from pflege_jobs.verify import extract_location
    assert extract_location(_LIST_VALUED_JSONLD) == ("Kitzingen", "97318", "jsonld")


def test_verify_one_survives_the_list_valued_jsonld_page_on_the_http_rung():
    class _S:
        def get(self, url, headers=None, timeout=None, allow_redirects=None):
            return _FakeResp2(url, _LIST_VALUED_JSONLD)

    out = verify_one(_S(), "https://www.komm-ins-klinikland.de/stelle/op-schwester-pfleger-oder-ota-m-w-d/",
                     "OP-Schwester / -Pfleger oder OTA (m/w/d)", rungs=("http",))
    assert out["verify_status"] == "live" and out["city"] == "Kitzingen" and out["plz"] == "97318"


def test_verify_all_records_a_crashed_http_row_instead_of_ending_the_whole_pass(monkeypatch):
    """One row raising must not take the other 2559 down with it."""
    import pflege_jobs.verify as V

    def _boom_on_one(session, url, title, rungs=(), towns=None):
        if "boom" in url:
            raise TypeError("unhashable type: 'list'")
        return {"verify_status": "live", "verify_http": 200, "verify_note": "ok", "method": "http",
                "city": None, "plz": None, "loc_source": None, "final_url": url}

    monkeypatch.setattr(V, "verify_one", _boom_on_one)
    monkeypatch.setattr(V, "requests", type("R", (), {"Session": lambda: object()}))
    rows = [{"posting_id": i, "external_url": f"https://x.example/{'boom' if i == 1 else 'ok'}/{i}",
             "source_url": None, "title": "Pflegefachkraft Intensivstation Nürnberg"} for i in range(4)]

    res = V.verify_all(rows, workers=2, log=lambda *a, **k: None, render=False, firecrawl=False)
    by_id = {r["posting_id"]: r for r in res}
    assert len(res) == 4, "every row must come back, crashed one included"
    assert by_id[1]["verify_status"] == "error" and "http rung crashed: TypeError" in by_id[1]["verify_note"]
    assert all(by_id[i]["verify_status"] == "live" for i in (0, 2, 3))
