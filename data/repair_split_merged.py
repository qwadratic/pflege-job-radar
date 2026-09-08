"""Repair for the 2026-09-08 URL-variant merge bug (pflege_jobs.cli.canonical_ref dropped `?query`/`#fragment`,
so link-cross folded every job of a board whose ATS keeps the id there into ONE posting: 7416 = 27 Helios
jobs, 5625 = 10 Bezirkskliniken jobs, 7543 = 9 helixjobs jobs, ...).

Scope: OPEN postings with >= 2 distinct observation titles that split into >= 2 canonical URLs. Each canonical
URL (across sources -- the same URL seen by employer_ats and firecrawl_agent is one job) becomes one posting:
  * the group holding the posting's earliest observation keeps the posting_id;
  * a group whose URL already has a clean posting elsewhere (e.g. the Firecrawl twin 10204 of 5625's obs 33750)
    moves into that posting;
  * every other group gets a new posting (status open, verify 'live' at the crawl's observed_at -- the same
    convention cmd_inbox applies to freshly observed rows).
Then the ingest function's `resolve` rebuilds title/city/employer/n_observations for all postings, and every
touched posting is re-linked to its Krankenhausplan site with the registry Matcher (manual links kept).

Writes go to the DIRECT project host with SUPABASE_SECRET_KEY (pattern: data/apply_008.py); resolve goes
through the pflege-ingest edge function (EdgeSink). Reads are keyless.

  python data/repair_split_merged.py --dry-run     # plan only
  python data/repair_split_merged.py               # apply
"""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict, Counter

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pflege_jobs.cli import canonical_ref                      # noqa: E402
from pflege_jobs.classify import employer_norm                 # noqa: E402
from pflege_jobs.registry import Matcher                       # noqa: E402
from pflege_jobs.sinks import EdgeSink                         # noqa: E402

PROJECT = os.environ.get("SUPABASE_PROJECT_URL", "https://klkxfvieaxpjlplloljn.supabase.co")
READ = os.environ.get("SUPABASE_URL", "https://supabase.int.exe.xyz")
NOTE = "split from posting {src} (URL-variant merge repair 2026-09-08); observed on the career site at crawl time"


def h_read():
    return {"Accept-Profile": "pflege_jobs"}


def h_write(sec):
    return {"apikey": sec, "Authorization": f"Bearer {sec}", "Content-Profile": "pflege_jobs",
            "Accept-Profile": "pflege_jobs", "Content-Type": "application/json", "Prefer": "return=representation"}


def page(path):
    out, off = [], 0
    while True:
        r = requests.get(f"{READ}/rest/v1/{path}&limit=1000&offset={off}", headers=h_read(), timeout=180)
        d = r.json()
        if not isinstance(d, list):
            raise SystemExit(f"read {path}: {r.status_code} {str(d)[:200]}")
        out += d; off += len(d)
        if len(d) < 1000:
            return out


def patch(sec, table, filt, body):
    r = requests.patch(f"{PROJECT}/rest/v1/{table}?{filt}", headers=h_write(sec), data=json.dumps(body), timeout=180)
    if r.status_code not in (200, 204):
        raise SystemExit(f"patch {table}?{filt} failed {r.status_code}: {r.text[:200]}")
    return r.json() if r.status_code == 200 else []


def insert(sec, table, rows):
    r = requests.post(f"{PROJECT}/rest/v1/{table}", headers=h_write(sec), data=json.dumps(rows), timeout=180)
    if r.status_code not in (200, 201):
        raise SystemExit(f"insert {table} failed {r.status_code}: {r.text[:300]}")
    return r.json()


def load_matcher(path):
    clinics = list(csv.DictReader(open(path, encoding="utf-8")))
    for c in clinics:
        c["beds"] = int(c["beds"]) if c.get("beds") else None
    return Matcher([dict(c) for c in clinics])


def victims_of(posts, obs_by_post):
    """open postings whose observations carry >= 2 distinct titles AND >= 2 canonical URLs."""
    out = {}
    for pid, os_ in obs_by_post.items():
        p = posts.get(pid)
        if not p or p["status"] != "open":
            continue
        titles = {(o["title"] or "").strip() for o in os_}
        keys = {canonical_ref(o["source_ref"]) for o in os_}
        if len(titles) >= 2 and len(keys) >= 2:
            out[pid] = p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clinics", default=os.path.join(os.path.dirname(__file__), "registry", "clinics.csv"))
    ap.add_argument("--app", default="http://localhost:8501", help="running dashboard; cache refreshed after the repair ('' to skip)")
    a = ap.parse_args()

    posts = {p["posting_id"]: p for p in page("postings?select=posting_id,status,clinic_id,clinic_match_rule,clinic_match_score,employer_id,title,city,verify_status,fuzzy_key&order=posting_id")}
    obs = page("posting_observations?select=observation_id,posting_id,source_id,source_ref,title,fuzzy_key,employer_name,employer_name_norm,city,observed_at&posting_id=not.is.null&order=observation_id")
    obs_by_post = defaultdict(list)
    for o in obs:
        obs_by_post[o["posting_id"]].append(o)
    victims = victims_of(posts, obs_by_post)
    print(f"postings {len(posts)} ({Counter(p['status'] for p in posts.values())}); observations linked {len(obs)}")
    print(f"open postings with >= 2 distinct titles and >= 2 canonical urls (to split): {len(victims)}")
    mixed_title_only = [pid for pid, os_ in obs_by_post.items() if posts.get(pid, {}).get("status") == "open"
                        and len({(o['title'] or '').strip() for o in os_}) >= 2 and pid not in victims]
    print(f"  (open postings with >= 2 titles but ONE canonical url -- genuine variants, left alone): {len(mixed_title_only)} {sorted(mixed_title_only)}")

    # every observation, db-wide, whose canonical url appears in a victim -- twins in other postings join in
    keys = {canonical_ref(o["source_ref"]) for pid in victims for o in obs_by_post[pid]}
    groups = defaultdict(list)
    for o in obs:
        k = canonical_ref(o["source_ref"])
        if k in keys:
            groups[k].append(o)
    anchor = {pid: canonical_ref(min(obs_by_post[pid], key=lambda o: o["observation_id"])["source_ref"]) for pid in victims}
    anchor_of = defaultdict(list)
    for pid, k in anchor.items():
        anchor_of[k].append(pid)

    plan = []   # (key, home | None, origin victim, obs list)
    for k, os_ in groups.items():
        clean = {o["posting_id"] for o in os_} - set(victims)
        origin = next((o["posting_id"] for o in sorted(os_, key=lambda o: o["observation_id"]) if o["posting_id"] in victims), None)
        if clean:
            home = min(clean)
        elif k in anchor_of:
            home = min(anchor_of[k])
        else:
            home = None
        plan.append((k, home, origin, os_))
    n_new = sum(1 for _, home, _, _ in plan if home is None)
    moves = [(o, home) for _, home, _, os_ in plan for o in os_ if home is not None and o["posting_id"] != home]
    print(f"canonical groups {len(plan)}: keep-in-place {sum(1 for _, h, _, _ in plan if h is not None)}, new postings {n_new}, observation moves into existing postings {len(moves)}")
    for pid in sorted(victims):
        n_groups = len({canonical_ref(o["source_ref"]) for o in obs_by_post[pid]})
        print(f"  {pid}: {len(obs_by_post[pid])} obs -> {n_groups} jobs (clinic {victims[pid]['clinic_id']}, rule {victims[pid]['clinic_match_rule']})")
    if a.dry_run:
        print("\n--dry-run: nothing written")
        return

    sec = os.environ["SUPABASE_SECRET_KEY"]
    expired_before = {pid for pid, p in posts.items() if p["status"] == "expired"}

    # 1. new postings, one per homeless group
    created = {}
    for k, home, origin, os_ in plan:
        if home is not None:
            continue
        first = min(os_, key=lambda o: o["observation_id"])
        row = {"fuzzy_key": first["fuzzy_key"], "status": "open", "verify_status": "live", "verify_http": 200,
               "verified_at": max(o["observed_at"] for o in os_), "verify_note": NOTE.format(src=origin)}
        pid = insert(sec, "postings", [row])[0]["posting_id"]
        created[k] = pid
    print(f"created {len(created)} postings: {sorted(created.values())[:5]}..{max(created.values()) if created else '-'}")

    # 2. move observations
    by_home = defaultdict(list)
    for k, home, origin, os_ in plan:
        home = home if home is not None else created[k]
        for o in os_:
            if o["posting_id"] != home:
                by_home[home].append(o["observation_id"])
    n_moved = 0
    for home, oids in by_home.items():
        for i in range(0, len(oids), 100):
            chunk = oids[i:i + 100]
            patch(sec, "posting_observations", f"observation_id=in.({','.join(map(str, chunk))})", {"posting_id": home})
            n_moved += len(chunk)
    print(f"moved {n_moved} observations")

    # 3. victims left without observations (anchor claimed by another victim) -> delete
    still = {o["posting_id"] for o in page("posting_observations?select=posting_id&posting_id=not.is.null")}
    empty = [pid for pid in victims if pid not in still]
    if empty:
        r = requests.delete(f"{PROJECT}/rest/v1/postings?posting_id=in.({','.join(map(str, empty))})", headers=h_write(sec), timeout=180)
        print(f"deleted {len(empty)} emptied postings ({r.status_code}): {empty}")

    # 4. golden rebuild (title, employer, city, n_observations, provenance, fuzzy_key) via the ingest function
    print("resolve:", EdgeSink(batch=400)._post({"resolve": True}).get("resolve"))
    after = {p["posting_id"]: p for p in page("postings?select=posting_id,status&order=posting_id")}
    flipped = [pid for pid in expired_before if after.get(pid, {}).get("status") == "open"]
    if flipped:
        patch(sec, "postings", f"posting_id=in.({','.join(map(str, flipped))})", {"status": "expired"})
        print(f"restored status=expired on {len(flipped)} postings the resolve re-opened")

    # 5. clinic links for every touched posting (registry Matcher on the resolved employer + city)
    touched = sorted(set(victims) | set(created.values()) | {h for _, h, _, _ in plan if h is not None})
    m = load_matcher(a.clinics)
    rows = []
    for i in range(0, len(touched), 200):
        chunk = ",".join(map(str, touched[i:i + 200]))
        rows += page(f"v_postings?select=posting_id,employer,city,clinic_id&posting_id=in.({chunk})")
    origin_of = {}
    for k, home, origin, os_ in plan:
        origin_of[home if home is not None else created[k]] = origin
    n_link = 0; unlinked = []
    for r in rows:
        pid = r["posting_id"]; cur = posts.get(pid) or {}
        if cur.get("clinic_match_rule") == "manual" and pid in posts and pid not in created.values():
            continue                                             # hand-set link on a surviving posting: keep
        mt = m.match(r.get("employer"), r.get("city"))
        if mt:
            body = {"clinic_id": mt[0], "clinic_match_rule": mt[1], "clinic_match_score": mt[2]}
        else:
            # no registry match: inherit a hand-set link from the origin when the employer is the same
            org = posts.get(origin_of.get(pid)) or {}
            org_emp = next((o["employer_name_norm"] for o in obs_by_post.get(org.get("posting_id"), [])), None)
            if org.get("clinic_id") and org.get("clinic_match_rule") in (None, "manual") and org_emp and org_emp == employer_norm(r.get("employer") or ""):
                body = {"clinic_id": org["clinic_id"], "clinic_match_rule": f"manual_inherited:{org['posting_id']}", "clinic_match_score": org.get("clinic_match_score")}
            else:
                unlinked.append((pid, r.get("employer"), r.get("city"))); continue
        patch(sec, "postings", f"posting_id=eq.{pid}", body); n_link += 1
    print(f"clinic links written {n_link}; unlinked {len(unlinked)}: {unlinked[:10]}")

    # 6. verification
    posts2 = {p["posting_id"]: p for p in page("postings?select=posting_id,status,clinic_id&order=posting_id")}
    obs2 = page("posting_observations?select=observation_id,posting_id,source_id,source_ref,title&posting_id=not.is.null&order=observation_id")
    by2 = defaultdict(list)
    for o in obs2:
        by2[o["posting_id"]].append(o)
    left = victims_of(posts2, by2)
    print(f"\nafter: postings {len(posts2)} ({Counter(p['status'] for p in posts2.values())}); open postings still mixed: {len(left)} {sorted(left)}")
    for cid in ("56202", "17401", "67804"):
        n = sum(1 for p in posts2.values() if p["status"] == "open" and p["clinic_id"] == cid)
        print(f"  clinic {cid}: jobs_open {n}")
    if a.app:
        try:
            requests.post(f"{a.app}/api/refresh-cache", timeout=300)
            for cid in ("56202", "17401", "67804"):
                d = requests.get(f"{a.app}/api/clinics/{cid}", timeout=60).json()
                print(f"  app /api/clinics/{cid}: jobs_open {d.get('jobs_open')} ({d.get('name')})")
        except Exception as e:
            print(f"  app refresh skipped: {e}")


if __name__ == "__main__":
    main()
