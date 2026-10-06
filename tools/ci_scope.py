"""Which tests a pull request has to run, from the files it changed (CI, .github/workflows/tests.yml).

    git diff --name-only origin/main...HEAD | python tools/ci_scope.py [--job offline] [--event pull_request]
    git diff --name-only origin/main...HEAD | python tools/ci_scope.py --job adapters
    python tools/ci_scope.py --event push [--job adapters]       # no stdin read: everything runs
    python tools/ci_scope.py --self-test

CI has two jobs. `offline` runs `pytest -m "not mirror and not network and not llm"` without the mirror, without secrets, fork PRs
included; `--job offline` (the default) prints one line: `ALL`, `NONE`, or the test files to run, space-separated, with the modules
that are all `mirror` tests left out (they belong to `adapters`; pytest exits 5, "nothing collected", over a file that is all
deselected). `adapters` pulls the mirror and runs `pytest -m mirror`; `--job adapters` prints `yes` or `no`. A push to main (any event
that is not a pull request) never asks: it runs everything, which is what catches a mistake in this map.

Only leaf areas are scoped. Core code (pflege_jobs/, crawlers/, tools/, data/, sql/, edge/, harness/,
app/ outside app/wa and app/cv*) runs everything, because app/crawl.py and app/data.py import the
crawler packages and the app tests exercise them (agreed with the crawler lane, 2026-10-05). Anything
this map does not name runs everything too.

A leaf area runs its own test files plus every test file whose source names the area, because tests
outside an area read its files (tests/test_ontology.py reads docs/ and web/).

A leaf that is Python code (app/wa/, app/cv*) also runs every test file that imports the changed
module through any chain of repo imports: app.main imports app.cv, which imports app.wa, so a test
of the board API reaches the harness without ever naming it.
"""
import argparse
import ast
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

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


def _module_file(name, root):
    """Repo file of a dotted module name, or None when it is not a module of this repo."""
    base = root.joinpath(*name.split("."))
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _imports(path, root):
    """Repo files this file imports directly. `from a import b` counts a and, when it is a module, a.b."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    package = list(path.relative_to(root).parts[:-1])
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            up = package[:len(package) - node.level + 1] if node.level else []
            base = ".".join(up + ([node.module] if node.module else []))
            if base:
                names.add(base)
            names |= {f"{base}.{a.name}" if base else a.name for a in node.names}
    found = set()
    for name in names:
        parts = name.split(".")
        for i in range(1, len(parts) + 1):               # importing a.b.c also runs a and a.b
            f = _module_file(".".join(parts[:i]), root)
            if f:
                found.add(f)
    return found


def import_closures(root=ROOT):
    """{test file name: every repo path (posix, relative) it imports through any chain}."""
    direct = {}

    def reach(path):
        seen, todo = set(), [path]
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            if p not in direct:
                direct[p] = _imports(p, root)
            todo += direct[p]
        return {q.relative_to(root).as_posix() for q in seen}
    return {t.name: reach(t) for t in (root / "tests").glob("test_*.py")}


def _importers(path, patterns, closures):
    """Test files whose closure holds the changed module; for a non-Python file of a code area
    (a prompt, a fixture), every test that imports anything from that area."""
    if path.endswith(".py"):
        return {name for name, files in closures.items() if path in files}
    return {name for name, files in closures.items() if any(fnmatch(f, p) for f in files for p in patterns)}


def _tests_for(own, mention, sources):
    rx = re.compile(mention)
    return {name for name, text in sources.items() if (own and fnmatch(name, own)) or rx.search(text)}


def scope(changed, sources, closures=None):
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
                if patterns[0].startswith("app/"):
                    picked |= _importers(path, patterns, closures if closures is not None else import_closures())
                break
        else:
            return "ALL"
    return picked


# What can change what the mirror tests (`pytest -m mirror`, job `adapters`) see: the adapters, the replay layer and the recorder, the
# tests themselves, the marker and the dependencies, the CI. The scope saying ALL starts the job too; this list is for the changes that
# are not ALL (a test file of its own) and for saying why the rest is.
ADAPTER_PATHS = ("crawlers/*", "tests/mirror.py", "tests/adapter_*.py", "tests/test_completeness_*.py", "tests/test_adapter_completeness.py",
                 "tests/test_verify_pi_loga_live.py", "tools/mirror.py", "pytest.ini", "requirements.txt", ".github/workflows/*")
_MIRROR_MARK = re.compile(r"^pytestmark\s*=\s*pytest\.mark\.mirror\b", re.M)


def mirror_only(sources):
    """Test files that assign `pytestmark = pytest.mark.mirror` at module level: all their tests need the pulled mirror."""
    return {name for name, text in sources.items() if _MIRROR_MARK.search(text)}


def offline(changed, sources, closures=None):
    """-> "ALL" or the set of test file names job `offline` runs: `scope`, without the modules that are all mirror tests."""
    got = scope(changed, sources, closures)
    return got if got == "ALL" else got - mirror_only(sources)


def adapters(changed, sources, closures=None):
    """-> True when job `adapters` (pull the mirror, `pytest -m mirror`) has to run for these changed paths."""
    return scope(changed, sources, closures) == "ALL" or any(fnmatch(path, pat) for path in changed for pat in ADAPTER_PATHS)


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
    cl = {"test_app_api.py": {"tests/test_app_api.py", "app/main.py", "app/cv.py", "app/wa/__init__.py", "app/wa/slots.py"},
          "test_bite.py": {"tests/test_bite.py", "pflege_jobs/sources/bite.py"}}
    assert scope(["app/wa/queue.py"], src, cl) == {"test_wa_queue.py"}   # wa_proxy is not app.wa
    assert scope(["app/wa/slots.py"], src, cl) == {"test_wa_queue.py", "test_app_api.py"}
    assert scope(["app/wa/prompts/luna.md"], src, cl) == {"test_wa_queue.py", "test_app_api.py"}
    assert scope(["app/cv.py"], src, cl) == {"test_app_api.py"}
    import tempfile
    with tempfile.TemporaryDirectory() as d:              # the real walk: a test -> app.main -> .cv -> .wa
        root = Path(d)
        for rel, text in {"tests/test_x.py": "from app import main", "app/__init__.py": "",
                          "app/main.py": "from . import cv", "app/cv.py": "from .wa import slots",
                          "app/wa/__init__.py": "", "app/wa/slots.py": "import os"}.items():
            root.joinpath(rel).parent.mkdir(parents=True, exist_ok=True)
            root.joinpath(rel).write_text(text)
        assert "app/wa/slots.py" in import_closures(root)["test_x.py"]
    assert scope(["tests/test_bite.py"], src) == {"test_bite.py"}
    assert scope(["README.md"], src) == {"test_runs.py"}
    assert scope(["LICENSE"], src) == "ALL"
    assert scope([], src) == set()
    src["test_completeness_dvinci.py"] = "import pytest\npytestmark = pytest.mark.mirror\n"      # the mirror split (job offline / job adapters)
    assert mirror_only(src) == {"test_completeness_dvinci.py"}
    assert offline(["tests/test_completeness_dvinci.py"], src) == set()                          # all deselected: pytest would exit 5
    assert offline(["tests/test_bite.py"], src) == {"test_bite.py"} and offline(["crawlers/x.py"], src) == "ALL"
    assert adapters(["crawlers/x.py"], src) and adapters(["tests/test_completeness_dvinci.py"], src) and adapters(["tests/mirror.py"], src)
    assert adapters([".github/workflows/tests.yml"], src) and adapters(["requirements.txt"], src) and adapters(["pytest.ini"], src)
    assert not adapters(["docs/wa-dashboard.md"], src) and not adapters(["tests/test_bite.py"], src) and not adapters([], src)
    print("ok")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--job", choices=("offline", "adapters"), default="offline")
    ap.add_argument("--event", default="pull_request", help="anything but pull_request is a full run and reads no stdin")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    changed = [line.strip() for line in sys.stdin if line.strip()] if a.event == "pull_request" else None
    if a.job == "adapters":
        print("yes" if changed is None or adapters(changed, _sources()) else "no")
        return
    got = "ALL" if changed is None else offline(changed, _sources())
    print(got if got == "ALL" else " ".join(sorted("tests/" + n for n in got)) or "NONE")


if __name__ == "__main__":
    main(sys.argv[1:])
