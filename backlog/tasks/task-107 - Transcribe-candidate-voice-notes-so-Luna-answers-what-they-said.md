---
id: TASK-107
title: Transcribe candidate voice notes so Luna answers what they said
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 22:15'
updated_date: '2026-09-16 14:36'
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
- [x] #1 an inbound audio message on a WA_BRAIN=luna thread is transcribed (OpenAI transcription, model configurable, key from env) from the stored original and the transcript is what Luna answers, marked as a voice note in the payload; the transcript is stored on the wa_documents row and the inbound message
- [x] #2 failures (no key, API error, empty transcript) fail loudly and are recorded; the thread shows as stuck and catch-up retries from the stored original, no silent flat reply; /api/wa/health reports whether transcription is configured
- [x] #3 audio sent as a document is handled the same way; video keeps the current handling
- [x] #4 offline tests with a fake transcription client cover success, failure, retry and the payload marker; one live transcription of a synthetic German voice note succeeds when a key is configured; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. config.py: OPENAI_API_KEY, WA_STT_MODEL (default whisper-1, old-system parity), WA_STT_TIMEOUT_SEC; readiness() reports checks.openai_api_key, stt_ready, stt_model (GET /api/wa/health).
2. New app/wa/stt.py: Client(transport=, api_key=, model=) -> transcribe(blob, filename, mime_type) -> {text, model, upload_name}. multipart POST /v1/audio/transcriptions over urllib (no openai SDK, same injectable-transport seam as meta.py). Upload suffix = old candidate_audio_stt.resolve_stt_suffix rules. No key, HTTP/network error, empty transcript -> TranscriptionError (loud).
3. store.py: one commit writes the transcript to wa_documents.text (text_key voice_transcript) and to the inbound wa_messages.meta (transcript, transcript_model, transcribed_at).
4. api.py finish_inbound/_store_and_read: WA_BRAIN=luna audio, and kind=document with an audio/* mime, is transcribed under the media:<wamid> claim from the bytes just stored or the stored original (sha256 checked); card untouched (no documents entry, no _unread_media); the transcript is the turn text. A stored transcript is reused on retry (no second API call). Failure raises -> TASK-99 path: pending row last_error, wa_send_failures, no MEDIA_REPLY, catch-up retries from the stored original. Video and the deterministic brain keep the flat ack.
5. luna_brain.py: turn_context/payload voice_note marker; latest_inbound = transcript; locked region text applies to a transcript like typed text (STOP already does). shadow_run uses a stored transcript.
6. prompts.py: VOICE NOTE rule (answer the content, transcript may mishear names/towns/numbers: ask back instead of recording a guess); _unread_media wording = videos (and voice notes from before TASK-107).
7. Tests: stt client (request shape, suffix rules, errors), luna audio success, audio-as-document, video unchanged, deterministic unchanged, failures (no key/API error/empty) recorded + stuck + catch-up retry from stored original, brain failure reuses transcript, payload marker, health; update existing luna voice-note tests (declined, campaign status, follow-ups) to video or transcription. llm persona: voice-note reply to the campaign template (fake STT). network: live synthetic German voice note (espeak-ng+ffmpeg), skipped without OPENAI_API_KEY.
8. Docs: whatsapp.md (voice notes section, documents table, recovery, env, health, prompt), rollout-runbook env. Offline suite once at the end.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (offline): app/wa/stt.py (urllib multipart client, injectable transport, old-system suffix rules, TranscriptionError on no key/HTTP/network/no text/empty), config OPENAI_API_KEY/WA_STT_MODEL/WA_STT_TIMEOUT_SEC + health stt_ready/stt_model/checks.openai_api_key, store.set_voice_transcript (wa_documents.text + text_key voice_transcript and wa_messages.meta transcript/transcript_model/transcribed_at, one commit), api._store_and_read transcribes luna audio and audio/* documents under media:<wamid> (stored transcript reused on retry), finish_inbound passes the transcript as turn text, luna payload voice_note, region shortcut on transcripts, shadow_run uses the stored transcript, prompt VOICE NOTE rule. Live DB read-only check (counts only, mode=ro, sha256 unchanged): no audio messages or pending media at all, so the catch-up timer running this tree has nothing to retry. tests/test_wa_voice_notes.py 28 passed; existing luna voice-note tests moved to video (unread media) plus transcribed voice-note variants; all tests/test_wa_*.py offline 669 passed.

Follow-up change: set_voice_transcript also writes wa_messages.body = transcript (was empty), as the old system did (body = COALESCE(body, transcript)), so thread history (GET /api/wa/threads?phone=), the consent CV analysis chat history, campaign --status last_text, replies_to and shadow_run see what was said; meta.transcript marks it (payload voice_note). shadow_run change reverted (body covers it).
Model research (OpenAI docs 2026-09-15): gpt-transcribe (Jul 2026, docs' recommended general model, $0.0045/min), gpt-4o-transcribe/-mini (model page: lower WER than original Whisper), whisper-1 $0.006/min; no German figure found. Default kept whisper-1 (old-system parity); WA_STT_MODEL switches, same request shape.
Mutation checks (throwaway plugin, deleted): transcript not used as turn text 9 failing tests; no transcript reuse 1; audio documents not detected 1; payload marker off 10; meta not written 10; audio not in read kinds 10.
llm (claude-sonnet-5, fake STT transport, one test id at a time): campaign voice-note reply [1] pass, [2] run twice (one pass, one transcript-only capture: correct reply); misheard 'Augsbuch' [1] pass, [2] 4 runs: 3 pass, 1 failed with an exception inside a turn before the voice-note reply (log discarded before reading, cause unknown).
Live transcription: skipped, no OPENAI_API_KEY in env or .env; tests/test_wa_stt_live.py (network) generates espeak-ng German speech -> ffmpeg ogg/opus (checked locally) and skips without the key; its body passed once with a fake transport.
Full offline suite (single process): 1547 passed, 126 skipped, 66 deselected, 141 s.

Final verification 2026-09-15 (~01:20 UTC, after review + adversarial verify + fixer): offline suite 1571 passed, 126 skipped, 0 failed. Live llm, one at a time: campaign full funnel 3/3 non-empty shortlist, flexible funnel 1/1 (5 clinics), imported opt-out silence then re-engagement 1/1, voice-note reply from transcript 1/1 (fake STT), misheard town asked back 1/1. Follow-up fix 01:30 UTC: the unmatched-department ToolError no longer carries candidate-facing English (a run had copied it into a bubble and broken JSON); tests/test_wa_luna_tools.py 15 passed, Urologie funnel llm 2/2. Not yet deployed: pflege-wa.service restart pending Ivan's go. AC4 live part open: OPENAI_API_KEY is not in .env yet; tests/test_wa_stt_live.py (espeak-ng + ffmpeg synthetic German voice note) skips. Until the key exists and pflege-wa is restarted on this tree, Luna voice notes stay pending (recorded, no reply) and catch-up answers them once the key is present.

Live transcription verified 2026-09-16 14:20-14:25 UTC after Ivan added OPENAI_API_KEY and restarted pflege-wa (health stt_ready=true, stt_model=whisper-1). tests/test_wa_stt_live.py (espeak-ng speech -> ffmpeg ogg/opus, webhook path, fake Meta, fake brain, real OpenAI endpoint) passed with the service EnvironmentFile: whisper-1 returned 'Hallo, ich bin Pflegefachfrau und möchte gern in München arbeiten.' verbatim in 2.9s for 13657 bytes. Model comparison on the same sample: gpt-4o-transcribe 0.8s and gpt-4o-mini-transcribe 1.3s, both with identical text. Default left at whisper-1 (old-system parity); switching needs only WA_STT_MODEL in the EnvironmentFile plus a restart. One clean synthetic sample is not evidence about noisy real voice notes -- flagged to Ivan.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
WhatsApp voice notes are transcribed (OpenAI, whisper-1 by default, WA_STT_MODEL) from the stored original inside the background worker, and Luna answers what was said instead of the flat media reply; failures are loud, recorded and retried by catch-up from the stored file, and /api/wa/health reports stt_ready. Verified by offline tests with a fake transport (success, three failure kinds, catch-up retry, payload marker), live llm runs (voice-note reply, misheard town asked back) and a live OpenAI transcription of a synthetic German voice note through the webhook path.
<!-- SECTION:FINAL_SUMMARY:END -->
