"""FastAPI router for /api/autopilot (contract: docs/autopilot.md, section "API contract").

Plain functions, Request.query_params, {"error": ...} via HTTPException (app/main.py formats it). The SQLite file is
initialised lazily on the first request and auto-seeded when there are no candidates, so the console never opens empty.
"""
import json
import statistics
from contextlib import contextmanager
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request

from . import db
from . import engine as E
from . import matching as M
from . import seed as S

router = APIRouter()
_ready = {"ok": False}
VIEWS = ("needs_reply", "manager", "paused", "luna", "clinics", "overdue", "waiting_candidate", "waiting_clinic")
MODES = ("luna", "paused", "human", "manager", "stopped")


def ensure():
    if _ready["ok"] and db.SQLITE_PATH.exists():
        return
    S.init()
    with db._lock, db.db() as c:
        n = c.execute("select count(*) from candidates").fetchone()[0]
    if n == 0:
        S.seed(reset=True)
    _ready["ok"] = True


@contextmanager
def conn():
    ensure()
    with db._lock:
        c = db.db()
        try:
            yield c
            c.commit()
        except ValueError as e:
            c.rollback()
            raise HTTPException(400, str(e))
        finally:
            c.close()


async def body(request):
    try:
        d = await request.json()
    except Exception:                                                          # empty body
        return {}
    return d if isinstance(d, dict) else {}


def _page(rows, q):
    limit = max(1, min(int(q.get("limit") or 100), 2000))
    offset = max(0, int(q.get("offset") or 0))
    return {"total": len(rows), "limit": limit, "offset": offset, "rows": rows[offset:offset + limit]}


def _like(key, val):
    """SQL LIKE patterns that hit a JSON target column for key=val (json.dumps spacing)."""
    return (f'%"{key}": {val},%', f'%"{key}": {val}}}%')


def _events_for(c, keys, limit=60):
    where, params = [], []
    for k, v in keys:
        if v is None:
            continue
        a, b = _like(k, v)
        where.append("(target like ? or target like ?)")
        params += [a, b]
    if not where:
        return []
    ev = db.rows("events", c.execute(f"select * from events where {' or '.join(where)} order by at desc, id desc limit {int(limit)}", params))
    return [{"id": e["id"], "at": e["at"], "actor": e["actor"], "kind": e["kind"], "detail": e["detail"], "target": e["target"]} for e in ev]


def _avatar(i):
    return S.AVATAR_COLORS[int(i or 0) % len(S.AVATAR_COLORS)]


# --- conversations -----------------------------------------------------------------------------------------
def _conv_rows(c, now):
    cands = {r["id"]: r for r in db.rows("candidates", c.execute("select * from candidates"))}
    threads = {r["id"]: r for r in db.rows("clinic_threads", c.execute("select * from clinic_threads"))}
    out = []
    for cv in db.rows("conversations", c.execute("select * from conversations")):
        cand = cands.get(cv.get("candidate_id"))
        th = threads.get(cv.get("clinic_thread_id"))
        overdue = bool(cv.get("sla_due_at") and cv["next_actor"] == "us" and cv["mode"] != "stopped" and S.parse(cv["sla_due_at"]) < now)
        if cand:
            name, ini, phone, email, stage, camp, clinic_id = cand["name"], cand["initials"], S.phone_masked(cand["phone"]), cand["email"], cand["stage"], cand["source_campaign_id"], None
        elif th:
            name, ini, phone, email, stage, camp, clinic_id = th["clinic_name"], S.initials(th["clinic_name"] or "K"), None, th["contact_email"], th["state"], None, th["clinic_id"]
        else:
            name, ini, phone, email, stage, camp, clinic_id = f"#{cv['id']}", "?", None, None, None, None, None
        out.append({"id": cv["id"], "kind": cv["kind"], "channel": cv["channel"], "name": name, "initials": ini, "avatar_color": _avatar(cv.get("candidate_id") or cv["id"]),
                    "phone_masked": phone, "email": email, "last_preview": cv.get("last_preview"), "last_at": cv.get("last_message_at"), "unread": cv.get("unread") or 0,
                    "mode": cv["mode"], "state": cv["state"], "next_actor": cv.get("next_actor"), "sla_due_at": cv.get("sla_due_at"), "overdue": overdue, "stage": stage,
                    "tags": cv.get("tags") or [], "language": cv.get("language"), "campaign_id": camp, "clinic_id": clinic_id, "candidate_id": cv.get("candidate_id"),
                    "clinic_thread_id": cv.get("clinic_thread_id"), "pause_reason": cv.get("pause_reason"), "owner": cand["owner"] if cand else None,
                    "account_id": cv.get("account_id"), "closed": bool(cv.get("closed_at"))})
    return out


def _view_pred(view):
    return {"needs_reply": lambda r: r["next_actor"] == "us" and r["mode"] != "stopped",
            "manager": lambda r: r["mode"] == "manager",
            "paused": lambda r: r["mode"] == "paused",
            "luna": lambda r: r["mode"] == "luna" and not r["closed"],
            "clinics": lambda r: r["kind"] == "clinic",
            "overdue": lambda r: r["overdue"],
            "waiting_candidate": lambda r: r["next_actor"] == "candidate" and r["mode"] != "stopped",
            "waiting_clinic": lambda r: r["next_actor"] == "clinic" and r["mode"] != "stopped"}.get(view)


def _filter_convs(rows, q):
    view = q.get("view")
    if view:
        if view not in VIEWS:
            raise HTTPException(400, f"view must be one of {VIEWS}")
        rows = [r for r in rows if _view_pred(view)(r)]
    for key in ("kind", "channel", "mode", "next_actor", "state", "stage", "language", "owner"):
        v = q.get("lang" if key == "language" else key)
        if v:
            vals = set(v.split(","))
            rows = [r for r in rows if r.get(key) in vals]
    if q.get("campaign_id"):
        rows = [r for r in rows if str(r.get("campaign_id")) == str(q["campaign_id"])]
    if q.get("q"):
        s = q["q"].lower()
        rows = [r for r in rows if s in (r["name"] or "").lower() or s in (r.get("phone_masked") or "").lower() or s in (r.get("last_preview") or "").lower()
                or s in (r.get("email") or "").lower() or any(s in t.lower() for t in r["tags"])]
    sort = q.get("sort") or "-last_at"
    key = sort.lstrip("-")
    rows.sort(key=lambda r: (r.get(key) is None, r.get(key) or ""), reverse=sort.startswith("-"))
    return rows


@router.get("/overview")
def overview():
    with conn() as c:
        pol = db.policy(c)
        now = S.parse(pol["sim_now"])
        rows = _conv_rows(c, now)
        counts = {v: sum(1 for r in rows if _view_pred(v)(r)) for v in ("needs_reply", "waiting_candidate", "waiting_clinic", "manager", "paused", "overdue")}
        counts["luna_active"] = sum(1 for r in rows if _view_pred("luna")(r))
        counts["approvals_pending"] = c.execute("select count(*) from approvals where status='pending'").fetchone()[0]
        counts["queue_due"] = c.execute("select count(*) from queue where status='scheduled' and due_at<=?", (S.iso(now + timedelta(hours=24)),)).fetchone()[0]
        counts["stopped"] = sum(1 for r in rows if r["mode"] == "stopped")
        counts["human"] = sum(1 for r in rows if r["mode"] == "human")
        by = {r["stage"]: r["n"] for r in c.execute("select stage, count(*) n from candidates group by stage")}
        funnel = [{"stage": s, "count": by.get(s, 0)} for s in S.STAGES + ["lost", "dormant"]]
        accounts = [{"id": a["id"], "kind": a["kind"], "name": a["name"], "status": a["status"], "quality": a["quality"], "used_today": a["used_today"], "daily_cap": a["daily_cap"],
                     "last_error": a["last_error"]} for a in db.rows("accounts", c.execute("select * from accounts order by kind desc, id"))]
        alerts = _alerts(c, counts, accounts)
        recent = db.rows("events", c.execute("select * from events order by at desc, id desc limit 30"))
        return {"sim_now": pol["sim_now"], "mode": pol["mode"], "counts": counts, "funnel": funnel, "accounts": accounts, "alerts": alerts, "recent_events": recent,
                "totals": db.counts(c)}


def _alerts(c, counts, accounts):
    out = []
    for camp in db.rows("campaigns", c.execute("select * from campaigns")):
        cpl, spike = E.campaign_cpl(camp)
        if spike and camp["status"] == "active":
            out.append({"level": "warn", "text": f"CPL-Spike: „{camp['name']}“ Ø {cpl:.2f} € – Pause vorgeschlagen", "link": f"#/campaigns/{camp['id']}", "campaign_id": camp["id"]})
    for a in accounts:
        if a["kind"] == "whatsapp" and (a["quality"] == "red" or a["status"] == "rate_limited"):
            out.append({"level": "warn" if a["quality"] != "red" else "error", "text": f"WhatsApp „{a['name']}“: {a['last_error'] or 'Qualität ' + a['quality']}", "link": "#/accounts"})
        if a["kind"] == "mailbox" and a["status"] == "degraded":
            out.append({"level": "warn", "text": f"Mailbox „{a['name']}“: {a['last_error']}", "link": "#/accounts"})
        if a["kind"] == "api" and a["status"] == "disconnected":
            out.append({"level": "info", "text": f"{a['name']}: {a['last_error']}", "link": "#/accounts"})
    for lp in db.rows("landing_pages", c.execute("select * from landing_pages")):
        for v in lp["variants"]:
            if v["visits"] and v["leads"] / v["visits"] < 0.06:
                out.append({"level": "info", "text": f"Landingpage {lp['slug']} Variante {v['key']}: Conversion {v['leads'] / v['visits'] * 100:.1f} %", "link": f"#/campaigns/lp/{lp['id']}"})
    if counts.get("overdue"):
        out.append({"level": "warn", "text": f"{counts['overdue']} Konversationen über SLA", "link": "#/inbox?view=overdue"})
    hi = c.execute("select count(*) from approvals where status='pending' and risk='high'").fetchone()[0]
    if hi:
        out.append({"level": "error", "text": f"{hi} Eskalationen warten auf Manager-Entscheidung", "link": "#/approvals?risk=high"})
    return out


@router.get("/conversations")
def conversations(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        now = S.sim_now(c)
        return _page(_filter_convs(_conv_rows(c, now), q), q)


@router.post("/conversations/bulk")
async def conversations_bulk(request: Request):
    b = await body(request)
    mode = b.get("mode")
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    with conn() as c:
        ids = b.get("ids")
        if not ids:
            rows = _filter_convs(_conv_rows(c, S.sim_now(c)), b.get("filter") or {})
            ids = [r["id"] for r in rows]
        n = 0
        for i in ids:
            cv = db.get(c, "conversations", int(i))
            if cv and cv["mode"] != mode and cv["mode"] != "stopped":
                E.set_mode(c, cv, mode, b.get("reason"), b.get("by") or "operator")
                n += 1
        return {"updated": n, "mode": mode}


def _conversation_detail(c, cv):
    now = S.sim_now(c)
    msgs = db.rows("messages", c.execute("select * from messages where conversation_id=? order by at, id", (cv["id"],)))
    cand = E.candidate_of(c, cv)
    th = E.thread_of(c, cv)
    out = {"conversation": dict(cv, overdue=bool(cv.get("sla_due_at") and cv["next_actor"] == "us" and cv["mode"] != "stopped" and S.parse(cv["sla_due_at"]) < now)), "messages": msgs}
    if cand:
        docs = E.documents_of(c, cand["id"])
        out["candidate"] = dict(cand, phone_masked=S.phone_masked(cand["phone"]), documents=docs, avatar_color=_avatar(cand["id"]))
        out["checklist"] = E.checklist(cand, docs)
        out["matches"] = [_match_row(c, m) for m in db.rows("matches", c.execute("select * from matches where candidate_id=? order by score desc limit 5", (cand["id"],)))] \
            or [dict(m, candidate_id=cand["id"], status="suggested") for m in E.top_matches(c, cand, 5)]
        out["timeline"] = _events_for(c, [("conversation_id", cv["id"]), ("candidate_id", cand["id"])])
    else:
        out["candidate"] = None
        out["checklist"] = []
        out["matches"] = []
        out["timeline"] = _events_for(c, [("conversation_id", cv["id"]), ("clinic_thread_id", cv.get("clinic_thread_id"))])
    if th:
        out["clinic_thread"] = dict(th, clinic=E.clinic_row(c, th["clinic_id"]), candidates=[_cand_brief(db.get(c, "candidates", i)) for i in th["candidate_ids"]],
                                    interviews=db.rows("interviews", c.execute("select * from interviews where clinic_thread_id=? order by at", (th["id"],))))
    else:
        out["clinic_thread"] = None
    out["suggestion"] = E.next_action(cv, c)
    out["approvals"] = db.rows("approvals", c.execute("select * from approvals where status='pending' and (context like ? or context like ?) order by id desc", _like("conversation_id", cv["id"])))
    out["queue"] = db.rows("queue", c.execute("select * from queue where status='scheduled' and (target like ? or target like ?) order by due_at", _like("conversation_id", cv["id"])))
    return out


def _cand_brief(cand):
    if not cand:
        return None
    return {"id": cand["id"], "initials": cand["initials"], "name": cand["name"], "role_class": cand["role_class"], "german_level": cand["german_level"], "stage": cand["stage"],
            "city": cand["city"], "region": cand["region"], "anerkennung_status": cand["anerkennung_status"], "avatar_color": _avatar(cand["id"])}


@router.get("/conversations/{cid}")
def conversation(cid: int):
    with conn() as c:
        cv = db.get(c, "conversations", cid)
        if not cv:
            raise HTTPException(404, "conversation not found")
        return _conversation_detail(c, cv)


@router.post("/conversations/{cid}/mode")
async def conversation_mode(cid: int, request: Request):
    b = await body(request)
    if b.get("mode") not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    with conn() as c:
        cv = db.get(c, "conversations", cid)
        if not cv:
            raise HTTPException(404, "conversation not found")
        E.set_mode(c, cv, b["mode"], b.get("reason"), b.get("by") or "operator")
        return _conversation_detail(c, db.get(c, "conversations", cid))["conversation"]


@router.post("/conversations/{cid}/send")
async def conversation_send(cid: int, request: Request):
    b = await body(request)
    text = (b.get("text") or "").strip()
    with conn() as c:
        cv = db.get(c, "conversations", cid)
        if not cv:
            raise HTTPException(404, "conversation not found")
        as_ = b.get("as") or "operator"
        if as_ not in ("operator", "luna"):
            raise HTTPException(400, "as must be operator or luna")
        risk, kind, tid = b.get("risk"), "message", b.get("template_id")
        if not text:                                                             # no text: send Luna's suggestion
            sug = E.next_action(cv, c)
            text, risk, tid, kind = sug.get("text") or "", sug.get("risk"), sug.get("template_id"), sug.get("kind", "message")
            if not text:
                raise HTTPException(400, "text is empty and Luna has no suggestion")
        elif tid:
            t = db.get(c, "templates", tid)
            if t:
                cand = E.candidate_of(c, cv)
                text = S.render(text, E.vars_for(c, cand)) if cand else text
        if not risk:
            sug = E.next_action(cv, c)
            risk = sug.get("risk") if as_ == "luna" else "low"
            kind = sug.get("kind", "message") if as_ == "luna" else "message"
        if E.detect_trigger(text, db.policy(c)) and as_ == "luna":
            risk = "high"
        return E.send(c, cv, text, as_, risk, tid, kind=kind, reason=b.get("reason"))


@router.post("/conversations/{cid}/action")
async def conversation_action(cid: int, request: Request):
    b = await body(request)
    action, params = b.get("action"), b.get("params") or {k: v for k, v in b.items() if k != "action"}
    with conn() as c:
        cv = db.get(c, "conversations", cid)
        if not cv:
            raise HTTPException(404, "conversation not found")
        cand = E.candidate_of(c, cv)
        now = S.sim_now(c)
        if action == "suggest":
            return {"ok": True, "result": E.next_action(cv, c)}
        if action == "mark_read":
            db.update(c, "conversations", cid, {"unread": 0})
            return {"ok": True, "result": db.get(c, "conversations", cid)}
        if action == "snooze":
            minutes = int(params.get("minutes") or 240)
            db.update(c, "conversations", cid, {"sla_due_at": S.iso(now + timedelta(minutes=minutes))})
            db.event(c, S.iso(now), "operator", "snoozed", {"conversation_id": cid, "candidate_id": cv.get("candidate_id")}, f"SLA +{minutes} min")
            return {"ok": True, "result": db.get(c, "conversations", cid)}
        if action == "request_doc":
            if not cand:
                raise HTTPException(400, "not a candidate conversation")
            kinds = params.get("kinds") or ([params["kind"]] if params.get("kind") else None)
            docs = E.documents_of(c, cand["id"])
            if not kinds:
                kinds = [d["kind"] for d in docs if d["status"] in ("missing", "requested") and d["kind"] in ("cv", "education_cert", "language_cert", "anerkennung")]
            for d in docs:
                if d["kind"] in kinds and d["status"] == "missing":
                    db.update(c, "documents", d["id"], {"status": "requested", "note": "angefordert"})
            t = E.template_for(c, "whatsapp", cv.get("language") or "de", "doc_reminder")
            r = E.send(c, cv, S.render(t["body"], E.vars_for(c, cand)), params.get("as") or "luna", "low", t["id"], reason="Unterlagen anfordern")
            return {"ok": True, "result": r}
        if action == "propose_matches":
            if not cand:
                raise HTTPException(400, "not a candidate conversation")
            top = E.top_matches(c, cand, int(params.get("n") or 3))
            created = []
            for m in top:
                ex = c.execute("select id from matches where candidate_id=? and clinic_id=?", (cand["id"], m["clinic_id"])).fetchone()
                if not ex:
                    mid = db.insert(c, "matches", {"candidate_id": cand["id"], "clinic_id": m["clinic_id"], "posting_id": m["posting_id"], "score": m["score"], "reasons": m["reasons"], "status": "proposed", "created_at": S.iso(now)})
                    created.append(mid)
            if cand["stage"] in ("qualified",):
                E.set_stage(c, cand, "matching", "Matches vorgeschlagen", "luna", now)
            db.event(c, S.iso(now), "luna", "matches_proposed", {"candidate_id": cand["id"], "conversation_id": cid}, f"{len(top)} Kliniken für {cand['initials']}")
            return {"ok": True, "result": {"matches": [_match_row(c, m) for m in db.rows("matches", c.execute("select * from matches where candidate_id=? order by score desc", (cand["id"],)))], "created": created}}
        if action == "follow_up_now":
            if cv["kind"] == "clinic":
                th = E.thread_of(c, cv)
                return {"ok": True, "result": E.thread_action(c, th, "follow_up_now", {}, params.get("as") or "luna")}
            t = E.template_for(c, "whatsapp", cv.get("language") or "de", "follow_up")
            return {"ok": True, "result": E.send(c, cv, S.render(t["body"], E.vars_for(c, cand)), params.get("as") or "luna", "low", t["id"], reason="Nachfassen (manuell)")}
        if action == "share_posting":
            return {"ok": True, "result": E.share_posting(c, cv, params.get("posting_id"), params.get("clinic_id"), params.get("as") or "luna")}
        raise HTTPException(400, "action must be one of suggest|request_doc|propose_matches|follow_up_now|snooze|mark_read|share_posting")


# --- candidates ----------------------------------------------------------------------------------------------
@router.get("/candidates")
def candidates(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        rows = db.rows("candidates", c.execute("select * from candidates order by id"))
        docs = {}
        for d in c.execute("select candidate_id, kind, status from documents"):
            docs.setdefault(d["candidate_id"], []).append({"kind": d["kind"], "status": d["status"]})
        nmatch = {r[0]: r[1] for r in c.execute("select candidate_id, count(*) from matches group by candidate_id")}
        convs = {r[0]: r[1] for r in c.execute("select candidate_id, id from conversations where kind='candidate'")}
        out = []
        for cand in rows:
            if q.get("stage") and cand["stage"] not in q["stage"].split(","):
                continue
            if q.get("region") and cand["region"] != q["region"]:
                continue
            if q.get("role_class") and cand["role_class"] != q["role_class"]:
                continue
            if q.get("language") and cand["language"] != q["language"]:
                continue
            if q.get("anerkennung") and cand["anerkennung_status"] != q["anerkennung"]:
                continue
            if q.get("campaign_id") and str(cand["source_campaign_id"]) != str(q["campaign_id"]):
                continue
            if q.get("q"):
                s = q["q"].lower()
                if not any(s in str(cand.get(k) or "").lower() for k in ("name", "city", "qualification", "phone", "email", "origin_country", "initials")):
                    continue
            ck = E.checklist(cand, docs.get(cand["id"], []))
            out.append(dict(cand, phone_masked=S.phone_masked(cand["phone"]), avatar_color=_avatar(cand["id"]), checklist_ok=sum(1 for x in ck if x["status"] == "ok"),
                            checklist_total=len(ck), matches=nmatch.get(cand["id"], 0), conversation_id=convs.get(cand["id"])))
        sort = q.get("sort") or "-created_at"
        key = sort.lstrip("-")
        out.sort(key=lambda r: (r.get(key) is None, r.get(key) or ""), reverse=sort.startswith("-"))
        return _page(out, q)


@router.get("/candidates/{cid}")
def candidate(cid: int):
    with conn() as c:
        cand = db.get(c, "candidates", cid)
        if not cand:
            raise HTTPException(404, "candidate not found")
        docs = E.documents_of(c, cid)
        matches = [_match_row(c, m) for m in db.rows("matches", c.execute("select * from matches where candidate_id=? order by score desc", (cid,)))]
        cohorts = db.rows("cohorts", c.execute("select * from cohorts where candidate_ids like ? or candidate_ids like ? or candidate_ids like ?", (f"[{cid},%", f"%, {cid},%", f"%, {cid}]")))
        cohorts = [x for x in cohorts if cid in x["candidate_ids"]]
        conv = E._cand_conv_id(c, cid)
        return {"candidate": dict(cand, phone_masked=S.phone_masked(cand["phone"]), avatar_color=_avatar(cid), campaign=db.get(c, "campaigns", cand["source_campaign_id"]) if cand.get("source_campaign_id") else None),
                "documents": docs, "checklist": E.checklist(cand, docs), "matches": matches or [dict(m, status="suggested") for m in E.top_matches(c, cand, 5)],
                "cohorts": cohorts, "interviews": db.rows("interviews", c.execute("select * from interviews where candidate_id=? order by at", (cid,))),
                "conversation_id": conv, "timeline": _events_for(c, [("candidate_id", cid), ("conversation_id", conv)])}


@router.post("/candidates/{cid}/stage")
async def candidate_stage(cid: int, request: Request):
    b = await body(request)
    stage = b.get("stage")
    if stage not in S.STAGES + ["lost", "dormant"]:
        raise HTTPException(400, f"stage must be one of {S.STAGES + ['lost', 'dormant']}")
    with conn() as c:
        cand = db.get(c, "candidates", cid)
        if not cand:
            raise HTTPException(404, "candidate not found")
        return E.set_stage(c, cand, stage, b.get("reason"), b.get("by") or "operator")


# --- funnel --------------------------------------------------------------------------------------------------
def _funnel_stages(cands, now):
    idx = {s: i for i, s in enumerate(S.STAGES)}
    out, prev = [], None
    for i, s in enumerate(S.STAGES):
        reached = sum(1 for x in cands if x["stage"] in idx and idx[x["stage"]] >= i)
        here = [x for x in cands if x["stage"] == s]
        days = [max(0.0, (now - S.parse(x["stage_changed_at"] or x["created_at"])).total_seconds() / 86400) for x in here if x.get("stage_changed_at") or x.get("created_at")]
        out.append({"stage": s, "count": len(here), "reached": reached, "conversion_from_prev": (round(min(1.0, reached / prev), 3) if prev else None),
                    "median_days": round(statistics.median(days), 1) if days else None})
        prev = reached if reached else prev
    return out


@router.get("/funnel")
def funnel(by: str = "source"):
    if by not in ("source", "language", "region", "owner"):
        raise HTTPException(400, "by must be source|language|region|owner")
    with conn() as c:
        now = S.sim_now(c)
        cands = db.rows("candidates", c.execute("select * from candidates"))
        camps = {r["id"]: r for r in db.rows("campaigns", c.execute("select * from campaigns"))}
        keyf = {"source": lambda x: (camps.get(x["source_campaign_id"]) or {}).get("name") or "unbekannt", "language": lambda x: x["language"] or "de",
                "region": lambda x: x["region"] or "unbekannt", "owner": lambda x: x["owner"] or "–"}[by]
        groups = {}
        for x in cands:
            groups.setdefault(keyf(x), []).append(x)
        leaks = [{"reason": r["lost_reason"] or "unbekannt", "count": r["n"]} for r in c.execute("select lost_reason, count(*) n from candidates where stage='lost' group by lost_reason order by n desc")]
        spend = sum(x["spend_total"] or 0 for x in camps.values())
        idx = {s: i for i, s in enumerate(S.STAGES)}
        n_q = sum(1 for x in cands if idx.get(x["stage"], -1) >= idx["qualified"])
        n_i = sum(1 for x in cands if idx.get(x["stage"], -1) >= idx["interview_scheduled"])
        n_p = sum(1 for x in cands if x["stage"] == "placed")
        baseline = {"leads": len(cands), "qualified": n_q, "interviews": n_i, "placed": n_p, "spend_total": round(spend, 2), "lost": sum(1 for x in cands if x["stage"] == "lost"),
                    "dormant": sum(1 for x in cands if x["stage"] == "dormant"), "cost_per_lead": round(spend / len(cands), 2) if cands else None,
                    "cost_per_qualified": round(spend / n_q, 2) if n_q else None, "cost_per_interview": round(spend / n_i, 2) if n_i else None, "cost_per_placed": round(spend / n_p, 2) if n_p else None}
        return {"by": by, "sim_now": S.iso(now), "stages": _funnel_stages(cands, now),
                "groups": [{"key": k, "count": len(v), "stages": _funnel_stages(v, now), "lost": sum(1 for x in v if x["stage"] == "lost")} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))],
                "leaks": leaks, "baseline": baseline}


# --- matches -------------------------------------------------------------------------------------------------
def _match_row(c, m):
    cand = db.get(c, "candidates", m["candidate_id"])
    cl = E.clinic_row(c, m["clinic_id"]) or {}
    post = E.posting_row(c, m["posting_id"]) if m.get("posting_id") else None
    return {"id": m["id"], "candidate": _cand_brief(cand), "clinic": {"clinic_id": m["clinic_id"], "name": cl.get("name"), "town": cl.get("town"), "regierungsbezirk": cl.get("regierungsbezirk"), "jobs_open": cl.get("jobs_open")},
            "posting": {"posting_id": post["posting_id"], "title": post["title"], "url": post.get("url"), "department_hint": post.get("department_hint")} if post else None,
            "score": m["score"], "reasons": m["reasons"], "status": m["status"], "cohort_id": m.get("cohort_id"), "created_at": m.get("created_at"), "candidate_id": m["candidate_id"], "clinic_id": m["clinic_id"]}


@router.get("/matches")
def matches(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        where, params = [], []
        if q.get("candidate_id"):
            where.append("candidate_id=?")
            params.append(int(q["candidate_id"]))
        if q.get("clinic_id"):
            where.append("clinic_id=?")
            params.append(str(q["clinic_id"]))
        if q.get("status"):
            where.append(f"status in ({','.join('?' * len(q['status'].split(',')))})")
            params += q["status"].split(",")
        if q.get("cohort_id"):
            where.append("cohort_id=?")
            params.append(int(q["cohort_id"]))
        sql = "select * from matches" + (" where " + " and ".join(where) if where else "") + " order by score desc, id"
        rows = [_match_row(c, m) for m in db.rows("matches", c.execute(sql, params))]
        if q.get("clinic_id") and not rows and not q.get("status"):                   # live ranking for a registry clinic without stored matches
            cl = E.clinic_row(c, q["clinic_id"])
            if cl:
                jobs = [dict(r) for r in c.execute("select * from registry_postings where clinic_id=?", (str(q["clinic_id"]),))]
                for cand in db.rows("candidates", c.execute("select * from candidates where stage in ('qualified','matching','profile_sent','docs_pending')")):
                    sc, why = M.score(cand, cl, jobs)
                    if sc >= 50:
                        rows.append({"id": None, "candidate": _cand_brief(cand), "clinic": {"clinic_id": cl["clinic_id"], "name": cl["name"], "town": cl["town"]}, "posting": None,
                                     "score": sc, "reasons": why, "status": "suggested", "cohort_id": None, "candidate_id": cand["id"], "clinic_id": cl["clinic_id"]})
                rows.sort(key=lambda r: -r["score"])
        return _page(rows, q)


@router.post("/matches/{mid}/status")
async def match_status(mid: int, request: Request):
    b = await body(request)
    st = b.get("status")
    allowed = ("proposed", "approved", "sent", "clinic_interested", "interview", "declined_by_clinic", "declined_by_candidate", "placed")
    if st not in allowed:
        raise HTTPException(400, f"status must be one of {allowed}")
    with conn() as c:
        m = db.get(c, "matches", mid)
        if not m:
            raise HTTPException(404, "match not found")
        if st == "sent":
            r = E.match_send(c, m, b.get("as") or "luna")
            return dict(_match_row(c, db.get(c, "matches", mid)), approval_id=r.get("approval_id"), send_status=r.get("status"), thread_id=r.get("thread_id"))
        db.update(c, "matches", mid, {"status": st})
        now = S.sim_now(c)
        cand = db.get(c, "candidates", m["candidate_id"])
        db.event(c, S.iso(now), b.get("by") or "operator", "match_status", {"candidate_id": m["candidate_id"], "clinic_id": m["clinic_id"], "match_id": mid, "conversation_id": E._cand_conv_id(c, m["candidate_id"])},
                 f"Match {cand['initials'] if cand else m['candidate_id']} × {m['clinic_id']}: {m['status']} → {st}")
        if st == "placed" and cand and cand["stage"] != "placed":
            E.set_stage(c, cand, "placed", "Match platziert", b.get("by") or "operator", now)
        return _match_row(c, db.get(c, "matches", mid))


# --- cohorts -------------------------------------------------------------------------------------------------
def _cohort_detail(c, coh):
    members = [_cand_brief(db.get(c, "candidates", i)) for i in coh["candidate_ids"]]
    clinics = [dict(E.clinic_row(c, k) or {"clinic_id": k, "name": f"Klinik {k}"}) for k in coh["clinic_ids"]]
    threads = db.rows("clinic_threads", c.execute("select * from clinic_threads where cohort_id=? order by id", (coh["id"],)))
    outcomes = [_match_row(c, m) for m in db.rows("matches", c.execute("select * from matches where cohort_id=? order by clinic_id, score desc", (coh["id"],)))]
    by_status = {}
    for m in outcomes:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
    return dict(coh, members=[m for m in members if m], clinics=clinics, threads=threads, outcomes=outcomes, outcome_counts=by_status,
                thread_states={s: sum(1 for t in threads if t["state"] == s) for s in S.THREAD_STATES if any(t["state"] == s for t in threads)})


@router.get("/cohorts")
def cohorts(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        rows = db.rows("cohorts", c.execute("select * from cohorts order by id desc"))
        if q.get("status"):
            rows = [r for r in rows if r["status"] in q["status"].split(",")]
        out = []
        for coh in rows:
            threads = db.rows("clinic_threads", c.execute("select state from clinic_threads where cohort_id=?", (coh["id"],)))
            out.append(dict(coh, n_candidates=len(coh["candidate_ids"]), n_clinics=len(coh["clinic_ids"]), n_threads=len(threads),
                            thread_states={s: sum(1 for t in threads if t["state"] == s) for s in S.THREAD_STATES if any(t["state"] == s for t in threads)},
                            interested=sum(1 for t in threads if t["state"] in ("interested", "scheduling", "scheduled", "feedback_pending", "closed_won"))))
        return _page(out, q)


@router.post("/cohorts/preview")
async def cohorts_preview(request: Request):
    b = await body(request)
    crit = b.get("criteria") or {k: v for k, v in b.items() if k in ("role_class", "region", "qualification", "german_level_min", "anerkennung", "departments")}
    with conn() as c:
        pol = db.policy(c)
        clinics, jobs = S.registry(c)
        cands = db.rows("candidates", c.execute("select * from candidates where stage not in ('lost','dormant','placed','new')"))
        matches_ = db.rows("matches", c.execute("select * from matches"))
        prev = M.cohort_preview(crit, cands, clinics, jobs, pol, matches_, pol["sim_now"])
        cs = prev["candidates"]
        profiles = "\n\n".join(S.anonymised_profile(x) for x in cs[:8])
        subject = f"{len(cs)} Pflegekräfte ({M.ROLE_LABEL.get(crit.get('role_class'), crit.get('role_class') or 'Pflege')}{', ' + crit['region'] if crit.get('region') else ''}) – anonymisierte Profile"
        t = E.template_for(c, "email", "de", "cohort_bundle")
        criteria_parts = []
        if crit.get("role_class"):
            criteria_parts.append(M.ROLE_LABEL.get(crit["role_class"], crit["role_class"]))
        if crit.get("qualification"):
            criteria_parts.append(crit["qualification"])
        if crit.get("departments"):
            criteria_parts.append(", ".join(crit["departments"]) if isinstance(crit["departments"], list) else str(crit["departments"]))
        if crit.get("region"):
            criteria_parts.append(crit["region"])
        if crit.get("german_level_min"):
            criteria_parts.append(f"Deutsch mind. {crit['german_level_min']}")
        if crit.get("anerkennung"):
            criteria_parts.append(f"Anerkennung: {crit['anerkennung']}")
        criteria_label = ", ".join(criteria_parts) or "Pflege"
        bodytxt = S.render(t["body"], {"contact_name": "Damen und Herren", "clinic_name": "{{clinic_name}}", "criteria": criteria_label, "n": len(cs), "profiles": profiles}) if t else profiles
        return {"criteria": crit, "candidates": [dict(_cand_brief(x), profile=S.anonymised_profile(x), qualification=x["qualification"], experience_years=x["experience_years"]) for x in cs],
                "clinics": prev["clinics"], "throttled": prev["throttled"], "bundle_preview": {"subject": subject, "body": bodytxt}}


@router.post("/cohorts")
async def cohorts_create(request: Request):
    b = await body(request)
    if not b.get("candidate_ids") or not b.get("clinic_ids"):
        raise HTTPException(400, "candidate_ids and clinic_ids are required")
    with conn() as c:
        now = S.sim_now(c)
        crit = b.get("criteria") or {}
        coh = {"name": b.get("name") or f"Cohort {crit.get('role_class') or ''} {crit.get('region') or ''}".strip(), "criteria": crit, "candidate_ids": [int(i) for i in b["candidate_ids"]],
               "clinic_ids": [str(i) for i in b["clinic_ids"]], "status": "draft", "created_at": S.iso(now)}
        cid = db.insert(c, "cohorts", coh)
        db.event(c, S.iso(now), b.get("by") or "operator", "cohort_created", {"cohort_id": cid}, f"Cohort „{coh['name']}“: {len(coh['candidate_ids'])} Profile, {len(coh['clinic_ids'])} Kliniken")
        return _cohort_detail(c, db.get(c, "cohorts", cid))


@router.get("/cohorts/{cid}")
def cohort(cid: int):
    with conn() as c:
        coh = db.get(c, "cohorts", cid)
        if not coh:
            raise HTTPException(404, "cohort not found")
        return _cohort_detail(c, coh)


@router.post("/cohorts/{cid}/send")
async def cohort_send(cid: int, request: Request):
    b = await body(request)
    with conn() as c:
        coh = db.get(c, "cohorts", cid)
        if not coh:
            raise HTTPException(404, "cohort not found")
        if coh["status"] not in ("draft", "pending_approval"):
            raise HTTPException(400, f"cohort already {coh['status']}")
        r = E.cohort_send(c, coh, b.get("as") or "luna")
        return dict(r, cohort=db.get(c, "cohorts", cid))


# --- clinic threads --------------------------------------------------------------------------------------------
def _thread_row(c, th):
    conv = E.clinic_conv(c, th)
    return dict(th, clinic=E.clinic_row(c, th["clinic_id"]), conversation_id=conv["id"] if conv else None, unread=conv["unread"] if conv else 0, mode=conv["mode"] if conv else None,
                next_actor=conv["next_actor"] if conv else None, sla_due_at=conv["sla_due_at"] if conv else None, last_preview=conv["last_preview"] if conv else None,
                candidates=[_cand_brief(db.get(c, "candidates", i)) for i in th["candidate_ids"]])


@router.get("/clinic-threads")
def clinic_threads(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        where, params = [], []
        if q.get("state"):
            where.append(f"state in ({','.join('?' * len(q['state'].split(',')))})")
            params += q["state"].split(",")
        if q.get("clinic_id"):
            where.append("clinic_id=?")
            params.append(str(q["clinic_id"]))
        if q.get("cohort_id"):
            where.append("cohort_id=?")
            params.append(int(q["cohort_id"]))
        sql = "select * from clinic_threads" + (" where " + " and ".join(where) if where else "") + " order by coalesce(last_at, created_at) desc"
        rows = [_thread_row(c, t) for t in db.rows("clinic_threads", c.execute(sql, params))]
        if q.get("q"):
            s = q["q"].lower()
            rows = [r for r in rows if s in (r["clinic_name"] or "").lower() or s in ((r.get("clinic") or {}).get("town") or "").lower()]
        return _page(rows, q)


@router.get("/clinic-threads/{tid}")
def clinic_thread(tid: int):
    with conn() as c:
        th = db.get(c, "clinic_threads", tid)
        if not th:
            raise HTTPException(404, "thread not found")
        conv = E.clinic_conv(c, th)
        msgs = db.rows("messages", c.execute("select * from messages where conversation_id=? order by at, id", (conv["id"],))) if conv else []
        return {"thread": _thread_row(c, th), "conversation": conv, "messages": msgs,
                "negotiation": {"proposed_slots": th["proposed_slots"], "agreed_slot": th["agreed_slot"], "round": th["round"], "followup_attempt": th["followup_attempt"], "followup_max": th["followup_max"], "next_followup_at": th["next_followup_at"]},
                "interviews": db.rows("interviews", c.execute("select * from interviews where clinic_thread_id=? order by at", (tid,))),
                "matches": [_match_row(c, m) for m in db.rows("matches", c.execute("select * from matches where clinic_id=? and candidate_id in (%s)" % ",".join("?" * len(th["candidate_ids"] or [0])), (th["clinic_id"], *(th["candidate_ids"] or [0]))))],
                "suggestion": E.next_action(conv, c) if conv else None, "cohort": db.get(c, "cohorts", th["cohort_id"]) if th.get("cohort_id") else None,
                "timeline": _events_for(c, [("clinic_thread_id", tid), ("conversation_id", conv["id"] if conv else None)])}


@router.post("/clinic-threads/{tid}/action")
async def clinic_thread_action(tid: int, request: Request):
    b = await body(request)
    action = b.get("action")
    if action not in ("follow_up_now", "propose_slots", "confirm_slot", "counter_slot", "request_feedback", "close"):
        raise HTTPException(400, "action must be one of follow_up_now|propose_slots|confirm_slot|counter_slot|request_feedback|close")
    with conn() as c:
        th = db.get(c, "clinic_threads", tid)
        if not th:
            raise HTTPException(404, "thread not found")
        params = b.get("params") or {k: v for k, v in b.items() if k != "action"}
        r = E.thread_action(c, th, action, params, params.get("as") or b.get("as") or "luna")
        return {"ok": True, "thread": _thread_row(c, db.get(c, "clinic_threads", tid)), "result": r}


@router.get("/interviews")
def interviews(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        where, params = [], []
        if q.get("status"):
            where.append(f"i.status in ({','.join('?' * len(q['status'].split(',')))})")
            params += q["status"].split(",")
        if q.get("from"):
            where.append("i.at>=?")
            params.append(q["from"])
        if q.get("to"):
            where.append("i.at<=?")
            params.append(q["to"])
        sql = "select i.*, t.clinic_name, t.contact_name from interviews i left join clinic_threads t on t.id=i.clinic_thread_id" + (" where " + " and ".join(where) if where else "") + " order by i.at"
        rows = []
        for r in c.execute(sql, params):
            d = dict(r)
            d["candidate"] = _cand_brief(db.get(c, "candidates", d["candidate_id"]))
            d["clinic"] = E.clinic_row(c, d["clinic_id"])
            rows.append(d)
        return _page(rows, q)


# --- approvals -------------------------------------------------------------------------------------------------
def _approval_row(c, a):
    ctx = a.get("context") or {}
    d = dict(a)
    d["candidate"] = _cand_brief(db.get(c, "candidates", ctx["candidate_id"])) if ctx.get("candidate_id") else None
    d["clinic_thread"] = db.get(c, "clinic_threads", ctx["clinic_thread_id"]) if ctx.get("clinic_thread_id") else None
    d["cohort"] = db.get(c, "cohorts", ctx["cohort_id"]) if ctx.get("cohort_id") else None
    d["campaign"] = db.get(c, "campaigns", ctx["campaign_id"]) if ctx.get("campaign_id") else None
    return d


@router.get("/approvals")
def approvals(request: Request):
    q = dict(request.query_params)
    status = q.get("status", "pending")
    with conn() as c:
        where, params = [], []
        if status and status != "all":
            where.append(f"status in ({','.join('?' * len(status.split(',')))})")
            params += status.split(",")
        if q.get("risk"):
            where.append("risk=?")
            params.append(q["risk"])
        if q.get("kind"):
            where.append("kind=?")
            params.append(q["kind"])
        sql = "select * from approvals" + (" where " + " and ".join(where) if where else "") + " order by case risk when 'high' then 0 when 'medium' then 1 else 2 end, created_at desc"
        rows = [_approval_row(c, a) for a in db.rows("approvals", c.execute(sql, params))]
        return _page(rows, q)


@router.post("/approvals/bulk")
async def approvals_bulk(request: Request):
    b = await body(request)
    decision = b.get("decision")
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    with conn() as c:
        where, params = ["status='pending'"], []
        if b.get("risk"):
            where.append("risk=?")
            params.append(b["risk"])
        if b.get("kind"):
            where.append("kind=?")
            params.append(b["kind"])
        if b.get("ids"):
            where.append(f"id in ({','.join('?' * len(b['ids']))})")
            params += [int(i) for i in b["ids"]]
        n = 0
        for a in db.rows("approvals", c.execute(f"select * from approvals where {' and '.join(where)} order by id", params)):
            E.decide(c, a, decision, None, bool(b.get("remember")), b.get("by") or "operator")
            n += 1
        return {"decided": n, "decision": decision}


@router.post("/approvals/{aid}")
async def approval_decide(aid: int, request: Request):
    b = await body(request)
    decision = b.get("decision")
    if decision not in ("approve", "reject", "edit"):
        raise HTTPException(400, "decision must be approve|reject|edit")
    with conn() as c:
        a = db.get(c, "approvals", aid)
        if not a:
            raise HTTPException(404, "approval not found")
        if a["status"] != "pending":
            raise HTTPException(400, f"approval already {a['status']}")
        appr, effect = E.decide(c, a, decision, b.get("text"), bool(b.get("remember")), b.get("by") or "operator")
        return {"approval": _approval_row(c, appr), "effect": effect}


# --- queue -----------------------------------------------------------------------------------------------------
def _queue_row(c, q, now):
    d = dict(q)
    d["overdue"] = q["status"] == "scheduled" and S.parse(q["due_at"]) < now
    tgt = q.get("target") or {}
    d["candidate"] = _cand_brief(db.get(c, "candidates", tgt["candidate_id"])) if tgt.get("candidate_id") else None
    th = db.get(c, "clinic_threads", tgt["clinic_thread_id"]) if tgt.get("clinic_thread_id") else None
    d["clinic_name"] = th["clinic_name"] if th else None
    d["conversation_id"] = tgt.get("conversation_id")
    return d


@router.get("/queue")
def queue(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        now = S.sim_now(c)
        where, params = [], []
        status = q.get("status", "scheduled")
        if status and status != "all":
            where.append(f"status in ({','.join('?' * len(status.split(',')))})")
            params += status.split(",")
        if q.get("kind"):
            where.append("kind=?")
            params.append(q["kind"])
        if q.get("due_before"):
            where.append("due_at<=?")
            params.append(q["due_before"])
        sql = "select * from queue" + (" where " + " and ".join(where) if where else "") + " order by due_at, id"
        rows = [_queue_row(c, r, now) for r in db.rows("queue", c.execute(sql, params))]
        return dict(_page(rows, q), sim_now=S.iso(now), overdue=sum(1 for r in rows if r["overdue"]))


@router.post("/queue/{qid}")
async def queue_action(qid: int, request: Request):
    b = await body(request)
    action = b.get("action")
    if action not in ("run_now", "cancel", "snooze"):
        raise HTTPException(400, "action must be run_now|cancel|snooze")
    with conn() as c:
        row = db.get(c, "queue", qid)
        if not row:
            raise HTTPException(404, "queue row not found")
        now = S.sim_now(c)
        if row["status"] != "scheduled":
            raise HTTPException(400, f"queue row is {row['status']}")
        if action == "run_now":
            E.run_queue_row(c, row, now)
        elif action == "cancel":
            db.update(c, "queue", qid, {"status": "cancelled", "result": b.get("reason") or "manuell abgebrochen"})
            db.event(c, S.iso(now), b.get("by") or "operator", "queue_cancelled", dict(row.get("target") or {}, queue_id=qid), row.get("reason"))
        else:
            minutes = int(b.get("minutes") or 240)
            db.update(c, "queue", qid, {"due_at": S.iso(max(S.parse(row["due_at"]), now) + timedelta(minutes=minutes))})
            db.event(c, S.iso(now), b.get("by") or "operator", "queue_snoozed", dict(row.get("target") or {}, queue_id=qid), f"+{minutes} min")
        return _queue_row(c, db.get(c, "queue", qid), now)


# --- policy, tick -----------------------------------------------------------------------------------------------
@router.get("/policy")
def policy_get():
    with conn() as c:
        return db.policy(c)


@router.put("/policy")
async def policy_put(request: Request):
    b = await body(request)
    with conn() as c:
        old = db.policy(c)
        changed = []
        for k, v in b.items():
            if k == "sim_now" or k not in db.DEFAULT_POLICY and k != "remembered_rules":
                continue
            if k == "mode" and v not in ("off", "assist", "auto"):
                raise HTTPException(400, "mode must be off|assist|auto")
            if k == "quiet_hours" and not (isinstance(v, list) and len(v) == 2 and 0 <= int(v[0]) < int(v[1]) <= 24):
                raise HTTPException(400, "quiet_hours must be [start, end]")
            if old.get(k) != v:
                db.set_policy(c, k, v)
                changed.append(k)
        if changed:
            db.event(c, old["sim_now"], b.get("by") or "operator", "policy_changed", {"keys": changed}, ", ".join(f"{k}: {old.get(k)} → {b[k]}" for k in changed)[:300])
        return db.policy(c)


@router.post("/tick")
async def tick(request: Request):
    b = await body(request)
    minutes = int(b.get("minutes") or 60)
    if not 1 <= minutes <= 60 * 24 * 7:
        raise HTTPException(400, "minutes must be between 1 and 10080")
    with conn() as c:
        return E.tick(minutes, c)


# --- campaigns, landing pages ------------------------------------------------------------------------------------
def _campaign_row(c, camp, cands):
    idx = {s: i for i, s in enumerate(S.STAGES)}
    mine = [x for x in cands if x["source_campaign_id"] == camp["id"]]
    leads = len(mine)
    st = {"leads": leads, "contacted": sum(1 for x in mine if idx.get(x["stage"], -1) >= idx["contacted"]), "qualified": sum(1 for x in mine if idx.get(x["stage"], -1) >= idx["qualified"]),
          "profile_sent": sum(1 for x in mine if idx.get(x["stage"], -1) >= idx["profile_sent"]), "interview": sum(1 for x in mine if idx.get(x["stage"], -1) >= idx["interview_scheduled"]),
          "placed": sum(1 for x in mine if x["stage"] == "placed"), "lost": sum(1 for x in mine if x["stage"] == "lost")}
    spend = camp["spend_total"] or 0
    cost = {k: (round(spend / v, 2) if v else None) for k, v in st.items() if k != "lost"}
    cpl, spike = E.campaign_cpl(camp)
    row = dict(camp, cpl=round(cpl, 2) if cpl else None, cpq=cost["qualified"], cpp=cost["placed"], cpi=cost["interview"], cpl_spike=spike, ctr=round(camp["clicks"] / camp["impressions"] * 100, 2) if camp["impressions"] else None,
               attributed=st, cost_per=cost)
    return row, {"campaign_id": camp["id"], "name": camp["name"], "stages": st, "cost_per": cost, "spend_total": spend}


@router.get("/campaigns")
def campaigns():
    with conn() as c:
        cands = db.rows("candidates", c.execute("select * from candidates"))
        rows, attribution = [], []
        for camp in db.rows("campaigns", c.execute("select * from campaigns order by id")):
            r, a = _campaign_row(c, camp, cands)
            rows.append(r)
            attribution.append(a)
        lps = []
        for lp in db.rows("landing_pages", c.execute("select * from landing_pages order by id")):
            vs = [dict(v, conversion=round(v["leads"] / v["visits"], 4) if v["visits"] else 0) for v in lp["variants"]]
            lps.append(dict(lp, variants=vs, visits=sum(v["visits"] for v in vs), leads=sum(v["leads"] for v in vs), conversion=round(sum(v["leads"] for v in vs) / max(1, sum(v["visits"] for v in vs)), 4),
                            attributed_leads=sum(1 for x in cands if x.get("landing_page_id") == lp["id"])))
        alerts = [a for a in _alerts(c, {}, [dict(a) for a in db.rows("accounts", c.execute("select * from accounts"))]) if a.get("campaign_id") or "Landingpage" in a["text"]]
        return {"campaigns": rows, "landing_pages": lps, "attribution": attribution, "alerts": alerts, "sim_now": db.policy(c)["sim_now"]}


@router.post("/campaigns/{cid}")
async def campaign_action(cid: int, request: Request):
    b = await body(request)
    action = b.get("action")
    if action not in ("pause", "resume", "budget"):
        raise HTTPException(400, "action must be pause|resume|budget")
    with conn() as c:
        camp = db.get(c, "campaigns", cid)
        if not camp:
            raise HTTPException(404, "campaign not found")
        r = E.campaign_action(c, camp, action, b.get("daily_budget"), b.get("by") or "operator")
        cands = db.rows("candidates", c.execute("select * from candidates"))
        row, _ = _campaign_row(c, db.get(c, "campaigns", cid), cands)
        if r.get("approval_id"):
            row.update(approval_id=r["approval_id"], pending_budget=r["pending_budget"], status_note="Budgeterhöhung wartet auf Freigabe")
        return row


@router.post("/landing-pages/{lid}")
async def landing_page_action(lid: int, request: Request):
    b = await body(request)
    action, variant = b.get("action"), b.get("variant")
    if action not in ("set_winner", "toggle"):
        raise HTTPException(400, "action must be set_winner|toggle")
    with conn() as c:
        lp = db.get(c, "landing_pages", lid)
        if not lp:
            raise HTTPException(404, "landing page not found")
        now = S.sim_now(c)
        if action == "set_winner":
            if variant not in [v["key"] for v in lp["variants"]]:
                raise HTTPException(400, "unknown variant")
            for v in lp["variants"]:
                v["winner"] = v["key"] == variant
            db.update(c, "landing_pages", lid, {"variants": lp["variants"]})
            db.event(c, S.iso(now), b.get("by") or "operator", "lp_winner", {"landing_page_id": lid}, f"{lp['slug']}: Variante {variant} gewinnt")
        else:
            st = "paused" if lp["status"] == "live" else "live"
            db.update(c, "landing_pages", lid, {"status": st})
            db.event(c, S.iso(now), b.get("by") or "operator", "lp_toggled", {"landing_page_id": lid}, f"{lp['slug']}: {st}")
        return db.get(c, "landing_pages", lid)


# --- templates, accounts, seed --------------------------------------------------------------------------------------
@router.get("/templates")
def templates(request: Request):
    q = dict(request.query_params)
    with conn() as c:
        where, params = [], []
        for k in ("channel", "lang", "stage"):
            if q.get(k):
                where.append(f"{k}=?")
                params.append(q[k])
        sql = "select * from templates" + (" where " + " and ".join(where) if where else "") + " order by channel, lang, id"
        return _page(db.rows("templates", c.execute(sql, params)), q)


@router.put("/templates/{tid}")
async def template_put(tid: int, request: Request):
    b = await body(request)
    with conn() as c:
        t = db.get(c, "templates", tid)
        if not t:
            raise HTTPException(404, "template not found")
        upd = {k: b[k] for k in ("name", "subject", "body", "active", "stage", "lang", "channel") if k in b}
        if "body" in upd:
            import re
            upd["variables"] = sorted(set(re.findall(r"{{\s*(\w+)\s*}}", upd["body"])))
        if "active" in upd:
            upd["active"] = 1 if upd["active"] else 0
        if not upd:
            raise HTTPException(400, "nothing to update")
        upd["version"] = (t["version"] or 1) + 1
        db.update(c, "templates", tid, upd)
        db.event(c, db.policy(c)["sim_now"], b.get("by") or "operator", "template_changed", {"template_id": tid}, f"{t['name']}: v{upd['version']}")
        return db.get(c, "templates", tid)


@router.get("/accounts")
def accounts():
    with conn() as c:
        rows = db.rows("accounts", c.execute("select * from accounts order by id"))
        camps = {r["id"]: r["name"] for r in c.execute("select id, name from campaigns")}
        routing = []
        for a in rows:
            if a["kind"] == "whatsapp":
                r = a.get("routing") or {}
                routing.append({"account_id": a["id"], "name": a["name"], "identifier": a["identifier"], "campaign_ids": r.get("campaign_ids") or [],
                                "campaigns": [camps.get(i, f"#{i}") for i in r.get("campaign_ids") or []], "languages": r.get("languages") or [], "note": r.get("note")})
        return {"whatsapp": [a for a in rows if a["kind"] == "whatsapp"], "mailboxes": [a for a in rows if a["kind"] == "mailbox"], "apis": [a for a in rows if a["kind"] == "api"], "routing": routing}


@router.post("/seed/reset")
def seed_reset():
    with db._lock:
        counts = S.seed(reset=True)
    _ready["ok"] = True
    return {"ok": True, "counts": counts}
