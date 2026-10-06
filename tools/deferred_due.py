"""List the rows of the deferred-candidates register whose next review date has come.

The register is the backlog document "Deferred candidates register" (backlog/docs, written with `backlog doc update`): findings that were
measured and judged worth keeping but not adopted now, each with the agent's comment and impact, the date of the comment, the date of the
next review and what would make it worth adopting.

  .venv/bin/python tools/deferred_due.py              # rows due today or earlier
  .venv/bin/python tools/deferred_due.py 2026-11-06   # rows due on that day or earlier
"""
import datetime
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
COLS = ("id", "candidate", "source", "comment", "comment_date", "by", "next_review", "adopt_when")


def register_path():
    (path,) = (ROOT / "backlog" / "docs").glob("doc-* - Deferred-candidates-register.md")
    return path


def rows(text):
    out = []
    for line in text.splitlines():
        if not re.match(r"\|\s*D\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(COLS):
            sys.exit(f"register row {cells[0]} has {len(cells)} cells, expected {len(COLS)}")
        r = dict(zip(COLS, cells))
        for k in ("comment_date", "next_review"):
            datetime.date.fromisoformat(r[k])
        out.append(r)
    return out


def due(rs, today):
    return [r for r in rs if r["next_review"] <= today.isoformat()]


def main(argv):
    today = datetime.date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.date.today()
    rs = rows(register_path().read_text(encoding="utf-8"))
    hit = due(rs, today)
    print(f"{len(hit)} of {len(rs)} candidates due on or before {today}")
    for r in hit:
        print(f"  {r['id']}  {r['candidate']}  (commented {r['comment_date']}, review {r['next_review']}, from {r['source']})\n      {r['comment']}\n      adopt when: {r['adopt_when']}")


if __name__ == "__main__":
    main(sys.argv)
