# Pro activity rail (TASK-283.7): bridge queue + job health

Two board-token read routes under `/api/wa/pro/*`, mounted the same way as every route in the
["Pro API" section of `docs/whatsapp.md`](whatsapp.md#pro-api-task-395396-board-proxy--daria-write-back)
(same auth, same `db_ro()`/never-`ST._lock` discipline, same `_scrub_wamids` net) — that section's
"Auth", "Thread identity" and "Reads never lock" paragraphs apply here unchanged and are not repeated.
This file is the binding field-shape contract for these two routes specifically (`docs/wa-dashboard.md`
does not cover them); `app/wa/pro_models.py` is that contract as Pydantic, `response_model` on both
routes so a dropped or mistyped field fails loudly.

~/plans/2026-10-01-pro-activity-rail-view.md is the plan this was built from. pflege-fe's UI: a second
tab on `#/leads` ("Leads | Rail & jobs"), with a health strip on top of both tabs.

**Board proxy** (`app/wa_proxy.py`, server-side only — the token never reaches a browser):
`/wa/activity` → `GET /api/wa/pro/activity`, `/wa/ops` → `GET /api/wa/pro/ops`. Gated the same way as
every other `/wa/*` owner-read route: `/api/wa/activity` and `/api/wa/ops` are in `app/auth.py`'s
`OWNER_READ_PREFIXES` (anon → 401, a customer token → 403, the owner proxies through — see
`tests/test_app_wa_proxy.py`/`tests/test_auth.py`).

**Data path.** `bridge/relay_pull.py`'s `Relay` is the only thing that ever talks to the bridge
(`GET /v1/ops` on the mini) or to the phone-rail health call; it mirrors both into `app/wa/store.py`
(`wa_ops_mirror`, `wa_rail_snapshot`) every drain cycle. These two routes only ever read that mirror —
never the bridge, never the live phone — so a slow or unreachable bridge never slows a board read.
Five deploy-unit jobs (`catchup`, `followups`, `tunnel_watch`, `purge_test`, `agent_notes`) record their
own heartbeat row in `wa_job_runs` via `app/wa/store.py:job_run()`; three more (`relay_sync`,
`luna_reply`, `broadcasts`) are derived read-only from signals that already exist for other reasons
(no new writes for those three).

**Polling.** Every 15s normally, every 5s while the rail tab is open (pflege-fe's own choice, not
enforced server-side — these routes have no rate limit of their own).

## `GET /wa/pro/activity`

```
{generated_at, snapshot_at, synced_at, synced_source,
 rail: {tunnel: {up, since, last_error}, phone: {state, since},
        watcher: {alive, heartbeat_at}, last_sync_at},
 queue: {queued, running, done, failed},
 jobs: [{job, enabled, last_run_at, last_ok_at, last_error, next_run_at, ok_24h, failed_24h, overdue}],
 human: {queued, running, done, failed},
 source}
```

- **`queue`** — complete `COUNT(*)` over the *entire* ops mirror, grouped by state. Never a cut list,
  never windowed (see "No caps" below).
- **`human`** — the same shape, filtered to `origin=pro_human`. Human tasks are phone_ops rows with
  that origin; there is no separate `/tasks` endpoint, this is the one list.
- **`jobs`** — exactly 8 rows (see "Job keys" below; `nudges` is deliberately never one of them).
- **`rail.tunnel`** / **`rail.phone`** — the latest `wa_rail_snapshot` row, trimmed. `since` on each
  moves only on an actual state transition, not on every snapshot write that repeats the same value.
- **`rail.watcher`** — `alive: null` means no snapshot has ever been taken at all (not "taken and
  dead" — that's `alive: false`).
- **`rail.last_sync_at`** and the top-level **`synced_at`**/**`synced_source`** are the same
  `wa_rail_sync` heartbeat `docs/whatsapp.md`'s "Freshness" paragraph already documents, repeated in
  both places: one bundle for a rail-only widget, one top-level pair for a reader that wants it
  without destructuring `rail`.
- **`generated_at`** is request time; **`snapshot_at`** is `wa_rail_snapshot`'s own timestamp (`null`
  before `relay_pull.py` has ever written one — a fresh install, not an error).

## `GET /wa/pro/ops?status=&origin=&before_id=&after_id=&limit=`

```
{generated_at, source, synced_at, synced_source,
 rows: [{id, kind, origin, status, thread_id, phone_masked, created_at, started_at, finished_at,
         attempts, error}],
 next_before_id}
```

- **Row order is newest first** — the opposite convention from `/wa/pro/threads/{id}/messages`, which
  is oldest first. This is deliberate, not an inconsistency: an activity feed reads newest-on-top, a
  chat transcript reads top-to-bottom-in-time. No cursor and `before_id` both page **descending**
  (`next_before_id` is the last row's position, pass it back as the next `before_id` to go further
  back); `after_id` (poll mode, "has anything new landed since I last looked") stays **ascending** —
  same 3-mode cursor mechanics as `/messages`, opposite row order.
- `before_id`/`after_id` are `wa_ops_mirror.position` integers, an internal sequence number — never
  the bridge's own `op_id` string. Giving both at once is `400`.
- `limit` is the client's page size, not a server cap — any positive integer; `limit<=0` is `400`.
- **`status`** — exact match, one value, no `auto`/`pro` shorthand (that shorthand is `origin`-only).
- **`origin`** — `auto` means every *automated* origin (every value in the Origin table below except
  `pro_human`); `pro` means exactly `pro_human`; anything else must be one of the exact Origin values
  or `400` — a typo in this query parameter is never silently folded to `unknown` (that fallback exists
  only for a value the *bridge* supplies, which this harness does not control).
- **`thread_id`**/**`phone_masked`** are `null` for a background op with no target phone (e.g.
  `reconcile`, `list_chats`). Never a raw phone, never a `wamid` — same masking and `_scrub_wamids` net
  as every other Pro API route.
- **`attempts`** is always `null` today — the bridge tracks no retry count for a phone_ops row at all,
  so this field exists for forward compatibility only, per the contract's own wording ("`attempts` is
  null when the bridge does not track it").

## No caps

The mirror holds **every** phone_ops row the bridge has ever reported, not a "last 100" window: an
incremental position cursor (`after_position`) plus a standing refresh of whatever is still
non-terminal (`active=1`/`ids=`) keeps it complete without ever needing to re-page the bridge's full
history. `queue`/`human` are always a `COUNT(*)` over that complete mirror — never a windowed or
sampled approximation.

## Closed value lists

Per the contract's own wording, an **unknown value is shown raw**, not rejected, for every enum below
except `phone.state` and `error.code` — a future bridge release must not 500 this endpoint just because
it added a new op kind or origin before this harness's own list caught up (review discipline: see
`DeliveryStatus` in `docs/whatsapp.md`'s Pro API section for the same reasoning applied to message
delivery statuses).

**`phone.state`** — closed (this harness's own derivation, `bridge/relay_pull.py::_derive_phone_state`,
never the bridge's raw value): one of exactly

| value | meaning |
|---|---|
| `ready` | phone-rail driver connected, nothing else wrong |
| `recovering` | connected, but the last snapshot's journal replay recovered a dirty or idle-dirty state |
| `blocked` | connected, but the doctor reports a stuck op blocking the queue |
| `disconnected` | the driver itself reports not connected |
| `unknown` | no snapshot has ever reported a connected state either way (fresh install) |

Priority when more than one condition is true: `disconnected` > `blocked` > `recovering` > `ready`.

**`status`** (an op's own state) — `queued`, `running`, `done`, `failed` (`bridge/ledger.py`'s own
`phone_ops` states) **plus any other string the bridge's `/v1/ops` ever reports**, shown exactly as
received; this harness defines no exhaustive list for it.

**`kind`** — the bridge executor's own op-kind names, shown raw: `send`, `send_photos`,
`send_gallery`, `send_document`, `reconcile`, `list_chats`, `read_thread` (every kind `bridge/server.py`
currently enqueues) plus anything a future bridge build adds.

**`origin`** (`app/wa/store.py:ORIGIN_VALUES`) — exactly these 12; anything else the bridge sends on
`X-WA-Origin` is folded to `unknown` **before** it is ever mirrored (so this one, unlike `status`/`kind`
above, never shows a raw unrecognized string — the fold happens at write time, in `store.py`, not at
read time in this API):

`luna`, `luna_tool`, `followups`, `nudges`, `catchup`, `campaign`, `broadcast`, `operator`,
`agent_notes`, `bridge`, `pro_human`, `unknown`

**`job`** — exactly these 9 keys, per the contract's own list:

| job | source | cadence | notes |
|---|---|---|---|
| `catchup` | heartbeat (`wa_job_runs`) | 180s | |
| `followups` | heartbeat | 900s | **also covers `nudges`** — see below |
| `tunnel_watch` | heartbeat | 30s | |
| `purge_test` | heartbeat | 86400s | daily 03:00 Europe/Berlin cron; see "Known imprecisions" |
| `agent_notes` | heartbeat | 300s | |
| `relay_sync` | derived (`wa_rail_sync`) | ~3s | relay's own drain-loop interval |
| `luna_reply` | derived (`wa_luna_calls`/`wa_send_failures`) | — event-driven, no cadence | |
| `broadcasts` | derived (`bridge/broadcast.py` runner heartbeat) | — event-driven, no cadence | |
| `nudges` | **never emitted** | — | see below |

`nudges` is listed by the contract's own job-key enum but this harness never emits a row for it:
`app/wa/luna/followups.py` **is** the entire proactive-nudge sender (TASK-189) — there is no second
pass to wrap under a second name. Its one heartbeat is recorded under `job="followups"`. A frontend
that looks for a `nudges` row will never find one; this is a documented omission, not a bug.

`enabled` is `true` on every row, always — no job in this harness has a kill switch in
`app/wa/config.py`, so there is nothing for this field to ever report as `false`.

`next_run_at` = `last_run_at + cadence`; `overdue` (additive, not in the contract's own field list, but
`pflege-fe` asked for it) = `now > last_run_at + 2×cadence`. Both are `null` for the two event-driven
jobs (`luna_reply`, `broadcasts` — neither has a cadence to compute from) and for any job that has
never run at all.

**`error.code`** — an enum code plus free text, per the contract's own wording; the codes this harness
can actually produce, closed to exactly this list:

| code | where it comes from |
|---|---|
| `op_expired` | mirrored straight from `bridge/ledger.py` — the op's own budget_sec elapsed unacked |
| `op_cancelled` | mirrored straight from `bridge/ledger.py` |
| `restarted_while_running` | mirrored straight from `bridge/ledger.py` — the bridge process restarted mid-op |
| `bridge_no_ops_route` | `relay_pull.py`'s own: the mini's bridge build predates `GET /v1/ops` (404) |
| *(a Python exception class name, e.g. `ConnectionRefusedError`, `URLError`, `TimeoutError`)* | `relay_pull.py`'s own: the bridge/tunnel was unreachable when the relay tried to poll it |
| `relay_sync_failed` | this API's own derivation for the `relay_sync` job row, from `wa_rail_sync.last_error` |
| `broadcast_runner_error` | this API's own derivation for the `broadcasts` job row, from the runner's own `last_error` |

An op's own `error.code`/`error.text` come straight from the bridge (first three rows above); a job
row's `last_error` is either store.py's own recorded `job_run` failure (heartbeat jobs: whatever
exception the wrapped `main()` raised, named by its Python type) or one of the two derived-job codes
above.

## Freshness semantics

Three distinct timestamps appear and must not be confused:

- **`generated_at`** — this response's own request time. Always present.
- **`snapshot_at`** (`activity` only) — when `relay_pull.py` last wrote a `wa_rail_snapshot` row
  (health + phone state). `null` before the first snapshot ever happens.
- **`synced_at`**/**`synced_source`** — the engine's phone-rail heartbeat (`docs/whatsapp.md`'s
  "Freshness" paragraph), unrelated to the ops mirror or the snapshot; present in both envelopes and
  also nested as `rail.last_sync_at` inside `activity`.

None of the three is reconstructed or guessed when its underlying row doesn't exist yet — each is
`null` rather than a fabricated value (CLAUDE.md "no safety nets": no invented fallback timestamp).

## Known imprecisions (documented, not bugs)

- **`relay_sync`/`broadcasts` `ok_24h`/`failed_24h` are not true 24h windows.** `relay_sync` has one
  row, overwritten every pass (~3s): its counts are really "was the most recent pass ok" (`1`/`0`),
  not a count of passes over 24h. `broadcasts` exposes only lifetime `attempted`/`errors` counters from
  the runner's own heartbeat, not a windowed count at all. Both are flagged in the code comments next
  to `JOB_CADENCE_SEC`/`_relay_sync_job_summary`/`_broadcasts_job_summary` in `app/wa/pro_api.py`; the
  5 heartbeat jobs (`catchup`, `followups`, `tunnel_watch`, `purge_test`, `agent_notes`) have true
  windowed counts from `wa_job_runs`.
- **`purge_test`'s `next_run_at` drifts across DST.** Its real schedule is a daily 03:00 Europe/Berlin
  wall-clock cron, not a fixed interval; this API computes `next_run_at` as `last_run_at + 86400s` like
  every other cadence-based job, which can be off by up to an hour around a DST transition. The
  contract's own formula is generic; a timezone-aware special case for this one job was not built.
- **`nudges` is never a row** — see the `job` table above.

## Fixtures

`tests/fixtures/wa_pro_api/activity.json` and `tests/fixtures/wa_pro_api/ops.json`, synthetic, no real
phone numbers or thread ids — validated against `app/wa/pro_models.py` on every run by
`tests/test_wa_pro_fixtures.py`, the same way `threads.json` is, so they cannot silently drift from
this contract.
