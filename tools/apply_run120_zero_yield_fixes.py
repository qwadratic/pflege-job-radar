"""Registry corrections for 3 of the run-120 (2026-09-22) zero-yield boards -- the other boards in
that batch needed a code fix instead (committed separately) or are filed as backlog tasks (see the
run's own final summary). These 4 need no code change at all: the right adapter/vendor already
exists, the board only needs to be routed to it.

  56404  Diakoneo (Klinik Hallerwiese Nürnberg): careers_url renders via client JS on plain fetch,
         so crawl_wp_jobs reads 0 -- but the Pflege-section subpage carries a B-ITE jobs-api widget
         fingerprint (data-bite-jobs-api-listing="diakoniewerk-schwaebisch-hall:main-listing").
         data/registry/bite_seeds.json already has this seed (added this session, no production
         write needed for it). Verified live: bite.crawl() reads 223 postings, 145 role_class !=
         nicht_pflege. Only ats_type needs to change here.
  77201/77202  Wertachkliniken: registered domain has no real content (cookie-consent-gated JS
         widget); the working board is a DIFFERENT subdomain, a rexx-systems board TASK-49 already
         found and left undelivered. Verified live 2026-09-22: crawl_rexx reads 13 rows there.
  26205  BKH Passau: its own /karriere/ page hands off to "Alle Stellenangebote anzeigen" ->
         mainkofen.de/karriere-bkm/stellen/ (a different operator site, BKH Passau is an outpost of
         Bezirksklinikum Mainkofen) -- which itself hands off again to a mein-check-in board.
         Verified live: crawl_mein_check_in reads 42 rows there, several "Pflegefachpersonen".

NOT included: 16254 (www.pflegejobs.brk-muenchen.de) also has a working pi_asp route (verified live,
20 rows, companyEid=123), but the board is BRK-Kreisverband München's own ambulant/social-services
job list, not clinic 16254's own postings -- 16254 is "Tagesklinik Süd für Psychiatrie und
Psychotherapie", the registered careers_url just happens to point there. Same over-attribution
question TASK-109 and TASK-103 already raise for other operators; needs Ivan's explicit call, not a
silent default.kez seed (reverted from data/registry/pi_seeds.json this session).

Snapshots the live rows to backups/ first, writes via EdgeSink, reads back and prints the result.
Idempotent: a second run changes nothing once applied.

  set -a; source .env; set +a && .venv/bin/python tools/apply_run120_zero_yield_fixes.py
"""
import datetime, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A                                    # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC                      # noqa: E402
from pflege_jobs.sinks import EdgeSink                          # noqa: E402

TARGETS = {
    "56404": {"ats_type": "bite"},                                                              # careers_url unchanged, seeded
    "77201": {"careers_url": "https://karriere-wertachkliniken.de/stellenangebote.html", "ats_type": "rexx"},
    "77202": {"careers_url": "https://karriere-wertachkliniken.de/stellenangebote.html", "ats_type": "rexx"},
    "26205": {"careers_url": "http://www.mein-check-in.de/mainkofen/", "ats_type": "mein-check-in"},
}

live = A.rest_get("clinics", {"select": "*", "clinic_id": f"in.({','.join(TARGETS)})"})
if len(live) != len(TARGETS):
    sys.exit(f"expected {len(TARGETS)} row(s), got {len(live)}: {sorted(str(r.get('clinic_id')) for r in live)}")

stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = os.path.join(str(A.ROOT), "backups", f"run120_zero_yield_fixes_before_{stamp}.json")
os.makedirs(os.path.dirname(backup), exist_ok=True)
with open(backup, "w", encoding="utf-8") as f:
    json.dump(live, f, ensure_ascii=False, indent=1)
print(f"backup: {backup}")

rows = []
for r in sorted(live, key=lambda x: str(x["clinic_id"])):
    cid = str(r["clinic_id"])
    changes = TARGETS[cid]
    diff = "; ".join(f"{col} {r.get(col)!r} -> {new!r}" for col, new in changes.items())
    print(f"  {cid} {str(r.get('name'))[:34]:34} {diff}")
    # CLINIC_SPEC is a list of (column, pg_type) pairs, not bare column names
    rows.append({col: r.get(col) for col, _ in CLINIC_SPEC} | {"clinic_id": cid} | changes)

n = EdgeSink().write_clinics(rows)
print(f"rows written: {n}")

back = A.rest_get("clinics", {"select": "clinic_id,name,careers_url,ats_type", "clinic_id": f"in.({','.join(TARGETS)})"})
ok = True
for r in sorted(back, key=lambda x: str(x["clinic_id"])):
    cid = str(r["clinic_id"])
    changes = TARGETS[cid]
    good = all(r.get(col) == new for col, new in changes.items())
    ok &= good
    print(f"  {'OK ' if good else 'NO '} {cid} careers_url={r.get('careers_url')!r} ats_type={r.get('ats_type')!r}")
print("result:", f"all {len(TARGETS)} rows applied" if ok else "NOT ALL APPLIED, see above")
