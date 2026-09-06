"""Bridge to pflege_jobs.mechanics.REGISTRY (owned by the pipeline): list, try, run tests.
When the registry module is missing the endpoints answer with an empty list instead of failing."""
import inspect
import re
import subprocess

from . import config as A

_FAKE = None       # tests inject a registry here


def registry():
    if _FAKE is not None:
        return _FAKE
    try:
        from pflege_jobs import mechanics as M
        return list(getattr(M, "REGISTRY", []) or [])
    except Exception:
        return []


def _attr(m, name, default=None):
    if isinstance(m, dict):
        return m.get(name, default)
    return getattr(m, name, default)


def _source(fn):
    try:
        return inspect.getsource(fn)
    except Exception:
        return ""


def describe(m):
    fns = []
    for f in _attr(m, "functions", []) or []:
        fns.append({"name": getattr(f, "__name__", str(f)), "source": _source(f), "doc": (getattr(f, "__doc__", None) or "").strip()})
    test_file = _attr(m, "test_file") or f"tests/test_mech_{_attr(m, 'id')}.py"
    p = A.ROOT / test_file
    n_tests = len(re.findall(r"^def test_", p.read_text(encoding="utf-8"), re.M)) if p.exists() else 0
    return {"id": _attr(m, "id"), "title": _attr(m, "title", {}), "description": _attr(m, "description", {}),
            "patterns_section": _attr(m, "patterns_section"), "functions": fns, "inputs": _attr(m, "inputs", []) or [],
            "test_file": test_file, "n_tests": n_tests}


def get(mid):
    return next((m for m in registry() if _attr(m, "id") == mid), None)


def try_it(m, inputs):
    fn = _attr(m, "try")
    if fn is None:
        fns = _attr(m, "functions", []) or []
        if not fns:
            raise ValueError("mechanic has no callable")
        fn = lambda inp: {"result": fns[0](**inp), "rule": None}      # noqa: E731
    out = fn(dict(inputs or {}))
    if not isinstance(out, dict) or "result" not in out:
        out = {"result": out, "rule": None}
    out.setdefault("rule", None)
    return out


def run_tests(m, timeout=120):
    test_file = describe(m)["test_file"]
    p = A.ROOT / test_file
    if not p.exists():
        return {"passed": 0, "failed": 0, "output": f"{test_file} missing"}
    try:
        r = subprocess.run([A.PYTHON, "-m", "pytest", "-q", str(p)], cwd=str(A.ROOT), capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr)[-6000:]
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "output": f"timeout after {timeout}s"}
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", out))
    failed = sum(int(x) for x in re.findall(r"(\d+) (?:failed|error)", out))
    return {"passed": passed, "failed": failed, "output": out, "rc": r.returncode}
