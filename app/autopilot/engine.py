"""Luna's rules (docs/autopilot.md, "The engine"). No LLM: deterministic, keyword and state driven.

Entry points: next_action(conv) · apply_inbound(conv, text, attachments) · tick(minutes).
Everything outbound goes through send(): the policy (off/assist/auto), the per-conversation mode, the risk tier,
remembered approvals and the quiet hours decide whether a message is sent, queued or parked as an approval.
"""
import random
import re
from datetime import timedelta

from . import db
from . import matching as M
from . import seed as S

TRIGGERS = {
    "salary": ["gehalt", "lohn", "euro", "€", "bezahlung", "salary", "verdienst", "brutto", "netto"],
    "visa_legal": ["visum", "visa", "anwalt", "aufenthalt", "blue card", "legal", "rechtlich", "aufenthaltstitel", "lawyer"],
    "asks_for_human": ["mensch", "echte person", "mitarbeiter sprechen", "human", "real person", "echten menschen", "kollegin sprechen"],
    "complaint": ["beschwerde", "unzufrieden", "schlecht", "complaint", "enttäuscht", "unprofessionell"],
}
TRIGGER_LABEL = {"salary": "Gehaltsfrage", "visa_legal": "Visum/Rechtsfrage", "asks_for_human": "Kandidat möchte Menschen sprechen", "complaint": "Beschwerde"}
STOP_RE = re.compile(r"^\s*(stop|stopp|abmelden|unsubscribe|nicht mehr schreiben)\b", re.I)
YES_RE = re.compile(r"\b(ja|yes|da|ok|okay|einverstanden|gerne|passt|sure|klar)\b", re.I)
STATE_STAGE = {"greeting": "new", "consent": "contacted", "collect_basics": "qualifying", "collect_location": "qualifying", "collect_docs": "docs_pending",
               "docs_review": "docs_pending", "qualified": "qualified", "matching": "matching", "profile_sent": "profile_sent",
               "interview_prep": "interview_scheduling", "awaiting_feedback": "interviewed", "placed": "placed", "lost": "lost", "dormant": "dormant"}
CHECK_LABEL = {"cv": "Lebenslauf", "education_cert": "Examenszeugnis", "anerkennung": "Anerkennung", "language_cert": "Sprachzertifikat", "location": "Wohnort/PLZ"}
DOC_WORDS = {"cv": r"lebenslauf|\bcv\b|resume", "education_cert": r"zeugnis|urkunde|diplom|examen", "anerkennung": r"anerkennung|bescheid|urkunde der regierung",
             "language_cert": r"zertifikat|telc|goethe|b2-pr|sprachnachweis", "work_permit": r"aufenthaltstitel|arbeitserlaubnis|visum kopie", "references": r"arbeitszeugnis|referenz"}
DAY_DE = {"Mon": "Mo", "Tue": "Di", "Wed": "Mi", "Thu": "Do", "Fri": "Fr", "Sat": "Sa", "Sun": "So"}

CAND_REPLIES = {                                            # simulated candidate corpus per conversation state (tick)
    "greeting": ["Hallo, ist da jemand?", "Hi, I'm interested in the nursing jobs."],
    "consent": ["Ja, einverstanden.", "JA", "Yes, ok."],
    "collect_basics": ["Ich bin {quali}, {years} Erfahrung, zuletzt {dept}.", "{quali}, {years}, Station {dept}."],
    "collect_location": ["{plz} {city}, bis {radius} km.", "Ich wohne in {city}, PLZ {plz}."],
    "collect_docs": ["Hier mein Lebenslauf.|cv", "Zeugnis anbei.|education_cert", "Lebenslauf und Zeugnis anbei.|cv,education_cert", "Ich schicke die Unterlagen morgen."],
    "docs_review": ["Haben Sie die Unterlagen schon geprüft?", "Fehlt noch etwas?"],
    "qualified": ["Gibt es schon passende Kliniken?", "Ich könnte auch in {city} pendeln, 50 km sind ok."],
    "matching": ["Gibt es schon Neuigkeiten?", "Danke, ich warte."],
    "profile_sent": ["Hat die Klinik schon geantwortet?", "Wie viel Gehalt zahlt die Klinik? Ich brauche mindestens 3.800 Euro.", "Danke, ich warte auf Rückmeldung."],
    "interview_prep": ["Ja, der Termin passt mir.", "Passt, danke!", "Kann ich mit einem Menschen sprechen? Ich habe Fragen zum Visum."],
    "awaiting_feedback": ["Das Gespräch war gut, ich habe ein gutes Gefühl.", "Sie wollen sich bis Freitag melden."],
}
CLINIC_REPLIES = {
    "intro_sent": ["Vielen Dank für das Profil. Das klingt interessant – bitte schicken Sie uns die vollständigen Unterlagen.", None, None, "Vielen Dank, aktuell ist die Stelle bereits besetzt."],
    "awaiting_reply": ["Das Profil passt gut zu unserer Station. Wann könnte ein Gespräch stattfinden?", None, "Vielen Dank, aktuell ist die Stelle bereits besetzt.", None],
    "interested": ["Wann könnte ein Gespräch stattfinden? Gern per Video.", None],
    "scheduling": ["Wir nehmen Termin 2.", "Termin 2 passt uns, bitte per Video.", "Die Termine passen leider nicht. Ginge {counter}?"],
    "feedback_pending": ["Das Gespräch war sehr positiv. Wir möchten ein Angebot machen.", "Fachlich gut, aber das Deutsch reicht noch nicht – leider eine Absage.", None],
}


# --- small helpers -----------------------------------------------------------------------------------------
def now(c):
    return S.sim_now(c)


def slot_label(d):
    s = S.parse(d).strftime("%a %d.%m. %H:%M")
    return DAY_DE.get(s[:3], s[:3]) + s[3:]


def business_slots(start, n=3, rng=None):
    rng = rng or random.Random(7)
    out, d = [], S.parse(start)
    while len(out) < n:
        d = d + timedelta(days=1)
        if d.weekday() >= 5:
            continue
        out.append({"at": S.iso(d.replace(hour=rng.choice([9, 10, 11, 14, 15]), minute=rng.choice([0, 30]), second=0)), "format": "video"})
    return out


def conv_by_id(c, conv_id):
    return db.get(c, "conversations", conv_id)


def candidate_of(c, conv):
    return db.get(c, "candidates", conv["candidate_id"]) if conv and conv.get("candidate_id") else None


def thread_of(c, conv):
    return db.get(c, "clinic_threads", conv["clinic_thread_id"]) if conv and conv.get("clinic_thread_id") else None


def documents_of(c, cand_id):
    return db.rows("documents", c.execute("select * from documents where candidate_id=? order by id", (cand_id,)))


def template_for(c, channel, lang, stage):
    r = c.execute("select * from templates where channel=? and lang=? and stage=? and active=1 order by version desc limit 1", (channel, lang, stage)).fetchone()
    if not r and lang != "de":
        r = c.execute("select * from templates where channel=? and lang='de' and stage=? and active=1 order by version desc limit 1", (channel, stage)).fetchone()
    return db.row("templates", r)


def clinic_row(c, clinic_id):
    r = c.execute("select * from registry_clinics where clinic_id=?", (str(clinic_id),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["fachrichtungen"] = db.uj(d["fachrichtungen"], [])
    return d


def posting_row(c, posting_id):
    r = c.execute("select * from registry_postings where posting_id=?", (posting_id,)).fetchone()
    return dict(r) if r else None


def vars_for(c, cand, extra=None):
    v = {"first_name": S.first_name(cand["name"]), "name": cand["name"], "region": cand.get("region") or "Bayern", "initials": cand.get("initials"),
         "start_from": cand.get("start_from"), "profile": S.anonymised_profile(cand), "format": "Video"}
    v["missing_docs"] = S.missing_docs_label(documents_of(c, cand["id"]))
    v.update(extra or {})
    return v


def checklist(cand, docs):
    """[{field, label, status, value}] – status ok / missing / requested / unclear."""
    out = []
    by = {d["kind"]: d for d in docs}
    for kind in ("cv", "education_cert", "anerkennung", "language_cert"):
        d = by.get(kind)
        st = (d or {}).get("status") or "missing"
        status = "ok" if st in ("received", "verified") else "requested" if st == "requested" else "unclear" if st == "rejected" else "missing"
        value = None
        if kind == "anerkennung":
            value = M.ANERK_LABEL.get(cand.get("anerkennung_status"), cand.get("anerkennung_status"))
            if cand.get("anerkennung_status") == "not_needed":
                status = "ok"
        if kind == "language_cert":
            value = f"Deutsch {cand.get('german_level')}" if cand.get("german_level") else None
            if st == "rejected":
                value = (d or {}).get("note") or value
        if kind in ("cv", "education_cert") and d and d.get("received_at"):
            value = "erhalten " + d["received_at"][:10]
        out.append({"field": kind, "label": CHECK_LABEL[kind], "status": status, "value": value})
    loc_status = "ok" if cand.get("plz") and cand.get("city") else "unclear" if cand.get("city") else "missing"
    out.append({"field": "location", "label": CHECK_LABEL["location"], "status": loc_status,
                "value": " ".join(x for x in (cand.get("plz"), cand.get("city")) if x) or None})
    return out


def top_matches(c, cand, n=5):
    clinics, jobs = S.registry(c)
    return M.rank(cand, clinics, jobs, n=n)


# --- policy: what may go out ----------------------------------------------------------------------------------
def remembered_decision(policy, kind, risk):
    for r in policy.get("remembered_rules") or []:
        if r.get("kind") == kind and r.get("risk") == risk:
            return r.get("decision")
    return None


def resolve(policy, conv, risk, as_="luna", kind="message"):
    """-> 'send' | 'approve' | 'log' | 'hold'. Operators always send (except in a stopped thread)."""
    if conv and conv.get("mode") == "stopped":
        raise ValueError("conversation is stopped (opt-out) – nothing may be sent")
    if as_ == "operator":
        return "send"
    mode = policy.get("mode", "auto")
    if mode == "off":
        return "log"
    if conv and conv.get("mode") == "paused":
        return "hold"
    if conv and conv.get("mode") in ("human", "manager"):
        return "approve"
    if mode == "assist":
        return "approve"
    rem = remembered_decision(policy, kind, risk)
    if rem == "approve":
        return "send"
    if rem == "reject":
        return "approve"
    if risk == "high":
        return "approve"           # high-risk topics always escalate (docs/autopilot.md) – risk_rules cannot auto-send them
    rule = (policy.get("risk_rules") or {}).get(risk, "approve")
    return "send" if rule == "auto" else "approve"


def in_quiet_hours(policy, t):
    lo, hi = policy.get("quiet_hours") or [8, 20]
    return not (lo <= t.hour < hi)


def next_window(policy, t):
    lo = (policy.get("quiet_hours") or [8, 20])[0]
    d = t.replace(hour=lo, minute=0, second=0, microsecond=0)
    if d <= t:
        d += timedelta(days=1)
    return d


def _deliver(c, msg_id, at, actor="luna"):
    """Mark a message sent, update the conversation bookkeeping, log the event."""
    m = db.get(c, "messages", msg_id)
    conv = conv_by_id(c, m["conversation_id"])
    db.update(c, "messages", msg_id, {"status": "sent", "at": S.iso(at)})
    nxt = "clinic" if conv["kind"] == "clinic" else "candidate"
    pol = db.policy(c)
    sla_h = pol["cadence_candidate_hours"][0] if conv["kind"] == "candidate" else pol["cadence_clinic_business_days"][0] * 24
    db.update(c, "conversations", conv["id"], {"last_message_at": S.iso(at), "last_preview": m["text"].split("\n\n", 1)[-1][:120], "next_actor": nxt,
                                               "sla_due_at": S.iso(at + timedelta(hours=sla_h)), "unread": 0})
    if m.get("template_id"):
        c.execute("update templates set uses=uses+1 where id=?", (m["template_id"],))
    if conv.get("account_id"):
        c.execute("update accounts set used_today=used_today+1, last_ok_at=? where id=?", (S.iso(at), conv["account_id"]))
    who = conv.get("candidate_id")
    label = None
    if who:
        cand = db.get(c, "candidates", who)
        label = cand["initials"] if cand else None
    else:
        th = thread_of(c, conv)
        label = th["clinic_name"] if th else None
    db.event(c, S.iso(at), actor, "message_sent" if conv["channel"] == "whatsapp" else "email_sent",
             {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id"), "clinic_thread_id": conv.get("clinic_thread_id"), "message_id": msg_id},
             f"an {label}: {m['text'].split(chr(10))[0][:70]}")
    return db.get(c, "messages", msg_id)


def send(c, conv, text, as_="luna", risk="low", template_id=None, kind="message", reason=None, at=None, meta=None, suggested=None):
    """The one outbound path. Returns the message row (+ approval_id when parked) or {'status': 'logged'} in mode off."""
    policy = db.policy(c)
    at = at or now(c)
    decision = resolve(policy, conv, risk, as_, kind)
    author = "operator" if as_ == "operator" else "luna"
    if decision == "log":
        db.event(c, S.iso(at), "luna", "would_send", {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id")}, f"Autopilot aus – nicht gesendet: {text[:60]}")
        return {"status": "logged", "conversation_id": conv["id"], "text": text, "author": author, "risk": risk}
    if decision == "hold":
        db.event(c, S.iso(at), "luna", "held", {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id")}, f"Pausiert ({conv.get('pause_reason') or 'ohne Grund'}) – zurückgehalten: {text[:60]}")
        return {"status": "held", "conversation_id": conv["id"], "text": text, "author": author, "risk": risk, "pause_reason": conv.get("pause_reason")}
    capped = False
    if decision == "send" and conv["kind"] == "candidate":
        cap = int(policy.get("max_msgs_per_candidate_per_day") or 0)
        if cap > 0:
            day_start = S.iso(at.replace(hour=0, minute=0, second=0, microsecond=0))
            sent_today = c.execute("select count(*) from messages where conversation_id=? and dir='out' and author in ('luna','operator') and status='sent' and at>=?",
                                   (conv["id"], day_start)).fetchone()[0]
            capped = sent_today >= cap
    status = "pending_approval" if decision == "approve" else ("queued" if (in_quiet_hours(policy, at) or capped) else "sent")
    msg_id = db.insert(c, "messages", {"conversation_id": conv["id"], "dir": "out", "author": author, "text": text, "at": S.iso(at), "status": status,
                                       "template_id": template_id, "attachments": [], "meta": dict(meta or {}, risk=risk)})
    if status == "pending_approval":
        ctx = {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id"), "clinic_thread_id": conv.get("clinic_thread_id"), "message_id": msg_id}
        aid = db.insert(c, "approvals", {"kind": kind if kind in ("message", "match_send", "cohort_send", "escalation", "stage_change", "campaign_change") else "message",
                                         "risk": risk, "reason": reason or f"{risk}-Risiko in Modus {policy.get('mode')}/{conv.get('mode')}", "context": ctx, "draft": text,
                                         "suggested": suggested or {}, "status": "pending", "created_at": S.iso(at)})
        db.event(c, S.iso(at), "luna", "approval_created", dict(ctx, approval_id=aid), f"Freigabe nötig ({risk}): {reason or text[:60]}")
        return dict(db.get(c, "messages", msg_id), approval_id=aid)
    if status == "queued":
        due = next_window(policy, at)
        reason_txt = (f"Tageslimit erreicht ({policy.get('max_msgs_per_candidate_per_day')}/Tag): Versand {due.strftime('%d.%m. %H:%M')}" if capped
                      else f"Ruhezeit ({policy['quiet_hours'][0]}–{policy['quiet_hours'][1]} Uhr): Versand um {due.strftime('%H:%M')}")
        qid = db.insert(c, "queue", {"due_at": S.iso(due), "kind": "follow_up_clinic" if conv["kind"] == "clinic" else "follow_up_candidate",
                                     "target": {"message_id": msg_id, "conversation_id": conv["id"], "candidate_id": conv.get("candidate_id")},
                                     "reason": reason_txt, "status": "scheduled", "created_by": author})
        db.event(c, S.iso(at), author, "message_queued", {"conversation_id": conv["id"], "message_id": msg_id, "queue_id": qid}, reason_txt)
        return dict(db.get(c, "messages", msg_id), queue_id=qid)
    return _deliver(c, msg_id, at, actor=author)


# --- next_action ------------------------------------------------------------------------------------------
def next_action(conv, c=None):
    own = c is None
    c = c or db.db()
    try:
        return _next_action(c, conv)
    finally:
        if own:
            c.close()


def _next_action(c, conv):
    if conv["mode"] == "stopped":
        return {"action": "none", "label": "Gestoppt (Opt-out) – nie wieder anschreiben", "template_id": None, "risk": "high", "text": ""}
    if conv["kind"] == "clinic":
        return _next_action_clinic(c, conv)
    cand = candidate_of(c, conv)
    if not cand:
        return {"action": "none", "label": "kein Kandidat", "template_id": None, "risk": "low", "text": ""}
    docs = documents_of(c, cand["id"])
    esc = next((t.split(":", 1)[1] for t in conv.get("tags") or [] if t.startswith("escalation:")), None)
    if esc and conv["mode"] in ("manager", "human"):
        return {"action": "escalation_reply", "label": f"Eskalation: {TRIGGER_LABEL.get(esc, esc)} – Antwort freigeben", "template_id": None, "risk": "high",
                "text": S.escalation_draft(esc, cand), "kind": "escalation"}
    lang, state = conv.get("language") or "de", conv["state"]
    v = vars_for(c, cand)
    top = top_matches(c, cand, n=1)
    if top:
        v.update({"clinic_name": top[0]["name"], "clinic_town": top[0]["town"], "posting_title": top[0]["posting_title"] or "Pflegefachkraft (m/w/d)"})
    v["slot"] = slot_label(business_slots(now(c), 1)[0]["at"])

    def tpl(stage, action, label, risk="low", kind="message"):
        t = template_for(c, "whatsapp", lang, stage)
        return {"action": action, "label": label, "template_id": t["id"] if t else None, "risk": risk, "text": S.render(t["body"], v) if t else "", "kind": kind}

    ck = {x["field"]: x["status"] for x in checklist(cand, docs)}
    if state == "greeting":
        return tpl("greeting", "greet", "Begrüßen und Einwilligung einholen")
    if state == "consent":
        return tpl("consent", "ask_consent", "Einwilligung nachfassen")
    if state == "collect_basics":
        return tpl("collect_basics", "ask_basics", "Ausbildung und Erfahrung erfragen")
    if state == "collect_location":
        return tpl("collect_location", "ask_location", "Wohnort und Radius erfragen")
    if state in ("collect_docs", "docs_review"):
        missing = [k for k in ("cv", "education_cert") if ck.get(k) != "ok"]
        if missing:
            requested = any(ck.get(k) == "requested" for k in missing)
            return tpl("doc_reminder" if requested else "collect_docs", "request_doc", "Fehlende Unterlagen anfordern: " + ", ".join(CHECK_LABEL[k] for k in missing))
        if state == "collect_docs":
            return tpl("docs_review", "acknowledge_docs", "Unterlagen bestätigen, Prüfung ankündigen")
        return {"action": "advance_stage", "label": "Unterlagen vollständig → als qualifiziert markieren", "template_id": None, "risk": "low",
                "text": S.render(template_for(c, "whatsapp", lang, "qualified")["body"], v), "kind": "stage_change"}
    if state in ("qualified", "matching"):
        if not top:
            return {"action": "none", "label": "keine passende Klinik im Register", "template_id": None, "risk": "low", "text": ""}
        return tpl("profile_sent", "propose_matches", f"Profil an {top[0]['name']} senden (Score {top[0]['score']})", "medium", "match_send")
    if state == "profile_sent":
        if conv.get("next_actor") == "us":
            return {"action": "status_update", "label": "Zwischenstand geben, Klinik nachfassen", "template_id": None, "risk": "low",
                    "text": f"Hallo {v['first_name']}, von {v.get('clinic_name', 'der Klinik')} habe ich noch keine Rückmeldung – ich fasse heute nach und melde mich sofort.", "kind": "message"}
        return tpl("follow_up", "follow_up_clinic", "Klinik nachfassen (Kadenz 3/7/14 Werktage)")
    if state == "interview_prep":
        return tpl("interview_prep", "propose_slots", "Terminvorschlag an Kandidat/in", "medium")
    if state == "awaiting_feedback":
        return tpl("awaiting_feedback", "request_feedback", "Feedback nach dem Gespräch erfragen")
    if state == "dormant":
        return tpl("nurture", "nurture", "Nurture-Nachricht (inaktiv)")
    if state == "placed":
        return {"action": "none", "label": "Vermittelt – Onboarding beim Kunden", "template_id": None, "risk": "low", "text": ""}
    return {"action": "none", "label": "nichts zu tun", "template_id": None, "risk": "low", "text": ""}


def _thread_vars(c, th, cand=None):
    cands = [db.get(c, "candidates", i) for i in th.get("candidate_ids") or []]
    cands = [x for x in cands if x]
    first = cand or (cands[0] if cands else None)
    posting = next((dict(r) for r in c.execute("select * from registry_postings where clinic_id=? order by posting_id limit 1", (th["clinic_id"],))), None)
    return {"contact_name": (th.get("contact_name") or "Damen und Herren").split(" (")[0], "clinic_name": th.get("clinic_name"),
            "posting_title": posting["title"] if posting else "Pflegefachkraft (m/w/d)", "initials": first["initials"] if first else "–",
            "profile": S.anonymised_profile(first) if first else "", "start_from": first["start_from"] if first else "", "format": "Video",
            "slots": "\n".join(f"{i + 1}. {slot_label(s['at'])}" for i, s in enumerate(th.get("proposed_slots") or [])),
            "slot": slot_label(th["agreed_slot"]) if th.get("agreed_slot") else "", "n": len(cands), "criteria": "",
            "profiles": "\n\n".join(S.anonymised_profile(x) for x in cands)}


def email_text(c, stage, v):
    t = template_for(c, "email", "de", stage)
    return (f"Betreff: {S.render(t['subject'], v)}\n\n{S.render(t['body'], v)}" if t else ""), (t["id"] if t else None)


def _next_action_clinic(c, conv):
    th = thread_of(c, conv)
    if not th:
        return {"action": "none", "label": "kein Thread", "template_id": None, "risk": "low", "text": ""}
    v = _thread_vars(c, th)
    st = th["state"]
    if st == "draft":
        text, tid = email_text(c, "cohort_bundle" if len(th.get("candidate_ids") or []) > 1 else "clinic_intro", v)
        return {"action": "send_intro", "label": f"Erstkontakt an {th['clinic_name']} (neuer Kontakt)", "template_id": tid, "risk": "high", "text": text, "kind": "match_send"}
    if st in ("intro_sent", "awaiting_reply"):
        text, tid = email_text(c, "clinic_follow_up", v)
        return {"action": "follow_up_now", "label": f"Nachfassen #{th['followup_attempt'] + 1} von {th['followup_max']}", "template_id": tid, "risk": "low", "text": text}
    if st == "interested":
        slots = th.get("proposed_slots") or business_slots(now(c))
        v["slots"] = "\n".join(f"{i + 1}. {slot_label(s['at'])}" for i, s in enumerate(slots))
        text, tid = email_text(c, "propose_slots", v)
        return {"action": "propose_slots", "label": "Drei Gesprächstermine vorschlagen", "template_id": tid, "risk": "medium", "text": text}
    if st == "scheduling":
        text, tid = email_text(c, "confirm_slot", v)
        return {"action": "confirm_slot", "label": "Termin bestätigen (Klinik wählt / kontert)", "template_id": tid, "risk": "medium", "text": text}
    if st == "scheduled":
        return {"action": "remind", "label": f"Erinnerungen 24 h / 2 h vor {v['slot']}", "template_id": None, "risk": "low", "text": ""}
    if st == "feedback_pending":
        text, tid = email_text(c, "feedback_request", v)
        return {"action": "request_feedback", "label": "Feedback der Klinik erbitten", "template_id": tid, "risk": "low", "text": text}
    return {"action": "none", "label": {"closed_won": "Vermittelt", "closed_lost": "Abgesagt", "no_response": "Keine Antwort nach 3 Versuchen"}.get(st, "geschlossen"), "template_id": None, "risk": "low", "text": ""}


# --- apply_inbound ----------------------------------------------------------------------------------------
def extract(c, text, attachments=None):
    """Regex field extraction: PLZ, city (registry towns), German level, years, document kinds."""
    meta = {}
    t = text or ""
    low = t.lower()
    m = re.search(r"\b(\d{5})\b", t)
    if m:
        meta["plz"] = m.group(1)
    towns = [r[0] for r in c.execute("select distinct town from registry_clinics where town is not null and town<>''")]
    hit = [tw for tw in towns if re.search(r"\b" + re.escape(tw.lower()) + r"\b", low)]
    if hit:
        meta["city"] = max(hit, key=len)
    m = re.search(r"\b([ABC][12])\b", t.upper())
    if m:
        meta["german_level"] = m.group(1)
    m = re.search(r"(\d{1,2})\s*(jahre|years|ani|jahren)", low)
    if m:
        meta["experience_years"] = int(m.group(1))
    docs = [a.get("kind") for a in attachments or [] if a.get("kind")]
    for kind, rx in DOC_WORDS.items():
        if attachments and re.search(rx, low) and kind not in docs:
            docs.append(kind)
    if docs:
        meta["documents"] = docs
    return meta


def detect_trigger(text, policy):
    low = (text or "").lower()
    allowed = policy.get("escalation_triggers") or TRIGGERS
    for kind, words in TRIGGERS.items():
        if kind in allowed and any(w in low for w in words):
            return kind
    return None


def apply_inbound(conv, text, attachments=None, c=None, at=None, author=None):
    own = c is None
    c = c or db.db()
    try:
        r = _apply_inbound(c, conv, text, attachments or [], at, author)
        if own:
            c.commit()
        return r
    finally:
        if own:
            c.close()


def _apply_inbound(c, conv, text, attachments, at=None, author=None):
    at = at or now(c)
    author = author or ("clinic" if conv["kind"] == "clinic" else "candidate")
    meta = extract(c, text, attachments) if conv["kind"] == "candidate" else {}
    msg_id = db.insert(c, "messages", {"conversation_id": conv["id"], "dir": "in", "author": author, "text": text, "at": S.iso(at), "status": "received",
                                       "attachments": attachments, "meta": meta})
    target = {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id"), "clinic_thread_id": conv.get("clinic_thread_id"), "message_id": msg_id}
    db.event(c, S.iso(at), author, "message_received" if conv["channel"] == "whatsapp" else "email_received", target, f"{text[:80]}")
    result = {"message_id": msg_id, "extracted": meta, "escalation": None, "stopped": False, "state": conv["state"]}
    upd = {"last_message_at": S.iso(at), "last_preview": text[:120], "unread": (conv.get("unread") or 0) + 1, "next_actor": "us", "sla_due_at": S.iso(at + timedelta(hours=4))}
    if conv["kind"] == "clinic":
        upd.update(_clinic_inbound(c, conv, text, at))
        db.update(c, "conversations", conv["id"], upd)
        result["state"] = upd.get("state", conv["state"])
        return result
    cand = candidate_of(c, conv)
    tags = list(conv.get("tags") or [])
    if STOP_RE.search(text or ""):
        upd.update({"mode": "stopped", "next_actor": "none", "sla_due_at": None, "tags": tags + (["STOP"] if "STOP" not in tags else []), "closed_at": S.iso(at), "state": "lost", "unread": 0})
        db.update(c, "conversations", conv["id"], upd)
        if cand:
            db.update(c, "candidates", cand["id"], {"stage": "lost", "stage_changed_at": S.iso(at), "lost_reason": "Opt-out (STOP)"})
        db.event(c, S.iso(at), "system", "opt_out", target, f"{cand['initials'] if cand else conv['id']}: STOP empfangen – Modus stopped, nie wieder anschreiben")
        db.event(c, S.iso(at), "system", "mode_changed", target, "luna → stopped (STOP)")
        result.update(stopped=True, state="lost")
        return result
    if not cand:
        db.update(c, "conversations", conv["id"], upd)
        return result
    # field updates
    cu = {}
    for k in ("plz", "city", "german_level", "experience_years"):
        if meta.get(k) and meta[k] != cand.get(k):
            cu[k] = meta[k]
    if "city" in cu:
        cl = c.execute("select regierungsbezirk from registry_clinics where town=? limit 1", (cu["city"],)).fetchone()
        if cl:
            cu["region"] = cl[0]
    if conv["state"] == "consent" and YES_RE.search(text or "") and not cand.get("consent_at"):
        cu["consent_at"] = S.iso(at)
    for kind in meta.get("documents") or []:
        d = c.execute("select id, status from documents where candidate_id=? and kind=?", (cand["id"], kind)).fetchone()
        if d:
            db.update(c, "documents", d["id"], {"status": "received", "received_at": S.iso(at), "note": "per WhatsApp erhalten"})
        else:
            db.insert(c, "documents", {"candidate_id": cand["id"], "kind": kind, "status": "received", "received_at": S.iso(at), "note": "per WhatsApp erhalten"})
        db.event(c, S.iso(at), "candidate", "document_received", target, f"{cand['initials']}: {kind} erhalten")
    # state machine
    state = conv["state"]
    docs = documents_of(c, cand["id"])
    ck = {x["field"]: x["status"] for x in checklist({**cand, **cu}, docs)}
    if state == "greeting":
        state = "consent" if cand.get("consent_at") or cu.get("consent_at") else "greeting"
    elif state == "consent" and (cu.get("consent_at") or cand.get("consent_at")) and YES_RE.search(text or ""):
        state = "collect_basics"
    elif state == "collect_basics" and (meta.get("experience_years") is not None or len(text or "") > 15):
        state = "collect_location"
    elif state == "collect_location" and (meta.get("plz") or meta.get("city")):
        state = "collect_docs"
    elif state == "collect_docs" and ck.get("cv") == "ok" and ck.get("education_cert") == "ok":
        state = "docs_review"
    elif state == "interview_prep" and YES_RE.search(text or ""):
        if "slot_confirmed" not in tags:
            tags.append("slot_confirmed")
    if state != conv["state"]:
        upd["state"] = state
        db.event(c, S.iso(at), "luna", "state_changed", target, f"{cand['initials']}: {conv['state']} → {state}")
        new_stage = STATE_STAGE.get(state)
        if new_stage and new_stage in S.STAGES and cand["stage"] in S.STAGES and S.STAGES.index(new_stage) > S.STAGES.index(cand["stage"]):
            cu.update({"stage": new_stage, "stage_changed_at": S.iso(at)})
            db.event(c, S.iso(at), "luna", "stage_changed", target, f"{cand['initials']}: {cand['stage']} → {new_stage}")
    if cand["stage"] == "dormant" and cand["stage"] not in cu:
        cu.update({"stage": STATE_STAGE.get(state, "qualifying") if state not in ("dormant", "lost") else "qualifying", "stage_changed_at": S.iso(at)})
        if state == "dormant":
            upd["state"] = state = "collect_docs"
        db.event(c, S.iso(at), "luna", "stage_changed", target, f"{cand['initials']}: dormant → {cu['stage']} (wieder aktiv)")
    if cu:
        db.update(c, "candidates", cand["id"], cu)
    # escalation
    trig = detect_trigger(text, db.policy(c))
    if trig:
        result["escalation"] = trig
        if f"escalation:{trig}" not in tags:
            tags.append(f"escalation:{trig}")
        if conv["mode"] in ("luna", "paused"):
            upd["mode"] = "manager"
            db.event(c, S.iso(at), "system", "mode_changed", target, f"{cand['initials']}: {conv['mode']} → manager (Eskalation: {TRIGGER_LABEL[trig]})")
        aid = db.insert(c, "approvals", {"kind": "escalation", "risk": "high", "reason": TRIGGER_LABEL[trig], "context": dict(target),
                                         "draft": S.escalation_draft(trig, cand), "suggested": {"action": "reply_and_release", "mode_after": "luna"},
                                         "status": "pending", "created_at": S.iso(at)})
        db.event(c, S.iso(at), "luna", "approval_created", dict(target, approval_id=aid), f"Eskalation ({TRIGGER_LABEL[trig]}) bei {cand['initials']} – Manager-Entscheidung nötig")
        result["approval_id"] = aid
    upd["tags"] = tags
    db.update(c, "conversations", conv["id"], upd)
    result["state"] = state
    return result


def _clinic_inbound(c, conv, text, at):
    """Clinic e-mail state machine: interested / slot accepted / counter-proposal / decline / feedback."""
    th = thread_of(c, conv)
    if not th:
        return {}
    low = (text or "").lower()
    upd, tupd = {}, {"last_at": S.iso(at)}
    target = {"conversation_id": conv["id"], "clinic_thread_id": th["id"], "clinic_id": th["clinic_id"]}
    slots = th.get("proposed_slots") or []
    m = re.search(r"termin\s*(\d)|(\d)\.\s*termin|(ersten|zweiten|dritten)\s+termin", low)
    if th["state"] in ("scheduling", "interested") and m and slots:
        idx = int(m.group(1) or m.group(2) or {"ersten": 1, "zweiten": 2, "dritten": 3}[m.group(3)]) - 1
        if 0 <= idx < len(slots):
            _confirm_slot(c, th, slots[idx]["at"], at, by="clinic")
            upd["state"] = "scheduled"
            return dict(upd, next_actor="candidate", sla_due_at=S.iso(at + timedelta(hours=24)))
    if th["state"] in ("scheduling", "interested") and re.search(r"passen leider nicht|ginge|gegenvorschlag|alternativ|stattdessen", low):
        mm = re.search(r"(\d{1,2})\.(\d{1,2})\.?\s*(?:um\s*)?(\d{1,2})[:.](\d{2})", text or "")
        base = now(c)
        if mm:
            try:
                new = base.replace(month=int(mm.group(2)), day=int(mm.group(1)), hour=int(mm.group(3)), minute=int(mm.group(4)), second=0)
            except ValueError:
                new = business_slots(base, 1)[0]["at"]
        else:
            new = business_slots(base + timedelta(days=3), 1)[0]["at"]
        slots = slots + [{"at": S.iso(new) if not isinstance(new, str) else new, "format": "video", "by": "clinic"}]
        tupd.update({"proposed_slots": slots, "state": "scheduling"})
        upd["state"] = "scheduling"
        db.event(c, S.iso(at), "clinic", "slot_countered", target, f"{th['clinic_name']} schlägt {slot_label(slots[-1]['at'])} vor")
    elif re.search(r"besetzt|leider nicht|absage|kein bedarf|nicht ausreichend", low) and th["state"] not in ("closed_won",):
        tupd["state"] = "closed_lost"
        upd.update({"state": "closed_lost", "closed_at": S.iso(at), "next_actor": "none", "sla_due_at": None})
        c.execute("update matches set status='declined_by_clinic' where clinic_id=? and candidate_id in (%s) and status in ('sent','clinic_interested','interview')" % ",".join("?" * len(th.get("candidate_ids") or [0])),
                  (th["clinic_id"], *(th.get("candidate_ids") or [0])))
        db.event(c, S.iso(at), "clinic", "thread_closed", target, f"{th['clinic_name']}: Absage")
    elif th["state"] == "feedback_pending" and re.search(r"positiv|angebot|einstellen|zusage|probearbeiten", low):
        tupd["state"] = "closed_won"
        upd.update({"state": "closed_won", "closed_at": S.iso(at), "next_actor": "us"})
        c.execute("update interviews set status='done', feedback=? where clinic_thread_id=? and feedback is null", (text[:200], th["id"]))
        for cid in th.get("candidate_ids") or []:
            c.execute("update matches set status='placed' where clinic_id=? and candidate_id=?", (th["clinic_id"], cid))
            cand = db.get(c, "candidates", cid)
            if cand and cand["stage"] in S.STAGES and S.STAGES.index(cand["stage"]) < S.STAGES.index("offer"):
                db.update(c, "candidates", cid, {"stage": "offer", "stage_changed_at": S.iso(at)})
                db.event(c, S.iso(at), "luna", "stage_changed", dict(target, candidate_id=cid), f"{cand['initials']}: {cand['stage']} → offer ({th['clinic_name']})")
        db.event(c, S.iso(at), "clinic", "feedback_received", target, f"{th['clinic_name']}: positives Feedback / Angebot")
    elif th["state"] in ("intro_sent", "awaiting_reply", "draft") and re.search(r"interessant|gerne|kennenlernen|unterlagen|gespräch|passt", low):
        tupd.update({"state": "interested", "next_followup_at": None})
        upd["state"] = "interested"
        c.execute("update queue set status='cancelled', result='Klinik hat geantwortet' where status='scheduled' and kind='follow_up_clinic' and target like ?", (f'%"clinic_thread_id": {th["id"]}%',))
        c.execute("update matches set status='clinic_interested' where clinic_id=? and candidate_id in (%s) and status='sent'" % ",".join("?" * len(th.get("candidate_ids") or [0])),
                  (th["clinic_id"], *(th.get("candidate_ids") or [0])))
        for cid in th.get("candidate_ids") or []:
            cand = db.get(c, "candidates", cid)
            if cand and cand["stage"] == "profile_sent":
                db.update(c, "candidates", cid, {"stage": "interview_scheduling", "stage_changed_at": S.iso(at)})
        db.event(c, S.iso(at), "clinic", "clinic_interested", target, f"{th['clinic_name']} ist interessiert")
    db.update(c, "clinic_threads", th["id"], tupd)
    return upd


# --- clinic thread actions ------------------------------------------------------------------------------------
def clinic_conv(c, th):
    return db.row("conversations", c.execute("select * from conversations where clinic_thread_id=?", (th["id"],)).fetchone())


def _ensure_clinic_conv(c, th, at):
    conv = clinic_conv(c, th)
    if conv:
        return conv
    cid = db.insert(c, "conversations", {"kind": "clinic", "clinic_thread_id": th["id"], "channel": "email", "account_id": th.get("mailbox_id"), "mode": "luna", "state": th["state"],
                                         "next_actor": "us", "language": "de", "tags": ["COHORT"] if th.get("cohort_id") else [], "opened_at": S.iso(at)})
    return db.get(c, "conversations", cid)


def thread_action(c, th, action, params=None, as_="luna", at=None):
    params = params or {}
    at = at or now(c)
    conv = _ensure_clinic_conv(c, th, at)
    v = _thread_vars(c, th)
    target = {"conversation_id": conv["id"], "clinic_thread_id": th["id"], "clinic_id": th["clinic_id"]}
    pol = db.policy(c)
    if action == "follow_up_now":
        text, tid = email_text(c, "clinic_follow_up", v)
        r = send(c, conv, text, as_, "low", tid, reason=f"Nachfassen bei {th['clinic_name']}", at=at)
        attempt = th["followup_attempt"] + 1
        cad = pol["cadence_clinic_business_days"]
        upd = {"followup_attempt": attempt, "state": "awaiting_reply" if attempt < th["followup_max"] else "no_response"}
        if attempt < th["followup_max"]:
            upd["next_followup_at"] = business_slots(at + timedelta(days=cad[min(attempt, len(cad) - 1)] - 1), 1)[0]["at"]
            db.insert(c, "queue", {"due_at": upd["next_followup_at"], "kind": "follow_up_clinic", "target": {"clinic_thread_id": th["id"], "conversation_id": conv["id"]},
                                   "reason": f"{th['clinic_name']}: Nachfassen #{attempt + 1}", "status": "scheduled", "attempt": attempt, "created_by": "luna"})
        else:
            upd["next_followup_at"] = None
            db.update(c, "conversations", conv["id"], {"next_actor": "none", "sla_due_at": None, "closed_at": S.iso(at), "state": "no_response"})
            db.event(c, S.iso(at), "luna", "thread_closed", target, f"{th['clinic_name']}: keine Antwort nach {attempt} Versuchen")
        db.update(c, "clinic_threads", th["id"], upd)
        if r.get("status") == "sent":
            db.update(c, "conversations", conv["id"], {"state": upd["state"]})
        return r
    if action == "propose_slots":
        slots = params.get("slots") or business_slots(at, 3, random.Random(th["id"]))
        slots = [s if isinstance(s, dict) else {"at": s, "format": params.get("format", "video")} for s in slots]
        v["slots"] = "\n".join(f"{i + 1}. {slot_label(s['at'])}" for i, s in enumerate(slots))
        text, tid = email_text(c, "propose_slots", v)
        r = send(c, conv, text, as_, "medium", tid, reason=f"Terminvorschläge an {th['clinic_name']}", at=at)
        db.update(c, "clinic_threads", th["id"], {"proposed_slots": slots, "state": "scheduling"})
        db.update(c, "conversations", conv["id"], {"state": "scheduling"})
        db.event(c, S.iso(at), "luna", "slots_proposed", target, f"{len(slots)} Termine an {th['clinic_name']}")
        return r
    if action == "confirm_slot":
        slot = params.get("slot") or params.get("at")
        if slot is None and params.get("index") is not None and th.get("proposed_slots"):
            slot = th["proposed_slots"][int(params["index"])]["at"]
        if not slot:
            slot = (th.get("proposed_slots") or business_slots(at, 1))[0]["at"]
        _confirm_slot(c, th, slot, at, by=as_)
        th = db.get(c, "clinic_threads", th["id"])
        v = _thread_vars(c, th)
        text, tid = email_text(c, "confirm_slot", v)
        return send(c, conv, text, as_, "medium", tid, reason=f"Terminbestätigung an {th['clinic_name']}", at=at)
    if action == "counter_slot":
        slot = params.get("slot") or params.get("at") or business_slots(at + timedelta(days=2), 1)[0]["at"]
        slots = (th.get("proposed_slots") or []) + [{"at": slot, "format": params.get("format", "video"), "by": "us"}]
        db.update(c, "clinic_threads", th["id"], {"proposed_slots": slots, "state": "scheduling"})
        v["slots"] = f"1. {slot_label(slot)}"
        text, tid = email_text(c, "propose_slots", v)
        return send(c, conv, text, as_, "medium", tid, reason=f"Gegenvorschlag an {th['clinic_name']}", at=at)
    if action == "request_feedback":
        text, tid = email_text(c, "feedback_request", v)
        r = send(c, conv, text, as_, "low", tid, reason=f"Feedback von {th['clinic_name']}", at=at)
        db.update(c, "clinic_threads", th["id"], {"state": "feedback_pending"})
        db.update(c, "conversations", conv["id"], {"state": "feedback_pending"})
        return r
    if action == "close":
        won = (params.get("outcome") or params.get("result") or "lost") == "won"
        st = "closed_won" if won else "closed_lost"
        db.update(c, "clinic_threads", th["id"], {"state": st, "next_followup_at": None})
        db.update(c, "conversations", conv["id"], {"state": st, "closed_at": S.iso(at), "next_actor": "none", "sla_due_at": None})
        for cid in th.get("candidate_ids") or []:
            c.execute("update matches set status=? where clinic_id=? and candidate_id=? and status not in ('placed')", ("placed" if won else "declined_by_clinic", th["clinic_id"], cid))
            if won:
                db.update(c, "candidates", cid, {"stage": "placed", "stage_changed_at": S.iso(at)})
                cc = db.row("conversations", c.execute("select * from conversations where candidate_id=?", (cid,)).fetchone())
                if cc:
                    db.update(c, "conversations", cc["id"], {"state": "placed"})
        db.event(c, S.iso(at), as_, "thread_closed", target, f"{th['clinic_name']}: {'Vermittlung' if won else 'geschlossen'} ({params.get('reason') or '-'})")
        return {"status": "ok"}
    raise ValueError(f"unknown action {action!r}")


def _confirm_slot(c, th, slot_at, at, by="luna"):
    """Agreed slot → thread scheduled, interview confirmed, candidate informed, reminders 24 h / 2 h queued."""
    target = {"clinic_thread_id": th["id"], "clinic_id": th["clinic_id"]}
    db.update(c, "clinic_threads", th["id"], {"agreed_slot": slot_at, "state": "scheduled"})
    conv = clinic_conv(c, th)
    if conv:
        db.update(c, "conversations", conv["id"], {"state": "scheduled"})
    for cid in (th.get("candidate_ids") or [])[:1]:
        iv = c.execute("select id from interviews where clinic_thread_id=? and candidate_id=? and status in ('proposed','confirmed')", (th["id"], cid)).fetchone()
        if iv:
            db.update(c, "interviews", iv["id"], {"at": slot_at, "status": "confirmed"})
            ivid = iv["id"]
        else:
            ivid = db.insert(c, "interviews", {"candidate_id": cid, "clinic_id": th["clinic_id"], "clinic_thread_id": th["id"], "at": slot_at, "format": "video", "round": th.get("round") or 1, "status": "confirmed"})
        c.execute("update matches set status='interview' where clinic_id=? and candidate_id=? and status in ('sent','clinic_interested','approved','proposed')", (th["clinic_id"], cid))
        cand = db.get(c, "candidates", cid)
        if cand:
            if cand["stage"] in S.STAGES and S.STAGES.index(cand["stage"]) < S.STAGES.index("interview_scheduled"):
                db.update(c, "candidates", cid, {"stage": "interview_scheduled", "stage_changed_at": S.iso(at)})
            cc = db.row("conversations", c.execute("select * from conversations where candidate_id=?", (cid,)).fetchone())
            if cc and cc["mode"] != "stopped":
                db.update(c, "conversations", cc["id"], {"state": "interview_prep"})
                t = template_for(c, "whatsapp", cc.get("language") or "de", "interview_prep")
                v = vars_for(c, cand, {"clinic_name": th["clinic_name"], "slot": slot_label(slot_at), "format": "Video"})
                send(c, cc, S.render(t["body"], v) if t else f"Termin bei {th['clinic_name']}: {slot_label(slot_at)}", "luna", "medium", t["id"] if t else None,
                     reason=f"Termin an {cand['initials']} bestätigen", at=at)
            for h in (24, 2):
                due = S.parse(slot_at) - timedelta(hours=h)
                if due > at:
                    db.insert(c, "queue", {"due_at": S.iso(due), "kind": "interview_reminder", "target": {"interview_id": ivid, "candidate_id": cid, "clinic_thread_id": th["id"]},
                                           "reason": f"Erinnerung {h} h vor Gespräch bei {th['clinic_name']}", "status": "scheduled", "created_by": "luna"})
            db.event(c, S.iso(at), by, "interview_scheduled", dict(target, candidate_id=cid, interview_id=ivid), f"Gespräch {cand['initials']} × {th['clinic_name']} am {slot_label(slot_at)}")


# --- matches / cohorts / postings ---------------------------------------------------------------------------------
def _throttle_counts(c):
    """Live recheck of the two match throttles (max_concurrent_profiles_per_candidate, max_profiles_per_clinic_per_week)
    against the current matches table – the same counting logic cohort_preview() uses at preview time
    (matching.throttle_counts), so match_send()/cohort_send() cannot bypass what the preview already enforced."""
    pol = db.policy(c)
    matches_ = db.rows("matches", c.execute("select * from matches"))
    return M.throttle_counts(matches_, pol, pol["sim_now"])


def match_send(c, match, as_="luna", at=None, force=False):
    """Send one anonymised profile to a clinic: obeys the risk policy and the concurrency/weekly throttles unless force (approval granted)."""
    at = at or now(c)
    cand = db.get(c, "candidates", match["candidate_id"])
    cl = clinic_row(c, match["clinic_id"]) or {"name": f"Klinik {match['clinic_id']}", "town": ""}
    if not force:
        decision = resolve(db.policy(c), None, "medium", as_, "match_send")
        if decision == "approve":
            ctx = {"candidate_id": cand["id"], "match_id": match["id"], "clinic_id": match["clinic_id"], "conversation_id": _cand_conv_id(c, cand["id"])}
            v = {"contact_name": "Pflegedirektion", "clinic_name": cl["name"], "posting_title": (posting_row(c, match.get("posting_id")) or {}).get("title") or "Pflegefachkraft (m/w/d)",
                 "initials": cand["initials"], "profile": S.anonymised_profile(cand)}
            text, _ = email_text(c, "clinic_intro", v)
            aid = db.insert(c, "approvals", {"kind": "match_send", "risk": "medium", "reason": f"Profil {cand['initials']} an {cl['name']} senden (Score {int(match['score'])})",
                                             "context": ctx, "draft": text, "suggested": {"action": "send_profile"}, "status": "pending", "created_at": S.iso(at)})
            db.update(c, "matches", match["id"], {"status": "approved"})
            db.event(c, S.iso(at), "luna", "approval_created", dict(ctx, approval_id=aid), f"Profil-Versand {cand['initials']} → {cl['name']} wartet auf Freigabe")
            return {"status": "pending_approval", "approval_id": aid, "match": db.get(c, "matches", match["id"])}
        if decision == "log":
            return {"status": "logged", "match": match}
        active, weekly, max_c, max_k = _throttle_counts(c)
        if active[cand["id"]] >= max_c or weekly[str(match["clinic_id"])] >= max_k:
            reason_txt = (f"Durchsatzlimit: {cand['initials']} hat bereits {active[cand['id']]} laufende Profile (Limit {max_c})" if active[cand["id"]] >= max_c
                          else f"Durchsatzlimit: {cl['name']} hat diese Woche schon {weekly[str(match['clinic_id'])]} Profile erhalten (Limit {max_k})")
            ctx = {"candidate_id": cand["id"], "match_id": match["id"], "clinic_id": match["clinic_id"], "conversation_id": _cand_conv_id(c, cand["id"])}
            aid = db.insert(c, "approvals", {"kind": "throttle_exceeded", "risk": "medium", "reason": reason_txt, "context": ctx, "draft": None,
                                             "suggested": {"action": "match_send"}, "status": "pending", "created_at": S.iso(at)})
            db.update(c, "matches", match["id"], {"status": "approved"})
            db.event(c, S.iso(at), "luna", "approval_created", dict(ctx, approval_id=aid), reason_txt)
            return {"status": "pending_approval", "approval_id": aid, "match": db.get(c, "matches", match["id"])}
    th_id = db.insert(c, "clinic_threads", {"clinic_id": str(match["clinic_id"]), "clinic_name": cl["name"], "contact_name": "Pflegedirektion", "contact_email": f"pflegedirektion.{match['clinic_id']}@example.org",
                                            "mailbox_id": _mailbox(c, "clinic_intro"), "cohort_id": match.get("cohort_id"), "state": "draft", "followup_attempt": 0, "followup_max": 3,
                                            "candidate_ids": [cand["id"]], "proposed_slots": [], "round": 1, "created_at": S.iso(at)})
    th = db.get(c, "clinic_threads", th_id)
    conv = _ensure_clinic_conv(c, th, at)
    v = _thread_vars(c, th, cand)
    v["posting_title"] = (posting_row(c, match.get("posting_id")) or {}).get("title") or v["posting_title"]
    text, tid = email_text(c, "clinic_intro", v)
    r = send(c, conv, text, "operator" if force else as_, "medium", tid, kind="match_send", reason=f"Profil {cand['initials']} an {cl['name']}", at=at)
    cad = db.policy(c)["cadence_clinic_business_days"]
    nfu = business_slots(at + timedelta(days=cad[0] - 1), 1)[0]["at"]
    db.update(c, "clinic_threads", th_id, {"state": "intro_sent", "next_followup_at": nfu, "last_at": S.iso(at)})
    db.update(c, "conversations", conv["id"], {"state": "intro_sent"})
    db.insert(c, "queue", {"due_at": nfu, "kind": "follow_up_clinic", "target": {"clinic_thread_id": th_id, "conversation_id": conv["id"]}, "reason": f"{cl['name']}: Nachfassen #1", "status": "scheduled", "created_by": "luna"})
    db.update(c, "matches", match["id"], {"status": "sent"})
    if cand["stage"] in S.STAGES and S.STAGES.index(cand["stage"]) < S.STAGES.index("profile_sent"):
        db.update(c, "candidates", cand["id"], {"stage": "profile_sent", "stage_changed_at": S.iso(at)})
    cc = db.row("conversations", c.execute("select * from conversations where candidate_id=?", (cand["id"],)).fetchone())
    if cc and cc["mode"] != "stopped":
        db.update(c, "conversations", cc["id"], {"state": "profile_sent"})
        t = template_for(c, "whatsapp", cc.get("language") or "de", "profile_sent")
        send(c, cc, S.render(t["body"], vars_for(c, cand, {"clinic_name": cl["name"], "clinic_town": cl.get("town") or ""})), "luna", "low", t["id"], reason="Profil-Versand mitteilen", at=at)
    db.event(c, S.iso(at), "luna" if not force else "operator", "profile_sent", {"candidate_id": cand["id"], "clinic_id": str(match["clinic_id"]), "clinic_thread_id": th_id, "conversation_id": _cand_conv_id(c, cand["id"])},
             f"Profil {cand['initials']} an {cl['name']} ({cl.get('town') or ''}) gesendet – Score {int(match['score'])}")
    return {"status": r.get("status", "sent"), "thread_id": th_id, "match": db.get(c, "matches", match["id"])}


def _cand_conv_id(c, cand_id):
    r = c.execute("select id from conversations where candidate_id=? and kind='candidate'", (cand_id,)).fetchone()
    return r[0] if r else None


def _mailbox(c, purpose):
    r = c.execute("select id from accounts where kind='mailbox' and routing like ? and status<>'disconnected' order by id limit 1", (f'%"{purpose}"%',)).fetchone()
    if not r:
        r = c.execute("select id from accounts where kind='mailbox' order by id limit 1").fetchone()
    return r[0] if r else None


def cohort_send(c, cohort, as_="luna", at=None, force=False):
    at = at or now(c)
    if not force:
        decision = resolve(db.policy(c), None, "medium", as_, "cohort_send")
        if decision == "approve":
            cands = [db.get(c, "candidates", i) for i in cohort["candidate_ids"]]
            draft = "Betreff: Pflegekräfte für Ihre offenen Stellen – anonymisierte Profile\n\n" + "\n\n".join(S.anonymised_profile(x) for x in cands if x)
            aid = db.insert(c, "approvals", {"kind": "cohort_send", "risk": "medium", "reason": f"Cohort „{cohort['name']}“ an {len(cohort['clinic_ids'])} Kliniken senden ({len(cohort['candidate_ids'])} Profile)",
                                             "context": {"cohort_id": cohort["id"]}, "draft": draft, "suggested": {"action": "send_cohort"}, "status": "pending", "created_at": S.iso(at)})
            db.update(c, "cohorts", cohort["id"], {"status": "pending_approval"})
            db.event(c, S.iso(at), "luna", "approval_created", {"cohort_id": cohort["id"], "approval_id": aid}, f"Cohort „{cohort['name']}“ wartet auf Freigabe")
            return {"status": "pending_approval", "approval_id": aid, "threads_created": 0}
        if decision == "log":
            return {"status": "logged", "threads_created": 0}
        active, weekly, max_c, max_k = _throttle_counts(c)
        over_c = sum(1 for cid in cohort["candidate_ids"] if active[cid] >= max_c)
        over_k = sum(1 for clid in cohort["clinic_ids"] if weekly[str(clid)] >= max_k)
        if over_c or over_k:
            cands = [db.get(c, "candidates", i) for i in cohort["candidate_ids"]]
            draft = "Betreff: Pflegekräfte für Ihre offenen Stellen – anonymisierte Profile\n\n" + "\n\n".join(S.anonymised_profile(x) for x in cands if x)
            reason_txt = f"Durchsatzlimit: {over_c} Kandidat/in(nen) am Limit ({max_c} laufende Profile), {over_k} Klinik(en) am Wochenlimit ({max_k} Profile/Woche)"
            aid = db.insert(c, "approvals", {"kind": "throttle_exceeded", "risk": "medium", "reason": reason_txt,
                                             "context": {"cohort_id": cohort["id"]}, "draft": draft, "suggested": {"action": "cohort_send"}, "status": "pending", "created_at": S.iso(at)})
            db.update(c, "cohorts", cohort["id"], {"status": "pending_approval"})
            db.event(c, S.iso(at), "luna", "approval_created", {"cohort_id": cohort["id"], "approval_id": aid}, reason_txt)
            return {"status": "pending_approval", "approval_id": aid, "threads_created": 0}
    created = 0
    cad = db.policy(c)["cadence_clinic_business_days"]
    for clinic_id in cohort["clinic_ids"]:
        cl = clinic_row(c, clinic_id) or {"name": f"Klinik {clinic_id}", "town": ""}
        th_id = db.insert(c, "clinic_threads", {"clinic_id": str(clinic_id), "clinic_name": cl["name"], "contact_name": "Pflegedirektion", "contact_email": f"pflegedirektion.{clinic_id}@example.org",
                                                "mailbox_id": _mailbox(c, "cohort_send"), "cohort_id": cohort["id"], "state": "draft", "followup_attempt": 0, "followup_max": 3,
                                                "candidate_ids": list(cohort["candidate_ids"]), "proposed_slots": [], "round": 1, "created_at": S.iso(at)})
        th = db.get(c, "clinic_threads", th_id)
        conv = _ensure_clinic_conv(c, th, at)
        v = _thread_vars(c, th)
        v["criteria"] = cohort["name"]
        text, tid = email_text(c, "cohort_bundle", v)
        send(c, conv, text, "operator" if force else as_, "medium", tid, kind="cohort_send", reason=f"Cohort an {cl['name']}", at=at)
        nfu = business_slots(at + timedelta(days=cad[0] - 1), 1)[0]["at"]
        db.update(c, "clinic_threads", th_id, {"state": "intro_sent", "next_followup_at": nfu, "last_at": S.iso(at)})
        db.update(c, "conversations", conv["id"], {"state": "intro_sent"})
        db.insert(c, "queue", {"due_at": nfu, "kind": "follow_up_clinic", "target": {"clinic_thread_id": th_id, "conversation_id": conv["id"]}, "reason": f"{cl['name']}: Nachfassen #1", "status": "scheduled", "created_by": "luna"})
        for cid in cohort["candidate_ids"]:
            m = c.execute("select id from matches where candidate_id=? and clinic_id=?", (cid, str(clinic_id))).fetchone()
            if m:
                db.update(c, "matches", m[0], {"status": "sent", "cohort_id": cohort["id"]})
            else:
                cand = db.get(c, "candidates", cid)
                sc, why = M.score(cand, cl, [dict(r) for r in c.execute("select * from registry_postings where clinic_id=?", (str(clinic_id),))]) if cand else (0, [])
                db.insert(c, "matches", {"candidate_id": cid, "clinic_id": str(clinic_id), "posting_id": None, "score": sc, "reasons": why, "status": "sent", "cohort_id": cohort["id"], "created_at": S.iso(at)})
        created += 1
    for cid in cohort["candidate_ids"]:
        cand = db.get(c, "candidates", cid)
        if cand and cand["stage"] in S.STAGES and S.STAGES.index(cand["stage"]) < S.STAGES.index("profile_sent"):
            db.update(c, "candidates", cid, {"stage": "profile_sent", "stage_changed_at": S.iso(at)})
    db.update(c, "cohorts", cohort["id"], {"status": "sent", "sent_at": S.iso(at)})
    db.event(c, S.iso(at), "operator" if force else as_, "cohort_sent", {"cohort_id": cohort["id"]}, f"Cohort „{cohort['name']}“ an {created} Kliniken gesendet")
    return {"status": "sent", "threads_created": created}


def share_posting(c, conv, posting_id=None, clinic_id=None, as_="luna", at=None):
    """Compose 'this job could fit' in German with the real posting title/clinic/town and a link; same policy path as /send."""
    cand = candidate_of(c, conv)
    if not cand:
        raise ValueError("share_posting needs a candidate conversation")
    post = posting_row(c, posting_id) if posting_id else None
    if not post and clinic_id:
        post = next((dict(r) for r in c.execute("select * from registry_postings where clinic_id=? and role_class=? order by posting_id limit 1", (str(clinic_id), cand.get("role_class")))), None) \
            or next((dict(r) for r in c.execute("select * from registry_postings where clinic_id=? order by posting_id limit 1", (str(clinic_id),))), None)
    cl = clinic_row(c, (post or {}).get("clinic_id") or clinic_id)
    if not cl:
        raise ValueError("unknown posting/clinic")
    title = (post or {}).get("title") or "Pflegefachkraft (m/w/d)"
    url = (post or {}).get("url") or cl.get("careers_url") or f"/#/clinic/{cl['clinic_id']}"
    t = template_for(c, "whatsapp", conv.get("language") or "de", "share_posting")
    v = vars_for(c, cand, {"posting_title": title, "clinic_name": cl["name"], "clinic_town": cl.get("town") or "", "url": url})
    text = S.render(t["body"], v) if t else f"{v['first_name']}, diese Stelle könnte passen: {title} bei {cl['name']} in {cl.get('town')}. {url}"
    r = send(c, conv, text, as_, "low", t["id"] if t else None, reason=f"Stelle {title} an {cand['initials']}", at=at,
             meta={"posting_id": (post or {}).get("posting_id"), "clinic_id": cl["clinic_id"]})
    return dict(r, posting={"posting_id": (post or {}).get("posting_id"), "title": title, "clinic_id": cl["clinic_id"], "clinic_name": cl["name"], "town": cl.get("town"), "url": url})


# --- approvals -----------------------------------------------------------------------------------------------
def decide(c, appr, decision, text=None, remember=False, by="operator", at=None):
    """approve | reject | edit → apply the effect. Returns (approval, effect)."""
    at = at or now(c)
    ctx = appr.get("context") or {}
    effect = {"kind": appr["kind"], "decision": decision}
    status = {"approve": "approved", "reject": "rejected", "edit": "edited"}[decision]
    if decision == "edit" and text is not None:
        db.update(c, "approvals", appr["id"], {"draft": text})
    if decision in ("approve", "edit"):
        draft = text if (decision == "edit" and text is not None) else appr.get("draft")
        k = appr["kind"]
        if k in ("message",) or (k in ("match_send", "cohort_send") and ctx.get("message_id")):
            mid = ctx.get("message_id")
            if mid:
                if text is not None:
                    db.update(c, "messages", mid, {"text": text})
                m = db.get(c, "messages", mid)
                if m and m["status"] == "pending_approval":
                    if in_quiet_hours(db.policy(c), at):
                        db.update(c, "messages", mid, {"status": "queued"})
                        due = next_window(db.policy(c), at)
                        db.insert(c, "queue", {"due_at": S.iso(due), "kind": "follow_up_candidate", "target": {"message_id": mid, "conversation_id": m["conversation_id"]}, "reason": "Ruhezeit: Versand um 08:00", "status": "scheduled", "created_by": by})
                        effect["message"] = db.get(c, "messages", mid)
                    else:
                        effect["message"] = _deliver(c, mid, at, actor=by)
        elif k == "escalation":
            conv = conv_by_id(c, ctx.get("conversation_id")) if ctx.get("conversation_id") else None
            if conv and conv["mode"] != "stopped" and draft:
                effect["message"] = send(c, conv, draft, "operator", "high", reason="Eskalation beantwortet", at=at)
                mode_after = (appr.get("suggested") or {}).get("mode_after") or "luna"
                db.update(c, "conversations", conv["id"], {"mode": mode_after, "tags": [t for t in conv.get("tags") or [] if not t.startswith("escalation:")]})
                db.event(c, S.iso(at), by, "mode_changed", {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id")}, f"{conv['mode']} → {mode_after} (Eskalation entschieden)")
                effect["mode"] = mode_after
        elif k == "match_send":
            m = db.get(c, "matches", ctx.get("match_id")) if ctx.get("match_id") else None
            if m:
                effect.update(match_send(c, m, "operator", at, force=True))
        elif k == "cohort_send":
            coh = db.get(c, "cohorts", ctx.get("cohort_id")) if ctx.get("cohort_id") else None
            if coh:
                effect.update(cohort_send(c, coh, "operator", at, force=True))
        elif k == "throttle_exceeded":
            action = (appr.get("suggested") or {}).get("action")
            if action == "match_send" and ctx.get("match_id"):
                m = db.get(c, "matches", ctx["match_id"])
                if m:
                    effect.update(match_send(c, m, "operator", at, force=True))
            elif action == "cohort_send" and ctx.get("cohort_id"):
                coh = db.get(c, "cohorts", ctx["cohort_id"])
                if coh:
                    effect.update(cohort_send(c, coh, "operator", at, force=True))
        elif k == "stage_change":
            cid, stage = ctx.get("candidate_id"), (appr.get("suggested") or {}).get("stage") or ctx.get("stage")
            if cid and stage:
                effect["candidate"] = set_stage(c, db.get(c, "candidates", cid), stage, f"Freigabe #{appr['id']}", by, at)
        elif k == "campaign_change":
            camp = db.get(c, "campaigns", ctx.get("campaign_id")) if ctx.get("campaign_id") else None
            sug = appr.get("suggested") or {}
            if camp:
                effect["campaign"] = campaign_action(c, camp, sug.get("action", "pause"), sug.get("daily_budget"), by, at, force=True)
    else:
        if ctx.get("message_id"):
            db.update(c, "messages", ctx["message_id"], {"status": "failed", "meta": {"rejected_by": by}})
        if appr["kind"] in ("match_send", "throttle_exceeded") and ctx.get("match_id"):
            db.update(c, "matches", ctx["match_id"], {"status": "proposed"})
        if appr["kind"] in ("cohort_send", "throttle_exceeded") and ctx.get("cohort_id"):
            db.update(c, "cohorts", ctx["cohort_id"], {"status": "draft"})
    db.update(c, "approvals", appr["id"], {"status": status, "decided_at": S.iso(at), "decided_by": by, "remember": 1 if remember else 0})
    if remember:
        pol = db.policy(c)
        rules = [r for r in pol.get("remembered_rules") or [] if not (r["kind"] == appr["kind"] and r["risk"] == appr["risk"])]
        rules.append({"kind": appr["kind"], "risk": appr["risk"], "decision": "approve" if decision in ("approve", "edit") else "reject", "by": by, "at": S.iso(at)})
        db.set_policy(c, "remembered_rules", rules)
        db.event(c, S.iso(at), by, "policy_changed", {"approval_id": appr["id"]}, f"Regel gemerkt: {appr['kind']}/{appr['risk']} → {'auto' if decision != 'reject' else 'immer fragen'}")
        effect["remembered"] = rules[-1]
    db.event(c, S.iso(at), by, "approval_decided", dict(ctx, approval_id=appr["id"]), f"{appr['kind']} ({appr['risk']}): {status}")
    return db.get(c, "approvals", appr["id"]), effect


def set_stage(c, cand, stage, reason=None, by="operator", at=None):
    at = at or now(c)
    upd = {"stage": stage, "stage_changed_at": S.iso(at)}
    if stage == "lost":
        upd["lost_reason"] = reason or "manuell"
    db.update(c, "candidates", cand["id"], upd)
    conv_id = _cand_conv_id(c, cand["id"])
    state = {"qualified": "qualified", "matching": "matching", "profile_sent": "profile_sent", "interview_scheduling": "interview_prep", "interview_scheduled": "interview_prep",
             "interviewed": "awaiting_feedback", "offer": "awaiting_feedback", "placed": "placed", "lost": "lost", "dormant": "dormant", "docs_pending": "collect_docs",
             "qualifying": "collect_basics", "contacted": "consent", "new": "greeting"}.get(stage)
    if conv_id and state:
        db.update(c, "conversations", conv_id, {"state": state})
    db.event(c, S.iso(at), by, "stage_changed", {"candidate_id": cand["id"], "conversation_id": conv_id}, f"{cand['initials']}: {cand['stage']} → {stage}" + (f" ({reason})" if reason else ""))
    return db.get(c, "candidates", cand["id"])


def set_mode(c, conv, mode, reason=None, by="operator", at=None):
    at = at or now(c)
    upd = {"mode": mode, "pause_reason": reason if mode == "paused" else None}
    tags = [t for t in conv.get("tags") or [] if t not in ("PAUSIERT", "STOP")]
    if mode == "paused":
        tags.append("PAUSIERT")
    if mode == "stopped":
        tags.append("STOP")
        upd.update({"next_actor": "none", "sla_due_at": None, "closed_at": S.iso(at), "unread": 0})
        if conv.get("candidate_id"):
            db.update(c, "candidates", conv["candidate_id"], {"stage": "lost", "stage_changed_at": S.iso(at), "lost_reason": reason or "Opt-out (STOP)"})
        c.execute("update queue set status='cancelled', result='Konversation gestoppt' where status='scheduled' and target like ?", (f'%"conversation_id": {conv["id"]}%',))
    if mode == "luna" and conv["mode"] == "stopped":
        raise ValueError("a stopped conversation cannot be reactivated (opt-out)")
    upd["tags"] = tags
    db.update(c, "conversations", conv["id"], upd)
    db.event(c, S.iso(at), by, "mode_changed", {"conversation_id": conv["id"], "candidate_id": conv.get("candidate_id"), "clinic_thread_id": conv.get("clinic_thread_id")},
             f"{conv['mode']} → {mode}" + (f" ({reason})" if reason else ""))
    return db.get(c, "conversations", conv["id"])


def campaign_action(c, camp, action, daily_budget=None, by="operator", at=None, force=False):
    at = at or now(c)
    if action == "budget":
        new = float(daily_budget) if daily_budget is not None else float(camp["daily_budget"])
        old = float(camp["daily_budget"] or 0)
        if not force and old and new > old * 1.5:
            aid = db.insert(c, "approvals", {"kind": "campaign_change", "risk": "medium", "reason": f"Budget Kampagne #{camp['id']} von {old:.0f} € auf {new:.0f} € (+{(new / old - 1) * 100:.0f} %)",
                                             "context": {"campaign_id": camp["id"]}, "draft": None, "suggested": {"action": "budget", "daily_budget": new}, "status": "pending", "created_at": S.iso(at)})
            db.event(c, S.iso(at), by, "approval_created", {"campaign_id": camp["id"], "approval_id": aid}, f"Budgeterhöhung Kampagne #{camp['id']} wartet auf Freigabe")
            return dict(camp, approval_id=aid, pending_budget=new)
        db.update(c, "campaigns", camp["id"], {"daily_budget": new})
        db.event(c, S.iso(at), by, "campaign_budget", {"campaign_id": camp["id"]}, f"Kampagne #{camp['id']}: Budget {old:.0f} € → {new:.0f} €")
    elif action in ("pause", "resume"):
        st = "paused" if action == "pause" else "active"
        db.update(c, "campaigns", camp["id"], {"status": st})
        db.event(c, S.iso(at), by, f"campaign_{st}", {"campaign_id": camp["id"]}, f"Kampagne #{camp['id']} „{camp['name']}“ {'pausiert' if st == 'paused' else 'wieder aktiv'}")
    else:
        raise ValueError(f"unknown campaign action {action!r}")
    return db.get(c, "campaigns", camp["id"])


# --- queue -----------------------------------------------------------------------------------------------
def run_queue_row(c, q, at=None):
    """Execute one queue row now. Returns the result string."""
    at = at or now(c)
    tgt = q.get("target") or {}
    db.update(c, "queue", q["id"], {"status": "running", "attempt": (q.get("attempt") or 0) + 1})
    try:
        result = _run_queue_row(c, q, tgt, at)
        db.update(c, "queue", q["id"], {"status": "done", "result": result})
    except Exception as e:                                                       # noqa: BLE001 – a failed row must not stop the tick
        result = f"failed: {type(e).__name__}: {str(e)[:120]}"
        db.update(c, "queue", q["id"], {"status": "failed", "result": result})
    db.event(c, S.iso(at), "luna", "queue_run", dict(tgt, queue_id=q["id"]), f"{q['kind']}: {result}")
    return result


def _run_queue_row(c, q, tgt, at):
    pol = db.policy(c)
    if tgt.get("message_id"):                                                    # a message parked for the quiet hours
        m = db.get(c, "messages", tgt["message_id"])
        if m and m["status"] == "queued":
            _deliver(c, m["id"], at, actor=m["author"])
            return "zugestellt (Ruhezeit vorbei)"
        return "übersprungen: Nachricht nicht mehr in Warteschlange"
    kind = q["kind"]
    if kind in ("follow_up_candidate", "doc_reminder", "nurture"):
        conv = conv_by_id(c, tgt.get("conversation_id"))
        if not conv:
            return "übersprungen: Konversation fehlt"
        if conv["mode"] != "luna":
            return f"übersprungen: Modus {conv['mode']}"
        if conv["next_actor"] != "candidate":
            return "übersprungen: Kandidat hat bereits geantwortet"
        cand = candidate_of(c, conv)
        stage = {"follow_up_candidate": "follow_up", "doc_reminder": "doc_reminder", "nurture": "nurture"}[kind]
        t = template_for(c, "whatsapp", conv.get("language") or "de", stage)
        r = send(c, conv, S.render(t["body"], vars_for(c, cand)), "luna", "low", t["id"], reason=q.get("reason"), at=at)
        attempt = (q.get("attempt") or 0)
        cad = pol["cadence_candidate_hours"]
        if kind != "nurture" and attempt < len(cad):
            db.insert(c, "queue", {"due_at": S.iso(at + timedelta(hours=cad[min(attempt, len(cad) - 1)])), "kind": kind, "target": tgt, "reason": f"{cand['initials']}: Nachfassen #{attempt + 1}",
                                   "status": "scheduled", "attempt": attempt, "created_by": "luna"})
        elif kind != "nurture" and cand["stage"] not in ("lost", "placed", "dormant"):
            set_stage(c, cand, "dormant", "keine Antwort nach 3 Nachfassversuchen", "luna", at)
        return f"{r.get('status')}: {stage}"
    if kind == "follow_up_clinic":
        th = db.get(c, "clinic_threads", tgt.get("clinic_thread_id"))
        if not th:
            return "übersprungen: Thread fehlt"
        if th["state"] not in ("intro_sent", "awaiting_reply"):
            return f"übersprungen: Thread-Status {th['state']}"
        r = thread_action(c, th, "follow_up_now", {}, "luna", at)
        return f"{r.get('status')}: Nachfassen #{th['followup_attempt'] + 1}"
    if kind == "interview_reminder":
        iv = db.get(c, "interviews", tgt.get("interview_id"))
        if not iv or iv["status"] in ("cancelled", "done", "no_show"):
            return "übersprungen: Gespräch nicht mehr offen"
        th = db.get(c, "clinic_threads", iv["clinic_thread_id"])
        cand = db.get(c, "candidates", iv["candidate_id"])
        conv = db.row("conversations", c.execute("select * from conversations where candidate_id=?", (cand["id"],)).fetchone())
        t = template_for(c, "whatsapp", conv.get("language") or "de", "interview_reminder")
        r = send(c, conv, S.render(t["body"], vars_for(c, cand, {"clinic_name": th["clinic_name"], "slot": slot_label(iv["at"])})), "luna", "low", t["id"], reason=q.get("reason"), at=at)
        db.update(c, "interviews", iv["id"], {"status": "reminded"})
        return f"{r.get('status')}: Erinnerung"
    if kind == "feedback_request":
        th = db.get(c, "clinic_threads", tgt.get("clinic_thread_id"))
        r = thread_action(c, th, "request_feedback", {}, "luna", at)
        cand = db.get(c, "candidates", tgt.get("candidate_id")) if tgt.get("candidate_id") else None
        if cand:
            conv = db.row("conversations", c.execute("select * from conversations where candidate_id=?", (cand["id"],)).fetchone())
            t = template_for(c, "whatsapp", conv.get("language") or "de", "awaiting_feedback")
            if conv and conv["mode"] != "stopped":
                send(c, conv, S.render(t["body"], vars_for(c, cand, {"clinic_name": th["clinic_name"]})), "luna", "low", t["id"], reason=q.get("reason"), at=at)
                db.update(c, "conversations", conv["id"], {"state": "awaiting_feedback"})
        return f"{r.get('status')}: Feedback erbeten"
    if kind == "cohort_send":
        coh = db.get(c, "cohorts", tgt.get("cohort_id"))
        if not coh or coh["status"] not in ("draft", "pending_approval"):
            return f"übersprungen: Cohort-Status {(coh or {}).get('status')}"
        r = cohort_send(c, coh, "luna", at)
        return f"{r['status']}: {r.get('threads_created', 0)} Threads"
    if kind == "campaign_check":
        camp = db.get(c, "campaigns", tgt.get("campaign_id"))
        if not camp:
            return "übersprungen: Kampagne fehlt"
        cpl, spike = campaign_cpl(camp)
        if spike and camp["status"] == "active":
            if "campaign_cpl_spike" in (pol.get("auto_pause_on") or []):
                campaign_action(c, camp, "pause", None, "luna", at, force=True)
                return f"CPL-Spike ({cpl:.2f} €) → Kampagne pausiert"
            return f"CPL-Spike ({cpl:.2f} €) – Pause vorgeschlagen"
        return f"ok: CPL {cpl:.2f} €"
    return f"unbekannte Art {kind}"


def campaign_cpl(camp):
    leads = camp.get("leads") or 0
    cpl = (camp.get("spend_total") or 0) / leads if leads else 0.0
    adsets = camp.get("adsets") or []
    worst = max((a.get("cpl") or 0) for a in adsets) if adsets else cpl
    spike = bool(cpl and worst > 1.8 * cpl) or bool(camp.get("cpl_7d") and camp["cpl_7d"] > 1.8 * cpl)
    return cpl, spike


# --- tick -----------------------------------------------------------------------------------------------
def tick(minutes=60, c=None):
    own = c is None
    c = c or db.db()
    try:
        r = _tick(c, int(minutes or 60))
        if own:
            c.commit()
        return r
    finally:
        if own:
            c.close()


def _tick(c, minutes):
    pol = db.policy(c)
    t0 = S.parse(pol["sim_now"])
    t1 = t0 + timedelta(minutes=minutes)
    rng = random.Random(f"{S.iso(t0)}|{minutes}")
    first_event = c.execute("select coalesce(max(id),0) from events").fetchone()[0]
    summary = {"messages_out": 0, "messages_in": 0, "approvals_created": 0, "queue_run": 0, "leads_new": 0, "paused": 0}
    db.set_policy(c, "sim_now", S.iso(t1))
    db.event(c, S.iso(t1), "system", "tick", {"minutes": minutes}, f"Uhr: {S.iso(t0)} → {S.iso(t1)}")
    if t0.date() != t1.date():
        c.execute("update accounts set used_today=0 where kind in ('whatsapp','mailbox')")
    # 1. due queue rows
    for q in db.rows("queue", c.execute("select * from queue where status='scheduled' and due_at<=? order by due_at, id", (S.iso(t1),))):
        run_queue_row(c, q, max(S.parse(q["due_at"]), t0))
        summary["queue_run"] += 1
    # 2. simulated inbound for ~25 % of waiting threads
    share = 0.25 * min(1.0, minutes / 60.0)
    waiting = db.rows("conversations", c.execute("select * from conversations where next_actor in ('candidate','clinic') and mode<>'stopped' and closed_at is null order by id"))
    for conv in waiting:
        if rng.random() >= share:
            continue
        at = t0 + timedelta(minutes=rng.randint(1, max(1, minutes - 1)))
        if conv["kind"] == "candidate":
            cand = candidate_of(c, conv)
            if not cand:
                continue
            corpus = CAND_REPLIES.get(conv["state"])
            if not corpus:
                continue
            line = rng.choice(corpus)
            if rng.random() < 0.03:
                line = "STOP"
            elif rng.random() < 0.03:
                line = "Kann ich bitte mit einem Menschen sprechen?"
            text, att = line, []
            if "|" in line:
                text, kinds = line.split("|", 1)
                att = [{"kind": k, "name": {"cv": "Lebenslauf.pdf", "education_cert": "Zeugnis.jpg"}.get(k, k + ".pdf"), "size_kb": rng.randint(90, 1800)} for k in kinds.split(",")]
            text = text.format(quali=cand.get("qualification"), years=S.years_label(cand.get("experience_years")), dept=(cand.get("departments") or ["Station"])[0], plz=cand.get("plz") or "",
                               city=cand.get("city") or "", radius=cand.get("radius_km"))
            r = _apply_inbound(c, conv, text, att, at)
            summary["messages_in"] += 1
            if r.get("approval_id"):
                summary["approvals_created"] += 1
            conv2 = conv_by_id(c, conv["id"])
            if conv2["mode"] == "luna" and not r.get("stopped"):                  # Luna answers what came in during the tick
                act = _next_action(c, conv2)
                if act.get("text") and act["action"] not in ("none",):
                    kind = act.get("kind", "message")
                    if act["action"] == "advance_stage":
                        set_stage(c, cand, "qualified", "Unterlagen vollständig", "luna", at)
                        conv2 = conv_by_id(c, conv["id"])
                    out = send(c, conv2, act["text"], "luna", act["risk"], act.get("template_id"), kind="message" if kind == "stage_change" else kind, reason=act["label"], at=at + timedelta(minutes=1))
                    if out.get("status") == "sent":
                        summary["messages_out"] += 1
                    elif out.get("status") == "pending_approval":
                        summary["approvals_created"] += 1
        else:
            th = thread_of(c, conv)
            if not th:
                continue
            corpus = CLINIC_REPLIES.get(th["state"])
            if not corpus:
                continue
            line = rng.choice(corpus)
            if not line:
                continue
            counter = slot_label(business_slots(t1 + timedelta(days=2), 1, rng)[0]["at"])
            text = line.format(counter=counter)
            _apply_inbound(c, conv, text, [], at)
            summary["messages_in"] += 1
            th2 = db.get(c, "clinic_threads", th["id"])
            if th2["state"] == "interested" and conv["mode"] == "luna":           # Luna proposes slots right away (medium risk → policy)
                out = thread_action(c, th2, "propose_slots", {}, "luna", at + timedelta(minutes=2))
                if out.get("status") == "sent":
                    summary["messages_out"] += 1
                elif out.get("status") == "pending_approval":
                    summary["approvals_created"] += 1
    # 3. follow-ups per cadence for threads that fell silent (no queue row yet)
    cad = pol["cadence_candidate_hours"]
    for conv in db.rows("conversations", c.execute("select * from conversations where kind='candidate' and next_actor='candidate' and mode='luna' and closed_at is null and last_message_at<=?",
                                                    (S.iso(t1 - timedelta(hours=cad[0])),))):
        has = c.execute("select 1 from queue where status='scheduled' and kind in ('follow_up_candidate','doc_reminder','nurture') and target like ?", (f'%"conversation_id": {conv["id"]}%',)).fetchone()
        if has or c.execute("select count(*) from queue where kind in ('follow_up_candidate','doc_reminder') and status='done' and target like ?", (f'%"conversation_id": {conv["id"]}%',)).fetchone()[0] >= len(cad):
            continue
        cand = candidate_of(c, conv)
        kind = "doc_reminder" if conv["state"] in ("collect_docs", "docs_review") else "follow_up_candidate"
        db.insert(c, "queue", {"due_at": S.iso(t1 + timedelta(minutes=rng.randint(5, 240))), "kind": kind, "target": {"conversation_id": conv["id"], "candidate_id": conv["candidate_id"]},
                               "reason": f"{cand['initials'] if cand else conv['id']}: keine Antwort seit {cad[0]} h", "status": "scheduled", "created_by": "luna"})
    # 4. campaigns spend, new leads
    for camp in db.rows("campaigns", c.execute("select * from campaigns where status in ('active','learning')")):
        frac = minutes / 1440.0
        spend = round((camp["daily_budget"] or 0) * frac * rng.uniform(0.8, 1.1), 2)
        cpl, spike = campaign_cpl(camp)
        imps = int(spend * rng.randint(90, 160))
        clicks = int(imps * rng.uniform(0.012, 0.03))
        expected = spend / (cpl or 15.0)
        new_leads = int(expected) + (1 if rng.random() < (expected - int(expected)) else 0)
        db.update(c, "campaigns", camp["id"], {"spend_total": round((camp["spend_total"] or 0) + spend, 2), "spend_7d": round((camp["spend_7d"] or 0) + spend, 2),
                                               "impressions": (camp["impressions"] or 0) + imps, "clicks": (camp["clicks"] or 0) + clicks, "leads": (camp["leads"] or 0) + new_leads})
        for _ in range(new_leads):
            at = t0 + timedelta(minutes=rng.randint(1, max(1, minutes - 1)))
            new_lead(c, camp, rng, at, pol)
            summary["leads_new"] += 1
            summary["messages_in"] += 1
        if spike and camp["status"] == "active" and "campaign_cpl_spike" in (pol.get("auto_pause_on") or []):
            campaign_action(c, camp, "pause", None, "luna", t1, force=True)
            db.event(c, S.iso(t1), "system", "alert", {"campaign_id": camp["id"]}, f"CPL-Spike Kampagne #{camp['id']}: Ø {cpl:.2f} € – automatisch pausiert (Policy)")
            summary["paused"] += 1
    # 5. WhatsApp account health: reset daily counters at midnight; a red number pauses its threads
    for acc in db.rows("accounts", c.execute("select * from accounts where kind='whatsapp'")):
        if acc["status"] == "rate_limited" and t0.date() != t1.date():
            db.update(c, "accounts", acc["id"], {"status": "connected", "last_error": None})
        if acc["quality"] == "yellow" and acc["status"] == "rate_limited" and rng.random() < 0.15 * min(1.0, minutes / 60.0):
            db.update(c, "accounts", acc["id"], {"quality": "red", "last_error": "Qualitätsbewertung ROT (Meta): Nutzerblockierungen gestiegen"})
            db.event(c, S.iso(t1), "system", "alert", {"account_id": acc["id"]}, f"WhatsApp „{acc['name']}“: Qualität rot")
            acc["quality"] = "red"
        if acc["quality"] == "red" and "wa_quality_red" in (pol.get("auto_pause_on") or []):
            n = 0
            for conv in db.rows("conversations", c.execute("select * from conversations where account_id=? and mode='luna'", (acc["id"],))):
                set_mode(c, conv, "paused", f"WhatsApp-Nummer „{acc['name']}“ Qualität rot", "system", t1)
                n += 1
            if n:
                summary["paused"] += n
                db.event(c, S.iso(t1), "system", "auto_pause", {"account_id": acc["id"]}, f"{n} Konversationen pausiert (Nummer {acc['name']} rot)")
    events = db.rows("events", c.execute("select * from events where id>? order by id", (first_event,)))
    summary["approvals_created"] = max(summary["approvals_created"], sum(1 for e in events if e["kind"] == "approval_created"))
    summary["messages_out"] = max(summary["messages_out"], sum(1 for e in events if e["kind"] in ("message_sent", "email_sent")))
    return {"sim_now": S.iso(t1), "events": events, "summary": summary}


def new_lead(c, camp, rng, at, pol=None):
    """A brand-new candidate from a campaign: candidate + WhatsApp conversation + opener + Luna's greeting (policy)."""
    origin = rng.choice(S.ORIGIN_POOL)
    fn, ln = rng.choice(S.NAMES[origin][0]), rng.choice(S.NAMES[origin][1])
    name = f"{fn} {ln}"
    lang = "ro" if origin == "Rumänien" and rng.random() < 0.4 else "en" if origin in ("Indien", "Philippinen", "Vietnam") and rng.random() < 0.45 else "de"
    adset = rng.choice(camp.get("adsets") or [{"city": "München"}])
    cl = c.execute("select town, regierungsbezirk from registry_clinics where town=? limit 1", (adset.get("city"),)).fetchone()
    region = cl[1] if cl else "Oberbayern"
    lp = c.execute("select id from landing_pages where campaign_ids like ? limit 1", (f"%{camp['id']}%",)).fetchone()
    role = rng.choice(S.ROLE_POOL)
    cand = {"name": name, "initials": S.initials(name), "phone": f"+49 {rng.choice(['151', '152', '157', '160', '162', '170', '176', '177'])} {rng.randint(1000000, 9999999)}",
            "email": re.sub(r"[^a-z0-9.@]", "", f"{fn.split()[0].lower()}.{ln.split()[-1].lower()}{rng.randint(1, 99)}@example.org"), "language": lang,
            "german_level": "C2" if origin == "Deutschland" else rng.choice(["A2", "B1", "B2"]), "origin_country": origin, "city": adset.get("city"), "plz": None, "region": region,
            "radius_km": 30, "role_class": role, "departments": rng.sample(S.DEPARTMENTS, 1), "qualification": rng.choice(S.QUALI[role]), "experience_years": rng.randint(0, 12),
            "anerkennung_status": "not_needed" if origin == "Deutschland" else rng.choice(["none", "applied"]), "work_permit": "EU" if origin in ("Deutschland", "Rumänien") else "keine",
            "employment_type": "Vollzeit", "shifts": ["Wechsel"], "start_from": (at + timedelta(days=60)).strftime("%d.%m.%Y"), "source_campaign_id": camp["id"],
            "landing_page_id": lp[0] if lp else None, "stage": "new", "stage_changed_at": S.iso(at), "owner": "Luna", "tags": [lang.upper()] if lang != "de" else [], "created_at": S.iso(at)}
    cid = db.insert(c, "candidates", cand)
    for kind in S.DOC_KINDS:
        db.insert(c, "documents", {"candidate_id": cid, "kind": kind, "status": "missing"})
    acc = c.execute("select id from accounts where kind='whatsapp' and status='connected' and routing like ? order by id limit 1", (f'%"{lang}"%',)).fetchone() \
        or c.execute("select id from accounts where kind='whatsapp' and status='connected' order by id limit 1").fetchone()
    conv_id = db.insert(c, "conversations", {"kind": "candidate", "candidate_id": cid, "channel": "whatsapp", "account_id": acc[0] if acc else None, "mode": "luna", "state": "greeting",
                                             "next_actor": "us", "language": lang, "tags": list(cand["tags"]) + [f"ref:{S.CAMPAIGN_SLUGS[(camp['id'] - 1) % len(S.CAMPAIGN_SLUGS)]}"], "opened_at": S.iso(at)})
    opener = rng.choice(S.OPENERS[lang]).format(quali=cand["qualification"], years=S.years_label(cand["experience_years"], lang=lang),
                                                 years_dat=S.years_label(cand["experience_years"], lang=lang, dative=True), start=cand["start_from"], origin=origin, dept=cand["departments"][0])
    conv = db.get(c, "conversations", conv_id)
    db.event(c, S.iso(at), "meta", "lead_created", {"candidate_id": cid, "conversation_id": conv_id, "campaign_id": camp["id"]}, f"Neuer Lead {cand['initials']} über Kampagne #{camp['id']} (ref={S.CAMPAIGN_SLUGS[(camp['id'] - 1) % 6]})")
    _apply_inbound(c, conv, opener, [], at)
    conv = db.get(c, "conversations", conv_id)
    act = _next_action(c, conv)
    if act.get("text"):
        r = send(c, conv, act["text"], "luna", act["risk"], act.get("template_id"), reason=act["label"], at=at + timedelta(minutes=1))
        if r.get("status") == "sent":
            db.update(c, "conversations", conv_id, {"state": "consent"})
            db.update(c, "candidates", cid, {"stage": "contacted", "stage_changed_at": S.iso(at)})
    return cid
