---
id: decision-6
title: 'WhatsApp transport: phone rail on a new number; Meta Coexistence rejected'
date: '2026-09-21 01:18'
status: accepted
---
## Context

Today the WhatsApp harness (`app/wa/`) talks to exactly one transport: `app/wa/meta.py`, the Meta
WhatsApp Cloud API, on the "Valentyn NDT" number. Ivan wants WhatsApp off Meta and onto a phone rail:
a home machine plus a real handset, driving a genuine WhatsApp client.

Full investigation, contract and milestones: `/home/claude/plans/2026-09-20-wa-home-transport-plan.md`.
Key findings that shaped this decision:

- The "new home link" is not a WhatsApp agent. It is a Cursor mac-mini SSH farm (root key
  `macmini-worker1`, added 2026-09-17, 4180 logins, no `from=`/`command=`/`restrict`, sshd LogLevel
  unset). The WhatsApp-capable link on port 8791 is the colleague's, two months old, and hard-disabled
  by his own `CONSUMER_WHATSAPP_MODE = "manual_only"` flag. Not ours, and the wrong tool anyway.
- Reply buttons and list messages are Cloud API features. Neither the consumer app nor the free
  WhatsApp Business app can compose one. On a screen-automated handset buttons are not unimplemented,
  they are **impossible**. Restoring them on a linked-device library needs `native_flow` +
  `biz_bot: '1'` binary-node injection, i.e. spoofing business traffic on the one number whose
  survival is the point.
- Our only consent gate today is set exclusively by a genuine button tap
  (`app/wa/luna_brain.py:988-991`). Going text-only degrades both brains.
- Meta Coexistence (same number on the Business app AND the Cloud API, GA May 2025) would have kept
  real buttons, templates, delivery statuses, webhooks and an appeals path, left `meta.py` as the
  transport, and deleted ~90% of the plan.

## Decision

Ivan decided on 2026-09-21:

1. **Phone rail, not Meta Coexistence.** Coexistence is **rejected**. WhatsApp moves onto a home
   machine plus a real handset, behind one HTTP contract (`/v1/*`) so the executor behind it stays
   swappable.
2. **Handset: `huawei_p30_lite_02`** — the idle one. Not `huawei_p30_lite_01`: that one runs the
   colleague's ChatGPT lead research at ~77k requests/day.
3. **A NEW SIM and a new number.** The "Valentyn NDT" number cannot move. It is Cloud-API-registered,
   so it cannot run in the consumer app; it cannot be deleted within 30 days of a paid send; and
   taking it off the API destroys the colleague's 29 APPROVED `recruitment_*` templates plus the live
   nginx webhook at `/candidate-action/webhooks/meta/whatsapp`.
4. **Cold first-contact outreach moves to the phone too.** This reverses the plan's own risk-1
   mitigation, which had cold campaigns staying on the Cloud API permanently as the single largest
   available risk reduction.
5. **Buttons are replaced by numbered-text replies.** Text-only from line one. `native_flow` button
   injection stays rejected: a ban is the one unrecoverable failure, transient flaps are recoverable.

## Consequences

**What rejecting Coexistence costs us, explicitly:**

- **No buttons.** Every question slot built by `app/wa/brain.py:106-141` and every `CONSENT_BUTTONS`
  offer degrades to numbered text plus a server-side matcher (ordinal -> folded exact title -> unique
  title prefix, with a small German keyword map for two-button offers only).
- **No templates.** No Graph template lookup, no approval status, no 24h-window reopen template. A
  local template store replaces Graph lookup so campaign validate/render discipline survives.
- **No delivery statuses from Meta.** Statuses become tick-state read off the screen, reported with
  our own `wab-*` error slugs. Forging Meta numeric codes is rejected as provenance forgery into a
  table whose reader documents itself as "The latest Meta delivery status".
- **No ban appeal.** A consumer number can be permanently banned with no appeal. Enforcement arrives
  in non-deterministic waves; the dominant trigger is recipient feedback, not raw volume. Ivan accepts
  this risk.

**What moving cold outreach to the phone costs us:**

- It is now the largest single risk in the project. Cold first contact is exactly the traffic pattern
  that draws blocks and reports, and blocks/reports are the dominant ban trigger.
- It forces rail-aware warm-up pacing and a per-number daily cap that does not exist today: 1 send per
  90s with jitter, <=12/hour, <=20 first contacts/day rising to <=50/day over four weeks, against
  today's ~50/hour. Enforced twice: as campaign flags on the server and as a non-overridable fuse on
  the home machine that a server bug cannot open.
- A STOP-word detector plus a persistent suppression list becomes **blocking before any campaign**.
  Opt-out is today learned only from Meta error code 131050 (`app/wa/luna/campaign.py:378-395`), which
  a phone rail can never produce, so moving transports without it silently deletes our only opt-out
  detector. Legal minimum: § 7 Abs. 3 UWG and § 174 TKG both require a free, easy opt-out.
- Legal exposure under § 7 Abs. 2 Nr. 2 UWG / § 174 TKG does not improve on a phone; it worsens.
  OLG Hamm 18 U 154/22 (03.05.2023) expressly covers WhatsApp; AG Düsseldorf 23 C 120/25 (20.11.2025)
  holds that a platform connection is not consent.

**Other consequences:**

- The Meta rail is not deleted. `meta.py` stays live, `META_WHATSAPP_*` stay set permanently, and the
  Meta rail is the rollback path. Rails are pinned per thread on `wa_threads.rail`, set once and never
  changed, because the two rails are two different sender numbers: switching mid-thread means the
  candidate sees a stranger answering. Moving a live thread between rails therefore needs an explicit,
  human-approved "number changed" bridging message.
- The new number needs two-step verification with a PIN **and** a recovery email, both stored where
  they are findable at 3am. A lost PIN is a hard 7-day outage with no way to expedite.
- Add `/opt/clinic-dispatcher/data/private/handover_exports/**/whatsapp/threads/` and
  `/opt/clinic-dispatcher/data/private/diagnostics/` to the PII no-go list. Both hold named
  candidate/clinic content and a broad grep reaches them.

**Open question, NOT decided — needs a legal call, not an engineering one:**

- **Is a typed "ja" acceptable as documented consent** for forwarding a candidate profile to a clinic,
  or must the consent turn happen on the Meta rail with a real tap? Today the gate is tap-only by
  design (TASK-80). Until this is answered, synthetic consent from typed text stays behind
  `WA_BRIDGE_SYNTHETIC_CONSENT`, default **off**, and no typed reply can set `anonymous_send_consent`.
  If it is ever turned on it requires a tier-1 or tier-2 match plus an explicit confirmation turn, and
  the verbatim typed token is stored in `wa_messages.meta` as the audit artefact. A prefix or keyword
  match never sets consent.

## Alternatives

- **Meta Coexistence.** Dominant on capability: real buttons, templates, statuses, appeals path,
  `meta.py` untouched, M2-M12 cancelled. Rejected by Ivan 2026-09-21. Constraints that would have
  applied: 20 mps fixed throughput; Business app >= 2.24.17; blue-badge Official Business Accounts not
  supported; Calling API not supported; all companion devices unlinked at onboarding; the app must be
  opened at least every 13 days.
- **Keep cold first-contact on the Cloud API, conversations on the phone.** The plan's own
  recommendation and its largest free risk reduction. Rejected: cold outreach moves to the phone.
- **Move the Valentyn NDT number.** Impossible without destroying 29 approved templates and the live
  webhook; a Cloud-API-registered number cannot run in the consumer app.
- **Baileys / linked-device library with `native_flow` buttons.** Restores real buttons by injecting
  `biz_bot: '1'`, the loudest possible "not a human client" signal. Rejected.
- **Angle C, adb UI automation as the v1 actuator.** Lowest protocol-level ban risk (genuine app,
  genuine device, credential never leaves the handset) but worst actuator reliability by its own
  evidence and no prior art for the inbound half. Kept as the documented fallback actuator if the
  WhatsApp Web `data-id` premise dies.
- **Reuse the colleague's port-8791 phone agent.** Right shape, wrong tool: hard-disabled by his own
  safety flag, no inbound path, no batch endpoint, no idempotency, planner blocked on his agent loop,
  and the phones hold candidate personas rather than our business number. We reuse its ideas, not its
  instance.
