"""Settings: keyword patterns (pflege_jobs/patterns.json, hot-reloaded), autocrawl schedule, Firecrawl defaults."""
import json
import os
import re
import tempfile

from . import config as A
from . import runs as R
from . import scheduler as S

FIRECRAWL_DEFAULT = {"default_max_credits": 40}


def _patterns_path():
    try:
        from pflege_jobs import config as C
        p = getattr(C, "PATTERNS_PATH", None)
        if p:
            return str(p)
    except Exception:
        pass
    return str(A.PATTERNS_PATH)


def get_patterns():
    try:
        from pflege_jobs import config as C
        pats = getattr(C, "PATTERNS", None)
        if pats:
            return pats
    except Exception:
        pass
    for p in (_patterns_path(), A.FALLBACK_DIR / "patterns.json"):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
    return {"version": 0, "note": "patterns.json not present yet"}


def validate_patterns(obj):
    """Every 're' (and every string under a key that looks like a regex) must compile. Returns list of errors."""
    errs = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "re" and isinstance(v, str):
                    try:
                        re.compile(v, re.I)
                    except re.error as e:
                        errs.append(f"{path}.{k}: {e}")
                elif isinstance(v, str) and k in ("pflege_gate", "nicht_pflege", "strong_pflege", "housing", "bonus", "childcare", "language", "anerkennung", "experience_years", "languages", "email", "legal_forms"):
                    try:
                        re.compile(v, re.I)
                    except re.error as e:
                        errs.append(f"{path}.{k}: {e}")
                else:
                    walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    if not isinstance(obj, dict):
        return ["patterns must be a JSON object"]
    walk(obj, "$")
    return errs


def save_patterns(obj):
    errs = validate_patterns(obj)
    if errs:
        raise ValueError("; ".join(errs[:10]))
    path = _patterns_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".patterns-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    try:
        from pflege_jobs import config as C
        if hasattr(C, "reload"):
            C.reload()
        import importlib
        from pflege_jobs import classify
        importlib.reload(classify)                  # its module-level compiled copies
    except Exception as e:
        return {"saved": path, "reloaded": False, "warning": str(e)[:200]}
    return {"saved": path, "reloaded": True}


def get_all():
    return {"patterns": get_patterns(), "patterns_path": _patterns_path(), "schedule": S.status(),
            "firecrawl": {**FIRECRAWL_DEFAULT, **(R.get_setting("firecrawl") or {})}}


def save_schedule(obj):
    cur = S.schedule()
    for k in ("enabled", "include_firecrawl"):
        if k in obj:
            cur[k] = bool(obj[k])
    for k in ("weekday", "hour", "batches", "firecrawl_weekly_budget", "firecrawl_max_credits"):
        if k in obj and obj[k] is not None:
            cur[k] = int(obj[k])
    if "mode" in obj and obj["mode"] in ("auto", "adapter", "firecrawl"):
        cur["mode"] = obj["mode"]
    cur["hour"] = max(0, min(23, cur["hour"]))
    cur["batches"] = max(1, min(30, cur["batches"]))
    R.set_setting("schedule", cur)
    return S.status()


def save_firecrawl(obj):
    cur = {**FIRECRAWL_DEFAULT, **(R.get_setting("firecrawl") or {})}
    if "default_max_credits" in obj:
        cur["default_max_credits"] = max(1, min(500, int(obj["default_max_credits"])))
    R.set_setting("firecrawl", cur)
    return cur
