# Full coverage of the Bavarian nursing job market — plan for review

**Goal.** Every open, certified-nursing posting (Pflegefachkraft and up; no assistants, no trainees) at every site in the Bavarian Krankenhausplan, kept fresh, with a known cost per posting and a measurable ceiling to converge to.
**Metric.** `clinics_with_open` (of 399 active sites) and `open_jobs`, frozen by `data/metric.sh`; today 180 and ~1776. The new `GET /api/coverage` breaks both down per adapter, with Firecrawl as its own row, on the Clawl page (`/pro#/clawl`).
**Status of this document.** Sections 1–3 are decisions I am asking you to confirm in plan mode; section 4 is the critique you asked for; section 5 is the order I would build it in and what each step costs.

## 1. What "coverage" means and how to read it on Clawl

The Clawl page's coverage table has one row per adapter plus one synthetic `firecrawl` row (every clinic with no working adapter). Per row: clinics labelled, routable, boards, clinics with ≥1 open posting, open, fresh, coverage % (with-jobs ÷ labelled), last run (status, rows, new, errors), and Firecrawl credits spent in 7 days. Reading rules:

- A vendor whose coverage % lags its neighbours is the next adapter to fix; the Clawl run log names the board and the error.
- The `firecrawl` row is the backlog: those clinics have no cheap path yet. Its size is the argument for or against spending credits.
- Comparing adapters against Firecrawl is only fair on the same clinic. That is what the evals system in section 3.7 does: Firecrawl becomes the reference count per clinic, and the coded adapter's count is measured against it.

## 2. Ground truth and constraints

| fact | value | consequence |
|---|---|---|
| Firecrawl agent free allowance | **5 free agent runs per account per UTC day** ("All users receive 5 free daily runs, which can be used from either the playground or the API"; verified 2026-09-08 — the one experiment was run 1/5 and cost 0) ≈ 150 runs/month | the exhaustive pass is 5 clinics per day at zero credits; the 58 clinics in the `firecrawl` row take ~12 days; `spend_gate` prefers the allowance and only applies the EUR cap from the 6th run of a day |
| Firecrawl credits left | 379 of 8000 until 2026-09-19 | credits only matter past 5 runs/day; nothing waits for the reset |
| Firecrawl Extract tokens | 5,685 of 120,000, same period (`GET /v2/team/token-usage`); September so far: 71 credits, 1,065 tokens; August: 6,694 credits, 100,410 tokens (`/historical`) | the Agent bills credits, not tokens; both pools are shown on `/api/firecrawl/credits` and the coverage row so nobody reads one balance as the other |
| typical billable agent run | **measured 2026-09-08: 27 credits for an empty page, 47 for a board with nothing matching, 77 for 11 postings** (plus ~15 Extract tokens per credit: 705 and 1,155 tokens); failed runs ("Agent reached max credits") are not billed; a 60-credit cap fails on a real board, 120 succeeds | ≈ €0.41 per unique certified job at $0.0053/credit — above the $0.20 bar; only the 5 free runs/day meet it, so the exhaustive pass stays inside the allowance |
| price per credit | unknown for the 8000/mo plan; Hobby is $16/3000 ≈ $0.0053 | `eur_per_credit` is a setting, not a constant; refine after the first bill |
| your bar | ≤ $0.20 per unique certified job | free runs meet it by construction; for a billable run at $0.0053/credit that is ≤ 38 credits per job — a 150-credit run must return ≥ 4 unique certified jobs |
| adapters | 371 of 407 sites routable at zero credits | Firecrawl is the last resort, not the plan |
| LLM gateway | no credits | classification stays rule-based until `LLM_API_BASE` has a budget |
| webhook reachability | VM is login-gated unless `ssh exe.dev share set-public pflege-board` | webhooks are an optimisation with polling as fallback until that is confirmed |

## 3. Design decisions

### 3.1 Raw posting store, versioned in git, with explicit removal

`data/raw/<clinic_id>/<sha1(source_url)[:12]>.json` — one file per posting, the adapter's or agent's raw record (title, URL, board category, description, first/last seen, adapter, run id). Committed to git by a nightly job on its own branch (`raw-data`), never on `main`, so the code history stays readable and the raw history is still diffable and revertable.

Removal is explicit, not implicit: a posting file is deleted only when the board was fetched successfully and the URL was absent twice in a row (two runs ≥ 24 h apart), or its detail page answered 404/410. A fetch error, a rate limit, or an empty board on a walled host never deletes anything. The portal reads `postings` in Supabase, not the raw store, so a removal in git is a separate commit from the `status='expired'` flip in the database; the two are reconciled by the same nightly job. That is what keeps "portal itself must work and other jobs showing" true while a single job disappears.

### 3.2 Adapter resilience and technique ladder

Every adapter gets the same envelope (`pflege_jobs/fetch.py`, shared): retries with exponential backoff and jitter (1 s → 2 → 4 → 8, cap 60 s, five attempts), `Retry-After` and `X-RateLimit-Reset` parsed and honoured, per-host token bucket, and a per-board circuit breaker (three consecutive failures → board marked `unavailable` with reason and next-try time; visible on Clawl). Unavailability is a first-class state, so a walled or throttled board is never mistaken for "no jobs".

Techniques form a ladder, cheapest first, chosen per board and recorded in the registry: direct API (SmartRecruiters, d.vinci, softgarden feed, b-ite) → plain HTTP with URL guessing (rexx, mein-check-in, typo3, WordPress sitemaps) → Playwright with clicking and consent dismissal (JS boards; `crawlers/portals.py` already has `fetch_page`, `dismiss_consent`, `autoscroll`) → Firecrawl agent. A board only climbs the ladder when the rung below fails structurally (no URLs, no JSON-LD, walled), never on a transient error.

Real-time delivery: every technique yields inbox rows as they are produced, not at the end of the run — the adapter loop posts in batches of 50 through the existing `_post_inbox`, so a run that dies half-way has already delivered half. Firecrawl agents deliver through the webhook (`POST /api/firecrawl/webhook`, secret header, events `agent.*`), with polling as fallback; both paths converge on the same inbox rows.

### 3.3 Spend policy

- **Free allowance first**: Firecrawl grants 5 free agent runs per UTC day. `FA.agent_runs_today()` counts accepted submissions in the local ledger (`firecrawl_usage.job_id`, written the moment the API accepts a job); while it is below 5 the run is expected-free and the EUR cap is not applied — the reserve floor and the kill switch still are, because the promise could change. From the 6th run the EUR cap applies and the log says `billable run`. Every run is charged to the ledger at the measured balance delta (`GET /team/credit-usage` before/after), not the API's `creditsUsed`, which reads 0 inside the allowance.
- **Unknown clinic** (no adapter route), billable runs: cap = min(requested, `max_eur_unknown_clinic` ÷ `eur_per_credit`, remaining − `reserve_credits`). Default €5, reserve 150.
- **Known clinic** (adapter route exists): run the adapter first; count rows whose URL is not already in inbox or observations ("unseen"); Firecrawl only if unseen is 0 *and* the adapter failed structurally. No agent run for something we can already do.
- **24 h kill switch**, thresholds as percent of plan credits spent in a rolling 24 h: 10 % warn on every run, 20 % refuse Firecrawl for scheduled and `auto` runs, 30 % refuse every Firecrawl call and pause the scheduler until a human re-enables it. Failures are charged to the local ledger even if Firecrawl does not bill them (over-counting is the safe direction).
- **Clinic-level refresh on new jobs** (your ask): when an adapter run finds ≥1 unseen posting at a clinic, and that clinic's board is one where the adapter historically under-counts against the Firecrawl reference (section 3.7), queue one Firecrawl full sync for that clinic, subject to the same gate. Where the adapter matches the reference, no Firecrawl — new jobs are simply the adapter's new rows. Without the reference this rule would burn credits on every routine change, so it lands after the evals.

### 3.4 Classification sub-prompts for postings outside the Pflegedienst section

Postings confirmed inside a nursing department already skip the keyword gate (`classify_role(..., nursing_section_confirmed=True)`). For everything else — postings filed under a medical specialty, a "Jobwelt", an unlabelled board — the rules stay, and an optional second opinion is asked as explicit questions against the text, rendered on both the list view (title + board category only) and the page view (full description):

1. Is this a role for certified nursing staff (three-year qualification or higher)? yes / no / unclear
2. Which level: Pflegefachkraft · Fachpflege · Praxisanleitung · Leitung · APN/Experte · Hebamme · OTA/ATA · other
3. Is it explicitly for assistants (Pflegehelfer, Assistenz) or trainees (Ausbildung, Praktikum, FSJ)? yes / no
4. Is the workplace in Bavaria? yes / no / unclear
5. One sentence of evidence quoted from the text.

The answers are stored per posting with the rule that produced them (`role_rule`), shown in the job page's "why" box, and asked again when the description changes. They run through `LLM_API_BASE` when it has credits and through the regex mechanics otherwise, so the UI is identical either way. This is the cheapest place to spend LLM tokens in the whole system: a few hundred tokens per new posting, only for postings the structural signal could not settle.

### 3.5 Dynamics without a full rescan

Every run already writes `run_log` and `crawl_runs` with per-board rows, new, errors. A small "delta" job (`python -m app.dynamics`, also `GET /api/dynamics`) compares the latest run per board with the previous one and reports: boards that went to zero (candidate walled or dead), boards whose new-row rate spiked (candidate new department or new season), postings that disappeared without an expiry, and per-adapter coverage % over the last 30 days. That answers "how are we doing" from data we already collect. A weekly Claude routine (`/schedule`) can read that endpoint and write a two-paragraph digest; no `/autoplan`, `/wayfinder` or `gstack` are installed in this environment — only `caveman` and `ponytail` — so if you want one of those, install it and I will wire the routine to it.

### 3.6 The Firecrawl experiment and the exhaustive run

One agent run, 150-credit cap, on a clinic no adapter serves, with the caveman prompt (maximise Pflegedienst openings, capture seniority, certified only). Success = ≤ $0.20 per unique certified job at the configured price per credit, and the webhook arrived. Result (`docs/firecrawl.md` §5): 5 of 5 certified jobs, `creditsUsed` 0 and the balance unchanged at 379 — because it was **free daily run 1/5**, not because agent runs are cheap. It therefore says nothing about the cost of a billable run, which stays unknown until a 6th run in one UTC day is measured (`run_agent()` now records that delta automatically). The webhook did not arrive (login-gated host); polling delivered the result.

The exhaustive run (one agent per clinic) is scheduled **inside the free allowance**: 5 clinics per UTC day at zero credits ≈ 150 runs per month, which covers the 58 clinics in the `firecrawl` row in about 12 days — without waiting for the 2026-09-19 reset and without touching the 379 remaining credits. It is deliberately *not* a one-day burst after the reset: everything past the 5th run of a day is billable at the docs' "a few hundred credits" per run, i.e. 58 clinics × 200–300 credits ≈ 12,000–17,000 credits, more than a month's plan; anything beyond 5/day goes through the EUR cap and the €/credit math in section 2. Target list: the `firecrawl` row only, not all 407; largest clinics first; each clinic's reference count is frozen with its date (3.7). The scheduler batch for it is therefore "5 firecrawl-row clinics not yet referenced, daily", and the gate refuses the 6th on its own.

### 3.7 Evals with Firecrawl as the oracle (plan-mode item)

An eval set is a list of clinics with a reference count of mid-level-and-up certified openings, produced by one Firecrawl agent run per clinic and frozen with its date. The coded adapter for that clinic is then scored: recall (adapter ∩ reference ÷ reference), precision (adapter ∩ reference ÷ adapter), and drift over time. The set starts with the clinics the experiment and the exhaustive run already paid for, plus one clinic per adapter chosen where the adapter's coverage % is lowest. Adapter work sessions then have a number to converge to instead of "looks better". The harness is a directory per eval (`evals/<clinic_id>/reference.json`, `graders/`), a runner that executes the adapter offline against cached HTML where possible, and a Clawl panel showing recall per adapter.

## 4. Critique, from the credit-and-token side

- **Past the 5 free daily runs, Firecrawl agents are the most expensive way to read a web page, and 371 of 407 sites do not need them.** Every credit spent on a clinic an adapter can serve is wasted twice: once on Firecrawl, once on the classifier re-reading the same jobs. The spend gate's "unseen == 0 → refuse" rule exists to stop exactly that. Keep the agent for the `firecrawl` row and for building the reference set; nothing else.
- **"Update all of a clinic's jobs via Firecrawl whenever there are new jobs" is the rule most likely to drain the account.** Hospitals post weekly; 180 clinics with jobs × a few hundred credits is the whole plan every week, and the free allowance covers 35 runs a week, not 180. Tied to the evals it becomes cheap: only clinics where the adapter provably under-counts get the refresh.
- **Webhooks do not save credits; they save wall time and polling requests.** Worth having, but the login-gated VM means they may never arrive until the share is made public. Do not build anything that depends on them.
- **The 24 h kill switch is the right shape, but 10/20/30 % of plan is 800/1600/2400 credits.** With 379 left, no threshold can fire this period. That is fine for the mechanism; just do not read silence as safety until the reset.
- **The raw store in git will grow.** ~2,000 postings × ~4 KB is 8 MB, and every nightly commit rewrites changed files. On a separate branch with periodic squashing that is acceptable; on `main` it would bury the code history within a month. Hence the branch.
- **On tokens: the biggest spend in this project so far was not Firecrawl, it was agents re-deriving state.** Six independent investigations disagreed with each other until a verification pass reconciled them; the harvest plan needed 22 agents; the section-mining work three more. The fix is what section 3.5 gives you: a small, always-current delta report and a coverage table, so a future session reads two endpoints instead of re-crawling and re-reading the repo. Every workflow in this session was cheaper when it started from a measured number.
- **LLM classification sub-prompts are cheap only if they are rare.** Run them for postings the structural signal could not settle, never for the whole corpus, and cache by content hash so a re-crawl of an unchanged description costs nothing.

## 5. Build order and what each step costs

| step | what | credits | effort |
|---|---|---|---|
| 0 | Clawl rename + coverage table, Firecrawl gate/kill switch/webhook, caveman prompt, CLI wrapper, one experiment | 0 (free daily run 1/5) | done |
| 1 | shared fetch envelope: backoff, Retry-After, circuit breaker, batch streaming into inbox, unavailability on Clawl | 0 | 1 session |
| 2 | raw store on the `raw-data` branch with explicit removal; nightly reconcile with `postings.status` | 0 | 1 session |
| 3 | delta report `GET /api/dynamics` + weekly digest routine | 0 | ½ session |
| 4 | classification questions for non-section postings (rules now, LLM when funded), shown on list and page | 0 now | 1 session |
| 5 | evals harness: reference set from the experiment clinics, recall/precision per adapter on Clawl | 0 (reuses paid runs) | 1 session — **plan mode** |
| 6 | exhaustive Firecrawl pass over the `firecrawl` row, **5 clinics per UTC day inside the free allowance**, starting now | 0 (≈150 free runs/month; 58 clinics ≈ 12 days); a 6th run in a day is billable, measured, and refused by the gate unless asked for | ~12 days of calendar time, scheduled, gated by reserve + kill switch |
| 7 | clinic-level Firecrawl refresh on new jobs, only where evals show the adapter under-counts | recurring; free while the day's allowance lasts, then bounded by the gate | after 5 |

Decisions I need from you in plan mode: confirm the `raw-data` branch (vs. committing to `main`), confirm 5 € / 150-credit reserve / 10-20-30 % as the defaults, confirm that the exhaustive run targets only the `firecrawl` row, and tell me whether the evals reference should be "mid-level and up" (Fachkraft and above) or "all certified" — the number the adapters converge to depends on that choice.
