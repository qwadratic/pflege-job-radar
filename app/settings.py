"""Settings: keyword patterns (pflege_jobs/patterns.json, hot-reloaded), autocrawl schedule, Firecrawl defaults."""
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone

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
    "enabled": True,                       # false = no Firecrawl call at all (the hunter reads it as a kill switch)
}

# app/hunter.py -- the resilient runner over every fetch=='firecrawl' Plan-KH clinic (docs/firecrawl.md §6).
HUNTER_DEFAULT = {
    "enabled": False,                      # the daemon only submits while this is true (POST /api/hunter/start|stop)
    "concurrency": 3,                      # agent runs in flight at once
    "cap": 120,                            # maxCredits of the first attempt (a 60 cap fails on any real board, 120 succeeds)
    "escalate_cap": 200,                   # one retry at this cap after an UNBILLED 'Agent reached max credits' failure
    "max_refills": 2,                      # combined bar: stop once refills >= this AND cost_per_posting > max_usd_per_posting
    "max_usd_per_posting": 0.5,
    "max_credits_per_hour": 600,           # burn rate over the hunter's runs of the last 60 minutes
    "min_tokens": 300,                     # Extract-token pool floor
    "max_charge_per_run": 150,             # one run charging more than this is suspicious
    "max_tokens_per_run": 2500,            # one run moving more Extract tokens than this is suspicious
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


REQUIRED_SECTIONS = ("employer", "role", "qualification", "department", "enrichment")   # pflege_jobs/config.py:validate


def validate_patterns(obj):
    """Every 're' must compile AND the document must be one the classifier can actually load. Returns a list
    of errors; empty means save_patterns() may write it.

    The structural half is pflege_jobs/config.py:validate(), the very function reload() calls -- not a second
    copy of the section list, so the two cannot drift. It is here and not only in save_patterns() because
    POST /api/settings/patterns/validate is the dry run for this route and has to answer the same question.

    Why it exists: until 2026-09-11 this checker only asked "do the regexes compile?", so a body with no
    regexes in it passed. PUT /api/settings/patterns with an EMPTY body is `{}` (app/data.py:json_body
    documents that on purpose) -- it validated, was written over pflege_jobs/patterns.json, and answered
    200 {"reloaded": false}: 14,131 bytes and 9 sections replaced by 2 bytes, and every classifier then died
    with ValueError: patterns: missing section 'employer'."""
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
    from pflege_jobs import config as C          # an ImportError here must fail loudly, not skip the check
    stored = get_patterns()
    if all(k in stored for k in REQUIRED_SECTIONS):          # not the "not present yet" placeholder
        dropped = sorted(set(stored) - set(obj))
        if dropped:
            # The second half of the same incident. PUT replaces the whole document, and C.validate() only
            # requires the five sections classify.py cannot start without -- so a body carrying just those
            # five passes, writes, and silently drops the rest. Measured: `cv` (app/cv.py's CV regexes) gone,
            # and `excluded_role_classes` gone took the INTAKE POLICY with it -- pflege_jobs/config.py:_apply
            # falls back to a 3-entry default, so `pflegehelfer` postings would start passing
            # sinks.only_pflege(). A section that disappears from the stored document is a loud 422; a caller
            # that really means to empty one sends it with an empty value ("cv": {}, "excluded_role_classes":
            # []), which _apply() accepts, so nothing is walled off -- only silence is.
            errs.append("would drop section(s) " + ", ".join(dropped) + " that the stored document defines; "
                        "PUT replaces the whole document, so send every section (an empty value clears one)")
    try:
        C.validate(obj)
    except ValueError as e:
        errs.append(str(e))
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        # A section that is present but the wrong shape (employer as a list, role.rules as a string, an
        # entry without "re"). validate() indexes into the document, so it raises these rather than
        # ValueError -- a caller mistake either way, and a 422 rather than a 500.
        errs.append(f"patterns: {type(e).__name__}: {e}")
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
    return {"patterns": get_patterns(), "patterns_path": _patterns_path(), "scheduler": S.status(), "firecrawl": public_firecrawl(),
            "hunter": get_hunter(), "feature_flags": get_feature_flags(), "feature_flags_info": FEATURE_FLAGS_INFO,
            "feature_status_notes": FEATURE_STATUS_NOTES, "agent_key": public_agent_key()}


# --- feature flags: things that are real code paths but not production-ready. One place to see and toggle
# them, so "is X finished" has an answer other than reading the diff. Only entries in FEATURE_FLAGS_DEFAULT
# are actually togglable (save_feature_flags); FEATURE_STATUS_NOTES documents things this settings block
# cannot safely toggle itself (an env var, a daemon with its own switch, a paused cloud routine) so they are
# still visible in one place.
FEATURE_FLAGS_DEFAULT = {"stripe": False, "chats_dock": False}

FEATURE_FLAGS_INFO = {
    "stripe": {"label": "Stripe billing (pay-per-closed-posting)",
               "note": "Scaffold only: no live key, no customer-facing UI, never charged anyone. Off by default; "
                       "the checkout/webhook/usage endpoints 503 regardless of this flag until STRIPE_SECRET_KEY is "
                       "also set. Turn on only after testing in Stripe test mode."},
    "chats_dock": {"label": "Chat dock (Autopilot widget on / and /pro)",
                    "note": "The 'Chats' launcher pill and side panel on both job-board pages. Off by default -- "
                            "the recruiting funnel it talks to is a synthetic-data PoC, not a real candidate "
                            "channel yet. dock.js reads this from the public GET /api/flags and renders nothing "
                            "at all (no DOM, no polling) while it is off."},
}

FEATURE_STATUS_NOTES = [
    {"key": "autopilot", "label": "Autopilot console (/autopilot)",
     "note": "WIP, route disabled 2026-09-08 (503) -- recruiting-funnel proof of concept on synthetic, seeded "
             "data, no real WhatsApp/e-mail/Meta integration wired. Code and API untouched, needs more work "
             "before it's worth exposing; not a runtime toggle -- re-enable in app/main.py:autopilot_page."},
    {"key": "tailnet_login", "label": "Tailnet login for /pro",
     "note": "Removed 2026-09-10, together with the exe.dev proxy-header door. identity() has no tailnet branch "
             "any more and no code reads TAILNET_TRUST -- the service binds 0.0.0.0, so a source-IP check was a "
             "forge hole rather than a login. Sign in with POST /api/auth/login or a magic link (docs/auth.md)."},
    {"key": "hunter", "label": "Firecrawl hunter (24/7 agent runner)",
     "note": "Caused a credit overrun on 2026-09-08 (a daemon restart mid-run orphaned 3 jobs; fixed since). "
             "Toggle from the Clawl page's Auto-Modus switch, not here."},
    {"key": "judge_runner", "label": "Judge runner (scheduled bug-finder + MR proposer)",
     "note": "Two cloud routines exist (judge-find 06:00 UTC, judge-propose 14:00 UTC) but are paused. gh is not "
             "authenticated on this VM, so a proposal today is a pushed branch + e-mail, not a real pull request. "
             "Manage at claude.ai/code/routines."},
]


PUBLIC_FLAG_KEYS = ("chats_dock",)


def get_feature_flags():
    return {**FEATURE_FLAGS_DEFAULT, **(R.get_setting("feature_flags") or {})}


def public_feature_flags():
    flags = get_feature_flags()
    return {k: flags[k] for k in PUBLIC_FLAG_KEYS}


def save_feature_flags(obj):
    """Validated merge into the 'feature_flags' settings block; only known keys are accepted."""
    if not isinstance(obj, dict):
        raise ValueError("feature flags must be a JSON object")
    cur = get_feature_flags()
    for k in FEATURE_FLAGS_DEFAULT:
        if k in obj:
            cur[k] = bool(obj[k])
    R.set_setting("feature_flags", cur)
    return get_feature_flags()


def get_hunter():
    return {**HUNTER_DEFAULT, **(R.get_setting("hunter") or {})}


_HUNTER_INT = {"concurrency": (1, 10), "cap": (20, 500), "escalate_cap": (20, 500), "max_refills": (0, 100),
               "max_credits_per_hour": (0, 100000), "min_tokens": (0, 10 ** 6), "max_charge_per_run": (1, 1000), "max_tokens_per_run": (1, 10 ** 6)}


def save_hunter(obj):
    """Validated merge into the 'hunter' settings block; unknown keys are ignored, bad values raise ValueError."""
    if not isinstance(obj, dict):
        raise ValueError("hunter settings must be a JSON object")
    cur = get_hunter()
    for k, (lo, hi) in _HUNTER_INT.items():
        if k in obj:
            try:
                v = int(obj[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be an integer")
            if isinstance(obj[k], bool) or not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            cur[k] = v
    if "max_usd_per_posting" in obj:
        try:
            v = float(obj["max_usd_per_posting"])
        except (TypeError, ValueError):
            raise ValueError("max_usd_per_posting must be a number")
        if v < 0:
            raise ValueError("max_usd_per_posting must be >= 0")
        cur["max_usd_per_posting"] = v
    if "enabled" in obj:
        cur["enabled"] = bool(obj["enabled"])
    if cur["escalate_cap"] < cur["cap"]:
        raise ValueError("escalate_cap must be >= cap")
    R.set_setting("hunter", cur)
    return get_hunter()


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
    if "enabled" in obj:
        cur["enabled"] = bool(obj["enabled"])
    if "kill_switch_pct" in obj:
        pct = obj["kill_switch_pct"]
        if not (isinstance(pct, list) and len(pct) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pct)
                and pct[0] < pct[1] < pct[2]):
            raise ValueError("kill_switch_pct must be an ascending [warn, throttle, disable] triple, e.g. [10, 20, 30]")
        cur["kill_switch_pct"] = [float(x) for x in pct]
    R.set_setting("firecrawl", cur)
    return public_firecrawl()


# --- agent API keys: non-interactive doors, each with a label and a scope set. A scope opens exactly the
# routes app/auth.py's AGENT_ROUTES lists for it and nothing else; everything outside that table stays
# owner-session-only whatever the key carries. Only SHA-256 hashes are ever persisted (settings key
# "agent_keys", hash -> record); the plaintext exists nowhere after the one response that mints it.
# The function names are the ones callers already use, so the single-key shape of GET /api/settings and
# tools/mint_kindt_env.py keeps working. ------------------------------------------------------------------
def _hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def get_agent_keys():
    """{sha256: {label, scopes[], created_at, rotated_at}}. A pre-scope install still has the single
    settings.agent_key record; it is read as one full-scope key so an already-issued key keeps working."""
    keys = R.get_setting("agent_keys")
    if keys is not None:
        return keys
    from . import auth as AU
    old = R.get_setting("agent_key") or {}
    if not old.get("hash"):
        return {}
    return {old["hash"]: {"label": "legacy", "scopes": list(AU.SCOPES),
                          "created_at": old.get("created_at"), "rotated_at": old.get("rotated_at")}}


def public_agent_key():
    """Status only, safe for the owner-gated GET /api/settings -- never a key or a hash. created_at is the
    oldest key's, rotated_at the newest rotation, so the shape the pro page already reads still means what
    it did; `keys` is the per-label detail the scope model adds."""
    recs = sorted(get_agent_keys().values(), key=lambda r: r.get("created_at") or "")
    return {"configured": bool(recs),
            "created_at": recs[0].get("created_at") if recs else None,
            "rotated_at": max((r.get("rotated_at") or "" for r in recs), default="") or None,
            "keys": [{k: r.get(k) for k in ("label", "scopes", "created_at", "rotated_at")} for r in recs]}


def set_agent_key(rotate=False, label=None, scopes=None):
    """Mint a key, store only its hash, return the plaintext once.

    rotate=True revokes every existing key first (what the old single-key door did). Without it, a key
    carrying the same label is replaced and keys with other labels stay valid. scopes=None means every
    scope in auth.SCOPES -- the same reach the one old key had; pass a list to narrow it."""
    from . import auth as AU
    label = (label or "default").strip() or "default"
    scopes = list(AU.SCOPES) if scopes is None else [s.strip() for s in scopes if s and s.strip()]
    bad = [s for s in scopes if s not in AU.SCOPES]
    if bad:
        raise ValueError(f"unknown scope(s): {', '.join(bad)}; known: {', '.join(AU.SCOPES)}")
    cur = get_agent_keys()
    prev = [r for r in cur.values() if r.get("label") == label]
    keys = {} if rotate else {h: r for h, r in cur.items() if r.get("label") != label}
    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {"label": label, "scopes": scopes, "created_at": (prev[0].get("created_at") if prev else None) or now}
    if rotate or prev:
        record["rotated_at"] = now
    keys[_hash_key(raw)] = record
    R.set_setting("agent_keys", keys)
    return raw


def clear_agent_key(label=None):
    """No label: revoke every key. With one: revoke only that label's."""
    keys = {} if label is None else {h: r for h, r in get_agent_keys().items() if r.get("label") != label}
    R.set_setting("agent_keys", keys)


def check_agent_key(candidate):
    """The key's record ({label, scopes, ...}) or None. Only ever compares hashes."""
    if not candidate:
        return None
    given = _hash_key(candidate)
    for stored, record in get_agent_keys().items():
        if hmac.compare_digest(given, stored):
            return record
    return None
