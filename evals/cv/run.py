"""Synthetic CV eval runner: loads evals/cv/cases/*.json, runs each cv_text through app.cv.analyse
(the real POST /api/cv code path, in-process -- no server needed), checks the extracted profile and
match reasoning against `expected`, prints PASS/FAIL with why.

  python evals/cv/run.py               # every case
  python evals/cv/run.py intensiv      # cases whose id contains "intensiv"

Add a case: drop a new cases/<id>.json (see cases/README or any existing file for the shape).
Update expected results: edit the `expected` block of the case file directly, then re-run --
this file has no separate "golden" store, the case file IS the expectation.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import cv as CV  # noqa: E402

CASES_DIR = Path(__file__).parent / "cases"


def _check(expected, profile, matches):
    """Yields (ok: bool, message: str) per expected key -- subset checks only, so a case doesn't
    have to pin every field the extractor happens to produce."""
    for key in ("roles_any", "qualifications_all", "departments_any", "languages_all"):
        want = expected.get(key)
        if not want:
            continue
        field = key.split("_")[0]
        got = set(profile.get(field) or [])
        if key.endswith("_any"):
            ok = bool(got & set(want))
            yield ok, f"{key}: want one of {want}, got {sorted(got)}"
        else:
            ok = set(want) <= got
            yield ok, f"{key}: want all of {want}, got {sorted(got)}"
    if "experience_years" in expected:
        ok = profile.get("experience_years") == expected["experience_years"]
        yield ok, f"experience_years: want {expected['experience_years']}, got {profile.get('experience_years')}"
    if "cities_any" in expected:
        got = set(profile.get("cities") or [])
        ok = bool(got & set(expected["cities_any"]))
        yield ok, f"cities_any: want one of {expected['cities_any']}, got {sorted(got)}"
    if "min_matches" in expected:
        ok = len(matches) >= expected["min_matches"]
        yield ok, f"min_matches: want >= {expected['min_matches']}, got {len(matches)}"
    if "min_score" in expected and matches:
        ok = matches[0]["score"] >= expected["min_score"]
        yield ok, f"min_score (top match): want >= {expected['min_score']}, got {matches[0]['score']}"


def run_case(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    result = CV.analyse(text=case["cv_text"], limit=5)
    profile, matches = result["profile"], result["matches"]
    checks = list(_check(case.get("expected", {}), profile, matches))
    ok = all(c for c, _ in checks)
    print(f"{'PASS' if ok else 'FAIL'} {case['id']} -- {case.get('description', '')}")
    for c, msg in checks:
        print(f"  {'ok ' if c else 'BAD'} {msg}")
    if matches:
        print("  top matches (platform's own reasoning, `why`):")
        for m in matches[:3]:
            print(f"    #{m['posting_id']} score={m['score']} {m['title']!r} -- why: {', '.join(m['why'])}")
    else:
        print("  no matches against the current live posting snapshot")
    return ok


def main():
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    paths = sorted(p for p in CASES_DIR.glob("*.json") if filt in p.stem)
    if not paths:
        print(f"no cases matching {filt!r} in {CASES_DIR}")
        return 1
    results = [run_case(p) for p in paths]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
