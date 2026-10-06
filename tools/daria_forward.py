#!/usr/bin/env python3
"""Forward mail of daria.s@pflege-connect.work to Ivan and the parallel operator by hand (TASK-345.10; Ivan, 2026-09-28: "а ты разве
не можешь мне пересылать руками?"; Ivan, 2026-10-05: every forward goes to the operators; the parallel operator's address bounced, copies to Ivan only for now).

  list FETCH          the inbound messages of a daria-inbox output file (not sent by daria, not drafts): received,
                      folder, sender, subject, and whether each was forwarded already
  send FETCH KEY... [--to ADDR...]
                      forward these messages (--to: to these addresses instead of the default operator list, for a
                      message that reached only part of them). KEY is a message's internetMessageId, or a sender's address for every
                      inbound message from that sender in FETCH. One mail each from daria to Ivan and the parallel operator: the header lines,
                      the text, and the original attached as message/rfc822. Every forward is logged in
                      forwarded.jsonl next to FETCH, and a message forwarded once is not sent again.

FETCH is written by `python3 -I tools/daria_inbox.py --since ISO > FETCH` (no sudo since 2026-10-06).
"""
import argparse
import base64
import email
import email.policy
import html
import json
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clinic_mailer as M  # noqa: E402
from mailer_doc import Doc, Quote, Table  # noqa: E402

BOX = "daria.s@pflege-connect.work"
TO = ["ivan.d.kotelnikov@gmail.com"]     # the parallel operator is back here once he has a working address (Ivan, 2026-10-05)
TZ = ZoneInfo("Europe/Berlin")


def one_line(s):
    return re.sub(r"\s*[\r\n]+\s*", " ", s or "").strip()


def inbound(fetch):
    out = []
    for line in Path(fetch).read_text().splitlines():
        m = json.loads(line)
        if not m["isDraft"] and ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower() != BOX:
            out.append(m)
    return out


def forwarded(fetch, to):
    """Keys of the messages already forwarded to every address in `to`."""
    p = Path(fetch).parent / "forwarded.jsonl"
    recs = [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []
    return {r["key"] for r in recs if {a.lower() for a in to} <= {a.lower() for a in r["to"]}}


def text_of(msg):
    """The readable text: the plain part, else the HTML part without tags."""
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return "(текста нет)"
    try:
        text = body.get_content()
    except LookupError:                            # a charset Python does not know: shown as UTF-8, and said so
        text = "(неизвестная кодировка, текст показан как UTF-8)\n" + body.get_payload(decode=True).decode("utf-8", "replace")
    if body.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def people(msg, header):
    return ", ".join(f"{n} <{a}>" if n else a for n, a in getaddresses([str(v) for v in msg.get_all(header, [])])) or "—"


def notification(m, to):
    raw = base64.b64decode(m["mime_b64"])
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(msg.get("From") or ""))
    received = datetime.fromisoformat(m["receivedDateTime"]).astimezone(TZ)
    doc = Doc(f"Входящее на {BOX}",
              Table(None, [["От", f"{name} <{addr}>" if name else addr or "—"],
                           ["Кому", people(msg, "To")],
                           ["Копия", people(msg, "Cc")],
                           ["Получено", f"{received:%d.%m.%Y %H:%M} (Берлин)"],
                           ["Папка", f"{m['folder']}{' (спам)' if m['junk'] else ''}"]]),
              "Оригинал во вложении (original.eml).", "Текст письма:", Quote(text_of(msg)))
    n = EmailMessage()
    n["From"] = formataddr(("Daria | входящие", BOX))
    n["To"] = ", ".join(to)
    n["Subject"] = f"[daria{' · спам' if m['junk'] else ''}] {one_line(name) or addr}: {one_line(str(msg.get('Subject') or '(без темы)'))}"
    n["Date"] = formatdate(localtime=True)
    n["Message-ID"] = make_msgid(domain=BOX.split("@")[1])
    n["Auto-Submitted"] = "auto-generated"
    n.set_content(doc.text())
    n.add_alternative(doc.html(), subtype="html")
    # 8bit keeps the original's exact bytes: the generator writes a string payload of message/rfc822 as it is
    n.add_attachment(raw, maintype="message", subtype="rfc822", cte="8bit", filename="original.eml")
    return n, one_line(str(msg.get("Subject") or ""))


def send(fetch, keys, to):
    creds = M.mailbox(BOX)
    done = forwarded(fetch, to)
    todo = [m for m in inbound(fetch) if m["internetMessageId"] in keys
            or ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower() in {k.lower() for k in keys}]
    if not todo:
        raise M.MailerError(f"no inbound message in {fetch} matches {keys}")
    port = int(creds["SMTP_PORT"])
    with (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(creds["SMTP_HOST"], port, timeout=60) as s:
        if port != 465:
            s.starttls()
        s.login(creds["ADDRESS"], creds["PASSWORD"])
        for m in todo:
            if m["internetMessageId"] in done:
                print(f"already forwarded: {m['receivedDateTime']} {m['internetMessageId']}")
                continue
            n, subject = notification(m, to)
            refused = s.send_message(n)
            if refused:
                raise M.MailerError(f"SMTP refused {refused} for {m['internetMessageId']}")
            rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "key": m["internetMessageId"],
                   "received": m["receivedDateTime"], "from": m["from"]["emailAddress"]["address"], "subject": subject,
                   "folder": m["folder"], "to": to, "notify_message_id": n["Message-ID"]}
            with (Path(fetch).parent / "forwarded.jsonl").open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done.add(m["internetMessageId"])
            print(f"forwarded {rec['received']} {rec['from']}: {subject[:70]} -> {', '.join(to)} {rec['notify_message_id']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("fetch")
    p = sub.add_parser("send")
    p.add_argument("--to", nargs="+", metavar="ADDR", default=TO)
    p.add_argument("fetch")
    p.add_argument("keys", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        done = forwarded(a.fetch, TO)
        for m in inbound(a.fetch):
            t = datetime.fromisoformat(m["receivedDateTime"]).astimezone(TZ)
            print(f"{t:%d.%m %H:%M} {'fwd ' if m['internetMessageId'] in done else '    '}{'spam ' if m['junk'] else '     '}"
                  f"{m['folder'][:14]:14} {m['from']['emailAddress']['address'][:42]:42} {one_line(m.get('subject'))[:60]}")
    else:
        send(a.fetch, a.keys, a.to)


if __name__ == "__main__":
    main()
