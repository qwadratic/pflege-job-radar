# Reingest campaign — Firecrawl-only re-verification, adapter fallback for every clinic

Launched 2026-09-08. The campaign has two goals: use Firecrawl to re-verify every clinic's job count from
scratch (finding what adapters miss), and use those misses to fix or build a free adapter for every clinic
that isn't genuinely bot-walled, so Firecrawl spend trends toward zero over time.

**What.** A scheduled reasoning loop, not a script with fixed thresholds. Each firing reads the live
dashboard and logs, decides the next bounded batch, runs it, and records what it saw — so the *next*
firing can compare against a real trend instead of one snapshot.
**Where.** State: `app/campaign.py` (`GET`/`POST /api/campaign`, owner-only, like `/api/hunter`). Batches
run through the existing `POST /api/crawl` with `mode=firecrawl` — nothing new there; `app/crawl.py`'s
`spend_gate()` already refuses Firecrawl for a clinic whose adapter demonstrably covers it ("adapter
covers it", 0 credits) and only spends where the adapter misses rows. That one rule *is* the "Firecrawl
only, but don't pay to re-learn what a free adapter already knows" policy — the campaign doesn't bypass
it, it drives it at scale, clinic by clinic, adapter-covered or not.
**Adapter fallback track.** Separate from the above: `crawlers/vendor_adapters.py` and
`pflege_jobs/sources/{softgarden,bite,pi_asp,ats_seeds,career_crawl}.py` get fixed as real bugs are found
(dead selectors, wrong pagination, a board that moved), so today's 349/407 routable clinics closes toward
407. `app/hunter.py` is unchanged by this campaign — it still only runs the 58 clinics with no adapter at
all (`fetch=='firecrawl'`), now at the tightened `max_usd_per_posting: 0.10` (was 0.50).

## Policy the routine reasons under

- **Start safe, earn greedy.** Default posture is cautious batches (a handful of boards per firing). The
  routine may escalate batch size / cap only if the trend it reads from `/api/campaign` history genuinely
  shows fewer errors over time *and* rising open-postings *and* falling cost-per-posting. One good tick is
  not a trend; look at several.
- **Hard stop 1 — Firecrawl's own monthly limit.** If the Firecrawl API itself returns a monthly/plan
  spending-limit error (not the ordinary unbilled "Agent reached max credits" refusal — that one is free
  and retryable, see `docs/firecrawl.md` §6), stop firing Firecrawl runs for the rest of that period and
  record why in `stop_reason`. **Never raise Firecrawl's own account-level spending cap to work around
  this** — that cap is the intended backstop, not a bug. (The account balance briefly went negative —
  `-29` credits — earlier on 2026-09-08 without the documented pay-as-you-go auto-reload visibly firing;
  submissions were still accepted and unbilled failures still cost 0. It had returned to a positive balance
  (971/8000) by the time this was checked again the same day, so treat this as a resolved anomaly, not a
  monthly-limit error — treat an actual limit error, not this, as the stop signal.)
- **Hard stop 2 — plateau.** If neither track (Firecrawl re-verification nor adapter fixes) has increased
  the aggregate open-postings count across Bavaria (`/api/coverage` totals, or `data/metric.sh`) over
  several consecutive firings, stop escalating and say so — further spend at that point isn't buying
  anything.
- **Big clinics first, individually; small clinics batched.** Coverage is measured as a flat clinic count
  (`clinics_with_jobs / clinics_routable`, `app/coverage.py:_pct`) — it does not weight by `beds`, so a gap
  at a 750-bed hospital counts the same as a gap at a 6-bed one, even though the former matters far more to
  a nurse job-seeker. Order batches by `beds` descending (the registry field already used for this by
  `app/hunter.py:Hunter.clinics()`): verify large clinics (roughly top third by beds, or any clinic over
  ~150 beds) one at a time so a miss there is never silently absorbed into an aggregate percentage: small
  clinics can be grouped several-per-batch since an individual miss there moves the aggregate far less.
- **Bot-walled is an accepted exception, not a default.** A clinic only counts as bot-walled after repeated
  attempts show a real block (repeated 403/429, a CAPTCHA/challenge page, consistent timeouts) — one failed
  fetch is inconclusive, not a verdict.
- **Every write goes through the existing gates.** Reserve-credits floor, `max_eur_unknown_clinic`, the 24h
  kill switch (`app/crawl.py:kill_switch`) — this campaign adds a batching/escalation policy on top, it
  does not loosen anything underneath.

## Scheduled

One cloud routine on the `pflege-board:repo` bridge environment (same environment as judge-runner — it
executes on this VM, so it can read the live dashboard, `journalctl`, and Firecrawl's own credit-usage API
directly). See `claude.ai/code/routines` for the live schedule; `docs/judge-runner.md` documents the
sibling routine pattern this one reuses. The routine is `reingest-campaign` (cron `54 */4 * * *`, every 4
hours). As of 2026-09-08 it is disabled (`enabled=false`); re-enable it on `claude.ai/code/routines` to
resume ticks.

## Fail-safe

The routine always records a snapshot via `POST /api/campaign` before it exits, even on a quiet or failed
tick, so a silent failure shows up as a gap in the history rather than nothing. Backups of `postings` /
`posting_observations` taken before the campaign started live under `backups/*.jsonl.gz` (gitignored,
regenerate with `python data/backup_postings.py`).
