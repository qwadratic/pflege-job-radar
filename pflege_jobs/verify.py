"""Web-liveness verification: is each posting still reachable on the public web?

- arbeitsagentur rows: GET v4 jobdetails/{base64(refnr)} -> 200 live, 404/410 gone (the public page
  https://www.arbeitsagentur.de/jobsuche/jobdetail/<ref> is the human-readable proof of the same record).
- employer_ats rows: GET job_url with a browser UA, follow redirects -> 404/410 gone; 200 whose body still
  contains the job title (first 3 significant words) -> live; 200 without the title (redirected to a job list,
  "Stelle nicht mehr verfügbar" page) -> gone_soft (reported as gone with note); 403/429/5xx/timeouts -> blocked/error.
Results are pushed to pflege_jobs.postings via the ingest function (`verify` op). Only 'gone' expires a posting.
"""
import base64
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from . import config as C
from .classify import norm_text

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
GONE_MARKERS = re.compile(r"nicht mehr verfügbar|nicht mehr online|nicht gefunden|stelle wurde bereits besetzt|"
                          r"job is no longer|no longer available|position has been filled|page not found|404", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def verify_aa(session, ref):
    enc = base64.b64encode(ref.encode()).decode()
    try:
        r = session.get(f"{C.AA_DETAILS_BASE}/jobdetails/{enc}", headers={"X-API-Key": C.AA_API_KEY}, timeout=40)
    except requests.RequestException as e:
        return "error", None, f"{type(e).__name__}"
    if r.status_code == 200:
        return "live", 200, None
    if r.status_code in (404, 410):
        return "gone", r.status_code, None
    if r.status_code == 429:                      # rate limit: back off once, retry
        time.sleep(3)
        r = session.get(f"{C.AA_DETAILS_BASE}/jobdetails/{enc}", headers={"X-API-Key": C.AA_API_KEY}, timeout=40)
        if r.status_code == 200: return "live", 200, "after 429 retry"
        if r.status_code in (404, 410): return "gone", r.status_code, None
    if r.status_code in (403, 429):
        return "blocked", r.status_code, "arbeitsagentur rate limit" if r.status_code == 429 else None
    return "error", r.status_code, None


def _title_tokens(title):
    toks = [t for t in re.findall(r"[a-zäöüß]{4,}", norm_text(title or "")) if t not in ("pflegefachkraft", "gesundheits", "krankenpfleger")]
    return toks[:3]


def verify_url(session, url, title):
    try:
        r = session.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}, timeout=40, allow_redirects=True)
    except requests.RequestException as e:
        return "error", None, type(e).__name__
    if r.status_code in (404, 410):
        return "gone", r.status_code, None
    if r.status_code in (401, 403, 429):
        return "blocked", r.status_code, None
    if r.status_code >= 500:
        return "error", r.status_code, None
    if r.status_code != 200:
        return "error", r.status_code, None
    body = norm_text(r.text[:400000])
    toks = _title_tokens(title)
    hit = sum(1 for t in toks if t in body)
    if toks and hit == 0 and GONE_MARKERS.search(body):
        return "gone", 200, "200 but title missing + gone marker"
    if toks and hit == 0:
        return "error", 200, "200 but title not found (JS-rendered or list page)"
    return "live", 200, f"title tokens {hit}/{len(toks)}"


def verify_all(rows, workers=6, log=print):
    """rows: dicts with posting_id, source_codes, source_url, external_url, title, aa_ref (may be None)."""
    s = requests.Session()
    out, done = [], 0

    def work(r):
        if r.get("aa_ref"):
            st, code, note = verify_aa(s, r["aa_ref"])
            if st == "live" or not r.get("external_url") or not r["external_url"].startswith("http"):
                return {"posting_id": r["posting_id"], "verify_status": st, "verify_http": code, "verified_at": _now(), "verify_note": note}
        url = r.get("external_url") or r.get("source_url")
        st, code, note = verify_url(s, url, r["title"])
        return {"posting_id": r["posting_id"], "verify_status": st, "verify_http": code, "verified_at": _now(), "verify_note": note}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(work, r) for r in rows]):
            out.append(f.result()); done += 1
            if done % 500 == 0:
                log(f"verified {done}/{len(rows)}")
    return out
