---
id: TASK-436
title: >-
  Status documents at public token links: harness route and publish tool, board
  proxies /s/<token>/
status: In Progress
assignee:
  - wa-harness
created_date: '2026-10-06 08:09'
updated_date: '2026-10-06 09:16'
labels:
  - whatsapp
  - hosting
  - email
dependencies: []
priority: medium
ordinal: 312000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Candidate status documents (the TASK-434 one-pager, detail.html, PDF; first the wave-3 booklet from PR #9) need a public link with an unguessable token. Ivan agreed on 2026-10-06. Nobody has a sudo password on tasker-dispatcher-01, so the harness serves them through the existing nginx /api/wa/pro/ location instead, which is reachable only from the board VM. The harness adds GET /api/wa/pro/status/{token}/[name] behind the board bearer, serving files from a claude-owned directory outside the repository (the token never goes into the public repo). tools/status_docs_publish.py <slug> <dir> mints or reuses the token and prints the URL; Daria runs it locally on the same host, so no upload API and no new key. The board (pflege-fe) adds a public /s/{token}/ route that proxies server-side with WA_API_TOKEN, so the public URL is https://pflege-board.exe.xyz/s/<token>/. Requested by the WhatsApp lane (wa-harness).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The harness route serves index.html, detail.html and PDFs for a known token, behind the board/write bearer. Unknown tokens, bad names and symlinks return 404. Responses carry noindex and no-referrer. Offline tests cover all of this
- [x] #2 The publish tool mints and reuses tokens, keeps the slug -> token map outside the repository with mode 600, replaces a document atomically, refuses unexpected files loudly, and prints the public URL. Offline tests cover all of this
- [x] #3 docs/wa-dashboard.md states the board-side /s/ proxy contract for pflege-fe
- [ ] #4 The wave-3 booklet is published and its public URL answers 200 through the board
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Harness half (this branch): route GET /api/wa/pro/status/{token}[/][name] behind the board bearer; publish tool tools/status_docs_publish.py; offline tests; contract in docs/wa-dashboard.md. Board half: pflege-fe, PR #11 (public /s/{token}/ proxy). Then: merge both, deploy the harness (pflege-wa restart) and the board, publish the wave-3 booklet, curl the public URL.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
No sudo on tasker-dispatcher-01, so no new nginx location: the harness serves through the existing ki-workflow.agency /api/wa/pro/ location (allow-listed to the board VM, bearer-checked) and the board proxies /s/ to it. Documents and tokens.tsv live in ~/.local/state/pflege-status/ (outside the repository; pflege-fe relayed Ivan's rule that candidate booklets never go into the public repo). Sonnet build, one Opus review POSITIVE; its findings applied once (catch-all route behind auth: no slash 307 and identical 404s; on-disk regression tests; 401-before-filesystem tests; token-map validation; copy exactly the checked entries; docstring/contract wording). Mutation check: removing TOKEN_RE, allowed_name, the PDF case rule, auth-before-serve, the tool's symlink check or the catch-all each turns tests red. Offline: tests/test_wa_status_docs.py + test_wa_pro_api.py 134 passed; WA lane 2832 passed; pre-commit passed. Board-side contract now points to docs/auth.md (pflege-fe's request).

Open: AC 4. Re-check by wa-harness after both PRs are merged and deployed: publish the staged wave-3 booklet (~/.local/state/pflege-status/build/2026-10-06-wave3-booklet) with the tool, curl https://pflege-board.exe.xyz/s/<token>/ for 200, give pflege-fe a synthetic token for its end-to-end check, send Ivan the link. pflege-fe asks Ivan to confirm the public board route before merging PR #11.
<!-- SECTION:NOTES:END -->
