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
(`wa_ops_mirror`, `wa_rail_snapshot`). These two tables are **not** refreshed on the same cadence
(review finding 6 — the two used to be described together as "every drain cycle", which is only true
of one of them): `wa_ops_mirror` is refreshed on every drain cycle that actually ran (the relay's own
~3s loop, skipped only on a cycle whose drain itself just failed); `wa_rail_snapshot` is refreshed at
a much coarser 60s cadence (`ALARM_CHECK_INTERVAL_SEC`), plus once extra on any cycle where the ops
mirror found new rows — never on every single drain pass. Both routes only ever read these mirrors —
never the bridge, never the live phone — so a slow or unreachable bridge never slows a board read.
`wa_ops_mirror` also carries its own heartbeat, separate from both (see "Freshness semantics" below).
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
 queue: {queued, running, done, failed, other, as_of},
 jobs: [{job, enabled, last_run_at, last_ok_at, last_error, next_run_at, ok_24h, failed_24h, overdue}],
 human: {queued, running, done, failed, other, as_of},
 source}
```

- **`queue`** — complete `COUNT(*)` over the *entire* ops mirror, grouped by state. Never a cut list,
  never windowed (see "No caps" below).
- **`queue.other`** (review finding 6, MAJOR) — a `{state: count}` map for any op state this harness
  has not seen before (a future bridge addition outside `queued`/`running`/`done`/`failed`). Never
  added as a new top-level key: `QueueCounts` is `extra="forbid"`, so a bridge that ships one new op
  state would otherwise 500 this whole endpoint the moment it appears. `{}` when every row is one of
  the 4 known states (the normal case today).
- **`queue.as_of`** / **`human.as_of`** (review finding 4, MAJOR) — the ops mirror's **own**
  heartbeat (same value both places; see "Freshness semantics" below), distinct from `synced_at` and
  `snapshot_at`. `null` before the mirror has ever completed a pass.
- **`human`** — the same shape, filtered to `origin=pro_human`. Human tasks are phone_ops rows with
  that origin; there is no separate `/tasks` endpoint, this is the one list. (Today this is always
  all-zero in practice: `pro_human` is reserved for this feature's own write half, TASK-283.3, which
  has not shipped yet — see the Origin list below.)
- **`jobs`** — exactly 8 rows (see "Job keys" below; `nudges` is deliberately never one of them).
- **`rail.tunnel.up`** — `true`/`false` once a snapshot has been written; `null` before the very first
  one (review finding 11, NIT — this used to read `false` indistinguishably from a genuinely down
  tunnel; a fresh install now reads honestly as "unknown", same convention as `rail.watcher.alive`
  right below). **`rail.phone`** — the latest `wa_rail_snapshot` row, trimmed either way. `since` on
  each moves only on an actual state transition, not on every snapshot write that repeats the same
  value.
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
{generated_at, source, synced_at, synced_source, mirrored_at,
 rows: [{id, position, kind, origin, status, thread_id, phone_masked, created_at, started_at,
         finished_at, attempts, error}],
 next_before_id}
```

- **`mirrored_at`** (review finding 4, MAJOR) — the ops mirror's own heartbeat; see "Freshness
  semantics" below. `null` before the mirror has ever completed a pass.
- **`position`** (review finding 5, MAJOR) — each row's own `wa_ops_mirror.position`, the same
  integer `before_id`/`after_id` page on. Added so a poller can build its next `after_id` straight
  from the newest row it already has, without a round trip through `next_before_id` first (which
  only ever gives the *oldest* row's position, and only when there is a further page).
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
except `phone.state` — a future bridge release must not 500 this endpoint just because it added a new
op kind or origin before this harness's own list caught up (review discipline: see `DeliveryStatus`
in `docs/whatsapp.md`'s Pro API section for the same reasoning applied to message delivery statuses).
`error.code` is its own case, covered in its own section below: it is an **open** set (review finding
6, MAJOR — the contract doc used to describe it as closed, which does not match the code and never
did), but every code this harness can actually produce today is still worth naming.

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

Two of these 12 have no real phone_ops-emitting code path today (review finding 6's own fixture
repro caught this — see "Fixtures" below): `nudges`, because `followups` already is the entire
tiered-nudge sender with no second pass to split out from it; `broadcast`, because a broadcast run
never touches phone_ops at all — it is queued through the wholly separate `/v1/broadcasts` table
(`bridge/broadcast.py`). Both stay in the enum (a future code path could legitimately start sending
either), but neither should be expected in a real `/ops` response yet. `pro_human` is also not yet
emitted by anything today — it is reserved for this feature's own write half, TASK-283.3, not built
here — but unlike the two above, that is the plan, not a mistake to fix.

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
`pflege-fe` asked for it) = `now > last_run_at + threshold`, where `threshold` is `2×cadence` for
every job **except** `relay_sync` (review finding 9, MINOR): at relay_sync's own ~3s cadence,
`2×cadence` is 6s, tight enough that perfectly ordinary scheduling jitter under load reads as
"overdue" — `relay_sync` uses a flat 60s threshold instead (`app/wa/pro_api.py:JOB_OVERDUE_SEC`).
Both `next_run_at`/`overdue` are `null` for the two event-driven jobs (`luna_reply`, `broadcasts` —
neither has a cadence to compute from) and for any job that has never run at all.

**`luna_reply`'s own `last_error` semantics** (review findings 2 and 6): the **newest send failure
within the last 24h**, else `null` — never the newest ever. Before this fix pass it read as the
newest failure ever recorded, so a send failure from a week ago would still show as "the" current
error today, long after the thread recovered; it now clears the same way every heartbeat job's own
`last_error` already did (a later success supersedes an earlier failure).

**`error.code`** — code only, never free text (review finding 2, BLOCKER: an op error's `error.text`
and a job row's `last_error.text` used to carry the bridge's/an exception's own raw string
word-for-word, which can and does carry a raw phone number or message body — see the two concrete
examples in that finding). An **open** set (review finding 6, MAJOR), not closed: a future bridge
release can add an op error code this harness has never seen, and any heartbeat job's `main()` can
raise an exception type not in this list — both are shown raw rather than rejected, same discipline
as `status`/`kind`/`origin` above. Every code this harness can actually produce today:

| code | where it comes from |
|---|---|
| `op_expired` | mirrored straight from `bridge/ledger.py` — the op's own budget_sec elapsed unacked |
| `op_cancelled` | mirrored straight from `bridge/ledger.py` |
| `restarted_while_running` | mirrored straight from `bridge/ledger.py` — the bridge process restarted mid-op |
| `bridge_no_ops_route` | `relay_pull.py`'s own: the mini's bridge build predates `GET /v1/ops` — either a genuine `404`, or the 400 `invalid_request` an *old* bridge build's own catch-all answers for any unmatched path (review finding 4, MAJOR: folded into this same code, since this module's own `/v1/ops` query is always well-formed, so a 400 here is never a real malformed-query refusal in practice) |
| `tunnel_down` | `tunnel_watch.py`'s own heartbeat: `check_once()` returned false (review finding 1, BLOCKER) |
| `messages_errored` | `catchup.py`'s own heartbeat: at least one message in the pass errored (review finding 1) |
| `purge_problems` | `purge_test_history.py`'s own heartbeat: the run reported `problems > 0` (review finding 1) |
| `nonzero_exit` | `agent_note_worker.py`'s own heartbeat: `run_once()` returned a non-zero exit code (review finding 1) |
| `send_failed` | `luna_reply`'s own derivation: at least one `wa_send_failures` row within the last 24h |
| `relay_sync_failed` | this API's own derivation for the `relay_sync` job row, from `wa_rail_sync.last_error` |
| `broadcast_runner_error` | this API's own derivation for the `broadcasts` job row, from the runner's own `last_error` |
| `ledger_position_reset` | `relay_pull.py`'s own ops-mirror heartbeat (review finding 11, NIT) — see "Known imprecisions" below |
| *(a Python exception class name, e.g. `ConnectionRefusedError`, `URLError`, `TimeoutError`, `PermissionError`)* | either `relay_pull.py`'s own (the bridge/tunnel was unreachable, or the ops mirror pass hit a bug), or any of the 5 heartbeat jobs' own `main()` raising uncaught rather than setting one of the explicit codes above |

An op's own `error.code` comes straight from the bridge (the first three rows above); a heartbeat
job's `last_error.code` is either one it set explicitly before a clean `ok=False` exit (the four
`review finding 1` rows above) or an uncaught exception's own type name; `relay_sync`/`broadcasts`/
`luna_reply` use their own derived codes.

## Freshness semantics

Four distinct timestamps appear and must not be confused (review finding 4, MAJOR, added the
fourth — before this fix pass nothing told the API the ops mirror itself had gone stale, so `queue`
and `/ops` could show frozen counts as current while every mirror pass had been failing):

- **`generated_at`** — this response's own request time. Always present.
- **`snapshot_at`** (`activity` only) — when `relay_pull.py` last wrote a `wa_rail_snapshot` row
  (health + phone state). `null` before the first snapshot ever happens.
- **`synced_at`**/**`synced_source`** — the engine's phone-rail heartbeat (`docs/whatsapp.md`'s
  "Freshness" paragraph), unrelated to the ops mirror or the snapshot; present in both envelopes and
  also nested as `rail.last_sync_at` inside `activity`.
- **`queue.as_of`**/**`human.as_of`** (`activity`) and **`mirrored_at`** (`ops`, top-level) — WHEN
  the ops mirror **last succeeded** (same "a failure leaves the last success exactly as it was"
  convention `synced_at` already uses, `app/wa/store.py:record_rail_sync_error`'s own rule): a
  `mirror_ops()` pass writes its own heartbeat row on every outcome, success or failure, including
  an **empty** success ("the mirror tried and found nothing new" is itself current information, not
  nothing to report) — but this timestamp specifically only advances on success, so it staying
  frozen while `generated_at` keeps moving is itself the stale-mirror signal (review finding 4,
  MAJOR's own point: nothing told a caller the mirror had gone stale while `queue`/`/ops` kept
  looking current). `null` before the mirror has ever succeeded even once. The pass's own
  error/error_code (`bridge_no_ops_route`, `ledger_position_reset`, or an exception class name) is
  recorded in the same underlying row for on-box debugging, but is not surfaced through either of
  these two routes today — staleness alone is what a caller needs to stop trusting the counts.

None of the four is reconstructed or guessed when its underlying row doesn't exist yet — each is
`null` rather than a fabricated value (CLAUDE.md "no safety nets": no invented fallback timestamp).

## Known imprecisions (documented, not bugs)

- **`relay_sync`/`broadcasts` `ok_24h`/`failed_24h` are `null`, not a count** (review "Documented
  gaps" — changed by this fix pass; they used to return a number that looked like a real 24h window
  but was not one: `relay_sync` has one row, overwritten every pass, so its old `1`/`0` really meant
  "was the most recent pass ok", and `broadcasts` exposed lifetime `attempted`/`errors` counters from
  the runner's own heartbeat that reset on a bridge restart — both misleading in the exact way a UI
  would trust least, a number that looks precise and isn't. Both fields are now `Optional[int]`,
  always `null` for these two jobs, rather than a number with a hidden asterisk. The 5 heartbeat jobs
  (`catchup`, `followups`, `tunnel_watch`, `purge_test`, `agent_notes`) are unaffected — they have
  true windowed counts from `wa_job_runs` and keep reporting a real number.
- **`purge_test`'s `next_run_at` drifts across DST.** Its real schedule is a daily 03:00 Europe/Berlin
  wall-clock cron, not a fixed interval; this API computes `next_run_at` as `last_run_at + 86400s` like
  every other cadence-based job, which can be off by up to an hour around a DST transition. The
  contract's own formula is generic; a timezone-aware special case for this one job was not built.
- **`nudges` is never a row** — see the `job` table above.
- **A ledger reset with nothing open in the mirror at that exact moment has no signal to catch**
  (review finding 11, NIT). The reset detector (`ledger_position_reset`, see "Freshness semantics"
  and the `error.code` table above) works by noticing every op_id the mirror still has open vanish
  from the bridge at once — the one false-positive-free signal this read-only interface offers. A
  reset that happens to land while the mirror has zero open ops leaves nothing to vanish, so it is
  silently missed until the next op the mirror tracks exposes the mismatch some other way. Narrow
  (a ledger reset is itself rare) and accepted as-is, not fixed here.
- **The `ids=` query string on `GET /v1/ops` grows with the number of open ops** (review finding 11,
  NIT) — `Relay.mirror_ops()` refreshes every currently-open op by passing all of their ids in one
  query string, every pass. Fine at today's volumes; a phone-rail with a very large backlog of
  simultaneously open ops would eventually hit a URL-length wall. Left as-is, not fixed here.

## Fixtures

Generated, never hand-written (`tools/wa_pro_fixtures.py` — its own module docstring has the full
mechanism): `tests/fixtures/wa_pro_api/activity.json`, `activity_tunnel_down.json`, `ops.json` and
`ops_page2.json`, synthetic, no real phone numbers or thread ids. `activity.json` is the normal
state (tunnel up); `activity_tunnel_down.json` is the same engine a little later with the bridge
unreachable (`tunnel.up: false`, an `since`/`last_error`, and `snapshot_at` left stale rather than
blanked — `write_rail_snapshot`'s own rule). `ops.json` is the first page of the ops mirror (newest
first); `ops_page2.json` is the next page via `before_id`, showing the cursor shape. All four are
seeded through the real engine-side writers (`ST.job_run`, `ST.upsert_mirrored_op`,
`ST.write_rail_snapshot`, `ST.record_rail_sync_ok`/`record_rail_sync_error`,
`ST.record_luna_call`/`record_send_failure`) with fake bridge `/v1/ops` and `/v1/health` payloads
shaped like `bridge/ledger.py`'s and `bridge/executor.py`'s own real ones, never a raw INSERT or a
hand-typed response body — the hand-written versions these replaced had already drifted from the
real routes once (a leaked real hostname, and an op error message that said "...was running"
where the bridge's own code says "...was in flight"). All four are validated against
`app/wa/pro_models.py` on every run by `tests/test_wa_pro_fixtures.py`, and checked byte-identical
to a fresh generator run by `tests/test_wa_pro_fixtures_generated.py`, the same way `threads.json`
is, so none of the eight committed fixture files can silently drift from this contract.

**Review finding 6's own fixture repro, fixed in place.** `ops.json` used to show `origin=broadcast`
and `origin=nudges` on two rows — neither has a real phone_ops-emitting code path (`broadcast` runs
never touch phone_ops at all, through the wholly separate `/v1/broadcasts` table; `followups` already
covers the tiered nudge sweep, per `bridge/ledger.py`'s own origin comment) — so those two rows now
carry `followups`/`campaign` instead, the real origins those two code paths actually stamp. The old
`activity.json`'s `relay_sync` row used to show a populated `last_error` even on an otherwise fully
healthy relay_sync (the `_relay_sync_job_summary` bug this fix pass also fixed — see git history);
that is gone too, since `ok_24h`/`failed_24h` are now `null` for that job (see "Known imprecisions"
above) and its `last_error` is genuinely `null` on a healthy pass. All four fixtures also now carry
`other`/`as_of` on every `QueueCounts`, `position` on every op row, and `mirrored_at` on the ops
envelope.
