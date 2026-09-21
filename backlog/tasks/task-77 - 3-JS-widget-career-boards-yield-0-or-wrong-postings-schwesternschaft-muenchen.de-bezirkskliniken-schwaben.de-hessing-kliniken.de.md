---
id: TASK-77
title: >-
  3 JS-widget career boards yield 0 or wrong postings:
  schwesternschaft-muenchen.de, bezirkskliniken-schwaben.de, hessing-kliniken.de
status: To Do
assignee: []
created_date: '2026-09-20 19:30'
labels: []
dependencies: []
ordinal: 77000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-76 AC#5 live-checked (2026-09-20) the 3 JS-widget boards the 2026-09-18 crawler audit flagged as unexercisable without a browser. All 3 confirmed to currently yield materially fewer postings than they actually publish, each for a different root cause:

1. schwesternschaft-muenchen.de (Rotkreuzklinikum München, clinic_id 16215, ats_type blank -> falls back to crawl_wp_jobs): live Playwright render of the registry careers_url itself (https://www.schwesternschaft-muenchen.de/stellenangebote/index.php) shows '18 Stellen gefunden' with real, nursing-heavy titles (Pflegefachkraft, Pflegefachhelfer, Wohnbereichsleitung, ...), all injected by inline JS with no sitemap entries and no server-rendered anchors. crawl_wp_jobs returns 0 rows for this URL (confirmed live: '[wp_jobs] find_job_urls: no job links in sitemap ... (0 sitemap urls seen)'). The titles carry standard (m/w/d) gender markers, the same shape crawlers/portals.py's parse_mwd_anchors_pw already parses for muenchen-klinik/altmuehlfranken -- this board fits that existing pattern directly, but crawlers/portals.py's JS_PORTALS crawler is a standalone CLI script never wired into crawlers/routing.py, so even adding an entry there would not make it part of the scheduled/routed crawl without further wiring.

2. bezirkskliniken-schwaben.de (Bezirkskliniken Schwaben KU, 6 clinics: 76114, 76203, 76304, 76403, 77707, 77907, all sharing careers_url https://www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung, ats_type blank): that careers_url is a marketing/FAQ landing page with zero job links of any kind (confirmed live: 0 job-like anchors, no iframe, no job-related XHR/script requests even after full render+scroll). The real, actively job-listing board lives on a DIFFERENT subdomain the landing page links to via 'Jetzt bewerben!'/'Offene Stellen': https://jobs.bezirkskliniken-schwaben.de/Jobs (a plain requests.get returns only an unrendered template shell containing the literal placeholder '/Job/{{Id}}'; Playwright render shows 59 distinct real posting URLs matching /Job/<numeric-id>). Neither the registry careers_url nor any current adapter targets this subdomain.

3. hessing-kliniken.de (Orthopädische Fachkliniken der Hessing Stiftung, clinic_id 76111, careers_url https://www.hessing-kliniken.de/karriere/ausbildung/, ats_type softgarden): crawl_oracle/crawl_softgarden's own pflege_jobs/sources/softgarden.py:find_host() returns (None, None) live against this exact careers_url -- 0 postings. Two compounding problems: (a) the registered careers_url is the Ausbildung (apprenticeship) subpage, not the real careers hub at https://www.hessing-kliniken.de/karriere/ (confirmed live: that page 200s and links 12+ /karriere/detail/?tx_softgarden_kategorieliste... category URLs); (b) even the correct /karriere/ page does not match find_host()'s detection patterns (SG_HOST expects *.career.softgarden.de / *.softgarden.io / jobdb.softgarden.de, or a same-page 'softgarden' + '/job/<id>/' link) -- this tenant runs softgarden through a TYPO3 extension (tx_softgarden_kategorieliste) whose links are /karriere/detail/?...cat=<uuid>&controller=Api&action=joblist, a shape find_host() has no branch for.

Same category of defect as TASK-65 (wrong/stale careers_url, aggregator host swaps) but these three were not part of that pass. Filed as its own task because two of the three also need new/widened adapter logic, not just a registry field fix.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 schwesternschaft-muenchen.de: registry ats_type/route change (or a new wired-in crawler) makes the real Stellenangebote list (Pflegefachkraft/Pflegefachhelfer roles etc., ~18 postings as of 2026-09-20) reach the inbox for clinic_id 16215
- [ ] #2 bezirkskliniken-schwaben.de: careers_url is corrected to (or a new adapter targets) https://jobs.bezirkskliniken-schwaben.de/Jobs so real postings (59 as of 2026-09-20) reach the inbox for clinic_ids 76114/76203/76304/76403/77707/77907
- [ ] #3 hessing-kliniken.de: careers_url is corrected to https://www.hessing-kliniken.de/karriere/ (or wherever the real listing lives) and pflege_jobs/sources/softgarden.py:find_host() (or a fallback) recognizes this tenant's TYPO3 tx_softgarden_kategorieliste shape so postings reach the inbox for clinic_id 76111
- [ ] #4 Each fix is verified with a live re-crawl showing postings > 0 for the affected clinic_id(s), not just a code-presence check
<!-- AC:END -->
