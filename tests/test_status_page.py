"""tools/status_page.py: a candidate's status page from one JSON object. The example is invented (tests/fixtures/status_page/)."""
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from tools import status_page as SP

DATA = json.loads((Path(__file__).parent / "fixtures" / "status_page" / "example.json").read_text(encoding="utf-8"))


def test_the_headline_counts_what_she_wants_to_know_first():
    page = SP.render(DATA)
    assert "<h1>Ihr Profil liegt bei 5 Kliniken. <em>2 haben geantwortet.</em></h1>" in page
    one = {**DATA, "sent": [c for c in DATA["sent"] if c["state"] in ("interview_scheduled", "sent_to_clinic")]}
    assert "<h1>Ihr Profil liegt bei 2 Kliniken. <em>1 hat geantwortet.</em></h1>" in SP.render(one)
    quiet = {**DATA, "sent": [c for c in DATA["sent"] if c["state"] == "sent_to_clinic"]}
    assert "<h1>Ihr Profil liegt bei <em>1 Klinik</em>.</h1>" in SP.render(quiet)
    none = SP.render({**DATA, "sent": []})
    assert "Wir haben es noch <em>nicht verschickt</em>." in none and "Noch bei keiner Klinik." in none


def test_clinics_that_moved_come_first_and_an_unknown_state_is_shown_as_it_is():
    page = SP.render(DATA)
    names = [c["name"] for c in DATA["sent"]]
    assert sorted(names, key=page.index) == ["Kreisklinik Beispielheim", "Universitätsklinikum Probedorf", "Klinikum Musterstadt",
                                              "St. Anna Krankenhaus Testingen", "Klinik am Musterberg"]
    assert '<span class="st s-good">Gespräch · 02.10.</span>' in page and '<span class="st s-bad">Absage · 27.09.</span>' in page
    assert '<span class="st ">on_hold · 25.09.</span>' in page
    assert page.count('class="card moved"') == 2


def test_every_text_is_escaped_and_nothing_is_fetched_but_the_fonts():
    evil = {**DATA, "wishes": [{"label": "<b>x", "text": '"><script>alert(1)</script>'}], "chat_url": 'javascript:"><img src=x>'}
    page = SP.render(evil)
    assert "<script" not in page and "<img" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Augenklinik &lt;Beispiel&gt; &amp; Partner" in page
    page = SP.render(DATA)
    assert "<script" not in page and "<img" not in page and "url(" not in page
    assert [x.split('"')[0] for x in page.split('href="')[1:]] == [
        "https://fonts.googleapis.com", "https://fonts.googleapis.com/css2?family=Archivo+Black&amp;family=JetBrains+Mono:wght@400;500&amp;display=swap",
        "https://wa.me/490000000000"]
    assert "Zum WhatsApp-Chat" not in SP.render({**DATA, "chat_url": None})


def test_the_states_are_the_harness_s_own_list():
    """Luna answers status questions from the same states; a state the page has no word for would be shown raw."""
    from typing import get_args

    from app.wa.pro_models import HandoffStatus
    assert set(SP.STATES) == set(get_args(HandoffStatus)) and set(SP.MOVED) < set(SP.STATES)


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
        page.route("https://fonts.g*/**", lambda r: r.abort())    # the fallback fonts are wider or equal: the stricter case, and no network
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        assert page.evaluate("document.documentElement.scrollWidth") == width
        side = page.evaluate("[...document.querySelectorAll('h1,h2,.card,.wishes,.profile')].map(x=>x.getBoundingClientRect()).map(r=>[r.left,innerWidth-r.right])")
        assert min(min(pair) for pair in side) >= 16
        assert page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)") >= 15
        assert page.locator(".card").count() == len(DATA["sent"]) + len(DATA["more"])
        browser.close()
    srv.shutdown()
