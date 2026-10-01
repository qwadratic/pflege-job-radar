"""TASK-180 one-off: copy the 181 lines of data/ledger.jsonl (TASK-174, 2026-09-29) into
pflege_jobs.corrections (sql/013_corrections.sql), classifying each line with a reason code.

Code per line -- every line lands in exactly one rule:
  clinics.beds                                  -> parse_error     (TASK-167, '1.020' read as NULL)
  clinics.<any other field>                     -> board_location  (careers_url / ats_type, TASK-169..171)
  postings.clinic_id / _match_rule / _score     -> wrong_clinic    (TASK-153, 163, 166, 171)
  postings status/verify_status, 'Duplicate...' -> duplicate       (TASK-163, 169)
  postings status/verify_status, otherwise      -> not_a_vacancy   (TASK-170 phantoms + pool ad)

Refuses to run on a non-empty corrections table (a second run would double every row).

  set -a; source .env; set +a
  .venv/bin/python data/migrate_ledger_to_corrections.py            # dry run: counts per code
  .venv/bin/python data/migrate_ledger_to_corrections.py --push
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import ledger as L  # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.jsonl")


def code(e):
    if e["table"] == "clinics":
        return "parse_error" if e["field"] == "beds" else "board_location"
    if e["field"].startswith("clinic_"):
        return "wrong_clinic"
    return "duplicate" if e["reason"].startswith("Duplicate") else "not_a_vacancy"


def main():
    lines = [json.loads(x) for x in open(SRC, encoding="utf-8")]
    rows = [{**{k: v for k, v in e.items() if k != "backfilled"}, "code": code(e)} for e in lines]
    print(len(rows), "lines:", dict(collections.Counter((r["table"], r["code"]) for r in rows)))
    conn = L.connect()
    with conn.cursor() as q:
        q.execute("select count(*) from pflege_jobs.corrections")
        have = q.fetchone()[0]
    if have:
        sys.exit(f"pflege_jobs.corrections already holds {have} row(s) -- not migrating twice")
    if "--push" not in sys.argv:
        print("dry run (pass --push to insert)")
        return
    print("inserted:", L.record(rows, conn))
    with conn.cursor() as q:
        q.execute("select table_name, reason_code, count(*) from pflege_jobs.corrections group by 1, 2 order by 1, 2")
        print("read back:", q.fetchall())


if __name__ == "__main__":
    main()
