"""The permanent network guard (tests/conftest.py): run tiny pytest sessions in a temp dir with a copy of that conftest and
look at what they report. 192.0.2.1 is TEST-NET-1 (RFC 5737, never routed) and *.invalid never resolves; the guard refuses
before a packet or a DNS query leaves, so none of these tests touches the network either."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONFTEST = Path(__file__).with_name("conftest.py")


def _session(tmp_path, body, *, env=None, extra=()):
    (tmp_path / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test_inner.py").write_text(body, encoding="utf-8")
    e = {k: v for k, v in os.environ.items() if k != "MIRROR_RECORD"}
    e.update(env or {})
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no", "--rootdir", str(tmp_path), *extra],
                       cwd=tmp_path, env=e, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def test_a_connect_to_a_non_local_host_fails_the_test_even_when_the_code_swallows_it(tmp_path):
    rc, out = _session(tmp_path, '''
import socket
def test_swallowed():
    try:
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    except OSError:
        pass          # what crawlers.vendor_adapters.get() does with every exception
''')
    assert rc == 1 and "1 failed" in out
    assert "network guard" in out and "192.0.2.1:80" in out and "test_inner.py::test_swallowed" in out
    assert "tests/mirror.py" in out and "tools/mirror.py" in out  # the failure says what to do instead
    assert "network guard hits: 1" in out


def test_a_dns_lookup_fails_the_test_and_names_the_host(tmp_path):
    rc, out = _session(tmp_path, '''
import socket, urllib.request
def test_lookup():
    try:
        urllib.request.urlopen("https://registry-read-proxy.invalid/rest/v1/clinics", timeout=1)
    except Exception:
        pass
def test_gethostbyname():
    try:
        socket.gethostbyname("project-rest.supabase.invalid")
    except OSError:
        pass
''')
    assert rc == 1 and "2 failed" in out
    assert "dns registry-read-proxy.invalid" in out and "dns project-rest.supabase.invalid" in out


def test_loopback_is_allowed(tmp_path):
    rc, out = _session(tmp_path, '''
import socket, threading
def test_local_server():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    t = threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True); t.start()
    socket.create_connection(("127.0.0.1", srv.getsockname()[1]), timeout=2).close()
    socket.getaddrinfo("localhost", 80)
    srv.close()
''')
    assert rc == 0 and "1 passed" in out and "network guard hits: 0" in out


def test_a_hit_in_a_module_scoped_fixture_fails_the_first_test_that_used_it(tmp_path):
    rc, out = _session(tmp_path, '''
import socket, pytest
@pytest.fixture(scope="module")
def shared():
    try:
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    except OSError:
        pass
def test_a(shared): pass
def test_b(shared): pass
''')
    assert rc == 1 and "network guard" in out and "test_a (setup)" in out
    assert "1 passed" in out or "test_b" not in out.split("network guard", 1)[1].splitlines()[0]


def test_a_hit_while_collecting_stops_the_session(tmp_path):
    rc, out = _session(tmp_path, '''
import socket
try:
    socket.getaddrinfo("registry.proxy.invalid", 443)
except OSError:
    pass
def test_never_runs(): pass
''')
    assert rc != 0 and "network guard: collection reached" in out and "registry.proxy.invalid" in out


def test_mirror_record_lifts_the_guard_and_says_so(tmp_path):
    rc, out = _session(tmp_path, '''
import socket
def test_unpatched():
    assert not getattr(socket.getaddrinfo, "_network_guard", False)
    assert not getattr(socket.socket.connect, "_network_guard", False)
''', env={"MIRROR_RECORD": "1"}, extra=("-v",))
    assert rc == 0 and "network guard: OFF (MIRROR_RECORD=1)" in out


def test_the_guard_is_on_by_default_and_says_so(tmp_path):
    rc, out = _session(tmp_path, "def test_x(): pass\n", extra=("-v",))
    assert rc == 0 and "network guard: ON" in out


def _hosts_file_name():
    """A name /etc/hosts resolves without any network (the VM's own), other than localhost -- Chromium resolves it through the
    system resolver, so only the guard's resolver rule can make it fail. (A *.invalid name would fail either way.)"""
    for line in Path("/etc/hosts").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            names = [n for n in line.split()[1:] if n != "localhost"]
            if names:
                return names[0]


def test_chromium_still_loads_a_page_from_loopback(tmp_path):
    """The tests of web/ (test_web_*.py) drive Chromium against a local server at http://127.0.0.1:<port>: the resolver rule that blocks every
    other name must leave loopback alone (found by running the suite: it first blocked 127.0.0.1 too and 170 web tests failed)."""
    pytest.importorskip("playwright.sync_api")
    rc, out = _session(tmp_path, '''
import http.server, threading
import pytest
from playwright.sync_api import sync_playwright

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b"<p id=x>loopback ok</p>")
    def log_message(self, *a): pass

def test_browser():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(args=["--no-sandbox"])
        except Exception as e:
            pytest.skip(f"chromium unavailable: {e}")
        pg = b.new_page()
        for host in ("127.0.0.1", "localhost"):
            pg.goto(f"http://{host}:{srv.server_address[1]}/", timeout=10000)
            assert pg.inner_text("#x") == "loopback ok"
        b.close()
    srv.shutdown()
''')
    assert rc == 0 and "network guard hits: 0" in out, out


def test_chromium_cannot_resolve_anything_but_localhost(tmp_path):
    pytest.importorskip("playwright.sync_api")
    name = _hosts_file_name()
    if not name:
        pytest.skip("/etc/hosts names nothing but localhost here: no locally resolvable name to prove the resolver rule with")
    rc, out = _session(tmp_path, f'''
import pytest
from playwright.sync_api import sync_playwright
def test_browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(args=["--no-sandbox"])
        except Exception as e:
            pytest.skip(f"chromium unavailable: {{e}}")
        pg = b.new_page()
        with pytest.raises(Exception, match="ERR_NAME_NOT_RESOLVED"):   # without the rule: ERR_CONNECTION_REFUSED (the name resolves)
            pg.goto("http://{name}:59999/", timeout=10000)
        b.close()
''')
    assert rc == 0, out
