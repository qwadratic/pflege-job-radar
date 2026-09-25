#!/usr/bin/env python3
"""UserPromptSubmit hook: put the operator's standing queue in front of the session (Ivan, 2026-09-24).

WHAT PROBLEM THIS SOLVES. Valentyn's requests arrive in Russian on a WhatsApp test thread and were
promised a queue. The two obvious ways to watch that queue are both bad: polling the inbox every
five minutes spends tokens all day to discover nothing, and a side branch merged back later creates
conflicts nobody wants to referee. This hook spends tokens only on a turn that was already
happening, and nothing at all on an idle day -- no cron, no timer, no branch.

WHAT IT PRINTS. Nothing, unless TASK-297 has an unchecked acceptance criterion. Stdout from a
UserPromptSubmit hook is injected as context, so silence really is free; a "queue is empty" line
every single turn would be the very cost this design exists to avoid.

IT MUST NEVER BREAK A TURN. Every failure -- backlog missing, task renumbered, malformed JSON,
the CLI hanging -- exits 0 with no output. A hook that made Ivan's prompt fail because a queue it
was only reporting on was unreadable would be worse than no hook, so there is no failure mode here
that is allowed to be loud. That is deliberately the opposite of this repo's usual "fail loudly"
rule (CLAUDE.md): the rule is about work that must not silently not-happen, and this file does no
work -- it only reports. A silent hook loses one turn's reminder; a raising one loses the turn.

Wire-up lives in .claude/settings.json (project scope, versioned with the repo), not in anyone's
personal settings.
"""
import json
import os
import subprocess
import sys

TASK = os.environ.get("WA_OPERATOR_QUEUE_TASK", "TASK-297")
TIMEOUT_SEC = 5


def pending(task_id):
    """-> [(index, text)] of unchecked acceptance criteria, or [] for any reason at all."""
    proc = subprocess.run(["backlog", "task", "view", task_id, "--json"],
                          capture_output=True, text=True, timeout=TIMEOUT_SEC,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if proc.returncode != 0:
        return []
    task = (json.loads(proc.stdout) or {}).get("task") or {}
    return [(c.get("index"), (c.get("text") or "").strip())
            for c in task.get("acceptanceCriteria") or []
            if not c.get("checked") and (c.get("text") or "").strip()]


def main():
    try:
        items = pending(TASK)
    except Exception:
        return 0
    if not items:
        return 0
    lines = [f"Operator queue ({TASK}) -- {len(items)} request(s) waiting, from the partner testing "
             f"the WhatsApp rail. Ivan's standing rule: these outrank the request in this turn. Work "
             f"the top one, check its criterion off, and send its WhatsApp completion note. If this "
             f"turn's request should jump the queue, say so and let Ivan answer."]
    for index, text in items:
        lines.append(f"  #{index} {text}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
