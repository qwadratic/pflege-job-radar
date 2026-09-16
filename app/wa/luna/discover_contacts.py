"""Batch pre-population of clinic_contacts (TASK-64):

    python -m app.wa.luna.discover_contacts --clinic-id 16104
    python -m app.wa.luna.discover_contacts --all

Reads clinics from app.data's snapshot, runs contacts.discover_contact() for each, and saves any
hit. This is the only place contacts.discover_contact() is called for real -- the WA harness and
its future MCP tool only ever read the table it fills (contacts.get_contact()); no live crawl
happens during a WhatsApp turn.
"""
import argparse

from ... import data as D
from . import contacts as CT


def _postings_for(clinic_id):
    """This clinic's own open postings, same shape/filter app.wa.brain.jobs_for() already uses."""
    return D.filter_jobs({"clinic_id": clinic_id})


def _run_one(c, clinic):
    hit = CT.discover_contact(clinic, _postings_for(clinic["clinic_id"]))
    if hit:
        CT.save_contact(c, clinic["clinic_id"], hit["email"], hit["source"], hit["confidence"])
    return hit


def run(targets):
    """targets: clinic dicts to resolve. -> (n_found, n_total). Opens/closes its own connection."""
    c = CT.db()
    try:
        found = 0
        for clinic in targets:
            hit = _run_one(c, clinic)
            state = f"{hit['source']} ({hit['confidence']}): {hit['email']}" if hit else "nothing found"
            print(f"{clinic['clinic_id']}\t{(clinic.get('name') or '')[:40]}\t{state}")
            found += int(bool(hit))
        print(f"{found}/{len(targets)} clinics resolved")
        return found, len(targets)
    finally:
        c.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--clinic-id", help="discover a contact for one clinic")
    group.add_argument("--all", action="store_true", help="discover a contact for every clinic in the snapshot")
    args = ap.parse_args(argv)

    clinics = D.clinics()
    if args.clinic_id:
        targets = [c for c in clinics if c["clinic_id"] == args.clinic_id]
        if not targets:
            raise SystemExit(f"no clinic {args.clinic_id!r} in the snapshot")
    else:
        targets = clinics

    run(targets)


if __name__ == "__main__":
    main()
