---
id: TASK-345.12.9
title: >-
  Clinic answer classifier names the pattern of each answer: request for terms,
  redirect to another address, out of office with a substitute, opt-out of an
  address
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 09:58'
updated_date: '2026-10-05 14:23'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 291000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: clinic answers repeat a few communication patterns and each needs its own classifier and tool. Real examples from 02.10 and 05.10: Ilmtalklinik (Karin Nadler) asks us to send the terms ("schicken Sie mir bitte Ihre Konditionen"); LMU Klinikum München (pflegestellen@med.uni-muenchen.de) redirects to PA.ProfileLAK@med.uni-muenchen.de and asks us to stop writing to the pflegestellen address and delete it; Ilmtalklinik (Sandra Bär) and Zentralklinikum Starnberg send an out-of-office that names a substitute with an address or phone number. Today `classify` in tools/clinic_mailer.py is regex only (reply, stop, bounce, complaint, auto_reply); the Haiku classifier reads only the operators' mail. LMU's answer went through as a plain "reply", and out-of-offices are not forwarded to the operators because auto_reply stays in the ledger only. Ivan, same day: the classifier gets tools and decides and acts by itself, for example on a redirect it writes the replacement into the do-not-contact list. The list now has the schema for it: an entry with "replace_with" (addresses) does not block, the planner swaps the address (tools/clinic_mailer.py replacements, done 05.10; LMU entered by hand). First step is recognition and the do-not-contact action; replying to a clinic (terms) stays with TASK-345.12.8. Fits next to TASK-345.12.7 (clinic mail vs warm-up traffic).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each clinic answer the watch logs as reply, auto_reply or unmatched is read by the Haiku classifier (claude CLI, same setup as the operator-command classifier) and gets one pattern from a closed list: terms_request, redirect, out_of_office, opt_out, other; redirect and out_of_office carry the new or substitute addresses, names and phone numbers found in the text, opt_out carries the address it concerns and whether it is that address only or the whole clinic
- [x] #2 The pattern and its fields are written into the ledger event and named in Russian in the forward to the operators (for example: клиника просит условия; клиника просит писать на PA.ProfileLAK@med.uni-muenchen.de и больше не писать на pflegestellen@med.uni-muenchen.de); an out of office that names a substitute or an address is forwarded, one that names nobody stays in the ledger only
- [x] #3 A classifier failure fails loudly like the operator classifier: the batch halts as an error and the answer is read again on resume
- [x] #4 tests cover the four real examples above with the classifier stubbed, and the failure path
- [x] #5 The classifier has tools and uses them by itself, no operator go-ahead: on redirect it writes a do-not-contact entry with replace_with (the addresses from the answer), on opt_out of an address it writes a blocking entry for that address only (the whole clinic only when the answer says so), by = the clinic sender, date, why = the quote from the answer; it sends no letter; each action is named in Russian in the forward to the operators
- [x] #6 A terms_request, an out_of_office and any other pattern change nothing but the label and the forward; the sequence of the clinic ends only as the kind already ends it
- [x] #7 Redirect rule of the do-not-contact table (Ivan, 2026-10-05): an out-of-office or answer that names a substitute makes the substitute the main recipient (a Cc address that is named moves to To) and mutes the redirecting address with reason "redirect" via replace_with; when only names are given and the named person is already To or Cc, the redirecting address is muted and nothing else changes; names without any address of ours change nothing but the label. Ilmtalklinik (Sandra Bär to Karin Nadler), Starnberg (Tobias Heckelsmüller to Stefanie Son) and LMU are entered by hand on 05.10
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10 plan as executed: (1) ask_claude extracted from classify_command; (2) classify_answer with a closed pattern list and checks that every address and the quote are in the mail and already_ours is one of our addresses; (3) act_on_answer + table_add (flock on do_not_contact.json.lock, tmp+replace keeping the owner, one entry per match); (4) watch classifies reply/auto_reply/unmatched before logging, so a failure leaves the answer unlogged and a resume reads it again; (5) forward = the daily digest (TASK-345.12.11) with pattern and actions per row; auto_reply forwarded only when it asks for something or names someone (config forward.auto_reply, Ivan only, my choice). Decisions: "whole clinic" = every to/cc address of the clinic record, not the domain (a domain entry could block other clinics of a group); a redirect from an address we never wrote to, and an unmatched mail, change no table and say so in the digest; the muted address is the sender. Evidence: tests/test_clinic_mailer.py 65 passed with the desk tests, four real examples + failure path + invented address/quote + opt_out scopes; real Haiku on nine sample answers (the four real ones, no-name OOO, address and clinic opt_out, no need, interest) gave the right pattern and fields, as claude and as root via runuser. Not seen live: no clinic answer has arrived since the restart at 14:42. OPEN QUESTION for Ivan: the table acts on the next plan only. guard() compares an approved batch with its recipients exactly, so a redirect or opt-out from an out-of-office (which ends no sequence) does not change letters already planned; only a replan does.

05.10 later, Ivan answered "нужна" to the open question: routed(cfg, it, live) in tools/clinic_mailer.py swaps the addresses of a letter at send time by the replace_with entries of the table (live sends only; an allowlist test copy keeps its allowlist addresses; the cc is deduped against To like in plan). guard() and the approval are untouched: they cover the planned addresses; the sent event carries to/cc as sent plus planned_to/planned_cc. Tests: a live approved batch with an entry written after planning, and the cc-to-To case; 67 passed. NOT covered: a blocking entry (opt_out) is still read by plan only, so an opt-out from an automatic reply does not drop a letter already planned; no target check after the swap (plan checks suppressed() on the swapped addresses). Ask Ivan before adding either.

05.10, Ivan asked for the open gap to be closed ("на момент отправки нужна проверка"): clinic_mailer.routed() now repeats the plan block check at send time on the addresses that go (after the redirect swap), live only. A blocked To raises Blocked: the letter is not sent, a "blocked" ledger event is written (item_states shows the clinic as "адрес в списке блокировки", its other letters of the batch go with it), the console prints BLOCKED, and in a scheduled batch Ivan gets a notice "письмо не ушло"; the batch does not halt and the other clinics go on. A blocked Cc is dropped; the sent event keeps planned_to/planned_cc. The address a redirect sends to is checked too. Both lists count (do_not_contact blocking entries and sales_brain suppression_list), as at plan time. An allowlist test copy is not checked. Tests: test_a_block_written_after_planning_stops_the_letter_at_send_time, test_the_send_time_block_check_drops_a_blocked_cc_stops_a_blocked_swap_target_and_spares_test_copies, test_a_blocked_letter_of_a_simple_batch_is_skipped_loudly_and_the_others_go; 92 passed. Live audit (read-only, as root): none of the unsent letters of the approved batches of both waves is blocked today. Processes and desk restarted 16:23 Berlin.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Clinic answers (reply, auto_reply, unmatched) are read by a Haiku classifier into terms_request, redirect, out_of_office, opt_out or other; redirect/out_of_office with a substitute write a replace_with entry, opt_out writes a blocking entry (address, or all addresses of the clinic when the mail says so), the rest change only the label; a failure halts the batch and the answer is read again on resume; the daily digest names pattern and actions. Verified by 65 passing tests and a real-model check of nine sample answers; live since the 14:42 restart, first live answer pending. Open: table entries do not reach letters already planned in an approved batch.
<!-- SECTION:FINAL_SUMMARY:END -->
