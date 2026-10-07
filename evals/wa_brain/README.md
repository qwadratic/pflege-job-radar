# WhatsApp brain eval (manual)

Compares the OLD WhatsApp bot with the NEW brain (Luna, `app/wa/luna_brain.py`) on real conversations.
**Run by hand, never in CI, never collected by pytest** (no `test_*.py` under `evals/`).

- **Phase 1 (now):** a test point is a real old conversation, taken as it was, old bot's replies included as
  history, with the new brain run at a chosen turn. A judge scores the old reply and the new runs side by side.
  Nobody writes a best answer in advance.
- **Phase 2 (later, once our bot runs live):** the history holds our own bot's replies; new features get new points.

## Where things live

The repo is public; conversations hold real PII. Case files, results and scratch state live in directories
**outside every git checkout**: `--cases` and `--out` (and `--judge DIR`) are refused inside one. The repo carries
only code, this README and `example_case.json` (synthetic; the smoke test pairs it with a fake sales brain).

## Case file (`DIR/*.json`)

```json
{"id": "short_slug", "candidate_id": 4711, "at_turns": [3, 7],
 "covers": {"archetype": "engaged_talker", "scenario": "asks_salary"},
 "region": "bavaria", "note": "optional, English"}
```

`candidate_id`: the sales-brain candidate. `at_turns`: turn numbers to run (one candidate may have several
points). `region`: `bavaria` or `other`. `covers.archetype`: `engaged_with_documents`, `engaged_talker`,
`middle`, `short_replies` or `never_replied_or_decliner`; `scenario` is a free slug. Silence follow-ups are NOT
covered: they are not an inbound turn (they depend on the planned next_step feature).

## Run

```
python evals/wa_brain/run.py --list-turns 4711 --sales-brain PATH       # no model call; pick turns
python evals/wa_brain/run.py --cases DIR --out DIR [--runs 3] [--sales-brain PATH] [id-substring]
python evals/wa_brain/run.py --judge DIR [--judge-model M] [--judge-effort E]
```

`--list-turns` prints per turn the first 120 characters of the inbound burst and of the old bot's reply, phone-like
numbers scrubbed (`+?\d[\d ()/-]{7,}\d` -> `<num>`). A run replays each case `--runs` times (default 3), fresh
scratch state each pass, through `app/wa/luna/replay.py` with `at_turns`: chosen turns get one brain turn each,
all other turns are recorded as plain history with no model call. Output per case: `<id>.jsonl` (turn, run,
bubbles, action, escalation, error, git sha, model, effort) and `<id>.points.json` (history, message, old reply).
The brain runs with `WA_LUNA_MODEL`/`WA_LUNA_EFFORT` from your environment (config defaults otherwise); the JSONL
records what was used. In a worktree without `config/wa-client.json`, point `WA_CLIENT_CONFIG` at one.

## Isolation

Live credentials and transport variables are dropped and `WA_LUNA_NO_SEND=1` is set before the app is imported;
every turn runs `no_send=True`. The sales brain is opened read-only. Scratch SQLite and `LUNA_SESSION_DIR` sit
under `--out/scratch/<id>/r<N>/`. The board is a small invented Bavarian fixture (in `run.py`), served to the
brain and to its MCP tools server, never the live board. So board-specific answers (which clinic, how many
postings) differ from what the old bot said; read them as behaviour, not as data.

## Judge

One `claude -p` call per point (`--tools ""`, `--output-format json`, model/effort default to the
`AGENT_NOTE_DECODE_*` config constants, a Sonnet tier). It sees the scrubbed history, the candidate's message and
an unlabelled, shuffled set of replies: the old reply plus every successful new run. It first writes its own
analysis of the best reply, then scores each reply `pass|partial|fail` with one sentence, and comments on
variance. Code unseals the labels afterwards (`judge_key.json`, never shown to the judge), counts an errored run
as `fail`, and computes better/equal/worse (mean new grade vs old grade, band 0.25). One final call reads all
points and gives the overall comment. Output: a compact report on stdout and `results.json`.

## Limits

- The card is advanced only by turns that really run: a late point sees a card the skipped turns before it did not
  update, and the brain sees every earlier outbound row as history since its last real turn.
- Old replies and new replies are judged by a model: use it to find where to look, then read the pairs.
- Phone-like numbers are scrubbed before the judge; names are not, the judge is told never to repeat them.

## Cost

Each point costs `--runs` real brain turns (default model Opus tier, high effort, with board tool calls) plus
one judge call; a decline point adds the refusal classifier. 20 points x 3 runs is 60 brain turns. Start with
one or two cases.

Smoke test (offline, fakes only): `tests/test_wa_brain_eval_smoke.py`.
