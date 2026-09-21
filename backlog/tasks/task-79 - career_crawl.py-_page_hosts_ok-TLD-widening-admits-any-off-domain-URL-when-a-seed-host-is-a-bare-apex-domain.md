---
id: TASK-79
title: >-
  career_crawl.py _page_hosts_ok TLD-widening admits any off-domain URL when a
  seed host is a bare apex domain
status: To Do
assignee: []
created_date: '2026-09-20 19:30'
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
- [ ] #1 Confirm live whether any current seeded-vendor board with a bare-apex host (start with kbo-iak.de/umantis) actually has this widened check admit an off-domain URL today, with the specific URL as evidence -- or confirm none do
- [ ] #2 _page_hosts_ok's widening is narrowed so a bare apex host (e.g. 'kbo-iak.de') no longer collapses to a bare TLD suffix match -- e.g. always compare against the last two labels of h, never fewer
- [ ] #3 A regression test pins the reported false-accept (_page_hosts_ok('https://irrelevantsite-jobs.de/x', {'kbo-iak.de'}) must be False after the fix) alongside the existing legitimate widening cases (subdomain hop within the same registrable domain) staying True
<!-- AC:END -->
