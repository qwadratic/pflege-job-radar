"""Firecrawl `/agent` as a source (source_id 25, kind employer_ats, precedence 2).

Used for clinics that no adapter can reach: bot-walled hosts (Helios), JS-only boards (d.vinci, Oracle),
sites without a vendor fingerprint. The agent gets a prompt + a JSON schema it MUST answer with, and a
hard `maxCredits` cap — credits are scarce, so nothing here runs without a cap.

Two agents:
  run_jobs_agent(clinic)    -> nursing vacancies at that site           -> inbox rows (collector "firecrawl-agent")
  run_career_agent(clinic)  -> where the career portal is, which ATS, which filters/categories the board offers

API (v2, verified 2026-09-06/08): POST /v2/agent {prompt, urls?, schema, model, maxCredits} -> {id, status}
                                 GET  /v2/agent/{id}   -> {status: processing|completed|failed, data, creditsUsed}
                                 GET  /v2/team/credit-usage -> {data: {remainingCredits, planCredits, billingPeriodStart/End}}
                                 GET  /v2/team/token-usage  -> {data: {remainingTokens, planTokens, billingPeriodStart/End}}
                                 GET  /v2/team/credit-usage/historical -> {periods: [{startDate, endDate|null, creditsUsed}]}
                                 GET  /v2/team/token-usage/historical  -> {periods: [{startDate, endDate|null, tokensUsed}]}

Cost model (Firecrawl agent docs, verified 2026-09-08): "All users receive 5 free daily runs, which can be used from
either the playground or the API"; after that "additional usage is billed based on credit consumption", dynamic,
"most agent runs consume a few hundred credits", capped by maxCredits. So a run's API-reported creditsUsed of 0
means "inside today's free allowance", not "this run is cheap": run_agent() therefore also records the before/after
delta of the account's remaining credits (authoritative) and says which run of the day it was. The token pool is
the Extract pool (the Agent bills credits); it is surfaced by credits() for completeness, never spent here.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

API = os.environ.get("FIRECRAWL_API_BASE", "https://api.firecrawl.dev/v2")
MODEL = os.environ.get("FIRECRAWL_AGENT_MODEL", "spark-2")
DEFAULT_MAX_CREDITS = int(os.environ.get("FIRECRAWL_MAX_CREDITS", "40"))
FREE_RUNS_PER_DAY = int(os.environ.get("FIRECRAWL_FREE_RUNS_PER_DAY", "5"))   # Firecrawl's documented free daily agent runs (see module docstring)
COLLECTOR = "firecrawl-agent"
KNOWN_VENDORS = ["softgarden", "typo3_jobs", "bite", "rexx", "umantis", "mein-check-in", "dvinci", "pi_asp", "concludis",
                 "oracle", "personio", "smartrecruiters", "talention", "helix", "workday", "sap_successfactors", "other", "unknown"]

SENIORITY = ("leitung", "fach", "fachkraft", "experte", "unknown")

JOBS_SCHEMA = {
    "type": "object",
    "properties": {
        "portal_url": {"type": "string", "description": "URL of the job board / listing page the jobs were read from"},
        "jobs": {
            "type": "array",
            "description": "Every open, certified-nursing (Pflegedienst) vacancy at this hospital site -- maximise count, do not stop early",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Job title exactly as advertised, incl. (m/w/d)"},
                    "url": {"type": "string", "description": "Direct URL of this job advertisement (detail page), not the list page"},
                    "city": {"type": "string", "description": "Town of the workplace"},
                    "plz": {"type": "string", "description": "German postal code of the workplace, if shown"},
                    "department": {"type": "string", "description": "Ward / department / Fachbereich AS THE BOARD ITSELF LABELS IT, if the board has a category/filter for it (e.g. Intensivstation, OP, Notaufnahme); empty if the board has no such label"},
                    "seniority": {"type": "string", "enum": list(SENIORITY),
                                  "description": "leitung=Stationsleitung/Pflegedienstleitung/PDL; fach=Fachpflege specialist (Intensiv/Anästhesie/OP); "
                                                  "fachkraft=general Pflegefachkraft/Gesundheits- und Krankenpfleger; experte=APN/Pflegeexperte; unknown=cannot tell"},
                    "employment_type": {"type": "string", "description": "Vollzeit, Teilzeit, Minijob, or a combination"},
                    "contract": {"type": "string", "description": "unbefristet or befristet, if stated"},
                    "start_date": {"type": "string", "description": "Earliest start (ISO date or 'ab sofort')"},
                    "published": {"type": "string", "description": "Publication date of the ad (ISO date) if visible"},
                    "description": {"type": "string", "description": "Short summary of tasks and offer, max ~600 characters"},
                    "requirements": {"type": "string", "description": "Required qualification / experience, one or two sentences"},
                    "tariff_or_salary": {"type": "string", "description": "Tariff (TVöD, TV-L, AVR ...) or pay grade / salary if stated"},
                    "contact_email": {"type": "string", "description": "Application or contact e-mail if shown"},
                },
                "required": ["title", "url"],
            },
        },
        "notes": {"type": "string", "description": "Anything odd: login wall, jobs only as PDF, pagination you could not follow, non-Bavarian sites skipped"},
        "blocked_reason": {"type": "string", "description": "Empty if jobs were found. Otherwise WHY jobs is empty: login wall, bot block, board offline, no nursing roles listed, etc."},
    },
    "required": ["jobs"],
}

VERDICTS = ("board_found", "not_a_board", "not_clinic_specific")

CAREER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS),
                    "description": "board_found = a real job board exists and belongs to this hospital (or its own operator/group, filterable to it); "
                                    "not_a_board = no vacancy board reachable at all from the given URL(s); "
                                    "not_clinic_specific = what was found is a shared/group board with no way to filter to this hospital, or the URL belongs to an unrelated entity"},
        "careers_url": {"type": "string", "description": "The hospital's own careers / Karriere / Stellenangebote page on its website"},
        "portal_url": {"type": "string", "description": "The actual job board URL where vacancies are listed (may be an external ATS domain); the entry point a page-by-page fetcher should start from"},
        "ats_vendor": {"type": "string", "enum": KNOWN_VENDORS, "description": "Applicant-tracking vendor behind the board, judged from URLs/markup (softgarden.io, jobs.b-ite.com, rexx-systems, umantis.com, mein-check-in.de, dvinci, pi-asp.de, concludis, oracle cloud, personio, smartrecruiters, talention, helixjobs)"},
        "listing_technology": {"type": "string", "enum": ["html", "js", "pdf", "iframe", "unknown"], "description": "How the list renders: plain HTML links, JavaScript app, PDF flyers, embedded iframe"},
        "filters": {"type": "array", "description": "Filter controls the board offers, with ALL their selectable values",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}}, "required": ["name", "values"]}},
        "categories": {"type": "array", "items": {"type": "string"}, "description": "Job categories / Berufsgruppen the board groups vacancies into"},
        "visible_job_count": {"type": "integer", "description": "Total vacancies listed (all professions)"},
        "nursing_job_count": {"type": "integer", "description": "Vacancies that are nursing (Pflege) roles"},
        "has_rss_or_json": {"type": "boolean", "description": "Whether a feed / JSON / XML endpoint for the jobs exists"},
        "crawl_strategy": {"type": "string", "description": "Only when verdict=board_found: concrete, step-by-step instructions a PLAIN page-by-page fetcher "
                                                              "(no JavaScript, no reasoning) can follow to pull every vacancy -- the exact URL(s) to start from, how pagination "
                                                              "works (query param, 'mehr laden' button target, numbered pages, infinite scroll's underlying endpoint), whether "
                                                              "listing links go straight to job detail pages or need another hop, any JSON/XML/RSS endpoint cheaper than HTML"},
        "alternative_locations": {"type": "array", "items": {"type": "string"},
                                   "description": "Only when verdict != board_found: concrete places to check instead (a parent operator's own portal, a regional/group HR site, "
                                                   "a different subdomain, or a plain note that no online board exists at all) -- as many as you can find"},
        "notes": {"type": "string", "description": "Login walls, cookie walls, bot protection, pagination pattern, anything an engineer writing a crawler needs"},
    },
    "required": ["verdict", "careers_url", "ats_vendor", "listing_technology"],
}


_TEMPLATE_CLINIC = {"name": "<hospital name>", "town": "<town>", "operator": None, "website": None}


def render_prompts(clinic=None):
    """GET /api/firecrawl/prompts: the two live prompt templates + schemas, rendered for a real clinic
    when given one, else with placeholder text -- so an operator can see exactly what Firecrawl is
    asked to do without reading firecrawl_agent.py. Read-only, no network, no credits."""
    c = clinic or _TEMPLATE_CLINIC
    return {"model": MODEL, "default_max_credits": DEFAULT_MAX_CREDITS,
            "jobs": {"prompt": _jobs_prompt(c), "schema": JOBS_SCHEMA},
            "career": {"prompt": _career_prompt(c), "schema": CAREER_SCHEMA}}


def _jobs_prompt(clinic):
    name = clinic.get("name")
    town = clinic.get("town") or "Bavaria"
    op = clinic.get("operator") or "unknown"
    return (
        f"GOAL: find MAX number of open Pflegedienst (nursing dept) jobs at ONE hospital: \"{name}\", {town}, Bavaria, Germany "
        f"(operator: {op}). Also capture seniority per job. Not a survey -- a full count. Missed job = failure.\n"
        "HARD RULES, no exceptions:\n"
        "1. ONE hospital/operator's OWN board. Start at given URL(s): careers page + any board it links to (softgarden, B-ITE, rexx, "
        "umantis, mein-check-in, d.vinci, Personio, ...). If that board is shared across UNRELATED operators (a nationwide vendor "
        "board), keep only this operator's listings. If it is this operator's OWN dedicated portal, keep every nursing listing on it "
        "even for an affiliated site/service (rehab, senior care, mobile/SAPV, day clinic) under the SAME operator -- a job is not "
        "out of scope just because its title or department does not repeat the hospital's exact name.\n"
        "2. Paginate FULLY. Follow next/page2/'mehr laden' until board end. Do not stop at page 1.\n"
        "3. INCLUDE only certified nursing staff: Pflegefachkraft, Gesundheits- und Krankenpfleger(in), Fachpflege "
        "(Intensiv, Anästhesie, OP), Praxisanleitung, Stationsleitung / Pflegedienstleitung / PDL, Hebamme, OTA/ATA, "
        "APN / Pflegeexperte.\n"
        "4. EXCLUDE always: Pflegehelfer, Pflegefachhelfer, Assistenz; Ausbildung/Azubi/Schüler; Praktikum; Werkstudent; "
        "FSJ/BFD; physicians; MFA; therapists; admin; logistics; any job outside Bavaria.\n"
        "5. Each job = own direct detail-page URL. Never the list-page URL. Never a URL you did not see.\n"
        "6. Never invent a job. Board empty or blocked (login wall, bot block, offline, no nursing listed) -> jobs=[] "
        "and say why in blocked_reason.\n"
        "PER JOB, fill: department = board's own category label if it has one (else empty); seniority = one of "
        "leitung|fach|fachkraft|experte|unknown (leitung=Stationsleitung/PDL, fach=Fachpflege specialist e.g. "
        "Intensiv/Anästhesie/OP, fachkraft=general Pflegefachkraft/GKP, experte=APN/Pflegeexperte, unknown=unclear).\n"
        "Answer strictly in the schema."
    )


def _career_prompt(clinic):
    return (f"Study the hospital \"{clinic.get('name')}\" in {clinic.get('town') or 'Bavaria'}, Germany "
            f"(operator: {clinic.get('operator') or 'unknown'}; website: {clinic.get('website') or 'unknown'}) to PLAN a crawl -- "
            "do not extract jobs yourself.\n"
            "GOAL: find the OPTIMAL crawl strategy for a plain page-by-page fetcher (no JavaScript, no reasoning) to later pull "
            "EVERY open vacancy from this hospital's job board.\n"
            "Decide which of three cases applies and set verdict accordingly:\n"
            "1. board_found -- a real job board exists and belongs to this hospital (or its own operator/group, filterable to this "
            "hospital's own listings). Fill careers_url, portal_url, ats_vendor, listing_technology, filters, categories, "
            "visible_job_count, nursing_job_count, has_rss_or_json, and crawl_strategy: concrete, step-by-step instructions a dumb "
            "per-page fetcher can follow -- the exact URL(s) to start from, how pagination works (query param, 'mehr laden' button "
            "target, numbered pages, infinite scroll's underlying endpoint), whether listing links go straight to job detail pages or "
            "need another hop, and any JSON/XML/RSS endpoint that is cheaper to read than the HTML.\n"
            "2. not_a_board -- no vacancy board could be found anywhere reachable from the given URL(s) (dead link, no careers "
            "section, jobs only as PDF flyers or a phone number). Leave portal_url empty and fill alternative_locations with "
            "concrete places to check instead (a parent operator's own portal, a regional/group HR site, a different subdomain, a "
            "print/newspaper-only note) -- as many as you can find.\n"
            "3. not_clinic_specific -- what you found is not this hospital's own board: a nationwide/group portal with no way to "
            "filter to this hospital, or the given URL belongs to an unrelated entity. Explain in notes and, if you can tell where "
            "this hospital's OWN listings might be instead, fill alternative_locations.\n"
            "Also identify the ATS vendor from URLs/markup, and note any login/cookie/bot wall or anything else an engineer writing "
            "the fetcher needs to know. Answer strictly in the schema; use 'unknown' where you cannot tell.")


class AgentFailed(RuntimeError):
    """Firecrawl agent run ended in status=='failed' or timed out. Carries credits_used so the caller
    can still charge the local ledger -- over-charging it is safe, under-charging is what lets the
    weekly budget check (_budget_left) silently drift away from what the account actually spent."""

    def __init__(self, message, credits_used, job_id=None, credits_delta=None, tokens_delta=None):
        super().__init__(message)
        self.credits_used = credits_used
        self.job_id = job_id                    # lets the caller update the ledger row on_submit created
        self.credits_delta = credits_delta      # measured balance movement, or None when it could not be read
        self.tokens_delta = tokens_delta        # same for the Extract token pool


def _headers():
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _get_json(path, session=None, timeout=30):
    r = (session or requests).get(API + path, headers=_headers(), timeout=timeout)
    j = r.json()
    return j if isinstance(j, dict) else {}


def _remaining(path, field, session=None, timeout=20):
    """One balance read (int) or None; never raises -- it only feeds the before/after deltas."""
    try:
        d = _get_json(path, session, timeout).get("data") or {}
        v = d.get(field)
        return v if isinstance(v, int) else None
    except Exception:
        return None


def _balances(session=None, timeout=20):
    """{'credits': int|None, 'tokens': int|None} -- both pools, read together so a run's movement in each is
    attributable to that run (the Agent bills credits; the 2026-09-08 batch also moved Extract tokens)."""
    return {"credits": _remaining("/team/credit-usage", "remainingCredits", session, timeout),
            "tokens": _remaining("/team/token-usage", "remainingTokens", session, timeout)}


def _delta(before, after):
    """before - after when both were read and the balance did not go UP (period reset / top-up in between), else None."""
    return (before - after) if isinstance(before, int) and isinstance(after, int) and after <= before else None


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _current_period(periods):
    """/historical answers [{startDate, endDate|null, creditsUsed|tokensUsed}, ...] (calendar months, the open one has
    endDate null) -> the period containing now (UTC), else the last one listed."""
    now = datetime.now(timezone.utc)
    periods = [p for p in (periods or []) if isinstance(p, dict)]
    for p in periods:
        st, en = _iso(p.get("startDate")), _iso(p.get("endDate"))
        if st and st <= now and (en is None or now < en):
            return p
    return periods[-1] if periods else None


def agent_runs_today():
    """Accepted agent submissions today (UTC) according to the app's local ledger (app/runs.py firecrawl_usage
    rows of kind jobs|career with a job id). None when the ledger is not available (library use outside the app),
    which callers must treat as 'unknown, assume billable' -- never as 0."""
    try:
        from app import runs as R
        return R.agent_runs_today()
    except Exception:
        return None


def tokens_spent_by_app(days=None):
    """Sum of the Extract-token deltas the app's own agent runs recorded (app/runs.py tokens_total); None outside the app."""
    try:
        from app import runs as R
        return R.tokens_total(days=days)
    except Exception:
        return None


def free_runs_left_today():
    n = agent_runs_today()
    return max(0, FREE_RUNS_PER_DAY - n) if isinstance(n, int) else None


def credits(session=None, timeout=30, tokens=True, historical=True):
    """Account balance, both pools, and the free-run allowance. Every block is tolerant of its own failure
    (keys stay None, message in error / tokens_error / hist_error) so one dead endpoint never hides the others.

      remaining, plan, period_start, period_end        GET /team/credit-usage   -- credits, what the Agent bills past the free runs
      tokens_remaining, tokens_plan                    GET /team/token-usage    -- the Extract token pool (surfaced, not spent here)
      credits_used_hist, tokens_used_hist,             GET /team/credit-usage/historical + /team/token-usage/historical,
      hist_period_start, hist_period_end                 the current (calendar-month) period; skipped when historical=False
      free_runs_per_day, agent_runs_today, free_runs_left_today   local ledger, UTC day (agent_runs_today() above)
      spent_tokens_by_app, spent_tokens_by_app_7d      local ledger: Extract tokens the app's own agent runs moved (tokens_total())
    """
    out = {"remaining": None, "plan": None, "period_start": None, "period_end": None}
    try:
        d = _get_json("/team/credit-usage", session, timeout).get("data") or {}
        out.update({"remaining": d.get("remainingCredits", d.get("remaining_credits")), "plan": d.get("planCredits", d.get("plan_credits")),
                    "period_start": d.get("billingPeriodStart", d.get("billing_period_start")), "period_end": d.get("billingPeriodEnd", d.get("billing_period_end"))})
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    if tokens:
        out.update({"tokens_remaining": None, "tokens_plan": None})
        try:
            d = _get_json("/team/token-usage", session, timeout).get("data") or {}
            out.update({"tokens_remaining": d.get("remainingTokens", d.get("remaining_tokens")), "tokens_plan": d.get("planTokens", d.get("plan_tokens"))})
        except Exception as e:
            out["tokens_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    if historical:
        out.update({"credits_used_hist": None, "tokens_used_hist": None, "hist_period_start": None, "hist_period_end": None})
        errs = []
        for path, key, field in (("/team/credit-usage/historical", "credits_used_hist", "creditsUsed"),
                                 ("/team/token-usage/historical", "tokens_used_hist", "tokensUsed")):
            try:
                p = _current_period(_get_json(path, session, timeout).get("periods"))
                if p:
                    out[key] = p.get(field, p.get("totalCredits" if field == "creditsUsed" else "totalTokens"))
                    out["hist_period_start"] = out["hist_period_start"] or p.get("startDate")
                    out["hist_period_end"] = out["hist_period_end"] or p.get("endDate")
            except Exception as e:
                errs.append(f"{path}: {type(e).__name__}: {str(e)[:100]}")
        if errs:
            out["hist_error"] = "; ".join(errs)
    n = agent_runs_today()
    out.update({"free_runs_per_day": FREE_RUNS_PER_DAY, "agent_runs_today": n,
                "free_runs_left_today": max(0, FREE_RUNS_PER_DAY - n) if isinstance(n, int) else None,
                "spent_tokens_by_app": tokens_spent_by_app(), "spent_tokens_by_app_7d": tokens_spent_by_app(days=7)})
    return out


def build_request(prompt, schema, urls=None, max_credits=DEFAULT_MAX_CREDITS, model=MODEL, webhook=None):
    body = {"prompt": prompt, "schema": schema, "model": model, "maxCredits": int(max_credits)}
    if urls:
        body["urls"] = [u for u in urls if u]
    if webhook:
        body["webhook"] = webhook
    return body


def _run_label(run_no):
    if not isinstance(run_no, int):
        return "run number today unknown (ledger unavailable) -- treat as billable"
    if run_no <= FREE_RUNS_PER_DAY:
        return f"free daily run {run_no}/{FREE_RUNS_PER_DAY}"
    return f"billable run (#{run_no} today, {FREE_RUNS_PER_DAY} free runs already used)"


def _settle(j, api_used, before, session, run_no, job_id, log):
    """Measure the run's real cost as the balance deltas (credits and Extract tokens), attach them to the raw
    answer as j['_cost'] and return the credits to charge the local ledger: the API's per-job creditsUsed when it
    reports a charge (balance deltas are shared by concurrent jobs), else the measured delta (0 inside the free
    allowance), else 0."""
    after = _balances(session)
    delta = _delta(before["credits"], after["credits"])
    tdelta = _delta(before["tokens"], after["tokens"])
    # The API's creditsUsed is per job; the balance delta is shared by every job in flight at the same time (three
    # concurrent hunter runs once booked 174 credits on one run and 0 on the other two). Prefer the per-job figure
    # whenever the API reports a charge; fall back to the delta only inside the free allowance / when the API says 0.
    charged = int(api_used) if api_used not in (None, "", 0, "0") and int(api_used) > 0 else (delta if delta is not None else 0)
    j["_cost"] = {"job_id": job_id, "api_credits_used": int(api_used or 0),
                  "credits_before": before["credits"], "credits_after": after["credits"], "credits_delta": delta,
                  "tokens_before": before["tokens"], "tokens_after": after["tokens"], "tokens_delta": tdelta,
                  "charged": charged, "run_number_today": run_no,
                  "free_run": (run_no <= FREE_RUNS_PER_DAY) if isinstance(run_no, int) else None}
    unk = lambda v: "unknown" if v is None else v  # noqa: E731
    api_charged = api_used not in (None, "", 0, "0") and int(api_used) > 0
    # DISAGREE only matters when delta is what gets charged (api_used <= 0, the free-run branch above) -- that's
    # the only case a wrong delta could actually mischarge. When the API reports a real per-job charge, `charged`
    # already uses THAT, ignoring delta entirely, so a gap is expected noise (three concurrent runs share the same
    # balance reads: 2026-09-08 run 55 measured a 24-credit delta for a 20-credit job, the other 4 being a sibling
    # run's charge landing inside this job's before/after window) and must not flag or trip the suspicious check.
    log(f"firecrawl agent job {job_id or '-'}: creditsUsed {int(api_used or 0)} (API); credits {before['credits']} -> {after['credits']}, "
        f"tokens {before['tokens']} -> {after['tokens']}: credits delta {unk(delta)}, tokens delta {unk(tdelta)}; charging {charged} -- {_run_label(run_no)}"
        + (" -- API creditsUsed and balance delta DISAGREE" if not api_charged and delta is not None and delta != int(api_used or 0) else ""))
    return charged


def run_agent(prompt, schema, urls=None, max_credits=DEFAULT_MAX_CREDITS, poll=5, timeout=900, log=print, session=None,
              webhook=None, check_webhook=None, on_submit=None):
    """Submit an agent job and poll it. Returns (data, credits_used, raw). Raises on failure.

    credits_used is the amount to charge the local ledger: the before/after delta of GET /team/credit-usage
    when both reads worked (authoritative), else the API's creditsUsed. raw['_cost'] carries both numbers,
    the credit AND Extract-token balance snapshots with their deltas (tokens_before/after/delta -- the token
    pool moved on 2026-09-08's batch and only a per-run read can say which run did it), the run's number in
    today's UTC count and whether that is inside Firecrawl's FREE_RUNS_PER_DAY free allowance ("free daily
    run N/5" vs "billable run" in the log).
    webhook: optional {url, headers, metadata, events} passed straight to the API (see docs/firecrawl.md).
    check_webhook: optional callable(job_id) -> agent-shaped dict ({'status', 'data', 'creditsUsed', ...}) or None,
    checked before every poll so a webhook event that already arrived stops the polling loop early.
    on_submit: optional callable(job_id), called as soon as the API accepted the job -- the app uses it to
    write the ledger row that makes agent_runs_today() count this submission even if the process dies."""
    if not max_credits or int(max_credits) <= 0:
        raise ValueError("max_credits must be a positive cap")
    s = session or requests.Session()
    body = build_request(prompt, schema, urls, max_credits, webhook=webhook)
    before = _balances(s)
    n_today = agent_runs_today()
    run_no = (n_today + 1) if isinstance(n_today, int) else None
    r = s.post(API + "/agent", headers=_headers(), json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"firecrawl agent submit {r.status_code}: {r.text[:300]}")
    j = r.json()
    job_id = j.get("id")
    if job_id and on_submit:
        try:
            on_submit(job_id)
        except Exception as e:
            log(f"firecrawl agent on_submit failed: {type(e).__name__}: {str(e)[:120]}")
    if j.get("status") == "completed" and "data" in j:                # synchronous answer
        log(f"firecrawl agent job {job_id or '-'} answered synchronously (maxCredits {max_credits}): {_run_label(run_no)}")
        return j.get("data") or {}, _settle(j, j.get("creditsUsed"), before, s, run_no, job_id, log), j
    if not job_id:
        raise RuntimeError(f"firecrawl agent: no job id in {str(j)[:200]}")
    log(f"firecrawl agent job {job_id} submitted (maxCredits {max_credits}): {_run_label(run_no)}")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        hit = check_webhook(job_id) if check_webhook else None
        if hit is not None:
            log(f"firecrawl agent job {job_id}: webhook event arrived, stopping poll")
            j = hit
        else:
            g = s.get(f"{API}/agent/{job_id}", headers=_headers(), timeout=60)
            j = g.json() if g.status_code < 500 else {}
        st = j.get("status")
        if st == "completed":
            return j.get("data") or {}, _settle(j, j.get("creditsUsed"), before, s, run_no, job_id, log), j
        if st == "failed":
            used = j.get("creditsUsed")
            after = _balances(s)
            raise AgentFailed(f"firecrawl agent failed: {str(j.get('error') or j)[:300]}",
                               credits_used=int(used) if used is not None else int(max_credits), job_id=job_id,
                               credits_delta=_delta(before["credits"], after["credits"]), tokens_delta=_delta(before["tokens"], after["tokens"]))
    raise AgentFailed(f"firecrawl agent {job_id} still processing after {timeout}s", credits_used=int(max_credits), job_id=job_id)


def _host(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _employment(s):
    s = (s or "").lower()
    return [t for t, k in (("FULL_TIME", "vollzeit"), ("PART_TIME", "teilzeit"), ("MINI", "minijob"), ("FULL_TIME", "full")) if k in s]


def jobs_to_inbox_rows(data, clinic):
    """Agent answer -> inbox-shaped rows (kind jobposting) the existing loader understands.
    The inbox processor maps collector 'firecrawl*' to source 25 and re-classifies the title, so trainee
    rows the agent let through are still refused there."""
    rows, seen = [], set()
    town = clinic.get("town")
    for j in (data or {}).get("jobs") or []:
        url = (j.get("url") or "").strip()
        title = (j.get("title") or "").strip()
        if not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        agent_city = (j.get("city") or "").strip() or None
        city = agent_city or town
        plz = re.sub(r"\D", "", j.get("plz") or "")[:5] or None
        desc_parts = [j.get("description"), ("Anforderungen: " + j["requirements"]) if j.get("requirements") else None,
                      ("Vergütung: " + j["tariff_or_salary"]) if j.get("tariff_or_salary") else None,
                      ("Beschäftigung: " + j["employment_type"]) if j.get("employment_type") else None,
                      ("Vertrag: " + j["contract"]) if j.get("contract") else None,
                      ("Kontakt: " + j["contact_email"]) if j.get("contact_email") else None]
        pub = (j.get("published") or "")[:10]
        seniority = (j.get("seniority") or "unknown").strip().lower()
        payload = {"title": title, "org": clinic.get("name"), "org_source": "seed",  # JOBS_SCHEMA has no employer field at all
                   "loc": [{"city": city, "plz": plz, "region": None}], "city_source": "page" if agent_city else "seed",
                   "url": url,
                   "page": (data or {}).get("portal_url") or clinic.get("careers_url"),
                   "description": " ".join(p for p in desc_parts if p)[:20000], "department": j.get("department") or None,
                   # app/crawl.py's pre-inbox role filter and pflege_jobs/sources/inbox.py's intake
                   # gate both read section_labels (pflege_jobs.section.job_confirmed_nursing), never
                   # department -- an agent-read board category was silently discarded here, so a
                   # nursing posting whose own title carries no pflege keyword was dropped even when
                   # its own board category said "Pflegedienst" (confirmed live: clinic 37504,
                   # "Intensivfachkräfte (m/w/d) für unsere Intensiv- und Weaningstation").
                   "section_labels": [j["department"]] if j.get("department") else None,
                   "seniority": seniority if seniority in SENIORITY else "unknown",
                   "employmentType": _employment(j.get("employment_type")) + (["TEMPORARY"] if "befristet" in (j.get("contract") or "").lower() and "unbefristet" not in (j.get("contract") or "").lower() else []),
                   "datePosted": pub if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub) else None, "start_date": j.get("start_date"),
                   "contact_email": j.get("contact_email"), "agent": "firecrawl", "clinic_id": clinic.get("clinic_id")}
        rows.append({"kind": "jobposting", "source_host": _host(url), "source_url": url, "payload": payload,
                     "collector": COLLECTOR, "client_id": f"{COLLECTOR}-{clinic.get('clinic_id')}"})
    return rows


def run_jobs_agent(clinic, max_credits=DEFAULT_MAX_CREDITS, urls=None, log=print, session=None, webhook=None, check_webhook=None, **run_kw):
    urls = urls or [u for u in (clinic.get("careers_url"), clinic.get("website")) if u]
    data, used, raw = run_agent(_jobs_prompt(clinic), JOBS_SCHEMA, urls=urls, max_credits=max_credits, log=log, session=session,
                                 webhook=webhook, check_webhook=check_webhook, **run_kw)
    rows = jobs_to_inbox_rows(data, clinic)
    cost = _cost_of(raw, used)
    log(f"firecrawl agent: {len((data or {}).get('jobs') or [])} jobs -> {len(rows)} rows, credits charged {used} "
        f"(API creditsUsed {cost['credits_api']}, credits delta {cost['credits_delta']}, tokens delta {cost['tokens_delta']})"
        + (f"; notes: {data.get('notes')[:200]}" if isinstance(data, dict) and data.get("notes") else "")
        + (f"; blocked_reason: {data.get('blocked_reason')[:200]}" if isinstance(data, dict) and data.get("blocked_reason") else ""))
    return {"rows": rows, "credits_used": used, "raw": raw, "data": data, **cost}


def _cost_of(raw, used):
    """The run_agent() cost record as flat result keys (credits_used stays what the ledger is charged)."""
    c = (raw or {}).get("_cost") if isinstance(raw, dict) else None
    c = c or {}
    return {"credits_api": c.get("api_credits_used", used), "credits_delta": c.get("credits_delta"),
            "credits_before": c.get("credits_before"), "credits_after": c.get("credits_after"),
            "tokens_before": c.get("tokens_before"), "tokens_after": c.get("tokens_after"), "tokens_delta": c.get("tokens_delta"),
            "job_id": c.get("job_id"), "run_number_today": c.get("run_number_today"), "free_run": c.get("free_run")}


def run_career_agent(clinic, max_credits=DEFAULT_MAX_CREDITS, log=print, session=None, webhook=None, check_webhook=None, **run_kw):
    urls = [u for u in (clinic.get("careers_url"), clinic.get("website")) if u]
    data, used, raw = run_agent(_career_prompt(clinic), CAREER_SCHEMA, urls=urls or None, max_credits=max_credits, log=log, session=session,
                                 webhook=webhook, check_webhook=check_webhook, **run_kw)
    profile = dict(data or {})
    profile["clinic_id"] = clinic.get("clinic_id")
    profile["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    v = (profile.get("ats_vendor") or "unknown").lower()
    profile["ats_vendor"] = v if v in KNOWN_VENDORS else "other"
    vd = str(profile.get("verdict") or "").strip().lower()
    if vd not in VERDICTS:
        log(f"firecrawl career agent: unrecognised verdict {profile.get('verdict')!r}, treating as not_a_board")
        vd = "not_a_board"
    profile["verdict"] = vd
    cost = _cost_of(raw, used)
    log(f"firecrawl career agent: verdict {profile['verdict']}, vendor {profile['ats_vendor']}, tech {profile.get('listing_technology')}, "
        f"jobs {profile.get('visible_job_count')} / nursing {profile.get('nursing_job_count')}, credits charged {used} "
        f"(API creditsUsed {cost['credits_api']}, credits delta {cost['credits_delta']}, tokens delta {cost['tokens_delta']})")
    return {"profile": profile, "credits_used": used, "raw": raw, **cost}


# ats_vendor (agent enum) -> clinics.ats_type label used by routing. Vendors without an adapter stay
# labelled (dvinci) so the coverage gap is visible; 'other'/'unknown' never overwrite a label.
VENDOR_TO_ATS = {v: v for v in KNOWN_VENDORS if v not in ("other", "unknown", "workday", "sap_successfactors")}


def ats_type_for(profile):
    return VENDOR_TO_ATS.get((profile or {}).get("ats_vendor") or "")


# --- TASK-128: a raw-fetch fallback rung, no LLM reasoning step ------------------------------------
# run_jobs_agent() above pays the /v2/agent LLM to both defeat a bot-wall AND invent the extraction
# (title/city/department/role via JOBS_SCHEMA) -- duplicating what classify.py/registry.py already do
# for every other adapter. Ivan's idea, 2026-09-23: split the two concerns. /v2/scrape (verified live
# 2026-09-23: POST {url, formats:["html"]} -> {data: {html, metadata: {sourceURL, statusCode,
# creditsUsed}}}) is a plain page fetch through Firecrawl's proxy/renderer -- it gets past the SAME
# wall, at Firecrawl's own reported cost per page (1 credit for a static page in that live check; a
# JS-rendered board may cost more, unmeasured before TASK-128's own AC#3 cost comparison runs), with
# no schema and no LLM. Feeding that HTML into career_crawl.Crawler's existing walk means classify_role/
# classify_employer/fuzzy_key/content_hash (all already inside Crawler._base(), called by every row
# the walk produces) run unchanged -- nothing new to build downstream of the fetch itself.
class _ScrapeResponse:
    """Duck-typed requests.Response substitute (.text/.url/.status_code/.headers) -- the only shape
    career_crawl.Crawler.fetch()'s callers (_crawl_urls, _section_link, sitemap_job_urls) read."""
    def __init__(self, html, url):
        self.text, self.url, self.status_code = html, url, 200
        self.headers = {"content-type": "text/html"}


def scrape(url, session=None, timeout=60):
    """POST /v2/scrape for one page. Returns (response_or_None, {credits, status, error?}) -- never
    raises, same tolerant shape run_agent()'s own balance reads use, since a single walled page failing
    must not abort the whole board walk (Crawler.fetch() already treats a None return as unfetchable)."""
    s = session or requests.Session()
    try:
        r = s.post(API + "/scrape", headers=_headers(), json={"url": url, "formats": ["html"]}, timeout=timeout)
    except requests.RequestException as e:
        return None, {"credits": 0, "status": None, "error": f"{type(e).__name__}: {str(e)[:150]}"}
    if r.status_code >= 300:
        return None, {"credits": 0, "status": r.status_code, "error": r.text[:200]}
    j = r.json()
    d = (j.get("data") or {}) if j.get("success") else {}
    html = d.get("html")
    meta = d.get("metadata") or {}
    if not html:
        return None, {"credits": meta.get("creditsUsed", 0), "status": meta.get("statusCode"),
                       "error": (j.get("error") or "no html in response")[:200]}
    return _ScrapeResponse(html, meta.get("sourceURL") or meta.get("url") or url), \
        {"credits": meta.get("creditsUsed", 1), "status": meta.get("statusCode")}


class ScrapeExhausted(RuntimeError):
    """max_credits reached mid-walk -- carries what was spent so the caller can still charge the ledger."""
    def __init__(self, credits_used):
        super().__init__(f"firecrawl scrape: max_credits reached ({credits_used} spent)")
        self.credits_used = credits_used


def crawl_via_scrape(clinic, towns, max_credits=DEFAULT_MAX_CREDITS, log=print, session=None,
                      per_site_pages=200, list_pages=80):
    """AC#1/#2: career_crawl.Crawler's own listing-first walk (section-first + full BFS, JSON-LD +
    heuristic title extraction, classify_role/classify_employer/fuzzy_key already inside _base()), with
    fetch() backed by Firecrawl /v2/scrape instead of a direct HTTP GET -- for a board no adapter can
    otherwise reach. Returns (rows, stats, credits_used); rows are the SAME observation-shaped dicts
    any other Crawler-based source produces, ready for the existing inbox path unchanged.

    max_credits is a hard stop (mirrors run_jobs_agent's own cap): fetch() raises ScrapeExhausted the
    moment cumulative spend would exceed it, aborting the walk mid-page rather than silently going
    over budget -- this is cost control on a paid, unbounded-fanout API call, not an invented crawl-size
    ceiling (see CLAUDE.md's 'no safety nets': that rule is about truncating what a board itself offers,
    not about spending without a cap on a per-page-billed external API)."""
    from pflege_jobs.sources.career_crawl import Crawler
    s = session or requests.Session()
    spent = [0]

    class ScrapeCrawler(Crawler):
        def fetch(self, url):
            if spent[0] >= max_credits:
                raise ScrapeExhausted(spent[0])
            resp, cost = scrape(url, session=s)
            spent[0] += cost.get("credits") or 0
            log(f"  firecrawl scrape {url[:80]}: {cost.get('credits', 0)} credit(s), status {cost.get('status')}"
                + (f", error {cost['error']}" if cost.get("error") else "") + f" (spent {spent[0]}/{max_credits})")
            return resp

    seed = {"name": clinic["name"], "kez": clinic["clinic_id"], "career": clinic["careers_url"],
            "town": clinic.get("town"), "operator": clinic.get("operator"), "bavaria_only_operator": True}
    cr = ScrapeCrawler(towns, per_site_pages=per_site_pages, list_pages=list_pages, sleep=0, log=log)
    try:
        rows, stats = cr.crawl(seed)
    except ScrapeExhausted as e:
        log(f"firecrawl scrape crawl {clinic['clinic_id']} {clinic['name'][:40]}: aborted, {e.credits_used} credits spent")
        return [], {"truncated": True, "aborted": "max_credits"}, e.credits_used
    log(f"firecrawl scrape crawl {clinic['clinic_id']} {clinic['name'][:40]}: {len(rows)} rows, {spent[0]} credits")
    return rows, stats, spent[0]


def discover_then_scrape(clinic, towns, max_credits_recon=DEFAULT_MAX_CREDITS, max_credits_scrape=DEFAULT_MAX_CREDITS,
                          log=print, session=None, webhook=None, check_webhook=None, **scrape_kw):
    """Ivan's fix, 2026-09-23, for what crawl_via_scrape() alone cannot tell: a live test against Simssee
    Klinik (18713) spent its whole 20-credit cap wandering into clinical-department subpages and returned
    0 rows -- the raw walk has no way to distinguish 'wrong entry URL' from 'slow board', so it just burns
    the cap. One cheap CAREER_SCHEMA recon call first (run_career_agent(), no job extraction, no full-board
    pagination) either finds the real board and how to walk it (verdict board_found, portal_url as the
    entry point, crawl_strategy for a human/future reader) or says so and stops -- not_a_board /
    not_clinic_specific spend zero scrape credits and return alternative_locations for a human to check,
    instead of guessing.
    """
    recon = run_career_agent(clinic, max_credits=max_credits_recon, log=log, session=session,
                              webhook=webhook, check_webhook=check_webhook)
    profile = recon["profile"]
    verdict = profile["verdict"]
    result = {"verdict": verdict, "profile": profile, "rows": [], "stats": {"skipped": verdict},
              "recon_credits": recon["credits_used"], "scrape_credits": 0, "credits_used": recon["credits_used"]}
    if verdict != "board_found":
        log(f"firecrawl discover: verdict {verdict} for {clinic.get('name')} -- no scrape credits spent"
            + (f"; alternatives: {profile.get('alternative_locations')}" if profile.get("alternative_locations") else ""))
        return result
    entry = profile.get("portal_url") or clinic.get("careers_url")
    scrape_clinic = dict(clinic, careers_url=entry)
    rows, stats, scrape_credits = crawl_via_scrape(scrape_clinic, towns, max_credits=max_credits_scrape, log=log, session=session, **scrape_kw)
    result.update(rows=rows, stats=stats, scrape_credits=scrape_credits, credits_used=recon["credits_used"] + scrape_credits)
    return result
