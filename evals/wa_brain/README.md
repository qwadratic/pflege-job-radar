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
 "region": "bavaria", "note": "optional, English",
 "edits": [{"what": "city named by the candidate", "from": "Bremen", "to": "Augsburg"}]}
```

`candidate_id`: the sales-brain candidate. `at_turns`: turn numbers to run (one candidate may have several
points). `region`: `bavaria` or `other`. `covers.archetype`: `engaged_with_documents`, `engaged_talker`,
`middle`, `short_replies` or `never_replied_or_decliner`; `scenario` is a free slug. Silence follow-ups are NOT
covered: they are not an inbound turn (they depend on the planned next_step feature).

`edits` (optional): a record of every variable you changed in this eval copy of a real conversation (a city, a
region). It is an **echo only**: copied into `<id>.points.json` and printed per case in the run log and the final
report, never applied by code (there is no substitution logic) and never sent to the judge. Keep edits small: a
variable, not a rewrite of the conversation.

## Prepare (once per case), then run

```
python evals/wa_brain/run.py --list-turns 4711 --sales-brain PATH       # no model call; pick turns
python evals/wa_brain/run.py --prepare --cases DIR --out DIR --sales-brain PATH [--media-root DIR] [id-substring]
python evals/wa_brain/run.py --cases DIR --out DIR [--runs 3] [--sales-brain PATH] [--media-root DIR] [id-substring]
python evals/wa_brain/run.py --judge DIR [--judge-model M] [--judge-effort E]
```

`--list-turns` prints per turn the first 120 characters of the inbound burst and of the old bot's reply, phone-like
numbers scrubbed (`+?\d[\d ()/-]{7,}\d` -> `<num>`).

**Why a preparation.** A chosen mid-conversation turn must see the card (and the documents) a real thread would
hold at that point, and the real files must be attached, read and classified like live. Neither exists without
running the earlier turns. `--prepare` does that **once per case**: per candidate one walk that runs the REAL brain
on every turn before the last chosen one and reads the real files (real `read_and_classify`, same card updates as
a live media message), and captures, per chosen turn, right before the brain would run it (after that turn's own
files are ingested): the whole card (`slots` with its underscore keys, nothing dropped), `asked`, the stop flags,
every `wa_documents` row of the thread (text, classification, path) and the turn's file list. It writes
`DIR/prep/<case id>.json` (directory 0700, file 0600; also git sha, model, effort, prepared-at, the files met
with their status). All model calls of the preparation happen here and cost real tokens once per case; if an
earlier turn errors or a file fails to read, nothing is written and the command exits 1. Prep files hold real
conversation text and CV text: they live in `--cases DIR`, outside every checkout.

**What is free afterwards.** A run reuses the prep: earlier turns are plain history (inbound and the real
replies recorded, no model call, no file read), the seed is applied right before each chosen turn (card, `asked`,
documents; the document rows keep a path to the read-only source file) and the brain runs once there. No
planning and no recomputation per run; re-running after a prompt change costs only the chosen turns. A case with
a chosen turn after the first, or with a readable file in a chosen turn, and no prep file stops the run before any
model call and prints the exact `--prepare` command; so does a prep made for other `candidate_id`/`at_turns`.
A first-turn point without files needs none. The prep is a snapshot: after a change of case turns or of the
conversation, prepare again (the run log prints the prep's git sha, model, effort and date).

**Files.** An inbound document/image resolves via `candidate_attachments.storage_path` under `--media-root`
(repeatable; default is the one readable CRM root `/opt/clinic-dispatcher/data/private/candidate_whatsapp_media`;
a root that is missing or unreadable is an error, never skipped, and the bridge-v2 root this user cannot read is not
worked around). If the file is not there, any OTHER attachment row with the same sha256 whose file exists is used.
A file whose bytes do not match the recorded sha256 is an error. The sources are only read. A file that
cannot be found is **unavailable**: the turn keeps today's placeholder (`[document].pdf`), and it is recorded, never
dropped: `file_unavailable: true` plus a `files` list (kind, status, reason; no name, no path) in the turn's JSONL
line, `files` in the prep, a "file unavailable" note in the run log, `inbound_files` in `<id>.points.json`.
Voice notes are not transcribed here: the old system's transcript, if the CRM has one, is the text; without one it
is an unavailable file.

**Substitute files.** A case may carry `"substitute_files": {"cv_attachment_id": 10, "qualification_attachment_id": 11}`
(donor rows of `candidate_attachments`, from another candidate): an eval-input edit like `edits`, recorded, never a
fallback. Only rows unavailable for `not_on_a_readable_root` are touched (never a readable file, voice note or
missing row): each such row gets the donor of the kind the old system's CRM class says the original was (`urkunde` -> certificate, `cv`/`cv_standardized` -> CV; other or no class stays unavailable), so the context is the same and only the person differs, and the bot can
open any earlier file from history. `--donor-packs FILE` (JSON list of such objects, outside the checkout) gives every
case without its own `substitute_files` the pack `packs[candidate_id % len(packs)]`, logged per case; use it on every
run. The donor goes through the same sha256 check and live media path; an unreadable donor is an error. Entries say
`found_via: "substitute"` plus `substitute_kind`/`substitute_attachment_id` (no name, path); the judge sees them as
attached; the prepare log prints "N substituted"; `<id>.points.json` echoes the field. All cases of one candidate must agree.

**A/B arms.** `WA_LUNA_LOCKED_TEMPLATES` (`app/wa/config.py`) picks the arm: `all` (default, today) or `exceptions` (the
harness sends no locked out-of-scope-region text and no locked refusal; the model answers both itself). The
environment is passed through unchanged, so run each arm with the same cases and prep and only the variable
changed: `WA_LUNA_LOCKED_TEMPLATES=exceptions python evals/wa_brain/run.py --cases DIR --out DIR2 ...`. Every JSONL
record, and every prep file, carries `locked_templates` next to `model`/`effort`; use a separate `--out` per arm.

A run replays each case `--runs` times (default 3), fresh scratch state each pass, through
`app/wa/luna/replay.py` with `at_turns` and the case's seeds. Output per case: `<id>.jsonl` (turn, run, bubbles,
action, escalation, error, git sha, model, effort, file_unavailable) and `<id>.points.json` (history, message,
files, old reply, edits). The brain runs with `WA_LUNA_MODEL`/`WA_LUNA_EFFORT` from your environment (config
defaults otherwise); the JSONL records what was used. In a worktree without `config/wa-client.json`, point
`WA_CLIENT_CONFIG` at one.

## Isolation

Live credentials and transport variables are dropped and `WA_LUNA_NO_SEND=1` is set before the app is imported;
every turn runs `no_send=True`. The sales brain is opened read-only. Scratch SQLite and `LUNA_SESSION_DIR` sit
under `--out/scratch/<id>/r<N>/` (the preparation walk: `--out/scratch/prepare/<candidate_id>/`). Source files are
only read, never copied or written. The board is a small invented Bavarian fixture (in `run.py`), served to the
brain and to its MCP tools server, never the live board. So board-specific answers (which clinic, how many
postings) differ from what the old bot said; read them as behaviour, not as data.

## Judge

One `claude -p` call per point (`--tools ""`, `--output-format json`, model/effort default to the
`AGENT_NOTE_DECODE_*` config constants, a Sonnet tier). It sees the scrubbed history, the candidate's message, one
neutral `inbound_files` line when files came with that message (`attached` = the file's content was readable,
`unavailable` = only a placeholder was; the judge is told not to fault a reply for not using unavailable content)
and an unlabelled, shuffled set of replies: the old reply plus every successful new run. It first writes its own
analysis of the best reply, then scores each reply `pass|partial|fail` with one sentence, and comments on
variance. Code unseals the labels afterwards (`judge_key.json`, never shown to the judge), counts an errored run
as `fail`, and computes better/equal/worse (mean new grade vs old grade, band 0.25). One final call reads all
points and gives the overall comment. Output: a compact report on stdout and `results.json`.

## Limits

- The prepared card is what Luna's own earlier turns made of the conversation (the real replies are history, Luna's
  are not), on the invented fixture board, with the model and prompts of the preparation. It is a stand-in for a live
  thread, not the old bot's state.
- Unavailable files (in the CRM snapshot of 2026-10-07, 95 of 161 inbound attachments had no readable copy) run on placeholders;
  read such a point as "what Luna does when it only sees that a file arrived".
- Old replies and new replies are judged by a model: use it to find where to look, then read the pairs.
- Phone-like numbers are scrubbed before the judge; names are not, the judge is told never to repeat them.

## Cost

Each point costs `--runs` real brain turns (default model Opus tier, high effort, with board tool calls) plus
one judge call; a decline point adds the refusal classifier. 20 points x 3 runs is 60 brain turns. Start with
one or two cases. `--prepare` is paid once per case: one brain turn per earlier turn plus one reading and one
classification call per readable file (images use the vision reader).

Smoke test (offline, fakes only): `tests/test_wa_brain_eval_smoke.py`; capture, seeds and files:
`tests/test_wa_replay.py`.
