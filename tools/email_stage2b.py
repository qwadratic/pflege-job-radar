#!/usr/bin/env python3
"""Stage 2b: targeted deterministic strata the Stage-1 critic showed were unread, across ALL engagement
classes, plus partner re-keying and an org-topic table. Pseudonymises from/to/cc at pack time.
Writes data/email-analysis/batches_stage2b/b2_NNN.jsonl, strata_manifest.json, org_topics.jsonl, org_topics_stats.json."""
import os, re, json, hashlib, collections
from datetime import datetime, timedelta
A = "/home/claude/repo/pflege-board/data/email-analysis"; B = os.path.join(A, "batches_stage2b"); os.makedirs(B, exist_ok=True)
for fn in os.listdir(B): os.remove(os.path.join(B, fn))
msgs = {}
for l in open(os.path.join(A, "messages.jsonl"), encoding="utf-8"):
    m = json.loads(l); msgs[m["msg_id"]] = m
threads = {}
for l in open(os.path.join(A, "threads.jsonl"), encoding="utf-8"):
    t = json.loads(l); threads[t["thread_id"]] = t
orgs = {o["domain"]: o for o in (json.loads(l) for l in open(os.path.join(A, "orgs.jsonl"), encoding="utf-8"))}
ce = json.load(open(os.path.join(A, "clinic_engaged_set.json"))); WARM = set(ce["warm_domains"])
read = set()
for d in ("batches", "batches_stage2"):
    for fn in os.listdir(os.path.join(A, d)):
        for l in open(os.path.join(A, d, fn), encoding="utf-8"): read.add(json.loads(l)["thread_id"])
MB = sorted({m["mailbox"] for m in msgs.values()})
LABEL = {}
for mb in MB:
    LABEL[mb] = ("K" if mb.endswith(".agency") else "M") + str(len([x for x in LABEL.values() if x[0] == ("K" if mb.endswith(".agency") else "M")]) + 1)
OUR_DOM = {mb.split("@")[1] for mb in MB} | {"kindt.agency", "ki-agent.agency", "ki-ndt.agency", "ki-workflow.agency"}
PARTNER_DOM = {"ndt-group.agency"}
TAG = re.compile(r"\[(SNOV|WRM)\]|\bwsn\b|warm-?up", re.I)
NDR = re.compile(r"undeliverable|delivery (status|failure|has failed)|unzustellbar|mail delivery|returned mail|nicht zugestellt|failure notice|delivery notification|nicht zustellbar", re.I)
def pseud(a):
    if not a: return None
    a = a.lower()
    if a in LABEL: return LABEL[a]
    d = a.split("@")[-1]
    if d in PARTNER_DOM: return "partner@" + d
    return "p" + hashlib.md5(a.encode()).hexdigest()[:5] + "@" + d
def subj(t): return " | ".join(sorted({msgs[i]["subj_norm"] for i in t["msg_ids"] if msgs[i]["subj_norm"]}))[:300]
def anymsg(t, f): return any(f(msgs[i]) for i in t["msg_ids"])
def is_partner_thread(t): return anymsg(t, lambda m: m["from"] and m["from"].split("@")[-1] in PARTNER_DOM)
def warm(t): return anymsg(t, lambda m: TAG.search(m["subject"] or ""))
EXC_A = {"commercial", "hire_stage", "multi_person", "long_conversation", "long_running", "doc_heavy"}
FLAG_A = {"interview", "hire", "fee_invoice", "contract", "legal_complaint", "foreign_nurse", "reject"}
STRATA = [
 ("partner_copies", lambda t: is_partner_thread(t)),
 ("ndr", lambda t: anymsg(t, lambda m: NDR.search(m["subject"] or "") or (m["from"] and re.search(r"mailer-daemon|postmaster", m["from"], re.I)))),
 ("invoice_subject", lambda t: re.search(r"rechnung|zahlung|mahnung|invoice|honorar|provision", subj(t), re.I) is not None),
 ("vorstellung_kurzprofil", lambda t: re.search(r"vorstellung von|kurzprofil|kandidat|profil", subj(t), re.I) is not None and anymsg(t, lambda m: m["direction"] == "out")),
 ("interview", lambda t: re.search(r"interview|vorstellungsgespr|kennenlerngespr|einladung|invitation|termin", subj(t), re.I) is not None or "interview" in t["flags"]),
 ("hire", lambda t: "hire" in t["flags"]),
 ("candidate_side", lambda t: re.search(r"ihre bewerbung|bewerbung als|anerkennung|defizit|kenntnispr", subj(t), re.I) is not None),
 ("internal", lambda t: t["engagement"] == "internal"),
 ("tierA_dropped", lambda t: t["engagement"] == "engaged" and ((set(t["flags"]) & FLAG_A) or (set(t["exceptional"]) & EXC_A))),
 ("contract_cold", lambda t: t["engagement"] == "cold_no_reply" and "contract" in t["flags"] and anymsg(t, lambda m: m["direction"] == "out" and (any("vertrag" in (a or "").lower() for a in m["attachments"]) or re.search(r"personalvermittlungsvertrag|vertragsentwurf|vertrag im anhang|anbei.*vertrag", m["body_clean"] or "", re.I)))),
 ("fee_invoice_flag", lambda t: "fee_invoice" in t["flags"]),
]
assigned = {}; tags = collections.defaultdict(list)
for tid, t in threads.items():
    if tid in read or warm(t): continue
    if t["primary_ext"] in WARM: continue
    for name, f in STRATA:
        try: hit = f(t)
        except Exception: hit = False
        if hit:
            tags[tid].append(name)
            if tid not in assigned: assigned[tid] = name
# drop obvious vendor spam from invoice/fee strata: inbound_only with no clinic-ish word anywhere
CLIN = re.compile(r"pflege|klinik|krankenhaus|examiniert|fachkr|vermittl|kandidat|profil|termin|vertrag", re.I)
def clinicish(t): return anymsg(t, lambda m: CLIN.search((m["subject"] or "") + " " + (m["body_clean"] or "")[:1000]))
sel = []
dropped = collections.Counter()
for tid, s in assigned.items():
    t = threads[tid]
    if s in ("invoice_subject", "fee_invoice_flag", "ndr") and t["engagement"] == "inbound_only" and not clinicish(t):
        dropped[s] += 1; continue
    sel.append(tid)
counts = collections.Counter(assigned[i] for i in sel)
CAP = 2000
def pack(t):
    ms = [{"date": msgs[i]["date"], "dir": ("partner" if msgs[i]["from"] and msgs[i]["from"].split("@")[-1] in PARTNER_DOM else msgs[i]["direction"]),
           "from": pseud(msgs[i]["from"]), "to": [pseud(a) for a in msgs[i]["to"]], "cc": [pseud(a) for a in msgs[i]["cc"]],
           "subject": msgs[i]["subject"], "body": msgs[i]["body_clean"][:CAP], "truncated": len(msgs[i]["body_clean"]) > CAP,
           "attachments": msgs[i]["attachments"], "flags": msgs[i]["flags"], "mailbox": LABEL.get(msgs[i]["mailbox"], msgs[i]["mailbox"])} for i in t["msg_ids"]]
    o = orgs.get(t["primary_ext"]) or {}
    return {"thread_id": t["thread_id"], "stratum": assigned[t["thread_id"]], "all_strata": tags[t["thread_id"]], "org": t["primary_ext"], "engagement": t["engagement"],
            "org_context": {"n_threads_with_org": o.get("n_threads"), "n_people": o.get("n_people"), "our_mailboxes": [LABEL.get(x, x) for x in (o.get("mailboxes") or [])]},
            "stats": {k: t[k] for k in ("n", "n_in", "n_out", "duration_days", "max_followups", "flags", "exceptional")}, "messages": ms}
order = [n for n, _f in STRATA]
sel.sort(key=lambda i: (order.index(assigned[i]), threads[i]["first"] or ""))
nb = tot = 0; batches = []
for i in range(0, len(sel), 60):
    nb += 1; chunk = sel[i:i + 60]; chars = 0
    with open(os.path.join(B, "b2_%03d.jsonl" % nb), "w", encoding="utf-8") as f:
        for tid in chunk:
            s = json.dumps(pack(threads[tid]), ensure_ascii=False); chars += len(s); f.write(s + "\n")
    batches.append({"file": "b2_%03d.jsonl" % nb, "threads": len(chunk), "strata": dict(collections.Counter(assigned[x] for x in chunk)), "approx_tokens": chars // 4}); tot += chars
manifest = {"strata_counts_selected": dict(counts), "dropped_non_clinicish_inbound_only": dict(dropped), "already_read_excluded": len(read), "selected": len(sel), "batches": batches, "approx_tokens": tot // 4, "mailbox_labels": LABEL}
json.dump(manifest, open(os.path.join(A, "strata_manifest.json"), "w"), ensure_ascii=False, indent=2)
# ---- org-topics: partner re-keyed, all engagement classes, windows split at >45 d gaps
CALL = re.compile(r"kennenlerngespr|termin|einladung|invitation|teams|webex|zoom|gespräch", re.I)
DOCS = re.compile(r"unterlagen|präsentation|vertrag|agb|konditionen|profil|kurzprofil", re.I)
INTV = re.compile(r"interview|vorstellungsgespr", re.I)
MONEY = re.compile(r"rechnung|honorar|provision|gebühr|monatsgeh|jahresgeh|%|prozent|zahlung", re.I)
GEN1 = re.compile(r"examinierte pflegefachkr")
ev = collections.defaultdict(list)
for m in msgs.values():
    if not m["date"] or TAG.search(m["subject"] or ""): continue
    fd = m["from"].split("@")[-1] if m["from"] else None
    if m["direction"] == "out" or fd in PARTNER_DOM:
        doms = {a.split("@")[-1] for a in m["to"] + m["cc"]} - OUR_DOM - PARTNER_DOM; who = "partner" if fd in PARTNER_DOM else "us"
    elif fd and fd not in OUR_DOM:
        doms = {fd}; who = "clinic"
    else: continue
    for d in doms:
        if d in WARM: continue
        ev[d].append((datetime.fromisoformat(m["date"]), who, m))
topics = []; stats = collections.Counter()
for d, evs in ev.items():
    if not any(GEN1.search(m["subj_norm"] or "") for _t, _w, m in evs) and not any(w == "clinic" for _t, w, _m in evs): continue
    evs.sort(key=lambda x: x[0]); cur = []; wins = []
    for e in evs:
        if cur and (e[0] - cur[-1][0]) > timedelta(days=45): wins.append(cur); cur = []
        cur.append(e)
    if cur: wins.append(cur)
    for w in wins:
        txt = lambda m: (m["subject"] or "") + " " + (m["body_clean"] or "")[:800]
        rec = {"org": d, "first": w[0][0].isoformat(), "last": w[-1][0].isoformat(), "days": round((w[-1][0] - w[0][0]).total_seconds() / 86400, 1),
               "n_msgs": len(w), "n_threads": len({m.get("thread_id") for _t, _w, m in w}), "our_mailboxes": sorted({LABEL.get(m["mailbox"], m["mailbox"]) for _t, wh, m in w if wh == "us"}),
               "partner_involved": any(wh == "partner" for _t, wh, _m in w), "clinic_people": len({m["from"] for _t, wh, m in w if wh == "clinic"}),
               "our_people_targeted": len({a for _t, wh, m in w if wh != "clinic" for a in m["to"] if a.split("@")[-1] == d}),
               "gen1_touches": sum(1 for _t, wh, m in w if wh == "us" and GEN1.search(m["subj_norm"] or "")),
               "clinic_human_msgs": sum(1 for _t, wh, m in w if wh == "clinic" and not (set(m["flags"]) & {"ooo_auto", "bounce"}) and m["body_len"] > 0),
               "ev_call": any(CALL.search(txt(m)) for _t, wh, m in w if wh != "clinic" and not GEN1.search(m["subj_norm"] or "")) or any(CALL.search(m["subject"] or "") for _t, wh, m in w if wh == "clinic"),
               "ev_docs": any(DOCS.search(m["subject"] or "") or any("vertrag" in (a or "").lower() or "einstellung" in (a or "").lower() for a in m["attachments"]) for _t, wh, m in w if wh != "clinic"),
               "ev_interview": any(INTV.search(m["subject"] or "") for _t, _wh, m in w),
               "ev_contract_signed": any(re.search(r"unterschrieben|unterzeichnet|gegengezeichnet|signed", txt(m), re.I) for _t, wh, m in w if wh == "clinic"),
               "ev_money": any(MONEY.search(txt(m)) for _t, _wh, m in w if not GEN1.search(m["subj_norm"] or "")),
               "ev_invoice_sent": any(re.search(r"rechnung", m["subject"] or "", re.I) for _t, wh, m in w if wh != "clinic"),
               "engagement_classes": sorted({threads[m["thread_id"]]["engagement"] for _t, _wh, m in w if m.get("thread_id") in threads})}
        topics.append(rec)
        stats["topics"] += 1
        for k in ("partner_involved", "ev_call", "ev_docs", "ev_interview", "ev_contract_signed", "ev_money", "ev_invoice_sent"):
            if rec[k]: stats[k] += 1
        if rec["clinic_human_msgs"]: stats["with_clinic_human"] += 1
        if rec["n_threads"] > 1: stats["multi_thread"] += 1
        if len(rec["our_mailboxes"]) > 1: stats["multi_mailbox"] += 1
        if rec["clinic_people"] > 1: stats["multi_clinic_people"] += 1
with open(os.path.join(A, "org_topics.jsonl"), "w", encoding="utf-8") as f:
    for r in sorted(topics, key=lambda r: (-r["ev_interview"], -r["ev_contract_signed"], -r["ev_call"], -r["clinic_human_msgs"])): f.write(json.dumps(r, ensure_ascii=False) + "\n")
stats["orgs"] = len({r["org"] for r in topics})
json.dump(dict(stats), open(os.path.join(A, "org_topics_stats.json"), "w"), indent=2)
print(json.dumps({"strata": dict(counts), "dropped": dict(dropped), "selected": len(sel), "batches": nb, "approx_tokens": tot // 4, "org_topics": dict(stats)}, ensure_ascii=False, indent=1))
