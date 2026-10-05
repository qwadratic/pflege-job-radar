"""Every backlog task id is held by exactly one task file.

Two branches that each create a task get the same next id from the backlog CLI. Their file names differ
(the title slug), so git merges both without a conflict and the backlog then holds two tasks under one id.
This test turns that silent merge into a red build. It names every shared id with its files; the fix is to
give one of the tasks a new id and rewrite the references to it.
"""
import collections
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUBDIRS = ("tasks", "completed", "drafts", "archive/tasks")
NAME = re.compile(r"^task-([0-9][0-9a-z.]*) - .+\.md$")     # 163, 283.7, 58a


def _files_by_id():
    by_id = collections.defaultdict(list)
    for sub in SUBDIRS:
        for p in sorted((ROOT / "backlog" / sub).glob("task-*")):
            m = NAME.match(p.name)
            assert m, f"{p.relative_to(ROOT)} does not look like 'task-<id> - <title>.md'"
            by_id[m.group(1)].append(p.relative_to(ROOT).as_posix())
    return by_id


def test_each_task_file_carries_the_id_of_its_name():
    """A renumbering that renames the file but not its `id:` line (or the reverse) leaves a task nobody can find."""
    bad = []
    for sub in SUBDIRS:
        for p in sorted((ROOT / "backlog" / sub).glob("task-*")):
            m = NAME.match(p.name)
            line = re.search(r"^id:\s*(\S+)\s*$", p.read_text(encoding="utf-8").split("\n---", 1)[0], re.M)
            if not m or not line or line.group(1).lower() != f"task-{m.group(1)}":
                bad.append(f"{p.relative_to(ROOT).as_posix()}: id line {line.group(1) if line else None!r}")
    assert not bad, f"{len(bad)} task file(s) whose `id:` line does not match the file name:\n" + "\n".join(bad)


def test_no_two_task_files_share_an_id():
    shared = {i: f for i, f in _files_by_id().items() if len(f) > 1}
    assert not shared, f"{len(shared)} task id(s) held by more than one file:\n" + "\n".join(
        f"  task-{i}: " + " | ".join(f) for i, f in sorted(shared.items()))
