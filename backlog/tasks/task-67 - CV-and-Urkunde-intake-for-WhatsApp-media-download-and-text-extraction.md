---
id: TASK-67
title: 'CV and Urkunde intake for WhatsApp: media download and text extraction'
status: To Do
assignee: []
created_date: '2026-09-12 16:01'
labels: []
dependencies: []
ordinal: 67000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CV/Urkunde documents sent over WhatsApp are never read today -- app/wa/meta.py has no media-download capability at all, and app/wa/api.py short-circuits every document/image/audio/video message with a canned acknowledgement before either brain ever runs. Ivan asked for CV reasoning to read chat history and process Urkunde uploads. This is genuinely new infrastructure, not a small prompt change. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 5. The final choice of extraction/reasoning path for analyse_candidate depends on TASK-65 comparison result; this task can and should proceed in parallel and wire a placeholder (the existing deterministic path) until that result lands.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 app/wa/api.py:parse_message captures media id and mime type for document/image/audio/video messages instead of dropping them
- [ ] #2 app/wa/meta.py:Client gains media_url(media_id) and download_media(url) using a new binary-aware fetch path (not the existing transport= which always returns a parsed dict/text and would corrupt binary content); media URLs are fetched and used immediately, never persisted
- [ ] #3 For WA_BRAIN=luna threads only, document/image messages are downloaded and extracted (PDF/DOCX via app/cv.py:extract_text, images and near-empty scanned PDFs via a Claude vision path through the claude CLI, with a documented Anthropic SDK fallback only if the CLI genuinely cannot take image input) instead of the flat acknowledgement; the deterministic brain's existing media handling is untouched
- [ ] #4 Extracted text merges into the Luna card as cv_text/urkunde_text and the normal turn() still runs afterward so the model reacts to it in-conversation
- [ ] #5 app/cv.py gains analyse_candidate(phone, ...) that also reads chat history via app.wa.store.history() and calls whichever extraction path TASK-65 determined (or the existing deterministic path as an interim placeholder if TASK-65 has not landed yet)
- [ ] #6 Unit tests cover extraction against fixture PDF/text without network; an llm-marked test covers the image/Urkunde vision path
- [ ] #7 Full offline suite stays green
<!-- AC:END -->
