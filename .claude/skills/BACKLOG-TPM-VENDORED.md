# backlog-technical-project-manager, vendored (one file, not the MCP server)

Source: [MrLesk/Backlog.md](https://github.com/MrLesk/Backlog.md/blob/main/.codex/skills/backlog-technical-project-manager/SKILL.md),
MIT, last touched upstream in commit `9016c91` (2026-02-21, "Update backlog-technical-project-manager skill");
upstream's `.claude/skills` is a symlink to `.codex/skills`, so this is the file Claude Code loads there too.
Installed 2026-09-11 alongside the `backlog` CLI 1.51.0 (`npm i -g backlog.md`), which this repo already
drives from `CLAUDE.md` and `.claude/agents/project-manager-backlog.md`.

Copied verbatim except three edits, all so the text is true for this repo:

| where | upstream | here |
| --- | --- | --- |
| TPM Operating Constraints §1 | `backlog://workflow/*` MCP resources only | + one line naming the CLI equivalents (`backlog instructions …`), because no MCP server is registered here |
| Sub-Agent Workspace Rules | `../Backlog.md-copies/backlog-<taskId>`, `bun i` | `../pflege-board-copies/pflege-board-<taskId>`, `python -m venv .venv && pip install -r requirements.txt` |
| Common Agent Mistakes §2 | `git remote set-url origin https://github.com/MrLesk/Backlog.md.git` | `…/qwadratic/pflege-job-radar.git` |

The `agents/openai.yaml` beside it upstream (Codex UI metadata, `allow_implicit_invocation: false`) has no
Claude Code equivalent; the same intent is the skill's own Activation Rule, so it was not copied.
The skill keeps upstream's "asks Codex" wording in its description; it applies to whichever agent is running.
