"""The executor's HTTP surface: loopback only, bearer token (TASK-130, TASK-119, TASK-147).

    POST /v1/messages          one bubble, one deterministic key, one flock acquisition
    GET  /v1/outbox            the durable inbound handover -- the pull is the contract
    GET  /v1/health            what the 3-minute timer alarms on (TASK-132)
    POST /v1/reconcile         three-valued verdicts; only confirmed_absent authorises a resend
    GET  /v1/chats             the chat list, read-only
    GET  /v1/thread            the visible bubbles of one chat, read-only
    POST /v1/chats/clear       empty a conversation, keep it        } confirm + identity + audit
    POST /v1/chats/delete      remove a conversation                } see bridge/operations.py
    POST /v1/broadcasts        queue a run; the runner sends it, paced by the governor
    GET  /v1/broadcasts        every run and its per-status counts
    GET  /v1/broadcasts/<id>   one run, with every item's status
    POST /v1/broadcasts/<id>/stop   the hard stop; takes effect between items
    GET  /v1/audit             the destruction record

The handlers are thin on purpose and the rule is enforceable by reading them: every one of them
parses, calls exactly one Executor method, and serialises the result. No decision about a send is
made in this file.

BOUND TO 127.0.0.1, ALWAYS. There is no bind-address setting, because the only reason to have one
would be to move this off loopback, and this process can message real people. Our VPS reaches it
through one VPS-initiated ssh -R leg; the connection therefore arrives from 127.0.0.1 on the mini,
and any peer that is not 127.0.0.1 is refused before the token is even compared. The machine also
runs a root-installed Cursor cloud worker under the same uid as us -- a port on 0.0.0.0 there is
not a mistake we get to make twice.

WHAT THIS VERSION DOES NOT DO, stated rather than stubbed: there is no 202 "queued" answer and no
batch route. The revision document's 202-first shape needs a scheduler and a second pacing
authority on this side; until that exists a paced request is refused loudly with 429 rail_parked
and a next_slot_at, which our campaign classifier already turns into "failed, ownership restored".
Accepting work we cannot pace would be the same lie as reporting an unverified send as sent.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import adb_driver as AD
from . import broadcast as B
from . import errors as E
from . import executor as X
from . import governor as G
from . import ledger as L
from . import operations as O
from . import watcher as W

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8793          # plan section 5: the server-side base URL is http://127.0.0.1:8793
MAINTENANCE_INTERVAL_SEC = 3600.0


class Handler(BaseHTTPRequestHandler):
    server_version = "pflege-wa-bridge/" + X.VERSION
    protocol_version = "HTTP/1.1"

    # --- plumbing ---------------------------------------------------------------------------------
    def log_message(self, fmt, *args):
        """Method and path only. A default access log would print query strings, and a query
        string on this box can carry a candidate's number."""
        self.server.log(f"{self.command} {urlparse(self.path).path} -> {args[1] if len(args) > 1 else ''}")

    def _guard(self):
        if self.client_address[0] != LOOPBACK:
            raise E.unauthorized(f"peer {self.client_address[0]} is not loopback")
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        token = header[len(prefix):] if header.startswith(prefix) else ""
        if not hmac.compare_digest(token, self.server.token):
            raise E.unauthorized("bad or missing bearer token")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as exc:
            raise E.invalid_request(f"body is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise E.invalid_request("body must be a JSON object")
        return payload

    def _write(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _dispatch(self, route):
        try:
            self._guard()
            status, payload = route()
        except E.BridgeRefusal as refusal:
            self._write(refusal.status_code, refusal.envelope())
        except Exception as exc:  # a bug in our executor, named as one
            self.server.log(f"executor_error: {type(exc).__name__}: {exc}")
            self._write(500, E.executor_error(f"{type(exc).__name__}: {exc}").envelope())
        else:
            self._write(status, payload)

    # --- routes ------------------------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        stop = re.match(r"/v1/broadcasts/([^/]+)/stop\Z", path)
        if path == "/v1/messages":
            self._dispatch(lambda: self.server.executor.send(self._body()))
        elif path == "/v1/reconcile":
            self._dispatch(lambda: (200, {"ok": True, "results": self.server.executor.reconcile(
                self._body().get("client_msg_ids") or [])}))
        elif path == "/v1/chats/clear":
            self._dispatch(lambda: (200, self.server.operations.clear_chat(**_chat_args(
                self._body(), extra=("include_starred",)))))
        elif path == "/v1/chats/delete":
            self._dispatch(lambda: (200, self.server.operations.delete_chat(**_chat_args(
                self._body()))))
        elif path == "/v1/broadcasts":
            self._dispatch(lambda: (200, self.server.broadcast.create(self._body())))
        elif stop:
            self._dispatch(lambda: (200, self.server.broadcast.stop(unquote(stop.group(1)))))
        else:
            self._dispatch(lambda: _no_route(path))

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        one_run = re.match(r"/v1/broadcasts/([^/]+)\Z", parsed.path)
        if parsed.path == "/v1/health":
            self._dispatch(lambda: (200, self.server.executor.health()))
        elif parsed.path == "/v1/outbox":
            self._dispatch(lambda: (200, self.server.executor.outbox(
                after=_int(query, "after", 0), limit=_int(query, "limit", None),
                ack=_int(query, "ack", None))))
        elif parsed.path == "/v1/chats":
            self._dispatch(lambda: (200, self.server.operations.list_chats(
                include_archived=_flag(query, "include_archived", True))))
        elif parsed.path == "/v1/thread":
            self._dispatch(lambda: (200, self.server.operations.read_thread(
                phone=_str(query, "phone"), chat=_str(query, "chat"),
                include_text=_flag(query, "include_text", True),
                archived=_flag(query, "archived", False))))
        elif parsed.path == "/v1/broadcasts":
            self._dispatch(lambda: (200, self.server.broadcast.list_runs()))
        elif one_run:
            self._dispatch(lambda: (200, self.server.broadcast.view(unquote(one_run.group(1)))))
        elif parsed.path == "/v1/audit":
            self._dispatch(lambda: (200, {"ok": True, "rows": self.server.executor.ledger.audit_rows(
                limit=_int(query, "limit", None))}))
        else:
            self._dispatch(lambda: _no_route(parsed.path))


def _no_route(path):
    raise E.invalid_request(f"no route {path}")


def _int(query, name, default):
    values = query.get(name)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError as exc:
        raise E.invalid_request(f"{name} must be an integer") from exc


def _str(query, name):
    values = query.get(name)
    return values[0] if values else None


def _flag(query, name, default):
    values = query.get(name)
    if not values:
        return default
    if values[0] not in ("0", "1", "true", "false"):
        raise E.invalid_request(f"{name} must be one of 0, 1, true, false")
    return values[0] in ("1", "true")


def _chat_args(body, *, extra=()):
    """The destructive routes' arguments, named explicitly rather than **body.

    Spreading a JSON object into a call that can delete a conversation is how an unknown key
    becomes a silently ignored one -- ``confirm_delete: true`` would sail past a ``confirm``
    parameter and the operation would refuse or, worse, not.
    """
    allowed = ("chat", "phone", "confirm", "expect_messages", "archived") + tuple(extra)
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise E.invalid_request(f"unknown field(s) {unknown}; this route takes {list(allowed)}")
    return {name: body[name] for name in allowed if name in body}


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, executor, token, *, port=DEFAULT_PORT, log=print, operations=None,
                 broadcast=None):
        if not token:
            raise RuntimeError("WA_BRIDGE_TOKEN is empty: this process can message real people")
        super().__init__((LOOPBACK, port), Handler)
        self.executor = executor
        # Both are built from the executor and hold no state of their own -- the run store is the
        # ledger. They are arguments so a test can hand in the same instances it drives directly.
        self.operations = operations or O.Operations(executor)
        self.broadcast = broadcast or B.Broadcast(executor)
        self.token = token
        self.log = log


def maintenance_once(executor, now=None):
    """TASK-130 AC#9: the retention sweeps ship with the first commit, not later."""
    now = now or executor.clock()
    swept = executor.ledger.sweep(now)
    shots = executor.driver.sweep_screenshots(now)
    executor.ledger.note(now, "maintenance", None, ledger=swept, screenshots=shots)
    return {"ledger": swept, "screenshots": shots}


def maintenance_loop(executor, stop, interval=MAINTENANCE_INTERVAL_SEC):
    while not stop.wait(interval):
        maintenance_once(executor)


def stamped(msg):  # pragma: no cover - journald gets the line, not a test
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def main():  # pragma: no cover - the entry point on the mini, not exercised offline
    token = os.environ.get("WA_BRIDGE_TOKEN", "")
    cap = os.environ.get("WA_BRIDGE_PER_NUMBER_DAILY_CAP")
    if not cap:
        raise RuntimeError(
            "WA_BRIDGE_PER_NUMBER_DAILY_CAP is unset. There is no per-recipient cap to inherit and "
            "this code will not invent one (CLAUDE.md). TASK-127 / Ivan names the number.")
    root = os.path.expanduser(os.environ.get("WA_BRIDGE_STATE", "~/.local/share/pflege-wa-bridge"))
    os.makedirs(root, exist_ok=True)
    ledger = L.Ledger(os.path.join(root, "ledger.sqlite"))
    driver = AD.AdbDriver(shots_dir=os.path.join(root, "shots"), log=stamped)
    governor = G.Governor(ledger, per_number_daily_cap=int(cap))
    executor = X.Executor(ledger=ledger, governor=governor, driver=driver,
                          rail_number=os.environ.get("WA_BRIDGE_RAIL_NUMBER") or None)
    stop = threading.Event()
    threading.Thread(target=maintenance_loop, args=(executor, stop), daemon=True).start()
    watcher = W.InboundWatcher(
        executor, interval=float(os.environ.get("WA_BRIDGE_WATCH_INTERVAL_SEC",
                                                W.DEFAULT_INTERVAL_SEC)),
        log=stamped).start()
    # The broadcast runner sends only what a caller queued: with no run in the ledger it is a
    # thread that asks a question every few seconds and goes back to sleep (TASK-147).
    broadcast = B.Broadcast(executor)
    runner = B.BroadcastRunner(
        broadcast, interval=float(os.environ.get("WA_BRIDGE_BROADCAST_POLL_SEC",
                                                 B.DEFAULT_POLL_SEC)), log=stamped)
    executor.broadcast_runner = runner
    runner.start()
    server = BridgeServer(executor, token, port=int(os.environ.get("WA_BRIDGE_PORT", DEFAULT_PORT)),
                          log=stamped, operations=O.Operations(executor), broadcast=broadcast)
    server.log(f"bridge executor {X.VERSION} on {LOOPBACK}:{server.server_address[1]}, "
               f"driver={driver.describe()['kind']}, watcher every {watcher.interval:.0f}s, "
               f"broadcast runner every {runner.interval:.0f}s")
    try:
        server.serve_forever()
    finally:
        stop.set()
        watcher.stop()
        runner.stop()
        server.server_close()
        ledger.close()


if __name__ == "__main__":  # pragma: no cover
    main()
