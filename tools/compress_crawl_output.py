"""Gzip the per-run raw dumps crawl_output/run_<id>.jsonl of every run that is finished (Ivan 2026-10-08: whatever is not read and can be compressed is compressed, always).

A dump is ~110 MB plain and ~16 MB as .gz, no row lost; app/hunter.py and tools/task95_replay.py read both forms. The only dumps left plain are those of runs
that are running or queued (the crawl worker still appends to them) and the ids named with --skip. Nothing is deleted: the .gz is written beside the file,
read back, compared line for line, and only then replaces it. A dump whose .gz already exists, a dump of a run unknown to crawl_runs and a failed read-back
are problems: they are printed and the exit code is 1; nothing is guessed.

  .venv/bin/python tools/compress_crawl_output.py              # dry run: what would be compressed
  .venv/bin/python tools/compress_crawl_output.py --apply      # cron: deploy/crontab, daily after the nightly pass
"""
import argparse
import gzip
import itertools
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_FILE = re.compile(r"^run_(\d+)\.jsonl$")
BUSY = ("running", "queued")


def run_status(db_path, run_id):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = db.execute("select status from crawl_runs where run_id = ?", (run_id,)).fetchone()
    finally:
        db.close()
    return row[0] if row else None


def same_lines(plain, gz):
    with open(plain, "rb") as a, gzip.open(gz, "rb") as b:
        return all(x == y for x, y in itertools.zip_longest(a, b))


def compress(plain, gz):
    tmp = gz + ".tmp"
    with open(plain, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)
    if not same_lines(plain, tmp):
        os.unlink(tmp)
        raise RuntimeError("read-back of the .gz differs from the dump")
    os.replace(tmp, gz)
    os.unlink(plain)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "crawl_output"))
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "app.sqlite"))
    ap.add_argument("--skip", type=int, nargs="*", default=[], help="run ids left plain on purpose")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    done = kept = 0
    saved = 0
    problems = []
    for name in sorted(os.listdir(a.dir)):
        m = RUN_FILE.match(name)
        if not m:
            continue
        rid, plain = int(m.group(1)), os.path.join(a.dir, name)
        if rid in a.skip:
            kept += 1
            continue
        status = run_status(a.db, rid)
        if status is None:
            problems.append(f"{name}: run {rid} is not in crawl_runs, left plain")
            continue
        if status in BUSY:
            kept += 1
            continue
        if os.path.exists(plain + ".gz"):
            problems.append(f"{name}: {name}.gz exists beside it, left both")
            continue
        size = os.path.getsize(plain)
        if not a.apply:
            print(f"would gzip {name} ({size >> 20} MB, run {rid} {status})")
            continue
        try:
            compress(plain, plain + ".gz")
        except Exception as e:  # loud: a failed read-back leaves the plain dump where it was
            problems.append(f"{name}: {e}")
            continue
        done += 1
        saved += size - os.path.getsize(plain + ".gz")
    print(f"compressed {done}, left plain {kept}, saved {saved >> 20} MB, problems {len(problems)}")
    for p in problems:
        print("PROBLEM", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
