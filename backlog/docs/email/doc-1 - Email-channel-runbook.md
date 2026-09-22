---
id: doc-1
title: Email channel runbook
type: guide
created_date: '2026-09-17 17:30'
updated_date: '2026-09-18 01:07'
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
New-ManagementScope -Name "pflege-mail-boxes" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'daria.s@pflege-connect.work' -or PrimarySmtpAddress -eq 'maria.b@bewerbung-direkt.work' -or PrimarySmtpAddress -eq 'viktoriia.s@pflege-expert.work'"
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
### 2026-09-17 — preflight, 11 mailboxes, 10 domains
| # | Mailbox | Prov | SMTP | IMAP | Note |
|---|---|---|---|---|---|
| 1 | anastasiya.yeremenko@bewerbung-pflege.work | Zoho | FAIL 535 | FAIL | wrong password |
| 2 | maria@bewerbung-pflege.work | Zoho | OK | off | enable IMAP |
| 3 | dashandt@pflege-ndt.work | Zoho | OK | OK (293) | |
| 4 | evelina.vihandt@bewerbungpflege.work | Zoho | OK | off | enable IMAP |
| 5 | dana@pflege.works | Zoho | OK | off | enable IMAP |
| 6 | valentyn.v@pflege-team.work | Zoho | OK | OK (282) | |
| 7 | evelina.v@pflege-direkt.work | Zoho | OK | OK (284) | |
| 8 | viktoriia.s@pflege-karriere.work | Zoho | OK | OK (234) | |
| 9 | maria.b@bewerbung-direkt.work | M365 | OK | basic auth off | needs OAuth2 |
| 10 | daria.s@pflege-connect.work (WARM) | M365 | OK | basic auth off | needs OAuth2 |
| 11 | viktoriia.s@pflege-expert.work | M365 | OK | basic auth off | needs OAuth2 |

Domains: all 10 have MX on own provider, exactly 1 SPF, DMARC `p=quarantine`, DKIM DNS present (Zoho `zmail`, M365 `selector1/2`, tenant `ndtgroup864` for all 3 M365 domains).
Sites: `bewerbung-pflege.work`, `bewerbungpflege.work`, `pflege.works` → redirect to `pflege-ndt.work` (NDT landing, no Impressum found). Other 6 → GoDaddy parking.
Not checked yet (needs a real send): DKIM signing on, sending-IP blocklists, mail-tester score, inbox placement.

### 2026-09-17 — existing creds found + 6-month dump
- TASK-111.1 done via Ivan (Claude's Bash is classifier-blocked from reading/copying secret files; Ivan runs those cmds). Old mailbox tooling at `/opt/clinic-dispatcher-worktrees/mailbox-sync-durable`; creds in `/opt/clinic-dispatcher/data/private/{mailboxes.env,zoho_kindt_oauth.env}`. Imported into `pflege-board/.env`: 5 `MICROSOFT_GRAPH_*` (existing Entra app, tenant ndtgroup864, PublicClientApplication + device-flow, delegated, NO client secret) + 9 `ZOHO_*`.
- Graph auth = SHARED MSAL cache `/opt/clinic-dispatcher/data/private/microsoft_graph_msal_cache.json` (also used by colleague's service). Our `.env` MSAL_CACHE/ACCOUNT_MAP repointed to absolute paths. Token verified: scopes = User.Read, Mail.Read, Mail.Send, Calendars.ReadWrite, OnlineMeetings.ReadWrite.
- Dump tools: `tools/email_dump_graph.py` (M365 via Graph, run with `sudo -E` — Ivan only, reads root cache) and `tools/email_dump_imap.py` (Zoho via IMAP, Claude can run — reads our .env). Both write `data/email-dump/<addr>/messages.jsonl` + `_summary.json`. `SKIP_EXISTING=1` skips already-dumped boxes; `ONLY_MAILBOX=<addr>` for one; `MONTHS=6` window.
- **Cache ≠ our .env:** MSAL cache has 5 accounts; only daria.s@pflege-connect (#10) + maria.b@bewerbung-direkt (#9) are ours. Our #11 viktoriia.s@pflege-expert is NOT in the cache → not Graph-dumpable (needs own OAuth, TASK-111.2). Cache also has 3 non-ours: dariia.so@ki-agent, maria.bu@ki-ndt, valentyn.vi@ki-workflow (colleague's outreach personas).
- Dump progress: daria.s ✅ 5174 msg/4298 threads; dashandt (#3) ✅ 17568 msg/12 folders. Zoho #6/7/8 + M365 (#9 + 3 extras) in flight.
- **PITFALLS:**
  - `sudo` Graph dump is classifier-blocked for Claude (reads root MSAL cache) → Ivan runs it. IMAP dump (our .env) Claude can run.
  - Shared cache: refresh rotates the RT. Dumper re-reads cache per account and writes back ATOMICALLY (temp+os.replace, preserve root:600) so the colleague's concurrent service isn't corrupted. NEVER a non-atomic write to that file.
  - Graph `/me/messages` includes Junk Email → warm-box dump is ~half spam; filter Junk during analysis, not at dump.
  - Zoho IMAP folder names come in modified-UTF-7 (e.g. `&BB0E...+-` = a Cyrillic folder); decode during analysis. Messages inside are intact.
  - Zoho `snovio` folder (10947 in #3) = Snov.io cold-outreach archive — primary source for clinic-flow extraction (TASK-111.7).
- Sending decision: PRIMARY Graph sendMail (Mail.Send present), FALLBACK SMTP basic. Not yet built.

### Open
- Deliverability test still not run (needs a real send) — TASK-111.3.
- #11 + full-scope M365 read need our own app (TASK-111.2); IMAP-off Zoho #2/4/5 + dead #1 pw → TASK-111.5.

### 2026-09-18 — 6-month dump analysed (TASK-111.7)
Pipeline (all in `tools/`, all deterministic except the agent passes): `email_index.py` (dedupe, global threading, org dossiers, flags) → `email_sample.py` (tiers, quant stats, template families) → `email_sample_rest.py` / `email_stage2b.py` (targeted strata, partner re-keying, org-topics) → `email_ledgers.py` (bounce/compliance/response/recontact/roles). Agent passes = 3 Workflow runs (Stage 1 wf_d3ccaeea-fd2, Stage 2 wf_3f0ddc5f-d57, Stage 2b wf_f74bdd4d-3a1). Outputs in `data/email-analysis/out/`: `report_stage1.md`, `report_stage2.md`, `report_stage2b.md` (closing evidence), `decode_outreach.md`, `decode_pushback.md`, `attachments.md`, `ledgers.md`, `critique_stage1.json`. Theory research: `data/email-analysis/research_de_placement.md`.

Corrected headline numbers (clinic-gated, UTC): Gen1 campaign "Examinierte Pflegefachkräfte für [Klinik]" 2,176 threads / 4,973 sends / 835 domains, 3 touches d0/+5/+11; strict human reply **204 = 9.4%** (41 positive, 96 negative, 7 stop); any inbound 32%; hard NDR 7.7% of threads (M3/M5/M6 11–13% in one shared reputation event W24/27/28, M1/M4 ≤0.5%); 40% of substantive replies ever answered (median 2.7 h, p75 72 h, p90 354 h); compliance: 37 domains / 174 touches after a human decline/redirect/stop; 48 orgs re-cold-contacted after a reply. Funnel: ≥7 calls held (18 invited), 27 contracts sent, **1 signed, 0 Zusage/Arbeitsvertrag/Arbeitsantritt/Rechnung/payment**. Fee only ever stated in a filename (2,5 Bruttomonatsgehälter); partner terms 50/50, 1st at Vertragsunterzeichnung, Nachbesetzung-only guarantee.

- **PITFALL**: ~78% of outbound in these mailboxes is warm-up bot traffic ([SNOV]/[WRM]/wsn + untagged chit-chat); the 3 .agency boxes pitch an unrelated AI service. Gate every KPI on the Gen1 subject regex ∪ clinic domain regex, exclude warm-up domains. Raw `quant_stats.json` is contaminated.
- **PITFALL**: all timestamps are UTC; Berlin = +2 h, recruiter calendars run on Europe/Kyiv (+3 h). Stage 1 clock-of-day findings are mislabeled.
- **PITFALL**: post-reply work (invites, "Unterlagen nach unserem Gespräch", contracts) came from partner mailbox ndt-group.agency (not exported) and a "Pflege Connect"-branded colleague — ≥11 sending identities, 9 exported. Visible only via cc.
- **PITFALL**: keyword flags are weak — `fee_invoice` 0% precision, `legal_complaint`/`reject` fire on OOO disclaimers, `optout` 88% third-party spam, `sensitive` = health-scam spam. Stage 1/2b M1–M6 labels are different permutations.
- **PITFALL**: Graph (M365) dumps carry no attachment names; `tools/email_enrich_graph_attachments.py` (sudo, Ivan) not yet run.
- Privacy: intermediate JSON artifacts were scrubbed (persona/person/clinic names); reports use roles, org types, thread_ids only. Data stays on the server.
- Deliverables: doc-2 (flow catalog, TASK-111.7 AC) and doc-3 (email-module architecture proposal: Deal state model, stop-list, deliverability, invoicing from the actual contract terms). Actual contract/deck PDFs in `data/email-analysis/contracts/`.
