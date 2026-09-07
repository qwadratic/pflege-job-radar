"""CLI. Reusable for backfill AND recurring refresh (same code path, idempotent upserts).
Sources are hospital career sites only (employer_ats 20, firecrawl_agent 25); crawlers write inbox rows.

  python -m pflege_jobs.cli inbox                                                   # inbox rows -> observations (+ KeZ link, verify=live)
  python -m pflege_jobs.cli link-clinics                                            # registry push + posting -> KeZ links
  python -m pflege_jobs.cli link-cross                                              # merge the same job seen twice (url variants, title similarity)
  python -m pflege_jobs.cli verify --workers 6                                      # web-liveness check of all open postings
  python -m pflege_jobs.cli load  --inp data/obs.json --sink csv|sql|edge [--no-resolve]   # load a json {"observations":[...]} dump
  python -m pflege_jobs.cli load-board --csv data/board_snapshot.csv --sink edge   # career-site snapshot (source employer_ats)
"""
import argparse, re
import json
import os
import sys
import time

from . import config as C
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
    n = 0
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
    for i in range(0, len(clinics), 400):
        batch = [{k: v for k, v in c.items() if not k.startswith("_")}
                 for c in clinics[i:i + 400]]
        n += sink._post({"clinics": batch}).get("clinics", 0)
    m = 0
    for i in range(0, len(links), 400):
        m += sink._post({"clinic_links": links[i:i+400]}).get("clinic_links", 0)
    print(f"pushed clinics {n}, links {m}")


CANON = [
    (re.compile(r"^(https?://jobs\.smartrecruiters\.com/[^/]+/\d+)[-/].*$", re.I), r"\1"),           # strip slug after posting id
    (re.compile(r"^(https?://[^?#]+?)/?(\?.*)?(#.*)?$"), r"\1"),                                        # drop query/fragment/trailing slash
]


def canonical_ref(url):
    u = url or ""
    for rx, rep in CANON:
        u = rx.sub(rep, u)
    return u.lower()


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
        q = f"{url}/rest/v1/posting_observations?select=posting_id,source_id,source_ref&order=observation_id&limit=1000&offset={off}"
        ch = _rows(rq.get(q, headers=H, timeout=120), "observations"); obs += ch; off += len(ch)
        if len(ch) < 1000: break
    groups = defaultdict(set)
    for o in obs: groups[(o["source_id"], canonical_ref(o["source_ref"]))].add(o["posting_id"])
    pairs = [{"src": max(ps), "dst": min(ps)} for ps in groups.values() if len(ps) > 1]
    n1 = 0
    for i in range(0, len(pairs), 400): n1 += int(sink._post({"merges": pairs[i:i+400]}).get("merges") or 0)
    print(f"same-source url variants: {len(pairs)} pairs, merged {n1}")
    # (2) cross-source by title similarity
    rows, off = [], 0
    while True:
        q = f"{url}/rest/v1/v_postings?select=posting_id,title,city,clinic_id,source_codes,n_observations&status=eq.open&clinic_id=not.is.null&order=posting_id&limit=1000&offset={off}"
        ch = _rows(rq.get(q, headers=H, timeout=120), "v_postings"); rows += ch; off += len(ch)
        if len(ch) < 1000: break
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


def _drain_once(a, url, H, m, towns):
    """Fetch and process one page (<=1000 rows) of the inbox queue. Returns the number of rows read."""
    import requests as rq
    from urllib.parse import urlparse
    from .sources.inbox import jobposting_to_obs
    rows = _rows(rq.get(f"{url}/rest/v1/inbox?select=*&processed_at=is.null&order=inbox_id&limit=1000", headers=H, timeout=120), "inbox")
    obs, ack, probes = [], [], []
    for r in rows:
        if r["kind"] == "jobposting":
            o = jobposting_to_obs(r, towns)
            if o["role_class"] in C.EXCLUDED_ROLE_CLASSES:
                ack.append({"inbox_id": r["inbox_id"], "note": f"skipped: {o['role_class']} (not an experienced nursing role)"}); continue
            if o["in_bavaria"] is False: ack.append({"inbox_id": r["inbox_id"], "note": "skipped: outside Bavaria"}); continue
            mt = m.match(o["employer_name"], o["city"])
            o["_kez"] = mt[0] if mt else None; o["_rule"] = mt[1] if mt else None
            if o["_kez"]: o["employer_class"] = "clinic"; o["employer_class_rule"] = "registry_match|" + o["employer_class_rule"]
            obs.append(o); ack.append({"inbox_id": r["inbox_id"], "note": "loaded" + (f" -> {o['_kez']}" if o["_kez"] else " (no site match)")})
        elif r["kind"] == "probe" and (r["payload"] or {}).get("probe") == "ats_discovery":
            pl = r["payload"]; cid = pl.get("clinic_id")
            if not cid and pl.get("employer"):
                mt = m.match(pl["employer"], None); cid = mt[0] if mt else None
            cl = _rows(rq.get(f"{url}/rest/v1/clinics?select=*&clinic_id=eq.{cid}", headers=H, timeout=60), "clinics") if cid else []
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
    print(f"inbox rows {len(rows)}: observations {len(obs)}, kez-linked {sum(1 for o in obs if o.get('_kez'))}")
    sink = EdgeSink(batch=200)
    if obs:
        print("load:", sink.write(obs, resolve=True, log=lambda *_: None))
        ids = {}
        # Look up only the refs we just wrote, in chunks, instead of scanning every employer_ats
        # observation: the full scan grew past the anon role's statement_timeout as the
        # table filled up, and a timeout here silently cost us the clinic links and verify marks.
        want = [o["source_ref"] for o in obs]
        for i in range(0, len(want), 50):
            batch = want[i:i + 50]
            q = ",".join('"' + s.replace('"', '\\"') + '"' for s in batch)
            try:
                resp = rq.get(f"{url}/rest/v1/posting_observations?select=posting_id,source_ref&source_ref=in.({q})",
                              headers=H, timeout=120)
                ch = resp.json()
            except Exception as e:
                print(f"  ref lookup failed ({e}); skipping {len(batch)} refs"); continue
            if not isinstance(ch, list):        # PostgREST returns {code,message} on error
                print(f"  ref lookup error: {str(ch)[:120]}"); continue
            for x in ch:
                if x.get("posting_id"):
                    ids[x["source_ref"]] = x["posting_id"]
        links = [{"posting_id": ids[o["source_ref"]], "clinic_id": o["_kez"], "clinic_match_rule": o["_rule"], "clinic_match_score": 0.9} for o in obs if o["source_ref"] in ids and o.get("_kez")]
        ver = [{"posting_id": ids[o["source_ref"]], "verify_status": "live", "verify_http": 200, "verified_at": o["observed_at"], "verify_note": "collected in a user's browser"} for o in obs if o["source_ref"] in ids]
        for i in range(0, len(links), 400): sink._post({"clinic_links": links[i:i + 400]})
        for i in range(0, len(ver), 400): sink._post({"verify": ver[i:i + 400]})
    if probes:
        print("clinics ats updated:", sink._post({"clinics": probes}).get("clinics"))
    if ack and not a.no_ack:
        acked = 0
        for i in range(0, len(ack), 400): acked += sink._post({"inbox_ack": ack[i:i + 400]}).get("inbox_ack", 0)
        print("acked", acked)
    return len(rows)


def cmd_inbox(a):
    """Process browser-collector inbox rows -> observations (+ registry link), then ack them.

    Pages through the whole queue (not just one 1000-row page): PostgREST hard-caps a single
    response at 1000 rows regardless of the limit param, so a batch of exactly 1000 means more
    may be waiting. Acked rows drop out of the processed_at=is.null filter, so no offset is
    needed between batches -- the same query just returns the next page.
    """
    import csv
    from .registry import Matcher
    from .classify import norm_text
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    H = {"apikey": key, "Accept-Profile": "pflege_jobs"}
    clinics = list(csv.DictReader(open(a.clinics, encoding="utf-8")))
    towns = {norm_text(c["town"]) for c in clinics if c.get("town")}
    for c in clinics: c["beds"] = int(c["beds"]) if c.get("beds") else None
    m = Matcher([dict(c) for c in clinics])
    total = 0
    for _ in range(a.max_batches):
        n = _drain_once(a, url, H, m, towns)
        total += n
        if n < 1000 or a.no_ack:      # --no-ack never acks, so the same page would repeat forever
            break
    print(f"inbox drained {total} rows")


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
    ib = sp.add_parser("inbox"); ib.add_argument("--clinics", default="data/registry/clinics.csv"); ib.add_argument("--no-ack", action="store_true")
    ib.add_argument("--max-batches", type=int, default=20); ib.set_defaults(fn=cmd_inbox)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
