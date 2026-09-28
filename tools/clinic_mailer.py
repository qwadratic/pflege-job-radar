#!/usr/bin/env python3
"""Clinic mailer: first letters and follow-ups on a cadence the operator edits in a JSON config.

A campaign is one config file plus recipients, templates and an append-only ledger (JSONL). Nothing is kept in
memory between runs: every command re-reads the ledger, so a halted or interrupted batch resumes by planning again.

Commands:
  plan   CONFIG [--now ISO] [--only ID ...] [--announce-at ISO [--start-at ISO]]
                                 render every step that is due now into <batches>/<id>.json (+ .txt for reading)
                                 and write <approvals>/<id>.pending.json listing exact recipients and body hashes.
                                 --only plans just these recipient ids; the others stay unplanned for a later run.
                                 --announce-at makes a scheduled batch (Ivan, 2026-09-28): it carries every remaining
                                 cadence step of every clinic, so one approval covers the first letters and all
                                 follow-ups, and each letter has a fixed send_at. An announcement with one PDF of the
                                 whole plan (tools/mailer_announce.py) goes to the config's "operators" at that time, or
                                 when the send starts if that is later. The first letters start a pause after --start-at
                                 (default: the announcement plus announce.window_minutes, and never less), every next one
                                 a pause after the previous; pauses are drawn from "pause_seconds" and no send time
                                 falls on a minute divisible by 5. A follow-up goes the cadence's "after" past the
                                 clinic's letter before it, so a business-day step keeps the time of day. Every later
                                 round (all clinics' follow-up N) gets a report to the operators as far before its first
                                 letter as --start-at is before the first letters minus the window: with --start-at
                                 09:00 and a 60-minute window, 08:00 on the follow-up day. The approval covers the
                                 times, the announcement, the report times and the operators.
  send   CONFIG BATCH_ID         allowlist mode: refuses unless every To/Cc address is in the allowlist file.
  send   CONFIG BATCH_ID --no-watch
                                 allowlist mode without reading the inbox, for senders whose IMAP is closed; says so
                                 loudly. Refused together with --live: a live batch must see bounces and stop replies.
  send   CONFIG BATCH_ID --live  real recipients: refuses unless <approvals>/<id>.json exists and matches the batch
                                 exactly (same items, same recipients, same sha256). Only Ivan runs --live; he
                                 approves a batch by reading the .txt and renaming <id>.pending.json to <id>.json.
                                 A scheduled batch waits for its announcement time, sends the announcement, then
                                 sends every letter at its send_at and every round's report at its time, over as many
                                 days as the cadence spans, and answers the operators' mail all the while (below). A
                                 clinic's answer takes its later letters out. After each round but the last the
                                 operators get what went and what comes next; after the last, "рассылка завершена".
                                 It refuses to start when the announcement would leave less than the window before
                                 the first letter, or when a letter's send_at or a report's time has already passed.
                                 After an error halt (network, classifier, Ctrl-C, SIGTERM or SIGHUP) the same command
                                 resumes the batch and tells the operators; after an operator's stop, a bounce, a
                                 complaint, a clinic's stop request or an SMTP refusal only a new plan continues.
  watch  CONFIG                  read the sender's inbox and log replies, bounces, complaints and stop requests
                                 against the campaign's sent messages. Over IMAP by default; config
                                 "watch_via": "graph" reads an M365 box through Microsoft Graph instead (folders are
                                 Graph's well-known names: inbox, junkemail). Graph uses the shared MSAL cache, which
                                 is root-owned: run every command that watches (watch, send) as
                                 sudo -E python3 tools/clinic_mailer.py ...
                                 "watch_via": "daria-inbox" reads through the root-owned helper `sudo -n
                                 /usr/local/sbin/daria-inbox` (tools/daria_inbox.py), for runs as the claude user.
  status CONFIG [--now ISO]      one line per recipient: steps sent, state, next step and when it is due.

Cadence (config "cadence"): an ordered list of steps. Step 0 goes when planned; every later step goes `after`
the previous step's send: "<n>m" minutes, "<n>h" hours, "<n>d" calendar days, "<n>bd" business days (the
config's window weekdays minus its holidays). A step with "in_thread": true answers the previous message
(In-Reply-To/References). A recipient leaves the sequence on any inbound kind listed in "stop_on"; a running
batch halts on any kind in "halt_on". Sends happen one at a time, with a random pause from "pause_seconds",
only inside "window" (config timezone), and the inbox is read during every pause.

Operator commands (scheduled batches): from the announcement until the batch ends, every mail to the sender box
from an address in "operators" is read by a classifier (the `claude` CLI with the Haiku model, config
"classifier") and answered to all operators. "stop" cancels the batch before the first letter and halts it after,
follow-ups included; "skip" takes the named clinics out, none of their letters still to come goes; "status" reports.
Anything else about the mailing is answered "not available, an operator is needed", and anything that is no command
at all is answered as such; neither changes the batch. A classifier failure halts the batch; the unread mail is read
again when the batch resumes. Every report, round, halt, resume and the end of the batch are mailed to the operators
and written to the ledger. Only stop, skip and status exist because each only removes or reports: a mail can never
add a recipient or send earlier than the approved plan.

Letters: every message has a plain-text part and an HTML part rendered from the same template. A recipient's
"html_vars" replace placeholders in the HTML part only (links); "vars" fill both. Config "signature" is appended
to every step: its "text" to the plain part, its "image" (inline, by Content-ID) to the HTML part, or the text
when there is no image. A step's "attachments" are paths or {"path", "name"} objects; a path may hold
[PLACEHOLDERS] filled from the recipient's vars, so every recipient can get its own file. The approval hash
covers subject, both parts, the signature image and every attachment.

Credentials come from pflege-board/.env (MAILBOX_<n>_{ADDRESS,PASSWORD,SMTP_HOST,SMTP_PORT,IMAP_HOST,IMAP_PORT});
the sender is found by address. Nothing here prints a credential.
"""
import argparse
import base64
import email
import email.policy
import hashlib
import html
import imaplib
import json
import os
import pwd
import random
import re
import signal
import smtplib
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mailer_announce  # noqa: E402

ENV = Path(__file__).resolve().parent.parent / ".env"
DARIA = "daria.s@pflege-connect.work"
DARIA_INBOX = "/usr/local/sbin/daria-inbox"          # root-owned, the claude user's one sudo command (TASK-111.10)
STEP_AFTER = re.compile(r"^(\d+)(m|h|d|bd)$")
PLACEHOLDER = re.compile(r"\[[A-ZÄÖÜ_/]+\]")


class MailerError(Exception):
    pass


class DeliveryHalt(MailerError):
    """A halt from the recipients' side (bounce, complaint, stop request, SMTP refusal): only a new plan continues."""


# ---------- config and files ----------

def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    cfg["_dir"] = path.parent
    for k in ("recipients", "allowlist", "ledger", "batches", "approvals"):
        cfg[k] = (path.parent / cfg[k]).resolve()
    if cfg.get("do_not_contact"):
        cfg["do_not_contact"] = (path.parent / cfg["do_not_contact"]).resolve()
    for step in cfg["cadence"]:
        step["template"] = (path.parent / step["template"]).resolve()
        step["attachments"] = [a if isinstance(a, dict) else {"path": a} for a in step.get("attachments", [])]   # resolved per recipient
        if step is not cfg["cadence"][0] and not STEP_AFTER.match(step["after"]):
            raise MailerError(f"step {step['step']}: 'after' must look like 5bd, 3d, 2h or 10m, got {step['after']!r}")
    sig = cfg.get("signature")
    if sig and sig.get("image"):
        sig["image"] = (path.parent / sig["image"]).resolve()
        sig["sha256"] = hashlib.sha256(sig["image"].read_bytes()).hexdigest()
        sig["cid"] = f"sig-{sig['sha256'][:16]}@{cfg['sender'].split('@')[1]}"
    ann = cfg.get("announce")
    if ann:
        ann["template"] = (path.parent / ann["template"]).resolve()
        if ann.get("notes"):
            ann["notes"] = (path.parent / ann["notes"]).resolve()
    cfg["operators"] = [a.lower() for a in cfg.get("operators", [])]
    cfg["tz"] = ZoneInfo(cfg["tz"])
    cfg["holidays"] = {date.fromisoformat(d) for d in cfg["holidays"]}
    return cfg


def read_ledger(cfg):
    p = cfg["ledger"]
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def append_ledger(cfg, event):
    event = {"ts": now_in(cfg).isoformat(timespec="seconds"), "campaign": cfg["campaign"], **event}
    with cfg["ledger"].open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def allowlist(cfg):
    return {l.strip().lower() for l in cfg["allowlist"].read_text().splitlines() if l.strip() and not l.startswith("#")}


def load_env():
    e = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("export "):
            s = s[7:]
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def mailbox(address):
    e = load_env()
    for n in range(1, int(e["MAILBOX_COUNT"]) + 1):
        if e.get(f"MAILBOX_{n}_ADDRESS", "").lower() == address.lower():
            return {k: e.get(f"MAILBOX_{n}_{k}") for k in ("ADDRESS", "PASSWORD", "SMTP_HOST", "SMTP_PORT", "IMAP_HOST", "IMAP_PORT")}
    raise MailerError(f"no MAILBOX_<n>_ADDRESS={address} in .env: add this mailbox's SMTP/IMAP credentials first")


# ---------- time ----------

def now_in(cfg):
    return datetime.now(cfg["tz"])


def sleep(seconds):
    time.sleep(seconds)


def odd_minute(t):
    """Ivan, 2026-09-28: no letter goes at a round time; a minute divisible by 5 counts as round."""
    return t.minute % 5 != 0


def after_pause(prev, lo, hi):
    """prev plus a whole-second pause drawn uniformly from [lo, hi] among the pauses that end on an odd minute."""
    offsets = [s for s in range(int(lo), int(hi) + 1) if odd_minute(prev + timedelta(seconds=s))]
    if not offsets:
        raise MailerError(f"no pause of {lo}-{hi} s after {prev:%H:%M:%S} ends on an odd minute: widen pause_seconds")
    return prev + timedelta(seconds=random.choice(offsets))


def schedule(cfg, start, n):
    """Send times of n letters from `start`: the first a pause after it, each next one a pause after the previous;
    every one inside the send window."""
    lo, hi = cfg["pause_seconds"]
    times = [after_pause(start, lo, hi)]
    while len(times) < n:
        times.append(after_pause(times[-1], lo, hi))
    outside = [t for t in times if not in_window(cfg, t)]
    if outside:
        raise MailerError(f"{len(outside)} of {n} send times fall outside the send window ({outside[0]:%a %Y-%m-%d %H:%M} first): "
                          "start earlier")
    return times


def in_window(cfg, t):
    w = cfg["window"]
    return (t.isoweekday() in w["weekdays"] and t.date() not in cfg["holidays"]
            and w["from"] <= t.strftime("%H:%M") < w["to"])


def add_after(cfg, t, after):
    n, unit = STEP_AFTER.match(after).groups()
    n = int(n)
    if unit == "m":
        return t + timedelta(minutes=n)
    if unit == "h":
        return t + timedelta(hours=n)
    if unit == "d":
        return t + timedelta(days=n)
    d = t
    while n:
        d += timedelta(days=1)
        if d.isoweekday() in cfg["window"]["weekdays"] and d.date() not in cfg["holidays"]:
            n -= 1
    return d


# ---------- state per recipient ----------

def recipient_state(cfg, rid, ledger):
    """Return (sent events in step order, stop event or None) for one recipient."""
    sent = [e for e in ledger if e["event"] == "sent" and e["recipient_id"] == rid]
    stop = next((e for e in ledger if e["event"] == "inbound" and e.get("recipient_id") == rid and e["kind"] in cfg["stop_on"]), None)
    order = {s["step"]: i for i, s in enumerate(cfg["cadence"])}
    return sorted(sent, key=lambda e: order[e["step"]]), stop


def next_due(cfg, rid, ledger):
    """(step index, due datetime) of the recipient's next step, or (None, reason)."""
    sent, stop = recipient_state(cfg, rid, ledger)
    if stop:
        return None, f"stopped: {stop['kind']} {stop['ts'][:16]}"
    if len(sent) == len(cfg["cadence"]):
        return None, "sequence complete"
    i = len(sent)
    if i == 0:
        return 0, None
    prev = datetime.fromisoformat(sent[-1]["sent_at"]).astimezone(cfg["tz"])
    return i, add_after(cfg, prev, cfg["cadence"][i]["after"])


# ---------- render ----------

def fill(text, rec, where):
    for k, v in rec["vars"].items():
        text = text.replace(f"[{k}]", v)
    left = sorted(set(PLACEHOLDER.findall(text)))
    if left:
        raise MailerError(f"{rec['id']} {where}: placeholders without a value: {left}")
    return text


URL = re.compile(r"https?://[^\s<>\"]+")


def html_part(template_body, rec, sig):
    """The template body as HTML: text escaped, bare URLs linked, rec["html_vars"] put in raw, blank lines as paragraphs."""
    out = []
    for tok in re.split(r"(\[[A-ZÄÖÜ_/]+\])", template_body):
        key = tok[1:-1] if PLACEHOLDER.fullmatch(tok) else None
        if key and key in rec.get("html_vars", {}):
            out.append(rec["html_vars"][key])
        else:
            out.append(URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', html.escape(rec["vars"][key] if key else tok, quote=False)))
    paras = "".join(f'<p style="margin:0 0 14px">{p.strip().replace(chr(10), "<br>" + chr(10))}</p>\n' for p in "".join(out).split("\n\n") if p.strip())
    if sig and sig.get("image"):
        paras += (f'<img src="cid:{sig["cid"]}" width="{sig["width"]}" height="{sig["height"]}" alt="{html.escape(sig["alt"])}" '
                  f'style="display:block;border:0;max-width:100%;height:auto">\n')
    elif sig:
        paras += f'<p style="margin:0">{html.escape(sig["text"]).replace(chr(10), "<br>")}</p>\n'
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#23201E">\n{paras}</div></body></html>\n')


def render(step, rec, sig=None):
    """(subject, plain body, HTML body) of one step for one recipient."""
    raw = step["template"].read_text()
    head, _, body = fill(raw, rec, f"step {step['step']}").partition("\n\n")
    if not head.startswith("Betreff: "):
        raise MailerError(f"template {step['template']} must start with 'Betreff: ' and a blank line")
    plain = body.strip() + ("\n" + sig["text"] if sig else "") + "\n"
    return head[len("Betreff: "):].strip(), plain, html_part(raw.partition("\n\n")[2].strip(), rec, sig)


def body_sha(item):
    h = hashlib.sha256((item["subject"] + "\n\n" + item["body"]).encode())
    if item.get("html"):
        h.update(item["html"].encode())
    for a in item.get("inline", []) + item["attachments"]:
        h.update(a["sha256"].encode())
    return h.hexdigest()


def suppressed(cfg, addresses):
    """Addresses on sales_brain's suppression list (read-only), and addresses on our own do-not-contact list: the JSON
    file named by "do_not_contact", entries {"match": an address or a whole domain, "clinic", "by", "date", "why"}."""
    db = cfg.get("suppression_db")
    hits = set()
    if db:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for a in addresses:
            if con.execute("select 1 from suppression_list where lower(value)=?", (a.lower(),)).fetchone():
                hits.add(a)
        con.close()
    if cfg.get("do_not_contact"):
        match = {e["match"].lower() for e in json.loads(cfg["do_not_contact"].read_text())}
        hits |= {a for a in addresses if a.lower() in match or a.lower().split("@")[1] in match}
    return hits


# ---------- plan ----------

def render_item(cfg, rec, step, to, cc):
    """One cadence step's letter to one recipient, without send time, thread headers or hash."""
    sig = cfg.get("signature")
    subject, body, html_body = render(step, rec, sig)
    attachments = []
    for a in step["attachments"]:
        p = (cfg["_dir"] / fill(a["path"], rec, f"step {step['step']} attachment")).resolve()
        if not p.is_file():
            raise MailerError(f"{rec['id']} step {step['step']}: attachment {p} does not exist")
        attachments.append({"path": str(p), "name": a.get("name") or step.get("attachment_names", {}).get(p.name, p.name),
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return {"recipient_id": rec["id"], "clinic": rec["clinic"], "step": step["step"], "subject": subject, "body": body,
            "html": html_body, "to": list(to), "cc": list(cc), "attachments": attachments,
            "inline": [{"path": str(sig["image"]), "name": sig["image"].name, "cid": sig["cid"], "sha256": sig["sha256"]}]
                      if sig and sig.get("image") else []}


def plan(cfg, now, only=None, announce_at=None, start_at=None):
    ledger = read_ledger(cfg)
    recipients = json.loads(cfg["recipients"].read_text())
    unknown = set(only or []) - {rec["id"] for rec in recipients}
    if unknown:
        raise MailerError(f"--only: no recipient with id {sorted(unknown)} in {cfg['recipients']}")
    if announce_at:
        if not (cfg.get("announce") and cfg["operators"] and cfg.get("classifier")):
            raise MailerError("--announce-at needs \"announce\", \"operators\" and \"classifier\" in the config")
        if announce_at <= now:
            raise MailerError(f"--announce-at {announce_at:%Y-%m-%d %H:%M} is not in the future")
        window = timedelta(minutes=cfg["announce"]["window_minutes"])
        announce_at = announce_at.astimezone(cfg["tz"])
        start_at = (start_at or announce_at + window).astimezone(cfg["tz"])
        if start_at < announce_at + window:
            raise MailerError(f"--start-at {start_at:%a %d.%m %H:%M} is less than {window} after the announcement at "
                              f"{announce_at:%a %d.%m %H:%M}")
    elif start_at:
        raise MailerError("--start-at needs --announce-at")
    test = cfg.get("redirect_to_allowlist", False)
    alist = sorted(allowlist(cfg) - set(cfg["operators"])) if test else []   # an operator's mail is a command, never a clinic's answer
    if test and not alist:
        raise MailerError(f"{cfg['allowlist']} lists no address besides the operators: a test campaign sends only to those")
    items, report = [], []
    for k, rec in enumerate(recipients):
        if only and rec["id"] not in only:
            continue
        i, due = next_due(cfg, rec["id"], ledger)
        if i is None:
            report.append(f"-  {rec['id']} {rec['clinic']}: {due}")
            continue
        if due and now < due:
            report.append(f"-  {rec['id']} {rec['clinic']}: {cfg['cadence'][i]['step']} due {due:%Y-%m-%d %H:%M}")
            continue
        to, cc = list(rec["to"]), list(rec.get("cc", []))
        blocked = suppressed(cfg, to + cc)
        if set(to) & blocked:
            report.append(f"!! {rec['id']} {rec['clinic']}: To on suppression list {sorted(set(to) & blocked)}, not planned")
            continue
        if blocked:
            report.append(f"!! {rec['id']} {rec['clinic']}: Cc on suppression list dropped {sorted(blocked)}")
            cc = [a for a in cc if a not in blocked]
        sent, _ = recipient_state(cfg, rec["id"], ledger)
        steps = list(range(i, len(cfg["cadence"]))) if announce_at else [i]     # a scheduled batch carries the follow-ups too
        for rnd, j in enumerate(steps):
            item = render_item(cfg, rec, cfg["cadence"][j], to, cc)
            if announce_at:
                item["round"] = rnd
            if cfg["cadence"][j].get("in_thread"):
                if rnd == 0 and sent:
                    item["in_reply_to"] = sent[-1]["message_id"]
                    item["references"] = [e["message_id"] for e in sent]
                elif rnd:
                    item["in_thread"] = True            # answers the letter before it, whose Message-ID exists once it went
            if test:
                item["real_to"], item["real_cc"] = item["to"], item["cc"]
                item["to"] = [alist[k % len(alist)]]
                item["cc"] = [alist[(k + 1) % len(alist)]] if cc and len(alist) > 1 else []
            item["sha256"] = body_sha(item)
            items.append(item)
        report.append(f"+  {rec['id']} {rec['clinic']}: {', '.join(cfg['cadence'][j]['step'] for j in steps)} to {', '.join(items[-1]['to'])}"
                      + (f" cc {', '.join(items[-1]['cc'])}" if items[-1]["cc"] else ""))
    if only:
        report.append(f"   --only: {len(recipients) - len(set(only))} other recipients not looked at")
    if not items:
        return None, report
    bid = f"{cfg['campaign']}-{now:%Y%m%d-%H%M}"
    batch = {"batch_id": bid, "campaign": cfg["campaign"], "sender": cfg["sender"], "planned_at": now.isoformat(timespec="seconds"),
             "test": test, "items": items}
    cfg["batches"].mkdir(parents=True, exist_ok=True)
    cfg["approvals"].mkdir(parents=True, exist_ok=True)
    if announce_at:
        batch["operators"] = list(cfg["operators"])
        batch["start_at"] = start_at.isoformat(timespec="seconds")
        batch["reports"] = timed(cfg, items, start_at, window)
        batch["announce"] = announcement(cfg, batch, {rec["id"]: rec for rec in recipients}, announce_at)
        report.append(f"   announcement {announce_at:%a %Y-%m-%d %H:%M} to {', '.join(cfg['operators'])} (or when the send starts, "
                      f"if later); first letters from {start_at:%a %d.%m %H:%M}")
        for r in rounds_info(cfg, batch):
            report.append(f"   {r['step']}: {r['n']} letters {r['first']:%a %d.%m %H:%M:%S}-{r['last']:%H:%M:%S}"
                          + (f", report {r['report']:%a %d.%m %H:%M}" if r["report"] else ""))
    (cfg["batches"] / f"{bid}.json").write_text(json.dumps(batch, ensure_ascii=False, indent=1))
    ann, reps = batch.get("announce"), batch.get("reports")
    (cfg["batches"] / f"{bid}.txt").write_text("\n\n".join(
        ([f"=== 0. {ann['clinic']} · {ann['step']}\nFrom: {cfg['sender_name']} <{cfg['sender']}>\nTo: {', '.join(ann['to'])}\n"
          f"Send at: {ann['send_at']} (or when the send starts, if later; at least {cfg['announce']['window_minutes']} min "
          f"before {batch['start_at']})\nSubject: {ann['subject']}\n"
          f"Attachments: {', '.join(a['name'] + ' (' + a['path'] + ')' for a in ann['attachments'])}\n"
          f"sha256: {ann['sha256']}\n\n{ann['body']}"] if ann else [])
        + ([f"=== Reports before the follow-up rounds, to {', '.join(batch['operators'])}; the text is written when each goes, "
            "from the batch's state: who gets the step, who answered or was taken out, one letter as an example\n"
            + "\n".join(f"{r['step']}: {r['send_at']}" for r in reps)] if reps else [])
        + [f"=== {n}. {it['clinic']} · {it['step']}\nFrom: {cfg['sender_name']} <{cfg['sender']}>\nTo: {', '.join(it['to'])}\n"
           + (f"Cc: {', '.join(it['cc'])}\n" if it["cc"] else "")
           + (f"(test copy; real To: {', '.join(it['real_to'])}; real Cc: {', '.join(it['real_cc']) or '-'})\n" if test else "")
           + (f"Send at: {it['send_at']}\n" if it.get("send_at") else "")
           + (f"In-Reply-To: {it['in_reply_to']}\n" if it.get("in_reply_to") else "")
           + ("In-Reply-To: the clinic's letter before this one, set when this one goes\n" if it.get("in_thread") else "")
           + f"Subject: {it['subject']}\nAttachments: {', '.join(a['name'] + ' (' + Path(a['path']).name + ')' for a in it['attachments']) or '-'}\n"
           + f"HTML part: yes; signature image: {', '.join(i['name'] for i in it['inline']) or '-'}\nsha256: {it['sha256']}\n\n{it['body']}"
           for n, it in enumerate(items, 1)]))
    (cfg["approvals"] / f"{bid}.pending.json").write_text(json.dumps(approval_of(batch), ensure_ascii=False, indent=1))
    return bid, report


def timed(cfg, items, start_at, window):
    """Give a scheduled batch's items their send times and return its reports. Round 0 (every clinic's first planned
    step, the same step for all) goes one pause after another from start_at; every later letter goes the cadence's
    "after" past the clinic's letter before it. The report before round r goes as far before that round's first letter
    as start_at minus the window is before round 0's first letter. Sorts the items by send time."""
    firsts = [it for it in items if it["round"] == 0]
    steps = {it["step"] for it in firsts}
    if len(steps) > 1:
        raise MailerError(f"a scheduled batch starts every clinic at the same step; these are at {sorted(steps)}: plan each "
                          "step's clinics with --only")
    for it, t in zip(firsts, schedule(cfg, start_at, len(firsts))):
        it["send_at"] = t
    rounds = max(it["round"] for it in items) + 1
    for rnd in range(1, rounds):
        for it in (x for x in items if x["round"] == rnd):
            prev = next(x for x in items if x["recipient_id"] == it["recipient_id"] and x["round"] == rnd - 1)
            it["send_at"] = add_after(cfg, prev["send_at"], next(s for s in cfg["cadence"] if s["step"] == it["step"])["after"])
    bad = next((it for it in items if not (in_window(cfg, it["send_at"]) and odd_minute(it["send_at"]))), None)
    if bad:
        raise MailerError(f"{bad['clinic']} {bad['step']} would go at {bad['send_at']:%a %d.%m %H:%M}, outside the send window or "
                          "on a minute divisible by 5: change the cadence")
    first0, reports = min(it["send_at"] for it in firsts), []
    for rnd in range(1, rounds):
        its = [it for it in items if it["round"] == rnd]
        at = start_at + (min(it["send_at"] for it in its) - first0) - window
        before = max(it["send_at"] for it in items if it["round"] == rnd - 1)
        if at <= before:
            raise MailerError(f"the {its[0]['step']} report would go at {at:%a %d.%m %H:%M}, before the last letter of the round "
                              f"before it ({before:%a %d.%m %H:%M}): lengthen the cadence")
        reports.append({"round": rnd, "step": its[0]["step"], "send_at": at.isoformat(timespec="seconds"), "to": list(cfg["operators"])})
    items.sort(key=lambda it: it["send_at"])
    for it in items:
        it["send_at"] = it["send_at"].isoformat(timespec="seconds")
    return reports


def rounds_info(cfg, batch):
    """Per round of a scheduled batch: step, its Russian name, letters, first and last send time, and the report's time
    (None for round 0, which the announcement covers)."""
    out = []
    for rnd in sorted({it["round"] for it in batch["items"]}):
        its = [it for it in batch["items"] if it["round"] == rnd]
        times = [datetime.fromisoformat(it["send_at"]) for it in its]
        out.append({"round": rnd, "step": its[0]["step"], "name": step_name(cfg, its[0]["step"], plural=True), "n": len(its),
                    "first": min(times), "last": max(times),
                    "report": next((datetime.fromisoformat(r["send_at"]) for r in batch["reports"] if r["round"] == rnd), None)})
    return out


def notes_html(text):
    """Plain notes as HTML: lines starting with "- " become a list, blank lines separate paragraphs."""
    out, para, items = [], [], []
    for line in text.splitlines() + [""]:
        if line.startswith("- "):
            if para:
                out.append("<p>" + "<br>".join(para) + "</p>")
                para = []
            items.append(f"<li>{html.escape(line[2:].strip(), quote=False)}</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        if line.strip():
            para.append(html.escape(line.strip(), quote=False))
        elif para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para = []
    return "".join(out)


def announcement(cfg, batch, recs, announce_at):
    """The announcement of a scheduled batch: config announce.template filled with the plan, and the plan PDF."""
    items, rows = batch["items"], []
    for n, it in enumerate((x for x in items if x["round"] == 0), 1):
        rows.append({**it, "n": n, "send_at": datetime.fromisoformat(it["send_at"]),
                     "follow_ups": [(x["step"], datetime.fromisoformat(x["send_at"])) for x in items
                                    if x["recipient_id"] == it["recipient_id"] and x["round"]],
                     "greeting": it["body"].split("\n", 1)[0].rstrip(","),
                     "ads": [line[2:].strip() for line in it["body"].splitlines() if line.startswith("– ")],
                     "marked": (recs[it["recipient_id"]].get("meta") or {}).get("marked")})
    info = rounds_info(cfg, batch)
    first, last, end = rows[0]["send_at"], rows[-1]["send_at"], max(r["last"] for r in info)
    wd = mailer_announce.WD
    notes = cfg["announce"]["notes"].read_text().strip() if cfg["announce"].get("notes") else ""
    course = [f"{r['name'].capitalize()} — {wd[r['first'].weekday()]} {r['first']:%d.%m}, {r['first']:%H:%M}–{r['last']:%H:%M}"
              + (f", клиник: {r['n']}." if not r["report"] else
                 ", тем, кто не ответил; предварительный отчёт вам в " + f"{r['report']:%H:%M}"
                 + ("" if r["report"].date() == r["first"].date() else f" {wd[r['report'].weekday()]} {r['report']:%d.%m}") + ".")
              for r in info]
    rec = {"id": "announce",
           "vars": {"KAMPAGNE": cfg["campaign"], "DATUM": f"{wd[first.weekday()]} {first:%d.%m.%Y}",
                    "ANZAHL": str(len(rows)), "ANKUENDIGUNG": f"{announce_at:%H:%M}", "ERSTER": f"{first:%H:%M}",
                    "LETZTER": f"{last:%H:%M}", "FENSTER": str(cfg["announce"]["window_minutes"]), "ABSENDER": cfg["sender"],
                    "OPERATOREN": ", ".join(cfg["operators"]), "NOTIZEN": notes, "ABLAUF": "\n".join(course),
                    "ENDE": f"{wd[end.weekday()]} {end:%d.%m}",
                    "PLAN": "\n".join(f"{r['send_at']:%H:%M}  {r['clinic']} — {', '.join(r['to'])}" for r in rows)},
           "html_vars": {"NOTIZEN": notes_html(notes),
                         "PLAN": "<br>\n".join(f'<span style="font-family:monospace">{r["send_at"]:%H:%M}</span>&nbsp; '
                                               f'<b>{html.escape(r["clinic"], quote=False)}</b> — {html.escape(", ".join(r["to"]), quote=False)}'
                                               for r in rows)}}
    subject, body, html_body = render({"step": "announce", "template": cfg["announce"]["template"]}, rec)
    pdf, _ = mailer_announce.render_pdf(cfg, batch, rows, notes_html(notes), rows[0],
                                        {"window_minutes": cfg["announce"]["window_minutes"], "poll_seconds": cfg["command_poll_seconds"],
                                         "rounds": info, "end": end},
                                        cfg["batches"] / f"{batch['batch_id']}.announce.pdf")
    ann = {"recipient_id": "announce", "clinic": "Анонс", "step": "announce", "subject": subject, "body": body, "html": html_body,
           "to": list(cfg["operators"]), "cc": [], "inline": [], "send_at": announce_at.isoformat(timespec="seconds"),
           "attachments": [{"path": str(pdf), "name": f"Plan_{cfg['campaign']}_{first:%Y-%m-%d}.pdf",
                            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}]}
    ann["sha256"] = body_sha(ann)
    return ann


def approval_of(batch):
    ap = {"batch_id": batch["batch_id"], "items": [{"recipient_id": it["recipient_id"], "step": it["step"], "to": it["to"], "cc": it["cc"],
                                                    "sha256": it["sha256"], **{k: it[k] for k in ("send_at", "round") if k in it}}
                                                   for it in batch["items"]]}
    if batch.get("announce"):
        a = batch["announce"]
        ap["announce"] = {"send_at": a["send_at"], "to": a["to"], "sha256": a["sha256"]}
        ap["operators"] = batch["operators"]
        ap["start_at"] = batch["start_at"]
        ap["reports"] = batch["reports"]
    return ap


# ---------- send ----------

def guard(cfg, batch, live):
    messages = batch["items"] + ([batch["announce"]] if batch.get("announce") else [])
    for it in messages:
        if body_sha(it) != it["sha256"]:
            raise MailerError(f"{batch['batch_id']}: {it['recipient_id']} was edited after planning (sha256 differs)")
    if not live:
        allowed = allowlist(cfg)
        outside = sorted({a for it in messages for a in it["to"] + it["cc"] if a.lower() not in allowed}
                         | {a for a in batch.get("operators", []) if a.lower() not in allowed})
        if outside:
            raise MailerError(f"{batch['batch_id']}: not in the allowlist {outside}; real recipients need an approval and --live")
        return
    ap = cfg["approvals"] / f"{batch['batch_id']}.json"
    if not ap.exists():
        raise MailerError(f"no approval {ap}: read the batch .txt, then rename {batch['batch_id']}.pending.json to {batch['batch_id']}.json")
    if json.loads(ap.read_text()) != approval_of(batch):
        raise MailerError(f"approval {ap} does not match batch {batch['batch_id']} exactly")


class MailPolicy(email.policy.EmailPolicy):
    """email.policy.default, except that a non-ASCII Subject is encoded by email.header.Header. The default folding can
    end an encoded word right before a space, and the reader drops that space ("первыеписьма")."""

    def fold(self, name, value):
        if name.lower() == "subject" and not str(value).isascii():
            return f"{name}: {Header(str(value), 'utf-8', header_name=name).encode(linesep=self.linesep)}{self.linesep}"
        return super().fold(name, value)

    def fold_binary(self, name, value):
        if name.lower() == "subject" and not str(value).isascii():
            return self.fold(name, value).encode("ascii")
        return super().fold_binary(name, value)


POLICY = MailPolicy()


def build_message(cfg, it):
    m = EmailMessage(policy=POLICY)
    m["From"] = formataddr((cfg["sender_name"], cfg["sender"]))
    m["To"] = ", ".join(it["to"])
    if it["cc"]:
        m["Cc"] = ", ".join(it["cc"])
    m["Subject"] = it["subject"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=cfg["sender"].split("@")[1])
    if it.get("in_reply_to"):
        m["In-Reply-To"] = it["in_reply_to"]
        m["References"] = " ".join(it["references"])
    m.set_content(it["body"])
    if it.get("html"):
        m.add_alternative(it["html"], subtype="html")
        html_msg = m.get_payload()[-1]
        for img in it.get("inline", []):
            data = Path(img["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != img["sha256"]:
                raise MailerError(f"signature image {img['path']} changed after planning")
            html_msg.add_related(data, maintype="image", subtype=Path(img["path"]).suffix.lstrip(".").lower().replace("jpg", "jpeg"),
                                 cid=f"<{img['cid']}>", disposition="inline", filename=img["name"])
    for a in it["attachments"]:
        data = Path(a["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != a["sha256"]:
            raise MailerError(f"attachment {a['path']} changed after planning")
        m.add_attachment(data, maintype="application", subtype="pdf" if a["name"].lower().endswith(".pdf") else "octet-stream",
                         filename=a["name"])
    return m


def smtp_send(box, msg):
    """Send one message through the box's SMTP server; return the refused recipients (empty when all were taken)."""
    port = int(box["SMTP_PORT"])
    with (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(box["SMTP_HOST"], port, timeout=60) as s:
        if port != 465:
            s.starttls()
        s.login(box["ADDRESS"], box["PASSWORD"])
        return s.send_message(msg)


def check_inbox(cfg, box):
    """Fail before anything is sent when the inbox cannot be read."""
    via = cfg.get("watch_via", "imap")
    if via == "graph":
        graph_token(cfg)
    elif via == "daria-inbox":
        if cfg["sender"].lower() != DARIA:
            raise MailerError(f"watch_via daria-inbox reads only {DARIA}, not {cfg['sender']}")
        list(helper_messages(cfg, now_in(cfg), set()))
    elif not box["IMAP_HOST"]:
        raise MailerError(f"{cfg['sender']} has no IMAP host in .env: the batch cannot watch for bounces and stop replies, so it does not start")
    else:
        imap_login(box).logout()


def send(cfg, batch_id, live, watch_inbox=True):
    if live and not watch_inbox:
        raise MailerError("--no-watch is for allowlist tests only: a live batch reads the inbox between sends and halts on bounces, complaints and stop replies")
    batch = json.loads((cfg["batches"] / f"{batch_id}.json").read_text())
    guard(cfg, batch, live)
    if batch.get("announce"):
        if not watch_inbox:
            raise MailerError("--no-watch does not apply to a scheduled batch: it reads the operators' stop commands from the inbox")
        return send_scheduled(cfg, batch, live)
    box = mailbox(cfg["sender"])
    started = datetime.now(cfg["tz"]).isoformat(timespec="seconds")
    if watch_inbox:
        check_inbox(cfg, box)
        watch(cfg, box)               # replies since the last run take their senders out of this batch before anything goes
        halt_on_inbound(cfg, started)
    else:
        print(f"WARNING --no-watch: {cfg['sender']}'s inbox is NOT read during this batch. Replies, bounces, complaints and stop "
              "requests are neither logged nor halt it; check the inbox by hand.", file=sys.stderr)
    for n, it in enumerate(batch["items"]):
        ledger = read_ledger(cfg)
        if any(e["event"] == "sent" and e["recipient_id"] == it["recipient_id"] and e["step"] == it["step"] for e in ledger):
            print(f"skip {it['recipient_id']} {it['step']}: already sent")
            continue
        _, stop = recipient_state(cfg, it["recipient_id"], ledger)
        if stop:
            print(f"skip {it['recipient_id']} {it['step']}: {stop['kind']} arrived after planning")
            continue
        now = datetime.now(cfg["tz"])
        if not in_window(cfg, now):
            raise MailerError(f"outside the send window at {now:%a %Y-%m-%d %H:%M}; {len(batch['items']) - n} left, rerun send inside the window")
        msg = build_message(cfg, it)
        refused = smtp_send(box, msg)
        ev = append_ledger(cfg, {"event": "sent", "batch_id": batch_id, "mode": "live" if live else "allowlist", "inbox_watched": watch_inbox,
                                 "recipient_id": it["recipient_id"], "clinic": it["clinic"], "step": it["step"],
                                 "to": it["to"], "cc": it["cc"], "real_to": it.get("real_to"), "message_id": msg["Message-ID"],
                                 "subject": it["subject"], "sha256": it["sha256"], "sent_at": datetime.now(cfg["tz"]).isoformat(timespec="seconds"),
                                 "smtp_refused": {k: [v[0], v[1].decode("utf-8", "replace")] for k, v in refused.items()}})
        print(f"sent {n + 1}/{len(batch['items'])} {it['recipient_id']} {it['step']} to {', '.join(it['to'])} {ev['message_id']}")
        if refused:
            raise MailerError(f"HALT: SMTP refused {sorted(refused)}")
        if n + 1 < len(batch["items"]):
            time.sleep(random.uniform(*cfg["pause_seconds"]))
        if not watch_inbox:
            continue
        watch(cfg, box)
        halt_on_inbound(cfg, started)
    print("batch done", batch_id + ("" if watch_inbox else " (inbox NOT watched)"))


def halt_on_inbound(cfg, started):
    started = datetime.fromisoformat(started) if isinstance(started, str) else started
    halts = [e for e in read_ledger(cfg) if e["event"] == "inbound" and e["kind"] in cfg["halt_on"]
             and datetime.fromisoformat(e["ts"]) >= started]
    if halts:
        raise DeliveryHalt("HALT: " + "; ".join(f"{e['kind']} from {e['from']} ({e.get('recipient_id')})" for e in halts))


# ---------- scheduled batch: announcement, reports, send times, operator commands ----------

def when(iso):
    """'вт 29.09 09:02' for an ISO time, in its own UTC offset."""
    return mailer_announce.when(datetime.fromisoformat(iso))


def step_name(cfg, step, plural=False):
    """A cadence step as the operators call it: the first letter(s), фоллоу-ап 1, фоллоу-ап 2 ..."""
    i = next(k for k, s in enumerate(cfg["cadence"]) if s["step"] == step)
    return ("первые письма" if plural else "первое письмо") if i == 0 else f"фоллоу-ап {i}"


def sent_event(ledger, it):
    return next((e for e in ledger if e["event"] == "sent" and e["recipient_id"] == it["recipient_id"] and e["step"] == it["step"]), None)


def halt_event(ledger, bid):
    """The batch's halt in force: its last "halt" event, unless a "resumed" event came after it."""
    halt = None
    for e in ledger:
        if e.get("batch_id") == bid and e["event"] in ("halt", "resumed"):
            halt = e if e["event"] == "halt" else None
    return halt


def reported(ledger, bid, rnd):
    return any(e["event"] == "report" and e.get("batch_id") == bid and e["round"] == rnd for e in ledger)


STOP_RU = {"reply": "клиника ответила", "stop": "клиника просит больше не писать", "bounce": "недоставка (bounce)",
           "complaint": "жалоба на спам"}


def item_states(cfg, batch, ledger=None, ignore_halt=False):
    """(item, state, Russian detail) per letter; state is sent, skipped, stopped (the clinic answered), halted or pending.
    A skip or an answer takes out every letter of that clinic that has not gone yet."""
    ledger = read_ledger(cfg) if ledger is None else ledger
    bid = batch["batch_id"]
    halt = None if ignore_halt else halt_event(ledger, bid)
    skips = {e["recipient_id"]: e for e in ledger if e["event"] == "skip" and e.get("batch_id") == bid}
    out = []
    for it in batch["items"]:
        sent, skip = sent_event(ledger, it), skips.get(it["recipient_id"])
        _, stop = recipient_state(cfg, it["recipient_id"], ledger)
        if sent:
            out.append((it, "sent", ("ушло " if it["step"] == cfg["cadence"][0]["step"] else "ушёл ") + when(sent["sent_at"])))
        elif skip:
            out.append((it, "skipped", f"убрано по письму {skip['by']} ({when(skip['ts'])})"))
        elif stop:
            out.append((it, "stopped", f"{STOP_RU.get(stop['kind'], stop['kind'])} ({when(stop['ts'])})"))
        elif halt:
            out.append((it, "halted", "рассылка остановлена"))
        else:
            out.append((it, "pending", when(it["send_at"])))
    return out


def by_clinic(cfg, batch, ledger=None):
    """[(recipient id, clinic, [(item, state, detail)] in step order)] in plan order."""
    out = {}
    for it, s, d in item_states(cfg, batch, ledger):
        out.setdefault(it["recipient_id"], (it["recipient_id"], it["clinic"], []))[2].append((it, s, d))
    return list(out.values())


def clinic_line(cfg, states):
    """One clinic's letters: what went and when, what is planned and when, or why nothing (more) goes."""
    parts, why = [], None
    for it, s, d in states:
        if s in ("sent", "pending"):
            parts.append(f"{step_name(cfg, it['step'])} {d}")
        elif why is None:
            why = d
    if why:
        parts.append(("дальше ничего не уйдёт: " if parts else "ничего не уйдёт: ") + why)
    return "; ".join(parts)


def state_text(cfg, batch, ledger=None):
    return (f"Состояние на {mailer_announce.when(now_in(cfg))}:\n"
            + "\n".join(f"  {clinic} — {clinic_line(cfg, states)}" for _, clinic, states in by_clinic(cfg, batch, ledger)))


def clinic_list(cfg, batch):
    """The batch's clinics as the classifier sees them."""
    return [{"id": rid, "clinic": clinic, "letters": clinic_line(cfg, states)} for rid, clinic, states in by_clinic(cfg, batch)]


def left_to_send(cfg, batch):
    """What is left, for the classifier: "first letters and follow-ups", "first letters", "only follow-ups" or "nothing"."""
    rounds = {it["round"] for it, s, _ in item_states(cfg, batch) if s == "pending"}
    if not rounds:
        return "nothing"
    if 0 not in rounds:
        return "only follow-ups"
    return "first letters and follow-ups" if any(it["round"] for it in batch["items"]) else "first letters"


def commands_help(cfg):
    return (f"Команды — письмом на {cfg['sender']} с адреса {' или '.join(cfg['operators'])}: «стоп» или «отмена» — остановить всю "
            "рассылку, фоллоу-апы тоже; «не отправлять в <клинику>» — убрать клинику, ей больше ничего не уйдёт; «статус». "
            "Остальное (другое время, другой текст, продолжить после стопа) — только через оператора.")


def operator_mail(cfg, batch, subject, body, in_reply_to=None, references=None, auto="auto-generated"):
    m = EmailMessage(policy=POLICY)
    m["From"] = formataddr((cfg["sender_name"], cfg["sender"]))
    m["To"] = ", ".join(batch["operators"])
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=cfg["sender"].split("@")[1])
    m["Auto-Submitted"] = auto
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = " ".join(references or [in_reply_to])
    m.set_content(body)
    return m


def notify(cfg, box, batch, kind, title, text, **extra):
    """A notice to the operators (round, halt, resumed, done), logged as a "notice" event."""
    m = operator_mail(cfg, batch, f"Рассылка {cfg['campaign']}: {title}", f"{text}\n\n{state_text(cfg, batch)}\n\nDaria\n")
    refused = smtp_send(box, m)
    append_ledger(cfg, {"event": "notice", "batch_id": batch["batch_id"], "kind": kind, "title": title, **extra,
                        "to": batch["operators"], "message_id": m["Message-ID"], "smtp_refused": sorted(refused)})
    if refused:
        raise MailerError(f"SMTP refused the {kind} notice for {sorted(refused)}")
    print(f"notice {kind} to {', '.join(batch['operators'])}: {title}")


def send_report(cfg, box, batch, rep):
    """The report before a follow-up round: who gets it and when, who does not and why, and one letter of it."""
    states = [(it, s, d) for it, s, d in item_states(cfg, batch) if it["round"] == rep["round"]]
    going = [it for it, s, _ in states if s == "pending"]
    first, last = (datetime.fromisoformat(going[k]["send_at"]) for k in (0, -1))
    name, ex, day = step_name(cfg, rep["step"]), going[0], f"{mailer_announce.WD[first.weekday()]} {first:%d.%m}"
    span = f"в {first:%H:%M}" if len(going) == 1 else f"с {first:%H:%M} до {last:%H:%M}, по одному"
    lines = [f"{name.capitalize()} — {day}, {span}: ответ в той же переписке на прошлое "
             f"письмо, только тем, кто не ответил. Клиник: {len(going)} из {len(states)}.", "", "Уйдёт:"]
    lines += [f"  {datetime.fromisoformat(it['send_at']):%H:%M}  {it['clinic']} — {', '.join(it['to'])}"
              + (f", копия {', '.join(it['cc'])}" if it["cc"] else "") for it in going]
    if len(going) < len(states):
        lines += ["", "Не уйдёт:"] + [f"  {it['clinic']} — {d}" for it, s, d in states if s != "pending"]
    lines += ["", f"Текст у всех один, обращение и вакансия у каждой клиники свои. Пример — {ex['clinic']}:", "",
              f"Тема: {ex['subject']}", "", ex["body"].rstrip(), "",
              f"До {first:%H:%M} можно остановить рассылку или убрать клинику; команды принимаются и потом, "
              "пока идёт рассылка.", commands_help(cfg), "", "Daria"]
    m = operator_mail(cfg, batch, f"Рассылка {cfg['campaign']}: {name} {day} с {first:%H:%M}, предварительный отчёт", "\n".join(lines) + "\n")
    refused = smtp_send(box, m)
    append_ledger(cfg, {"event": "report", "batch_id": batch["batch_id"], "round": rep["round"], "step": rep["step"],
                        "to": batch["operators"], "going": [it["recipient_id"] for it in going], "message_id": m["Message-ID"],
                        "sent_at": now_in(cfg).isoformat(timespec="seconds"), "smtp_refused": sorted(refused)})
    print(f"report {rep['step']} to {', '.join(batch['operators'])}: {len(going)} of {len(states)} clinics")
    if refused:
        raise MailerError(f"SMTP refused the {rep['step']} report for {sorted(refused)}")


def round_done(cfg, box, batch, rnd):
    """After the last letter of a round that is not the last one: what went, what comes next and when."""
    states = item_states(cfg, batch)
    mine = [s for it, s, _ in states if it["round"] == rnd]
    nxt = next(r for r in batch["reports"] if r["round"] == rnd + 1)
    first = min(it["send_at"] for it, s, _ in states if it["round"] == rnd + 1 and s == "pending")
    name = step_name(cfg, next(it["step"] for it in batch["items"] if it["round"] == rnd), plural=True)
    n = mine.count("sent")
    notify(cfg, box, batch, "round", f"{name} — ушло {n} из {len(mine)}",
           f"{name.capitalize()} — ушло {n} из {len(mine)}. Дальше {step_name(cfg, nxt['step'])}: предварительный отчёт "
           f"{when(nxt['send_at'])}, письма с {when(first)}. Команды принимаются всё это время.", round=rnd)


COMMAND_INTENTS = ("stop", "skip", "status", "other_command", "not_command")
COMMAND_SYSTEM = """You sort one email that an operator wrote to the mailbox of an email campaign while a batch of letters to clinics is announced or being sent. A batch sends every clinic a first letter and then follow-ups in the same thread on later days, until the clinic answers. Operators write briefly and informally, in Russian, Ukrainian, German or English. You get the subject, the operator's own new words (quoted history is removed), what is left to send ("left": "first letters and follow-ups", "first letters", "only follow-ups" or "nothing") and the list of clinics in the batch, each with its letters: what went and when, what is planned and when, or why nothing more goes.

Decide what the operator wants from this mailing:
- "stop": stop, cancel, abort, pause or hold the whole mailing, or send nothing more to anyone ("стоп", "отмена", "остановить рассылку", "не отправляй", "подожди, не запускай", "stop", "cancel", "abbrechen"). Dropping all follow-ups for everyone is "stop" when "left" is "only follow-ups", because then nothing else is left; while first letters are left it is "other_command".
- "skip": send nothing more to one or more particular clinics while the rest continues ("не отправляй в Weiden", "убери Эрлер", "без Байройта", "Эрлер больше не пиши"). recipient_ids lists the ids of exactly those clinics.
- "status": asks what has been sent or what is still planned.
- "other_command": wants any other change to the mailing: other times, other text, other or more recipients, only some steps for everyone (for example no second follow-up, or no follow-ups while first letters are still planned), resume or restart after a stop, send now, faster or slower.
- "not_command": everything else: thanks, agreement, comments, questions that are not about the mailing's state.
A message that wants everything stopped is "stop" even if it says more. A message that negates a stop ("не останавливай", "не надо отменять") is not "stop". A request to stop only some clinics is "skip".
Only an explicit instruction changes the mailing. A question ("зачем", "почему", "why", "warum"), a doubt or a comment about a clinic is "not_command" even when it names clinics: "skip" needs an explicit request not to send to them.

Answer with one JSON object and nothing else: {"intent": "stop|skip|status|other_command|not_command", "recipient_ids": [], "why": "one short sentence in Russian"}"""


def classify_command(cfg, subject, text, clinics, left=None):
    """{"intent", "recipient_ids" (known ids only), "unknown_ids", "why"} for one operator mail, from the `claude` CLI.
    As root (Ivan's sudo run) the CLI runs as classifier.run_as, whose login it uses. Any failure raises.
    --strict-mcp-config and --disable-slash-commands keep the account's MCP connectors and skills out of the request:
    with them loaded one call carried about 125k tokens of tool definitions and some failed "Prompt is too long"
    (2026-09-28); without them about 400."""
    c = cfg["classifier"]
    cmd = [c["claude_bin"], "-p", "--restricted", "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
           "--output-format", "json", "--no-session-persistence", "--model", c["model"], "--system-prompt", COMMAND_SYSTEM]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDECODE", "CLAUDE_CODE"))}
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", c["run_as"], "--"] + cmd
        env["HOME"] = pwd.getpwnam(c["run_as"]).pw_dir      # its login lives there; sudo may have set HOME=/root
    try:
        p = subprocess.run(cmd, input=json.dumps({"subject": subject, "text": text, "left": left, "clinics": clinics}, ensure_ascii=False),
                           capture_output=True, text=True, timeout=c["timeout_seconds"], env=env, cwd="/")
    except subprocess.TimeoutExpired:
        raise MailerError(f"classifier: no answer within {c['timeout_seconds']} s")
    except FileNotFoundError as e:
        raise MailerError(f"classifier: {e.filename} not found")
    if p.returncode:
        raise MailerError(f"classifier: {Path(c['claude_bin']).name} exited {p.returncode}: {(p.stderr or p.stdout).strip()[-400:]}")
    try:
        envelope = json.loads(p.stdout)
        if envelope.get("is_error"):
            raise MailerError(f"classifier reported an error: {envelope.get('result')!r}")
        found = re.search(r"\{.*\}", envelope.get("result") or "", re.S)
        out = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError as e:
        raise MailerError(f"classifier: not JSON ({e}): {p.stdout[:300]!r}")
    if not isinstance(out, dict) or out.get("intent") not in COMMAND_INTENTS:
        raise MailerError(f"classifier: unexpected answer {envelope.get('result')!r}")
    known = {k["id"] for k in clinics}
    ids = [str(i) for i in out.get("recipient_ids") or []]
    return {"intent": out["intent"], "recipient_ids": [i for i in ids if i in known], "unknown_ids": [i for i in ids if i not in known],
            "why": str(out.get("why") or "")}


QUOTE_HEAD = re.compile(r"^\s*(-{2,}\s*(original|ursprüngliche|исходное|пересылаемое|forwarded)|_{8,}\s*$|on .{5,200} wrote:\s*$|am .{5,200} schrieb)", re.I)


def fresh_text(cfg, msg):
    """The operator's own words: the plain part (else the HTML part without tags), without quoted lines, cut at the
    quoted history (a quote header, or the first line that names the sender box)."""
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    text = body.get_content()
    if body.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style|blockquote).*?</\1>", "", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)))
    out = []
    for line in text.splitlines():
        if QUOTE_HEAD.match(line) or cfg["sender"].lower() in line.lower():
            break
        if not line.startswith(">"):
            out.append(line)
    return "\n".join(out).strip()


def automatic(msg):
    return (str(msg.get("Auto-Submitted", "no")).lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond")
            or str(msg.get("Precedence", "")).lower() in ("auto_reply", "bulk", "junk") or AUTO_SUBJ.match(str(msg.get("Subject", ""))))


def do_stop(cfg, batch, frm, mid):
    states = [s for _, s, _ in item_states(cfg, batch)]
    sent, pending = states.count("sent"), states.count("pending")
    if not pending:
        return "Больше ничего не запланировано, останавливать нечего."
    append_ledger(cfg, {"event": "halt", "batch_id": batch["batch_id"], "kind": "operator", "by": frm, "command_id": mid,
                        "reason": "operator stop"})
    after = (" Процесс рассылки завершён, команды письмом больше не принимаются; продолжить можно только новым планом и "
             "одобрением Ивана.")
    if not sent:
        return "Рассылка отменена: ни одно письмо клиникам не ушло и не уйдёт, фоллоу-апы тоже." + after
    return f"Рассылка остановлена. Писем ушло: {sent}; не уйдёт: {pending}, фоллоу-апы тоже." + after


def do_skip(cfg, batch, c, frm, mid):
    if not c["recipient_ids"]:
        return "Не понял, какую клинику убрать. Ничего не изменено: напишите название клиники как в списке ниже."
    clinics = {rid: (clinic, states) for rid, clinic, states in by_clinic(cfg, batch)}
    done, not_now = [], []
    for rid in c["recipient_ids"]:
        clinic, states = clinics[rid]
        if any(s == "pending" for _, s, _ in states):
            append_ledger(cfg, {"event": "skip", "batch_id": batch["batch_id"], "recipient_id": rid, "by": frm, "command_id": mid})
            done.append(clinic)
        else:
            not_now.append(f"{clinic} ({clinic_line(cfg, states)})")
    return ((f"Убрано: {', '.join(done)} — больше ничего не уйдёт. Остальные идут по плану." if done else "Ничего не убрано.")
            + (f" Уже не в очереди: {'; '.join(not_now)}." if not_now else ""))


def command(cfg, box, batch, msg, mid, frm, folder):
    """Classify one operator mail, do what it asks if that is stop, skip or status, answer all operators, log it."""
    subject = str(msg.get("Subject") or "").strip()
    ev = {"event": "command", "batch_id": batch["batch_id"], "from": frm, "message_id": mid, "subject": subject,
          "date": str(msg.get("Date") or ""), "folder": folder,
          "auth": " ".join(re.findall(r"\b(?:spf|dkim|dmarc)=\w+", str(msg.get("Authentication-Results") or "")))}
    if automatic(msg):
        return append_ledger(cfg, {**ev, "intent": "auto_reply", "result": "not answered: an automatic reply"})
    text = fresh_text(cfg, msg)
    try:
        c = classify_command(cfg, subject, text, clinic_list(cfg, batch), left_to_send(cfg, batch))
    except MailerError as e:
        append_ledger(cfg, {**ev, "intent": "classifier_failed", "text": text, "error": str(e)})
        raise MailerError(f"HALT: could not read the mail from {frm} ({subject!r}): {e}")
    if c["intent"] == "stop":
        result = do_stop(cfg, batch, frm, mid)
    elif c["intent"] == "skip":
        result = do_skip(cfg, batch, c, frm, mid)
    elif c["intent"] == "status":
        result = "Статус ниже, ничего не изменено."
    elif c["intent"] == "other_command":
        result = ("Не выполнено: такой команды письмом нет, нужен оператор (Иван). Рассылка идёт по плану, ничего не изменено."
                  if not halt_event(read_ledger(cfg), batch["batch_id"]) else "Не выполнено: такой команды письмом нет, нужен оператор (Иван).")
    else:
        result = "Это не похоже на команду рассылки, ничего не изменено."
    quoted = " ".join(text.split())
    live = not halt_event(read_ledger(cfg), batch["batch_id"])      # after a stop no command is read any more: no command list
    reply = operator_mail(cfg, batch, subject if re.match(r"(?i)^(re|aw|отв):", subject) else f"Re: {subject or 'команда'}",
                          f"Письмо от {frm}: «{quoted}»\nПонято как: {c['intent']} ({c['why']})\n\n{result}\n\n"
                          f"{state_text(cfg, batch)}\n\n" + (f"{commands_help(cfg)}\n\n" if live else "") + "Daria\n",
                          in_reply_to=mid, auto="auto-replied")
    refused = smtp_send(box, reply)
    print(f"command {c['intent']} from {frm}: {result}")
    if refused:
        raise MailerError(f"SMTP refused the answer to {frm} for {sorted(refused)}")
    return append_ledger(cfg, {**ev, "intent": c["intent"], "recipient_ids": c["recipient_ids"], "unknown_ids": c["unknown_ids"],
                               "why": c["why"], "text": text, "result": result, "reply_message_id": reply["Message-ID"]})


def handle_commands(cfg, box, batch, since):
    """Answer every operator mail received since the announcement that has no "command" event yet. A mail the classifier
    could not read has one, but is read again: it halted the batch, and a resumed batch must not lose a stop."""
    ledger = read_ledger(cfg)
    seen = ({e["message_id"] for e in ledger if e["event"] == "command" and e["intent"] != "classifier_failed"}
            | {e["imap_message_id"] for e in ledger if e["event"] == "inbound"})
    done = []
    for folder, raw in inbox_messages(cfg, box, since, seen):
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        frm = parseaddr(str(msg.get("From") or ""))[1].lower()
        mid = str(msg.get("Message-ID") or "").strip() or "sha256:" + hashlib.sha256(raw).hexdigest()
        if frm not in cfg["operators"] or mid in seen:
            continue
        if cfg.get("watch_via", "imap") == "imap" and msg.get("Date") and parsedate_to_datetime(str(msg["Date"])) < since:
            continue                 # IMAP searches by day; Graph and the helper already filter by the received time
        seen.add(mid)
        done.append(command(cfg, box, batch, msg, mid, frm, folder))
    return done


def poll_until(cfg, box, batch, since, due):
    """Answer the operators and read the inbox until `due`; the last round ends just before it. False when an
    operator's stop halted the batch."""
    while True:
        t0 = now_in(cfg)
        handle_commands(cfg, box, batch, since)
        if halt_event(read_ledger(cfg), batch["batch_id"]):
            return False
        watch(cfg, box)
        halt_on_inbound(cfg, since)
        t1 = now_in(cfg)
        lead = (t1 - t0).total_seconds() + 2
        wait = (due - t1).total_seconds()
        if wait <= lead:
            sleep(max(wait, 0))
            return True
        sleep(min(wait - lead, cfg["command_poll_seconds"]))


def timeline(batch):
    """(time, "report" or "letter", the report or item) in time order; a report goes before a letter at the same time."""
    ev = ([(datetime.fromisoformat(r["send_at"]), 0, "report", r) for r in batch["reports"]]
          + [(datetime.fromisoformat(it["send_at"]), 1, "letter", it) for it in batch["items"]])
    return [(t, kind, obj) for t, _, kind, obj in sorted(ev, key=lambda e: e[:2])]


def threaded(cfg, it, ledger):
    """The item with In-Reply-To and References to the clinic's letters before it, for a follow-up planned before
    they went."""
    if not it.get("in_thread"):
        return it
    sent, _ = recipient_state(cfg, it["recipient_id"], ledger)
    if not sent:
        raise MailerError(f"{it['clinic']} {it['step']} answers the letter before it, but no letter to {it['recipient_id']} is in the ledger")
    return {**it, "in_reply_to": sent[-1]["message_id"], "references": [e["message_id"] for e in sent]}


def ended(signum, frame):
    """SIGTERM (shutdown, kill) and SIGHUP (the terminal closed) end a scheduled batch like Ctrl-C: halted, logged and
    told to the operators, instead of dying silently."""
    raise SystemExit(f"{signal.Signals(signum).name}: the process was told to end")


def send_scheduled(cfg, batch, live):
    """Announce, then send every letter at its send_at and every round's report at its time, answering the operators'
    mail all the while. After an error halt the same command resumes the batch, if no send time has passed."""
    bid, items, ann = batch["batch_id"], batch["items"], batch["announce"]
    at = datetime.fromisoformat(ann["send_at"]).astimezone(cfg["tz"])
    start = datetime.fromisoformat(batch["start_at"]).astimezone(cfg["tz"])
    window = timedelta(minutes=cfg["announce"]["window_minutes"])
    ledger = read_ledger(cfg)
    halt = halt_event(ledger, bid)
    if halt and halt.get("kind") != "error":
        raise MailerError(f"{bid} was halted ({halt['reason']}): plan a new batch")
    announced = next((e for e in ledger if e["event"] == "announced" and e["batch_id"] == bid), None)
    now = now_in(cfg)
    if not announced and now > start - window:
        raise MailerError(f"too late for {bid}: an announcement now ({now:%a %H:%M}) leaves less than {window} before the first "
                          f"letters from {start:%a %H:%M}; plan again with a later --start-at")
    states = item_states(cfg, batch, ledger, ignore_halt=True)
    missed = [it for it, s, _ in states if s == "pending" and datetime.fromisoformat(it["send_at"]) < now]
    if missed:
        raise MailerError(f"the send time of {len(missed)} letters has passed ({missed[0]['clinic']} {missed[0]['step']}, "
                          f"{missed[0]['send_at']}): plan again (the ledger keeps what went out) and approve the new batch")
    open_rounds = {it["round"] for it, s, _ in states if s == "pending"}
    late = [r for r in batch["reports"] if r["round"] in open_rounds and not reported(ledger, bid, r["round"])
            and datetime.fromisoformat(r["send_at"]) < now]
    if late:
        raise MailerError(f"the {late[0]['step']} report was due at {late[0]['send_at']} and did not go: plan again and approve "
                          "the new batch")
    box = mailbox(cfg["sender"])
    check_inbox(cfg, box)
    check = classify_command(cfg, "статус", "Какой статус рассылки?", clinic_list(cfg, batch), left_to_send(cfg, batch))
    if check["intent"] != "status":
        raise MailerError(f"classifier self-check: 'Какой статус рассылки?' came back as {check['intent']}, not status")
    print(f"inbox and classifier ok; {len(items)} letters {items[0]['send_at'][:16]} to {items[-1]['send_at'][:16]}, "
          f"{len(batch['reports'])} reports, announcement " + (f"sent {announced['sent_at'][:16]}" if announced else
                                                                f"{at:%a %d.%m %H:%M:%S}") + f" to {', '.join(ann['to'])}")
    before = {s: signal.signal(s, ended) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        if halt:
            append_ledger(cfg, {"event": "resumed", "batch_id": bid, "halt_ts": halt["ts"], "halt_reason": halt["reason"]})
            notify(cfg, box, batch, "resumed", "рассылка продолжается",
                   f"Рассылка продолжается по плану после остановки из-за ошибки ({halt['reason']}).")
        if not announced:
            while (wait := (at - now_in(cfg)).total_seconds()) > 0:
                print(f"waiting for the announcement at {at:%a %H:%M:%S} ({wait / 60:.0f} min)")
                sleep(min(wait, 300))
            msg = build_message(cfg, ann)
            refused = smtp_send(box, msg)
            announced = append_ledger(cfg, {"event": "announced", "batch_id": bid, "to": ann["to"], "message_id": msg["Message-ID"],
                                            "sha256": ann["sha256"], "sent_at": now_in(cfg).isoformat(timespec="seconds"),
                                            "smtp_refused": sorted(refused)})
            print(f"announced to {', '.join(ann['to'])} {msg['Message-ID']}")
            if refused:
                raise MailerError(f"SMTP refused the announcement for {sorted(refused)}; no letter was sent")
        since = datetime.fromisoformat(announced["sent_at"])
        last_round = max(it["round"] for it in items)
        round_end = {rnd: max(it["send_at"] for it in items if it["round"] == rnd) for rnd in range(last_round + 1)}
        for t, kind, obj in timeline(batch):
            ledger = read_ledger(cfg)
            if halt_event(ledger, bid):
                break
            states = {id(it): s for it, s, _ in item_states(cfg, batch, ledger)}
            if "pending" not in states.values():
                break
            if kind == "report":
                if not reported(ledger, bid, obj["round"]) and any(states[id(it)] == "pending" for it in items if it["round"] == obj["round"]):
                    if poll_until(cfg, box, batch, since, t):
                        send_report(cfg, box, batch, obj)
                continue
            it = obj
            if states[id(it)] == "pending" and poll_until(cfg, box, batch, since, t):
                ledger = read_ledger(cfg)
                state = next(s for i, s, _ in item_states(cfg, batch, ledger) if i is it)
                if state != "pending":
                    print(f"skip {it['recipient_id']} {it['clinic']} {it['step']}: {state}")
                else:
                    while not odd_minute(tt := now_in(cfg)):           # late on a round minute: the next minute is odd
                        sleep(60 - tt.second - tt.microsecond / 1e6 + random.uniform(1, 20))
                    if not in_window(cfg, now_in(cfg)):
                        raise MailerError(f"outside the send window at {now_in(cfg):%a %H:%M}")
                    msg = build_message(cfg, threaded(cfg, it, ledger))
                    refused = smtp_send(box, msg)
                    ev = append_ledger(cfg, {"event": "sent", "batch_id": bid, "mode": "live" if live else "allowlist", "inbox_watched": True,
                                             "recipient_id": it["recipient_id"], "clinic": it["clinic"], "step": it["step"], "to": it["to"],
                                             "cc": it["cc"], "real_to": it.get("real_to"), "message_id": msg["Message-ID"],
                                             "in_reply_to": msg["In-Reply-To"], "subject": it["subject"], "sha256": it["sha256"],
                                             "send_at": it["send_at"], "sent_at": now_in(cfg).isoformat(timespec="seconds"),
                                             "smtp_refused": {k: [v[0], v[1].decode("utf-8", "replace")] for k, v in refused.items()}})
                    print(f"sent {ev['sent_at'][:19]} {it['recipient_id']} {it['step']} to {', '.join(it['to'])} {ev['message_id']}")
                    if refused:
                        raise DeliveryHalt(f"HALT: SMTP refused {sorted(refused)}")
            ledger = read_ledger(cfg)
            if halt_event(ledger, bid):
                break
            rnd = it["round"]
            if (it["send_at"] == round_end[rnd] and rnd < last_round
                    and not any(e["event"] == "notice" and e.get("batch_id") == bid and e.get("round") == rnd for e in ledger)
                    and any(s == "pending" for i, s, _ in item_states(cfg, batch, ledger) if i["round"] > rnd)):
                round_done(cfg, box, batch, rnd)
        if halt_event(read_ledger(cfg), bid):
            print(f"halted by an operator's mail: {sum(s == 'sent' for _, s, _ in item_states(cfg, batch))} of {len(items)} letters went out")
            return
        handle_commands(cfg, box, batch, since)             # a stop that came with the last letter still gets its answer
        watch(cfg, box)
        states = [s for _, s, _ in item_states(cfg, batch)]
        notify(cfg, box, batch, "done", "рассылка завершена",
               f"Рассылка завершена {mailer_announce.when(now_in(cfg))}. Писем ушло: {states.count('sent')} из {len(states)} "
               "запланированных. Процесс закончился, команды письмом больше не принимаются.")
        print("batch done", bid)
    except BaseException as e:                              # Ctrl-C and signals too: the operators hear of every stop
        kind = "delivery" if isinstance(e, DeliveryHalt) else "error"
        reason = str(e) or type(e).__name__
        append_ledger(cfg, {"event": "halt", "batch_id": bid, "kind": kind, "reason": reason})
        try:
            if kind == "delivery":
                notify(cfg, box, batch, "halt", "рассылка остановлена",
                       f"Рассылка остановлена: {reason}\n\nОстаток не уйдёт, фоллоу-апы тоже. Продолжить можно только новым планом и "
                       "одобрением Ивана.")
            else:
                notify(cfg, box, batch, "halt", "рассылка прервана",
                       f"Рассылка прервана: {reason}\n\nПока процесс не запущен снова, ничего не уходит и команды письмом не "
                       "читаются. Иван запускает его той же командой: рассылка продолжится по плану, если ни одно время "
                       "отправки ещё не прошло; иначе нужен новый план и одобрение.")
        except Exception as ne:
            print(f"ERROR: the operators were not told of the halt: {ne}", file=sys.stderr)
        raise
    finally:
        for s, h in before.items():
            signal.signal(s, h)


# ---------- watch ----------

def imap_login(box):
    im = imaplib.IMAP4_SSL(box["IMAP_HOST"], int(box["IMAP_PORT"] or 993))
    im.login(box["ADDRESS"], box["PASSWORD"])
    return im


STOP_WORDS = re.compile(r"keine weiteren|nicht mehr (kontaktieren|anschreiben|schreiben)|abmelden|austragen|unsubscribe|"
                        r"werbewiderspruch|widerspreche|von ihrer (e-?mail-?)?liste|remove me", re.I)
AUTO_SUBJ = re.compile(r"^(automatische antwort|abwesenheit|out of office|automatic reply|autoreply|abwesend)", re.I)


def text_of(msg):
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == "text" and "attachment" not in str(part.get("Content-Disposition") or ""):
            try:
                out.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
            except Exception:
                pass
    return "\n".join(out)


def classify(msg, text):
    ctype = msg.get_content_type()
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if ctype == "multipart/report" and "feedback-report" in (msg.get("Content-Type") or ""):
        return "complaint"
    if ctype == "multipart/report" or sender.split("@")[0] in ("mailer-daemon", "postmaster"):
        return "bounce"
    auto = (msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond")
            or (msg.get("Precedence", "").lower() in ("auto_reply", "bulk", "junk")) or AUTO_SUBJ.match(msg.get("Subject", "")))
    if auto:
        return "auto_reply"
    fresh = "\n".join(l for l in text.splitlines() if not l.startswith(">"))
    fresh = re.split(r"\n(Am .{5,80} schrieb|On .{5,80} wrote|-----\s*(Original|Ursprüngliche))", fresh)[0]
    return "stop" if STOP_WORDS.search(fresh) else "reply"


def watch(cfg, box=None):
    """Log every new inbound message that belongs to this campaign; return the new events."""
    ledger = read_ledger(cfg)
    sent = [e for e in ledger if e["event"] == "sent"]
    if not sent:
        return []
    seen = {e["imap_message_id"] for e in ledger if e["event"] == "inbound"}
    by_mid = {e["message_id"]: e["recipient_id"] for e in sent}
    by_addr = {}
    for e in sent:
        for a in e["to"] + e["cc"]:
            by_addr[a.lower()] = e["recipient_id"]
    domains = {a.split("@")[1] for a in by_addr}
    since = min(datetime.fromisoformat(e["sent_at"]) for e in sent).astimezone(cfg["tz"]).replace(hour=0, minute=0, second=0, microsecond=0)
    via = cfg.get("watch_via", "imap")
    fetch = inbox_messages(cfg, box, since, seen)
    new, n_read = [], 0
    for folder, raw in fetch:
        n_read += 1
        msg = email.message_from_bytes(raw)
        mid = (msg.get("Message-ID") or "").strip() or "sha256:" + hashlib.sha256(raw).hexdigest()
        frm = parseaddr(msg.get("From", ""))[1].lower()
        if mid in seen or frm == cfg["sender"].lower() or frm in cfg["operators"]:
            continue                 # an operator's mail is a command (handle_commands), never a clinic's answer
        text = text_of(msg)
        kind = classify(msg, text)
        refs = set(re.findall(r"<[^>]+>", " ".join(filter(None, [msg.get("In-Reply-To"), msg.get("References")]))))
        raw_text = raw.decode("utf-8", "replace")
        rid, how = next((by_mid[r] for r in refs if r in by_mid), None), "thread"
        if not rid:
            rid, how = next((by_mid[m] for m in by_mid if m in raw_text), None), "quoted message-id"
        if not rid:
            rid, how = by_addr.get(frm), "from address"
        if not rid and kind in ("bounce", "complaint"):
            rid, how = next((r for a, r in by_addr.items() if a in raw_text.lower()), None), "address in report"
        if not rid:
            if frm.split("@")[-1] not in domains:
                continue
            kind, how = "unmatched", None
        ev = append_ledger(cfg, {"event": "inbound", "kind": kind, "recipient_id": rid, "matched_by": how,
                                 "folder": folder, "from": frm, "subject": msg.get("Subject", ""),
                                 "date": msg.get("Date", ""), "imap_message_id": mid})
        seen.add(mid)
        new.append(ev)
        print(f"inbound {ev['kind']:10} {rid} from {ev['from']} [{how}] {ev['subject'][:70]}"
              + ("  <- from a campaign domain but matches no sent message: read it by hand" if kind == "unmatched" else ""))
    print(f"watch {cfg['sender']} via {via}: {n_read} messages read in "
          f"{'all folders' if via == 'daria-inbox' else ', '.join(cfg['watch_folders'])} since {since:%Y-%m-%d}, {len(new)} new for this campaign")
    return new


def inbox_messages(cfg, box, since, seen):
    """(folder, raw MIME) of the sender box's messages received since `since`, by the config's "watch_via"."""
    via = cfg.get("watch_via", "imap")
    if via == "graph":
        return graph_messages(cfg, since, seen)
    if via == "daria-inbox":
        return helper_messages(cfg, since, seen)
    return imap_messages(cfg, box or mailbox(cfg["sender"]), since)


def helper_messages(cfg, since, seen):
    """(folder, raw MIME) of every message daria-inbox returns since `since`, in every folder, except drafts, daria's
    own messages (Sent Items) and Message-IDs in `seen`; the Junk folder is named junkemail, like Graph's."""
    p = subprocess.run(["sudo", "-n", DARIA_INBOX, "--since", since.isoformat(timespec="seconds")],
                       capture_output=True, text=True, timeout=900)
    if p.returncode:
        raise MailerError(f"daria-inbox exited {p.returncode}: {p.stderr.strip()[-300:]}")
    for line in p.stdout.splitlines():
        m = json.loads(line)
        frm = (((m.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
        if m.get("isDraft") or frm == DARIA or (m.get("internetMessageId") or "").strip() in seen:
            continue
        yield ("junkemail" if m["junk"] else m["folder"]), base64.b64decode(m["mime_b64"])


def imap_messages(cfg, box, since):
    """(folder, raw message) for every message in the watched IMAP folders since `since` (a date)."""
    im = imap_login(box)
    try:
        for folder in cfg["watch_folders"]:
            typ, _ = im.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                raise MailerError(f"IMAP folder {folder!r} not found in {cfg['sender']}")
            _, data = im.search(None, "SINCE", since.strftime("%d-%b-%Y"))
            for num in data[0].split():
                _, raw = im.fetch(num, "(RFC822)")
                yield folder, raw[0][1]
    finally:
        im.logout()


def graph_token(cfg):
    """(access token, email_dump_graph module) for the sender's M365 box, from the shared MSAL cache. email_dump_graph
    re-reads the cache, refreshes silently and writes it back atomically, because another service uses the same cache."""
    try:
        import email_dump_graph as G
    except ModuleNotFoundError as e:
        raise MailerError(f"the Graph watch needs the {e.name} module: run with the system python3, which has it "
                          "(sudo -E python3 tools/clinic_mailer.py ...)")
    env = load_env()
    gcfg = {"cache": env["MICROSOFT_GRAPH_MSAL_CACHE"], "cid": env["MICROSOFT_GRAPH_CLIENT_ID"], "auth": env["MICROSOFT_GRAPH_AUTHORITY"],
            "scopes": [s for s in env["MICROSOFT_GRAPH_SCOPES"].split() if s.lower() not in ("openid", "profile", "offline_access")]}
    try:
        token, err = G.get_token_for(cfg["sender"], gcfg)
    except PermissionError:
        raise MailerError(f"cannot read the MSAL cache {gcfg['cache']}: it is root-owned, run this command with sudo -E")
    if err:
        raise MailerError(f"no Graph token for {cfg['sender']}: {err}")
    return token, G


RAW = {}      # Graph message id -> MIME: a scheduled batch reads the inbox every minute and downloads each message once


def graph_messages(cfg, since, seen):
    """(folder, raw MIME) for every message received since `since` in the watched Graph folders, except Message-IDs
    already in the ledger (those are not downloaded again)."""
    token, G = graph_token(cfg)
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for folder in cfg["watch_folders"]:
        url = (f"{G.GRAPH}/me/mailFolders/{folder}/messages?$select=id,internetMessageId&$top=50&$filter="
               + urllib.parse.quote(f"receivedDateTime ge {stamp}"))
        while url:
            page = G.graph_get(url, token)
            for m in page.get("value", []):
                if (m.get("internetMessageId") or "").strip() in seen:
                    continue
                if m["id"] not in RAW:
                    RAW[m["id"]] = graph_raw(f"{G.GRAPH}/me/messages/{m['id']}/$value", token)
                yield folder, RAW[m["id"]]
            url = page.get("@odata.nextLink")


def graph_raw(url, token):
    """A Graph response as bytes (a message's MIME source from /$value); 429 and 5xx are retried like graph_get does."""
    for _ in range(6):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(min(int(e.headers.get("Retry-After", "5")), 30))
                continue
            raise
    raise MailerError("Graph: too many retries on " + url)


# ---------- status ----------

def status(cfg, now):
    ledger = read_ledger(cfg)
    for rec in json.loads(cfg["recipients"].read_text()):
        sent, stop = recipient_state(cfg, rec["id"], ledger)
        i, due = next_due(cfg, rec["id"], ledger)
        steps = ", ".join(f"{e['step']} {e['sent_at'][:16]}" for e in sent) or "nothing sent"
        nxt = (f"next {cfg['cadence'][i]['step']} " + (f"due {due:%Y-%m-%d %H:%M}" if due else "now")) if i is not None else due
        print(f"{rec['id']:>6} {rec['clinic'][:40]:40} | {steps} | {nxt}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("config"); p.add_argument("--now"); p.add_argument("--only", nargs="+", metavar="ID")
    p.add_argument("--announce-at", metavar="ISO", help="a scheduled batch announced to the operators at this time")
    p.add_argument("--start-at", metavar="ISO", help="the first letters start a pause after this time (default: the announcement "
                                                     "plus announce.window_minutes)")
    p = sub.add_parser("send"); p.add_argument("config"); p.add_argument("batch_id"); p.add_argument("--live", action="store_true")
    p.add_argument("--no-watch", action="store_true", help="allowlist tests only: do not read the inbox during the batch")
    p = sub.add_parser("watch"); p.add_argument("config")
    p = sub.add_parser("status"); p.add_argument("config"); p.add_argument("--now")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    now = datetime.fromisoformat(a.now).astimezone(cfg["tz"]) if getattr(a, "now", None) else datetime.now(cfg["tz"])
    try:
        if a.cmd == "plan":
            at, start = (v and datetime.fromisoformat(v) for v in (a.announce_at, a.start_at))
            if (at and at.tzinfo is None) or (start and start.tzinfo is None):
                raise MailerError("--announce-at and --start-at need a UTC offset, e.g. 2026-09-29T09:00:00+02:00")
            bid, report = plan(cfg, now, a.only, at and at.astimezone(cfg["tz"]), start and start.astimezone(cfg["tz"]))
            print("\n".join(report))
            print(f"batch {bid}: read {cfg['batches'] / (bid + '.txt')}" if bid else "nothing due")
        elif a.cmd == "send":
            send(cfg, a.batch_id, a.live, watch_inbox=not a.no_watch)
        elif a.cmd == "watch":
            watch(cfg)
        else:
            status(cfg, now)
    except MailerError as e:
        print("ERROR:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
