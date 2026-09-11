# pstack, vendored (six files, not the plugin)

Source: [cursor/plugins/pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan (@poteto),
MIT, manifest v0.15.1, commit `7366ac1` (2026-09-09). Licence notice: `PSTACK-LICENSE` beside this file.

Copied verbatim, one directory each, following the convention of `.claude/skills/firecrawl/`:

| skill | why it is here |
| --- | --- |
| `principle-prove-it-works` | verify against the real artifact, never a proxy or a self-report |
| `principle-test-behavior-not-implementation` | what the completeness contract already does against a live board |
| `principle-fix-root-causes` | matches the repo's bug rule |
| `principle-boundary-discipline` | the adapter/oracle seam depends on it |
| `principle-sequence-verifiable-units` | one verifiable unit at a time, evidence before the next |
| `blast-radius` | what a change breaks outside its diff, proved by running code |

The five `principle-*` files had `disable-model-invocation: true` removed so they can load when they apply;
`blast-radius` keeps it, because it is a workflow you invoke on purpose. That one line is the only edit.
`blast-radius` mentions the `how` and `why` skills, which are deliberately not vendored — read it as prose,
not as a link.

## What was deliberately left out

The plugin is not installed and no marketplace is registered.

- **The hook.** Both Claude ports (`ericlitman/open-pstack`, `michael-denyer/pstack-claude`) register a
  `SessionStart` hook on `startup|clear|compact` that injects an `<EXTREMELY_IMPORTANT>` block ordering every
  session into `pstack:poteto-mode`. It would land in context beside this machine's existing caveman and
  ponytail SessionStart setup, and a plugin update restores it after any manual delete.
- **`setup-pstack`.** The only skill in the set without `disable-model-invocation`, and the port writes
  `~/.claude/pstack-models.md` and edits `~/.claude/CLAUDE.md`. Global config is a human's call.
- **The multi-model playbooks** (`poteto-mode`, `arena`, `swarm`, `interrogate`, `how`, `why`, `teach`,
  `architect`). They shell out to the `claude`, `codex` and `grok` CLIs. Credits are this repo's binding
  constraint, and `.claude/workflows/judge-runner.js` already covers multi-lens review.
- **`principle-never-block-on-the-human`.** "Proceed, and let the human course-correct after the fact"
  is the opposite of this repo's `CLAUDE.md` rule ("If a limit or guard seems necessary, ask first").
- **`no-comments` / `comment-sicko`.** They would strip the docstrings that *are* the method in
  `tests/adapter_contract.py`, `tests/test_adapter_completeness.py` and `app/coverage.py`.
- **`make-bot-ui`** (the Grok Bot piece Ivan remembered). It pipes `tailscale.com/install.sh` into
  `sudo sh` and binds the served page to `0.0.0.0`. Both Claude ports drop it too.
- **`create-verification-skill` / `maintain-verification-skill`.** These hold the "feature map" Ivan asked to
  cut: a generated `verify-<app>/` skill plus one Markdown file per user-facing feature. This repo already has
  the harder version of the same idea — a live board as oracle (`tests/test_adapter_completeness.py`), evidence
  on disk (`crawl_snapshots/`), and mutation tests that prove each check can go red (`pytest -m mutation`).
  What was taken from them instead is written down in `docs/feature-matrix.md`: the triage split (doc drift /
  harness gap / product gap), the three run outcomes (clean / changed / blocked), "never edit product code
  during a verification run", and "a cleanup that eats the proof fails the step".
