"""Scheduler thread: every minute, fire every enabled schedule whose cron just came due (app/schedules.py).
`next_run_at()` is the earliest upcoming firing across schedules (shown in /api/stats)."""
import threading
import time
from datetime import datetime, timezone

from . import runs as R
from . import schedules as SC

_thread = None
_state = {"last_tick": None, "fired": []}
PAUSE_KEY = "scheduler_pause"       # settings row, not a variable: crawl.kill_switch() pauses from the crawl worker process, this thread lives in the web process


def next_run_at(now=None):
    return SC.status()["next_run_at"]


def pause(reason=None):
    """Stop the scheduler from firing anything -- set by the Firecrawl 24h kill switch's 'disable' tier
    (app/crawl.py kill_switch()) when >=30% of plan credits were spent in a rolling 24h."""
    R.set_setting(PAUSE_KEY, {"reason": reason})


def resume():
    R.set_setting(PAUSE_KEY, None)


def is_paused():
    return R.get_setting(PAUSE_KEY) is not None


def tick(force=False, now=None):
    """Evaluate all schedules. force=True fires every enabled schedule now (today's stagger slice).
    A paused scheduler fires nothing on its own clock (force still works, e.g. from /api/autocrawl/tick,
    since that is an explicit human action, not the automatic loop)."""
    now = now or datetime.now(timezone.utc)
    _state["last_tick"] = now.isoformat(timespec="minutes")
    paused = R.get_setting(PAUSE_KEY)
    if paused is not None and not force:
        return {"runs": [], "fired": [], "paused": True, "paused_reason": paused["reason"]}
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
    paused = R.get_setting(PAUSE_KEY)
    return {**SC.status(), "last_tick": _state["last_tick"], "last_fired": _state["fired"],
            "paused": paused is not None, "paused_reason": paused["reason"] if paused else None}
