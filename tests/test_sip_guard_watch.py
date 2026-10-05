"""tools/sip_guard_watch.py: alert decisions over a fake asterisk log and fail2ban events log. Never sends mail."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sip_guard_watch", Path(__file__).resolve().parent.parent / "tools/sip_guard_watch.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

T0 = 1790880000.0  # 2026-10-01 18:40 UTC


def fail_line(ip, hhmmss, method="REGISTER"):
    return (f"[Oct  1 {hhmmss}] NOTICE[205354] res_pjsip/pjsip_distributor.c: Request '{method}' from "
            f"'<sip:100@192.0.2.10>' failed for '{ip}:5060' (callid: abc@{ip}) - Failed to authenticate\n")


class Rig:
    def __init__(self, tmp):
        self.ast, self.ev, self.state = tmp / "messages.log", tmp / "events.log", tmp / "state"
        self.ast.write_text("[Oct  1 18:00:00] VERBOSE[1] noise\n")
        self.ev.write_text("1790880000 start asterisk\n")
        self.sent, self.fail_send = [], False

    def append(self, path, text):
        with path.open("a") as f:
            f.write(text)

    def send(self, subject, body):
        if self.fail_send:
            raise OSError("smtp down")
        self.sent.append((subject, body))
        return f"<id{len(self.sent)}@x>"

    def run(self, now, active="active", disk_free=5 << 30):
        return W.run(state_dir=self.state, ast_log=self.ast, events_log=self.ev, now=now, active=active,
                     disk_free=disk_free, send=self.send)

    def health(self):
        return json.loads((self.state / "health.json").read_text())


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    assert r.run(T0) == []  # first run starts at the end of both logs
    return r


def test_first_run_never_alerts_on_backlog(tmp_path):
    r = Rig(tmp_path)
    r.append(r.ast, "".join(fail_line("1.2.3.4", "18:30:00") for _ in range(500)))
    assert r.run(T0) == [] and r.sent == []


def test_scanner_rate_is_quiet(rig):
    rig.append(rig.ast, "".join(fail_line("5.6.7.8", f"18:41:{s:02d}", "INVITE") for s in range(0, 40)))
    assert rig.run(T0 + 300) == [] and rig.sent == []
    assert rig.health()["failures_last_run"] == 40


def test_short_burst_before_ban_alerts_once_and_names_the_ban(rig):
    rig.append(rig.ast, "".join(fail_line("213.202.230.211", "18:41:07") for _ in range(45)))
    rig.append(rig.ev, f"{T0 + 70} ban asterisk 213.202.230.211 failures=6 bantime=7200 bancount=2\n")
    due = rig.run(T0 + 300)
    assert [(k, ip) for k, ip, _ in due] == [("brute_force", "213.202.230.211")]
    subject, body = rig.sent[0]
    assert "213.202.230.211" in subject and "атака" in subject
    assert "45 неудачных запросов в секунду" in body and "на 2 ч, бан №2" in body
    # the same IP again within the cooldown: no second mail
    rig.append(rig.ast, "".join(fail_line("213.202.230.211", "18:47:07") for _ in range(45)))
    assert rig.run(T0 + 600) == [] and len(rig.sent) == 1
    # after the cooldown it is mailed again
    rig.append(rig.ast, "".join(fail_line("213.202.230.211", "01:00:07") for _ in range(45)))
    assert len(rig.run(T0 + 600 + W.COOLDOWN_SEC["brute_force"])) == 1 and len(rig.sent) == 2


def test_unblocked_attack_is_not_blocked_and_log_growth(rig):
    lines = "".join(fail_line("9.9.9.9", f"18:4{m}:{s:02d}") for m in range(1, 6) for s in range(60) for _ in range(3))
    rig.append(rig.ast, lines)
    due = rig.run(T0 + 300)
    kinds = {k for k, _, _ in due}
    assert {"not_blocked", "brute_force"} <= kinds
    subject, body = rig.sent[0]
    assert "бан не держит" in subject and "Бана на момент проверки нет." in body


def test_log_growth_needs_ten_minutes_of_samples(rig):
    blob = "[Oct  1 18:41:00] VERBOSE[1] " + "x" * 1000 + "\n"
    rig.append(rig.ast, blob * 3000)  # ~3 MB in 5 min = 36 MB/h, but the span is under 10 min
    assert rig.run(T0 + 300) == []
    rig.append(rig.ast, blob * 3000)
    due = rig.run(T0 + 600)
    assert [k for k, _, _ in due] == ["log_growth"]
    body = rig.sent[0][1]
    assert "МБ/ч" in body
    # the file size in the alert must come from the real ast_log passed in, not the module's own
    # ASTERISK_LOG global (which does not exist on this machine and would always read as 0 МБ)
    assert "файл 0 МБ" not in body


def test_guard_down_and_jail_stop(rig):
    assert [k for k, _, _ in rig.run(T0 + 300, active="failed")] == ["guard_down"]
    rig.append(rig.ev, f"{T0 + 400} stop asterisk\n")
    assert rig.run(T0 + 500) == []  # same key, inside its cooldown
    assert rig.health()["jail_last_event"][1] == "stop"
    assert [k for k, _, _ in rig.run(T0 + 300 + W.COOLDOWN_SEC["guard_down"])] == ["guard_down"]


def test_failed_send_is_retried_next_run(rig):
    rig.fail_send = True
    rig.append(rig.ast, "".join(fail_line("1.1.1.1", "18:41:07") for _ in range(40)))
    assert len(rig.run(T0 + 300)) == 1 and rig.sent == []
    assert "smtp down" in rig.health()["last_alert_error"]["error"]
    # M5: the second pass appends NO new lines -- ast_pos already moved past the burst above, so if
    # the finding were not kept in state["pending"] it would be gone by now. It is still mailed.
    rig.fail_send = False
    assert len(rig.run(T0 + 600)) == 1 and len(rig.sent) == 1
    h = rig.health()
    assert h["last_alert"]["message_id"] == "<id1@x>" and h["last_alert_error"] is None
    subject = rig.sent[0][0]
    assert "1.1.1.1" in subject


def test_pending_finding_survives_two_failed_sends_then_sent_once(rig):
    rig.fail_send = True
    rig.append(rig.ast, "".join(fail_line("8.8.8.8", "18:41:07") for _ in range(40)))
    assert len(rig.run(T0 + 300)) == 1 and rig.sent == []
    # second failed send, still no new lines at all: the pending finding must not be dropped
    assert len(rig.run(T0 + 600)) == 1 and rig.sent == []
    rig.fail_send = False
    assert len(rig.run(T0 + 900)) == 1 and len(rig.sent) == 1
    assert "8.8.8.8" in rig.sent[0][0]


def test_copytruncate_rotation_reads_from_start(rig):
    rig.ast.write_text("")  # logrotate copytruncate
    rig.append(rig.ast, "".join(fail_line("2.2.2.2", "00:00:05") for _ in range(35)))
    assert [k for k, _, _ in rig.run(T0 + 300)] == ["brute_force"]


def test_partial_last_line_waits_for_its_newline(rig):
    rig.append(rig.ast, fail_line("3.3.3.3", "18:41:00").rstrip("\n"))
    rig.run(T0 + 300)
    assert rig.health()["failures_last_run"] == 0
    rig.append(rig.ast, "\n")
    rig.run(T0 + 600)
    assert rig.health()["failures_last_run"] == 1


def test_bans_and_unbans_tracked(rig):
    rig.append(rig.ev, f"{T0 + 10} ban asterisk 4.4.4.4 failures=6 bantime=3600 bancount=1\n"
                       f"{T0 + 20} ban asterisk 5.5.5.5 failures=6 bantime=3600 bancount=1\n"
                       f"{T0 + 30} unban asterisk 4.4.4.4\n")
    rig.run(T0 + 300)
    h = rig.health()
    assert h["banned_now"] == 1 and h["bans_24h"] == 2 and h["ips_banned_24h"] == 2


def test_dry_run_keeps_state(rig, capsys):
    rig.append(rig.ast, "".join(fail_line("6.6.6.6", "18:41:07") for _ in range(40)))
    W.run(state_dir=rig.state, ast_log=rig.ast, events_log=rig.ev, now=T0 + 300, active="active",
          disk_free=1, send=rig.send, dry_run=True)
    assert rig.sent == [] and "would mail" in capsys.readouterr().out
    assert len(rig.run(T0 + 300)) == 1  # the real run still sees the same lines
