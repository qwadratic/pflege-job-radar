# Judge runner — five verified bugs a day, up to two merge requests

**What.** A scheduled quality loop over this repo. Morning: lens-specific finders hunt real, reproducible defects; every candidate is judged by K independent judges with different lenses and survives only by majority. Afternoon: the top verified bugs get a branch each, the smallest correct fix plus a regression test, a green test run, and a merge request proposal.
**Where.** Workflow script `.claude/workflows/judge-runner.js` (named workflow `judge-runner`), helper `tools/judge_propose.py`, config `.judge/config.json`, state `.judge/queue/<day>.json`, `.judge/mr/<branch>.md`, `.judge/log.md` (all untracked except the config).
**Style.** Every agent works in caveman ultra for prose and ponytail lite for code: terse reports, simplest working change, stdlib first, never cut validation, security or tests.

## Run it by hand

```
# find only (writes .judge/queue/<day>.json)
Workflow name=judge-runner args={"phase":"find","bugs":5,"judges":3}
# propose only (reads the newest queue, pushes judge/<day>-<slug> branches)
Workflow name=judge-runner args={"phase":"propose","mrs":2}
# dry run of the propose step (no push, no mail)
Workflow name=judge-runner args={"phase":"propose","mrs":2,"dry":true}
```

From a shell: `claude -p 'Run the judge-runner workflow with args {"phase":"find"}'` inside the repo.

## Scheduled

Two cloud routines on the `pflege-board:repo` bridge environment (they execute on this VM, so they see the repo, `.env`, the local app and the exe.dev proxies):

| routine | cron (UTC) | does |
|---|---|---|
| judge-find | `0 6 * * *` | phase=find: 5 finder lenses → 3 judges each → `.judge/queue/<day>.json` |
| judge-propose | `0 14 * * *` | phase=propose: top 2 → worktree fix + test → branch push → PR (gh) or MR file + e-mail |

As of 2026-09-08 both routines are disabled (`enabled=false`) on claude.ai/code/routines — re-enable them there to resume the daily schedule.

Frequency: change the cron on the routine (claude.ai/code/routines) or ask for an update; counts and lenses live in `.judge/config.json` and can be overridden per run through `args`. Minimum interval for routines is one hour.

## Multiple judges mode

`judges` (default 3) picks how many independent judges see each finding; `judge_lenses` assigns each a distinct lens (correctness, security, reproduction), so agreement is not three copies of the same reading. A finding needs more than half the votes. Judges default to *not real* and must re-derive the evidence themselves (run the repro, read the path, try the exploit). Raise `judges` to 5 for a stricter bar; the finders loop until `bugs` survive or two rounds in a row add nothing (max 3 rounds).

## Merge requests

`gh` is installed but not authenticated on this VM, and the git remote is the exe.dev GitHub proxy. So today a proposal = the branch pushed to origin, a Markdown file under `.judge/mr/` with the title, body and compare link, and an e-mail to the owner through the VM mail gateway. Log in with `gh auth login` (or put a token in the environment) and the same step opens real pull requests instead. Branches are always `judge/<day>-<slug>`; `main` is never pushed.

## Fail-safe

Finders return zero rather than pad; judges default to rejecting; proposals stop at `mrs` per day; a proposal whose tests are not green is reported as failed, not pushed; everything is idempotent per day (queue file per day, MR files per branch).
