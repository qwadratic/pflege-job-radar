---
id: TASK-67
title: 'CV and Urkunde intake for WhatsApp: media download and text extraction'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 16:01'
updated_date: '2026-09-12 17:27'
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
- [x] #1 app/wa/api.py:parse_message captures media id and mime type for document/image/audio/video messages instead of dropping them
- [x] #2 app/wa/meta.py:Client gains media_url(media_id) and download_media(url) using a new binary-aware fetch path (not the existing transport= which always returns a parsed dict/text and would corrupt binary content); media URLs are fetched and used immediately, never persisted
- [x] #3 For WA_BRAIN=luna threads only, document/image messages are downloaded and extracted (PDF/DOCX via app/cv.py:extract_text, images and near-empty scanned PDFs via a Claude vision path through the claude CLI, with a documented Anthropic SDK fallback only if the CLI genuinely cannot take image input) instead of the flat acknowledgement; the deterministic brain's existing media handling is untouched
- [x] #4 Extracted text merges into the Luna card as cv_text/urkunde_text and the normal turn() still runs afterward so the model reacts to it in-conversation
- [x] #5 app/cv.py gains analyse_candidate(phone, ...) that also reads chat history via app.wa.store.history() and calls whichever extraction path TASK-65 determined (or the existing deterministic path as an interim placeholder if TASK-65 has not landed yet)
- [x] #6 Unit tests cover extraction against fixture PDF/text without network; an llm-marked test covers the image/Urkunde vision path
- [x] #7 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Spike (done): confirmed `claude -p` CAN read a local image/scanned-PDF via its own built-in
   Read tool, when invoked with `--restricted` (not `--tools ""`, which disables Read too and was
   confirmed to fail) plus `--add-dir <single-file temp dir>` (Read is confined to cwd + added
   dirs). CLI-first path is viable; no Anthropic SDK fallback needed.
2. app/wa/api.py:parse_message -- capture media id/mime_type/filename for document/image/audio/
   video from Meta's `{"<type>": {"id":..., "mime_type":..., "filename":...}}` field.
3. app/wa/meta.py:Client -- add media_url(media_id) (reuses the existing JSON transport=) and
   download_media(url) (new binary-aware transport seam, Bearer token on both calls, never
   persists the CDN url).
4. app/cv.py -- add extract_text_vision()/VisionClient (claude -p --restricted --add-dir <tmp>,
   raises loudly on NO_TEXT_FOUND or any bad response) and analyse_candidate(phone, conn, cv_text,
   urkunde_text, ...) that folds app.wa.store.history() in and calls analyse_llm (TASK-65's
   measured winner) -- not a placeholder, TASK-65 has landed.
5. app/wa/api.py -- for WA_BRAIN=luna threads only, document/image messages: download via
   meta.Client, dispatch to extract_text (document, cv_text) or extract_text_vision (image, or a
   near-empty-text PDF falling through the same as an image -- both land in urkunde_text), merge
   into the thread's card, then still call LB.turn() normally. Deterministic brain's flat
   MEDIA_REPLY ack (and luna's own ack for audio/video, out of scope for extraction) untouched.
6. app/wa/luna/prompts.py -- one additive RULES line telling the model how to react to a new
   cv_text/urkunde_text on the card (VENDORED.md already flags "CV/document OCR ingestion and the
   rules that react to it" as dropped-until-now); update VENDORED.md's table accordingly.
7. Tests: meta media download (fake binary transport), api.py parse_message + luna media-turn
   wiring (fake meta client + fake LB client, tiny real PDF/text bytes, no network), cv.py vision
   extraction + analyse_candidate (fake VisionClient/LLMClient, fixture PDF bytes, fixture wa.db
   history) -- all offline. One llm-marked test spawns the real CLI against a tiny real image.
8. Run full offline suite (`pytest -q -m "not network and not completeness and not mutation and
   not llm"`), then the new llm-marked test(s) for real, then finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLI-image-input spike (done first, per plan): confirmed `claude -p` CAN read a local image/PDF via
its own built-in Read tool. Two non-obvious findings:
1. `--tools ""` (LLMClient/luna_brain.Client's pattern) also disables Read -- with it, the model
   answered "I don't have a tool available to read local files in this session". This path must use
   `--restricted` alone (drops command/code-exec/WebFetch, keeps Read/Glob/Grep).
2. Even with Read available, it is confined to the CLI's cwd + `--add-dir` grants -- a path outside
   both was refused (permission_denials: Read). Fix: write the downloaded bytes to a throwaway,
   single-file temp dir and `--add-dir` exactly that dir (never a broader one).
Verified live: a real PNG with rendered text ("HELLO SPIKE 12345") -> read correctly; the same
image re-saved as a one-page PDF with no text layer (scanned-Urkunde stand-in) -> read correctly;
a blank image -> correctly produced the NO_TEXT_FOUND sentinel. Verdict: CLI-first path is viable,
used as the sole implementation (app/cv.py:VisionClient/extract_text_vision) -- the Anthropic SDK
multimodal fallback the plan allows for was not needed and is not implemented.

Implementation:
- app/wa/api.py:parse_message captures media_id/media_mime_type/media_filename for
  document/image/audio/video (Meta's `{"<type>": {"id","mime_type","filename"}}` field).
- app/wa/meta.py:Client gains media_url(media_id) (reuses the existing JSON transport=) and
  download_media(url) (new media_transport= seam, raw bytes, never JSON-parsed/decoded -- would
  corrupt binary content). Both require the bearer token; the CDN url from media_url() is used
  immediately by the caller and never persisted.
- app/wa/api.py: for WA_BRAIN=luna threads only, document/image go through a new _ingest_media ->
  _extract_media_text dispatch: PDF/DOCX/text via CV.extract_text (-> card key cv_text); a bare
  image, or a PDF whose extract_text result is <20 chars (the same "no readable text" threshold
  CV.analyse()/analyse_llm() already use), falls through to CV.extract_text_vision (-> card key
  urkunde_text -- a photographed/scanned upload is far more often the Urkunde than the CV). The
  extracted text is merged onto t["slots"] (the Luna card) BEFORE LB.turn() runs, so Valentina
  reacts to it in the same turn. audio/video, and every kind on the deterministic brain, are
  completely untouched (still the flat MEDIA_REPLY ack) -- verified by dedicated regression tests.
  Vision failures (NO_TEXT_FOUND / empty reply) raise RuntimeError and propagate uncaught, same as
  any other turn-processing failure (CLAUDE.md, "no invented safety nets").
- app/cv.py gains extract_text_vision()/VisionClient (claude -p --restricted --add-dir <tmp>, same
  subprocess-result validation pattern as LLMClient) and analyse_candidate(phone, conn, cv_text,
  urkunde_text, ...) -- folds app.wa.store.history(conn, phone) in as chat_history alongside the
  cv_text/urkunde_text (joined with "--- CV ---"/"--- Urkunde ---" markers), then calls
  analyse_llm() (TASK-65's measured winner -- not a placeholder, TASK-65 already landed on this
  branch). The public /api/cv route (app/main.py) still calls analyse()/analyse_llm() directly and
  is untouched (asserted by a dedicated test).
- app/wa/luna/prompts.py: one additive RULES line telling the model how to react to a new
  cv_text/urkunde_text on the card (thank them, extract into card_patch, treat garbled text like
  UNREADABLE MEDIA) -- VENDORED.md's table previously listed "CV/document OCR ingestion and the
  rules that react to it" under "dropped entirely"; updated to note TASK-67 reintroduces it as new,
  not-ported-from-source infrastructure.

Tests (all new, offline unless marked):
- tests/test_wa_harness.py: parse_message media id/mime/filename capture; meta.Client media_url/
  download_media (happy path, missing url, missing token, and that download_media returns raw
  bytes byte-for-byte, never parsed).
- tests/test_cv_intake.py: a small hand-built valid PDF fixture (pdf_bytes_with_text, no PDF-writer
  library on this host) reused by test_wa_media_intake.py; extract_text_vision happy path + temp-file
  cleanup + NO_TEXT_FOUND/empty-reply failures; a regression test asserting VisionClient's argv never
  contains `--tools` and does contain `--restricted`/`--add-dir` (guards the spike finding); 
  analyse_candidate folding cv_text+urkunde_text+chat history, single-field cases, no-history case,
  ValueError with no text, and a signature-based check that analyse()/analyse_llm() are untouched.
  Plus one llm-marked real test (renders a real PNG with PIL, runs the real `claude` CLI) -- run and
  passed: `pytest -q -m llm tests/test_cv_intake.py` (1 passed).
- tests/test_wa_media_intake.py: _extract_media_text/_suffix_for dispatch unit tests; end-to-end
  handle_payload flows for WA_BRAIN=luna (document-with-real-text -> cv_text + LB.turn() still
  replies; image -> vision -> urkunde_text; scanned/near-empty PDF -> vision fallback -> urkunde_text;
  vision failure propagates, inbound recorded but no reply claimed; audio/video still flat-acked)
  and two explicit deterministic-brain regression tests (document/image still get the flat ack,
  zero media_url/download_media calls).

Verification:
- Full offline suite: `PFLEGE_TESTS_OFFLINE=1 pytest -q -m "not network and not completeness and
  not mutation and not llm"` -> 970 passed, 127 skipped, 15 deselected, 6 failed. All 6 failures are
  pre-existing and unrelated to this task (none touch app/wa/* or app/cv.py, none of their files were
  touched by this change): live-Supabase 401s in test_app_api.py/test_auth.py (no network egress in
  this sandbox), 3 pre-existing failures in tests/test_career_crawl_section.py (crawler section-first
  logic, unrelated module), and a missing local data/app.sqlite table in test_ontology.py.
- New tests in isolation: `pytest -q -m "not llm" tests/test_cv_intake.py tests/test_wa_media_intake.py
  tests/test_wa_harness.py` -> all green (19 + 46 incl. new cases, no failures).
- llm-marked test added by this task, run for real: `pytest -q -m llm tests/test_cv_intake.py` -> 1
  passed (real `claude` CLI, real PNG). Also spot-checked one existing real persona test
  (tests/test_wa_luna_personas.py::test_maria_verified_urkunde_reaches_a_city_and_department_without_a_reject)
  after the prompts.py edit -- still passes.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Built CV/Urkunde intake for the WhatsApp harness: app/wa/api.py:parse_message now captures
media_id/mime_type/filename; app/wa/meta.py:Client gained media_url()/download_media() (a new
binary-safe transport, bearer-token-gated, CDN url never persisted); for WA_BRAIN=luna threads
only, document/image messages are downloaded and extracted (PDF/DOCX/text via CV.extract_text ->
cv_text; images and near-empty/scanned PDFs via a new claude-CLI vision path,
CV.extract_text_vision/VisionClient -> urkunde_text) and merged onto the Luna card before LB.turn()
runs, so Valentina reacts in-conversation; the deterministic brain and audio/video are completely
untouched (flat MEDIA_REPLY ack, regression-tested). app/cv.py gained analyse_candidate(phone,
conn, cv_text, urkunde_text, ...), folding app.wa.store history in and calling analyse_llm()
(TASK-65's measured winner, already landed -- not a placeholder); the public /api/cv route is
untouched. A CLI-image-input spike (done first) confirmed `claude -p --restricted --add-dir <dir>`
(not `--tools ""`, which disables the Read tool the vision path depends on) can read a real
image/scanned PDF via its own Read tool, so the CLI-first path was used with no Anthropic SDK
fallback needed. Vision/extraction failures raise loudly, never silently swallowed.

Verified: 65 new offline tests (tests/test_cv_intake.py, tests/test_wa_media_intake.py, additions
to tests/test_wa_harness.py) all green; full offline suite
(`PFLEGE_TESTS_OFFLINE=1 pytest -q -m "not network and not completeness and not mutation and not
llm"`) at 970 passed/127 skipped/15 deselected with 6 pre-existing failures unrelated to this task
(live-Supabase network 401s, unrelated crawler-section tests, a missing local sqlite table -- none
touch app/wa/* or app/cv.py, none of their files were changed here); the new llm-marked test
(real `claude` CLI against a real PNG) passed, and one existing real persona test was spot-checked
against the prompts.py change and still passes.
<!-- SECTION:FINAL_SUMMARY:END -->
