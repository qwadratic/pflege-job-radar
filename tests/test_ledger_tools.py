"""tools/ledger.py + the two apply tools: no hand-made data change without a classified reason, evidence
and task, and pflege_jobs.corrections gets exactly the fields that change (Ivan, 2026-09-29). No network."""
import json

import pytest

from tools import ledger as L
from tools.apply_clinic_corrections import load_corrections
from tools.apply_posting_changes import load_changes, plan

WHY = {"code": "board_location", "reason": "board lives on the group portal",
       "evidence": ["https://x.de/jobs fetched 2026-09-29: 12 jobs"], "task": "TASK-169"}
CLINIC_CODES = {"parse_error", "source_outdated", "source_error", "not_in_source", "board_location"}
POSTING_CODES = {"wrong_clinic", "duplicate", "not_a_vacancy", "false_gone", "role_misclassified"}
PWHY = {**WHY, "code": "wrong_clinic"}


@pytest.mark.parametrize("bad, missing", [
    (None, "missing _why"),
    ({**WHY, "code": None}, "code (one of"),
    ({**WHY, "code": "wrong_clinic"}, "code (one of"),      # a postings code on a clinic
    ({k: v for k, v in WHY.items() if k != "reason"}, "reason"),
    ({**WHY, "evidence": []}, "evidence"),
    ({**WHY, "evidence": ["  "]}, "evidence"),
    ({**WHY, "task": "task 1"}, "task"),
])
def test_require_why_refuses_incomplete_blocks(bad, missing):
    with pytest.raises(SystemExit) as e:
        L.require_why(bad, "clinic 1", CLINIC_CODES)
    assert missing in str(e.value) and "clinic 1" in str(e.value)


def test_entries_record_only_changed_fields_with_old_and_new():
    old = {"careers_url": "https://a.de/jobs", "ats_type": "softgarden", "beds": 300}
    lines = L.entries("clinics", "57707", old, {"ats_type": "self_hosted", "beds": 300}, L.require_why(WHY, "x", CLINIC_CODES),
                      "claude", "2026-09-29T21:00:00Z", backup="backups/b.json")
    assert lines == [{"at": "2026-09-29T21:00:00Z", "table": "clinics", "id": "57707", "field": "ats_type",
                      "old": "softgarden", "new": "self_hosted", "code": "board_location", "reason": WHY["reason"],
                      "evidence": WHY["evidence"], "task": "TASK-169", "by": "claude", "backup": "backups/b.json"}]


class _Cursor:
    def __init__(self, sink): self.sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params): self.sink.append((sql, params))


class _Conn:
    def __init__(self): self.sent = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _Cursor(self.sent)


def test_record_inserts_one_row_per_line_with_json_values_and_sql_null():
    conn = _Conn()
    line = L.entries("postings", 7063, {"clinic_id": "56404"}, {"clinic_id": None}, L.require_why(PWHY, "x", POSTING_CODES),
                     "claude", "2026-09-29T21:00:00Z")[0]
    assert L.record([line, {**line, "field": "*", "old": None, "new": {"a": 1}}], conn) == 2
    (sql, p1), (_, p2) = conn.sent
    assert sql.startswith("insert into pflege_jobs.corrections")
    assert p1[:4] == ("2026-09-29T21:00:00Z", "postings", "7063", "clinic_id")
    assert p1[4].adapted == "56404" and p1[5] is None                   # old as jsonb, new None as SQL NULL
    assert p1[6:] == ("wrong_clinic", PWHY["reason"], PWHY["evidence"], "TASK-169", "claude", None)
    assert p2[4] is None and p2[5].adapted == {"a": 1}


def test_clinic_corrections_need_why_and_real_columns(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"57707": {"ats_type": "self_hosted", "_why": WHY}}), encoding="utf-8")
    changes, whys, inserts = load_corrections(str(p), CLINIC_CODES)
    assert changes == {"57707": {"ats_type": "self_hosted"}} and whys["57707"]["code"] == "board_location" and not inserts
    p.write_text(json.dumps({"57707": {"ats_type": "self_hosted"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="clinic 57707: missing _why"):
        load_corrections(str(p), CLINIC_CODES)
    p.write_text(json.dumps({"57707": {"atstype": "x", "_why": WHY}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not clinic columns"):
        load_corrections(str(p), CLINIC_CODES)


def test_clinic_insert_flag_is_taken_off_the_fields(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"16101": {"name": "Klinikum Ingolstadt", "beds": 798, "_insert": True,
                                       "_why": {**WHY, "code": "parse_error"}}}), encoding="utf-8")
    changes, whys, inserts = load_corrections(str(p), CLINIC_CODES)
    assert changes == {"16101": {"name": "Klinikum Ingolstadt", "beds": 798}} and inserts == {"16101"}


def _changes(tmp_path, items):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return load_changes(str(p), POSTING_CODES)


@pytest.mark.parametrize("items, msg", [
    ([{"posting_id": 1, "action": "delete", "_why": PWHY}], "action must be"),
    ([{"posting_id": 1, "action": "relink", "_why": PWHY}], "relink needs clinic_id"),
    ([{"posting_id": 1, "action": "retire"}], "missing _why"),
    ([{"posting_id": 1, "action": "retire", "_why": WHY}], "code (one of"),     # a clinics code on a posting
    ([{"posting_id": 1, "action": "retire", "_why": PWHY}, {"posting_id": 1, "action": "unlink", "_why": PWHY}], "twice"),
])
def test_posting_changes_refuse_bad_input(tmp_path, items, msg):
    with pytest.raises(SystemExit, match=msg.replace("(", r"\(")):
        _changes(tmp_path, items)


def test_plan_maps_each_action_to_its_edge_op_and_expected_state(tmp_path):
    ch = _changes(tmp_path, [{"posting_id": 5706, "action": "retire", "_why": PWHY},
                             {"posting_id": 10076, "action": "relink", "clinic_id": 67705, "_why": PWHY},
                             {"posting_id": 6376, "action": "unlink", "_why": PWHY},
                             {"posting_id": 7063, "action": "unlink", "lock": True, "_why": PWHY}])
    at = "2026-09-29T21:00:00Z"
    op, row, expect = plan(ch[0], at)
    assert op == "verify" and row["verify_status"] == "gone" and row["verified_at"] == at
    assert row["verify_note"].startswith("retired by hand (TASK-169): board lives")
    assert expect == {"verify_status": "gone", "status": "expired"}
    assert plan(ch[1], at) == ("clinic_links", {"posting_id": 10076, "clinic_id": "67705", "clinic_match_rule": "manual",
                                                "clinic_match_score": None}, {"clinic_id": "67705", "clinic_match_rule": "manual"})
    assert plan(ch[2], at)[2] == {"clinic_id": None, "clinic_match_rule": None}
    reopen = _changes(tmp_path, [{"posting_id": 15113, "action": "reopen", "_why": {**PWHY, "code": "false_gone"}}])[0]
    op, row, expect = plan(reopen, at)
    assert (op, row["verify_status"], expect) == ("verify", "live", {"verify_status": "live", "status": "open"})
    assert plan(ch[3], at)[2] == {"clinic_id": None, "clinic_match_rule": "manual"}
