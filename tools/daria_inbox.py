#!/usr/bin/python3 -I
"""daria-inbox --since ISO: every message in daria.s@pflege-connect.work received at or after ISO, oldest first, one
JSON line each on stdout: the Graph metadata, the folder's name, whether it is the Junk folder, and the raw MIME
(base64). TASK-345.10.

Ivan, 2026-09-28, granted the claude user this one command through sudo without a password, as the root-owned
/usr/local/sbin/daria-inbox (tools/daria_inbox_install.sh), so Claude reads daria's mail and forwards it to him by hand.
Ivan, 2026-10-06: the MSAL cache is deliberately the claude user's now (~/.local/state/pflege-mail, .env changed at 20:37
on 05.10), so the root copy refused it and the mailing stopped; the command is now run by the claude user
itself, `/usr/bin/python3 -I tools/daria_inbox.py --since ISO`, no sudo. It stays self-contained (standard library and
msal); python -I drops the user site, PYTHON* variables and the script directory from sys.path.

Read-only: Graph GET requests only, nothing is written. Unlike email_dump_graph, the refreshed MSAL cache is not written
back: the old refresh token stays valid, and whatever owns the cache keeps it fresh; a cache that went stale fails
loudly here ("no Graph token").

The Graph settings come from the repo's .env, so they are checked before they are used: the cache is opened without
following a symlink and must be a regular file that the running user owns and only the owner can write; the authority
must be login.microsoftonline.com. Anything else stops the command. Errors never print a value
from .env.
"""
import argparse
import base64
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import msal

BOX = "daria.s@pflege-connect.work"
ENV = "/home/claude/repo/pflege-board/.env"
GRAPH = "https://graph.microsoft.com/v1.0"
FIELDS = ("id,internetMessageId,conversationId,receivedDateTime,parentFolderId,isDraft,from,toRecipients,ccRecipients,"
          "subject,hasAttachments")


def stop(why):
    sys.exit(f"daria-inbox: {why}; stopping")


def load_env():
    env = {}
    with open(ENV, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("export "):
                s = s[7:]
            if "=" in s and not s.startswith("#"):
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def token():
    """An access token for BOX from the shared MSAL cache, refreshed silently in memory only."""
    env = load_env()
    if not env["MICROSOFT_GRAPH_AUTHORITY"].startswith("https://login.microsoftonline.com/"):
        stop("MICROSOFT_GRAPH_AUTHORITY in .env must start with https://login.microsoftonline.com/")
    try:
        fd = os.open(env["MICROSOFT_GRAPH_MSAL_CACHE"], os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        stop(f"cannot open the MSAL cache named in .env ({e.strerror})")
    with os.fdopen(fd, encoding="utf-8") as f:
        s = os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            stop("the MSAL cache named in .env must be a regular file that the running user owns and only the owner can write")
        cache = msal.SerializableTokenCache()
        cache.deserialize(f.read())
    app = msal.PublicClientApplication(env["MICROSOFT_GRAPH_CLIENT_ID"], authority=env["MICROSOFT_GRAPH_AUTHORITY"],
                                       token_cache=cache)
    acct = next((a for a in app.get_accounts() if a.get("username", "").lower() == BOX), None)
    if not acct:
        stop(f"{BOX} is not in the MSAL cache")
    scopes = [x for x in env["MICROSOFT_GRAPH_SCOPES"].split() if x.lower() not in ("openid", "profile", "offline_access")]
    r = app.acquire_token_silent(scopes, account=acct)
    if not (r and "access_token" in r):
        stop(f"no Graph token for {BOX}: {(r or {}).get('error', 'no token')} {str((r or {}).get('error_description', ''))[:120]}")
    return r["access_token"]


def get(url, tok, parse=True):
    """One Graph GET, JSON or raw bytes; 429 and 5xx are retried like email_dump_graph.graph_get does."""
    for _ in range(6):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp) if parse else resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(min(int(e.headers.get("Retry-After", "5")), 30))
                continue
            raise
    raise RuntimeError("too many retries: " + url)


def main():
    ap = argparse.ArgumentParser(prog="daria-inbox", description=__doc__.split("\n")[0])
    ap.add_argument("--since", required=True, help="ISO time with a UTC offset, e.g. 2026-09-28T00:00:00+02:00")
    t = datetime.fromisoformat(ap.parse_args().since)
    if t.tzinfo is None:
        stop("--since needs a UTC offset, e.g. 2026-09-28T00:00:00+02:00")
    since = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tok = token()
    junk = get(f"{GRAPH}/me/mailFolders/junkemail?$select=id", tok)["id"]
    folders = {}
    url = (f"{GRAPH}/me/messages?$select={FIELDS}&$filter={urllib.parse.quote('receivedDateTime ge ' + since)}"
           f"&$orderby={urllib.parse.quote('receivedDateTime asc')}&$top=50")
    while url:
        page = get(url, tok)
        for m in page["value"]:
            fid = m["parentFolderId"]
            if fid not in folders:
                folders[fid] = get(f"{GRAPH}/me/mailFolders/{fid}?$select=displayName", tok)["displayName"]
            m.update(folder=folders[fid], junk=fid == junk,
                     mime_b64=base64.b64encode(get(f"{GRAPH}/me/messages/{m['id']}/$value", tok, parse=False)).decode())
            print(json.dumps(m, ensure_ascii=False), flush=True)
        url = page.get("@odata.nextLink")


if __name__ == "__main__":
    main()
