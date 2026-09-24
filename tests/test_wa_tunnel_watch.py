"""Offline tests for app/wa/tunnel_watch.py (TASK-294, Ivan 2026-09-24): a periodic probe of the
phone rail's local forwarded port, alerting once a failure streak crosses a threshold and logging
recovery afterwards. No real socket, no real timer -- ``probe`` is monkeypatched throughout.
"""
import json

from app.wa import tunnel_watch as TW


def _state_file(tmp_path, monkeypatch):
    path = tmp_path / "tunnel_watch.json"
    monkeypatch.setattr(TW, "STATE_PATH", path)
    monkeypatch.setattr(TW, "ALERT_AFTER_SEC", 60.0)
    return path


def test_a_healthy_probe_clears_any_prior_state(tmp_path, monkeypatch):
    path = _state_file(tmp_path, monkeypatch)
    path.write_text(json.dumps({"failing_since": 100.0, "alerted": False}), encoding="utf-8")
    monkeypatch.setattr(TW, "probe", lambda: True)

    assert TW.check_once(now=200.0) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_a_short_outage_records_a_streak_without_alerting(tmp_path, monkeypatch, caplog):
    _state_file(tmp_path, monkeypatch)
    monkeypatch.setattr(TW, "probe", lambda: False)
    caplog.set_level("ERROR")

    assert TW.check_once(now=1000.0) is False
    state = json.loads(TW.STATE_PATH.read_text(encoding="utf-8"))
    assert state == {"failing_since": 1000.0, "alerted": False}
    assert not any(r.levelname == "ERROR" for r in caplog.records)


def test_a_streak_past_the_threshold_alerts_exactly_once(tmp_path, monkeypatch, caplog):
    _state_file(tmp_path, monkeypatch)
    monkeypatch.setattr(TW, "probe", lambda: False)
    caplog.set_level("ERROR")

    TW.check_once(now=1000.0)                    # streak begins, not yet alerted
    caplog.clear()
    TW.check_once(now=1000.0 + 61.0)              # crosses the 60s threshold -> ERROR, once
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "DOWN for" in errors[0].message
    state = json.loads(TW.STATE_PATH.read_text(encoding="utf-8"))
    assert state == {"failing_since": 1000.0, "alerted": True}

    caplog.clear()
    TW.check_once(now=1000.0 + 120.0)             # still down, already alerted -> silent
    assert not any(r.levelname == "ERROR" for r in caplog.records)


def test_recovery_after_an_alerted_outage_logs_how_long_it_lasted(tmp_path, monkeypatch, caplog):
    _state_file(tmp_path, monkeypatch)
    caplog.set_level("INFO")
    TW.STATE_PATH.write_text(json.dumps({"failing_since": 1000.0, "alerted": True}), encoding="utf-8")
    monkeypatch.setattr(TW, "probe", lambda: True)

    assert TW.check_once(now=1000.0 + 300.0) is True
    infos = [r for r in caplog.records if r.levelname == "INFO"]
    assert len(infos) == 1 and "RECOVERED after 300s" in infos[0].message
    assert json.loads(TW.STATE_PATH.read_text(encoding="utf-8")) == {}


def test_recovery_before_ever_alerting_is_silent(tmp_path, monkeypatch, caplog):
    """A blip that self-heals inside the threshold never alerted, so recovery is not news either --
    only a RECOVERED after an actual ERROR is worth a line."""
    _state_file(tmp_path, monkeypatch)
    caplog.set_level("INFO")
    TW.STATE_PATH.write_text(json.dumps({"failing_since": 1000.0, "alerted": False}), encoding="utf-8")
    monkeypatch.setattr(TW, "probe", lambda: True)

    TW.check_once(now=1000.0 + 10.0)
    assert not any(r.levelname == "INFO" and "RECOVERED" in r.message for r in caplog.records)


def test_probe_is_a_plain_tcp_connect_that_never_sends_bytes(monkeypatch):
    """AC#3: the probe must never touch the bridge executor or the mini -- proven by connecting to
    a real local socket this test owns and asserting it never received any data."""
    import socket
    import threading

    received = []
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()

    def _accept_once():
        conn, _ = server.accept()
        conn.settimeout(1)
        try:
            received.append(conn.recv(1024))
        except OSError:
            received.append(b"")
        conn.close()

    t = threading.Thread(target=_accept_once, daemon=True)
    t.start()
    assert TW.probe(host=host, port=port, timeout=2) is True
    t.join(timeout=2)
    server.close()
    assert received == [b""], "the probe must not send any bytes"


def test_probe_returns_false_when_nothing_is_listening():
    assert TW.probe(host="127.0.0.1", port=1, timeout=0.5) is False
