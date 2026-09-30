#!/usr/bin/env python3
"""Dump the last N months of mail from readable Zoho mailboxes via IMAP.

Read-only (readonly SELECT). Writes JSONL per mailbox under
data/email-dump/<address>/, matching the Graph dumper's layout. Creds come
from pflege-board/.env (MAILBOX_<n>_*). Boxes whose IMAP login fails are
skipped with an error line. No sudo needed. Binary attachments are not
stored — only their filenames are recorded.

Usage:
  python3 tools/email_dump_imap.py                              # all Zoho boxes
  ONLY_MAILBOX=dashandt@pflege-ndt.work python3 tools/email_dump_imap.py
  MONTHS=6 python3 tools/email_dump_imap.py
"""
import imaplib, email, json, os, re, time
from email.header import decode_header, make_header

ENV      = "/home/claude/repo/pflege-board/.env"
OUT_ROOT = "/home/claude/repo/pflege-board/data/email-dump"
MONTHS   = int(os.environ.get("MONTHS", "6"))


def load_env(p):
    e = {}
    for l in open(p, encoding="utf-8", errors="replace"):
        s = l.strip()
        if s.startswith("export "):
            s = s[7:]
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def dec(s):
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def _payload(part):
    try:
        b = part.get_payload(decode=True)
        if b is None:
            return ""
        return b.decode(part.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def body_text(msg):
    if msg.is_multipart():
        plain, html = [], []
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition") or ""):
                continue
            ct = part.get_content_type()
            if ct == "text/plain":
                plain.append(_payload(part))
            elif ct == "text/html":
                html.append(_payload(part))
        if plain:
            return "\n".join(plain)
        if html:
            return re.sub(r"<[^>]+>", " ", "\n".join(html))
        return ""
    return _payload(msg)


def attach_names(msg):
    names = []
    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition") or ""):
                names.append(dec(part.get_filename() or "unnamed"))
    return names


def imap_since(months):
    return time.strftime("%d-%b-%Y", time.gmtime(time.time() - months * 30 * 24 * 3600))


def folder_names(list_lines):
    names = []
    for f in list_lines or []:
        m = re.search(rb'"([^"]*)"\s*$', f) or re.search(rb'([^\s]+)\s*$', f)
        if m:
            names.append(m.group(1).decode("utf-8", "replace"))
    return names


def dump_box(n, g, since):
    addr, pw = g(n, "ADDRESS"), g(n, "PASSWORD")
    host, port = g(n, "IMAP_HOST"), int(g(n, "IMAP_PORT") or 993)
    try:
        c = imaplib.IMAP4_SSL(host, port, timeout=40)
        c.login(addr, pw)
    except Exception as e:
        return {"mailbox": addr, "error": "login " + type(e).__name__ + " " + str(e)[:90]}
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", addr)
    d = os.path.join(OUT_ROOT, safe)
    os.makedirs(d, exist_ok=True)
    _typ, folders = c.list()
    fnames = folder_names(folders)
    n_msgs = 0
    with open(os.path.join(d, "messages.jsonl"), "w", encoding="utf-8") as out:
        for folder in fnames:
            try:
                st, _ = c.select('"%s"' % folder, readonly=True)
                if st != "OK":
                    continue
                st, data = c.search(None, '(SINCE "%s")' % since)
                if st != "OK" or not data or not data[0]:
                    continue
                ids = data[0].split()
                for i in range(0, len(ids), 50):
                    st, fetched = c.fetch(b",".join(ids[i:i + 50]), "(RFC822)")
                    if st != "OK":
                        continue
                    for item in fetched:
                        if not isinstance(item, tuple):
                            continue
                        msg = email.message_from_bytes(item[1])
                        rec = {
                            "folder": folder,
                            "message_id": dec(msg.get("Message-ID")),
                            "in_reply_to": dec(msg.get("In-Reply-To")),
                            "references": dec(msg.get("References")),
                            "subject": dec(msg.get("Subject")),
                            "from": dec(msg.get("From")),
                            "to": dec(msg.get("To")),
                            "cc": dec(msg.get("Cc")),
                            "date": dec(msg.get("Date")),
                            "body": body_text(msg)[:100000],
                            "attachments": attach_names(msg),
                        }
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        n_msgs += 1
            except Exception as e:
                out.write(json.dumps({"folder_error": folder, "err": str(e)[:120]},
                                     ensure_ascii=False) + "\n")
    try:
        c.logout()
    except Exception:
        pass
    summary = {"mailbox": addr, "messages": n_msgs, "folders": len(fnames), "since": since}
    json.dump(summary, open(os.path.join(d, "_summary.json"), "w"), ensure_ascii=False, indent=2)
    return summary


def main():
    env = load_env(ENV)
    g = lambda n, s: env.get("MAILBOX_%d_%s" % (n, s), "")
    since = imap_since(MONTHS)
    only = os.environ.get("ONLY_MAILBOX")
    skip_existing = os.environ.get("SKIP_EXISTING")
    os.makedirs(OUT_ROOT, exist_ok=True)
    report = []
    for n in range(1, 12):
        if g(n, "PROVIDER") != "zoho":
            continue
        addr = g(n, "ADDRESS")
        if only and addr.lower() != only.lower():
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", addr)
        if skip_existing and os.path.exists(os.path.join(OUT_ROOT, safe, "_summary.json")):
            res = {"mailbox": addr, "skipped": "already dumped"}
            report.append(res)
            print(json.dumps(res, ensure_ascii=False))
            continue
        res = dump_box(n, g, since)
        report.append(res)
        print(json.dumps(res, ensure_ascii=False))
    print("=== imap dump summary ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
