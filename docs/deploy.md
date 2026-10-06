# Deploy and operate

How the live system runs on its exe.dev VM, what starts it, what runs on a schedule, and what to check before touching it.
Code stays in `/home/exedev/repo`; the services run from that same working tree, so a checkout or merge there is live
code only after the restart below. Public URL: <https://pflege-board.exe.xyz> (exe.dev proxy to port 8501, see
<https://exe.dev/docs/proxy.md>).

## Processes

| unit | what | state |
|---|---|---|
| `pflege-web.service` | `uvicorn app.main:app` on :8501 — board, API, `/pro`, and the scheduler thread that fires the cron schedules. It only inserts `queued` rows into `crawl_runs` and reads status; it runs no crawl | enabled, install file `deploy/pflege-web.service` |
| `pflege-crawl.service` | `python -m app.crawl_worker` — the crawl worker: takes `queued` rows of `crawl_runs` in `run_id` order and runs them one at a time (`app.crawl.dispatch`) | install steps in the header of `deploy/pflege-crawl.service` and in "Switching to the crawl service" below |
| `pflege-hunter.service` | `python -m app.hunter --daemon` — Firecrawl agent runs per clinic, own stop bar (`docs/firecrawl.md` §6) | enabled, install steps in the header of `deploy/pflege-hunter.service` |
| `pflege.service` | old Streamlit app from `/home/exedev/app`, also :8501 | **disabled, keep it so** (port clash with `pflege-web`; unit is not in this repo) |

All active units read `/home/exedev/repo/.env` (`EnvironmentFile`). Key names are in `.env.example`; Stripe and auth keys are in
`docs/stripe.md` and `docs/auth.md`. Never commit `.env`.

```bash
journalctl -u pflege-web -f
systemctl is-active pflege-web pflege-crawl pflege-hunter
journalctl -u pflege-crawl -f
```

## Restart rule

The crawl worker is its own unit (`pflege-crawl`), so the services restart independently. New code reaches a service only after its restart
(the pipeline CLI steps `inbox`, `link-cross`, `link-clinics` start fresh processes and pick up code at once; tracked as TASK-149).

| restart | a running crawl | a queued row |
|---|---|---|
| `pflege-web` | **untouched**, it keeps running in `pflege-crawl` (TASK-435) | stays queued, taken by `pflege-crawl` |
| `pflege-crawl` | **killed** (`KillMode=control-group`); its row is marked `failed` with `process restarted` when the unit starts again | stays queued, taken after the start |
| `pflege-hunter` | not related | not related |

```bash
sudo systemctl restart pflege-web          # any time: app/main.py, app/*_api.py, web/, the scheduler changed
sqlite3 data/app.sqlite "select run_id, mode, status from crawl_runs where status in ('running','queued')"   # before pflege-crawl: must print nothing
sudo systemctl restart pflege-crawl        # when app/crawl.py, app/runs.py, app/crawl_worker.py, crawlers/ or pflege_jobs/ changed
sudo systemctl restart pflege-hunter       # when app/hunter.py or code it imports changed
```

The nightly pass runs about 03:00 to 09:45 UTC. Restart `pflege-crawl` after 10:00 UTC, or cancel the run first (`POST /api/crawl/runs/{id}/cancel`).
Cancel needs no restart and no shared memory: a queued row becomes `cancelled` and is never taken, a running one is flagged
(`cancel_requested`) and `crawl.execute()` stops at the next board or clinic.

Three things that live on the other side of the process line now:

- The cron schedules fire from the scheduler thread of `pflege-web` (checked every minute, a firing is picked up within 90 s of its cron time),
  so a `pflege-web` restart that spans the cron minute skips that firing.
- The board data `pflege-web` serves is a snapshot refreshed in the process that reads it. `pflege-crawl` refreshes its own after a run;
  the web one catches up within 10 min (`data.TTL`) or at once with `POST /api/refresh-cache`.
- The Firecrawl kill switch (`crawl.kill_switch`) runs in `pflege-crawl` and pauses the scheduler, which lives in `pflege-web`: the pause is the
  `scheduler_pause` row of the `settings` table now, not a variable. So it also survives a `pflege-web` restart; `POST /api/scheduler/resume` lifts it.

### Switching to the crawl service (once, TASK-435)

Before the switch the crawl worker was a thread of `pflege-web`; a restart killed the run and marked it failed (run 235 lost 4 h 33 min).
Order matters: restart the web first (the new web code runs no worker), then start the new unit. The other way round, the old web worker
and the new service would both take the same queued row.

```bash
sqlite3 data/app.sqlite "select run_id, mode, status from crawl_runs where status = 'running'"   # must print nothing, see below
sudo systemctl restart pflege-web          # the checkout in /home/exedev/repo already holds the merged code
sudo cp deploy/pflege-crawl.service /etc/systemd/system/pflege-crawl.service
sudo systemctl daemon-reload
sudo systemctl enable --now pflege-crawl
journalctl -u pflege-crawl -n 20           # "crawl worker pid ..."
```

- A run `running` at that moment is lost: in the old web it dies with the restart, and the first start of `pflege-crawl` marks every row still
  `running` as `failed` (`process restarted`), **also a run that lives in a transient unit** (`pflege-crawl-manual`, see "Manual run"), whose
  process the new service knows nothing about. So wait until nothing is `running`; deploy between 10:00 and 03:00 UTC.
- A row `queued` at that moment survives: it waits while the web restarts and until `pflege-crawl` is up, then it is taken (oldest `run_id` first).
- Drill for "a restart of `pflege-web` leaves a running run running" (TASK-435 AC#1), on one clinic: queue it with `POST /api/crawl`
  (`{"target": {"scope": "clinic", "values": ["<clinic_id>"]}, "mode": "adapter"}`), wait until the status query shows it `running`,
  `sudo systemctl restart pflege-web`, then the query still shows it `running` and `GET /api/crawl/runs/<run_id>` ends `done`.

### Manual run

A run started from `/pro`, `POST /api/crawl`, `POST /api/schedules/{id}/run-now` (owner login) or by the scheduler is a `queued` row that
`pflege-crawl` takes; none of them dies with a restart of `pflege-web`. Without an API session, insert the same row from the shell (what `run-now`
does; schedule 1 is "Daily full pass", all clinics, adapter). `pflege-crawl` takes it within `POLL_SECONDS` (5 s):

```bash
cd /home/exedev/repo && set -a && . ./.env && set +a
.venv/bin/python -c "from app import schedules as SC; print(SC.fire(SC.get(1), stagger=False, trigger='run-now'))"   # prints the run_id
```

One crawl at a time: a second queued row waits for the first. Look at what is queued or running first (query above). The row's own log is in
`crawl_runs` / `run_log` (`GET /api/crawl/runs/{id}`), the process output in `journalctl -u pflege-crawl`.
Before TASK-435 a backend run that survives a restart had to be started in a transient systemd unit (`systemd-run --unit=pflege-crawl-manual`,
run 237); that is no longer needed.

## Schedules (inside the app)

Table `schedules` in `data/app.sqlite`, edited on `/pro` → Clawl or read at `GET /api/schedules`. Two rows are live:

| name | cron (UTC) | mode |
|---|---|---|
| Daily full pass (all clinics every day) | `0 3 * * *` | `adapter` |
| Daily status re-verification (all postings) | `17 5 * * *` | `verify` |

There is one crawl worker, so runs queue. The 05:17 verify starts when the 03:00 pass ends (about 09:45).
Data path of a pass: adapters → `data/inbox.sqlite` (raw rows, unfiltered) → `python -m pflege_jobs.cli inbox` (classify, match, convert) →
Supabase Postgres (`postings`, `posting_observations`) → `link-cross`, then `verify`. The backend runs these after each crawl.

## Host cron

`deploy/crontab` is the whole crontab of user `exedev`. Install it on a new VM with `crontab deploy/crontab`; check with `crontab -l`.

| when (UTC) | command | why |
|---|---|---|
| Sunday 12:00 | `python -m pflege_jobs.cli purge-inbox --days 4` | `data/inbox.sqlite` grows ~100 MB per nightly run. Keeps the last 4 runs for `cli inbox --reprocess-run`; deletes older rows by `received_at`. Log: `data/purge_inbox.log` |

| Sunday 12:05 | `find crawl_output -name 'run_*.jsonl' -mtime +7 -exec gzip {} +` | `crawl_output/run_<id>.jsonl` is ~110 MB per nightly run. Older files become `run_<id>.jsonl.gz` (~16 MB, no row lost); `app/hunter.py` and `tools/task95_replay.py` read both |

The purge deletes rows but does not shrink the file; SQLite reuses the freed pages, so the file stays flat (about 0.5 to 1.2 GB).
To give the space back to the disk, stop writers (no crawl running) and run `sqlite3 data/inbox.sqlite VACUUM` (needs free space about the size of the live rows).

## Disk

The VM disk is 25 GB. Check with `df -h /`. What is big and what it is (all gitignored):

| path | size 2026-10-05 | note |
|---|---|---|
| `crawl_output/run_<id>.jsonl[.gz]` | plain +~110 MB per nightly run, `.gz` ~16 MB | per-run dump of the raw rows; the only copy once the inbox purge has run. Files older than 7 days are gzipped weekly, none deleted |
| `crawl_snapshots/` | 1.7 GB | recorded pages the old adapter tests read; the mirror (TASK-197, not merged yet) is to replace them |
| `data/clinic_photos/` | 1.5 GB | 399 chosen photos (`clinic_photos` table) plus 2121 unused candidates, input of TASK-120 and TASK-121 |
| `data/inbox.sqlite` | 0.5 GB | bounded by the weekly purge |
| `~/.cache/ms-playwright/` | 0.65 GB per browser revision | one Playwright only: the `.venv` package (`CLAUDE.md` → One Playwright). A second version adds a second revision |
| `backups/` | 0.1 GB | before-images of hand-made data writes (`tools/apply_*`, `tools/ledger.py`) |

Disk clean-up rule: delete only what this table says is rebuildable or recorded elsewhere, and look at the target first.

## Check after a deploy

```bash
.venv/bin/python -m pytest -q tests          # no network by design
curl -s localhost:8501/api/clinics | head -c 200
```
