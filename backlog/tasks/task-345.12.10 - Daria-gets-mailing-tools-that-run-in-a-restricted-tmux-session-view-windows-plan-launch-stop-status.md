---
id: TASK-345.12.10
title: >-
  Daria gets mailing tools that run in a restricted tmux session: view windows,
  plan, launch, stop, status
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 09:58'
updated_date: '2026-10-05 13:44'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 292000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: planning and launching mailings is Daria's job, not a Claude Code session's. Today batches and the desk run in tmux session nurse79 (windows started by hand or by a Claude session with allow rules in .claude/settings.local.json), and Daria's answerer has no shell, only the tools in tools/daria_tools.py. Daria needs limited tmux access: a tool to look at the mailing tmux windows, and a separate restricted tmux environment where only mailing commands can run. Her side is only tools; whether they work through tmux or not is ours to decide, for now through tmux. Related: TASK-345.12.4 (Daria plans mailings that operators stop or trim by mail).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Daria has read tools that list the mailing tmux windows and show the last lines of a window and of a campaign send output file
- [x] #2 Daria has tools to plan a campaign batch, launch an approved batch, stop a running batch, resume a halted one and start the desk; each runs its fixed command (tools/clinic_mailer.py or tools/daria_desk.py with a campaign config) in a dedicated tmux session that accepts no other command, and she still has no shell
- [x] #3 Every call is written to the desk ledger with who asked, the command and its result
- [x] #4 tests/test_daria_desk.py or a new test file covers the tools with tmux and the commands stubbed, including a refused command outside the list
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10 plan as executed: seven tools in tools/daria_tools.py (mailing_windows, mailing_window_tail, send_output_tail, plan_batch, start_batch, stop_batch, start_desk); the desk passes DARIA_CAMPAIGNS, DARIA_DESK_CONFIG, DARIA_TMUX (desk config "tmux": session daria-mailing, view [nurse79, daria-mailing]), DARIA_ASKED_BY (the operator of the mail she answers), DARIA_TZ. Decisions: start_batch is one tool for launch and resume (the same `send --live` command does both, the mailer decides and refuses a halted-for-good or late batch); stop_batch sends Ctrl-C to the window that runs the batch (checked on a scratch process under sudo: the handler runs, the mailer logs an error halt, resumable); a command is one shell line built from the fixed argv with every argument checked against the desk own campaigns, batch files, recipient ids and ISO times, and each window runs only that line, no shell is left; plan runs without sudo, send and desk with sudo -E like the hand launches; plan_batch waits up to 600 s and otherwise returns finished false with the file; every call (also a refused one) is a mailing_tool event in the desk ledger with by, args, command, result or error, and a call whose ledger cannot be written does not run. Live finding: the desk ledger was root-owned, her tool server runs as claude, so the first real answer got an empty error; the desk now chowns it for run_as before each answer, and I chowned the file. Also: mailing_state only listed announced batches, so Daria could not see the planned follow-up batches; it now lists planned ones with approval state or halt (tests/test_daria_desk.py). Her prompt now says: plan_batch for Ivan or Valentyn, stop_batch for either, start_batch and start_desk only when Ivan asks, never approve a plan. Evidence: tests 77 passed (tests/test_clinic_mailer.py + tests/test_daria_desk.py, tmux and ps faked, including refused arguments); a real answer through the real answerer listed the windows, read a batch output and a window (calls in the desk ledger); a real plan_batch with a past announce time ran in a real tmux window and returned exit status 2 with the mailer error. NOT run live: start_batch, stop_batch and start_desk against real processes. OPEN for Ivan: none of start_batch and start_desk refuses when a process for that batch or a desk already runs, so a double launch would send the letters twice or answer every mail twice. Also: backlog doctor reports 10 duplicate task ID groups, which makes Daria pipeline_list fail (ID-based commands blocked) until someone repairs them.

05.10, after Ivan asked for the guard ("Да, добавь проверку"): a second process is now refused in two places. (1) The tool: start_batch and start_desk look at ps first and refuse, naming the running command, while a clinic_mailer.py send for that batch id or a daria_desk.py run is alive. (2) The programs: clinic_mailer.single_process takes an exclusive non-blocking flock on the batch file (send) and on the desk config (daria_desk.run) and fails at once with "already running"; the lock goes with the process, so SIGKILL frees it, and it holds for a hand launch too. Verified live: after the restart every batch file and the desk config reported as held by a flock probe. Tests: test_a_second_process_for_the_same_batch_fails_at_once, test_a_second_desk_fails_before_it_logs_or_mails_anything, test_start_batch_and_start_desk_are_refused_while_the_process_runs. The blocking-entries-at-send-time gap is still open for Ivan.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Daria has seven mailing tools: three to look at tmux windows and a batch output file, and plan_batch, start_batch (launch or resume), stop_batch (Ctrl-C) and start_desk, each running one fixed command in her own tmux session with checked arguments; every call is audited in the desk ledger with the operator who asked. Verified by 77 passing tests with tmux faked, a real answer that used the read tools, and a real past-dated plan in a real window; start, stop and desk were not run live. Open: no guard against a double start.
<!-- SECTION:FINAL_SUMMARY:END -->
