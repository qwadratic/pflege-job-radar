"""Exa-powered career-site discovery for the never-posted cohort.

COHORT: clinics with a blank careers_url AND zero currently-open postings (108 live, minus the
17 whose name is a near-duplicate of a sibling clinic that already has a careers_url -- copying a
twin's URL yields zero postings, see below -- leaves ~91 clinics actually worth a paid search).

Two problems make the raw registry data unfit to search on directly:

  1. NAME corruption. A few dozen names in this cohort carry a duplicated tail (the PDF parser
     glued the site label, an address fragment and the operator name back onto the name when the
     51. Fortschreibung dropped the `Träger` separator line -- see data/registry/README.md).
     clean_name() finds the shortest repeated prefix and cuts the name back to it:
       "Schön Klinik Roseneck Prien am Chiemsee Schön Klinik Roseneck SE & Co." -> "Schön Klinik Roseneck"

  2. TOWN corruption. For the same reason, `town` is sometimes an operator fragment, a person's
     name, or a lake instead of a place ('(GeBO)', 'Willi Pinkow', 'Ammersee', ...). A denylist of
     bad words leaks (there is always one more bad word); town_ok() flips it around: a town is
     trustworthy only if it already appears inside the (cleaned) name, or it is a name this
     registry has independently seen attached to >=2 different clinics (a corrupt fragment never
     repeats; a real town does).

TWIN MATCH: before spending anything, clinics whose cleaned name is a near-duplicate of a clinic
that already HAS a careers_url are pulled out and printed for human review (never auto-written --
every twin's URL turned out to already be in the crawl rotation, or unroutable). twin_match() uses
the same clean()-to-bare-alnum-ascii transform as the town/name work, gated on a true prefix
relationship (>=12 chars, not just "first 12 characters happen to match" -- that trivially fires on
any two "Kreiskrankenhaus ..." names) OR SequenceMatcher ratio >= 0.93. Do not lower the ratio: 0.85
pairs "Klinik Krumbach" with "Klinikum Kulmbach".

EXA QUERY: shape A only -- "<clean_name> <town-if-trustworthy> Karriere Stellenangebote Pflege",
POST https://api.exa.ai/search, {type: "auto", numResults: 5}. Never includeDomains (collapses the
result set to the bare homepage for sites Exa already half-knows). Shape B ("<clean_name>
Stellenangebote") only fires when shape A produced zero *acceptable* candidates. $0.007/search;
this module is the only one in the harvest pipeline allowed to spend, and it hard-stops at
--max-searches (default 120 = $0.84), printing a running spend total.

RANK + ACCEPT: aggregator hosts (job boards that are never a clinic's own site) are dropped
outright. Host affinity (does the clinic's own name show up in the candidate's domain) is a
ranking *signal* only -- never a hard gate, because several correct results are a parent company's
board on an unrelated domain (Adula-Klinik -> reisach-kliniken.de). A candidate is accepted when
(it looks like a listing/board, OR it fingerprints to a known ATS vendor) AND a name/town token
shows up in the fetched page text. 403/429 responses are recorded as decision='blocked' and the
candidate is KEPT (rate limiting is not evidence the URL is wrong -- augencentrum.de flipped
200 -> 403 mid-run).

Every raw Exa result array is cached to data/registry/exa_career_seeds.json, keyed by clinic_id, so
the accept/rank heuristic above can be retuned against --from-cache for free.

Only *accepted* or *blocked* candidates are written out, as the same inbox 'probe' row
crawlers/ats_discover2.py already emits (kind='probe', payload.probe='ats_discovery') so
pflege_jobs/cli.py:cmd_inbox picks them up with no changes.

  python crawlers/career_discover_exa.py --report                 # cohort size, twins, nothing spent
  python crawlers/career_discover_exa.py --max-searches 20        # smoke test, $0.14 cap
  python crawlers/career_discover_exa.py --from-cache             # re-rank data already in the cache, $0
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.ats_discover2 import ATS, CAREER_RX, H, JOB_URL_RX, SKIP_EXT, fingerprint, get  # noqa: E402

EXA_URL = "https://api.exa.ai/search"
COST_PER_SEARCH = 0.007
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "registry", "exa_career_seeds.json")
OUT = os.environ.get("EGRESS_OUTPUT_DIR", "crawl_output")
CID = "career-discover-exa-" + os.environ.get("EGRESS_CLIENT", "default")

AGGREGATORS = (
    "indeed", "stepstone", "kimeta", "meinestadt", "jobijoba", "stellenmarkt",
    "rekruter", "trawox", "carerockets", "ethimedis", "resuminder", "pflegia",
    "linkedin", "xing", "arbeitsagentur", "monster", "glassdoor", "kununu",
    "jobvector", "jooble", "absolventa", "yourfirm", "azubiyo", "medi-karriere",
)

# A URL that names one specific opening rather than a list of them -- still a usable candidate,
# just ranked below a real board (JOB_URL_RX already flags "is this career-shaped at all").
SINGLE_JOB_RX = re.compile(r"/(job|stelle|stellenangebot|position|vacan\w*)[-/](\d{3,}|[a-f0-9]{6,})", re.I)

DE_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def clean(s):
    """lowercase -> ae/oe/ue/ss transliteration -> strip everything but alnum. Bare comparison key."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower().translate(DE_MAP))


def clean_name(name):
    """Cut a name back to its shortest repeated prefix: the PDF parser glued the site label, an
    address fragment and the operator name onto some names after the 51. Fortschreibung dropped the
    `Träger` separator line. Finds the smallest j>=2 where tokens[j:j+k] echoes tokens[:k], and
    keeps only that first copy. Names without a repeat are returned unchanged."""
    tokens = (name or "").split()
    n = len(tokens)
    for j in range(2, n):
        if tokens[j] != tokens[0]:
            continue
        k = 0
        while j + k < n and tokens[j + k] == tokens[k]:
            k += 1
        return " ".join(tokens[:k])
    return name


def is_true_prefix(a, b, min_len=12):
    """One cleaned string is a genuine prefix of the other (not just "first N chars coincide" --
    that fires on every "Kreiskrankenhaus ..." pair). Both must clear min_len."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= min_len and long_.startswith(short)


def twin_match(cohort, has_url):
    """For each cohort clinic, find the best-matching clinic (by combined prefix+ratio score)
    among those that already have a careers_url. Returns [(cohort_clinic, twin_clinic, ratio, is_prefix), ...]
    for pairs clearing the bar -- true prefix (>=12 chars) OR ratio >= 0.93. Do not lower 0.93."""
    candidates = [(clean(c["name"]), c) for c in has_url]
    hits = []
    for c in cohort:
        cc = clean(c["name"])
        best, best_score = None, -1.0
        for wc, w in candidates:
            ratio = SequenceMatcher(None, cc, wc).ratio()
            prefix = is_true_prefix(cc, wc)
            score = ratio + (1.0 if prefix else 0.0)
            if score > best_score:
                best_score, best = score, (w, ratio, prefix)
        if best and (best[2] or best[1] >= 0.93):
            hits.append((c, best[0], round(best[1], 3), best[2]))
    return hits


# ---------------------------------------------------------------------------
# town_ok: a positive check, not a denylist (a denylist always leaks the next bad word)
# ---------------------------------------------------------------------------
_LEGAL_FORM_RX = re.compile(r"\b(gmbh|mbh|ggmbh|kg|gbr|ag|e\.?\s?v\.?|co\.?)\b", re.I)


def town_ok(town, name="", registry_town_counts=None):
    """A town string is trustworthy for the query if it already shows up inside the (cleaned) name,
    or it is a name this registry has independently seen attached to >=2 clinics -- a corrupt
    fragment (an operator suffix, a person's name, a lake) is unique to the one row it corrupted;
    a real town repeats. registry_town_counts=None skips the repeat check (used by the standalone
    unit test)."""
    t = (town or "").strip()
    if len(t) < 3:
        return False
    if _LEGAL_FORM_RX.search(t) or "&" in t or "(" in t or ")" in t:
        return False
    if name and clean(t) in clean(name):
        return True
    if registry_town_counts is not None:
        return registry_town_counts.get(t, 0) >= 2
    return False


def town_for_query(town, name, registry_town_counts):
    return town.strip() if town and town_ok(town, name, registry_town_counts) else None


def build_queries(clean_nm, town):
    qa = clean_nm + ((" " + town) if town else "") + " Karriere Stellenangebote Pflege"
    qb = clean_nm + " Stellenangebote"
    return re.sub(r"\s+", " ", qa).strip(), re.sub(r"\s+", " ", qb).strip()


# ---------------------------------------------------------------------------
# rank + accept
# ---------------------------------------------------------------------------
def is_aggregator(url):
    """Match by brand name, not a fixed domain+TLD: jobijoba runs jobijoba.de in Germany, not the
    .com the first pass assumed, and other aggregators are just as likely to be seen on a
    country-specific TLD."""
    host = urlparse(url or "").netloc.lower()
    return any(a in host for a in AGGREGATORS)


def affinity(name, url):
    """Ranking signal only, never a gate: does the clinic's own name show up in this host, or is
    the host already a known ATS vendor domain (cheaper than fetching the page to fingerprint it)?
    Umlaut-aware (both sides run through clean())."""
    host = clean(urlparse(url or "").netloc)
    toks = [clean(t) for t in re.split(r"[^\wäöüß]+", (name or "").lower()) if len(t) >= 5]
    hits = sum(1 for t in toks if t and t in host)
    vendor_bonus = 1.0 if any(re.search(pat, url or "", re.I) for _, pat in ATS) else 0.0
    return hits + vendor_bonus + SequenceMatcher(None, clean(name), host).ratio()


def board_score(html, url):
    """Does this page look like a listing rather than a single job or plain marketing copy?
    CAREER_RX is checked against the URL only, not the html body: almost every clinic site's global
    nav carries a "Karriere" link, so a body-text match fires on any page of the site, not just the
    career one. Actual job/apply URLs found in the body (JOB_URL_RX) are the real listing signal."""
    if not html:
        return 0
    score = 0
    if CAREER_RX.search(url or ""):
        score += 1
    n_jobs = len(JOB_URL_RX.findall(html))
    score += 2 if n_jobs >= 3 else (1 if n_jobs >= 1 else 0)
    if SINGLE_JOB_RX.search(url or ""):
        score -= 1                                     # a single opening, not a board -- still usable
    return score


def name_token_hit(name, town, html):
    """A name or town token must appear in the fetched page text -- the one check every accepted
    candidate shares, vendor fingerprint or not."""
    text = clean(re.sub(r"<[^>]+>", " ", html or ""))
    toks = [clean(t) for t in re.split(r"[^\wäöüß]+", ((name or "") + " " + (town or "")).lower()) if len(t) >= 4]
    return any(t and t in text for t in toks)


def accept(url, html, name, town):
    """accept = (looks like a board OR fingerprints to a vendor) AND a name/town token is on the page."""
    ats, ev = fingerprint(html or "", url or "")
    ok = bool(ats) or board_score(html, url) > 0
    return ok and name_token_hit(name, town, html), ats, ev


# ---------------------------------------------------------------------------
# Exa
# ---------------------------------------------------------------------------
class Budget:
    """Thread-safe: discover_one() runs inside a ThreadPoolExecutor, so check-and-increment must be
    one atomic step or concurrent workers can overshoot --max-searches."""
    def __init__(self, max_searches):
        self.max_searches = max_searches
        self.n = 0
        self._lock = threading.Lock()

    def spend(self):
        return self.n * COST_PER_SEARCH

    def try_charge(self):
        with self._lock:
            if self.n >= self.max_searches:
                return False
            self.n += 1
            return True


def exa_search(query, api_key, num_results=5, timeout=30):
    r = requests.post(EXA_URL, headers={"x-api-key": api_key, "Content-Type": "application/json"},
                       json={"query": query, "type": "auto", "numResults": num_results}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, CACHE_PATH)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_clinics():
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H_ = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    r = requests.get(url + "/rest/v1/clinics?select=clinic_id,name,town,status,website,careers_url,ats_type&limit=1000",
                     headers=H_, timeout=60)
    r.raise_for_status()
    return r.json()


def load_open_clinic_ids():
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H_ = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    ids, offset = set(), 0
    while True:
        r = requests.get(url + "/rest/v1/v_postings", params={"select": "clinic_id", "status": "eq.open",
                                                                "limit": 1000, "offset": offset}, headers=H_, timeout=60)
        r.raise_for_status()
        rows = r.json()
        ids.update(row["clinic_id"] for row in rows if row.get("clinic_id"))
        if len(rows) < 1000:
            return ids
        offset += 1000


def cohort_of(clinics, open_ids):
    """Blank careers_url AND zero open postings, minus sites the plan already excludes from any
    scope='all' crawl (app/targets.py:clinics_for drops status='nicht_mehr_im_plan')."""
    return [c for c in clinics
            if not (c.get("careers_url") or "").strip()
            and c["clinic_id"] not in open_ids
            and c.get("status") != "nicht_mehr_im_plan"]


# ---------------------------------------------------------------------------
# per-clinic discovery
# ---------------------------------------------------------------------------
def discover_one(c, town_counts, budget, api_key, session=None):
    """Run shape A, then shape B only if A produced nothing acceptable. Returns
    (clinic, decision_dict or None, raw_results_by_shape)."""
    session = session or requests.Session(); session.headers.update(H)
    nm = clean_name(c["name"])
    town = town_for_query(c.get("town"), nm, town_counts)
    qa, qb = build_queries(nm, town)
    raw = {}

    for shape, q in (("A", qa), ("B", qb)):
        if not budget.try_charge():
            return c, {"decision": "budget_exhausted"}, raw
        try:
            resp = exa_search(q, api_key)
        except Exception as e:
            raw[shape] = {"query": q, "error": str(e)[:200]}
            continue
        results = resp.get("results") or []
        raw[shape] = {"query": q, "results": results, "cost": resp.get("costDollars")}

        cands = [r for r in results if r.get("url") and not is_aggregator(r["url"]) and not SKIP_EXT.search(r["url"])]
        cands.sort(key=lambda r: affinity(c["name"], r["url"]), reverse=True)
        best = None
        for r in cands:
            page = get(r["url"], session=session)
            if page is None:
                continue
            if page.status_code in (403, 429):
                best = {"decision": "blocked", "careers_url": r["url"], "ats": None, "evidence": None,
                        "angle": "exa:" + shape, "query": q, "http": page.status_code}
                break
            if not page.ok:
                continue
            ok, ats, ev = accept(page.url, page.text, c["name"], town)
            if ok:
                best = {"decision": "accepted", "careers_url": page.url, "ats": ats, "evidence": ev,
                        "angle": "exa:" + shape, "query": q, "http": page.status_code}
                break
        if best:
            return c, best, raw
        # else: fall through to shape B (the for-loop's next iteration) -- that IS "B only when
        # A yielded no accepted candidate", no extra condition needed
    return c, None, raw


# ---------------------------------------------------------------------------
# write-back: probe rows (this module's jsonl output) -> pflege-ingest `clinics` op
#
# The inbox 'probe' path is dead for this module's output: writing an inbox row needs
# POST /rest/v1/inbox, which the ANON-level SUPABASE_URL proxy returns 42501 for, and the edge
# function has no inbox INSERT op. So this writes straight to the `clinics` op instead of going
# through pflege_jobs.cli's inbox drain.
#
# Two guards on top of the edge function's own coalesce(nullif(excluded.col,''), stored):
#   (1) never overwrite a NON-EMPTY stored ats_type with a different discovered one -- the edge
#       function's coalesce only protects against *blanking*; a non-empty discovered value still
#       wins. A stored label is asserted by an earlier, presumably more specific pass; log the
#       conflict and keep the stored value instead of silently overwriting it.
#   (2) only accept a vendor label when the vendor host is confirmed by the candidate's OWN url,
#       or the match sits inside a <script> element or an <iframe src=...> -- never a bare
#       <a href="...">. fingerprint()'s patterns mix real vendor hostnames (recruitingapp-\d+,
#       concludis\.de, ...) with generic path/keyword fragments (pi_asp's `bewerber-web`, rexx's
#       `/stellenangebote\.html\?`) that a plain marketing page can pick up from a single outbound
#       link to someone else's board. careers_url is written regardless (the cohort's stored value
#       is always blank, so there is nothing to protect there) -- only ats_type is gated.
# ---------------------------------------------------------------------------
def tag_context(html, start):
    """Is position `start` in `html` inside a <script> element, or an <iframe ...> opening tag
    (where src= lives)? Returns 'script' | 'iframe' | None. Used to require a vendor-label match
    sit in one of those, not a bare <a href=...>."""
    window = html[:start]
    so, sc = window.rfind("<script"), window.rfind("</script")
    if so > sc:
        return "script"
    io = window.rfind("<iframe")
    if io != -1:
        end = html.find(">", io)
        if end != -1 and io <= start <= end:
            return "iframe"
    return None


def vendor_confirmed(ats, url, session=None):
    """Re-fetch `url` fresh (the jsonl's stored `evidence` is truncated to ~300 chars, too short to
    find the enclosing tag) and require fingerprint() to still find `ats`, sitting either in the
    URL itself or inside a <script>/<iframe src>. Returns (ok, why) for logging."""
    if not ats:
        return True, "no-label-claimed"
    pat = dict(ATS)[ats]
    if re.search(pat, url or "", re.I):
        return True, "url"
    r = get(url, session=session)
    if r is None or not r.ok:
        return False, f"refetch-failed({r.status_code if r is not None else 'error'})"
    ats2, _ = fingerprint(r.text, r.url)
    if ats2 != ats:
        return False, f"refetch-mismatch(now={ats2})"
    m = re.search(pat, r.text, re.I)
    ctx = tag_context(r.text, m.start()) if m else None
    return (ctx in ("script", "iframe")), (ctx or "bare-href/inline-text")


def load_live_clinics(clinic_ids):
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H_ = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    live = {}
    ids = sorted(set(clinic_ids))
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        q = ",".join(chunk)
        r = requests.get(url + "/rest/v1/clinics", params={"select": "*", "clinic_id": f"in.({q})"}, headers=H_, timeout=60)
        r.raise_for_status()
        for row in r.json():
            live[row["clinic_id"]] = row
    return live


def build_write_back(probe_rows, live, session=None, log=print):
    """-> (clinic_rows for EdgeSink.write_clinics, stats Counter). probe_rows are this module's own
    jsonl records (kind='probe', payload.probe='ats_discovery')."""
    from pflege_jobs.schema import CLINIC_SPEC
    stats = Counter()
    out = []
    for r in probe_rows:
        p = r["payload"]
        cid = p["clinic_id"]
        cur = live.get(cid)
        if not cur:
            stats["no_live_row"] += 1
            log(f"  {cid}: no live clinics row -- skipped")
            continue
        row = {k: cur.get(k) for k, _ in CLINIC_SPEC}
        changed = False

        stored_careers = (cur.get("careers_url") or "").strip()
        disc_careers = (p.get("careers_url") or "").strip()
        if not stored_careers and disc_careers:
            row["careers_url"] = disc_careers
            changed = True
            stats["careers_url_written"] += 1

        stored_ats = (cur.get("ats_type") or "").strip()
        disc_ats = p.get("ats")
        if disc_ats:
            if stored_ats and disc_ats != stored_ats:
                stats["ats_conflict_kept_stored"] += 1
                log(f"  {cid}: ats conflict -- stored={stored_ats!r} discovered={disc_ats!r} ({disc_careers}); keeping stored")
            elif stored_ats == disc_ats:
                stats["ats_already_matches"] += 1
            else:
                ok, why = vendor_confirmed(disc_ats, disc_careers, session=session)
                if ok:
                    row["ats_type"] = disc_ats
                    changed = True
                    stats["ats_type_written"] += 1
                    stats["ats_type_written:" + disc_ats] += 1
                else:
                    stats["ats_rejected_bare_evidence"] += 1
                    log(f"  {cid}: vendor label {disc_ats!r} rejected ({why}) -- {disc_careers}")
        if changed:
            out.append(row)
        else:
            stats["nothing_to_write"] += 1
    return out, stats


def write_back(path, dry_run=False, log=print):
    from pflege_jobs.sinks import EdgeSink
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    rows = [r for r in rows if r.get("kind") == "probe" and (r.get("payload") or {}).get("probe") == "ats_discovery"]
    log(f"{len(rows)} probe rows in {path}")
    live = load_live_clinics([r["payload"]["clinic_id"] for r in rows])
    session = requests.Session(); session.headers.update(H)
    out, stats = build_write_back(rows, live, session=session, log=log)
    log(f"\n{len(out)} clinics to upsert: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items()) if not k.startswith("ats_type_written:")))
    for k, v in sorted(stats.items()):
        if k.startswith("ats_type_written:"):
            log(f"   {k:<32} {v}")
    if dry_run:
        log("--dry-run: not posting")
        return out, stats
    sink = EdgeSink(batch=200)
    n = sink.write_clinics(out, log=log)
    log(f"edge clinics op reports {n} rows upserted")
    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-searches", type=int, default=120)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--from-cache", action="store_true", help="re-rank cached results, spend $0")
    ap.add_argument("--write", metavar="PATH", help="write back a previous run's jsonl output through the edge clinics op (guarded; see module docstring)")
    ap.add_argument("--dry-run", action="store_true", help="with --write: build and print the guarded rows but do not POST")
    a = ap.parse_args()

    if a.write:
        write_back(a.write, dry_run=a.dry_run)
        return

    clinics = load_clinics()
    open_ids = load_open_clinic_ids()
    cohort = cohort_of(clinics, open_ids)
    has_url = [c for c in clinics if (c.get("careers_url") or "").strip()]

    if a.report:
        retired = sum(1 for c in clinics
                      if not (c.get("careers_url") or "").strip() and c["clinic_id"] not in open_ids
                      and c.get("status") == "nicht_mehr_im_plan")
        print(f"{len(clinics)} clinics, {len(cohort) + retired} blank+no-open-postings "
              f"({retired} nicht_mehr_im_plan excluded) -> cohort {len(cohort)}")
        twins = twin_match(cohort, has_url)
        print(f"{len(twins)} twins (do not auto-write):")
        for c, w, ratio, prefix in twins:
            print(f"  {c['clinic_id']} <-> {w['clinic_id']}  ratio={ratio} prefix={prefix}  "
                  f"{c['name'][:50]!r} / {w['name'][:50]!r}")
        print(f"searchable: {len(cohort) - len(twins)}")
        return

    town_counts = Counter(c.get("town") for c in clinics if (c.get("town") or "").strip())
    twins = twin_match(cohort, has_url)
    twin_ids = {c["clinic_id"] for c, *_ in twins}
    print(f"{len(twins)} twins excluded (printed below, not auto-written):")
    for c, w, ratio, prefix in twins:
        print(f"  {c['clinic_id']} <-> {w['clinic_id']}  ratio={ratio} prefix={prefix}")

    todo = [c for c in cohort if c["clinic_id"] not in twin_ids]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} clinics to search")

    cache = load_cache()
    api_key = os.environ.get("EXA_API_KEY")
    budget = Budget(0 if a.from_cache else a.max_searches)
    if not a.from_cache and not api_key:
        print("EXA_API_KEY not set; nothing to do (use --from-cache to re-rank without spending)")
        return

    rows, stats = [], Counter()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(discover_one, c, town_counts, budget, api_key) for c in todo]
        for i, f in enumerate(as_completed(futs), 1):
            try:
                c, hit, raw = f.result()
            except Exception as e:
                print("  worker failed:", str(e)[:100]); continue
            if raw:
                cache[c["clinic_id"]] = raw
            if hit and hit.get("decision") in ("accepted", "blocked"):
                stats[hit["decision"]] += 1
                if hit.get("ats"):
                    stats["ats:" + hit["ats"]] += 1
                print(f"  [{i:3}/{len(todo)}] {c['name'][:44]:<44} -> {hit['decision']:<9} {hit['careers_url']}")
                rows.append({"kind": "probe", "source_host": urlparse(hit["careers_url"]).netloc,
                             "source_url": hit["careers_url"], "collector": "career-discover-exa-v1", "client_id": CID,
                             "payload": {"probe": "ats_discovery", "clinic_id": c["clinic_id"], "clinic_name": c["name"],
                                         "employer": c["name"], "ats": hit.get("ats"), "apply_url": None,
                                         "careers_url": hit["careers_url"], "angle": hit["angle"],
                                         "evidence": hit.get("evidence"), "query": hit.get("query"),
                                         "decision": hit["decision"]}})
            elif hit and hit.get("decision") == "budget_exhausted":
                stats["budget_exhausted"] += 1
            else:
                stats["no_candidate"] += 1
            save_cache(cache)                            # atomic per-clinic so a crash loses nothing

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "career_discover_exa_%s.jsonl" % time.strftime("%Y%m%dT%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nsearched {budget.n} queries (${budget.spend():.2f}) in {time.time()-t0:.0f}s: "
          f"{stats['accepted']} accepted, {stats['blocked']} blocked, {stats['no_candidate']} no candidate, "
          f"{stats['budget_exhausted']} budget-exhausted -> {path}")
    for k, v in sorted(stats.items()):
        if k.startswith("ats:"):
            print(f"   {k:<28} {v}")


if __name__ == "__main__":
    main()
