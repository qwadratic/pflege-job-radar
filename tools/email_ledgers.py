#!/usr/bin/env python3
"""Deterministic Stage-2 ledgers over the clinic-gated corpus (no LLM).
Gate: campaign-target domains (received a Gen1 'examinierte pflegefachkr*' send) ∪ clinic_engaged_set.
Outputs data/email-analysis/out/ledgers.json + ledgers.md:
  1. true bounce ledger (NDR threads joined to sends) per mailbox/week/domain
  2. compliance ledger: outbound touches to a domain AFTER a human decline/stop/redirect
  3. response ledger: our answer latency to EVERY human clinic reply, from ANY mailbox
  4. recontact ledger: new Gen1 cold thread to an org after a human reply elsewhere, and what followed
  5. replier role (signature titles) vs outcome
  6. Gen1 funnel per mailbox / per touch with the clinic gate
Domains and counts only — no names."""
import json, re, os, collections, statistics
from datetime import datetime, timedelta
A = "/home/claude/repo/pflege-board/data/email-analysis"; OUT = os.path.join(A, "out")
msgs = {}
for l in open(os.path.join(A, "messages.jsonl"), encoding="utf-8"):
    m = json.loads(l); msgs[m["msg_id"]] = m
threads = {}
for l in open(os.path.join(A, "threads.jsonl"), encoding="utf-8"):
    t = json.loads(l); threads[t["thread_id"]] = t
ce = json.load(open(os.path.join(A, "clinic_engaged_set.json")))
FINAL = set(ce["final_ids"]); WARM = set(ce["warm_domains"])
OUR_DOM = {m["mailbox"].split("@")[1] for m in msgs.values()} | {"kindt.agency", "ki-agent.agency", "ki-ndt.agency", "ki-workflow.agency", "ndt-group.agency"}
GEN1 = re.compile(r"examinierte pflegefachkr")
AUTO_FROM = re.compile(r"mailer-daemon|postmaster|no-?reply|noreply|donotreply|bounce", re.I)
NDR_SUBJ = re.compile(r"undeliverable|delivery (status|failure|has failed)|unzustellbar|mail delivery|returned mail|nicht zugestellt|failure notice|delivery notification|nicht zustellbar|zustellung fehlgeschlagen", re.I)
POS = re.compile(r"\b(gern|gerne|interesse|termin|gespräch|rückruf|anrufen|unterlagen|konditionen|profil|kurzprofil|senden sie|schicken sie|zusenden|melden sie sich|wann passt|vorschlag|teams|zoom|webex|telefonisch)\b", re.I)
NEG = re.compile(r"\b(kein bedarf|keinen bedarf|kein interesse|nicht interessiert|stellenplan|gut aufgestellt|keine vakanz|keine offenen|derzeit nicht|aktuell nicht|momentan nicht|kein leasing|keine leiharbeit|keine personalvermittl|keine zeitarbeit|keine agentur|zufrieden mit|bestehende partner|abstand nehmen)\b", re.I)
STOP = re.compile(r"\b(keine weiteren|abmelden|austragen|streichen|löschen sie|widerspr|unterlassen|nicht mehr kontaktieren|nicht mehr anschreiben|dsgvo|werbung)\b", re.I)
REDIR = re.compile(r"\b(weitergeleitet|weiterleiten|wenden sie sich|zuständig|ansprechpartner(in)? (ist|hierfür)|nicht mehr (abgerufen|erreichbar|gelesen)|wird nicht (mehr )?(abgerufen|gelesen)|nicht mehr im (unternehmen|haus)|nicht mehr tätig)\b", re.I)
ROLES = [("PDL", re.compile(r"pflegedienstleit|pflegedirekt|\bpdl\b|pflegeleitung|stationsleit|bereichsleit|pflegemanag", re.I)),
         ("HR", re.compile(r"personal(abteilung|leit|referent|gewinnung|entwicklung|management|service|wesen|recruit)|recruit|human resources|\bhr\b|talent", re.I)),
         ("GF", re.compile(r"geschäftsführ|\bgf\b|vorstand|kaufmännische|verwaltungsleit|verwaltungsdirekt|prokurist|direktor", re.I)),
         ("SEK", re.compile(r"sekretariat|assistenz|assistent|sekretär|vorzimmer|büro", re.I)),
         ("MED", re.compile(r"chefarzt|chefärzt|ärztlich|oberarzt|oberärzt|medizinische", re.I))]
def dt(s): return datetime.fromisoformat(s) if s else None
def is_auto(m): return bool(set(m["flags"]) & {"ooo_auto", "bounce"}) or bool(m["from"] and AUTO_FROM.search(m["from"])) or bool(NDR_SUBJ.search(m["subject"] or ""))
def role_of(body):
    sig = (body or "")[-700:]
    for r, rx in ROLES:
        if rx.search(sig): return r
    return "unknown"
# ---- gate: campaign target domains
gen1_out = [m for m in msgs.values() if m["direction"] == "out" and GEN1.search(m["subj_norm"] or "")]
TARGET = set()
for m in gen1_out:
    for a in m["to"] + m["cc"]:
        d = a.split("@")[-1]
        if d not in OUR_DOM: TARGET.add(d)
CLINIC = (TARGET | {threads[i]["primary_ext"] for i in FINAL if threads[i]["primary_ext"]}) - WARM - OUR_DOM
# per-domain event stream (clinic domains only)
ev = collections.defaultdict(list)  # domain -> list of (dt, msg)
for m in msgs.values():
    if not m["date"]: continue
    doms = set()
    if m["direction"] == "out":
        doms = {a.split("@")[-1] for a in m["to"] + m["cc"]}
    elif m["from"]:
        doms = {m["from"].split("@")[-1]}
    for d in doms & CLINIC:
        ev[d].append((dt(m["date"]), m))
for d in ev: ev[d].sort(key=lambda x: x[0])
# ---- 1. bounce ledger
ndr = [m for m in msgs.values() if m["direction"] != "out" and m["date"] and (NDR_SUBJ.search(m["subject"] or "") or (m["from"] and re.search(r"mailer-daemon|postmaster", m["from"], re.I)))]
EM = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
bounce_events = []
for m in ndr:
    body = (m["body_clean"] or "")[:4000]
    tdoms = {d.lower() for d in EM.findall(body)} - OUR_DOM
    tdoms = {d for d in tdoms if d in CLINIC} or ({m["primary_ext"]} if m["primary_ext"] in CLINIC else set())
    spam = bool(re.search(r"5\.7\.1|classified as spam|spam|reputation|blocked|blacklist|policy", body, re.I))
    for d in tdoms:
        t0 = dt(m["date"])
        prior = [x for x in ev.get(d, []) if x[1]["direction"] == "out" and x[1]["mailbox"] == m["mailbox"] and t0 - timedelta(days=3) <= x[0] <= t0]
        bounce_events.append({"mailbox": m["mailbox"], "domain": d, "date": m["date"], "spam_classified": spam, "matched_send": bool(prior), "gen1": any(GEN1.search(x[1]["subj_norm"] or "") for x in prior)})
b_mb = collections.Counter(b["mailbox"] for b in bounce_events)
b_spam = collections.Counter(b["mailbox"] for b in bounce_events if b["spam_classified"])
b_dom = collections.Counter(b["domain"] for b in bounce_events)
b_week = collections.Counter((b["mailbox"], datetime.fromisoformat(b["date"]).strftime("%G-W%V")) for b in bounce_events)
gen1_t1 = collections.Counter()
for tid, t in threads.items():
    outs = [msgs[i] for i in t["msg_ids"] if msgs[i]["direction"] == "out" and GEN1.search(msgs[i]["subj_norm"] or "")]
    if outs: gen1_t1[outs[0]["mailbox"]] += 1
bounce = {"ndr_messages": len(ndr), "bounce_events_clinic_domains": len(bounce_events), "spam_classified_events": sum(1 for b in bounce_events if b["spam_classified"]),
          "per_mailbox": {mb: {"gen1_threads": gen1_t1[mb], "bounce_events": b_mb[mb], "spam_classified": b_spam[mb], "bounce_share_of_gen1_threads": round(b_mb[mb] / gen1_t1[mb], 3) if gen1_t1[mb] else None} for mb in sorted(set(gen1_t1) | set(b_mb))},
          "domains_bouncing_ge2": sum(1 for d, c in b_dom.items() if c >= 2), "top_bouncing_domain_counts": sorted(b_dom.values(), reverse=True)[:10],
          "weekly_by_mailbox": {f"{mb}|{wk}": c for (mb, wk), c in sorted(b_week.items())}}
# ---- 2. compliance ledger
comp = []
for d, evs in ev.items():
    humans = [(t0, m) for t0, m in evs if m["direction"] == "in" and not is_auto(m) and m["body_len"] > 0]
    for t0, m in humans:
        body = (m["subject"] or "") + " " + (m["body_clean"] or "")[:1500]
        kind = "stop" if STOP.search(body) else "decline" if NEG.search(body) else "redirect" if REDIR.search(body) else None
        if not kind: continue
        after = [(t1, x) for t1, x in evs if x["direction"] == "out" and t1 > t0]
        same_person = [x for _t, x in after if m["from"] in x["to"] + x["cc"]]
        comp.append({"domain": d, "kind": kind, "date": m["date"], "touches_after": len(after), "touches_after_same_person": len(same_person),
                     "distinct_recipients_after": len({a for _t, x in after for a in x["to"] if a.split("@")[-1] == d}),
                     "mailboxes_after": len({x["mailbox"] for _t, x in after}), "days_span_after": round((after[-1][0] - t0).total_seconds() / 86400, 1) if after else 0,
                     "new_gen1_thread_after": any(GEN1.search(x["subj_norm"] or "") and msgs[threads[x["thread_id"]]["msg_ids"][0]]["msg_id"] == x["msg_id"] for _t, x in after)})
def agg(kind):
    rows = [c for c in comp if c["kind"] == kind]
    viol = [c for c in rows if c["touches_after"] > 0]
    return {"signals": len(rows), "domains": len({c["domain"] for c in rows}), "with_touches_after": len(viol), "domains_with_touches_after": len({c["domain"] for c in viol}),
            "touches_after_total": sum(c["touches_after"] for c in viol), "same_person_touches_after": sum(c["touches_after_same_person"] for c in viol),
            "new_gen1_thread_after": sum(1 for c in viol if c["new_gen1_thread_after"]), "median_days_span": statistics.median([c["days_span_after"] for c in viol]) if viol else 0}
compliance = {k: agg(k) for k in ("stop", "decline", "redirect")}
# ---- 3. response ledger
resp = []
for d, evs in ev.items():
    for t0, m in evs:
        if m["direction"] != "in" or is_auto(m) or m["body_len"] == 0: continue
        nxt = [(t1, x) for t1, x in evs if x["direction"] == "out" and t1 > t0 and (t1 - t0) <= timedelta(days=30) and (m["from"] in x["to"] + x["cc"] or x["thread_id"] == m.get("thread_id"))]
        body = (m["subject"] or "") + " " + (m["body_clean"] or "")[:1500]
        sent = "stop" if STOP.search(body) else "neg" if NEG.search(body) else "pos" if POS.search(body) else "other"
        r = {"domain": d, "mailbox_in": m["mailbox"], "sentiment": sent, "answered": bool(nxt), "gap_h": round((nxt[0][0] - t0).total_seconds() / 3600, 1) if nxt else None,
             "same_mailbox": (nxt[0][1]["mailbox"] == m["mailbox"]) if nxt else None, "answer_mailbox": nxt[0][1]["mailbox"] if nxt else None, "role": role_of(m["body_clean"])}
        resp.append(r)
def dist(v):
    v = sorted(x for x in v if x is not None)
    if not v: return None
    n = len(v); q = lambda p: v[min(n - 1, int(p * n))]
    return {"n": n, "median": statistics.median(v), "p25": q(.25), "p75": q(.75), "p90": q(.9)}
def resp_agg(rows):
    ans = [r for r in rows if r["answered"]]
    return {"replies": len(rows), "answered": len(ans), "answered_share": round(len(ans) / len(rows), 3) if rows else None,
            "answered_same_mailbox": sum(1 for r in ans if r["same_mailbox"]), "gap_hours": dist([r["gap_h"] for r in ans])}
response = {"all": resp_agg(resp), "by_sentiment": {s: resp_agg([r for r in resp if r["sentiment"] == s]) for s in ("pos", "neg", "redirect", "stop", "other") if any(r["sentiment"] == s for r in resp)},
            "by_mailbox_in": {mb: resp_agg([r for r in resp if r["mailbox_in"] == mb]) for mb in sorted({r["mailbox_in"] for r in resp})},
            "answer_mailbox_dist": dict(collections.Counter(r["answer_mailbox"] for r in resp if r["answered"]))}
# ---- 4. recontact ledger
recon = []
for d, evs in ev.items():
    first_human = next((t0 for t0, m in evs if m["direction"] == "in" and not is_auto(m) and m["body_len"] > 0), None)
    if not first_human: continue
    reply_thread = next((m.get("thread_id") for t0, m in evs if m["direction"] == "in" and not is_auto(m) and m["body_len"] > 0), None)
    new_cold = [(t1, x) for t1, x in evs if x["direction"] == "out" and t1 > first_human and GEN1.search(x["subj_norm"] or "") and x.get("thread_id") != reply_thread and threads[x["thread_id"]]["msg_ids"][0] == x["msg_id"]]
    if not new_cold: continue
    t_rc = new_cold[0][0]
    later_in = [m for t0, m in evs if m["direction"] == "in" and not is_auto(m) and m["body_len"] > 0 and t0 > t_rc]
    bodies = " ".join(((m["subject"] or "") + " " + (m["body_clean"] or "")[:1500]) for m in later_in)
    recon.append({"domain": d, "new_cold_threads_after_reply": len(new_cold), "later_human_replies": len(later_in), "later_positive": bool(POS.search(bodies)) and not STOP.search(bodies), "later_stop": bool(STOP.search(bodies)), "later_decline": bool(NEG.search(bodies))})
recontact = {"orgs_recontacted_after_human_reply": len(recon), "new_cold_threads_total": sum(r["new_cold_threads_after_reply"] for r in recon),
             "with_any_later_reply": sum(1 for r in recon if r["later_human_replies"]), "later_positive": sum(1 for r in recon if r["later_positive"]),
             "later_stop": sum(1 for r in recon if r["later_stop"]), "later_decline": sum(1 for r in recon if r["later_decline"])}
# ---- 5. role vs outcome
role_tab = collections.defaultdict(collections.Counter)
for r in resp: role_tab[r["role"]][r["sentiment"]] += 1
roles = {ro: dict(c) for ro, c in role_tab.items()}
# ---- 6. Gen1 funnel per mailbox / touch
fun = collections.defaultdict(collections.Counter); touch_reply = collections.Counter(); touch_sent = collections.Counter()
for tid, t in threads.items():
    ms = [msgs[i] for i in t["msg_ids"]]
    outs = [m for m in ms if m["direction"] == "out" and GEN1.search(m["subj_norm"] or "")]
    if not outs or t["primary_ext"] not in CLINIC: continue
    mb = outs[0]["mailbox"]; fun[mb]["threads"] += 1; fun[mb]["sends"] += len(outs)
    for k in range(1, len(outs) + 1): touch_sent[k] += 1
    hum = [m for m in ms if m["direction"] == "in" and not is_auto(m) and m["body_len"] > 0]
    if hum:
        fun[mb]["human_reply"] += 1
        first = min(hum, key=lambda m: m["date"] or "")
        k = sum(1 for m in outs if (m["date"] or "") < (first["date"] or "")); touch_reply[k] += 1
        body = (first["subject"] or "") + " " + (first["body_clean"] or "")[:1500]
        fun[mb]["stop" if STOP.search(body) else "neg" if NEG.search(body) else "pos" if POS.search(body) else "other"] += 1
funnel = {"per_mailbox": {mb: dict(c) for mb, c in sorted(fun.items())}, "reply_after_touch": dict(sorted(touch_reply.items())), "threads_reaching_touch": dict(sorted(touch_sent.items()))}
ledgers = {"gate": {"campaign_target_domains": len(TARGET), "clinic_domains_used": len(CLINIC), "warm_domains_excluded": len(WARM)}, "bounce": bounce, "compliance": compliance, "response": response, "recontact": recontact, "replier_role_vs_sentiment": roles, "gen1_funnel": funnel}
json.dump(ledgers, open(os.path.join(OUT, "ledgers.json"), "w"), ensure_ascii=False, indent=2)
# markdown
L = ["# Deterministic ledgers (clinic-gated, Stage 2)", "", f"Gate: {len(TARGET)} campaign-target domains; {len(CLINIC)} clinic domains used; {len(WARM)} warm-up domains excluded. Counts and domains only.", "",
     "## 1. True bounce ledger", f"- NDR messages found: {len(ndr)}; bounce events on clinic domains: {len(bounce_events)}; spam/reputation-classified: {bounce['spam_classified_events']}; domains bouncing ≥2×: {bounce['domains_bouncing_ge2']}", "",
     "| mailbox | Gen1 threads | bounce events | spam-classified | share |", "|---|---:|---:|---:|---:|"]
for mb, v in bounce["per_mailbox"].items(): L.append(f"| {mb} | {v['gen1_threads']} | {v['bounce_events']} | {v['spam_classified']} | {v['bounce_share_of_gen1_threads']} |")
L += ["", "## 2. Compliance ledger (outbound touches to the same domain AFTER a human signal)", "", "| signal | signals | domains | with touches after | domains w/ touches after | touches after (total) | to the same person | new Gen1 cold thread after | median days span |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
for k, v in compliance.items(): L.append(f"| {k} | {v['signals']} | {v['domains']} | {v['with_touches_after']} | {v['domains_with_touches_after']} | {v['touches_after_total']} | {v['same_person_touches_after']} | {v['new_gen1_thread_after']} | {v['median_days_span']} |")
L += ["", "## 3. Response ledger (our answer to EVERY human clinic reply, any mailbox, ≤30 d)", "", f"- all: {json.dumps(response['all'])}", ""]
for s, v in response["by_sentiment"].items(): L.append(f"- {s}: {json.dumps(v)}")
L += ["", "| inbound mailbox | replies | answered | share | same mailbox | gap h median/p75/p90 |", "|---|---:|---:|---:|---:|---|"]
for mb, v in response["by_mailbox_in"].items():
    g = v["gap_hours"] or {}; L.append(f"| {mb} | {v['replies']} | {v['answered']} | {v['answered_share']} | {v['answered_same_mailbox']} | {g.get('median')}/{g.get('p75')}/{g.get('p90')} |")
L += ["", f"- who answers (mailbox distribution): {json.dumps(response['answer_mailbox_dist'])}", "", "## 4. Recontact ledger (new Gen1 cold thread to an org after a human reply elsewhere)", f"- {json.dumps(recontact)}", "",
      "## 5. Replier role (signature titles) vs sentiment of first reply", "", "| role | pos | neg | stop | other | total |", "|---|---:|---:|---:|---:|---:|"]
for ro, c in sorted(roles.items(), key=lambda x: -sum(x[1].values())): L.append(f"| {ro} | {c.get('pos',0)} | {c.get('neg',0)} | {c.get('stop',0)} | {c.get('other',0)} | {sum(c.values())} |")
L += ["", "## 6. Gen1 funnel (clinic-gated)", "", "| mailbox | threads | sends | human reply | pos | neg | stop | other | reply rate |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
for mb, c in funnel["per_mailbox"].items(): L.append(f"| {mb} | {c.get('threads',0)} | {c.get('sends',0)} | {c.get('human_reply',0)} | {c.get('pos',0)} | {c.get('neg',0)} | {c.get('stop',0)} | {c.get('other',0)} | {round(c.get('human_reply',0)/c['threads'],3) if c.get('threads') else None} |")
L += ["", f"- threads reaching touch k: {funnel['threads_reaching_touch']}", f"- first human reply arrived after touch k: {funnel['reply_after_touch']}", ""]
open(os.path.join(OUT, "ledgers.md"), "w", encoding="utf-8").write("\n".join(L))
print(json.dumps({"gate": ledgers["gate"], "bounce_events": len(bounce_events), "spam_classified": bounce["spam_classified_events"], "compliance": {k: (v["with_touches_after"], v["touches_after_total"]) for k, v in compliance.items()}, "response_all": response["all"], "recontact": recontact, "roles": roles}, ensure_ascii=False, indent=1))
