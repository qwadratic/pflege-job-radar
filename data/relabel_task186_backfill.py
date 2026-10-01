"""TASK-186 one-off relabel backfill. Run from /home/exedev/repo after the TASK-186 classifier change (branch
worktree-agent-a1ebce798259000c1) is deployed and pflege-web restarted. Dry run by default; --push has NOT been
executed anywhere (the branch that wrote this had no DB access and was told not to push).

Rows: the 166 postings in data/relabel_task186_backfill_set.json -- the replay-measured set (all status=open in the
2026-10-01 vacancy-landscape export) whose stored role_class or department_hint differs from what the TASK-186
code returns because of TASK-186:
  group ausbildung_body   26  role_class -> ausbildung       (Ausbildung under a staff title, read from the ad text)
  group nicht_pflege      93  role_class -> nicht_pflege     (teaching, IT, lab, sales, MFA ... clear non-nursing clusters)
  group no_vacancy_page    6  role_class -> nicht_pflege     (the page says nothing is open), reason code not_a_vacancy
  group department_hint   41  department_hint set/extended  (the ward the posting names in its own recruiting statement)
Rows whose stored value already differed from the replay WITHOUT this change (114 role, 81 department) are listed in
the set file under not_pushed and are never touched here.

Each row is re-classified here from its LIVE title and description with the deployed code; a posting that does not
come out exactly as the reviewed set says stops the run (patch not deployed, or the text changed since the export).

Mechanism (same as TASK-177): one transaction over the Supavisor pooler (SUPABASE_DB_POOLER_URL):
  UPDATE posting_observations SET role_class, role_rule / department_hint   (all observations of those postings --
         what resolve_postings() folds into the golden row, so the nightly resolve keeps the new value)
  UPDATE postings SET role_class, role_rule / department_hint, updated_at   (the golden row, so the board changes now)
  INSERT pflege_jobs.corrections, one row per changed postings field, the posting's own quote as evidence
status is NOT touched (relabel, not retire); the board hides excluded classes (app/data.py _build()). Read-back
inside the transaction; commit only if every row matches, so the change and its reasons land together or not at all.
Backup of both tables' rows first; the observations' own before-values are in that backup.

  set -a; source .env; set +a
  .venv/bin/python data/relabel_task186_backfill.py                       # dry run: plan only
  .venv/bin/python data/relabel_task186_backfill.py --push --by "<who>"
"""
import argparse
import datetime
import json
import os
import sys
from collections import Counter

ROOT = os.environ.get("RELABEL_ROOT", "/home/exedev/repo")   # override only to dry-run a worktree
sys.path.insert(0, ROOT)
import psycopg2                                   # noqa: E402
from pflege_jobs import classify as K             # noqa: E402
from tools import ledger as L                     # noqa: E402

T = "tests/test_mech_role_class.py::"
WHY = {
    "ausbildung_body": {
        "code": "role_misclassified",
        "reason": "classify_role now reads the ad text: a training place posted under a staff title (Ausbildungsbeginn, "
                  "Haupt-/Realschulabschluss or mittlere Reife as admission requirement in the body) is class ausbildung, not "
                  "the class its title suggests. Stored rows keep the old class because intake skips excluded rows instead of "
                  "re-writing them. Relabel only, status unchanged; the board hides excluded classes.",
        "evidence": ["TASK-186: classify_role(title, desc=...) replay over all 3870 open postings (2026-10-01), 26 flip to ausbildung",
                     T + "test_an_ausbildung_under_a_staff_title_is_read_from_the_body",
                     T + "test_a_staff_posting_that_mentions_ausbildung_stays_what_its_title_says"],
        "task": "TASK-186"},
    "nicht_pflege": {
        "code": "role_misclassified",
        "reason": "classify_role now classifies teaching staff (Pflegepädagoge, Lehrkraft, Dozent), school administration, EDV/IT, "
                  "laboratory, sales, physicians and MFA titles as nicht_pflege (clear non-nursing clusters only). Stored rows keep "
                  "the old class because intake skips excluded rows instead of re-writing them. Relabel only, status unchanged; "
                  "the board hides excluded classes.",
        "evidence": ["TASK-186: old-vs-new classify_role replay over all 3870 open postings (2026-10-01), 93 flip to nicht_pflege",
                     T + "test_clear_non_nursing_titles_are_not_nursing",
                     T + "test_neighbours_of_the_non_nursing_clusters_stay_nursing"],
        "task": "TASK-186"},
    "no_vacancy_page": {
        "code": "not_a_vacancy",
        "reason": "The stored page itself says nothing is open ('keine offenen Stellen', 'keine Stellenanzeigen gefunden'). "
                  "classify_role now records such a page as nicht_pflege / no_vacancy_page, the mechanism speculative_application "
                  "already uses. Relabel only, status unchanged (the board hides excluded classes), so the rows stay in the DB and "
                  "come back if the rule changes; retire them with tools/apply_posting_changes.py if they should expire.",
        "evidence": ["TASK-186: classify_role(title, desc=...) replay over all 3870 open postings (2026-10-01), 6 flip to no_vacancy_page",
                     T + "test_a_page_that_says_nothing_is_open_is_not_a_vacancy",
                     T + "test_a_vacancy_that_only_mentions_nothing_open_elsewhere_stays_a_vacancy"],
        "task": "TASK-186"},
    "department_hint": {
        "code": "role_misclassified",
        "reason": "department_hint now also reads the ward the posting names in its own recruiting statement or board header "
                  "('... sucht für die Station M62 (Dialyse) ab sofort', 'Bereich Akutgeriatrie Einstiegsdatum'; patterns.json "
                  "enrichment.dept_anchor) when title and Aufgaben/Profil name no department. Stored rows keep the old hint until a "
                  "crawl re-reads them, closed rows never. Recorded under role_misclassified, the closest relabel code: "
                  "pflege_jobs.correction_reasons has no code for a department label (gap flagged in TASK-186).",
        "evidence": ["TASK-186: old-vs-new department_hint replay over all 3870 open postings (2026-10-01), 41 postings gain or extend a hint",
                     "tests/test_mech_department.py::test_the_ward_named_in_the_recruiting_statement_is_found",
                     "tests/test_mech_department.py::test_the_bereich_field_of_a_board_header_is_found"],
        "task": "TASK-186"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=os.path.join(ROOT, "data", "relabel_task186_backfill_set.json"))
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--by")
    a = ap.parse_args()
    if a.push and not (a.by or "").strip():
        sys.exit("--push needs --by (pflege_jobs.corrections records who made the change)")
    doc = json.load(open(a.set, encoding="utf-8"))
    rows = {r["posting_id"]: r for r in doc["postings"]}
    ids = sorted(rows)
    conn = psycopg2.connect(os.environ["SUPABASE_DB_POOLER_URL"])
    codes = L.reason_codes("postings", conn)
    why = {g: L.require_why(w, f"TASK-186 relabel {g}", codes) for g, w in WHY.items()}
    cur = conn.cursor()
    cur.execute("select posting_id, title, status, role_class, role_rule, department_hint, coalesce(hauptberuf,''), "
                "coalesce(offer_kind,''), coalesce(description,'') from pflege_jobs.postings where posting_id = any(%s) "
                "order by posting_id", (ids,))
    posts, text = {}, {}
    for r in cur.fetchall():
        posts[r[0]] = dict(zip(("posting_id", "title", "status", "role_class", "role_rule", "department_hint", "hauptberuf",
                                "offer_kind"), r[:8]))
        text[r[0]] = r[8]
    cur.execute("select observation_id, posting_id, source_id, source_ref, role_class, role_rule, department_hint "
                "from pflege_jobs.posting_observations where posting_id = any(%s) order by observation_id", (ids,))
    obs = [dict(zip(("observation_id", "posting_id", "source_id", "source_ref", "role_class", "role_rule", "department_hint"), r))
           for r in cur.fetchall()]
    missing = sorted(set(ids) - set(posts))
    if missing:
        sys.exit(f"{len(missing)} posting(s) not found: {missing}")
    plan, bad = {}, []
    for pid in ids:
        p, r = posts[pid], rows[pid]
        want = {}
        if "role" in r:
            want["role_class"], want["role_rule"] = r["role"]["new"]
        if "department" in r:
            want["department_hint"] = r["department"]["new"]
        role = K.classify_role(p["title"] or "", p["hauptberuf"], p["offer_kind"], desc=text[pid])
        got = {"role_class": role[0], "role_rule": role[1], "department_hint": K.department_hint(p["title"] or "", text[pid])}
        if any(got[f] != v for f, v in want.items()):
            bad.append((pid, want, {f: got[f] for f in want}, p["title"]))
        plan[pid] = want
    if bad:
        for b in bad:
            print("  NOT AS REVIEWED:", b)
        sys.exit(f"{len(bad)} posting(s) do not come out as the reviewed set says under the deployed classifier; "
                 f"is the TASK-186 change deployed in {ROOT}? nothing written")
    obs_per = {pid: sum(o["posting_id"] == pid for o in obs) for pid in ids}
    for pid in ids:
        p, r = posts[pid], rows[pid]
        moved = "; ".join(f"{f} {p[f]!r} -> {v!r}" + (" (already)" if p[f] == v else "") for f, v in plan[pid].items())
        print(f"  {pid:>6} [{p['status']:7}] {r['group']:15} obs={obs_per[pid]} {moved} | {(p['title'] or '')[:60]}")
    print(f"\n{len(ids)} postings ({Counter(p['status'] for p in posts.values())}), {len(obs)} observations; "
          f"groups {Counter(r['group'] for r in rows.values())}")
    print("role_class moves:", Counter((posts[pid]["role_class"], plan[pid]["role_class"]) for pid in ids if "role_class" in plan[pid]))
    print("not touched (set file, not_pushed):", {k: len(v) for k, v in doc["not_pushed"].items()})
    if not a.push:
        print("--dry-run (pass --push --by <who> to write)")
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bpath = os.path.join(ROOT, "backups", f"task186_relabel_before_{stamp}.json")
    os.makedirs(os.path.dirname(bpath), exist_ok=True)
    json.dump({"postings": list(posts.values()), "posting_observations": obs}, open(bpath, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("backup:", bpath)
    at = L.now()
    cur.executemany("update pflege_jobs.posting_observations set role_class=%s, role_rule=%s where observation_id=%s",
                    [(plan[o["posting_id"]]["role_class"], plan[o["posting_id"]]["role_rule"], o["observation_id"])
                     for o in obs if "role_class" in plan[o["posting_id"]]])
    cur.executemany("update pflege_jobs.posting_observations set department_hint=%s where observation_id=%s",
                    [(plan[o["posting_id"]]["department_hint"], o["observation_id"])
                     for o in obs if "department_hint" in plan[o["posting_id"]]])
    cur.executemany("update pflege_jobs.postings set role_class=%s, role_rule=%s, updated_at=now() where posting_id=%s",
                    [(plan[pid]["role_class"], plan[pid]["role_rule"], pid) for pid in ids if "role_class" in plan[pid]])
    cur.executemany("update pflege_jobs.postings set department_hint=%s, updated_at=now() where posting_id=%s",
                    [(plan[pid]["department_hint"], pid) for pid in ids if "department_hint" in plan[pid]])
    cur.execute("select posting_id, role_class, role_rule, department_hint, status from pflege_jobs.postings "
                "where posting_id = any(%s)", (ids,))
    back_p = {r[0]: dict(zip(("role_class", "role_rule", "department_hint", "status"), r[1:])) for r in cur.fetchall()}
    cur.execute("select observation_id, role_class, role_rule, department_hint from pflege_jobs.posting_observations "
                "where posting_id = any(%s)", (ids,))
    back_o = {r[0]: dict(zip(("role_class", "role_rule", "department_hint"), r[1:])) for r in cur.fetchall()}
    wrong = [pid for pid in ids if any(back_p[pid][f] != v for f, v in plan[pid].items()) or back_p[pid]["status"] != posts[pid]["status"]]
    wrong += [f"obs {o['observation_id']}" for o in obs
              if any(back_o[o["observation_id"]][f] != v for f, v in plan[o["posting_id"]].items())]
    if wrong:
        conn.rollback()
        sys.exit(f"read-back mismatch, ROLLED BACK, nothing written: {wrong[:20]}")
    by = a.by.strip()
    lines = []
    for pid in ids:
        r = rows[pid]
        if "role" in r:
            w = {**why[r["group"]], "evidence": why[r["group"]]["evidence"] + [f'quote: "{r["quote"]}"']}
            lines += L.entries("postings", pid, posts[pid], {f: plan[pid][f] for f in ("role_class", "role_rule")}, w, by, at, backup=bpath)
        if "department" in r:
            w = {**why["department_hint"], "evidence": why["department_hint"]["evidence"] + [f'quote: "{r["department_quote"]}"']}
            lines += L.entries("postings", pid, posts[pid], {"department_hint": plan[pid]["department_hint"]}, w, by, at, backup=bpath)
    n = L.record(lines, conn)          # same transaction: commits the updates and their reasons together
    print(f"committed: {len(ids)} postings, {len(obs)} observations; corrections: {n} row(s)")


if __name__ == "__main__":
    main()
