"""Producer for WA_REAL_SYSTEM_PHONES_FILE (TASK-87, follow-up to TASK-75): a fully generic,
operator-configured export of the phone numbers a real, external system already knows about, into
the exact plain newline-delimited format app/wa/routing.py's own ``_is_known_to_real_system``
reads. Nothing else in this repo produces that file; without it, WA_REAL_SYSTEM_PHONES_FILE stays
an unmet promise and routing.py refuses to route anything at all (by design -- see its own
docstring).

Same genericize-the-real-system discipline as app/wa/luna/external_contacts.py (TASK-69) and
app/wa/luna/migrate_candidates.py (TASK-71): this module never names or hardcodes any specific
external system's schema, table, or column names. It knows nothing about where the operator's
data actually lives -- the operator supplies the exact SQL query themselves (``--query``), and this
module only ever reads the first column of each row it gets back. Nothing here is a description of
any real product's database.

Two safety properties, matching TASK-87's acceptance criteria:
1. The source database is opened strictly read-only -- SQLite URI ``mode=ro``, the same pattern
   app/wa/luna/shadow_run.py's db_copy() uses -- so a typo'd or malicious --query can select but
   never write, and a missing --db path fails loudly instead of silently creating an empty file.
2. The output is written atomically: a temp file in the same directory as --out, then
   ``os.replace()`` into place. This file is read by a live, concurrently-running webhook process
   (routing.py) on every brand-new phone number it sees; a reader must never be able to observe a
   half-written file mid-export, and a failed export must never corrupt or truncate whatever was
   there before.

A row whose value does not canonicalize to a real-looking number is reported back, never silently
dropped -- same "no silent safety nets" rule as migrate_candidates.py, and the same MIN_PHONE_DIGITS
footgun that module already guards against (canonicalize_phone() can return a truthy-but-bogus
value like "+49" for garbage input).
"""
import argparse
import os
import sqlite3
import tempfile

from .. import meta as M

MIN_PHONE_DIGITS = 8  # shorter than any real E.164 number -- catches e.g. canonicalize_phone("not
                      # a number") -> "+49" (country code only, every digit stripped as non-numeric)


def _canonical_phone(raw, default_country_code):
    canon = M.canonicalize_phone(raw, default_country_code)
    if not canon or len(canon.lstrip("+")) < MIN_PHONE_DIGITS:
        return None, f"phone did not canonicalize to a real number: {raw!r} -> {canon!r}"
    return canon, None


def export(db_path, query, out_path, default_country_code=None):
    """Run ``query`` against ``db_path`` (opened strictly read-only) and write every row's first
    column, canonicalized and deduplicated, one per line, sorted, to ``out_path`` -- atomically, so
    a concurrent reader of ``out_path`` never sees a half-written file. -> {"exported": int,
    "skipped": [{"row": ..., "reason": ...}]}. ``query`` is run exactly as given: this function has
    no opinion about, and makes no assumption about, what table or columns it selects from."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        rows = src.execute(query).fetchall()
    finally:
        src.close()

    phones, skipped = set(), []
    for row in rows:
        raw = row[0]
        canon, reason = _canonical_phone(raw, default_country_code)
        if reason:
            skipped.append({"row": raw, "reason": reason})
            continue
        phones.add(canon)

    out_path = os.fspath(out_path)
    out_dir = os.path.dirname(out_path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".export_known_phones-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for phone in sorted(phones):
                f.write(phone + "\n")
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return {"exported": len(phones), "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="path to the operator's own sqlite database, opened read-only")
    ap.add_argument("--query", required=True,
                     help="exact SQL to run; the first column of each row must be a phone-like value")
    ap.add_argument("--out", required=True,
                     help="path to write (atomically) -- typically WA_REAL_SYSTEM_PHONES_FILE")
    ap.add_argument("--country-code", help="default country code for un-prefixed numbers (see canonicalize_phone)")
    args = ap.parse_args(argv)

    result = export(args.db, args.query, args.out, default_country_code=args.country_code)
    print(f"exported {result['exported']}, skipped {len(result['skipped'])}")
    for s in result["skipped"]:
        print(f"  SKIPPED ({s['reason']}): {s['row']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
