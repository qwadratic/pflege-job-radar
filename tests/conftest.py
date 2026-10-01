"""Shared test defaults.

AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the existing API tests keep
calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test.

THE NETWORK GUARD (TASK-197): a test never talks to a clinic site, and never to our own infrastructure either. Any socket
connect or DNS lookup to a host that is not loopback is refused, recorded, and fails the test that made it -- even when the code
under test swallows the exception (crawlers.vendor_adapters.get() returns None on any Exception, and a swallowed refusal
would otherwise look like "the board answered nothing"). Hits during collection stop the session. Chromium is a separate
process the socket patch cannot see, so every Playwright launch gets a resolver that knows nothing but localhost.

  clinic sites          read the local mirror (tests/mirror.py; tools/mirror.py records it, on purpose, outside pytest)
  Supabase, ingest, Firecrawl, LLM APIs    fake them: a monkeypatched client or a local sqlite, never the real service

`MIRROR_RECORD=1` lifts the guard (and only that): a person re-recording what a mirror lacks, see tests/mirror.py.
"""
import os
import socket

import pytest

os.environ.setdefault("AUTH_DISABLED", "1")

_RECORDING = os.environ.get("MIRROR_RECORD") == "1"
_LOCAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", "", None}
_hits = []  # (kind, host, port) in the order they happened; a phase fails when its own slice is not empty
_real = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex, "getaddrinfo": socket.getaddrinfo,
         "gethostbyname": socket.gethostbyname, "gethostbyname_ex": socket.gethostbyname_ex}


def _local(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return host in _LOCAL or str(host).startswith("127.")


def _refuse(kind, host, port):
    _hits.append((kind, str(host), port))
    return host, port


def _connect(self, address):
    if isinstance(address, tuple) and address and not _local(address[0]):
        _refuse("connect", *address[:2])
        raise OSError(f"network guard: a test may not connect to {address[0]}:{address[1]}")
    return _real["connect"](self, address)


def _connect_ex(self, address):
    if isinstance(address, tuple) and address and not _local(address[0]):
        _refuse("connect", *address[:2])
        return 111  # ECONNREFUSED
    return _real["connect_ex"](self, address)


def _is_ip(host):
    import ipaddress
    try:
        ipaddress.ip_address(str(host).split("%")[0])
        return True
    except ValueError:
        return False


def _resolver(name):
    def resolve(host, *a, **k):
        # an IP literal resolves to itself, no DNS happens; the connect is what gets refused
        if not _local(host) and not _is_ip(host):
            _refuse("dns", host, a[0] if a else None)
            raise socket.gaierror(socket.EAI_NONAME, f"network guard: a test may not resolve {host}")
        return _real[name](host, *a, **k)
    resolve._network_guard = True
    return resolve


_connect._network_guard = _connect_ex._network_guard = True
if not _RECORDING:
    socket.socket.connect, socket.socket.connect_ex = _connect, _connect_ex
    socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex = (_resolver(n) for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"))
if not _RECORDING:
    try:  # a Chromium started by any test (or by a mirror replay) cannot resolve anything but localhost
        from playwright.sync_api import BrowserType

        _launch = BrowserType.launch

        def _guarded_launch(self, *a, **k):
            k["args"] = [*(k.get("args") or []), "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1 , EXCLUDE [::1]"]
            return _launch(self, *a, **k)

        BrowserType.launch = _guarded_launch
    except ImportError:
        pass

_ADVICE = ("a test never talks to a clinic site or to our own infrastructure. Clinic sites: replay the mirror "
           "(tests/mirror.py; record it with tools/mirror.py). Supabase, the ingest endpoint, Firecrawl, LLM APIs: fake them.")


def _failure(mark, where):
    hits = _hits[mark:]
    if not hits:
        return None
    what = ", ".join(dict.fromkeys(f"{k} {h}:{p}" if k == "connect" else f"dns {h}" for k, h, p in hits))
    return f"network guard: {where} reached {len(set(h for _, h, _ in hits))} non-local host(s): {what}\n  {_ADVICE}"


def _phase(item, name):
    mark = len(_hits)
    try:
        out = yield
    except BaseException as e:
        msg = _failure(mark, f"{item.nodeid} ({name})")
        if msg:
            raise AssertionError(msg) from e
        raise
    msg = _failure(mark, f"{item.nodeid} ({name})")
    if msg:
        raise AssertionError(msg)
    return out


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    return (yield from _phase(item, "setup"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from _phase(item, "call"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    return (yield from _phase(item, "teardown"))


def pytest_collection_finish(session):
    msg = _failure(0, "collection")
    if msg:
        pytest.exit(msg, returncode=2)


def pytest_report_header(config):
    return "network guard: " + ("OFF (MIRROR_RECORD=1)" if _RECORDING else "ON (a non-local socket or DNS lookup in a test is an error)")


def pytest_sessionfinish(session, exitstatus):
    if not _RECORDING:
        print(f"\nnetwork guard hits: {len(_hits)}")
