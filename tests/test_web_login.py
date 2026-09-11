"""The built sign-in page (web/login.html): posts to /api/auth/login, shows a 401 in the alert node,
follows ?next= on success, and raises the default-credentials banner from GET /api/me.

The API is mocked in the browser (page.route), so this needs no app and no network.
Skipped when Playwright or its Chromium build is not installed.
"""
import http.server, functools, json, threading, pathlib, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


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


def open_login(browser, base_url, query="", default_credentials=False, password="toor"):
    """A fresh page with /api/me and /api/auth/login mocked; returns (page, list of posted bodies)."""
    page = browser.new_page()
    posted = []
    page.route("**/api/me", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"role": "anonymous", "default_credentials": default_credentials})))

    def login(route, request):
        body = request.post_data_json
        posted.append(body)
        ok = body.get("user") == "root" and body.get("pass") == password
        route.fulfill(status=200 if ok else 401, content_type="application/json",
                      body=json.dumps({"ok": True, "default_credentials": default_credentials} if ok
                                      else {"status": 401, "detail": "owner only"}))

    page.route("**/api/auth/login", login)
    page.goto(f"{base_url}/login.html{query}")
    page.wait_for_selector("form#f")
    return page, posted


def test_wrong_password_renders_the_alert(browser, base_url):
    page, posted = open_login(browser, base_url)
    page.fill("#user", "root")
    page.fill("#pass", "nope")
    page.press("#pass", "Enter")                                  # Enter submits, no click needed
    page.wait_for_function("document.querySelector('#err').textContent.length > 0")
    assert posted == [{"user": "root", "pass": "nope"}]
    assert page.get_attribute("#err", "role") == "alert"
    assert "stimmt nicht" in page.inner_text("#err") or "Wrong" in page.inner_text("#err")
    assert page.url.endswith("/login.html")                       # still here
    assert not page.is_disabled("#go")                            # and can be retried
    page.close()


def test_success_follows_next(browser, base_url):
    page, posted = open_login(browser, base_url, query="?next=/deck")
    page.fill("#user", "root")
    page.fill("#pass", "toor")
    page.click("#go")
    page.wait_for_url("**/deck")
    assert posted == [{"user": "root", "pass": "toor"}]
    page.close()


def test_absolute_next_is_ignored(browser, base_url):
    """?next=https://evil.example would be an open redirect; only same-origin paths are followed."""
    page, _ = open_login(browser, base_url, query="?next=https://evil.example/x")
    assert page.evaluate("NEXT") == "/pro"
    page.close()


@pytest.mark.parametrize("raw", ["/\\evil.example/",          # passed the old /^\/(?!\/)/ test
                                 "/%5Cevil.example",          # same thing, get() decodes it
                                 "//evil.example/",
                                 "https://evil.example",
                                 "http:/\\/\\evil.example",
                                 # ... and these passed the version after it, which checked the input for a
                                 # leading "/" and no backslash and then trusted u.pathname: WHATWG
                                 # normalisation resolves "/..//host" to the *pathname* "//host", which is a
                                 # scheme-relative URL the moment it reaches location.href. Proven in
                                 # Chromium against the running app on 2026-09-10: a signed-in owner landed
                                 # on another origin. What is checked now is the resolved value.
                                 "/..//evil.example/x",
                                 "/%2e%2e//evil.example/x",
                                 "/./..//evil.example",
                                 "/..//..//evil.example",
                                 "///evil.example"])
def test_off_origin_next_is_ignored(browser, base_url, raw):
    """A leading backslash is a second slash to every browser: "/\\evil.example/" is one slash for a
    regex and another origin for location.href. NEXT has to fall back to /pro for all of these."""
    page, _ = open_login(browser, base_url, query="?next=" + raw)
    assert page.evaluate("NEXT") == "/pro"
    page.close()


@pytest.mark.parametrize("raw,want", [("/pro%23/clawl", "/pro#/clawl"),     # %23 -> the SPA route
                                      ("/deck", "/deck"),
                                      ("/pro?tab=x", "/pro?tab=x")])
def test_same_origin_next_is_followed(browser, base_url, raw, want):
    page, _ = open_login(browser, base_url, query="?next=" + raw)
    assert page.evaluate("NEXT") == want
    page.close()


def test_default_credentials_banner(browser, base_url):
    page, _ = open_login(browser, base_url, default_credentials=True)
    page.wait_for_selector("#warn:not([hidden])")
    assert "PUT /api/auth/password" in page.inner_text("#warn")
    page.close()
