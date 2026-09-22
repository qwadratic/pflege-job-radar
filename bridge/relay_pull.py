"""The VPS side of the pull: drain the mini's inbound outbox into our webhook (TASK-143).

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


class RelayError(RuntimeError):
    """The relay stopped on purpose. The cursor did not move."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env(name, default=None, *, required=False):
    value = (os.environ.get(name) or "").strip()
    if not value and required:
        raise RelayError(f"{name} is unset -- the relay will not guess it")
    return value or default


#: Every variable ``from_env`` needs, and where it is supposed to come from. Named together so a
#: misconfigured host is one message and not four restarts (TASK-146): this unit reads three
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

        Without that first branch (TASK-146) a borrowed forward was never "up", so every drain went
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
                 log=print, fetch_limit=None):
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
            raise RelayError(f"bridge-webhook answered {status}: {answer}")
        results = answer.get("results") or []
        if not results:
            raise RelayError(
                "bridge-webhook accepted the payload but handled no message "
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
        while stop is None or not stop.is_set():
            try:
                self.drain_once()
                backoff = interval
            except RelayError as exc:
                # Loud, and it keeps trying: the mini rebooting and our webhook being restarted are
                # both normal, and both look like this. The cursor has not moved either way.
                self.log(f"relay: {exc}")
                self.tunnel.close()
                backoff = min(backoff * 2, 60.0)
            except Exception as exc:  # our own bug, named as one
                self.log(f"relay: {type(exc).__name__}: {exc}")
                self.tunnel.close()
                backoff = min(backoff * 2, 60.0)
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
