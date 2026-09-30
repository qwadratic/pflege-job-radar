"""Hand-made posting changes -- retire, reopen, relink, unlink -- with backup, read-back and one
pflege_jobs.corrections row per changed field (see tools/ledger.py). Replaces ad-hoc EdgeSink pushes
from the shell.

Changes file: a JSON list, one object per posting:
  {"posting_id": 5706, "action": "retire", "_why": {...}}          -> verify_status=gone, status=expired
  {"posting_id": 15113, "action": "reopen", "_why": {...}}         -> verify_status=live, status=open (wrongly expired)
  {"posting_id": 10076, "action": "relink", "clinic_id": "67705", "_why": {...}}   -> clinic_match_rule=manual
  {"posting_id": 6376, "action": "unlink", "lock": false, "_why": {...}}  -> clinic_id=NULL; lock=true
                                                                            also sets rule 'manual' so no crawl relinks it
  _why = {"code": "<reason code>", "reason": "...", "evidence": ["url + what it showed + date", ...], "task": "TASK-n"}
  code: one of pflege_jobs.correction_reasons for postings (wrong_clinic, duplicate, not_a_vacancy, false_gone, ...)

  set -a; source .env; set +a
  .venv/bin/python tools/apply_posting_changes.py changes.json --dry-run
  .venv/bin/python tools/apply_posting_changes.py changes.json --push --by "<who>"
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A                # noqa: E402
from pflege_jobs.sinks import EdgeSink      # noqa: E402
from tools import ledger as L               # noqa: E402

COLS = "posting_id,title,status,verify_status,verify_note,clinic_id,clinic_match_rule,clinic_match_score"


def load_changes(path, codes):
    items = json.load(open(path, encoding="utf-8"))
    out = []
    for it in items:
        pid, action = it.get("posting_id"), it.get("action")
        where = f"posting {pid}"
        if not isinstance(pid, int):
            raise SystemExit(f"{where}: posting_id must be an int")
        if action not in ("retire", "reopen", "relink", "unlink"):
            raise SystemExit(f"{where}: action must be retire | reopen | relink | unlink, got {action!r}")
        if action == "relink" and not it.get("clinic_id"):
            raise SystemExit(f"{where}: relink needs clinic_id")
        out.append({**{k: v for k, v in it.items() if k != "_why"}, "why": L.require_why(it.get("_why"), where, codes)})
    ids = [c["posting_id"] for c in out]
    if len(ids) != len(set(ids)):
        raise SystemExit("a posting appears twice in the changes file")
    return out


def plan(change, at):
    """-> (edge op name, payload row, expected {field: value} after the write)."""
    pid, why = change["posting_id"], change["why"]
    if change["action"] == "retire":
        note = f"retired by hand ({why['task']}): {why['reason']}"[:500]
        return "verify", {"posting_id": pid, "verify_status": "gone", "verify_http": None, "verified_at": at,
                          "verify_note": note}, {"verify_status": "gone", "status": "expired"}
    if change["action"] == "reopen":
        note = f"reopened by hand ({why['task']}): {why['reason']}"[:500]
        return "verify", {"posting_id": pid, "verify_status": "live", "verify_http": None, "verified_at": at,
                          "verify_note": note}, {"verify_status": "live", "status": "open"}
    if change["action"] == "relink":
        return "clinic_links", {"posting_id": pid, "clinic_id": str(change["clinic_id"]), "clinic_match_rule": "manual",
                                "clinic_match_score": None}, {"clinic_id": str(change["clinic_id"]), "clinic_match_rule": "manual"}
    rule = "manual" if change.get("lock") else None
    return "clinic_links", {"posting_id": pid, "clinic_id": None, "clinic_match_rule": rule, "clinic_match_score": None}, \
        {"clinic_id": None, "clinic_match_rule": rule}


def fetch_live(ids):
    rows = A.rest_get("postings", {"select": COLS, "posting_id": f"in.({','.join(map(str, ids))})"})
    return {r["posting_id"]: r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("changes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--by", help="who makes the change (required with --push)")
    a = ap.parse_args()
    if a.push and not (a.by or "").strip():
        sys.exit("--push needs --by: pflege_jobs.corrections records who made the change")

    conn = L.connect()
    changes = load_changes(a.changes, L.reason_codes("postings", conn))
    ids = [c["posting_id"] for c in changes]
    live = fetch_live(ids)
    missing = [i for i in ids if i not in live]
    if missing:
        sys.exit(f"{len(missing)} posting(s) not found live: {missing}")
    if any(c["action"] == "relink" for c in changes):
        want = sorted({str(c["clinic_id"]) for c in changes if c["action"] == "relink"})
        have = {r["clinic_id"] for r in A.rest_get("clinics", {"select": "clinic_id", "clinic_id": f"in.({','.join(want)})"})}
        if set(want) - have:
            sys.exit(f"relink target clinic(s) not found live: {sorted(set(want) - have)}")

    at = L.now()
    planned = [(c, *plan(c, at)) for c in changes]
    for c, op, payload, expect in planned:
        row = live[c["posting_id"]]
        print(f"  {c['posting_id']:>6} {c['action']:6} {str(row['title'])[:44]:44} "
              + ", ".join(f"{k} {row.get(k)!r} -> {v!r}" for k, v in expect.items()))
        print(f"         why ({c['why']['code']}, {c['why']['task']}): {c['why']['reason'][:100]}")
    if not a.push:
        print("\n--dry-run (pass --push --by <who> to write)")
        return

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bpath = os.path.join(str(A.ROOT), "backups", f"apply_posting_changes_{os.path.splitext(os.path.basename(a.changes))[0]}_before_{stamp}.json")
    os.makedirs(os.path.dirname(bpath), exist_ok=True)
    json.dump([live[i] for i in ids], open(bpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"backup: {bpath}")

    sink = EdgeSink()
    for op in ("verify", "clinic_links"):
        rows = [p for _, o, p, _ in planned if o == op]
        if rows:
            print(f"{op}: {sink._post({op: rows}).get(op)}")

    back = fetch_live(ids)
    ok, lines = True, []
    for c, _, _, expect in planned:
        row = back.get(c["posting_id"], {})
        good = all(row.get(k) == v for k, v in expect.items())
        ok &= good
        print(f"  {'OK ' if good else 'NO '} {c['posting_id']} " + ", ".join(f"{k}={row.get(k)!r}" for k in expect))
        if good:
            lines += L.entries("postings", c["posting_id"], live[c["posting_id"]], expect, c["why"], a.by.strip(), at, backup=bpath)
    try:
        print(f"corrections: {L.record(lines, conn)} row(s) -> pflege_jobs.corrections")
    except Exception:
        # the data change is already live: keep its reasons so they can be recorded by hand
        json.dump(lines, open(bpath + ".corrections.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"NOT RECORDED -- correction rows saved to {bpath}.corrections.json", file=sys.stderr)
        raise
    print("result:", f"all {len(ids)} posting(s) applied" if ok else "NOT ALL APPLIED, see above")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
