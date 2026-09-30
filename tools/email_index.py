#!/usr/bin/env python3
"""Deterministic pre-pass over all email dumps (no LLM).

Reads data/email-dump/*/messages.jsonl (Graph + IMAP schemas), dedupes by
Message-ID across mailboxes, threads globally via In-Reply-To / References /
conversationId (union-find), classifies engagement, clusters outbound
templates, flags exceptional cases, and builds per-clinic dossiers (all
threads + all people for one external domain — topics span threads and new
clinic people join mid-procedure, so the domain is the unit of analysis).

Writes data/email-analysis/:
  messages.jsonl        normalized messages (+thread_id, direction, template)
  threads.jsonl         one row per global thread with stats + flags
  orgs.jsonl            one row per external domain (clinic dossier)
  templates.jsonl       outbound template clusters with counts + example
  stats.json            overall numbers
Read-only over the dumps.
"""
import os, re, json, hashlib, collections, statistics
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime, timezone

DUMP = "/home/claude/repo/pflege-board/data/email-dump"
OUT  = "/home/claude/repo/pflege-board/data/email-analysis"
ENV  = "/home/claude/repo/pflege-board/.env"
os.makedirs(OUT, exist_ok=True)


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


# ---------- who is "us" ----------
env = load_env(ENV)
our_addrs = set()
for n in range(1, 12):
    a = env.get("MAILBOX_%d_ADDRESS" % n, "").lower()
    if a:
        our_addrs.add(a)
for d in os.listdir(DUMP):
    s = os.path.join(DUMP, d, "_summary.json")
    if os.path.exists(s):
        our_addrs.add(json.load(open(s))["mailbox"].lower())
our_domains = {a.split("@")[1] for a in our_addrs}
our_domains |= {"kindt.agency", "ki-agent.agency", "ki-ndt.agency", "ki-workflow.agency"}

FREEMAIL = {"gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "t-online.de",
            "outlook.com", "hotmail.com", "yahoo.com", "yahoo.de", "icloud.com", "ukr.net",
            "mail.ru", "yandex.ru", "aol.com", "freenet.de", "live.de", "outlook.de"}

# ---------- helpers ----------
def norm_mid(m):
    if not m:
        return None
    mm = re.search(r"<([^>]+)>", m)
    m = (mm.group(1) if mm else m).strip().lower()
    return m or None


def parse_addrs(s):
    if not s:
        return []
    if isinstance(s, list):
        return [x.lower().strip() for x in s if x]
    return [a.lower().strip() for _n, a in getaddresses([s]) if a and "@" in a]


def parse_date(rec):
    d = rec.get("receivedDateTime") or rec.get("sentDateTime")
    if d:
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass
    d = rec.get("date")
    if d:
        try:
            return parsedate_to_datetime(d).astimezone(timezone.utc)
        except Exception:
            pass
    return None


PREFIX = re.compile(r"^\s*((re|fwd?|aw|wg|antw|sv|vs|tr|fw)\s*:\s*)+", re.I)


def subj_norm(s):
    s = PREFIX.sub("", s or "").strip().lower()
    return re.sub(r"\s+", " ", s)


GREET = re.compile(r"^(sehr geehrte|guten (tag|morgen|abend)|hallo|liebe[rs]?|dear|hi\b|hello|moin|servus)", re.I)
QUOTE = re.compile(r"^(am .{4,80} schrieb|on .{4,80} wrote|von:|from:|gesendet:|sent:|-----+ ?(original|ursprüngliche|weitergeleitete))", re.I)


def clean_body(body):
    """Body without quoted history; used for template fingerprinting and agent reading."""
    out = []
    for l in (body or "").splitlines():
        s = l.strip()
        if s.startswith(">"):
            break
        if QUOTE.match(s):
            break
        out.append(l)
    return "\n".join(out).strip()


def tmpl_fp(body_clean):
    if not body_clean:
        return None
    lines = [l for l in body_clean.lower().splitlines() if l.strip() and not GREET.match(l.strip())]
    t = " ".join(lines)
    t = re.sub(r"\S+@\S+", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\d+", " ", t)
    t = re.sub(r"[^a-zäöüß ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 40:
        return None
    return hashlib.md5(t[:500].encode()).hexdigest()[:12]


KEYFLAGS = {
    "legal_complaint": r"\b(anwalt|abmahnung|beschwerde|datenschutz|dsgvo|unterlassung|rechtlich|rechtsabteilung)\b",
    "optout":          r"\b(abmelden|austragen|unsubscribe|keine weiteren|nicht mehr (kontaktieren|anschreiben)|löschen sie|entfernen sie|kein interesse|nicht interessiert|kein bedarf)\b",
    "contract":        r"\b(vertrag|agb|konditionen|rahmenvertrag|vermittlungsvertrag|überlassung|arbeitnehmerüberlassung|zeitarbeit|vereinbarung)\b",
    "fee_invoice":     r"\b(rechnung|honorar|provision|gebühr|vergütung|preis|kosten|zahlung|überweisung|fällig|mahnung|invoice|rate)\b",
    "scheduling":      r"\b(termin|meeting|teams|zoom|telefonat|rückruf|anruf|telefonisch|kalender|verschieben|absagen|uhr|vorschlag)\b",
    "interview":       r"\b(vorstellungsgespräch|interview|kennenlernen|probearbeit|hospitation|probetag|gespräch)\b",
    "hire":            r"\b(arbeitsvertrag|einstellung|zusage|arbeitsantritt|startdatum|einstellen|übernehmen|stelle)\b",
    "reject":          r"\b(absage|leider|nicht (möglich|passend)|derzeit (kein|nicht)|keine (vakanz|stelle|offenen))\b",
    "foreign_nurse":   r"\b(anerkennung|defizitbescheid|kenntnisprüfung|anpassungslehrgang|visum|visa|aufenthalt|b2|b1|sprachniveau|sprachzertifikat|fachkräfteverfahren|ukraine|ukrainisch)\b",
    "documents":       r"\b(lebenslauf|cv|zeugnis|urkunde|unterlagen|profil|exposé|expose|dokumente|nachweis|zertifikat|pass)\b",
    "ooo_auto":        r"\b(abwesenheit|out of office|automatische antwort|automatic reply|autoreply|nicht im (büro|haus)|urlaub|zurück am)\b",
    "bounce":          r"\b(undeliverable|delivery (status|failure)|unzustellbar|mailer-daemon|nicht zugestellt|returned mail)\b",
}
KEYRE = {k: re.compile(v, re.I) for k, v in KEYFLAGS.items()}

ATT_TYPES = [
    ("cv",          r"lebenslauf|cv|resume|vita"),
    ("certificate", r"zeugnis|urkunde|zertifikat|certificate|diplom|bescheinigung|nachweis"),
    ("contract",    r"vertrag|agb|vereinbarung|contract|rahmen"),
    ("invoice",     r"rechnung|invoice|honorar|angebot|offer|preis"),
    ("profile",     r"profil|exposé|expose|kurzprofil|kandidat|candidate|vorstellung"),
    ("id_visa",     r"pass|visum|visa|aufenthalt|ausweis|anerkennung|defizit"),
    ("language",    r"sprach|b2|b1|telc|goethe|ösd"),
    ("presentation",r"präsentation|presentation|broschüre|flyer|unternehmen|firma|vorstellung"),
]


def att_type(name):
    n = (name or "").lower()
    ext = n.rsplit(".", 1)[-1] if "." in n else ""
    for t, pat in ATT_TYPES:
        if re.search(pat, n):
            return t, ext
    if ext in ("png", "jpg", "jpeg", "gif"):
        return "image", ext
    return "other", ext


# ---------- union-find ----------
uf = {}
def find(x):
    while uf[x] != x:
        uf[x] = uf[uf[x]]
        x = uf[x]
    return x
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        uf[ra] = rb
def node(x):
    if x not in uf:
        uf[x] = x


# ---------- load + normalize ----------
msgs = {}
conv_groups = collections.defaultdict(list)
raw_total = 0
for d in sorted(os.listdir(DUMP)):
    f = os.path.join(DUMP, d, "messages.jsonl")
    s = os.path.join(DUMP, d, "_summary.json")
    if not (os.path.exists(f) and os.path.exists(s)):
        continue
    mailbox = json.load(open(s))["mailbox"].lower()
    # Graph dumps carry no attachment names; merge attachments.jsonl if the enrichment ran
    att_map = {}
    af = os.path.join(DUMP, d, "attachments.jsonl")
    if os.path.exists(af):
        for aline in open(af, encoding="utf-8"):
            a = json.loads(aline)
            k = norm_mid(a.get("internetMessageId"))
            if k:
                att_map[k] = [x["name"] for x in a.get("attachments", []) if x.get("name") and not x.get("isInline")]
    for line in open(f, encoding="utf-8"):
        r = json.loads(line)
        if "folder_error" in r:
            continue
        raw_total += 1
        source = "graph" if "conversationId" in r else "imap"
        mid = norm_mid(r.get("internetMessageId") or r.get("message_id"))
        if not mid:
            mid = "syn:" + hashlib.md5((mailbox + "|" + str(r.get("id") or "") + "|" + (r.get("subject") or "")
                                        + "|" + str(r.get("date") or r.get("receivedDateTime"))).encode()).hexdigest()[:16]
        if mid in msgs:
            m = msgs[mid]
            if mailbox not in m["seen_in"]:
                m["seen_in"].append(mailbox)
            if r.get("folder") and r["folder"] not in m["folders"]:
                m["folders"].append(r["folder"])
            if not m["attachments"] and r.get("attachments"):
                m["attachments"] = r["attachments"]
            continue
        frm = parse_addrs(r.get("from"))
        frm = frm[0] if frm else None
        to, cc = parse_addrs(r.get("to")), parse_addrs(r.get("cc"))
        dt = parse_date(r)
        fdom = frm.split("@")[-1] if frm else None
        direction = "out" if (frm and (frm in our_addrs or fdom in our_domains)) else ("in" if frm else "unknown")
        parts = set(to + cc + ([frm] if frm else []))
        ext = sorted(a for a in parts if a.split("@")[-1] not in our_domains)
        ext_domains = sorted({a.split("@")[-1] for a in ext})
        primary_ext = fdom if (direction == "in" and fdom) else (ext_domains[0] if ext_domains else None)
        body = r.get("body") or r.get("bodyPreview") or ""
        bclean = clean_body(body)
        text_for_flags = ((r.get("subject") or "") + "\n" + bclean).lower()
        flags = [k for k, rx in KEYRE.items() if rx.search(text_for_flags)]
        atts = r.get("attachments") or []
        rec = {
            "msg_id": mid, "mailbox": mailbox, "seen_in": [mailbox], "source": source,
            "folders": [r["folder"]] if r.get("folder") else [],
            "subject": r.get("subject") or "", "subj_norm": subj_norm(r.get("subject")),
            "from": frm, "to": to, "cc": cc, "direction": direction,
            "ext_addrs": ext, "ext_domains": ext_domains, "primary_ext": primary_ext,
            "date": dt.isoformat() if dt else None,
            "body_clean": bclean[:20000], "body_len": len(body),
            "attachments": atts, "att_types": [att_type(a) for a in atts],
            "flags": flags,
            "in_reply_to": norm_mid(r.get("in_reply_to")),
            "references": [norm_mid(x) for x in re.findall(r"<[^>]+>", r.get("references") or "")],
            "conv": r.get("conversationId"), "is_draft": bool(r.get("isDraft")),
            "tmpl": tmpl_fp(bclean) if direction == "out" else None,
        }
        msgs[mid] = rec
        node(mid)
        if rec["in_reply_to"]:
            node(rec["in_reply_to"]); union(mid, rec["in_reply_to"])
        for ref in rec["references"]:
            if ref:
                node(ref); union(mid, ref)
        if rec["conv"]:
            conv_groups[rec["conv"]].append(mid)
for _conv, ids in conv_groups.items():
    for i in ids[1:]:
        union(ids[0], i)

# ---------- threads ----------
by_thread = collections.defaultdict(list)
for mid in msgs:
    by_thread[find(mid)].append(mid)

threads = {}
for root, ids in by_thread.items():
    ms = sorted((msgs[i] for i in ids), key=lambda m: m["date"] or "")
    tid = "t_" + hashlib.md5(root.encode()).hexdigest()[:10]
    for m in ms:
        m["thread_id"] = tid
    n_in = sum(1 for m in ms if m["direction"] == "in")
    n_out = sum(1 for m in ms if m["direction"] == "out")
    ext_addrs = sorted({a for m in ms for a in m["ext_addrs"]})
    ext_domains = collections.Counter(d for m in ms for d in m["ext_domains"])
    primary = ext_domains.most_common(1)[0][0] if ext_domains else None
    dates = [m["date"] for m in ms if m["date"]]
    first, last = (min(dates), max(dates)) if dates else (None, None)
    dur = 0.0
    if first and last:
        dur = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds() / 86400
    if not ext_addrs:
        eng = "internal"
    elif n_in and n_out:
        eng = "engaged"
    elif n_out:
        eng = "cold_no_reply"
    else:
        eng = "inbound_only"
    # follow-ups: max consecutive outbound before an inbound
    run = best = 0
    for m in ms:
        if m["direction"] == "out":
            run += 1; best = max(best, run)
        elif m["direction"] == "in":
            run = 0
    # timing
    first_out = next((m for m in ms if m["direction"] == "out"), None)
    first_in_after = None
    if first_out and first_out["date"]:
        first_in_after = next((m for m in ms if m["direction"] == "in" and m["date"] and m["date"] > first_out["date"]), None)
    reply_gap_h = None
    if first_out and first_in_after:
        reply_gap_h = round((datetime.fromisoformat(first_in_after["date"]) - datetime.fromisoformat(first_out["date"])).total_seconds() / 3600, 1)
    our_resp = []
    out_gaps = []
    prev = None
    for m in ms:
        if prev and prev["date"] and m["date"]:
            gap_h = (datetime.fromisoformat(m["date"]) - datetime.fromisoformat(prev["date"])).total_seconds() / 3600
            if prev["direction"] == "in" and m["direction"] == "out":
                our_resp.append(round(gap_h, 1))
            if prev["direction"] == "out" and m["direction"] == "out":
                out_gaps.append(round(gap_h / 24, 1))
        prev = m
    flags = sorted({f for m in ms for f in m["flags"]})
    atts = [a for m in ms for a in m["attachments"]]
    att_types = collections.Counter(t for m in ms for t, _e in m["att_types"])
    threads[tid] = {
        "thread_id": tid, "subj_norm": collections.Counter(m["subj_norm"] for m in ms).most_common(1)[0][0],
        "n": len(ms), "n_in": n_in, "n_out": n_out,
        "mailboxes": sorted({b for m in ms for b in m["seen_in"]}),
        "our_senders": sorted({m["from"] for m in ms if m["direction"] == "out" and m["from"]}),
        "ext_addrs": ext_addrs, "ext_domains": [d for d, _c in ext_domains.most_common()],
        "primary_ext": primary, "primary_is_freemail": bool(primary and primary in FREEMAIL),
        "first": first, "last": last, "duration_days": round(dur, 1),
        "engagement": eng, "max_followups": best,
        "first_reply_gap_h": reply_gap_h,
        "our_response_h_median": round(statistics.median(our_resp), 1) if our_resp else None,
        "outbound_gaps_days": out_gaps[:10],
        "flags": flags, "n_attachments": len(atts), "att_types": dict(att_types),
        "tmpls": [m["tmpl"] for m in ms if m["tmpl"]],
        "folders": sorted({f for m in ms for f in m["folders"]}),
        "msg_ids": [m["msg_id"] for m in ms],
    }

# ---------- exceptional flags per thread ----------
EXC_FLAGS = ("legal_complaint", "optout", "bounce")
for t in threads.values():
    exc = []
    if any(f in t["flags"] for f in EXC_FLAGS):
        exc.append("sensitive")
    if t["engagement"] == "engaged" and t["n"] >= 8:
        exc.append("long_conversation")
    if len(t["ext_addrs"]) >= 3:
        exc.append("multi_person")
    if t["duration_days"] >= 45 and t["engagement"] == "engaged":
        exc.append("long_running")
    if t["engagement"] == "engaged" and ("fee_invoice" in t["flags"] or "contract" in t["flags"]):
        exc.append("commercial")
    if t["engagement"] == "engaged" and "hire" in t["flags"]:
        exc.append("hire_stage")
    if t["n_attachments"] >= 3:
        exc.append("doc_heavy")
    t["exceptional"] = exc

# ---------- org dossiers ----------
orgs = {}
for t in threads.values():
    dom = t["primary_ext"]
    if not dom or dom in FREEMAIL:
        continue
    o = orgs.setdefault(dom, {"domain": dom, "threads": [], "people": {}, "mailboxes": set(),
                              "n_msgs": 0, "n_in": 0, "n_out": 0, "engaged_threads": 0,
                              "first": None, "last": None, "flags": collections.Counter(),
                              "att_types": collections.Counter(), "exceptional": collections.Counter()})
    o["threads"].append(t["thread_id"])
    o["n_msgs"] += t["n"]; o["n_in"] += t["n_in"]; o["n_out"] += t["n_out"]
    o["engaged_threads"] += 1 if t["engagement"] == "engaged" else 0
    o["mailboxes"] |= set(t["mailboxes"])
    for f in t["flags"]:
        o["flags"][f] += 1
    for k, v in t["att_types"].items():
        o["att_types"][k] += v
    for e in t["exceptional"]:
        o["exceptional"][e] += 1
    if t["first"] and (o["first"] is None or t["first"] < o["first"]):
        o["first"] = t["first"]
    if t["last"] and (o["last"] is None or t["last"] > o["last"]):
        o["last"] = t["last"]
for m in msgs.values():
    for a in m["ext_addrs"]:
        dom = a.split("@")[-1]
        if dom in orgs and m["date"]:
            p = orgs[dom]["people"].setdefault(a, {"first_seen": m["date"], "n": 0, "wrote_to_us": 0})
            p["n"] += 1
            if m["direction"] == "in" and m["from"] == a:
                p["wrote_to_us"] += 1
            if m["date"] < p["first_seen"]:
                p["first_seen"] = m["date"]
org_rows = []
for o in orgs.values():
    o["mailboxes"] = sorted(o["mailboxes"])
    o["n_threads"] = len(o["threads"])
    o["n_people"] = len(o["people"])
    o["people_joined_later"] = sum(1 for p in o["people"].values() if o["first"] and p["first_seen"] > o["first"])
    o["is_engaged"] = o["engaged_threads"] > 0
    o["flags"] = dict(o["flags"]); o["att_types"] = dict(o["att_types"]); o["exceptional"] = dict(o["exceptional"])
    org_rows.append(o)
org_rows.sort(key=lambda o: (-o["engaged_threads"], -o["n_in"], -o["n_msgs"]))

# ---------- templates ----------
tm = collections.defaultdict(lambda: {"count": 0, "mailboxes": collections.Counter(), "subjects": collections.Counter(),
                                      "positions": collections.Counter(), "example": None, "example_subject": None,
                                      "reply_rate_threads": [0, 0]})
for t in threads.values():
    pos = 0
    for mid in t["msg_ids"]:
        m = msgs[mid]
        if m["direction"] != "out":
            continue
        pos += 1
        if not m["tmpl"]:
            continue
        e = tm[m["tmpl"]]
        e["count"] += 1
        e["mailboxes"][m["mailbox"]] += 1
        e["subjects"][m["subj_norm"]] += 1
        e["positions"][pos] += 1
        if e["example"] is None:
            e["example"] = m["body_clean"][:1500]; e["example_subject"] = m["subject"]
        e["reply_rate_threads"][1] += 1
        if t["engagement"] == "engaged":
            e["reply_rate_threads"][0] += 1
tmpl_rows = []
for fp, e in tm.items():
    used, eng = e["reply_rate_threads"][1], e["reply_rate_threads"][0]
    tmpl_rows.append({
        "tmpl": fp, "count": e["count"],
        "mailboxes": dict(e["mailboxes"]), "top_subjects": e["subjects"].most_common(3),
        "positions": dict(e["positions"]),
        "used_in_threads": used, "threads_with_reply": eng,
        "reply_rate": round(eng / used, 3) if used else None,
        "example_subject": e["example_subject"], "example": e["example"],
    })
tmpl_rows.sort(key=lambda r: -r["count"])

# ---------- write ----------
with open(os.path.join(OUT, "messages.jsonl"), "w", encoding="utf-8") as f:
    for m in msgs.values():
        f.write(json.dumps(m, ensure_ascii=False) + "\n")
with open(os.path.join(OUT, "threads.jsonl"), "w", encoding="utf-8") as f:
    for t in threads.values():
        f.write(json.dumps(t, ensure_ascii=False) + "\n")
with open(os.path.join(OUT, "orgs.jsonl"), "w", encoding="utf-8") as f:
    for o in org_rows:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
with open(os.path.join(OUT, "templates.jsonl"), "w", encoding="utf-8") as f:
    for r in tmpl_rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

eng_dist = collections.Counter(t["engagement"] for t in threads.values())
exc_dist = collections.Counter(e for t in threads.values() for e in t["exceptional"])
flag_dist = collections.Counter(f for t in threads.values() for f in t["flags"])
per_mailbox = collections.Counter(m["mailbox"] for m in msgs.values())
att_dist = collections.Counter(t for m in msgs.values() for t, _e in m["att_types"])
ext_dist = collections.Counter(e for m in msgs.values() for _t, e in m["att_types"])
stats = {
    "raw_rows": raw_total, "unique_messages": len(msgs),
    "dupes_across_mailboxes": raw_total - len(msgs),
    "direction": dict(collections.Counter(m["direction"] for m in msgs.values())),
    "threads": len(threads), "engagement": dict(eng_dist),
    "engaged_threads": eng_dist["engaged"],
    "orgs": len(org_rows), "engaged_orgs": sum(1 for o in org_rows if o["is_engaged"]),
    "orgs_with_people_joining_later": sum(1 for o in org_rows if o["people_joined_later"]),
    "templates": len(tmpl_rows), "top10_template_share": round(sum(r["count"] for r in tmpl_rows[:10]) / max(1, sum(r["count"] for r in tmpl_rows)), 3),
    "exceptional": dict(exc_dist), "flags": dict(flag_dist),
    "per_mailbox": dict(per_mailbox),
    "attachments_total": sum(len(m["attachments"]) for m in msgs.values()),
    "attachment_types": dict(att_dist), "attachment_ext": dict(ext_dist.most_common(12)),
    "note": "Graph (M365) dumps carry no attachment names; attachment stats are IMAP (Zoho) boxes only.",
}
json.dump(stats, open(os.path.join(OUT, "stats.json"), "w"), ensure_ascii=False, indent=2)
print(json.dumps(stats, ensure_ascii=False, indent=2))
