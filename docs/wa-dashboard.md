# Leads — the WhatsApp view in Pro

**What:** `/pro#/leads`. The leads that need a human come first. The status of every other card is listed below them.
**Data:** the WhatsApp harness (`app/wa/*`, see [whatsapp.md](whatsapp.md) once PR #1 lands). The board does not
own that data. It proxies `/api/wa/*` server-side to the harness. The harness serves a token-gated read API, and the
board holds `WA_API_BASE` / `WA_API_TOKEN` (topology B, Ivan 2026-09-29). The token never reaches the browser.
**Access:** owner only. `/api/wa/threads` (which also covers `/threads/{id}` and `/threads/{id}/messages`) is in
`OWNER_READ_PREFIXES` in `app/auth.py` (PR #1), and no agent-key scope opens it. `/api/wa/health` is owner-only too
(wa-harness, 2026-09-30).
**Status:** phase 1, read-only. There are no writes (reply, pause, note) until Ivan asks for them.
**Code:** `web/pro.template.html` (`pageLeads`, `WA_*`), demo data in its `mockApi` (`?mock=1#/leads`).

## What "needs a human" means

The view invents no reason of its own. It sorts and shows what the harness already names:

| tier | badge | source (harness) | counted |
|---|---|---|---|
| escalated | **MANAGER** (red) | `escalation_codes`: `card._escalation_codes`, the closed list in `app/wa/luna/escalation.py` (Ivan 2026-09-22). A code outside the list is shown raw, never hidden. `lead_status.status == "red"` (TASK-316) lands here too | yes |
| reply stuck | **ANTWORT NÖTIG** (amber) | `stuck_reply` (ball on us longer than `WA_STUCK_REPLY_HOURS`), `last_send_error` (`wa_send_failures`), `pending_inbound` (`wa_inbound_pending`) | yes |
| handoff | **ÜBERGABE** (sky) | `handoff.status` `queued` (the anonymised profile waits to be sent) or `attention` (a clinic target needs us), or funnel stage `submitted` with no `handoff` yet. `in_progress`, `signed` and `closed` wait for nobody; the stage label names them ("Übergeben · unterschrieben"). An unknown status counts and is shown raw | yes |
| check | **HINWEIS** (amber outline) | `flag_codes` (`card._flag_codes`: worth a look, never pulls a human in), any non-green, non-red `lead_status` | **no** |
| ended | grey | `stopped` (STOP), `suppression` (do-not-contact, any rail), `outcome` declined / already_placed / not_placeable | no |
| green | **LUNA AKTIV** | none of the above | no |

The `lead_status` rows are provisional: TASK-316 has not named its statuses yet, and the mapping gets settled
when it does. A stopped or suppressed thread never counts, whatever else is on its card: nobody may write to it. Test threads
(`is_test`) are hidden by default and never counted. The count of the first three tiers is the red badge on the
Leads nav item on every Pro page, and it is also shown in the tab title.

A Luna reply refused by `WA_REPLY_SCOPE=test_only` is stored as `kind: draft`. It is shown as a dashed draft bubble
and explained in the health strip. It is not a reason by itself, because a non-owned thread is answered by the other
system (`/api/wa/ownership`).

## Every card in conversation order

Green only says that nobody is needed. It says nothing about how far a lead has got (Ivan 2026-09-30). So every lead
shows its card as one line, in the needs-a-human list and in the board. The line follows the harness's own gate order
(`luna_brain.py` `requirement_scoreboard` and `_OBJECTIVE_ORDER`): region, qualification, town or department,
housing, CV, certificate (Urkunde or Defizitbescheid), consent.

- A satisfied gate shows what we know, for example `Bayern`, `Defizitbescheid`, `München · Intensiv` or
  `flat for 2 · or without`. The CV, certificate and consent gates have no value to show, so a satisfied one shows its
  own name with a green square.
- The current step is the first gate that is not satisfied. `funnel_stage` names it, and Luna asks about it next. The
  view outlines it. It can already carry part of the answer: `flat yes, for how many?` (`housing_needed` is true and
  `people_count` is still open) or `consent asked` (`anonymous_send_offered`).
- The gates after the current step are dim. A blocked gate (`qualification_path: reject`) is red. An ended thread has
  no current step.
- Within one status the board lists the lead that has got furthest first, so the green leads run from consent down
  to first contact.

The side panel shows the same facts next to each gate's name, with the stage stepper and Luna's `next_objective`.

## Refresh

Polling only, because Pro has no SSE or WebSocket. The list and the health strip refresh every 15 s while the view
is open. The open thread polls `after_id` every 5 s. The nav badge refreshes every 60 s on every other Pro page. A
failed refresh keeps the last data on screen under a red line that names the error and the time of that data. It is
never replaced by an empty list.

## Contract (what the view reads)

Every time is ISO-8601 with an offset. A missing optional field is `null` or absent, and the view shows nothing for
it (never a guessed default). The raw phone number never appears in any response, only `phone_masked`: last 4 digits
visible, e.g. `"+43 ••• •••• 1234"` (Ivan 2026-09-29). `thread_id` is opaque and stable per phone, and it is the
only key in URLs.

### `GET /api/wa/threads?include_test=1&limit=&offset=`

The board's list envelope (`app/data.py:page`), with no maximum page size. The view follows `next_offset` until it is
`null`.

```json
{"total": 27, "limit": 500, "offset": 0, "next_offset": null, "test_threads": 1,
 "generated_at": "2026-09-29T14:32:05+00:00", "source": "harness@tasker-dispatcher-01",
 "synced_at": "2026-09-29T14:31:40+00:00", "synced_source": "bridge",
 "rows": [ThreadRow]}
```

`ThreadRow`:

| field | type | from |
|---|---|---|
| `thread_id` | string | opaque, e.g. an HMAC of the canonical phone |
| `phone_masked` | string | last 4 digits visible |
| `is_test` | bool | `wa_threads.is_test` |
| `rail` | `meta` \| `bridge` \| null | `wa_threads.rail` |
| `opened_at`, `last_inbound_at`, `last_outbound_at` | time \| null | `wa_threads` |
| `turns` | int | `wa_threads.turns` |
| `ball` | `us` \| `them` \| `silent` \| `none` | `luna/reporting.py:ball_for` |
| `stage` | `contact` \| `qualification` \| `matching` \| `cv` \| `documents` \| `consent` \| `submitted` | `luna_brain.funnel_stage` |
| `stage_since` | time \| null | `requirement_scoreboard().stage_since` |
| `outcome` | `declined` \| `already_placed` \| `not_placeable` \| null | `luna/reporting.py:stage_for`, the terminal labels only |
| `gates` | object: gate → `satisfied` \| `open` \| `blocked` | `requirement_scoreboard`, keys `region`, `qualification`, `city_or_department`, `housing`, `cv_document`, `qualification_document`, `handoff_consent` |
| `card` | object | a safe summary of `wa_threads.slots`: `region`, `city` (a string or a list), `department` (`card.department_pref`, passed raw; the view shows `"flexibel"` as any department), `qualification_path`, `housing_needed`, `people_count`, `housing_flexible` (would also take a clinic without a flat), `anonymous_send_offered` (the consent question has gone out), `campaign` (a campaign_id or null), `match_branch`. Never `cv_text` or any document text |
| `stopped`, `stopped_reason` | bool, string \| null | `wa_threads` |
| `suppression` | `{reason, lane, at}` \| null | `suppression.py` (no `trigger_text` in the list) |
| `escalation_codes`, `flag_codes` | string[] | `card._escalation_codes`, `card._flag_codes` |
| `escalated_at` | time \| null | first escalation, if the harness records it. Without it the view says "last message X ago", not "since" |
| `stuck_reply` | bool | `api.py:_is_stuck` + pending age |
| `pending_inbound` | `{count, oldest_recorded_at, last_error}` \| null | `store.pending_inbound_summary` |
| `last_send_error` | `{error, at}` \| null | `store.recent_send_failure` |
| `handoff` | `{status, consented_at, clinics, targets}` \| null | `wa_queue_candidates`. `status` is `queued` \| `attention` \| `in_progress` \| `signed` \| `closed`, and only `queued` and `attention` need a human. `clinics` is the matched count (the detail's `handoff_matches` lists them). `targets` is the status each clinic reported back once the profile went out: `{clinic_id, clinic_name, external_ref, status, ts, attention}`. `status` is a closed list: `sent_to_clinic`, `followup_sent`, `clinic_replied`, `interview_scheduled`, `trial_scheduled`, `offer`, `contract_signed`, `declined`, `closed`, `halted`. `attention` is a bool: a reply, interview, trial, offer or halt with no later `contract_signed`, `declined`, `closed` or `sent_to_clinic` |
| `last_message` | `{direction, kind, preview, at}` | newest `wa_messages` row, `preview` ≤ 140 chars; a deleted row gives `kind: "deleted"` and no preview |
| `lead_status` | `{status, reason, at}` \| null | TASK-316 (P4). `null` until it ships |

### `GET /api/wa/threads/{thread_id}`

```json
{"thread": ThreadRow,
 "escalation_notes": ["explicit_human_request: asked to talk to a person about the contract"],
 "flag_notes": [],
 "next_objective": "ask for the still-missing Defizitbescheid as a photo/PDF (...)",
 "documents": [{"id": 12, "kind": "document", "document_type": "lebenslauf", "mime_type": "application/pdf",
                "size_bytes": 183200, "received_at": "...", "reuse_state": null}],
 "send_failures": [{"error": "...", "at": "..."}],
 "handoff_matches": [{"clinic_id": "16100", "clinic_name": "...", "town": "...", "score": 87}],
 "synced_at": "...", "synced_source": "bridge"}
```

`escalation_notes` / `flag_notes` are `card._escalate_reason_notes` / `card._flags_notes`. `next_objective` is the
internal English hint from `requirement_scoreboard`. It is shown as internal, never as candidate text. `documents`
carries metadata only: no bytes, no path, no extracted text.

### `GET /api/wa/threads/{thread_id}/messages?limit=50&before_id=|after_id=`

```json
{"rows": [{"id": 311, "direction": "in", "kind": "text", "body": "...", "at": "...", "deleted": false,
           "status": null, "meta": {}}],
 "next_before_id": 290}
```

Rows are in ascending `id` order. Without a cursor the call returns the newest `limit` rows. `before_id` pages
older rows, and `next_before_id` is `null` at the start of the thread. `after_id` returns everything newer, which is
the 5 s poll. `kind` is one of `text`, `audio` (the body is the transcript; `meta.transcript` only marks it as one,
and before transcription the body is empty),
`buttons` (`meta.buttons` holds the titles), `draft` (`meta.scope_refusal` holds the reason), `document`, `image`,
`template` (`meta.template` names it). `meta` carries only `buttons`, `scope_refusal`, `action`, `template` and
`transcript`.
An unknown kind is shown with its raw name. `deleted: true` is a tombstone with `body: null`. `status` is the latest
delivery status of an outbound row (`sent`, `delivered`, `read`, `failed`), and there is no `wamid`.

### `GET /api/wa/health`

This is the harness's own `/wa/health` (`config.readiness()` + `rails`), proxied unchanged and owner-only. The view
reads `webhook_ready`, `outbound_ready`, `checks`, `reply_scope`, `autosend`, `transport`, `brain`, `rails`, and
`synced_at` / `synced_source`: the engine's last confirmed contact with a rail, shown as "Rail contact 2 min ago ·
bridge". The list envelope and the thread detail carry the same two fields.

## Errors the view distinguishes

| answer | what the view says |
|---|---|
| 404 on `/api/wa/threads` | this board is not connected to the harness yet (the proxy is missing) |
| 401 / 403 | owners only |
| 502 / 503 / 504 | no connection to the harness, with the proxy's message: the harness is down or timed out, it rejected the board token (502), or `WA_API_BASE` is not set on the board (503, `app/wa_proxy.py`) |
| anything else | the status and the message |

Each of these replaces the view with an error card and a link to the demo. None of them renders as "0 leads".
