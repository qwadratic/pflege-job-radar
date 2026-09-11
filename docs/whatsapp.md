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
Persona (intern „Luna", im Chat **Valentina von NDT Group**), dieselbe Stilregel (kurze Bubbles, eine
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
>> Hallo, hier ist Valentina von NDT Group – ich bin die digitale Assistentin …
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

## Was hier absichtlich fehlt

- **Kein LLM.** Der Produktions-Bot schreibt mit `gpt-5.6-luna`; hier ist die Antwort deterministisch,
  damit jede Regel einen Test hat. `LLM_API_BASE` wird nicht gelesen.
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
