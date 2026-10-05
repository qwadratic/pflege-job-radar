#!/usr/bin/env python3
"""Watch the SIP gateway guard and mail Ivan when the brute force comes back (Ivan, 2026-10-01: "защити сим шлюз
через fail2ban, поставь мониторинг, и если повторится сообщи").

fail2ban bans offenders on its own: jail "asterisk", /etc/fail2ban/jail.d/zz-sip-guard.local. This job only watches
and tells. Every run reads what was appended since the last run to two logs:
  /var/log/asterisk/messages.log   asterisk's failed SIP requests ("... failed for '<ip>:<port>' ...")
  /var/log/sip-guard/events.log    one line per jail start/stop and ban/unban, written by fail2ban's
                                   sip-guard-events action
It also checks that fail2ban is running. It mails Ivan from the daria box, as tools/daria_forward.py does, when:
  brute_force   one IP sent >= BURST_SEC_LINES failed requests within one second, or >= BURST_MIN_LINES within
                one minute. On 2026-09-28..10-01 scanners peaked at 10 a second and 39 a minute; the 2026-09-30
                attack ran at 241 and ~5800. fail2ban bans within a second or two, which still leaves a burst.
  not_blocked   one IP sent >= BURST_MIN_LINES a minute for NOT_BLOCKED_MINUTES minutes in one run: the ban is
                not holding.
  volume        all IPs together failed >= VOLUME_PER_H times an hour (many IPs, each under the ban limit).
  log_growth    messages.log grows >= GROWTH_MB_PER_H, whatever the reason. The disk filled from this file once.
  guard_down    fail2ban is not active, or the jail's last event is a stop.
Each alert kind is mailed at most once per its cooldown; brute_force and not_blocked count it per IP. A failed send
is retried on the next run. The state file and health.json live in ~/.local/state/sip-guard/.

  sip_guard_watch.py              one run (cron: tools/sip_guard_cron.sh, every 5 minutes)
  sip_guard_watch.py --dry-run    one run; print the mail instead of sending it and leave the state unchanged
  sip_guard_watch.py --test-mail  send one test mail to check the channel
"""
import argparse
import json
import os
import re
import shutil
import smtplib
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

ASTERISK_LOG = Path("/var/log/asterisk/messages.log")
EVENTS_LOG = Path("/var/log/sip-guard/events.log")
STATE_DIR = Path.home() / ".local/state/sip-guard"
ENV = Path(__file__).resolve().parent.parent / ".env"
BOX = "daria.s@pflege-connect.work"
TO = "ivan.d.kotelnikov@gmail.com"
HOST = "tasker-dispatcher-01"

BURST_SEC_LINES = 30
BURST_MIN_LINES = 100
NOT_BLOCKED_MINUTES = 3
VOLUME_PER_H = 3000
GROWTH_MB_PER_H = 30
RATE_MIN_SPAN_SEC = 600
COOLDOWN_SEC = {"guard_down": 3600, "not_blocked": 3600, "log_growth": 3 * 3600, "volume": 6 * 3600,
                "brute_force": 6 * 3600}
SEVERITY = ["guard_down", "not_blocked", "log_growth", "volume", "brute_force"]

FAIL_RE = re.compile(r"^\[(\w{3} +\d+ \d\d:\d\d:\d\d)[^\]]*\].* failed for '(\[[0-9a-fA-F:.]+\]|[0-9.]+)(?::\d+)?'")


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def hm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M")


def span(sec):
    sec = int(sec)
    if sec % 86400 == 0:
        return f"{sec // 86400} д"
    if sec % 3600 == 0:
        return f"{sec // 3600} ч"
    return f"{sec // 60} мин"


def read_new(path, pos, from_start=False):
    """The complete lines appended to path since pos, the new pos and the bytes the file grew by.

    pos is [inode, offset], or None on the first run: start at the end (never alert on a backlog), or at the start
    with from_start (the small events log, so the ban list is complete from the first run). A shrunk file
    (logrotate copytruncate) or a new inode is read from its start."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return [], pos, 0
    if pos is None and not from_start:
        return [], [st.st_ino, st.st_size], 0
    ino, off = pos or (st.st_ino, 0)
    if ino != st.st_ino or st.st_size < off:
        off = 0
    grown = st.st_size - off
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read(grown)
    cut = data.rfind(b"\n") + 1
    return data[:cut].decode("utf-8", "replace").splitlines(), [st.st_ino, off + cut], grown


def parse_failures(lines):
    """{ip: {"Mon DD HH:MM:SS": failed-request lines}} for asterisk's "... failed for '<ip>:<port>' ..." lines."""
    per = defaultdict(lambda: defaultdict(int))
    for line in lines:
        m = FAIL_RE.match(line)
        if m:
            per[m.group(2).strip("[]")][re.sub(r" +", " ", m.group(1))] += 1
    return per


def parse_events(lines):
    """[(ts, kind, jail, ip or None, {k: v})] from sip-guard-events lines."""
    out = []
    for line in lines:
        p = line.split()
        if len(p) < 3:
            continue
        try:
            ts = float(p[0])
        except ValueError:
            continue
        ip = p[3] if p[1] in ("ban", "unban") and len(p) > 3 else None
        kv = dict(x.split("=", 1) for x in p[4:] if "=" in x)
        out.append((ts, p[1], p[2], ip, kv))
    return out


def fail2ban_active():
    try:
        r = subprocess.run(["systemctl", "is-active", "fail2ban"], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {e}"


def rate(samples, idx, now):
    """Per hour over the kept samples [start, end, fail_lines, grown_bytes]; None until they span RATE_MIN_SPAN_SEC."""
    if not samples:
        return None
    first = min(s[0] for s in samples)
    if now - first < RATE_MIN_SPAN_SEC:
        return None
    return sum(s[idx] for s in samples) * 3600 / (now - first)


def check(state, now, ast_lines, grown, events, active, disk_free, ast_log=ASTERISK_LOG):
    """Update state in place; return the findings due now: [(kind, key, text)]."""
    for ts, kind, jail, ip, kv in events:
        state["last_event"] = [ts, kind, jail]
        if kind == "ban":
            state["banned"][ip] = {"at": ts, "bantime": int(float(kv.get("bantime", 0) or 0)),
                                   "bancount": int(kv.get("bancount", 0) or 0)}
            state["bans"].append([ts, ip])
        elif kind == "unban":
            state["banned"].pop(ip, None)
        elif kind == "stop":
            state["banned"].clear()
    state["bans"] = [b for b in state["bans"] if b[0] >= now - 86400]

    per = parse_failures(ast_lines)
    total = sum(sum(m.values()) for m in per.values())
    state["samples"].append([state.get("last_run") or now, now, total, grown])
    state["samples"] = [s for s in state["samples"] if s[1] >= now - 3600]
    fail_h, bytes_h = rate(state["samples"], 2, now), rate(state["samples"], 3, now)

    def ban_note(ip):
        b = state["banned"].get(ip)
        if not b:
            return "Бана на момент проверки нет."
        n = f", бан №{b['bancount']}" if b["bancount"] else ""
        return f"fail2ban забанил в {hm(b['at'])} на {span(b['bantime'])}{n}."

    found = []
    if active != "active":
        found.append(("guard_down", "guard_down", f"fail2ban не работает: systemctl is-active = {active}. "
                      "Защиты нет, атака пойдёт в лог asterisk без ограничений."))
    elif state.get("last_event") and state["last_event"][1] == "stop":
        found.append(("guard_down", "guard_down", f"Jail asterisk остановлен в {hm(state['last_event'][0])}, "
                      "fail2ban сам работает. Защиты нет."))
    for ip, seconds in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        minutes = defaultdict(int)
        for sec, n in seconds.items():
            minutes[sec[:-3]] += n
        peak_sec, per_sec = max(seconds.items(), key=lambda kv: kv[1])
        peak_min, per_min = max(minutes.items(), key=lambda kv: kv[1])
        if per_sec < BURST_SEC_LINES and per_min < BURST_MIN_LINES:
            continue
        hot = sum(1 for n in minutes.values() if n >= BURST_MIN_LINES)
        n_all = sum(seconds.values())
        if hot >= NOT_BLOCKED_MINUTES:
            found.append(("not_blocked", ip, f"Бан не держит: {ip} шлёт от {BURST_MIN_LINES} неудачных SIP-запросов "
                          f"в минуту уже {hot} мин (всего {n_all} с прошлой проверки). {ban_note(ip)}"))
        found.append(("brute_force", ip, f"Атака на SIP: {ip}, до {per_sec} неудачных запросов в секунду и {per_min} "
                      f"в минуту (пик {peak_sec}), всего {n_all} с прошлой проверки. {ban_note(ip)}"))
    if bytes_h is not None and bytes_h >= GROWTH_MB_PER_H << 20:
        size = ast_log.stat().st_size if ast_log.exists() else 0
        found.append(("log_growth", "log_growth", f"Лог asterisk растёт на {bytes_h / (1 << 20):.0f} МБ/ч (файл "
                      f"{size / (1 << 20):.0f} МБ). На диске свободно {disk_free / (1 << 30):.1f} ГБ."))
    if fail_h is not None and fail_h >= VOLUME_PER_H:
        found.append(("volume", "volume", f"Много неудачных SIP-запросов: {fail_h:.0f} в час со всех адресов "
                      f"(обычно до 300). За последнюю проверку {len(per)} адресов."))

    prev = state.get("health") or {}
    state["health"] = {
        "checked_at": utc(now), "fail2ban": active, "jail_last_event": state.get("last_event"),
        "banned_now": len(state["banned"]), "bans_24h": len(state["bans"]),
        "ips_banned_24h": len({b[1] for b in state["bans"]}),
        "failures_last_run": total, "top_ips_last_run": sorted(((sum(m.values()), ip) for ip, m in per.items()),
                                                                reverse=True)[:5],
        "failures_per_h": None if fail_h is None else round(fail_h),
        "log_mb_per_h": None if bytes_h is None else round(bytes_h / (1 << 20), 1),
        "disk_free_gb": round(disk_free / (1 << 30), 2),
        "last_alert": prev.get("last_alert"), "last_alert_error": prev.get("last_alert_error"),
    }
    alerted = state["alerted"]
    return [f for f in found if now - alerted.get(f"{f[0]}:{f[1]}", 0) >= COOLDOWN_SEC[f[0]]]


def compose(state, now, due):
    due = sorted(due, key=lambda f: SEVERITY.index(f[0]))
    head = {"guard_down": "fail2ban не работает", "not_blocked": "бан не держит", "log_growth": "лог растёт",
            "volume": "много попыток", "brute_force": "атака"}[due[0][0]]
    ips = [f[1] for f in due if f[0] in ("brute_force", "not_blocked")]
    subject = f"[SIP-шлюз] {head}" + (f": {', '.join(dict.fromkeys(ips))}" if ips else "")
    h = state["health"]
    body = [f"SIP-шлюз ({HOST}, asterisk 5060/udp), {utc(now)}.", ""]
    body += [f"- {f[2]}" for f in due]
    body += ["", f"За 24 ч: {h['bans_24h']} банов, {h['ips_banned_24h']} адресов. Сейчас в бане: {h['banned_now']}.",
             "Проверить: sudo fail2ban-client status asterisk",
             f"Состояние сторожа: {STATE_DIR / 'health.json'}"]
    return subject, "\n".join(body) + "\n"


def load_box():
    """SMTP credentials of BOX from the repo's .env (MAILBOX_<n>_*), parsed here so a mid-edit of
    tools/clinic_mailer.py by another session can never break the alert path."""
    e = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("export "):
            s = s[7:]
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            e[k.strip()] = v.strip().strip('"').strip("'")
    for n in range(1, int(e.get("MAILBOX_COUNT", 0)) + 1):
        if e.get(f"MAILBOX_{n}_ADDRESS", "").lower() == BOX:
            return {k: e.get(f"MAILBOX_{n}_{k}") for k in ("ADDRESS", "PASSWORD", "SMTP_HOST", "SMTP_PORT")}
    raise RuntimeError(f"no MAILBOX_<n>_ADDRESS={BOX} in {ENV}")


def send_mail(subject, body):
    box = load_box()
    msg = EmailMessage()
    msg["From"] = formataddr(("SIP guard", box["ADDRESS"]))
    msg["To"] = TO
    msg["Reply-To"] = TO
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=box["ADDRESS"].split("@", 1)[1])
    msg.set_content(body)
    port = int(box["SMTP_PORT"])
    with (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(box["SMTP_HOST"], port, timeout=60) as s:
        if port != 465:
            s.starttls()
        s.login(box["ADDRESS"], box["PASSWORD"])
        refused = s.send_message(msg)
    if refused:
        raise RuntimeError(f"SMTP refused {refused}")
    return msg["Message-ID"]


def load_state(state_dir):
    p = state_dir / "state.json"
    s = json.loads(p.read_text()) if p.exists() else {}
    for k, v in (("banned", {}), ("bans", []), ("samples", []), ("alerted", {}), ("pending", {})):
        s.setdefault(k, v)
    return s


def save_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def run(*, state_dir=STATE_DIR, ast_log=ASTERISK_LOG, events_log=EVENTS_LOG, now=None, active=None,
        disk_free=None, send=send_mail, dry_run=False):
    now = time.time() if now is None else now
    state_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(state_dir)
    ast_lines, state["ast_pos"], grown = read_new(ast_log, state.get("ast_pos"))
    ev_lines, state["ev_pos"], _ = read_new(events_log, state.get("ev_pos"), from_start=True)
    new_findings = check(state, now, ast_lines, grown, parse_events(ev_lines),
                         fail2ban_active() if active is None else active,
                         shutil.disk_usage("/").free if disk_free is None else disk_free, ast_log)
    state["last_run"] = now
    # Findings due now = this run's new ones plus anything still unsent from a prior run (same key
    # merges to the freshest text) -- ast_pos already advanced past the lines a finding came from, so
    # once a finding is found it must live in state["pending"] until a send actually succeeds, or a
    # failed send (SMTP timeout, etc.) loses it the moment the attack that caused it stops.
    pending = state["pending"]
    for f in new_findings:
        pending[f"{f[0]}:{f[1]}"] = list(f)
    due = list(pending.values())
    if due:
        subject, body = compose(state, now, due)
        if dry_run:
            print(f"--- would mail {TO}: {subject}\n{body}")
        else:
            try:
                mid = send(subject, body)
                for f in due:
                    state["alerted"][f"{f[0]}:{f[1]}"] = now
                pending.clear()
                state["health"]["last_alert"] = {"at": utc(now), "subject": subject, "message_id": mid}
                state["health"]["last_alert_error"] = None
            except Exception as e:  # noqa: BLE001 -- stays in state["pending"], retried next run; alerted/cooldown untouched
                state["health"]["last_alert_error"] = {"at": utc(now), "error": f"{type(e).__name__}: {e}"}
                print(f"[{utc(now)}] ALERT SEND FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    state["alerted"] = {k: t for k, t in state["alerted"].items()
                        if now - t < COOLDOWN_SEC.get(k.split(":", 1)[0], 86400)}
    if not dry_run:
        save_json(state_dir / "state.json", state)
        save_json(state_dir / "health.json", state["health"])
    h = state["health"]
    print(f"[{utc(now)}] fail2ban={h['fail2ban']} banned={h['banned_now']} bans24h={h['bans_24h']} "
          f"fails={h['failures_last_run']} due={[f'{f[0]}:{f[1]}' for f in due]}")
    return due


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-mail", action="store_true")
    a = ap.parse_args()
    if a.test_mail:
        mid = send_mail("[SIP-шлюз] проверка канала уведомлений",
                        f"Сторож SIP-шлюза на {HOST} включён. Это тестовое письмо, проверка канала.\n"
                        "Дальше письма придут, только если атака повторится, бан не удержит её, лог asterisk "
                        "начнёт быстро расти или fail2ban упадёт.\n")
        print(f"sent {mid} -> {TO}")
        return 0
    run(dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
