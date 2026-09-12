"""Best-effort clinic contact discovery (TASK-64, plan section 3). No external "sales brain" data
source exists anywhere in this repo or in prior session notes -- confirmed and skipped, per Ivan.
Instead a Pflegedirektion/HR contact is found from data the board already has, or can politely read
for itself, tried in order of how much it costs to check:

  1. enr_contact_emails on this clinic's own postings -- already scraped off the job ad by the
     ingestion pipeline (pflege_jobs/classify.py:enrich_description) and already unredacted here.
     app.wa.brain.jobs_for() calls app.data.filter_jobs() directly, and app.data.redact() is only
     ever invoked from app/main.py's HTTP routes (GET /api/jobs, /api/jobs/{id}) -- never from
     filter_jobs()/filter_clinics() themselves (confirmed by reading both call chains). This
     in-process WA harness therefore already reads the same unredacted rows the app itself holds;
     no separate unlock is needed to use this as the primary source.
  2. One polite fetch (requests, short per-request timeout, one request per clinic) of the
     clinic's own careers page (falling back to its website when no careers_url is set --
     pflege_jobs/schema.py:CLINIC_SPEC has both columns), regex-scanning the visible page text for
     a plain email near a recruiting-context word (Pflegedirektion/Personalabteilung/Bewerbung/
     Karriere/HR). Throttled with app.crawl.POLITE_SLEEP -- reused, not reinvented.
  3. A second, differently-shaped regex pass over the job ad's own description text, tuned for
     obfuscated spellings ("vorname.name (at) klinik-x.de", "name[at]klinik[punkt]de" -- exactly
     the shapes app/data.py's own redact() docstring names as what a plain email pattern misses).
     The ingestion pipeline (pflege_jobs/patterns.json: enrichment.email) already ran a plain email
     regex over this same text once at intake; this is a deliberately cheap re-scan for a different
     pattern shape, not a duplicate of that one. The in-memory snapshot's job rows do not carry
     `description` (see app/data.py:JOB_COLS), so it is lazily fetched via app.data.job_detail()
     only for postings that reach this step.

Deliberately NOT app.firecrawl_gate's credit-gated agent pipeline: that is for full job discovery
across a whole board, not a single best-effort contact lookup.

Storage: a new clinic_contacts table, own schema block, same sqlite file as app.wa.store
(app.wa.config.SQLITE_PATH) -- not mixed into wa_threads/wa_messages. Populated ahead of time by
the batch entry point (python -m app.wa.luna.discover_contacts), read at WhatsApp-turn time only
via get_contact() (by a future MCP tool, a separate task) -- nothing here runs live during a turn.
"""
import html
import re
import time

import requests

from ... import data as D
from ...crawl import POLITE_SLEEP
from .. import store as ST

# Same UA convention as pflege_jobs/sources/career_crawl.py and bite.py.
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 pflege-jobs-crawler"
FETCH_TIMEOUT_SEC = 15   # a normal per-request client timeout, not a cap on anything this task asks for

CONTEXT_WORDS = ("Pflegedirektion", "Personalabteilung", "Bewerbung", "Karriere", "HR")
CONTEXT_RE = re.compile("|".join(re.escape(w) for w in CONTEXT_WORDS), re.IGNORECASE)
CONTEXT_WINDOW = 200     # characters either side of a found email that count as "near" a context word

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

# Obfuscated-address pass (step 3): a different shape than the plain EMAIL_RE the ingestion
# pipeline already tried once over this same description text -- see module docstring.
_AT = r"(?:@|\(at\)|\[at\]|\s+at\s+)"
_DOT = r"(?:\.|\(punkt\)|\[punkt\]|\(dot\)|\[dot\]|\s+punkt\s+|\s+dot\s+)"
OBFUSCATED_EMAIL_RE = re.compile(
    rf"[A-Za-z0-9._%+-]+\s*{_AT}\s*[A-Za-z0-9-]+(?:\s*{_DOT}\s*[A-Za-z0-9-]+)+", re.IGNORECASE)

TAG_RE = re.compile(r"<[^>]+>")

SCHEMA = """
create table if not exists clinic_contacts (
  clinic_id text primary key,
  email text not null,
  source text not null,
  confidence text not null,
  discovered_at text not null
);
"""


# --- storage -----------------------------------------------------------------------------------

def db():
    """Same connection/pragma setup as app.wa.store.db() (same wa.sqlite file), plus this
    module's own table -- not mixed into wa_threads/wa_messages."""
    c = ST.db()
    c.executescript(SCHEMA)
    return c


def save_contact(c, clinic_id, email, source, confidence):
    """Upsert: one row per clinic_id, latest discovery wins."""
    c.execute(
        """insert into clinic_contacts (clinic_id, email, source, confidence, discovered_at)
           values (?,?,?,?,?)
           on conflict(clinic_id) do update set
             email=excluded.email, source=excluded.source, confidence=excluded.confidence,
             discovered_at=excluded.discovered_at""",
        (clinic_id, email, source, confidence, ST.now_iso()))
    c.commit()


def get_contact(c, clinic_id):
    """Read-only lookup for a WhatsApp turn (a future MCP tool calls this): {email, source,
    confidence, discovered_at} or None. Never crawls -- this table is populated ahead of time."""
    row = c.execute("select email, source, confidence, discovered_at from clinic_contacts where clinic_id=?",
                     (clinic_id,)).fetchone()
    return dict(row) if row else None


# --- source 1: enr_contact_emails on this clinic's own postings --------------------------------

def _from_enr_contact_emails(postings):
    for p in postings:
        for e in (p.get("enr_contact_emails") or []):
            if e and e.strip():
                return {"email": e.strip().lower(), "source": "enr_contact_emails", "confidence": "high"}
    return None


# --- source 2: one polite fetch of the clinic's own careers page / website ----------------------

def _visible_text(body):
    return html.unescape(TAG_RE.sub(" ", body or ""))


def _email_near_context(text):
    for m in EMAIL_RE.finditer(text):
        lo, hi = max(0, m.start() - CONTEXT_WINDOW), m.end() + CONTEXT_WINDOW
        if CONTEXT_RE.search(text[lo:hi]):
            return m.group(0).lower()
    return None


def _fetch(url, session=None):
    return (session or requests).get(url, headers={"User-Agent": UA}, timeout=FETCH_TIMEOUT_SEC)


def _from_clinic_site(clinic, session=None):
    # careers_url first: it is the recruiting page itself, more likely to carry the context words
    # this heuristic looks for than the clinic's general homepage.
    url = (clinic.get("careers_url") or clinic.get("website") or "").strip()
    if not url:
        return None
    try:
        resp = _fetch(url, session=session)
        resp.raise_for_status()
        text = _visible_text(resp.text)
    except Exception:
        return None
    finally:
        time.sleep(POLITE_SLEEP)   # one polite fetch per clinic, win or lose
    email = _email_near_context(text)
    return {"email": email, "source": "website", "confidence": "medium"} if email else None


# --- source 3: second, differently-shaped regex pass over the job ad's own description ---------

def _normalize_obfuscated(raw):
    s = re.sub(_AT, "@", raw, flags=re.IGNORECASE)
    s = re.sub(_DOT, ".", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", s).lower()


def _description_for(posting):
    """The job ad's full body text. The in-memory snapshot's rows do not carry it (JOB_COLS), so
    it is fetched on demand -- but a posting dict that already has the key (e.g. a test fixture,
    or a caller that already loaded the full row) is used as-is, even when the value is falsy."""
    if "description" in posting:
        return posting.get("description")
    pid = posting.get("posting_id")
    if pid is None:
        return None
    return (D.job_detail(pid) or {}).get("description")


def _from_description_rescan(postings):
    for p in postings:
        desc = _description_for(p)
        if not desc:
            continue
        m = OBFUSCATED_EMAIL_RE.search(desc)
        if m:
            return {"email": _normalize_obfuscated(m.group(0)), "source": "description_rescan", "confidence": "low"}
    return None


# --- entry point ---------------------------------------------------------------------------------

def discover_contact(clinic, postings, session=None):
    """Best-effort {email, source, confidence} for one clinic, or None when none of the three
    sources above found anything. `session` is only for tests (a fake with a `.get()`); production
    callers leave it unset and get the real `requests` module."""
    return (_from_enr_contact_emails(postings)
            or _from_clinic_site(clinic, session=session)
            or _from_description_rescan(postings))
