"""Hand-made data changes are recorded in pflege_jobs.corrections (sql/013_corrections.sql): one row per
changed field, with its old and new value and a classified reason from pflege_jobs.correction_reasons.
Ivan, 2026-09-29: every change must say why, when and how, with evidence that can be re-checked, and
the reason must live in the database itself -- so a deviation of the DB from a primary source
(Krankenhausplan PDF, RHV) is either explained there or unexplained, and a wrong explanation can be
traced to its task. (Until 2026-09-29 these rows went to data/ledger.jsonl; that file was moved into
the table by data/migrate_ledger_to_corrections.py.)

Row shape: {at, table, id, field, old, new, code, reason, evidence[], task, by, backup?}
field '*' = a whole row inserted (old None, new = the row).
"""
import datetime
import os
import re

import psycopg2
from psycopg2.extras import Json

INSERT = ("insert into pflege_jobs.corrections (at, table_name, row_id, field, old_value, new_value, reason_code, "
          "reason, evidence, task, made_by, backup) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")


def connect():
    return psycopg2.connect(os.environ["SUPABASE_DB_POOLER_URL"])


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reason_codes(table, conn):
    """The reason codes pflege_jobs.correction_reasons allows on rows of `table`."""
    with conn.cursor() as q:
        q.execute("select code from pflege_jobs.correction_reasons where applies_to = %s", (table,))
        return {r[0] for r in q.fetchall()}


def require_why(why, where, codes):
    """The {code, reason, evidence, task} block every change carries. Missing or empty parts, or a code
    outside `codes`, stop the run before anything is written."""
    if not isinstance(why, dict):
        raise SystemExit(f"{where}: missing _why {{code, reason, evidence, task}}")
    code, reason, evidence, task = why.get("code"), why.get("reason"), why.get("evidence"), why.get("task")
    problems = []
    if code not in codes:
        problems.append(f"code (one of {', '.join(sorted(codes))})")
    if not (isinstance(reason, str) and reason.strip()):
        problems.append("reason")
    if not (isinstance(evidence, list) and evidence and all(isinstance(e, str) and e.strip() for e in evidence)):
        problems.append("evidence (non-empty list of strings)")
    if not (isinstance(task, str) and re.fullmatch(r"TASK-\d+", task)):
        problems.append("task (TASK-<n>)")
    if problems:
        raise SystemExit(f"{where}: _why lacks {', '.join(problems)}")
    return {"code": code, "reason": reason.strip(), "evidence": [e.strip() for e in evidence], "task": task}


def entries(table, row_id, old_row, changes, why, by, at, backup=None):
    """One row per field whose value actually changes; a no-op field writes nothing."""
    return [{"at": at, "table": table, "id": str(row_id), "field": f, "old": old_row.get(f), "new": v,
             **why, "by": by, **({"backup": backup} if backup else {})}
            for f, v in changes.items() if old_row.get(f) != v]


def _json(v):
    return None if v is None else Json(v)


def record(lines, conn):
    """Insert `lines` into pflege_jobs.corrections in one transaction; returns how many."""
    with conn, conn.cursor() as q:
        for e in lines:
            q.execute(INSERT, (e["at"], e["table"], e["id"], e["field"], _json(e["old"]), _json(e["new"]), e["code"],
                               e["reason"], e["evidence"], e["task"], e["by"], e.get("backup")))
    return len(lines)
