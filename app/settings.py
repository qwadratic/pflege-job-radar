"""Settings: keyword patterns (pflege_jobs/patterns.json, hot-reloaded), autocrawl schedule, Firecrawl defaults."""
import json
import os
import re
import tempfile

from . import config as A
from . import runs as R
from . import scheduler as S

FIRECRAWL_DEFAULT = {
    "default_max_credits": 40, "weekly_budget": 100,
    # spend gate + 24h kill switch (docs/firecrawl.md) -- eur_per_credit is Hobby-plan pricing
    # ($16/3000); the user's actual 8000/mo plan price is unknown so it stays editable here.
    "eur_per_credit": 0.0053, "max_eur_unknown_clinic": 5.0,
    "kill_switch_pct": [10, 20, 30],       # % of plan credits spent in a rolling 24h -> warn / throttle / disable
    "reserve_credits": 150,                # never let remaining credits fall below this in the current period
}


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


def get_firecrawl():
    cur = {**FIRECRAWL_DEFAULT, **(R.get_setting("firecrawl") or {})}
    legacy = R.get_setting("schedule") or {}
    if "weekly_budget" not in (R.get_setting("firecrawl") or {}) and legacy.get("firecrawl_weekly_budget"):
        cur["weekly_budget"] = int(legacy["firecrawl_weekly_budget"])
    cur["spent_7d"] = R.usage_total(days=7)
    return cur


def public_firecrawl():
    """get_firecrawl() minus the webhook secret: GET /api/settings has no auth, so the secret must never leave the server."""
    return {k: v for k, v in get_firecrawl().items() if k != "webhook_secret"}


def get_all():
    return {"patterns": get_patterns(), "patterns_path": _patterns_path(), "scheduler": S.status(), "firecrawl": public_firecrawl()}


def save_firecrawl(obj):
    cur = {k: v for k, v in get_firecrawl().items() if k != "spent_7d"}
    if "default_max_credits" in obj:
        cur["default_max_credits"] = max(1, min(500, int(obj["default_max_credits"])))
    if "weekly_budget" in obj:
        cur["weekly_budget"] = max(0, min(8000, int(obj["weekly_budget"])))
    if "eur_per_credit" in obj:
        cur["eur_per_credit"] = max(0.0, float(obj["eur_per_credit"]))
    if "max_eur_unknown_clinic" in obj:
        cur["max_eur_unknown_clinic"] = max(0.0, float(obj["max_eur_unknown_clinic"]))
    if "reserve_credits" in obj:
        cur["reserve_credits"] = max(0, int(obj["reserve_credits"]))
    if "kill_switch_pct" in obj:
        pct = obj["kill_switch_pct"]
        if not (isinstance(pct, list) and len(pct) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pct)
                and pct[0] < pct[1] < pct[2]):
            raise ValueError("kill_switch_pct must be an ascending [warn, throttle, disable] triple, e.g. [10, 20, 30]")
        cur["kill_switch_pct"] = [float(x) for x in pct]
    R.set_setting("firecrawl", cur)
    return public_firecrawl()
