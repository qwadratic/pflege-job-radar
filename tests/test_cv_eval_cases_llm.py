"""LLM-driven CV extraction (app.cv.analyse_llm, TASK-65) against the widened evals/cv/cases/*.json
set, through the real `claude` CLI -- costs real money and takes several seconds per case.
`llm`-marked: excluded from the default run (``-m "not llm"``), same convention as
tests/test_wa_luna_personas.py. Skipped automatically if the `claude` CLI is not on PATH.

Run explicitly: ``pytest -q -m llm tests/test_cv_eval_cases_llm.py``

See evals/cv/README.md for the full measured comparison (deterministic 9/12, LLM 11-12/12 across
sampled runs). This test asserts a pass-rate FLOOR, not 12/12: one case
(defizitbescheid_received_anerkennungspfad) was directly observed to flip pass/fail across repeated
runs of the IDENTICAL prompt (real, already-documented CLI non-determinism -- see the README) -- a
hard 100% assertion here would be asserting away a real, measured finding, not testing anything.
"""
import importlib.util
import json
import pathlib
import shutil
import time

import pytest

from app import cv as CV
from app import data as D

_RUN_PATH = pathlib.Path(__file__).resolve().parents[1] / "evals" / "cv" / "run.py"
_spec = importlib.util.spec_from_file_location("evals_cv_run_llm", _RUN_PATH)
_cv_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cv_eval)

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not shutil.which("claude"), reason="'claude' is not on PATH -- CV.analyse_llm needs the Claude Code CLI installed and authenticated"),
]

# Documented, measured exception -- see the module docstring and evals/cv/README.md.
_KNOWN_BORDERLINE = {"defizitbescheid_received_anerkennungspfad"}
_PASS_RATE_FLOOR = 10  # out of len(_CASES) == 12 at the time this floor was set


@pytest.fixture()
def fixture_snapshot(monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _cv_eval._JOBS, "clinics": _cv_eval._CLINICS,
                    "by_clinic": _cv_eval._BY_CLINIC, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)


def test_llm_extraction_pass_rate_floor(fixture_snapshot):
    cases = sorted(_cv_eval.CASES_DIR.glob("*.json"))
    results = []
    for case_path in cases:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        result = CV.analyse_llm(text=case["cv_text"], limit=5)
        checks = list(_cv_eval._check(case.get("expected", {}), result["profile"], result["matches"]))
        bad = [msg for ok, msg in checks if not ok]
        results.append((case["id"], not bad, bad))
    n_pass = sum(1 for _, ok, _ in results if ok)
    failures = {cid: bad for cid, ok, bad in results if not ok}
    unexpected_failures = {cid: bad for cid, bad in failures.items() if cid not in _KNOWN_BORDERLINE}
    assert n_pass >= _PASS_RATE_FLOOR, (
        f"LLM path pass rate floor not met: {n_pass}/{len(cases)} passed (floor {_PASS_RATE_FLOOR}); "
        f"failures: {failures}"
    )
    assert not unexpected_failures, f"LLM path failed a case with no documented history of flakiness: {unexpected_failures}"
