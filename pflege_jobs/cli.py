"""CLI. Reusable for backfill AND recurring refresh (same code path, idempotent upserts).
Sources are hospital career sites only (employer_ats 20, firecrawl_agent 25); crawlers write inbox rows.

  python -m pflege_jobs.cli inbox                                                   # inbox rows -> observations (+ KeZ link, verify=live)
  python -m pflege_jobs.cli link-clinics                                            # registry push + posting -> KeZ links
  python -m pflege_jobs.cli link-cross                                              # merge the same job seen twice (url variants, title similarity)
  python -m pflege_jobs.cli verify --workers 6                                      # web-liveness check of all open postings
  python -m pflege_jobs.cli load  --inp data/obs.json --sink csv|sql|edge [--no-resolve]   # load a json {"observations":[...]} dump
  python -m pflege_jobs.cli load-board --csv data/board_snapshot.csv --sink edge   # career-site snapshot (source employer_ats)
  python -m pflege_jobs.cli purge-inbox [--days 30]                                # delete local-queue rows older than N days (maintenance, not part of any drain)
"""
import argparse, re
import json
import os
import sys
import time

from . import config as C
from . import inbox_db as IB
from .sinks import CsvSink, SqlSink, EdgeSink


def _rows(resp, what="query"):
    """PostgREST returns a JSON *object* {code,message} on error, a list on success.

    Unpacking the error object as if it were rows fails much later with a confusing
    'string indices must be integers', after the caller has already half-committed work — so turn
    it into an exception at the point it happens. Statement timeouts on big unfiltered scans are
    the usual cause; the fix is to filter the query, not to retry it.
    """
    try:
        data = resp.json()
    except Exception as e:
        raise SystemExit(f"{what}: HTTP {resp.status_code}, body is not JSON ({e})")
    if isinstance(data, list):
        return data
    raise SystemExit(f"{what}: HTTP {resp.status_code} {json.dumps(data)[:300]}")


def _load_inp(path):
    d = json.load(open(path, encoding="utf-8"))
    return d["observations"], d.get("slice_counts", {})


def cmd_load_board(a):
    """Import a pflege-board CSV snapshot as employer_ats observations (precedence 2)."""
    from .sources.board_csv import load_csv, city_coords_from
    coords = city_coords_from(_load_inp(a.inp)[0]) if a.inp and os.path.exists(a.inp) else {}
    obs = load_csv(a.csv, coords)
    print(f"board rows -> {len(obs)} observations; with coords {sum(1 for o in obs if o['lat'] is not None)}")
    s = _sink(a)
    kw = {"resolve": not a.no_resolve} if a.sink == "edge" else {}
    print(json.dumps(s.write(obs, **kw), ensure_ascii=False))


def cmd_verify(a):
    """Verify every open posting against the web (reads our API, writes via ingest function)."""
    import requests as rq
    from .verify import verify_all
    from .sinks import EdgeSink
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    rows, off = [], 0
    while True:
        q = f"{url}/rest/v1/v_postings?select=posting_id,title,source_codes,source_url,external_url,status&status=eq.open&order=posting_id&limit=1000&offset={off}"
        chunk = _rows(rq.get(q, headers=H, timeout=120), "v_postings"); rows += chunk; off += len(chunk)
        if len(chunk) < 1000: break
    if a.only_status:
        want = set(a.only_status.split(","))
        vs = {}; off = 0
        while True:
            q = f"{url}/rest/v1/postings?select=posting_id,verify_status&order=posting_id&limit=1000&offset={off}"
            chunk = _rows(rq.get(q, headers=H, timeout=120), "paged query"); off += len(chunk)
            for o in chunk: vs[o["posting_id"]] = o["verify_status"]
            if len(chunk) < 1000: break
        rows = [r for r in rows if vs.get(r["posting_id"]) in want]
    if a.limit: rows = rows[: a.limit]
    print(f"verifying {len(rows)} open postings via employer URL")
    t = time.time(); res = verify_all(rows, workers=a.workers)
    from collections import Counter
    print("result:", dict(Counter(x["verify_status"] for x in res)), f"in {time.time()-t:.0f}s")
    with open(a.out, "w", encoding="utf-8") as f: json.dump(res, f)
    if not a.dry_run:
        sink = EdgeSink(batch=400); n = 0
        for i in range(0, len(res), 400):
            n += sink._post({"verify": res[i:i+400]}).get("verify", 0)
        print(f"pushed {n} verification results")


def cmd_link_clinics(a):
    """Load registry CSV (Krankenhausplan) and link postings to sites; pushes clinics + clinic_links via ingest."""
    import csv, requests as rq
    from .registry import link_postings, merge_discovered
    from .sinks import EdgeSink
    clinics = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    for c in clinics:
        for k in ("beds", "day_places"): c[k] = int(c[k]) if c.get(k) not in (None, "") else None
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    rows, off = [], 0
    while True:
        # Read the base tables, not v_postings: the view adds a per-row "most authoritative source_url"
        # subquery over posting_observations that pushes this scan past the statement timeout as the
        # table grows. Nothing here needs that column.
        q = (f"{url}/rest/v1/postings?select=posting_id,city,clinic_match_rule,employers!inner(name_display,employer_class)"
             f"&employers.employer_class=in.(clinic,unknown)&order=posting_id&limit=1000&offset={off}")
        chunk = _rows(rq.get(q, headers=H, timeout=120), "postings+employers")
        rows += [{"posting_id": r["posting_id"], "city": r["city"],
                  "clinic_match_rule": r.get("clinic_match_rule"),
                  "employer": (r.get("employers") or {}).get("name_display"),
                  "employer_class": (r.get("employers") or {}).get("employer_class")} for r in chunk]
        off += len(chunk)
        if len(chunk) < 1000: break
    rows = [r for r in rows if r.get("clinic_match_rule") != "manual"]
    links = link_postings(rows, clinics)
    from collections import Counter
    print(f"postings considered {len(rows)}; linked {len(links)} ({100*len(links)/max(1,len(rows)):.0f}%); rules {dict(Counter(l['clinic_match_rule'] for l in links))}")
    if a.dry_run:
        json.dump(links, open(a.out, "w")); return
    sink = EdgeSink(batch=400)
    # `ats_type` / `careers_url` are discovered asynchronously (crawlers/ats_discover2.py) and also
    # live in this CSV. The edge upsert assigns every column it receives, and a column that is simply
    # *omitted* still arrives as NULL from json_to_recordset -- so omitting them (the previous guard)
    # erased the discovered labels just as surely as sending blanks did. Send the CSV value when we
    # have one, else whatever is already in the DB, so a registry push is never lossy.
    # (The deployed edge function still lacks the coalesce fix; see PLAN.md item 1.)
    try:
        live = _rows(rq.get(f"{url}/rest/v1/clinics?select=clinic_id,ats_type,careers_url&limit=2000",
                            headers=H, timeout=60), "clinics")
    except Exception as e:
        live = []
        print(f"  warning: could not read current ATS labels ({e}); sending CSV values as-is")
    merge_discovered(clinics, live)
    # TASK-86 review finding #2: this registry push is a real production write of careers_url (the
    # CSV can still carry a known job-detail-page URL a human hasn't corrected yet -- lint_csv flags
    # it), so it must go through the same sanctioned gate as career_discover_exa.py's and this file's
    # own ats-probe write-back, not straight through sink._post.
    n = sink.write_clinics([{k: v for k, v in c.items() if not k.startswith("_")} for c in clinics])
    m = 0
    for i in range(0, len(links), 400):
        m += sink._post({"clinic_links": links[i:i+400]}).get("clinic_links", 0)
    print(f"pushed clinics {n}, links {m}")


CANON = [
    (re.compile(r"^(https?://jobs\.smartrecruiters\.com/[^/]+/\d+)[-/].*$", re.I), r"\1"),           # strip slug after posting id
    (re.compile(r"^(https?://[^/]+\.softgarden\.(?:io|de)/jobs?/\d+)(?:[/?#].*)?$", re.I), r"\1"),  # softgarden: /job/<id>/<slug>?jobDbPVId=..&l=de
]
# Query parameters that never identify a job: campaign/click trackers and TYPO3's cHash cache token
# (karriere-im.klinikverbund-allgaeu.de emits the same job with and without cHash).
NOISE_PARAM = re.compile(r"^(utm(_\w+)?|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|_ga|_gl|chash|lang|language|locale|portfoliocats)$", re.I)
PLAIN_ANCHOR = re.compile(r"^[A-Za-z_-]*$")                                                         # '#top', '#content' -- not '#position,id=73'


def _noise_param(k, v):
    """`ref=homepage` (klinikum-landsberg) is a referrer marker, but `ref` is also a common name for a job
    reference number -- treat it as noise only when the value is a plain word."""
    return bool(NOISE_PARAM.match(k)) or (k.lower() == "ref" and re.fullmatch(r"[A-Za-z_-]+", v or "") is not None)


def canonical_ref(url):
    """Same-source identity of a job URL, used to fold URL *variants* of one posting (link-cross pass 1).

    Conservative by design: within one source identity is (source_id, source_ref), so folding two refs
    means asserting they are the same page. Many hospital ATSs carry the job id ONLY in the query
    string or fragment (`index.php?ac=jobad&id=613`, `jobad?prj=2618P932`, `index.html?detID=185`,
    `bewerber-web/?companyEid=1135#position,id=73`) -- the previous rule dropped both, which folded
    every job on such a board into one posting (2026-09-08: 27 distinct Helios/Bezirkskliniken jobs
    collapsed into postings 7416/5625/7543). Now only known tracking/cache parameters, plain anchors,
    the trailing slash and the smartrecruiters title slug are removed; parameter order is normalised.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    u = (url or "").strip()
    for rx, rep in CANON:
        u = rx.sub(rep, u)
    p = urlsplit(u)
    q = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _noise_param(k, v))
    frag = "" if PLAIN_ANCHOR.match(p.fragment or "") else p.fragment
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), re.sub(r"/+$", "", p.path).lower(), urlencode(q), frag))   # path case-folded (asklepios emits both), query/fragment kept verbatim


# These ATS platforms are frequently white-labelled behind the clinic's own vanity domain (a CNAME
# keeps the SaaS's own path shape, just under a different host -- pflege_jobs/sources/softgarden.py:
# find_host tries the clinic's own domain and the *.softgarden.io/*.career.softgarden.de one), or split
# across a TLD (personio <slug>.jobs.personio.de vs .com) or subdomain (jobs. vs api.smartrecruiters.com)
# -- canonical_ref (conservative by design, above) correctly treats the two as different pages since
# the netloc differs, so the same job opens two postings. Matched host-agnostically (except personio,
# where the alias IS the host) and folded on the platform's own numeric job id in same_source_variant_pairs.
_ATS_JOB_ID_RX = [
    re.compile(r"/jobs?/(\d{6,})(?:[/?#]|$)", re.I),                # softgarden: canonical host or vanity CNAME
    re.compile(r"\.jobs\.personio\.(?:de|com)/job/(\d+)", re.I),    # personio: .de/.com twin of the same tenant
    re.compile(r"smartrecruiters\.com/[^/]+/(\d{9,})", re.I),       # smartrecruiters: jobs./api. subdomain twin
]


def _ats_job_id(url):
    for rx in _ATS_JOB_ID_RX:
        m = rx.search(url or "")
        if m:
            return m.group(1)
    return None


def collapse_merge_chains(pairs):
    """Rewrite a pairs dst that is itself later merged away to its own final dst (dropping a genuine
    cycle rather than looping) -- one `merges` call must never ask the edge function to move
    observations onto, then delete, a posting that is also some other pair's destination: its single
    CTE hit a posting_observations FK violation doing exactly that (concrete case: clinic 77402,
    Klinik Krumbach/Kreiskliniken Guenzburg-Krumbach). Pure: [{src,dst}] in, same shape out.
    """
    dst_of = {p["src"]: p["dst"] for p in pairs}
    resolved = {}
    for src, dst in dst_of.items():
        seen, d = {src}, dst
        while d in dst_of and d not in seen:
            seen.add(d); d = dst_of[d]
        if d != src:
            resolved[src] = d
    return sorted(({"src": s, "dst": d} for s, d in resolved.items()), key=lambda x: (x["src"], x["dst"]))


def same_source_variant_pairs(observations):
    """Merge pairs for postings that hold URL variants of one job within one source.

    Groups by (source_id, canonical_ref(source_ref)); every posting in a multi-posting group is merged
    into the group's lowest posting_id (all of them, not just the highest -- so one run converges). A
    second grouping by (source_id, ats-platform numeric job id, fuzzy_key) folds a vanity-domain alias
    into its ATS-host twin (see _ats_job_id above); gated on fuzzy_key (already employer+city+title-
    specific) so two unrelated boards never fold just because a numeric id coincides.

    One posting_id can land in both groups at once (its own URL-variant group AND an ats-job-id group
    -- e.g. its fuzzy_key drifted across re-crawls of the same URL), each with a different min -- so
    every group is folded through one shared union-find rather than resolved independently: resolving
    independently built two {src: dst} pairs for that one posting_id, and collapse_merge_chains'
    dst_of dict silently kept only the last one, dropping a real duplicate-posting merge instead of
    erroring on it. Union-find makes every posting_id in a connected component agree on one final
    (lowest) destination no matter how many groups tie it to that component.
    Pure: takes rows with posting_id/source_id/source_ref(/fuzzy_key), returns [{src, dst}] sorted by src.
    """
    from collections import defaultdict
    groups = defaultdict(set)
    for o in observations:
        if not o.get("posting_id"):
            continue
        groups[(o["source_id"], canonical_ref(o["source_ref"]))].add(o["posting_id"])
        jid = _ats_job_id(o.get("source_ref"))
        if jid and o.get("fuzzy_key"):
            groups[(o["source_id"], "ats:" + jid, o["fuzzy_key"])].add(o["posting_id"])
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for ps in groups.values():
        ps = sorted(ps)
        for p in ps[1:]:
            union(ps[0], p)
    pairs = [{"src": pid, "dst": find(pid)} for pid in parent if find(pid) != pid]
    return collapse_merge_chains(sorted(pairs, key=lambda x: (x["src"], x["dst"])))


def source_codes_by_posting(obs_rows, source_code_by_id):
    """TASK-124: v_postings.source_codes is a per-row correlated subquery Postgres evaluates for every
    row matching a query's WHERE clause before that query's own ORDER BY/LIMIT/OFFSET apply (confirmed
    live via EXPLAIN ANALYZE: dropping it from a SELECT cut buffer reads 12x on a 2240-row eligible
    set, and neither keyset pagination nor a supporting index changed that -- the view's Subquery
    Scan + a blocking Sort node run it regardless). This computes the same {posting_id: [sorted
    codes]} shape client-side, from posting_observations rows already fetched for exactly the
    posting_ids a page needs (not the whole eligible table) and the small, cacheable sources table.
    Pure: no network, no ordering assumptions on the inputs."""
    from collections import defaultdict
    out = defaultdict(set)
    for o in obs_rows:
        code = source_code_by_id.get(o["source_id"])
        if code:
            out[o["posting_id"]].add(code)
    return {pid: sorted(codes) for pid, codes in out.items()}


def cmd_link_cross(a):
    """Dedupe: (1) same-source URL variants (canonical URL equal) -> merge; (2) cross-source: same clinic_id + city + similar
    title (Jaccard>=0.6 or overlap>=0.9 w/ 3 shared tokens) between two different sources (employer_ats, firecrawl_agent) -> merge."""
    import requests as rq
    from collections import defaultdict
    from .classify import norm_text
    from .sinks import EdgeSink
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    sink = EdgeSink(batch=400)
    # (1) same-source URL variants
    obs, off = [], 0
    while True:
        q = f"{url}/rest/v1/posting_observations?select=posting_id,source_id,source_ref,fuzzy_key&order=observation_id&limit=1000&offset={off}"
        ch = _rows(rq.get(q, headers=H, timeout=120), "observations"); obs += ch; off += len(ch)
        if len(ch) < 1000: break
    pairs = same_source_variant_pairs(obs)
    n1 = 0
    if a.dry_run:
        json.dump(pairs, open(a.out.replace(".json", "_url_variants.json"), "w"))
    else:
        for i in range(0, len(pairs), 400): n1 += int(sink._post({"merges": pairs[i:i+400]}).get("merges") or 0)
    print(f"same-source url variants: {len(pairs)} pairs, merged {n1}" + (" (dry-run)" if a.dry_run else ""))
    # (2) cross-source by title similarity
    rows, off = [], 0
    while True:
        q = f"{url}/rest/v1/v_postings?select=posting_id,title,city,clinic_id,n_observations&status=eq.open&clinic_id=not.is.null&order=posting_id&limit=1000&offset={off}"
        ch = _rows(rq.get(q, headers=H, timeout=120), "v_postings"); rows += ch; off += len(ch)
        if len(ch) < 1000: break
    # TASK-124: v_postings.source_codes is a per-row correlated subquery (posting_observations JOIN
    # sources) -- confirmed live 2026-09-24 via EXPLAIN ANALYZE that Postgres evaluates it for EVERY
    # row matching this query's WHERE clause before this query's own ORDER BY/LIMIT/OFFSET are applied
    # (the view sits behind a Subquery Scan + a blocking Sort node, so neither a bigger page, keyset
    # pagination on posting_id, nor a supporting partial index changes this -- all three were tried
    # live against the real table and none stopped the subquery from running over the whole eligible
    # set on every page). Dropping it from this SELECT cut buffer reads 12x on a 2240-row eligible set
    # (15256 -> 1312) with zero rows removed; fetched here instead as a targeted, index-backed query
    # scoped to exactly the posting_ids already paginated above, cost O(page) not O(whole table).
    src_codes = {s["source_id"]: s["code"] for s in _rows(rq.get(f"{url}/rest/v1/sources?select=source_id,code", headers=H, timeout=30), "sources")}
    obs_rows, ids = [], [r["posting_id"] for r in rows]
    for i in range(0, len(ids), 300):
        chunk = ids[i:i + 300]
        off2 = 0
        while True:
            q = (f"{url}/rest/v1/posting_observations?select=posting_id,source_id"
                 f"&posting_id=in.({','.join(str(x) for x in chunk)})&limit=1000&offset={off2}")
            ch2 = _rows(rq.get(q, headers=H, timeout=60), "posting_observations")
            obs_rows += ch2; off2 += len(ch2)
            if len(ch2) < 1000: break
    codes_by_posting = source_codes_by_posting(obs_rows, src_codes)
    for r in rows:
        r["source_codes"] = codes_by_posting.get(r["posting_id"], [])
    STOP = {"m","w","d","und","oder","für","in","der","die","das","mit","als","im","am","bzw","vollzeit","teilzeit","voll","teil","zeit","ab","sofort","unbefristet","befristet","stunden","std","unser","unsere","unseren","gesucht","zum","nächstmöglichen","zeitpunkt","wir","suchen","sie","eine","einen","ein","mitarbeiter","mitarbeiterin","pflegedienst"}
    def tk(t):
        t = re.sub(r"\((?:m|w|d|x|i|gn|\s|/|\*|:)+\)", " ", norm_text(t)); t = re.sub(r"[^\wäöüß ]", " ", t)
        return {x for x in t.split() if len(x) > 2 and x not in STOP}
    RANK = {code: v["precedence"] for code, v in C.SOURCES.items()}          # merge lower-precedence into higher; ties -> lower posting_id
    grp = defaultdict(list)
    for r in rows: grp[(r["clinic_id"], norm_text(r["city"] or "").split(",")[0])].append(r)
    pairs = []
    for g in grp.values():
        g = sorted(g, key=lambda r: (min((RANK.get(c, 9) for c in (r["source_codes"] or [])), default=9), r["posting_id"]))
        used = set()
        for i, y in enumerate(g):                       # y = candidate destination (more authoritative)
            ty = tk(y["title"])
            for x in g[i+1:]:
                if x["posting_id"] in used or set(x["source_codes"] or []) & set(y["source_codes"] or []): continue   # never merge same-source here
                tx = tk(x["title"])
                if not tx or not ty: continue
                j = len(tx & ty) / len(tx | ty); o = len(tx & ty) / min(len(tx), len(ty))
                if j >= 0.6 or (o >= 0.9 and len(tx & ty) >= 3):
                    used.add(x["posting_id"]); pairs.append({"src": x["posting_id"], "dst": y["posting_id"]})
    # `used` above only ever guards the src side, so a posting already chosen as a *destination* for
    # one pair can still be picked as the *source* of another (y iterates every posting in the group,
    # unfiltered) -- collapse the resulting chain before it reaches one `merges` call (AC3 above).
    pairs = collapse_merge_chains(pairs)
    print(f"cross-source pairs {len(pairs)}")
    if a.dry_run: json.dump(pairs, open(a.out, "w")); return
    n = 0
    for i in range(0, len(pairs), 400): n += int(sink._post({"merges": pairs[i:i+400]}).get("merges") or 0)
    print("merged", n, "resolve:", sink._post({"resolve": True}).get("resolve"))


# A path that names one specific job posting rather than a listing/board page. Used to reject an
# ats-discovery probe's candidate careers_url: 44 of 46 probe rows have payload.ats null (careers-only
# discovery is intentional, see ats_discover2.py stats['careers_only']), but ~10 of those wrote a
# job-DETAIL url (payload.careers_url, apply_url is null in 44/46) as if it were the board itself.
JOB_DETAIL_RX = re.compile(r"/(stellenanzeigen?|stellenangebote?|job|stelle)/[^/?#]+|-j\d+\.html|/Job/\d+", re.I)


def lookup_posting_ids(get, url, H, observations, chunk=50, log=print):
    """posting_id of each observation we just wrote, keyed by (source_id, source_ref).

    Only the refs just written are queried, in chunks, per source (a full employer_ats scan used to
    trip the anon statement_timeout). Two things this must get right, both regressions seen live on
    2026-09-08 (Firecrawl run for clinic 56202, postings 10201-10204 left with clinic_id NULL):
      * refs go through `params=` so PostgREST sees them URL-encoded -- interpolated into the query
        string, a ref like `index.php?ac=jobad&id=968` split the filter at `&` and every batch came
        back `400 PGRST100 "failed to parse filter"`, silently skipping all links and verify marks;
      * the same URL may be observed by two sources (employer_ats 20 and firecrawl_agent 25 both saw
        `...&id=745`), so the filter is per source_id and the result keyed by (source_id, source_ref)
        -- keyed by ref alone the row of the *other* source could win and mis-link the posting.
    `get` is requests.get-compatible (injected for tests). Returns {(source_id, source_ref): posting_id}.
    """
    from collections import defaultdict
    ids = {}
    by_src = defaultdict(list)
    for o in observations:
        by_src[o["source_id"]].append(o["source_ref"])
    for sid, refs in by_src.items():
        for i in range(0, len(refs), chunk):
            batch = refs[i:i + chunk]
            q = ",".join('"' + s.replace('"', '\\"') + '"' for s in batch)
            try:
                resp = get(f"{url}/rest/v1/posting_observations",
                           params={"select": "posting_id,source_id,source_ref", "source_id": f"eq.{sid}", "source_ref": f"in.({q})"},
                           headers=H, timeout=120)
                ch = resp.json()
            except Exception as e:
                log(f"  ref lookup failed ({e}); skipping {len(batch)} refs"); continue
            if not isinstance(ch, list):        # PostgREST returns {code,message} on error
                log(f"  ref lookup error: {str(ch)[:120]}"); continue
            for x in ch:
                if x.get("posting_id"):
                    ids[(x["source_id"], x["source_ref"])] = x["posting_id"]
    return ids


def _drain_once(a, url, H, m, towns, resolve=True, stats=None, link_candidates=None):
    """Fetch and process one page (<=1000 rows) of the Postgres inbox queue -- the queue for
    producers that hold only the anon key (web/collect.html, POST /api/ingest, the Firecrawl
    webhook). The crawler's own rows go through the local queue below. Returns rows read."""
    import requests as rq
    rows = _rows(rq.get(f"{url}/rest/v1/inbox?select=*&processed_at=is.null&order=inbox_id&limit=1000", headers=H, timeout=120), "inbox")
    return _process_rows(rows, a, url, H, m, towns, _ack_postgres, "postgres", resolve=resolve, stats=stats, link_candidates=link_candidates)


def _drain_local_once(a, url, H, m, towns, resolve=True, stats=None, link_candidates=None):
    """Same processing over one page of the local SQLite queue (pflege_jobs.inbox_db), where the
    crawler now puts every row it finds, unfiltered. Returns rows read."""
    rows = IB.pending(1000, path=a.inbox_db)
    return _process_rows(rows, a, url, H, m, towns, lambda acks: IB.ack(acks, path=a.inbox_db), "sqlite", resolve=resolve, stats=stats, link_candidates=link_candidates)


def _ack_postgres(acks):
    sink, acked = EdgeSink(batch=200), 0
    for i in range(0, len(acks), 400):
        acked += sink._post({"inbox_ack": acks[i:i + 400]}).get("inbox_ack", 0)
    return acked


def _city_inherited(o):
    """True when this observation's city was copied from the seed clinic's own registry town rather
    than read off the posting (TASK-81 mechanism #3), so Matcher.match must not let it fabricate
    agreement with that seed via R0_board_name/R0_board_town. pflege_jobs/sources/inbox.py buries the
    city_source='seed' marker inside the jobposting branch's serialized payload; a seeded-adapter
    observation that sets it would carry it as a plain top-level key, the same as _emp_inherited
    already does for that branch."""
    if o.get("city_source") == "seed":
        return True
    payload = o.get("payload")
    if isinstance(payload, str):
        try:
            return json.loads(payload).get("city_source") == "seed"
        except ValueError:
            return False
    return False


def _process_rows(rows, a, url, H, m, towns, ack_fn, queue="postgres", resolve=True, stats=None, link_candidates=None):
    """One page of queued rows -> observations in Postgres. This is where filtering, matching and
    conversion happen for every queue: the crawler stores raw rows and nothing else decides what is
    kept, so a rule change can be replayed over the stored rows (inbox_db.reset).

    resolve/stats let a multi-page caller (cmd_inbox) defer resolve_postings() to one call at the
    end of the whole drain instead of once per page -- with the queue now ~9,000 rows (was tens),
    a page-by-page resolve fired 10x a night for the historically heaviest server-side operation in
    the log. stats, if given, is set stats['wrote']=True the first time this (or an earlier) page
    actually had observations to write, so the caller knows whether a final resolve is needed at all.

    link_candidates, if given, collects matched observations instead of looking up their posting_id
    and pushing clinic_links here. A brand-new posting has posting_id=NULL in posting_observations
    until resolve_postings() runs (sql/002_task73_migration.sql assigns it there); with resolve now
    deferred to one call at the end of the whole drain (both queues, every page), looking up the
    posting_id per-page here -- before that resolve has happened -- found nothing for every
    brand-new posting and silently dropped its clinic_links. The caller does the lookup+push once,
    after the single end-of-run resolve, over everything accumulated here across all pages/queues."""
    import requests as rq
    from urllib.parse import urlparse
    from .sources.inbox import jobposting_to_obs, NON_PROD_HOST
    obs, ack, probes = [], [], []
    for r in rows:
        if r["kind"] == "jobposting":
            if NON_PROD_HOST.search(urlparse(r.get("source_url") or "").netloc):
                ack.append({"inbox_id": r["inbox_id"], "note": "skipped: non-production host (staging/preview)"}); continue
            o = jobposting_to_obs(r, towns)
            if o["role_class"] in C.EXCLUDED_ROLE_CLASSES:
                ack.append({"inbox_id": r["inbox_id"], "note": f"skipped: {o['role_class']} (not an experienced nursing role)"}); continue
            if o["in_bavaria"] is False: ack.append({"inbox_id": r["inbox_id"], "note": "skipped: outside Bavaria"}); continue
            mt = m.match(o["employer_name"], o["city"], board=o.pop("_board", None), employer_inherited=o.pop("_emp_inherited", False),
                        city_inherited=_city_inherited(o), description=o.get("description"))
            o["_kez"] = mt[0] if mt else None; o["_rule"] = mt[1] if mt else None
            if o["_kez"]: o["employer_class"] = "clinic"; o["employer_class_rule"] = "registry_match|" + o["employer_class_rule"]
            obs.append(o); ack.append({"inbox_id": r["inbox_id"], "note": "loaded" + (f" -> {o['_kez']}" if o["_kez"] else " (no site match)")})
        elif r["kind"] == "observation":
            # Seeded adapters (softgarden/bite/umantis/pi_asp) already return a finished observation,
            # not a raw jobposting -- app/crawl.py._obs_row wraps it as-is so it is queued and
            # archived like every other row (2026-09-21 review: these rows never touched the queue at
            # all, and 21% of run 96 was dropped right here with no trace). The payload IS the
            # observation; apply the same three gates jobposting rows get, no jobposting_to_obs.
            o = dict(r["payload"])
            if NON_PROD_HOST.search(urlparse(o.get("source_url") or "").netloc):
                ack.append({"inbox_id": r["inbox_id"], "note": "skipped: non-production host (staging/preview)"}); continue
            if o.get("role_class") in C.EXCLUDED_ROLE_CLASSES:
                ack.append({"inbox_id": r["inbox_id"], "note": f"skipped: {o.get('role_class')} (not an experienced nursing role)"}); continue
            if o.get("in_bavaria") is False:
                ack.append({"inbox_id": r["inbox_id"], "note": "skipped: outside Bavaria"}); continue
            mt = m.match(o.get("employer_name"), o.get("city"), board=o.pop("_board", None), employer_inherited=o.pop("_emp_inherited", False),
                        city_inherited=_city_inherited(o), description=o.get("description"))
            # Unlike the jobposting branch, a seed can already carry its own _kez (e.g. a
            # bavaria_only_operator seed) -- keep it when the registry match itself finds nothing,
            # same as app/crawl.py's retired _load_observations did.
            o["_kez"] = (mt[0] if mt else None) or o.get("_kez")
            o["_rule"] = (mt[1] if mt else None) or ("seed_kez" if o.get("_kez") else None)
            if o["_kez"]: o["employer_class"] = "clinic"; o["employer_class_rule"] = "registry_match|" + (o.get("employer_class_rule") or "")
            obs.append(o); ack.append({"inbox_id": r["inbox_id"], "note": "loaded" + (f" -> {o['_kez']}" if o["_kez"] else " (no site match)")})
        elif r["kind"] == "probe" and (r["payload"] or {}).get("probe") == "ats_discovery":
            pl = r["payload"]; cid = pl.get("clinic_id")
            if not cid and pl.get("employer"):
                mt = m.match(pl["employer"], None); cid = mt[0] if mt else None
            cl, err = [], None
            if cid:
                # clinic_id is payload data (crawler/agent supplied), not a literal -- params= lets
                # requests URL-encode it, same as lookup_posting_ids. String-interpolating it into the
                # query broke PostgREST's filter parser on any clinic_id containing "&", and _rows()
                # turned that 400 into a SystemExit right here inside the row loop, before any ack --
                # one poison row then wedged every later drain forever (same row re-read, re-died).
                try:
                    resp = rq.get(f"{url}/rest/v1/clinics", params={"select": "*", "clinic_id": f"eq.{cid}"}, headers=H, timeout=60)
                    cl = resp.json()
                except Exception as e:
                    err = str(e)
                if err is None and not isinstance(cl, list):
                    err = str(cl)[:200]
                if err is not None:
                    ack.append({"inbox_id": r["inbox_id"], "note": f"probe: clinic lookup for clinic_id={cid!r} failed: {err[:200]}"})
                    continue
            if cl and (pl.get("ats") or pl.get("careers_url")) and (not cl[0].get("ats_type") or pl.get("ats") and pl.get("ats") != cl[0].get("ats_type")):
                from .schema import CLINIC_SPEC
                row = {k: cl[0].get(k) for k, _ in CLINIC_SPEC}; row["ats_type"] = pl.get("ats") or row.get("ats_type")
                cand = pl.get("apply_url") or pl.get("careers_url")
                if cand and JOB_DETAIL_RX.search(urlparse(cand).path):
                    cand = None  # looks like a single posting, not a listing page -- never overwrite careers_url with it
                row["careers_url"] = cand or row.get("careers_url")
                probes.append(row); ack.append({"inbox_id": r["inbox_id"], "note": f"ats set: {row['ats_type']} ({row['careers_url']})"})
            else:
                ack.append({"inbox_id": r["inbox_id"], "note": "probe: nothing to update"})
        else:
            ack.append({"inbox_id": r["inbox_id"], "note": f"{r['kind']}: {len((r['payload'] or {}).get('links', []))} links recorded (detail pages needed)"})
    print(f"{queue} inbox rows {len(rows)}: observations {len(obs)}, kez-linked {sum(1 for o in obs if o.get('_kez'))}")
    sink = EdgeSink(batch=200)
    if obs:
        if stats is not None:
            stats["wrote"] = True
        print("load:", sink.write(obs, resolve=resolve, log=lambda *_: None))
        if link_candidates is not None:
            link_candidates.extend(o for o in obs if o.get("_kez"))
        # No collector writing to this inbox is an actual browser that fetched *this* URL and got 200 --
        # every one is a crawler/adapter/agent (vendor-*, playwright-*, firecrawl-agent, ats-discover2,
        # career-discover-exa). Stamping verify_status=live/200 here was fabricated (248 rows in the
        # 2026-09-17 03:00 run alone). Real verification happens downstream: app/crawl.py's _verify_ids
        # right after this same drain when called from a crawl run, or the next `cli verify` full sweep
        # otherwise -- both leave verify_status NULL until then, which is honest.
    if probes:
        print("clinics ats updated:", sink.write_clinics(probes, log=lambda *_: None))
    if ack and not a.no_ack:
        print("acked", ack_fn(ack))
    return len(rows)


def _live_clinics(url, H):
    """Every row of the live clinics table -- paged the same way every other full-table read in this
    file is (cmd_verify, cmd_link_clinics), rather than assuming the ~450 rows always fit one
    PostgREST page (it hard-caps a single response at 1000 regardless of the limit param)."""
    import requests as rq
    rows, off = [], 0
    while True:
        chunk = _rows(rq.get(f"{url}/rest/v1/clinics?select=*&order=clinic_id&limit=1000&offset={off}",
                             headers=H, timeout=60), "clinics")
        rows += chunk; off += len(chunk)
        if len(chunk) < 1000: break
    return rows


def cmd_inbox(a):
    """Process queued raw rows -> observations (+ registry link), then ack them.

    Two queues, one processor: the local SQLite queue the crawler writes (pflege_jobs.inbox_db --
    every row it found, unfiltered) and the Postgres inbox, which stays for the producers holding
    only the anon key (web/collect.html, POST /api/ingest, the Firecrawl webhook).

    Pages through the whole queue (not just one 1000-row page): PostgREST hard-caps a single
    response at 1000 rows regardless of the limit param, so a batch of exactly 1000 means more
    may be waiting. Acked rows drop out of the processed_at=is.null filter, so no offset is
    needed between batches -- the same query just returns the next page.

    resolve_postings() runs once, after every page of both queues has drained, not once per page:
    the queue used to be tens of rows (1-2 pages); at ~9,000 rows it pages 10x, and a per-page
    resolve made this the heaviest server-side call in the log 10x a night instead of once.

    posting_id lookup + clinic_links push also happen once here, after that single resolve, not
    per page inside _process_rows: a brand-new posting has posting_id=NULL in posting_observations
    until resolve_postings() runs, so a per-page lookup -- before resolve had happened -- found
    nothing for every posting created this run and silently dropped its clinic_links (2026-09-21
    review). _process_rows only accumulates matched observations into link_candidates now.

    Registry source (TASK-80): --clinics defaults to None, which reads the live clinics table --
    the same source of truth app/data.py's D.clinics() and crawlers.routing.plan() already use to
    plan crawls. Every production call (app/crawl.py's `_cli(["inbox"])`) passes no --clinics, so it
    always got this. Before this fix the default was data/registry/clinics.csv, a hand-maintained
    copy that ATS/career discovery (the 'probe' branch below, and pflege_jobs/mechanics.py) writes
    straight to the live table and never back to -- 117 of 399 active clinics had drifted to a blank
    careers_url in the CSV while live had the real one, so the Matcher's board rules (R0_board*,
    pflege_jobs/registry.py) could not fire for postings on those clinics' boards. --clinics still
    accepts an explicit CSV path (tests use this to stay off the network).
    """
    import csv
    import requests as rq
    from .registry import Matcher
    from .classify import norm_text
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    if a.clinics:
        clinics = list(csv.DictReader(open(a.clinics, encoding="utf-8")))
        for c in clinics: c["beds"] = int(c["beds"]) if c.get("beds") else None
    else:
        clinics = _live_clinics(url, H)
    towns = {norm_text(c["town"]) for c in clinics if c.get("town")}
    m = Matcher([dict(c) for c in clinics])
    if a.reprocess_all or a.reprocess_run is not None:
        n = IB.reset(run_id=a.reprocess_run, path=a.inbox_db) if not a.reprocess_all else IB.reset(path=a.inbox_db)
        print(f"reprocess: {n} local row(s) unmarked"
              + (f" (run {a.reprocess_run})" if a.reprocess_run is not None else " (whole history)"))
    total = 0
    stats = {"wrote": False}
    link_candidates = []
    for drain in (_drain_local_once, _drain_once):
        for _ in range(a.max_batches):
            n = drain(a, url, H, m, towns, resolve=False, stats=stats, link_candidates=link_candidates)
            total += n
            if n < 1000 or a.no_ack:      # --no-ack never acks, so the same page would repeat forever
                break
        else:
            # max_batches is a loop-safety ceiling now, not a real queue-size cap (raised well past any
            # observed queue) -- if it's ever actually reached, the drain stopped with rows still
            # waiting and that must be loud, not a silent "inbox drained N rows" indistinguishable from
            # a real full drain (TASK-72 AC#2).
            print(f"TRUNCATED: max_batches={a.max_batches} reached with the queue still returning full "
                  f"batches -- the drain is incomplete", file=sys.stderr)
    if stats["wrote"]:
        print("resolve:", EdgeSink()._post({"resolve": True}).get("resolve"))
    if link_candidates:
        ids = lookup_posting_ids(rq.get, url, H, link_candidates)
        key_fn = lambda o: (o["source_id"], o["source_ref"])
        links = [{"posting_id": ids[key_fn(o)], "clinic_id": o["_kez"], "clinic_match_rule": o["_rule"], "clinic_match_score": 0.9}
                 for o in link_candidates if key_fn(o) in ids]
        if len(ids) < len(link_candidates):
            print(f"  warning: {len(link_candidates) - len(ids)} of {len(link_candidates)} written observations not found on re-read; their clinic links are skipped")
        sink = EdgeSink(batch=200)
        for i in range(0, len(links), 400): sink._post({"clinic_links": links[i:i + 400]})
        print(f"clinic links pushed: {len(links)}")
    print(f"inbox drained {total} rows")


def cmd_purge_inbox(a):
    """Delete local-queue rows older than --days (default 30, Ivan's 2026-09-21 decision on
    data/inbox.sqlite retention: an ordinary maintenance step, run separately from the crawl/drain
    path -- the crawl keeps writing every row unfiltered, this just ages old ones out. Age is
    received_at (enqueue time), not processed_at, so an unprocessed row does not live forever."""
    n = IB.purge_older_than(a.days, path=a.inbox_db)
    print(f"purged {n} row(s) older than {a.days} day(s) from {a.inbox_db or IB.PATH}")


def _sink(a):
    if a.sink == "csv": return CsvSink(a.out_dir)
    if a.sink == "sql": return SqlSink(a.out_dir)
    if a.sink == "edge": return EdgeSink()
    raise SystemExit("unknown sink")


def cmd_load(a):
    obs, counts = _load_inp(a.inp)
    if a.bavaria_only:
        obs = [o for o in obs if o.get("in_bavaria")]
    s = _sink(a)
    kw = {"resolve": not a.no_resolve} if a.sink == "edge" else {}
    print(json.dumps(s.write(obs, **kw), ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog="pflege_jobs")
    sp = p.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("load"); l.add_argument("--inp", default="data/obs.json"); l.add_argument("--sink", default="csv"); l.add_argument("--out-dir", default="data/out")
    l.add_argument("--no-resolve", action="store_true"); l.add_argument("--bavaria-only", action="store_true", default=True); l.set_defaults(fn=cmd_load)
    lb = sp.add_parser("load-board"); lb.add_argument("--csv", required=True); lb.add_argument("--inp", default="", help="optional observations json for city coords"); lb.add_argument("--sink", default="edge")
    lb.add_argument("--out-dir", default="data/out"); lb.add_argument("--no-resolve", action="store_true"); lb.set_defaults(fn=cmd_load_board)
    v = sp.add_parser("verify"); v.add_argument("--workers", type=int, default=6); v.add_argument("--limit", type=int, default=0)
    v.add_argument("--out", default="data/verify.json"); v.add_argument("--only-status", default=""); v.add_argument("--dry-run", action="store_true"); v.set_defaults(fn=cmd_verify)
    lc = sp.add_parser("link-clinics"); lc.add_argument("--csv", default="data/registry/clinics.csv"); lc.add_argument("--dry-run", action="store_true"); lc.add_argument("--out", default="data/clinic_links.json"); lc.set_defaults(fn=cmd_link_clinics)
    lx = sp.add_parser("link-cross"); lx.add_argument("--dry-run", action="store_true"); lx.add_argument("--out", default="data/cross_merge_pairs.json"); lx.set_defaults(fn=cmd_link_cross)
    ib = sp.add_parser("inbox")
    ib.add_argument("--clinics", default=None, help="registry CSV to match against instead of the live clinics table "
                     "(default: live, the same source crawl planning uses -- see cmd_inbox's docstring, TASK-80)")
    ib.add_argument("--no-ack", action="store_true")
    ib.add_argument("--max-batches", type=int, default=100_000)  # loop-safety ceiling, not a queue-size cap
    ib.add_argument("--inbox-db", default=None, help="local raw queue (default pflege_jobs/inbox_db.PATH)")
    # Replay: the raw rows are kept, so a classifier or matcher change can be re-run over them
    # instead of re-crawling the boards. --reprocess-all is the whole table, on purpose and never a default.
    ib.add_argument("--reprocess-run", type=int, default=None, help="unmark this crawl run's local rows and process them again")
    ib.add_argument("--reprocess-all", action="store_true", help="unmark every local row and process the whole history again")
    ib.set_defaults(fn=cmd_inbox)
    pg = sp.add_parser("purge-inbox", help="delete local-queue rows older than --days (Ivan, 2026-09-21: 30-day rotation)")
    pg.add_argument("--days", type=int, default=30)
    pg.add_argument("--inbox-db", default=None, help="local raw queue (default pflege_jobs/inbox_db.PATH)")
    pg.set_defaults(fn=cmd_purge_inbox)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
