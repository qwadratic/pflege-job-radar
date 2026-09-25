"""The executor's HTTP surface: loopback only, bearer token (TASK-130, TASK-222, TASK-147).

    POST /v1/messages          one bubble, one deterministic key, one flock acquisition
    GET  /v1/outbox            the durable inbound handover -- the pull is the contract
    GET  /v1/health            polled by relay_pull.py's own slow-cadence alarm (TASK-255); the
                               3-minute conjunction-alert timer TASK-132 describes is not built
    POST /v1/reconcile         three-valued verdicts; only confirmed_absent authorises a resend
    GET  /v1/chats             the chat list, read-only
    GET  /v1/thread            the visible bubbles of one chat, read-only
                               (POST /v1/chats/clear and /v1/chats/delete used to live here too --
                               removed entirely, TASK-289, see bridge/operations.py's own docstring)
    POST /v1/broadcasts        queue a run; the runner sends it, paced by the governor
    GET  /v1/broadcasts        every run and its per-status counts
    GET  /v1/broadcasts/<id>   one run, with every item's status
    POST /v1/broadcasts/<id>/stop   the hard stop; takes effect between items
    GET  /v1/audit             the destruction record
    GET  /v1/unresolved        sends still ATTEMPTING/UNCONFIRMED: id, thread, state, age (TASK-261)
    GET  /v1/media/<id>        pulled inbound media's metadata: mime type, filename, size, a path
    GET  /v1/media/<id>/raw    the bytes themselves, at the path the metadata route just answered
    GET  /v1/media             the queue: unattached files, kind/size/age/folder/related threads
    POST /v1/media/attach      the human escape hatch: attach one queued file to a phone by id
                               (TASK-131 round 5 -- automatic attachment is gone, decision-9)
    GET  /v1/ops/<id>          one queued phone op's state/result/error (TASK-227)
    POST /v1/ops/<id>/resolve  mark a failed op reviewed and safe to delete (TASK-230)
    POST /v1/ops/<id>/cancel   the caller gave up: cancel it if still queued, never mid-run (TASK-243)

The handlers are thin on purpose and the rule is enforceable by reading them: every phone-touching
one of them parses, then enqueues onto the ops dispatcher (bridge/dispatcher.py) rather than
calling an Executor method inline -- see that module's own docstring for why. No decision about a
send is made in this file.

BOUND TO 127.0.0.1, ALWAYS. There is no bind-address setting, because the only reason to have one
would be to move this off loopback, and this process can message real people. Our VPS reaches it
through one VPS-initiated ssh -R leg; the connection therefore arrives from 127.0.0.1 on the mini,
and any peer that is not 127.0.0.1 is refused before the token is even compared. The machine also
runs a root-installed Cursor cloud worker under the same uid as us -- a port on 0.0.0.0 there is
not a mistake we get to make twice.

WHAT THIS VERSION DOES NOT DO, stated rather than stubbed: there is no batch route, and a paced
request is still refused loudly with 429 rail_parked and a next_slot_at (which our campaign
classifier turns into "failed, ownership restored") rather than accepted and queued for later --
accepting work we cannot pace would be the same lie as reporting an unverified send as sent. A
phone-touching route DOES now answer a 200 {"op_id", "state": "queued"} before TASK-227's queue
guarantees the operation runs at all (bridge/dispatcher.py's own docstring is explicit about why
that is not the same lie): every caller above the wire -- app/wa/bridge.py::Client, and everything
built on it -- polls that to a terminal state internally, so nothing above this file's own routes
ever sees "queued" and mistakes it for done.
"""
from __future__ import annotations

import dataclasses
import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import adb_driver as AD
from . import broadcast as B
from . import dispatcher as OD
from . import driver as D
from . import errors as E
from . import executor as X
from . import governor as G
from . import ledger as L
from . import operations as O
from . import retention as RT
from . import watcher as W

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8793          # plan section 5: the server-side base URL is http://127.0.0.1:8793
MAINTENANCE_INTERVAL_SEC = 3600.0
#: Mirrors app/wa/config.py's WA_LUNA_MEDIA_DIR (VPS side, same name): the literal directory THIS
#: machine receives clinic photos into over ssh/scp from
#: app/wa/luna/tools_server.py::_stage_on_mini, never through this executor's own routes
#: (TASK-272). Same default as tools_server.py's own fallback, so an unconfigured deploy sweeps
#: the same path it has always been scp'd into.
LUNA_MEDIA_DIR = os.environ.get("WA_LUNA_MEDIA_DIR", "").strip() or "/home/cursorworker1/wa_luna_media"
#: TASK-272: age past this is the whole rule for that directory -- a staged clinic photo carries
#: no op_id and never touches the ledger (see _sweep_luna_media below), so there is nothing for
#: retention.py's review-before-delete machinery to adjudicate. Same number as
#: driver.SCREENSHOT_RETENTION_DAYS's current value, kept as its own constant rather than a shared
#: import: the two directories carry unrelated evidence and a future change to one must not move
#: the other by accident. A first number, not a reviewed one -- same caveat SCREENSHOT_RETENTION_DAYS's
#: own comment states.
LUNA_MEDIA_RETENTION_DAYS = 14


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

    def _enqueue(self, kind, args, priority=L.PRIORITY_NORMAL):
        """-> (200, {"ok", "op_id", "state": "queued"}) for a phone-touching route (TASK-227): the
        route handler's whole job becomes naming which already-existing operations/executor
        method to run and with what arguments -- see bridge/dispatcher.py's own docstring for why
        this is not the bare-202 lie this file's own module docstring warns against.

        ``priority`` (TASK-296-adjacent, 2026-09-24) defaults NORMAL; each route below passes what
        it knows about its own urgency -- this is deliberately the one place that classification
        happens (bridge/dispatcher.py stays generic dispatch, on purpose, per its own docstring)."""
        op_id = self.server.dispatcher.enqueue(kind, args, budget_sec=self._op_budget_sec(),
                                               priority=priority)
        return 200, {"ok": True, "op_id": op_id, "state": "queued"}

    def _op_budget_sec(self):
        """-> the caller's own patience for the op about to be queued (TASK-243), from the header
        ``app/wa/bridge.py::Client._request`` sends with every call -- the exact number it is
        about to poll ``GET /v1/ops/<id>`` against. ``None`` for a caller that sends no header
        (an older client, a direct ``curl``): ``claim_next_op`` treats that row as unbounded, not
        as a reason to invent one here."""
        raw = self.headers.get("X-Wa-Op-Budget-Sec")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError as exc:
            raise E.invalid_request(f"X-Wa-Op-Budget-Sec must be a number, got {raw!r}") from exc

    def _op_status(self, op_id):
        """-> the op_status body for ``GET /v1/ops/<op_id>`` (TASK-227). Raises 404
        ``op_not_found`` for an id this ledger never enqueued -- there is no phone state to report
        on an id nobody minted."""
        row = self.server.executor.ledger.op_status(op_id)
        if row is None:
            raise E.op_not_found(f"no such op {op_id!r}")
        return {"ok": True, "op_id": row["op_id"], "kind": row["kind"], "state": row["state"],
               "result": row["result"], "error": row["error"], "created_at": row["created_at"],
               "started_at": row["started_at"], "finished_at": row["finished_at"]}

    def _cancel_op(self, op_id):
        """-> the body for ``POST /v1/ops/<id>/cancel`` (TASK-243): the caller (``_await_op``'s own
        timeout) gave up waiting. Flips a still-``queued`` row to a terminal cancellation via
        ``ledger.cancel_op``; a no-op, reported honestly as ``cancelled: False``, once the row is
        ``running`` or already terminal -- a live adb call is never interrupted from here. Raises
        404 for an id this ledger never enqueued, same as ``_op_status``/``_resolve_op``."""
        executor = self.server.executor
        if executor.ledger.op_status(op_id) is None:
            raise E.op_not_found(f"no such op {op_id!r}")
        cancelled = executor.ledger.cancel_op(op_id, executor.clock())
        return {"ok": True, "op_id": op_id, "cancelled": cancelled}

    def _resolve_op(self, op_id):
        """-> the body for ``POST /v1/ops/<id>/resolve`` (TASK-230): the human escape hatch for a
        failed op that minted no client_msg_id to auto-resolve against (read_thread,
        send_photos/gallery/document). Raises 404 for an id this ledger never enqueued, same as
        ``_op_status``. Never touches the phone -- a plain ledger write, so it answers directly
        rather than going through the dispatcher."""
        executor = self.server.executor
        if executor.ledger.op_status(op_id) is None:
            raise E.op_not_found(f"no such op {op_id!r}")
        executor.ledger.resolve_op(op_id, executor.clock())
        return {"ok": True, "op_id": op_id, "resolved": True}

    def _write(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _write_binary(self, status, blob, content_type):
        """TASK-131: the one route on this surface whose body is not JSON. Bearer-guarded and
        loopback-checked exactly like every other route (``_dispatch_binary`` below runs the same
        ``_guard()`` this one skips)."""
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

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

    def _dispatch_binary(self, route):
        """Same contract as ``_dispatch``, for a handler that returns ``(blob, mime_type)`` instead
        of a JSON-able payload. A refusal here still answers the ordinary JSON error envelope --
        only a 200 carries bytes."""
        try:
            self._guard()
            blob, mime_type = route()
        except E.BridgeRefusal as refusal:
            self._write(refusal.status_code, refusal.envelope())
        except Exception as exc:  # a bug in our executor, named as one
            self.server.log(f"executor_error: {type(exc).__name__}: {exc}")
            self._write(500, E.executor_error(f"{type(exc).__name__}: {exc}").envelope())
        else:
            self._write_binary(200, blob, mime_type)

    # --- routes ------------------------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        stop = re.match(r"/v1/broadcasts/([^/]+)/stop\Z", path)
        resolve_op = re.match(r"/v1/ops/([^/]+)/resolve\Z", path)
        cancel_op = re.match(r"/v1/ops/([^/]+)/cancel\Z", path)
        # TASK-227: every phone-touching route below enqueues onto the ops dispatcher instead of
        # calling the executor/operations method inline -- see bridge/dispatcher.py's own
        # docstring. The route's whole job is naming which method and what arguments; running it,
        # in what order, and recovering a dirty phone first (TASK-226) all happen on the
        # dispatcher's one thread now.
        if path == "/v1/messages":
            # TASK-296-adjacent: URGENT for a reply (app/wa/bridge.py's Client tags every send's
            # trace.action PACING_REPLY/PACING_FIRST_TOUCH, read here straight off the body this
            # route already has in hand) -- a candidate waiting on an answer outranks a cold
            # first-touch/campaign send. Missing/unknown action (an older or direct caller) gets
            # NORMAL, never guessed urgent. The body is read INSIDE the dispatched call, same as
            # every other route here, so an unauthenticated request never gets its body parsed
            # before ``_dispatch``'s own ``_guard()`` has a chance to refuse it.
            def _enqueue_send():
                body = self._body()
                action = (body.get("trace") or {}).get("action")
                priority = L.PRIORITY_URGENT if action == "reply" else L.PRIORITY_NORMAL
                return self._enqueue("send", {"req": body}, priority=priority)
            self._dispatch(_enqueue_send)
        elif path == "/v1/photos":
            # URGENT: there is no cold-outreach photo path on this rail, every send here answers
            # something a candidate is waiting on mid-conversation.
            self._dispatch(lambda: self._enqueue("send_photos", _photos_args(self._body()),
                                                 priority=L.PRIORITY_URGENT))
        elif path == "/v1/gallery":
            self._dispatch(lambda: self._enqueue("send_gallery", _gallery_args(self._body()),
                                                 priority=L.PRIORITY_URGENT))
        elif path == "/v1/document":
            self._dispatch(lambda: self._enqueue("send_document", _document_args(self._body()),
                                                 priority=L.PRIORITY_URGENT))
        elif path == "/v1/reconcile":
            # TASK-230: reconcile's own _scan touches the phone (opens the chat, reads bubbles,
            # parks) exactly like every other queued verb -- it was left calling straight through
            # when TASK-227 shipped (a known, documented gap) and is now load-bearing for
            # retention review, so it goes through the same queue as everything else.
            # LOW (TASK-296-adjacent): background sync nobody is waiting on -- the exact op kind
            # that starved real sends behind it on 2026-09-23/24 (see bump_reconcile_attempts/
            # escalate in bridge/ledger.py for the other half of that fix).
            self._dispatch(lambda: self._enqueue(
                "reconcile", {"client_msg_ids": self._body().get("client_msg_ids") or []},
                priority=L.PRIORITY_LOW))
        elif path == "/v1/broadcasts":
            self._dispatch(lambda: (200, self.server.broadcast.create(self._body())))
        elif path == "/v1/media/attach":
            self._dispatch(lambda: (200, self.server.executor.attach_media(**_media_attach_args(
                self._body()))))
        elif stop:
            self._dispatch(lambda: (200, self.server.broadcast.stop(unquote(stop.group(1)))))
        elif resolve_op:
            self._dispatch(lambda: (200, self._resolve_op(unquote(resolve_op.group(1)))))
        elif cancel_op:
            self._dispatch(lambda: (200, self._cancel_op(unquote(cancel_op.group(1)))))
        else:
            self._dispatch(lambda: _no_route(path))

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        one_run = re.match(r"/v1/broadcasts/([^/]+)\Z", parsed.path)
        one_op = re.match(r"/v1/ops/([^/]+)\Z", parsed.path)
        media_raw = re.match(r"/v1/media/([^/]+)/raw\Z", parsed.path)
        media_meta = re.match(r"/v1/media/([^/]+)\Z", parsed.path)
        if one_op:
            self._dispatch(lambda: (200, self._op_status(unquote(one_op.group(1)))))
        elif media_raw:
            self._dispatch_binary(lambda: self.server.executor.media_bytes(
                unquote(media_raw.group(1))))
        elif media_meta:
            self._dispatch(lambda: (200, self.server.executor.media_metadata(
                unquote(media_meta.group(1)))))
        elif parsed.path == "/v1/media":
            self._dispatch(lambda: (200, self.server.executor.unresolved_media()))
        elif parsed.path == "/v1/health":
            self._dispatch(lambda: (200, self.server.executor.health()))
        elif parsed.path == "/v1/outbox":
            self._dispatch(lambda: (200, self.server.executor.outbox(
                after=_int(query, "after", 0), limit=_int(query, "limit", None),
                ack=_int(query, "ack", None))))
        elif parsed.path == "/v1/chats":
            # TASK-269: this was calling operations.list_chats inline, the same TASK-227 leftover
            # TASK-230 already fixed for /v1/reconcile -- it could win huawei01.lock ahead of an
            # already-queued op regardless of arrival order, and never showed up as an op_id for a
            # caller to poll. Queued like every other phone-touching route.
            # LOW (TASK-296-adjacent): background listing -- the live reply path has not read the
            # phone screen at all since TASK-289; this is catchup/an operator's own `chats`.
            self._dispatch(lambda: self._enqueue("list_chats", {
                "include_archived": _flag(query, "include_archived", True)}, priority=L.PRIORITY_LOW))
        elif parsed.path == "/v1/thread":
            self._dispatch(lambda: self._enqueue("read_thread", {
                "phone": _str(query, "phone"), "chat": _str(query, "chat"),
                "include_text": _flag(query, "include_text", True),
                "archived": _flag(query, "archived", False)}, priority=L.PRIORITY_LOW))
        elif parsed.path == "/v1/broadcasts":
            self._dispatch(lambda: (200, self.server.broadcast.list_runs()))
        elif one_run:
            self._dispatch(lambda: (200, self.server.broadcast.view(unquote(one_run.group(1)))))
        elif parsed.path == "/v1/audit":
            self._dispatch(lambda: (200, {"ok": True, "rows": self.server.executor.ledger.audit_rows(
                limit=_int(query, "limit", None))}))
        elif parsed.path == "/v1/unresolved":
            self._dispatch(lambda: (200, self.server.executor.unresolved_sends()))
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


def _media_attach_args(body):
    """``POST /v1/media/attach``'s two fields, named explicitly (TASK-131): an unknown key silently
    ignored on a route that ties a document to a person is exactly the mistake this rail's other
    write routes already refuse."""
    allowed = ("queue_id", "phone")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise E.invalid_request(f"unknown field(s) {unknown}; this route takes {list(allowed)}")
    missing = [name for name in allowed if name not in body]
    if missing:
        raise E.invalid_request(f"missing field(s) {missing}; this route takes {list(allowed)}")
    return {name: body[name] for name in allowed}


def _photos_args(body):
    """``POST /v1/photos``'s two fields, named explicitly (TASK-131 round 7) -- same reason as
    ``_media_attach_args``: an unknown key silently ignored on a route that drives a real send is
    exactly the mistake this rail's other write routes already refuse. ``local_paths`` names files
    already on THIS machine (the mini) -- there is no upload endpoint here, an operator (or a
    caller with filesystem access, TASK-131 round 7's own scope) puts them there first."""
    allowed = ("phone", "local_paths")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise E.invalid_request(f"unknown field(s) {unknown}; this route takes {list(allowed)}")
    missing = [name for name in allowed if name not in body]
    if missing:
        raise E.invalid_request(f"missing field(s) {missing}; this route takes {list(allowed)}")
    if not isinstance(body["local_paths"], list) or not body["local_paths"]:
        raise E.invalid_request("local_paths must be a non-empty list of file paths")
    return {name: body[name] for name in allowed}


def _gallery_args(body):
    """``POST /v1/gallery``'s three fields, named explicitly -- same reason as ``_photos_args``,
    plus ``caption`` (optional: an empty/omitted caption sends the album with none)."""
    allowed = ("phone", "local_paths", "caption")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise E.invalid_request(f"unknown field(s) {unknown}; this route takes {list(allowed)}")
    missing = [name for name in ("phone", "local_paths") if name not in body]
    if missing:
        raise E.invalid_request(f"missing field(s) {missing}; this route takes {list(allowed)}")
    if not isinstance(body["local_paths"], list) or not body["local_paths"]:
        raise E.invalid_request("local_paths must be a non-empty list of file paths")
    out = {"phone": body["phone"], "local_paths": body["local_paths"]}
    if "caption" in body:
        out["caption"] = body["caption"]
    return out


def _document_args(body):
    """``POST /v1/document``'s three fields, named explicitly -- same reason as ``_gallery_args``,
    one file instead of a list (``local_path``, not ``local_paths``)."""
    allowed = ("phone", "local_path", "caption")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise E.invalid_request(f"unknown field(s) {unknown}; this route takes {list(allowed)}")
    missing = [name for name in ("phone", "local_path") if name not in body]
    if missing:
        raise E.invalid_request(f"missing field(s) {missing}; this route takes {list(allowed)}")
    out = {"phone": body["phone"], "local_path": body["local_path"]}
    if "caption" in body:
        out["caption"] = body["caption"]
    return out


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, executor, token, *, port=DEFAULT_PORT, log=print, operations=None,
                 broadcast=None, dispatcher=None):
        if not token:
            raise RuntimeError("WA_BRIDGE_TOKEN is empty: this process can message real people")
        super().__init__((LOOPBACK, port), Handler)
        self.executor = executor
        # All three are built from the executor and hold no state of their own -- the run store is
        # the ledger. They are arguments so a test can hand in the same instances it drives
        # directly. dispatcher is the one every phone-touching route now enqueues onto (TASK-227);
        # a caller that does not start it (most offline tests) gets a queue that fills and never
        # drains, which is deliberate -- a test that wants a real answer calls dispatcher.cycle()
        # itself, the same shape as bridge/watcher.py's watchers.
        self.operations = operations or O.Operations(executor)
        self.broadcast = broadcast or B.Broadcast(executor)
        self.dispatcher = dispatcher or OD.OpsDispatcher(executor.ledger, executor, self.operations)
        self.token = token
        self.log = log


def _sweep_luna_media(now):
    """Delete every file under LUNA_MEDIA_DIR older than LUNA_MEDIA_RETENTION_DAYS (TASK-272).
    Age-only, unlike review_and_sweep's screenshots/recordings below: a clinic photo staged here
    carries no op_id and never touches the ledger at all -- _stage_on_mini scp's it straight in
    from the VPS, bypassing this executor's dispatcher/ledger entirely -- so there is nothing for
    retention.py's HAPPY/AUTO-RESOLVED/MANUALLY-RESOLVED machinery to adjudicate; age past the
    cutoff is the whole rule. A directory that does not exist yet (nothing ever staged into it) is
    zero deleted, not an error."""
    media_dir = Path(LUNA_MEDIA_DIR)
    if not media_dir.is_dir():
        return {"deleted": 0}
    cutoff = now.timestamp() - LUNA_MEDIA_RETENTION_DAYS * 86400
    deleted = 0
    for path in media_dir.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            deleted += 1
    return {"deleted": deleted}


def _unlink_swept_media(paths):
    """Delete the bytes of a media_file row ledger.sweep() (TASK-278) just decided is safe to
    drop: its inbound row is gone, and no other media_seen row (attached-and-live, or still
    unattached) still names this media_id. Same tolerance as bridge/adb_driver.py::AdbDriver.
    delete_paths -- a path already gone (this pass crashed here last time) is not an error."""
    removed = 0
    for local_path in paths:
        try:
            Path(local_path).unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def maintenance_once(executor, now=None):
    """TASK-130 AC#9: the retention sweeps ship with the first commit, not later. TASK-230: the
    screenshot/recording sweep now reviews before it deletes (bridge/retention.py) -- an artefact
    past its 14-day cutoff is held, not removed, until it is either a happy op_done or a resolved
    issue. ``executor.last_retention`` is set here so /v1/health can show whether the held pile is
    growing without an operator having to read the journal by hand.

    TASK-253: the candidate listing this pass drives (adb_driver.py's list_screenshot_candidates/
    list_recording_candidates) globs then stats each path, and a file a human removed by hand on
    the mini between those two steps raises FileNotFoundError -- through review_and_sweep,
    unguarded, straight into whatever called this. Wrapped the same way every watcher in
    bridge/watcher.py wraps its own cycle: count it, journal it, return None instead of raising, so
    maintenance_loop's caller keeps its hourly schedule instead of losing the thread for good.

    TASK-272: _sweep_luna_media rides the same guarded pass and the same hourly cadence, for a
    directory review_and_sweep itself never walks (it is not phone_ops-shaped).

    TASK-278: ledger.sweep() decides which media_file rows (and their media_seen/media_link rows)
    are safe to drop -- their inbound row is gone, nothing still names the bytes -- and hands back
    the local_paths it already removed the row for; unlinking them is the one piece of filesystem
    work that decision still owes, same split as _sweep_luna_media's own raw unlink above."""
    now = now or executor.clock()
    started = executor.monotonic()
    try:
        swept = executor.ledger.sweep(now)
        swept["media_file"] = _unlink_swept_media(swept.pop("media_file_paths"))
        reviewed = RT.review_and_sweep(executor, now, screenshot_days=D.SCREENSHOT_RETENTION_DAYS,
                                       recording_days=D.SCREENSHOT_RETENTION_DAYS)
        reviewed["luna_media"] = _sweep_luna_media(now)
    except Exception as exc:  # a bug in our code or a TOCTOU race under it, named, never swallowed
        executor.retention_errors += 1
        executor.last_retention_error = f"{type(exc).__name__}: {exc}"
        executor.last_retention_error_at = L.utc(now)
        executor.ledger.note(now, "maintenance_error", None, error=executor.last_retention_error)
        return None
    executor.last_retention = {**reviewed, "at": L.utc(now),
                               "duration_sec": executor.monotonic() - started}
    executor.last_retention_ok_at = L.utc(now)
    executor.ledger.note(now, "maintenance", None, ledger=swept, **reviewed)
    return {"ledger": swept, **reviewed}


def maintenance_loop(executor, stop, interval=MAINTENANCE_INTERVAL_SEC):
    while not stop.wait(interval):
        maintenance_once(executor)


def stamped(msg):  # pragma: no cover - journald gets the line, not a test
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def active_hours_override(raw):
    """-> a widened ``(lo, hi)`` for ``bridge/governor.py::MINI_FLOOR.active_hours``, or ``None`` to
    leave the built-in 9-20 fuse untouched -- the caller's default when ``raw`` (from
    ``WA_BRIDGE_ACTIVE_HOURS_OVERRIDE``) is unset. TASK-131 UAT, Ivan 2026-09-22: 'расширь окно...
    это рассылка тестовая' -- one explicit, env-only, opt-in override for a single test night, never
    a change to the fuse's own default (governor.py's own docstring: 'the last thing between a bug
    and a real person'). Unset -> behaviour is bit-for-bit what it always was."""
    raw = (raw or "").strip()
    if not raw:
        return None
    lo_s, sep, hi_s = raw.partition("-")
    if not sep:
        raise RuntimeError(
            f"WA_BRIDGE_ACTIVE_HOURS_OVERRIDE={raw!r} must be 'LO-HI' hours, e.g. '9-23'")
    try:
        lo, hi = int(lo_s), int(hi_s)
    except ValueError as exc:
        raise RuntimeError(
            f"WA_BRIDGE_ACTIVE_HOURS_OVERRIDE={raw!r} must be 'LO-HI' hours, e.g. '9-23'") from exc
    if not (0 <= lo < hi <= 24):
        raise RuntimeError(
            f"WA_BRIDGE_ACTIVE_HOURS_OVERRIDE={raw!r} must satisfy 0 <= LO < HI <= 24")
    return (lo, hi)


def first_touch_gap_override(raw):
    """-> a narrowed ``(lo, hi)`` seconds range for ``bridge/governor.py::MINI_FLOOR.
    first_touch_gap_sec``, or ``None`` to leave the built-in 240-600s (4-10 min) floor untouched --
    the caller's default when ``raw`` (from ``WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC``) is unset.

    Same override shape and same reasoning as ``active_hours_override`` above (TASK-131 UAT
    precedent): one explicit, env-only, opt-in override for a single test night, never a change to
    the fuse's own default. The built-in floor exists to keep a burst of first-contact messages from
    reading as spam to WhatsApp on a consumer number that has no appeals path if it gets banned
    (docs/whatsapp.md) -- this override is for testing against known test numbers, not for widening
    the pacing that will ever run a real cold-outreach campaign. Ivan, 2026-09-23 UAT: the 4-10 min
    floor made two already-composed messages LOOK stuck when they were paced exactly as designed;
    'давай одну минуту задержку'. Unset -> behaviour is bit-for-bit what it always was."""
    raw = (raw or "").strip()
    if not raw:
        return None
    lo_s, sep, hi_s = raw.partition("-")
    if not sep:
        raise RuntimeError(
            f"WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC={raw!r} must be 'LO-HI' seconds, e.g. '60-90'")
    try:
        lo, hi = float(lo_s), float(hi_s)
    except ValueError as exc:
        raise RuntimeError(
            f"WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC={raw!r} must be 'LO-HI' seconds, e.g. '60-90'"
        ) from exc
    if not (0 < lo <= hi):
        raise RuntimeError(
            f"WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC={raw!r} must satisfy 0 < LO <= HI")
    return (lo, hi)


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
    driver = AD.AdbDriver(shots_dir=os.path.join(root, "shots"),
                         recordings_dir=os.path.join(root, "recordings"), log=stamped)
    # TASK-275: a previous process's debug recording, orphaned on /sdcard by a restart landing
    # between start_recording and stop_recording's finally -- self-heals here the same way
    # ledger's own _recover_stuck_ops does above, and for the same reason (one dispatcher thread
    # per process, so anything left over belongs to a process that no longer exists).
    driver.sweep_orphaned_recordings()
    hours = active_hours_override(os.environ.get("WA_BRIDGE_ACTIVE_HOURS_OVERRIDE"))
    gap = first_touch_gap_override(os.environ.get("WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC"))
    pacing = G.MINI_FLOOR
    if hours is not None:
        pacing = dataclasses.replace(pacing, active_hours=hours)
        stamped(f"WA_BRIDGE_ACTIVE_HOURS_OVERRIDE is set: active hours widened from the built-in "
               f"{G.MINI_FLOOR.active_hours} to {hours}. This is a fuse override for one test run -- "
               f"unset it in bridge.env and restart once the test is done.")
    if gap is not None:
        pacing = dataclasses.replace(pacing, first_touch_gap_sec=gap)
        stamped(f"WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC is set: first-touch pacing narrowed from "
               f"the built-in {G.MINI_FLOOR.first_touch_gap_sec} to {gap}. This is a fuse override "
               f"for one test run -- unset it in bridge.env and restart once the test is done.")
    # The operators' own handsets: no window, no caps, no gap, and their sends left out of the
    # budget every real recipient is paced against (bridge/governor.py's own docstring). Configured
    # HERE, on the machine that owns the phone -- never accepted from a request, or the fuse would be
    # something any caller could switch off. Empty by default: an unconfigured deploy is governed.
    ungoverned = [n.strip() for n in
                  os.environ.get("WA_BRIDGE_UNGOVERNED_NUMBERS", "").split(",") if n.strip()]
    if ungoverned:
        stamped(f"WA_BRIDGE_UNGOVERNED_NUMBERS is set: {len(ungoverned)} number(s) bypass the fuse "
                f"entirely -- no active-window check, no daily or hourly cap, no minimum gap, and "
                f"their sends do not count against anyone else's budget: {', '.join(ungoverned)}. "
                f"These must be handsets an operator holds. Remove any number that reaches a real "
                f"candidate and restart.")
    governor = G.Governor(ledger, per_number_daily_cap=int(cap), pacing=pacing,
                          ungoverned=ungoverned)
    executor = X.Executor(ledger=ledger, governor=governor, driver=driver,
                          rail_number=os.environ.get("WA_BRIDGE_RAIL_NUMBER") or None,
                          reconcile_attempt_limit=int(os.environ.get(
                              "WA_BRIDGE_RECONCILE_ATTEMPT_LIMIT",
                              X.DEFAULT_RECONCILE_ATTEMPT_LIMIT)))
    stop = threading.Event()
    threading.Thread(target=maintenance_loop, args=(executor, stop), daemon=True).start()
    watcher = W.InboundWatcher(
        executor, interval=float(os.environ.get("WA_BRIDGE_WATCH_INTERVAL_SEC",
                                                W.DEFAULT_INTERVAL_SEC)),
        log=stamped, operator_hold_path=os.path.join(root, "operator_hold")).start()
    # TASK-131: the handset's WhatsApp media folders -> pulled files -> linked to a message. Its
    # own thread, its own interval, and no flock -- see bridge/watcher.py::MediaWatcher.
    media_watcher = W.MediaWatcher(
        driver, ledger, os.path.join(root, "media"),
        interval=float(os.environ.get("WA_BRIDGE_MEDIA_INTERVAL_SEC",
                                      W.DEFAULT_MEDIA_INTERVAL_SEC)),
        log=stamped).start()
    executor.media_watcher = media_watcher
    # TASK-131 round 6: the one watcher that may open a chat, on its own schedule -- see
    # bridge/watcher.py::IdentityWatcher's own docstring.
    identity_watcher = W.IdentityWatcher(
        executor, interval=float(os.environ.get("WA_BRIDGE_IDENTITY_INTERVAL_SEC",
                                                W.DEFAULT_IDENTITY_INTERVAL_SEC)),
        log=stamped).start()
    # The broadcast runner sends only what a caller queued: with no run in the ledger it is a
    # thread that asks a question every few seconds and goes back to sleep (TASK-147).
    broadcast = B.Broadcast(executor)
    runner = B.BroadcastRunner(
        broadcast, interval=float(os.environ.get("WA_BRIDGE_BROADCAST_POLL_SEC",
                                                 B.DEFAULT_POLL_SEC)), log=stamped)
    executor.broadcast_runner = runner
    runner.start()
    # TASK-227: the one thread that ever calls a phone-touching executor/operations method now --
    # see bridge/dispatcher.py's own docstring for why this replaces the old bare-flock race.
    operations = O.Operations(executor)
    # TASK-234: the third door -- see bridge/watcher.py::ReconcileWatcher's own docstring.
    reconcile_watcher = W.ReconcileWatcher(
        operations, ledger, interval=float(os.environ.get("WA_BRIDGE_RECONCILE_INTERVAL_SEC",
                                                          W.DEFAULT_RECONCILE_INTERVAL_SEC)),
        log=stamped).start()
    executor.reconcile_watcher = reconcile_watcher
    # TASK-228 shipped this "первое время" (Ivan, 2026-09-22) as an off-by-default flag. TASK-230
    # (Ivan, 2026-09-23: "там на макмини вроде, достаточно места ... давай просто не больше 2
    # недель хранить это и все") made it safe to leave on by default -- review-before-delete
    # (bridge/retention.py) means the artefacts this produces get reviewed, not just aged out, so
    # the original caution ("не в полном разрешении хранить" -- keep it lean, which downscale/14d
    # already do) is covered. WA_BRIDGE_DEBUG_CAPTURE=0 still turns it back off if ever needed.
    debug_capture = os.environ.get("WA_BRIDGE_DEBUG_CAPTURE", "1").strip() not in ("0", "false", "no")
    ops_dispatcher = OD.OpsDispatcher(
        ledger, executor, operations,
        poll_interval=float(os.environ.get("WA_BRIDGE_OPS_POLL_SEC", OD.DEFAULT_POLL_SEC)),
        debug_capture=debug_capture, log=stamped).start()
    stamped(f"debug capture: {'on' if debug_capture else 'off'} -- pre/post/error screenshots + a "
           f"screen recording per queued op under WA_BRIDGE_STATE/{{shots,recordings}}, reviewed "
           f"before deletion (bridge/retention.py), 14 days. WA_BRIDGE_DEBUG_CAPTURE=0 to disable.")
    executor.ops_dispatcher = ops_dispatcher
    # TASK-235: nothing periodic ever called POST /v1/reconcile for a send left ATTEMPTING or
    # UNCONFIRMED -- see bridge/watcher.py::UnresolvedSendWatcher's own docstring. Needs
    # ops_dispatcher, so it is built after it, same as ReconcileWatcher needs operations.
    unresolved_send_watcher = W.UnresolvedSendWatcher(
        ledger, ops_dispatcher,
        interval=float(os.environ.get("WA_BRIDGE_UNRESOLVED_SEND_INTERVAL_SEC",
                                      W.DEFAULT_UNRESOLVED_SEND_INTERVAL_SEC)),
        log=stamped).start()
    executor.unresolved_send_watcher = unresolved_send_watcher
    server = BridgeServer(executor, token, port=int(os.environ.get("WA_BRIDGE_PORT", DEFAULT_PORT)),
                          log=stamped, operations=operations, broadcast=broadcast,
                          dispatcher=ops_dispatcher)
    server.log(f"bridge executor {X.VERSION} on {LOOPBACK}:{server.server_address[1]}, "
               f"driver={driver.describe()['kind']}, watcher every {watcher.interval:.0f}s, "
               f"media watcher every {media_watcher.interval:.0f}s, "
               f"identity watcher every {identity_watcher.interval:.0f}s, "
               f"reconcile watcher every {reconcile_watcher.interval:.0f}s, "
               f"unresolved send watcher every {unresolved_send_watcher.interval:.0f}s, "
               f"broadcast runner every {runner.interval:.0f}s, "
               f"ops dispatcher every {ops_dispatcher.poll_interval:.1f}s")
    try:
        server.serve_forever()
    finally:
        stop.set()
        watcher.stop()
        media_watcher.stop()
        identity_watcher.stop()
        reconcile_watcher.stop()
        unresolved_send_watcher.stop()
        runner.stop()
        ops_dispatcher.stop()
        server.server_close()
        ledger.close()


if __name__ == "__main__":  # pragma: no cover
    main()
