---
id: TASK-81
title: CV/Urkunde document-type classification (Helfer vs Fachkraft discrimination)
status: Done
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:45'
labels: []
dependencies: []
ordinal: 81000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon found the real system classifies documents into doc_type (urkunde/lebenslauf/defizitbescheid/aufenthaltstitel/dienstplan) and explicitly discriminates a genuine Pflegefachkraft-Urkunde from a lesser Pflegehelfer/-assistent certificate. Our vision/text extraction just buckets everything into cv_text/urkunde_text with no type or level awareness -- a real qualification-relevant gap, not just a nice-to-have, since a Helfer-level certificate should not read as satisfying a Fachkraft qualification path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New classification step (app/cv.py) returns a document_type in the same vocabulary the real system uses, plus a certificate_level distinguishing fachkraft/helfer/unknown for urkunde-type documents
- [x] #2 app/wa/api.py's media intake stores this classification onto the card alongside cv_text/urkunde_text
- [x] #3 The classification is surfaced into the model's context (market_snapshot or card) so a Helfer-level certificate is visibly not conflated with a Fachkraft qualification -- informational for now, not a new silent auto-reject, since the existing qualification gate's own logic was not being restructured as part of this task
- [x] #4 Unit tests cover fixture text for each doc_type and both certificate levels
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/cv.py: classify_document(text, client=None) -- a small, separate LLM call (reuses LLMClient's CLI machinery via cl._call, not a new call format) returning {document_type, certificate_level}. document_type in (urkunde, lebenslauf, defizitbescheid, aufenthaltstitel, dienstplan, other) -- same taxonomy the real reference system uses. certificate_level in (fachkraft, helfer, unknown), explicitly discriminating Pflegehelfer/Pflegefachhelfer/Pflegefachassistent (helper level, despite 'Pflegefachhelfer' containing the word Fach) from a real 3-year Fachkraft qualification. Wired into app/wa/api.py's _ingest_media right after text extraction -- stored onto the card as document_type/certificate_level. No separate prompt-plumbing needed to surface it to the model: luna_brain._user_payload already serializes the whole card, so it's visible the moment it lands; a new DOCUMENT TYPE rule in prompts.py tells the model what it means and that helfer-level must never satisfy a Fachkraft qualification_path. 12 new tests. Offline suite: 1097 passed, same 5 pre-existing unrelated failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/cv.py:classify_document() closes a real qualification-relevant gap: uploaded documents are now typed (urkunde/lebenslauf/defizitbescheid/aufenthaltstitel/dienstplan/other) and, for an urkunde, explicitly leveled (fachkraft vs helfer), matching the real reference system's own document classification. Deliberately informational only, per the task's own scope -- it does not auto-flip qualification_ok in code; it is surfaced into the model's normal context and the model is told the rule via a new prompt entry.
<!-- SECTION:FINAL_SUMMARY:END -->
