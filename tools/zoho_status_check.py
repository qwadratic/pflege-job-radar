#!/usr/bin/env python3
"""Hygiene check for the Zoho mailboxes (#1-8): which can SEND (SMTP AUTH)
and which can READ (IMAP login). LOGIN-ONLY — no mail is sent. Creds come
from pflege-board/.env (MAILBOX_<n>_*). A 1s pause between boxes avoids
tripping Zoho's rapid-auth throttling.
"""
import smtplib, imaplib, ssl, time

ENV = "/home/claude/repo/pflege-board/.env"


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


def check_smtp(host, port, user, pw):
    try:
        c = smtplib.SMTP(host, int(port), timeout=25)
        c.ehlo()
        c.starttls(context=ssl.create_default_context())
        c.ehlo()
        c.login(user, pw)
        c.quit()
        return "OK"
    except Exception as e:
        return "FAIL: " + type(e).__name__ + " " + str(e)[:90]


def check_imap(host, port, user, pw):
    try:
        c = imaplib.IMAP4_SSL(host, int(port), timeout=25)
        c.login(user, pw)
        typ, folders = c.list()
        c.select("INBOX", readonly=True)
        typ2, data = c.search(None, "ALL")
        cnt = len(data[0].split()) if data and data[0] else 0
        c.logout()
        return "OK (inbox %d msgs, %d folders)" % (cnt, len(folders) if folders else 0)
    except Exception as e:
        return "FAIL: " + type(e).__name__ + " " + str(e)[:90]


def main():
    env = load_env(ENV)
    g = lambda n, s: env.get("MAILBOX_%d_%s" % (n, s), "")
    for n in range(1, 12):
        if g(n, "PROVIDER") != "zoho":
            continue
        addr, pw = g(n, "ADDRESS"), g(n, "PASSWORD")
        smtp = check_smtp(g(n, "SMTP_HOST"), g(n, "SMTP_PORT"), addr, pw)
        imap = check_imap(g(n, "IMAP_HOST"), g(n, "IMAP_PORT"), addr, pw)
        print("#%-2d %s" % (n, addr))
        print("     SEND (SMTP): %s" % smtp)
        print("     READ (IMAP): %s" % imap)
        time.sleep(1)


if __name__ == "__main__":
    main()
