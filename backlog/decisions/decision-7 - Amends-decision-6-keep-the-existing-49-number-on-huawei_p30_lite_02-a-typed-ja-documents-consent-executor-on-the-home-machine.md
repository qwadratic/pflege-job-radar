---
id: decision-7
title: >-
  Amends decision-6: keep the existing +49 number on huawei_p30_lite_02; a typed
  ja documents consent; executor on the home machine
date: '2026-09-21 02:57'
status: accepted
---
## Context

decision-6 was filed 2026-09-21 01:18, before Ivan answered three questions it had left open. His answers
are recorded in `/home/claude/plans/2026-09-20-wa-home-transport-plan.md`, section "ADDENDUM — decisions,
2026-09-21 (Ivan)" (appended 01:30, i.e. after decision-6). This decision ratifies that addendum. Where the
two disagree, this one wins; decision-6 stays as the record of what was decided first and why.

The CLI has no `decision edit`, so decision-6 keeps its superseded wording ("A NEW SIM and a new number",
"Open question, NOT decided — needs a legal call"). Read it together with this file.

## Decision

1. **No new SIM.** The bot sends from the +49 number already on `huawei_p30_lite_02`. That number is a
   candidate-role persona ("Babu22") from the colleague's August soak tests, not the WABA number, so
   decision-6's constraint ("the Valentyn NDT number cannot move") is untouched — Valentyn NDT stays on the
   Cloud API as the rollback rail.
2. **A typed "ja" documents consent.** `WA_BRIDGE_SYNTHETIC_CONSENT` ships ON (TASK-122), with a tier-1
   ordinal or tier-2 exact-title match plus a confirmation turn, and the verbatim typed token stored in
   `wa_messages.meta` as the audit artefact. Ivan owns this call; the UWG/DSGVO exposure was stated to him
   and is unchanged. Without it the funnel's close step is unreachable, because the phone rail has no buttons.
3. **The executor runs on the home machine**, per plan §5.8 (home-initiated ssh, `-R` for tasks in, `-L` for
   inbound out — the `-L` leg is what satisfies `router.py:_is_local_caller`). The alternative, running the
   linked-device session on tasker-dispatcher-01 and dropping the home machine entirely, was offered and
   declined.
4. **The credential risk on that machine is accepted as-is** (Ivan, 2026-09-21). The linked-device session
   reads every candidate thread and can send as us, on a machine that also holds an unrestricted root key
   into this server. No isolated account, no separate machine. Risk owner: Ivan. Not to be re-litigated.
5. **The handset is ours to use**: the colleague agreed on 2026-09-21. It stays registered in his phone-agent
   registry, so "one device, two dispatchers" is still a live hazard until his service stops targeting it.

## Consequences

- M3 changes from "procure SIM + handset" to "prepare the existing account": confirm the account is alive and
  unrestricted (the 2026-08-08 ban-check tasks were cancelled with no result — health is unknown), re-identify
  the profile away from "Babu22", set a two-step PIN **with a recovery e-mail**, fix sleep/battery/autostart,
  decide the fate of the persona chats. TASK-128 carries this; its ACs were rewritten accordingly.
- TASK-122 ships the consent flag on by default. Its AC that "off means no typed reply can set consent" stays:
  that is still the behaviour the flag must guarantee, it is simply no longer the shipped default.
- The account's unknown history is now a project risk, not a procurement question. A banned or restricted
  number is discovered at M3, before anything is built on top of it — TASK-128 is a prerequisite, not paperwork.
- Nothing in decision-6's other content changes: Coexistence stays rejected, cold outreach still moves to the
  phone with warm-up pacing (TASK-127) and the suppression list (TASK-113) blocking before any campaign.
