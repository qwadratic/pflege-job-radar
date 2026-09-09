"""Crawl routing: turn the clinics registry into "where do I fetch, and with what?".

The registry already stores the two facts a crawler needs -- `ats_type` (which adapter) and
`careers_url` (where the board is). This module makes that pair *usable* by adding the three things
the raw columns do not express:

1. **Which adapter implements a vendor.** `ats_type` is a label produced by discovery; some labels
   have no adapter yet. Routing must say "labelled but unsupported", not crash.
2. **Shared boards.** 143 of 407 clinics share a careers_url with at least one other clinic (kbo 8,
   Schön Klinik 7, Asklepios 7 ...). Fetching per clinic multiplied 490 real vacancies into 1,319
   rows. The board -- not the clinic -- is the unit of work, so routing groups by it.
   Grouping is by *exact* URL on purpose: two mein-check-in tenants share a host but list different
   jobs, so host-level grouping would silently drop one of them.
3. **Known-unfetchable sites.** Some boards are bot-walled (Helios returns 403 "Access Denied" to any
   datacenter IP). That is a property worth carrying, so a zero-yield crawl is not mistaken for
   "this clinic is not hiring".

Usage:
    python -m crawlers.routing              # human-readable coverage report
    python -m crawlers.routing --plan       # the fetch plan, one line per board
"""
import argparse
import json
import os
import re
from collections import defaultdict

import requests

PROJECT = os.environ.get("SUPABASE_PROJECT_URL", "https://klkxfvieaxpjlplloljn.supabase.co")

# Vendors with a working adapter, and where it lives. Kept here rather than importing every crawler
# so that routing can be inspected without pulling in Playwright and friends.
# Vendors we can actually fetch, and the callable that does it. Verified importable by
# tests/test_routing.py -- a label with no working entry point is worse than no label, because the
# scheduler would keep handing it work that silently returns nothing.
#
# Two shapes exist, and routing records which:
#   "vendor"  -> crawlers.vendor_adapters.VENDORS[<name>](clinic, session)  -- takes a clinic row
#   "seeded"  -> pflege_jobs.sources.<mod>.crawl(seed, towns, ...)          -- takes a prepared seed
# The seeded ones need a seed built by their own module (softgarden.seed_for, bite.api_key, ...),
# so they are listed as supported but are driven by their existing runners, not called directly here.
ADAPTERS = {
    "rexx":            ("vendor", "crawlers.vendor_adapters:crawl_rexx"),
    "mein-check-in":   ("vendor", "crawlers.vendor_adapters:crawl_mein_check_in"),
    "personio":        ("vendor", "crawlers.vendor_adapters:crawl_personio"),
    "smartrecruiters": ("vendor", "crawlers.vendor_adapters:crawl_smartrecruiters"),
    "helix":           ("vendor", "crawlers.vendor_adapters:crawl_helix"),
    "concludis":       ("vendor", "crawlers.vendor_adapters:crawl_wp_jobs"),
    "typo3_jobs":      ("vendor", "crawlers.vendor_adapters:crawl_wp_jobs"),
    "talention":       ("vendor", "crawlers.vendor_adapters:crawl_wp_jobs"),
    "oracle":          ("vendor", "crawlers.vendor_adapters:crawl_oracle"),
    # Default fallback for careers_url-but-no-vendor-label boards (see plan() below) -- same
    # sitemap/page-link job discovery as the other WordPress/TYPO3 sites, just without a
    # fingerprinted vendor name attached.
    "wp_jobs":         ("vendor", "crawlers.vendor_adapters:crawl_wp_jobs"),
    "dvinci":          ("vendor", "crawlers.vendor_adapters:crawl_dvinci"),
    "bite":            ("seeded", "pflege_jobs.sources.bite:crawl"),
    "bite_jobs":       ("seeded", "pflege_jobs.sources.bite:crawl"),
    "pi_asp":          ("seeded", "pflege_jobs.sources.pi_asp:crawl"),
    "softgarden":      ("seeded", "pflege_jobs.sources.softgarden:seed_for"),
    # STALE: nothing imports crawlers.portals for this; ats_seeds.BUILDERS["umantis"] + the generic
    # listing-first career_crawl.Crawler drive umantis in practice via _seed_obs. Left here only
    # because ADAPTERS is keyed by ats_type and something may still look this entry up -- do not
    # trust it as the live code path.
    "umantis":         ("external", "crawlers.portals:parse_umantis"),
}

# Boards that reject datacenter traffic outright; a 0-row crawl here means "walled", not "no jobs".
WALLED = re.compile(r"helios-gesundheit\.de|helios\.de", re.I)


def load(key=None):
    key = key or os.environ["SUPABASE_ANON_KEY"]
    r = requests.get(PROJECT + "/rest/v1/clinics?select=clinic_id,name,town,beds,website,"
                     "careers_url,ats_type,status&limit=1000",
                     headers={"apikey": key, "Authorization": "Bearer " + key,
                              "Accept-Profile": "pflege_jobs"}, timeout=60)
    r.raise_for_status()
    return r.json()


PI_SEEDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "registry", "pi_seeds.json")


def seed_overlays(path=PI_SEEDS):
    """P&I boards are seeded by hand (data/registry/pi_seeds.json) because the vendor cannot be fingerprinted
    from the clinic's own site -- Helios walls helios-gesundheit.de, but its P&I board answers. The seed is
    therefore a routing fact in its own right: clinic_id -> (ats_type, careers_url)."""
    try:
        with open(path, encoding="utf-8") as f:
            seeds = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for s in seeds:
        url = "https://%s/bewerber-web/?companyEid=%s" % (s.get("host"), s.get("companyEid"))
        kezs = [(s.get("default") or {}).get("kez")] + [(x or {}).get("kez") for x in (s.get("sites") or {}).values()]
        for k in kezs:
            if k:
                out[str(k)] = ("pi_asp", url)
    return out


def plan(clinics):
    """Group routable clinics into one entry per board. Returns (boards, unroutable)."""
    boards, unroutable = defaultdict(lambda: {"clinics": [], "vendor": None, "adapter": None}), []
    overlays = seed_overlays()
    for c in clinics:
        if not (c.get("ats_type") or "").strip() and c.get("clinic_id") in overlays:
            c = {**c, "ats_type": overlays[c["clinic_id"]][0], "careers_url": overlays[c["clinic_id"]][1]}
        vendor = (c.get("ats_type") or "").strip()
        url = (c.get("careers_url") or "").strip()
        if not url:
            unroutable.append((c, "no careers_url" if not vendor else "vendor known, no entry point"))
            continue
        if not vendor:
            # No fingerprinted vendor, but there is a careers_url -- default to the generic
            # WordPress/TYPO3-style crawler (sitemap-or-page-link job discovery) rather than
            # skipping the board outright. A zero-yield crawl_wp_jobs pass still beats never
            # fetching at all; see plan action "Give unlabelled boards a default wp_jobs route".
            vendor = "wp_jobs"
            c = {**c, "ats_type": vendor}
        if vendor not in ADAPTERS:
            unroutable.append((c, "no adapter for %s" % vendor))
            continue
        b = boards[url.lower()]
        b["clinics"].append(c)
        b["vendor"], b["url"] = vendor, url
        b["kind"], b["adapter"] = ADAPTERS[vendor]
        b["walled"] = bool(WALLED.search(url))
    return boards, unroutable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="print the fetch plan as JSON lines")
    a = ap.parse_args()
    clinics = load()
    boards, unroutable = plan(clinics)
    if a.plan:
        for url, b in sorted(boards.items(), key=lambda kv: -len(kv[1]["clinics"])):
            print(json.dumps({"url": b["url"], "vendor": b["vendor"], "adapter": b["adapter"],
                              "kind": b["kind"], "walled": b["walled"],
                              "clinic_ids": [c["clinic_id"] for c in b["clinics"]]}, ensure_ascii=False))
        return
    routable = sum(len(b["clinics"]) for b in boards.values())
    shared = {u: b for u, b in boards.items() if len(b["clinics"]) > 1}
    print("clinics %d" % len(clinics))
    print("  routable            %4d clinics" % routable)
    print("  -> boards to fetch  %4d   (%d shared by >1 clinic; %d fetches saved)"
          % (len(boards), len(shared), routable - len(boards)))
    print("  walled boards       %4d" % sum(1 for b in boards.values() if b["walled"]))
    print("  not routable        %4d" % len(unroutable))
    why = defaultdict(int)
    for _, reason in unroutable:
        why[reason] += 1
    for reason, n in sorted(why.items(), key=lambda kv: -kv[1]):
        print("      %-34s %4d" % (reason, n))
    print("\n  biggest shared boards:")
    for url, b in sorted(shared.items(), key=lambda kv: -len(kv[1]["clinics"]))[:6]:
        print("    %2dx %-14s %s" % (len(b["clinics"]), b["vendor"], b["url"][:64]))


if __name__ == "__main__":
    main()
