"""Migrate real candidates into this harness's own wa_threads (TASK-71) -- idempotent, from a
generic JSON export an operator produces from wherever their real candidate data actually lives.
This module never queries any specific external system directly (same discipline as
external_contacts.py, TASK-69): it only knows the shape below, which any operator's own export
script can produce.

Usage:
    python -m app.wa.luna.migrate_candidates --input candidates_export.json [--dry-run]

Expected input: a JSON array of objects, only "phone" required:
    {"phone": "+491701234567",
     "qualification_path": "urkunde"|"defizit"|"kenntnispruefung"|null,
     "city": "München"|null, "department_pref": "Intensiv/IMC"|null,
     "housing_known": true|false, "housing_needed": true|false, "people_count": 1,
     "anonymous_send_consent": false,
     "cv_text": "..."|null, "urkunde_text": "..."|null}

A row missing "phone", or whose phone does not canonicalize (app.wa.meta.canonicalize_phone),
is reported as unmappable -- never silently dropped. Migrating twice is safe: an existing
thread's card is merged with (not replaced by) whatever the export carries, so a re-run only
fills in what it actually knows and never erases progress the harness has made since.
"""
import argparse
import json

from .. import meta as M
from .. import store as ST

# housing_needed (TASK-108) is the answer to "do you need a flat?"; housing_known only says it was asked.
CARD_FIELDS = ("qualification_path", "city", "department_pref", "housing_known", "housing_needed", "people_count",
               "anonymous_send_consent", "cv_text", "urkunde_text")


def _row_to_card(row):
    return {k: row[k] for k in CARD_FIELDS if row.get(k) is not None}


MIN_PHONE_DIGITS = 8  # shorter than any real E.164 number -- catches e.g. canonicalize_phone("not
                      # a number") -> "+49" (country code only, every digit stripped as non-numeric)


def _canonical_phone(row):
    phone = row.get("phone")
    if not phone:
        return None, "missing phone"
    canon = M.canonicalize_phone(str(phone))
    if not canon or len(canon.lstrip("+")) < MIN_PHONE_DIGITS:
        return None, f"phone did not canonicalize to a real number: {phone!r} -> {canon!r}"
    return canon, None


def migrate(rows, conn=None, write=True):
    """-> {migrated: [phones], unmappable: [{row, reason}]}. write=False previews without touching
    the database (still resolves phone canonicalization for an accurate count)."""
    own_conn = conn is None and write
    c = conn or (ST.db() if write else None)
    migrated, unmappable = [], []
    try:
        for row in rows:
            canon, reason = _canonical_phone(row)
            if reason:
                unmappable.append({"row": row, "reason": reason})
                continue
            if write:
                t = ST.thread(c, canon)
                t["slots"] = {**t["slots"], **_row_to_card(row)}
                ST.save_thread(c, t)
            migrated.append(canon)
    finally:
        if own_conn:
            c.close()
    return {"migrated": migrated, "unmappable": unmappable}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="path to the generic candidate export JSON")
    ap.add_argument("--dry-run", action="store_true", help="report what would be migrated, write nothing")
    args = ap.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise SystemExit(f"{args.input} must contain a JSON array of candidate objects")

    result = migrate(rows, write=not args.dry_run)
    verb = "would migrate" if args.dry_run else "migrated"
    print(f"[dry-run] {verb} {len(result['migrated'])}, unmappable {len(result['unmappable'])}"
          if args.dry_run else f"{verb} {len(result['migrated'])}, unmappable {len(result['unmappable'])}")
    for u in result["unmappable"]:
        print(f"  SKIPPED ({u['reason']}): {u['row']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
