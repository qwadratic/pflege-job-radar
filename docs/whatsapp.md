# WhatsApp — eingehende Pflege-Leads beantworten

**Was:** eine Pflegekraft schreibt aus einer Meta-Anzeige heraus auf WhatsApp; Valentina antwortet,
stellt pro Nachricht **eine** Frage, und sobald die Suche eng genug ist, nennt sie echte offene
Stellen aus genau den Daten, die `GET /api/jobs` ausliefert — mit Klinik, Ort und Link zur
Originalanzeige.
**Wo:** `app/wa/` (Router `/api/wa/*`), Zustand in `data/wa.sqlite`, Unit `deploy/pflege-wa.service`.
**Status:** minimaler Harness. Er beantwortet eingehende Nachrichten und bereitet die Übergabe an
einen Menschen vor. Er schickt **nichts** an Kliniken, sammelt keine Dokumente, und ohne
`WA_AUTOSEND=1` geht überhaupt nichts an Meta — die Antworten landen als `draft` in der Datenbank.

Vorbild ist der Produktions-Bot auf `tasker-dispatcher-01`
(`/opt/clinic-dispatcher/apps/connectors/`): dieselbe Meta-Cloud-API, dieselben Env-Namen, dieselbe
Persona (intern „Luna", im Chat **Valentina**), dieselbe Stilregel (kurze Bubbles, eine
Frage pro Zug), dieselbe Qualifikations-Hürde (Urkunde / Defizitbescheid / bestandene
Kenntnisprüfung). Neu ist, dass die Fragen aus den Board-Daten kommen statt aus einem festen Skript,
und dass der LLM-Aufruf entfällt: die Antwort ist deterministisch und damit testbar.

## Das Gespräch

Der Harness führt kein Skript, sondern füllt **Slots** — jeder Slot ist ein Board-Filter:

| Slot | Filter in `GET /api/jobs` | woraus gelesen |
|---|---|---|
| `role` | `role_class` | „examinierte Krankenschwester", „OTA", „Stationsleitung" |
| `city` / `bezirk` | `city` / `regierungsbezirk` | Ortsnamen, die das Board wirklich kennt |
| `department` | `department_hint` | „ITS", „OP", „Kreissaal" … (Alias-Liste des Produktions-Bots) |
| `hours` | `employment_types` | „Vollzeit", „75%" |
| `housing` | `housing=1` | „brauche eine Wohnung" |
| `urkunde` | — | Urkunde, Defizitbescheid, Kenntnisprüfung; **kein** Filter, sondern die Hürde |

**Welche Frage als nächste kommt, entscheiden die Daten.** Für jeden offenen Slot rechnet
`app/wa/brain.py:_gain` auf den noch passenden Stellen aus, wie stark eine Antwort die Liste
zerlegen würde: Abdeckung × (1 − Anteil des häufigsten Werts). „Intensiv oder OP?" bringt nichts,
wenn 90 % der restlichen Zeilen Intensiv sind — bei 40/35/25 halbiert es die Liste. Gefragt wird der
Slot mit dem höchsten Wert; unter `MIN_GAIN` wird gar nicht gefragt, sondern geliefert.

Damit ist die Reihenfolge nicht festgelegt und passt sich dem Bestand an. Auf den echten Daten
(3625 offene Stellen, Stand 2026-09-11) beginnt sie mit dem Ort — 406 der Stellen sind in München,
also zerlegt der Ort die Liste am stärksten.

Weitere Regeln, alle in `app/wa/brain.py`:

- **Zwei Bubbles, eine Frage.** `_check()` wirft, wenn ein Zug mehr enthält — die Stilregel ist eine
  Zusicherung, kein Ratschlag, und bricht im Test statt im Chat.
- **Zahlen statt Floskeln.** Jede Frage trägt den Stand mit: „14 Stellen passen bisher."
- **Buttons aus dem Bestand.** Die drei häufigsten Werte werden als Meta-Reply-Buttons angeboten
  (max. 3, Titel ≤ 20 Zeichen); Freitext bleibt immer möglich, wer „Stroke Unit" schreibt, ist nicht
  an die Vorschläge gebunden.
- **Eine Nachricht darf mehrere Slots füllen.** „Intensiv in Würzburg, Teilzeit" füllt drei, und dann
  werden diese drei nicht mehr gefragt.
- **Die letzte Aussage gilt.** „eigentlich lieber Augsburg" korrigiert den Ort. Ein knappes „ja"
  beantwortet nur die Frage, die gerade gestellt wurde — nie eine von vor vier Zügen.
- **Nichts offen heißt nicht Schweigen.** Passt keine Stelle, wird der lockerste Wunsch fallen
  gelassen (Wohnung → Vollzeit/Teilzeit → Bereich → Stadt) und **benannt**: „Für OP in Augsburg,
  Vollzeit, mit Wohnung ist nichts offen – ohne Wohnungs-Wunsch: …". Gibt es auch dann nichts, sagt
  der Harness das gerade heraus.
- **Keine Zusage ohne Anerkennung.** Ohne Urkunde, Defizitbescheid oder bestandene Kenntnisprüfung
  wird niemand vorgestellt (wie in der Produktions-Constitution). „Nicht bestanden" wird auch dann
  gelesen, wenn niemand danach gefragt hat.
- **STOP ist STOP.** „stop", „stopp", „abmelden", „löschen" → `wa_threads.stopped`, ab dann geht
  nichts mehr raus; die eingehende Nachricht wird trotzdem gespeichert, sie ist die Willenserklärung.
  Ganze Wörter, damit „Stopfen" und „Intensivstation" nichts auslösen.
- **Dateien.** CV oder Urkunde als Anhang werden bestätigt („kann ich hier noch nicht lesen"), nicht
  stillschweigend verschluckt — der Harness hat keine Dokumenten-Pipeline.

Ein Verlauf sieht damit so aus (echte Zahlen, gekürzt):

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

Meta WhatsApp Cloud API, Graph `v25.0`. Signatur- und Challenge-Prüfung sind aus dem
Produktions-Client übernommen, weil sie die gesamte Vertrauensgrenze nach innen sind.

| Route | Auth | was |
|---|---|---|
| `GET /api/wa/webhook` | Metas `hub.verify_token` | Handshake, echot `hub.challenge` |
| `POST /api/wa/webhook` | `X-Hub-Signature-256` (HMAC-SHA256 über den Rohbody) | eingehende Nachrichten |
| `GET /api/wa/health` | öffentlich | Bereitschaft ohne Secrets |
| `GET /api/wa/threads` | Owner-Session | Threads mit Slots, `?phone=` mit Verlauf |

Reihenfolge eines POST, jeder Schritt mit Grund: Signatur prüfen → `phone_number_id` vergleichen
(ein Webhook für eine zweite Nummer wird ignoriert, nicht beantwortet) → `INSERT` auf
`wa_messages.wamid` (UNIQUE, damit eine Meta-Wiederholung genau hier endet) → Antwort entscheiden
(kein Netz, kein Schreiben) → senden → Ausgang speichern. Ein Meta-Fehler wird **nicht**
verschluckt: die Route antwortet 502, es steht keine gesendete Nachricht in der Datenbank, und die
Wiederholung von Meta liefert die Antwort dann wirklich aus.

`GET /api/wa/threads` ist owner-only (`app/auth.py:OWNER_READ_PREFIXES`) — Telefonnummer und was
jemand über sich erzählt hat sind die persönlichsten Daten in diesem Repo.

## Betrieb

```bash
# Env (Namen identisch zum Produktions-Bridge, damit eine Meta-App für beide reicht)
META_WHATSAPP_APP_SECRET=…        # Pflicht: Webhook-Signatur
META_WHATSAPP_VERIFY_TOKEN=…      # Pflicht: Handshake
META_WHATSAPP_ACCESS_TOKEN=…      # Pflicht zum Senden
META_WHATSAPP_PHONE_NUMBER_ID=…   # Pflicht zum Senden, und filtert fremde Webhooks
WA_AUTOSEND=1                     # ohne das: alles wird nur als draft gespeichert

.venv/bin/uvicorn app.wa.asgi:app --port 8502      # eigener Prozess (deploy/pflege-wa.service)
curl -s localhost:8502/api/wa/health
.venv/bin/python -m pytest -q tests/test_wa_harness.py
```

Webhook bei Meta eintragen: `https://<host>/api/wa/webhook`, Feld `messages`, Verify-Token wie oben.

Zwei Türen, eine Implementierung: `app/main.py` mountet denselben Router (Port 8501), damit der
Harness lokal ohne zweiten Prozess läuft. In Produktion zeigt nginx auf **eine** davon — der eigene
Prozess, damit ein Lead nicht hinter einem Crawl-Snapshot wartet. Beide schreiben `data/wa.sqlite`
(WAL); schreiben tut nur die, die Webhooks bekommt.

## Zweites Gehirn: dieselbe Persona, dieselben Regeln, Claude statt ChatGPT

`WA_BRAIN=luna` schaltet auf `app/wa/luna_brain.py` um — dieselbe Transport-Schicht, dieselbe
`data/wa.sqlite`, aber die Antwort kommt jetzt von Claude statt aus der Fragen-Leiter oben.
Persona, Qualifikations-Gate, Regions-Grenze (nur Bayern) und die Live-Markt-Logik sind aus
der Produktions-Implementierung übernommen — mit Firmenbezug entfernt, weil dieses Repo
öffentlich ist und die Quelle privat/firmengebunden (`app/wa/luna/VENDORED.md` listet genau,
was übernommen, was verallgemeinert und was weggelassen wurde). `docs/index.json`/README
bleiben Deutsch; dieser Abschnitt auch.

**Was in Code entschieden wird, nicht vom Modell:**

- **STOP** erreicht das Modell nie — genau wie beim deterministischen Zweig.
- **Nicht platzierbar** (Pflegehelfer, Ausbildung ohne Anerkennungspfad, durchgefallene
  Kenntnisprüfung): die erste Ablehnung ist der feste deutsche Text
  (`app/wa/luna/prompts.py:REJECT_BODY_DE`), nie die eigene Formulierung des Modells — genau
  die "prozessgenaue Formulierung, die das Modell nicht umschreiben darf"-Regel aus der Quelle.
- **Bundesland außerhalb Bayerns**: löst die feste Absage aus (`OUT_OF_SCOPE_REGION_DE`), weil
  das Board keine Daten für andere Länder hat.

Alles andere — Tonfall, welche Frage als nächste kommt, wie der Marktstand formuliert wird,
wann eskaliert wird — entscheidet Claude, aus dem Zustand, den `app/wa/luna_brain.py` mitgibt:
der Karte (`card`, das Äquivalent zu `card_patch` aus der Quelle), einem `requirement_scoreboard`
(Zustand, kein Skript) und einem `market_snapshot` aus genau den Filtern, die auch
`GET /api/jobs` nutzt (`app/wa/brain.py:jobs_for`) — kein zweiter Datenpfad. Den Thread selbst
gibt es hier nicht mehr als Text zu übergeben: er lebt in der Sitzung (nächster Absatz).

**Eine Claude-Code-Sitzung pro WhatsApp-Nummer, nicht ein zustandsloser Aufruf pro Zug.** Beim
ersten Kontakt einer Nummer startet `app/wa/luna_brain.py:Client` `claude -p --session-id <uuid>`
und merkt sich die UUID auf der Karte (`card._session_id`); jeder weitere Zug derselben Nummer
ruft `claude -p --resume <dieselbe uuid>` auf. Die eingehende WhatsApp-Nachricht wird damit zur
echten `user`-Nachricht dieser Sitzung, genau wie in einem interaktiven Chat — Claude sieht die
bisherigen Züge aus der Sitzung selbst, nicht aus einem selbstgebauten Thread-Feld. Was pro Zug
trotzdem frisch mitgeschickt wird, ist nur, was sich unabhängig vom Gespräch ändern kann: der
aktuelle Kartenstand, der Requirement-Scoreboard und der Markt-Snapshot (neue Stellen erscheinen,
alte schließen) — das kann sich Claude nicht "merken", das muss jeder Zug neu bekommen.
`--resume`/`--session-id` finden eine Sitzung nur wieder, wenn `claude` **aus demselben
Arbeitsverzeichnis** aufgerufen wird, in dem sie begonnen hat — deshalb läuft jeder Aufruf mit
festem `cwd=WA_LUNA_SESSION_DIR` (`data/wa_luna_sessions/`, Default), unabhängig davon, aus
welchem Verzeichnis der FastAPI-Prozess selbst gerade läuft.

**Aufruf: die `claude`-CLI, nicht der Anthropic-SDK-Schlüssel.** `app/wa/luna_brain.py:Client`
ruft `claude -p --restricted --output-format json --system-prompt "…"` auf (Systemprompt als
volle Ersetzung, nicht Anhängsel; `--restricted` nimmt Bash/Code-Ausführung/WebFetch weg, die
für eine Chat-Antwort ohnehin nichts zu tun hätten) und schickt die Nutzlast über stdin. Das
nutzt die Claude-Code-Anmeldung, die auf dem Host schon existiert — kein separates
`ANTHROPIC_API_KEY`. Ein fehlendes Binary, ein Timeout (`WA_LUNA_TIMEOUT_SEC`, Default 120s — auf
120 von ursprünglich 60 angehoben, nachdem TASK-68s Ende-zu-Ende-Lauf real einen
`subprocess.TimeoutExpired` bei 60s auf einem gewöhnlichen Zug produzierte: der Tool-Aufruf
(TASK-62) plus `effort=high` brauchen zusammen manchmal mehr Zeit als die reine Antwort),
ein Nicht-JSON-Ergebnis oder eine Antwort ohne Pflichtfelder werfen laut, statt eine Nachricht
zu erfinden.

```bash
WA_BRAIN=luna                  # deterministic (Default) | luna
WA_LUNA_MODEL=claude-sonnet-5  # jedes Modell, das `claude --model` akzeptiert (claude-haiku-4-5 = billiger/schneller)
WA_LUNA_EFFORT=high            # low|medium|high|xhigh|max -- "high" seit TASK-62: Tool-Einsatz planen ist echte Denkarbeit
```

**Eigene Tools für das Modell (TASK-62, verdrahtet):** `app/wa/luna/tools_server.py` ist ein
kleiner stdio-MCP-Server mit vier read-only Tools -- `search_postings`, `get_posting`,
`list_clinics`, `get_clinic_contact` -- die dieselben `D.filter_jobs`/`D.filter_clinics`-Funktionen
aufrufen wie `app/wa/brain.py` und `GET /api/jobs`/`/api/clinics`. `Client._live_reply` startet ihn
über `--mcp-config`/`--strict-mcp-config`/`--allowedTools` (auf genau diese vier Tool-Namen
begrenzt, Form `mcp__pflege_board__<tool>` -- live verifiziert, nirgendwo offiziell dokumentiert).
`market_snapshot`/`requirement_scoreboard` bleiben trotzdem in jeder Nutzlast: ein Tool-Aufruf ist
eine Ergänzung, kein Ersatz, und ein Fehler dabei fällt nur auf das bestehende Snapshot-Reasoning
zurück (kein Retry-Mechanismus -- bewusst verworfen, siehe unten). Der Prompt (`prompts.py`,
TOOLS-Regel) verlangt proaktiven Einsatz: sobald der Kandidat einen Ort/Fachbereich/eine Klinik
nennt, die der Snapshot nicht schon zeigt, muss ein echter Tool-Aufruf erfolgen, nie eine Vermutung.

Zwei Stolperfallen, die live beim Aufbau auftraten und für jede künftige Änderung hier gelten:
1. Das Modell hat "search_postings" anfangs als Wert für `action` ins JSON geschrieben, statt den
   Tool wirklich aufzurufen -- die strikte "gib NUR ein JSON-Objekt zurück"-Anweisung wurde als
   Verbot jeder Zwischenaktion missverstanden. Fix: `OUTPUT_INSTRUCTION` sagt jetzt ausdrücklich,
   dass sich das nur auf den *finalen* Text nach etwaigen Tool-Aufrufen bezieht.
2. Das per-Server `cwd`-Feld in `--mcp-config` wird von dieser CLI-Version beim stdio-Start nicht
   beachtet -- der Server erbt das cwd des äußeren `claude`-Prozesses (`C.LUNA_SESSION_DIR`, nicht
   das Repo-Root), und `python -m app.wa.luna.tools_server` scheitert dann mit
   `ModuleNotFoundError: No module named 'app'`. Fix: `env.PYTHONPATH` im generierten Config-JSON
   erzwingt die richtige Modulauflösung unabhängig vom tatsächlichen cwd. Aus demselben Grund
   bekommt der Server auch `WA_SQLITE_PATH`/`WA_LUNA_SESSION_DIR` als env-Variablen durchgereicht --
   er importiert `app.wa.config` frisch in seinem eigenen Prozess, sodass ein `monkeypatch` im
   Testprozess ihn sonst nie erreicht.

**Der Abschluss-Ablauf (TASK-63):** sobald Qualifikation, Stadt, Fachbereich und Wohnsituation
alle geklärt sind, liefert `market_snapshot` zusätzlich `matching_clinics_count` (Anzahl passender
Kliniken) und `shortlist` (bis zu 5 davon, erst ab diesem Zeitpunkt gefüllt). Der Prompt verlangt
vier getrennte Züge: Gesamtzahl nennen → Shortlist nennen → Kriterien in einem Satz
zusammenfassen → erst dann um Einwilligung zur anonymisierten Weiterleitung fragen
(`anonymous_send_consent`). Ein späterer Zug darf eine bereits genannte Klinik erneut nennen (z. B.
in der Einwilligungsfrage selbst) — verboten ist nur, eine Klinik zum ersten Mal in demselben Zug
zu nennen, in dem auch schon nach Einwilligung gefragt wird.

**Kandidaten-Queue nach Einwilligung (TASK-66):** sobald `anonymous_send_consent` in einem Zug neu
auf `true` wechselt, baut `app/wa/api.py` — erst NACHDEM der Thread gespeichert ist und NACHDEM die
Pro-Nachricht-Sperre (`ST._lock`) wieder freigegeben ist, damit ein Matching-Lauf nicht alle
anderen Threads blockiert — über `app/wa/queue.py:build_queue_entry` einen echten Eintrag:
`app.autopilot.matching.rank()` (dieselbe transparente Scoring-Engine, aber ohne echte
Kandidaten-PII in `app/autopilot`s eigene, ausdrücklich synthetische Demo-Datenbank zu schreiben)
rankt die Kandidatin gegen alle Kliniken des Live-Snapshots; ein bekannter Kontakt (TASK-64/69) wird
mit aufgenommen. Zwei neue, eigene Tabellen (`wa_queue_candidates`, `wa_queue_matches`, gleiche
sqlite-Datei wie `app/wa/store.py`) sind idempotent (Upsert), ein wiederholtes Einverständnis
dupliziert also nichts. `GET /api/wa/queue` (Kandidaten × passende Kliniken) und `GET
/api/wa/queue/mailing-list` (flache Vorschau: Kandidat × Klinik × Kontakt-E-Mail) sind owner-only
wie `GET /api/wa/threads` — beide senden nichts, sie sind ein Report für einen Menschen.

**Optionale externe Kontakt-CRM-Quelle (TASK-69, Ergänzung zu TASK-64):** ein Betreiber kann eine
eigene, separat gepflegte Klinik-Kontakt-CRM anschließen (menschlich/agentisch gepflegte Kontakte,
idealerweise mit Quelle/Beleg pro Eintrag) — `app/wa/luna/external_contacts.py` ist ein No-op,
solange `WA_EXTERNAL_CONTACT_DB` nicht gesetzt ist. Wenn konfiguriert, fragt es die angegebene
sqlite-Datei read-only ab (Lesebefehl über `WA_EXTERNAL_CONTACT_READER`, Default `sudo sqlite3`, da
so eine CRM-Datei oft restriktivere Rechte hat als dieser Prozess selbst), matcht den Kliniknamen
unscharf (rapidfuzz, auf `bundesland='Bayern'` eingegrenzt) und bevorzugt eine Person mit
`role_category` `pflege_leadership`/`hr_leadership`/`hr` (erwartetes Schema: `companies`/`people`/
`contact_channels`, siehe das Modul für Details). `contacts.discover_contact` versucht diese Quelle
zuerst, vor `enr_contact_emails`/Website/JD-Rescan, und fällt bei jedem Fehler (nicht konfiguriert,
kein Lesezugriff, o. ä.) genauso großzügig durch wie die bestehende Website-Quelle schon immer.

**24h-Fenster und Reopen-Template (TASK-70):** WhatsApps eigene Regel, nicht unsere: reiner Freitext
geht nur innerhalb von `WA_FREEFORM_WINDOW_HOURS` (Default 24) nach der letzten Nachricht der
Kandidatin raus; danach lehnt Meta Freitext ab. `app/wa/api.py:_send` prüft das in Code, nie das
Modell: ist das Fenster zu, geht statt der Bubbles ein vorab bei Meta genehmigtes Template raus
(`Client.send_template`, `WA_REOPEN_TEMPLATE_NAME`/`WA_REOPEN_TEMPLATE_LANG`). Ohne konfiguriertes
Template wirft das laut einen Fehler, statt Freitext zu versuchen (den Meta ohnehin ablehnt) oder
still gar nichts zu tun. In der aktuellen, rein Webhook-getriebenen Zustellung (`_handle_one`)
ist das Fenster durch den frischen `last_inbound_at`-Zeitstempel praktisch immer offen — die Prüfung
greift vor allem, sobald das Dry-Run-Werkzeug (`shadow_run.py`, TASK-72) einen älteren,
unbeantworteten Thread erneut anfasst.

**Stage/Ball-Reporting und Migration (TASK-71):** `app/wa/luna/reporting.py` liefert `stage_for(card)`
(new_lead → qualifying → documents_in → ready → consented, oder not_placeable) und `ball_for(conn,
phone)` (us/them/none, aus der letzten Zeile in `wa_messages`) — beides reine Ableitungen aus
bereits vorhandenen Feldern, keine neuen Spalten, nur fürs Reporting (Dry-Run-Tool, Migration).
`app/wa/luna/migrate_candidates.py` importiert echte Kandidaten idempotent in `wa_threads` — aus
einem generischen JSON-Export (`--input`, nur `phone` Pflichtfeld), nicht direkt aus irgendeinem
konkreten externen System (gleiche Zurückhaltung wie bei `external_contacts.py`, TASK-69). Eine
Zeile ohne gültige Telefonnummer wird gemeldet, nie still übersprungen; ein zweiter Lauf ergänzt
die Karte nur, statt sie zu überschreiben.

**Dry-Run-Werkzeug (TASK-72):** `python -m app.wa.luna.shadow_run` — dieselbe Absicherung, die das
echte Produktionsteam für genau diesen Zweck schon einsetzt (`wa_shadow_run.py` auf
tasker-dispatcher-01): "was würde die Antwort sein, ohne zu senden", immer gegen eine Kopie der
Datenbank, nie gegen die echte. `shadow_run.db_copy()` öffnet die Quelle strikt lesend (SQLite-URI
`mode=ro` — verweigert nicht nur jeden Schreibzugriff, sondern legt die Datei auch nicht erst an,
falls sie fehlt) und sichert sie über SQLite's eigene Online-Backup-API in eine
In-Memory-Kopie; alles Weitere liest und schreibt nur noch diese Kopie. Betrachtet werden alle
Threads, bei denen `reporting.ball_for() == "us"` ist (die letzte Nachricht ist eingehend, eine
Antwort steht noch aus) — in diesem Harness ein Zustand, der bei einem echten Webhook-Aufruf nur
kurz auftritt (Antwort wird synchron berechnet und verschickt); bleibt ein Thread in der echten
Datenbank so hängen, ist irgendwo mittendrin etwas fehlgeschlagen, und genau dafür ist ein
gefahrloses Inspektionswerkzeug gedacht.

Eine Besonderheit bei `WA_BRAIN=luna`: die Karte trägt eine echte, fortsetzbare Claude-Code-Session-Id
(`_session_id`). Ein Dry-Run, der diese Session mit `--resume` fortsetzen würde, hinterließe einen
echten, dauerhaften Eintrag in genau der Session, die der nächste echte Webhook-Aufruf fortsetzt —
ein nicht rückgängig zu machender Seiteneffekt auf geteilten externen Zustand, den ein reines
Report-Werkzeug niemals riskieren darf. `shadow_turn()` entfernt `_session_id` deshalb immer, bevor
das Gehirn aufgerufen wird, sodass jede Luna-Antwort hier aus einer frischen, folgenlosen Session
kommt — die Karte selbst (was Gates und Matching tatsächlich steuert) ist unverändert echt, nur das
Gesprächsgedächtnis der Session fehlt, wodurch der Wortlaut etwas kühler ausfallen kann als die
echte Antwort. Das Ergebnis pro Thread nennt Stage, Aktion, Bubbles und das TASK-70-Gate
(`freeform`/`reopen_template`/`reopen_template_missing`/`no_send`/`stopped`) — nie geschrieben,
weder in die Kopie noch ins Original.

**Was hier zusätzlich fehlt, verglichen mit der Quelle:** kein Dokumenten-OCR, keine
Interview-Terminfindung, kein Klinik-Einreichungs-E-Mail-Fluss, keine Manager-CRM-Übernahme,
keine proaktiven Nachfass-Nachrichten — dieselben Lücken wie beim deterministischen Zweig
(siehe unten), aus demselben Grund: die Infrastruktur dafür existiert in diesem Repo nicht.
Eine Eskalation (`escalate_to_manager`) wird auf dem Thread vermerkt (`_escalated`,
`_escalate_reason`, lesbar über `GET /api/wa/threads`), löst aber keinen Versand aus.

## Was hier absichtlich fehlt

- **Kein LLM (Standard).** Die Fragen-Leiter oben ist deterministisch, damit jede Regel einen
  Test hat. `WA_BRAIN=luna` (oben) schaltet auf Claude um, wenn die volle Persona/Konversation
  gebraucht wird.
- **Keine Klinik-Seite.** `handover_requested` wird im Thread vermerkt, verschickt aber nichts;
  ein Mensch übernimmt. Der Harness verspricht dem Lead genau das und nicht mehr.
- **Keine proaktiven Nachrichten**, also auch keine Nachfass-Kadenz, keine Nachtruhe-Fenster und
  keine 24-Stunden-Template-Logik: der Harness antwortet nur, und eine Antwort innerhalb von 24
  Stunden braucht kein Template.
- **Keine Medien-Pipeline** (Download, STT, CV-Parsing) und keine Dedupe-Tabellen für Ausgänge, die
  der Produktions-Draft/Preview/Confirm-Fluss dort braucht, wo Menschen und Bot dieselbe Nummer
  bedienen.

Eine Abweichung von der Produktions-Vorlage ist bewusst: dort wird die Trefferliste **ohne** Links
verschickt („sie ziehen Leute aus dem Chat"). Hier steht der `source_url` dabei, weil eine Liste, die
eine Pflegekraft nicht nachprüfen kann, wertlos ist — und weil das Board ohnehin keinen anderen
ausgehenden Link kennt.

## Persona-Tests gegen die echte CLI

`tests/test_wa_luna_personas.py` (Marker `llm`, ausgeschlossen mit `-m "not llm"` wie
`network`/`completeness`/`mutation`, weil jeder Test wirklich `claude` aufruft, echtes Geld kostet
und mehrere Sekunden pro Zug braucht) spielt sechs frei erfundene Personas durch den echten
Claude-Aufruf. Die Personas selbst sind erfunden — Namen, Details, Dialogzeilen — aber die
**Muster**, die sie durchspielen (Qualifikationspfad-Mix, Gesprächsform, typische Stolperfallen),
kommen aus einer anonymisierten Auswertung von zwei Monaten echter WhatsApp-Historie der
Referenzimplementierung: gelesen, zu Archetyp-Gruppen zusammengefasst, dann verworfen — kein
echter Name, keine Telefonnummer, kein wörtliches Zitat landet in dieser Datei.

```bash
.venv/bin/python -m pytest -q -m llm tests/test_wa_luna_personas.py
```

Der erste echte Durchlauf fand zwei echte Bugs, die eine rein gefakte Test-Suite nicht hätte
finden können:

- **Gehaltsfrage beantwortet statt weitergereicht.** Ohne explizite Regel hat das Modell einmal
  eine konkrete Gehaltsspanne genannt ("zwischen ca. 3.400 und 4.200 € brutto"), obwohl der
  Harness dafür keine verlässliche Datenquelle hat. Behoben mit einer neuen Regel
  (`app/wa/luna/prompts.py:RULES`, „SALARY"): nie eine Zahl nennen oder schätzen, immer auf eine
  Bestätigung durch die Klinik verweisen.
- **Abgesetzte Antwort ohne `no_send` ließ den Harness abstürzen.** Ein Modellzug kam mit leerem
  `bubbles: []` zurück, aber ohne `no_send: true` gesetzt zu haben — `_check()` erwartete
  mindestens eine Bubble und warf einen `AssertionError`. Behoben: ein leeres `bubbles`-Array
  gilt jetzt für sich allein als „nichts zu sagen", unabhängig vom `no_send`-Flag
  (`app/wa/luna_brain.py:turn`, Regressionstest in `tests/test_wa_luna_brain.py`).

Eine dritte Sache stellte sich als Härtung heraus statt als Logikfehler: `--restricted` allein
lässt weiterhin dateilesende Tools zu (nur Kommando-/Code-Ausführung und WebFetch fallen weg),
und das Modell hat einmal einen Dateizugriffsversuch als Fließtext vor die eigentliche JSON-Antwort
geschrieben. Der Aufruf läuft jetzt zusätzlich mit `--tools ""` (alle Tools aus), und das Parsen
selbst (`app/wa/luna_brain.py:_parse_reply_json`) versucht zur Absicherung auch noch, das
JSON-Objekt aus umgebendem Text herauszuschneiden, bevor es wirklich aufgibt.

## Ende-zu-Ende-Trichtertest mit zwei lebenden Agenten (TASK-68)

`tests/test_wa_luna_e2e_funnel.py` (Marker `llm`) geht einen Schritt weiter als die Persona-Tests
oben: dort ist nur Valentina ein echter Modellaufruf, das Kandidaten-Skript ist absichtlich fest
verdrahtet (stabil für Regressionstests). Hier spielt ein `_CandidateAgent` (eigene, resumierbare
Claude-Code-Sitzung, `claude-haiku-4-5`, freier Text statt JSON-Schema) die Kandidatenseite frei
nach einem kurzen Personenprofil — beide Seiten sind also echte, nichtdeterministische
Modellaufrufe. Drei Personas (Urkunde/München, Defizitbescheid/Augsburg,
Kenntnisprüfung-bestanden-Urkunde-ausstehend/Bayern-offen) laufen bis zur Einwilligung
(`anonymous_send_consent`), mit einer geloggten Zugobergrenze statt einem stillen Erfolg, falls
eine Persona nicht konvergiert. Jede erfolgreiche Persona läuft danach durch
`app/wa/queue.py:build_queue_entry` (TASK-66) gegen ein kleines Fixture-Board mit einem
vorab-gespeicherten Klinik-Kontakt.

```bash
.venv/bin/python -m pytest -q -m llm tests/test_wa_luna_e2e_funnel.py -s
```

Ergebnis eines echten Laufs: alle drei Personas erreichten die Einwilligung in 4–5 statt der
erlaubten 12 Züge, die Ablauf-Sequenz (Anzahl → Shortlist → Kriterien-Recap → Einwilligungsfrage)
lief sichtbar getrennt ab, und die Mailing-List-Ansicht zeigte 9 Zeilen (3 Kandidatinnen × 3
Kliniken) — für die eine vorab bekannte Klinik korrekt mit Kontakt-E-Mail, für die anderen beiden
ehrlich als „UNKNOWN" (kein erfundener Kontakt). Der volle Gesprächsverlauf wird bei jedem Lauf
nach `tests/.artifacts/e2e_funnel_report.md` geschrieben (git-ignoriert, da synthetisch aber
gesprächsförmig) und auf stdout ausgegeben.

Ein Nebenfund dieses Laufs: `WA_LUNA_TIMEOUT_SEC` (60s) reichte nicht mehr aus, seit TASK-62 einen
Tool-Aufruf plus `effort=high` in den Zug eingeführt hat — ein gewöhnlicher Zug lief einmal in
einen echten `subprocess.TimeoutExpired`. Der Default ist deshalb auf 120s angehoben.
