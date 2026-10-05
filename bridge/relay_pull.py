"""The VPS side of the pull: drain the mini's inbound outbox into our webhook (TASK-372).

THIS IS THE ONE MODULE IN ``bridge/`` THAT RUNS ON OUR VPS, not on the handset machine. Everything
else in this package is the executor. It is here because it is the other half of the same contract
and the two have to be read together: the mini appends, this drains, and the cursor is the seam.

WHY PULL. An ingress tunnel into our VPS is not available -- the environment refuses it -- so
nothing on the mini may connect to us. Every leg is therefore opened FROM here: an ``ssh -L``
forward puts the executor's loopback port on our loopback, we fetch ``GET /v1/outbox?after=<cursor>``
over it, and we POST each item to our own webhook on 127.0.0.1. An ``ssh -R`` into this host would
be the other direction and is not available; this design needs neither.

THE CURSOR RULE, and it is the whole correctness argument: the cursor advances only after our server
has ACCEPTED the item. A crash between the fetch and the POST redelivers; a redelivery is free,
because ``wa_messages.wamid`` is UNIQUE and the webhook answers a repeat as ``duplicate``. Losing a
candidate's message is not free, which is why the order is fetch -> post -> persist -> ack and never
anything else.

WHAT A REFUSAL DOES. If our server answers 200 but handled nothing, the message did not enter a
conversation -- almost always a ``metadata.phone_number_id`` the server does not recognise, which
``api._number_matches`` skips silently. The relay stops and says so rather than advancing past it.
A stalled cursor with a loud log is recoverable; a cursor that walked past a candidate's question is
not.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import envelope as EV
from . import ledger as L

#: The executor's loopback port on the mini (bridge/server.py DEFAULT_PORT).
REMOTE_PORT = 8793
#: Where the ssh forward lands on this host. Loopback only, like everything else on this rail.
LOCAL_PORT = 18793
#: Seconds between drains. A candidate's reply must not wait minutes; this is how often we ask.
INTERVAL_SEC = 3.0
#: How long ssh may take to establish the forward before we call it down and rebuild it.
TUNNEL_READY_SEC = 20.0
#: What GET /v1/health may cost, derived rather than assumed: it is not a table read.
#: ``executor.health`` calls ``driver.describe()``, which runs ``dumpsys package`` through
#: ``AdbDriver.shell`` (that call's own ceiling is 40 s) and ``adb devices`` through ``connected()``
#: (30 s). The generic 30 s of ``http_json`` therefore expires on an executor that is alive and
#: merely waiting for a slow adb, and the probe reports it as unreachable -- the same mismatch
#: between a client's patience and the server's real work that lost a delete on 2026-09-21.
HEALTH_TIMEOUT_SEC = 75.0
#: How often Relay.run() asks executor.health() for the watcher's own heartbeat, on top of the
#: drain cadence above (TASK-255: last_ok_at/alive/inbound_backlog already existed and, outside a
#: hand-run --probe, nothing ever asked). Slower than INTERVAL_SEC on purpose: health() can cost up
#: to HEALTH_TIMEOUT_SEC when the phone is genuinely gone, so asking on every drain pass would make
#: the alarm itself a source of the stalls it exists to catch.
ALARM_CHECK_INTERVAL_SEC = 60.0
#: How stale InboundWatcher.heartbeat()'s last_ok_at may sit before this counts as dead rather than
#: quiet (TASK-255). The watcher polls every 5s (bridge/watcher.py::DEFAULT_INTERVAL_SEC); a live
#: one touches last_ok_at every cycle, so this is several missed cycles' worth of slack. A first
#: number so the alarm exists at all, not a reviewed one -- TASK-361 (still To Do) is where a
#: considered threshold and an actual alert channel belong.
WATCHER_STALE_SEC = 60.0
#: How old the oldest unacked inbound row may sit before this counts as delivery having stopped
#: rather than the ordinary gap to the next drain (INTERVAL_SEC above). Same caveat as
#: WATCHER_STALE_SEC: a first number, not a considered one.
INBOUND_BACKLOG_STALE_SEC = 60.0
#: Review finding 8 (MINOR): GET /v1/ops used to ride the generic 30s http_json timeout, same as a
#: real send -- but this route is a plain ledger read (bridge/server.py::_ops_list's own
#: docstring: "NEVER takes huawei01.lock and NEVER enqueues"), so a slow one should give up well
#: before it could delay the next drain pass by a noticeable amount.
OPS_TIMEOUT_SEC = 10.0


class RelayError(RuntimeError):
    """The relay stopped on purpose. The cursor did not move."""


class OpsRouteMissing(RelayError):
    """GET /v1/ops answered 404 -- an executor build that predates this route (TASK-283.7). Its own
    subclass, not a bare RelayError, so mirror_ops() below can tell "the route does not exist yet"
    apart from every other transport failure and skip the mirror pass quietly instead of treating it
    as the drain-stopping kind of error (it isn't: the drain itself never touches /v1/ops at all)."""
    code = "bridge_no_ops_route"


def _write_rail_sync(ok, error=None):
    """The real freshness heartbeat (review item 13, Ivan 2026-09-30): a successful drain_once() is
    the strongest available signal that "the harness has current phone state" -- it is the literal
    mechanism that pulls fresh inbound WhatsApp data into our webhook, stronger than
    app/wa/tunnel_watch.py's own TCP probe (which only proves the ssh port is open, not that
    anything was drained through it). Writes into the SAME wa.sqlite the harness itself reads: this
    module is the one file in ``bridge/`` that runs on our own VPS, in the harness's own checkout and
    venv (module docstring above) -- see deploy/wa-bridge/pflege-wa-bridge-relay.service and
    deploy/pflege-wa.service, both ``User=claude``, ``WorkingDirectory=/home/claude/repo/pflege-board``
    on the live host (checked 2026-09-30; the checked-in templates' ``exedev``/``/home/exedev/repo``
    are placeholders an install script rewrites at deploy time).

    Imported lazily, inside the function, so importing this module -- and every test of it -- never
    pays for pulling in app.wa.store/config unless a Relay is actually built with this as its
    sync_writer (only from_env's production Relay is, below; Relay()/FakeRelay() built directly, as
    every existing test does, default sync_writer to None and never call this at all)."""
    from app.wa import store as ST
    with ST.db() as c:
        if ok:
            ST.record_rail_sync_ok(c, ST.RAIL_SYNC_SOURCE)
        else:
            ST.record_rail_sync_error(c, ST.RAIL_SYNC_SOURCE, error or "unknown error")


# --- Pro activity rail view (TASK-283.7): the health snapshot whitelist ---------------------------
# What check_watcher_alarm() may hand to snapshot_writer, trimmed from the SAME executor.health()
# body it already fetches for its own watcher/inbound checks -- never a second health() call (see
# ALARM_CHECK_INTERVAL_SEC's own cost notes above: health() can cost up to HEALTH_TIMEOUT_SEC when
# the phone is genuinely gone, so this snapshot rides the existing 60s alarm cadence rather than
# adding one of its own).
#: Whole sub-objects passed through unchanged -- every one of these already went through this same
#: module's own PII review (check_watcher_alarm reads watcher/inbound directly off this body today)
#: or bridge/doctor.py's, bridge/broadcast.py's, bridge/executor.py's own heartbeat()/health() methods,
#: none of which ever include a phone number, a wamid, or message text.
_HEALTH_PASSTHROUGH_KEYS = ("watcher", "media_watcher", "identity_watcher", "reconcile_watcher",
                            "unresolved_send_watcher", "ops_dispatcher", "doctor", "phone_ops",
                            "quota", "inbound", "oldest_unresolved_sec", "reconcile", "broadcast",
                            "retention", "journal_recent")


def _strip_free_text_errors(value):
    """Recursively replace every ``last_error``/``error`` STRING with the boolean ``True`` (NIT-1,
    10-05 review) -- each one of these, everywhere it appears in the passthrough sub-objects below
    (``watcher``/`media_watcher`/`identity_watcher`/`reconcile_watcher`/`unresolved_send_watcher`/
    `ops_dispatcher`/`retention` directly, `broadcast.runner` one level deeper), is built from an
    uncaught exception's own f"{type(exc).__name__}: {exc}" (bridge/watcher.py, bridge/dispatcher.py,
    bridge/broadcast.py, bridge/executor.py's own retention heartbeat all follow that same pattern)
    -- free text that can and does carry a raw phone number or message body pulled into that
    exception's own message, the same class of leak review finding 2 already fixed for
    wa_job_runs/wa_ops_mirror. A boolean, not a dropped key: app/wa/pro_api.py's own
    ``_broadcasts_job_summary`` reads ``runner.get("last_error")`` as a plain truthy/falsy presence
    check (paired with ``last_error_at`` to tell "an error happened" from "which pass"), never the
    text itself -- dropping the key outright would silently stop that derivation from ever firing
    again. ``None`` (no error on this pass) passes through unchanged, same as every other value:
    codes, other booleans, counts (``errors`` is a plain int cycle-counter, never this string), and
    timestamps (``last_error_at`` stays -- staleness is still worth knowing)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("last_error", "error") and isinstance(v, str):
                out[k] = True
            else:
                out[k] = _strip_free_text_errors(v)
        return out
    if isinstance(value, list):
        return [_strip_free_text_errors(v) for v in value]
    return value


def _trim_health(body):
    """Whitelist ``body`` (Relay.health()'s raw GET /v1/health response) down to what may be
    persisted into wa_rail_snapshot.health_json. Drops ``ok``/``version``/``at`` (this snapshot
    stamps its own snapshot_at), ``rail.number``/``msisdn_verified``/``note`` (the rail's own phone
    number -- the one PII field this body carries, and one check_watcher_alarm() never reads
    either), and ``audit`` (a lifetime destruction counter with no Pro-facing use, and not named by
    the plan's whitelist). ``rail.driver`` is trimmed further still, to {connected, kind} --
    bridge/adb_driver.py's ``describe()`` also returns an adb serial, a WhatsApp version string and
    a sha256 of its own module file, none of it rail-health information. Every passthrough value
    also goes through ``_strip_free_text_errors`` (NIT-1): the sub-objects named below were only
    ever reviewed for a raw phone/wamid/message field, never for a free-text exception string."""
    driver = ((body or {}).get("rail") or {}).get("driver") or {}
    out = {"driver": {"connected": driver.get("connected"), "kind": driver.get("kind")}}
    for key in _HEALTH_PASSTHROUGH_KEYS:
        out[key] = _strip_free_text_errors((body or {}).get(key))
    return out


#: Closed (app/wa/pro_models.py's PhoneState mirrors this exactly) -- OUR OWN derivation, not the
#: bridge's, so nothing outside what _derive_phone_state actually returns can ever appear here.
PHONE_STATE_VALUES = ("ready", "recovering", "blocked", "disconnected", "unknown")


def _derive_phone_state(trimmed_health):
    """-> one of PHONE_STATE_VALUES, from the SAME trimmed snapshot wa_rail_snapshot stores (never
    re-derived differently by pro_api.py at read time): no single field in bridge/executor.py's
    health() answers "what state is the phone in", so this is this harness's own single derivation,
    stated once. Priority order, most urgent first:
      disconnected -- driver.connected is False
      blocked      -- the doctor sees a phone_ops row stuck running (doctor.blocked_by_stuck_op,
                       bridge/doctor.py::heartbeat)
      recovering   -- a dirty-state or idle-dirty recovery landed in the recent journal window
                       (journal_recent.dirty_state_recovered / idle_dirty_recovered,
                       bridge/executor.py's own HEALTH_JOURNAL_WINDOW_SEC)
      ready        -- none of the above, and driver.connected is True
    "unknown" only when driver.connected is itself missing (None) -- not merely falsy, since False
    is the clear "disconnected" signal above it."""
    driver = (trimmed_health or {}).get("driver") or {}
    connected = driver.get("connected")
    if connected is None:
        return "unknown"
    if connected is False:
        return "disconnected"
    doctor = (trimmed_health or {}).get("doctor") or {}
    if doctor and doctor.get("blocked_by_stuck_op"):
        return "blocked"
    journal = (trimmed_health or {}).get("journal_recent") or {}
    if journal and ((journal.get("dirty_state_recovered") or 0) > 0
                    or (journal.get("idle_dirty_recovered") or 0) > 0):
        return "recovering"
    return "ready"


def _write_rail_snapshot(*, ok, health=None, error=None, error_code=None, tunnel_up=None,
                         phone_state=None):
    """The production snapshot_writer (from_env only, same lazy-import convention as
    _write_rail_sync above)."""
    from app.wa import store as ST
    with ST.db() as c:
        ST.write_rail_snapshot(c, ok=ok, health=health, error=error, error_code=error_code,
                               tunnel_up=tunnel_up, phone_state=phone_state)


class _OpsMirror:
    """The production mirror_writer (from_env only): lazy-imported app.wa.store, same pattern as
    _write_rail_sync/_write_rail_snapshot above -- importing this module never pays for
    app.wa.store unless a Relay is actually built with a mirror_writer (only from_env's production
    Relay is; every direct Relay()/FakeRelay() construction in tests/test_bridge_relay.py defaults
    mirror_writer to None and never calls any of these three methods)."""

    def max_position(self):
        from app.wa import store as ST
        with ST.db() as c:
            return ST.ops_mirror_max_position(c)

    def open_ids(self):
        from app.wa import store as ST
        with ST.db() as c:
            return ST.ops_mirror_open_ids(c)

    def write(self, ops):
        from app.wa import store as ST
        with ST.db() as c:
            for op in ops:
                ST.upsert_mirrored_op(c, op)

    def reset(self):
        """Review finding 11 (NIT): the recovery for a detected ledger position reset (the
        bridge's own ``position`` counter has restarted below this mirror's stored max) -- wipes
        the mirror outright, so the next pass's ``after_position`` starts from 0 instead of
        staying wedged above every position the bridge is about to reuse."""
        from app.wa import store as ST
        with ST.db() as c:
            ST.clear_ops_mirror(c)


def _write_mirror_sync(ok, error_code=None):
    """The production mirror_sync_writer (from_env only, same lazy-import convention as
    _write_rail_sync/_write_rail_snapshot above): the ops mirror's own heartbeat (review finding 4,
    MAJOR), sharing wa_rail_sync's table under a second ``source`` key
    (app/wa/store.py::OPS_MIRROR_SYNC_SOURCE) rather than a dedicated column/table -- that table is
    already exactly this shape (an ok/at-or-error heartbeat, keyed by source)."""
    from app.wa import store as ST
    with ST.db() as c:
        if ok:
            ST.record_rail_sync_ok(c, ST.OPS_MIRROR_SYNC_SOURCE)
        else:
            ST.record_rail_sync_error(c, ST.OPS_MIRROR_SYNC_SOURCE, error_code or "unknown_error")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env(name, default=None, *, required=False):
    value = (os.environ.get(name) or "").strip()
    if not value and required:
        raise RelayError(f"{name} is unset -- the relay will not guess it")
    return value or default


#: Every variable ``from_env`` needs, and where it is supposed to come from. Named together so a
#: misconfigured host is one message and not four restarts (TASK-375): this unit reads three
#: EnvironmentFiles and the first version of it stopped at the first missing name, which on this
#: host meant finding out about WA_BRIDGE_TOKEN, then WA_BRIDGE_INBOUND_TOKEN, then
#: WA_BRIDGE_PHONE_NUMBER_ID one at a time.
REQUIRED_ENV = {
    "WA_BRIDGE_SSH_HOST": "relay.env -- the handset machine's ssh alias",
    "WA_BRIDGE_TOKEN": "rail.env -- must equal the executor's own, bridge.env on the mini",
    "WA_BRIDGE_INBOUND_TOKEN": "rail.env -- our webhook's own secret, never the one above",
    "WA_BRIDGE_PHONE_NUMBER_ID": "rail.env -- read by api._number_matches too, and must be equal",
}


def check_env():
    """Raise once, naming every missing variable and where it belongs. -> the names it checked."""
    missing = [f"{name} ({where})" for name, where in sorted(REQUIRED_ENV.items())
               if not (os.environ.get(name) or "").strip()]
    if missing:
        raise RelayError("the relay is not configured; nothing was drained. Missing:\n  - "
                         + "\n  - ".join(missing))
    return sorted(REQUIRED_ENV)


# --- the cursor -------------------------------------------------------------------------------
class Cursor:
    """One row, persisted. It is the only state the relay has, and it is worth a file of its own."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute("create table if not exists relay_cursor ("
                        "id integer primary key check(id=1), position integer not null, "
                        "updated_at text not null)")
        self.db.execute("create table if not exists relay_delivered ("
                        "inbound_id text primary key, outbox_id integer not null, "
                        "status text not null, delivered_at text not null)")
        self.db.commit()

    def position(self):
        row = self.db.execute("select position from relay_cursor where id=1").fetchone()
        return row[0] if row else 0

    def advance(self, outbox_id, inbound_id, status):
        self.db.execute("insert into relay_delivered(inbound_id, outbox_id, status, delivered_at) "
                        "values(?,?,?,?) on conflict(inbound_id) do update set "
                        "outbox_id=excluded.outbox_id, status=excluded.status, "
                        "delivered_at=excluded.delivered_at",
                        (inbound_id, int(outbox_id), status, utc_now()))
        self.db.execute("insert into relay_cursor(id, position, updated_at) values(1,?,?) "
                        "on conflict(id) do update set position=excluded.position, "
                        "updated_at=excluded.updated_at", (int(outbox_id), utc_now()))
        self.db.commit()
        return int(outbox_id)

    def close(self):
        self.db.close()


# --- the leg to the mini -------------------------------------------------------------------------
class SshForward:
    """An ``ssh -N -L`` child, supervised. Opened from here, which is the only direction available.

    Not ``ssh <host> curl ...`` per poll: that is a new TCP handshake, a new SSH handshake and a new
    remote process every few seconds over a Wi-Fi link with heavy retransmission. One long-lived
    forward pays that once and every later request is a loopback connect.
    """

    def __init__(self, host, *, remote_port=REMOTE_PORT, local_port=LOCAL_PORT, log=print):
        self.host = host
        self.remote_port = int(remote_port)
        self.local_port = int(local_port)
        self.log = log
        self.proc = None
        self.borrowed = False       # the forward is someone else's; we only use it
        self.opened_at = None

    def up(self):
        """Is the forward usable? A BORROWED one has no child of ours to poll -- the port is the
        whole answer for it.

        Without that first branch (TASK-375) a borrowed forward was never "up", so every drain went
        through ``close()`` -- which resets ``borrowed`` -- and re-announced "tunnel borrowed" on
        the next line. At a 3 s interval that is a line every 3 s forever, which is how a journal
        stops being readable at the moment someone needs it.
        """
        if self.borrowed:
            return self._port_open()
        return self.proc is not None and self.proc.poll() is None and self._port_open()

    def _port_open(self):
        with socket.socket() as probe:
            probe.settimeout(1.0)
            return probe.connect_ex(("127.0.0.1", self.local_port)) == 0

    def open(self):
        if self.up():
            return self
        self.close()
        if self._port_open():
            # A dedicated tunnel unit already owns this port, and the outbound rail
            # (WA_BRIDGE_URL) is pointed at it. Opening a second forward would just fail on
            # ExitOnForwardFailure; using the live one is the whole point of a shared port.
            if not self.borrowed:
                self.log(f"tunnel borrowed: 127.0.0.1:{self.local_port} is already forwarded")
            self.borrowed = True
            self.opened_at = self.opened_at or utc_now()
            return self
        self.borrowed = False
        self.proc = subprocess.Popen(
            ["ssh", "-N", "-T",
             "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
             "-o", "ConnectTimeout=15",
             "-L", f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}", self.host],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + TUNNEL_READY_SEC
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip()
                raise RelayError(f"ssh -L to {self.host} exited: {err or 'no message'}")
            if self._port_open():
                self.opened_at = utc_now()
                self.log(f"tunnel up: 127.0.0.1:{self.local_port} -> {self.host}:{self.remote_port}")
                return self
            time.sleep(0.3)
        self.close()
        raise RelayError(f"ssh -L to {self.host} did not come up in {TUNNEL_READY_SEC:.0f}s")

    def close(self):
        """Only ever kills a child WE started. A borrowed forward belongs to its own unit."""
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.borrowed = False

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.local_port}"


def http_json(method, url, *, headers=None, body=None, timeout=30.0):
    """-> (status, parsed body, elapsed seconds). Raises only on a transport failure."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    elapsed = time.monotonic() - started
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {"raw": (raw or b"").decode("utf-8", "replace")[:500]}
    return status, parsed, elapsed


# --- the relay ---------------------------------------------------------------------------------
class Relay:
    def __init__(self, *, host, token, webhook_url, inbound_token, cursor, phone_number_id,
                 display_phone_number, waba_id, remote_port=REMOTE_PORT, local_port=LOCAL_PORT,
                 log=print, fetch_limit=None, sync_writer=None, mirror_writer=None,
                 snapshot_writer=None, mirror_sync_writer=None):
        self.tunnel = SshForward(host, remote_port=remote_port, local_port=local_port, log=log)
        self.token = token
        self.webhook_url = webhook_url
        self.inbound_token = inbound_token
        self.cursor = cursor
        self.phone_number_id = phone_number_id
        self.display_phone_number = display_phone_number
        self.waba_id = waba_id
        self.fetch_limit = fetch_limit
        self.log = log
        self.delivered = 0
        self.duplicates = 0
        # Review item 13: opt-in only (None = no heartbeat at all), so every existing test that
        # builds a Relay/FakeRelay directly keeps writing nothing to any database -- only
        # from_env's production Relay (below) ever passes the real _write_rail_sync.
        self.sync_writer = sync_writer
        # TASK-283.7, same opt-in convention: mirror_writer (an _OpsMirror-shaped object: methods
        # max_position/open_ids/write) drives mirror_ops() below; snapshot_writer (a callable, same
        # shape as sync_writer) drives the extra side effect check_watcher_alarm() gets below. Both
        # default to None so every test in tests/test_bridge_relay.py that builds a Relay/FakeRelay
        # directly keeps touching no database for either, exactly like sync_writer.
        self.mirror_writer = mirror_writer
        self.snapshot_writer = snapshot_writer
        # TASK-283.7 review finding 4 (MAJOR): the mirror's own heartbeat, same opt-in convention.
        self.mirror_sync_writer = mirror_sync_writer
        # Review finding 4 (log only on an OpsRouteMissing state CHANGE, not every ~3s pass) and
        # finding 8 (route the mirror's own health check through the SAME throttle run() already
        # applies to its own periodic check, instead of bypassing it on every new op).
        self._ops_route_missing = False
        self._next_alarm_check = 0.0
        #: NIT-2, 10-05 review: any OTHER /v1/ops failure (RelayError, not OpsRouteMissing -- that
        #: one already had its own state-change gate above) used to log every single pass -- a
        #: persistent 500 gave one line per ~3s drain cycle, ~1200 lines/hour. None, or the last
        #: logged message (str(exc), not just the exception TYPE -- a 500 replaced by a 503 is new
        #: information worth a fresh line, even though both are plain RelayError instances).
        self._ops_last_error_message = None

    def _mark_synced(self, ok, error=None):
        """Call self.sync_writer, if any, and never let it take the drain loop down with it -- a
        heartbeat write failing (disk full, a locked db past its own busy_timeout) must not be
        confused with the drain itself failing."""
        if self.sync_writer is None:
            return
        try:
            self.sync_writer(ok, error)
        except Exception as exc:  # the heartbeat is a supplement to the drain loop, never load-bearing for it
            self.log(f"relay: could not record rail sync heartbeat: {type(exc).__name__}: {exc}")

    # --- the mini side --------------------------------------------------------------------------
    def executor(self, path, *, timeout=30.0):
        self.tunnel.open()
        return http_json("GET", self.tunnel.base_url + path,
                         headers={"Authorization": f"Bearer {self.token}"}, timeout=timeout)

    def health(self):
        status, body, elapsed = self.executor("/v1/health", timeout=HEALTH_TIMEOUT_SEC)
        if status != 200:
            raise RelayError(f"executor health is {status}: {body}")
        return body, elapsed

    def _write_snapshot(self, *, ok, health=None, error=None, error_code=None, phone_state=None):
        """Call self.snapshot_writer, if any, and never let it take the drain loop (or the alarm
        check it rides on) down with it -- same never-load-bearing discipline as _mark_synced
        above. ``tunnel_up`` is read fresh off self.tunnel here rather than inferred from ``ok``:
        the tunnel can be open while the executor itself answers with an error, and the inverse
        (tunnel down) is exactly the ``ok=False`` case -- two different facts, never conflated."""
        if self.snapshot_writer is None:
            return
        try:
            self.snapshot_writer(ok=ok, health=health, error=error, error_code=error_code,
                                 tunnel_up=self.tunnel.up(), phone_state=phone_state)
        except Exception as exc:  # the snapshot is a supplement, never load-bearing for the alarm
            self.log(f"relay: could not record rail snapshot: {type(exc).__name__}: {exc}")

    def _mark_mirrored(self, ok, error_code=None):
        """Call self.mirror_sync_writer, if any, and never let it take the drain loop down with it
        -- same never-load-bearing discipline as _mark_synced/_write_snapshot above (review
        finding 4, MAJOR)."""
        if self.mirror_sync_writer is None:
            return
        try:
            self.mirror_sync_writer(ok, error_code)
        except Exception as exc:  # the heartbeat is a supplement, never load-bearing for mirroring
            self.log(f"relay: could not record ops-mirror heartbeat: {type(exc).__name__}: {exc}")

    def _run_alarm_check_if_due(self):
        """check_watcher_alarm(), but only once self._next_alarm_check has actually passed (review
        finding 8, MINOR) -- the single gate run() and mirror_ops() both go through, so a burst of
        new ops cannot make mirror_ops() fire the (up to HEALTH_TIMEOUT_SEC-costly) health check
        more often than ALARM_CHECK_INTERVAL_SEC, the exact hammering that interval exists to
        prevent. ``_next_alarm_check`` starts at 0.0 (TASK-255: checked once immediately)."""
        now = time.monotonic()
        if now >= self._next_alarm_check:
            self.check_watcher_alarm()
            self._next_alarm_check = time.monotonic() + ALARM_CHECK_INTERVAL_SEC

    def check_watcher_alarm(self):
        """Ask health() for the watcher heartbeat and log loudly if it looks dead (TASK-255):
        ``last_ok_at`` stale, ``alive`` false, or the oldest unacked inbound row sitting past
        INBOUND_BACKLOG_STALE_SEC. -> the problems found (empty if none). Never raises past this --
        a bug or a transport failure here must not take down the drain loop it only supplements.

        TASK-283.7: this is also the ONE place wa_rail_snapshot gets written (opt-in,
        self.snapshot_writer) -- reusing this method's own health() call rather than adding a
        second one, per ALARM_CHECK_INTERVAL_SEC's own cost notes above (health() can cost up to
        HEALTH_TIMEOUT_SEC when the phone is genuinely gone; calling it more often than this method
        already does would make the snapshot itself a second source of the stalls the alarm exists
        to catch)."""
        try:
            body, _elapsed = self.health()
        except Exception as exc:  # our own bug or a transport failure, named, never swallowed
            self.log(f"ALARM: could not reach the executor for the watcher health check: "
                     f"{type(exc).__name__}: {exc}")
            self._write_snapshot(ok=False, error=str(exc), error_code=type(exc).__name__)
            return [f"health check failed: {exc}"]
        now = datetime.now(timezone.utc)
        watcher = body.get("watcher") or {}
        problems = []
        if watcher.get("alive") is False:
            problems.append("watcher.alive is false")
        last_ok_at = watcher.get("last_ok_at")
        if last_ok_at is None:
            problems.append("watcher.last_ok_at has never been set")
        else:
            stale = L.age_sec(last_ok_at, now)
            if stale > WATCHER_STALE_SEC:
                problems.append(f"watcher.last_ok_at is {stale:.0f}s old "
                                f"(over {WATCHER_STALE_SEC:.0f}s)")
        oldest_unacked_at = (body.get("inbound") or {}).get("oldest_unacked_at")
        if oldest_unacked_at is not None:
            backlog_age = L.age_sec(oldest_unacked_at, now)
            if backlog_age > INBOUND_BACKLOG_STALE_SEC:
                problems.append(f"oldest unacked inbound row is {backlog_age:.0f}s old "
                                f"(over {INBOUND_BACKLOG_STALE_SEC:.0f}s)")
        if problems:
            self.log("ALARM: watcher heartbeat looks dead -- " + "; ".join(problems))
        trimmed = _trim_health(body)
        self._write_snapshot(ok=True, health=trimmed, phone_state=_derive_phone_state(trimmed))
        return problems

    # --- Pro activity rail view (TASK-283.7): the ops mirror -------------------------------------
    def ops(self, *, after_position=None, limit=None, active=None, ids=None):
        """GET /v1/ops (bridge/server.py, the shared interface's own shape,
        ~/plans/2026-10-01-pro-activity-rail-view.md "Endpoints"). Raises OpsRouteMissing
        specifically on 404 (an executor build that predates this route) and plain RelayError on
        anything else but 200.

        Review finding 4 (MAJOR): an executor build that predates this route does not actually
        answer 404 for it -- bridge/server.py's own catch-all for an unmatched path (``_no_route``)
        raises ``invalid_request`` (400), the SAME refusal a genuinely malformed query from this
        module would get. NIT-3 (10-05 review) narrowed the fold to the EXACT message
        ``_no_route`` raises for this path ("no route /v1/ops") rather than any 400
        invalid_request -- a 400 with a different message is a real refusal (this module built a
        malformed query, or hit an actual bug), never the old-build fallback, and must surface as
        a RelayError that stops the drain loop over it, not get silently folded into "the route
        does not exist yet"."""
        query = []
        if after_position is not None:
            query.append(f"after_position={int(after_position)}")
        if limit is not None:
            query.append(f"limit={int(limit)}")
        if active:
            query.append("active=1")
        if ids:
            query.append("ids=" + ",".join(ids))
        path = "/v1/ops" + ("?" + "&".join(query) if query else "")
        status, body, _elapsed = self.executor(path, timeout=OPS_TIMEOUT_SEC)
        if status == 404:
            raise OpsRouteMissing(f"executor has no /v1/ops route (pre-283.7 build?): {body}")
        # NIT-3, 10-05 review: narrowed to the EXACT message bridge/server.py's own _no_route
        # raises for this path ("no route /v1/ops", invalid_request/400, no query string in it --
        # parsed.path never carries one) -- not every 400 invalid_request. A 400 with any other
        # message is a genuine refusal (a malformed query THIS module itself built, a real bug),
        # never an old-build fallback, and must not be swallowed into "the route doesn't exist yet".
        if (status == 400 and isinstance(body, dict)
                and (body.get("error") or {}).get("code") == "invalid_request"
                and (body.get("error") or {}).get("message") == "no route /v1/ops"):
            raise OpsRouteMissing(f"executor has no /v1/ops route (pre-283.7 build, 400 fallback): {body}")
        if status != 200:
            raise RelayError(f"executor ops is {status}: {body}")
        return body

    def mirror_ops(self):
        """One mirroring pass (TASK-283.7): page our own highest mirrored position forward through
        every GET /v1/ops page, then refresh every row the mirror still has open (active=1 AND an
        explicit ids= of our own open set) -- position paging alone would never see a row's own
        state change after the fact, since a bridge op's position is assigned once, at enqueue, and
        never moves (the shared interface). No-op when self.mirror_writer is None (same opt-in
        convention as sync_writer/snapshot_writer).

        Review finding 3 (MAJOR): the ``active`` fetch (every currently non-terminal op, no
        position filter at all) can return a row enqueued AFTER the position page above was
        fetched -- writing that row raises the mirror's own max_position past it, and the NEXT
        pass's ``after_position`` then starts above a DIFFERENT op that was enqueued in between and
        had already finished (quota refusal, idempotent replay) before THIS pass's own ``active``
        fetch ever looked: that op is skipped forever, and done/failed counts undercount from then
        on. Fixed by never writing an ``active`` row whose position is above this pass's own
        high-water mark (the highest position actually seen via position-paging, or ``after`` when
        nothing new paged in at all) -- such a row is simply left for the position-paging page that
        will reach it on a later pass, same as any other op not yet visible through ``active``.

        Review finding 11 (NIT): also checks for a ledger position reset (a ledger reset, or a
        retention sweep that outlived every row this mirror's own cursor was already past) --
        since positions only ever grow on a live bridge, the one false-positive-free signal
        available through this interface is every op_id this mirror still has open (self.
        mirror_writer.open_ids()) vanishing from the bridge AT ONCE: list_ops's own ``ids=`` branch
        returns whatever still exists for exactly those ids, state ignored, so losing every single
        one of them in one pass (never just one stale straggler retention swept on its own) means
        the whole phone_ops table underneath them is gone, not that they individually finished or
        were cleaned up. Detected, this pass wipes the mirror and returns early (the next pass
        pages from 0 again) instead of staying permanently blind, and logs it -- naturally only
        once, since open_ids is empty right after the wipe and this check is vacuously false then.
        (A reset with NOTHING open in this mirror at the moment it happens has no signal to catch
        here at all -- a known, narrow gap for this NIT-level fix, not a false-positive risk.)

        Also triggers an alarm/snapshot check when NEW ops appeared (the plan's own data-path note:
        the snapshot is "refreshed at the existing alarm cadence and also on every cycle in which
        the ops mirror changed"), through the SAME throttle run() applies to its own periodic check
        (review finding 8, MINOR: this used to call check_watcher_alarm() unconditionally on any
        new op, bypassing ALARM_CHECK_INTERVAL_SEC -- the exact every-cycle hammering that interval
        exists to prevent, once anything is enqueued often enough).

        Writes a heartbeat (self.mirror_sync_writer, review finding 4, MAJOR) on every pass that
        reaches a real outcome -- ok on a clean pass (even an empty one: "the mirror tried and
        found nothing new" is itself current information), ok=False with a code on a detected
        reset, an OpsRouteMissing or any other failure -- so a caller can tell a frozen mirror from
        a healthy, idle one (GET /api/wa/pro/activity's queue.as_of / GET /api/wa/pro/ops'
        mirrored_at).

        Tolerates OpsRouteMissing by logging ONLY ON A STATE CHANGE (review finding 4) -- this used
        to log every single pass, which at the 3s drain cadence is a line every 3s forever, exactly
        the kind of noise TASK-375's own tunnel-borrowed fix (see SshForward.open's docstring)
        already had to fix once for a different route. Tolerates any other RelayError/bug the same
        way check_watcher_alarm() does for its own health() call -- a mirroring failure must not
        take down the drain loop it only supplements."""
        if self.mirror_writer is None:
            return
        try:
            new_ops = []
            after = self.mirror_writer.max_position()
            high_water = after
            page = self.ops(after_position=after, limit=500)
            new_ops.extend(page.get("ops") or [])
            next_after = page.get("next_after_position")
            while next_after is not None:
                page = self.ops(after_position=next_after, limit=500)
                new_ops.extend(page.get("ops") or [])
                next_after = page.get("next_after_position")
            if new_ops:
                high_water = max(op["position"] for op in new_ops)
            open_ids = self.mirror_writer.open_ids()
            refreshed = self.ops(ids=open_ids).get("ops") or [] if open_ids else []
            # limit=10000 here is NOT a real ceiling (NIT-5, 10-05 review): Ledger.list_ops's own
            # active=True branch ("every row still queued or running, ALL of them") runs a plain
            # "state in (?, ?) order by position" query with no LIMIT clause at all -- this kwarg
            # is simply ignored on that code path. Kept as a documented, honest value (large enough
            # to look intentional rather than an accidental small default) rather than removed and
            # re-litigated later; the bridge's own docstring is the actual source of truth.
            active = self.ops(active=True, limit=10000).get("ops") or []

            if open_ids and not refreshed:
                self.log(f"relay: ledger position reset detected (all {len(open_ids)} op(s) this "
                         f"mirror still had open vanished from the bridge at once) -- resetting "
                         f"the ops mirror")
                self.mirror_writer.reset()
                self._mark_mirrored(False, "ledger_position_reset")
                return

            if self._ops_route_missing:
                self.log("relay: /v1/ops is reachable again -- ops mirror resumed")
                self._ops_route_missing = False
            if self._ops_last_error_message is not None:  # NIT-2: log the recovery, once
                self.log("relay: ops mirror recovered")
                self._ops_last_error_message = None

            # Finding 3's own fix: an active row above this pass's high-water mark is left for a
            # later position-paging pass, never written now.
            active = [op for op in active if op["position"] <= high_water]

            all_ops = new_ops + refreshed + active
            if all_ops:
                self.mirror_writer.write(all_ops)
            if new_ops:
                self._run_alarm_check_if_due()
            self._mark_mirrored(True)
        except OpsRouteMissing as exc:
            if not self._ops_route_missing:
                self.log(f"relay: {exc} -- ops mirror will be skipped until the executor is upgraded")
                self._ops_route_missing = True
            self._mark_mirrored(False, exc.code)
        except RelayError as exc:
            # NIT-2, 10-05 review: log only on a state CHANGE (first failure, or the message
            # changing -- a 500 replaced by a 503 is new information) -- same discipline as
            # OpsRouteMissing just above, for the same reason: this used to log every single ~3s
            # pass, a line per cycle for as long as the failure lasts.
            message = str(exc)
            if message != self._ops_last_error_message:
                self.log(f"relay: ops mirror failed: {exc}")
                self._ops_last_error_message = message
            self._mark_mirrored(False, type(exc).__name__)
        except Exception as exc:  # our own bug, named as one -- never takes the drain loop with it
            message = f"{type(exc).__name__}: {exc}"
            if message != self._ops_last_error_message:
                self.log(f"relay: ops mirror failed: {message}")
                self._ops_last_error_message = message
            self._mark_mirrored(False, type(exc).__name__)

    def fetch(self):
        """-> the outbox items after our cursor. The ack rides along, so the mini can sweep."""
        position = self.cursor.position()
        query = f"/v1/outbox?after={position}&ack={position}"
        if self.fetch_limit:
            query += f"&limit={int(self.fetch_limit)}"
        status, body, _elapsed = self.executor(query)
        if status != 200:
            raise RelayError(f"executor outbox is {status}: {body}")
        return body.get("events") or []

    # --- our side -------------------------------------------------------------------------------
    def deliver(self, item):
        """POST one envelope to our own webhook. -> 'accepted' | 'duplicate'. Raises otherwise."""
        payload = item["payload"]
        body = EV.meta_envelope(payload, phone_number_id=self.phone_number_id,
                                display_phone_number=self.display_phone_number,
                                waba_id=self.waba_id)
        status, answer, elapsed = http_json(
            "POST", self.webhook_url, body=body,
            headers={"X-Pflege-Bridge-Token": self.inbound_token,
                     "X-Bridge-Delivery-Id": payload["inbound_id"]},
            timeout=60.0)
        if status != 200:
            raise RelayError(f"bridge-webhook answered {status} for outbox #{item['id']} "
                             f"(inbound_id={payload['inbound_id']}): {answer}")
        results = answer.get("results") or []
        if not results:
            raise RelayError(
                f"bridge-webhook accepted outbox #{item['id']} (inbound_id={payload['inbound_id']}) "
                "but handled no message "
                f"(skipped={answer.get('skipped')}, events={answer.get('events')}). The cursor "
                "stays where it is. Check WA_BRIDGE_PHONE_NUMBER_ID against the server's.")
        verdict = results[0].get("status") or "accepted"
        self.log(f"delivered outbox #{item['id']} in {elapsed * 1000:.0f} ms -> {verdict}")
        return verdict

    def drain_once(self):
        """-> how many items reached our server this pass. Stops at the first refusal."""
        moved = 0
        for item in self.fetch():
            verdict = self.deliver(item)
            self.cursor.advance(item["id"], item["payload"]["inbound_id"], verdict)
            self.duplicates += verdict == "duplicate"
            self.delivered += verdict == "accepted"
            moved += 1
        return moved

    def run(self, *, interval=INTERVAL_SEC, stop=None):
        self.log(f"relay: cursor at {self.cursor.position()}, every {interval:.0f}s")
        backoff = interval
        # TASK-255: checked once immediately, not only after the first ALARM_CHECK_INTERVAL_SEC --
        # a relay that starts up already blind should say so now, not wait a full interval to ask.
        self._next_alarm_check = time.monotonic()
        while stop is None or not stop.is_set():
            drained_ok = True
            try:
                self.drain_once()
                backoff = interval
                self._mark_synced(True)
            except RelayError as exc:
                # Loud, and it keeps trying: the mini rebooting and our webhook being restarted are
                # both normal, and both look like this. The cursor has not moved either way.
                self.log(f"relay: {exc}")
                self.tunnel.close()
                backoff = min(backoff * 2, 60.0)
                self._mark_synced(False, str(exc))
                drained_ok = False
            except Exception as exc:  # our own bug, named as one
                self.log(f"relay: {type(exc).__name__}: {exc}")
                self.tunnel.close()
                backoff = min(backoff * 2, 60.0)
                self._mark_synced(False, f"{type(exc).__name__}: {exc}")
                drained_ok = False
            # TASK-283.7: a no-op when mirror_writer is None (every test in
            # tests/test_bridge_relay.py builds a Relay without one), self-contained (never
            # raises) when it isn't -- but skipped on a cycle whose drain itself just failed
            # (review finding 8, MINOR): the tunnel the drain just closed would otherwise be
            # reopened immediately for a second, unrelated purpose, adding a second ssh attempt of
            # up to TUNNEL_READY_SEC on top of the failure the drain loop is already backing off.
            if drained_ok:
                self.mirror_ops()
            self._run_alarm_check_if_due()
            time.sleep(backoff)

    def close(self):
        self.tunnel.close()


def from_env(**override):
    """Build a Relay from the environment. Every name is required except the tuning ones."""
    check_env()
    state = os.path.expanduser(env("WA_BRIDGE_RELAY_STATE", "~/.local/state/pflege-wa-bridge/relay.sqlite"))
    os.makedirs(os.path.dirname(state), exist_ok=True)
    kwargs = dict(
        host=env("WA_BRIDGE_SSH_HOST", required=True),
        token=env("WA_BRIDGE_TOKEN", required=True),
        inbound_token=env("WA_BRIDGE_INBOUND_TOKEN", required=True),
        webhook_url=env("WA_BRIDGE_WEBHOOK_URL",
                        "http://127.0.0.1:8502/api/wa/bridge-webhook"),
        phone_number_id=env("WA_BRIDGE_PHONE_NUMBER_ID", required=True),
        display_phone_number=env("WA_BRIDGE_RAIL_NUMBER", ""),
        waba_id=env("WA_BRIDGE_WABA_ID", "pflege-wa-bridge"),
        remote_port=int(env("WA_BRIDGE_REMOTE_PORT", REMOTE_PORT)),
        local_port=int(env("WA_BRIDGE_LOCAL_PORT", LOCAL_PORT)),
        cursor=Cursor(state),
        sync_writer=_write_rail_sync,   # review item 13: only the production Relay gets a real writer
        mirror_writer=_OpsMirror(),     # TASK-283.7: same convention -- only from_env wires one
        snapshot_writer=_write_rail_snapshot,
        mirror_sync_writer=_write_mirror_sync,  # review finding 4: same convention
    )
    kwargs.update(override)
    return Relay(**kwargs)


def main(argv=None):  # pragma: no cover - the unit's entry point
    parser = argparse.ArgumentParser(description="Drain the mini's inbound outbox into our webhook")
    parser.add_argument("--once", action="store_true", help="one drain pass, then exit")
    parser.add_argument("--probe", action="store_true",
                        help="measure the round-trip to the executor and exit")
    parser.add_argument("--interval", type=float, default=float(env("WA_BRIDGE_RELAY_INTERVAL_SEC",
                                                                    INTERVAL_SEC)))
    args = parser.parse_args(argv)
    relay = from_env()
    try:
        if args.probe:
            samples = []
            for _ in range(5):
                body, elapsed = relay.health()
                samples.append(elapsed)
            print(json.dumps({"ok": True, "rtt_ms": [round(s * 1000, 1) for s in samples],
                              "health": body}, indent=2))
            return 0
        if args.once:
            moved = relay.drain_once()
            print(json.dumps({"ok": True, "moved": moved, "cursor": relay.cursor.position()}))
            return 0
        relay.run(interval=args.interval)
        return 0
    finally:
        relay.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
