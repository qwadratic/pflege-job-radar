#!/usr/bin/env python3
"""Fold a pytest --json-report of tests/test_adapter_completeness.py into feature-matrix cells.

    .venv/bin/python -m pytest tests/test_adapter_completeness.py -m completeness \
        --json-report --json-report-file=/tmp/completeness.json
    .venv/bin/python tools/cells_from_pytest.py /tmp/completeness.json >> data/feature_cells.jsonl

One JSON object per line, one line per cell, append-only: for a (subject, feature_id) pair the
last line in the file wins. The row vocabulary, the verdict enum and the truncated rule are
defined in docs/feature-matrix.md -- this script only implements them, it decides nothing.

Self-check, needs no report and no network:

    .venv/bin/python tools/cells_from_pytest.py --selfcheck
"""
import json
import re
import sys
from datetime import datetime, timezone

# The frozen row vocabulary: one feature per live check in tests/test_adapter_completeness.py:265-300.
FEATURES = ("read_path_coverage", "declared_total_parity", "field_completeness", "public_url", "round_trip")

# A run that stopped on a budget or cap gate never got to observe the feature, whether it stopped by
# failing or by skipping. That is recorded as a stop, never as a filled cell (docs/feature-matrix.md).
TRUNCATED_RE = re.compile(r"budget (?:too thin|exhausted)|weekly budget|credits? exhausted|AgentFailed|cap below", re.I)

_NODE_RE = re.compile(r"::test_(\w+)\[(.+)\]$")


def _message(test):
    """The shortest decisive line pytest kept for this test: the crash message, else the longrepr."""
    for name in ("call", "setup", "teardown"):
        ph = test.get(name) or {}
        if ph.get("outcome") in ("failed", "error", "skipped"):
            crash = ph.get("crash") or {}
            text = crash.get("message") or ph.get("longrepr") or ""
            text = " ".join(str(text).split())
            if text:
                return text
    return ""


def verdict_for(outcome, message):
    """(verdict, stop). unknown = looked, could not tell. not_checked = nobody looked (or was cut off)."""
    if TRUNCATED_RE.search(message or ""):
        return "not_checked", "truncated"
    if outcome == "passed":
        return "supported", None
    if outcome == "failed":
        return "absent", None
    if outcome == "error":                     # the shared board fetch blew up: looked, cannot tell
        return "unknown", None
    return "not_checked", None                 # skipped, xfailed, xpassed, anything pytest grows later


def cell(test, checked_at):
    """One cell dict, or None when the node is not one of the five completeness checks."""
    m = _NODE_RE.search(test.get("nodeid") or "")
    if not m or m.group(1) not in FEATURES:
        return None
    feature_id, subject = m.group(1), m.group(2)
    message = _message(test)
    verdict, stop = verdict_for(test.get("outcome"), message)
    out = {"subject": subject,
           # the pytest param id is "<ats_type>__<board host>", so the adapter is its first half
           "adapter": subject.split("__")[0],
           "feature_id": feature_id,
           "verdict": verdict,
           "evidence": [{"path": test["nodeid"], "line_or_quote": message or test.get("outcome") or "",
                         "fetched_at": checked_at}],
           "method": "pytest:test_adapter_completeness",
           "confidence": "high" if verdict in ("supported", "absent") else "low",
           "checked_by": "tools/cells_from_pytest.py",
           "checked_at": checked_at}
    if stop:
        out["stop"] = stop
    return out


def cells(report):
    at = datetime.fromtimestamp(report["created"], timezone.utc).isoformat(timespec="seconds")
    return [c for c in (cell(t, at) for t in report["tests"]) if c]


def selfcheck():
    report = {"created": 1757462400.0, "tests": [
        {"nodeid": "tests/test_adapter_completeness.py::test_public_url[rexx__kbo.de]", "outcome": "passed",
         "call": {"outcome": "passed"}},
        {"nodeid": "tests/test_adapter_completeness.py::test_round_trip[rexx__kbo.de]", "outcome": "failed",
         "call": {"outcome": "failed", "crash": {"message": "AssertionError: 2/5 sampled urls failed round trip"}}},
        {"nodeid": "tests/test_adapter_completeness.py::test_field_completeness[dvinci__x.de]", "outcome": "error",
         "setup": {"outcome": "failed", "longrepr": "ConnectionError:\n  read timed out"}},
        {"nodeid": "tests/test_adapter_completeness.py::test_declared_total_parity[dvinci__x.de]", "outcome": "skipped",
         "setup": {"outcome": "skipped", "longrepr": ["x.py", 9, "Skipped: budget too thin for a viable attempt"]}},
        {"nodeid": "tests/test_adapter_completeness.py::test_mutation_cap_first_page[rexx]", "outcome": "passed"},
    ]}
    got = {(c["subject"], c["feature_id"]): c for c in cells(report)}
    assert len(got) == 4, got                                          # the mutation meta-test is not a cell
    assert got[("rexx__kbo.de", "public_url")]["verdict"] == "supported"
    assert got[("rexx__kbo.de", "round_trip")]["verdict"] == "absent"
    assert got[("rexx__kbo.de", "round_trip")]["adapter"] == "rexx"
    assert "round trip" in got[("rexx__kbo.de", "round_trip")]["evidence"][0]["line_or_quote"]
    assert got[("dvinci__x.de", "field_completeness")]["verdict"] == "unknown"       # looked, could not tell
    assert "stop" not in got[("dvinci__x.de", "field_completeness")]
    budget = got[("dvinci__x.de", "declared_total_parity")]
    assert budget["verdict"] == "not_checked" and budget["stop"] == "truncated"      # never a filled cell
    assert all(c["checked_at"] == "2025-09-10T00:00:00+00:00" for c in got.values())
    print("selfcheck ok:", len(got), "cells")


def main(argv):
    if "--selfcheck" in argv:
        return selfcheck()
    if len(argv) != 2:
        sys.exit(__doc__)
    with open(argv[1], encoding="utf-8") as fh:
        report = json.load(fh)
    for c in cells(report):
        print(json.dumps(c, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
