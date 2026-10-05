---
id: TASK-197
title: >-
  Tests read a local mirror DB of the clinic sites, never a live site; a new
  page shape means re-record plus a new test
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-01 21:25'
updated_date: '2026-10-01 22:29'
labels:
  - harvester
  - adapter-testing
dependencies: []
priority: high
ordinal: 194000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01: tests hit real clinic sites. They must read a local database of mirrors of the clinic sites, and every new case that appears on new pages is handled by re-mirroring and covering it with a new test. Today tests/test_adapter_completeness.py parametrises 2,124 network-marked cases over the live registry and fetches live boards (its module level also calls the registry proxy at collection, so even -m 'not network' touches the network); further network marks sit in test_completeness_beesite_hr4you/dvinci/helix/smartrecruiters, test_verify_pi_loga_live, test_geo, test_autopilot. crawl_snapshots/ (git-ignored, 1.6 GB, 336 hosts, 34,375 pages, 14k of them JS bundles) is a write-only side effect of tests.adapter_contract.save(): oracle pages, not the adapter's request sequence, so it cannot be replayed. tests/fixtures/board_samples are hand-cut redacted slices and stay. This replaces the live-board-as-oracle part of TASK-26's method (2026-09-10): the oracle stays what the board's own client reads and declares, read from a frozen recording that is refreshed on purpose. Related: TASK-24 (browsable mirror, link graph), TASK-26, TASK-130. The mirror holds third-party pages with HR names and contacts, so it is local and git-ignored (the repo is public).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A mirror store under data/mirror/ (git-ignored) holds, per board, every response (hop by hop incl. redirects, status, headers, body, fetched_at) that the production crawl path and the completeness oracle fetches make; tools/mirror.py has record, add, diff, status
- [ ] #2 A replay layer serves requests, urllib and Playwright from the mirror; a request the mirror lacks raises MirrorMiss naming board, URL and the re-record command; nothing falls back to the live site
- [ ] #3 A conftest guard makes any non-local socket or DNS use in any test fail with the host; running pytest with no arguments produces zero guard hits
- [ ] #4 tests/test_adapter_completeness.py (5 checks and the mutation meta-tests) runs over the mirror index with no network at collection or run; case count and run time reported before and after
- [ ] #5 Every other network-marked test is mirror-backed or deleted with a reason; the network marker is gone; boards that cannot be replayed (if any) are named, listed, and visible as explicit xfails
- [ ] #6 The rule is written down in CLAUDE.md: new page shape or board => tools/mirror.py record or add => red test on the mirror => fix => green; plus the proposed backlog Definition-of-Done line
- [ ] #7 A worked example from the AMEOS or Sana Oracle fix shows a test red on the old adapter and green on the new one, against a freshly recorded mirror
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Scan: run the whole suite with a socket+DNS guard, list every test that reaches the network (marked and unmarked). 2. Mirror store (data/mirror/<board_id>.sqlite, index) and tools/mirror.py record/add/diff/status. 3. Replay layer tests/mirror.py (requests, urllib, Playwright, sleep) with MirrorMiss, no fallback. 4. conftest guard for every test. 5. Convert tests/test_adapter_completeness.py and the other network-marked files to the mirror. 6. First fill: record every registry board, report size, failures, gaps. 7. Rule in CLAUDE.md, adapter_contract docstring, fixtures README; propose DoD line. 8. Worked example: AMEOS or Sana Oracle red then green. Agent E in worktree builds 2-8, brief in the job dir (briefs/E_mirror.md).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-01 scan (no .env, socket+DNS guard plugin, code of worktree-integration 7b35091, pytest -m 'not network', 17 min 40 s): 13 failed, 1754 passed, 21 skipped, 3 deselected, 2 collection errors. 10 guard hits, none on a clinic site: 5 DNS lookups of the registry read proxy supabase.int.exe.xyz at collection (module-level code of network-test modules), 5 lookups of the Supabase project REST host from tests/test_clinic_photos.py (5 tests that pass but reach our own DB host: separate finding). tests/test_completeness_dvinci.py fetches a live board at import (URLError at collection under the guard). tests/test_adapter_completeness.py skipped itself at module level because the proxy was unreachable; with the network up it parametrises 2,124 live cases at collection. The 13 failures are the known baseline (PFLEGE_INGEST_URL x5 incl. cli_inbox_drain 2, FIRECRAWL_API_KEY x3, missing sqlite tables x2, two Playwright timing flakes, one assert None) and unrelated to the network. Scan tooling and result: job dir tmp/nonet (nonet_plugin.py, pt.py, SCAN_RESULT.md). Agent E (worktree) builds the mirror, replay layer, guard and conversion; brief tmp/briefs/E_mirror.md.

Agent E, 2026-10-01 (branch worktree-agent-aba79e1b41c5c3b98, commits bc0ae5d..a51b16a): built so far -- tests/mirror.py (store: one sqlite per board, xz as a whole because pages of one board are near-copies: AMEOS 860 pages 14.2 MB zlib-per-blob vs 0.36 MB solid xz; replay of requests via HTTPAdapter.send, urllib via AbstractHTTPHandler.do_open, Playwright via a context route, time.sleep; MirrorMiss names board, URL and the re-record command and also fails the run when an adapter swallows it), tools/mirror.py (record/add/diff/status/list-urls/show/sql/reindex/record-infra; every recording is replayed from memory and compared with the live run before it is saved), tests/adapter_harness.py (the five checks and the mutation machinery, shared by the tests and the recorder), conftest.py network guard (found tests/test_clinic_photos.py: 5 route tests read the real Supabase REST host, now stubbed), test_adapter_completeness and the 6 other network-marked files read the mirror, network marker removed. Mirror root: MIRROR_ROOT or <main checkout>/data/mirror, shared by every worktree (git-ignored). Measured: Playwright replay of the Klinikum Ingolstadt P&I board is identical once analytics beacons (Matomo POST with a random id and clock in the URL) are answered 204 and not stored; first fill in progress.
<!-- SECTION:NOTES:END -->
