"""TASK-81 mechanism #1: retroactively split la-regio-kliniken.de's already-stored inbox rows between
26108 (LA-Regio Kliniken Landshut, general hospital) and 26103 (Kinderkrankenhaus St. Marien Landshut,
pediatric) by title (crawlers.vendor_adapters.split_la_regio_landshut) -- the crawl-time fix in
app/crawl.py._vendor_rows only narrows board_clinic_ids for FUTURE crawls. Patches every stored row on
this board (not just currently-'loaded' ones -- 'skipped: nicht_pflege' rows carry the wrong pool too,
harmless today but wrong if the classifier is ever loosened), resets processed_at, re-runs the real
`pflege_jobs.cli inbox` path.

  set -a; source .env; set +a && .venv/bin/python tools/task81_backfill_la_regio_landshut.py
"""
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawlers.vendor_adapters import split_la_regio_landshut  # noqa: E402
from pflege_jobs import inbox_db as IB  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inbox.sqlite")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("select inbox_id, payload from inbox where json_extract(payload,'$.board_clinic_ids') like '%26108%' "
                     "or json_extract(payload,'$.board_clinic_ids') like '%26103%'").fetchall()
ids, counts = [], {"26108": 0, "26103": 0}
for r in rows:
    payload = json.loads(r["payload"])
    new_pool = [split_la_regio_landshut(payload.get("title"))]
    if payload.get("board_clinic_ids") != new_pool:
        payload["board_clinic_ids"] = new_pool
        conn.execute("update inbox set payload=? where inbox_id=?", (json.dumps(payload, ensure_ascii=False), r["inbox_id"]))
        ids.append(r["inbox_id"])
        counts[new_pool[0]] += 1
conn.commit()
print(f"la-regio-kliniken.de: patched board_clinic_ids on {len(ids)} of {len(rows)} row(s) -> {counts}")
conn.close()

if ids:
    n = IB.reset(inbox_ids=ids, path=DB)
    print(f"reset {n} row(s) for reprocessing")
    print("re-running pflege_jobs.cli inbox ...")
    r = subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "inbox"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(r.returncode)
print("nothing to backfill")
