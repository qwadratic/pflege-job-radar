#!/usr/bin/env python3
"""Stage-2 batches: every real engaged thread NOT in the Stage-1 sample, compact form
(100 threads/batch, body cap 1200, no from/to fields) for cheap Sonnet triage.
Also (re)builds attachments_all.jsonl from whatever attachment data exists now."""
import os, json
A = "/home/claude/repo/pflege-board/data/email-analysis"
R = os.path.join(A, "batches_rest"); os.makedirs(R, exist_ok=True); os.makedirs(os.path.join(A, "out"), exist_ok=True)
sel = set()
for fn in sorted(os.listdir(os.path.join(A, "batches"))):
    for l in open(os.path.join(A, "batches", fn), encoding="utf-8"):
        sel.add(json.loads(l)["thread_id"])
msgs = {}
for l in open(os.path.join(A, "messages.jsonl"), encoding="utf-8"):
    m = json.loads(l); msgs[m["msg_id"]] = m
AUTO = {"ooo_auto", "bounce"}
rest = []
for l in open(os.path.join(A, "threads.jsonl"), encoding="utf-8"):
    t = json.loads(l)
    if t["engagement"] != "engaged" or t["thread_id"] in sel:
        continue
    if not any(msgs[i]["direction"] == "in" and msgs[i]["body_len"] > 0 and not (set(msgs[i]["flags"]) & AUTO) for i in t["msg_ids"]):
        continue
    rest.append(t)
for fn in os.listdir(R):
    os.remove(os.path.join(R, fn))
n = tot = 0
for i in range(0, len(rest), 100):
    n += 1
    with open(os.path.join(R, "rest_%03d.jsonl" % n), "w", encoding="utf-8") as f:
        for t in rest[i:i + 100]:
            ms = [{"date": msgs[x]["date"], "dir": msgs[x]["direction"], "subject": msgs[x]["subject"],
                   "body": msgs[x]["body_clean"][:1200], "attachments": msgs[x]["attachments"], "flags": msgs[x]["flags"]}
                  for x in t["msg_ids"]]
            s = json.dumps({"thread_id": t["thread_id"], "org": t["primary_ext"],
                            "stats": {k: t[k] for k in ("n", "n_in", "n_out", "duration_days", "max_followups", "flags", "exceptional")},
                            "messages": ms}, ensure_ascii=False)
            tot += len(s); f.write(s + "\n")
print(json.dumps({"rest_threads": len(rest), "rest_batches": n, "approx_tokens": tot // 4}))
na = 0
with open(os.path.join(A, "attachments_all.jsonl"), "w", encoding="utf-8") as f:
    for m in msgs.values():
        for a, te in zip(m["attachments"], m["att_types"]):
            f.write(json.dumps({"mailbox": m["mailbox"], "source": m["source"], "dir": m["direction"], "name": a,
                                "type": te[0], "ext": te[1], "subject": m["subject"], "org": m["primary_ext"],
                                "thread_id": m.get("thread_id"), "date": m["date"]}, ensure_ascii=False) + "\n")
            na += 1
print(json.dumps({"attachments_all": na}))
