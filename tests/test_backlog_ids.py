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


def test_no_two_task_files_share_an_id():
    shared = {i: f for i, f in _files_by_id().items() if len(f) > 1}
    assert not shared, f"{len(shared)} task id(s) held by more than one file:\n" + "\n".join(
        f"  task-{i}: " + " | ".join(f) for i, f in sorted(shared.items()))
