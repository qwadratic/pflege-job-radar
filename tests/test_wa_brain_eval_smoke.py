"""Smoke test for evals/wa_brain/run.py: proves the harness runs end to end OFFLINE and nothing more.
A fake sales brain, the brain's own ``LB.Client(reply=fn)`` seam instead of the CLI, a fake judge callable.
The eval itself is manual (evals/wa_brain/README.md) and is never collected: no test_*.py under evals/."""
import importlib.util
import json
import os
import pathlib
import shutil
import stat

import pytest

from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa.luna import replay as RP
from tests.conftest import _LIVE_CREDENTIALS
from tests.test_wa_replay import CV_BYTES, _FakeReaders, _make_sales_brain, _media_root, _row, _sha

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("wa_brain_eval_run", ROOT / "evals" / "wa_brain" / "run.py")
RUN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(RUN)


def test_eval_harness_runs_end_to_end_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(RP, "_git_sha", lambda repo_dir=None: "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("WA_LUNA_NO_SEND", "0")                 # main() sets it to 1 for the process: restored after
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
    cases = RUN.load_cases(cases_dir)
    prep_dir = cases_dir / "prep"
    media = tmp_path / "media"
    media.mkdir()

    # the case points after turn 1: a run without its preparation stops before any model call, naming the command
    with pytest.raises(SystemExit, match=r"no prep file.*--prepare --cases .*cases --sales-brain .*sb.sqlite --out .*out"):
        RUN.run_cases(cases, out, 2, sb, make_client=lambda: LB.Client(reply=fake_reply), prep_dir=prep_dir)
    assert seen_payloads == []

    # --prepare, once: the real brain (fake client) on the earlier turns, the card stored as the case's prep file
    assert RUN.main(["--prepare", "--cases", str(cases_dir), "--out", str(out), "--sales-brain", sb,
                     "--media-root", str(media)], make_client=lambda: LB.Client(reply=fake_reply),
                    prepared_at="2030-09-02T08:00:00+00:00") == 0
    assert len(seen_payloads) == 2                                                    # turns 1 and 2; turn 3 is only captured
    prep_file = prep_dir / "example.json"
    assert stat.S_IMODE(os.stat(prep_file).st_mode) == 0o600 and stat.S_IMODE(os.stat(prep_dir).st_mode) == 0o700
    prep = json.loads(prep_file.read_text(encoding="utf-8"))
    assert (prep["case"], prep["candidate_id"], prep["at_turns"], prep["prepared_at"]) == (
        "example", 9001, [2, 3], "2030-09-02T08:00:00+00:00")
    assert prep["git_sha"].startswith("0123") and prep["model"] == C.LUNA_MODEL and prep["effort"] == C.LUNA_EFFORT
    assert prep["locked_templates"] == C.LUNA_LOCKED_TEMPLATES
    assert sorted(prep["points"]) == ["2", "3"] and prep["files"] == []
    assert prep["points"]["3"]["slots"]["_session_id"] == "fake-session"
    assert prep["points"]["2"]["asked"] == [] and prep["points"]["3"]["documents"] == []

    # the run reuses it: no model call for turn 1, the seed is the card of turns 2 and 3
    seen_payloads.clear()
    totals = RUN.run_cases(cases, out, 2, sb, make_client=lambda: LB.Client(reply=fake_reply), prep_dir=prep_dir)
    assert totals == {"records": 4, "errors": 0, "failed_cases": 0}                    # turns 2 and 3, two runs
    assert len(seen_payloads) == 4                                                     # turn 1 was plain history
    assert [m["text"] for m in seen_payloads[0]["outbound_since_last_turn"]] == ["Guten Tag, in welcher Region?"]
    # the prepared point sees the tail a live thread would hold: the earlier real messages, the old bot's replies
    # among them, never the inbound it answers
    assert [(m["direction"], m["text"]) for m in seen_payloads[0]["recent_messages"]][1:] == [
        ("out", "Guten Tag, in welcher Region?")]
    assert [(m["direction"], m["text"]) for m in seen_payloads[1]["recent_messages"]][1:] == [
        ("out", "Guten Tag, in welcher Region?"), ("in", "Bayern"), ("out", "Haben Sie die Urkunde?")]
    records = [json.loads(l) for l in (out / "example.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {(r["turn"], r["run"]) for r in records} == {(2, 1), (2, 2), (3, 1), (3, 2)}
    assert records[0]["bubbles"] == ["Verstanden, danke."] and records[0]["git_sha"].startswith("0123")
    assert {r["locked_templates"] for r in records} == {C.LUNA_LOCKED_TEMPLATES}
    points_file = json.loads((out / "example.points.json").read_text(encoding="utf-8"))
    points = points_file["points"]
    assert points[1]["old_reply"] == ["Danke."] and points[1]["inbound"] == ["Ja"]
    assert [h["from"] for h in points[1]["history"]] == ["candidate", "assistant", "candidate", "assistant"]
    assert points_file["edits"] == [{"what": "city named by the candidate", "from": "Bremen", "to": "Augsburg"}]
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
    report = RUN.format_report(results)
    assert "OVERALL: ok" in report and "example: city named by the candidate: Bremen -> Augsburg" in report
    assert not any("Bremen" in p for p in judge_payloads)                              # an echo, never sent to the judge
    assert all("inbound_files" not in p for p in judge_payloads)                       # no file in this conversation

    # --single: ONE blind call for every point and the overall comment; old/new tally still computed by code
    single_payloads = []

    def fake_single(system, payload):
        single_payloads.append(payload)
        body = json.loads(payload)
        pts = [{"id": pt["id"], "analysis": "a", "variance": "v",
                "scores": [{"label": r["label"], "verdict": "pass" if "Verstanden" in r["bubbles"][0] else "fail",
                            "reason": "fine"} for r in pt["replies"]]} for pt in body["points"]]
        return json.dumps({"points": pts, "overall": {"better": ["x"], "worse": [], "look_first": ["y"], "summary": "one call"}})

    single = RUN.judge_single(out, fake_single)
    assert len(single_payloads) == 1 and "old" not in single_payloads[0] and "run1" not in single_payloads[0]
    assert [p["comparison"] for p in single["points"]] == ["better", "better"]
    assert (out / "results_single.json").exists() and (out / "judge_key_single.json").exists()
    assert "OVERALL: one call" in RUN.format_report(single)

    # an --out / --cases / --judge directory inside a checkout is refused before anything happens, --prepare too
    for flags in (["--cases", str(ROOT / "evals"), "--out", str(tmp_path / "o2")],
                  ["--cases", str(cases_dir), "--out", str(ROOT / "evals" / "wa_brain" / "out")],
                  ["--judge", str(ROOT / "evals" / "wa_brain")],
                  ["--prepare", "--cases", str(ROOT / "evals"), "--out", str(tmp_path / "o2"), "--sales-brain", sb],
                  ["--prepare", "--cases", str(cases_dir), "--out", str(ROOT / "evals" / "wa_brain" / "out"),
                   "--sales-brain", sb]):
        with pytest.raises(SystemExit, match="inside a git checkout"):
            RUN.main(flags)
    for flags in (["--prepare", "--judge", str(out)], ["--prepare", "--cases", str(cases_dir), "--out", str(out)]):
        with pytest.raises(SystemExit):                                                # argparse: wrong combination
            RUN.main(flags)


def test_prepared_files_reach_the_run_and_the_judge_sees_a_neutral_files_line(tmp_path, monkeypatch):
    monkeypatch.setattr(RP, "_git_sha", lambda repo_dir=None: "0123456789abcdef0123456789abcdef01234567")
    readers = _FakeReaders(monkeypatch)
    media = _media_root(tmp_path, {"a/cv.txt": CV_BYTES})
    sb = _make_sales_brain(tmp_path / "sb.sqlite", [
        # candidate 9101: the point is the turn with a readable file
        _row(1, 9101, "inbound", "text", "2030-09-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9101, "outbound", "text", "2030-09-01T10:00:05+00:00", body="Guten Tag"),
        _row(3, 9101, "inbound", "document", "2030-09-01T10:01:00+00:00", media_filename="cv.txt", attachment_id=1),
        _row(4, 9101, "outbound", "text", "2030-09-01T10:01:05+00:00", body="Danke fürs CV"),
        # candidate 9102: first-turn point whose file nobody can read: no preparation needed
        _row(5, 9102, "inbound", "document", "2030-09-01T10:00:00+00:00", media_filename="x.pdf", attachment_id=2),
        _row(6, 9102, "outbound", "text", "2030-09-01T10:00:05+00:00", body="Danke"),
    ], [{"id": 1, "storage_path": "a/cv.txt", "sha256": _sha(CV_BYTES), "mime_type": "text/plain"},
        {"id": 2, "storage_path": "gone/x.pdf", "sha256": "ab" * 32, "mime_type": "application/pdf"}])
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    for cid, cand, turns in (("with_file", 9101, [2]), ("lost_file", 9102, [1])):
        (cases_dir / f"{cid}.json").write_text(json.dumps({
            "id": cid, "candidate_id": cand, "at_turns": turns, "covers": {"archetype": "engaged_with_documents",
                                                                           "scenario": "sends_file"}, "region": "bavaria"}))
    texts = []

    def fake_reply(system, user, session_id):
        texts.append(json.loads(user)["latest_inbound"])
        return ({"action": "reply", "bubbles": ["Verstanden, danke."], "escalate_to_manager": False,
                 "no_send": False, "card_patch": {}}, "fake-session")

    def client():
        return LB.Client(reply=fake_reply)

    cases = RUN.load_cases(cases_dir)
    out = tmp_path / "out"
    logs = []
    # a point whose own turn holds a readable file needs the preparation too, even at turn 1
    unprepared = [c for c in cases if c["id"] == "with_file"]
    with pytest.raises(SystemExit, match="with_file.*--prepare"):
        RUN.run_cases(unprepared, out, 1, sb, make_client=client, prep_dir=cases_dir / "prep", media_roots=[media])

    totals = RUN.prepare_cases(cases, out, cases_dir / "prep", sb, client, "2030-09-02T08:00:00+00:00", [media],
                               log=logs.append)
    assert totals == {"prepared": 2, "errors": 0, "failed_cases": 0}
    assert texts == ["Hallo"]                                                                # 9101's turn 1; 9102 is only captured
    assert readers.classified == [CV_BYTES.decode("utf-8")]                                  # the one real, readable file
    prep = json.loads((cases_dir / "prep" / "with_file.json").read_text(encoding="utf-8"))
    assert prep["points"]["2"]["files"][0]["status"] == "attached"
    assert prep["points"]["2"]["documents"][0]["document_type"] == "lebenslauf"
    assert (cases_dir / "prep" / "lost_file.json").exists()                                  # a turn-1 point may be prepared too
    lost = json.loads((cases_dir / "prep" / "lost_file.json").read_text(encoding="utf-8"))
    assert lost["points"]["1"]["files"][0]["reason"] == "not_on_a_readable_root"
    assert [l for l in logs if "with_file" in l] == ["with_file: prepared turns [2] (walk of 2 turn(s); files: 1 attached, 0 unavailable)"]

    # the run: no model call and no file read for anything earlier, the brain gets the live media turn text
    texts.clear()
    classified = len(readers.classified)
    totals = RUN.run_cases(cases, out, 2, sb, make_client=client, prep_dir=cases_dir / "prep", media_roots=[media])
    assert totals == {"records": 4, "errors": 0, "failed_cases": 0}
    assert texts == ["[document].pdf"] * 2 + [""] * 2     # unreadable file: today's placeholder; attached: the live empty text
    assert len(readers.classified) == classified
    unavailable = json.loads((out / "lost_file.points.json").read_text(encoding="utf-8"))["points"][0]
    attached = json.loads((out / "with_file.points.json").read_text(encoding="utf-8"))["points"][0]
    assert unavailable["inbound"] == ["[document].pdf"] and attached["inbound"] == ["[document].txt"]
    assert [f["status"] for f in unavailable["inbound_files"]] == ["unavailable"]
    assert [f["status"] for f in attached["inbound_files"]] == ["attached"]
    assert [json.loads(l)["file_unavailable"] for l in (out / "lost_file.jsonl").read_text(encoding="utf-8").splitlines()] \
        == [True, True]

    # the judge gets one neutral line per point, never a name, a path, a type or "old"/"new"
    payloads = []

    def fake_cli(system, payload):
        payloads.append(payload)
        body = json.loads(payload)
        if "replies" not in body:
            return json.dumps({"better": [], "worse": [], "look_first": [], "summary": "ok"})
        return json.dumps({"analysis": "a", "variance": "v", "scores": [
            {"label": r["label"], "verdict": "pass", "reason": "fine"} for r in body["replies"]]})

    RUN.judge_results(out, fake_cli)
    entries = {json.loads(p)["candidate_message"]: json.loads(p) for p in payloads if "replies" in json.loads(p)}
    assert entries["[document].txt"]["inbound_files"] == [{"kind": "document", "status": "attached"}]
    assert entries["[document].pdf"]["inbound_files"] == [{"kind": "document", "status": "unavailable"}]
    assert not any(w in " ".join(payloads) for w in ("lebenslauf", "cv.txt", "gone/", str(media), "run1"))
    assert "inbound_files" in RUN.JUDGE_SYSTEM and "placeholder" in RUN.JUDGE_SYSTEM


def test_a_prepare_whose_earlier_turn_errored_writes_no_prep_and_a_stale_prep_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(RP, "_git_sha", lambda repo_dir=None: "0123456789abcdef0123456789abcdef01234567")
    sb = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9201, "inbound", "text", "2030-09-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9201, "outbound", "text", "2030-09-01T10:00:05+00:00", body="Guten Tag"),
        _row(3, 9201, "inbound", "text", "2030-09-01T10:01:00+00:00", body="Bayern"),
        _row(4, 9201, "outbound", "text", "2030-09-01T10:01:05+00:00", body="Gut"),
        _row(5, 9201, "inbound", "text", "2030-09-01T10:02:00+00:00", body="Ja"),
    ])
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case = {"id": "c", "candidate_id": 9201, "at_turns": [3], "covers": {"archetype": "middle", "scenario": "s"},
            "region": "bavaria"}
    (cases_dir / "c.json").write_text(json.dumps(case))
    calls = []

    def broken_reply(system, user, session_id):
        calls.append(1)
        raise RuntimeError("model down")

    def good_reply(system, user, session_id):
        return ({"action": "reply", "bubbles": ["ok"], "escalate_to_manager": False, "no_send": False,
                 "card_patch": {}}, "s")

    media = tmp_path / "media"
    media.mkdir()
    logs = []
    totals = RUN.prepare_cases(RUN.load_cases(cases_dir), tmp_path / "out", cases_dir / "prep", sb,
                               lambda: LB.Client(reply=broken_reply), "2030-09-02T08:00:00+00:00", [media], log=logs.append)
    assert totals["failed_cases"] == 1 and totals["prepared"] == 0 and totals["errors"] == 2
    assert not (cases_dir / "prep" / "c.json").exists() and "FAILED, not prepared" in logs[0]

    RUN.prepare_cases(RUN.load_cases(cases_dir), tmp_path / "out", cases_dir / "prep", sb,
                      lambda: LB.Client(reply=good_reply), "2030-09-02T08:00:00+00:00", [media], log=logs.append)
    assert (cases_dir / "prep" / "c.json").exists()
    # the case now wants a turn the prep does not hold: refused before any run, with the command
    (cases_dir / "c.json").write_text(json.dumps({**case, "at_turns": [3, 4]}))
    with pytest.raises(SystemExit, match=r"c: .*prepared for candidate_id=9201 and turns \[3\].*turns \[3, 4\].*--prepare"):
        RUN.run_cases(RUN.load_cases(cases_dir), tmp_path / "out", 1, sb, prep_dir=cases_dir / "prep")


def test_case_edits_must_be_what_from_to_strings(tmp_path):
    base = {"id": "e", "candidate_id": 9301, "at_turns": [1], "covers": {"archetype": "middle", "scenario": "s"},
            "region": "bavaria"}
    for bad in ([{"what": "city", "from": "A"}], [{"what": "city", "from": "A", "to": "B", "why": "x"}],
                "city A to B", [{"what": "city", "from": "A", "to": 3}]):
        (tmp_path / "e.json").write_text(json.dumps({**base, "edits": bad}))
        with pytest.raises(SystemExit, match="edits must be a list"):
            RUN.load_cases(tmp_path)
    (tmp_path / "e.json").write_text(json.dumps({**base, "edits": [{"what": "city", "from": "A", "to": "B"}]}))
    assert RUN.load_cases(tmp_path)[0]["edits"] == [{"what": "city", "from": "A", "to": "B"}]
    (tmp_path / "e.json").write_text(json.dumps(base))
    assert "edits" not in RUN.load_cases(tmp_path)[0]


def test_media_roots_must_be_readable_directories(tmp_path):
    assert RUN.check_media_roots([tmp_path]) == [tmp_path]
    with pytest.raises(SystemExit, match="is not a directory"):
        RUN.check_media_roots([tmp_path / "nope"])
