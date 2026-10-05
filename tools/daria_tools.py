"""Daria's own tools, a stdio MCP server the `claude` CLI starts for each of her answers (TASK-345.12.2).

Ivan, 2026-10-01: Daria answers him and Valentyn with "брейн, тот же тулсет что у wa harness для доски, + свой раздел в
беклоге (пайплайн задач)". The board tools come from app/wa/luna/tools_server.py (server "jobs"); this server adds the
rest:

- brain_tables / brain_query: sales_brain, read-only (URI mode=ro, PRAGMA query_only, ATTACH refused).
- mailing_state: the state of every campaign on Daria's desk, a text file the desk writes before each answer.
- read_doc: one of the case documents named in the desk config.
- desk_log: the desk's own ledger, newest first: operator mails, commands, answers, failures.
- pipeline_list / pipeline_view / pipeline_create: her section of the backlog, project "daria", through the backlog CLI.
- mailing_windows / mailing_window_tail / send_output_tail: look at the mailing tmux windows and a batch's output file.
- plan_batch / start_batch / stop_batch / start_desk: the mailing commands, each one fixed command of tools/clinic_mailer.py
  or tools/daria_desk.py run in a window of her own tmux session (TASK-345.12.10). She has no shell and no way to type
  into a window; a batch goes live only with the approval file that an operator renamed.

Run as `.venv/bin/python tools/daria_tools.py` (the venv has the `mcp` package). Everything it needs comes from the
environment the desk writes into the MCP config: DARIA_BRAIN, DARIA_STATE, DARIA_DESK_LEDGER, DARIA_DOCS (JSON name -> path),
DARIA_REPO.
Every call is appended to DARIA_LOG. A failure is a ToolError the model sees, never an empty result.
The mailing tools also need DARIA_CAMPAIGNS (JSON campaign -> config path), DARIA_DESK_CONFIG, DARIA_TMUX (JSON
{"session": the session her commands run in, "view": [sessions she may look at]}), DARIA_ASKED_BY (the operator whose mail
she is answering) and DARIA_TZ; each of those calls is also written to the desk ledger.
"""
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

PROJECT = "daria"
# A budget, not a ceiling on the query: the rows past it are counted and reported as truncated (CLAUDE.md).
RESULT_BUDGET_BYTES = 150_000

mcp = MCPServer("daria")


def _env(name):
    v = os.environ.get(name)
    if not v:
        raise ToolError(f"{name} is not set: the desk starts this server with it")
    return v


def _log(tool, args):
    path = os.environ.get("DARIA_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": tool, "args": args, "at": time.time()}, ensure_ascii=False) + "\n")


def _brain():
    con = sqlite3.connect(f"file:{_env('DARIA_BRAIN')}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    con.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
                       else sqlite3.SQLITE_OK)
    return con


@mcp.tool()
def brain_tables() -> list[dict]:
    """The tables of sales_brain (our CRM: companies, people, contact_channels, lead_records, activities, candidates,
    suppression_list ...) with their columns. Call this before brain_query. Its data ends at the last sync
    (about 2026-09-16); say so when a date matters."""
    _log("brain_tables", {})
    con = _brain()
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return [{"table": n, "columns": [c[1] for c in con.execute(f'PRAGMA table_info("{n}")')]} for n in names]
    finally:
        con.close()


@mcp.tool()
def brain_query(sql: str) -> dict:
    """One read-only SQL statement (SQLite) on sales_brain. Returns columns, rows, rows_total and truncated: past
    about 150 KB the rows are counted but not returned, so narrow the query (WHERE, selected columns) when truncated
    is true. Notes: suppression_list rows sometimes store one of OUR mailboxes instead of the person who opted out;
    status "bounced" can be a spam rejection; lead_records stage labels can be years old, the timeline is in
    activities (raw_payload_json holds from_email, to_emails, subject)."""
    _log("brain_query", {"sql": sql})
    con = _brain()
    try:
        cur = con.execute(sql)
    except sqlite3.Error as e:
        con.close()
        raise ToolError(f"sqlite: {e}")
    try:
        cols = [d[0] for d in cur.description or []]
        rows, size, total, truncated = [], 0, 0, False
        for r in cur:
            total += 1
            if truncated:
                continue
            enc = json.dumps(r, ensure_ascii=False, default=str)
            if size + len(enc) > RESULT_BUDGET_BYTES:
                truncated = True
                continue
            rows.append(list(r))
            size += len(enc)
        return {"columns": cols, "rows": rows, "rows_total": total, "truncated": truncated}
    finally:
        con.close()


@mcp.tool()
def mailing_state() -> str:
    """Every campaign on Daria's desk: what was planned, what went to which clinic and when, what is still planned,
    who answered or was taken out, the recipients with their vacancies, and the operator notes. Written by the desk
    right before this answer."""
    _log("mailing_state", {})
    return Path(_env("DARIA_STATE")).read_text(encoding="utf-8")


@mcp.tool()
def desk_log(since: str = "") -> dict:
    """Daria's own desk log, newest first: every operator mail the desk read (who, subject, text, what it was taken
    for), every command carried out and every answer sent, failures, desk starts and stops. `since` is an ISO date or
    time (Berlin offset in the log); empty means the whole log. Events past the result budget are counted and reported
    as truncated."""
    _log("desk_log", {"since": since})
    events = [json.loads(line) for line in Path(_env("DARIA_DESK_LEDGER")).read_text(encoding="utf-8").splitlines() if line.strip()]
    if since:
        events = [e for e in events if e.get("ts", "") >= since]
    events.reverse()
    out, size, truncated = [], 0, False
    for e in events:
        enc = len(json.dumps(e, ensure_ascii=False).encode())
        if size + enc > RESULT_BUDGET_BYTES:
            truncated = True
            break
        out.append(e)
        size += enc
    return {"events": out, "events_total": len(events), "truncated": truncated}


@mcp.tool()
def read_doc(name: str) -> str:
    """One case document by name. Call with an unknown name to get the list of names."""
    _log("read_doc", {"name": name})
    docs = json.loads(_env("DARIA_DOCS"))
    if name not in docs:
        raise ToolError(f"no document {name!r}; known: {', '.join(sorted(docs))}")
    return Path(docs[name]).read_text(encoding="utf-8")


def _backlog(*args):
    p = subprocess.run(["backlog", *args], cwd=_env("DARIA_REPO"), capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise ToolError(f"backlog {args[0]} {args[1]} exited {p.returncode}: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout


def _own_task(task_id):
    data = json.loads(_backlog("task", "view", task_id, "--json"))
    task = data.get("task", data)
    project = task.get("project") or task.get("projectName")
    if project != PROJECT:
        raise ToolError(f"{task_id} is not in project {PROJECT} (it is in {project!r}); Daria reads and writes only her own tasks")
    return task


@mcp.tool()
def pipeline_list(status: str = "") -> str:
    """Daria's task pipeline (backlog project "daria"): every task, or those with status "To Do", "In Progress" or
    "Done"."""
    _log("pipeline_list", {"status": status})
    args = ["task", "list", "--project", PROJECT, "--plain"] + (["--status", status] if status else [])
    return _backlog(*args)


@mcp.tool()
def pipeline_view(task_id: str) -> str:
    """One task of Daria's pipeline in full."""
    _log("pipeline_view", {"task_id": task_id})
    _own_task(task_id)
    return _backlog("task", "view", task_id, "--plain")


@mcp.tool()
def pipeline_create(title: str, why: str, acceptance_criteria: list[str], painless: bool = False) -> str:
    """Put a job into Daria's pipeline: something an operator asked for that cannot be done by mail (a new or changed
    mailing, other texts, recipients or times, research that needs more time). Title, `why` and the acceptance
    criteria are in English (the backlog's language); `why` says who asked, when and why. No candidate's name, phone
    or email anywhere: candidates by number ("candidate 79"). `painless` marks a task that changes nothing outside
    and needs no approval (research, a draft, a list), which Daria may take up on her own. Returns the new task id."""
    _log("pipeline_create", {"title": title, "painless": painless})
    if not acceptance_criteria:
        raise ToolError("give at least one acceptance criterion: what is true when the job is done")
    args = ["task", "create", title, "--project", PROJECT, "-l", "daria,painless" if painless else "daria",
            "-a", "@daria", "-d", why]
    for ac in acceptance_criteria:
        args += ["--ac", ac]
    return _backlog(*args, "--plain")


# ---------- mailing: tmux windows and the fixed mailing commands (TASK-345.12.10) ----------

PLAN_WAIT_SECONDS = 600          # a plan renders a PDF; past this the tool says "not finished" and leaves the window running


def _tmux(*args):
    p = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise ToolError(f"tmux {args[0]} exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")
    return p.stdout


def _sessions():
    try:
        return _tmux("list-sessions", "-F", "#{session_name}").split()
    except ToolError as e:
        if "no server running" in str(e):
            return []
        raise


def _processes():
    """[(pid, ppid, args)] of every process."""
    out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, timeout=30, check=True).stdout
    return [(int(a), int(b), c) for a, b, c in (line.strip().split(None, 2) for line in out.splitlines() if line.strip())]


def _ledger():
    """The desk ledger, opened for appending; it is the audit trail, so a call that cannot be written down does not run."""
    path = _env("DARIA_DESK_LEDGER")
    try:
        return open(path, "a", encoding="utf-8")
    except OSError as e:
        raise ToolError(f"cannot write the desk ledger {path}: {e}")


def _desk_event(event):
    tz = ZoneInfo(_env("DARIA_TZ"))
    line = json.dumps({"ts": datetime.now(tz).isoformat(timespec="seconds"), "event": "mailing_tool",
                       "by": os.environ.get("DARIA_ASKED_BY"), **event}, ensure_ascii=False)
    with _ledger() as f:
        f.write(line + "\n")


@contextmanager
def _audit(tool, args):
    """Every call goes to the desk ledger with who asked, the command and its result (or its error)."""
    _log(tool, args)
    rec = {"tool": tool, "args": args}
    _ledger().close()
    try:
        yield rec
    except Exception as e:
        _desk_event({**rec, "error": str(e)})
        raise
    _desk_event(rec)


def _tmux_sessions():
    t = json.loads(_env("DARIA_TMUX"))
    return t["session"], t["view"]


def _campaign(name):
    """(config path, config dict, directory) of a campaign on the desk."""
    known = json.loads(_env("DARIA_CAMPAIGNS"))
    if name not in known:
        raise ToolError(f"no campaign {name!r}; known: {', '.join(sorted(known))}")
    path = Path(known[name])
    return path, json.loads(path.read_text(encoding="utf-8")), path.parent


def _rel(cfgdir, cfg, key):
    return (cfgdir / cfg[key]).resolve()


def _batch(name, batch_id):
    path, cfg, d = _campaign(name)
    if Path(batch_id).name != batch_id or not (_rel(d, cfg, "batches") / f"{batch_id}.json").is_file():
        raise ToolError(f"campaign {name} has no batch {batch_id!r}")
    return path, cfg, d


def _windows():
    """[{"session", "window", "pane_pid", "dead", "processes": [args]}] of the sessions she may look at."""
    _, view = _tmux_sessions()
    if not _sessions():
        return []
    procs = _processes()
    kids = {}
    for pid, ppid, args in procs:
        kids.setdefault(ppid, []).append((pid, args))
    out = []
    for line in _tmux("list-panes", "-a", "-F", "#{session_name}\t#{window_name}\t#{pane_pid}\t#{pane_dead}").splitlines():
        session, window, pid, dead = line.split("\t")
        if session not in view:
            continue
        tree, todo = [args for p, _, args in procs if p == int(pid)], [int(pid)]       # the pane's own process and everything under it
        while todo:
            for cpid, args in kids.get(todo.pop(), []):
                tree.append(args)
                todo.append(cpid)
        out.append({"session": session, "window": window, "pane_pid": int(pid), "dead": dead == "1", "processes": tree})
    return out


def _tail(path, lines):
    p = Path(path)
    if not p.exists():
        raise ToolError(f"{p} does not exist")
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    if len(text.encode()) > RESULT_BUDGET_BYTES:
        return {"file": str(p), "text": text.encode()[-RESULT_BUDGET_BYTES:].decode(errors="replace"), "truncated": True}
    return {"file": str(p), "text": text, "truncated": False}


def _running(pattern):
    """The args of every process whose command line matches `pattern`."""
    return [args for _, _, args in _processes() if re.search(pattern, args)]


def _window_command(argv, out):
    """The one shell line a window runs: the fixed command, its output appended to `out`, then its exit status."""
    return f"cd {shlex.quote(_env('DARIA_REPO'))} && {shlex.join(argv)} >> {shlex.quote(str(out))} 2>&1; echo \"exit status: $?\" >> {shlex.quote(str(out))}"


def _run_window(name, argv, out):
    """Start `argv` in a window of her own session (made when it is gone). No shell is left open in it."""
    session, _ = _tmux_sessions()
    line = _window_command(argv, out)
    if session in _sessions():
        _tmux("new-window", "-d", "-t", f"{session}:", "-n", name, line)
    else:
        _tmux("new-session", "-d", "-s", session, "-n", name, line)
    return line


@mcp.tool()
def mailing_windows() -> list[dict]:
    """The windows of the mailing tmux sessions: session, window name, whether its pane is dead and the processes in it
    (a batch is "clinic_mailer.py send <config> <batch id> --live"; the desk is "daria_desk.py run"). A window that is not
    listed has no process any more."""
    with _audit("mailing_windows", {}) as rec:
        rec["command"] = ["tmux", "list-panes", "-a"]
        out = [{k: w[k] for k in ("session", "window", "dead", "processes")} for w in _windows()]
        rec["result"] = f"{len(out)} windows"
        return out


@mcp.tool()
def mailing_window_tail(session: str, window: str, lines: int = 60) -> dict:
    """The last `lines` lines on screen of one tmux window of the mailing sessions. Batches and the desk write their
    output to a file, so their window is often empty: read that with send_output_tail."""
    with _audit("mailing_window_tail", {"session": session, "window": window, "lines": lines}) as rec:
        if (session, window) not in {(w["session"], w["window"]) for w in _windows()}:
            raise ToolError(f"no window {session}:{window}; mailing_windows lists them")
        rec["command"] = ["tmux", "capture-pane", "-p", "-t", f"{session}:{window}", "-S", f"-{lines}"]
        text = _tmux(*rec["command"][1:])
        rec["result"] = f"{len(text.splitlines())} lines"
        return {"text": text}


@mcp.tool()
def send_output_tail(campaign: str, batch_id: str, lines: int = 60) -> dict:
    """The last `lines` lines of the output file of one batch's send process (what it logs: the announcement wait,
    every letter, every watch of the inbox, the halt reason when it stopped) and of "exit status:" when it ended.
    `campaign` is the campaign name (mailing_state names them)."""
    with _audit("send_output_tail", {"campaign": campaign, "batch_id": batch_id, "lines": lines}) as rec:
        _, cfg, d = _batch(campaign, batch_id)
        rec["command"] = ["tail", "-n", str(lines), str(_rel(d, cfg, "ledger").parent / f"send-{batch_id}.out")]
        out = _tail(rec["command"][-1], lines)
        rec["result"] = f"{len(out['text'].splitlines())} lines of {out['file']}"
        return out


def _iso(name, value):
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{name} {value!r} is not an ISO time like 2026-10-09T10:32:00+02:00")
    if t.tzinfo is None:
        raise ToolError(f"{name} {value!r} needs a UTC offset, e.g. 2026-10-09T10:32:00+02:00")
    return value


@mcp.tool()
def plan_batch(campaign: str, announce_at: str, start_at: str = "", only: list[str] | None = None) -> dict:
    """Plan a scheduled batch of a campaign: the mailer's `plan` command. It writes the batch, its text and a pending
    approval; it sends nothing and approves nothing: an operator renames the approval after reading the plan. `announce_at`
    is when the announcement goes to the operators, `start_at` (optional) when the first letters start, both ISO with a
    UTC offset. `only` limits it to those recipient ids. Waits for the command and returns its output (the batch id and
    the file to read) and exit status."""
    with _audit("plan_batch", {"campaign": campaign, "announce_at": announce_at, "start_at": start_at, "only": only or []}) as rec:
        path, cfg, d = _campaign(campaign)
        argv = ["python3", "-u", "tools/clinic_mailer.py", "plan", str(path), "--announce-at", _iso("announce_at", announce_at)]
        if start_at:
            argv += ["--start-at", _iso("start_at", start_at)]
        if only:
            ids = {r["id"] for r in json.loads(_rel(d, cfg, "recipients").read_text(encoding="utf-8"))}
            unknown = [i for i in only if i not in ids]
            if unknown:
                raise ToolError(f"campaign {campaign} has no recipient ids {unknown}")
            argv += ["--only", *only]
        out = _rel(d, cfg, "ledger").parent / f"plan-{datetime.now(ZoneInfo(_env('DARIA_TZ'))):%Y%m%d-%H%M%S}.out"
        rec["command"] = argv
        _run_window(f"plan-{campaign}", argv, out)
        waited = 0
        while waited < PLAN_WAIT_SECONDS:
            text = out.read_text(encoding="utf-8") if out.exists() else ""
            if "exit status: " in text:
                status = int(text.rsplit("exit status: ", 1)[1].split()[0])
                rec["result"] = {"exit_status": status, "output_file": str(out)}
                return {"finished": True, "exit_status": status, "output": text, "output_file": str(out)}
            time.sleep(1)
            waited += 1
        rec["result"] = {"finished": False, "output_file": str(out)}
        return {"finished": False, "output": out.read_text(encoding="utf-8") if out.exists() else "", "output_file": str(out),
                "note": f"not finished after {PLAN_WAIT_SECONDS} s; the window keeps running, read the file later"}


@mcp.tool()
def start_batch(campaign: str, batch_id: str) -> dict:
    """Start the send process of a planned batch live, or restart it after it was halted or stopped: the mailer's
    `send <config> <batch id> --live`, one command for both. The mailer itself refuses a batch without the approval
    file an operator renamed from its pending one, one that was halted for good (an operator's stop, a delivery
    failure: it needs a new plan) and one whose send time has passed; the reason is in its output file, read it with
    send_output_tail a minute later. Refused while a process for that batch runs (it would send the letters twice).
    Returns the window and the output file."""
    with _audit("start_batch", {"campaign": campaign, "batch_id": batch_id}) as rec:
        path, cfg, d = _batch(campaign, batch_id)
        approval = _rel(d, cfg, "approvals") / f"{batch_id}.json"
        if not approval.is_file():
            raise ToolError(f"{batch_id} has no approval {approval.name}: an operator reads the plan and renames {batch_id}.pending.json first")
        running = _running(rf"clinic_mailer\.py send \S+ {re.escape(batch_id)}(\s|$)")
        if running:
            raise ToolError(f"{batch_id} already runs, a second process would send its letters twice: {running[0][:200]}")
        argv = ["sudo", "-E", "python3", "-u", "tools/clinic_mailer.py", "send", str(path), batch_id, "--live"]
        out = _rel(d, cfg, "ledger").parent / f"send-{batch_id}.out"
        rec["command"] = argv
        _run_window(batch_id, argv, out)
        rec["result"] = {"window": batch_id, "output_file": str(out)}
        return rec["result"]


@mcp.tool()
def stop_batch(campaign: str, batch_id: str) -> dict:
    """End the running send process of a batch the way Ctrl-C does: it logs a halt (resumable with start_batch while no
    send time has passed) and sends nothing more. This is not an operator's stop (that cancels the letters and needs a new
    plan); it ends the process. Fails when no window runs that batch."""
    with _audit("stop_batch", {"campaign": campaign, "batch_id": batch_id}) as rec:
        _, cfg, d = _batch(campaign, batch_id)
        mine = [w for w in _windows() if any(f"clinic_mailer.py send " in a and f" {batch_id} " in a + " " for a in w["processes"])]
        if not mine:
            raise ToolError(f"no window runs a send process for {batch_id}; mailing_windows lists what runs")
        w = mine[0]
        rec["command"] = ["tmux", "send-keys", "-t", f"{w['session']}:{w['window']}", "C-c"]
        _tmux(*rec["command"][1:])
        time.sleep(3)
        out = _rel(d, cfg, "ledger").parent / f"send-{batch_id}.out"
        rec["result"] = {"window": f"{w['session']}:{w['window']}", "sent": "C-c", "output_file": str(out)}
        return rec["result"]


@mcp.tool()
def start_desk() -> dict:
    """Start the desk (`daria_desk.py run <desk config>`), the process that reads the operators' mail and answers it,
    in the window "desk" of her tmux session. Refused while a desk runs (a second one would answer every mail twice)."""
    with _audit("start_desk", {}) as rec:
        cfg = Path(_env("DARIA_DESK_CONFIG"))
        running = _running(r"daria_desk\.py run \S+")
        if running:
            raise ToolError(f"a desk already runs, a second one would answer every mail twice: {running[0][:200]}")
        argv = ["sudo", "-E", "python3", "-u", "tools/daria_desk.py", "run", str(cfg)]
        out = cfg.parent / "desk.out"
        rec["command"] = argv
        _run_window("desk", argv, out)
        rec["result"] = {"window": "desk", "output_file": str(out)}
        return rec["result"]


if __name__ == "__main__":
    mcp.run(transport="stdio")
