# The phone rail, end to end (TASK-130 / TASK-142 / TASK-143)

Two processes on two machines and one ssh connection between them, opened from our side.

```
VPS tasker-dispatcher-01                      handset machine (user cursorworker1)
+---------------------------------+           +-------------------------------------+
| pflege-wa.service  :8502        |           | pflege-wa-bridge.service  :8793     |
|   POST /api/wa/bridge-webhook   |<--+       |   POST /v1/messages   one bubble     |
|                                 |   |       |   GET  /v1/outbox     inbound pull   |
| pflege-wa-bridge-relay.service  |---+       |   GET  /v1/health                    |
|                                 |           |   + the chat operations, below       |
|   ssh -L 18793 -> mini:8793 ----------------->  watcher: shade -> outbox, every 5s |
+---------------------------------+           +----------------+--------------------+
                                                               | adb
                                                        Huawei 01, WhatsApp
```

Every leg is opened **from the VPS**. Nothing on the handset machine connects to us: an ingress
tunnel into the VPS is not available, so inbound is a pull. The handset side appends to a local
SQLite outbox with a monotonic cursor and the relay drains it; the cursor advances only after our
server has accepted the item, so a crash redelivers rather than loses.

## 1. The executor, on the handset machine

```bash
rsync -a --delete --exclude __pycache__ bridge/ macmini:~/pflege-wa-bridge/bridge/
scp deploy/wa-bridge/pflege-wa-bridge.service macmini:~/.config/systemd/user/
ssh macmini 'systemctl --user daemon-reload && systemctl --user enable --now pflege-wa-bridge'
```

`~/pflege-wa-bridge/bridge.env`, mode 600:

| variable | what it is |
|---|---|
| `WA_BRIDGE_TOKEN` | bearer on every `/v1` call. Must equal the VPS's `WA_BRIDGE_TOKEN`. |
| `WA_BRIDGE_PORT` | 8793, loopback only. |
| `WA_BRIDGE_STATE` | ledger, inbound outbox and escalation screenshots. Outside any repo: candidate metadata. |
| `WA_BRIDGE_PER_NUMBER_DAILY_CAP` | **required, no default.** See below. |
| `WA_BRIDGE_WATCH_INTERVAL_SEC` | how often the watcher asks the notification shade. 5. |

Python 3.12 from the distribution, stdlib only. No virtualenv, no pip install, nothing from our
repo's `requirements.txt` — `bridge/` imports nothing outside the standard library, which is the
whole reason it can live on a machine we do not own the dependencies of.

### The one number this code refuses to choose

`WA_BRIDGE_PER_NUMBER_DAILY_CAP` has no default and the executor will not start without it. There is
no per-recipient daily cap to inherit from anywhere, and CLAUDE.md forbids inventing one. The deploy
put **30** in `bridge.env` so the rail could run; that number is the deploy's, not a decision. Ivan
or TASK-127 names the real one, and changing it is one line plus
`systemctl --user restart pflege-wa-bridge`.

It is a **fuse, not a schedule**: reaching it is a loud `429 rail_parked` with a `next_slot_at`,
which our campaign classifier turns into "failed, ownership restored". It never drops a message
quietly.

## 2. The tunnel and the relay, on the VPS — INSTALLED 2026-09-21 (TASK-146)

```bash
sudo install -m0644 deploy/wa-bridge/pflege-wa-bridge-tunnel.service /etc/systemd/system/
sudo install -m0644 deploy/wa-bridge/pflege-wa-bridge-relay.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pflege-wa-bridge-tunnel pflege-wa-bridge-relay
journalctl -u pflege-wa-bridge-relay -f
```

**Two units, one forward, on purpose.** The rail is two directions over one `ssh -L`: outbound is
`app/wa/bridge.Client` against `WA_BRIDGE_URL=http://127.0.0.1:18793` and has to work whether or not
the inbound relay is up, so the forward cannot belong to the relay's process. The relay notices a
forward that is already there and uses it; with the tunnel unit stopped it opens its own. Either
arrangement works, and the relay never kills a forward it did not start.

It reads three environment files: the repo's `.env` for the harness's own settings,
**`~/.local/state/pflege-wa-bridge/rail.env`** (mode 600) for the rail, and
`~/.local/state/pflege-wa-bridge/relay.env` (mode 600) for the ssh target, the ports, the webhook URL
and the cursor's path. The relay refuses to advance its cursor past a payload our webhook handled
nothing of, and says exactly that in the log. It checks its whole environment at startup and names
**every** missing variable at once (`relay_pull.check_env`), rather than one per restart.

One drain by hand is still a fair test, and it reads nothing from a unit:

```bash
set -a; . ~/.local/state/pflege-wa-bridge/rail.env; . ~/.local/state/pflege-wa-bridge/relay.env; set +a
.venv/bin/python -m bridge.relay_pull --probe   # round-trip + the executor's health
.venv/bin/python -m bridge.relay_pull --once    # one drain pass, then exit
```

## 3. `rail.env` — the rail's single source of truth on the VPS

**Not the repo's `.env`.** `.env` is the harness's own file and it lives in a git worktree; the
rail's secrets do not belong there. Splitting them the other way round — secrets in `.env`, settings
somewhere else — is what left the rail unconnected for a day: `grep WA_BRIDGE .env` returned nothing,
`WA_BRIDGE_PHONE_NUMBER_ID` existed in no file on the host at all, and the two units named two
different sources. One file, read by **both** `pflege-wa.service` (through the drop-in
`/etc/systemd/system/pflege-wa.service.d/10-phone-rail.conf` — the shipped unit is a template whose
`/home/exedev` paths an installer rewrites, and a state path outside the repo would not be) and
`pflege-wa-bridge-relay.service`.

| variable | what it is |
|---|---|
| `WA_TRANSPORT` | `bridge`. Which rail an **unpinned** thread resolves to; a thread that has already sent keeps its own pinned rail whatever this says (TASK-117). |
| `WA_BRIDGE_URL` | `http://127.0.0.1:18793` — the executor's port, put on our loopback by the tunnel unit. |
| `WA_BRIDGE_PHONE_NUMBER_ID` | `pflege-wa-bridge-huawei01`. What the relay writes into `metadata.phone_number_id` and what `api._number_matches` compares against — **the same variable on both sides is the only thing that keeps them equal.** Deliberately not a numeric: a forged Meta id would be provenance forgery into a column whose reader documents itself as Meta's. A value that does not match makes our own server skip the message as "another WhatsApp number's change", silently as far as a candidate is concerned. |
| `WA_BRIDGE_TOKEN` | VPS to executor, `Authorization: Bearer`. Must equal the handset machine's `bridge.env`. |
| `WA_BRIDGE_INBOUND_TOKEN` | relay to our own webhook, `X-Pflege-Bridge-Token`. |

The two secrets are separate on purpose: a leak in one direction must not grant the other.
`META_WHATSAPP_APP_SECRET` never leaves the VPS. The handset machine holds no Meta credential at
all: the WhatsApp account lives on the phone.

Check the harness really has them — `GET /api/wa/health` reports the live values, so which rail
carries traffic is never guessed from a file:

```bash
curl -s http://127.0.0.1:8502/api/wa/health | python3 -m json.tool
# transport: "bridge", bridge_ready: true, bridge_inbound_ready: true, bridge_phone_number_id: "..."
```

## 4. Proving it works

```bash
ssh macmini 'systemctl --user is-active pflege-wa-bridge'
.venv/bin/python -m bridge.relay_pull --probe | head -40
```

`/v1/health` answers with six blocks worth reading:

* `rail.driver` — serial, the adb binary in use, the WhatsApp version, and the sha256 of our own
  `adb_driver.py` as the executor actually loaded it.
* `queue` — ledger rows by state. An `attempting` row is a send whose outcome nobody knows yet; only
  a reconcile resolves it and only `confirmed_absent` authorises a resend.
* `quota` — the fuse's own reading: window, caps, what today has spent.
* `watcher` — `last_ok_at` is the heartbeat. An empty outbox looks exactly like a dead watcher; this
  is the difference, and it is what a health alarm should key on.
* `broadcast` — open runs, and the runner's own heartbeat. Same reason as the watcher's: a run that
  is not moving and a runner that is dead leave the same queue behind.
* `audit` — how many chat destructions this handset has on record.

## 4a. The chat operations (TASK-147)

The handset operations are code, not a script rewritten per occasion. Each one is a function in
`bridge/operations.py` (the broadcast is `bridge/broadcast.py`) and each is one route:

| route | what it does |
|---|---|
| `GET  /v1/chats?include_archived=1` | the chat list: title, the E.164 the handset can prove, unread, archived. Read-only, and it never carries a message body — only whether a preview line exists. |
| `GET  /v1/thread?phone=+49…` or `?chat=<title>` | the visible bubbles: direction, clock, delivery tick. `include_text=0` to get shapes without words. |
| `POST /v1/messages` | unchanged. `Operations.send_message(...)` is the same call as a function. |
| `POST /v1/broadcasts` | queue a run: `{run_id, note, pacing, items:[{client_msg_id, to, body, action}]}`. Nothing is sent by this call — the runner picks the run up and the governor paces it. |
| `GET  /v1/broadcasts`, `GET /v1/broadcasts/<id>` | every run, and one run's per-item status: `sent / queued / refused / failed`. |
| `POST /v1/broadcasts/<id>/stop` | the hard stop. It is a flag in the ledger, so it survives a restart, and it takes effect between items — never mid-bubble. |
| `POST /v1/chats/clear`, `POST /v1/chats/delete` | `{chat, phone?, confirm: true, expect_messages?, archived?}`. |
| `GET  /v1/audit?limit=N` | the destruction record, newest first. `tools/wa_bridge.py audit [--title …]` is the operator's front door to it, and it is what answers "did that delete go through" when the answer to the call was lost. |

**The destructive pair refuses more often than it acts, on purpose.** No `confirm: true`, no taps.
An unknown field (`confirm_delete`) is a refusal, not an ignored key. The chat is read first and the
answer says what is about to be destroyed; `expect_messages` lets the caller assert that count and
refuse if the conversation moved on. Two rows with one display name, or a name the address book ties
to two numbers, is a `chat_identity_mismatch` and not a coin toss. Afterwards the result is verified
off the handset — the row is gone, or the conversation reads back empty — and a failure to verify is
a `504 destruction_unverified`, the same rule as a send with no tick. Every destruction appends an
audit row **before** the refusal is raised, and the audit table is the one thing the retention sweep
does not touch.

**A destruction takes about two minutes, and the caller waits for all of it.** The flock is held
across preview, long press, menu, confirm and the verifying rescan — four walks of the chat list at
a measured 30-33 s each, plus the taps' own waits and up to 30 s waiting for the other lane to give
the phone up. The executor's log timed the three deletes of 2026-09-21 at 110 s, 98 s and 91 s, and
the third expired against the generic 90 s `WA_BRIDGE_TIMEOUT_SEC` one second before the executor
answered `200`: the operator was told the rail "may still be sending", read that as "nothing
happened", pressed again and was told there was no such chat. There was not — because the first call
had deleted it. The client now gives a destructive call a budget derived from that work
(`app/wa/bridge.py`, `DESTROY_BUDGET_SEC` = 222 s) and, if the answer is lost anyway, reads the
audit row instead of guessing: destroyed and verified is reported as done, a started-and-unproved
row as `504`, no row as "nothing had been destroyed as of T", and an unreadable audit as exactly
that, with `tools/wa_bridge.py audit --title …` to run. Asking twice for a chat this rail already
deleted is reported as done by `delete-chat` and refused by `clear-chat` — there is no conversation
left to empty.

**A broadcast resumes, it does not restart.** The run and every item live in the ledger, so a
`systemctl --user restart` mid-run continues where it stopped. The window where the bubble went out
and the process died before the item was updated is covered one layer below: the re-attempt carries
the same deterministic `client_msg_id`, and first-body-wins replays the original result and types
nothing.

**There are seven archived chats on this handset** as of 2026-09-21, besides the three on the main
list. `GET /v1/chats` walks the archive folder and marks them `archived: true`; acting on one takes
an explicit `archived: true`. Nothing in here deletes by pattern, count or sweep: one named chat per
call.

## 5. Sharing the handset

The executor takes the same `~/.local/share/wa_phone/huawei01.lock` the other lane on that machine
takes, one bubble per acquisition, released between bubbles and never held across a network call.
A watcher cycle that cannot get the lock within 5 s is skipped and counted (`busy_cycles`), not
queued — the notification shade still holds the message, so the next cycle sees it.

The other lane's daemon is not ours to stop. If it is running, both lanes queue on the flock; that
is the design, not a workaround.

## 6. What this rail cannot do, stated rather than stubbed

* **No media.** A photo or a voice note arrives as the notification's own placeholder text
  ("📷 Foto") with `media_kind` recorded beside it. The bytes are TASK-131. Synthesising an `image`
  object with an id nothing can fetch would move the failure later instead of naming it now.
* **No provider message id, in either direction.** Outbound is keyed by the `client_msg_id` we mint
  (`app/wa/bridge_ids.py`), inbound by the id `bridge/inbound.py` mints. A `sent` row requires a
  delivery tick read back off the bubble; an unverified send is a `504` and is never auto-resent.
* **No `202 queued`.** A paced request is refused with `429 rail_parked` and a `next_slot_at`.
  Accepting work we cannot pace would be the same lie as reporting an unverified send as sent.
