"""The llm Luna tests' board, served to Luna's MCP tools too (TASK-96 repair round 2).

The claude CLI spawns app/wa/luna/tools_server.py as a fresh process, so a test's ``D._snap`` fixture never
reaches it: the server built its own snapshot from Supabase (401 without the service env), every
search_postings/list_clinics call returned nothing, and Luna told personas "Für München ... keine offene Stelle"
two turns before the harness shortlist named Klinikum München.

``use_fixture_board(monkeypatch, tmp_path)`` (call it after the fixture filled ``D._snap``) dumps the board to
JSON and points ``LB._mcp_config_path`` at ``python -m tests.luna_fixture_tools_server`` with WA_TEST_BOARD_JSON;
the rest of the generated config (PYTHONPATH, WA_SQLITE_PATH, WA_LUNA_SESSION_DIR) stays as luna_brain writes it.
Run that way, this module loads the JSON into ``D._snap``, pins ``D.refresh`` to it (same as the test fixtures)
and runs the real tools server.

``D.job_detail`` is pinned to the board too, in both processes (2026-10-05): it is a live PostgREST read of the
``postings`` row (luna_brain's warming shortlist, the get_posting tool). Unpinned, a checkout with the board's
.env fetched REAL ads for the fixture's synthetic posting ids 1..N, and the pre-push gate's scratch checkout
(no .env) failed every persona with "PostgREST 401: No API key found".
"""
import json
import os
import time
from pathlib import Path

from app import data as D

_MODULE = "tests.luna_fixture_tools_server"
_BOARD_KEYS = ("jobs", "clinics", "by_clinic", "facets", "taxonomy")
_CLINIC_KEYS = ("clinic_id", "name", "town", "regierungsbezirk", "ats_type", "beds", "size", "careers_url", "website")


def fixture_job_detail(posting_id):
    """app/data.py:job_detail's shape, served from the fixture board: no live columns (description is None,
    as job_detail's callers already allow), no observations."""
    job = next((j for j in D.jobs() if j["posting_id"] == int(posting_id)), None)
    if job is None:
        return None
    row = {**job, "observations": []}
    c = D.clinic(job.get("clinic_id") or "")
    if c:
        row["clinic"] = {k: c.get(k) for k in _CLINIC_KEYS}
    return row


def use_fixture_board(monkeypatch, tmp_path):
    from app.wa import luna_brain as LB

    board_json = tmp_path / "fixture_board.json"
    board_json.write_text(json.dumps({k: D._snap[k] for k in _BOARD_KEYS}, ensure_ascii=False), encoding="utf-8")
    real_config_path = LB._mcp_config_path

    def fixture_config_path(*args, **kw):
        path = real_config_path(*args, **kw)
        config = json.loads(path.read_text(encoding="utf-8"))
        server = config["mcpServers"][LB.MCP_SERVER_NAME]
        server["args"] = ["-m", _MODULE]
        server["env"]["WA_TEST_BOARD_JSON"] = str(board_json)
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    monkeypatch.setattr(LB, "_mcp_config_path", fixture_config_path)
    monkeypatch.setattr(D, "job_detail", fixture_job_detail)


if __name__ == "__main__":
    D._snap.update(json.loads(Path(os.environ["WA_TEST_BOARD_JSON"]).read_text(encoding="utf-8")),
                   at=time.time(), loading=False, error=None)
    D.refresh = lambda: D._snap
    D.job_detail = fixture_job_detail
    from app.wa.luna import tools_server as TS
    # TS.serve(), not mcp.run(): it applies the tool descriptions the model reads (built by the parent
    # from this same fixture board and passed in WA_LUNA_BOARD_VOCABULARY, TASK-110) and stamps the
    # readiness file _live_reply checks, so an llm run exercises both sides exactly as production does.
    TS.serve()
