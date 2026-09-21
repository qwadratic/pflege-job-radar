---
id: TASK-79
title: >-
  career_crawl.py _page_hosts_ok TLD-widening admits any off-domain URL when a
  seed host is a bare apex domain
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 19:30'
updated_date: '2026-09-21 04:01'
labels: []
dependencies: []
priority: low
ordinal: 79000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-76 AC#6 re-checked the REFUTED '_page_hosts_ok TLD widening' finding from the 2026-09-18 crawler audit against the current registry (post-TASK-65).

pflege_jobs/sources/career_crawl.py:283 _page_hosts_ok(u, hosts) widens a same-host check to: p.netloc.endswith('.' + h.split('.', 1)[-1]) and ('job' in p.netloc or 'karriere' in p.netloc or 'softgarden' in p.netloc or 'dvinci' in p.netloc), for any h in hosts. h.split('.', 1)[-1] is meant to strip one leading subdomain label (e.g. 'karriere.example.de' -> 'example.de', a safe same-registrable-domain check). But when h is itself a BARE two-label apex domain (e.g. 'kbo-iak.de', no subdomain), h.split('.', 1)[-1] degenerates to just 'de' -- the TLD alone. Reproduced live (2026-09-20): _page_hosts_ok('https://irrelevantsite-jobs.de/x', {'kbo-iak.de'}) returns True, i.e. an ENTIRELY unrelated .de domain is accepted as 'the same board' purely because its netloc happens to contain the substring 'job'.

59 of the registry's current careers_url values have a bare two-label netloc (grep data/registry/clinics.csv for careers_url whose host has exactly one dot before the TLD). Of those, the ones that actually reach this code path are the 'seeded' vendors that run career_crawl.Crawler's generic BFS (_section_link/_crawl_urls) rather than a bespoke fetcher: confirmed live that softgarden's own feed-first fetch_feed() and bite.py/pi_asp.py never call Crawler's BFS at all (only softgarden's own no-feed FALLBACK path and ats_seeds.py's umantis hub-hop do) -- e.g. kbo-iak.de (7 clinics: 16251/16252/16263/17405/17704/17803/18402, all umantis) is one live board that takes the umantis hub-hop route through this exact widened check.

Not yet confirmed live: whether any of these boards' actually-fetched pages contain an outbound link matching the degenerate pattern (an unrelated .de host containing 'job'/'karriere'/'softgarden'/'dvinci'), which would need per-tenant HTML inspection this investigation did not complete. The mechanism itself is proven exploitable by construction; whether it has already pulled a wrong URL into a live crawl is the open question this task should answer before or while fixing it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Confirm live whether any current seeded-vendor board with a bare-apex host (start with kbo-iak.de/umantis) actually has this widened check admit an off-domain URL today, with the specific URL as evidence -- or confirm none do
- [x] #2 _page_hosts_ok's widening is narrowed so a bare apex host (e.g. 'kbo-iak.de') no longer collapses to a bare TLD suffix match -- e.g. always compare against the last two labels of h, never fewer
- [x] #3 A regression test pins the reported false-accept (_page_hosts_ok('https://irrelevantsite-jobs.de/x', {'kbo-iak.de'}) must be False after the fix) alongside the existing legitimate widening cases (subdomain hop within the same registrable domain) staying True
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. AC#1 evidence: enumerate every board that actually reaches career_crawl.Crawler's BFS (routing.plan -> umantis + softgarden-no-feed-fallback), build each seed live, print its `hosts` set, flag bare-apex entries; for every BFS-reaching board fetch the real start URLs and diff current-check vs fixed-check over every anchor. Cross-check stored posting_observations.source_url (source_id 20) against each clinic's careers_url registrable domain.
2. Root-cause fix: replace the naive one-label strip h.split('.',1)[-1] with a last-two-labels registrable-domain compare, shared by a small module-level helper in career_crawl.py.
3. Same defect, sibling call site: pflege_jobs/sources/career_browser.py:84 has the identical degenerate strip AND a missing leading dot (endswith without '.'), so fix it through the same helper -- report it rather than expand silently.
4. Regression tests in tests/test_career_crawl_section.py pinning the reported kbo-iak.de false-accept plus the legitimate subdomain-hop widening cases; mutation-test them (revert fix -> red, restore -> green).
5. Targeted tests, then the full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 answered: NO live crawl has ever been affected, and the task description's premise is wrong about kbo-iak.de.

Evidence (all read-only, free HTTP + read-only DB), 2026-09-21:

1. Only two vendors drive career_crawl.Crawler's BFS in production (app/crawl.py::_seed_obs): umantis, and softgarden's no-feed FALLBACK. crawlers.routing.plan over the current registry gives 5 umantis + 21 softgarden boards. Built every one of those 26 seeds live and printed the resulting `hosts` set (seed['hosts'] | {netloc of seed['career']}, exactly what _page_hosts_ok is handed): 5 softgarden careers_urls yield no seed at all, and **every single one of the remaining 21 hosts sets contains only 3+-label hosts** -- softgarden tenant hosts (*.softgarden.io / *.career.softgarden.de), umantis instances (recruitingapp-NNNN.de.umantis.com) and www.anregiomed.de. bare-apex entries: zero.

2. kbo-iak.de specifically does NOT enter `hosts`. ats_seeds.umantis()'s hub-hop reads the umantis instance off the hub page and seeds hosts=[that instance]; live today the kbo board resolves to hosts={'recruitingapp-5656.de.umantis.com'}, career=https://recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=All. The bare apex is only the registry careers_url, which is consumed by the seed builder and then dropped. hub_needs_own_host (the one branch that WOULD put a site's own host into hosts) fires on no live board right now.

3. Fetched the real start URLs of all 14 BFS-reaching boards and ran every anchor through both the old and the new check: **zero links classified differently in either direction** (no false-accept the old code admitted, and nothing legitimate the new code drops). So the fix recovers no postings and loses none -- it closes a latent hole.

4. DB cross-check (read-only): 3318 employer_ats (source_id 20) observations; for the 48 clinics sitting on a umantis/softgarden board, all 681 observations' source_url hosts are the board's own softgarden tenant, its umantis instance, the clinic's own careers domain, softgarden's short.sg shortener or the operator's sibling softgarden tenant (kjf-augsburg.softgarden.io for Josefinum). No unrelated third-party host anywhere.

Conclusion: exploitable by construction, never exploited live -- the degenerate input (a bare two-label host inside `hosts`) is currently unreachable on every routed board, but it is one softgarden custom-domain detection or one umantis hub_needs_own_host hop away from being reachable.

Sibling call site fixed too (flagged, not silent): pflege_jobs/sources/career_browser.py:84 (BrowserCrawler.crawl) carried its own copy of the same widening and was strictly worse -- p.netloc.endswith(h.split('.',1)[-1]) has no leading dot and no keyword gate at all, so with hosts={'kbo-iak.de'} it admitted every .de host, and with hosts={'karriere.klinik.de'} it admitted 'notklinik.de'. Both call sites now go through the new career_crawl._registrable_domain helper (same naive-eTLD+1 notion as the existing crawlers.vendor_adapters._registrable_domain/_same_board, which the generic crawler already uses).

Mutation-tested both halves: reverting the career_crawl expression reds test_page_hosts_ok_rejects_an_unrelated_domain_for_a_bare_apex_seed_host; reverting the career_browser expression reds test_browser_crawler_link_gate_rejects_an_unrelated_domain_for_a_bare_apex_seed_host (job_links_found 3 != 1). Restored -> 13 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Root cause: both same-board host gates computed a 'registrable domain' by stripping exactly one leading label (h.split('.', 1)[-1]), which is only correct when h has a subdomain -- for a bare two-label apex host ('kbo-iak.de') it collapsed to the bare TLD 'de'. Replaced with a last-two-labels compare via a new pflege_jobs/sources/career_crawl.py::_registrable_domain helper (same notion as the existing crawlers.vendor_adapters._registrable_domain), used by Crawler._page_hosts_ok and by the sibling copy in career_browser.BrowserCrawler.crawl -- which was strictly worse, missing the leading dot and the keyword gate entirely.

Verified: (a) AC#1 answered live -- built all 26 umantis/softgarden seeds (the only boards that reach this BFS) and none carries a bare-apex host in its hosts set, kbo-iak.de included (it resolves to recruitingapp-5656.de.umantis.com); re-ran every anchor on all 14 BFS-reaching boards' start pages through both the old and the new check with zero classification differences; DB cross-check of all 681 employer_ats observations on those 48 clinics found no foreign host. Exploitable by construction, never exploited live. (b) Three regression tests in tests/test_career_crawl_section.py pin the reported false-accept, the legitimate subdomain hops, and the browser-crawler gate; both halves mutation-tested red on revert. (c) Full offline suite: 1231 passed, 1 skipped, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
