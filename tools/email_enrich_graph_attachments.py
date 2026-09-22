#!/usr/bin/env python3
"""Enrich the Graph (M365) dumps with attachment names — the base dump has none.

Queries /me/messages with hasAttachments eq true inside the same window and
writes data/email-dump/<mailbox>/attachments.jsonl, one line per message:
  {internetMessageId, subject, receivedDateTime, attachments:[{name,contentType,size,isInline}]}
Read-only on mail. Same shared-cache handling as the dumper (fresh read,
silent refresh, ATOMIC write-back). Run with `sudo -E` (cache is root-owned).
Re-run tools/email_index.py afterwards — it merges attachments.jsonl if present.

Usage:
  sudo -E python3 tools/email_enrich_graph_attachments.py
  sudo -E ONLY_MAILBOX=daria.s@pflege-connect.work python3 tools/email_enrich_graph_attachments.py
"""
import os, re, sys, json, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from email_dump_graph import load_env, get_token_for, graph_get, since_iso, ENV, OUT_ROOT, GRAPH, MONTHS  # noqa: E402
import msal  # noqa: E402


def main():
    env = load_env(ENV)
    cfg = {
        "cache": env["MICROSOFT_GRAPH_MSAL_CACHE"], "cid": env["MICROSOFT_GRAPH_CLIENT_ID"],
        "auth": env["MICROSOFT_GRAPH_AUTHORITY"],
        "scopes": [x for x in env["MICROSOFT_GRAPH_SCOPES"].split()
                   if x.lower() not in ("openid", "profile", "offline_access")],
    }
    since = since_iso(MONTHS)
    boot = msal.SerializableTokenCache()
    boot.deserialize(open(cfg["cache"]).read())
    users = [a["username"] for a in msal.PublicClientApplication(
        cfg["cid"], authority=cfg["auth"], token_cache=boot).get_accounts()]
    only = os.environ.get("ONLY_MAILBOX")
    report = []
    for user in users:
        if only and user.lower() != only.lower():
            continue
        token, err = get_token_for(user, cfg)
        if err:
            report.append({"mailbox": user, "error": err})
            print(json.dumps(report[-1]))
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", user)
        d = os.path.join(OUT_ROOT, safe)
        os.makedirs(d, exist_ok=True)
        url = (GRAPH + "/me/messages"
               + "?$select=" + urllib.parse.quote("id,internetMessageId,subject,receivedDateTime,hasAttachments")
               + "&$filter=" + urllib.parse.quote("hasAttachments eq true and receivedDateTime ge " + since)
               + "&$expand=" + urllib.parse.quote("attachments($select=name,contentType,size,isInline)")
               + "&$top=50")
        n = na = 0
        with open(os.path.join(d, "attachments.jsonl"), "w", encoding="utf-8") as out:
            while url:
                page = graph_get(url, token)
                for m in page.get("value", []):
                    atts = [{"name": a.get("name"), "contentType": a.get("contentType"),
                             "size": a.get("size"), "isInline": a.get("isInline")}
                            for a in m.get("attachments", [])]
                    out.write(json.dumps({"internetMessageId": m.get("internetMessageId"),
                                          "subject": m.get("subject"),
                                          "receivedDateTime": m.get("receivedDateTime"),
                                          "attachments": atts}, ensure_ascii=False) + "\n")
                    n += 1
                    na += len(atts)
                url = page.get("@odata.nextLink")
        res = {"mailbox": user, "messages_with_attachments": n, "attachments": na}
        report.append(res)
        print(json.dumps(res))
    try:
        import pwd
        u = pwd.getpwnam("claude")
        for root, _ds, files in os.walk(OUT_ROOT):
            os.chown(root, u.pw_uid, u.pw_gid)
            for f in files:
                os.chown(os.path.join(root, f), u.pw_uid, u.pw_gid)
    except Exception as e:
        print("chown note:", e)
    print("=== attachments summary ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
