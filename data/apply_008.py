"""Apply sql/008_experienced_only.sql through PostgREST with the service-role key.

The project has no exec_sql RPC and we hold a service-role key, not a management token, so DDL
(CHECK constraints) cannot be issued from here — see README "Known limits". The data part of the
migration is expressed as ordered DELETEs, which service-role PostgREST does support. The
application-level gate (pflege_jobs/config.py:EXCLUDED_ROLE_CLASSES, enforced in sinks.only_pflege)
is what keeps these rows out going forward; this script removes what earlier runs already stored.

EXCLUDED mirrors pflege_jobs/patterns.json:excluded_role_classes -- pflegehelfer (assistants) joined
2026-09-07 alongside the trainee/intern/non-nursing classes, since the board's stated scope is
certified nursing staff only.

  python data/apply_008.py --dry-run     # report only
  python data/apply_008.py               # delete
"""
import argparse
import json
import os
import sys

import requests

EXCLUDED = ("ausbildung", "werkstudent_praktikum", "nicht_pflege", "pflegehelfer")
PROJECT = os.environ.get("SUPABASE_PROJECT_URL", "https://klkxfvieaxpjlplloljn.supabase.co")


def h(secret, write=False):
    x = {"apikey": secret, "Authorization": f"Bearer {secret}"}
    x["Content-Profile" if write else "Accept-Profile"] = "pflege_jobs"
    if write:
        x["Accept-Profile"] = "pflege_jobs"
    return x


def page(sec, path):
    out, off = [], 0
    while True:
        r = requests.get(f"{PROJECT}/rest/v1/{path}&limit=1000&offset={off}", headers=h(sec), timeout=180)
        if r.status_code != 200:
            raise SystemExit(f"query failed {r.status_code}: {r.text[:200]}")
        ch = r.json(); out += ch; off += len(ch)
        if len(ch) < 1000:
            return out


def delete_in(sec, table, col, ids, chunk=100):
    n = 0
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        q = ",".join(str(x) for x in batch)
        r = requests.delete(f"{PROJECT}/rest/v1/{table}?{col}=in.({q})",
                            headers={**h(sec, write=True), "Prefer": "count=exact"}, timeout=180)
        if r.status_code not in (200, 204):
            print(f"  delete {table} failed {r.status_code}: {r.text[:200]}"); continue
        n += len(batch)
    return n


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    sec = os.environ["SUPABASE_SECRET_KEY"]
    rc = ",".join(EXCLUDED)

    posts = page(sec, f"postings?select=posting_id,role_class,status&role_class=in.({rc})")
    obs_excl = page(sec, f"posting_observations?select=observation_id,posting_id,role_class&role_class=in.({rc})")
    post_ids = [p["posting_id"] for p in posts]
    obs_of_posts = page(sec, f"posting_observations?select=observation_id&posting_id=in.({','.join(map(str,post_ids))})") if post_ids else []

    from collections import Counter
    print(f"postings to delete    : {len(posts)}  {dict(Counter(p['role_class'] for p in posts))}")
    print(f"observations (by role): {len(obs_excl)}")
    print(f"observations (by post): {len(obs_of_posts)}")
    if a.dry_run:
        print("\n--dry-run: nothing deleted"); return

    obs_ids = sorted({o["observation_id"] for o in obs_excl} | {o["observation_id"] for o in obs_of_posts})
    print(f"\ndeleting {len(obs_ids)} observations ...")
    print("  deleted:", delete_in(sec, "posting_observations", "observation_id", obs_ids))
    print(f"deleting {len(post_ids)} postings ...")
    print("  deleted:", delete_in(sec, "postings", "posting_id", post_ids))

    # postings whose every observation just went away
    left = page(sec, "postings?select=posting_id")
    have = {o["posting_id"] for o in page(sec, "posting_observations?select=posting_id&posting_id=not.is.null")}
    orphans = [p["posting_id"] for p in left if p["posting_id"] not in have]
    print(f"orphan postings (no observations left): {len(orphans)}")
    if orphans:
        print("  deleted:", delete_in(sec, "postings", "posting_id", orphans))

    for t in ("postings", "posting_observations"):
        rest = page(sec, f"{t}?select=role_class&role_class=in.({rc})")
        print(f"verify {t:<22}: {len(rest)} excluded rows remain")


if __name__ == "__main__":
    main()
