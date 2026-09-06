"""Weekly autocrawl, spread out: every day at `hour` (UTC) the boards whose hash falls into today's batch
are crawled, so a full pass over all routable boards takes `batches` days instead of one burst.
Unroutable clinics are only touched through Firecrawl when the weekly credit budget allows (mode auto)."""
import hashlib
import threading
import time
from datetime import datetime, timedelta, timezone

from . import data as D
from . import runs as R

DEFAULT = {"enabled": True, "weekday": 0, "hour": 4, "batches": 7, "mode": "auto", "firecrawl_weekly_budget": 100,
           "include_firecrawl": False}
_thread = None
_state = {"last_tick": None, "last_batch": None, "next_at": None}


def schedule():
    s = dict(DEFAULT)
    s.update(R.get_setting("schedule") or {})
    return s


def _batch_of(url, batches):
    return int(hashlib.sha1(url.lower().encode()).hexdigest(), 16) % max(1, batches)


def todays_boards(day=None):
    s = schedule()
    day = day or datetime.now(timezone.utc)
    b = day.toordinal() % max(1, int(s["batches"]))
    boards = {}
    for c in D.clinics():
        if c.get("board") and c.get("routable") and not c.get("walled"):
            if _batch_of(c["board"], int(s["batches"])) == b:
                boards.setdefault(c["board"], []).append(c["clinic_id"])
    return b, boards


def next_run_at(now=None):
    s = schedule()
    if not s.get("enabled"):
        return None
    now = now or datetime.now(timezone.utc)
    nxt = now.replace(hour=int(s["hour"]), minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt.isoformat(timespec="minutes")


def tick(force=False):
    s = schedule()
    now = datetime.now(timezone.utc)
    _state["next_at"] = next_run_at(now)
    if not force and (not s.get("enabled") or now.hour != int(s["hour"])):
        return None
    stamp = now.strftime("%Y-%m-%d")
    if not force and R.get_setting("autocrawl_last_day") == stamp:
        return None
    b, boards = todays_boards(now)
    R.set_setting("autocrawl_last_day", stamp)
    _state["last_tick"], _state["last_batch"] = now.isoformat(timespec="minutes"), b
    ids = []
    for url, cids in boards.items():
        rid = R.create_run("board", url, "adapter", {"max_credits": 0, "verify": True}, cids, trigger="autocrawl")
        R.enqueue(rid); ids.append(rid)
    if s.get("include_firecrawl"):
        # unroutable clinics, a few per day within the weekly budget: rotate by the same batch rule
        cap = int(s.get("firecrawl_max_credits", 40))
        left = int(s.get("firecrawl_weekly_budget", 100)) - R.usage_total(days=7)
        for c in D.clinics():
            if left < cap:
                break
            if not c.get("routable") and c.get("status") != "nicht_mehr_im_plan" and _batch_of(c["clinic_id"], int(s["batches"])) == b:
                rid = R.create_run("clinic", c["clinic_id"], "firecrawl", {"max_credits": cap}, [c["clinic_id"]], trigger="autocrawl")
                R.enqueue(rid); ids.append(rid); left -= cap
    return {"batch": b, "runs": ids, "boards": len(boards)}


def _loop():
    time.sleep(20)
    while True:
        try:
            tick()
        except Exception:
            pass
        time.sleep(60)


def start():
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="autocrawl", daemon=True)
        _thread.start()


def status():
    s = schedule()
    try:
        b, boards = todays_boards()
        today = {"batch": b, "boards": len(boards), "clinics": sum(len(v) for v in boards.values())}
    except Exception:
        today = None
    return {**s, "next_autocrawl": next_run_at(), "today": today, "last_tick": _state["last_tick"]}
