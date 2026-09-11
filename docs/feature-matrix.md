# Feature matrix

Which adapter actually does what, per board, with the evidence attached. One cell per
(board × feature). The store is `data/feature_cells.jsonl`; the table on the Clawl page is a
render of it and is never the source of truth.

## Method

1. **The row vocabulary is frozen first.** Rows are not invented per run — see below.
2. **One worker per column (board), never per row.** A worker fills every row of one board in one
   pass, because the expensive part is fetching the board, not judging the feature.
3. **Cells carry evidence, not prose.** Every cell names the path or quote it was derived from.
4. **Merge with a script, not an LLM.** `tools/cells_from_pytest.py` folds a pytest run into cells;
   the store is append-only, so a merge is `>>`. A human only flags collisions and gaps.
5. **Verify by re-deriving.** A second pass sees only `evidence` and re-derives `verdict`; a
   mismatch reopens the cell. For the five pytest rows the stronger form already exists:
   `pytest tests/test_adapter_completeness.py -m mutation` proves each check can go red
   (`tests/adapter_contract.py:265-271`).
6. **Score last.** The weighted score is a computed column (`app/coverage.py`), so re-weighting
   costs zero crawls.
7. **Persist as JSONL in git**, one line per cell, so a diff is readable.

## Rows (`feature_id`)

The vocabulary is the five live checks in `tests/test_adapter_completeness.py:265-300`, each with a
matching breakage in `tests.adapter_contract.MUTATIONS`:

| `feature_id` | what a `supported` cell means | weight |
|---|---|---|
| `read_path_coverage` | the adapter's calls cover every API-shaped read path the board's own client uses | 3 |
| `declared_total_parity` | rows returned == the count the board itself declares | 3 |
| `field_completeness` | description / city / datePosted / employmentType are populated on at least one row | 2 |
| `public_url` | every stored url opens as a posting, never an API self-link | 2 |
| `round_trip` | up to 5 stored urls resolve live with a title match | 1 |

Weights live in exactly one place, `FEATURE_WEIGHTS` in `app/coverage.py`.

Candidate rows named in the design brief — `pagination_followed`, `detail_fetch`, `feed_available`
(TASK-16), `robots_blocked`, `method_justification` (TASK-20), `completeness_verdict` (TASK-15) —
are **not** in the vocabulary yet. Nothing produces them, so adding them would add a column of
`not_checked` with no path to filling it. They join when their task lands a check that can go red.

## Columns (`subject`)

One column per board, not per clinic and not per adapter. The subject id is the pytest param id of
that board, `<ats_type>__<board host>` (`tests/test_adapter_completeness.py:_board_id`), which is
derived from the lowercased `careers_url`; a second board on the same host gets a `-1` suffix.
Columns group by adapter via the `adapter` field on the cell — the 19 entries of
`crawlers.routing.ADAPTERS` (`crawlers/routing.py:43-77`), plus the synthetic `firecrawl` column
that `app/coverage.py` already produces for every clinic with no working adapter.

## Cell schema

One JSON object per line of `data/feature_cells.jsonl`:

```json
{"subject": "rexx__kbo.de", "adapter": "rexx", "feature_id": "round_trip", "verdict": "absent",
 "evidence": [{"path": "tests/test_adapter_completeness.py::test_round_trip[rexx__kbo.de]",
               "line_or_quote": "AssertionError: 2/5 sampled urls failed round trip",
               "fetched_at": "2025-09-10T00:00:00+00:00"}],
 "method": "pytest:test_adapter_completeness", "confidence": "high",
 "checked_by": "tools/cells_from_pytest.py", "checked_at": "2025-09-10T00:00:00+00:00"}
```

The file is append-only. For a repeated `(subject, feature_id)` the **last line wins**; the earlier
lines stay as the history of that cell.

### `verdict`

| value | meaning | scores |
|---|---|---|
| `supported` | the feature is there, evidence attached | 1.0 |
| `partial` | there, but incomplete (some pages, some fields) | 0.5 |
| `absent` | looked, it is not there | 0.0 |
| `unknown` | looked, could not tell (fetch blew up, board ambiguous) | not scored |
| `not_checked` | nobody looked | not scored |

`unknown` and `not_checked` are never collapsed into each other, and never into `absent`. That
collapse is the 2026-09-09 failure `CLAUDE.md` was written about: a cell nobody checked rendered as
a cell that failed, and boards got silently written off. Unscored verdicts are excluded from the
numerator *and* the denominator, so an adapter with nothing checked scores `null`, not `0`.

### `stop: "truncated"`

A run that stops on a budget or credit-cap gate never observed the feature. It is recorded as
`{"verdict": "not_checked", "stop": "truncated"}` with the gate message as evidence — never as a
filled cell, and never as `absent`. The two fields together say more than either enum value alone:
nothing was observed (`not_checked`), and the reason was a budget stop that a re-run can clear
(`stop`). A truncated cell is a stop condition recorded as a stop, not as a success.

## Producing cells

```
.venv/bin/python -m pytest tests/test_adapter_completeness.py -m completeness \
    --json-report --json-report-file=/tmp/completeness.json
.venv/bin/python tools/cells_from_pytest.py /tmp/completeness.json >> data/feature_cells.jsonl
```

The fold maps `passed -> supported`, `failed -> absent`, `error -> unknown`, everything else
(`skipped`, `xfailed`, …) `-> not_checked`, and a budget-stop message anywhere in that set to
`not_checked` + `truncated`. It emits **no** `partial` cells: a pytest assertion is binary, so
`partial` can only come from a human or a swarm worker that saw a degree. A test missing from the
report produces no cell at all, which reads as `not_checked` — the tool never writes a row it did
not observe.

`tools/cells_from_pytest.py --selfcheck` exercises all five outcomes against a synthetic report,
offline.

## Score

`GET /api/coverage` returns two computed fields per adapter row and on `totals`:

- `feature_score` — `100 × Σ(weight × verdict value) / Σ(weight)` over the scored cells of that
  adapter, or `null` when it has no scored cell.
- `feature_verdicts` — the raw count per verdict, so the renderer can show what the score is made of.

The Clawl coverage table (`web/pro.template.html`) renders `feature_score` beside `coverage_pct`.
`null` renders as a dashed grey "not checked" label, never as `0.0 %`.

`app/coverage.py` reads `data/feature_cells.jsonl` without a fallback: if the file is gone,
`/api/coverage` fails loudly. A missing store must not read as "every board scores zero".

## What is not_checked today

**Everything.** `data/feature_cells.jsonl` is empty. 5 rows × every board = 0 cells filled, so
every `feature_score` is `null`.

The completeness harness is `-m network -m completeness` and needs live boards; nothing here was
run against a live board, and no number was invented to fill the gap.

Open questions, not decided here:

- `pytest --json-report` needs the `pytest-json-report` plugin, which is **not installed** and
  cannot be added (no new dependencies). Until it is, no report exists to fold. `--junit-xml` is
  built into pytest and carries the same node ids and messages — adding a second input format to
  the tool is a one-time ~6 lines, but nobody asked for it.
- The ~232 clinics with a blank `ats_type` in `data/registry/clinics.csv` have no board column at
  all, because they have no adapter to run the harness against. Filling them is the swarm job the
  brief describes, gated on the Firecrawl budget; every budget stop there lands as `truncated`.
