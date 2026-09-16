"""Synthetic CV eval runner: loads evals/cv/cases/*.json and runs each cv_text through one of two
extraction paths -- app.cv.analyse (deterministic regex, the real POST /api/cv code path) or
app.cv.analyse_llm (the `claude` CLI reasoning path, TASK-65) -- then checks the extracted profile
and match reasoning against `expected`, printing PASS/FAIL with why.

  python evals/cv/run.py                      # every case, deterministic path (default)
  python evals/cv/run.py intensiv             # cases whose id contains "intensiv"
  python evals/cv/run.py --path=llm           # every case through the real `claude` CLI --
                                               # costs real money/time, see app/cv.py:analyse_llm
  python evals/cv/run.py --path=llm intensiv  # a path flag and a filter can both be given
  CV_EVAL_PATH=llm python evals/cv/run.py     # env-var form of --path, same precedence as
                                               # WA_BRAIN/WA_LUNA_* elsewhere in this repo

Add a case: drop a new cases/<id>.json (see any existing file for the shape).
Update expected results: edit the `expected` block of the case file directly, then re-run -- this
file has no separate "golden" store, the case file IS the expectation.

Runs fully offline regardless of path: a small fixture job/clinic snapshot (real Bavarian
town/Regierungsbezirk pairs, invented clinics/postings, one town per Regierungsbezirk) is loaded
into app.data's in-process cache before any case runs, so profile_from_text's city/Regierungsbezirk
lookup and match()'s scoring have real data to work against regardless of whether this host has a
live Supabase connection (SUPABASE_ANON_KEY is not always set -- see TASK-23). Without this, cities/
matches assertions would silently measure network reachability instead of extraction quality; the
`--path=llm` run still spawns the real `claude` CLI subprocess (that part is never mocked), only the
job-board data it matches against is a fixture.

--- TASK-65 result (2026-09-12) -- see evals/cv/README.md for the full write-up ------------------
Ran both paths over the 12-case set below. Deterministic (one run -- it is deterministic by
construction): 9/12. LLM (sampled across several runs): 11-12/12, but see the non-determinism note
below. Verdict: lean LLM for profile EXTRACTION -- it won every case that hit a real domain trap
this task set out to find (Pflegefachhelfer's 1-year-helper-vs-3-year-Fachkraft conflation, a birth
year confusing the deterministic experience-year date-range fallback, "general medicine ward" not
matching the German-oriented department regex, "Eng" as an abbreviation for Englisch) while tying
the deterministic path on every other clean, unambiguous CV. `match()` (job-matching scoring) is
unchanged either way: both paths hand its output shape unchanged to the same function, so this
result is about profile EXTRACTION only, per this task's scope -- not a claim about matching
quality.

Not a clean sweep, and not fully stable: defizitbescheid_received_anerkennungspfad.json (a
Defizitbescheid-already-received candidate, placeable per qualification_knowledge.json) is
genuinely borderline for the LLM -- of 3 sampled runs of the IDENTICAL prompt, one got the
pflegefachkraft role and GuK qualification right, one omitted GuK, one omitted both. The
deterministic regex gets this case right on every run (it always will -- same input, same regex,
same output), via the literal "Registered Nurse"/"Anerkennungsverfahren" tokens. This is the same
real, previously-confirmed CLI non-determinism this repo's own planning doc already flagged
elsewhere (same input, two fresh sessions, two different phrasings, same decision) -- here it is
reproduced on a structured-extraction task, not just conversational phrasing, and it can flip a
field, not just the wording around it. Practical takeaway for TASK-67 (CV/Urkunde intake, which
consumes this result): analyse_llm is the better default for reading free text, but a borderline
recognition-path candidate is exactly the case worth a human's second look either way, regardless
of which path produced the profile.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import cv as CV  # noqa: E402
from app import data as D  # noqa: E402

CASES_DIR = Path(__file__).parent / "cases"

# --- offline fixture snapshot -----------------------------------------------------------------
# One clinic/town per Bavarian Regierungsbezirk (real public geography, not synthetic) plus a
# small invented board of postings spanning the role/department variety the case set exercises.
_CLINICS = [
    {"clinic_id": "F1", "name": "Klinikum Nordbayern (fixture)", "town": "München", "regierungsbezirk": "Oberbayern"},
    {"clinic_id": "F2", "name": "Klinikum Isartor (fixture)", "town": "Landshut", "regierungsbezirk": "Niederbayern"},
    {"clinic_id": "F3", "name": "Klinikum Regenbogen (fixture)", "town": "Regensburg", "regierungsbezirk": "Oberpfalz"},
    {"clinic_id": "F4", "name": "Klinikum Fichtelberg (fixture)", "town": "Bayreuth", "regierungsbezirk": "Oberfranken"},
    {"clinic_id": "F5", "name": "Klinikum Pegnitztal (fixture)", "town": "Nürnberg", "regierungsbezirk": "Mittelfranken"},
    {"clinic_id": "F6", "name": "Klinikum Mainfranken (fixture)", "town": "Würzburg", "regierungsbezirk": "Unterfranken"},
    {"clinic_id": "F7", "name": "Klinikum Fuggerstadt (fixture)", "town": "Augsburg", "regierungsbezirk": "Schwaben"},
]
_BY_CLINIC = {c["clinic_id"]: c for c in _CLINICS}


def _job(posting_id, clinic_id, role_class, department_hint, title):
    c = _BY_CLINIC[clinic_id]
    return {"posting_id": posting_id, "title": title, "role_class": role_class,
            "department_hint": department_hint, "department_raw": department_hint,
            "city": c["town"], "clinic_town": c["town"], "regierungsbezirk": c["regierungsbezirk"],
            "clinic_id": clinic_id, "clinic_name": c["name"], "employer": c["name"],
            "employment_types": ["vollzeit"], "verify_status": "live", "status": "open",
            "first_published": "2026-09-01", "fresh": True,
            "source_url": f"https://example.org/job/{posting_id}"}


_JOBS = [
    _job(1, "F1", "pflegefachkraft", "Intensiv/IMC", "Pflegefachkraft Intensivstation"),
    _job(2, "F1", "fachpflege", "Anästhesie", "Fachpflegekraft Anästhesie/Aufwachraum"),
    _job(3, "F5", "pflegefachkraft", "Innere Medizin", "Pflegefachkraft Innere Medizin"),
    _job(4, "F7", "pflegefachkraft", "Intensiv/IMC", "Pflegefachkraft Intensivstation"),
    _job(5, "F7", "pflegefachkraft", "Innere Medizin", "Pflegefachkraft Innere Medizin"),
    _job(6, "F2", "pflegefachkraft", "Innere Medizin", "Pflegefachkraft Innere Medizin"),
    _job(7, "F3", "ota_ata", "OP", "OTA im Zentral-OP"),
    _job(8, "F3", "ota_ata", "Anästhesie", "ATA Anästhesie/Aufwachraum"),
    _job(9, "F6", "hebamme", "Geburtshilfe", "Hebamme Kreißsaal"),
    _job(10, "F6", "pflegefachkraft", "Intensiv/IMC", "Pflegefachkraft Intensivstation"),
    _job(11, "F4", "leitung", "Chirurgie/Orthopädie", "Stationsleitung Chirurgie"),
]


def _load_fixture_snapshot():
    """Replace app.data's in-process cache with the fixture above, in place of a live Supabase
    read. D.snapshot()/match() read D._snap directly -- the same seam
    tests/test_wa_luna_personas.py and tests/test_app_api.py monkeypatch per-test; here it is a
    plain reassignment for the life of this process (this script has no test framework to restore
    it afterwards, and every case in one run must see the same fixture)."""
    D._snap.update({"at": time.time(), "jobs": _JOBS, "clinics": _CLINICS, "by_clinic": _BY_CLINIC,
                    "facets": {}, "taxonomy": {}, "loading": False, "error": None})
    D.refresh = lambda: D._snap


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
    if "roles_none" in expected:
        got = set(profile.get("roles") or [])
        bad = got & set(expected["roles_none"])
        yield not bad, f"roles_none: must not include any of {expected['roles_none']}, got {sorted(got)}"
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


def _deterministic(text):
    return CV.analyse(text=text, limit=5)


def _llm(text):
    return CV.analyse_llm(text=text, limit=5)


PATHS = {"deterministic": _deterministic, "llm": _llm}


def run_case(path, extractor):
    case = json.loads(path.read_text(encoding="utf-8"))
    try:
        result = extractor(case["cv_text"])
    except Exception as exc:  # noqa: BLE001 -- reported loudly below, not swallowed
        print(f"ERROR {case['id']} -- {case.get('description', '')}")
        print(f"  extraction raised: {type(exc).__name__}: {exc}")
        return False
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
        print("  no matches against the fixture posting board")
    return ok


def main():
    args = list(sys.argv[1:])
    path_flag = None
    rest = []
    for a in args:
        if a.startswith("--path="):
            path_flag = a.split("=", 1)[1].strip().lower()
        else:
            rest.append(a)
    path_name = path_flag or os.environ.get("CV_EVAL_PATH", "deterministic").strip().lower()
    if path_name not in PATHS:
        print(f"unknown path {path_name!r} -- expected one of {sorted(PATHS)} (--path=<name> or CV_EVAL_PATH=<name>)")
        return 2
    filt = rest[0] if rest else ""
    paths = sorted(p for p in CASES_DIR.glob("*.json") if filt in p.stem)
    if not paths:
        print(f"no cases matching {filt!r} in {CASES_DIR}")
        return 1
    _load_fixture_snapshot()
    print(f"path={path_name}\n")
    results = [run_case(p, PATHS[path_name]) for p in paths]
    print(f"\n{sum(results)}/{len(results)} passed ({path_name})")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
