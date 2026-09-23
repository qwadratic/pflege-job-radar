---
id: TASK-114
title: >-
  augencentrum.de's CleanTalk anti-bot wall triggers on this repo's own
  User-Agent specifically
status: To Do
assignee: []
created_date: '2026-09-22 17:13'
labels: []
dependencies: []
ordinal: 114000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinic 16307 (AugenCentrum Rosenheim), board https://www.augencentrum.de/ueber-uns/karriere/. Every request with this repo's UA (crawlers.vendor_adapters.UA, 'Mozilla/5.0 (X11; Linux x86_64) ... Chrome/125.0.0.0 ...') gets CleanTalk's 'Anti-Crawler-Schutz' JS-cookie-challenge page (a WordPress plugin, cleantalk-spam-protect) instead of the real page. Confirmed live: swapping to a plain Windows-Chrome UA, or even bare curl's own default UA, gets the REAL page first try (no cookie dance needed) -- so this is not a genuine 'needs a browser' JS-rendering case, just a UA-specific block, and the real page has real content once past it: at least 1 nursing posting ('Pflegefachkraft (m/w/d)') plus an MFA posting. crawlers.vendor_adapters.get()/H is one shared module-level constant used by every board in this vendor family -- changing it globally risks fingerprint/rate-limit fallout across every other board, so this needs a per-domain override mechanism, not a blanket UA swap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A per-domain (or per-board) User-Agent override mechanism is added to crawlers.vendor_adapters.get() (or a narrowly-scoped equivalent), used ONLY for augencentrum.de, leaving the shared UA/H constant untouched for every other board
- [ ] #2 Verified live: crawl_wp_jobs on this board's careers_url returns the real postings (Pflegefachkraft, MFA, ...) with the override in place
- [ ] #3 A red-green test proves the override is what unblocks it (e.g. mock two responses keyed by header, or document why a live-only proof was used instead)
<!-- AC:END -->
