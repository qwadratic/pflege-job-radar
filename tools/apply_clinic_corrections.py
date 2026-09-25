"""Generic clinic field-correction applier -- replaces the repeated one-off pattern (apply_run120_
zero_yield_fixes.py, apply_allgaeu_ats.py, task86_apply_registry_corrections.py,
task116_apply_6_clinic_fixes.py, and others): a per-task script hardcoding a TARGETS dict, fetching
live rows, merging changed fields, pushing via EdgeSink, reading back to verify. Same shape every
time -- only the corrections differ. This tool takes the corrections as a JSON file instead of a new
.py file per task.

Corrections file shape: {"<clinic_id>": {"<field>": <new_value>, ...}, ...} -- any subset of
pflege_jobs.schema.CLINIC_SPEC's columns per clinic; every other column is preserved from the live
row untouched.

  set -a; source .env; set +a
  .venv/bin/python tools/apply_clinic_corrections.py corrections.json --dry-run
  .venv/bin/python tools/apply_clinic_corrections.py corrections.json --push
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A                # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC  # noqa: E402
from pflege_jobs.sinks import EdgeSink      # noqa: E402


def load_corrections(path):
    return json.load(open(path, encoding="utf-8"))


def fetch_live(clinic_ids):
    live = A.rest_get("clinics", {"select": "*", "clinic_id": f"in.({','.join(clinic_ids)})"})
    return {str(r["clinic_id"]): r for r in live}


def backup(live_by_id, tag):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(str(A.ROOT), "backups", f"apply_clinic_corrections_{tag}_before_{stamp}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(live_by_id.values()), f, ensure_ascii=False, indent=1)
    return path


def merged_row(live_row, changes):
    row = {col: live_row.get(col) for col, _ in CLINIC_SPEC}
    row.update(changes)
    return row


def diff_lines(clinic_id, live_row, changes):
    return [f"  {clinic_id} {str(live_row.get('name'))[:34]:34} {col} {live_row.get(col)!r} -> {new!r}"
            for col, new in changes.items()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corrections", help="JSON file: {clinic_id: {field: value, ...}, ...}")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    corrections = load_corrections(a.corrections)
    ids = sorted(corrections)
    live_by_id = fetch_live(ids)
    missing = [i for i in ids if i not in live_by_id]
    if missing:
        sys.exit(f"{len(missing)} clinic_id(s) not found live: {missing}")

    for cid in ids:
        for line in diff_lines(cid, live_by_id[cid], corrections[cid]):
            print(line)

    if not a.push:
        print("\n--dry-run (pass --push to write)" if not a.dry_run else "\n--dry-run: nothing written")
        return

    tag = os.path.splitext(os.path.basename(a.corrections))[0]
    path = backup(live_by_id, tag)
    print(f"backup: {path}")

    rows = [merged_row(live_by_id[cid], corrections[cid]) for cid in ids]
    n = EdgeSink().write_clinics(rows)
    print(f"rows written: {n}")

    back = fetch_live(ids)
    ok = True
    for cid in ids:
        row = back.get(cid, {})
        good = all(row.get(col) == new for col, new in corrections[cid].items())
        ok &= good
        print(f"  {'OK ' if good else 'NO '} {cid} " +
              ", ".join(f"{col}={row.get(col)!r}" for col in corrections[cid]))
    print("result:", f"all {len(ids)} row(s) applied" if ok else "NOT ALL APPLIED, see above")


if __name__ == "__main__":
    main()
