"""tools/status_page.py: a candidate's status page from one JSON object. The example is invented (tests/fixtures/status_page/)."""
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from tools import status_page as SP

DATA = json.loads((Path(__file__).parent / "fixtures" / "status_page" / "example.json").read_text(encoding="utf-8"))


def test_the_page_says_where_we_applied_and_that_the_next_step_comes_by_chat():
    page = SP.render(DATA)
    assert "<h1>Wir haben Ihr Profil an <em>5 Kliniken</em> geschickt.</h1>" in page
    assert "Jetzt warten wir auf die Kliniken. Sobald es einen nächsten Schritt gibt, schreiben wir Ihnen im WhatsApp-Chat." in page
    assert "<b>5</b><span>Kliniken haben Ihr Profil</span>" in page and "<b>3</b><span>weitere Kliniken passen</span>" in page
    names = [c["name"] for c in DATA["sent"]]
    assert sorted(names, key=page.index) == names                              # the order given, nothing is ranked
    assert page.count('<span class="st">Gesendet · ') == 5 and '<span class="st">Gesendet · 22.09.</span>' in page
    one = SP.render({**DATA, "sent": DATA["sent"][:1], "more": DATA["more"][:1]})
    assert "an <em>1 Klinik</em> geschickt" in one and "<span>Klinik hat Ihr Profil</span>" in one and "<span>weitere Klinik passt</span>" in one
    none = SP.render({**DATA, "sent": []})
    assert "Wir haben es noch <em>nicht verschickt</em>." in none and "Noch an keine Klinik." in none


def test_the_page_never_reports_how_a_clinic_answered():
    """Ivan, 2026-10-06: the page does not report who answered. A reply, an interview or a refusal in the data must not reach it."""
    loud = {**DATA, "sent": [{**c, "state": s, "state_at": "2026-10-02"} for c, s in zip(
        DATA["sent"], ("interview_scheduled", "clinic_replied", "declined", "offer", "closed"))]}
    assert SP.render(loud) == SP.render(DATA)
    page = SP.render(DATA)
    for word in ("geantwortet", "Absage", "Gespräch", "Angebot", "interview", "declined", "clinic_replied"):
        assert word not in page, word


def test_the_chat_button_goes_to_the_number_the_data_names_and_a_page_without_it_is_refused():
    page = SP.render({**DATA, "chat_url": "https://wa.me/4915100000001"})
    assert '<a href="https://wa.me/4915100000001">Zum WhatsApp-Chat</a>' in page
    for bad in (None, "", "http://wa.me/4915100000001", "https://example.org/x", 'https://wa.me/49151"><b>'):
        with pytest.raises(ValueError, match="chat_url"):
            SP.render({**DATA, "chat_url": bad})
    with pytest.raises(ValueError, match="chat_url"):
        SP.render({k: v for k, v in DATA.items() if k != "chat_url"})


def test_every_text_is_escaped_and_nothing_is_fetched_from_a_third_party():
    evil = {**DATA, "wishes": [{"label": "<b>x", "text": '"><script>alert(1)</script>'}]}
    page = SP.render(evil)
    assert "<script" not in page and "<img" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Augenklinik &lt;Beispiel&gt; &amp; Partner" in page
    page = SP.render(DATA)
    assert "<script" not in page and "<img" not in page and "<link" not in page
    assert [x.split(")")[0] for x in page.split("url(")[1:]] == ["/fonts/archivo-black.woff2", "/fonts/jetbrains-mono.woff2"]
    assert [x.split('"')[0] for x in page.split('href="')[1:]] == [DATA["chat_url"]]
    assert page.count("http") == 1                                             # the chat link; no third party is asked for anything


@pytest.mark.parametrize("width", [320, 390, 768, 1280])
def test_the_page_fits_a_phone_without_sideways_scrolling(tmp_path, width):
    sync_api = pytest.importorskip("playwright.sync_api")
    (tmp_path / "index.html").write_text(SP.render(DATA), encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": width, "height": 800})
        asked = []
        page.on("request", lambda r: asked.append(r.url))        # /fonts/ is not served here: the fallback fonts, the wider case
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        assert page.evaluate("document.documentElement.scrollWidth") == width
        side = page.evaluate("[...document.querySelectorAll('h1,h2,.card,.wishes,.profile')].map(x=>x.getBoundingClientRect()).map(r=>[r.left,innerWidth-r.right])")
        assert min(min(pair) for pair in side) >= 16
        assert page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)") >= 15
        assert page.locator(".card").count() == len(DATA["sent"]) + len(DATA["more"])
        assert [u for u in asked if "127.0.0.1" not in u] == []
        browser.close()
    srv.shutdown()
