---
id: TASK-436
title: >-
  Status documents at public token links: harness route and publish tool, board
  proxies /s/<token>/
status: To Do
assignee:
  - wa-harness
created_date: '2026-10-06 08:09'
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
- [ ] #1 The harness route serves index.html, detail.html and PDFs for a known token, behind the board/write bearer. Unknown tokens, bad names and symlinks return 404. Responses carry noindex and no-referrer. Offline tests cover all of this
- [ ] #2 The publish tool mints and reuses tokens, keeps the slug -> token map outside the repository with mode 600, replaces a document atomically, refuses unexpected files loudly, and prints the public URL. Offline tests cover all of this
- [ ] #3 docs/wa-dashboard.md states the board-side /s/ proxy contract for pflege-fe
- [ ] #4 The wave-3 booklet is published and its public URL answers 200 through the board
<!-- AC:END -->
