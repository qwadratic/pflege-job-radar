---
id: TASK-443
title: >-
  Clinic contact registry: public e-mail addresses with provenance, LLM
  extraction from a site mirror, scheduled recheck, block/redirect check when an
  address is chosen
status: To Do
assignee: []
created_date: '2026-10-06 11:23'
labels:
  - email
  - registry
  - provenance
dependencies: []
priority: medium
ordinal: 320000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Requested by the e-mail lane (pflege-board-25 / daria-desk), Ivan 2026-10-06, nurse-79 wave 3. Finding the Pflegedirektion address of a clinic needs LLM judgment, not only regex (TASK-324 / TASK-406 discovery). Wave 3 showed: addresses hidden behind JS unescape() or TYPO3 data-mailto-token links; the same person's address misspelled in two ads ("Jesscia.Zwosta" next to "Jessica.Zwosta"); role labels that differ per page ("Pflegedirektorin" vs "Pflegedienstleitung"); a person with a contact form and no printed address; third-party directories (DKG) that print an address the clinic's own site does not. Wanted: (1) a mirror of every clinic's own site pages that matter (Pflegedirektion, Kontakt, Impressum, Karriere, live ads), recorded with the existing tools/mirror.py store of TASK-197, so extraction runs on the mirror and a re-run needs no new crawl; (2) LLM extraction on the mirror: person, role, address, verbatim quote, URL, fetched_at, provenance class (official page, ad, official document, third party), conflicts recorded not resolved; the addresses are public; (3) provenance per address built like the board's provenance of other clinic data (see TASK-441); (4) a regular recheck; (5) the choice of the address for a letter follows Ivan's rule (Pflegedirektor(in) with a printed address first, then deputies, then the secretariat) and checks at selection time that the address is not blocked or redirected: data/email-analysis/do_not_contact.json (block entries and replace_with redirects) and the sales_brain suppression_list; (6) a clinic with no address after a second recheck is recorded as "no address", visibly, never skipped silently; (7) keep it small first; it is its own entity and API, not part of the mailer, so more can be switched on later. No caps and no silent fallbacks (CLAUDE.md). Record shape to start from: one JSON per clinic with pd / stellv / sek (name, role, email, email_kind, url, evidence), ad_contacts, conflicts, notes (the nurse-79 files wave3/contacts_w3_<clinic id>.json, git-ignored). Related: TASK-324 (website and JD discovery, clinic_contacts table), TASK-406 (external CRM), TASK-197 (mirror), TASK-345.12.9 (do_not_contact replace_with), TASK-441 (provenance catalogue).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Mirror of each clinic's own contact-relevant pages (Pflegedirektion, Kontakt, Impressum, Karriere, live ads) in the TASK-197 store; extraction re-runs on the mirror with no new crawl
- [ ] #2 Extraction record per address: person, role, address, verbatim quote, URL, fetched_at, provenance class; conflicts recorded, not resolved; public addresses only
- [ ] #3 Address choice follows the rule (Pflegedirektor(in) with printed address, then deputies, then secretariat) and is checked against do_not_contact.json and the suppression list at selection time
- [ ] #4 A clinic with no address after a second recheck is recorded as "no address" and listed; a scheduled recheck runs
- [ ] #5 Own entity and API, not part of the mailer; the first version covers the clinics of nurse-79 wave 3 before it widens
<!-- AC:END -->
