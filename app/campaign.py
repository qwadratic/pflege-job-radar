"""State for the Firecrawl-only reingest campaign (2026-09-08 onward, see docs/coverage-plan.md).

This module holds no policy -- the actual reasoning (which clinics to re-verify next, whether the
trend justifies escalating from 'safe' to 'greedy', whether the plateau stop condition is met) is done
by the scheduled cloud routine each time it fires (a Claude agent reading /api/coverage, /api/billing,
/api/firecrawl/credits and journalctl, per docs/campaign.md). This module is just where it persists
what it decided, so the next tick (and a human) can see the trend instead of only the latest snapshot.
"""
from . import runs as R

DEFAULT = {
    "safety_level": "safe",           # 'safe' | 'greedy' -- routine escalates, never a human toggle in code
    "stopped": False,
    "stop_reason": None,
    "history": [],                    # newest last; each entry a routine-authored snapshot dict (see append)
}
MAX_HISTORY = 200


def get():
    return {**DEFAULT, **(R.get_setting("campaign") or {})}


def save(patch):
    """Validated merge: only known top-level keys, history entries are appended (not replaced) unless
    the caller passes history=[] explicitly to reset it."""
    if not isinstance(patch, dict):
        raise ValueError("campaign patch must be a JSON object")
    cur = get()
    if "safety_level" in patch:
        if patch["safety_level"] not in ("safe", "greedy"):
            raise ValueError("safety_level must be 'safe' or 'greedy'")
        cur["safety_level"] = patch["safety_level"]
    if "stopped" in patch:
        cur["stopped"] = bool(patch["stopped"])
    if "stop_reason" in patch:
        cur["stop_reason"] = (str(patch["stop_reason"])[:500] or None) if patch["stop_reason"] else None
    if "snapshot" in patch:
        if not isinstance(patch["snapshot"], dict):
            raise ValueError("snapshot must be a JSON object")
        entry = {"ts": R.now(), **patch["snapshot"]}
        cur["history"] = (cur.get("history") or []) + [entry]
        cur["history"] = cur["history"][-MAX_HISTORY:]
    R.set_setting("campaign", cur)
    return get()
