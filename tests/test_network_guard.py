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
    assert "network guard hits: 1" in out


def test_a_dns_lookup_fails_the_test_and_names_the_host(tmp_path):
    rc, out = _session(tmp_path, '''
import socket, urllib.request
def test_lookup():
    try:
        urllib.request.urlopen("https://supabase.int.exe.xyz/rest/v1/clinics", timeout=1)
    except Exception:
        pass
def test_gethostbyname():
    try:
        socket.gethostbyname("klkxfvieaxpjlplloljn.supabase.co")
    except OSError:
        pass
''')
    assert rc == 1 and "2 failed" in out
    assert "dns supabase.int.exe.xyz" in out and "dns klkxfvieaxpjlplloljn.supabase.co" in out


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


def test_chromium_cannot_resolve_anything_but_localhost(tmp_path):
    pytest.importorskip("playwright.sync_api")
    rc, out = _session(tmp_path, '''
import pytest
from playwright.sync_api import sync_playwright
def test_browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(args=["--no-sandbox"])
        except Exception as e:
            pytest.skip(f"chromium unavailable: {e}")
        pg = b.new_page()
        with pytest.raises(Exception, match="ERR_NAME_NOT_RESOLVED"):
            pg.goto("https://example.invalid/", timeout=10000)
        b.close()
''')
    assert rc == 0, out
