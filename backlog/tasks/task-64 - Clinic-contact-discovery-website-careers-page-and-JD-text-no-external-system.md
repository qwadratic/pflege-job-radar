---
id: TASK-64
title: 'Clinic contact discovery: website/careers page and JD text, no external system'
status: To Do
assignee: []
created_date: '2026-09-12 16:00'
labels: []
dependencies: []
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked whether clinic Pflegedirektion/HR emails are available and, when the board own enr_contact_emails column is empty for a clinic, to look for a contact on the clinic own website or in the job ad text -- a previously-assumed "sales brain" data source does not exist anywhere in this repo or in prior session notes, and Ivan confirmed to skip it and use best-effort discovery instead. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New app/wa/luna/contacts.py:discover_contact(clinic, postings) tries enr_contact_emails first (confirmed available unredacted in-process, not just at the HTTP boundary), then a single polite fetch of the clinic website/careers_url with regex extraction near Pflegedirektion/Personalabteilung/Bewerbung/Karriere/HR context, then a second JD description text pass
- [ ] #2 Results are stored in a new clinic_contacts(clinic_id, email, source, confidence, discovered_at) table in the same sqlite file as app/wa/store.py, populated by a batch entry point (python -m app.wa.luna.discover_contacts) rather than live per WhatsApp turn
- [ ] #3 Unit tests cover the extraction heuristic against fixture HTML with no live network call in the default run; a network-marked test exists for a real sanity check, deselected by default like the repo existing network marker
- [ ] #4 Full offline suite stays green
<!-- AC:END -->
