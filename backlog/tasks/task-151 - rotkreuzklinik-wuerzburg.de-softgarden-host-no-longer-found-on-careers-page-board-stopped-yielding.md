---
id: TASK-151
title: >-
  rotkreuzklinik-wuerzburg.de: softgarden host no longer found on careers page,
  board stopped yielding
status: Done
assignee: []
created_date: '2026-09-24 10:56'
updated_date: '2026-09-24 11:32'
labels: []
dependencies: []
ordinal: 151000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the 2026-09-24 overnight crawl (run 177/app run 177, crawl_issues kind='seeded', vendor='softgarden'): https://rotkreuzklinik-wuerzburg.de/stellenangebote/ -> 0 observations, error 'no softgarden host found on careers page'. Clinic 66303. The registry's ats_type='softgarden' seed presumably pointed at a softgarden-hosted iframe/link that the page no longer carries (redesign, vendor migration, or a URL/host change) -- needs a live check of the current page to find out what changed and whether it moved to a new vendor or just a new softgarden tenant host.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Live-checked what rotkreuzklinik-wuerzburg.de/stellenangebote/ actually serves today: still softgarden under a different host, moved to a different ATS entirely, or genuinely has no careers listing anymore
- [x] #2 Registry corrected (careers_url/ats_type) to match what's actually live, or documented as a dead board if it genuinely has nothing, via the same tools/apply_registry_corrections.py pattern TASK-116 uses
- [x] #3 Live-verified: a crawl against clinic 66303 after the fix yields real rows (or the documented reason it cannot)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-24 live investigation.

AC#1 -- what the page serves today. Reproduced the exact overnight error live with the repo's own code path: _seed_obs({vendor:'softgarden', careers_url: live DB value}, clinic 66303 row, ...) -> observations=0, stats={'error': 'no softgarden host found on careers page'} -- byte-identical to the run 177 finding. Root cause traced deeper than task 50's 2026-09-21/23 check: /stellenangebote/ still 403s to every UA (curl default, Chrome-128 full header set, plain requests with the crawler's own UA/career_crawl.UA -- all 403 "You don't have permission to access this resource"). But this is NOT the career-page-specific 403 task 50 recorded -- widened the probe today and found /karriere/, /kontakt/, /medizin-und-pflege/, /ihr-aufenthalt/ (i.e. every directory-style URL on the whole site, career-related or not) also 403, while a genuinely nonexistent path (/nichtvorhanden/) correctly 404s and real content lives only at flat .php files (e.g. /Helfen-durch-Spenden.php, 200). That is a generic "no index file, autoindex off" webserver behavior across this whole site, not a targeted anti-bot wall and not career-specific -- so routing.py's WALLED exclusion decision from TASK-50 AC#2 stays correct (page-specific/sitewide-directory quirk, not a datacenter block; today's wider probe reconfirms rather than overturns that call).
Homepage (https://rotkreuzklinik-wuerzburg.de/, 200) still exists and is still branded "Rotkreuzklinik Würzburg", but its own nav "Karriere" submenu (Arbeiten in der Rotkreuzklinik Würzburg / Ausbildung und Studium / Praktikum) has every href='' -- non-functional, no real link anywhere on the rendered page. sitemap.xml (200) is a stale dev-environment artifact: every <loc> is prefixed with the old build host "rotkreuz.kunden-projekt.dev" (never rewritten to production), lastmod dates 2021-2023, listing /wuerzburg/karriere/index.php, /wuerzburg/karriere/bewerbungsformular.php, /wuerzburg/stellenangebote/index.php -- none of those resolve live (404/403) on the production domain today.
Ran crawlers/ats_discover2.py's real fingerprint() against both the live homepage HTML and the live /stellenangebote/ 403 response body+URL: both return (None, None) -- no vendor signal (softgarden or any of the other 25 fingerprinted ATS vendors) anywhere on this domain today. Not a host/tenant change, not a vendor migration -- genuinely no careers listing exists on this domain.
Cross-checked backlog task 50 (Done, 3/3 AC) and decision-1 (status=deferred): task 50's 2026-09-21 finding already established this exact page-specific 403 and explicitly chose NOT to add it to WALLED because decision-1 records the clinic filed Schutzschirmverfahren (Sept 2025) and ceased operations 2026-04-01 (staff absorbed by Klinikum Würzburg Mitte, per medconweb/kma-online/t-online/bibliomedmanager) -- i.e. the 0 is a real 0, not a transport failure. Today's evidence (broken nav, stale sitemap, no fingerprint anywhere, site-wide not career-specific directory 403s) is fully consistent with and reinforces that verdict; nothing found today contradicts task 50 or decision-1.

AC#2 -- registry correction or dead-board documentation. Verdict: genuinely dead board, same category as TASK-116's 67802 (Krankenhaus Markt Werneck) -- documented here, no registry value corrected to a new working URL because none exists to correct to. Checked whether the current live careers_url/ats_type (still 'https://rotkreuzklinik-wuerzburg.de/stellenangebote/' / 'softgarden', confirmed via live A.rest_get on pflege_jobs.clinics clinic_id=66303 today) should be blanked to stop the daily false 'seeded' crawl_issues error -- not done, and not actually possible through the standard write path: tools/apply_registry_corrections.py's own docstring/code confirms the upstream edge function coalesces careers_url/ats_type with `coalesce(nullif(excluded.col,''), stored)`, so writing '' for either field is a no-op against the stored value, not a clear. TASK-116's own precedent for the genuinely-dead case (67802) is exactly this: no registry write, backlog documentation only. Followed the same precedent here. Did NOT touch decision-1's proposed status='nicht_mehr_im_plan' change -- that is a separate deferred decision on a different field (operational status vs. careers board), explicitly waiting on Ivan's own go-ahead per its own text, out of this task's scope.

AC#3 -- live-verified crawl against 66303. Ran the actual production code path (app.crawl._seed_obs, the same function app/crawl.py's execute() calls for every seeded vendor) against clinic 66303's live, unmodified registry row (careers_url/ats_type read fresh via A.rest_get just before the call). Result: observations=0, stats={'error': 'no softgarden host found on careers page'} -- reproduces run 177 exactly, live, today, via the real adapter, not a guess. Precise reason it cannot yield rows: no softgarden host (or any other ATS vendor) is discoverable anywhere on rotkreuzklinik-wuerzburg.de today, per the ats_discover2.fingerprint() check above -- the site's careers section has been removed, consistent with the clinic's documented 2026-04-01 closure.

No code changed by this task (investigation + documentation only, no registry write applied, no source files touched) -- nothing to mutation-test.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Genuinely dead board, not a vendor migration: crawlers/ats_discover2.py's live fingerprint() found no ATS vendor anywhere on rotkreuzklinik-wuerzburg.de today (homepage or the /stellenangebote/ response), the site's own Karriere nav submenu is non-functional (every href=''), and its sitemap.xml is a stale pre-launch artifact pointing at a dev host that was never wired to production. Every directory-style URL on the whole site 403s (not just career pages) -- confirms task 50's TASK-50/decision-1 finding (page-quirk, not a bot wall) rather than overturning it; decision-1's closure evidence (insolvency Sept 2025, ceased operations 2026-04-01, staff absorbed by Klinikum Würzburg Mitte) explains why. No registry write applied: the live careers_url/ats_type point at nothing recoverable, and the upstream edge function's own coalesce behavior makes blanking those fields via the standard write path a no-op anyway -- documented as dead here, same precedent as TASK-116's 67802. Live-verified via the real production code path (app.crawl._seed_obs against the clinic's live, unmodified registry row): 0 observations, same error as run 177, reproduced today.
<!-- SECTION:FINAL_SUMMARY:END -->
