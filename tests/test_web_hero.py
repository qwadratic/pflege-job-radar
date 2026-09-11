"""The home page hero: the flipping town in the h1 and the rotating facet line under the lead.

Runs the built page against its own offline mock (`?mock=1`): no API, no network, no credentials.
Skipped when Playwright or its Chromium build is missing.
"""
import http.server, functools, threading, pathlib, re, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
# Facets that count hospitals, not postings. A line that says "N open jobs <clinic facet>" would be a lie,
# so none of them may ever reach the rotating line.
CLINIC_FACETS = {"cities", "regierungsbezirk", "landkreis", "traegerart", "versorgungsstufe", "size",
                 "fachrichtungen", "status", "ats_type"}


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_selector("#board .c a")
    yield pg
    pg.close()


def test_the_deck_is_built_from_the_facets(page):
    cards = page.eval_on_selector_all("#board .c", "ns=>ns.map(n=>n.textContent.trim())")
    assert len(cards) > 6, cards                                  # CV ask + index card + one per facet value
    assert any("part time" in c for c in cards), cards            # employment_types reached the deck
    assert any("intensive care" in c for c in cards), cards       # department_hint reached the deck
    assert page.eval_on_selector_all("#flap .c", "n=>n.length") > 2


def test_the_line_only_ever_rotates_job_level_facets(page):
    keys = set()
    for href in page.eval_on_selector_all("#board .c a", "ns=>ns.map(n=>n.getAttribute('href'))"):
        keys |= set(re.findall(r"[?&]([a-z_]+)=", href))
    assert keys, "no facet links in the rotating line"
    assert not keys & CLINIC_FACETS, f"clinic-level facet in a per-job line: {keys & CLINIC_FACETS}"


def test_a_facet_value_with_no_german_phrase_is_shown_not_dropped(page):
    cards = page.evaluate("""(()=>{ heroBoard({department_hint:[{v:'Zzz-Station',n:7}]},null);
      return [...document.querySelectorAll('#board .c')].map(n=>n.textContent.trim()); })()""")
    assert any("Zzz-Station" in c for c in cards), cards


def test_a_card_lands_on_the_list_it_promised_and_the_chip_clears_it(page, base_url):
    href = next(h for h in page.eval_on_selector_all("#board .c a", "ns=>ns.map(n=>n.getAttribute('href'))")
                if "department_hint=" in h)
    page.goto(f"{base_url}/index.html?mock=1&lang=en{href}")
    page.wait_for_selector(".list .row")
    n_filtered = page.eval_on_selector_all(".list .row", "n=>n.length")
    chip = page.locator(".filters .tag", has_text="×").first
    assert chip.count() == 1
    chip.click()
    page.wait_for_function(f"document.querySelectorAll('.list .row').length!=={n_filtered}")
    assert page.locator(".filters .tag").count() == 0


def test_the_flip_changes_the_town_but_never_the_headings_name(page):
    name = page.locator("h1").aria_snapshot()
    first = page.eval_on_selector("#flap .c.on", "e=>e.textContent")
    page.wait_for_function("document.querySelector('#flap .c.on').textContent!==%r" % first, timeout=20000)
    assert page.locator("h1").aria_snapshot() == name             # the flap is aria-hidden, the .vh twin is not
    spoken = re.search(r'heading "(.*?)"', name).group(1)
    assert not re.search(r"\d", spoken), spoken                   # the counters stay out of the spoken name


def test_reduced_motion_hydrates_the_hero_but_never_starts_the_clock(browser, base_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_function("document.querySelectorAll('#board .c').length>6")   # the deck is still built ...
    assert pg.evaluate("HB.t") == 0                                # ... and nothing is scheduled
    pg.wait_for_timeout(1200)
    assert pg.eval_on_selector_all("#flap .c.on", "n=>n.length") == 1
    pg.close()


# --- the clock yields to the readout, and the CV path ---------------------------------------------
def test_the_first_town_stops_the_clock_and_turns_the_flap_into_a_readout(page):
    """The rotation is a demo. The first real choice ends it: heroStop() clears both handles and the two
    lines become a live readout of the filter, which never rotates again."""
    page.wait_for_selector(".mp .d")
    assert page.evaluate("HB.t") != 0                             # the demo is scheduled
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_function("HB.done===true")
    assert page.evaluate("HB.t") == 0                             # timeout and interval both off the one handle
    assert page.eval_on_selector_all("#flap .c", "n=>n.length") == 1
    assert page.inner_text("#flap").startswith("in München")

    page.wait_for_selector("#board .c.on a")
    line = page.inner_text("#board .c.on")
    assert "jobs" in line and "München" in line                   # the active-filter sentence, not the stats one
    frozen = page.inner_text("#flap")
    page.wait_for_timeout(2600)                                   # longer than a beat: nothing may move
    assert page.inner_text("#flap") == frozen
    assert page.eval_on_selector_all("#flap .c", "n=>n.length") == 1

    page.locator('.mp .d[aria-label^="Passau"]').click()          # ... but it stays live
    page.wait_for_function("document.querySelector('#flap').innerText.includes('+ 1')")
    assert "in München + 1 town" in page.inner_text("#flap")


def test_one_town_spelled_several_ways_is_one_town_in_the_readout(page):
    """job_cities carries one facet value per spelling, so "München", "80331 München" and "München,
    Bayern" arrived as three towns: the readout counted three places, counted only the postings filed
    under the one spelling it was given, and linked to that spelling alone — so the list it opened was
    missing two thirds of the jobs it had just promised."""
    got = page.evaluate("""(()=>{ const fc={job_cities:[{v:"München",n:5},{v:"80331 München",n:3},
                                                        {v:"München, Bayern",n:2}]};
        townSrc(fc); heroReadout(fc,["München"]);
        return {flap:document.querySelector('#flap').innerText.trim(),
                jobs:document.querySelector('#board .c.on b').textContent,
                href:document.querySelector('#board .c.on a').getAttribute('href')}; })()""")
    assert got["flap"].startswith("in München"), got["flap"]
    assert "+" not in got["flap"], got["flap"]                     # one town, not "in München + 2 towns"
    assert got["jobs"] == "10", got["jobs"]                        # 5 + 3 + 2, every spelling counted
    assert "80331" in got["href"] and "Bayern" in got["href"], got["href"]     # ... and every one of them asked for


def test_the_cv_control_sends_an_anonymous_visitor_to_the_login(page):
    """POST /api/cv is session-gated, so an anonymous visitor gets the login link -- never a file picker
    that would upload into a 401."""
    assert page.locator("#hero-rest .sec button").count() == 0
    assert page.locator("#hero-rest .sec a").get_attribute("href") == "/login?next=%2F%23%2Fcv"


def test_a_gated_cv_upload_says_why_and_offers_the_login(page):
    page.evaluate("uploadCv(new File(['cv'],'cv.txt',{type:'text/plain'}))")
    page.wait_for_selector("#view a[href^='/login']")
    text = page.inner_text("#view")
    assert "signed-in session" in text                            # the reason, in the visitor's language
    assert "sign in required" in text                             # ... and the server's own words, not swallowed


def test_a_signed_in_visitor_gets_the_picker_and_the_matches(browser, base_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{base_url}/index.html?mock=1&me=member")
    pg.wait_for_selector("#hero-rest .sec button")                # /api/me lands after the first paint
    assert pg.locator("#hero-rest .sec a").count() == 0
    pg.evaluate("uploadCv(new File(['cv'],'cv.txt',{type:'text/plain'}))")
    pg.wait_for_selector("#view .card")
    assert pg.locator("#view .list .row").count() > 0
    pg.close()
