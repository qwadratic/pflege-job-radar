---
id: decision-8
title: >-
  Amends decision-6 and decision-7: the rail is huawei_p30_lite_01, the executor
  wraps an existing lane on Ubuntu, and a verified delivery tick replaces the
  provider message id
date: '2026-09-21 09:07'
status: accepted
---
## Context

decision-6 (2026-09-21 01:18) and decision-7 (02:57) were both filed before anyone had logged into the
machine they describe. Access came later the same day. The read-only investigation is
`/home/claude/plans/2026-09-21-macmini-revision.md`; it supersedes
`/home/claude/plans/2026-09-20-wa-home-transport-plan.md` and its ADDENDUM where the two differ.

Four load-bearing facts in decisions 6 and 7 are wrong. Each correction below was read off the machine,
read-only; nothing was written, deployed or run there.

The CLI has no `decision edit`, so decisions 6 and 7 keep their superseded wording. Where they disagree
with this file, this one wins. Read the three together.

## Decision

1. **The handset was named in error, and the name we used is the busiest phone in the room.**
   The colleague's own code binds the names: `~/clinic-dispatcher/tools/farm_usb.py:55-56` —
   *"# huawei_p30_lite_02 (the Plus-account phone on the Mac mini cable). 01 = L2N4C19B14054874."* —
   then `DEFAULT_SERIAL = "L2N4C19B14054035"`. So `huawei_p30_lite_02` is serial **L2N4C19B14054035**,
   the ChatGPT Plus farm phone, under live USB automation. His WhatsApp code refuses that serial by
   construction: `~/wa-phone-outreach/apps/wa_phone/config.py:10
   FORBIDDEN_SERIALS = frozenset({"L2N4C19B14054035"})`, and `device.py` raises `WrongPhone` on it.
   The WhatsApp lane runs on **`huawei_p30_lite_01` = L2N4C19B14054874** (`config.py:9
   HUAWEI_01_SERIAL`). decision-6 item 2, decision-7 item 1 and item 5, and TASK-128 are **void on this
   point**: they pointed an agent at the colleague's live revenue lane. The "offline since 2026-09-19"
   signal that made `_02` look idle was reassignment, not availability — the on-device agent stopped
   when the farm moved to USB on 2026-09-20.

2. **"No new SIM, reuse the +49 number on `_02`" is void as written, and the sender identity is now
   blocking.** The number that wording referred to sits on a handset we cannot have. The only handset
   identity ever captured on either phone is for **…035** — a **+48 (Poland)** account, profile name
   **"Babu22"** (`phone_agent.sqlite task_f60da4c83e63`, 2026-08-07). The country code in decision-7,
   in the plan ADDENDUM item 4 and in `docs/whatsapp.md` was wrong.
   The MSISDN registered to WhatsApp on **…874** — the phone that actually runs the lane — is
   **recorded nowhere read-only on either machine**: their `store.py` schema has no self/me row,
   `config.py` holds serials and pacing but no account, and their `doctor` command prints model, IME,
   WhatsApp version, queue and lock and never a number.
   The sender identity on …874 is therefore **UNVERIFIED and blocking**. Two read-only settles, both
   needing a human: the Graph call
   `GET /v25.0/{META_WHATSAPP_PHONE_NUMBER_ID}?fields=display_phone_number,verified_name`, and asking
   the colleague to re-run his own proven read-only identify task against `huawei_p30_lite_01`.
   Until both answers are in hand **and differ**, nothing is pinned to the phone rail except Ivan's own
   test number, and no candidate is messaged from that handset. If the answer is Valentyn's personal
   number, the rail is dead. Whether we still need a SIM is **re-opened, not re-answered**. TASK-136
   carries this; TASK-128 is closed as obsolete.

3. **The executor is not built from scratch — it wraps an existing live lane.**
   `~/wa-phone-outreach/apps/wa_phone` is 1955 lines across 12 modules, committed 2026-09-21 by "Cursor
   Agent", cycling as a daemon since 2026-09-20 15:53: device layer, ADBKeyboard typing, chat-open by
   `smsto:` intent, a send-verify loop, notification and screen inbound, media pull, queue, drafts and
   pacing. Architecture is **Option A (WRAP)**: our own executor imports their `device.py`,
   `whatsapp.py` and `inbox.py` as a **driver library only**. Their brain, doctrine, queue, campaign,
   drafts and store are not used by us. Luna stays the brain on the VPS, `data/wa.sqlite` stays the
   source of truth, the remote machine moves fingers on one handset.
   Consequence for decision-6's "the executor behind the contract stays swappable": still true, but the
   thing behind it on day one is their code, not ours, and it is a disposable agent worktree
   (`gitdir: …/clinic-dispatcher/.git/worktrees/wa-phone-outreach`, branch `cursor/wa-phone-outreach-7972`)
   that a `git worktree remove` can swap under us. TASK-140 monitors that drift. TASK-112 (falsify the
   WhatsApp-Web `data-id` premise) is answered by the ground and closed: there is no WhatsApp Web here,
   the actuator is adb, and there is no message-id space at all.

4. **The machine is not a Mac and macOS supervision is void.**
   `uname -a` → `Linux macmini-worker1 6.8.0-139-generic … x86_64`; Ubuntu 24.04.5 LTS on Apple
   hardware. Every `launchd` / `LaunchDaemon` / `.plist` / `pmset` line in plan §5.8, in decision-7
   item 3 and in TASK-129 is void. The correct primitive is **`systemd --user` + `loginctl
   enable-linger`**, already proven in that account without sudo by the existing reverse-tunnel unit
   (`~/.config/systemd/user/macmini-reverse-tunnel.service`, `Linger=yes`). `pmset autorestart 1` has
   no Linux analogue: "does the machine power on after a power cut" becomes an Apple EFI question for a
   human at the site, and there is no UPS.

5. **The invariant is renegotiated, on this rail only.**
   "No `sent` without a confirmed provider message id" becomes **"no `sent` without a verified delivery
   tick"** for the phone rail. Their stack has **no message identifier of any kind** — a grep for
   `msg_id|message_id|wamid|@c.us|idempot` over the whole package returns one docstring hit — so the
   only id an outbound message has is the **`client_msg_id` we mint**. What they do have is a
   per-message delivery tick scraped off the bubble's `status` content-desc at send time
   (`whatsapp.py:27`: `'' | 'Gesendet' | 'Zugestellt' | 'Gelesen'`).
   A status of **`unverified` must never become `sent`** — it is a 504, uncertain, never auto-resent.
   Their own code treats it the other way (`whatsapp.py:141-142` "treating as sent (unverified)";
   `cli.py:111` then stores `fingerprint=None`, and NULLs are unlimited in a SQLite UNIQUE column),
   and it fired on **2 of 23** live sends. Refusing that at our boundary is one of the two reasons the
   wrap is worth owning.
   **`app/wa/meta.py:560-563` keeps its form unchanged.** The Meta rail still raises when the API
   answers 2xx with no id. This is a per-rail renegotiation, not a weakening of the Meta rule.
   Honest cost, stated not hidden: a tick is readable at send time but cannot be correlated afterwards.
   If the executor dies between pressing send and reading the tick, the only reconcile is a body scan
   of the chat — a heuristic dressed as a verdict, and a wrong `confirmed_absent` authorises a resend to
   a real candidate. That failure class does not exist on the Meta rail.

6. **Cold first contact stays on the phone rail. Ivan re-confirmed this on 2026-09-21**, after being
   shown the revision's case against it (the same 59 people on the staged shortlist already hold 2,159
   WhatsApp messages with our WABA number, so a "first touch" from an unidentified consumer number is a
   duplicate approach to warm contacts, and there is no opt-out mechanism on either side today).
   ADDENDUM item 2 and decision-6 item 4 therefore **stand**.
   Two hard prerequisites, both blocking **before any first touch on either rail**:
   - **Cross-lane suppression list and STOP detector first** (TASK-113 + TASK-137). Their stack has
     neither: a grep for `stop|opt.?out|suppress|abmeld|dsgvo|consent|einwillig` over their `*.py` and
     `*.json` returns two hits, one of them `log("daemon stop")`. Ours has a whole-word STOP detector
     (`app/wa/slots.py:97` → `brain.py`, `luna_brain.py`) but **no suppression table at all** — 13
     tables in `app/wa/store.py` and none of them is one. Suppression travels between lanes as salted
     sha256 digests, never as a plaintext do-not-contact list in a shared home directory.
   - **Pacing follows the mini-side constants, which are stricter than the ADDENDUM asked for**:
     `config.py:38-49` — 9–20 Europe/Berlin, 240–600 s log-uniform gaps between first touches, 10 first
     touches/day, 4/hour, 20–90 s reply think time, and `humanize.py:20-22` blocks Sunday. Adopt these
     as the floor; our side may only be slower. Two holes in their implementation are ours to fix, not
     inherit: the daily cap is global rather than per number and is counted on a UTC day while pacing
     runs Berlin, and quiet hours are applied to first touches only (an outbound reply was observed
     live at 07:53 Europe/Berlin).

7. **Surviving unchanged from decisions 6 and 7.** No Meta Coexistence (decision-6 item 1). A typed
   "ja" counts as documented consent and `WA_BRIDGE_SYNTHETIC_CONSENT` ships **ON** with the verbatim
   token stored in `wa_messages.meta` as the audit artefact (decision-7 item 2, TASK-122) — with the
   matcher running **server-side only**, never on the remote machine. The executor runs on the remote
   machine (decision-7 item 3). Buttons stay impossible and are replaced by numbered text (decision-6
   item 5). The Meta rail is not deleted and stays the rollback; rails stay pinned per thread.

8. **The risk acceptance in decision-7 item 4 is re-stated, not re-litigated — but it was accepted
   against a different machine.** §8.6 was accepted believing this was a private Mac in Ivan's home. It
   is the colleague's worker box. A root-installed **Cursor cloud agent worker runs remote-dispatched
   jobs as the same uid we log in as** (`cursor-worker.service`, `User=cursorworker1`, ~2.1 G RSS), and
   `cursorworker1` is in the `lxd` group with a `security.privileged: "true"` container already
   running — so "we have no sudo" is not isolation. There is no boundary between the two machines, only
   etiquette: that home holds an unrestricted root key into our VPS (`~/.ssh/config` → `Host hetzner /
   User root / IdentityFile ~/.ssh/hetzner_root`, no `from=`, no `command=`, no `restrict`) and their
   `farm_autopilot.py:568` already pipes Python into `ssh hetzner "… python3 -"`.
   Risk owner remains Ivan. The one genuine advantage of this direction survives the correction and is
   worth naming: **the executor holds no WhatsApp credential at all** — the account lives on the
   handset, and `META_WHATSAPP_APP_SECRET` never goes near that machine.

## Consequences

- **Closed as obsolete** (terminal status, history kept, reason in the task notes): TASK-112 (the
  WhatsApp-Web id premise is answered by the ground), TASK-128 (it prepares the ChatGPT farm phone),
  TASK-133 (a third model lane on an account shared with a cloud-agent worker).
- **Rescoped**: TASK-119 (contract doc: tick-not-id, 202-first, outbox pull), TASK-120 (`verified.tick`
  replaces `provider_msg_id` as the 200 requirement), TASK-124 (a consumer number has no Meta template,
  so it becomes the first-touch message set for the phone rail rather than a Graph replacement),
  TASK-127 (joint pacing on one handset at the mini-side constants), TASK-129 (systemd user units, and
  the tunnel flips to a single VPS-initiated `-R` leg under our own key), TASK-130 (the send path
  exists; we build the ledger, the deterministic key, the governor fuse and the refusal of
  `unverified`), TASK-131 (inbound exists; we build the Meta envelope, a collision-free fingerprint and
  content-addressed media), TASK-132 (the free-space alarm moves from the mini, which has 457 G at 4%
  used, to our VPS at 98%).
- **New**: TASK-134 (lane ownership, blocking, replaces TASK-112 at the head of the chain), TASK-135
  (freeze the staged 59-candidate shortlist, move the exporter into our repo), TASK-136 (device → serial
  → MSISDN map, blocking), TASK-137 (cross-lane suppression store and digest export), TASK-138 (restrict
  the mini's inbound root key on our VPS), TASK-139 (rewrite plan §5.8 for Ubuntu and correct
  `docs/whatsapp.md`), TASK-140 (dependency-drift monitor on their three driver modules), TASK-141 (the
  coexistence agreement with the colleague, plus the bug reports as gifts).
- **TASK-113 and TASK-137 are blocking for any campaign on either rail.** Not a milestone, a gate.
- **Our shipped documentation was wrong in production-visible ways** and is corrected under TASK-139:
  the rail table said the phone rail yields no delivery status (it yields ticks, scraped at send time,
  with no webhook), the handset name pointed at the farm phone, and the sender number was written as
  +49.
- **Nothing is deployed to the remote machine by this decision.** Six things there need the colleague's
  yes before any of it runs: the flock on Huawei 01, a directory and a user unit in his account, his
  daemon staying reply-only to his own test number until we agree otherwise, freezing his staged
  shortlist, re-running his identify task on `_01`, and read-only sqlite access for reconciliation.
  We hold no veto on any of them (TASK-141).
- **One entry condition that is a sequencing problem, not a design one**: two automated senders on one
  consumer account is the one thing neither side can observe from its own side. We do not start until
  his daemon is reply-only to his own test number — never bare `--auto-reply`, never
  `--send-first-touches`.

## Alternatives

- **Defer cold outreach to the Meta rail and ship the phone rail reply-only in v1.** This was the
  revision's own recommendation and the largest free risk reduction available: the rail's honest v1
  value is `requires_freeform_window = False`, i.e. a thread whose 24 h Meta window closed becomes
  answerable again, and that needs no campaign traffic at all. **Offered to Ivan on 2026-09-21 with the
  2,159-prior-messages evidence, and declined.** Cold first contact stays on the phone rail, gated on
  suppression plus STOP and on the mini-side pacing constants.
- **Adopt their lane outright (Option B) and contribute upstream.** Rejected: value requires a merge
  into `ukrainebz1-arch/clinic-dispatcher`, where we have no identity and no channel (`gh auth status`
  → not logged in; root's `gh` is the colleague's account with `push: false`). Its retry path also
  drops turns silently — `cli.py:256` classifies retryable only on four substrings, so a rail failure
  is logged `brain silent` and the turn is dropped, on a link measured at 11.4% retransmit — and
  `brain.reply` runs inside the phone lock, so a 180 s Luna turn would hold the handset for 180 s.
- **Federate: two lanes, two brains, one handset (Option C).** Rejected on candidate experience: it lets
  two lanes message one person by design ("reply is allowed unless suppressed"). Its per-number
  lane-claim and salted-digest suppression ledger are adopted into TASK-137 regardless.
- **Build our own actuator from scratch as originally planned.** Rejected: ~14–18 days, most of it the
  actuator that already exists and already sends.
- **Keep `huawei_p30_lite_02`.** Not an option: it is the ChatGPT farm phone, under live automation,
  and their own WhatsApp code raises `WrongPhone` on that serial.
