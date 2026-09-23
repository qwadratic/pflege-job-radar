"""TASK-117 AC#1: clinic 16254 (Tagesklinik Sued fuer Psychiatrie und Psychotherapie, BRK Muenchen)
routes through crawl_wp_jobs against the registered careers_url (a marketing landing page) and finds
nothing -- the real board is brkm.pi-asp.de, a P&I LOGA bewerber-web instance, live-verified this
session to carry 20 real postings (several Pflege). Two things were missing, both fixed here:

1. pflege_jobs/sources/pi_asp.py's crawl() hardcoded "?companyEid=<id>" -- BRK Muenchen's tenant
   needs "?company=<id>" instead (confirmed live via a direct Playwright probe before touching code).
   Code fix: crawl() now reads an optional seed["param"], defaulting to "companyEid" so the two
   existing seeds (Helios, Regiomed) are unaffected.
2. data/registry/pi_seeds.json had no entry for brkm.pi-asp.de at all, and clinic 16254's live
   ats_type ('typo3_jobs') is non-empty, so routing.seed_overlays()'s "only when ats_type is blank"
   guard would never have applied a seed even if one existed. Adds the seed AND sets ats_type='pi_asp'
   directly on the live clinic row (the sanctioned EdgeSink.write_clinics path, full CLINIC_SPEC row,
   only ats_type changed -- matches TASK-86's own discipline for registry writes).

  set -a; source .env; set +a && .venv/bin/python tools/task117_fix_brk_muenchen_pi_asp.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A  # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC  # noqa: E402
from pflege_jobs.sinks import EdgeSink  # noqa: E402

PI_SEEDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "registry", "pi_seeds.json")

seeds = json.load(open(PI_SEEDS, encoding="utf-8"))
if not any(s.get("host") == "brkm.pi-asp.de" for s in seeds):
    seeds.append({"name": "BRK München", "host": "brkm.pi-asp.de", "companyEid": "123-FIRMA-ID", "param": "company",
                  "default": {"kez": "16254", "town": "München"}, "sites": {}})
    with open(PI_SEEDS, "w", encoding="utf-8") as f:
        json.dump(seeds, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("pi_seeds.json: added brkm.pi-asp.de")
else:
    print("pi_seeds.json: brkm.pi-asp.de already present, left unchanged")

cols = ",".join(c for c, _ in CLINIC_SPEC)
row = A.rest_get("clinics", {"select": cols, "clinic_id": "eq.16254"})[0]
if row["ats_type"] == "pi_asp":
    print("clinic 16254: ats_type already pi_asp, no write needed")
else:
    row["ats_type"] = "pi_asp"
    n = EdgeSink().write_clinics([row])
    print(f"clinic 16254: ats_type typo3_jobs -> pi_asp, wrote {n} row(s)")

check = A.rest_get("clinics", {"select": "clinic_id,ats_type,careers_url", "clinic_id": "eq.16254"})
print("live now:", check)
