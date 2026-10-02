"""The mirror layer itself (tests/mirror.py): record against a LOCAL http server, replay with the server gone.

Everything here talks to 127.0.0.1 only -- the conftest guard lets loopback through and nothing else. The server
is the stand-in for a clinic site; what is tested is that a replay is indistinguishable, to the code under test,
from the recording it was made from (status, headers, redirect hops, POST bodies, transport errors, repeated URLs),
and that a request the mirror does not hold fails loudly with the board, the URL and the command to re-record it.
"""
import gzip
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from tests import mirror as M

BOARD = "wp_jobs__site.test"


class _Site(BaseHTTPRequestHandler):
    seq = 0

    def log_message(self, *a):
        pass

    def _send(self, status, body=b"", headers=(), ctype="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if p.startswith("/page"):
            self._send(200, ("<html><body>Pflegefachkraft m/w/d " + p + "</body></html>").encode())
        elif p == "/redir":
            self._send(302, b"", [("Location", "/page?landed=1")])
        elif p == "/gz":
            self._send(200, gzip.compress(b"<html>gzipped body</html>"), [("Content-Encoding", "gzip")])
        elif p == "/cookies":
            self._send(200, b"ok", [("Set-Cookie", "a=1; Path=/"), ("Set-Cookie", "b=2; Path=/")])
        elif p == "/seq":
            type(self).seq += 1
            self._send(200, str(type(self).seq).encode())
        elif p.startswith("/missing"):
            self._send(404, b"nope")
        else:
            self._send(404, b"?")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(200, json.dumps({"echo": body.decode(), "n": len(body)}).encode(), ctype="application/json")


@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setenv("MIRROR_ROOT", str(tmp_path / "mirror"))
    _Site.seq = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    t = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _record(fn, meta=None):
    """Run fn(); returns (its result, board saved). The live server is whatever the caller's fixture started."""
    with M.recording(BOARD) as rec:
        out = fn()
    rec.save(meta or {})
    return out


# --------------------------------------------------------------------------------------------- requests
def test_a_replayed_page_is_the_recorded_page(site):
    live = _record(lambda: requests.get(site + "/page?x=1", timeout=5))
    with M.mirror_board(BOARD):
        got = requests.get(site + "/page?x=1", timeout=5)
    assert (got.status_code, got.text, got.headers["Content-Type"]) == (live.status_code, live.text, live.headers["Content-Type"])
    assert got.url == site + "/page?x=1"


def test_a_redirect_is_recorded_hop_by_hop_and_followed_by_requests_itself(site):
    _record(lambda: requests.get(site + "/redir", timeout=5))
    with M.mirror_board(BOARD) as m:
        got = requests.get(site + "/redir", timeout=5)
    assert [h.status_code for h in got.history] == [302]
    assert got.url == site + "/page?landed=1" and "landed=1" in got.text
    assert m.store.count() == 2


def test_the_body_of_a_post_is_part_of_the_key(site):
    def walk():
        s = requests.Session()
        return [s.post(site + "/p", data=b"page=1", timeout=5).json()["echo"],
                s.post(site + "/p", data=b"page=2", timeout=5).json()["echo"]]
    assert _record(walk) == ["page=1", "page=2"]
    with M.mirror_board(BOARD):
        s = requests.Session()
        assert s.post(site + "/p", data=b"page=2", timeout=5).json()["echo"] == "page=2"
        assert s.post(site + "/p", data=b"page=1", timeout=5).json()["echo"] == "page=1"
    with pytest.raises(M.MirrorMiss, match="POST .*/p with request body"):
        with M.mirror_board(BOARD):
            requests.Session().post(site + "/p", data=b"page=3", timeout=5)


def test_a_miss_names_the_board_the_request_and_the_command_to_record_it(site):
    _record(lambda: requests.get(site + "/page?x=1", timeout=5))
    with pytest.raises(M.MirrorMiss) as e:
        with M.mirror_board(BOARD):
            requests.get(site + "/page?x=2", timeout=5)
    msg = str(e.value)
    assert BOARD in msg and "GET " + site + "/page?x=2" in msg
    assert f"tools/mirror.py add {BOARD} " in msg and f"tools/mirror.py record {BOARD}" in msg
    assert "/page?x=1" in msg  # the nearest thing the mirror does hold


def test_a_miss_the_adapter_swallows_still_fails_the_run(site):
    """crawlers.vendor_adapters.get() turns every exception into None. The miss must not vanish with it."""
    _record(lambda: requests.get(site + "/page", timeout=5))
    with pytest.raises(M.MirrorMiss) as e:
        with M.mirror_board(BOARD):
            try:
                requests.get(site + "/not-recorded", timeout=5)
            except Exception:
                pass
    assert "/not-recorded" in str(e.value)


def test_repeated_requests_replay_the_recorded_sequence_and_then_its_last_answer(site):
    got = _record(lambda: [requests.get(site + "/seq", timeout=5).text for _ in range(3)])
    assert got == ["1", "2", "3"]
    with M.mirror_board(BOARD):
        assert [requests.get(site + "/seq", timeout=5).text for _ in range(5)] == ["1", "2", "3", "3", "3"]


def test_a_transport_failure_is_recorded_and_replayed_as_the_same_kind_of_exception(site):
    dead = "http://127.0.0.1:9/never"

    def walk():
        try:
            requests.get(dead, timeout=2)
        except requests.exceptions.RequestException as e:
            return type(e).__name__
    kind = _record(walk)
    assert kind == "ConnectionError"
    with M.mirror_board(BOARD):
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(dead, timeout=2)


def test_a_gzip_body_replays_decoded_without_its_transport_headers(site):
    _record(lambda: requests.get(site + "/gz", timeout=5))
    with M.mirror_board(BOARD):
        got = requests.get(site + "/gz", timeout=5)
    assert got.text == "<html>gzipped body</html>"
    assert "Content-Encoding" not in got.headers and got.headers["Content-Length"] == str(len(got.content))


def test_two_set_cookie_headers_both_survive(site):
    _record(lambda: requests.get(site + "/cookies", timeout=5))
    with M.mirror_board(BOARD):
        got = requests.get(site + "/cookies", timeout=5)
    assert got.raw.headers.getlist("Set-Cookie") == ["a=1; Path=/", "b=2; Path=/"]
    assert {c.name: c.value for c in got.cookies} == {"a": "1", "b": "2"}


def test_query_parameter_order_is_the_second_chance_match_only(site):
    _record(lambda: [requests.get(site + "/page?a=1&b=2", timeout=5), requests.get(site + "/page?b=2&a=1&c=3", timeout=5)])
    with M.mirror_board(BOARD):
        assert requests.get(site + "/page?b=2&a=1", timeout=5).url.endswith("?b=2&a=1")  # not recorded as written, same set
        assert requests.get(site + "/page?c=3&a=1&b=2", timeout=5).status_code == 200
    with pytest.raises(M.MirrorMiss):
        with M.mirror_board(BOARD):
            requests.get(site + "/page?a=1&b=3", timeout=5)


def test_a_session_token_and_a_clock_in_the_query_are_not_part_of_the_request(site):
    """The Regiomed P&I tenants load images as .../files?xsrf=<session>&key=<file>&ts=<ms>: new on every run, naming no content."""
    _record(lambda: requests.get(site + "/page?xsrf=AAA&key=7&ts=1790914134", timeout=5))
    with M.mirror_board(BOARD):
        assert requests.get(site + "/page?ts=1790999999&key=7&xsrf=BBB", timeout=5).status_code == 200
    with pytest.raises(M.MirrorMiss):  # the parameter that does name content still has to match
        with M.mirror_board(BOARD):
            requests.get(site + "/page?xsrf=AAA&key=8&ts=1790914134", timeout=5)


def test_the_fragment_is_not_part_of_the_request(site):
    _record(lambda: requests.get(site + "/page", timeout=5))
    with M.mirror_board(BOARD):
        assert requests.get(site + "/page#position,id=7", timeout=5).status_code == 200


def test_scopes_keep_their_own_sequences_and_fall_back_to_any_recorded_scope(site):
    def walk():
        with M.scope("adapter"):
            a = requests.get(site + "/seq", timeout=5).text
        with M.scope("oracle"):
            o = requests.get(site + "/seq", timeout=5).text
        return a, o
    assert _record(walk) == ("1", "2")
    with M.mirror_board(BOARD):
        with M.scope("oracle"):
            assert requests.get(site + "/seq", timeout=5).text == "2"
        with M.scope("adapter"):
            assert requests.get(site + "/seq", timeout=5).text == "1"
        with M.scope("roundtrip"):  # never recorded under this scope: the page is still in the mirror
            assert requests.get(site + "/seq", timeout=5).text in ("1", "2")


def test_a_replay_never_opens_a_socket(site, monkeypatch):
    _record(lambda: requests.get(site + "/page", timeout=5))
    import socket

    def no(*a, **k):
        raise AssertionError("replay opened a socket")
    monkeypatch.setattr(socket.socket, "connect", no)
    with M.mirror_board(BOARD):
        assert requests.get(site + "/page", timeout=5).status_code == 200


def test_sleeps_are_skipped_in_replay_and_kept_while_recording(site):
    def walk():
        t = time.monotonic()
        time.sleep(0.2)
        return time.monotonic() - t
    assert _record(walk) >= 0.2
    with M.mirror_board(BOARD):
        t = time.monotonic()
        time.sleep(5)
        assert time.monotonic() - t < 1
    t = time.monotonic()
    time.sleep(0.05)
    assert time.monotonic() - t >= 0.05  # outside any mirror time.sleep is the real one


# --------------------------------------------------------------------------------------------- urllib
def test_urllib_is_served_from_the_same_mirror_redirects_and_http_errors_included(site):
    def walk():
        a = urllib.request.urlopen(site + "/redir", timeout=5)
        out = (a.status, a.geturl(), a.read().decode())
        try:
            urllib.request.urlopen(site + "/missing", timeout=5)
        except urllib.error.HTTPError as e:
            return out, e.code, e.read()
    live = _record(walk)
    with M.mirror_board(BOARD):
        a = urllib.request.urlopen(site + "/redir", timeout=5)
        assert (a.status, a.geturl(), a.read().decode()) == live[0]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(site + "/missing", timeout=5)
        assert (e.value.code, e.value.read()) == live[1:]


def test_urllib_and_requests_do_not_answer_for_each_other(site):
    _record(lambda: requests.get(site + "/page", timeout=5))
    with pytest.raises(M.MirrorMiss) as e:
        with M.mirror_board(BOARD):
            urllib.request.urlopen(site + "/page", timeout=5)
    assert "urllib" in str(e.value)


def test_a_urllib_transport_failure_replays_as_urlerror(site):
    def walk():
        try:
            urllib.request.urlopen("http://127.0.0.1:9/never", timeout=2)
        except urllib.error.URLError as e:
            return type(e).__name__
    assert _record(walk) == "URLError"
    with M.mirror_board(BOARD):
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen("http://127.0.0.1:9/never", timeout=2)


# --------------------------------------------------------------------------------------------- store
def test_the_store_dedupes_bodies_compresses_the_file_and_keeps_the_previous_one(site, tmp_path):
    def walk():
        for q in range(30):
            requests.get(site + f"/page?{q}", timeout=5)
        requests.get(site + "/missing", timeout=5)
        requests.get(site + "/missing?again", timeout=5)  # same body as the line above
    _record(walk, {"note": "first"})
    f = M.board_file(BOARD)
    assert f.name == f"{BOARD}.sqlite.xz" and f.exists()
    with M.mirror_board(BOARD) as m:
        assert m.store.count() == 32
        assert m.store.blob_count() == 31  # the two 404 bodies are one blob
        assert m.store.meta("note") == "first"
    first_size = f.stat().st_size
    assert first_size < 4000
    _record(lambda: requests.get(site + "/page", timeout=5), {"note": "second"})
    prev = f.with_name(f.name + ".prev")
    assert prev.exists() and prev.stat().st_size == first_size
    assert not list(f.parent.glob("*.tmp"))
    with M.mirror_board(BOARD) as m:
        assert m.store.meta("note") == "second" and m.store.count() == 1


def test_replays_share_one_unpacked_store_until_the_board_is_re_recorded(site):
    _record(lambda: requests.get(site + "/page?v=1", timeout=5))
    with M.mirror_board(BOARD) as a:
        pass
    with M.mirror_board(BOARD) as b:
        assert b.store is a.store  # unpacking the biggest board takes ~0.5 s; a board's tests open it a handful of times
    _record(lambda: requests.get(site + "/page?v=2", timeout=5))
    with M.mirror_board(BOARD) as c:
        assert c.store is not a.store and [r.url.rsplit("?", 1)[1] for r in c.store.rows()] == ["v=2"]


def test_a_recording_refuses_our_own_infrastructure_and_paid_apis(site):
    """The recorder talks to clinic sites only: no DB read through a clinic board's recording, no Firecrawl, no LLM."""
    for url in ("https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/clinics", "https://api.firecrawl.dev/v1/scrape",
                "https://supabase.int.exe.xyz/rest/v1/clinics", "https://api.anthropic.com/v1/messages"):
        with M.recording(BOARD) as rec:
            with pytest.raises(RuntimeError, match="clinic sites only"):
                requests.get(url, timeout=5)
        assert rec.refused == [url] and rec.store.count() == 0  # nothing went out, nothing was stored


def test_a_board_that_was_never_recorded_fails_with_the_command_not_with_a_fallback(site):
    with pytest.raises(M.MirrorMiss) as e:
        with M.mirror_board("nope__nowhere.test"):
            pass
    assert "tools/mirror.py record nope__nowhere.test" in str(e.value)


def test_through_mode_records_only_what_the_mirror_lacks(site, monkeypatch):
    _record(lambda: requests.get(site + "/page?known", timeout=5))
    monkeypatch.setenv("MIRROR_RECORD", "1")
    with M.mirror_board(BOARD) as m:
        assert requests.get(site + "/page?known", timeout=5).status_code == 200  # served from the store
        assert requests.get(site + "/page?new", timeout=5).status_code == 200    # live, then stored
        assert m.live_requests == 1
    monkeypatch.delenv("MIRROR_RECORD")
    with M.mirror_board(BOARD):
        assert requests.get(site + "/page?new", timeout=5).status_code == 200


def test_index_entries_are_written_atomically_and_read_back(site):
    M.update_index({"board_id": "a__x.test", "url": "https://x.test/", "pages": 3})
    M.update_index({"board_id": "b__y.test", "url": "https://y.test/", "pages": 5})
    M.update_index({"board_id": "a__x.test", "url": "https://x.test/", "pages": 4})
    idx = M.read_index()
    assert {k: v["pages"] for k, v in idx["boards"].items()} == {"a__x.test": 4, "b__y.test": 5}
    assert not list(M.mirror_dir().glob("*.tmp"))
    assert M.read_index_or_none() is not None


def test_a_missing_index_is_an_error_naming_the_command(site):
    with pytest.raises(M.MirrorMiss) as e:
        M.read_index()
    assert "tools/mirror.py record --all" in str(e.value)


# --------------------------------------------------------------------------------------------- playwright
_APP = b"""<html><body><script>
fetch('/api', {method: 'POST', body: 'hello'}).then(r => r.text()).then(t => document.body.setAttribute('data-api', t));
fetch('/redir').then(r => r.text()).then(t => document.body.setAttribute('data-redir', t));
fetch('/matomo.php?r=' + Math.random(), {method: 'POST', body: String(Math.random())});
</script></body></html>"""
_DONE = "document.body.dataset.api && document.body.dataset.redir"


@pytest.fixture
def app_site(site, monkeypatch):
    orig_get = _Site.do_GET

    def do_get(self):
        if self.path == "/app":
            self._send(200, _APP)
        else:
            orig_get(self)

    def do_post(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(200, b"echo:" + body, ctype="text/plain")

    monkeypatch.setattr(_Site, "do_GET", do_get)
    monkeypatch.setattr(_Site, "do_POST", do_post)
    return site


def _browser(pw):
    try:
        return pw.chromium.launch(args=["--no-sandbox"])
    except Exception as exc:  # no browser binary in this env
        pytest.skip(f"chromium unavailable: {exc}")


def _browse(url):
    pw_api = pytest.importorskip("playwright.sync_api")
    with pw_api.sync_playwright() as pw:
        b = _browser(pw)
        pg = b.new_context().new_page()
        pg.goto(url, wait_until="networkidle")
        pg.wait_for_function(_DONE, timeout=5000)
        out = pg.evaluate("[document.body.dataset.api, document.body.dataset.redir]")
        b.close()
        return out


def test_playwright_requests_are_recorded_by_a_route_and_replayed_from_it(app_site):
    live = _record(lambda: _browse(app_site + "/app"))
    assert live == ["echo:hello", "<html><body>Pflegefachkraft m/w/d /page?landed=1</body></html>"]
    rows = M.Store.load(BOARD).rows()
    assert {r.via for r in rows} == {"playwright"}
    assert [r.status for r in rows if r.url.endswith("/redir")] == [302]  # the redirect is its own recorded hop
    assert not [r for r in rows if "matomo" in r.url]  # the analytics beacon (a new random URL and body every run) is answered 204, never stored
    with M.mirror_board(BOARD):
        assert _browse(app_site + "/app") == live


def test_a_navigation_redirect_goes_back_through_the_route_and_the_browser_ends_on_the_final_url(app_site):
    """Playwright routes only the first request of a redirect chain; the later hops would reach the network unrecorded."""
    def go():
        pw_api = pytest.importorskip("playwright.sync_api")
        with pw_api.sync_playwright() as pw:
            b = _browser(pw)
            pg = b.new_context().new_page()
            pg.goto(app_site + "/redir", wait_until="networkidle")
            out = (pg.url, pg.content())
            b.close()
            return out
    live = _record(go)
    assert live[0] == app_site + "/page?landed=1"
    rows = M.Store.load(BOARD).rows()
    assert [(r.status, r.url.rsplit("/", 1)[-1]) for r in rows if r.via == "playwright"] == [(302, "redir"), (200, "page?landed=1")]
    with M.mirror_board(BOARD):
        assert go() == live


def test_a_page_the_browser_asks_for_and_the_mirror_lacks_fails_the_run(app_site):
    _record(lambda: _browse(app_site + "/app"))
    with pytest.raises(M.MirrorMiss, match="via playwright"):
        with M.mirror_board(BOARD):
            pw_api = pytest.importorskip("playwright.sync_api")
            with pw_api.sync_playwright() as pw:
                b = _browser(pw)
                pg = b.new_context().new_page()
                try:
                    pg.goto(app_site + "/page?never-recorded", wait_until="networkidle")
                except Exception:
                    pass  # the route aborted it; the page cannot tell a miss from a dead host
                b.close()
