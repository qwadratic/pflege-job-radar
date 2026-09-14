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
curl -s "http://127.0.0.1:8502/api/wa/threads?phone=<the+test+number>"   # owner session needed via browser; journalctl is faster
```

**Rollback (fast, one command, no data loss either side):** remove the `location =
/candidate-action/webhooks/meta/whatsapp` block from the nginx config and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Meta traffic goes back to hitting `candidate-connector-bridge.service` directly, exactly as before
step 4 -- that service was never stopped or touched. To also stop our own service from doing
anything further: `sudo systemctl stop pflege-wa.service pflege-wa-catchup.timer pflege-wa-followups.timer`.

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
