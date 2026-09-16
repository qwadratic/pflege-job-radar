---
id: TASK-107
title: Transcribe candidate voice notes so Luna answers what they said
status: To Do
assignee: []
created_date: '2026-09-14 22:15'
labels: []
dependencies: []
type: feature
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today a WhatsApp voice note (audio) is stored (TASK-95) but not read: the candidate gets the flat media reply and the thread is flagged for a human, so a campaign reply spoken as a voice note stalls. The old system transcribed candidate audio with OpenAI Whisper (apps/connectors/candidate_audio_stt.py: model whisper-1, OPENAI_API_KEY, suffix rules for audio.bin / ogg-opus). Ivan 2026-09-14: do it like the old system.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 an inbound audio message on a WA_BRAIN=luna thread is transcribed (OpenAI transcription, model configurable, key from env) from the stored original and the transcript is what Luna answers, marked as a voice note in the payload; the transcript is stored on the wa_documents row and the inbound message
- [ ] #2 failures (no key, API error, empty transcript) fail loudly and are recorded; the thread shows as stuck and catch-up retries from the stored original, no silent flat reply; /api/wa/health reports whether transcription is configured
- [ ] #3 audio sent as a document is handled the same way; video keeps the current handling
- [ ] #4 offline tests with a fake transcription client cover success, failure, retry and the payload marker; one live transcription of a synthetic German voice note succeeds when a key is configured; docs updated
<!-- AC:END -->
