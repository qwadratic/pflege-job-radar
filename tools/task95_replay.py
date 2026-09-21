"""TASK-95 proof, no writes: replay a real run's raw rows through the new path.

  .venv/bin/python tools/task95_replay.py crawl_output/run_108.jsonl [--db /tmp/task95.sqlite]

Reads one run's crawl output (the exact rows the crawler handed to intake), puts every one of them
in a local SQLite queue unfiltered, then runs the real processing step over it with the EdgeSink and
the posting_observations lookup stubbed out -- so it measures what WOULD reach Postgres without
touching production. Then reprocesses the same stored rows (inbox_db.reset) to show a rule change
can be replayed without re-crawling.

Prints the old path's numbers from the same file for comparison: what the crawler-side classify
filter dropped, and what it would have INSERTed into the 2000-per-rolling-24h Postgres inbox.
"""
import argparse
import collections
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from pflege_jobs import cli, config as PC, inbox_db as IB, section
from pflege_jobs.classify import classify_role, norm_text
from pflege_jobs.registry import Matcher


class _Sink:
    """EdgeSink's shape, writing nothing."""
    written, links = [], 0

    def __init__(self, *a, **kw):
        pass

    def write(self, obs, **kw):
        _Sink.written.extend(obs)
        return {"observations": len(obs)}

    def write_clinics(self, rows, log=print):
        return len(rows)

    def _post(self, body):
        _Sink.links += sum(len(v) for v in body.values() if isinstance(v, list))
        return {k: (len(v) if isinstance(v, list) else 1) for k, v in body.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--db", default="/tmp/task95_replay.sqlite")
    ap.add_argument("--clinics", default="data/registry/clinics.csv")
    a = ap.parse_args()

    rows = []
    for line in open(a.jsonl, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    print(f"{a.jsonl}: {len(rows)} raw rows, {len({r.get('source_url') for r in rows})} distinct source_url")

    # --- the old path, from the same rows
    def _excluded(r):
        p = r.get("payload") or {}
        return (r.get("kind") == "jobposting"
                and classify_role(p.get("title") or "", "",
                                  nursing_section_confirmed=section.job_confirmed_nursing(p.get("section_labels")))[0]
                in PC.EXCLUDED_ROLE_CLASSES)

    kept = [r for r in rows if not _excluded(r)]
    print(f"OLD PATH: {len(rows) - len(kept)} rows dropped by classify_role before the insert; "
          f"{len({r['source_url'] for r in kept if r.get('source_url')})} distinct urls offered to the Postgres inbox, "
          f"which allows 2000 per client_id per rolling 24h")

    if os.path.exists(a.db):
        os.remove(a.db)
    n = IB.enqueue(rows, run_id=108, path=a.db)
    print(f"NEW PATH: {n} rows queued in {a.db} ({os.path.getsize(a.db) / 1e6:.1f} MB), 0 rows written to Postgres so far")

    clinics = list(csv.DictReader(open(a.clinics, encoding="utf-8")))
    towns = {norm_text(c["town"]) for c in clinics if c.get("town")}
    for c in clinics:
        c["beds"] = int(c["beds"]) if c.get("beds") else None
    m = Matcher([dict(c) for c in clinics])

    cli.EdgeSink = _Sink
    requests.get = lambda u, params=None, headers=None, timeout=None: type("R", (), {"json": lambda self: []})()
    args = argparse.Namespace(no_ack=False, inbox_db=a.db)

    def drain():
        _Sink.written, _Sink.links = [], 0
        total = 0
        while True:
            got = cli._drain_local_once(args, "https://db", {}, m, towns)
            total += got
            if got < 1000:
                break
        return total, list(_Sink.written)

    read, written = drain()
    notes = collections.Counter((r["process_note"] or "").split(" ->")[0].split(" (")[0]
                                for r in IB.connect(a.db).execute("select process_note from inbox"))
    print(f"\nprocessed {read} queued rows -> {len(written)} observations would be written to Postgres "
          f"({100.0 * len(written) / max(1, len(rows)):.1f}% of the raw rows)")
    for note, k in notes.most_common():
        print(f"   {k:>6}  {note}")
    print(f"   matched to a clinic: {sum(1 for o in written if o.get('_kez'))}")

    # --- reprocessing: same rows, no re-crawl
    unmarked = IB.reset(run_id=108, path=a.db)
    read2, written2 = drain()
    print(f"\nreprocess: {unmarked} raw rows unmarked and run again -> {read2} read, {len(written2)} observations "
          f"({'same result' if len(written2) == len(written) else 'DIFFERENT'})")


if __name__ == "__main__":
    main()
