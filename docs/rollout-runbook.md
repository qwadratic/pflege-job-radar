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

Run before the campaign sends (TASK-103 calls the same function per phone). Reads the old system read-only, writes
only our `data/wa.sqlite` and `data/wa_documents/`. Details: docs/whatsapp.md, "Campaign recipients".

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
# read the report: facts, placement, documents by old class and action; then the same with --apply
```

Load `.env` first (`set -a; . ./.env; set +a`): the same `WA_DOCUMENTS_DIR`/data paths as the service, and
`META_WHATSAPP_ACCESS_TOKEN`, which `--apply` needs to download a missing WhatsApp original again by `media_id`. Exit 1 = some document is not recoverable (listed per phone); the
rest is imported. Re-running is safe.

## 7. Campaign: send the template ourselves (TASK-103)

Full runbook: `docs/whatsapp.md`, "Campaign sender (TASK-103)". Deploy state it needs:

- **Restart `pflege-wa.service`** on this tree first. The running process loaded its code before TASK-99..103:
  it drops status webhooks (no delivery tracking, a 131042 payment failure stays invisible), parses a template
  tap without its payload and reply context, and counts imported documents without the reuse answer. The
  catch-up and follow-up timers already run the current tree (new process per run).
- No new unit, timer or env var. The sender is run by hand from the repo root with `.env` loaded; `--send`
  refuses without `WA_AUTOSEND=1`.
- Its table `wa_campaign_sends` is created in `data/wa.sqlite` by the first `--send` (not by the service).
  Dry-run and `--status` open the database read-only.
- Reports (phone numbers, names) go to `~/pflege-campaign-reports/` (0700/0600), outside the repo.

```bash
cd /home/claude/repo/pflege-board && set -a && . ./.env && set +a
# probe: operator number only, own campaign id; wait for "delivery delivered" in --status
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09-probe --template-id <ID> --leads probe.csv --send
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09-probe --status
# real list: dry-run, read the plan, then send (in tmux; it waits for the window and between batches)
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --template-id <ID> --leads leads.csv
.venv/bin/python -m app.wa.luna.campaign --campaign-id bayern-2026-09 --template-id <ID> --leads leads.csv --send
```

With the history import, add `--import-history-db /opt/clinic-dispatcher/var/sales_brain.sqlite
--import-history-queries deploy/import-history.example.sql --import-history-source clinic-dispatcher
--import-history-media-root ...` (step 6 grants first); the dry-run previews it, `--send` applies it per phone
right before that phone's claim.

Rollback of a campaign: stop the run (Ctrl-C). Already flipped phones stay with us (`wa_ownership` reason
`campaign:<id>`); there is no hand-back tool. Calls to and from those phones (and call statuses, call-permission
replies) still reach the old system's call bridge; a template that went out during an uncertain POST is recorded
with `--mark-sent PHONE=WAMID` (`docs/whatsapp.md` runbook).

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
