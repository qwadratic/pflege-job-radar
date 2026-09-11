---
id: TASK-48
title: Zero-yield boards with real static job links (recon 2026-09-11)
status: To Do
assignee: []
created_date: '2026-09-11 10:48'
updated_date: '2026-09-11 12:08'
labels: []
dependencies: []
ordinal: 48000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
During the full 220-board delivery pass, 43 boards returned raw=0 or kept=0. A quick curl+grep recon (no JS) against each board's exact careers_url found 15 of them DO have static, JOB_PATH-matching href links on the page (job_hrefs>0), yet the adapter still returned 0 rows. This means either the ats_type/adapter routing is wrong for these, the seed_for()/detection step (e.g. softgarden's SG_HOST regex) fails to fingerprint the vendor, or the matched hrefs point at content that is itself JS-hydrated at fetch time (confirmed root cause for hessing-kliniken.de, see crawlers/vendor_adapters.py comment near JOB_PATH -- its 3 job links needed an html.unescape fix already applied this session but still return a generic page shell, not real job content, because the actual data loads via a client-side softgarden widget call). Each board needs its own quick investigation: check ats_type routing, check whether seed_for()/find_host() actually fires, and check whether the detail page needs the widget's real AJAX endpoint instead of the static href.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each board below is triaged: fixed with a registry/routing change, OR confirmed to need widget/API reverse-engineering and filed as its own follow-up
- [ ] #2 boards, beds, and job_hrefs count (from recon): schwesternschaft-muenchen.de(435,1) karriere-barmherzige-muenchen.de(404,11) karriere.ameos.eu(358,11) karriere.kirinus.de/56413-Nuernberg-branch(295,71, already fixed 2026-09-11) www.kh-as.de(180,3) karriere.barmherzige.net(174,68) www.hessing-kliniken.de(150,3, JS-widget confirmed) www.spezialklinik-neukirchen.de(140,6) www.kreisklinik-woerth.de(125,3) www.klinik-feldafing.de(115,5) www.fachklinik-sankt-lukas.de(80,2) www.augenklinik-muenchen.de(47,3) www.orthopaedie-manufaktur.de(40,5) www.waldhausklinik.de(40,3) klinik-steger.de(25,5)
- [ ] #3 No new adapter code added without confirming the real job content is reachable without a browser -- a page having href='...job...' text does not guarantee its content is server-rendered
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2 of 15 boards fixed 2026-09-11: both had a wrong ats_type in the registry, not a genuine adapter gap. 16214 (Krankenhaus Barmherzige Bruder Muenchen, 404 beds) was labelled softgarden but is a plain static site with clean /stellenanzeige/<slug> detail links -- blanked ats_type so it falls back to the generic wp_jobs route; delivered 23 real pflege postings (0 before). 16219 (Krankenhaus Neuwittelsbach, 122 beds) shares karriere.barmherzige.net with 16226 (Maria-Theresia-Klinik, correctly labelled personio) -- same board, same vendor, just a different ?filter[company][] query value; 16219 was mislabelled softgarden, relabelled to personio; delivered 5 real pflege postings (0 before). Remaining 13 boards in this task not yet triaged.
<!-- SECTION:NOTES:END -->
