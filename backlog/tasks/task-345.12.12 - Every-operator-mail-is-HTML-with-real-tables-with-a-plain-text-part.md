---
id: TASK-345.12.12
title: 'Every operator mail is HTML with real tables, with a plain-text part'
status: Done
assignee: []
created_date: '2026-10-05 11:42'
updated_date: '2026-10-05 13:44'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: medium
ordinal: 294000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: letters to the operators from now on are HTML so a person reads them at a glance; tables (plan, round report, status, who is stopped and why) come as HTML tables, not as text columns. Covers the announcement (already has an HTML body and a PDF), round reports, halt, resumed and done notices, forwarded clinic answers, the answers of the desk to commands and questions, and the manual forward tool. Clinic letters stay plain German text as they are.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every mail the mailer, the desk and tools/daria_forward.py send to the operators is multipart/alternative: a text/plain part with the same facts and an HTML part; letters to clinics are unchanged
- [x] #2 Tables in operator mail (letters going out with time and clinic, who does not get one and why, campaign status) are HTML tables in the HTML part and aligned lines in the text part
- [x] #3 tests/test_clinic_mailer.py and tests/test_daria_desk.py check that a report, a notice, a forward and a desk answer carry both parts and that the HTML part holds a table where the text holds rows
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10 as built: new tools/mailer_doc.py (Doc, Table, Quote). A body is written once as blocks and rendered twice: text with aligned columns (two spaces apart, table title above), HTML with real tables. A plain string paragraph goes through a small Markdown subset in the HTML part only (lists, pipe tables, headings, bold, code), so the desk answerer can write a table; her prompt now says so. clinic_mailer.operator_mail takes a Doc or a string and always builds multipart/alternative; every operator mail goes through it: announcement (its PLAN is now an HTML table; the plan PDF stays), report (Уйдёт and Не уйдёт tables, the example letter as a quote block), notices (round, halt, resumed, done) and command answers with the state table, desk answers (state tables per wave, free text), the desk-stopped mail. digest_mail already had both parts and a table. tools/daria_forward.py notification: header lines as a table, text as a quote, original attached. state_text is now an aligned text table (state_table builds the Table), so Daria mailing_state shows aligned rows. Letters to clinics are untouched. Evidence: tests/test_mailer_doc.py (6), tests/test_clinic_mailer.py::test_every_mail_to_the_operators_has_a_text_part_and_an_html_part_with_real_tables (announcement, report, notice, command reply: both parts, HTML tables hold the text rows), tests/test_daria_desk.py::test_an_answer_with_a_markdown_table_goes_out_as_html_with_a_real_table_and_the_rows_in_the_text, tests/test_daria_forward.py; 89 passed; a rendered sample checked as a Chrome screenshot. Existing text-format assertions moved to has_row (aligned rows) and plain(m). Not done: a real mail to the operators was not sent as a visual check in a mail client; the next real notice is the first. Processes and desk restarted 05.10 15:43 Berlin on this code (nothing was mailed by the restart).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every mail the mailer, the desk and the manual forward tool send to the operators is multipart/alternative: a text part with aligned rows and an HTML part with real tables (new tools/mailer_doc.py). Verified by 89 passing tests across the mailer, desk, forward tool and renderer, and a Chrome screenshot of a sample; no real mail was sent as a check, the next real notice is the first. Clinic letters are unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
