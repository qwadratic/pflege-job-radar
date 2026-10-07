"""Smoke test for evals/wa_brain/run.py: proves the harness runs end to end OFFLINE and nothing more.
A fake sales brain, the brain's own ``LB.Client(reply=fn)`` seam instead of the CLI, a fake judge callable.
The eval itself is manual (evals/wa_brain/README.md) and is never collected: no test_*.py under evals/."""
import importlib.util
import json
import pathlib
import shutil

import pytest

from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa.luna import replay as RP
from tests.conftest import _LIVE_CREDENTIALS
from tests.test_wa_replay import _make_sales_brain, _row

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("wa_brain_eval_run", ROOT / "evals" / "wa_brain" / "run.py")
RUN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(RUN)


def test_eval_harness_runs_end_to_end_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(RP, "_git_sha", lambda repo_dir=None: "0123456789abcdef0123456789abcdef01234567")
    sqlite_before, sessions_before = C.SQLITE_PATH, C.LUNA_SESSION_DIR
    assert set(RUN.LIVE_CREDENTIALS) == set(_LIVE_CREDENTIALS)

    sb = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9001, "inbound", "text", "2030-09-01T10:00:00+00:00", body="Hallo, ich suche eine Stelle. Tel +49 170 1234567"),
        _row(2, 9001, "outbound", "text", "2030-09-01T10:00:05+00:00", body="Guten Tag, in welcher Region?"),
        _row(3, 9001, "inbound", "text", "2030-09-01T10:00:10+00:00", body="Bayern"),
        _row(4, 9001, "outbound", "text", "2030-09-01T10:00:15+00:00", body="Haben Sie die Urkunde?"),
        _row(5, 9001, "inbound", "text", "2030-09-01T10:00:20+00:00", body="Ja"),
        _row(6, 9001, "outbound", "text", "2030-09-01T10:00:25+00:00", body="Danke."),
    ])

    # --list-turns: the replay's own numbering, phone-like numbers scrubbed, no model call
    turns = RUN.list_turns(9001, sb)
    assert [t["turn"] for t in turns] == [1, 2, 3]
    assert "<num>" in turns[0]["inbound"] and "1234567" not in turns[0]["inbound"]

    # a run: the case file is copied out of the repo, as a real one lives there
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    shutil.copy(ROOT / "evals" / "wa_brain" / "example_case.json", cases_dir / "example.json")
    seen_payloads = []

    def fake_reply(system, user, session_id):
        seen_payloads.append(json.loads(user))
        return ({"action": "reply", "bubbles": ["Verstanden, danke."], "escalate_to_manager": False,
                 "no_send": False, "card_patch": {}}, "fake-session")

    out = tmp_path / "out"
    totals = RUN.run_cases(RUN.load_cases(cases_dir), out, 2, sb, make_client=lambda: LB.Client(reply=fake_reply))
    assert totals == {"records": 4, "errors": 0, "failed_cases": 0}                    # turns 2 and 3, two runs
    assert len(seen_payloads) == 4                                                     # turn 1 was plain history
    assert [m["text"] for m in seen_payloads[0]["outbound_since_last_turn"]] == ["Guten Tag, in welcher Region?"]
    records = [json.loads(l) for l in (out / "example.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {(r["turn"], r["run"]) for r in records} == {(2, 1), (2, 2), (3, 1), (3, 2)}
    assert records[0]["bubbles"] == ["Verstanden, danke."] and records[0]["git_sha"].startswith("0123")
    points = json.loads((out / "example.points.json").read_text(encoding="utf-8"))["points"]
    assert points[1]["old_reply"] == ["Danke."] and points[1]["inbound"] == ["Ja"]
    assert [h["from"] for h in points[1]["history"]] == ["candidate", "assistant", "candidate", "assistant"]
    assert (C.SQLITE_PATH, C.LUNA_SESSION_DIR) == (sqlite_before, sessions_before)    # nothing left pointed at scratch

    # the judge: a fake callable, labels shuffled and unlabelled, unsealed by code afterwards
    judge_payloads = []

    def fake_cli(system, payload):
        body = json.loads(payload)
        judge_payloads.append(payload)
        if "replies" not in body:
            return json.dumps({"better": ["x"], "worse": [], "look_first": ["y"], "summary": "ok"})
        scores = [{"label": r["label"], "verdict": "pass" if "Verstanden" in r["bubbles"][0] else "fail",
                   "reason": "fine"} for r in body["replies"]]
        return json.dumps({"analysis": "a", "scores": scores, "variance": "v"})

    results = RUN.judge_results(out, fake_cli)
    assert len(judge_payloads) == 3 and all("old" not in p and "run1" not in p for p in judge_payloads[:2])   # 2 points + final
    assert [p["comparison"] for p in results["points"]] == ["better", "better"]        # old reply failed, new runs pass
    assert (out / "results.json").exists() and (out / "judge_key.json").exists()
    assert "OVERALL: ok" in RUN.format_report(results)

    # an --out / --cases / --judge directory inside a checkout is refused before anything happens
    for flags in (["--cases", str(ROOT / "evals"), "--out", str(tmp_path / "o2")],
                  ["--cases", str(cases_dir), "--out", str(ROOT / "evals" / "wa_brain" / "out")],
                  ["--judge", str(ROOT / "evals" / "wa_brain")]):
        with pytest.raises(SystemExit, match="inside a git checkout"):
            RUN.main(flags)
