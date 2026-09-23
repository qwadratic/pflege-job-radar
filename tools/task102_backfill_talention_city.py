"""TASK-102: retroactively clean kliniken-nordoberpfalz.talention.com's stored payload.loc[].city on
ALREADY-processed inbox rows (the crawl-time fix in app/crawl.py._vendor_rows only cleans it for FUTURE
crawls). Same pattern as tools/task99_backfill_account_pools.py: patch the stored payload, reset
processed_at/process_note by inbox_id, re-run the real `pflege_jobs.cli inbox` path.

Only rows currently 'loaded (no site match)' are targeted.

  set -a; source .env; set +a && .venv/bin/python tools/task102_backfill_talention_city.py
"""
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawlers.vendor_adapters import clean_talention_city  # noqa: E402
from pflege_jobs import inbox_db as IB  # noqa: E402

POOL_TOWNS = ["Weiden", "Tirschenreuth", "Kemnath"]
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inbox.sqlite")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("select inbox_id, payload from inbox where source_url like '%kliniken-nordoberpfalz%' "
                     "and process_note='loaded (no site match)'").fetchall()
ids = []
for r in rows:
    payload = json.loads(r["payload"])
    changed = False
    for l in (payload.get("loc") or []):
        cleaned = clean_talention_city(l.get("city"), POOL_TOWNS)
        if cleaned != l.get("city"):
            l["city"] = cleaned
            changed = True
    if changed:
        conn.execute("update inbox set payload=? where inbox_id=?", (json.dumps(payload, ensure_ascii=False), r["inbox_id"]))
        ids.append(r["inbox_id"])
conn.commit()
print(f"kliniken-nordoberpfalz.talention.com: cleaned city on {len(ids)} of {len(rows)} 'no site match' row(s)")
conn.close()

if ids:
    n = IB.reset(inbox_ids=ids, path=DB)
    print(f"reset {n} row(s) for reprocessing")
    print("re-running pflege_jobs.cli inbox ...")
    r = subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "inbox"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(r.returncode)
print("nothing to backfill")
