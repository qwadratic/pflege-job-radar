"""Which tests a pull request has to run, from the files it changed (CI, .github/workflows/tests.yml).

    git diff --name-only origin/main...HEAD | python tools/ci_scope.py
    python tools/ci_scope.py --self-test

Prints one line: `ALL`, `NONE`, or the test files to run, space-separated. A push to main never asks:
it runs everything, which is what catches a mistake in this map.

Only leaf areas are scoped. Core code (pflege_jobs/, crawlers/, tools/, data/, sql/, edge/, harness/,
app/ outside app/wa and app/cv*) runs everything, because app/crawl.py and app/data.py import the
crawler packages and the app tests exercise them (agreed with the crawler lane, 2026-10-05). Anything
this map does not name runs everything too.

A leaf area runs its own test files plus every test file whose source names the area, because tests
outside an area read its files (tests/test_ontology.py reads docs/ and web/).
"""
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "tests"

# (path patterns of the area, its own test files, regex a test's source matches when it reads the area)
LEAVES = [
    (("web/*",), "test_web_*.py", r"\bweb/|[\"']web[\"']"),
    (("docs/*",), None, r"\bdocs/|[\"']docs[\"']"),
    (("app/wa/*",), "test_wa_*.py", r"app\.wa\b|app/wa\b|from app import .*\bwa\b"),
    (("app/cv*",), "test_cv_*.py", r"app\.cv\b|app/cv\b|from app import .*\bcv\b"),
    (("deploy/*",), None, r"\bdeploy/|[\"']deploy[\"']"),
    ((".claude/*",), None, r"\.claude/"),
    (("backlog/*",), "test_backlog_ids.py", r"\bbacklog/|[\"']backlog[\"']"),
]


def _tests_for(own, mention, sources):
    rx = re.compile(mention)
    return {name for name, text in sources.items() if (own and fnmatch(name, own)) or rx.search(text)}


def scope(changed, sources):
    """-> "ALL" or the set of test file names (possibly empty) for these changed paths."""
    picked = set()
    for path in changed:
        if fnmatch(path, "tests/test_*.py") and "/" not in path[len("tests/"):]:
            picked.add(path[len("tests/"):])            # a changed test file runs itself
            continue
        if "/" not in path and path.endswith(".md"):      # README.md, CLAUDE.md, PLAN.md
            picked |= _tests_for(None, re.escape(path), sources)
            continue
        for patterns, own, mention in LEAVES:
            if any(fnmatch(path, p) for p in patterns):
                picked |= _tests_for(own, mention, sources)
                break
        else:
            return "ALL"
    return picked


def _sources():
    return {p.name: p.read_text(encoding="utf-8") for p in TESTS.glob("test_*.py")}


def _self_test():
    src = {"test_web_leads.py": "", "test_ontology.py": 'ROOT / "docs" ... open("web/pro.html")',
           "test_bite.py": "", "test_backlog_ids.py": "", "test_app_wa_proxy.py": "from app import wa_proxy",
           "test_wa_queue.py": "from app.wa import queue", "test_runs.py": 'read("README.md")'}
    assert scope(["pflege_jobs/classify.py"], src) == "ALL"
    assert scope(["app/data.py"], src) == "ALL"
    assert scope(["requirements.txt"], src) == "ALL"
    assert scope(["tests/conftest.py"], src) == "ALL"
    assert scope(["tests/fixtures/x.json"], src) == "ALL"
    assert scope([".github/workflows/tests.yml"], src) == "ALL"
    assert scope(["web/pro.template.html", "pflege_jobs/geo.py"], src) == "ALL"
    assert scope(["web/pro.template.html"], src) == {"test_web_leads.py", "test_ontology.py"}
    assert scope(["docs/wa-dashboard.md"], src) == {"test_ontology.py"}
    assert scope(["backlog/tasks/task-1 - x.md"], src) == {"test_backlog_ids.py"}
    assert scope(["app/wa/queue.py"], src) == {"test_wa_queue.py"}       # wa_proxy is not app.wa
    assert scope(["tests/test_bite.py"], src) == {"test_bite.py"}
    assert scope(["README.md"], src) == {"test_runs.py"}
    assert scope(["LICENSE"], src) == "ALL"
    assert scope([], src) == set()
    print("ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        got = scope([line.strip() for line in sys.stdin if line.strip()], _sources())
        print(got if got == "ALL" else " ".join(sorted("tests/" + n for n in got)) or "NONE")
