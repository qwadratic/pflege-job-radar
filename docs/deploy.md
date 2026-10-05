# Deploy and operate

How the live system runs on its exe.dev VM, what starts it, what runs on a schedule, and what to check before touching it.
Code stays in `/home/exedev/repo`; the services run from that same working tree, so a checkout or merge there is live
code only after the restart below. Public URL: <https://pflege-board.exe.xyz> (exe.dev proxy to port 8501, see
<https://exe.dev/docs/proxy.md>).

## Processes

| unit | what | state |
|---|---|---|
| `pflege-web.service` | `uvicorn app.main:app` on :8501 — board, API, `/pro`, **and the crawl worker, in-process** | enabled, install file `deploy/pflege-web.service` |
| `pflege-hunter.service` | `python -m app.hunter --daemon` — Firecrawl agent runs per clinic, own stop bar (`docs/firecrawl.md` §6) | enabled, install steps in the header of `deploy/pflege-hunter.service` |
| `pflege.service` | old Streamlit app from `/home/exedev/app`, also :8501 | **disabled, keep it so** (port clash with `pflege-web`; unit is not in this repo) |

Both active units read `/home/exedev/repo/.env` (`EnvironmentFile`). Key names are in `.env.example`; Stripe and auth keys are in
`docs/stripe.md` and `docs/auth.md`. Never commit `.env`.

```bash
journalctl -u pflege-web -f
systemctl is-active pflege-web pflege-hunter
```

## Restart rule

The crawl worker lives inside `pflege-web`. A restart **kills a running crawl**. New code reaches the service only after a restart
(the pipeline CLI steps `inbox`, `link-cross`, `link-clinics` start fresh processes and pick up code at once; tracked as TASK-149).

```bash
sqlite3 data/app.sqlite "select run_id, mode, status from crawl_runs where status in ('running','queued')"   # must print nothing
sudo systemctl restart pflege-web            # and pflege-hunter if app/hunter.py or code it imports changed
```

The nightly pass runs about 03:00 to 09:45 UTC. Restart after 10:00 UTC, or cancel the run first (`POST /api/crawl/runs/{id}/cancel`).

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
