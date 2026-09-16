# CV eval harness

Synthetic-CV regression set for `app/cv.py`: text -> profile -> ranked matches. Not an LLM-judge
eval (see `harness/` for that kind) -- this is a golden-case check, and it now runs **two**
extraction paths over the same case set: `CV.analyse()` (deterministic, mostly regex) and
`CV.analyse_llm()` (the `claude` CLI reasoning over the raw CV text, TASK-65). Both hand their
extracted profile to the same `match()` afterwards, so the two runs measure extraction quality,
not job-matching scoring.

```
python evals/cv/run.py                      # every case, deterministic path (default, offline, free)
python evals/cv/run.py intensiv             # cases whose id contains "intensiv"
python evals/cv/run.py --path=llm           # every case through the real `claude` CLI -- costs
                                             # real money/time, run deliberately, not in every CI pass
python evals/cv/run.py --path=llm intensiv  # path flag + filter both apply
CV_EVAL_PATH=llm python evals/cv/run.py     # env-var form, same precedence as WA_BRAIN/WA_LUNA_*
```

Both paths run fully offline against a small **fixture** job/clinic snapshot baked into `run.py`
(one town per Bavarian Regierungsbezirk, real geography, invented clinics/postings) -- loaded into
`app.data`'s in-process cache before any case runs, so `profile_from_text`'s city/Regierungsbezirk
lookup and `match()`'s scoring have real data regardless of whether this host has a live Supabase
connection (`SUPABASE_ANON_KEY` is not always set -- see TASK-23). Only `--path=llm` still spawns a
real subprocess (the `claude` CLI itself is never mocked); the job board it matches against is not
live data either way.

Each case is one file in `cases/*.json`:

```json
{
  "id": "unique_id",
  "description": "one line, what this CV is and why it's here",
  "cv_text": "the CV as plain text (or paste extracted text from a PDF/DOCX)",
  "expected": {
    "roles_any": ["pflegefachkraft"],
    "roles_none": ["pflegehelfer"],
    "qualifications_all": ["GuK"],
    "departments_any": ["Intensiv/IMC"],
    "experience_years": 6,
    "languages_all": ["Deutsch C1"],
    "cities_any": ["München"],
    "min_matches": 1,
    "min_score": 60
  }
}
```

All `expected` keys are optional and independent -- set only the ones this case is actually
testing. `_any` keys pass if the extracted set intersects; `_all` keys need every listed value
present; `roles_none` fails if the extracted `roles` include any of the listed (forbidden) values --
added for TASK-65's Pflegefachhelfer trap case, where the interesting failure mode is an extra,
wrong role rather than a missing one; `experience_years` is exact; `min_matches`/`min_score` check
the match step against the fixture board above.

**Add a case:** drop a new `cases/<id>.json`. **Update expected results:** edit the `expected` block
directly and re-run -- there's no separate golden store, the case file is the expectation. Keep new
cases fully synthetic/genericized (no real names, phone numbers, or verbatim text from anyone's
real CV or chat history), same rule as everywhere else in this repo.

## TASK-65: deterministic vs. LLM-driven extraction -- result

Widened the case set from 2 to 12 (qualification-path variety: Urkunde/full recognition,
Defizitbescheid received, Kenntnisprüfung passed/Urkunde pending, plain Pflegehelfer, the
Pflegefachhelfer 1-year-helper-vs-3-year-Fachkraft trap, OTA/ATA, Hebamme, Leitung +
Praxisanleitung, plus 3 deliberately "messy" cases: mixed German/English, an abbreviation instead
of the full word, a distractor birth year next to a date-range instead of "X Jahre"). Added
`CV.analyse_llm()` (`app/cv.py`) -- same `{profile, matches, used_llm, chars}` shape as
`CV.analyse()`, same `claude` CLI subprocess pattern as `app/wa/luna_brain.py:Client` (Sonnet 5,
`--restricted --tools ""`, stdin payload, `--output-format json`, the `result` field parsed as
JSON, raises loudly on any bad response -- no fallback to the deterministic path). It reasons over
the raw CV text (+ an optional `chat_history` argument for TASK-67 to pass through later) and still
calls the same deterministic `match()` -- job-matching scoring is out of scope for this comparison.

**Pass rate:** deterministic (one run -- it is deterministic by construction) **9/12**. LLM (sampled
across several runs) **11-12/12**, with one case observed to be genuinely borderline (see below).

**Verdict: lean LLM for profile extraction.** It won every case that hit a real domain trap this
task set out to find:

- `pflegefachhelferin_qualification_trap` -- Pflegefachhelfer is a 1-year HELPER qualification, not
  the 3-year Pflegefachkraft, despite the "Fach" in its name (a trap this repo's own
  `app/wa/luna/qualification_knowledge.json` already documents for the WhatsApp brain). The
  deterministic per-line job-title classifier also picks up the *other* nurses the candidate merely
  assists ("Unterstützung der Pflegefachkräfte...") and wrongly credits her with that role too. The
  LLM read both correctly: `pflegehelfer` only, `Pflegehelfer` qualification, no `pflegefachkraft`.
  The deterministic path also has a real, separate gap here: its qualification-tag regex for
  "Pflegehelfer" does not include the "Pflegefachhelfer" alias at all (only the job-title
  classifier's *role* regex does), so it misses the qualification tag entirely on top of the wrong
  extra role.
- `messy_mixed_language_date_range_experience` -- experience given as a date range
  (03/2018-09/2023, ~5.5y) instead of "X Jahre", plus a birth year ("geboren 1990") as a distractor.
  The deterministic path's date-range fallback (used only when no explicit "X Jahre" phrase is
  found: take the max 4-digit year minus the min 4-digit year in the whole text) cannot tell a
  birth year from a career-start year, so it computes 2023-1990=33 (capped at 30) instead of ~5. It
  also missed "Eng B2" as an abbreviation for Englisch B2 (the regex requires the word
  "englisch"/"english"). The LLM got the real experience (5) and the abbreviated language tag right.
- `kenntnispruefung_passed_urkunde_pending` -- a CV in English naming a "general medicine ward";
  the department-hint regex is German-oriented (`innere|internist|...`) and does not recognise the
  English phrase, so the deterministic path returns no department at all. The LLM correctly mapped
  it to `Innere Medizin`.

It tied the deterministic path on every clean, unambiguous case (Urkunde/full recognition, plain
Pflegehelfer, OTA/ATA, Hebamme, Leitung+Praxisanleitung, the English/German mixed
Defizitbescheid+Anpassungslehrgang case) and, as a side effect, avoided one of the deterministic
path's own regex false positives along the way: `leitung_praxisanleitung_generalist`'s "Praxisanleiterin"
substring-matches the "Ambulanz" skill regex (which includes bare `praxis` as an alternative,
intended for "Praxis"/outpatient practice, not "Praxisanleitung"), tagging a spurious
`Ambulanz/Tagesklinik` department that the LLM never produced.

**Not a clean sweep, and not fully stable.** `defizitbescheid_received_anerkennungspfad` (a
Defizitbescheid-already-received candidate -- placeable per `qualification_knowledge.json`) is
genuinely borderline for the LLM: of 3 sampled runs of the **identical** prompt, one got both the
`pflegefachkraft` role and `GuK` qualification right, one omitted `GuK` only, one omitted both. The
deterministic regex gets this case right on every run (same input, same regex, always the same
output), via the literal "Registered Nurse"/"Anerkennungsverfahren" tokens. This reproduces, on a
structured-extraction task rather than conversational phrasing, the same real CLI non-determinism
already confirmed live during this repo's WhatsApp-harness planning session (same input, two fresh
sessions, two different phrasings, same underlying decision) -- here it can flip a *field*, not
just the wording around it.

**Recommendation for TASK-67 (CV/Urkunde intake, which wires up `analyse_candidate`):**
`CV.analyse_llm()` is the better default for reading free CV text (and, later, chat history) into a
profile -- it is measurably better at exactly the domain traps and messy real-world phrasing this
task set out to test, at the cost of occasional non-determinism on genuinely borderline recognition-
path cases. `match()` (job-matching scoring) is unaffected either way and should stay exactly as it
is -- this result is about extraction, not matching. A borderline recognition-path candidate is
worth a human's second look regardless of which extraction path produced the profile; that is a
product/process question for TASK-67, not something this comparison should paper over with a
deterministic tie-breaker rule invented after the fact.
