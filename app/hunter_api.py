"""/api/hunter/*: status, start, stop, run-once, targets, and PUT /api/settings/hunter (app/hunter.py).

Mounted by app/main.py under /api. The daemon (deploy/pflege-hunter.service) is a separate process: start/stop only
flip the enabled flag + clear today's stop reason in hunt_meta; the daemon polls them every 60 s. run-once kicks a
pass in a background thread of the web process when no daemon holds data/hunter.lock."""
import threading

from fastapi import APIRouter, HTTPException, Request

from . import hunter as H
from . import runs as R
from . import settings as ST

router = APIRouter()
_bg = {"thread": None}


@router.get("/hunter/status")
def hunter_status(live: int = 0):
    return H.status(live_pools=bool(live))


@router.get("/hunter/targets")
def hunter_targets(day: str = None):
    day = day or H.today()
    return {"day": day, "rows": H.state_rows(day)}


@router.post("/hunter/start")
def hunter_start():
    H.set_enabled(True)
    H.day_set(H.today(), "stop_reason", None)
    return {"enabled": True, "stop_reason": None, "daemon_alive": H.lock_held()}


@router.post("/hunter/stop")
def hunter_stop():
    """enabled=false: the daemon submits nothing more; runs already in flight finish (they are billed anyway)."""
    H.set_enabled(False)
    reason = "kill_switch: stopped via POST /api/hunter/stop"
    H.day_set(H.today(), "stop_reason", reason)
    return {"enabled": False, "stop_reason": reason, "running": bool(H.meta_get("running")) and H.lock_held()}


def _run_once_thread():
    lock = H.acquire_lock()
    if lock is None:
        return
    try:
        H.Hunter(log=lambda *p: R.log(0, "hunter " + " ".join(str(x) for x in p))).run_once()
    finally:
        lock.close()


@router.post("/hunter/run-once")
def hunter_run_once():
    if H.lock_held():
        raise HTTPException(409, "a hunter already holds data/hunter.lock (daemon or another pass)")
    if _bg["thread"] and _bg["thread"].is_alive():
        raise HTTPException(409, "a run-once pass is already in progress")
    if H.stop_file().exists():
        raise HTTPException(409, f"{H.stop_file()} exists; remove it first")
    H.set_enabled(True)
    H.day_set(H.today(), "stop_reason", None)
    t = threading.Thread(target=_run_once_thread, name="hunter-once", daemon=True)
    _bg["thread"] = t
    t.start()
    return {"started": True, "day": H.today()}


@router.put("/settings/hunter")
async def api_put_hunter(request: Request):
    try:
        return ST.save_hunter(await request.json())
    except ValueError as e:
        raise HTTPException(422, str(e))
