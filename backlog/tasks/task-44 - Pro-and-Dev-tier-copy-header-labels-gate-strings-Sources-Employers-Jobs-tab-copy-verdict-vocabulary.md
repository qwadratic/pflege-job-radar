---
id: TASK-44
title: >-
  Pro and Dev tier copy: header labels, gate strings, Sources/Employers/Jobs tab
  copy, verdict vocabulary
status: To Do
assignee: []
created_date: '2026-09-10 18:08'
labels:
  - frontend
  - copy
  - pro
  - dev
dependencies: []
ordinal: 44000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan outlined three tiers on 2026-09-10 (Clawl chat, 16:35): Simple at / (public), Pro at /pro (login root/toor or exe.dev; settings, richer filtering, embedded Simple, agent-key read-only view), Dev at /dev (exe.dev login, one approved email; tabs Sources with Clawl inside, Employers by type, Jobs unique per employer, full docs). The frontend IA work belongs to the backend/frontend sessions; this task carries the wording so every tier, tab, gate and verdict uses one vocabulary in DE and EN. Full rationale and the complete tables were hosted at /skill/reviews/copy/pro-dev.html (deleted end of day); the durable strings are below.

Tier pitches (card + gate lead):
- Simple: DE "Jede Klinik. Jede Stelle. Mit Nachweis." / EN "Every hospital. Every job. With proof."
- Pro: DE "Dieselben Daten. Deine Filter, deine Einstellungen." / EN "Same data. Your filters, your settings."
- Dev: DE "Der Crawler hinter der Seite. Quellen, Arbeitgeber, Stellen. Lokal." / EN "The crawler behind the site. Sources, employers, jobs. Local."
- GDPR line under the Dev pitch, never the word "compliant" alone: DE "Nur öffentliche Stellenanzeigen. Keine Personendaten. Datensatz auf deinem Host, keine Google-APIs." / EN "Public job postings only. No personal data. Dataset on your host, no Google APIs."

Headers:
- Pro: Kliniken · Stellen · Einstellungen · Für Agenten (n_docs renamed) + green button "Clawl →" (tooltip DE "Der Crawler. Dev-Zugang mit exe.dev.") + Abmelden. Kosten and Clawl tabs leave Pro.
- Dev: Quellen · Arbeitgeber · Stellen · Docs (EN Sources · Employers · Jobs · Docs). Wordmark suffix PRO / DEV in small grey; no other tier badge.

Gate strings:
- g_lead (Pro): DE "Pro: dieselben Daten, deine Filter und Einstellungen. Anmelden mit Benutzername und Passwort oder mit exe.dev." / EN "Pro: same data, your filters and settings. Sign in with username and password, or with exe.dev."
- g_default_banner (red while default_credentials): DE "Standard-Zugangsdaten aktiv. Jeder mit Zugang zum Port kommt rein. Unter Einstellungen → Passwort ändern." / EN "Default credentials active. Anyone who can reach the port gets in. Change them under Settings → Password."
- g_agent_lead: DE "Agent-Key eingeben. Öffnet die Lese-Ansicht und die Skill-Datei." / EN "Enter the agent key. Opens the read-only view and the skill file."
- g_dev_lead: DE "Clawl ist der Dev-Zugang. Anmeldung nur mit exe.dev und freigegebener E-Mail." / EN "Clawl is the Dev tier. Sign-in only with exe.dev and an approved email."
- g_dev_stub (reached /dev without exe.dev): DE "Hier endet Pro. Clawl braucht ein exe.dev-Konto. Zurück zu Pro." / EN "Pro ends here. Clawl needs an exe.dev account. Back to Pro."
- g_dev_denied: DE "Angemeldet als {email}. Diese Adresse hat keinen Dev-Zugang." / EN "Signed in as {email}. This address has no Dev access."
- No string anywhere says "sicher", "geschützt" or "secure" while docs/auth.md leaves the login rate limiter undecided.

Dev → Quellen: lead DE "Alles, was in den Datensatz fließt. Lokale SQLite, keine externe API außer den Boards selbst." Cards: "Krankenhausplan (PDF) · Stand {year} · {n} Standorte · Neu einlesen" (empty "Kein PDF geladen. PDF hochladen."), "Geokodierung (Datei) · {n} Orte mit Koordinaten · {m} ohne · Datei ersetzen" (empty "Keine Geodatei. Ohne Koordinaten keine Karte, keine Umkreissuche."), "Gesammelte Quellen · {n} Boards · {k} Adapter · {f} über Firecrawl", "Clawl · Läufe · Zeitpläne · Hunter · Abdeckung · Kosten". Run states: wartet · läuft · fertig · fehlgeschlagen · abgebrochen. Truncated run renders "abgebrochen: Budget · {credits} Credits · nicht vollständig", never "fertig".

Feature-matrix verdict labels (six, never merged): supported=belegt/supported, partial=teilweise/partial, absent=fehlt/absent, unknown=unklar/unclear, not_checked=nicht geprüft/not checked (score cell "—", never 0 %), not_checked+truncated=nicht geprüft · Budget-Stopp / not checked · budget stop.

Dev → Arbeitgeber: sub-tabs Kliniken · Träger · Sonstige · Unbekannt with live counts; drafted tabs carry badge "Entwurf" + "Noch ohne Detailansicht. Liste und Zählung sind echt."; row line 2 "{Ort} · {Träger} · {ATS oder 'kein Board bekannt'} · {n} Stellen" (replaces "kein ATS bekannt"); match column "Zuordnung: Titel · Beschreibung · offen" (employer_match_rule title/description/neither); empty "Kein Arbeitgeber in dieser Klasse."

Dev → Stellen: lead DE "Eine Zeile je Stelle und Arbeitgeber. Beschreibung vollständig, Quelle verlinkt." Facets: Land · Arbeitgeber-Klasse · Zuordnung · Rolle · Fachbereich · Status · Nachweis; Land chips incl. "ohne Land" for unresolved rows; verify_status online · weg · gesperrt · Fehler (tooltip for gesperrt: "Board blockt unseren Prüfer, nicht die Bewerber."); missing description renders "Beschreibung fehlt in der Quelle."

Pro → Für Agenten: lead DE "Lese-Ansicht für Agenten. Alles, was die API liefert, in einer Tabelle. grep filtert, sonst nichts."; grep placeholder "grep: Titel, Klinik, Ort … Regex erlaubt"; links "Skill-Datei (eine Datei) · API-Referenz · Datenmodell".

Video captions (≤36 ch per language): simple-home "Jede Klinik, jede Stelle, mit Nachweis." · simple-to-login "Pro: dieselben Daten, deine Filter." · exedev-stub "Hier endet Pro. Clawl braucht exe.dev." · pro-nav "Kliniken, Stellen, Einstellungen, Für Agenten." · pro-clawl-scrape "Clawl: Plan ansehen, nichts läuft."

Open for Ivan: tier name on screen (Dev vs Clawl), Pro price sentence once a number exists, Kosten inside Clawl or own Dev tab, login rate limiter decision.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Pro header shows Kliniken · Stellen · Einstellungen · Für Agenten and a green 'Clawl →' button; Kosten and Clawl tabs are absent from Pro
- [ ] #2 Dev header shows Quellen · Arbeitgeber · Stellen · Docs in DE and Sources · Employers · Jobs · Docs in EN, with the signed-in email at the right
- [ ] #3 All gate strings above exist in both I18N dicts with the exact wording; the default-credentials banner renders red while GET /api/me returns default_credentials true
- [ ] #4 Feature-matrix cells render the six verdict labels distinctly; a not_checked cell shows '—' as score and a truncated cell carries the 'Budget-Stopp' tag
- [ ] #5 Every list under Dev has the specified empty-state string; no string in /pro or /dev contains 'sicher', 'geschützt', 'secure' or 'compliant'
<!-- AC:END -->
