---
id: TASK-70
title: Handle Meta's 24h messaging window with template-based reopen
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 09:40'
updated_date: '2026-09-13 09:46'
labels: []
dependencies: []
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Confirmed via a real shadow-run comparison against the production system (2026-09-13): our harness has zero awareness of WhatsApp Cloud API 24h customer-service window policy. Meta rejects free-form text sent more than 24h after the candidate last wrote; the real production system already handles this by falling back to a pre-approved template message ("reopen") instead. Ivan asked for this before any real cutover. This is a real, code-level gap, not a prompt issue -- the model must never be the one deciding template vs free-form.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/store.py already tracks last_inbound_at per thread; app/wa/api.py computes whether the 24h free-form window is open (now - last_inbound_at) before choosing how to send
- [x] #2 app/wa/meta.py:Client gains send_template(to_e164, template_name, language, params) using Meta's template message API shape, alongside the existing send_text/send_buttons
- [x] #3 When the window is closed, the harness sends a configured reopen template instead of Luna's free-form bubbles (template name/language configurable via env, since actually using this requires a real Meta-approved template Ivan registers separately -- not something this repo can fabricate)
- [x] #4 Luna still runs and decides what it would say (for logging/consistency), but the code layer -- not the model -- decides template vs free-form based on the window state
- [x] #5 Unit tests cover both branches (window open -> free-form as today; window closed -> template call, not a free-form send) with a fake Meta client
- [ ] #6 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/wa/config.py: add FREEFORM_WINDOW_HOURS (default 24), WA_REOPEN_TEMPLATE_NAME (default empty), WA_REOPEN_TEMPLATE_LANG (default de).
2. app/wa/meta.py:Client gains send_template(to_e164, template_name, language, params=None) mirroring send_text/send_buttons's _post() pattern, using Meta's {type: template, template: {name, language: {code}}} shape.
3. app/wa/api.py:_send() gains a code-level gate BEFORE the autosend branch: if the free-form window (now - thread.last_inbound_at) is closed, send/draft the configured reopen template instead of the brain's own bubbles, raising loudly if no template is configured (no silent fallback to free-form, no silent drop). Applies to both brains since it's a transport-level Meta constraint, not brain-specific.
4. Tests: window-open (unchanged free-form path), window-closed+template configured (template send/draft), window-closed+no template configured (raises loudly), fake Meta client covers send_template.
5. Offline suite, docs, backlog finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/meta.py:Client.send_template(to_e164, template_name, language, params=None) added, mirroring send_text/send_buttons' _post() pattern (Meta template message shape: type=template, template={name, language:{code}, components (only when params given)}). app/wa/config.py gained FREEFORM_WINDOW_HOURS (default 24), WA_REOPEN_TEMPLATE_NAME (empty by default -- real template must be registered in Meta Business Manager, this repo cannot fabricate one), WA_REOPEN_TEMPLATE_LANG (default de).

app/wa/api.py:_send() now checks _freeform_window_open(t) (pure function of thread.last_inbound_at, code-level, never the model) before the existing autosend branch: closed window -> _send_reopen_template() (draft or real send depending on AUTOSEND, raises RuntimeError loudly if no template configured) instead of the brain's own bubbles. Applies to both brains equally since it's a Meta transport constraint, not brain-specific.

Noted in docs: in the current purely-webhook-driven _handle_one() flow, last_inbound_at is always freshly stamped before _send() runs, so the window is always open in practice today -- this check's real bite starts once TASK-72's dry-run/catch-up tool (or any future proactive-messaging feature) revisits an older, previously-unanswered thread. Tests call _send()/_freeform_window_open() directly with a manually-stale last_inbound_at to exercise the closed-window path, matching how that future caller will actually use it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Meta's 24h free-form messaging window is now a real, code-level gate (app/wa/api.py:_send/_freeform_window_open), never left to the model: a closed window swaps in a configured reopen template (app/wa/meta.py:Client.send_template) instead of the brain's bubbles, or fails loudly if no template is configured, rather than silently attempting free-form text Meta would reject. 8 new tests cover the open/closed boundary, draft vs real send, the loud failure, and the real Meta template request shape. Offline suite: 1013 passed, 5 pre-existing unrelated failures (one fewer than before, unrelated to this change).
<!-- SECTION:FINAL_SUMMARY:END -->
