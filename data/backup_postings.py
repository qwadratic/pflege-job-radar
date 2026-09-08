"""Full backup of postings + posting_observations to timestamped JSONL, before any bulk Firecrawl-only
reingest campaign. Keyless read via the exe.dev Supabase proxy (same pattern as data/metric.sh).

  python data/backup_postings.py                 # writes backups/postings_<ts>.jsonl(.gz) etc.
"""
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

READ = os.environ.get("SUPABASE_URL", "https://supabase.int.exe.xyz")
HEADERS = {"Accept-Profile": "pflege_jobs"}
PAGE = 1000


PK = {"postings": "posting_id", "posting_observations": "observation_id"}


def fetch_all(table, order=None):
    order = order or PK[table]
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f"{READ}/rest/v1/{table}",
            headers={**HEADERS, "Range-Unit": "items", "Range": f"{offset}-{offset + PAGE - 1}"},
            params={"select": "*", "order": order},
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def dump(table, ts, out_dir):
    rows = fetch_all(table)
    path = out_dir / f"{table}_{ts}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{table}: {len(rows)} rows -> {path}")
    return len(rows)


def main():
    from pathlib import Path
    out_dir = Path(__file__).parent.parent / "backups"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    counts = {}
    for table in ("postings", "posting_observations"):
        counts[table] = dump(table, ts, out_dir)
    manifest = out_dir / f"manifest_{ts}.json"
    manifest.write_text(json.dumps({"ts": ts, "counts": counts, "source": READ}, indent=1))
    print(f"manifest -> {manifest}")


if __name__ == "__main__":
    main()
