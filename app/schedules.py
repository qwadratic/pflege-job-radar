"""Schedules: any number of cron-driven scrapes, each with its own target, mode and budget.

Presets map to a cron line; `stagger_days` spreads one schedule's boards over N consecutive firings
(board hash % N == day ordinal % N) so a weekly pass is seven small runs, not one burst.
Evaluated every minute by the scheduler thread; state lives in SQLite (table `schedules`)."""
import hashlib
import json
from datetime import datetime, timezone

from croniter import croniter

from . import runs as R
from . import targets as T

PRESETS = {
    "weekly_staggered": {"cron": "0 3 * * *", "stagger_days": 7},
    "daily":            {"cron": "0 3 * * *", "stagger_days": 1},
    "weekdays":         {"cron": "0 3 * * 1-5", "stagger_days": 1},
    "hourly":           {"cron": "0 * * * *", "stagger_days": 1},
    "custom":           {},
}
MODES = ("auto", "adapter", "firecrawl")
SCHEMA = """
create table if not exists schedules (
  id integer primary key autoincrement, name text, enabled integer default 1, preset text default 'custom', cron text,
  stagger_days integer default 1, target text default '{"scope":"all","values":[]}', mode text default 'auto',
  max_credits integer default 40, fetch_details integer default 0, last_run_at text, created_at text);
"""
DEFAULT = {"name": "Weekly pass (staggered over 7 days)", "enabled": True, "preset": "weekly_staggered", "cron": "0 3 * * *",
           "stagger_days": 7, "target": {"scope": "all", "values": []}, "mode": "adapter", "max_credits": 40, "fetch_details": False}

_WD = {"de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


def init():
    with R._lock, R.db() as c:
        c.executescript(SCHEMA)
        n = c.execute("select count(*) from schedules").fetchone()[0]
    if n == 0:
        create(DEFAULT)


def _row(r):
    d = dict(r)
    d["enabled"] = bool(d.get("enabled"))
    d["fetch_details"] = bool(d.get("fetch_details"))
    try:
        d["target"] = json.loads(d.get("target") or "{}")
    except Exception:
        d["target"] = {"scope": "all", "values": []}
    d["next_run_at"] = next_run(d)
    d["human"] = {"de": human(d, "de"), "en": human(d, "en")}
    d["target_summary"] = {"de": T.summary(d["target"], "de"), "en": T.summary(d["target"], "en")}
    return d


def normalise(obj, base=None):
    cur = dict(base or DEFAULT)
    cur.update({k: v for k, v in (obj or {}).items() if k in ("name", "enabled", "preset", "cron", "stagger_days", "target", "mode", "max_credits", "fetch_details")})
    preset = cur.get("preset") or "custom"
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {list(PRESETS)}")
    if preset != "custom":
        cur["cron"] = PRESETS[preset]["cron"]
        if "stagger_days" not in (obj or {}):
            cur["stagger_days"] = PRESETS[preset]["stagger_days"]
    cron = (cur.get("cron") or "").strip()
    if not cron or not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression: {cron!r}")
    cur["cron"] = cron
    cur["stagger_days"] = max(1, min(31, int(cur.get("stagger_days") or 1)))
    cur["target"] = T.parse({"target": cur.get("target") or {}})
    if cur.get("mode") not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    cur["max_credits"] = max(0, min(500, int(cur.get("max_credits") or 0)))
    cur["enabled"] = bool(cur.get("enabled", True))
    cur["fetch_details"] = bool(cur.get("fetch_details"))
    cur["name"] = (cur.get("name") or "").strip() or f"{preset} · {T.summary(cur['target'], 'en')}"
    return cur


def list_all():
    with R._lock, R.db() as c:
        rows = c.execute("select * from schedules order by id").fetchall()
    return [_row(r) for r in rows]


def get(sid):
    with R._lock, R.db() as c:
        r = c.execute("select * from schedules where id=?", (sid,)).fetchone()
    return _row(r) if r else None


def create(obj):
    s = normalise(obj)
    with R._lock, R.db() as c:
        cur = c.execute("insert into schedules(name,enabled,preset,cron,stagger_days,target,mode,max_credits,fetch_details,created_at) values(?,?,?,?,?,?,?,?,?,?)",
                        (s["name"], int(s["enabled"]), s["preset"], s["cron"], s["stagger_days"], json.dumps(s["target"], ensure_ascii=False),
                         s["mode"], s["max_credits"], int(s["fetch_details"]), R.now()))
        sid = cur.lastrowid
    return get(sid)


def update(sid, obj):
    old = get(sid)
    if not old:
        return None
    base = {k: old[k] for k in ("name", "enabled", "preset", "cron", "stagger_days", "target", "mode", "max_credits", "fetch_details")}
    s = normalise(obj, base)
    with R._lock, R.db() as c:
        c.execute("update schedules set name=?,enabled=?,preset=?,cron=?,stagger_days=?,target=?,mode=?,max_credits=?,fetch_details=? where id=?",
                  (s["name"], int(s["enabled"]), s["preset"], s["cron"], s["stagger_days"], json.dumps(s["target"], ensure_ascii=False),
                   s["mode"], s["max_credits"], int(s["fetch_details"]), sid))
    return get(sid)


def delete(sid):
    with R._lock, R.db() as c:
        n = c.execute("delete from schedules where id=?", (sid,)).rowcount
    return n > 0


def next_run(s, now=None):
    if not s.get("enabled") or not s.get("cron"):
        return None
    now = now or datetime.now(timezone.utc)
    try:
        return croniter(s["cron"], now).get_next(datetime).isoformat(timespec="minutes")
    except Exception:
        return None


def is_due(s, now):
    """Due when the cron's previous firing is within the last minute and we have not run it since."""
    if not s.get("enabled") or not s.get("cron"):
        return False
    try:
        prev = croniter(s["cron"], now).get_prev(datetime)
    except Exception:
        return False
    if (now - prev).total_seconds() > 90:
        return False
    last = s.get("last_run_at")
    return not last or datetime.fromisoformat(last) < prev


def _batch_of(key, n):
    return int(hashlib.sha1(key.lower().encode()).hexdigest(), 16) % max(1, n)


def clinics_today(s, day=None):
    """The schedule's clinics, reduced to today's stagger slice (whole boards, so shared boards stay together)."""
    cs = T.clinics_for(s["target"])
    n = int(s.get("stagger_days") or 1)
    if n <= 1:
        return cs
    day = day or datetime.now(timezone.utc)
    slot = day.toordinal() % n
    return [c for c in cs if _batch_of(c.get("board") or c.get("careers_url") or c["clinic_id"], n) == slot]


def fire(s, stagger=True, trigger="schedule"):
    """Queue one run for the schedule. Returns run_id or None when the slice is empty."""
    from . import crawl as CR
    cs = clinics_today(s) if stagger else T.clinics_for(s["target"])
    with R._lock, R.db() as c:
        c.execute("update schedules set last_run_at=? where id=?", (R.now(), s["id"]))
    if not cs:
        return None
    ids = [c["clinic_id"] for c in cs]
    params = {"max_credits": int(s.get("max_credits") or 0), "deep": bool(s.get("fetch_details")), "verify": True,
              "schedule_id": s["id"], "schedule_name": s.get("name"), "stagger_days": int(s.get("stagger_days") or 1), "target": s["target"]}
    rid = R.create_run("clinic", ",".join(ids), s["mode"], params, ids, trigger=trigger)
    R.enqueue(rid)
    return rid


def human(s, lang="de"):
    """'täglich 03:00 UTC, Boards über 7 Tage verteilt · alle Kliniken · adapter'."""
    de = lang == "de"
    cron = (s.get("cron") or "").split()
    txt = s.get("cron") or ""
    if len(cron) == 5:
        m, h, dom, mon, dow = cron
        hhmm = f"{int(h):02d}:{int(m):02d}" if h.isdigit() and m.isdigit() else None
        if m.isdigit() and h == "*" and dom == "*" and mon == "*" and dow == "*":
            txt = ("stündlich" if de else "hourly") + f" (:{int(m):02d})"
        elif hhmm and dom == "*" and mon == "*" and dow == "*":
            txt = ("täglich " if de else "daily ") + hhmm + " UTC"
        elif hhmm and dom == "*" and mon == "*" and dow == "1-5":
            txt = ("werktags " if de else "weekdays ") + hhmm + " UTC"
        elif hhmm and dom == "*" and mon == "*" and dow.isdigit():
            txt = ("jeden " if de else "every ") + _WD[lang][(int(dow) - 1) % 7] + " " + hhmm + " UTC"
        else:
            txt = ("cron " if de else "cron ") + s.get("cron", "")
    n = int(s.get("stagger_days") or 1)
    if n > 1:
        txt += (f", Boards über {n} Tage verteilt" if de else f", boards spread over {n} days")
    return txt


def status():
    ss = list_all()
    nxt = [s["next_run_at"] for s in ss if s.get("next_run_at")]
    return {"schedules": len(ss), "enabled": sum(1 for s in ss if s["enabled"]), "next_run_at": min(nxt) if nxt else None}
