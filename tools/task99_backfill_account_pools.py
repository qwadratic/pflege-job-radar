"""TASK-99: retroactively fix board_clinic_ids on ALREADY-STORED, already-processed inbox rows whose
match failed because their board pool was a single clinic (the crawl-time fix in app/crawl.py._vendor_rows
only widens the pool for FUTURE crawls -- these rows already have the narrow pool baked into their stored
payload from when they were first queued).

Patches payload.board_clinic_ids to the full crawlers.vendor_adapters.VENDOR_ACCOUNT_POOLS entry, resets
processed_at/process_note on exactly those rows (pflege_jobs.inbox_db.reset, by inbox_id -- nothing else
is touched), then re-runs the real `pflege_jobs.cli inbox` processor so they go through the Matcher again
with the corrected pool, same code path a live crawl uses.

Only rows currently 'loaded (no site match)' are targeted -- an already-matched row (content-side match,
independent of board pool) is left alone.

  set -a; source .env; set +a && .venv/bin/python tools/task99_backfill_account_pools.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawlers.vendor_adapters import VENDOR_ACCOUNT_POOLS  # noqa: E402
from pflege_jobs import inbox_db as IB  # noqa: E402

HOSTS = {"jobs.smartrecruiters.com": ["16228", "16235", "18105", "18802", "18808", "18813", "18872", "76108"],
         "karriere.gesundheitswelt.de": ["18721", "18713"]}
for host, pool in HOSTS.items():
    assert any(set(pool) == set(p["clinic_ids"]) for p in VENDOR_ACCOUNT_POOLS), f"{host} pool drifted from vendor_adapters.py"

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inbox.sqlite")

import sqlite3  # noqa: E402
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
total_patched = 0
for host, pool in HOSTS.items():
    rows = conn.execute("select inbox_id, payload from inbox where source_host=? and process_note='loaded (no site match)'", (host,)).fetchall()
    ids = []
    for r in rows:
        payload = json.loads(r["payload"])
        payload["board_clinic_ids"] = pool
        conn.execute("update inbox set payload=? where inbox_id=?", (json.dumps(payload, ensure_ascii=False), r["inbox_id"]))
        ids.append(r["inbox_id"])
    conn.commit()
    print(f"{host}: patched board_clinic_ids on {len(ids)} row(s) -> pool {pool}")
    total_patched += len(ids)
    if ids:
        n = IB.reset(inbox_ids=ids, path=DB)
        print(f"  reset {n} row(s) for reprocessing")
conn.close()

if total_patched:
    print("re-running pflege_jobs.cli inbox ...")
    r = subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "inbox"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(r.returncode)
print("nothing to patch")
