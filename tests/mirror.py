"""Local mirror of the clinic boards -- tests read it, never a live site (TASK-197).

Ivan, 2026-10-01: tests must go to a local database of mirrors of the clinic sites, and every new case that shows up on
new pages is handled by re-recording the mirror and covering it with a new test.

One board = one file, data/mirror/<board_id>.sqlite.xz, plus data/mirror/INDEX.json (what the tests are parametrised
from, so collection opens no board). The directory is git-ignored: these are third-party pages with HR names, e-mails
and phone numbers, and the repo is public. Inside the file: an sqlite database (responses, blobs, meta) compressed as one
xz stream. The pages of one board are near-copies of each other, so solid xz beat a zlib blob per page by ~40x on the
first real board measured (AMEOS, 860 pages: 14.2 MB vs 0.36 MB) -- and the disk has 1.6 GB free.

    responses  one row per HTTP exchange, in the order it happened. A redirect chain is N rows (each hop is its own
               answer), a transport failure is a row with `exc` and no status. `scope` says which part of the board's
               test scenario made the request (adapter, oracle, roundtrip, mutation:<name>); `via` which client
               (requests, urllib, playwright) -- the two never answer for each other: a site may serve different bytes
               to a different client, and the mirror must not paper over that.
    blobs      response and request bodies by sha256 (a JS bundle shared by 30 pages is stored once). Bodies are DECODED
               for requests/playwright (Content-Encoding and Transfer-Encoding are transport, not content); urllib bodies
               are raw bytes, urllib never decodes.
    meta       JSON values: the board as the harness saw it (registry rows, towns), recorder git sha, rows returned...

Matching: client + method + URL (fragment dropped; exact string first, then the same URL with its query parameters
sorted) + sha256 of the request body (POST). The same request made N times replays the N recorded answers in order, then
repeats the last one; sequences are per scope, and a scope that never recorded a key falls back to any scope that did.
A request the mirror does not hold raises MirrorMiss (an AssertionError) AND is remembered, so a miss an adapter swallows
(crawlers.vendor_adapters.get() returns None on any Exception) fails the run when the `with` block ends. Nothing falls back
to the live site: recording is a different mode (`recording()`, tools/mirror.py), and `MIRROR_RECORD=1` makes mirror_board()
go live for exactly the requests the mirror lacks and store them -- by a person, on purpose.

Replay patches, while a mirror is active: requests.adapters.HTTPAdapter.send (Session, redirects, cookies, plain
requests.get all behave as in production, every hop served), urllib.request.AbstractHTTPHandler.do_open (redirect and
HTTPError handling stay urllib's own), Playwright's Browser.new_context (a context.route serves every request) and
time.sleep (replay is fast; the adapters sleep 0.3 s per request, Oracle took 8 minutes live; while recording the sleeps
stay, and are skipped only after a request the store answered).
"""
import contextlib
import fcntl
import hashlib
import http.client
import importlib
import io
import json
import lzma
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urldefrag, urlencode, urlparse, urlunparse

import requests

ROOT = Path(__file__).resolve().parent.parent
FORMAT = 1
RECORD = "tools/mirror.py"

_SCHEMA = """
CREATE TABLE responses(seq INTEGER PRIMARY KEY, scope TEXT NOT NULL, via TEXT NOT NULL, method TEXT NOT NULL,
  url TEXT NOT NULL, query_norm TEXT NOT NULL, req_body_sha256 TEXT, status INTEGER, reason TEXT, headers_json TEXT,
  content_type TEXT, body_sha256 TEXT, exc TEXT, fetched_at TEXT NOT NULL);
CREATE TABLE blobs(sha256 TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_COLS = "seq, scope, via, method, url, query_norm, req_body_sha256, status, reason, headers_json, body_sha256, exc, fetched_at"
Row = namedtuple("Row", "seq scope via method url norm req_sha status reason headers body_sha exc fetched_at")
# transport framing of the connection that carried the body, not part of the content
_FRAMING = {"content-encoding", "transfer-encoding", "content-length"}


class MirrorMiss(AssertionError):
    """The mirror does not hold a request (or a whole board). Never caught, never answered from the live site."""


def mirror_dir():
    return Path(os.environ.get("MIRROR_DIR") or ROOT / "data" / "mirror")


def board_file(board_id):
    return mirror_dir() / (board_id.replace("/", "_") + ".sqlite.xz")


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _body_bytes(body):
    if body is None:
        return b""
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    raise TypeError(f"the mirror cannot key a streamed request body ({type(body).__name__})")


def _norm_url(url):
    """Same request, query parameters in any order (the second-chance match)."""
    p = urlparse(urldefrag(url)[0])
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, p.params, urlencode(sorted(parse_qsl(p.query, keep_blank_values=True))), ""))


# ---------------------------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------------------------
class Store:
    """The sqlite database of one board, in memory. Loaded from / saved to <board_id>.sqlite.xz."""

    def __init__(self, board_id, db):
        self.board_id, self.db, self.lock, self._idx = board_id, db, threading.RLock(), None

    @classmethod
    def new(cls, board_id):
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.executescript(_SCHEMA)
        return cls(board_id, db)

    @classmethod
    def load(cls, board_id):
        path = board_file(board_id)
        if not path.exists():
            raise MirrorMiss(f"no mirror for board {board_id!r}: {path} does not exist.\n"
                             f"  record it:  .venv/bin/python {RECORD} record {board_id}")
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.deserialize(lzma.decompress(path.read_bytes()))
        return cls(board_id, db)

    # -- write
    def _blob(self, b):
        sha = _sha(b)
        self.db.execute("INSERT OR IGNORE INTO blobs(sha256, data) VALUES (?, ?)", (sha, b))
        return sha

    def add(self, scope, via, method, url, req_body, status, reason, headers, body, exc=None):
        url = urldefrag(url)[0]
        with self.lock:
            self.db.execute(
                "INSERT INTO responses(scope, via, method, url, query_norm, req_body_sha256, status, reason, headers_json,"
                " content_type, body_sha256, exc, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (scope, via, method.upper(), url, _norm_url(url), self._blob(req_body) if req_body else None, status, reason,
                 json.dumps(headers) if headers is not None else None,
                 next((v for k, v in headers or [] if k.lower() == "content-type"), None),
                 self._blob(body) if body is not None and exc is None else None,
                 json.dumps(exc) if exc else None, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            self._idx = None

    def set_meta(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def save(self, meta=None):
        """Replace <board_id>.sqlite.xz atomically: temp file, fsync, the old file kept as .prev, rename."""
        for k, v in (meta or {}).items():
            self.set_meta(k, v)
        self.set_meta("format", FORMAT)
        with self.lock:
            self.db.commit()  # serialize() leaves out what is not committed (seen on a loaded store, not on a new one)
            raw = self.db.serialize()
        path = board_file(self.board_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(lzma.compress(raw, preset=6))
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            link = path.with_name(path.name + ".prev.tmp")
            link.unlink(missing_ok=True)
            os.link(path, link)
            os.replace(link, path.with_name(path.name + ".prev"))
        os.replace(tmp, path)
        return path

    # -- read
    def meta(self, key, default=None):
        with self.lock:
            r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def body(self, sha):
        with self.lock:
            r = self.db.execute("SELECT data FROM blobs WHERE sha256=?", (sha,)).fetchone()
        return bytes(r[0])

    def rows(self):
        with self.lock:
            return [Row(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], json.loads(r[9]) if r[9] else None, r[10],
                        json.loads(r[11]) if r[11] else None, r[12])
                    for r in self.db.execute(f"SELECT {_COLS} FROM responses ORDER BY seq")]

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

    def blob_count(self):
        return self.db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]

    def stats(self):
        return {"pages": self.count(), "blobs": self.blob_count(), "n_exc": self.db.execute("SELECT COUNT(*) FROM responses WHERE exc IS NOT NULL").fetchone()[0],
                "bytes_raw": self.db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM blobs").fetchone()[0]}

    def lookup(self, scope, via, method, url, req_sha):
        """The recorded answers for one request, oldest first, or None. Exact URL before normalised URL; inside each,
        this scope's sequence before any other scope's."""
        with self.lock:
            if self._idx is None:
                ex, nm = {}, {}
                for r in self.rows():
                    ex.setdefault((r.via, r.method, r.req_sha, r.url), {}).setdefault(r.scope, []).append(r)
                    nm.setdefault((r.via, r.method, r.req_sha, r.norm), {}).setdefault(r.scope, []).append(r)
                self._idx = (ex, nm)
            u = urldefrag(url)[0]
            for tbl, k in ((self._idx[0], u), (self._idx[1], _norm_url(u))):
                by_scope = tbl.get((via, method.upper(), req_sha, k))
                if by_scope:
                    return (scope, k), by_scope.get(scope) or next(iter(by_scope.values()))
        return None

    def nearest(self, url, n=3):
        p = urlparse(url)
        rows = self.rows()
        same_path = [r.url for r in rows if urlparse(r.url).netloc == p.netloc and urlparse(r.url).path == p.path]
        same_host = [r.url for r in rows if urlparse(r.url).netloc == p.netloc]
        return list(dict.fromkeys(same_path or same_host))[:n]


# ---------------------------------------------------------------------------------------------
# a mirror in use: replay (tests), record (the tool), through (replay, live only for what is missing)
# ---------------------------------------------------------------------------------------------
class Mirror:
    def __init__(self, board_id, store, mode):
        self.board_id, self.store, self.mode = board_id, store, mode
        self.scope, self.misses, self.live_requests, self.last_live = "adapter", [], 0, True
        self._n, self._lock = {}, threading.Lock()

    def serve(self, via, method, url, body):
        """The recorded Row for this request; None when the caller must go live (record / through); MirrorMiss otherwise."""
        sha = _sha(body) if body else None
        if self.mode != "record":
            hit = self.store.lookup(self.scope, via, method, url, sha)
            if hit:
                (scope, k), rows = hit
                with self._lock:
                    i = self._n[(scope, via, method, sha, k)] = self._n.get((scope, via, method, sha, k), -1) + 1
                self.last_live = False
                return rows[min(i, len(rows) - 1)]
            if self.mode == "replay":
                self.miss(via, method, url, sha)
        with self._lock:
            self.live_requests += 1
        self.last_live = True
        return None

    def miss(self, via, method, url, sha):
        near = self.store.nearest(url)
        body = f" with request body sha256 {sha[:12]}" if sha else ""
        e = MirrorMiss(
            f"board {self.board_id!r} [scope {self.scope}, via {via}] has no recorded answer for\n"
            f"  {method.upper()} {urldefrag(url)[0]}{body}\n"
            + ("  nearest recorded on that host:\n" + "".join(f"    {u}\n" for u in near) if near else "  nothing is recorded for that host\n")
            + f"  the mirror is frozen on purpose and never answers from the live site. Record what is missing:\n"
              f"    one page:     .venv/bin/python {RECORD} add {self.board_id} {urldefrag(url)[0]!r}"
            + (" --method POST --data FILE" if method.upper() != "GET" else "") + "\n"
              f"    whole board:  .venv/bin/python {RECORD} record {self.board_id}")
        with self._lock:
            self.misses.append(e)
        raise e

    def raise_misses(self, cause=None):
        if not self.misses or isinstance(cause, MirrorMiss):
            return
        first = self.misses[0]
        raise MirrorMiss(f"{len(self.misses)} request(s) were not in the mirror (the code under test may have swallowed them); first:\n{first}") from cause

    def owes_sleep(self):
        return self.mode != "replay" and self.last_live

    def record(self, via, method, url, req_body, status, reason, headers, body, exc=None):
        self.store.add(self.scope, via, method, url, req_body, status, reason, headers, body, exc)

    def save(self, meta=None):
        return self.store.save(meta)


_STACK = []
_REAL = {}


def _top():
    return _STACK[-1] if _STACK else None


@contextlib.contextmanager
def scope(name):
    """Label the requests made inside the block (the harness names which part of a board's scenario it is running)."""
    m = _top()
    old = m.scope if m else None
    if m:
        m.scope = name
    try:
        yield
    finally:
        if m:
            m.scope = old


@contextlib.contextmanager
def _activate(m):
    _STACK.append(m)
    if len(_STACK) == 1:
        _install()
    try:
        yield m
    finally:
        _STACK.pop()
        if not _STACK:
            _uninstall()


@contextlib.contextmanager
def mirror_board(board_id, scope="adapter"):
    """Replay `board_id` for the block. Raises MirrorMiss at the end when anything inside asked for a page the mirror lacks."""
    through = os.environ.get("MIRROR_RECORD") == "1"
    store = Store.load(board_id) if (board_file(board_id).exists() or not through) else Store.new(board_id)
    m = Mirror(board_id, store, "through" if through else "replay")
    m.scope = scope
    try:
        with _activate(m):
            yield m
    except BaseException as e:
        m.raise_misses(e)
        raise
    else:
        m.raise_misses()
    finally:
        if through and m.live_requests:
            m.save()


@contextlib.contextmanager
def recording(board_id):
    """Live: every request goes out and is stored, hop by hop. The caller saves (`rec.save(meta)`). Only tools/mirror.py does this."""
    m = Mirror(board_id, Store.new(board_id), "record")
    with _activate(m):
        yield m


# ---------------------------------------------------------------------------------------------
# index: what the tests are parametrised from
# ---------------------------------------------------------------------------------------------
def index_path():
    return mirror_dir() / "INDEX.json"


def read_index_or_none():
    try:
        return json.loads(index_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def read_index():
    idx = read_index_or_none()
    if idx is None:
        raise MirrorMiss(f"no mirror index: {index_path()} does not exist.\n"
                         f"  record the boards:  .venv/bin/python {RECORD} record --all")
    return idx


def update_index(entry):
    d = mirror_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "INDEX.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        idx = read_index_or_none() or {"format": FORMAT, "boards": {}}
        idx["boards"][entry["board_id"]] = entry
        tmp = d / "INDEX.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, index_path())


def git_sha():
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "crawlers", "pflege_jobs", "app", "tests"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# seams
# ---------------------------------------------------------------------------------------------
class _Sock:
    """Just enough socket for http.client.HTTPResponse to parse bytes we already hold."""

    def __init__(self, data):
        self.data = data

    def makefile(self, *a, **k):
        return io.BytesIO(self.data)


def _httpclient_response(status, reason, headers, body):
    head = f"HTTP/1.1 {status} {reason or ''}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n"
    r = http.client.HTTPResponse(_Sock(head.encode("latin-1") + body))
    r.begin()
    return r


def _rebuild_exc(e, default, request=None):
    """The transport failure that was recorded, as the same kind of exception (the message is the recorded one)."""
    mod, _, name = e["type"].rpartition(".")
    cls = getattr(importlib.import_module(mod), name, None) if mod else None
    if not (isinstance(cls, type) and issubclass(cls, Exception)):
        cls = default
    try:
        return cls(e["msg"], request=request)
    except TypeError:
        return cls(e["msg"])


def _exc_row(e):
    return {"type": f"{type(e).__module__}.{type(e).__name__}", "msg": str(e)}


def _send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
    m = _top()
    if m is None:
        return _REAL["send"](self, request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
    body = _body_bytes(request.body)
    row = m.serve("requests", request.method, request.url, body)
    if row is None:
        try:
            resp = _REAL["send"](self, request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
        except Exception as e:
            m.record("requests", request.method, request.url, body, None, None, None, None, _exc_row(e))
            raise
        content = resp.content  # decoded; the caller's own .content reads the cached copy
        m.record("requests", request.method, request.url, body, resp.status_code, resp.reason, list(resp.raw.headers.items()), content)
        return resp
    if row.exc:
        raise _rebuild_exc(row.exc, requests.exceptions.ConnectionError, request)
    return _requests_response(self, request, row, m.store.body(row.body_sha))


def _requests_response(adapter, request, row, body):
    import urllib3
    headers = [(k, v) for k, v in row.headers if k.lower() not in _FRAMING] + [("Content-Length", str(len(body)))]
    raw = _httpclient_response(row.status, row.reason, headers, body)
    u3 = urllib3.response.HTTPResponse(body=raw, headers=urllib3.HTTPHeaderDict(raw.msg.items()), status=raw.status, version=raw.version,
                                       reason=raw.reason, preload_content=False, decode_content=False, original_response=raw,
                                       request_url=request.url)
    return adapter.build_response(request, u3)


def _do_open(self, http_class, req, **kw):
    m = _top()
    if m is None:
        return _REAL["do_open"](self, http_class, req, **kw)
    body = _body_bytes(req.data)
    row = m.serve("urllib", req.get_method(), req.full_url, body)
    if row is None:
        try:
            r = _REAL["do_open"](self, http_class, req, **kw)
            content = r.read()
        except Exception as e:
            m.record("urllib", req.get_method(), req.full_url, body, None, None, None, None, _exc_row(e))
            raise
        m.record("urllib", req.get_method(), req.full_url, body, r.status, r.reason, r.getheaders(), content)
        row, data = None, content
        status, reason, headers = r.status, r.reason, r.getheaders()
    else:
        if row.exc:
            raise _rebuild_exc(row.exc, urllib.error.URLError)
        data, status, reason, headers = m.store.body(row.body_sha), row.status, row.reason, row.headers
    headers = [(k, v) for k, v in headers if k.lower() not in ("transfer-encoding", "content-length")] + [("Content-Length", str(len(data)))]
    out = _httpclient_response(status, reason, headers, data)
    out.url = req.get_full_url()
    out.msg = out.reason  # what urllib's own do_open does: .msg carries the reason
    return out


def _sleep(s):
    m = _top()
    if m is None or m.owes_sleep():
        _REAL["sleep"](s)


def _install():
    _REAL["send"], _REAL["do_open"], _REAL["sleep"] = requests.adapters.HTTPAdapter.send, urllib.request.AbstractHTTPHandler.do_open, time.sleep
    requests.adapters.HTTPAdapter.send = _send
    urllib.request.AbstractHTTPHandler.do_open = _do_open
    time.sleep = _sleep


def _uninstall():
    requests.adapters.HTTPAdapter.send = _REAL["send"]
    urllib.request.AbstractHTTPHandler.do_open = _REAL["do_open"]
    time.sleep = _REAL["sleep"]
