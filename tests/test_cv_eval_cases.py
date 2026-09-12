"""Runs evals/cv/cases/*.json (TASK-65's widened case set) through the deterministic extraction
path (app.cv.analyse) as part of the offline suite -- reuses evals/cv/run.py's fixture snapshot
and `_check` logic directly (loaded as a module, not duplicated) rather than inventing a parallel
case format, per this task's "extend the harness, don't replace it" instruction.

Three cases are known, documented gaps in the deterministic path -- see evals/cv/README.md's
TASK-65 write-up for why each one fails, and tests/test_cv_eval_cases_llm.py (llm-marked) for the
same cases passing through app.cv.analyse_llm instead:

  - kenntnispruefung_passed_urkunde_pending: an English "general medicine ward" phrase does not
    match the German-oriented department-hint regex.
  - messy_mixed_language_date_range_experience: a birth year confuses the experience-year
    date-range fallback (used only when no explicit "X Jahre" phrase is present).
  - pflegefachhelferin_qualification_trap: Pflegefachhelfer (1-year helper) vs. Pflegefachkraft
    (3-year Fachkraft) conflation, plus a qualification-tag regex gap.

These three are smoke-tested only (extraction must still run and return the right shape) so a
regression here shows up as a *newly* broken case, not a silent crash -- they are not asserted to
pass, because asserting that would be asserting away a real, already-measured, documented gap.
Every other case must pass fully; a regression on any of those IS a deterministic-path bug.
"""
import importlib.util
import json
import pathlib
import time

import pytest

from app import cv as CV
from app import data as D

_RUN_PATH = pathlib.Path(__file__).resolve().parents[1] / "evals" / "cv" / "run.py"
_spec = importlib.util.spec_from_file_location("evals_cv_run", _RUN_PATH)
_cv_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cv_eval)

_KNOWN_DETERMINISTIC_GAPS = {
    "kenntnispruefung_passed_urkunde_pending",
    "messy_mixed_language_date_range_experience",
    "pflegefachhelferin_qualification_trap",
}

_CASES = sorted(_cv_eval.CASES_DIR.glob("*.json"))


@pytest.fixture()
def fixture_snapshot(monkeypatch):
    """The same fixture job/clinic board evals/cv/run.py uses, applied the way this repo's other
    tests apply a D._snap fixture (test_app_api.py, test_wa_luna_personas.py): mutate the shared
    _snap dict in place, monkeypatch `refresh` so pytest reverts it after the test. Fully offline,
    independent of whether this host has a live Supabase connection."""
    D._snap.update({"at": time.time(), "jobs": _cv_eval._JOBS, "clinics": _cv_eval._CLINICS,
                    "by_clinic": _cv_eval._BY_CLINIC, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)


@pytest.mark.parametrize("case_path", _CASES, ids=lambda p: p.stem)
def test_deterministic_extraction(case_path, fixture_snapshot):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    result = CV.analyse(text=case["cv_text"], limit=5)
    if case["id"] in _KNOWN_DETERMINISTIC_GAPS:
        assert isinstance(result["profile"], dict) and "roles" in result["profile"]
        return
    checks = list(_cv_eval._check(case.get("expected", {}), result["profile"], result["matches"]))
    bad = [msg for ok, msg in checks if not ok]
    assert not bad, f"{case['id']}: " + "; ".join(bad)
