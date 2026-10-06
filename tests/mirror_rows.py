"""The rows of a mirrored board as app/crawl.py and pflege_jobs/cli.py._process_rows hand them to Matcher.match (TASK-431.9).

`board_rows(board_id)` replays the board's adapter from the mirror the way production calls it (the registry towns reach _vendor_rows, which
tests/adapter_harness.run_adapter leaves out) and returns, per retained row, the arguments of the match: employer, city, the board's pool of
clinic ids, the two inherited flags, the description and title. A row the drain skips (outside Bavaria, an excluded role, a staging host) is not
returned. `registry()` is the 651 clinics of the `clinics` table of 2026-10-06 (tests/fixtures/registry_names_2026-10-06.json: id, name, town,
operator, beds, parse_quality; the older registry_clinics fixture has no name for the 229 Reha rows) as a Matcher takes them.

Mirror content is read at test time and never committed (CLAUDE.md, "Tests never touch a live site")."""
import json
import pathlib
from urllib.parse import urlparse

import requests

from app import crawl as AppCrawl
from pflege_jobs import cli
from pflege_jobs import config as C
from pflege_jobs.registry import Matcher
from pflege_jobs.sources.inbox import NON_PROD_HOST, jobposting_to_obs
from tests import adapter_contract as AC
from tests import adapter_harness as H
from tests import mirror as M

_FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "registry_names_2026-10-06.json"


def registry():
    return Matcher([dict(c) for c in json.loads(_FIXTURE.read_text(encoding="utf-8"))])


def board_rows(board_id):
    board = next((b for b in H.indexed_boards() if b["board_id"] == board_id), None)
    if board is None:
        raise M.MirrorMiss(f"the mirror has no board {board_id!r}: .venv/bin/python {M.RECORD} record {board_id}")
    towns = set(M.Store.load_shared(board_id).meta("towns"))
    ids = [x["clinic_id"] for x in board["clinics"]]
    out = []
    if board["kind"] == "vendor":
        with H._mirror(board, H.ADAPTER), AC.RecordCalls():
            rows = AppCrawl._vendor_rows(board, board["clinics"][0], requests.Session(), lambda *a: None, towns=towns)
        for r in rows:
            host = r.get("source_host") or urlparse(r.get("source_url") or "").netloc
            o = jobposting_to_obs({"kind": "jobposting", "source_host": host, "source_url": r.get("source_url"), "payload": r.get("payload"),
                                   "inbox_id": 0, "collector": r.get("collector")}, towns)
            out.append(_row(board_id, o, o.pop("_board", None), o.pop("_emp_inherited", False)))
    else:
        rows, _ = H.run_adapter(board)
        for r in rows:
            o = dict(r)
            o.setdefault("_board", ids)
            out.append(_row(board_id, o, o.pop("_board", None), o.pop("_emp_inherited", False)))
    return [r for r in out if r]


def _row(board_id, o, pool, emp_inherited):
    if (NON_PROD_HOST.search(urlparse(o.get("source_url") or "").netloc) or o.get("role_class") in C.EXCLUDED_ROLE_CLASSES
            or o.get("in_bavaria") is False):
        return None
    return {"board_id": board_id, "url": o.get("source_url"), "employer": o.get("employer_name"), "city": o.get("city"), "pool": pool,
            "employer_inherited": emp_inherited, "city_inherited": cli._city_inherited(o), "description": o.get("description"), "title": o.get("title")}


def match(m, row):
    """Matcher.match with exactly what _process_rows passes (the pool of one copy; the union over copies is not needed by the cases here)."""
    return m.match(row["employer"], row["city"], board=row["pool"], employer_inherited=row["employer_inherited"],
                   city_inherited=row["city_inherited"], description=row["description"])
