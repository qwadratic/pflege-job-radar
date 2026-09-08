"""Resilient Firecrawl hunter: one agent run per fetch=='firecrawl' Plan-KH clinic, N at a time, forever, failing fast.

  python -m app.hunter --dry-run    today's target list with the free pre-check verdicts; submits nothing
  python -m app.hunter --once       one pass over today's pending targets, then exit
  python -m app.hunter --daemon     the systemd mode (deploy/pflege-hunter.service): pass, sleep, repeat
  python -m app.hunter --status     the same JSON as GET /api/hunter/status

Every run is the production path (app.crawl.execute: spend gate, kill switch, ledger, inbox, drain) through a
crawl_runs row with trigger='hunter', exactly like tools/fc_hunt.py -- which is now a thin wrapper over the
helpers here (precheck, run_one, suspicious, pools). State is SQLite (app/runs.py: hunt_state one row per
clinic and UTC day, hunt_meta the per-day accumulators and flags), so a restart resumes and never re-runs a
clinic already done today. One instance at a time: flock on data/hunter.lock.

The user's bar (docs/firecrawl.md §6): "stop after (2 credit autorefills AND 0.5 $ per posting) OR all clinics
updated for today". Stop rules, first match wins, each logged with its name and the numbers behind it:
  1 all_updated   no pending target left today                       -> sleep until the next UTC day
  2 combined_bar  refills >= max_refills AND cost/posting > max_usd  -> wait for a human (POST /api/hunter/start)
  3 suspicious    fc_hunt rules, 3 failures in a row, burn rate, token floor, Firecrawl API down 5x -> wait for a human
  4 kill_switch   app.crawl.kill_switch refuses / firecrawl.enabled false / data/HUNTER_STOP exists -> wait for a human
Fail-fast ladder per clinic: free pre-check (a 'keine Stellen' page is skipped without a run) -> cap 120 ->
one retry at 200 only after an UNBILLED 'Agent reached max credits' -> needs_manual for today.
"""
import argparse
import fcntl
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import requests

from . import config as A
from . import crawl as CR
from . import data as D
from . import runs as R
from . import settings as ST
from pflege_jobs.sources import firecrawl_agent as FA

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
NO_JOBS = re.compile(r"(leider\s+)?(sind\s+)?(derzeit|aktuell|momentan|zur\s*zeit)\s+(sind\s+)?(in\s+diesem\s+bereich\s+)?keine\s+(offenen\s+)?stellen|keine\s+stellenangebote\s+(vorhanden|verf)", re.I)
MAX_CREDITS_RE = re.compile(r"agent reached max credits", re.I)
BACKOFF = (30, 60, 120, 300, 600)                       # seconds between retries of a failing Firecrawl API call
API_ERRORS_TO_STOP = 5
TERMINAL = ("done", "skipped", "failed", "needs_manual")
STATUSES = ("pending", "running") + TERMINAL
LOCK_NAME, STOP_NAME = "hunter.lock", "HUNTER_STOP"


# --- helpers shared with tools/fc_hunt.py -----------------------------------------------------
def precheck(c):
    """('run', why) or ('skip', why). Free: one plain GET of the careers page (or website)."""
    url = (c.get("careers_url") or c.get("website") or "").strip()
    if not url:
        return "run", "no url to pre-check (agent must search)"
    try:
        r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
    except Exception as e:
        return "run", f"pre-check fetch failed ({type(e).__name__}); leaving it to the agent"
    text = re.sub(r"<[^>]+>", " ", r.text or "")
    if r.status_code >= 400:
        return "run", f"careers page HTTP {r.status_code}; agent may find the board elsewhere"
    if NO_JOBS.search(text):
        return "skip", "careers page says there are no openings right now"
    return "run", f"careers page HTTP {r.status_code}, {len(text)//1000} kB"


def pools():
    d = FA.credits(historical=False) or {}
    return {"credits": d.get("remaining"), "tokens": d.get("tokens_remaining"), "runs_today": d.get("agent_runs_today"), "free_left": d.get("free_runs_left_today")}


def run_one(c, cap, trigger="hunter"):
    """One clinic through the production path: crawl_runs row -> app.crawl.execute -> summary dict."""
    rid = R.create_run("clinic", c["clinic_id"], "firecrawl", {"max_credits": cap}, [c["clinic_id"]], trigger=trigger)
    t0 = time.time()
    R.update_run(rid, status="running", started_at=R.now())
    CR.execute(rid)
    run = R.get_run(rid)
    log = run.get("log") or []
    charged = next((l for l in log if "charging" in l or "credits charged" in l), "")
    tok = re.search(r"tokens delta (-?\d+|None)", charged)
    return {"clinic_id": c["clinic_id"], "name": c.get("name"), "run_id": rid, "status": run.get("status"), "rows": run.get("n_rows"), "new": run.get("n_new"),
            "credits": run.get("credits_used"), "tokens_delta": (tok.group(1) if tok else None), "error": run.get("error"),
            "disagree": any("DISAGREE" in l for l in log), "gate": next((l for l in log if "spend gate" in l or "refused" in l or "skipped" in l), ""),
            "notes": next((l[l.find("notes:"):][:160] for l in log if "notes:" in l), ""), "secs": round(time.time() - t0),
            "max_credits_hit": any(MAX_CREDITS_RE.search(l) for l in log),
            "fail_text": " | ".join(l for l in log if "FAILED" in l or "refused" in l)[:400]}


def suspicious(res, a, zero_streak):
    """fc_hunt's rules: a is any object with max_charge / max_tokens. Returns the reason or None."""
    if res["error"]:
        return f"run {res['run_id']} error: {(res.get('fail_text') or res['error'])[:200]}"
    if res["disagree"]:
        return f"run {res['run_id']}: API creditsUsed and balance delta disagree"
    if (res["credits"] or 0) > a.max_charge:
        return f"run {res['run_id']} charged {res['credits']} credits (> {a.max_charge})"
    try:
        if res["tokens_delta"] not in (None, "None") and abs(int(res["tokens_delta"])) > a.max_tokens:
            return f"run {res['run_id']} token delta {res['tokens_delta']} (> {a.max_tokens})"
    except ValueError:
        pass
    if (res["rows"] or 0) > 60:
        return f"run {res['run_id']} returned {res['rows']} rows from one clinic"
    if zero_streak >= 3:
        return "three billable runs in a row returned nothing"
    return None


# --- state (hunt_state / hunt_meta) ------------------------------------------------------------
def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def host_of(url):
    try:
        h = (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return None
    return h[4:] if h.startswith("www.") else (h or None)


def meta_get(key, default=None):
    with R._lock, R.db() as c:
        r = c.execute("select value from hunt_meta where key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def meta_set(key, value):
    with R._lock, R.db() as c:
        c.execute("insert into hunt_meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value", (key, json.dumps(value)))


def day_get(day, name, default=None):
    return meta_get(f"{day}/{name}", default)


def day_set(day, name, value):
    meta_set(f"{day}/{name}", value)


def state_rows(day):
    with R._lock, R.db() as c:
        rows = c.execute("select * from hunt_state where day=? order by updated_at", (day,)).fetchall()
    return [dict(r) for r in rows]


def state_get(clinic_id, day):
    with R._lock, R.db() as c:
        r = c.execute("select * from hunt_state where clinic_id=? and day=?", (clinic_id, day)).fetchone()
    return dict(r) if r else None


def state_set(clinic_id, day, **kw):
    """Upsert one hunt_state row; unknown keys are refused so a typo never silently drops a field."""
    cols = {"name", "host", "status", "run_id", "cap", "credits", "tokens", "rows", "new", "attempts", "last_error"}
    bad = set(kw) - cols
    if bad:
        raise ValueError(f"hunt_state has no column {sorted(bad)}")
    if "last_error" in kw and kw["last_error"]:
        kw["last_error"] = str(kw["last_error"])[:400]
    kw["updated_at"] = R.now()
    with R._lock, R.db() as c:
        c.execute(f"insert into hunt_state(clinic_id,day,{','.join(kw)}) values(?,?,{','.join('?' * len(kw))}) "
                  f"on conflict(clinic_id,day) do update set {', '.join(f'{k}=excluded.{k}' for k in kw)}",
                  (clinic_id, day, *kw.values()))


def is_enabled():
    v = meta_get("enabled")
    return bool(ST.get_hunter().get("enabled")) if v is None else bool(v)


def set_enabled(v):
    meta_set("enabled", bool(v))
    ST.save_hunter({"enabled": bool(v)})


def stop_file():
    return A.DATA_DIR / STOP_NAME


def lock_path():
    return A.DATA_DIR / LOCK_NAME


def acquire_lock():
    """The single-instance flock; returns the open file (keep it referenced) or None when another process holds it."""
    A.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = open(lock_path(), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0); f.truncate(); f.write(f"{os.getpid()} {R.now()}\n"); f.flush()
    return f


def lock_held():
    """True when some process (a daemon, --once, or an API run-once thread) holds data/hunter.lock."""
    if not lock_path().exists():
        return False
    with open(lock_path(), "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


# --- the hunter --------------------------------------------------------------------------------
class Hunter:
    """One instance = one process's view; run_once() is one pass over today's targets. Everything that touches
    the network is an attribute (credits, execute_one, precheck, kill_switch, sleep) so tests inject stubs."""

    def __init__(self, cfg=None, log=print, clinics=None, precheck_fn=None, credits_fn=None, run_fn=None, kill_switch_fn=None, sleep=time.sleep):
        self.cfg = {**ST.get_hunter(), **(cfg or {})}
        self.log = log
        self._clinics = clinics
        self.precheck = precheck_fn or precheck
        self.credits = credits_fn or (lambda: FA.credits(historical=False))
        self.execute_one = run_fn or run_one
        self.kill_switch = kill_switch_fn or (lambda: CR.kill_switch(run_mode="hunter", trigger="hunter", log=self.log))
        self.sleep = sleep
        self.stop_reason = None
        self.consecutive_failures = 0
        self.zero_streak = 0
        self.api_error_streak = 0
        self.last_pools = {}
        self.last_result = None

    # -- targets
    def clinics(self):
        if self._clinics is not None:
            return self._clinics
        return D.snapshot().get("clinics") or []

    def candidates(self):
        cs = [c for c in self.clinics() if c.get("fetch") == "firecrawl" and c.get("status") == "Plan-KH"]
        cs.sort(key=lambda c: (-(c.get("beds") or 0), c.get("clinic_id") or ""))
        return cs

    def _row_for(self, c, day):
        return {"name": (c.get("name") or "")[:80], "host": host_of(c.get("careers_url"))}

    def next_target(self, day, in_flight=()):
        """The next clinic to submit, or None. Sibling boards: a host that already produced rows today under another
        clinic is recorded as skipped; a host that is in flight right now is deferred, not skipped."""
        rows = {r["clinic_id"]: r for r in state_rows(day)}
        harvested = {r["host"] for r in rows.values() if r.get("host") and (r.get("rows") or 0) > 0}
        busy = {host_of(c.get("careers_url")) for c in in_flight} - {None}
        busy |= {r["host"] for r in rows.values() if r.get("host") and r["status"] == "running"}
        for c in self.candidates():
            cid = c["clinic_id"]
            r = rows.get(cid)
            if r and r["status"] != "pending":
                continue
            h = host_of(c.get("careers_url"))
            if h and h in harvested:
                self.log(f"skip {cid} {c.get('name', '')[:40]}: sibling board {h} already harvested today")
                state_set(cid, day, status="skipped", last_error="sibling board already harvested today", **self._row_for(c, day))
                continue
            if h and h in busy:
                continue
            return c
        return None

    def pending_count(self, day):
        rows = {r["clinic_id"]: r for r in state_rows(day)}
        return sum(1 for c in self.candidates() if c["clinic_id"] not in rows or rows[c["clinic_id"]]["status"] == "pending")

    def dry_run(self, day=None):
        day = day or today()
        self.import_runs(day)
        rows = {r["clinic_id"]: r for r in state_rows(day)}
        harvested = {r["host"] for r in rows.values() if r.get("host") and (r.get("rows") or 0) > 0}
        out = []
        for c in self.candidates():
            r = rows.get(c["clinic_id"])
            h = host_of(c.get("careers_url"))
            if r and r["status"] != "pending":
                verdict, why = r["status"], f"already {r['status']} today" + (f": {r['last_error']}" if r.get("last_error") else "")
            elif h and h in harvested:
                verdict, why = "skip", f"sibling board {h} already harvested today"
            else:
                verdict, why = self.precheck(c)
            out.append({"clinic_id": c["clinic_id"], "name": c.get("name"), "beds": c.get("beds"), "host": h, "careers_url": c.get("careers_url"),
                        "cap": (r or {}).get("cap") or self.cfg["cap"], "verdict": verdict, "why": why})
        return {"day": day, "dry_run": True, "submitted": 0, "targets": out, "n_run": sum(1 for t in out if t["verdict"] == "run"),
                "n_skip": sum(1 for t in out if t["verdict"] != "run"), "rules": rules(self.cfg)}

    # -- accounting
    def _price(self):
        return float(ST.get_firecrawl().get("eur_per_credit") or 0.0053)

    def recount(self, day):
        """Rebuild the per-day accumulators in hunt_meta from hunt_state (the run rows are the source of truth)."""
        rows = state_rows(day)
        credits = sum(int(r.get("credits") or 0) for r in rows)
        new = sum(int(r.get("new") or 0) for r in rows if r["status"] == "done")
        acc = {"credits_spent": credits, "tokens_spent": sum(abs(int(r["tokens"])) for r in rows if isinstance(r.get("tokens"), int)),
               "new_postings": new, "runs": sum(int(r.get("attempts") or 0) for r in rows),
               "cost_per_posting_usd": cost_per_posting(credits, new, self._price())}
        for k, v in acc.items():
            day_set(day, k, v)
        return acc

    def read_balance(self, day, why=""):
        """FA.credits() with backoff; detects an auto-reload (remaining rose while period_end stayed the same)."""
        fc = None
        for i in range(API_ERRORS_TO_STOP):
            try:
                fc = self.credits() or {}
            except Exception as e:
                fc = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            if isinstance(fc.get("remaining"), int):
                self.api_error_streak = 0
                break
            self.api_error_streak += 1
            self.log(f"firecrawl API error {self.api_error_streak}/{API_ERRORS_TO_STOP} ({fc.get('error')}); backoff {BACKOFF[min(i, len(BACKOFF) - 1)]}s")
            if self.api_error_streak >= API_ERRORS_TO_STOP:
                return fc
            self.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])
        remaining, period_end = fc.get("remaining"), fc.get("period_end")
        last, last_pe = meta_get("last_balance"), meta_get("last_period_end")
        if isinstance(remaining, int) and isinstance(last, int) and period_end == last_pe and remaining > last:
            n = int(day_get(day, "refills", 0)) + 1
            day_set(day, "refills", n)
            meta_set("pack_size_observed", remaining - last)
            self.log(f"REFILL detected ({why}): remaining {last} -> {remaining} (+{remaining - last} credits, period_end {period_end} unchanged); refills today = {n}")
        if isinstance(remaining, int):
            meta_set("last_balance", remaining)
            meta_set("last_period_end", period_end)
        self.last_pools = {"credits": remaining, "tokens": fc.get("tokens_remaining"), "free_runs_left_today": fc.get("free_runs_left_today"),
                           "agent_runs_today": fc.get("agent_runs_today"), "plan": fc.get("plan"), "period_end": period_end, "at": R.now()}
        meta_set("last_pools", self.last_pools)
        return fc

    # -- stop rules
    def check_stop(self, day, in_flight=0, res=None):
        """First matching rule wins; sets and returns self.stop_reason (a string 'name: numbers')."""
        if self.stop_reason:
            return self.stop_reason
        cfg = self.cfg
        reason = None
        if in_flight == 0 and not self.candidates():
            reason = "suspicious: the registry snapshot has 0 fetch=firecrawl Plan-KH clinics (data layer down?)"
        elif in_flight == 0 and self.next_target(day) is None:
            rows = state_rows(day)
            reason = f"all_updated: {len(rows)} target(s) today -> " + ", ".join(f"{s} {sum(1 for r in rows if r['status'] == s)}" for s in TERMINAL)
        if not reason:
            acc = self.recount(day)
            refills = int(day_get(day, "refills", 0))
            cpp = acc["cost_per_posting_usd"]
            if refills >= cfg["max_refills"] and cpp is not None and cpp > cfg["max_usd_per_posting"]:
                reason = (f"combined_bar: refills {refills} >= {cfg['max_refills']} AND cost per posting ${cpp:.2f} > ${cfg['max_usd_per_posting']:.2f} "
                          f"({acc['credits_spent']} credits x ${self._price()} / {acc['new_postings']} new postings)")
        if not reason:
            reason = self._suspicious(day, res)
        if not reason:
            reason = self._kill_switch()
        if reason:
            self.stop_reason = reason
            day_set(day, "stop_reason", reason)
            self.log(f"STOP {reason}")
        return reason

    def _suspicious(self, day, res):
        cfg = self.cfg
        if res is not None:
            s = suspicious(res, SimpleNamespace(max_charge=cfg["max_charge_per_run"], max_tokens=cfg["max_tokens_per_run"]), self.zero_streak)
            if s:
                return f"suspicious: {s}"
        if self.consecutive_failures >= 3:
            return f"suspicious: {self.consecutive_failures} failures in a row"
        burn = burn_rate(day)
        if burn > cfg["max_credits_per_hour"]:
            return f"suspicious: burn rate {burn} credits/hour > {cfg['max_credits_per_hour']}"
        tok = self.last_pools.get("tokens")
        if isinstance(tok, int) and tok < cfg["min_tokens"]:
            return f"suspicious: tokens_remaining {tok} < {cfg['min_tokens']}"
        if self.api_error_streak >= API_ERRORS_TO_STOP:
            return f"suspicious: Firecrawl API 429/5xx {self.api_error_streak} times despite backoff"
        return None

    def _kill_switch(self):
        if stop_file().exists():
            return f"kill_switch: {stop_file()} exists"
        if ST.get_firecrawl().get("enabled", True) is False:
            return "kill_switch: firecrawl.enabled is false"
        try:
            allowed, why = self.kill_switch()
        except Exception as e:
            allowed, why = True, f"{type(e).__name__}: {e}"
            self.log(f"kill_switch() unreadable, not blocking on it: {why}")
        if not allowed:
            return f"kill_switch: {why}"
        return None

    # -- one clinic
    def _work(self, c, cap):
        verdict, why = self.precheck(c)
        if verdict == "skip":
            return {"clinic_id": c["clinic_id"], "skipped": why}
        return self.execute_one(c, cap)

    def on_result(self, day, c, res, cap):
        cid = c["clinic_id"]
        row = state_get(cid, day) or {}
        base = self._row_for(c, day)
        attempts = int(row.get("attempts") or 0)
        if res.get("skipped"):
            self.log(f"skip {cid} {c.get('name', '')[:40]}: {res['skipped']}")
            state_set(cid, day, status="skipped", last_error=res["skipped"], **base)
            return "skipped"
        attempts += 1
        credits = int(row.get("credits") or 0) + int(res.get("credits") or 0)
        tok = res.get("tokens_delta")
        try:
            tok = int(tok) if tok not in (None, "None") else row.get("tokens")
        except ValueError:
            tok = row.get("tokens")
        billable = int(res.get("credits") or 0) > 0
        common = dict(run_id=res.get("run_id"), cap=cap, credits=credits, tokens=tok, rows=int(res.get("rows") or 0), new=int(res.get("new") or 0), attempts=attempts, **base)
        self.last_result = {k: res.get(k) for k in ("clinic_id", "name", "run_id", "status", "rows", "new", "credits", "tokens_delta", "error", "secs")}
        meta_set("last_run", {**self.last_result, "at": R.now()})
        if res.get("error"):
            self.consecutive_failures += 1
            err = (res.get("fail_text") or res.get("gate") or res["error"])[:400]
            if res.get("max_credits_hit") and not billable and attempts == 1 and cap < self.cfg["escalate_cap"]:
                self.log(f"{cid} {c.get('name', '')[:40]}: unbilled 'Agent reached max credits' at cap {cap} -> one retry at {self.cfg['escalate_cap']}")
                state_set(cid, day, status="pending", last_error=err, **{**common, "cap": self.cfg["escalate_cap"]})
                return "retry"
            else:
                why = "billed failure" if billable else ("second max-credits failure" if res.get("max_credits_hit") else "failure")
                self.log(f"{cid} {c.get('name', '')[:40]}: {why} -> needs_manual for today ({err[:160]})")
                state_set(cid, day, status="needs_manual", last_error=err, **common)
            return "needs_manual"
        self.consecutive_failures = 0
        self.zero_streak = self.zero_streak + 1 if billable and not res.get("rows") else (0 if res.get("rows") else self.zero_streak)
        state_set(cid, day, status="done", last_error=None, **common)
        self.log(json.dumps({k: res.get(k) for k in ("clinic_id", "run_id", "status", "rows", "new", "credits", "tokens_delta", "secs")}))
        return "done"

    # -- one pass
    def import_runs(self, day):
        """Firecrawl runs of today that were not made through this state (tools/fc_hunt.py 'hunt', or a hunter
        pass before a schema reset) become hunt_state rows, so a clinic that already had its run today is never
        run again. Per clinic: any done run -> done (its rows/new), else needs_manual; credits summed over all."""
        have = {r["clinic_id"] for r in state_rows(day)}
        by = {c["clinic_id"]: c for c in self.candidates()}
        with R._lock, R.db() as c:
            runs = c.execute("select run_id, value, status, n_rows, n_new, credits_used, error, params from crawl_runs where mode='firecrawl' and scope='clinic' "
                             "and trigger in ('hunt','hunter') and finished_at >= ? and status in ('done','failed') order by run_id", (day,)).fetchall()
        per = {}
        for r in runs:
            if r["value"] in have or r["value"] not in by:
                continue
            per.setdefault(r["value"], []).append(dict(r))
        for cid, rs in per.items():
            best = next((r for r in reversed(rs) if r["status"] == "done"), rs[-1])
            try:
                cap = int(json.loads(best["params"] or "{}").get("max_credits") or 0) or None
            except Exception:
                cap = None
            state_set(cid, day, status="done" if best["status"] == "done" else "needs_manual", run_id=best["run_id"], cap=cap,
                      credits=sum(int(r["credits_used"] or 0) for r in rs), rows=int(best["n_rows"] or 0), new=int(best["n_new"] or 0),
                      attempts=len(rs), last_error=best["error"], **self._row_for(by[cid], day))
            self.log(f"{cid}: imported today's run(s) {[r['run_id'] for r in rs]} into hunt_state as {'done' if best['status'] == 'done' else 'needs_manual'}")

    def resume(self, day):
        """Rows left 'running' by a dead process: the run may have been billed, so no retry today."""
        self.import_runs(day)
        for r in state_rows(day):
            if r["status"] == "running":
                self.log(f"{r['clinic_id']}: was running when the previous process died -> failed for today")
                state_set(r["clinic_id"], day, status="failed", last_error="process restarted while running")
        if not day_get(day, "started_at"):
            day_set(day, "started_at", R.now())
        self.stop_reason = None
        day_set(day, "stop_reason", None)

    def run_once(self, day=None):
        day = day or today()
        self.resume(day)
        meta_set("running", {"pid": os.getpid(), "day": day, "since": R.now()})
        conc = max(1, int(self.cfg["concurrency"]))
        self.log(f"hunter pass {day}: {self.pending_count(day)} pending of {len(self.candidates())} targets, concurrency {conc}, cap {self.cfg['cap']}/{self.cfg['escalate_cap']}, rules {json.dumps(rules(self.cfg))}")
        try:
            with ThreadPoolExecutor(max_workers=conc, thread_name_prefix="hunter") as ex:
                futures = {}
                while True:
                    while len(futures) < conc and not self.stop_reason:
                        self.read_balance(day, "before submission")
                        if self.check_stop(day, in_flight=len(futures)):
                            break
                        c = self.next_target(day, in_flight=[x[0] for x in futures.values()])
                        if c is None:
                            break
                        row = state_get(c["clinic_id"], day) or {}
                        cap = int(row.get("cap") or self.cfg["cap"]) if row.get("status") == "pending" and row.get("attempts") else int(self.cfg["cap"])
                        state_set(c["clinic_id"], day, status="running", cap=cap, **self._row_for(c, day))
                        self.log(f"submit {c['clinic_id']} {c.get('name', '')[:40]} (cap {cap}, {c.get('beds')} beds)")
                        futures[ex.submit(self._work, c, cap)] = (c, cap)
                    if not futures:
                        if not self.stop_reason:
                            self.check_stop(day, in_flight=0)
                        break
                    done = next(as_completed(list(futures)))
                    c, cap = futures.pop(done)
                    try:
                        res = done.result()
                    except Exception as e:
                        res = {"clinic_id": c["clinic_id"], "name": c.get("name"), "run_id": None, "status": "exception", "rows": 0, "new": 0, "credits": 0,
                               "tokens_delta": None, "error": f"{type(e).__name__}: {str(e)[:160]}", "disagree": False, "gate": "", "notes": "", "secs": 0, "max_credits_hit": False}
                    action = self.on_result(day, c, res, cap)
                    self.read_balance(day, f"after run {res.get('run_id')}")
                    # a retryable (unbilled max-credits) failure is the cap ladder at work, not a suspicious run
                    self.check_stop(day, in_flight=len(futures), res=res if action in ("done", "needs_manual") else None)
                    if self.stop_reason and futures:
                        self.log(f"letting {len(futures)} in-flight run(s) finish, submitting no more")
        finally:
            self.recount(day)
            meta_set("running", None)
        return self.stop_reason

    def daemon(self, poll=60):
        """Forever: a pass whenever enabled and not stopped; after all_updated sleep until the next UTC day (the
        free runs reset), after any other stop wait until POST /api/hunter/start clears it."""
        self.log(f"hunter daemon pid {os.getpid()} (enabled={is_enabled()})")
        while True:
            day = today()
            reason = day_get(day, "stop_reason")
            if is_enabled() and not reason and not stop_file().exists():
                self.cfg = ST.get_hunter()
                try:
                    reason = self.run_once(day)
                except Exception as e:
                    reason = f"suspicious: hunter crashed: {type(e).__name__}: {str(e)[:200]}"
                    self.log(f"STOP {reason}")
                if reason:
                    day_set(day, "stop_reason", reason)       # run_once already did; belt and braces for the poll below
            self.sleep(poll)


# --- module-level helpers ---------------------------------------------------------------------
def rules(cfg=None):
    cfg = cfg or ST.get_hunter()
    return {k: cfg[k] for k in ("concurrency", "cap", "escalate_cap", "max_refills", "max_usd_per_posting", "max_credits_per_hour", "min_tokens",
                                "max_charge_per_run", "max_tokens_per_run")} | {"kill_switch_pct": ST.get_firecrawl().get("kill_switch_pct"),
                                                                                  "usd_per_credit": float(ST.get_firecrawl().get("eur_per_credit") or 0.0053)}


def cost_per_posting(credits, new, price):
    """USD per unique new posting; None when nothing was spent, inf (JSON: 1e12) when credits went out for nothing."""
    if not credits:
        return 0.0
    if not new:
        return float("inf")
    return round(credits * price / new, 4)


def burn_rate(day, hours=1.0):
    """Credits the hunter's runs charged in the last `hours` (hunt_state rows updated in that window)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with R._lock, R.db() as c:
        r = c.execute("select coalesce(sum(credits),0) from hunt_state where day=? and updated_at>=? and status in ('done','needs_manual','failed')", (day, since)).fetchone()
    return int(r[0]) / hours


def _json_safe(v):
    return 1e12 if isinstance(v, float) and v == float("inf") else v


def status(day=None, live_pools=False):
    """The GET /api/hunter/status document."""
    day = day or today()
    cfg = ST.get_hunter()
    h = Hunter(cfg)
    try:
        cands = h.candidates()
        h.import_runs(day)                       # today's fc_hunt / hunter runs show up even before a pass ran
    except Exception:
        cands = []
    rows = state_rows(day)
    by = {r["clinic_id"]: r for r in rows}
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    pending = sum(1 for c in cands if c["clinic_id"] not in by or by[c["clinic_id"]]["status"] == "pending")
    running = meta_get("running")
    alive = bool(running) and lock_held()
    acc = h.recount(day)
    pools_ = meta_get("last_pools") or {}
    stale = True
    try:
        stale = (datetime.now(timezone.utc) - datetime.fromisoformat(pools_["at"])).total_seconds() > 300
    except Exception:
        pass
    if live_pools or stale:
        try:
            fc = FA.credits(historical=False, timeout=10) or {}
            pools_ = {"credits": fc.get("remaining"), "tokens": fc.get("tokens_remaining"), "free_runs_left_today": fc.get("free_runs_left_today"),
                      "agent_runs_today": fc.get("agent_runs_today"), "plan": fc.get("plan"), "period_end": fc.get("period_end"), "at": R.now()}
        except Exception:
            pass
    else:
        pools_ = {**pools_, "free_runs_left_today": FA.free_runs_left_today(), "agent_runs_today": FA.agent_runs_today()}
    return {"enabled": is_enabled(), "running": alive, "daemon_alive": lock_held(), "day": day,
            "targets": {"total": len(cands), "pending": pending, **{s: counts[s] for s in TERMINAL}, "running": counts["running"]},
            "today": {"runs": acc["runs"], "credits": acc["credits_spent"], "tokens": acc["tokens_spent"], "new_postings": acc["new_postings"],
                      "cost_per_posting_usd": _json_safe(acc["cost_per_posting_usd"]), "refills": int(day_get(day, "refills", 0)),
                      "pack_size_observed": meta_get("pack_size_observed"), "started_at": day_get(day, "started_at")},
            "pools": {k: pools_.get(k) for k in ("credits", "tokens", "free_runs_left_today", "agent_runs_today", "plan", "period_end", "at")},
            "stop_reason": day_get(day, "stop_reason"), "stop_file": stop_file().exists(), "last_run": meta_get("last_run"),
            "rules": rules(cfg), "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--daemon", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    R.init()
    if a.status:
        print(json.dumps(status(), ensure_ascii=False, indent=1, default=str)); return 0
    if a.dry_run:                                # log lines go to stderr so stdout stays one JSON document
        print(json.dumps(Hunter(log=lambda *p: print(*p, file=sys.stderr)).dry_run(), ensure_ascii=False, indent=1, default=str)); return 0
    lock = acquire_lock()
    if lock is None:
        print(f"another hunter holds {lock_path()}; exiting", file=sys.stderr); return 2
    log = lambda *p: print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *p, flush=True)
    h = Hunter(log=log)
    if a.daemon:
        h.daemon()
    else:
        reason = h.run_once()
        print("stop:", reason)
        print("status:", json.dumps(status(), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
