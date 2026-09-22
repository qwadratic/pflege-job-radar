#!/usr/bin/env python3
"""Stratified sample + population stats for the agent classification pass.

Deterministic. Reads data/email-analysis/{messages,threads,orgs}.jsonl.
Writes into data/email-analysis/:
  quant_stats.json         population-level numbers over ALL threads (no sampling)
  template_families.jsonl  outbound grouped by normalized subject (+variants), reply rates, examples
  cold_sequences.jsonl     EVERY cold_no_reply thread with >=2 outbound: cadence + subjects
  pushback_inbound.jsonl   EVERY human inbound flagged optout/legal/reject (how clinics say no)
  batches/batch_NNN.jsonl  threads for agent deep-reading (tiered)
  sample_manifest.json     explicit selection rules + coverage numbers — nothing is silently dropped

Tiers: A = ALL real engaged threads that are exceptional/commercial/hire/interview/legal/etc.
       B = remaining real engaged threads (all if <= TIER_B_MAX, else stratified random sample)
       C = random cold sequences (cadence is fully covered deterministically anyway)
       D = random inbound-only pushback threads (all texts are in pushback_inbound.jsonl anyway)
"""
import os, json, random, re, collections, statistics
from datetime import datetime

A = "/home/claude/repo/pflege-board/data/email-analysis"
B = os.path.join(A, "batches")
os.makedirs(B, exist_ok=True)
BATCH      = int(os.environ.get("BATCH", "60"))
TIER_B_MAX = int(os.environ.get("TIER_B_MAX", "600"))
BODY_CAP   = int(os.environ.get("BODY_CAP", "1800"))
random.seed(20260917)

CLINIC = re.compile(r"klinik|krankenhaus|hospital|pflege|senior|alten|caritas|diakon|\bawo|\bdrk|\basb|johanniter|"
                    r"malteser|\bmed|gesundheit|reha|hospiz|sozial|stift|heim|residenz|vivantes|helios|asklepios|"
                    r"\bsana\b|ameos|schoen|paracelsus|mediclin|kursana|korian|alloheim|pro-seniore|azurit|curata|"
                    r"charleston|domicil|\bev-|kath|\bst-|sankt|betreu|wohnpark|lebenshilfe|sozialstation|ambulant|intensiv", re.I)

msgs = {}
for l in open(os.path.join(A, "messages.jsonl"), encoding="utf-8"):
    m = json.loads(l)
    msgs[m["msg_id"]] = m
threads = [json.loads(l) for l in open(os.path.join(A, "threads.jsonl"), encoding="utf-8")]
orgs = {o["domain"]: o for o in (json.loads(l) for l in open(os.path.join(A, "orgs.jsonl"), encoding="utf-8"))}


def clinic_like(dom):
    return bool(dom and CLINIC.search(dom))


for o in orgs.values():
    o["clinic_like"] = clinic_like(o["domain"])

AUTO = {"ooo_auto", "bounce"}


def human_inbound(t):
    for mid in t["msg_ids"]:
        m = msgs[mid]
        if m["direction"] == "in" and m["body_len"] > 0 and not (set(m["flags"]) & AUTO):
            return True
    return False


for t in threads:
    t["real"] = t["engagement"] == "engaged" and human_inbound(t)
    t["clinic_like"] = clinic_like(t["primary_ext"])


# ---------- quant stats over ALL threads ----------
def dist(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    q = lambda p: vals[min(n - 1, int(p * n))]
    return {"n": n, "median": statistics.median(vals), "p25": q(0.25), "p75": q(0.75), "p90": q(0.9),
            "mean": round(sum(vals) / n, 2)}


real = [t for t in threads if t["real"]]
cold = [t for t in threads if t["engagement"] == "cold_no_reply"]
outreach = [t for t in threads if t["engagement"] in ("cold_no_reply", "engaged")]
by_k = {}
for k in range(1, 7):
    pool = [t for t in outreach if t["n_out"] >= k]
    by_k[k] = {"threads": len(pool),
               "real_reply_rate": round(sum(1 for t in pool if t["real"]) / len(pool), 4) if pool else None}
touch_before_reply = collections.Counter()
for t in real:
    c = 0
    for mid in t["msg_ids"]:
        m = msgs[mid]
        if m["direction"] == "out":
            c += 1
        elif m["direction"] == "in" and not (set(m["flags"]) & AUTO):
            touch_before_reply[c] += 1
            break
weekly = collections.defaultdict(collections.Counter)
for m in msgs.values():
    if m["direction"] == "out" and m["date"]:
        weekly[m["mailbox"]][datetime.fromisoformat(m["date"]).strftime("%G-W%V")] += 1
inbound_auto = sum(1 for m in msgs.values() if m["direction"] == "in" and (set(m["flags"]) & AUTO))
inbound_total = sum(1 for m in msgs.values() if m["direction"] == "in")
per_mb = {}
for mb in {m["mailbox"] for m in msgs.values()}:
    ts = [t for t in outreach if mb in t["mailboxes"]]
    per_mb[mb] = {"outreach_threads": len(ts),
                  "real_reply_rate": round(sum(1 for t in ts if t["real"]) / len(ts), 4) if ts else None,
                  "optout_threads": sum(1 for t in ts if "optout" in t["flags"]),
                  "bounce_threads": sum(1 for t in ts if "bounce" in t["flags"])}
eng_orgs = [o for o in orgs.values() if o["is_engaged"]]
quant = {
    "population": {"threads": len(threads), "engaged": sum(1 for t in threads if t["engagement"] == "engaged"),
                   "real_engaged_(human_inbound)": len(real), "cold_no_reply": len(cold),
                   "inbound_only": sum(1 for t in threads if t["engagement"] == "inbound_only")},
    "inbound_auto_share": round(inbound_auto / inbound_total, 4) if inbound_total else None,
    "real_reply_rate_overall": round(len(real) / len(outreach), 4) if outreach else None,
    "reply_rate_by_min_touches": by_k,
    "reply_arrived_after_touch_no": dict(sorted(touch_before_reply.items())),
    "followups_max_dist": dict(sorted(collections.Counter(t["max_followups"] for t in outreach).items())),
    "outbound_gap_days": dist([g for t in outreach for g in t["outbound_gaps_days"]]),
    "first_reply_gap_hours": dist([t["first_reply_gap_h"] for t in real]),
    "our_response_hours_median_per_thread": dist([t["our_response_h_median"] for t in real]),
    "real_thread_len": dist([t["n"] for t in real]),
    "real_thread_duration_days": dist([t["duration_days"] for t in real]),
    "optout_rate_of_outreach": round(sum(1 for t in outreach if "optout" in t["flags"]) / len(outreach), 4) if outreach else None,
    "bounce_rate_of_outreach": round(sum(1 for t in outreach if "bounce" in t["flags"]) / len(outreach), 4) if outreach else None,
    "legal_complaint_threads": sum(1 for t in threads if "legal_complaint" in t["flags"]),
    "per_mailbox": per_mb,
    "weekly_outbound_per_mailbox": {mb: dict(sorted(c.items())) for mb, c in weekly.items()},
    "orgs": {"total": len(orgs), "engaged": len(eng_orgs),
             "clinic_like_total": sum(1 for o in orgs.values() if o["clinic_like"]),
             "clinic_like_engaged": sum(1 for o in eng_orgs if o["clinic_like"]),
             "engaged_with_people_joining_later": sum(1 for o in eng_orgs if o["people_joined_later"]),
             "people_per_engaged_org": dist([o["n_people"] for o in eng_orgs]),
             "threads_per_engaged_org": dist([o["n_threads"] for o in eng_orgs]),
             "engaged_orgs_touched_by_multiple_of_our_mailboxes": sum(1 for o in eng_orgs if len(o["mailboxes"]) > 1)},
    "flags_in_real_threads": dict(collections.Counter(f for t in real for f in t["flags"])),
    "exceptional_in_real_threads": dict(collections.Counter(e for t in real for e in t["exceptional"])),
}
json.dump(quant, open(os.path.join(A, "quant_stats.json"), "w"), ensure_ascii=False, indent=2)

# ---------- template families by normalized subject ----------
fam = collections.defaultdict(lambda: {"count": 0, "mailboxes": collections.Counter(), "variants": collections.Counter(),
                                       "positions": collections.Counter(), "threads": set(), "real_threads": set(),
                                       "examples": [], "first": None, "last": None})
for t in outreach:
    pos = 0
    for mid in t["msg_ids"]:
        m = msgs[mid]
        if m["direction"] != "out":
            continue
        pos += 1
        e = fam[m["subj_norm"] or "(no subject)"]
        e["count"] += 1
        e["mailboxes"][m["mailbox"]] += 1
        e["positions"][pos] += 1
        if m["tmpl"]:
            e["variants"][m["tmpl"]] += 1
        e["threads"].add(t["thread_id"])
        if t["real"]:
            e["real_threads"].add(t["thread_id"])
        if len(e["examples"]) < 2 and m["body_clean"] and all(m["body_clean"][:200] != x["body"][:200] for x in e["examples"]):
            e["examples"].append({"mailbox": m["mailbox"], "date": m["date"], "body": m["body_clean"][:1200]})
        if m["date"]:
            e["first"] = min(e["first"], m["date"]) if e["first"] else m["date"]
            e["last"] = max(e["last"], m["date"]) if e["last"] else m["date"]
rows = []
for subj, e in fam.items():
    rows.append({"subject": subj, "count": e["count"], "mailboxes": dict(e["mailboxes"]),
                 "n_variants": len(e["variants"]), "positions": dict(e["positions"]),
                 "threads": len(e["threads"]), "real_reply_threads": len(e["real_threads"]),
                 "real_reply_rate": round(len(e["real_threads"]) / len(e["threads"]), 4) if e["threads"] else None,
                 "first": e["first"], "last": e["last"], "examples": e["examples"]})
rows.sort(key=lambda r: -r["count"])
with open(os.path.join(A, "template_families.jsonl"), "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ---------- cold sequences (ALL, deterministic) ----------
with open(os.path.join(A, "cold_sequences.jsonl"), "w", encoding="utf-8") as f:
    for t in cold:
        if t["n_out"] < 2:
            continue
        seq, prev = [], None
        for mid in t["msg_ids"]:
            m = msgs[mid]
            if m["direction"] != "out":
                continue
            gap = None
            if prev and m["date"] and prev["date"]:
                gap = round((datetime.fromisoformat(m["date"]) - datetime.fromisoformat(prev["date"])).total_seconds() / 86400, 1)
            seq.append({"gap_days": gap, "subject": m["subj_norm"], "tmpl": m["tmpl"], "snippet": m["body_clean"][:160]})
            prev = m
        f.write(json.dumps({"thread_id": t["thread_id"], "mailboxes": t["mailboxes"], "org": t["primary_ext"],
                            "clinic_like": t["clinic_like"], "seq": seq}, ensure_ascii=False) + "\n")

# ---------- pushback inbound (ALL) ----------
PB = {"optout", "legal_complaint", "reject"}
n_pb = 0
with open(os.path.join(A, "pushback_inbound.jsonl"), "w", encoding="utf-8") as f:
    for m in msgs.values():
        if m["direction"] == "in" and (set(m["flags"]) & PB) and not (set(m["flags"]) & AUTO):
            f.write(json.dumps({"thread_id": m.get("thread_id"), "org": m["primary_ext"], "date": m["date"],
                                "flags": m["flags"], "subject": m["subject"], "text": m["body_clean"][:500]},
                               ensure_ascii=False) + "\n")
            n_pb += 1

# ---------- tiers ----------
EXC_A = {"commercial", "hire_stage", "multi_person", "long_conversation", "long_running", "doc_heavy"}
FLAG_A = {"interview", "hire", "fee_invoice", "contract", "legal_complaint", "foreign_nurse", "reject"}
tierA = [t for t in real if (set(t["exceptional"]) & EXC_A) or (set(t["flags"]) & FLAG_A)]
aids = {t["thread_id"] for t in tierA}
restB = [t for t in real if t["thread_id"] not in aids]


def bucket(t):
    return (t["clinic_like"], "short" if t["n"] <= 3 else "mid" if t["n"] <= 6 else "long")


strata = collections.defaultdict(list)
for t in restB:
    strata[bucket(t)].append(t)
if len(restB) <= TIER_B_MAX:
    tierB = restB
else:
    tierB = []
    for key, lst in strata.items():
        k = max(1, round(TIER_B_MAX * len(lst) / len(restB)))
        random.shuffle(lst)
        tierB.extend(lst[:k])
cold2 = [t for t in cold if t["n_out"] >= 2]
random.shuffle(cold2)
tierC = cold2[:80]
inb = [t for t in threads if t["engagement"] == "inbound_only" and (set(t["flags"]) & PB)]
random.shuffle(inb)
tierD = inb[:60]


def pack(t, tier):
    out = []
    for mid in t["msg_ids"]:
        m = msgs[mid]
        body = m["body_clean"]
        out.append({"date": m["date"], "dir": m["direction"], "from": m["from"], "to": m["to"], "cc": m["cc"],
                    "subject": m["subject"], "body": body[:BODY_CAP], "truncated": len(body) > BODY_CAP,
                    "attachments": m["attachments"], "flags": m["flags"], "mailbox": m["mailbox"]})
    o = orgs.get(t["primary_ext"]) or {}
    return {"thread_id": t["thread_id"], "tier": tier, "org": t["primary_ext"], "clinic_like": t["clinic_like"],
            "org_context": {"n_threads_with_org": o.get("n_threads"), "n_people": o.get("n_people"),
                            "people_joined_later": o.get("people_joined_later"), "our_mailboxes": o.get("mailboxes"),
                            "first_contact": o.get("first"), "last_contact": o.get("last")},
            "stats": {k: t[k] for k in ("n", "n_in", "n_out", "duration_days", "max_followups", "first_reply_gap_h",
                                        "our_response_h_median", "engagement", "flags", "exceptional")},
            "messages": out}


allsel = [(t, "A") for t in tierA] + [(t, "B") for t in tierB] + [(t, "C") for t in tierC] + [(t, "D") for t in tierD]
for fn in os.listdir(B):
    os.remove(os.path.join(B, fn))
batches = []
for i in range(0, len(allsel), BATCH):
    chunk = allsel[i:i + BATCH]
    name = "batch_%03d.jsonl" % (len(batches) + 1)
    chars = 0
    with open(os.path.join(B, name), "w", encoding="utf-8") as f:
        for t, tier in chunk:
            s = json.dumps(pack(t, tier), ensure_ascii=False)
            chars += len(s)
            f.write(s + "\n")
    batches.append({"file": name, "threads": len(chunk),
                    "tiers": dict(collections.Counter(tier for _t, tier in chunk)), "approx_tokens": chars // 4})
manifest = {
    "rules": {"real": "engagement==engaged AND >=1 inbound that is not ooo/bounce and has a body",
              "tierA": "ALL real threads with exceptional in %s or flags in %s" % (sorted(EXC_A), sorted(FLAG_A)),
              "tierB": "remaining real threads; ALL if <= TIER_B_MAX else stratified random sample by (clinic_like, length), seed 20260917",
              "tierC": "random 80 of cold_no_reply threads with >=2 outbound; full cadence data is deterministic in cold_sequences.jsonl",
              "tierD": "random 60 inbound_only threads flagged optout/legal/reject; all such texts are in pushback_inbound.jsonl",
              "body_cap_chars": BODY_CAP, "batch_size": BATCH},
    "coverage": {"real_engaged_total": len(real), "tierA_all": len(tierA), "tierB_pool": len(restB),
                 "tierB_selected": len(tierB), "tierC_pool": len(cold2), "tierC_selected": len(tierC),
                 "tierD_pool": len(inb), "tierD_selected": len(tierD), "pushback_inbound_all": n_pb,
                 "deep_read_threads": len(allsel),
                 "deep_read_share_of_real_engaged": round((len(tierA) + len(tierB)) / len(real), 4) if real else None,
                 "NOT_deep_read_real_engaged": len(restB) - len(tierB)},
    "batches": batches, "total_approx_tokens": sum(b["approx_tokens"] for b in batches),
}
json.dump(manifest, open(os.path.join(A, "sample_manifest.json"), "w"), ensure_ascii=False, indent=2)
print(json.dumps({"quant_population": quant["population"], "coverage": manifest["coverage"],
                  "batches": len(batches), "total_approx_tokens": manifest["total_approx_tokens"],
                  "template_families": len(rows)}, ensure_ascii=False, indent=2))
