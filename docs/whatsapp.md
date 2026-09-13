# WhatsApp — answering inbound Pflege leads

**What:** a candidate messages from a Meta ad on WhatsApp. Valentina replies, asks **one** question per message, and once the search is narrow enough names real open postings from exactly the data `GET /api/jobs` serves — clinic, city, link to the original ad.
**Where:** `app/wa/` (router `/api/wa/*`), state in `data/wa.sqlite`, unit `deploy/pflege-wa.service`.
**Status:** minimal harness. Answers inbound messages, prepares handoff to a human. Sends **nothing** to clinics, collects no documents, and without `WA_AUTOSEND=1` nothing reaches Meta at all — replies land as `draft` in the database.

Modeled on the production bot at `tasker-dispatcher-01` (`/opt/clinic-dispatcher/apps/connectors/`): same Meta Cloud API, same env names, same persona (internally "Luna", in-chat **Valentina**), same style rule (short bubbles, one question per turn), same qualification bar (Urkunde / Defizitbescheid / passed Kenntnisprüfung). New here: questions come from live board data instead of a fixed script, and the deterministic branch below has no LLM call at all — testable, not just plausible.

## The conversation

No script — the harness fills **slots**, each slot a board filter:

| Slot | Filter in `GET /api/jobs` | Read from |
|---|---|---|
| `role` | `role_class` | "examinierte Krankenschwester", "OTA", "Stationsleitung" |
| `city` / `bezirk` | `city` / `regierungsbezirk` | town names the board actually knows |
| `department` | `department_hint` | "ITS", "OP", "Kreissaal" … (production bot's alias list) |
| `hours` | `employment_types` | "Vollzeit", "75%" |
| `housing` | `housing=1` | "brauche eine Wohnung" |
| `urkunde` | — | Urkunde, Defizitbescheid, Kenntnisprüfung; **not** a filter, the qualification bar itself |

**Data decides the next question.** For every open slot, `app/wa/brain.py:_gain` scores how much an answer would narrow the remaining rows: coverage × (1 − share of the most common value). "Intensiv or OP?" is useless when 90% of rows are already Intensiv; at 40/35/25 it halves the list. Highest-gain slot gets asked; below `MIN_GAIN`, the harness stops asking and just delivers.

So question order isn't fixed — it follows the live board. On real data (3625 open postings, 2026-09-11), city goes first: 406 postings are in München, so city narrows the most.

More rules, all in `app/wa/brain.py`:

- **Two bubbles, one question.** `_check()` raises if a turn has more — the style rule is an assertion, not a suggestion; it fails in tests, not in chat.
- **Numbers, not filler.** Every question carries the current count: "14 Stellen passen bisher."
- **Buttons from live data.** The three most common values become Meta reply buttons (max 3, titles ≤ 20 chars); free text always works too — typing "Stroke Unit" isn't limited to the suggestions.
- **One message can fill several slots.** "Intensiv in Würzburg, Teilzeit" fills three at once; none of those three get asked again.
- **Last statement wins.** "eigentlich lieber Augsburg" corrects the city. A bare "ja" answers only the question just asked — never one from four turns ago.
- **Nothing open isn't silence.** No posting matches → drop the loosest preference (housing → full/part-time → department → city) and **say so**: "Für OP in Augsburg, Vollzeit, mit Wohnung ist nichts offen – ohne Wohnungs-Wunsch: …". Still nothing → the harness says that plainly too.
- **No offer without recognition.** No Urkunde, Defizitbescheid, or passed Kenntnisprüfung → nobody gets introduced to a posting (same as the production constitution). "Not passed" is read even if nobody asked.
- **STOP means STOP.** "stop"/"stopp"/"abmelden"/"löschen" → `wa_threads.stopped`; nothing goes out after. The inbound message itself is still stored — it's the opt-out's own record. Whole-word match only, so "Stopfen"/"Intensivstation" don't fire.
- **Files.** A CV/Urkunde attachment gets acknowledged ("can't read this yet"), never silently swallowed — the deterministic branch has no document pipeline.

A real (trimmed) transcript:

```
<< Hallo
>> Hallo, hier ist Valentina – ich bin die digitale Assistentin …
>> Wo möchten Sie arbeiten? Am meisten offen ist gerade in München, Neuburg/Donau, Augsburg.   [München] [Neuburg/Donau] [Augsburg]
<< Intensivpflege
>> 388 Stellen passen bisher.
>> Wo möchten Sie arbeiten?                                                                     [München] [Erlangen] [Augsburg]
<< München
>> 31 offene Stellen passen: Intensiv/IMC in München.
   • Klinikum Dritter Orden, München — Pflegefachkraft Intensivpflege …
     https://…
>> Haben Sie die deutsche Berufsurkunde, einen Defizitbescheid, oder die Kenntnisprüfung bestanden?
```

## Transport

Meta WhatsApp Cloud API, Graph `v25.0`. Signature and challenge checks are lifted from the production client — they're the whole trust boundary.

| Route | Auth | What |
|---|---|---|
| `GET /api/wa/webhook` | Meta's `hub.verify_token` | handshake, echoes `hub.challenge` |
| `POST /api/wa/webhook` | `X-Hub-Signature-256` (HMAC-SHA256 over the raw body) | inbound messages |
| `GET /api/wa/health` | public | readiness, no secrets |
| `GET /api/wa/threads` | owner session | threads with slots, `?phone=` for history |
| `GET /api/wa/ownership` | owner session | which system owns a phone's conversation (TASK-75) |

POST order, each step with its reason: verify signature → compare `phone_number_id` (a webhook for a second number is ignored, not answered) → `INSERT` on `wa_messages.wamid` (UNIQUE, so a Meta redelivery stops right here) → decide the reply (no network, no writes yet) → send → record the outbound. A Meta error is **not** swallowed: the route answers 502, no sent message lands in the database, and Meta's own redelivery actually gets an answer.

`GET /api/wa/threads`/`GET /api/wa/ownership` are owner-only (`app/auth.py:OWNER_READ_PREFIXES`) — a phone number and what someone said about themselves is the most personal data in this repo.

## Operations

```bash
# Env (same names as the production bridge, so one Meta app covers both)
META_WHATSAPP_APP_SECRET=…        # required: webhook signature
META_WHATSAPP_VERIFY_TOKEN=…      # required: handshake
META_WHATSAPP_ACCESS_TOKEN=…      # required to send
META_WHATSAPP_PHONE_NUMBER_ID=…   # required to send, also filters foreign webhooks
WA_AUTOSEND=1                     # without this: everything is stored as draft only

.venv/bin/uvicorn app.wa.asgi:app --port 8502      # own process (deploy/pflege-wa.service)
curl -s localhost:8502/api/wa/health
.venv/bin/python -m pytest -q tests/test_wa_harness.py
```

Register the webhook with Meta: `https://<host>/api/wa/webhook`, field `messages`, verify token as above.

Two doors, one implementation: `app/main.py` mounts the same router (port 8501) so the harness runs locally without a second process. In production, nginx points at **one** of them — the dedicated process, so a lead doesn't wait behind a crawl snapshot. Both write `data/wa.sqlite` (WAL); only whichever one receives webhooks actually writes.

**Conversation ownership (TASK-75):** `app/wa/routing.py`. Idea: route new leads to this harness, leave existing ones with the real production system, except when we reopen an old conversation ourselves via our own reopen template (TASK-70) — that one explicit event hands ownership over from then on. `route_decision(conn, phone)` returns `'us'`/`'them'`: once decided, it stays that way permanently (only `flip_to_us_on_reopen()`, wired into `app/wa/api.py:_send_reopen_template`, may ever change it). For an undecided number, `_is_known_to_real_system()` checks whether the real system already knows it — a deliberately generic, `WA_REAL_SYSTEM_PHONES_FILE`-configured text-file check (same restraint as `external_contacts.py`, TASK-69: no concrete system named here). Unconfigured means a loud error, not a guess — being wrong in either direction has a real cost. Readable via `GET /api/wa/ownership` (owner-only, same PII class as `/api/wa/threads`). **Deliberately missing:** an actual router in front of Meta's webhook that consults this table live — that's a production-infrastructure change needing its own sign-off with the real system's team, not part of this task.

## Second brain: same persona, same rules, Claude instead of ChatGPT

`WA_BRAIN=luna` switches to `app/wa/luna_brain.py` — same transport layer, same `data/wa.sqlite`, but the reply now comes from Claude instead of the question ladder above. Persona, qualification gate, region boundary (Bavaria only), and live-market logic are lifted from the production implementation with company references stripped (this repo is public, the source is private/company-bound — `app/wa/luna/VENDORED.md` lists exactly what was kept, generalized, or dropped).

**Decided in code, never by the model:**

- **STOP** never reaches the model — same as the deterministic branch.
- **Not placeable** (Pflegehelfer, training with no recognition path, failed Kenntnisprüfung): the first decline is the locked German text (`app/wa/luna/prompts.py:REJECT_BODY_DE`), never the model's own phrasing — the source's own "process-exact wording the model must not rewrite" rule.
- **A named Bundesland outside Bavaria** triggers the locked decline (`OUT_OF_SCOPE_REGION_DE`) — the board has no data for other states.

Everything else — tone, which question comes next, how the market snapshot is phrased, when to escalate — is Claude's call, from state `app/wa/luna_brain.py` hands it: the card (`card`, the equivalent of the source's `card_patch`), a `requirement_scoreboard` (state, not a script), and a `market_snapshot` from the exact filters `GET /api/jobs` also uses (`app/wa/brain.py:jobs_for`) — no second data path. The thread itself is never handed over as text anymore — it lives in the session (next section).

**One Claude Code session per WhatsApp number, not a stateless call per turn.** On first contact, `app/wa/luna_brain.py:Client` starts `claude -p --session-id <uuid>` and keeps the UUID on the card (`card._session_id`); every later turn for that number calls `claude -p --resume <same uuid>`. The inbound WhatsApp message becomes the real `user` message of that session, exactly like an interactive chat — Claude sees prior turns from the session itself, not a hand-built thread field. What's still resent fresh every turn is only what can change independent of the conversation: current card state, the requirement scoreboard, and the market snapshot (new postings appear, old ones close) — Claude can't "remember" that, every turn needs it fresh. `--resume`/`--session-id` only find a session again if `claude` runs from the **same working directory** it started in — so every call runs with a fixed `cwd=WA_LUNA_SESSION_DIR` (`data/wa_luna_sessions/`, default), regardless of the FastAPI process's own cwd.

**Call shape: the `claude` CLI, not an Anthropic SDK key.** `app/wa/luna_brain.py:Client` runs `claude -p --restricted --output-format json --system-prompt "…"` (full-replacement system prompt, not appended; `--restricted` strips bash/code-execution/WebFetch, none of which a chat reply needs anyway) and sends the payload over stdin. Rides whatever Claude Code login already exists on the host — no separate `ANTHROPIC_API_KEY`. A missing binary, a timeout (`WA_LUNA_TIMEOUT_SEC`, default 120s — raised from 60 after TASK-68's end-to-end run produced a real `subprocess.TimeoutExpired` at 60s on an ordinary turn: the tool call (TASK-62) plus `effort=high` sometimes need more time than the reply alone), a non-JSON result, or a response missing required fields all raise loudly instead of inventing a message.

```bash
WA_BRAIN=luna                  # deterministic (default) | luna
WA_LUNA_MODEL=claude-sonnet-5  # any model `claude --model` accepts (claude-haiku-4-5 = cheaper/faster)
WA_LUNA_EFFORT=high            # low|medium|high|xhigh|max -- "high" since TASK-62: planning tool use is real reasoning work
```

**Model's own tools (TASK-62, wired in):** `app/wa/luna/tools_server.py` is a small stdio MCP server with four read-only tools — `search_postings`, `get_posting`, `list_clinics`, `get_clinic_contact` — calling the same `D.filter_jobs`/`D.filter_clinics` functions as `app/wa/brain.py` and `GET /api/jobs`/`/api/clinics`. `Client._live_reply` launches it via `--mcp-config`/`--strict-mcp-config`/`--allowedTools` (scoped to exactly these four names, form `mcp__pflege_board__<tool>` — confirmed live, undocumented anywhere official). `market_snapshot`/`requirement_scoreboard` still ride in every payload regardless: a tool call is an addition, not a replacement, and a failed call just falls back to existing snapshot reasoning (no retry mechanism — deliberately rejected, see below). The prompt (`prompts.py`, TOOLS rule) demands proactive use: the moment a candidate names a city/department/clinic the snapshot doesn't already show, a real tool call must happen — never a guess.

Two gotchas hit live while building this, relevant to any future change here:
1. The model initially wrote `"search_postings"` as the value of `action` in its JSON instead of actually calling the tool — the strict "return ONLY a JSON object" instruction got misread as banning any intermediate action. Fix: `OUTPUT_INSTRUCTION` now states explicitly that this only governs the *final* text, after any tool calls.
2. `--mcp-config`'s per-server `cwd` field is not honored by this CLI version's stdio launcher — the server inherits the outer `claude` process's cwd (`C.LUNA_SESSION_DIR`, not repo root), so `python -m app.wa.luna.tools_server` fails with `ModuleNotFoundError: No module named 'app'`. Fix: `env.PYTHONPATH` in the generated config forces correct module resolution regardless of actual cwd. Same reason the server also gets `WA_SQLITE_PATH`/`WA_LUNA_SESSION_DIR` passed as env vars — it does a fresh `app.wa.config` import in its own process, so a test's `monkeypatch` in the parent process never reaches it otherwise.

**Close sequence (TASK-63):** once qualification, and either city or department, and housing are all settled, `market_snapshot` also carries `matching_clinics_count` and `shortlist` (up to 5, populated only from that point). The prompt requires four separate turns: state the total count → name the shortlist → recap the criteria in one line → only then ask for anonymized-send consent (`anonymous_send_consent`). A later turn may re-mention an already-named clinic (e.g. inside the consent question itself) — only naming a clinic for the *first* time in the same turn that also asks for consent is forbidden.

**Consent scope covers matching clinics generally, not one named clinic (TASK-83):** the shortlist can legitimately have just one entry (a narrow market), which used to make the consent question read as "may I forward your profile to Klinikum X" — but `build_queue_entry` always matches against the *whole* live clinic snapshot (up to 5 results), not just what got named out loud. The prompt now requires the consent question to be phrased generally ("an bayerische Kliniken, die zu Ihrem Profil passen" / "to Bavarian clinics matching your profile"), never tied to one clinic's name, so what the candidate agrees to actually matches what the system does next.

**Post-consent queue (TASK-66):** the moment `anonymous_send_consent` flips to `true` on a turn, `app/wa/api.py` — only AFTER the thread is saved and AFTER the per-message lock (`ST._lock`) is released, so a matching run never blocks every other thread — builds a real entry via `app/wa/queue.py:build_queue_entry`: `app.autopilot.matching.rank()` (same transparent scoring engine, without writing real candidate PII into `app/autopilot`'s own, explicitly-synthetic demo database) ranks the candidate against every clinic in the live snapshot; a known contact (TASK-64/69) is attached where available. Two new, dedicated tables (`wa_queue_candidates`, `wa_queue_matches`, same sqlite file as `app/wa/store.py`) are idempotent (upsert) — a repeat consent duplicates nothing. `GET /api/wa/queue` (candidates × matching clinics) and `GET /api/wa/queue/mailing-list` (flat preview: candidate × clinic × contact email) are owner-only like `GET /api/wa/threads` — both send nothing, they're a report for a human to act on.

**Optional external contact-CRM source (TASK-69, extends TASK-64):** an operator can plug in their own, separately-maintained clinic contact CRM (human- or agent-maintained contacts, ideally with a source/evidence per entry) — `app/wa/luna/external_contacts.py` is a no-op unless `WA_EXTERNAL_CONTACT_DB` is set. When configured, it queries the given sqlite file read-only (reader command via `WA_EXTERNAL_CONTACT_READER`, default `sudo sqlite3`, since such a CRM file often has tighter permissions than this process itself), fuzzy-matches the clinic name (rapidfuzz, scoped to `bundesland='Bayern'`), and prefers a person with `role_category` `pflege_leadership`/`hr_leadership`/`hr` (expected schema: `companies`/`people`/`contact_channels`, see the module for details). `contacts.discover_contact` tries this source first, before `enr_contact_emails`/website/JD-rescan, and falls through on any failure (not configured, no read access, etc.) exactly as forgivingly as the existing website source always has.

**24h window and reopen template (TASK-70):** WhatsApp's own rule, not ours — plain free text only goes out within `WA_FREEFORM_WINDOW_HOURS` (default 24) of the candidate's last message; after that Meta rejects free text. `app/wa/api.py:_send` checks this in code, never the model: window closed → a pre-approved Meta template goes out instead of the bubbles (`Client.send_template`, `WA_REOPEN_TEMPLATE_NAME`/`WA_REOPEN_TEMPLATE_LANG`). No template configured → raises loudly, instead of attempting free text (which Meta would reject anyway) or silently doing nothing. In the current, purely webhook-driven delivery (`_handle_one`), the window is practically always open since `last_inbound_at` is always fresh — the check mostly matters once the dry-run tool (`shadow_run.py`, TASK-72) or the catch-up driver (TASK-78) reaches an older, unanswered thread.

**Stage/ball reporting and migration (TASK-71):** `app/wa/luna/reporting.py` provides `stage_for(card)` (new_lead → qualifying → documents_in → ready → consented, or not_placeable) and `ball_for(conn, phone)` (us/them/none, from the last row in `wa_messages`) — both pure derivations from fields that already exist, no new columns, purely for reporting (dry-run tool, migration). `app/wa/luna/migrate_candidates.py` idempotently imports real candidates into `wa_threads` from a generic JSON export (`--input`, only `phone` required), never directly from any specific external system (same restraint as `external_contacts.py`, TASK-69). A row with no valid phone number is reported, never silently skipped; a second run only fills in the card, never overwrites it.

**Dry-run tool (TASK-72):** `python -m app.wa.luna.shadow_run` — the same safeguard the real production team already runs for exactly this purpose (`wa_shadow_run.py` on tasker-dispatcher-01): "what would the reply be, without sending", always against a copy of the database, never the real one. `shadow_run.db_copy()` opens the source strictly read-only (SQLite URI `mode=ro` — refuses writes, and refuses to even create the file if missing) and backs it up via SQLite's own online-backup API into an in-memory copy; everything after that reads and writes only the copy. Looks at every thread where `reporting.ball_for() == "us"` (last message inbound, a reply still owed) — in this harness, a state that a real webhook call only occupies briefly (reply is computed and sent synchronously); a thread stuck there in the real database means something failed mid-flight, exactly what this safe inspection tool is for.

One quirk specific to `WA_BRAIN=luna`: the card carries a real, resumable Claude Code session id (`_session_id`). A dry run that resumed that session with `--resume` would leave a real, permanent entry in the exact session the next real webhook call resumes from — an irreversible side effect on shared external state a report-only tool must never risk. `shadow_turn()` always strips `_session_id` before calling the brain, so every Luna reply here comes from a fresh, disposable session — the card itself (what actually drives gates and matching) stays fully real, only the session's own conversational memory is missing, so wording may read a bit colder than the real reply would. The per-thread result reports stage, action, bubbles, and the TASK-70 gate (`freeform`/`reopen_template`/`reopen_template_missing`/`no_send`/`stopped`) — never written, not to the copy, not to the original.

**Rate limit, dedup claim, catch-up, failure visibility (TASK-76/77/78/79):** a comparison against the real production system found four concrete gaps, closed here. `app/wa/config.py:WA_LUNA_MAX_CALLS_PER_HOUR` (default 20, 0 disables) caps Luna calls per candidate per hour — a backstop against a runaway loop, not a conversation throttle; hitting the cap doesn't lose the message, it gets picked up on the next catch-up pass. `app/wa/store.py:wa_reply_turn_claims` is a durable, cross-process claim per (phone, inbound wamid) — needed once two entry points (webhook and catch-up) could both try to answer the same unanswered message at once; an aborted or rejected claim is reclaimable, only an actually-sent (`sent`) claim blocks for good. `app/wa/api.py:process_owed_turn` bundles claim, rate check, brain call, send, and claim completion into one pipeline used by both `_handle_one` (webhook) and `app/wa/luna/catchup.py` (new, TASK-78 — the live counterpart to `shadow_run.py`: same owed-reply query, but a real send, not a dry run). A Meta failure is now durably recorded (`wa_send_failures`, `_send_and_record`) before the exception is re-raised, instead of vanishing without a trace, and `GET /api/wa/threads` shows `stuck_reply` per thread (ball on us longer than `WA_STUCK_REPLY_HOURS`, default 2h) and `last_send_error` — no invented Telegram/email channel, just a durable, discoverable signal.

**Button-confirmed consent (TASK-80):** the real reference system already has a real button tap for its own harder clinic-submission gate, never inferred from free text — the same rigor now applies to the one consent point this harness has. `anonymous_send_consent` is never taken from the model's own `card_patch` anymore (also removed from `OUTPUT_SCHEMA`/`OUTPUT_INSTRUCTION`) — it's set exclusively from a real tap on one of two buttons (`consent:yes`/`consent:no`, `LB.CONSENT_BUTTONS`), the same "decided in code, not by the model" pattern as opt-out/reject/out-of-scope. Buttons attach exactly on the turn where `anonymous_send_offered` newly flips `true`; a new `is_button_reply` field in the model's payload lets Valentina honestly tell a real tap from typed text that merely sounds affirmative — in the second case she asks for the tap instead of claiming consent that doesn't exist yet.

**Document-type classification (TASK-81):** `app/cv.py:classify_document()` assigns an uploaded document a `document_type` (`urkunde`/`lebenslauf`/`defizitbescheid`/`aufenthaltstitel`/`dienstplan`/`other`, same vocabulary as the reference system) and, for an Urkunde, a `certificate_level` (`fachkraft`/`helfer`/`unknown`) — explicitly distinguishing a real 3-year Fachkraft qualification from a Pflegehelfer/Pflegefachhelfer/Pflegefachassistent certificate (helper level despite the word "Fach"). Called directly at media intake (`_ingest_media`), lands on the card — no separate prompt wiring needed since `luna_brain._user_payload` already sends the whole card; a new rule in `prompts.py` just tells the model what the field means. Deliberately informational only: does not itself flip `qualification_ok` in code — out of this task's scope.

**Still missing compared to the source:** no interview scheduling, no clinic-submission email flow, no manager-CRM handoff, no proactive re-engagement messages (catch-up TASK-78 only retries an already-owed reply, it never initiates contact on a silent thread) — same gaps as the deterministic branch (below), same reason: the infrastructure doesn't exist in this repo. An escalation (`escalate_to_manager`) is recorded on the thread (`_escalated`, `_escalate_reason`, readable via `GET /api/wa/threads`) but triggers no send of any kind.

## Deliberately missing

- **No LLM by default.** The question ladder above is deterministic so every rule has a test. `WA_BRAIN=luna` (above) switches to Claude when the full persona/conversation is needed.
- **No clinic-side action.** `handover_requested` is recorded on the thread but sends nothing; a human takes over. The harness promises the lead exactly that, nothing more.
- **No proactive messages** — so also no follow-up cadence, no quiet-hours window, no 24-hour template logic for that purpose: the harness only ever replies, and a reply within 24h needs no template.
- **No media pipeline** (download, STT, CV parsing) and no outbound dedupe tables of the kind the production draft/preview/confirm flow needs where a human and the bot share one number.

One deliberate deviation from the production template: there, the match list ships **without** links ("keeps people from leaving the chat"). Here, `source_url` is included, because a list a candidate can't verify is worthless — and the board has no other outbound link anyway.

## Persona tests against the real CLI

`tests/test_wa_luna_personas.py` (marker `llm`, excluded by `-m "not llm"` like `network`/`completeness`/`mutation` — every test really calls `claude`, costs real money, takes several seconds per turn) plays several fully-invented personas through the real Claude call. The personas themselves are invented — names, details, dialogue lines — but the **patterns** they exercise (qualification-path mix, conversation shape, typical edge cases) come from an anonymized read of two months of the reference implementation's real WhatsApp history: read, aggregated into archetype groups, then discarded — no real name, phone number, or verbatim quote appears in this file.

```bash
.venv/bin/python -m pytest -q -m llm tests/test_wa_luna_personas.py
```

The first real run found two real bugs a fully-faked test suite couldn't have caught:

- **Salary question answered instead of deferred.** With no explicit rule, the model once quoted a concrete salary range ("zwischen ca. 3.400 und 4.200 € brutto") despite the harness having no reliable data source for that. Fixed with a new rule (`app/wa/luna/prompts.py:RULES`, "SALARY"): never state or estimate a number, always defer to clinic confirmation.
- **A silent turn without `no_send` crashed the harness.** A model turn came back with empty `bubbles: []` but without setting `no_send: true` — `_check()` expected at least one bubble and raised `AssertionError`. Fixed: an empty `bubbles` array now counts as "nothing to say" on its own, regardless of the `no_send` flag (`app/wa/luna_brain.py:turn`, regression test in `tests/test_wa_luna_brain.py`).

A third finding was hardening, not a logic bug: `--restricted` alone still allows file-reading tools (only command/code-execution/WebFetch are dropped), and the model once wrote a file-access attempt as prose ahead of its actual JSON reply. The call now also runs with `--tools ""` (every tool off), and parsing itself (`app/wa/luna_brain.py:_parse_reply_json`) additionally tries to cut the JSON object out of surrounding text before finally giving up.

## End-to-end funnel test with two live agents (TASK-68)

`tests/test_wa_luna_e2e_funnel.py` (marker `llm`) goes one step further than the persona tests above: there, only Valentina is a real model call, the candidate script is deliberately fixed (stable for regression tests). Here, a `_CandidateAgent` (own resumable Claude Code session, `claude-haiku-4-5`, free text instead of a JSON schema) plays the candidate side freely from a short persona brief — both sides are real, non-deterministic model calls. Three personas (Urkunde/München, Defizitbescheid/Augsburg, passed-Kenntnisprüfung-Urkunde-pending/Bayern-open) run through to consent (`anonymous_send_consent`), with a logged turn cap instead of a silent success if a persona doesn't converge. Every persona that succeeds then runs through `app/wa/queue.py:build_queue_entry` (TASK-66) against a small fixture board with one pre-seeded clinic contact.

```bash
.venv/bin/python -m pytest -q -m llm tests/test_wa_luna_e2e_funnel.py -s
```

A live run found a real bug (TASK-82), not a persona-style issue: `market_snapshot`'s `ready_to_close` required BOTH city AND department_pref, while `requirement_scoreboard` told the model the `city_or_department` gate was satisfied by EITHER one — a candidate genuinely flexible on department (a real, valid answer) saw "satisfied" but never got a shortlist to close with, stalling indefinitely. Fixed by sharing one predicate (`_city_or_department_satisfied`) between both functions.

Full transcripts are written to `tests/.artifacts/e2e_funnel_report.md` on every run (git-ignored — synthetic but conversation-shaped) and printed to stdout.

A side finding from this suite: `WA_LUNA_TIMEOUT_SEC` (60s) stopped being enough once TASK-62 added a tool call plus `effort=high` to the turn — an ordinary turn once hit a real `subprocess.TimeoutExpired`. Default raised to 120s.
