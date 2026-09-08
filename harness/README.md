# harness

Harness/eval work for this repo — added 2026-09-07 after installing the `caveman` and `ponytail`
Claude Code plugins (user scope) during the data-cleanup session on the clinics registry.

## evals/

One directory per eval suite: `prompt.md` (the task) + `graders/*.md` (pass/fail criteria),
matching the format `claude plugin eval` expects (`<eval dir>/**/case.yaml` or `prompt.md` +
`graders/*.md`).

- `pflege-board-clinic-report-terseness/` — checks caveman-mode compression on a real multi-batch
  data report (dropped filler, kept every number/URL/ID exact) using an actual task from the
  pflege-board session. Authored by hand; `claude plugin eval` itself errored as
  early-access/unstable when invoked non-interactively (`bfs: error: evals: No such file or
  directory`) — not yet run through the real grader, so treat scores from it as unverified until
  someone runs it interactively and confirms the harness picks it up.
