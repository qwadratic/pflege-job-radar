---
id: doc-1
title: Email channel runbook
type: guide
created_date: '2026-09-17 17:30'
updated_date: '2026-10-01 16:02'
tags:
  - email
  - runbook
---
# Email channel runbook (clinic communication)

Living runbook. Goal: repeat email-channel setup in any project. Update after every step. Pitfalls marked **PITFALL**.

## 0. Credentials intake
- Drop raw creds in a `700` folder outside the repo (`~/secrets-inbox/`).
- Import into gitignored `.env` (`chmod 600`): `MAILBOX_<n>_{PROVIDER,ADDRESS,PASSWORD,SMTP_HOST,SMTP_PORT,IMAP_HOST,IMAP_PORT}`, `MAILBOX_COUNT`, `MAILBOX_ACTIVE`.
- `shred -u` the raw file after import.
- **PITFALL**: domain-panel creds (GoDaddy) are an SSO login, not an API key. DNS changes are manual until an API key exists.

## 1. Preflight per mailbox (no sending)
- SMTP: connect `:587`, STARTTLS, `login()` → expect `235`. Do not send.
- IMAP: `:993` SSL, `login()`, `SELECT INBOX` readonly.
- Hosts: Zoho EU `smtp.zoho.eu` / `imap.zoho.eu`. M365 `smtp.office365.com` / `outlook.office365.com`.
- **PITFALL (Zoho)**: region-bound hosts. EU account on `.com` fails auth. Test `.eu` first.
- **PITFALL (Zoho)**: IMAP is off per account by default → `[ALERT] You are yet to enable IMAP`. Admin or user enables it.
- **PITFALL (M365)**: IMAP/POP basic auth is disabled by Microsoft → `Basic authentication is disabled`. Only OAuth2 reads mail.
- **PITFALL (M365)**: SMTP AUTH basic still works (2026-09) but Microsoft disables it by default end of Dec 2026. Plan OAuth2/Graph for sending too.

## 2. Preflight per domain (DNS + site)
```
dig +short MX d; dig +short TXT d | grep spf1   # exactly 1 line
dig +short TXT _dmarc.d
dig +short TXT zmail._domainkey.d               # Zoho DKIM selector
dig +short CNAME selector1._domainkey.d         # M365 DKIM (also selector2)
curl -sL https://d | grep -i impressum
```
- M365 tenant name is visible in the DKIM CNAME target (`...<tenant>.x-v1.dkim.mail.microsoft`).
- **PITFALL**: DKIM DNS present ≠ signing enabled. Confirm `dkim=pass` in headers of a real received test mail.
- **PITFALL**: GoDaddy parked domain serves a JS redirect to `/lander`. HTTP 200, but it's a parking page, not a site.
- Site with Impressum + Datenschutz on every sending domain: German B2B recipients check the domain.

## 3. Microsoft 365 app for mail (OAuth2, Graph) — admin steps
One app per tenant. Scope it to our mailboxes only.
1. entra.microsoft.com → App registrations → New registration. Name `pflege-mail`, single tenant, no redirect URI.
2. Copy **Application (client) ID** and **Directory (tenant) ID**.
3. Certificates & secrets → New client secret (24 months) → copy **Value** (shown once).
4. Do NOT add Graph application permissions in Entra (they grant access to every mailbox in the tenant). Scope via Exchange RBAC for Applications instead:
```powershell
Connect-ExchangeOnline
# ObjectId = Enterprise applications -> pflege-mail -> Object ID (NOT the App registration object ID)
New-ServicePrincipal -AppId <CLIENT_ID> -ObjectId <ENTERPRISE_APP_OBJECT_ID> -DisplayName "pflege-mail"
New-ManagementScope -Name "pflege-mail-boxes" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'daria.s@pflege-connect.work'"
New-ManagementRoleAssignment -App <CLIENT_ID> -Role "Application Mail.ReadWrite" -CustomResourceScope "pflege-mail-boxes"
New-ManagementRoleAssignment -App <CLIENT_ID> -Role "Application Mail.Send"      -CustomResourceScope "pflege-mail-boxes"
Test-ServicePrincipalAuthorization -Identity <CLIENT_ID> -Resource daria.s@pflege-connect.work   # expect InScope=True
```
5. Send back via a secure channel: tenant ID, client ID, secret value, secret expiry date.
- **PITFALL**: wrong ObjectId (App registration instead of Enterprise app) → `New-ServicePrincipal` succeeds but auth fails.
- **PITFALL**: RBAC changes take up to ~2 h to propagate.
- Env keys: `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`, `M365_CLIENT_SECRET_EXPIRES`.

## 4. Zoho admin steps
- Enable IMAP: mailadmin.zoho.eu → Users → user → Mail settings → IMAP. Or user: Settings → Mail Accounts → IMAP Access.
- 2FA on → app-specific password (Settings → Security → App Passwords) for SMTP/IMAP.
- Paid plan required for IMAP/SMTP.

## Status log
Mailbox in use: **daria.s@pflege-connect.work** only. Ivan, 2026-10-01: "список ящиков пока не актуален, работаем с дарьи" (the mailbox list is out of date for now; we work from Daria's box). The earlier 11-box roster and the per-box notes were removed from this runbook on that date.

### daria.s@pflege-connect.work
- Provider M365. SMTP basic auth works (it will be disabled by Microsoft by default at the end of Dec 2026). IMAP basic auth is off.
- Read: Graph, delegated, through the shared root-owned MSAL cache. Claude reads it only with `sudo -n /usr/local/sbin/daria-inbox --since ISO` (read-only, installed by `tools/daria_inbox_install.sh`). Forwards to Ivan go through `tools/daria_forward.py`.
- Send: `tools/clinic_mailer.py` from this box (SMTP), one config per campaign with its own allowlist; live sends are run by Ivan only.
- The box is in warm-up: most traffic is warm-up pool mail and cold sales mail to "Daria", plus the warm-up tool's own sends and replies. Real clinic mail is a small share; filter it by clinic domain or campaign subject.
- Domain: one MX on its own provider, exactly 1 SPF, DMARC `p=quarantine`, DKIM DNS present (M365 `selector1/2`). Not checked yet: DKIM signing in a received header, blocklists, mail-tester score, inbox placement.

### 2026-09-17 — existing creds and the 6-month dump
- Old mailbox tooling sits at `/opt/clinic-dispatcher-worktrees/mailbox-sync-durable`. Ivan imported the Graph and Zoho settings into `pflege-board/.env`. Claude's Bash is blocked from reading or copying secret files; Ivan runs those commands.
- Graph auth = SHARED MSAL cache `/opt/clinic-dispatcher/data/private/microsoft_graph_msal_cache.json`, also used by another service. Scopes: User.Read, Mail.Read, Mail.Send, Calendars.ReadWrite, OnlineMeetings.ReadWrite.
- Dump tools: `tools/email_dump_graph.py` (M365 via Graph, run with `sudo -E`, Ivan only, reads the root cache) and `tools/email_dump_imap.py` (Zoho via IMAP). Both write `data/email-dump/<addr>/messages.jsonl` + `_summary.json`. `SKIP_EXISTING=1` skips boxes already dumped; `ONLY_MAILBOX=<addr>` dumps one; `MONTHS=6` sets the window.
- **PITFALLS:**
  - Shared cache: a refresh rotates the RT. The dumper re-reads the cache per account and writes it back ATOMICALLY (temp+os.replace, root:600 preserved), so the other service is not corrupted. NEVER write that file non-atomically.
  - Graph `/me/messages` includes Junk Email, so a warm box dump is about half spam. Filter Junk during analysis, not at dump time.
  - Zoho IMAP folder names arrive in modified UTF-7 (e.g. `&BB0E...+-` is a Cyrillic folder); decode them during analysis.

### 2026-09-18 — 6-month dump analysed (TASK-345.7)
Pipeline (all in `tools/`, all deterministic except the agent passes): `email_index.py` (dedupe, global threading, org dossiers, flags) → `email_sample.py` (tiers, quant stats, template families) → `email_sample_rest.py` / `email_stage2b.py` (targeted strata, partner re-keying, org-topics) → `email_ledgers.py` (bounce/compliance/response/recontact/roles). Agent passes = 3 Workflow runs (Stage 1 wf_d3ccaeea-fd2, Stage 2 wf_3f0ddc5f-d57, Stage 2b wf_f74bdd4d-3a1). Outputs in `data/email-analysis/out/`: `report_stage1.md`, `report_stage2.md`, `report_stage2b.md` (closing evidence), `decode_outreach.md`, `decode_pushback.md`, `attachments.md`, `ledgers.md`, `critique_stage1.json`. Theory research: `data/email-analysis/research_de_placement.md`.

Corrected headline numbers (clinic-gated, UTC): Gen1 campaign "Examinierte Pflegefachkräfte für [Klinik]" 2,176 threads / 4,973 sends / 835 domains, 3 touches d0/+5/+11; strict human reply **204 = 9.4%** (41 positive, 96 negative, 7 stop); any inbound 32%; hard NDR 7.7% of threads (M3/M5/M6 11–13% in one shared reputation event W24/27/28, M1/M4 ≤0.5%); 40% of substantive replies ever answered (median 2.7 h, p75 72 h, p90 354 h); compliance: 37 domains / 174 touches after a human decline/redirect/stop; 48 orgs re-cold-contacted after a reply. Funnel: ≥7 calls held (18 invited), 27 contracts sent, **1 signed, 0 Zusage/Arbeitsvertrag/Arbeitsantritt/Rechnung/payment**. Fee only ever stated in a filename (2,5 Bruttomonatsgehälter); partner terms 50/50, 1st at Vertragsunterzeichnung, Nachbesetzung-only guarantee.

- **PITFALL**: ~78% of outbound in these mailboxes is warm-up bot traffic ([SNOV]/[WRM]/wsn + untagged chit-chat); some boxes pitch an unrelated AI service. Gate every KPI on the Gen1 subject regex ∪ clinic domain regex, exclude warm-up domains. Raw `quant_stats.json` is contaminated.
- **PITFALL**: all timestamps are UTC; Berlin = +2 h, recruiter calendars run on Europe/Kyiv (+3 h). Stage 1 clock-of-day findings are mislabeled.
- **PITFALL**: post-reply work (invites, "Unterlagen nach unserem Gespräch", contracts) came from the partner mailbox at ndt-group.agency (not exported) and other sending identities. Visible only via cc.
- **PITFALL**: keyword flags are weak — `fee_invoice` 0% precision, `legal_complaint`/`reject` fire on OOO disclaimers, `optout` 88% third-party spam, `sensitive` = health-scam spam. Stage 1/2b M1–M6 labels are different permutations.
- **PITFALL**: Graph (M365) dumps carry no attachment names; `tools/email_enrich_graph_attachments.py` (sudo, Ivan) not yet run.
- Privacy: intermediate JSON artifacts were scrubbed (persona/person/clinic names); reports use roles, org types, thread_ids only. Data stays on the server.
- Deliverables: doc-2 (flow catalog, TASK-345.7 AC) and doc-3 (email-module architecture proposal: Deal state model, stop-list, deliverability, invoicing from the actual contract terms). Actual contract/deck PDFs in `data/email-analysis/contracts/`.
