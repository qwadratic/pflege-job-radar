"""TASK-177 one-off relabel backfill. Run from /home/exedev/repo after the classifier change (cls.patch) is
deployed and pflege-web restarted. Ivan approved 2026-09-29 (option A: relabel, and the board hides
excluded classes -- app/data.py _build()).

Rows: the 146 postings (134 open + 12 expired) in data/relabel_task177_backfill_set.json -- the replay-measured
set whose stored role_class is a kept class while the patched classify_role() returns an excluded one. Each
is re-classified here from its LIVE title with the deployed code; a posting that does not come out excluded
stops the run (patch not deployed, or the title changed).

Mechanism: one transaction over the Supavisor pooler (SUPABASE_DB_POOLER_URL):
  UPDATE posting_observations SET role_class, role_rule  (all 154 observations of those postings -- what
         resolve_postings() folds into the golden row, so the nightly resolve keeps the new value)
  UPDATE postings SET role_class, role_rule, updated_at   (the golden row, so the board changes now)
  INSERT pflege_jobs.corrections, code role_misclassified, one row per changed postings field
status is NOT touched (relabel, not retire). Read-back inside the transaction; commit only if every row
matches, so the change and its reasons land together or not at all. Backup of both tables' rows first;
the observations' own before-values are in that backup.

  set -a; source .env; set +a
  .venv/bin/python data/relabel_task177_backfill.py                       # dry run: plan only
  .venv/bin/python data/relabel_task177_backfill.py --push --by "<who>"
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.environ.get("RELABEL_ROOT", "/home/exedev/repo")   # override only to dry-run a worktree
sys.path.insert(0, ROOT)
import psycopg2                                   # noqa: E402
from pflege_jobs import classify as K, config as C  # noqa: E402
from tools import ledger as L                     # noqa: E402

WHY = {"code": "role_misclassified",
       "reason": "Ivan 2026-09-29: Kinderpfleger/-in, Heilerziehungspfleger/-in, Landschafts-/Park-/Garten-/Anlagenpflege, "
                 "Tierpfleger, Raumpfleger, Fußpflege/Podologie/Kosmetik, Tagespflegeperson (Kita) and Patientenverpflegung "
                 "are not nursing (nursing jobs only). classify_role now excludes these titles (strong_pflege no longer "
                 "counts a -pfleg- word that is itself a nicht_pflege term); stored rows keep the old class because "
                 "intake skips excluded rows instead of re-writing them. Relabel only, status unchanged; the board "
                 "hides excluded classes (option A), so the rows stay in the DB and come back if the rule changes.",
       "evidence": ["TASK-177 notes: old-vs-new classify_role replay over all 3836 open postings (2026-09-29), "
                    "134 open + 12 expired postings flip to an excluded class, no genuine nursing title flips",
                    "tests/test_mech_role_class.py::test_hep_kinderpfleger_and_gardening_pfleger_titles_are_not_nursing",
                    "tests/test_mech_role_class.py::test_catering_grounds_animal_cleaning_cosmetic_and_childminder_titles_are_not_nursing",
                    "tests/test_onapply.py::test_grounds_keeping_titles_are_not_nursing (gardening rule)"],
       "task": "TASK-177"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=os.path.join(ROOT, "data", "relabel_task177_backfill_set.json"))
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--by")
    a = ap.parse_args()
    if a.push and not (a.by or "").strip():
        sys.exit("--push needs --by (pflege_jobs.corrections records who made the change)")
    ids = sorted(p["posting_id"] for p in json.load(open(a.set))["postings"])
    conn = psycopg2.connect(os.environ["SUPABASE_DB_POOLER_URL"])
    why = L.require_why(WHY, "TASK-177 relabel", L.reason_codes("postings", conn))
    cur = conn.cursor()
    cur.execute("select posting_id, title, status, role_class, role_rule, coalesce(hauptberuf,''), coalesce(offer_kind,'') "
                "from pflege_jobs.postings where posting_id = any(%s) order by posting_id", (ids,))
    posts = {r[0]: dict(zip(("posting_id", "title", "status", "role_class", "role_rule", "hauptberuf", "offer_kind"), r))
             for r in cur.fetchall()}
    cur.execute("select observation_id, posting_id, source_id, source_ref, role_class, role_rule "
                "from pflege_jobs.posting_observations where posting_id = any(%s) order by observation_id", (ids,))
    obs = [dict(zip(("observation_id", "posting_id", "source_id", "source_ref", "role_class", "role_rule"), r))
           for r in cur.fetchall()]
    missing = sorted(set(ids) - set(posts))
    if missing:
        sys.exit(f"{len(missing)} posting(s) not found: {missing}")
    plan, bad = {}, []
    for pid, p in posts.items():
        new = K.classify_role(p["title"] or "", p["hauptberuf"], p["offer_kind"])
        if new[0] not in C.EXCLUDED_ROLE_CLASSES or p["role_class"] in C.EXCLUDED_ROLE_CLASSES:
            bad.append((pid, p["role_class"], new, p["title"]))
        plan[pid] = {"role_class": new[0], "role_rule": new[1]}
    if bad:
        for b in bad:
            print("  NOT A RELABEL CASE:", b)
        sys.exit(f"{len(bad)} posting(s) do not flip kept -> excluded under the deployed classifier; "
                 f"is cls.patch applied in {ROOT}? nothing written")
    obs_per = {pid: sum(o["posting_id"] == pid for o in obs) for pid in ids}
    for pid in ids:
        p = posts[pid]
        print(f"  {pid:>6} [{p['status']:7}] {p['role_class']:15} -> {plan[pid]['role_class']:12} "
              f"{plan[pid]['role_rule']:30} obs={obs_per[pid]} | {(p['title'] or '')[:70]}")
    from collections import Counter
    print(f"\n{len(ids)} postings ({Counter(p['status'] for p in posts.values())}), {len(obs)} observations; "
          f"{Counter((p['role_class'], plan[pid]['role_class']) for pid, p in posts.items())}")
    if not a.push:
        print("--dry-run (pass --push --by <who> to write)")
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bpath = os.path.join(ROOT, "backups", f"task177_relabel_before_{stamp}.json")
    os.makedirs(os.path.dirname(bpath), exist_ok=True)
    json.dump({"postings": list(posts.values()), "posting_observations": obs}, open(bpath, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("backup:", bpath)
    at = L.now()
    cur.executemany("update pflege_jobs.posting_observations set role_class=%s, role_rule=%s where observation_id=%s",
                    [(plan[o["posting_id"]]["role_class"], plan[o["posting_id"]]["role_rule"], o["observation_id"]) for o in obs])
    cur.executemany("update pflege_jobs.postings set role_class=%s, role_rule=%s, updated_at=now() where posting_id=%s",
                    [(plan[pid]["role_class"], plan[pid]["role_rule"], pid) for pid in ids])
    cur.execute("select posting_id, role_class, role_rule, status from pflege_jobs.postings where posting_id = any(%s)", (ids,))
    back_p = {r[0]: r for r in cur.fetchall()}
    cur.execute("select observation_id, posting_id, role_class, role_rule from pflege_jobs.posting_observations "
                "where posting_id = any(%s)", (ids,))
    back_o = {r[0]: r for r in cur.fetchall()}
    wrong = [pid for pid in ids if back_p[pid][1:3] != (plan[pid]["role_class"], plan[pid]["role_rule"])
             or back_p[pid][3] != posts[pid]["status"]]
    wrong += [f"obs {o['observation_id']}" for o in obs
              if back_o[o["observation_id"]][2:4] != (plan[o["posting_id"]]["role_class"], plan[o["posting_id"]]["role_rule"])]
    if wrong:
        conn.rollback()
        sys.exit(f"read-back mismatch, ROLLED BACK, nothing written: {wrong[:20]}")
    by = a.by.strip()
    lines = []
    for pid in ids:
        lines += L.entries("postings", pid, posts[pid], plan[pid], why, by, at, backup=bpath)
    n = L.record(lines, conn)          # same transaction: commits the updates and their reasons together
    print(f"committed: {len(ids)} postings, {len(obs)} observations; corrections: {n} row(s)")


if __name__ == "__main__":
    main()
