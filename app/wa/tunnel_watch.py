"""Ivan, 2026-09-24: the phone rail's reverse tunnel to the mini (127.0.0.1:${WA_BRIDGE_LOCAL_PORT}
on this VPS, opened by pflege-wa-bridge-tunnel.service, deploy/wa-bridge/pflege-wa-bridge-tunnel.service)
dropped for 36 minutes with zero alert anywhere -- the systemd unit just retries silently, and the
outage was found only by an ad-hoc manual check. This is the smallest fix that closes that gap: a
periodic oneshot probe, driven by deploy/pflege-wa-tunnel-watch.timer, that does one plain TCP
connect to the local forwarded port (never touches the mini or the bridge executor itself) and logs
loudly -- an ERROR line once an outage has lasted past a threshold, and a matching RECOVERED line
when the next probe succeeds again -- so the outage window is visible in `journalctl -u
pflege-wa-tunnel-watch` without anyone needing to notice by hand.

STATE, NOT JUST A LOG LINE PER PROBE: a probe every ${INTERVAL}s that logged on every single failure
would spam the journal for a routine multi-minute reconnect (RestartSec=5 on the tunnel unit itself
already usually recovers within seconds) -- exactly the noise that trains a reader to ignore this
log. A tiny state file (``WA_TUNNEL_WATCH_STATE``, one JSON object) remembers when the CURRENT
failure streak started, so only a streak that crosses ``WA_TUNNEL_WATCH_ALERT_AFTER_SEC`` (default
60s) becomes a journal ERROR line, once, and every probe after that first alert stays silent until
recovery -- which then logs its own line naming how long the outage actually lasted.
"""
import json
import logging
import os
import socket
import time
from pathlib import Path

log = logging.getLogger(__name__)

HOST = os.environ.get("WA_BRIDGE_LOCAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WA_BRIDGE_LOCAL_PORT", "18793") or "18793")
CONNECT_TIMEOUT_SEC = float(os.environ.get("WA_TUNNEL_WATCH_CONNECT_TIMEOUT_SEC", "5") or "5")
ALERT_AFTER_SEC = float(os.environ.get("WA_TUNNEL_WATCH_ALERT_AFTER_SEC", "60") or "60")
STATE_PATH = Path(os.path.expanduser(
    os.environ.get("WA_TUNNEL_WATCH_STATE", "~/.local/state/pflege-wa-bridge/tunnel_watch.json")))


def probe(host=HOST, port=PORT, timeout=CONNECT_TIMEOUT_SEC):
    """-> True if a plain TCP connect to (host, port) succeeds, False otherwise. Opens and
    immediately closes the socket -- no bytes are ever sent, so this never reaches the bridge
    executor or the mini behind it, only proves the local forwarded port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def check_once(now=None):
    """Runs one probe, updates the failure-streak state file, and logs an ERROR/INFO line exactly
    at the two moments that matter (alert-threshold crossed, recovery after an alerted outage).
    -> the probe's own bool, for a caller (main(), or a test) that wants it."""
    now = time.time() if now is None else now
    ok = probe()
    state = _load_state()
    if ok:
        if state.get("alerted"):
            started = state.get("failing_since", now)
            log.info("phone-rail tunnel (%s:%s) RECOVERED after %.0fs down", HOST, PORT, now - started)
        _save_state({})
        return True
    failing_since = state.get("failing_since") or now
    alerted = bool(state.get("alerted"))
    down_for = now - failing_since
    if not alerted and down_for >= ALERT_AFTER_SEC:
        log.error("phone-rail tunnel (%s:%s) has been DOWN for %.0fs (>= %.0fs threshold) -- "
                 "pflege-wa-bridge-tunnel.service is not reconnecting on its own; check the mini "
                 "and the reverse tunnel", HOST, PORT, down_for, ALERT_AFTER_SEC)
        alerted = True
    _save_state({"failing_since": failing_since, "alerted": alerted})
    return False


def main():  # pragma: no cover - the entry point, not exercised offline
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    check_once()


if __name__ == "__main__":  # pragma: no cover
    main()
