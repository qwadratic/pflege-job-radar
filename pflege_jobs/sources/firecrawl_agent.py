"""Firecrawl `/agent` as a source (source_id 25, kind employer_ats, precedence 2).

Used for clinics that no adapter can reach: bot-walled hosts (Helios), JS-only boards (d.vinci, Oracle),
sites without a vendor fingerprint. The agent gets a prompt + a JSON schema it MUST answer with, and a
hard `maxCredits` cap — credits are scarce, so nothing here runs without a cap.

Two agents:
  run_jobs_agent(clinic)    -> nursing vacancies at that site           -> inbox rows (collector "firecrawl-agent")
  run_career_agent(clinic)  -> where the career portal is, which ATS, which filters/categories the board offers

API (v2, verified 2026-09-06): POST /v2/agent {prompt, urls?, schema, model, maxCredits} -> {id, status}
                              GET  /v2/agent/{id}   -> {status: processing|completed|failed, data, creditsUsed}
                              GET  /v2/team/credit-usage -> {data: {remainingCredits, planCredits, ...}}
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
COLLECTOR = "firecrawl-agent"
KNOWN_VENDORS = ["softgarden", "typo3_jobs", "bite", "rexx", "umantis", "mein-check-in", "dvinci", "pi_asp", "concludis",
                 "oracle", "personio", "smartrecruiters", "talention", "helix", "workday", "sap_successfactors", "other", "unknown"]

JOBS_SCHEMA = {
    "type": "object",
    "properties": {
        "portal_url": {"type": "string", "description": "URL of the job board / listing page the jobs were read from"},
        "jobs": {
            "type": "array",
            "description": "Every open nursing (Pflege) vacancy at this hospital site",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Job title exactly as advertised, incl. (m/w/d)"},
                    "url": {"type": "string", "description": "Direct URL of this job advertisement (detail page), not the list page"},
                    "city": {"type": "string", "description": "Town of the workplace"},
                    "plz": {"type": "string", "description": "German postal code of the workplace, if shown"},
                    "department": {"type": "string", "description": "Ward / department / Fachbereich as written (e.g. Intensivstation, OP, Notaufnahme)"},
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
    },
    "required": ["jobs"],
}

CAREER_SCHEMA = {
    "type": "object",
    "properties": {
        "careers_url": {"type": "string", "description": "The hospital's own careers / Karriere / Stellenangebote page on its website"},
        "portal_url": {"type": "string", "description": "The actual job board URL where vacancies are listed (may be an external ATS domain)"},
        "ats_vendor": {"type": "string", "enum": KNOWN_VENDORS, "description": "Applicant-tracking vendor behind the board, judged from URLs/markup (softgarden.io, jobs.b-ite.com, rexx-systems, umantis.com, mein-check-in.de, dvinci, pi-asp.de, concludis, oracle cloud, personio, smartrecruiters, talention, helixjobs)"},
        "listing_technology": {"type": "string", "enum": ["html", "js", "pdf", "iframe", "unknown"], "description": "How the list renders: plain HTML links, JavaScript app, PDF flyers, embedded iframe"},
        "filters": {"type": "array", "description": "Filter controls the board offers, with ALL their selectable values",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}}, "required": ["name", "values"]}},
        "categories": {"type": "array", "items": {"type": "string"}, "description": "Job categories / Berufsgruppen the board groups vacancies into"},
        "visible_job_count": {"type": "integer", "description": "Total vacancies listed (all professions)"},
        "nursing_job_count": {"type": "integer", "description": "Vacancies that are nursing (Pflege) roles"},
        "has_rss_or_json": {"type": "boolean", "description": "Whether a feed / JSON / XML endpoint for the jobs exists"},
        "notes": {"type": "string", "description": "Login walls, cookie walls, bot protection, pagination pattern, anything an engineer writing a crawler needs"},
    },
    "required": ["careers_url", "ats_vendor", "listing_technology"],
}


def _jobs_prompt(clinic):
    return (f"You are collecting OPEN NURSING VACANCIES (Pflege-Stellen) at ONE hospital in Bavaria, Germany: "
            f"\"{clinic.get('name')}\" in {clinic.get('town') or 'Bavaria'} (operator: {clinic.get('operator') or 'unknown'}). "
            "Start at the given URL(s): the hospital's careers page and any job board / ATS it links to (softgarden, B-ITE, rexx, umantis, "
            "mein-check-in, d.vinci, Personio, etc.). Follow pagination and 'mehr laden' until every job is seen. "
            "INCLUDE only nursing roles for qualified, experienced staff: Pflegefachkraft, Gesundheits- und Krankenpfleger, Fachpflege "
            "(Intensiv, Anästhesie, OP), Pflegehelfer, Praxisanleitung, Stationsleitung / Pflegedienstleitung, Hebamme, OTA/ATA, Pflegeexperte/APN. "
            "EXCLUDE trainees (Ausbildung, Azubi, Schüler), Praktikum, Werkstudent, FSJ/BFD, physicians, MFA, therapists, admin, logistics, "
            "and every job located outside Bavaria. Each job MUST have its own direct URL (the detail page, not the list). "
            "Do not invent jobs; if the board is empty or blocked, return an empty list and say why in notes.")


def _career_prompt(clinic):
    return (f"Find the careers portal of the hospital \"{clinic.get('name')}\" in {clinic.get('town') or 'Bavaria'}, Germany "
            f"(operator: {clinic.get('operator') or 'unknown'}; website: {clinic.get('website') or 'unknown'}). "
            "Locate (1) the careers page on the hospital website and (2) the actual job board where vacancies are listed — often an external "
            "applicant-tracking system. Identify the ATS vendor from URLs and page markup. Examine the board like an engineer who must crawl it: "
            "record every filter control with ALL its selectable values (Berufsgruppe, Standort, Fachbereich, Beschäftigungsart ...), the categories "
            "it groups jobs into, how many jobs are visible in total and how many are nursing (Pflege), whether the list is plain HTML, JavaScript-rendered, "
            "iframe-embedded or PDF flyers, whether an RSS/JSON/XML feed exists, and any login/cookie/bot wall or pagination pattern. "
            "Answer strictly in the schema; use 'unknown' when you cannot tell.")


class AgentFailed(RuntimeError):
    """Firecrawl agent run ended in status=='failed' or timed out. Carries credits_used so the caller
    can still charge the local ledger -- over-charging it is safe, under-charging is what lets the
    weekly budget check (_budget_left) silently drift away from what the account actually spent."""

    def __init__(self, message, credits_used):
        super().__init__(message)
        self.credits_used = credits_used


def _headers():
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def credits(session=None):
    """{'remaining': int, 'plan': int, 'period_start': str, 'period_end': str} or {'error': ...}"""
    try:
        r = (session or requests).get(API + "/team/credit-usage", headers=_headers(), timeout=30)
        d = (r.json() or {}).get("data") or {}
        return {"remaining": d.get("remainingCredits", d.get("remaining_credits")), "plan": d.get("planCredits", d.get("plan_credits")),
                "period_start": d.get("billingPeriodStart", d.get("billing_period_start")), "period_end": d.get("billingPeriodEnd", d.get("billing_period_end"))}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}", "remaining": None, "plan": None, "period_end": None}


def build_request(prompt, schema, urls=None, max_credits=DEFAULT_MAX_CREDITS, model=MODEL):
    body = {"prompt": prompt, "schema": schema, "model": model, "maxCredits": int(max_credits)}
    if urls:
        body["urls"] = [u for u in urls if u]
    return body


def run_agent(prompt, schema, urls=None, max_credits=DEFAULT_MAX_CREDITS, poll=5, timeout=900, log=print, session=None):
    """Submit an agent job and poll it. Returns (data, credits_used, raw). Raises on failure."""
    if not max_credits or int(max_credits) <= 0:
        raise ValueError("max_credits must be a positive cap")
    s = session or requests.Session()
    body = build_request(prompt, schema, urls, max_credits)
    r = s.post(API + "/agent", headers=_headers(), json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"firecrawl agent submit {r.status_code}: {r.text[:300]}")
    j = r.json()
    if j.get("status") == "completed" and "data" in j:                # synchronous answer
        return j.get("data") or {}, int(j.get("creditsUsed") or 0), j
    job_id = j.get("id")
    if not job_id:
        raise RuntimeError(f"firecrawl agent: no job id in {str(j)[:200]}")
    log(f"firecrawl agent job {job_id} submitted (maxCredits {max_credits})")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        g = s.get(f"{API}/agent/{job_id}", headers=_headers(), timeout=60)
        j = g.json() if g.status_code < 500 else {}
        st = j.get("status")
        if st == "completed":
            return j.get("data") or {}, int(j.get("creditsUsed") or 0), j
        if st == "failed":
            used = j.get("creditsUsed")
            raise AgentFailed(f"firecrawl agent failed: {str(j.get('error') or j)[:300]}",
                               credits_used=int(used) if used is not None else int(max_credits))
    raise AgentFailed(f"firecrawl agent {job_id} still processing after {timeout}s", credits_used=int(max_credits))


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
        city = (j.get("city") or "").strip() or town
        plz = re.sub(r"\D", "", j.get("plz") or "")[:5] or None
        desc_parts = [j.get("description"), ("Anforderungen: " + j["requirements"]) if j.get("requirements") else None,
                      ("Vergütung: " + j["tariff_or_salary"]) if j.get("tariff_or_salary") else None,
                      ("Beschäftigung: " + j["employment_type"]) if j.get("employment_type") else None,
                      ("Vertrag: " + j["contract"]) if j.get("contract") else None,
                      ("Kontakt: " + j["contact_email"]) if j.get("contact_email") else None]
        pub = (j.get("published") or "")[:10]
        payload = {"title": title, "org": clinic.get("name"), "loc": [{"city": city, "plz": plz, "region": "BAYERN"}], "url": url,
                   "page": (data or {}).get("portal_url") or clinic.get("careers_url"),
                   "description": " ".join(p for p in desc_parts if p)[:20000], "department": j.get("department") or None,
                   "employmentType": _employment(j.get("employment_type")) + (["TEMPORARY"] if "befristet" in (j.get("contract") or "").lower() and "unbefristet" not in (j.get("contract") or "").lower() else []),
                   "datePosted": pub if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub) else None, "start_date": j.get("start_date"),
                   "contact_email": j.get("contact_email"), "agent": "firecrawl", "clinic_id": clinic.get("clinic_id")}
        rows.append({"kind": "jobposting", "source_host": _host(url), "source_url": url, "payload": payload,
                     "collector": COLLECTOR, "client_id": f"{COLLECTOR}-{clinic.get('clinic_id')}"})
    return rows


def run_jobs_agent(clinic, max_credits=DEFAULT_MAX_CREDITS, urls=None, log=print, session=None):
    urls = urls or [u for u in (clinic.get("careers_url"), clinic.get("website")) if u]
    data, used, raw = run_agent(_jobs_prompt(clinic), JOBS_SCHEMA, urls=urls, max_credits=max_credits, log=log, session=session)
    rows = jobs_to_inbox_rows(data, clinic)
    log(f"firecrawl agent: {len((data or {}).get('jobs') or [])} jobs -> {len(rows)} rows, credits used {used}"
        + (f"; notes: {data.get('notes')[:200]}" if isinstance(data, dict) and data.get("notes") else ""))
    return {"rows": rows, "credits_used": used, "raw": raw, "data": data}


def run_career_agent(clinic, max_credits=DEFAULT_MAX_CREDITS, log=print, session=None):
    urls = [u for u in (clinic.get("careers_url"), clinic.get("website")) if u]
    data, used, raw = run_agent(_career_prompt(clinic), CAREER_SCHEMA, urls=urls or None, max_credits=max_credits, log=log, session=session)
    profile = dict(data or {})
    profile["clinic_id"] = clinic.get("clinic_id")
    profile["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    v = (profile.get("ats_vendor") or "unknown").lower()
    profile["ats_vendor"] = v if v in KNOWN_VENDORS else "other"
    log(f"firecrawl career agent: vendor {profile['ats_vendor']}, tech {profile.get('listing_technology')}, "
        f"jobs {profile.get('visible_job_count')} / nursing {profile.get('nursing_job_count')}, credits used {used}")
    return {"profile": profile, "credits_used": used, "raw": raw}


# ats_vendor (agent enum) -> clinics.ats_type label used by routing. Vendors without an adapter stay
# labelled (dvinci) so the coverage gap is visible; 'other'/'unknown' never overwrite a label.
VENDOR_TO_ATS = {v: v for v in KNOWN_VENDORS if v not in ("other", "unknown", "workday", "sap_successfactors")}


def ats_type_for(profile):
    return VENDOR_TO_ATS.get((profile or {}).get("ats_vendor") or "")
