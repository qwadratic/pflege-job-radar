# WhatsApp harness rollout runbook (2026-09-13)

Every step below was blocked for me by the auto-mode classifier (reasons varied: Data Exfiltration,
Credential Exploration, Credential Leakage, Production Deploy) even for clearly read-only or
local-only sub-steps. Run these yourself, in order. Each step says what it does and why.

`.env` already exists at the repo root with real Meta credentials (copied from
`/etc/clinic-dispatcher/candidate-whatsapp.env` earlier) and `WA_AUTOSEND=1` -- **the moment this
service starts and receives a message, it will really reply via Meta.**

## 1. Populate the known-phones file

```bash
cd /home/claude/repo/pflege-board
.venv/bin/python -m app.wa.luna.export_known_phones \
  --db /opt/clinic-dispatcher/var/sales_brain.sqlite \
  --query "select distinct phone_e164 from candidate_whatsapp_messages" \
  --out /home/claude/repo/pflege-board/data/known-real-system-phones.txt
```

Prints `exported N, skipped M`. This is our own tool (TASK-87) -- read-only against the real db
(SQLite `mode=ro`), writes only phone numbers, nothing else.

## 2. Install our own service + timers

```bash
cd /home/claude/repo/pflege-board
for f in pflege-wa.service pflege-wa-catchup.service pflege-wa-catchup.timer \
         pflege-wa-followups.service pflege-wa-followups.timer \
         known-phones-export.service known-phones-export.timer; do
  sed -e 's#User=exedev#User=claude#' \
      -e 's#/home/exedev/repo#/home/claude/repo/pflege-board#g' \
      "deploy/$f" | sudo tee "/etc/systemd/system/$f" > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now pflege-wa.service
```

Check it's up before doing anything else:

```bash
curl -s http://127.0.0.1:8502/api/wa/health
```

Expect `"webhook_ready": true, "outbound_ready": true, "brain": "luna"`. If either is `false`, stop
here and check `journalctl -u pflege-wa -n 50` -- something in `.env` didn't load.

## 3. Start the periodic jobs (catch-up, follow-ups, phone-list refresh)

```bash
sudo systemctl enable --now pflege-wa-catchup.timer
sudo systemctl enable --now pflege-wa-followups.timer
sudo systemctl enable --now known-phones-export.timer
```

`known-phones-export.service` needs `KNOWN_PHONES_SOURCE_DB`/`KNOWN_PHONES_SOURCE_QUERY` -- both
are already in `.env` (loaded via the same `EnvironmentFile=`), so no separate env file needed.

## 4. The actual cutover: nginx

**This is the irreversible-feeling step** -- once this reloads, real Meta webhook calls start
reaching our system. Add this location block to whichever nginx site config actually has the
`/candidate-action/` block proxying to `127.0.0.1:8816` (confirmed present in
`/etc/nginx/sites-available/business-jobs-bewerbung-pflege`; double-check it's also the one Meta
calls -- `BRIDGE_PUBLIC_BASE_URL=https://jobs.bewerbung-pflege.work` per
`/etc/systemd/system/candidate-connector-bridge.service`, so confirm which site config actually
serves that hostname before editing):

```nginx
# Meta WhatsApp webhook -- routed through our harness first (app/wa/router.py, TASK-84), which
# decides per-message whether we answer or forward to the real system's own internal address
# (127.0.0.1:8816, untouched, still running). Must be `location =` (exact match) so it takes
# precedence over the broader /candidate-action/ prefix block below it, without touching that
# block's routing for every other candidate-connector-bridge endpoint.
location = /candidate-action/webhooks/meta/whatsapp {
    proxy_pass http://127.0.0.1:8502/api/wa/route-webhook;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` first -- do not reload if it reports an error.

## 5. Verify live, and the fast rollback

Send a WhatsApp test message to the real number and watch:

```bash
journalctl -u pflege-wa -f
curl -s "http://127.0.0.1:8502/api/wa/threads?phone=<the+test+number>"   # no session needed on 8502 (local-only)
```

**Rollback (fast, one command, no data loss either side):** remove the `location =
/candidate-action/webhooks/meta/whatsapp` block from the nginx config and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Meta traffic goes back to hitting `candidate-connector-bridge.service` directly, exactly as before
step 4 -- that service was never stopped or touched. To also stop our own service from doing
anything further: `sudo systemctl stop pflege-wa.service pflege-wa-catchup.timer pflege-wa-followups.timer`.

## 6. Campaign recipients: import the old system's history (TASK-102)

Run before the campaign sends (TASK-103 calls the same function per phone, and since TASK-105 every campaign
dry-run and send needs it). Reads the old system read-only, writes only our `data/wa.sqlite` and
`data/wa_documents/`. Details: docs/whatsapp.md, "Campaign recipients".

**Read grants (applied by Ivan 2026-09-14 on this host, verified with getfacl).** The importer runs as `claude`,
never sudo. `sales_brain.sqlite` is world-readable; the file stores are not:

```bash
sudo setfacl -m u:claude:--x /opt/clinic-dispatcher/data/private
sudo setfacl -m u:claude:--x /opt/clinic-dispatcher-v2-bridge /opt/clinic-dispatcher-v2-bridge/data \
  /opt/clinic-dispatcher-v2-bridge/data/private
sudo setfacl -R -m u:claude:rX,d:u:claude:rX /opt/clinic-dispatcher/data/private/candidate_whatsapp_media \
  /opt/clinic-dispatcher/data/private/manager_crm_lebenslauf \
  /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media
```

Revert:

```bash
MEDIA="/opt/clinic-dispatcher/data/private/candidate_whatsapp_media /opt/clinic-dispatcher/data/private/manager_crm_lebenslauf /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media"
sudo setfacl -R -x u:claude $MEDIA
sudo find $MEDIA -type d -exec setfacl -x d:u:claude {} +
sudo setfacl -x u:claude /opt/clinic-dispatcher/data/private /opt/clinic-dispatcher-v2-bridge \
  /opt/clinic-dispatcher-v2-bridge/data /opt/clinic-dispatcher-v2-bridge/data/private
```

A missing grant fails the run with `SourceAccessError` naming the path (exit 2).

**Dry-run, then apply:**

```bash
cd /home/claude/repo/pflege-board
.venv/bin/python -m app.wa.luna.import_history --db /opt/clinic-dispatcher/var/sales_brain.sqlite \
  --queries deploy/import-history.example.sql --source clinic-dispatcher \
  --media-root /opt/clinic-dispatcher/data/private/candidate_whatsapp_media \
  --media-root /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media \
  --phones-file <campaign phone list, one +E.164 per line>
# read the report: facts, placement, documents by old class and action, "no contact wanted" lines (opt-out and
# decline records, chat Stopps) and whether the card gets declined; then the same with --apply
```

Load `.env` first (`set -a; . ./.env; set +a`): the same `WA_DOCUMENTS_DIR`/data paths as the service, and
`META_WHATSAPP_ACCESS_TOKEN`, which `--apply` needs to download a missing WhatsApp original again by `media_id`. Exit 1 = some document is not recoverable (listed per phone); the
rest is imported. Re-running is safe.

The `opt_outs` query (TASK-105) reads `candidates`, `job_wohnung_outreach`, `candidate_recruitment_state` and
`suppression_list` of `sales_brain.sqlite`, written from code only. A table the live database lacks fails every
phone loudly (`no such table`, `import_error` in the campaign): fix the query, never drop it. `--apply` marks the
card of a phone with such a record, or a Stopp in the old chat, declined (Luna stays silent unless the candidate
re-engages; no follow-ups).

## 7. Campaign: send the template ourselves (TASK-103)

Full runbook: `docs/whatsapp.md`, "Campaign sender (TASK-103)". Deploy state it needs:

- **Restart `pflege-wa.service`** on this tree first. The running process loaded its code before TASK-99..103:
  it drops status webhooks (no delivery tracking, a 131042 payment failure stays invisible), parses a template
  tap without its payload and reply context, counts imported documents without the reuse answer, and does not
  know an imported decline (TASK-101 silence, TASK-105 prompt and code-owned `prior_opt_outs`). The
  catch-up and follow-up timers already run the current tree (new process per run).
- No new unit, timer or env var. The sender is run by hand from the repo root with `.env` loaded; `--send`
  refuses without `WA_AUTOSEND=1`.
- Its table `wa_campaign_sends` is created in `data/wa.sqlite` by the first `--send` (not by the service), one
  row per attempt (TASK-106). A table from the TASK-103 layout (one row per campaign and phone) is rebuilt into
  attempt rows by the first `--send` from this tree, in one transaction; the live database had no such table on
  2026-09-14. Dry-run and `--status` open the database read-only.
- Reports (phone numbers, names) go to `~/pflege-campaign-reports/` (0700/0600), outside the repo.

```bash
cd /home/claude/repo/pflege-board && set -a && . ./.env && set +a
# history source (TASK-105, step 6 grants first): every dry-run and send needs it, --status does not
H="--import-history-db /opt/clinic-dispatcher/var/sales_brain.sqlite --import-history-queries deploy/import-history.example.sql --import-history-source clinic-dispatcher --import-history-media-root /opt/clinic-dispatcher/data/private/candidate_whatsapp_media --import-history-media-root /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media"
# probe: operator number only, own campaign id; wait for "delivery delivered" in --status
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09-probe --template-id <ID> --leads probe.csv $H --send
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09-probe --status
# real list: dry-run, read the plan, then send (in tmux; it waits for the window and between batches)
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --template-id <ID> --leads leads.csv $H
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --template-id <ID> --leads leads.csv $H --send
# Meta reported templates failed after the send (delivery_failed, e.g. 131042 unsettled payments): fix the cause,
# then resend them in the same campaign (TASK-106): dry-run, send, status until the new attempt is delivered
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --template-id <ID> --leads leads.csv $H --retry-delivery-failed
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --template-id <ID> --leads leads.csv $H --send --retry-delivery-failed
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --status
```

`--retry-delivery-failed` resends only phones whose latest attempt Meta reported `failed`, as a new attempt of the same
campaign; each attempt keeps its own wamid, error and delivery status (`--status` lists every attempt). A phone that
was stopped, declined or opted out, or wrote to us since the campaign first claimed it, is not resent
(`skip_replied` names the messages); once any attempt went out, the same holds for a later `retry_failed` or
`--retry-uncertain` resend. A retry Meta rejects (HTTP 4xx) restores the owner from before the campaign.

`$H` is the history source. Without it the sender exits 2; the dry-run
previews the import, `--send` applies it per phone right before that phone's claim, and a phone the old system
recorded as opted out or declined, or that wrote Stopp there, is skipped with the record named. Only
`--override-no-history-source` runs without the source; it is printed as a WARNING and recorded in the report
(`history_source`). Do not use it for the real list.

Rollback of a campaign: stop the run (Ctrl-C). Already flipped phones stay with us (`wa_ownership` reason
`campaign:<id>`); there is no hand-back tool. Calls to and from those phones (and call statuses, call-permission
replies) still reach the old system's call bridge; a template that went out during an uncertain POST is recorded
with `--mark-sent PHONE=WAMID` (`docs/whatsapp.md` runbook).

## 8. Voice notes (TASK-107)

`WA_BRAIN=luna` transcribes voice notes with OpenAI (`docs/whatsapp.md`, "Voice notes (TASK-107)"). Needs
`OPENAI_API_KEY` in `.env` (absent on 2026-09-15); optional `WA_STT_MODEL` (default `whisper-1`). Without the key, a
service restarted on this tree answers no voice note: each stays pending with `TranscriptionError: OPENAI_API_KEY is
not set` (`GET /api/wa/threads`: `pending_inbound`, `stuck_reply`) and catch-up retries it every 3 minutes. Add the key
before that restart, or accept the stall.

```bash
cd /home/claude/repo/pflege-board
sudo systemctl restart pflege-wa.service            # reads .env again
curl -s http://127.0.0.1:8502/api/wa/health         # expect "stt_ready": true, "stt_model": "whisper-1"
set -a && . ./.env && set +a
.venv/bin/python -m pytest -q -m network tests/test_wa_stt_live.py -s   # synthetic German voice note, real endpoint
```

The live test sends only synthetic espeak-ng speech to OpenAI; nothing goes to Meta.

## 9. Test numbers: mark the operator's number, install the nightly wipe (TASK-109)

The number the live harness is tested from by hand (Ivan's, ends 8778) must be marked, or it is counted in reports
like a candidate, can receive a campaign template mid-test, and starts every test from yesterday's card.
Details: `docs/whatsapp.md`, "Test numbers (TASK-109)".

```bash
cd /home/claude/repo/pflege-board
set -a && . ./.env && set +a
.venv/bin/python -m app.wa.luna.test_threads --mark +49XXXXXXX8778
.venv/bin/python -m app.wa.luna.test_threads --list
curl -s http://127.0.0.1:8502/api/wa/threads | jq '.test_threads, (.rows[] | select(.is_test) | .phone)'
```

The wipe is a separate job. Read a dry run before the first `--apply` -- it prints every row, file and session
transcript it would delete, per phone, and writes nothing:

```bash
.venv/bin/python -m app.wa.luna.purge_test_history --json | jq .      # dry run
.venv/bin/python -m app.wa.luna.purge_test_history --apply            # full wipe, now
sudo cp deploy/pflege-wa-purge-test.service deploy/pflege-wa-purge-test.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pflege-wa-purge-test.timer
systemctl list-timers pflege-wa-purge-test.timer     # expect the next 03:00 Europe/Berlin
journalctl -u pflege-wa-purge-test -n 50
```

Schedule (timer `OnCalendar`) and retention (`--older-than-hours` in the service, 0 = full wipe) live in the unit
files, not in the code. The unit runs as `User=exedev`: its `CLAUDE_CONFIG_DIR` (default `~/.claude`) must be the
config the `claude` CLI uses for the Luna turns, otherwise the report shows `0 session transcript(s)` for a thread
that has a session id -- the conversation would stay readable on disk. The job never calls Meta.

## Known gaps going into this rollout (not blockers, but real)

- No Meta-approved reopen template registered (`WA_REOPEN_TEMPLATE_NAME` unset) -- a thread that
  goes >24h without a reply from us fails loudly instead of sending (TASK-70). Low risk at
  launch (webhook-driven replies are synchronous, so the window is fresh at send time) but will
  matter once catch-up/follow-ups start reaching an older thread.
- `WA_REAL_SYSTEM_PHONES_FILE` only updates hourly (`known-phones-export.timer`) -- a phone that
  becomes a real candidate on the *other* system less than an hour ago may still be briefly
  treated as new by us. Conservative failure direction (we'd double-engage briefly, not ignore
  someone).
- This is the first time this exact pipeline (rate limit, dedup claim, consent buttons, document
  classification, router split) has run against real traffic. Everything is tested against
  fixtures and one live-CLI persona suite -- not the same as real candidates.
