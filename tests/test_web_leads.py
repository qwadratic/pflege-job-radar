"""Leads view (web/pro.html#/leads, docs/wa-dashboard.md): who needs a human comes first, every card below.

Most tests run the built page against its own offline mock (`?mock=1`): 28 invented threads that cover every
escalation code, every "reply stuck" signal, two handoffs, the checks (flags), the ended states and one test number.
The last two tests leave the mock and route /api/* by hand, to prove a missing or broken WhatsApp API is said out
loud instead of rendering as "0 leads".
"""
import functools, http.server, json, re, threading, pathlib, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
# The mock's 27 real threads: 8 escalated (7 codes + an evaluator "red"), 3 with a stuck reply, 2 handoffs.
NEEDS_HUMAN = 13


@pytest.fixture(scope="module")
def browser_and_base():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        yield browser, f"http://127.0.0.1:{srv.server_address[1]}"
        browser.close()
    srv.shutdown()


def _open(browser, url, width=1440):
    ctx = browser.new_context(viewport={"width": width, "height": 1000})
    ctx.add_init_script("localStorage.setItem('lang','en')")
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)[:300]))
    page.goto(url)
    return page


@pytest.fixture
def leads(browser_and_base):
    browser, base = browser_and_base
    page = _open(browser, f"{base}/pro.html?mock=1#/leads")
    page.wait_for_selector(".lh-list")
    yield page
    assert not page.errors, page.errors[:2]
    page.context.close()


def test_the_count_of_leads_needing_a_human_is_everywhere_and_leaves_out_the_test_number(leads):
    assert leads.inner_text("h1").startswith(f"{NEEDS_HUMAN} leads need a human")
    assert leads.locator("#nav a", has_text="Leads").locator(".bd.hot").inner_text() == str(NEEDS_HUMAN)
    assert leads.title().startswith(f"({NEEDS_HUMAN}) ")
    # the escalated test number (+43 ... 4242) is neither counted nor listed until the toggle shows it
    assert leads.locator(".lh-list > .lh").count() == NEEDS_HUMAN
    assert "4242" not in leads.inner_text(".lh-list")


def test_needs_a_human_is_grouped_by_tier_longest_waiting_first(leads):
    groups = [re.sub(r"\s*\d+\s*$", "", g).strip().lower() for g in leads.locator(".lh-g").all_inner_texts()]
    assert groups == ["escalated", "reply stuck", "handoff to clinics"]
    first = leads.locator(".lh-list > .lh").first
    assert "8830" in first.inner_text()                           # escalated 9 h ago, the oldest escalation
    assert "asks about an earlier application" in first.inner_text()
    # every escalation code the harness can raise shows up with its own label, never as a bare code
    text = leads.inner_text(".lh-list")
    for label in ["asked for a human", "visa / immigration specifics", "an attachment could not be read",
                  "two replies in a row broke a dialog rule", "a message type the harness does not process",
                  "pet policy in staff housing", "legal or contract question", "Reply overdue", "Send failed",
                  "inbound message(s) not processed", "send the anonymised profile to 5 clinics"]:
        assert label in text, label


def test_checks_are_shown_but_never_counted(leads):
    summary = leads.locator("details.lh-flags > summary")
    assert "for review only" in summary.inner_text().lower()
    assert summary.locator("b").inner_text() == "2"
    assert leads.locator("details.lh-flags .lh").count() == 2   # listed inside the collapsed group, not above it


def _chain(row):
    """One lead's card line: [what it shows, its state] per gate, in the harness's gate order."""
    return row.locator("ol.kc > li").evaluate_all(
        "els => els.map(li => [li.lastChild.textContent, li.getAttribute('aria-current') ? 'current' : li.className || 'open'])")


def test_every_lead_shows_what_we_know_in_conversation_order(leads):
    assert _chain(leads.locator(".lh", has_text="1180")) == [
        ["Bayern", "s"], ["Defizitbescheid", "s"], ["Nürnberg, Erlangen, Fürth", "s"], ["flat for 2 · or without", "s"],
        ["CV", "s"], ["Certificate", "s"], ["Consent", "s"]]
    board = leads.locator("tbody")
    assert _chain(board.locator("tr", has_text="2047")) == [
        ["Bayern", "s"], ["Urkunde", "s"], ["Landshut · Psychiatrie", "s"], ["no flat", "s"],
        ["CV", "s"], ["Certificate", "s"], ["consent asked", "current"]]
    assert _chain(board.locator("tr", has_text="4406"))[2:5] == [["Bamberg", "s"], ["flat yes, for how many?", "current"], ["CV", "open"]]
    assert _chain(board.locator("tr", has_text="8871"))[2] == ["Rosenheim · any department", "s"]
    assert _chain(board.locator("tr", has_text="5120"))[0] == ["Region", "current"]      # first contact, nothing known yet
    not_placeable = _chain(board.locator("tr", has_text="1942"))
    assert not_placeable[1] == ["not recognised", "b"]
    assert "current" not in [state for _, state in not_placeable]                    # an ended thread has no current step


def test_green_leads_run_from_the_furthest_along_down_to_first_contact(leads):
    stages = leads.locator("tbody tr").evaluate_all(
        "trs => trs.filter(tr => tr.querySelector('.lb.green')).map(tr => tr.querySelector('.stg').firstChild.textContent)")
    assert stages == ["Consent", "Documents", "CV", "Matching", "Matching", "Matching", "Qualification", "Qualification", "Contact"]


def test_the_green_rule_is_stated_and_the_board_lists_every_real_lead(leads):
    assert "LUNA ACTIVE (green) means" in leads.inner_text(".wa-rule")
    assert leads.locator("tbody tr").count() == 27
    leads.locator("label.toggle", has_text="Show test numbers").click()
    leads.wait_for_timeout(100)
    assert leads.locator("tbody tr").count() == 28
    assert leads.locator("tbody .lb.test").count() == 1
    assert leads.inner_text("h1").startswith(f"{NEEDS_HUMAN} ")    # showing the test number does not count it


def test_funnel_status_and_search_filter_the_board(leads):
    leads.locator(".wa-funnel button", has_text="Handed off").click()
    leads.wait_for_timeout(100)
    assert leads.locator("tbody tr").count() == 2
    assert "stage=submitted" in leads.url
    leads.locator(".wa-funnel button", has_text="Handed off").click()   # a second click clears it
    leads.locator(".seg .sg", has_text="Ended").click()
    leads.wait_for_timeout(100)
    assert leads.locator("tbody tr").count() == 5
    leads.locator(".seg .sg", has_text="All").click()
    leads.fill(".wa-fl input[type=search]", "8830")
    leads.wait_for_timeout(400)
    assert leads.locator("tbody tr").count() == 1


def test_no_raw_phone_number_anywhere(leads):
    leads.locator(".lh-list > .lh").first.click()
    leads.wait_for_selector("#ld-chat .bub")
    for sel in ["#view", "#lead-dlg"]:
        text = leads.inner_text(sel)
        assert not re.search(r"\+\d{1,3}(?!\d)\s+\d", text), sel   # a country code followed by digits, not bullets
        assert not re.search(r"\d{9,}", text), sel               # a bare canonical number (Meta error codes are 6)
    for masked in leads.locator("td a.pn").all_inner_texts():
        assert re.fullmatch(r"\+\d{2,3} [•\s]+\d{4}", masked), masked   # only the last 4 digits


def test_drawer_shows_card_documents_and_every_message_kind(leads):
    leads.locator(".lh", has_text="3318").click()
    leads.wait_for_selector("#ld-chat .bub")
    assert leads.evaluate("document.querySelector('#lead-dlg').open")
    assert "t=" in leads.url
    drawer = leads.inner_text("#lead-dlg")
    assert "asked for a human" in drawer and "Probezeit" in drawer          # the code plus the harness's own note
    assert "yes, 1 person" in drawer and "Lebenslauf" in drawer
    assert leads.locator("#ld-chat .bub.draft").count() == 1
    assert "WA_REPLY_SCOPE=test_only" in leads.inner_text("#ld-chat .bub.draft .cap")   # literal, not upper-cased
    assert leads.locator("#ld-chat .tomb").count() == 1                    # forgotten message: tombstone, no body
    assert leads.locator("#ld-chat .bub .btns span").count() == 2
    assert "voice note" in leads.inner_text("#ld-chat").lower()           # the caption is upper-cased by CSS
    before = leads.locator("#ld-chat .bub, #ld-chat .tomb").count()
    assert before == 30
    leads.locator("#ld-chat button", has_text="Load older messages").click()
    leads.wait_for_function("document.querySelectorAll('#ld-chat .bub, #ld-chat .tomb').length > 30")
    assert leads.locator("#ld-chat .bub, #ld-chat .tomb").count() == 32
    assert leads.locator("#ld-chat button", has_text="Load older messages").count() == 0


def test_keyboard_opens_and_escape_closes_the_drawer_and_drops_the_thread_from_the_url(leads):
    leads.locator(".lh-list > .lh").first.focus()
    leads.keyboard.press("Enter")
    leads.wait_for_selector("#ld-chat .bub")
    assert leads.evaluate("document.activeElement.classList.contains('x')")      # focus moved to the drawer's close button
    leads.keyboard.press("Escape")
    leads.wait_for_timeout(100)
    assert not leads.evaluate("document.querySelector('#lead-dlg').open")
    assert "t=" not in leads.url
    assert leads.evaluate("document.activeElement.classList.contains('lh')")     # and back to the row it came from


def test_the_open_thread_picks_up_a_new_message_by_polling(leads):
    # the mock has Luna answer the thread whose reply is owed ~8 s after it started; the drawer polls every 5 s
    leads.locator("tbody tr", has_text="7218").click()
    leads.wait_for_selector("#ld-chat .bub")
    before = leads.locator("#ld-chat .bub").count()
    leads.wait_for_function(f"document.querySelectorAll('#ld-chat .bub').length > {before}", timeout=20000)
    assert "In welcher Stadt in Bayern" in leads.locator("#ld-chat .bub").last.inner_text()


def test_leads_fits_a_phone(browser_and_base):
    browser, base = browser_and_base
    page = _open(browser, f"{base}/pro.html?mock=1#/leads", width=390)
    page.wait_for_selector(".lh-list")
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    page.locator(".lh-list > .lh").first.click()
    page.wait_for_selector("#ld-chat .bub")
    assert page.evaluate("document.querySelector('#lead-dlg').getBoundingClientRect().width") == 390
    assert not page.errors, page.errors[:2]
    page.context.close()


def _serve_api(page, wa):
    """No mock: answer the board's own reads, and hand /api/wa/* to `wa(route, path)`."""
    def handle(route):
        path = route.request.url.split("/api/", 1)[1].split("?")[0]
        if path.startswith("wa/"):
            return wa(route, path)
        body = {"me": {"role": "owner", "email": "owner@example.org", "via": "session"}}.get(path, {})
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    page.route("**/api/**", handle)


@pytest.mark.parametrize("status,expected", [(404, "not connected to the harness yet"), (502, "not answering (502)"),
                                             (403, "Owners only")])
def test_an_unreachable_api_is_an_error_never_zero_leads(browser_and_base, status, expected):
    browser, base = browser_and_base
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('lang','en')")
    page = ctx.new_page()
    _serve_api(page, lambda route, path: route.fulfill(status=status, content_type="application/json",
                                                       body=json.dumps({"detail": "upstream says no"})))
    page.goto(f"{base}/pro.html#/leads")
    page.wait_for_selector(".card[role=alert]")
    assert expected in page.inner_text("#view")
    assert page.locator(".lh-list, tbody").count() == 0
    assert page.locator("#nav a", has_text="Leads").locator(".bd.off").inner_text() == "!"
    ctx.close()


def test_a_quiet_board_says_nobody_is_waiting_and_walks_every_page(browser_and_base):
    browser, base = browser_and_base
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('lang','en')")
    page = ctx.new_page()
    green = lambda i: {"thread_id": f"t{i}", "phone_masked": f"+49 ••• ••• {1000 + i}", "is_test": False, "rail": "meta",
                       "opened_at": "2026-09-29T08:00:00+00:00", "last_inbound_at": "2026-09-29T09:00:00+00:00",
                       "last_outbound_at": "2026-09-29T09:01:00+00:00", "turns": 3, "ball": "them", "stage": "matching",
                       "gates": {"region": "satisfied", "qualification": "satisfied"}, "card": {}, "stopped": False,
                       "escalation_codes": [], "flag_codes": [], "stuck_reply": False}
    pages = {0: {"total": 3, "next_offset": 2, "rows": [green(1), green(2)]}, 2: {"total": 3, "next_offset": None, "rows": [green(3)]}}

    def wa(route, path):
        offset = int((route.request.url.split("offset=")[1].split("&")[0]) if "offset=" in route.request.url else 0)
        body = pages[offset] if path == "wa/threads" else {"webhook_ready": True, "outbound_ready": True, "reply_scope": "all", "autosend": True}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    _serve_api(page, wa)
    page.goto(f"{base}/pro.html#/leads")
    page.wait_for_selector(".lh-none")
    assert page.inner_text("h1") == "No lead needs a human right now"
    assert "All 3 running leads are with Luna" in page.inner_text(".lh-none")
    assert page.locator("tbody tr").count() == 3                          # the second page was fetched too
    assert page.locator("#nav a", has_text="Leads").locator(".bd").count() == 0
    assert not page.title().startswith("(")
    ctx.close()


def test_a_page_without_next_offset_breaks_the_contract_loudly(browser_and_base):
    browser, base = browser_and_base
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('lang','en')")
    page = ctx.new_page()

    def wa(route, path):
        body = {"total": 1, "rows": []} if path == "wa/threads" else {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    _serve_api(page, wa)
    page.goto(f"{base}/pro.html#/leads")
    page.wait_for_selector(".card[role=alert]")
    assert "breaks the contract" in page.inner_text("#view")
    ctx.close()
