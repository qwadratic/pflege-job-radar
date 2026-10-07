---
id: TASK-345.12.16
title: >-
  Terms reply: answer a clinic's request for conditions with the standard letter
  and a per-clinic PDF, sent immediately by a one-off sender
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-07 09:16'
updated_date: '2026-10-07 10:12'
labels:
  - mailer
  - daria
  - nurse-79
  - classifier
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 325000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When a clinic answers a mailing and asks for conditions or price ("Konditionen", "Kosten", "Preis", "Honorar"; the classifier already labels this pattern terms_request, TASK-345.12.9), Daria answers in the same thread (reply to the clinic's message, subject "AW: <their subject>") with a short template letter and a PDF "<prefix>_<clinic>.pdf" (the prefix is a key of the terms config block, kept out of the repo) made for that clinic (clinic name and addressee on it). Standard terms since 2026-10-06: 4,990 EUR net per hired nurse, 50 % at signing of the employment contract, 50 % after the third month, no refund; the e-mail confirmation of the clinic is the order. The full profile with the name goes out only after the clinic's written yes (not part of this task). The old placement contract is no longer used for new answers. Operator decision (Ivan, 2026-10-07): the first such letter is prepared and sent by hand inside a session, then fixed as the template; after that the classifier may send it by itself, without a plan, an announcement or an approval per letter.

Needed: (1) an "immediate" mode of the mailer: no plan, no announcement, no send window or odd-minute wait, one-off sender that sends and exits; it keeps the checks that guard recipients (suppression and do-not-contact lists, bounce/stop handling) and records the letter in the ledger as its own event kind, not as a cadence step (a "sent" event with a step outside the cadence breaks next_due and step_name); (2) the letter template and an HTML template for the PDF rendered with the repo's Playwright (.venv only); (3) the thread headers: In-Reply-To = the clinic's message, References = the whole chain; (4) the classifier path: pattern terms_request triggers the letter once per clinic and thread, tells both operators in the digest, and does not trigger again after a reply; (5) tests on the mirror/fixtures, never a live site.

Requested by the mailer lane (pflege-board-25) through pflege-clawl, 2026-10-07. No person or clinic names in this task (public repo).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A terms_request answer in a fixture produces exactly one immediate letter with the PDF attached and correct thread headers
- [ ] #2 A second terms_request in the same thread produces no letter
- [ ] #3 A suppressed address produces no letter and a loud record
- [ ] #4 No letter has a link
- [ ] #5 The ledger event is read by watch and the digest without errors
- [ ] #6 The clinic name, the addressee and the greeting on the letter and the PDF come from the classifier's reading of the whole thread; a value the thread does not carry is replaced by a fixed fallback (board name, From-line name, general greeting), the classifier never fails on it, and the ledger records the source of each name
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. tools/mailer_terms.py: reply subject, recipients (reply to all), thread, addressee, file name, resolve_names (what the classifier read in the whole thread is kept only when the thread carries it, else a fixed fallback), PDF HTML fill, PDF print with headless Chrome (google-chrome, as the announcement PDF; the host has no Playwright browser). 2. clinic_mailer.py: config block terms (template, pdf_html, stand); the answer classifier returns clinic_name, person_name and greeting for a terms_request, read from the whole thread; terms_letter(): once per recipient, routed() for swaps and blocks, own ledger events terms_sent / terms_blocked (never a cadence sent) that record the source of each name; act_on_answer sends it for a terms_request from a person matched to a recipient; the terms command (by hand, --preview). 3. Tests on fixtures: letter and PDF in the clinic's thread, once per recipient, suppressed address, no link, ledger event read by watch, digest and status, names from the thread and the fallbacks. 4. Deploy later with the slot: terms block in the campaign configs, restart of the desk and senders.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-07 (mailer lane): the first letter was prepared and sent by hand in the session at Ivan's go-ahead (the same letter and PDF the code now makes), recorded as a terms_sent event. Decisions in code: only an answer written by a person and matched to a recipient is answered (an automatic reply or an unmatched mail changes nothing, as before); the greeting is the recipient's own when the answer comes from its first To address, otherwise the general one (no Frau/Herr is guessed from a name); the PDF's addressee line is the name on the From line; the file name is the clinic's whole name, umlauts spelled out. The PDF HTML and the letter template live in the git-ignored data directory, never in the repo.

2026-10-07 11:50 (mailer lane): Ivan asked that the classifier (Haiku) extract the names, so that no garbage name goes out, predictably and without ever failing, reading the whole thread. Built: for a terms_request the classifier also returns clinic_name, person_name and greeting (Sehr geehrte Frau/Herr X, never decided from a first name); mailer_terms.resolve_names keeps each only when the thread (answer, signature, quoted history, our first letter's greeting, the From line) carries it, else falls back (board clinic name, From-line name, general greeting); the terms_sent event records thread/board/from-line/general per name. Replaces the earlier decision that the PDF's addressee is the From-line name. PDF printing changed from Playwright to headless Chrome (as tools/mailer_announce.py): this host has no Playwright browser and installing a second 650 MB set is against the One Playwright rule. Checked once against the real classifier on four synthetic mails (formal signature, title without first name, short mail without signature, forwarded to another person): all four named the right clinic and person, the gender-less cases got the general greeting.
<!-- SECTION:NOTES:END -->
