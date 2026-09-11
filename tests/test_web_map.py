"""The public board's map picker, infinite scroll and clinic marks (web/index.html).

Runs the built page against its own offline mock (`?mock=1`): no API, no network, no credentials.
Skipped when Playwright or its Chromium build is missing.
"""
import http.server, functools, re, threading, pathlib, pytest
from urllib.parse import parse_qs, urlparse

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
# The radius switch now names its centre before it is switched on, so it is addressed by that promise.
RADIUS_ON = '.mapbox .ctl button[aria-label^="Radius around"]'
MARKER = ".mapbox .attr .noteref"


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
    pg.wait_for_selector(".mp .d")
    pg.wait_for_selector(".list .row")                             # the list below loads separately
    yield pg
    pg.close()


def test_every_town_on_the_board_lands_on_the_map(page):
    """A town the municipality table cannot place is reported, never silently dropped -- so an empty
    'without coordinates' line is also the assertion that the name matching still works."""
    towns = page.evaluate("(async()=>(await api('/api/facets')).job_cities.length)()")
    assert page.locator(".mp .d").count() == towns
    assert page.locator(".mapbox .cap.sub").count() == 0


def test_clicking_a_dot_narrows_the_list_and_clicking_it_again_clears(page):
    dot = page.locator('.mp .d[aria-label^="München"]')
    before = page.locator(".list .row").count()
    dot.click()
    page.wait_for_selector('.mp .d.on[aria-label^="München"]')
    page.wait_for_function("document.querySelectorAll('.list .row').length < %d" % before)
    assert dot.get_attribute("aria-pressed") == "true"
    assert all("München" in t for t in page.locator(".list .row .m").all_inner_texts())
    narrowed = page.locator(".list .row").count()
    dot.click()
    page.wait_for_selector(".mp .d.on", state="detached")
    page.wait_for_function("document.querySelectorAll('.list .row').length > %d" % narrowed)


def test_a_dot_is_reachable_and_operable_from_the_keyboard(page):
    page.locator('.mp .d[aria-label^="Passau"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('.mp .d.on[aria-label^="Passau"]')
    assert "Passau" in page.inner_text(".mapbox .ctl .sel")        # the page's one town control, beside the map


def test_outline_is_the_real_border_and_is_attributed(page):
    """A real administrative border, not a silhouette -- and dl-de/by-2-0 requires the credit on the page.

    The visible tier is the licence guard: provider, licence link and change note all have to be readable
    without opening anything, in either language. Only the dataset URI and the full deed may sit in the
    endnote, so this asserts what stays outside it rather than merely that some credit exists.
    """
    d = page.locator(".mp .land").get_attribute("d")
    assert d.count("M") == 2                                      # border + the Jungholz hole
    assert d.count("L") > 400                                     # a real boundary, not a smoothed blob
    assert page.locator(".mp .land").get_attribute("fill-rule") == "evenodd"
    attr = page.locator(".mapbox .attr")
    text = attr.inner_text()
    assert "BKG" in text                                          # Namensnennung
    assert "dl-de/by-2-0" in text and "data modified" in text     # licence named, Veränderungshinweis visible
    assert attr.locator('a[href="https://www.bkg.bund.de"]').count() == 1                 # provider is a link
    assert attr.locator('a[href="https://www.govdata.de/dl-de/by-2-0"]').count() == 1     # licence must be a link
    assert attr.locator("#fnref-geo").count() == 1                # ... and nothing else moved into the note
    assert page.locator(".inset").count() == 0                    # the Germany thumbnail is gone
    # "deutlich sichtbarer Quellenvermerk": a notice the licence requires may not be the page's faintest
    # grey, and the marker has to look like something you can follow.
    assert _contrast(page, ".mapbox .attr") >= 4.5, _contrast(page, ".mapbox .attr")
    assert page.evaluate("getComputedStyle(document.querySelector('#fnref-geo')).textDecorationLine") == "underline"


def _contrast(page, sel):
    """WCAG 1.4.3 ratio of an element's own colour against the page background."""
    return page.evaluate(r"""(sel=>{ const lum=c=>{ const [r,g,b]=c.match(/[\d.]+/g).slice(0,3).map(Number)
          .map(v=>{ v/=255; return v<=.03928?v/12.92:((v+.055)/1.055)**2.4; });
        return .2126*r+.7152*g+.0722*b; };
      const a=lum(getComputedStyle(document.querySelector(sel)).color),
            b=lum(getComputedStyle(document.body).backgroundColor);
      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05); })""", sel)


def test_the_credit_survives_when_no_town_can_be_placed_on_the_map(page):
    """townMap() returns null when not one town resolves and the page falls back to plain chips; the BKG
    note and the footnote marker used to disappear with the map."""
    got = page.evaluate("""(()=>{ const r=townChips({job_cities:[{v:'Nowhere',n:3}]});
      return [!!r.querySelector('.attr a[href*="govdata.de/dl-de/by-2-0"]'), !!r.querySelector('.attr #fnref-geo')]; })()""")
    assert got == [True, True]


def test_the_footnote_marker_round_trips_to_the_endnote_and_back(page):
    """[1] -> the note (open, targeted, focused) -> back to [1], without re-rendering the page: the router
    treats a fragment as a route otherwise, and a re-render drops the marker the backlink returns to."""
    marker = page.locator("#fnref-geo")
    assert marker.get_attribute("role") == "doc-noteref"
    assert marker.get_attribute("aria-describedby") == "refs-h"   # the heading, never the licence text
    box = marker.bounding_box()
    assert box["width"] >= 24 and box["height"] >= 24             # WCAG 2.5.8 target size
    assert page.evaluate("getComputedStyle(document.querySelector('#fnref-geo')).verticalAlign") == "super"
    assert page.locator("#fn-geo details").evaluate("d=>d.open") is False        # closed until asked for
    page.evaluate("window._row=document.querySelector('.list .row')")            # a live node from before the jump

    marker.click()
    # Both halves are done by refJump() on hashchange, which the browser queues after its own fragment
    # scroll: wait for them together, or the assertions race the event.
    page.wait_for_function("document.activeElement.id==='fn-geo'&&document.querySelector('#fn-geo details').open")
    assert page.locator("#fn-geo:target").count() == 1
    assert "govdata.de/dl-de/by-2-0" in page.locator("#fn-geo details").inner_html()   # web/references.html landed
    assert "geometry simplified" in page.locator("#fn-geo details").inner_text()       # lang=en, the change note
    assert page.evaluate("window._row===document.querySelector('.list .row')")   # route() would have replaced it

    page.click("#fn-geo .backlink")
    page.wait_for_function("document.activeElement.id==='fnref-geo'")
    assert page.locator(".mp .d").count() > 0                      # ... and the map is still the one we left


def test_the_endnote_holds_its_place_while_the_list_grows_under_it(browser, base_url):
    """Tapping [1] put the visitor in the middle of the clinic list instead of at the note: the jump brings
    the list's sentinel into range, the next 20 rows are inserted *above* the note, and browser scroll
    anchoring does not survive a programmatic jump — recorded at 390x844 with the note 2742 px below the
    top of an 844 px window.

    Two halves, both of them the root cause: the list rests while the jump owns the scroll, and a page of
    rows that lands anyway (one already in flight when the marker was tapped) does not move the note. The
    visitor's own scroll takes the page back.
    """
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_selector(".list .row")
    total = pg.evaluate("(async()=>(await api('/api/clinics?limit=1')).total)()")
    rows = pg.locator(".list .row").count()
    assert 0 < rows < total                                        # there is more list to pull in
    top = lambda: pg.evaluate("document.querySelector('#fn-geo').getBoundingClientRect().top")

    pg.click("#fnref-geo")
    pg.wait_for_function("document.activeElement.id==='fn-geo'")
    pg.wait_for_timeout(700)                                       # the rows the jump itself used to pull in
    assert 0 <= top() <= 844 - 60, top()                           # the note, not the middle of the list
    assert pg.locator(".list .row").count() == rows                # the list rested, it did not chain-load

    # The reader's first gesture used to be the end of it: the hold let go on wheel/touchstart/pointerdown/
    # keydown, the freed sentinel appended the next page above the note, and the note went 2925 px down an
    # 844 px screen without the scroll position moving at all (eval_out/i2-note-gesture: note top 245 at
    # 4.36s, 3170 at 7.32s, scrollY 5082 throughout, HELD 1 -> 0). Touching the page is not leaving the note.
    pg.mouse.wheel(0, 40)
    pg.wait_for_timeout(400)
    assert pg.evaluate("HELD") == 1                                # they are still reading it
    assert pg.locator(".list .row").count() == rows                # the list did not wake up and chain-load
    moved = top()
    assert 0 <= moved <= 844 - 60, moved                           # ... and the note is still where the eye is

    # ... and the page that was already in flight lands under the note anyway — after the gesture, which is
    # what used to make it fatal. A scripted click carries no pointerdown, so this is the growth arriving on
    # its own, exactly as the in-flight fetch does.
    pg.evaluate("document.querySelector('#clinics .more button').click()")
    pg.wait_for_function(f"document.querySelectorAll('.list .row').length>{rows}")
    pg.wait_for_timeout(400)
    assert abs(top() - moved) <= 2, (top(), moved)                 # 20 rows above it move it by nothing
    assert pg.locator("#fn-geo:target").count() == 1
    assert pg.evaluate("document.activeElement.id") == "fn-geo"
    assert pg.locator("#fn-geo details").evaluate("d=>d.open") is True

    grown = pg.locator(".list .row").count()
    assert grown < total                                           # there is still list left to resume with
    pg.evaluate("window.scrollTo(0,0)")                            # the visitor leaves the note ...
    pg.wait_for_function("HELD===0")
    pg.wait_for_timeout(200)
    # ... and the list starts again where it stopped. The sentinel has been sitting inside the observer
    # band since the jump (82 px), and an observer does not re-fire for an element that never left it, so
    # releasing the hold has to re-arm it -- otherwise the list stays dead until it re-crosses the band.
    pg.evaluate("window.scrollTo(0,document.body.scrollHeight)")
    pg.wait_for_function(f"document.querySelectorAll('.list .row').length>{grown}")
    pg.wait_for_function(f"document.querySelectorAll('.list .row').length==={total}")   # ... and the list resumes
    pg.close()
    assert not errors, errors[:2]


def test_lists_load_the_rest_on_scroll_without_a_click(page):
    total = page.evaluate("(async()=>(await api('/api/clinics?limit=1')).total)()")
    assert 0 < page.locator(".list .row").count() < total                                     # first page only, so far
    for _ in range(8):
        page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
        page.wait_for_timeout(250)
        if page.locator(".list .row").count() >= total:
            break
    assert page.locator(".list .row").count() == total
    assert page.locator("#clinics .more").is_hidden()             # nothing left, so no fallback button


def test_clinic_rows_and_the_clinic_page_carry_a_mark(page):
    """No photo field in the API yet: the tile falls back to initials, on the page background so a
    transparent logo reads on either theme."""
    assert page.locator(".row.pic .ph").count() == page.locator(".list .row").count()
    page.click(".row.pic")
    page.wait_for_selector(".idh .ph.big")
    assert page.evaluate("clinicPhoto({name:'Klinikum Musterstadt'},true).textContent") == "MU"   # no site -> initials
    assert page.evaluate("clinicPhoto({name:'X',website:'https://a.example/x'}).querySelector('img').src")\
        .startswith("https://a.example/")                         # its own host, never a logo service
    # 161 of the 407 clinics are on file with an http:// website, and the app serves `img-src 'self' data:
    # https:` -- asked for as written, those marks are not slow or missing, they are refused before the
    # request, one console violation each, for a tile that could never have filled.
    assert page.evaluate("clinicPhoto({name:'X',website:'http://a.example/x'}).querySelector('img').src")\
        .startswith("https://a.example/")
    assert page.evaluate("clinicPhoto({name:'Klinikum Musterstadt'}).className") == "ph ini"   # a name tile says so


def test_one_town_spelled_several_ways_is_one_dot_one_option_and_one_chip(page):
    """job_cities is one facet value per spelling, and the map drew one dot per value: on the live board
    five of them landed as two dots on the same pixel and the picker offered seven "München" entries, two
    of them the same town written differently. One resolved town is one dot, one option and one chip with
    the counts added up — and nothing is lost behind it: the group keeps every spelling, the picker finds
    them, and the filter asks the API for all of them, so a posting spelled "80331 München" does not fall
    out of the list behind a dot that says "München"."""
    fc = """{job_cities:[{v:"München",n:5},{v:"80331 München",n:3},{v:"München, Bayern",n:2},
                         {v:"Nürnberg",n:4},{v:"Nirgendwo",n:1}]}"""
    got = page.evaluate("""(()=>{ const box=townMap(%s,[],()=>{},()=>{});
        return {dots:[...box.querySelectorAll('.mp .d')].map(d=>d.getAttribute('aria-label')),
                off:box.querySelector('.cap.sub')?.textContent,
                canon:canonTown("München, Bayern"), expand:expandTowns(["München"]),
                same:JSON.stringify(townXY("München"))===JSON.stringify(townXY("80331 München"))}; })()""" % fc)
    assert got["same"]                                             # they were always the same pair of coordinates
    assert got["dots"] == ["München, 10 jobs", "Nürnberg, 4 jobs"], got["dots"]     # 5+3+2 on one dot
    # Both counts, each saying what it counts, and both counting the same thing: towns. The right-hand one
    # used to count raw facet strings against a left-hand count of deduped towns.
    assert got["off"] == "2 towns on the map · 1 towns not on this map — mostly outside Bavaria: Nirgendwo"
    assert got["canon"] == "München"
    assert sorted(got["expand"]) == ["80331 München", "München", "München, Bayern"]

    # The picker: one option, still findable by the postcode a feed wrote, and picking it filters on all
    # three spellings rather than on the one the option is labelled with.
    pick = page.evaluate("""(()=>{ const [gs]=townSrc(%s); let got=null;
        const p=townPicker(gs,v=>got=v); document.body.append(p);
        const inp=p.querySelector('.pk-t'); inp.value="80331"; inp.dispatchEvent(new Event("input"));
        const hits=[...p.querySelectorAll('.dd .o')].map(o=>o.textContent);
        p.querySelector('.dd .o').dispatchEvent(new MouseEvent("mousedown",{bubbles:true}));
        p.remove(); return {hits,got,raw:expandTowns([got])}; })()""" % fc)
    assert pick["hits"] == ["München10"], pick["hits"]              # one option for the town, its counts summed
    assert pick["got"] == "München"
    assert sorted(pick["raw"]) == ["80331 München", "München", "München, Bayern"]


def test_town_names_resolve_through_their_dirtier_spellings(page):
    """Feeds spell a town with a postcode, a region tail or a slashed river; the register spells it plain."""
    got = page.evaluate("""['Kempten','Kempten (Allgäu)','82467 Garmisch-Partenkirchen','Augsburg, Bayern',
                             'Neuburg/Donau','Weiden i.d.OPf.','Rothenburg ob der Tauber','Bad Tölz']
                            .map(n => [n, !!townXY(n)])""")
    assert all(ok for _, ok in got), got
    assert page.evaluate("townXY('Kein Ort Der Existiert')") is None


# --- progressive disclosure (S0..S4) -------------------------------------------------------------
def _km(a, b):
    """Great-circle km, written out here on purpose: the page's own haversine must not be the oracle
    that judges the page's own dimming."""
    import math
    r = math.radians
    h = (math.sin(r(b[0] - a[0]) / 2) ** 2
         + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(r(b[1] - a[1]) / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def _rows(page):
    """The list's row count once it has stopped changing. A filter replaces the list, so a count read the
    moment a control moves is the count of the list that is on its way out."""
    prev = -1
    for _ in range(60):
        page.wait_for_timeout(120)
        n = page.locator(".list .row").count()
        if n and n == prev:
            return n
        prev = n
    raise AssertionError("the list never settled")


def _dots(page):
    return page.evaluate("""[...document.querySelectorAll('.mp .d')].map(d=>{
        const v=d.getAttribute('aria-label').split(',')[0];
        return [v, townXY(v), d.classList.contains('dim')]; })""")


def test_a_second_dot_adds_a_second_chip_and_removing_it_restores_the_state(page):
    """S0 -> S1 -> S0. Nothing is revealed until a town exists, every chip carries its own undo, and the
    undo puts the page back exactly where it was -- including the keyboard, which lands in the picker."""
    assert page.locator(".mapbox .ctl button").count() == 0
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    one = _rows(page)
    assert page.locator(".mapbox .ctl .sel button").all_inner_texts() == ["München ×"]
    assert page.get_by_role("button", name="+ Town").count() == 1
    assert page.locator(RADIUS_ON).count() == 1

    page.locator('.mp .d[aria-label^="Passau"]').click()
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    assert page.locator(".mapbox .ctl .sel button").all_inner_texts() == ["München ×", "Passau ×"]
    assert _rows(page) > one

    page.locator(".mapbox .ctl .sel button").last.click()             # the chip's own undo
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===1")
    assert _rows(page) == one
    assert page.locator(".mp .d.on").count() == 1
    assert page.evaluate("F.city") == ["München"]
    assert page.evaluate("document.activeElement.classList.contains('pk-t')")


def test_the_radius_dims_exactly_the_dots_outside_it(page):
    """S2. The circle is client-side haversine over the coordinates the dots are already drawn from --
    no API parameter -- and the dimming reaches the dots only through box.set()."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    page.locator(RADIUS_ON).click()
    rng = page.locator(".mapbox .ctl input[type=range]")
    rng.wait_for()
    assert [rng.get_attribute(a) for a in ("min", "max", "step")] == ["5", "100", "5"]

    rng.focus()
    for _ in range(15):                                            # 25 -> 100 km, keyboard only
        page.keyboard.press("ArrowRight")
    page.wait_for_function("document.querySelector('.mapbox .ctl output').textContent==='100 km'")
    assert rng.get_attribute("aria-valuetext") == "100 km"
    page.wait_for_selector(".mp .d.dim")

    dots = _dots(page)
    hub = next(xy for v, xy, _ in dots if v == "München")
    for v, xy, dim in dots:
        assert dim == (_km(hub, xy) > 100), (v, round(_km(hub, xy)), dim)
    by = {v: dim for v, _, dim in dots}
    assert by["Augsburg"] is False and by["Würzburg"] is True       # 55 km in, 220 km out

    page.get_by_role("button", name="Remove radius").click()       # the explicit undo
    page.wait_for_selector(".mp .d.dim", state="detached")
    assert page.locator(".mapbox .ctl input[type=range]").count() == 0


def test_a_specialisation_chip_narrows_the_list_through_the_fach_parameter(page):
    """S3 is not reachable before S1, and the chip is the existing `fach` parameter of GET /api/clinics."""
    assert page.locator("#clinics .fachrow").count() == 0
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector("#clinics .fachrow .chip")
    before = _rows(page)
    chip = page.locator("#clinics .fachrow .chip").last
    label = chip.evaluate("n=>n.firstChild.textContent")               # the chip text without its count
    chip.click()
    page.wait_for_function("F.fach.length===1")
    assert _rows(page) < before
    assert page.locator("#clinics .fachrow .chip").last.get_attribute("aria-pressed") == "true"
    assert label in page.inner_text("#board .c.on")                    # ... and the hero says what is on
    assert page.evaluate("""(async()=>{ const d=await api('/api/clinics'+qs({city:F.city,fach:F.fach,limit:500}));
        return d.rows.length>0 && d.rows.every(c=>(c.fachrichtungen||[]).includes(F.fach[0])); })()""")
    page.locator("#clinics .fachrow .chip").last.click()
    page.wait_for_function("F.fach.length===0")
    assert _rows(page) == before


def test_removing_the_last_town_clears_the_specialisation_it_revealed(page):
    """S3 is opened by S1 and has to close with it. The chip row vanished with the town while the filter
    stayed applied — and persisted — so the board was silently narrowed with no control anywhere on the
    page to undo it: 32 hospitals in the action bar where the unfiltered mock has 50."""
    page.wait_for_function("document.querySelector('.mapbox .go b').textContent!==''")
    unfiltered = page.inner_text(".mapbox .go b")

    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector("#clinics .fachrow .chip")
    page.locator("#clinics .fachrow .chip").first.click()
    page.wait_for_function("F.fach.length===1")
    assert page.inner_text(".mapbox .go b") != unfiltered

    page.locator(".mapbox .ctl .sel button").first.click()          # the town's own undo
    page.wait_for_function("F.city.length===0")
    assert page.locator("#clinics .fachrow").count() == 0           # the control is gone ...
    assert page.evaluate("F.fach") == []                            # ... so the filter it set goes with it
    assert page.evaluate("JSON.parse(localStorage['pf.home']).fach") == []      # not tomorrow either
    page.wait_for_function("document.querySelector('.mapbox .go b').textContent===%r" % unfiltered)
    assert "cleared" in page.inner_text(".mapbox .say")             # and the status line says so
    assert _rows(page) == page.evaluate("(async()=>(await api('/api/clinics?limit=20')).rows.length)()")


def test_a_deep_link_cannot_smuggle_a_specialisation_in_without_its_row(page, base_url):
    """Same invariant from the other side: ?fach= with no town would apply a filter whose control the page
    never shows."""
    page.goto(f"{base_url}/index.html?mock=1&lang=en#/?fach=NEU")
    page.wait_for_selector(".list .row")
    assert page.evaluate("F.fach") == []
    assert page.locator("#clinics .fachrow").count() == 0


def test_the_specialisation_row_says_that_it_appeared(page):
    """The row opens a viewport below the fold, and a reveal may not move the focus. So the one status
    line — beside the action bar, where the tap happened — names what appeared."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector("#clinics .fachrow .chip")
    said = page.inner_text(".mapbox .say")
    assert "München" in said
    assert f"{page.locator('#clinics .fachrow .chip').count()} departments" in said, said
    assert page.evaluate("document.querySelector('#clinics .fachrow').getBoundingClientRect().top") > 900
    assert page.evaluate("document.querySelector('.mapbox .say').getBoundingClientRect().top") < 900
    assert page.evaluate("!document.activeElement.closest('.fachrow')")      # the focus never followed


def test_no_mount_point_prints_the_word_null(page):
    """replaceChildren() stringifies null where el() drops it: with no town chosen, the fachrow ternary
    printed the literal "null" above the clinic list — to every first-time visitor. Two more mounts in this
    page take a ternary the same way (the KPI row when /api/stats is down, a CV match with no reason), so
    the fix is the one mount helper, and the last line is the assertion that it drops what el() drops."""
    def stray():
        return page.evaluate("""(()=>{ const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT), out=[];
            while(w.nextNode()) if(w.currentNode.textContent.trim()==="null") out.push(w.currentNode.parentNode.className||w.currentNode.parentNode.tagName);
            return out; })()""")
    assert stray() == []
    page.evaluate("location.hash='#/jobs'")
    page.wait_for_selector("#view .list .row")
    assert stray() == []
    assert page.evaluate("""(()=>{ const d=el("div"); put(d,null,"a",false,undefined,el("b",{text:"b"}));
        return d.textContent; })()""") == "ab"


def test_every_revealed_control_is_reachable_and_operable_from_the_keyboard(page):
    page.locator('.mp .d[aria-label^="München"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".mapbox .ctl .sel button")

    page.get_by_role("button", name="+ Town").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.classList.contains('pk-t')")

    page.locator(RADIUS_ON).focus()
    page.keyboard.press("Enter")
    page.locator(".mapbox .ctl input[type=range]").wait_for()
    page.locator(".mapbox .ctl input[type=range]").focus()
    page.keyboard.press("ArrowLeft")
    page.wait_for_function("document.querySelector('.mapbox .ctl output').textContent==='20 km'")

    page.get_by_role("button", name="Remove radius").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".mapbox .ctl input[type=range]", state="detached")

    page.locator("#clinics .fachrow .chip").first.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("F.fach.length===1")
    # Every revealed control is a real focusable element outside the svg: inside it, pointer-events:none
    # under (pointer:coarse) would make it dead to a finger.
    assert page.evaluate("""[...document.querySelectorAll('.mapbox .ctl button,.mapbox .ctl input')]
        .every(n=>n.tabIndex>=0 && !n.closest('svg'))""")


def test_one_status_line_announces_the_reveals_and_the_view_is_not_a_live_region(page):
    """Every route re-render used to be announced wholesale from #view. The page now has exactly one live
    region: the line beside the action bar, which says what a single reveal changed."""
    assert page.locator("#view").get_attribute("aria-live") is None
    assert page.locator("main [aria-live], main [role=status], main [role=alert]").count() == 1
    say = page.locator(".mapbox .say")
    assert say.get_attribute("role") == "status"
    page.locator('.mp .d[aria-label^="Passau"]').click()
    page.wait_for_function("document.querySelector('.mapbox .say').textContent.includes('Passau')")
    page.locator(RADIUS_ON).click()
    page.wait_for_function("document.querySelector('.mapbox .say').textContent.includes('25 km')")
    assert "Passau" in say.inner_text()


def test_the_revealed_controls_fit_a_phone(browser, base_url):
    """390px with every control open: the action bar wraps, and none of it lives inside the svg."""
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_selector(".list .row")
    pg.locator('.mp .d[aria-label^="München"]').click()
    pg.wait_for_selector(".mapbox .ctl .sel button")
    pg.locator('.mp .d[aria-label^="Passau"]').click()
    pg.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    pg.locator(RADIUS_ON).click()
    pg.locator(".mapbox .ctl input[type=range]").wait_for()
    pg.locator("#clinics .fachrow .chip").first.click()
    pg.wait_for_function("F.fach.length===1")
    overflow = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    pg.close()
    assert not errors, errors[:2]
    assert not overflow


def test_the_disclosure_survives_the_language_switch_and_a_reload(browser, base_url):
    """All of it lives on F, never on a DOM node: the DE/EN buttons rebuild every tree from scratch, and
    a returning visitor is not taught the map a second time."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_selector(".list .row")
    pg.locator('.mp .d[aria-label^="München"]').click()
    pg.wait_for_selector(".mapbox .ctl .sel button")
    pg.locator(RADIUS_ON).click()
    pg.locator(".mapbox .ctl input[type=range]").wait_for()
    dimmed = pg.locator(".mp .d.dim").count()
    assert dimmed

    pg.locator('.lang button[data-lang="de"]').click()                 # applyLang() + route(): every tree is new
    pg.get_by_role("button", name="Umkreis entfernen").wait_for()
    assert pg.locator(".mapbox .ctl .sel button").all_inner_texts() == ["München ×"]
    assert pg.locator(".mapbox .ctl input[type=range]").input_value() == "25"
    assert pg.locator(".mp .d.dim").count() == dimmed
    assert pg.inner_text("#flap").startswith("in München")             # the readout, not the clock
    assert "Stellen" in pg.inner_text("#board .c.on")

    pg.reload()                                                        # ... and the same visitor tomorrow
    pg.wait_for_selector(".list .row")
    pg.get_by_role("button", name="Umkreis entfernen").wait_for()
    assert pg.evaluate("F.city") == ["München"] and pg.evaluate("F.radius") == 25
    assert pg.locator(".mp .d.dim").count() == dimmed
    pg.close()


def test_the_radius_names_its_hub_and_is_removed_with_it(page):
    """The circle is measured from the town picked first. Removing that town used to slide the hub onto
    the next one in silence -- München + Nürnberg at 50 km, drop München, and the range's own name flipped
    to "Radius around Nürnberg": a different set of towns covered, nothing on the screen naming the hub,
    nothing announced. The radius now belongs to its hub and leaves with it."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    page.locator('.mp .d[aria-label^="Nürnberg"]').click()
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    page.locator(RADIUS_ON).click()
    rng = page.locator(".mapbox .ctl input[type=range]")
    rng.wait_for()
    rng.focus()
    for _ in range(5):                                             # 25 -> 50 km
        page.keyboard.press("ArrowRight")
    page.wait_for_function("document.querySelector('.mapbox .ctl output').textContent==='50 km'")
    assert "München" in page.inner_text(".mapbox .ctl")            # the hub is named where the slider is
    assert rng.get_attribute("aria-label") == "Radius around München in kilometres"
    covered = {v for v, xy, dim in _dots(page) if not dim}
    assert page.locator(".mp .d.dim").count()                      # a circle is actually on

    page.locator(".mapbox .ctl .sel button").first.click()         # the hub's own chip
    page.wait_for_function("F.city.length===1")
    assert page.evaluate("F.radius") is None                       # the circle leaves with its centre ...
    assert page.locator(".mapbox .ctl input[type=range]").count() == 0
    assert page.locator(".mp .d.dim").count() == 0                 # ... it does not re-form around Nürnberg
    assert {v for v, xy, dim in _dots(page) if not dim} != covered
    said = page.inner_text(".mapbox .say")
    assert "Radius removed" in said and "München" in said, said    # and the status line says whose it was


def test_removing_a_town_chip_does_not_open_the_picker_over_the_map(page):
    """The chip's undo called map.pick(), which focuses the combobox -- and focus expands it, so a listbox
    covered the map and stayed there. The keyboard still lands on the picker; the list stays shut."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    page.locator('.mp .d[aria-label^="Passau"]').click()
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    trg = page.locator(".mapbox .pick .pk-t")

    page.locator(".mapbox .ctl .sel button").last.click()
    page.wait_for_function("F.city.length===1")
    assert page.evaluate("document.activeElement.classList.contains('pk-t')")   # focus went somewhere useful
    assert trg.get_attribute("aria-expanded") == "false"
    assert page.locator(".mapbox .pick .dd").is_hidden()
    assert page.locator(".mapbox .pick .o").count() == 0

    page.get_by_role("button", name="+ Town").click()              # the control that is meant to open it still does
    page.wait_for_function("document.querySelector('.mapbox .pick .pk-t').getAttribute('aria-expanded')==='true'")
    assert page.locator(".mapbox .pick .dd").is_visible()


def test_a_failing_clinics_endpoint_is_told_to_the_visitor_with_a_way_back(browser, base_url):
    """A 500 from /api/clinics ended the board at the filters: heading, prose and chips, then nothing, a
    blank count and an unhandled rejection in the console. Failures fail loudly (CLAUDE.md), so the error
    lands where the rows would have been, with what failed and a retry -- and the scroll sentinel stands
    down while it is up, so a broken API is not re-asked by every scroll.

    Real fetch path, not the offline mock: the routes below are the API.
    """
    import json
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    errors, calls, down = [], [], [True]
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    j = lambda route, body, status=200: route.fulfill(status=status, content_type="application/json",
                                                      body=json.dumps(body))
    pg.route("**/api/taxonomy*", lambda r: j(r, {}))
    pg.route("**/api/facets*", lambda r: j(r, {"cities": [{"v": "München", "n": 2}],
                                               "job_cities": [{"v": "München", "n": 7}],
                                               "fachrichtungen": [], "regierungsbezirk": []}))

    def clinics(route):
        calls.append(route.request.url)
        j(route, {"detail": "clinics is down"}, 500) if down[0] else \
            j(route, {"total": 1, "rows": [{"clinic_id": "1", "name": "Klinikum Musterstadt",
                                            "town": "München", "jobs_open": 3}]})
    pg.route("**/api/clinics*", clinics)

    pg.goto(f"{base_url}/index.html")
    pg.wait_for_selector("#clinics .list .empty")
    told = pg.inner_text("#clinics .list")
    assert pg.inner_text("#clinics .list .empty b") == "The hospital list could not be loaded."   # the thing
    assert pg.inner_text("#clinics .list .empty .raw") == "clinics is down"          # ... the server's detail, second
    assert not told.split("\n")[0].startswith("Failed to load"), told                # never the headline any more
    assert pg.locator("#clinics .list .row").count() == 0
    assert pg.get_by_role("button", name="Retry").count() == 1
    assert not errors, errors[:2]                                  # ... and not only in the console

    pg.evaluate("window.scrollTo(0,document.body.scrollHeight)")   # the sentinel does not re-ask on its own
    pg.wait_for_timeout(600)
    assert len(calls) == 1, calls

    assert pg.inner_text(".mapbox .go b") == "Hospital list not loaded"     # the map's own bar says it too
    assert pg.locator(".mapbox .go b").evaluate("n=>n.classList.contains('bad')")
    assert pg.inner_text(".mapbox .go .gobtn") == "See the error"            # not still "See hospitals"
    # ... and it is announced, not only painted: the break was written into a plain <b> while the page's
    # one live region sat empty, so a screen reader was never told the board had stopped working.
    said = pg.inner_text(".mapbox .say")
    assert "Hospital list not loaded" in said and "clinics is down" in said, said

    down[0] = False
    pg.get_by_role("button", name="Retry").click()                 # the way back
    pg.wait_for_selector("#clinics .list .row")
    assert pg.locator("#clinics .list .empty").count() == 0
    assert "Klinikum Musterstadt" in pg.inner_text("#clinics .list")
    assert pg.inner_text(".mapbox .go .gobtn") == "See hospitals"            # ... and the bar comes back with it
    assert pg.inner_text(".mapbox .go b").startswith("1 hospitals")
    assert not pg.locator(".mapbox .go b").evaluate("n=>n.classList.contains('bad')")
    assert pg.inner_text(".mapbox .say") == ""                               # the announcement goes with the error
    pg.close()
    assert len(calls) == 2, calls


def test_the_bar_says_it_is_loading_before_it_can_say_a_count(browser, base_url):
    """The count is null for the whole in-flight window, and the bar printed nothing for it: an empty
    count beside a live "See hospitals" reads as a board with nothing in it, not as a board still
    fetching. Every text the bar ever holds is recorded, so the empty one cannot slip past."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.add_init_script("""window.__bar=[]; addEventListener("DOMContentLoaded",()=>{
        const seen=()=>{ const b=document.querySelector('.mapbox .go b');
          if(b&&window.__bar[window.__bar.length-1]!==b.textContent) window.__bar.push(b.textContent); };
        new MutationObserver(seen).observe(document.body,{subtree:true,childList:true,characterData:true}); });""")
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_function("(b=>b&&/hospitals/.test(b.textContent))(document.querySelector('.mapbox .go b'))")
    seen = pg.evaluate("window.__bar")
    pg.close()
    assert "Loading …" in seen, seen                                # it says which of the three states it is in
    assert "" not in seen, seen                                     # ... and never the empty count
    assert seen[-1].endswith("50 hospitals"), seen


def test_the_visible_change_note_follows_the_page_language(page):
    """The English frame carried one German phrase: "© BKG (2026) dl-de/by-2-0, Daten verändert". The
    licence obliges the change note, not a language, and the endnote already holds both."""
    attr = lambda: page.inner_text(".mapbox .attr")
    assert page.evaluate("document.documentElement.lang") == "en"
    assert "data modified" in attr() and "verändert" not in attr()

    page.click('.lang button[data-lang="de"]')                     # applyLang() + route(): a fresh tree
    page.wait_for_function("document.querySelector('.mapbox .attr').textContent.includes('verändert')")
    assert "data modified" not in attr()
    assert page.locator('.mapbox .attr a[href="https://www.govdata.de/dl-de/by-2-0"]').count() == 1


@pytest.mark.parametrize("width,height", [(390, 844), (768, 1024), (1280, 800)])
def test_the_credit_and_its_marker_are_hit_testable_under_the_stuck_bar(browser, base_url, width, height):
    """dl-de/by-2-0 wants the credit where the geometry is shown, and [1] is the only way to the full
    notice. The action bar is position:sticky, so it parked on both: at 1280x800 the credit line sat at
    y=769 under a bar whose top was 656, and document.elementFromPoint at the marker's centre returned
    the chip row of the bar -- the tap aimed at [1] was swallowed and the endnote was never reached.
    Asserted with every control open, which is the tallest the bar ever gets.

    Plainly: `bottom:0` only engages while the card is taller than the window, so of these three the bar
    is stuck at 1280x800 alone -- the two phone/tablet sizes exercise the credit under a bar sitting at
    its natural place, not under a stuck one. The assertion below states that instead of implying it."""
    pg = browser.new_page(viewport={"width": width, "height": height})
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_selector(".mp .d")
    pg.locator('.mp .d[aria-label^="München"]').click()
    pg.wait_for_selector(".mapbox .ctl .sel button")
    pg.locator('.mp .d[aria-label^="Nürnberg"]').click()
    pg.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    pg.locator(RADIUS_ON).click()
    pg.locator(".mapbox .ctl input[type=range]").wait_for()
    pg.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'start'})")   # park the bar
    pg.wait_for_timeout(300)

    def rect(sel):
        return pg.evaluate("(()=>{const r=document.querySelector(%r).getBoundingClientRect();"
                           "return [Math.round(r.top),Math.round(r.bottom)];})()" % sel)
    bar, credit = rect(".mapbox .go"), rect(".mapbox .attr")
    assert bar[1] <= height + 1, bar                                # the bar is on the screen ...
    assert 0 <= credit[0] and credit[1] <= height, (credit, height)  # ... and it no longer covers the credit
    card = pg.evaluate("Math.round(document.querySelector('.mapbox').getBoundingClientRect().height)")
    stuck = abs(bar[1] - height) <= 1
    assert stuck == (card > height), (width, height, stuck, card)   # sticky only where there is room to stick
    assert stuck == (width == 1280), (width, stuck)                 # ... which of these three is exactly one
    seen = pg.evaluate("""(()=>{ const n=document.querySelector(%r), r=n.getBoundingClientRect();
        const e=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        return {top:Math.round(r.top),bottom:Math.round(r.bottom),w:Math.round(r.width),h:Math.round(r.height),
                hit:e===n||n.contains(e),what:e?e.tagName+"."+e.className:null}; })()""" % MARKER)
    assert 0 <= seen["top"] and seen["bottom"] <= height, seen
    assert seen["w"] >= 24 and seen["h"] >= 24, seen               # WCAG 2.5.8, still
    assert seen["hit"], seen                                        # the tap aimed at [1] reaches [1]
    pg.close()


def test_the_status_line_rides_in_the_bar_the_chips_live_in(browser, base_url):
    """The hub change was announced into a line a full viewport below the chips that caused it: at
    1280x800 the say line sat at y=909 with the bar stuck at the bottom of the screen, so the covered set
    changed with nothing on the screen saying so. The status line travels inside the bar now."""
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(f"{base_url}/index.html?mock=1&lang=en")
    pg.wait_for_selector(".mp .d")
    pg.locator('.mp .d[aria-label^="München"]').click()
    pg.wait_for_selector(".mapbox .ctl .sel button")
    pg.locator('.mp .d[aria-label^="Nürnberg"]').click()
    pg.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    pg.locator(RADIUS_ON).click()
    pg.locator(".mapbox .ctl input[type=range]").wait_for()
    pg.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'start'})")
    pg.wait_for_timeout(300)
    assert pg.evaluate("(r=>Math.abs(r.bottom-innerHeight)<=1)"
                       "(document.querySelector('.mapbox .go').getBoundingClientRect())")   # the bar is stuck

    pg.locator(".mapbox .ctl .sel button").first.click()            # the hub's own chip, from inside the bar
    pg.wait_for_function("F.radius===null")
    said = pg.inner_text(".mapbox .say")
    assert "Radius removed" in said and "München" in said, said
    box = pg.evaluate("(r=>[Math.round(r.top),Math.round(r.bottom)])"
                      "(document.querySelector('.mapbox .say').getBoundingClientRect())")
    assert 0 <= box[0] and box[1] <= 800, box                       # ... where the hand that caused it is looking
    pg.close()


def test_the_bar_names_what_the_circle_pulled_in(page):
    """"78 hospitals · München · Nürnberg" read as 78 hospitals in two towns; they came from thirteen.
    With a circle on, the bar names the covered set instead of repeating the chips, which are on the row
    directly underneath it."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    page.locator('.mp .d[aria-label^="Nürnberg"]').click()
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    assert page.inner_text(".mapbox .go b").endswith("München · Nürnberg")   # no circle: the chips are the filter

    page.locator(".mapbox .ctl .sel button").last.click()           # drop Nürnberg: everything picked is now the hub
    page.wait_for_function("F.city.length===1")
    page.locator(RADIUS_ON).click()
    rng = page.locator(".mapbox .ctl input[type=range]")
    rng.wait_for()
    rng.focus()
    for _ in range(15):                                             # 25 -> 100 km, far enough to pull towns in
        page.keyboard.press("ArrowRight")
    page.wait_for_function("document.querySelector('.mapbox .ctl output').textContent==='100 km'")
    covered = page.locator(".mp .d:not(.dim)").count()
    assert covered > 2, covered                                     # the circle really did pull towns in
    said = page.inner_text(".mapbox .go b")
    assert re.fullmatch(rf"\d+ hospitals · {covered} towns inside the radius around München", said), (said, covered)


def test_a_town_picked_outside_the_circle_is_not_counted_as_being_inside_it(page):
    """The bar counted the union of "picked" and "covered by the circle" and then called the whole set
    "%d towns inside the radius around %s" — a claim about geography, and false: with a 25 km circle on
    München, Würzburg picked 220 km away was counted into it, and its undimmed dot agreed with the
    sentence (eval_out/i2-bar-lie 7.71s: "7 Kliniken · 2 Orte im Umkreis um München", Würzburg dimmed=False,
    km München->Würzburg=220). Only what the circle reaches is counted as inside it now; what was picked
    outside it is named as that, and its dot is marked as picked rather than as reached."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    page.locator('.mp .d[aria-label^="Würzburg"]').click()
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    page.locator(RADIUS_ON).click()
    page.locator(".mapbox .ctl input[type=range]").wait_for()

    dots, sel = _dots(page), page.evaluate("F.city")
    hub = next(xy for v, xy, _ in dots if v == "München")
    assert _km(hub, next(xy for v, xy, _ in dots if v == "Würzburg")) > 200      # ... with a 25 km circle on
    inside = [v for v, xy, _ in dots if _km(hub, xy) <= 25]
    assert inside == ["München"], inside                            # the hub, and nothing else, is inside 25 km
    said = page.inner_text(".mapbox .go b")
    assert "1 town inside the radius around München" in said, said
    assert said.endswith("+ 1 picked outside it"), said              # Würzburg, named for what it is
    assert "2 towns inside the radius" not in said, said

    wz = page.locator('.mp .d[aria-label^="Würzburg"]')
    assert wz.get_attribute("aria-pressed") == "true"                # the map says "picked" ...
    assert "on" in wz.get_attribute("class")
    assert "Würzburg" in sel                                         # ... and it is in the filter, so not dimmed
    for v, xy, dim in dots:                                          # nothing else changed: dim is still "not in the filter"
        assert dim == (_km(hub, xy) > 25 and v not in sel), (v, round(_km(hub, xy)), dim)


def test_the_radius_switch_names_the_town_it_will_centre_on(page):
    """"Radius" alone silently meant "around whichever town was picked first", and F.city[0] is not
    visibly the first anything. The hub is named beside the switch and in its accessible name."""
    page.locator('.mp .d[aria-label^="München"]').click()
    page.wait_for_selector(".mapbox .ctl .sel button")
    page.locator('.mp .d[aria-label^="Nürnberg"]').click()
    page.wait_for_function("document.querySelectorAll('.mapbox .ctl .sel button').length===2")
    assert page.locator(RADIUS_ON).get_attribute("aria-label") == "Radius around München"
    hub = page.locator(".mapbox .ctl .hub")
    assert hub.is_visible() and hub.inner_text() == "around München"
    assert page.locator(RADIUS_ON).inner_text() == "Radius"         # the switch is still the switch


def test_two_spellings_on_one_pair_of_coordinates_are_one_town(page):
    """The gazetteer carries several keys for the same municipality, so folding by key was not enough.
    On the live board (2026-09-11) "Lauf a. d. Pegnitz" (8 jobs) and "Lauf an der Pegnitz" (5) resolved to
    two different keys -- "laufadpegnitz" and "laufanderpegnitz" -- that both hold 49.512,11.278, and drew
    two dots, two labels and two chips on one pixel. Same point on the map is the same town: one dot with
    13, and both spellings still reach it and still ride out to the API."""
    fc = """{job_cities:[{v:"Lauf a. d. Pegnitz",n:8},{v:"Lauf an der Pegnitz",n:5},{v:"Nürnberg",n:4}]}"""
    got = page.evaluate("""(()=>{ const box=townMap(%s,[],()=>{},()=>{});
        const a="Lauf a. d. Pegnitz", b="Lauf an der Pegnitz";
        return {dots:[...box.querySelectorAll('.mp .d')].map(d=>d.getAttribute('aria-label')),
                keys:[townKey(a),townKey(b)],
                same_xy:JSON.stringify(townXY(a))===JSON.stringify(townXY(b)),
                canon:[canonTown(a),canonTown(b)], expand:expandTowns([b])}; })()""" % fc)
    assert got["keys"][0] != got["keys"][1], got["keys"]            # two keys: the case key-folding could not see
    assert got["same_xy"]                                          # ... and one pair of coordinates behind them
    assert got["dots"] == ["Lauf a. d. Pegnitz, 13 jobs", "Nürnberg, 4 jobs"], got["dots"]
    assert got["canon"] == ["Lauf a. d. Pegnitz", "Lauf a. d. Pegnitz"], got["canon"]
    assert sorted(got["expand"]) == ["Lauf a. d. Pegnitz", "Lauf an der Pegnitz"]   # nothing left behind the dot


def test_a_job_fact_prints_its_glossary_term_and_no_route_prints_an_object(page, base_url):
    """`term()` builds a <span> with the glossary tooltip on it, and the Web-Nachweis row concatenated it
    onto a string: 2126 of 2656 live postings (80%) printed the literal "[object HTMLSpanElement]" where
    the verify status should be. Every route is checked, not only the one row that was reported."""
    page.goto(f"{base_url}/index.html?mock=1&lang=en#/job/1")
    page.wait_for_selector(".card dl dd")
    facts = dict(page.evaluate("""[...document.querySelectorAll('.card dl dt')]
        .map(dt=>[dt.textContent,dt.nextElementSibling.textContent])"""))
    assert facts["Web proof"] == "reachable", facts                # the term, not the element it is built from, and not the raw enum
    for route in ["#/", "#/jobs", "#/clinic/16100", "#/job/1", "#/job/2"]:
        page.goto(f"{base_url}/index.html?mock=1&lang=en{route}")
        page.wait_for_selector("#view .row, #view .card, #view .list")
        page.wait_for_timeout(400)
        assert "[object " not in page.inner_text("body"), route


def test_browser_back_puts_the_rows_and_the_scroll_position_back(page):
    """Every view is built from nothing and paged() starts at its first page again, so browser-back landed
    at the top of the home page with 20 rows: the visitor's scroll position and every row they had loaded
    were gone. Leaving a route records both; coming back replays them."""
    for _ in range(4):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(500)
    rows = page.locator("#view .list .row").count()
    y = page.evaluate("scrollY")
    assert rows > 20 and y > 0, (rows, y)                          # the list really did grow past its first page
    page.evaluate("document.querySelectorAll('#view .list .row')[5].click()")   # click(), not Playwright's: no scroll
    page.wait_for_selector("#view .idh")                           # the clinic page
    assert page.evaluate("scrollY") == 0
    page.go_back()
    page.wait_for_function("document.querySelectorAll('#view .list .row').length===%d" % rows, timeout=20000)
    page.wait_for_function("Math.abs(scrollY-%d)<=2" % y, timeout=20000)


# --- the router, the shared town vocabulary and the web-proof row --------------------------------

def test_a_page_that_lands_late_cannot_paint_over_the_page_the_visitor_moved_to(page):
    """Every view is built by an async function and route() wrote whatever came back into #view: a detail
    fetch that resolved after the visitor had navigated away replaced the live page with the stale one, and
    since no hashchange followed, the wrong page stayed under that URL for the rest of the session.

    The clinic request is held for 1.2 s here, and the visitor leaves for the job list 200 ms into it."""
    page.evaluate("""()=>{ const orig=window.api;
        window.api=(p,o)=> p.startsWith("/api/clinics/")
            ? new Promise(r=>setTimeout(()=>orig(p,o).then(r),1200))
            : orig(p,o); }""")
    page.evaluate("location.hash='#/clinic/16100'")
    page.wait_for_timeout(200)                                     # the slow builder is in flight
    page.evaluate("location.hash='#/jobs'")
    page.wait_for_selector("#view a.row[href^='#/job/']")
    page.wait_for_timeout(1600)                                    # the held clinic page lands in here
    assert page.evaluate("location.hash") == "#/jobs"
    assert page.locator("#view .idh").count() == 0, "the stale clinic page painted over the job list"
    assert page.locator("#view a.row[href^='#/job/']").count() > 0


# One town, three spellings, and only two of them written the way the gazetteer writes it. The board has
# exactly this: "München" 332, "München Süd" 6, "München Mitte" 7, "München Flughafen" 6 -- four options,
# four counts, and picking any one of them asked /api/jobs for that spelling alone.
SPLIT_FACETS = {"job_cities": [{"v": "München", "n": 9}, {"v": "80331 München", "n": 3},
                               {"v": "München Süd", "n": 2}, {"v": "Nürnberg", "n": 4}],
                "role_class": [{"v": "pflegefachkraft", "n": 18}]}


def test_the_jobs_town_picker_speaks_the_towns_the_map_speaks(browser, base_url):
    """The map picker was deduped and the job board's was not: it offered the raw facet, one option per
    spelling with the counts split, and the pick it sent to the API was that one spelling. Both controls
    now speak resolved towns, and one town means every spelling behind it on both pages."""
    asked, pg = [], browser.new_page(viewport={"width": 1280, "height": 900})

    def serve(route):
        url = route.request.url
        if "/api/jobs" in url:
            asked.append(url)
            route.fulfill(json={"total": 0, "rows": []})
        else:
            route.fulfill(json=SPLIT_FACETS if "/api/facets" in url else {})

    pg.route("**/api/**", serve)                                   # no ?mock=1: the real fetch path, answered here
    pg.goto(f"{base_url}/index.html#/jobs")
    pg.wait_for_selector(".filters .pick.s .pk-t")
    hits = pg.evaluate("""(()=>{ const p=document.querySelector('.filters .pick.s'), i=p.querySelector('.pk-t');
        i.value='80331'; i.dispatchEvent(new Event('input'));      // the postcode spelling still finds the town
        const out=[...p.querySelectorAll('.dd .o')].map(o=>o.textContent);
        p.querySelector('.dd .o').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
        return out; })()""")
    assert hits == ["München14"], hits                             # one option, 9+3+2, under the one spelling the page shows
    pg.wait_for_function("window.__n=%d, document.querySelectorAll('.filters .sel button').length===1" % len(asked))
    assert pg.eval_on_selector(".filters .sel button", "n=>n.textContent") == "München ×"
    assert pg.evaluate("canonTown('München Süd')") == "München"    # the same vocabulary the map dots use
    picked = parse_qs(urlparse(asked[-1]).query)["city"][0].split(",")
    assert sorted(picked) == ["80331 München", "München", "München Süd"], picked
    pg.close()


def test_the_web_proof_row_is_translated_and_says_when_nothing_was_checked(page):
    """The row printed the raw enum in both languages ("Web-Nachweis: live", "Web proof: gone"), and a
    posting with no check at all -- 1082 of the 3203 open postings on the board -- had no row: silence read
    as "no proof question here" instead of "nobody has looked yet"."""
    labels = page.evaluate("""['live','gone','blocked','error',null,'brandneu']
        .map(v=>[v,proofNode(v).textContent,(proofNode(v).getAttribute('data-tip')||'').split('\\n')[1]])""")
    assert [l[1] for l in labels] == ["reachable", "removed (404)", "bot wall", "check failed",
                                      "not checked", "brandneu"], labels        # a value the taxonomy grows later prints itself
    assert labels[0][2] and "live" not in labels[0][1]                          # the taxonomy sentence rides along as the tooltip

    page.evaluate("""()=>{ const orig=window.api;                               // a posting nobody checked yet
        window.api=(p,o)=> p.startsWith("/api/jobs/")
            ? orig(p,o).then(j=>{ const {verify_status,...rest}=j; return rest; })
            : orig(p,o); }""")
    page.evaluate("location.hash='#/job/1'")
    page.wait_for_selector("#view .card dl dd")
    facts = dict(page.evaluate("""[...document.querySelectorAll('.card dl dt')]
        .map(dt=>[dt.textContent,dt.nextElementSibling.textContent])"""))
    assert facts["Web proof"] == "not checked", facts
    page.click('.lang button[data-lang="de"]')
    page.wait_for_selector("#view .card dl dd")
    facts = dict(page.evaluate("""[...document.querySelectorAll('.card dl dt')]
        .map(dt=>[dt.textContent,dt.nextElementSibling.textContent])"""))
    assert facts["Web-Nachweis"] == "nicht geprüft", facts


def test_towns_a_finger_cannot_tell_apart_are_one_pin_that_says_so(page):
    """Gräfelfing and Planegg land 1.4 CSS px apart on a 390 px screen (Bischofswiesen and Berchtesgaden
    2.8): one blob, and the tap went to whichever centre was a pixel closer -- a coin toss nobody can see
    or aim at. They share one pin, it says it is two towns, it names them, and it picks both."""
    fc = """{job_cities:[{v:"Gräfelfing",n:3},{v:"Planegg",n:2},{v:"Nürnberg",n:9}]}"""
    got = page.evaluate("""(()=>{ let picked=null; const box=townMap(%s,[],v=>picked=v,()=>{});
        const ds=[...box.querySelectorAll('.mp .d')];
        const merged=ds.find(d=>d.getAttribute('aria-label').includes('Planegg'));
        merged.dispatchEvent(new MouseEvent('mouseenter'));
        return {n:ds.length, aria:merged.getAttribute('aria-label'),
                label:[...box.querySelectorAll('.mp .lb')].map(n=>n.textContent),
                cap:box.querySelector('.cap').textContent, picked:(merged.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'})),picked)}; })()""" % fc)
    assert got["n"] == 2, got                                      # three towns, two pins
    assert got["aria"] == "Gräfelfing · Planegg, 5 jobs", got      # one name for the pin, both towns in it
    assert "2 towns" in got["label"], got                          # ... and the map says it is two, not a name
    assert got["cap"] == "Gräfelfing · Planegg  5 jobs", got       # the caption is where they are named
    assert sorted(got["picked"]) == ["Gräfelfing", "Planegg"]      # picking the blob picks both, not the nearer one
