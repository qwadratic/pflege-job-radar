# CV eval harness

Synthetic-CV regression set for `app/cv.py` (the `/api/cv` code path: text -> profile -> ranked
matches against the live posting snapshot). Not an LLM-judge eval (see `harness/` for that kind) --
this is a golden-case check on a deterministic (mostly regex) extractor, run in-process.

```
python evals/cv/run.py            # every case in cases/*.json
python evals/cv/run.py intensiv   # cases whose id contains "intensiv"
```

Each case is one file in `cases/*.json`:

```json
{
  "id": "unique_id",
  "description": "one line, what this CV is and why it's here",
  "cv_text": "the CV as plain text (or paste extracted text from a PDF/DOCX)",
  "expected": {
    "roles_any": ["pflegefachkraft"],
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
present; `experience_years` is exact; `min_matches`/`min_score` check the live match step, not just
extraction (needs Supabase reachable -- same PostgREST anon read every other tool in this repo uses).

**Add a case:** drop a new `cases/<id>.json`. **Update expected results:** edit the `expected` block
directly and re-run -- there's no separate golden store, the case file is the expectation. When a
real CV is available (redact PII first -- no real names/emails/phone numbers in `cv_text`), add it
as a new case the same way; matches are scored against whatever is live in the DB at run time, so a
case's `min_matches`/`min_score` can drift as postings expire -- treat a new failure there as a
"did the market change" signal first, not automatically a code bug.
