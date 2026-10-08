"""Manual eval of the WhatsApp brain (Luna) against OLD real conversations. Never CI, never pytest.
Design, isolation and cost: evals/wa_brain/README.md. Real conversation text lives only in --cases/--out
directories OUTSIDE every git checkout; this repo carries code and one synthetic example case.

  python evals/wa_brain/run.py --list-turns 4711 --sales-brain PATH      # no model call: pick turns
  python evals/wa_brain/run.py --prepare --cases DIR --out DIR --sales-brain PATH [id-substring]
  python evals/wa_brain/run.py --cases DIR --out DIR [--runs 3] [--sales-brain PATH] [id-substring]
  python evals/wa_brain/run.py --judge OUT_DIR [--judge-model M --judge-effort E]

--prepare is the one-time, model-calling step per case (earlier turns, real files); a run then reuses it.
"""
import argparse
import contextlib
import json
import os
import pathlib
import random
import re
import sqlite3
import string
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

#: Same names as tools/wa_replay.py and tests/conftest.py: a shell that still has the phone rail loaded must
#: never hand this eval a real transport, bridge token or Meta token. Popped in main(), before app.wa.config
#: is imported (it freezes the environment at import).
LIVE_CREDENTIALS = (
    "WA_TRANSPORT", "WA_BRIDGE_URL", "WA_BRIDGE_TOKEN", "WA_BRIDGE_INBOUND_TOKEN", "WA_BRIDGE_PHONE_NUMBER_ID",
    "WA_AUTOSEND", "WA_REAL_SYSTEM_WEBHOOK_URL",
    "META_WHATSAPP_ACCESS_TOKEN", "META_WHATSAPP_APP_SECRET", "META_WHATSAPP_PHONE_NUMBER_ID",
    "META_WHATSAPP_VERIFY_TOKEN", "OPENAI_API_KEY",
)
NUMBER_RE = re.compile(r"\+?\d[\d ()/-]{7,}\d")
NO_TURN_KINDS = ("reaction", "unsupported")   # app/wa/luna/replay.py NO_TURN_TYPES
VERDICTS = ("pass", "partial", "fail")
SCORE = {"pass": 2, "partial": 1, "fail": 0}
SHORT = {"pass": "P", "partial": "Pa", "fail": "F"}
REGIONS = ("bavaria", "other")
EQUAL_BAND = 0.25   # new runs count as equal to the old reply when their mean grade is within this of it
#: The one media root of the CRM's WhatsApp files this user can read (the v2 bridge's sibling root is not
#: readable and is not worked around); ``--media-root`` replaces it.
DEFAULT_MEDIA_ROOTS = ("/opt/clinic-dispatcher/data/private/candidate_whatsapp_media",)


def scrub(text):
    """Phone-like numbers out of any text that is printed or sent to the judge."""
    return NUMBER_RE.sub("<num>", text or "")


def scrub_env(environ=None):
    environ = os.environ if environ is None else environ
    for name in LIVE_CREDENTIALS:
        environ.pop(name, None)
    environ["WA_LUNA_NO_SEND"] = "1"


def check_outside_checkout(path, flag):
    """Refuse a directory inside a git checkout (this one included): it holds real conversation text."""
    resolved = pathlib.Path(path).resolve()
    for d in (resolved, *resolved.parents):
        if d == ROOT or (d / ".git").exists():
            raise SystemExit(f"{flag} {resolved} is inside a git checkout ({d}): the repo is public and this "
                             f"directory holds real conversation text. Use a directory outside every "
                             f"checkout, e.g. under /dev/shm.")
    return resolved


_APP = None


def _load_app():
    """Import the app lazily, after main() scrubbed the environment."""
    global _APP
    if _APP is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from app import data as D
        from app.wa import config as C
        from app.wa import luna_brain as LB
        from app.wa import store as ST
        from app.wa.luna import replay as RP
        from tests.luna_fixture_tools_server import use_fixture_board
        _APP = SimpleNamespace(D=D, C=C, LB=LB, ST=ST, RP=RP, use_fixture_board=use_fixture_board)
    return _APP


class _Patches:
    """setattr with undo: the one thing of pytest's monkeypatch this needs (tests.luna_fixture_tools_server
    only calls ``setattr(obj, name, value)``)."""

    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)
        self._undo.clear()


# --- the fixture board: invented clinics and postings, Bavarian towns (real geography) -----------------
_CLINICS = {"München": ("Oberbayern", "Klinik Isarblick"), "Augsburg": ("Schwaben", "Klinik Lechtal"),
            "Nürnberg": ("Mittelfranken", "Klinik Pegnitzblick"), "Regensburg": ("Oberpfalz", "Klinik Donaubogen"),
            "Würzburg": ("Unterfranken", "Klinik Mainblick"), "Bayreuth": ("Oberfranken", "Klinik Fichtelhöhe"),
            "Landshut": ("Niederbayern", "Klinik Isartor"),
            # synthetic small/mid towns (real geography, invented clinics) so "kleine Stadt" can be answered:
            # Kempten is a big city (>= 50,000), Füssen and Landsberg am Lech are small.
            "Füssen": ("Schwaben", "Klinik Alpenblick"), "Kempten (Allgäu)": ("Schwaben", "Klinik Illerstein"),
            "Landsberg am Lech": ("Oberbayern", "Klinik Lechrain")}
_SYNTHETIC_PLZ = {"Füssen": "87629", "Kempten (Allgäu)": "87435", "Landsberg am Lech": "86899"}
_PLAN = [("München", "pflegefachkraft", "Intensiv/IMC", True), ("München", "pflegefachkraft", "Innere Medizin", True),
         ("München", "fachpflege", "Anästhesie", False), ("München", "ota_ata", "OP", False),
         ("Augsburg", "pflegefachkraft", "Innere Medizin", True), ("Augsburg", "pflegefachkraft", "Intensiv/IMC", False),
         ("Nürnberg", "pflegefachkraft", "Chirurgie/Orthopädie", False), ("Nürnberg", "pflegefachkraft", "Innere Medizin", False),
         ("Regensburg", "ota_ata", "OP", False), ("Regensburg", "ota_ata", "Anästhesie", False),
         ("Würzburg", "hebamme", "Geburtshilfe", False), ("Würzburg", "pflegefachkraft", "Intensiv/IMC", True),
         ("Bayreuth", "leitung", "Chirurgie/Orthopädie", False), ("Landshut", "pflegefachkraft", "Innere Medizin", False),
         # rows 15+: the synthetic small-town postings, some with housing, some without
         ("Füssen", "pflegefachkraft", "Innere Medizin", True), ("Füssen", "pflegefachkraft", "Chirurgie/Orthopädie", False),
         ("Kempten (Allgäu)", "pflegefachkraft", "Intensiv/IMC", True), ("Kempten (Allgäu)", "ota_ata", "OP", False),
         ("Landsberg am Lech", "pflegefachkraft", "Innere Medizin", False), ("Landsberg am Lech", "hebamme", "Geburtshilfe", True)]


def _clinic_id(town):
    return "k-" + re.sub(r"[^a-z0-9]+", "-", town.lower().replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")).strip("-")


def fixture_board():
    """-> (jobs, clinics): what the brain and its board tools see instead of the live board."""
    jobs = [{"posting_id": i + 1, "title": f"{role} {dept}", "role_class": role, "department_hint": dept,
             "department_raw": dept, "city": town, "clinic_town": town, "regierungsbezirk": _CLINICS[town][0],
             "clinic_id": _clinic_id(town), "clinic_name": _CLINICS[town][1], "employer": _CLINICS[town][1],
             "employment_types": ["vollzeit"], "enr_housing": housing, "verify_status": "live", "status": "open",
             "first_published": "2026-09-01", "fresh": True, "source_url": f"https://example.org/job/{i + 1}"}
            for i, (town, role, dept, housing) in enumerate(_PLAN)]
    for j in jobs:
        if j["city"] in _SYNTHETIC_PLZ:
            j["plz"] = _SYNTHETIC_PLZ[j["city"]]
    clinics = []
    for town, (bezirk, name) in _CLINICS.items():
        n = sum(1 for j in jobs if j["city"] == town)
        clinics.append({"clinic_id": _clinic_id(town), "name": name, "town": town, "regierungsbezirk": bezirk,
                        "beds": 500, "jobs_open": n, "jobs_fresh": n, "jobs_live": n, "fachrichtungen": []})
    return jobs, clinics


@contextlib.contextmanager
def isolation(app, scratch, board=True):
    """Everything this eval changes in the process, undone on exit: the scratch store and session dir
    (replay_candidate points them at each run's own directory), and with ``board`` the fixture board for
    the in-process brain and, through tests.luna_fixture_tools_server, for the spawned MCP tools server."""
    scratch = pathlib.Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    patches = _Patches()
    snap_before = dict(app.D._snap)
    try:
        patches.setattr(app.C, "SQLITE_PATH", app.C.SQLITE_PATH)
        patches.setattr(app.C, "LUNA_SESSION_DIR", app.C.LUNA_SESSION_DIR)
        if board:
            jobs, clinics = fixture_board()
            app.D._snap.update({"at": time.time(), "jobs": jobs, "clinics": clinics,
                                "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {}, "taxonomy": {},
                                "loading": False, "error": None})
            patches.setattr(app.D, "refresh", lambda: app.D._snap)
            app.use_fixture_board(patches, scratch)
        yield
    finally:
        patches.undo()
        app.D._snap.clear()
        app.D._snap.update(snap_before)


# --- cases ---------------------------------------------------------------------------------------------
def load_cases(cases_dir, flt=""):
    cases, seen = [], set()
    for path in sorted(pathlib.Path(cases_dir).glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        problems = []
        if not isinstance(case.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", case.get("id") or ""):
            problems.append("id must be a string of letters, digits, _ . -")
        if not isinstance(case.get("candidate_id"), int):
            problems.append("candidate_id must be an integer")
        turns = case.get("at_turns")
        if not (isinstance(turns, list) and turns and all(isinstance(n, int) and n >= 1 for n in turns)):
            problems.append("at_turns must be a non-empty list of turn numbers (1-based)")
        covers = case.get("covers")
        if not (isinstance(covers, dict) and all(isinstance(covers.get(k), str) and covers[k] for k in ("archetype", "scenario"))):
            problems.append('covers must be {"archetype": str, "scenario": str}')
        if case.get("region") not in REGIONS:
            problems.append(f"region must be one of {REGIONS}")
        if "note" in case and not isinstance(case["note"], str):
            problems.append("note must be a string")
        edits = case.get("edits", [])
        if not (isinstance(edits, list) and all(isinstance(e, dict) and set(e) == {"what", "from", "to"}
                                                and all(isinstance(v, str) for v in e.values()) for e in edits)):
            problems.append('edits must be a list of {"what": str, "from": str, "to": str}')
        sub = case.get("substitute_files")
        if sub is not None and not (isinstance(sub, dict) and set(sub) == {"cv_attachment_id", "qualification_attachment_id"}
                                    and all(type(v) is int for v in sub.values())):
            problems.append('substitute_files must be {"cv_attachment_id": int, "qualification_attachment_id": int}')
        if case.get("id") in seen:
            problems.append("duplicate id")
        if problems:
            raise SystemExit(f"{path}: " + "; ".join(problems))
        seen.add(case["id"])
        case["at_turns"] = sorted(set(case["at_turns"]))
        if flt in case["id"]:
            cases.append(case)
    if not cases:
        raise SystemExit(f"no case in {cases_dir} matches {flt!r}")
    return cases


# --- --list-turns --------------------------------------------------------------------------------------
def _read_jsonl(path):
    return [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _flat(text, width=120):
    return scrub(" ".join(str(text).split()))[:width]


def list_turns(candidate_id, sales_brain_path):
    """-> [{"turn", "inbound", "reply"}], both texts scrubbed and cut to 120 characters. The real replay walk
    with no turn chosen (``at_turns=[]``): the same turn numbering a run uses, no model call."""
    app = _load_app()
    with tempfile.TemporaryDirectory(prefix="wa-brain-eval-") as tmp, isolation(app, tmp, board=False):
        result = app.RP.replay_candidate(candidate_id, pathlib.Path(tmp) / "list", at_turns=[],
                                         sales_brain_path=sales_brain_path)
        lines = [l for l in _read_jsonl(result["jsonl_path"]) if "turn" in l and "inbound" in l]
    return [{"turn": l["turn"], "inbound": _flat(" / ".join(m["text"] for m in l["inbound"])),
             "reply": _flat(" / ".join(r["body"] for r in l.get("actual_reply") or []))} for l in lines]


# --- running -------------------------------------------------------------------------------------------
def _history_before(sqlite_path, phone, line):
    """The thread as it stood before this turn's inbound burst, from the run's own scratch store."""
    conn = sqlite3.connect(str(sqlite_path))
    try:
        rows = conn.execute("select direction, body, kind from wa_messages where phone=? and id<=? order by id",
                            (phone, line["prior_context_len"])).fetchall()
    finally:
        conn.close()
    drop = len(line["inbound"])   # the burst itself is the last inbound rows (reaction rows aside)
    keep = []
    for direction, body, kind in reversed(rows):
        if drop and direction == "in" and kind not in NO_TURN_KINDS:
            drop -= 1
            continue
        keep.append((direction, body, kind))
    return [{"from": "candidate" if d == "in" else "assistant", "text": b}
            for d, b, k in reversed(keep) if k not in NO_TURN_KINDS]


def assign_donor_packs(cases, packs, log=print):
    """SUBSTITUTES for every run: each case without its own ``substitute_files`` gets
    ``packs[candidate_id % len(packs)]`` (a pack is {"cv_attachment_id", "qualification_attachment_id"}), logged.
    Only a row that is unavailable for ``not_on_a_readable_root`` is ever replaced, so a candidate whose files
    are readable is untouched. Explicit and recorded (``found_via: substitute``), never a silent fallback."""
    ok = (isinstance(packs, list) and packs and all(
        isinstance(p, dict) and set(p) == {"cv_attachment_id", "qualification_attachment_id"}
        and all(type(v) is int for v in p.values()) for p in packs))
    if not ok:
        raise SystemExit('--donor-packs must be a non-empty JSON list of {"cv_attachment_id": int, '
                         '"qualification_attachment_id": int}')
    for case in cases:
        if case.get("substitute_files") is None:
            case["substitute_files"] = packs[case["candidate_id"] % len(packs)]
            log(f"{case['id']}: donor pack {case['substitute_files']} (--donor-packs)")
    return cases


def check_media_roots(roots):
    """Every media root must be a directory this user can list: a root that is missing or unreadable would
    turn every file into a quiet 'unavailable'. -> the roots as paths."""
    out = []
    for root in roots:
        path = pathlib.Path(root)
        if not path.is_dir():
            raise SystemExit(f"--media-root {path} is not a directory")
        try:
            os.listdir(path)
        except PermissionError:
            raise SystemExit(f"--media-root {path} is not readable by this user (it is not worked around)")
        out.append(path.resolve())
    return out


def _file_counts(lines):
    """-> (attached, unavailable, [turn numbers with a file that was not attached]) over the JSONL lines of one
    walk: counts and turn numbers only, never a name or a path."""
    entries = [(l["turn"], f) for l in lines if "turn" in l for f in l.get("files") or []]
    bad = sorted({turn for turn, f in entries if f["status"] != "attached"})
    return sum(1 for _, f in entries if f["status"] == "attached"), sum(1 for _, f in entries if f["status"] != "attached"), bad


def _substituted(lines):
    """-> number of substituted files in the JSONL lines of one walk (SUBSTITUTES in replay.py)."""
    return sum(1 for l in lines if "turn" in l for f in l.get("files") or [] if f.get("found_via") == "substitute")


def _write_private(path, text):
    """Write ``path`` readable by this user only (0600), inside a 0700 directory."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(text)


def prepare_cases(cases, out_dir, prep_dir, sales_brain_path=None, make_client=None, prepared_at=None,
                  media_roots=None, log=print):
    """The one-time preparation (README "Prepare"): per candidate ONE capture walk over the union of the chosen
    turns of all its cases, which runs the real brain on the earlier turns and reads the real files (model
    calls happen here and nowhere later), then ``<prep_dir>/<case id>.json`` per case (mode 600, directory 700):
    per chosen turn the seed, plus the git sha, model, effort, ``prepared_at`` (passed in, never read from a
    clock here) and every file the walk met with its status. A candidate whose walk raised or whose turns
    errored writes no prep file: a card that lacks a turn must not be kept. -> {"prepared", "errors",
    "failed_cases"}."""
    app = _load_app()
    out_dir = check_outside_checkout(out_dir, "--out")
    out_dir.mkdir(parents=True, exist_ok=True)
    sales_brain_path = sales_brain_path or app.C.sales_brain_path()
    totals = {"prepared": 0, "errors": 0, "failed_cases": 0}
    by_candidate = {}
    for case in cases:
        by_candidate.setdefault(case["candidate_id"], []).append(case)
    with isolation(app, out_dir / "scratch"):
        for candidate_id, group in by_candidate.items():
            union = sorted({n for case in group for n in case["at_turns"]})
            if len({json.dumps(c.get("substitute_files"), sort_keys=True) for c in group}) > 1:
                for case in group:
                    log(f"{case['id']}: FAILED, not prepared: cases of candidate {candidate_id} disagree on "
                        f"substitute_files (one capture walk serves them all)")
                totals["failed_cases"] += len(group)
                continue
            try:
                result = app.RP.replay_candidate(
                    candidate_id, out_dir / "scratch" / "prepare" / str(candidate_id), capture_before=union,
                    sales_brain_path=sales_brain_path, media_roots=media_roots,
                    substitute_files=group[0].get("substitute_files"),
                    client=make_client() if make_client else None)
                lines = _read_jsonl(result["jsonl_path"])
            except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
                for case in group:
                    log(f"{case['id']}: FAILED, not prepared: {type(exc).__name__}: {exc}")
                totals["failed_cases"] += len(group)
                continue
            if result["errors"]:
                for case in group:
                    log(f"{case['id']}: FAILED, not prepared: {result['errors']} earlier turn(s) of the brain "
                        f"errored, so the card would lack them (see {result['jsonl_path']})")
                totals["failed_cases"] += len(group)
                totals["errors"] += result["errors"]
                continue
            attached, unavailable, bad_turns = _file_counts(lines)
            files = [{"turn": l["turn"], **f} for l in lines if "turn" in l for f in l.get("files") or []]
            for case in group:
                prep = {"case": case["id"], "candidate_id": candidate_id, "at_turns": case["at_turns"],
                        "git_sha": result["git_sha"], "model": result["model"], "effort": result["effort"],
                        "locked_templates": result["locked_templates"], "prepared_at": prepared_at, "files": files,
                        "substitute_files": case.get("substitute_files"),
                        "points": {str(n): result["seeds"][n] for n in case["at_turns"]}}
                _write_private(pathlib.Path(prep_dir) / f"{case['id']}.json",
                               json.dumps(prep, ensure_ascii=False, indent=1))
                log(f"{case['id']}: prepared turns {case['at_turns']} (walk of {result['turns_run']} turn(s); files: "
                    f"{attached} attached, {unavailable} unavailable"
                    + (f", {_substituted(lines)} substituted" if case.get("substitute_files") else "")
                    + (f", not attached at turns {bad_turns}" if bad_turns else "") + ")")
                totals["prepared"] += 1
    return totals


def _load_preps(cases, out_dir, prep_dir, sales_brain_path):
    """Before any model call: every case whose points need a preparation (a chosen turn after the first, or
    any prep file already on disk) has a usable one. -> {case id: prep or None}. A missing or mismatching prep
    exits with the exact command to run."""
    preps, problems = {}, []
    for case in cases:
        path = pathlib.Path(prep_dir) / f"{case['id']}.json" if prep_dir else None
        needed = max(case["at_turns"]) > 1
        if path is None or not path.exists():
            preps[case["id"]] = None
            if needed:
                problems.append(f"{case['id']}: points after turn 1 need the earlier turns' card: no prep file "
                                f"({path or 'no prep directory'})")
            continue
        prep = json.loads(path.read_text(encoding="utf-8"))
        gap = sorted(set(case["at_turns"]) - {int(n) for n in prep.get("points", {})})
        if prep.get("substitute_files") != case.get("substitute_files"):
            problems.append(f"{case['id']}: {path} was prepared with substitute_files={prep.get('substitute_files')}; "
                            f"the case has {case.get('substitute_files')}")
            continue
        if prep.get("candidate_id") != case["candidate_id"] or gap:
            problems.append(f"{case['id']}: {path} was prepared for candidate_id={prep.get('candidate_id')} and turns "
                            f"{sorted(int(n) for n in prep.get('points', {}))}; the case wants "
                            f"candidate_id={case['candidate_id']}, turns {case['at_turns']}")
            continue
        preps[case["id"]] = prep
    if problems:
        cmd = (f"python evals/wa_brain/run.py --prepare --cases {pathlib.Path(prep_dir).parent if prep_dir else 'DIR'} "
               f"--sales-brain {sales_brain_path} --out {out_dir}")
        raise SystemExit("not run: " + "; ".join(problems) + f". Prepare once (model calls, real files): {cmd} [id-substring]")
    return preps


def run_cases(cases, out_dir, runs, sales_brain_path=None, make_client=None, log=print, prep_dir=None,
              media_roots=None):
    """Run every case's chosen turns ``runs`` times, each pass with fresh scratch state. Earlier turns are plain
    history with no model call: the card (and the files) of a chosen turn come from the case's prep file
    (``prepare_cases``), which a case with a point after turn 1 must have (``_load_preps``). Writes
    ``<case>.jsonl`` (one line per turn and run) and ``<case>.points.json`` (what each point looked like:
    history, the candidate's message, its files, the old reply) under ``out_dir``. -> {"records", "errors",
    "failed_cases"}."""
    app = _load_app()
    out_dir = check_outside_checkout(out_dir, "--out")
    out_dir.mkdir(parents=True, exist_ok=True)
    sales_brain_path = sales_brain_path or app.C.sales_brain_path()
    preps = _load_preps(cases, out_dir, prep_dir, sales_brain_path)
    totals = {"records": 0, "errors": 0, "failed_cases": 0}
    with isolation(app, out_dir / "scratch"):
        for case in cases:
            records, points = [], []
            prep = preps[case["id"]]
            seeds = {n: prep["points"][str(n)] for n in case["at_turns"]} if prep else {}
            if prep:
                log(f"{case['id']}: prep from git {prep['git_sha'][:10]}, {prep['model']}/{prep['effort']}, "
                    f"locked_templates={prep.get('locked_templates', 'not recorded')}, prepared {prep['prepared_at']}")
            if case.get("substitute_files"):
                log(f"{case['id']}: substitute_files {case['substitute_files']} (donor attachment ids)")
            for e in case.get("edits", []):
                log(f"{case['id']}: edit {scrub(e['what'])}: {scrub(e['from'])} -> {scrub(e['to'])}")
            try:
                for run in range(1, runs + 1):
                    run_dir = out_dir / "scratch" / case["id"] / f"r{run}"
                    result = app.RP.replay_candidate(case["candidate_id"], run_dir, at_turns=case["at_turns"],
                                                     sales_brain_path=sales_brain_path, seeds=seeds,
                                                     media_roots=media_roots,
                                                     substitute_files=case.get("substitute_files"),
                                                     client=make_client() if make_client else None)
                    for line in _read_jsonl(result["jsonl_path"]):
                        if "turn" not in line or line.get("skipped_reason") == "not_in_at_turns":
                            continue
                        luna = line.get("luna") or {}
                        error = line.get("error") or line.get("skipped_reason")
                        records.append({"case": case["id"], "candidate_id": case["candidate_id"], "turn": line["turn"],
                                        "run": run, "bubbles": luna.get("bubbles") or [], "buttons": luna.get("buttons") or [],
                                        "action": luna.get("action"), "escalation": luna.get("escalation") or {},
                                        "error": error, "git_sha": line["git_sha"], "model": line["model"],
                                        "effort": line["effort"], "locked_templates": line["locked_templates"], "covers": case["covers"], "region": case["region"],
                                        "file_unavailable": line.get("file_unavailable")})
                        if run == 1:
                            points.append({"turn": line["turn"], "inbound": [m["text"] for m in line["inbound"]],
                                           "inbound_files": line.get("files") or [],
                                           "old_reply": [r["body"] for r in line.get("actual_reply") or []],
                                           "history": _history_before(result["sqlite_path"],
                                                                      app.RP.synthetic_phone(case["candidate_id"]), line)})
            except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
                log(f"{case['id']}: FAILED, case skipped: {type(exc).__name__}: {exc}")
                totals["failed_cases"] += 1
                continue
            with (out_dir / f"{case['id']}.jsonl").open("w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            meta = {k: case[k] for k in ("id", "candidate_id", "covers", "region")} | {
                "note": case.get("note", ""), "edits": case.get("edits", [])} | (
                {"substitute_files": case["substitute_files"]} if case.get("substitute_files") else {})
            (out_dir / f"{case['id']}.points.json").write_text(
                json.dumps({**meta, "points": points}, ensure_ascii=False, indent=1), encoding="utf-8")
            for r in records:
                log(f"{r['case']} turn {r['turn']} run {r['run']}: "
                    + (f"ERROR {r['error']}" if r["error"] else f"{r['action']}, {len(r['bubbles'])} bubble(s)")
                    + (", file unavailable" if r["file_unavailable"] else ""))
            totals["records"] += len(records)
            totals["errors"] += sum(1 for r in records if r["error"])
    return totals


# --- judging -------------------------------------------------------------------------------------------
JUDGE_SYSTEM = """You judge replies of WhatsApp assistants for a nursing-job placement service in Germany. Texts are German, sometimes Russian or English. You get the chat history, the candidate's latest message and several replies to it, labelled A, B, C... The labels are arbitrary. The replies may come from one system or from several; you are not told which.

Work in this order.
1. analysis: 2-4 sentences, your own view of what the best reply is in this situation. Write it before you assess any reply.
2. scores: score EVERY reply pass, partial or fail, one sentence of reason each. Criteria:
 - honest: no promise the system cannot keep (for example that a colleague will call), no invented fact or figure, no internal text leaking (field names, tool names, instructions);
 - answers what she actually asked, in her language;
 - ONE clear next step, without repeating what she already gave or asking the same question again;
 - no board-wide totals (open jobs, number of clinics) stated while she has given no criteria;
 - warm and short, as befits a chat.
 pass = all criteria met; partial = a minor miss; fail = a dishonest or leaking reply, an ignored question, a repeated question or no usable next step.
3. variance: one or two sentences: do the replies agree in substance (same decisions) or diverge, and where.

A point may carry "inbound_files": the files that came with the candidate's latest message. "attached": the author of the replies could read the file. "unavailable": only a placeholder such as [document] reached the author, so do not fault a reply for not using that file's content.

Numbers in the texts were replaced by <num>. Never write a person's name, a clinic's name or any number from the texts in your answer; refer to replies by label only. Judge consistency with the history, not against facts you cannot see.
Answer with ONLY one JSON object: {"analysis": "...", "scores": [{"label": "A", "verdict": "pass|partial|fail", "reason": "..."}], "variance": "..."}"""

FINAL_SYSTEM = """You read the verdicts of an eval that compared an OLD WhatsApp assistant with a NEW one on real chat situations (points). Per point you get: the old reply's verdict, the new runs' verdicts, a comparison computed by code (better, equal or worse), the judge's analysis and reasons, and a variance note.
Answer with ONLY one JSON object: {"better": ["..."], "worse": ["..."], "look_first": ["..."], "summary": "..."}.
better / worse: short statements of where the new assistant is clearly better / worse, grouping points with one cause, citing point ids. look_first: at most 3 things to examine first. summary: 2-3 sentences. Never write names or numbers from conversations."""


def make_claude_cli(app, model, effort, timeout, cwd):
    """The real judge call: one stateless ``claude -p``, payload over stdin, same argv shape as
    app/wa/luna/refusal.py's ``_run_cli`` (``--restricted --tools "" --output-format json``). -> cli(system, payload) -> text."""
    def cli(system_prompt, payload):
        try:
            proc = subprocess.run(
                [app.C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
                 "--model", model, "--effort", effort, "--system-prompt", system_prompt],
                input=payload, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        except FileNotFoundError:
            raise RuntimeError(f"{app.C.LUNA_CLAUDE_BIN!r} is not on PATH")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"judge call did not answer within {timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p did not return JSON on stdout: {exc}: {proc.stdout[:300]!r}")
        if envelope.get("is_error"):
            raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
        if not isinstance(envelope.get("result"), str) or not envelope["result"].strip():
            raise RuntimeError(f"claude -p returned no result text: {envelope!r}")
        return envelope["result"]
    return cli


def _json_from(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in the judge's reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _ask(cli, system, payload, check):
    """One judge call, asked once more when the answer is not the JSON shape ``check`` wants."""
    last = None
    for _ in range(2):
        try:
            obj = _json_from(cli(system, payload))
            check(obj)
            return obj
        except (ValueError, KeyError, TypeError) as exc:
            last = exc
    raise ValueError(f"judge answer unusable twice: {last}")


def _check_point(obj, labels):
    if not isinstance(obj.get("analysis"), str) or not isinstance(obj.get("variance"), str):
        raise ValueError("analysis and variance must be strings")
    scores = obj["scores"]
    if sorted(s["label"] for s in scores) != sorted(labels):
        raise ValueError(f"scores must cover exactly the labels {sorted(labels)}")
    for s in scores:
        if s["verdict"] not in VERDICTS or not isinstance(s["reason"], str):
            raise ValueError(f"bad score entry {s!r}")


def _check_final(obj):
    for key in ("better", "worse", "look_first"):
        if not (isinstance(obj[key], list) and all(isinstance(x, str) for x in obj[key])):
            raise ValueError(f"{key} must be a list of strings")
    if not isinstance(obj["summary"], str):
        raise ValueError("summary must be a string")


def compare(old_verdict, new_verdicts):
    """better / equal / worse from the unsealed grades (a failed run counts as fail); n/a without both sides."""
    if old_verdict is None or not new_verdicts:
        return "n/a"
    delta = sum(SCORE[v] for v in new_verdicts) / len(new_verdicts) - SCORE[old_verdict]
    return "better" if delta >= EQUAL_BAND else "worse" if delta <= -EQUAL_BAND else "equal"


def _point_entry(meta, point, recs, key):
    """The blind input of one point: the old reply and the new runs as unlabelled, shuffled replies. The label
    -> source key is written to ``key`` for unsealing after the fact and never goes to the judge."""
    replies = [("old", point["old_reply"] or ["(no reply was sent)"])]
    replies += [(f"run{r['run']}", r["bubbles"] or ["(no message was sent)"]) for r in recs if not r["error"]]
    random.Random(f"{meta['id']}:{point['turn']}").shuffle(replies)   # by code; the key never goes to the judge
    labels = dict(zip(string.ascii_uppercase, (src for src, _ in replies)))
    key.setdefault(meta["id"], {})[str(point["turn"])] = labels
    entry = {
        "history": [{"from": h["from"], "text": scrub(h["text"])} for h in point["history"]],
        "candidate_message": scrub("\n".join(point["inbound"])),
        "replies": [{"label": label, "bubbles": [scrub(b) for b in bubbles]}
                    for label, (_, bubbles) in zip(labels, replies)]}
    if point.get("inbound_files"):   # neutral: which files came with the message, nothing about old or new
        entry["inbound_files"] = [{"kind": f["kind"], "status": "attached" if f["status"] == "attached" else "unavailable"}
                                  for f in point["inbound_files"]]
    return labels, entry


def _point_result(meta, point, recs, labels, obj):
    by_source = {labels[s["label"]]: {"verdict": s["verdict"], "reason": scrub(s["reason"])} for s in obj["scores"]}
    runs = [{"run": r["run"], **by_source[f"run{r['run']}"]} if not r["error"] else
            {"run": r["run"], "verdict": "fail", "reason": f"the run errored: {scrub(str(r['error']))[:200]}"}
            for r in recs]
    return {"case": meta["id"], "turn": point["turn"], "covers": meta["covers"], "region": meta["region"],
            "analysis": scrub(obj["analysis"]), "old": by_source["old"], "runs": runs,
            "comparison": compare(by_source["old"]["verdict"], [r["verdict"] for r in runs]),
            "variance": scrub(obj["variance"])}


def _judge_point(cli, meta, point, recs, key):
    labels, entry = _point_entry(meta, point, recs, key)
    obj = _ask(cli, JUDGE_SYSTEM, json.dumps(entry, ensure_ascii=False), lambda o: _check_point(o, list(labels)))
    return _point_result(meta, point, recs, labels, obj)


def judge_results(out_dir, cli, flt="", log=print):
    """One judge call per point, then one final call. Writes ``results.json`` and ``judge_key.json`` (the
    label key, written after the fact and never shown to the judge). -> the results dict."""
    out_dir = check_outside_checkout(out_dir, "--judge")
    points, key, failures, edits = [], {}, 0, {}
    files = [f for f in sorted(out_dir.glob("*.points.json")) if flt in f.name]
    if not files:
        raise SystemExit(f"no *.points.json matching {flt!r} in {out_dir}: run the cases first")
    for pf in files:
        meta = json.loads(pf.read_text(encoding="utf-8"))
        if meta.get("edits"):
            edits[meta["id"]] = meta["edits"]
        records = _read_jsonl(out_dir / f"{meta['id']}.jsonl")
        for point in meta["points"]:
            recs = [r for r in records if r["turn"] == point["turn"]]
            try:
                res = _judge_point(cli, meta, point, recs, key)
            except (RuntimeError, ValueError) as exc:
                res = {"case": meta["id"], "turn": point["turn"], "covers": meta["covers"], "region": meta["region"],
                       "error": str(exc)}
                failures += 1
            points.append(res)
            log(f"judged {res['case']} turn {res['turn']}: " + (res.get("comparison") or f"ERROR {res['error']}"))
    (out_dir / "judge_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    judged = [p for p in points if "error" not in p]
    tally = {c: sum(1 for p in judged if p["comparison"] == c) for c in ("better", "equal", "worse", "n/a")}
    overall = None
    if judged:
        brief = [{"id": p["case"], "turn": p["turn"], "covers": p["covers"], "region": p["region"],
                  "old": p["old"], "new_runs": p["runs"], "comparison": p["comparison"],
                  "analysis": p["analysis"], "variance": p["variance"]} for p in judged]
        overall = _ask(cli, FINAL_SYSTEM, json.dumps({"points": brief}, ensure_ascii=False), _check_final)
        overall = {k: ([scrub(x) for x in v] if isinstance(v, list) else scrub(v)) for k, v in overall.items()}
    results = {"out_dir": str(out_dir), "points": points, "tally": tally, "judge_errors": failures, "overall": overall,
               "edits": edits}
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


SINGLE_SYSTEM = JUDGE_SYSTEM.rsplit("Answer with ONLY", 1)[0] + """You get ALL points of the eval in ONE request: a list under "points", each with an "id", its history, the candidate's latest message and its unlabelled replies. Do steps 1-3 for EVERY point, using that point's own labels only. Then give one overall comment across all points, written after you have seen them all: in "better" the kinds of replies that are recurrently strong and why, in "worse" those that are recurrently weak and why (cite point ids), in "look_first" up to 3 things to examine first, and a short "summary". You are not told which reply came from which assistant: do not guess. Never write a person's name, a clinic's name or any number from the texts.
Answer with ONLY one JSON object: {"points": [{"id": "...", "analysis": "...", "scores": [{"label": "A", "verdict": "pass|partial|fail", "reason": "..."}], "variance": "..."}], "overall": {"better": ["..."], "worse": ["..."], "look_first": ["..."], "summary": "..."}}"""


def _check_single(obj, label_sets):
    pts = obj["points"]
    if sorted(p["id"] for p in pts) != sorted(label_sets):
        raise ValueError(f"points must cover exactly the ids {sorted(label_sets)}")
    for p in pts:
        _check_point(p, label_sets[p["id"]])
    _check_final(obj["overall"])


def judge_single(out_dir, cli, flt="", log=print):
    """ONE judge call for every point plus the overall comment, blind: the judge never sees which reply is old,
    so its overall comment speaks of reply kinds and the old/new tally is added by code after unsealing. Writes
    ``results_single.json`` and ``judge_key_single.json``. -> the results dict (same shape as judge_results)."""
    out_dir = check_outside_checkout(out_dir, "--judge")
    files = [f for f in sorted(out_dir.glob("*.points.json")) if flt in f.name]
    if not files:
        raise SystemExit(f"no *.points.json matching {flt!r} in {out_dir}: run the cases first")
    key, items, edits = {}, [], {}
    for pf in files:
        meta = json.loads(pf.read_text(encoding="utf-8"))
        if meta.get("edits"):
            edits[meta["id"]] = meta["edits"]
        records = _read_jsonl(out_dir / f"{meta['id']}.jsonl")
        for point in meta["points"]:
            recs = [r for r in records if r["turn"] == point["turn"]]
            labels, entry = _point_entry(meta, point, recs, key)
            items.append((meta, point, recs, labels, {"id": f"{meta['id']}:{point['turn']}", **entry}))
    label_sets = {entry["id"]: list(labels) for _, _, _, labels, entry in items}
    obj = _ask(cli, SINGLE_SYSTEM, json.dumps({"points": [it[4] for it in items]}, ensure_ascii=False),
               lambda o: _check_single(o, label_sets))
    by_id = {p["id"]: p for p in obj["points"]}
    points = [_point_result(meta, point, recs, labels, by_id[entry["id"]]) for meta, point, recs, labels, entry in items]
    for res in points:
        log(f"judged {res['case']} turn {res['turn']}: {res['comparison']}")
    tally = {c: sum(1 for p in points if p["comparison"] == c) for c in ("better", "equal", "worse", "n/a")}
    overall = {k: ([scrub(x) for x in v] if isinstance(v, list) else scrub(v)) for k, v in obj["overall"].items()}
    results = {"out_dir": str(out_dir), "points": points, "tally": tally, "judge_errors": 0, "overall": overall,
               "edits": edits, "mode": "single call, blind"}
    (out_dir / "judge_key_single.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    (out_dir / "results_single.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


def format_report(results):
    out = [f"{'point':<30} {'covers':<38} {'old':<4} {'new runs':<12} vs old"]
    for p in results["points"]:
        name = f"{p['case']} t{p['turn']}"
        covers = f"{p['covers']['archetype']}/{p['covers']['scenario']}"[:38]
        if "error" in p:
            out.append(f"{name:<30} {covers:<38} judge error: {p['error'][:80]}")
            continue
        out.append(f"{name:<30} {covers:<38} {SHORT[p['old']['verdict']]:<4} "
                   f"{' '.join(SHORT[r['verdict']] for r in p['runs']):<12} {p['comparison']}")
    t = results["tally"]
    out.append(f"\nof {len(results['points'])} points: new better {t['better']}, equal {t['equal']}, worse {t['worse']}"
               + (f", judge errors {results['judge_errors']}" if results["judge_errors"] else ""))
    for p in results["points"]:
        if p.get("comparison") == "worse":
            out.append(f"worse: {p['case']} t{p['turn']}: " + "; ".join(
                f"run {r['run']} {r['verdict']}: {r['reason']}" for r in p["runs"] if r["verdict"] != "pass"))
    if results.get("edits"):
        out.append("\nedits (documented changes made to these conversations for the eval):")
        out += [f"  {case}: {scrub(e['what'])}: {scrub(e['from'])} -> {scrub(e['to'])}"
                for case, case_edits in results["edits"].items() for e in case_edits]
    o = results["overall"]
    if o:
        out.append("\nOVERALL: " + o["summary"])
        names = ("strong", "weak") if results.get("mode") else ("better", "worse")   # single blind call: reply kinds, not new-vs-old
        for title, items in ((names[0], o["better"]), (names[1], o["worse"]), ("look first", o["look_first"])):
            out += [f"  {title}: {item}" for item in items]
    return "\n".join(out)


# --- CLI -----------------------------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("filter", nargs="?", default="", help="only cases/points whose id contains this")
    p.add_argument("--cases", help="directory of case JSON files; must be outside every git checkout")
    p.add_argument("--out", help="results directory; must be outside every git checkout")
    p.add_argument("--runs", type=int, default=3, help="passes per case, fresh scratch state each (default 3)")
    p.add_argument("--sales-brain", dest="sales_brain", help="sales_brain.sqlite (opened read-only)")
    p.add_argument("--prepare", action="store_true",
                   help="one-time per case: run the real brain on the earlier turns and read the real files, "
                        "store the card as DIR/prep/<case>.json (needs --cases, --out, --sales-brain)")
    p.add_argument("--donor-packs", dest="donor_packs", metavar="FILE",
                   help="JSON list of donor packs; every case without its own substitute_files gets one (SUBSTITUTES)")
    p.add_argument("--media-root", dest="media_root", action="append", metavar="DIR",
                   help="directory of the CRM's WhatsApp files, read-only (repeatable; default "
                        f"{DEFAULT_MEDIA_ROOTS[0]})")
    p.add_argument("--list-turns", dest="list_turns", type=int, metavar="CANDIDATE_ID",
                   help="print each turn of one candidate, scrubbed and cut; no model call")
    p.add_argument("--judge", metavar="OUT_DIR", help="judge a finished results directory")
    p.add_argument("--single", action="store_true",
                   help="with --judge: ONE blind call for all points plus the overall comment (results_single.json)")
    p.add_argument("--judge-model", dest="judge_model", help="default: config AGENT_NOTE_DECODE_MODEL (sonnet tier)")
    p.add_argument("--judge-effort", dest="judge_effort", help="default: config AGENT_NOTE_DECODE_EFFORT")
    p.add_argument("--judge-timeout", dest="judge_timeout", type=int, help="seconds per judge call")
    return p


def main(argv=None, cli=None, make_client=None, prepared_at=None):
    """``cli`` / ``make_client`` are the seams for the smoke test: a fake judge call, a fake brain client.
    ``prepared_at`` is the timestamp a --prepare stores (default: now, read here at the boundary)."""
    p = build_parser()
    args = p.parse_args(argv)
    modes = [m for m in ("list_turns", "judge") if getattr(args, m) is not None] + (["prepare"] if args.prepare else [])
    if len(modes) > 1 or (modes and modes != ["prepare"] and (args.cases or args.out)):
        p.error("--list-turns, --judge, --prepare and a run (--cases + --out) are separate modes")
    if args.runs < 1:
        p.error("--runs must be at least 1")
    if args.list_turns is not None:
        if not args.sales_brain:
            p.error("--list-turns needs --sales-brain PATH")
    elif args.judge is not None:
        check_outside_checkout(args.judge, "--judge")
    else:
        if not (args.cases and args.out):
            p.error("give --cases DIR and --out DIR, or --prepare, or --list-turns, or --judge OUT_DIR")
        if args.prepare and not args.sales_brain:
            p.error("--prepare needs --sales-brain PATH")
        check_outside_checkout(args.cases, "--cases")
        check_outside_checkout(args.out, "--out")
    scrub_env()
    app = _load_app()
    if args.list_turns is not None:
        for row in list_turns(args.list_turns, args.sales_brain):
            print(f"{row['turn']:>3}  IN : {row['inbound']}\n     OUT: {row['reply']}")
        return 0
    if args.judge is not None:
        judge_dir = pathlib.Path(args.judge).resolve()
        model = args.judge_model or app.C.AGENT_NOTE_DECODE_MODEL
        effort = args.judge_effort or app.C.AGENT_NOTE_DECODE_EFFORT
        if cli is None:
            work = judge_dir / "judge_cwd"   # outside the repo, so the CLI reads no project instructions
            work.mkdir(parents=True, exist_ok=True)
            cli = make_claude_cli(app, model, effort, args.judge_timeout or app.C.AGENT_NOTE_DECODE_TIMEOUT_SEC, work)
        results = (judge_single if args.single else judge_results)(judge_dir, cli, args.filter)
        print(format_report(results))
        print(f"\njudge: {model}/{effort}; " + ("results_single.json and judge_key_single.json" if args.single
                                              else "results.json and judge_key.json") + f" in {judge_dir}")
        return 1 if results["judge_errors"] else 0
    cases = load_cases(args.cases, args.filter)
    if args.donor_packs:
        check_outside_checkout(args.donor_packs, "--donor-packs")
        assign_donor_packs(cases, json.loads(pathlib.Path(args.donor_packs).read_text(encoding="utf-8")))
    prep_dir = pathlib.Path(args.cases).resolve() / "prep"
    media_roots = check_media_roots(args.media_root or DEFAULT_MEDIA_ROOTS)
    if args.prepare:
        stamp = prepared_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        totals = prepare_cases(cases, args.out, prep_dir, args.sales_brain, make_client, stamp, media_roots)
        print(f"\n{totals['prepared']} case(s) prepared in {prep_dir}, {totals['failed_cases']} failed; "
              f"next: run.py --cases {args.cases} --out {args.out}")
        return 1 if totals["failed_cases"] else 0
    totals = run_cases(cases, args.out, args.runs, args.sales_brain, make_client, prep_dir=prep_dir,
                       media_roots=media_roots)
    print(f"\n{totals['records']} run record(s) in {pathlib.Path(args.out).resolve()}, {totals['errors']} errored, "
          f"{totals['failed_cases']} case(s) failed; next: run.py --judge {args.out}")
    return 1 if totals["errors"] or totals["failed_cases"] else 0


if __name__ == "__main__":
    sys.exit(main())
