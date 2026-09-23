---
id: TASK-115
title: >-
  reisach-kliniken.de's real board is an EasyHR JSON API (easyhr-proxy.php); its
  own widget embed is broken on the site
status: To Do
assignee: []
created_date: '2026-09-22 17:13'
labels: []
dependencies: []
ordinal: 115000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinics 78008/78071 (azubis-praktikanten-studenten.html) and 77607/77672 (stellenangebote.html), both www.reisach-kliniken.de. Both pages carry a raw, un-rendered developer comment left in the page instead of the actual widget markup: "Seite bearbeiten -> Element 'Code' -> diesen kompletten Block einfügen (HTML, CSS, JS zusammen). Vorher anpassen: - PROXY_URL: Pfad zu easyhr-proxy.php - DETAIL_PAGE_URL: ..." -- the site owner's own EasyHR widget deployment is incomplete/broken. The PHP proxy file still exists and works despite the missing front-end block: GET https://www.reisach-kliniken.de/easyhr-proxy.php returns clean JSON ({error, from_cache, count, positions[]}), verified live 2026-09-22: 22 real postings across BOTH sub-brands (Hochgrat Klinik and Adula Klinik -- the position's own 'jobgroup' field cleanly separates them, and 'where' names the department, e.g. 'Pflege'), including at least 2 'PFLEGEFACHKRAFT / GESUNDHEITS- und KRANKENPFLEGER (m/w/d)' rows. No existing adapter in this codebase handles EasyHR.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A new adapter fetches easyhr-proxy.php and maps positions[] to the standard row shape (title, org, loc, url, description, employmentType where available)
- [ ] #2 jobgroup ('Hochgrat Klinik' vs 'Adula Klinik') is used to attribute each posting to the correct clinic_id pairing (78008/78071 vs 77607/77672 -- confirm which jobgroup maps to which pair against the registry before wiring, do not guess)
- [ ] #3 Verified live: the adapter reads all current postings with correct titles, and nursing-relevant rows (Pflege where='Pflege') classify correctly downstream
- [ ] #4 Red-green test using a frozen sample of the real easyhr-proxy.php JSON response, mutation-tested
<!-- AC:END -->
