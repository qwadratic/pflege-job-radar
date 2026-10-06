---
id: doc-2
title: Clinic email communication — flow catalog (6-month analysis)
type: specification
created_date: '2026-09-18 00:58'
updated_date: '2026-09-18 01:07'
---
# Clinic email communication — flow catalog (6-month analysis)

Basis: 62,485 unique messages from 9 mailboxes (Mar–Sep 2026), 52,562 threads. All 459 clinic-engaged threads read; 107 clinic organizations reconstructed end-to-end; 348 targeted threads (NDRs, partner copies, interviews, profiles, internal) read; deterministic ledgers over the whole corpus. Full reports: `data/email-analysis/out/report_stage1.md`, `report_stage2.md`, `report_stage2b.md`, `ledgers.md`. Timestamps UTC.

No personal data: people by role, organizations by type, evidence by `thread_id`.

## 0. Exclusion rules (what is NOT a clinic flow)

| Excluded | Size | Rule |
|---|---|---|
| Mailbox warm-up bot traffic (Snov.io / TrulyInbox) | ~78% of all outbound (~18,600 msgs) | subject tag `[SNOV]`, `[WRM]`, `wsn`, or counterpart domain with ≥4 non-clinic chit-chat threads |
| Unrelated B2B/AI-agency pitch from the 3 `.agency` mailboxes | ~450 msgs, 0 clinic content | mailbox ∈ K1–K3 |
| Third-party spam/newsletters into our inboxes | 31,771 inbound-only threads | no outbound from us to that sender |
| Auto-replies (OOO, "mailbox not monitored", NDR, calendar auto-responses) | 14% of campaign threads OOO-only; 257 bounce events | counted as signals, never as replies |
| Internal test/simulation threads (Snov SMTP self-tests, an internal candidate-introduction simulation) | 139 internal + 11 simulation threads | both ends are our own addresses |
| Gen2 "pain-point" pilot (Aug–Sep) | 437 single-touch sends, 0 clinic replies | non-clinic list, broken merge fields |

Clinic gate used: outbound subject matches `examinierte pflegefachkr*` (the one real campaign) ∪ clinic-like domain, minus warm-up domains. Precision 0.996 / recall 0.982 on labelled threads.

## 1. The population the flows live in

- Campaign "Examinierte Pflegefachkräfte für [Klinik]": **2,176 threads, 4,973 sends, ~835 clinic domains**, 6 May – 3 Aug 2026, 3 touches (d0 / +5 d / +6 d), same subject on all touches.
- Threads reaching touch 2: 71%; touch 3: 57%.
- **Human reply: 204 threads = 9.4%**; first reply after touch 1 / 2 / 3: 56 / 83 / 64.
- Sentiment of first human reply: positive 20% (41), negative 47% (96), stop 3% (7), neutral/forward/question 29% (60).
- Any inbound incl. auto: 32%. OOO-only: 14%. Hard bounce: 11.8% of threads (257 events, 8% spam/reputation-classified), concentrated on 3 of 6 sending identities in two weekly collapses.
- Organization view: 107 deal-candidate orgs; **33 reached call-scheduled or beyond; 7–8 calls held; 24–27 contracts sent; 1 signed; profiles requested by 10 orgs, delivered to 1; 3 orgs got candidate interview invites; 0 Zusage / Arbeitsvertrag / Arbeitsantritt / Rechnung.**
- A topic spans several threads: of live clinic topics, 84% >1 thread, 65% >1 of our mailboxes, 41% >1 clinic person. The extra threads are our own parallel sequences, not the clinic's process.

## 2. Flows

Notation: **O** = our message, **T** = clinic message, **P** = partner "Projektleiter" mailbox (outside the export, seen only as copies). Frequencies are per campaign thread unless stated.

### F1 — Cold sequence into silence (modal)
- Trigger: scraped contact at an org with a public nursing vacancy; no role filter (GF, HR, PDL, Sekretariat, Verwaltung all get the same script).
- Messages: O cold_intro (≈96 words: "wir haben gesehen, dass [Org] Pflegefachkräfte sucht" + 5 qualification bullets + "Direktvermittlung, keine Leiharbeit" + call CTA) → O bump +5 d ("kurz sicherstellen…", offers anonymised Kurzprofile) → O breakup +6 d ("ein letztes Mal… jetzt oder später?").
- Next step: none; the same org is often re-hit by another persona within days (F13).
- Frequency: ≈65–75% of campaign threads. Dies after touch 3.

### F2 — Dead on arrival (bounce / spam-classified)
- Trigger: clinic gateway rejects touch 1 within seconds (5.7.1 "classified as spam", reputation, restricted group 5.7.133, unknown user 5.1.x, mail loop 5.4.12).
- Messages: O cold_intro → T NDR. The thread stops, but the org is re-targeted later in 52% of cases (164 new threads), 41 of them after a 5.7.x rejection.
- Frequency: 11.8% of campaign threads; by identity 16% / 16% / 16% / 10% / 1% / 0.4%; 91–97 domains rejected at least once.
- Should: stop the whole domain on 5.7.x, suppress the address on 5.1.x, retire an identity whose weekly bounce rate spikes.

### F3 — Out-of-office loop
- Trigger: the addressee is absent (vacation, left the company, mailbox decommissioned) and an auto-responder answers every touch.
- Messages: O touch → T OOO (auto) → O bump → T OOO → O breakup → T OOO.
- Frequency: 304 threads (14%) had OOO as the only inbound; 11 OOOs said "mailbox no longer monitored, contact [role]"; OOOs named deputies/successors in ≥4 deal orgs — never contacted.
- Should: parse return date and named deputy; re-time the sequence; treat "not monitored" as a redirect.

### F4 — Polite decline
- Trigger: any touch; 62% of declines arrive after touch 2–3.
- Messages: O touch → T reply_decline (median 1.5 h after the touch; PDL / HR / Sekretariat / GF): "Stellenplan erfüllt", "zufrieden mit Kooperationspartnern", "personell gut aufgestellt", "eigene internationale Personalgewinnung". ~10% name a date or "bei Bedarf".
- Next step observed: none, or a new cold thread to another person at the same org (21% of declined domains got 128 further touches).
- Frequency: ≈47% of human replies (96); x_declined 148 of 459 clinic threads.
- Should: soft cooldown 3–6 months on the site; snooze to the stated date; never a new cold sequence on the same site.

### F5 — Conditional / policy decline that is an opening
- Trigger: clinic distinguishes AÜG from Vermittlung, or names departments / a fee-only condition.
- Messages: T "kein Leasing, aber Vermittlung gegen Honorar in OP/Anästhesie möglich — Konditionen bitte schriftlich" → O deck + Rahmenvertrag PDF (same day – 2 d) → T silence | T forwards to GF | T "wir unterschreiben sicher keinen Vertrag, schicken Sie einfach Kandidaten".
- Frequency: 4 orgs raised AÜ vs Vermittlung first; 7–8 "no agencies/leasing" policy replies; 12 pricing questions.
- Should: one-line correction ("Direktvermittlung, Festeinstellung bei Ihnen, Honorar nur bei Einstellung") + Konditionen with the number in text + one anonymised Kurzprofil; Einzelauftrag instead of Rahmenvertrag.

### F6 — Internal redirect / forward
- Trigger: first recipient is not the decision-maker (GF, Verwaltung, Sekretariat, Fortbildung, wrong site).
- Messages: O touch → T "weitergeleitet an PDL / Recruiting / Kollegen" (often the whole reply) → nothing | the named person replies days–weeks later, often in a **new thread** from their own address.
- Frequency: 22 redirects + 19 forwards_to_hr + 7 forwards_to_pdl in the corpus; ≈7% of human replies; 70% of redirected domains still got further cold touches (43); 9 deal orgs died at "redirect not followed"; one warm hand-off bounced on our own address typo.
- Should: reroute the same topic to the named role; suppress the old address; hold the topic open ("await new contact") 45 d; never restart the cadence to other names.

### F7 — Interest → documents → call proposal (core positive flow)
- Trigger: T asks price / Konditionen / qualification (B2, Anerkennung, OP/ICU/IMC experience, departments) / "senden Sie Unterlagen" / "schicken Sie Profile".
- Messages: T question (hours after a touch) → O answer median 22.8 h (p75 47 h; outliers 9–71 days): in 49% of asks the deck + Personalvermittlungsvertrag PDF pair regardless of the question; fee stated in text 0× before a call → O call proposal with the partner Projektleiter → T requests_call (→ F8) | T "take it to the GF" and returns 1–3 weeks later | T silence.
- Frequency: 39 orgs with an explicit ask: call 18, Konditionen/fee/contract 15, candidates/profiles 10, qualification 7, AÜG? 4, Infomaterial 3, housing 1, Gütesiegel/seat 1.
- Dies: most often after the documents (no call booked); second after the call proposal (no slot chosen). Price questions answered with a PDF were declined within hours in 2 cases.
- Should: answer the question in the body (number, departments, availability); attach a 1-page Konditionen + 1 Kurzprofil; ask for the platform and a phone fallback.

### F8 — Call flow (multi-thread, multi-mailbox)
- Trigger: T requests a call or accepts a proposal.
- Messages: O/P propose slots (median 42 h after the clinic's opening) → T accepts (median 1.1 h) → **P sends the calendar invite in a new thread** ("Kennenlerngespräch | [Org]"; 18 invites, 14 accepts, 2 declines) → T "Teams-Link?" / "wir nutzen nur Webex" → link resend on another platform | invite never arrives | duplicate invites 19 s–3 min apart → call held (content invisible; 7–8 proven) → **P "Unterlagen nach unserem Gespräch – Präsentation und Vertrag"** (same day – 2 d; five of six such sends dated the same day = backlog cleared in one sitting) → O/P feedback bump +7–14 d → F9 | F10 | silence.
- Frequency: 33 orgs call-scheduled; 5 lost at logistics (2 undelivered invites, 2 platform mismatch / missed clinic-organised Webex, 1 confirmed slot not honoured by us); clinic-preferred platforms: Teams default, Webex 2, Zoom 1, phone 3.
- Should: capture platform + phone in the proposal; invite from a monitored calendar within 24 h of acceptance; delivery check; explicit `held` / `no_show` / `cancelled` entered by the human on the call; post-call artefact ≤24 h with a concrete next step, not "bump".

### F9 — Contract flow
- Trigger: post-call, or the clinic asks for Konditionen/AGB, or reflexively on the first positive reply (≥12 of 24 contract sends were pre-call).
- Messages: O/P Personalvermittlungsvertrag PDF (4 filename variants, no version control; fee inside: 3 / 2 Brutto-Monatsgehälter by category, 50/25/25 at Arbeitsvertrag signature / month 3 / month 6, 14-day Zahlungsziel, 3-month Nachbesetzung guarantee, Polish Sp. z o.o.) → T contracts clerk / GF questions (Gütesiegel, Garantie, company seat, payments abroad) → O answers → T returns countersigned scan (1) | O chases 1–2× | T "Sitz nicht in Deutschland, keine Zahlungen ins Ausland, kein Gütesiegel" (1) | T counter-proposal on terms (1: wanted tranche 1 at Arbeitsbeginn; partner held signature, offered 50/50 and free re-placement) | silence (11 of 22).
- Frequency: 24–27 orgs sent; 1 signed (then zero candidate action; clinic chased us after 11 days); 1 terms agreed unsigned; 1 refused outright ("send candidates instead"); 1 compliance decline.
- Should: contract only after a call or on request; Einzelauftrag/Auftragsbestätigung as the light default; after signature a candidate action within 48 h.

### F10 — Candidate presentation and interview
- Trigger: contract signed, or clinic says "schicken Sie Profile / Bewerbungsunterlagen".
- Messages: O/P anonymised profiles (12 PDFs once; otherwise "Vorstellung von [Name]" mails with the candidate's full name in the subject — contradicting the promised anonymisation) → T selects / asks experience detail → P/O "Video-Interview [Org] – [Kandidat] (date)" invite → T accept | reschedule | same-day cancel → Hospitationstag proposed (1 real) → skill-match refusal (ICU/IMC missing, 1).
- Frequency: profiles requested 10 orgs, delivered 1; interview invites 3 orgs / 4 candidates (Aug); outcomes 0; housing raised as a blocker in 3 of the few candidate-level threads.
- Should: Kurzprofil generator (region, Anerkennung Bundesland, department experience, availability, housing status), ≤48 h; interview invite from the module with candidate consent recorded; outcome entered by the human.

### F11 — Inbound-led
- Trigger: clinic writes first (a Pflegedirektor asking for a phone number; a "Vermittlung von Pflegefachkräften" inquiry).
- Messages: T inquiry → O slots (8.5 h) → F8 → profiles → bump.
- Next step: treat as a hot Deal: same-day answer with phone + slots; owner assigned; profile ≤48 h after the call.
- Frequency: 2 in the corpus; the most progressed non-contract topic. Handled well except the Teams link.

### F12 — Stop / opt-out
- Trigger: T "bitte keine weiteren E-Mails", "aus dem Verteiler streichen", "unwiderruflich löschen (DSGVO)", "widerspreche der Zusendung von Werbung"; often the 2nd person at the same clinic after touch 2–3.
- Messages: T demand → O silence (correct) → suppression only if the keyword tripped; 2 of 8 explicit stops were followed by 3 more touches to the same person.
- Frequency: 8 explicit stops in the campaign; ≈9 hard stops among 87 real pushback messages; 2–3 legally worded.
- Should: person-level hard stop immediately; written deletion confirmation on DSGVO wording; audit log; "mailbox not monitored" and redirects trigger address suppression, not a restart.

### F13 — Org-level spray (multi-persona, multi-contact)
- Trigger: scraper returns several names at one Träger; 4–6 personas run parallel cadences.
- Messages: persona A → person 1 (d0); persona B → person 2 (d+1); … each with its own 3-touch cadence; 6 sites hit within 20 minutes; 8 people at 6 sites in 48 h; 4 roles in 24 h; one Sekretariat replies for all; A declines while B is still bumped; 48 orgs got a **new** cold thread after a human reply (122 threads → 5 positive, 6 declines).
- Visible damage: a Chefarzt complained about four brands/addresses at interview stage; one-word "Delete" replies from GFs after a re-cold sequence; "Die Info ist nicht korrekt" challenges to the "we saw you are hiring" hook (3).
- Frequency: 497 of 832 orgs had >1 thread, 424 >1 of our mailboxes; deal orgs: median 3 of our targeted people (p90 11).
- Should: one topic per org; one contact at a time; cross-persona lock; no new cold sequence while a topic is open, declined-soft or stopped.

### F14 — Candidate-side leakage into clinic mailboxes
- Trigger: candidate applications, follow-ups and interview logistics are handled from the same mailboxes and threads as clinic outreach.
- Messages: "Ihre Bewerbung als Pflegefachkraft in …" threads, WhatsApp-number follow-ups to applicants (17), candidate freemail replies (12), interview invites to personal addresses with the partner in cc.
- Frequency: 11 application threads + 21 candidate-named subjects in campaign mailboxes; 0 candidate-side Vermittlungsverträge observed.
- Should: separate candidate pipeline and mailbox; § 296 SGB III Schriftform; consent record before any profile leaves; § 298 retention.

### F15 — Excluded flows (see §0)
- Trigger: traffic that is not clinic communication (warm-up ping-pong, K1–K3 pitch, Gen2 pilot, our auto-reply layer accepting vendor calls under random company names).
- Messages: bot-to-bot small talk; unrelated AI-service pitch; "Hi Freund" stock templates; auto "let's book a call" to vendor spam.
- Next step: exclude at ingestion; retire the auto-reply layer (reputational risk if a clinic ever lands in it).
- Frequency: ~78% of all outbound; ~450 K-mailbox sends; 437 Gen2 threads; ~25 auto-reply threads.

### F16 — Internal candidate-introduction loop (new)
- Trigger: an 18 Aug test of the intended candidate flow between our own alias mailboxes with simulated clinic replies.
- Messages: profile → experience Q&A → slots → invite → reminder → "positives Feedback" → Hospitationstag.
- Next step: exclude from every funnel count; use only as the intended-flow spec.
- Frequency: 1 strand, 11 threads, 8+ messages.

### F17 — Post-signature void (new)
- Trigger: clinic returns the countersigned contract.
- Messages: T signed scan → (11 days of our silence) → T "what happens next?" → O housing-logistics question, no candidate.
- Next step: candidate action (shortlist) ≤48 h after any signature; owner + due date set automatically.
- Frequency: 1 case = the only signature in the corpus.

### F18 — Clinic-organised call missed (new)
- Trigger: clinic sends its own meeting invite on its platform (Webex) after we proposed Teams.
- Messages: T Webex invite → (no join, no confirmation from us) → T "wir hatten zu einem Webex-Termin eingeladen — ist er nicht angekommen?" → second round fails the same way ("Teams funktioniert bei uns nicht").
- Next step: capture platform preference and a phone number in the first proposal; accept the clinic's platform; confirm receipt of any inbound invite.
- Frequency: 1 org, 2 rounds; platform mismatch in 2 of the 33 deep deals.

### F19 — Dated deferral without snooze (new)
- Trigger: clinic defers with a date or condition ("im Herbst nochmals", "Anfang 2027", "bei Bedarf", Trägerwechsel).
- Messages: T deferral → nothing from us at the stated time (or a new cold sequence from another persona instead).
- Next step: snooze the Deal to the stated date; dated re-approach quoting their words; no cold sequence meanwhile.
- Frequency: 17 `delays_later` threads, 1 explicit dated deferral; 0 dated re-approaches observed.

### F20 — Clinic chases us (new)
- Trigger: we owe the next step (status after signature, the invite, post-call feedback, the missed call) and miss it.
- Messages: T follow-up ("haben Sie schon Neuigkeiten?", "bislang keinen Teams-Link erhalten", "ich hatte den Eindruck, dass Sie interessiert sind") → O apology + the overdue artefact.
- Next step: every such message is an SLA-breach marker; owner escalation; the artefact goes out the same day.
- Frequency: 4 orgs / 6 messages.

## 3. Timings that matter

| Measure | Value |
|---|---|
| Clinic first human event after a touch | median 2.2 h (p25 0.4 h, p75 29 h); declines 1.5 h; acceptances 1.1 h; questions ~40 h; forwards ~19 h |
| Our answer to a positive reply | 53% answered within 30 d; median 16.9 h, p75 52 h, p90 164 h; outliers 8, 9, 13, 13, 43, 71 days; 47% never |
| Our documents after an ask | median 22.8 h |
| Call proposal after the clinic's opening | median 42 h |
| Acceptance → call held | 2–10 d (7 d fastest clean deal) |
| Call → post-call artefact | 0–2 d; then 6–14 d of our silence in the best deals |
| Call → signed contract | 10 d (1 case) |
| Late replies | nothing human arrives >45 d after our last outbound except our own re-cold contacts |
| Send windows | data is UTC; clinic replies cluster Mon–Wed 08–10 h Berlin; nothing sent Sat/Sun |

## 4. Who is on the other side

PDL / Pflegedirektion: engaged contact in 19 of 33 deep deals; names departments; asks for candidates; asks price bluntly. GF: replies personally on small houses (11 of 33), forwards down or asks for candidates; most negative role (45% decline + all role-attributed stops). HR / Personal: cold target in most threads; drives some deals; signed the one contract. Sekretariat: replies on behalf, runs scheduling. Vertragswesen: compliance questions (Gütesiegel, seat, payments abroad). Chefarzt: negotiates in Fachkliniken. People join mid-thread in 24% of clinic threads (41% of live topics).
