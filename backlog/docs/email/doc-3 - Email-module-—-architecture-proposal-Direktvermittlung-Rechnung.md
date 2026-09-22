---
id: doc-3
title: Email module — architecture proposal (Direktvermittlung + Rechnung)
type: specification
created_date: '2026-09-18 00:58'
updated_date: '2026-09-18 01:06'
---
# Email module — architecture proposal (Direktvermittlung + Rechnung)

Purpose: the e-mail module for placing nurses into German clinics **without a long-term staffing contract, invoicing per placement**. Built from what 6 months of real correspondence show, checked against web theory. Rule: **practice outweighs theory**; theory fills gaps practice has not reached; law is a constraint, not a design driver.

Evidence base: 62,485 messages / 9 mailboxes / 459 clinic threads read in full / 107 clinic organizations reconstructed / deterministic ledgers / the actual Personalvermittlungsvertrag and deck PDFs sent to clinics / `research_de_placement.md` (theory, sourced). Companion: flow catalog (doc-2).

---

## 1. Twelve facts the design rests on (practice)

1. **The pitch works modestly and is understood.** One template ("Examinierte Pflegefachkräfte für [Klinik]", 3 touches d0/+5/+6): 2,176 threads → 9.4% human reply (204) → 20–26% of replies positive. Clinics answer fast (median 2.2 h) and ask precise questions (B2, Anerkennung, OP/ICU/IMC, departments). Touch 2 harvests most replies; touch 3 harvests declines.
2. **What clinics ask first, in order:** a call (18/39 orgs with an explicit ask), Konditionen in writing (15), candidates/profiles (10), qualification (7), "is this AÜG?" (4). The candidates-first asks are the most committed ("wir unterschreiben sicher keinen Vertrag, vermitteln Sie einfach").
3. **Our reflex answer was the wrong artefact.** 49% of asks were answered with deck + Personalvermittlungsvertrag PDF; the fee was written in a message body 0 times before a call; profiles were requested by 10 orgs and delivered to 1. "Documents, then silence" is the #1 stall (14 of 107 orgs, 10 of the 33 deep ones).
4. **Our latency is the #2 killer.** 47% of positive replies never got an answer; median 16.9 h, p90 164 h; outliers 13–71 days; clinics chased *us* in 4 deals; the only signed clinic waited 11 days and got a housing question.
5. **Roles.** PDL is the gatekeeper and the one who names departments and asks for candidates; GF replies personally on small houses and is the most negative role; HR signed the one contract; Sekretariat runs scheduling; Vertragswesen asks Gütesiegel/seat.
6. **Call logistics lose deals at the deepest point:** 5 of 33 (invite never arrived, Teams vs Webex, confirmed slot not honoured, duplicate invites). Every held call ran through a partner mailbox outside the system; no `held` outcome was ever recorded.
7. **Identity fragmentation is a real objection.** 6 look-alike persona domains + partner + a second brand: a Chefarzt complained about four brands at interview stage; a Vertragswesen clerk declined over foreign seat + no Gütesiegel + no payments abroad.
8. **Deliverability is identity-specific.** 11.8% of campaign threads bounced (8% spam/reputation); three high-volume identities collapsed together in W24 and W27–28 (16% each); low-volume identities stayed at ≤1%. 52% of bounced domains were hit again.
9. **Compliance leaks come from parallel sequences, not malice.** After a decline 20 domains got 128 further touches; after a redirect 15 got 43; 2 of 8 explicit stops got 3 more; 48 orgs got a new cold sequence after a live reply (→ 5 positive, 6 declines, 2 one-word "Delete").
10. **The unit is the organization-topic, not the thread.** Live topics: 84% >1 thread, 65% >1 of our mailboxes, 41% >1 clinic person; a 45-day window never split a live conversation; nothing human arrives >45 d after our last mail except our own re-cold contacts.
11. **Money in practice = the contract that was actually sent** (14+ clinics): fee **3 Brutto-Monatsgehälter** (Kat. A: Anerkennung + ≥2 y stationäre Erfahrung) / **2** (Kat. B); one clinic got a 2,5 variant. **Fällig nur bei tatsächlicher Einstellung**; **50% nach Unterzeichnung des Arbeitsvertrags zwischen Klinik und Kandidat, 25% nach dem 3., 25% nach dem 6. Beschäftigungsmonat**; Rechnung fällig **14 Tage** netto; gesetzliche Verzugszinsen; **3-Monats-Zufriedenheitsgarantie = einmalig kostenfreie Ersatzkraft**; Honoraranspruch auch bei Arbeitsvertrag innerhalb 12 Monaten nach Vorstellung (Umgehungsschutz); deutsches Recht, Gerichtsstand beim Auftraggeber; Auftragnehmer = polnische Sp. z o.o. Clinic reactions: 1 signed; 1 wanted tranche 1 at Arbeitsbeginn (refused; partner offered 50/50 + free re-placement); 1 "wir zahlen keine Vermittlungsgebühren"; 1 "no contract, send candidates"; 1 declined foreign seat.
12. **Nothing beyond interview was ever observed:** 0 Zusage, 0 Arbeitsvertrag, 0 Arbeitsantritt, 0 Rechnung, 0 payment in 62,485 messages. Invoicing has never been practised in these mailboxes (a partner reference to "~20 placed nurses" at another client is unverified — see §7).

---

## 2. Theory vs practice

| Topic | Theory says | Practice shows | Verdict | Design decision |
|---|---|---|---|---|
| Model name | Personalvermittlung / Direktvermittlung vs Arbeitnehmerüberlassung; no AÜG permit needed | Template and deck already say "Direktvermittlung, keine Leiharbeit"; clinics say "Festeinstellung", "kein Leasing"; 4 orgs still misread it as AÜG and lost 9–14 days | practice = theory | Line 1 of every first message: "Direktvermittlung / Festanstellung bei Ihnen, kein AÜG, Honorar nur bei Einstellung" |
| Fee word | Vermittlungsprovision / -honorar / Erfolgshonorar | Contract and deck: "Honorar"; clinics write "Vermittlungskosten", "Konditionen", "AGBs"; "Provision" appears 0× | practice wins | Documents: "Vermittlungshonorar"; replies: the clinic's word ("Konditionen") **with the number** |
| Contract form | Vermittlungsvertrag / Rahmenvertrag + Einzelaufträge / Auftragsbestätigung + AGB | One 3-page Personalvermittlungsvertrag in 4 filename variants, called Rahmen-/Kooperations-/Personalvermittlungsvertrag interchangeably; no AGB, no Auftragsbestätigung; sent pre-call in ≥12 of 24 cases; 1 signed | theory fills a gap; practice shows the full contract is the wrong first artefact | Three artefacts, one name each: **Konditionen** (1 page, in the first written answer) → **Auftragsbestätigung/Einzelauftrag** (1 page, per vacancy, after a call or on "send candidates") → **Rahmenvertrag** (only when the clinic asks or after the first placement) |
| Fee level | 15–25% of Jahresbrutto or flat ≥€5k; 1–3 Monatsbrutto | 3 / 2 Brutto-Monatsgehälter (≈25% / 17% of annual gross) — inside the band | practice wins | Keep 3/2 BMG by category; state it as a number in text |
| Due trigger | signature / Arbeitsantritt / after Probezeit; staged models common; § 652 BGB fee on success | Contract: 50% at the **nurse's Arbeitsvertrag** signature, 25% month 3, 25% month 6; one clinic pushed for tranche 1 at Arbeitsbeginn | practice wins and is § 652-conform; clinic pushback noted | Trigger = evidence of the signed Arbeitsvertrag (copy or clinic confirmation); offer Arbeitsantritt as a negotiable alternative for tranche 1 |
| Zahlungsziel | 14 or 30 days; 30 typical for institutions | Contract: 14 days | practice wins, theory warns | Default 14 d on the invoice; 30 d allowed per Auftragsbestätigung for public Träger; Mahnung logic on the agreed term |
| Guarantee | Garantiezeit to Probezeit end / 6 months; free Nachbesetzung; no refund from the nurse (BAG 2023) | Contract: 3-month free one-time Ersatzkraft; month-3/6 tranches act as the guarantee; clinic asked for refund, refused | practice wins | Guarantee window = 3 months (or Probezeit if longer, negotiable); state it in the Konditionen proactively; never a Rückzahlungsklausel toward the nurse |
| Kurzprofil | anonymised 1-page profile before CV, per vacancy | Promised in every bump; delivered once (12 PDFs); real sends carried the candidate's full name in the subject; clinics ask for "anonymisierte Lebensläufe", region- and department-specific | practice on demand, theory on format | Kurzprofil generator: region, Anerkennung (Bundesland, date), department experience, years, availability, housing status, language level; anonymised; ≤48 h after any profile ask |
| Hiring stages | Vorstellungsgespräch → Probearbeiten/Hospitation → Zusage → Arbeitsvertrag → Arbeitsantritt → Probezeit | Observed: Kennenlerngespräch (company-level) → candidate video interview → Hospitationstag proposed once → nothing beyond | practice truncated; theory supplies the tail | State machine §3.2 uses observed states up to interview and theoretical states after, flagged as unvalidated |
| Documents | 8-document lifecycle set | Seen: deck + contract pair, 1 signed scan, 12 profiles, invites. Never: AGB, Auftragsbestätigung, Zusage-Bestätigung, Arbeitsvertrag copy, Rechnung, Mahnung, Garantieschreiben, Unternehmensnachweis | theory fills the gap | Templates in §4 |
| Invoice | § 14 UStG fields; XRechnung/ZUGFeRD for B2B (transition 2026/27); § 13b reverse charge for a foreign entity; § 286/288 BGB | 0 invoices ever; one clinic refused payments abroad | theory fills the gap; law constrains | Invoice module §3.10; legal-entity decision §7 |
| UWG / consent | § 7 Abs. 2 Nr. 2 UWG: cold B2B e-mail needs consent; Impressum; opt-out; DSGVO Art. 6(1)(f) does not replace it; Art. 14 notice; Art. 21 objection | 100% of the campaign template had no Impressum and no opt-out line; 2–3 legally worded objections in 2,176 threads; stops mostly respected, declines/redirects not | law overrides | §3.12: Impressum + opt-out in every send; person-level stop; Art. 14 notice; consent/legitimate-interest record; residual first-touch exposure accepted and documented |
| Foreign-nurse documents | B2/Fachsprachenprüfung, Anerkennung/Defizitbescheid, Kenntnisprüfung, § 16d/§ 81a AufenthG, ZAV | Clinics asked only: Anerkennung (which Bundesland), B2, department experience, "how long in Germany", housing. Nobody asked about visa, Defizitbescheid, ZAV — the pitch ("Urkunde vorhanden, bereits in Deutschland") pre-empts them | practice narrows theory | Candidate record: Anerkennung + Bundesland + date, B2 certificate, department experience, residence, housing need; recognition-pipeline fields optional |
| Stop-list scope | (theory: the objecting *person*; Art. 21) | Domain-wide stops would have killed the best deal (a site-level "no need, but sister site may" reply); person-level violations are what actually happened | law + practice agree | Four scopes §3.5: person hard-stop, address suppression, site cooldown, Träger never-suppress |
| Identity | (theory: Impressum, one Anbieterkennung) | 6 personas → complaints, distrust, a foreign-seat decline; concentrating volume on 3 identities → reputation collapse | practice on both sides | One brand and legal entity; few sending domains; low volume per identity; no warm-up on real identities (§3.7) |
| Cadence / SLA | (none in theory) | Clinics answer in hours; we answer in days; touch 2 good, touch 3 toxic | practice wins | §3.4 SLAs; cadence d0/+5, then a different-angle touch 3 or none |
| Targeting | (none) | 29% of clinic replies = "kein Bedarf"; "we saw you are hiring" challenged 3×; stale data (closed hospital, retired contacts, Trägerwechsel) ≥11 cases | practice wins | Vacancy evidence stored per topic (URL + date); contact freshness check; no hook without evidence |

---

## 3. Architecture

### 3.1 Core entity: **Deal** (organization-topic), not thread, not contact

- `org_id`: Träger group where known (manual map for the ~40 multi-site groups; sibling-domain auto-replies join them) → else registrable domain → freemail: full address.
- One **open Deal per org** at a time (site/department may open a sibling Deal only when a different decision-maker states a different need).
- All our identities are one side: persona mailboxes, the partner Projektleiter mailbox, any colleague brand. Every outbound must carry a `deal_id`; a send without an allowing state is refused.
- Every inbound from anyone at the org (any thread, any of our mailboxes, calendar responses, partner copies) attaches to the Deal.
- Fields: `owner` (human), `next_action` + `due`, `counterparts[]` (address, role, first_seen, consent/stop status), `platform_pref`, `phone`, `vacancy_evidence`, `stage`, `stage_since`, `documents_sent[]` (type, version, date), `campaign_tag`.
- Lifetime: `open` while activity; `dormant` after 45 d without activity; any human clinic inbound reopens the same Deal at any age; `declined_soft` reopens on clinic inbound or on its snooze date; `stopped` never reopens automatically. No 90-day window (nothing arrives there).

### 3.2 State machine — events promote, humans confirm

| State | Entered by (event) | Observed? | Exit / notes |
|---|---|---|---|
| `contacted` | first outbound sent | 2,176 | max 3 touches; any human inbound leaves this state |
| `replied` | human inbound (not OOO/NDR/calendar-auto) | 204 | classify: question / interest / call request / decline / redirect / stop |
| `qualified` | clinic states need (department, count, conditions asked) | ~40 | SLA: answer same working day, in text |
| `call_booked` | slot accepted (calendar response or human) | 33 orgs | invite from the module ≤24 h; platform + phone stored |
| `call_held` | **human enters** held / no_show / cancelled | 7–8 | post-call artefact ≤24 h with a concrete next step |
| `shortlist_sent` | Kurzprofil(e) sent (module-generated) | 1 | ≤48 h after a profile ask; ≤5 working days after a call |
| `auftrag_confirmed` | Auftragsbestätigung returned / Rahmenvertrag signed | 1 | not required before shortlist |
| `interview_scheduled` | candidate interview accepted | 3 orgs | invite from the module; candidate consent recorded |
| `interview_held` | human enters outcome | 0 | → `hospitation` / `zusage` / `rejected_candidate` |
| `hospitation` | proposed/held (human) | 1 proposed | theory tail — unvalidated |
| `zusage` | clinic offer confirmed (human) | 0 | unvalidated |
| `arbeitsvertrag_signed` | signed contract copy / written confirmation | 0 | **→ Rechnung 1 (50%)** |
| `arbeitsantritt` | start confirmed | 0 | guarantee window starts |
| `month3` / `month6` | scheduler + employment confirmed (human) | 0 | **→ Rechnung 2 (25%) / Rechnung 3 (25%)** |
| `closed_won` | Rechnung 3 paid | 0 | |
| `guarantee_case` | termination within 3 months reported | 0 | → free Nachbesetzung Deal, credit note if needed |
| terminal: `declined_soft` (snooze date or 3–6 months) · `declined_policy` (fee-only / no-agency segment) · `stopped` (person stop → Deal continues only with other consenting contacts) · `bounced_gateway` (domain 5.7.x) · `lost_after_call` (no clinic activity 45 d after a call) | | | |

Rules learned from practice: no state may be skipped by a document send; `contract_sent` is **not** a state (it is a document event, allowed from `qualified` on request, from `call_held` by default); a decline by one person does not end a Deal with another engaged person at the same org; a redirect creates `await_contact` (45 d) and suppresses the old address.

### 3.3 Ingestion (all identities, one store)

- Sources: Microsoft Graph for M365 boxes (own app, delegated or app-only — TASK-111.2), IMAP for Zoho; **the partner mailbox and every colleague brand must be ingested or replaced** — a send the module cannot see breaks the state model (all 8 held calls ran through it).
- Per message: dedupe by Message-ID; thread via In-Reply-To/References/conversationId; classify auto vs human (OOO/NDR/calendar/ticket regexes + sender patterns); parse NDR codes (5.7.x policy, 5.1.x unknown user, 5.4.x loop) from body; parse calendar responses (Zugesagt/Abgelehnt/Mit Vorbehalt); extract OOO return dates and named deputies; attachments with name/type/size (M365 needs the attachment call — today absent).
- Exclusions at ingestion: warm-up tags/domains, non-campaign mailboxes, vendor spam — never in KPIs.
- Campaign tag and `deal_id` stamped on every outbound; reply/bounce/opt-out computed per campaign and per Deal, never per raw mailbox.

### 3.4 Routing, ownership, SLA

- Every human inbound gets an owner within minutes (Deal owner; fallback: on-call). K-mailbox-style orphan inboxes do not exist in the new model.
- SLAs (from clinic behaviour): positive/question reply → answer **same working day**; call acceptance → invite **≤24 h**, delivery-checked; post-call artefact **≤24 h**; profile ask → Kurzprofil **≤48 h**; redirect → new contact addressed **≤2 working days**.
- Escalation: SLA breach → owner + lead; second breach → lead takes the Deal. Dashboard = Deals with `due` in the past.
- **No automatic replies to clinics.** The old auto-reply layer answered vendor spam with "let's book a call" under random company names. Drafts are generated; a human sends.
- Cadence stops on any human reply; bumps only from the module; a different-angle touch 3 (or none) replaces the breakup.

### 3.5 Stop-list — four scopes, explicit precedence

| Scope | Trigger | Effect | Lift |
|---|---|---|---|
| **Person hard-stop** (legal) | "keine weiteren E-Mails", DSGVO/Werbewiderspruch wording, complaint, 2nd decline by the same person | no send to this address ever; written confirmation on DSGVO wording; audit log entry | never automatic; lead + documented consent only |
| **Address suppression** | NDR 5.1.x, "mailbox not monitored", "no longer employed", redirect | address frozen; Deal continues with the named replacement | when the org names a new contact |
| **Site cooldown** | polite decline (no need / has provider / Stellenplan) | no new cold sequence to the site for 3–6 months (or the stated date); open Deal with another engaged person at the same org is **not** affected | date, or clinic inbound |
| **Träger** | never auto-suppressed | sister sites remain reachable — the best deal began with "no need here, but a sister site may" | — |

Detection: keyword + classifier + human confirmation before suppression (the old flags were 88% false positives); a suppression proposal expires unconfirmed after 1 working day, except literal stop phrases which apply immediately. Precedence: a person stop binds that person even inside an active `call_booked` Deal; the Deal continues with others. Propagation: the partner mailbox and colleague brands send **through** the module, so the list applies to them.

### 3.6 Deliverability — measured, not hoped

- Read NDRs (in-thread and separate-thread) and act: 5.7.x → domain `bounced_gateway` for 90 d, switch channel (phone) or identity; 5.1.1 → permanent address suppression; 5.4.x loop → address suppression + manual check.
- Caps: ≤1 outbound cold touch per org per week across all identities (cross-persona lock); ≤ N sends/day per identity (start 40–60, conversation model needs far less); pause an identity whose weekly bounce rate exceeds 3%; retire at 10%.
- No warm-up traffic on identities that send real mail; if warm-up is needed, separate domains never used for outreach.
- Health per identity/domain on the dashboard: bounce %, 5.7.x %, reply %, opt-out %, per week.

### 3.7 Identity

- One brand, one legal entity, one Impressum, one Unternehmensnachweis pack (Gewerbeanmeldung, USt-IdNr./NIP, references, AGB) attached to the first substantive answer — this answers the Gütesiegel/seat questions before they cost the deal.
- 2–3 sending domains under the brand, low volume each (the conversation model replaces the 250–460/week blast); persona = real named recruiter; the partner Projektleiter is the same brand.
- Existing 6 persona domains and their ~830 contacted orgs: contact again only inside an existing Deal or after the cooldown, under the new identity, with the Art. 14 notice (§3.12); the three burned identities are not reused.

### 3.8 Answer policy — what the clinic asked → what we send (the branch)

| Clinic ask | Send (≤ same working day) | Do not send |
|---|---|---|
| Price / Konditionen / AGB | number in the body (3/2 BMG, 50/25/25, 14 d, 3-month guarantee) + 1-page Konditionen + Unternehmensnachweis + one call slot offer | the full Rahmenvertrag |
| "Send candidates / profiles / Bewerbungsunterlagen" | 1–3 anonymised Kurzprofile ≤48 h + Einzelauftrag/Auftragsbestätigung (1 page) | contract-first; a housing question |
| Call request | 3 slots + platform question (Teams/Webex/phone) + our phone number; invite ≤24 h after acceptance | a bump |
| Info / Unterlagen "unverbindlich" | deck + Konditionen | contract |
| "Is this AÜG?" | one-line correction + Konditionen | deck only |
| Qualification (B2, Anerkennung, department) | answer in text with the current pool for that department/region | generic bullets |
| Redirect / forward | same-thread mail to the named role within 2 working days | new cold sequence |
| Decline with date | snooze; dated re-approach quoting their words | breakup |
| Stop | silence + written confirmation if DSGVO wording | anything |

### 3.9 Documents (templates to build — TASK-111.8)

German, one name and one version each, generated from Deal + candidate data:
1. **Erstansprache** (touch 1) with Impressum + opt-out line; evidence-based hook or none.
2. **Nachfass** (touch 2, +5 d) offering one concrete Kurzprofil.
3. **Konditionen** (1 page): Direktvermittlung/Festanstellung; Honorar 3/2 BMG by category; 50% bei Arbeitsvertrag, 25% nach 3., 25% nach 6. Monat; 14 Tage netto; 3-Monats-Nachbesetzungsgarantie; 12-Monats-Schutz; Datenschutz.
4. **Unternehmensnachweis** (1 page).
5. **Kurzprofil** (anonymised, 1 page).
6. **Auftragsbestätigung / Einzelauftrag** (1 page, per vacancy).
7. **Rahmenvertrag** (cleaned current contract; versioned; on request).
8. **Terminvorschlag / Einladung** (with platform + phone fallback).
9. **Unterlagen nach dem Gespräch** (next step named, dated).
10. **Vorstellungs-/Interviewbestätigung** (candidate + clinic).
11. **Zusage-Bestätigung** and **Arbeitsvertrag-Eingangsbestätigung** (starts invoicing).
12. **Rechnung** ×3 (see §3.10), **Zahlungserinnerung**, **Mahnung 1/2**, **Gutschrift** (guarantee case), **Nachbesetzungsschreiben**.
13. **Absage-/Cooldown-Antwort**, **DSGVO-Löschbestätigung**.

### 3.10 Invoicing module (theory fills a gap; contract terms are practice)

- **Triggers** (from the contract actually sent): Rechnung 1 = 50% on signed Arbeitsvertrag (evidence: copy or clinic confirmation, stored); Rechnung 2 = 25% after the 3rd, Rechnung 3 = 25% after the 6th Beschäftigungsmonat (scheduler from Arbeitsantritt; human confirms "still employed" — this is also the guarantee check). Base: Brutto-Monatsgehalt from the Arbeitsvertrag (or the Auftragsbestätigung if the clinic will not share the contract). Category A/B fixed at Auftragsbestätigung.
- **Mandatory fields (§ 14 Abs. 4 UStG):** names/addresses; Steuernummer or USt-IdNr.; date; unique sequential Rechnungsnummer; Leistungsbeschreibung ("Vermittlungshonorar für die Festanstellung einer Pflegefachkraft, Kat. A, Rate 1/3"); Leistungszeitpunkt (date of the Arbeitsvertrag); net, tax rate, tax, gross; Zahlungsziel (14 d default, 30 d if agreed); reference to Auftrag/Deal.
- **VAT:** German entity → 19% USt (hospitals under § 4 Nr. 14 UStG cannot deduct it — price it in); foreign entity → net invoice with "Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG)" and the clinic's USt-IdNr. The "20% cheaper without German VAT" pitch is wrong either way — drop it.
- **Format:** PDF + XRechnung/ZUGFeRD from day one (public Träger already require XRechnung; B2B e-invoice obligation is phasing in through 2027).
- **States:** issued → due → paid | overdue → Zahlungserinnerung (+7 d) → Mahnung 1 (+14 d, § 286 BGB default 30 d after due for B2B) → Mahnung 2 (+14 d, Verzugszinsen § 288 BGB Basiszins + 9 pp, €40 pauschal) → escalation. Guarantee case → Gutschrift or suspension of Rechnung 2/3 per contract.
- **Ledger:** every invoice linked to Deal, candidate, contract version, evidence document; export for accounting.

### 3.11 Candidate side (kept out of clinic mailboxes)

- Separate candidate pipeline and mailbox; the Deal only references a candidate id.
- Candidate record: Anerkennung (Bundesland, date, Urkunde on file), B2/Fachsprache certificate, department experience (OP/OTA, Anästhesie, ICU/IMC, Normalstation, Geriatrie/Reha), years, current residence + willingness to relocate, housing need, availability; **no nationality/origin field used for selection** (AGG).
- § 296/297 SGB III: Vermittlungsvertrag with the candidate in Schriftform, no fee to the nurse, no Rückzahlungsklausel (BAG 2023); consent to share the anonymised profile recorded before any send; § 298 SGB III retention (3 years business records; candidate documents returned/deleted after the placement ends).
- Housing: a checklist item and a question to the clinic (Wohnraum? Zuschuss?), never a money flow through us to the nurse (practice showed prices for apartments quoted to candidates — stop).

### 3.12 Compliance layer

- Every send: Impressum block, one-click opt-out line, campaign tag, Deal id, consent/legitimate-interest record for the recipient (source of the address, vacancy evidence, date).
- Art. 14 DSGVO notice at first contact (where the address came from, rights, objection); Art. 15/17/21 requests serviced across all identities from one place (the Deal store).
- UWG § 7 Abs. 2 Nr. 2: cold B2B e-mail without consent remains exposed; mitigation = evidence-based, low-volume, person-level stop, immediate compliance; the residual risk is a management decision, documented.
- Records: audit log of every suppressed send and every stop; written DSGVO deletion confirmations archived.

### 3.13 Analytics by construction

Reply / positive / call / shortlist / interview / contract / invoice per campaign, per identity, per Deal stage; bounce and opt-out per identity per week; SLA compliance per owner; funnel in orgs, not threads. Warm-up and non-campaign traffic never enter.

---

## 4. Answers to the open questions (critics' list)

| Question | Answer |
|---|---|
| State-bearing entity and merge key | Deal = org-topic; key Träger > domain > address; split only on new need/decision-maker; 45-d dormancy; reopen on any clinic inbound; `stopped` never auto-reopens (§3.1–3.2) |
| Stop-list scope | person hard-stop (legal), address suppression, site cooldown, Träger never-suppress; human-confirmed detection; precedence: person stop binds the person, not the Deal (§3.5) |
| Invoice trigger | signed **Arbeitsvertrag of the nurse** (contract clause 3.1/4.1, § 652 BGB), then month 3 / month 6; Arbeitsantritt negotiable for tranche 1 (§3.10) |
| Candidate-first vs contract-first | branch on what the clinic asked (§3.8); Auftragsbestätigung as the light artefact; Rahmenvertrag on request or post-call; the no-contract/candidates-only relationship is served by Einzelauftrag per vacancy |
| Partner mailbox | ingested or replaced; all sends through the module; `held/no_show` entered by the human on the call (§3.3, §3.4) |
| Deliverability | NDR-driven suppression, per-org and per-identity caps, cross-persona lock, identity pause/retire thresholds, no warm-up on real identities (§3.6) |
| Identity and entity | one brand, one entity (decision in §7), few low-volume domains, Unternehmensnachweis pack (§3.7) |
| Consent and provenance | address source + vacancy evidence + Art. 14 notice per contact; opt-out and Impressum in every send; Art. 15/17/21 from one store (§3.12) |
| SLA and ownership | owner per inbound within minutes; same-day / 24 h / 48 h SLAs; escalation; no auto-replies (§3.4) |
| Attachments / documents | Graph attachment ingestion; one named, versioned document per type; § 298 retention (§3.3, §3.9) |
| Targeting | vacancy evidence stored; contact freshness; 29% "no need" is a list problem, not a message problem (§2 last row) |

---

## 5. What to keep from the old operation

The touch-1 skeleton (concrete qualification bullets, Direktvermittlung framing, low-friction call CTA); touch 2 at +5 d; same-day document/answer when hot; a named Projektleiter and a calendar invite; the contract's commercial terms (3/2 BMG, 50/25/25, 3-month guarantee, 12-month protection) — they were accepted or negotiated, never the reason for a loss.

## 6. What to stop

Six look-alike personas and warm-up on real identities; contract as the answer to any question; touch-3 breakup; parallel sequences to one org; re-cold contacts after a reply; sending after NDR/OOO-redirect/decline; the auto-reply layer; candidate correspondence in clinic mailboxes; the "no German VAT, 20% cheaper" line; the unverifiable "we saw you are hiring" hook.

## 7. Decisions needed from Ivan

1. **Legal entity and brand** for the new module: German entity (19% USt, but no seat/payment objections) vs the existing foreign entity (§ 13b reverse charge; one clinic already declined on seat). The price to the clinic is the same either way; the objection risk is not.
2. **Partner mailbox**: ingest (Graph/IMAP, needs the partner's consent) or move calls/contracts into the module.
3. **Verify the "~20 nurses placed" reference client**: if real, obtain that client's contract, invoices and fee history — it would be the only invoicing practice we have.
4. **Run the M365 attachment enrichment** (`tools/email_enrich_graph_attachments.py`, sudo) to see the candidate-stage documents sent in August.
5. **TASK-111.2 own OAuth app** (needed to ingest all mailboxes without the colleague's shared cache); **TASK-111.5** IMAP on boxes 2/4/5 and the dead password.
6. Fee category rule (A/B) and whether Arbeitsantritt may replace signature for tranche 1.

## 8. Build order

1. Deal store + ingestion (all identities, NDR/OOO/calendar parsing, warm-up exclusion) + dashboard of Deals with due actions.
2. Stop-list (4 scopes) + send gate + audit log.
3. Answer policy + document generator (Konditionen, Kurzprofil, Auftragsbestätigung, Unternehmensnachweis) + SLA alerts.
4. Calendar/invite handling with platform + phone, `held` entry, post-call artefact.
5. Candidate pipeline separation + consent records.
6. Invoicing (3 triggers, § 14 fields, XRechnung/ZUGFeRD, Mahnung states).
7. Analytics.
