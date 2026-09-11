"""The built deck (web/deck.html): arrow keys move one slide, plain scrolling moves the counter too,
no slide starts underneath the fixed top bar, and printing gives one page per slide.

The deck now carries real content, so the slides are navigated as they ship — and a slide with a long
table is taller than the viewport, which is why the positions are read off the elements instead of
assuming one viewport per slide. No app, no network. Skipped when Playwright or Chromium is missing.
"""
import functools, http.server, io, json, pathlib, re, threading, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
DECK = WEB / "deck.html"


@pytest.fixture(scope="module")
def base_url():
    """web/ over http, so the deck's own fetch("/api/me") is a request page.route can answer."""
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


def open_deck(browser, base_url, width=1280, default_credentials=False, height=900):
    """The deck at `width`, with GET /api/me mocked -- default_credentials=True is what raises the red
    banner, which is the state that makes the bar tall enough to swallow a slide's heading."""
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route("**/api/me", lambda r: r.fulfill(status=200, content_type="application/json",
                                                body=json.dumps({"role": "anonymous",
                                                                 "default_credentials": default_credentials})))
    page.goto(f"{base_url}/deck.html")
    page.wait_for_selector("#deck .slide")
    return page


def test_arrow_keys_and_scrolling_agree():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        page.set_content(DECK.read_text(encoding="utf-8"))
        page.wait_for_selector(".slide")
        n = page.eval_on_selector_all("#deck .slide", "els => els.length")
        assert n > 1, "the deck needs at least two slides to navigate between"
        assert page.inner_text("#count") == f"1 / {n}" and page.is_disabled("#prev")

        second = page.eval_on_selector_all("#deck .slide", "els => Math.round(els[1].offsetTop)")
        page.keyboard.press("ArrowRight")
        page.wait_for_function(f"Math.round(scrollY) === {second}")     # exactly one slide down
        page.wait_for_function(f"document.querySelector('#count').textContent === '2 / {n}'")

        page.keyboard.press("ArrowLeft")
        page.wait_for_function("scrollY === 0")                         # let the smooth scroll land first
        page.wait_for_function(f"document.querySelector('#count').textContent === '1 / {n}'")

        page.keyboard.press("End")                                      # last slide, both ways
        page.wait_for_function(f"document.querySelector('#count').textContent === '{n} / {n}'")
        assert page.is_disabled("#next")

        page.evaluate(f"scrollTo(0, {second})")                         # every slide is reachable by scrolling
        page.wait_for_function(f"document.querySelector('#count').textContent === '2 / {n}'")
        browser.close()


def test_fast_clicks_are_not_dropped():
    """One click, one slide, however fast they come. A smooth scroll takes ~400 ms and the slide being left
    keeps crossing the observer's band while it runs; the observer used to write the counter back to that
    slide, so the next click re-issued the jump it had just made -- 13 clicks 40 ms apart landed on slide 4
    of 14 instead of the last one."""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        page.set_content(DECK.read_text(encoding="utf-8"))
        page.wait_for_selector(".slide")
        n = page.eval_on_selector_all("#deck .slide", "els => els.length")
        for _ in range(n - 1):
            page.click("#next")
            page.wait_for_timeout(40)
        page.wait_for_function(f"document.querySelector('#count').textContent === '{n} / {n}'")
        last = page.eval_on_selector_all("#deck .slide", "els => Math.round(els[els.length - 1].offsetTop)")
        page.wait_for_function(f"Math.round(scrollY) === {last}")       # counter and scroll agree
        assert page.is_disabled("#next")
        for _ in range(n - 1):                                          # and back, just as fast
            page.click("#prev")
            page.wait_for_timeout(40)
        page.wait_for_function(f"document.querySelector('#count').textContent === '1 / {n}'")
        page.wait_for_function("Math.round(scrollY) === 0")
        browser.close()


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_no_slide_starts_under_the_fixed_bar(browser, base_url, width):
    """The bar is fixed, so a slide scrolled to its start begins at y=0 of the viewport: everything above
    the bar's own height is hidden behind it. The bar is not a constant -- the default-credentials banner
    arrives from GET /api/me and wraps to two or three lines on a phone (141px at 390 vs 99px at 1280),
    which is how the first heading of every slide ended up underneath it. `.slide`'s top padding is
    max(104px, --bar + 24px) with --bar published by a ResizeObserver; this reads the geometry back.

    No scrolling: with scroll-snap-align:start / scrollIntoView({block:"start"}) the scroll position of a
    slide is its own document top, so the first element's viewport y there is exactly the distance between
    the two, measured statically.
    """
    page = open_deck(browser, base_url, width, default_credentials=True)
    page.wait_for_selector("#warn:not([hidden])")                 # banner in, bar at its tallest
    page.wait_for_function("() => { const b = document.querySelector('.bar').offsetHeight;"
                           "  return getComputedStyle(document.documentElement).getPropertyValue('--bar').trim() === b + 'px'; }")
    geo = page.evaluate("""() => {
      const y = el => el.getBoundingClientRect().top + scrollY;
      return {bar: Math.round(document.querySelector('.bar').getBoundingClientRect().height),
              tops: [...document.querySelectorAll('#deck .slide')].map(s => Math.round(y(s.firstElementChild) - y(s)))};
    }""")
    page.close()
    assert geo["bar"] > 0 and len(geo["tops"]) > 1
    hidden = [(i + 1, top) for i, top in enumerate(geo["tops"]) if top < geo["bar"]]
    assert not hidden, f"at {width}px the bar is {geo['bar']}px tall; (slide, first element y) under it: {hidden}"


def test_slide_one_fits_the_viewport_with_the_banner_up(browser, base_url):
    """Slide 1 has to be no taller than the viewport in the state the board is actually in: the red
    default-credentials banner up, which is what `GET /api/me` answers while the shipped passphrase is
    unchanged (deck slide 13, item 1).

    `html{scroll-snap-type: y proximity}` snaps to the *end* edge of a snap area that is taller than the
    snapport, so a slide 19px over the viewport drags a reader who stopped inside it back down -- measured
    2026-09-11: slide 1 was 919px against 900, the bar 109px, and a hand stopping the click-scroll at
    ~303px was re-snapped to 9px. Fixed in the copy and in the `.nums` grid (148px -> 128px columns, eight
    tiles in one row), not by weakening the snap: proximity is what makes the arrow keys and the wheel land
    on the same places.

    Only slide 1 is asserted. Every other slide is allowed to outgrow the viewport (slide 17 is 1,578px
    and its table has to be reachable); slide 1 is the one the deck opens on, and the one the interrupted-
    scroll test lands inside."""
    page = open_deck(browser, base_url, 1280, default_credentials=True, height=900)
    page.wait_for_selector("#warn:not([hidden])")                 # banner in, bar at its tallest
    page.wait_for_function("() => { const b = document.querySelector('.bar').offsetHeight;"
                           "  return getComputedStyle(document.documentElement).getPropertyValue('--bar').trim() === b + 'px'; }")
    geo = page.evaluate("""() => ({bar: document.querySelector('.bar').offsetHeight,
                                   first: document.querySelector('#deck .slide').offsetHeight,
                                   viewport: innerHeight})""")
    page.close()
    assert geo["bar"] > 0, f"the banner never raised the bar: {geo}"
    assert geo["first"] <= geo["viewport"], (
        f"slide 1 is {geo['first']}px against a {geo['viewport']}px viewport with the {geo['bar']}px bar up; "
        f"proximity snapping will pull a stopped reader back to its end")


REF = re.compile(r"([\w.-]+/[\w./-]+\.(?:py|json|sql|md|html|csv|service)):(\d+)(?:-(\d+))?")


def test_every_file_line_reference_in_the_deck_still_exists():
    """The deck cites the tree by file:line, and the tree moves under it -- four of those references had
    drifted by the sixth verification round (app/settings.py:162-163 had become :200-201, app/data.py:189
    -> :237, the magic-link pair :355/:383 -> :325/:353, app/main.py:249-268 -> :255-274). A reference
    pointing past the end of its file is worse than none, and it is the one half of "is this still true"
    that does not need a human. What it does not check is whether the line still *says* what the slide
    claims; that stays a re-read.

    Only references carrying a path are checked (`app/auth.py:48`, not the bare `bite.py:182` written
    inside a sentence that already names `pflege_jobs/sources/`)."""
    src = (WEB / "deck.template.html").read_text(encoding="utf-8")
    bad = []
    for rel, start, end in REF.findall(src):
        p = WEB.parent / rel
        if not p.is_file():
            bad.append(f"{rel}: no such file")
            continue
        n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        if not 1 <= int(start) <= int(end or start) <= n:
            bad.append(f"{rel}:{start}-{end or start} but the file has {n} lines")
    assert len(REF.findall(src)) >= 20, "the reference scan found almost nothing -- check the pattern"
    assert not bad, "file:line references that no longer resolve:\n" + "\n".join(bad)


def test_printing_gives_one_page_per_slide(browser, base_url):
    """14 slides printed as 15 pages: slide 13 was 16px too tall for its A4 page box, so it broke after its
    <h2> and its table opened a page of its own. The print rules (break-inside:avoid on .slide, table type
    down to body size) fixed it -- the count is the assertion, so any slide that grows past a page fails
    here instead of in someone's printer.

    It caught the same slide again on 2026-09-11: the round that rewrote slide 13 (throttle reverted, CSP
    finding, pending migration) took it to 1105px against a 1047px box and the deck printed 16 slides as
    17 pages. Trimmed back to 1000px; 16 -> 16 now. The measurement that localises it is the slide's own
    height under `emulate_media("print")` at a 718px viewport (A4 minus 10mm margins), not the pdf.
    """
    pdfplumber = pytest.importorskip("pdfplumber")
    page = open_deck(browser, base_url)
    slides = page.eval_on_selector_all("#deck .slide", "els => els.length")
    pdf = page.pdf(format="A4", margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"})
    page.close()
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        pages = len(doc.pages)
    assert pages == slides, f"{slides} slides printed as {pages} A4 pages"


# The slide the reader is on, measured the way the deck's own counter has to measure it: the slide the
# middle of the viewport is inside. Kept next to the tests that use it so a failure prints both numbers.
MID_SLIDE = ("() => [...document.querySelectorAll('#deck .slide')]"
             ".findLastIndex(s => s.offsetTop <= scrollY + innerHeight / 2) + 1")


def test_counter_recounts_after_an_interrupted_click_scroll(browser, base_url):
    """A hand that interrupts a nav-button scroll leaves the counter on the slide the reader stopped on.

    The click-scroll is smooth and takes ~400 ms; while it runs the click owns the counter, or a fast
    second click would re-issue the jump it just made. The lock used to come off on `scrollend` -- and
    `scrollend` is not guaranteed to arrive: interrupt the animation in its first frames and Chromium
    cancels it ~2px in and fires nothing at all, so the counter kept the clicked slide for good
    (eval_out/g4-deck-1280/steps.json, 15.51s "counter after the interruption=2 / 14" with slide 1 under
    the middle of the screen, still 2 / 14 at 18.11s). Reproduced as a mutation on 2026-09-11 rather than
    quoted: rebuild deck.html with `function settle(){}` plus the old `addEventListener("scrollend", () =>
    { target = null; show(current()); })` and this same hand leaves the counter on "2 / 17" while the
    reader sits in slide 1 at y=303, with 0 scrollend events fired. As it ships: "1 / 17" at the same y.
    (Re-run in the sixth round, 2026-09-11, on 17 slides; it read 2 / 16 against 1 / 16 before that.)

    The hand here catches the page 200px into the animation and stops it where it is -- exactly what a
    reader's finger does, and the case Chromium ends with no scrollend at all (the run below reports 0 of
    them). It leaves the page resting at ~303px, i.e. inside slide 1, one third of the way to slide 2.
    The assertion is the invariant and not the mechanism: once the page has stopped moving, the counter
    is the slide under the middle of the viewport, whatever event did or did not fire.
    """
    page = open_deck(browser, base_url)
    n = page.eval_on_selector_all("#deck .slide", "els => els.length")
    page.evaluate("() => { window.__ends = 0; addEventListener('scrollend', () => window.__ends++); }")
    page.evaluate("() => addEventListener('scroll', function hand() {"                      # the reader's hand
                  "  if (scrollY > 200) { removeEventListener('scroll', hand); scrollTo(0, scrollY); } })")
    page.click("#next")
    page.wait_for_timeout(1200)                                   # long past both the animation and any settle
    state = page.evaluate("""() => ({y: Math.round(scrollY), ends: window.__ends,
                                     counter: document.getElementById('count').textContent.trim()})""")
    state["mid"] = page.evaluate(MID_SLIDE)
    second = page.eval_on_selector_all("#deck .slide", "els => Math.round(els[1].offsetTop)")
    # A y far below 200 is not this test's subject failing, it is its setup: `scroll-snap-type: y proximity`
    # re-snaps a *stopped* scroll, and once slide 1 outgrows the 900px viewport (offsetHeight 909 was
    # enough) the snap that used to land on 0 lands on the slide's end instead and drags the page back to
    # 9px. Keeping slide 1 within one viewport is its own test now -- and in the state that matters, with
    # the red banner up, which this one does not have: test_slide_one_fits_the_viewport_with_the_banner_up.
    assert 200 < state["y"] < second, (f"the hand did not stop the scroll short of slide 2: {state}; "
                                       f"slide 1 is {second}px tall against a 900px viewport")
    assert state["counter"] == f"{state['mid']} / {n}", (
        f"counter {state['counter']!r} but slide {state['mid']} is under the middle of the screen "
        f"at scrollY={state['y']} ({state['ends']} scrollend events fired)")

    # ...and the counter is not merely right by accident: the lock is off, so reading on still moves it.
    page.evaluate(f"scrollTo({{top: {second}, behavior: 'instant'}})")
    page.wait_for_function(f"document.querySelector('#count').textContent === '2 / {n}'")
    page.close()


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_nothing_reads_underneath_the_slide_nav(browser, base_url, width):
    """The nav is opaque, so any text run under it is hidden, not merely overlapped.

    It used to be a pill fixed to the bottom right corner. A slide with a long list is taller than the
    viewport and keeps running underneath that corner: at 390px 13 of the 14 slides had body copy under
    the pill at their own start position (slide 4: 3 runs), and 4 of them still did at 768px. Measured
    per slide, at the position `scrollIntoView({block:"start"})` puts it in, with the client rects of
    every text node against the nav's own rect -- no screenshots.

    Re-verified 2026-09-11 as a mutation rather than as history: putting the pill back (fixed, bottom
    right, and `.bar{backdrop-filter:none}` so the blur stops being a containing block for it) hides 49
    runs on 16 of the 17 slides at 390px and 20 runs on 10 slides at 768px; at 1280px it now hides
    nothing, where the round before it hid 2. As it ships: 0, 0, 0. (Re-measured after the sixth round
    split security into three slides; it read 37/15, 14/7 and 2/1 before that, and 40/13, 10/5 and 1
    before that. The mutation's numbers move with the copy, the three zeros do not.)
    """
    page = open_deck(browser, base_url, width, default_credentials=True)
    page.wait_for_selector("#warn:not([hidden])")                 # banner in, bar at its tallest
    page.wait_for_function("() => { const b = document.querySelector('.bar').offsetHeight;"
                           "  return getComputedStyle(document.documentElement).getPropertyValue('--bar').trim() === b + 'px'; }")
    hits = page.evaluate("""() => {
      const nav = document.querySelector('.nav').getBoundingClientRect();
      const slides = [...document.querySelectorAll('#deck .slide')], out = [];
      for (let i = 0; i < slides.length; i++) {
        scrollTo({top: slides[i].offsetTop, behavior: 'instant'});
        const runs = [], walk = document.createTreeWalker(slides[i], NodeFilter.SHOW_TEXT);
        for (let t; (t = walk.nextNode()); ) {
          if (!t.data.trim()) continue;
          const r = document.createRange(); r.selectNodeContents(t);
          for (const b of r.getClientRects())
            if (b.width >= 1 && b.height >= 1 &&
                b.left < nav.right && b.right > nav.left && b.top < nav.bottom && b.bottom > nav.top)
              runs.push(t.data.trim().slice(0, 40));
        }
        if (runs.length) out.push([i + 1, runs.length, runs[0]]);
      }
      return {nav: [nav.left, nav.top, nav.right, nav.bottom].map(Math.round), covered: out};
    }""")
    page.close()
    assert hits["nav"][2] > hits["nav"][0], f"the nav has no width at {width}px: {hits['nav']}"
    assert not hits["covered"], (f"at {width}px the nav sits at {hits['nav']} (l,t,r,b); "
                                 f"(slide, text runs under it, first run): {hits['covered']}")


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_no_table_or_code_block_overflows_its_box(browser, base_url, width):
    """Every table and every <pre> fits the box it is drawn in, at every width the deck is read at.

    They used to hang out of it and be clipped: at 390px two tables (slide 9 by 170px) and three code
    blocks (up to 468px) were cut mid-word, and a touch device draws no scrollbar to say that the rest
    is one swipe away. scrollWidth vs clientWidth is the measurement -- an element that is wider than
    its box reports the difference whether or not a scrollbar is visible.

    Re-verified 2026-09-11 as a mutation, and again in the sixth round after two more tables joined the
    deck: with `pre{white-space:pre}` and `overflow-wrap:normal` in the cells put back, 390px reports 5
    elements over their box (pre 273px on slide 4, tables 170px and 22px on 9 and 10, pre 280px and 468px
    on 11) and 768px reports 1 (pre 90px); 1280px reports none even mutated. As it ships: none at any of
    the three widths -- the new security tables on 13-15 are 4-column ones that fit. (Slide 9's table
    reads 170px mutated, not the 298px an earlier round recorded.)
    """
    page = open_deck(browser, base_url, width)
    over = page.evaluate("""() => [...document.querySelectorAll('#deck table, #deck pre')].map(e => ({
        tag: e.tagName, over: e.scrollWidth - e.clientWidth, box: e.clientWidth,
        slide: [...document.querySelectorAll('#deck .slide')].indexOf(e.closest('.slide')) + 1,
        text: e.textContent.trim().slice(0, 40).replace(/\\s+/g, ' ')})).filter(o => o.over > 1)""")
    total = page.eval_on_selector_all("#deck table, #deck pre", "els => els.length")
    page.close()
    assert total >= 5, f"expected the deck's tables and code blocks to be measured, found {total}"
    assert not over, f"at {width}px, wider than their own box: {over}"


def test_the_9ch_floor_keeps_short_names_whole(browser, base_url):
    """`min-width:9ch` on the identifier columns, pinned by what it actually buys -- and only that.

    The tables fit their box at 390px only because `overflow-wrap:anywhere` lets a cell's min-content
    width fall to one character (the test above), and `anywhere` breaks a word wherever the line runs
    out, not only when the word is wider than its column. So mid-word breaks are the norm here, not the
    exception: re-measured 2026-09-11 on today's 17 slides, 85 words break across 63 cells at 390x844,
    28 of them inside the floored columns, none shorter than 9 characters. Nothing in this test claims
    otherwise.

    What the floor buys is the short end. Remove it (`min-width:0`) and the floored columns break 65
    words rather than 28, down to `GET` (3), `PUT` (3), `POST` (4), `idle` (4), `scope` (5), `clinic`
    (6), `adapter` (7), `employer` (8) -- which is the assertion: inside `.t th` / `.t td.mono` no word
    of 8 characters or fewer may be broken across lines. (11ch is no longer the loss it was on the fifth
    round's copy: today it breaks 25 in this column against 28, with the same 85 across the table. The
    floor is what is asserted; the totals move with every slide.)
    """
    page = open_deck(browser, base_url, 390, height=844)
    words = page.evaluate(r"""() => {
      const broken = [], subjects = [];
      for (const cell of document.querySelectorAll('#deck .t th, #deck .t td.mono')) {
        const walk = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        for (let t; (t = walk.nextNode()); ) {
          for (const m of t.data.matchAll(/\S+/g)) {
            if (m[0].length > 8) continue;                   // longer names are expected to break
            subjects.push(m[0]);
            const r = document.createRange();
            r.setStart(t, m.index); r.setEnd(t, m.index + m[0].length);
            const lines = new Set([...r.getClientRects()].filter(b => b.width >= 1 && b.height >= 1)
                                                         .map(b => Math.round(b.top)));
            if (lines.size > 1) broken.push([m[0], Math.round(cell.getBoundingClientRect().width)]);
          }
        }
      }
      return {broken, subjects: subjects.length};
    }""")
    page.close()
    assert words["subjects"] >= 40, f"too few short words in the floored columns to prove anything: {words}"
    assert not words["broken"], (f"at 390px these are broken mid-word inside a 9ch column "
                                 f"(word, cell width): {words['broken']}")
