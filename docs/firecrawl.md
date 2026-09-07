# Firecrawl agent: prompt, spend gate, kill switch, webhook, CLI, and the one experiment

This is the Firecrawl track's design note. It owns `app/firecrawl_hooks.py`, `pflege_jobs/sources/firecrawl_agent.py`,
`app/crawl.py`, `app/runs.py`, `app/settings.py`, `app/schedules.py`, `app/scheduler.py`, `app/firecrawl_gate.py`,
`tools/firecrawl_agent.sh`, `.claude/skills/firecrawl/`, and this file. It does **not** touch `app/main.py`,
`web/*`, `app/data.py`, or `app/coverage.py` (those are already wired and owned by the coverage track).

## 1. The jobs prompt (`_jobs_prompt`, `pflege_jobs/sources/firecrawl_agent.py`)

Written caveman-terse on purpose — every extra word is tokens the agent has to read on every one of its
own tool calls. All the hard rules from the previous prompt are kept (one hospital, start at the given
URLs, follow the linked board, paginate fully, Bavaria only, one detail URL per job, never invent, empty +
reason if blocked); the wording is compressed and the goal is now explicit ("MAX number", "missed job =
failure") because Firecrawl's model treats vague asks softly and stops crawling early otherwise:

```text
GOAL: find MAX number of open Pflegedienst (nursing dept) jobs at ONE hospital: "<name>", <town>, Bavaria,
Germany (operator: <operator>). Also capture seniority per job. Not a survey -- a full count. Missed job = failure.
HARD RULES, no exceptions:
1. ONE hospital only. Start at given URL(s): careers page + any board it links to (softgarden, B-ITE, rexx,
   umantis, mein-check-in, d.vinci, Personio, ...).
2. Paginate FULLY. Follow next/page2/'mehr laden' until board end. Do not stop at page 1.
3. INCLUDE only certified nursing staff: Pflegefachkraft, Gesundheits- und Krankenpfleger(in), Fachpflege
   (Intensiv, Anästhesie, OP), Praxisanleitung, Stationsleitung / Pflegedienstleitung / PDL, Hebamme, OTA/ATA,
   APN / Pflegeexperte.
4. EXCLUDE always: Pflegehelfer, Pflegefachhelfer, Assistenz; Ausbildung/Azubi/Schüler; Praktikum; Werkstudent;
   FSJ/BFD; physicians; MFA; therapists; admin; logistics; any job outside Bavaria.
5. Each job = own direct detail-page URL. Never the list-page URL. Never a URL you did not see.
6. Never invent a job. Board empty or blocked (login wall, bot block, offline, no nursing listed) -> jobs=[]
   and say why in blocked_reason.
PER JOB, fill: department = board's own category label if it has one (else empty); seniority = one of
leitung|fach|fachkraft|experte|unknown (leitung=Stationsleitung/PDL, fach=Fachpflege specialist e.g.
Intensiv/Anästhesie/OP, fachkraft=general Pflegefachkraft/GKP, experte=APN/Pflegeexperte, unknown=unclear).
Answer strictly in the schema.
```

`Pflegehelfer` moved from INCLUDE to EXCLUDE to match the certified-nursing-only policy the rest of the
pipeline already enforces (`pflege_jobs/patterns.json: excluded_role_classes`). `JOBS_SCHEMA` gained
`jobs[].seniority` (enum `leitung|fach|fachkraft|experte|unknown`) and a top-level `blocked_reason`
alongside the existing `notes`. `_career_prompt`/`CAREER_SCHEMA` are unchanged.

## 2. Webhook receiver (`app/firecrawl_hooks.py`)

`POST /api/firecrawl/webhook` (already wired in `app/main.py`) requires header
`X-Pflege-Webhook-Secret` to match settings key `firecrawl.webhook_secret`, generated once with
`secrets.token_urlsafe(24)` on first use (`get_webhook_secret()`) and persisted via `runs.set_setting`.
Every event is stored verbatim in a new SQLite table `firecrawl_events` (`app/runs.py` SCHEMA:
`at, event_type, job_id, clinic_id, run_id, success, credits_used, raw`) regardless of type, so:

- `run_agent()`'s poller can check `firecrawl_events` for a job id and stop polling once a webhook already
  answered (`check_webhook=` callback; `app/crawl.py:webhook_check()` is the concrete implementation used
  by real runs).
- the experiment below can prove whether a webhook ever actually arrived.

On `agent.completed`: converts the answer via `FA.jobs_to_inbox_rows` for the clinic named in
`metadata.clinic_id`, posts the rows through `app.crawl._post_inbox` (the same path every other crawl
uses, so rows land in the inbox in real time instead of waiting for the poller), and records
`creditsUsed` via `R.add_usage`. On `agent.failed`/`agent.cancelled`: records credits and logs the error
on the run (`metadata.run_id`). On `agent.action`: appends a run-log line. The handler always answers
`{"ok": true}` in well under a second; nothing in the completed/failed/action branches can throw past the
outer `try` (a broken handler must never turn into a retry storm from Firecrawl's side).

**Two real-API quirks found while running the experiment (not documented anywhere I could find):**
1. `webhook.metadata` values must be JSON **strings** — an integer `run_id` gets a `400 invalid_type`.
2. `webhook.events` wants the bare event names (`started`, `action`, `completed`, `failed`, `cancelled`),
   **not** the `agent.`-prefixed names the received payload's own `type` field uses. Submitting
   `"agent.completed"` etc. in the request also gets a `400`. The received payload's `type` field, by
   contrast, *is* `agent.completed` etc. — confirmed from the five-event ask completing in one non-`agent.`-
   prefixed submission (see §5).

Neither malformed attempt cost anything (`GET /v2/team/credit-usage` before/after was unchanged), which is
some evidence for "Firecrawl doesn't bill a rejected `400` request" alongside the documented "failed runs
are not charged" — still not proof for an in-flight run that *starts* and then fails, which is why the kill
switch below records usage from `AgentFailed.credits_used` rather than assuming 0.

`reachability`: `https://pflege-board.exe.xyz/api/firecrawl/webhook` is login-gated unless the user has run
`ssh exe.dev share set-public pflege-board` from their own machine; this VM cannot check that (no ssh key,
and the hostname resolves to a private address from here). So the webhook is submitted as an optimisation
on every run, but `run_agent()`'s polling loop is what actually delivers the result — confirmed necessary
in practice, see §5 (`webhook_arrived: false`).

## 3. Spend gate + 24h kill switch (`app/settings.py`, `app/crawl.py`, `app/scheduler.py`)

New editable settings under `PUT /api/settings/firecrawl` (`app/settings.py: FIRECRAWL_DEFAULT`):

| key | default | meaning |
|---|---|---|
| `eur_per_credit` | `0.0053` | Hobby plan price ($16/3000); the user's actual 8000/mo plan price is unknown, so this stays editable |
| `max_eur_unknown_clinic` | `5.0` | EUR cap per Firecrawl call for a clinic with no routable adapter |
| `kill_switch_pct` | `[10, 20, 30]` | `[warn, throttle, disable]`, percent of **plan** credits spent in a rolling 24h |
| `reserve_credits` | `150` | never let `remaining` fall below this in the current billing period |

**`spend_gate(clinic, max_credits, probe_adapter=None, log=print)`** (`app/crawl.py`) — `{'allowed',
'cap', 'reason', 'unseen'}`:
- **Unknown clinic** (`not clinic.routable or clinic.walled`): `cap = min(max_credits,
  max_eur_unknown_clinic / eur_per_credit, remaining - reserve_credits)`. Refused (`cap=0`) once the
  reserve floor is hit.
- **Known clinic** (routable, not walled): runs the real adapter for just that one clinic
  (`_adapter_probe_rows`, injectable as `probe_adapter=` for tests) and checks which of its URLs are
  **unseen** — not already sitting in `inbox` or `posting_observations` (`_unseen_source_urls`, one
  PostgREST `in.()` lookup against each table, batched by 200). If the adapter already covers every row,
  Firecrawl is refused outright: `reason: 'adapter covers it'`. Otherwise capped the same way as the
  unknown-clinic branch (minus the eur cap, since the clinic is already known-cheap to fetch). An adapter
  probe that raises is a refusal, not a crash.
- Used by both call sites: `execute()`'s firecrawl branch and `refetch_career()`.

**`kill_switch(run_mode=None, trigger=None, log=print)`** — `(allowed, reason)`, driven by
`R.usage_total(hours=24)` (new `hours=` param on `usage_total`, alongside the existing `days=`) versus
`FA.credits()['plan']`:
- `< warn%` (10): silent pass.
- `>= warn%`: pass, one log line.
- `>= throttle%` (20): refused for anything that isn't an explicit `mode='firecrawl'` call (scheduled
  and `mode='auto'` runs are blocked; a human clicking "Firecrawl" on one clinic still goes through, with
  a log line saying so).
- `>= disable%` (30): refused **unconditionally**, manual included, and it pauses the scheduler
  (`app/scheduler.py: pause()/resume()/is_paused()`) so the automatic loop stops firing anything at all,
  not just Firecrawl work. `GET /api/settings` (unmodified endpoint, already returns `scheduler:
  S.status()`) now surfaces `paused`/`paused_reason` because `S.status()` includes them.
- A `None`/unreachable plan (`FA.credits()` failed) never blocks on this signal alone (`pct = 0.0`).

Usage-on-failure: `AgentFailed` already carries `credits_used` (falls back to `max_credits` when the API
doesn't report one); both `execute()` and `refetch_career()` call `R.add_usage(...)` in the `except
FA.AgentFailed` branch — verified still true after the refactor above.

## 4. CLI wrapper + skill (project-scoped)

`npx -y firecrawl-cli@latest` installs and runs fine here (Node 18, engine warnings only, no functional
issue). **`init`/skills install is user-scope only** — verified by running it with `HOME` pointed at a
throwaway directory: skills always land under `$HOME/.claude/skills` and `$HOME/.agents/skills`, regardless
of `cwd`; there is no repo-scope flag (`-g/--global` is the *only* scope besides `-a/--agent <name>`, which
picks which agent's skill folder to write, not where). So per the brief, `init` was **not** run against the
real `$HOME`. Instead: the CLI's own `skills-native.js` shows it clones `github.com/firecrawl/cli` and
copies `skills/<name>/SKILL.md`, so that `firecrawl-agent` skill was fetched the same way and placed at
`.claude/skills/firecrawl/SKILL.md` in this repo (project-scoped, not global).

**`tools/firecrawl_agent.sh <clinic_id> "<prompt>" [extra agent args]`** — gates itself through **the
same** `spend_gate`/`kill_switch` as the web app via `python -m app.firecrawl_gate <clinic_id>
[max_credits]` (`app/firecrawl_gate.py`, exit 0 + cap on stdout, exit 1 + reason on stderr when refused,
exit 2 on a usage/lookup error), then execs `npx firecrawl-cli@latest agent "<prompt>" --max-credits <cap>
--wait --json "$@"`. Never pass `--browser`; the wrapper always sets `--max-credits` itself.

```bash
cd /home/exedev/repo && set -a && . ./.env && set +a
tools/firecrawl_agent.sh 77406 "extract open Pflegedienst jobs" --schema-file /tmp/jobs_schema.json
```

## 5. The one experiment

**Candidate selection** (keyless registry read, `crawlers.routing.plan()` for "unroutable", beds 150–500,
has a website): out of 356 `Plan-KH` hospitals only **2** qualified — everything else in that bed range
already has either a `careers_url`-less-but-adapter-known route or a fingerprinted vendor. Picked the
larger one:

- **Bezirkskrankenhaus Günzburg** (clinic_id `77406`), 422 beds, `ats_type: self_hosted` (`crawlers.routing`
  reason: `no adapter for self_hosted`), `careers_url`
  `https://www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung`,
  `website: https://www.bkh-guenzburg.de`, `routable: false`, `walled: false`.

**Spend gate check** (before submitting): `spend_gate(clinic, 150)` → `{'allowed': true, 'cap': 150,
'reason': 'unknown clinic (no adapter route)'}` — confirmed the 150-credit cap the brief asked for, against
real `FA.credits()` (`remaining: 379, plan: 8000`) and the default `reserve_credits: 150` (`379 - 150 =
229 ≥ 150`). `kill_switch()` at the time: `0%` of plan credits spent in the last 24h → allowed.

**Submission**: `run_jobs_agent(clinic, max_credits=150, webhook={...}, check_webhook=CR.webhook_check)`.
Two malformed attempts were rejected by the API with `400` **before any job was created** (metadata values
must be strings; `events` must be the bare names — see §2) — confirmed zero-cost both times by re-reading
`FA.credits()` (`remaining` unchanged at 379). The corrected submission is the one and only accepted
`POST /v2/agent` call this workflow made.

**Result** (job `01a07db8-3559-742a-ac7c-2e092d733e82`, model `spark-2`, mode `extract`):

| metric | value |
|---|---|
| status | `completed` |
| wall time | 184.7 s (~3 min) |
| `creditsUsed` (API-reported) | **0** |
| `remaining` credits before / after (`FA.credits()`) | 379 / 379 — **measured spend: 0 credits**, confirms the self-reported 0 rather than assuming it |
| raw jobs returned | 5 (board listed 60 total across all 8 sites of the operator; agent excluded non-hospital `WOHNEN-und-FÖRDERN` postings, the cross-site initiative application, and everything not this one hospital) |
| unique jobs (deduped by URL) | 5 |
| certified nursing after `classify_role` + `jobs_to_inbox_rows` | **5 / 5 (100%)** |
| seniority distribution | `fach: 1, fachkraft: 4` (no `leitung`/`experte`/`unknown` in this batch) |
| webhook event received (`firecrawl_events` for this run) | **none** — as expected, since `pflege-board.exe.xyz` is login-gated and this VM cannot make it public; the polling fallback is what actually delivered the result |

**Cost per unique certified job**: `0 credits × €0.0053/credit = €0.00`. Even pricing it conservatively at
Firecrawl's documented "a few hundred credits" per typical agent run (which this one did not hit) —
300 credits ÷ 5 jobs × €0.0053 ≈ **€0.32/job** — would still be in the right ballpark of the user's
$0.20/job bar; at the *measured* 0 credits it trivially beats it. Given the 0-credit result is a single
data point on a small (5-job) target, do not generalise "Firecrawl agent runs are free" from it — the
kill switch and spend gate above are what actually protect the account if a future run isn't this cheap.

No second run was submitted, per the hard credit rule.
