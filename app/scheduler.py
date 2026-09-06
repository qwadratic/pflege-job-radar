"""Scheduler thread: every minute, fire every enabled schedule whose cron just came due (app/schedules.py).
`next_run_at()` is the earliest upcoming firing across schedules (shown in /api/stats)."""
import threading
import time
from datetime import datetime, timezone

from . import schedules as SC

_thread = None
_state = {"last_tick": None, "fired": []}


def next_run_at(now=None):
    return SC.status()["next_run_at"]


def tick(force=False, now=None):
    """Evaluate all schedules. force=True fires every enabled schedule now (today's stagger slice)."""
    now = now or datetime.now(timezone.utc)
    _state["last_tick"] = now.isoformat(timespec="minutes")
    fired = []
    for s in SC.list_all():
        if force and s["enabled"] or (not force and SC.is_due(s, now)):
            try:
                rid = SC.fire(s, stagger=True, trigger="schedule")
                fired.append({"schedule_id": s["id"], "run_id": rid})
            except Exception as e:                                       # one bad schedule must not stop the rest
                fired.append({"schedule_id": s["id"], "error": str(e)[:200]})
    _state["fired"] = fired[-20:]
    return {"runs": [f["run_id"] for f in fired if f.get("run_id")], "fired": fired}


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
    SC.init()
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
        _thread.start()


def status():
    return {**SC.status(), "last_tick": _state["last_tick"], "last_fired": _state["fired"]}
