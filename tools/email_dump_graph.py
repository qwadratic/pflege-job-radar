#!/usr/bin/env python3
"""Dump the last N months of mail from the M365 mailboxes via Microsoft Graph.

Uses the shared MSAL cache (delegated, per-account) that lives in the
clinic-dispatcher tooling. Mail access is READ-ONLY. Output is written as
JSONL, one file per mailbox, under data/email-dump/<mailbox>/.

Colleague-safety: the shared cache is also used by another service. We
re-read the cache immediately before each account, refresh silently, and
write the refreshed cache BACK atomically (temp file + os.replace, original
owner/mode preserved) so a concurrent writer can never see a half-written or
reverted cache. Run with sudo (the cache file is root-owned).

Usage:
  sudo python3 tools/email_dump_graph.py                 # all M365 boxes
  sudo ONLY_MAILBOX=daria.s@pflege-connect.work \
       python3 tools/email_dump_graph.py                 # one box (test first)
  sudo MONTHS=6 python3 tools/email_dump_graph.py         # override window
"""
import os, re, json, time, tempfile, urllib.request, urllib.parse, urllib.error
import msal

ENV      = "/home/claude/repo/pflege-board/.env"
OUT_ROOT = "/home/claude/repo/pflege-board/data/email-dump"
GRAPH    = "https://graph.microsoft.com/v1.0"
MONTHS   = int(os.environ.get("MONTHS", "6"))


def load_env(path):
    env = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if s.startswith("export "):
            s = s[7:]
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def atomic_write_cache(path, data):
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode)
    os.replace(tmp, path)


def since_iso(months):
    t = time.gmtime(time.time() - months * 30 * 24 * 3600)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def get_token_for(user, cfg):
    """Fresh cache read -> silent refresh -> atomic write-back. Narrow window."""
    cache = msal.SerializableTokenCache()
    cache.deserialize(open(cfg["cache"]).read())
    app = msal.PublicClientApplication(cfg["cid"], authority=cfg["auth"], token_cache=cache)
    acct = next((a for a in app.get_accounts()
                 if a.get("username", "").lower() == user.lower()), None)
    if not acct:
        return None, "account not in cache"
    r = app.acquire_token_silent(cfg["scopes"], account=acct)
    if cache.has_state_changed:
        atomic_write_cache(cfg["cache"], cache.serialize())
    if r and "access_token" in r:
        return r["access_token"], None
    return None, (r or {}).get("error", "no token") + ":" + str((r or {}).get("error_description", ""))[:120]


def graph_get(url, token):
    for _ in range(6):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Prefer": 'outlook.body-content-type="text"',
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(min(int(e.headers.get("Retry-After", "5")), 30))
                continue
            raise
    raise RuntimeError("too many retries: " + url)


def dump_mailbox(user, cfg, since):
    token, err = get_token_for(user, cfg)
    if err:
        return {"mailbox": user, "error": err}
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", user)
    d = os.path.join(OUT_ROOT, safe)
    os.makedirs(d, exist_ok=True)
    fields = ("id,conversationId,internetMessageId,subject,from,toRecipients,"
              "ccRecipients,receivedDateTime,sentDateTime,isDraft,bodyPreview,body")
    url = (GRAPH + "/me/messages"
           + "?$select=" + urllib.parse.quote(fields)
           + "&$filter=" + urllib.parse.quote("receivedDateTime ge " + since)
           + "&$orderby=" + urllib.parse.quote("receivedDateTime desc")
           + "&$top=50")
    n = 0
    convs = set()
    with open(os.path.join(d, "messages.jsonl"), "w", encoding="utf-8") as out:
        while url:
            page = graph_get(url, token)
            for m in page.get("value", []):
                rec = {
                    "id": m.get("id"),
                    "conversationId": m.get("conversationId"),
                    "internetMessageId": m.get("internetMessageId"),
                    "subject": m.get("subject"),
                    "from": (m.get("from") or {}).get("emailAddress", {}).get("address"),
                    "to": [a["emailAddress"]["address"] for a in m.get("toRecipients", [])],
                    "cc": [a["emailAddress"]["address"] for a in m.get("ccRecipients", [])],
                    "receivedDateTime": m.get("receivedDateTime"),
                    "sentDateTime": m.get("sentDateTime"),
                    "isDraft": m.get("isDraft"),
                    "bodyPreview": m.get("bodyPreview"),
                    "body": (m.get("body") or {}).get("content"),
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                convs.add(rec["conversationId"])
                n += 1
            url = page.get("@odata.nextLink")
    summary = {"mailbox": user, "messages": n, "threads": len(convs), "since": since}
    json.dump(summary, open(os.path.join(d, "_summary.json"), "w"), ensure_ascii=False, indent=2)
    return summary


def main():
    env = load_env(ENV)
    cfg = {
        "cache": env["MICROSOFT_GRAPH_MSAL_CACHE"],
        "cid": env["MICROSOFT_GRAPH_CLIENT_ID"],
        "auth": env["MICROSOFT_GRAPH_AUTHORITY"],
        "scopes": [x for x in env["MICROSOFT_GRAPH_SCOPES"].split()
                   if x.lower() not in ("openid", "profile", "offline_access")],
    }
    since = since_iso(MONTHS)
    only = os.environ.get("ONLY_MAILBOX")

    # list accounts from a fresh cache read (no refresh here)
    boot = msal.SerializableTokenCache()
    boot.deserialize(open(cfg["cache"]).read())
    users = [a["username"] for a in
             msal.PublicClientApplication(cfg["cid"], authority=cfg["auth"],
                                          token_cache=boot).get_accounts()]

    os.makedirs(OUT_ROOT, exist_ok=True)
    skip_existing = os.environ.get("SKIP_EXISTING")
    report = []
    for user in users:
        if only and user.lower() != only.lower():
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", user)
        if skip_existing and os.path.exists(os.path.join(OUT_ROOT, safe, "_summary.json")):
            res = {"mailbox": user, "skipped": "already dumped"}
            report.append(res)
            print(json.dumps(res, ensure_ascii=False))
            continue
        try:
            res = dump_mailbox(user, cfg, since)
        except Exception as e:
            res = {"mailbox": user, "error": str(e)[:200]}
        report.append(res)
        print(json.dumps(res, ensure_ascii=False))

    # hand dumped files back to the claude user
    try:
        import pwd
        u = pwd.getpwnam("claude")
        for root, _dirs, files in os.walk(OUT_ROOT):
            os.chown(root, u.pw_uid, u.pw_gid)
            for f in files:
                os.chown(os.path.join(root, f), u.pw_uid, u.pw_gid)
    except Exception as e:
        print("chown note:", e)

    print("=== dump summary ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
