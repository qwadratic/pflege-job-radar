---
id: TASK-124
title: >-
  First-touch message set for the phone rail, with the validate and render
  discipline kept
status: To Do
assignee: []
created_date: '2026-09-21 01:22'
updated_date: '2026-09-22 06:11'
labels:
  - wa-transport
dependencies:
  - TASK-120
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 132000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6, rescoped by decision-8 (2026-09-21). NOT DEFERRED.

The revision recommended deferring this on the grounds that cold outreach would stay on the Meta rail, where Graph templates work. Ivan re-confirmed on 2026-09-21 that cold first contact stays on the phone rail. On a consumer number a first contact has NO META TEMPLATE -- there is no Graph API, no approval status, no template id to resolve. So this is not a local mirror of the Graph lookup; it is the first-touch message set for the phone rail, and it is on the critical path for the first campaign rather than optional.

What must survive from the Meta path is the discipline, not the API: variable validation and rendering through the existing build_template_components and render_template, so a malformed merge field cannot reach a candidate and campaign.py never degrades to free text. Reuse M.ensure_approved (app/wa/meta.py:174-182) for the reviewed/not-reviewed gate rather than writing a second one; the status field means "a human reviewed this wording", not "Meta approved it", and the doc must say so.

Read data/wa_templates/<id>.json in the same dict shape campaign.py already consumes: {id, name, language, status, components}. Any button component renders as numbered text, because buttons are impossible on a phone rail.

Ban control that belongs in the message set rather than in the sender: no two outbound first-touch bodies are byte-identical. A set of one opening line sent 20 times a day from an unwarmed consumer number is the pattern that draws reports.

The 29 APPROVED recruitment_* templates on the Valentyn NDT number are the colleague asset, they do not move, and they stay the Meta rail set.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_template(id) resolves a local definition by id and returns the same dict shape campaign.py already consumes
- [ ] #2 campaign.py renders a local definition through the existing build_template_components and render_template with variable validation intact
- [ ] #3 A definition whose status is not reviewed is rejected by the existing ensure_approved rather than a new check, and the doc states that status means a human reviewed the wording, not that Meta approved it
- [ ] #4 A missing or malformed definition raises loudly and names the id; it never falls back to free text
- [ ] #5 Any button component in a local definition renders as numbered text on the phone rail
- [ ] #6 The first-touch set holds more than one opening variant, and a test asserts that two consecutive first touches do not produce byte-identical bodies
- [ ] #7 The Meta rail still resolves its Graph templates unchanged, proven by a test on the Meta path
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT built, exactly as the code itself says. app/wa/bridge.py:566-596 raises citing '(TASK-124)' by name from get_template/send_template when called on the bridge rail -- there is no data/wa_templates/ local store and no first-touch message set. tests/test_wa_bridge_client.py::test_send_template_without_a_definition_says_why_there_is_no_registry and ::test_get_template_refuses_instead_of_inventing_an_approved_definition both assert the refusal names TASK-124. Status and description remain accurate as written.
<!-- SECTION:NOTES:END -->
