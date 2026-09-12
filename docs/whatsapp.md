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
`ANTHROPIC_API_KEY`. Ein fehlendes Binary, ein Timeout (`WA_LUNA_TIMEOUT_SEC`, Default 60s),
ein Nicht-JSON-Ergebnis oder eine Antwort ohne Pflichtfelder werfen laut, statt eine Nachricht
zu erfinden.

```bash
WA_BRAIN=luna                  # deterministic (Default) | luna
WA_LUNA_MODEL=claude-sonnet-5  # jedes Modell, das `claude --model` akzeptiert (claude-haiku-4-5 = billiger/schneller)
WA_LUNA_EFFORT=medium          # low|medium|high|xhigh|max
```

**Eigene Tools für das Modell:** noch nicht verdrahtet, aber die CLI unterstützt es —
`--mcp-config <datei-oder-json>` lädt einen oder mehrere MCP-Server, `--strict-mcp-config`
begrenzt die Sitzung auf genau die (keine anderen Projekt-/User-MCP-Konfigurationen), und
`--allowedTools`/`--tools` entscheidet, welche Tool-Namen davon überhaupt freigeschaltet sind.
Käme in Frage, falls Claude den Markt-Snapshot lieber selbst gezielt abfragen soll (z. B. eine
`search_pflege_jobs`-Funktion über `app/data.py`) statt ihn als fertigen Block zu bekommen —
bisher unnötig, weil der Snapshot pro Zug schon vollständig genug ist.

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
