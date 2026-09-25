"""tools/agent_note_cron.sh, exercised as a real subprocess (it is a bash script -- there is nothing
else to import) with every path it touches overridden by env so no test here reads or writes anything
under /home/claude/repo/pflege-board's own data/ or state/ dirs, and no test spawns the real `claude`
or `backlog` CLI (the worker itself is replaced by WA_AGENT_NOTE_WORKER_CMD, a small fake script)."""
import json
import os
import pathlib
import sqlite3
import stat
import subprocess

import pytest

SCRIPT = "/home/claude/repo/pflege-board/tools/agent_note_cron.sh"
REAL_CLAUDE = "/home/claude/.local/bin/claude"
REAL_VENV_PY = os.path.realpath("/home/claude/repo/pflege-board/.venv/bin/python")


@pytest.fixture()
def env(tmp_path):
    """A scratch repo dir (with a venv/bin/python symlinked to the REAL interpreter, so the script's
    own write_health() -- which always uses that path, never WA_AGENT_NOTE_WORKER_CMD -- produces real
    JSON), a scratch state dir, and a scratch sqlite db with just the one table/columns the prefilter
    query needs. -> the env dict a test starts from; each test overrides just what it needs to."""
    repo_dir = tmp_path / "repo"
    (repo_dir / ".venv" / "bin").mkdir(parents=True)
    (repo_dir / "data").mkdir()
    os.symlink(REAL_VENV_PY, repo_dir / ".venv" / "bin" / "python")
    state_dir = tmp_path / "state"
    db_path = repo_dir / "data" / "wa.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("create table wa_agent_notes (id integer primary key, status text, notified_at text)")
    conn.commit()
    conn.close()
    return {
        "HOME": "/home/claude", "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "WA_AGENT_NOTE_REPO_DIR": str(repo_dir), "WA_AGENT_NOTE_STATE_DIR": str(state_dir),
        "WA_SQLITE_PATH": str(db_path), "WA_AGENT_NOTE_CLAUDE_BIN": REAL_CLAUDE,
        "WA_AGENT_NOTE_TEST_HOUR": "10",
    }


def run(env, timeout=20):
    return subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True, timeout=timeout)


def health_path(env):
    return os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "health.json")


def read_health(env):
    with open(health_path(env), encoding="utf-8") as f:
        return json.load(f)


def add_pending_row(env, note_id=1):
    conn = sqlite3.connect(env["WA_SQLITE_PATH"])
    conn.execute("insert into wa_agent_notes (id, status, notified_at) values (?, 'pending', null)",
                (note_id,))
    conn.commit()
    conn.close()


def write_fake_worker(tmp_path, body):
    path = tmp_path / "fake_worker.sh"
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# --- outside the window ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour", ["00", "08", "22", "23"])
def test_outside_the_window_exits_0_and_touches_nothing(env, hour):
    env = {**env, "WA_AGENT_NOTE_TEST_HOUR": hour}
    proc = run(env)
    assert proc.returncode == 0
    assert not os.path.exists(env["WA_AGENT_NOTE_STATE_DIR"])


@pytest.mark.parametrize("hour", ["09", "10", "21"])
def test_inside_the_window_proceeds_past_the_gate(env, hour):
    """09 and 21 are the inclusive/exclusive edges of the half-open [09,22) window."""
    env = {**env, "WA_AGENT_NOTE_TEST_HOUR": hour}
    proc = run(env)
    assert proc.returncode == 0   # empty queue -> exits 0, but AFTER creating the state dir this time
    assert os.path.isdir(env["WA_AGENT_NOTE_STATE_DIR"])


def test_a_leading_zero_hour_is_read_as_decimal_not_octal(env):
    """`date +%H` prints '09' for 9 AM -- bash would read a leading-zero numeral as octal, and '09'/
    '08' are not valid octal digits, which crashes the comparison outright without the base-10 fix."""
    env = {**env, "WA_AGENT_NOTE_TEST_HOUR": "09"}
    proc = run(env)
    assert proc.returncode == 0
    assert "invalid" not in (proc.stderr or "").lower()


# --- binary guard (AC#2) ----------------------------------------------------------------------

def test_a_missing_claude_binary_is_a_problem_and_exit_1(env):
    env = {**env, "WA_AGENT_NOTE_CLAUDE_BIN": os.path.join(env["WA_AGENT_NOTE_REPO_DIR"], "no-such-claude")}
    proc = run(env)
    assert proc.returncode == 1
    h = read_health(env)
    assert h["ok"] is False and "claude binary guard failed" in h["problem"]


def test_a_zero_byte_claude_binary_is_a_problem_and_exit_1(env, tmp_path):
    empty = tmp_path / "empty-claude"
    empty.write_bytes(b"")
    empty.chmod(empty.stat().st_mode | stat.S_IEXEC)
    env = {**env, "WA_AGENT_NOTE_CLAUDE_BIN": str(empty)}
    proc = run(env)
    assert proc.returncode == 1
    assert "claude binary guard failed" in read_health(env)["problem"]


def test_a_non_executable_claude_binary_is_a_problem(env, tmp_path):
    not_exec = tmp_path / "not-executable"
    not_exec.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    env = {**env, "WA_AGENT_NOTE_CLAUDE_BIN": str(not_exec)}
    proc = run(env)
    assert proc.returncode == 1
    assert "claude binary guard failed" in read_health(env)["problem"]


def test_a_working_claude_symlink_passes_the_guard(env):
    """The real /home/claude/.local/bin/claude, resolved through readlink -f -- confirms the guard
    accepts a REAL symlink-to-executable, not just rejecting broken ones."""
    add_pending_row(env)
    fake = write_fake_worker(pathlib.Path(env["WA_AGENT_NOTE_REPO_DIR"]).parent, "exit 0\n")
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}
    proc = run(env)
    assert proc.returncode == 0
    assert "claude binary guard failed" not in (open(health_path(env)).read()
                                                if os.path.exists(health_path(env)) else "")


# --- sqlite prefilter: missing db/table is a problem, never silently 0 -------------------------

def test_a_missing_database_is_a_problem_and_exit_1(env):
    os.remove(env["WA_SQLITE_PATH"])
    proc = run(env)
    assert proc.returncode == 1
    h = read_health(env)
    assert h["ok"] is False and "db not found" in h["problem"]


def test_a_database_missing_the_table_is_a_problem_not_zero_rows(env):
    conn = sqlite3.connect(env["WA_SQLITE_PATH"])
    conn.execute("drop table wa_agent_notes")
    conn.commit()
    conn.close()
    proc = run(env)
    assert proc.returncode == 1
    h = read_health(env)
    assert h["ok"] is False and "sqlite prefilter" in h["problem"]


# --- empty queue -----------------------------------------------------------------------------

def test_an_empty_queue_exits_0_and_writes_no_health_file(env):
    proc = run(env)
    assert proc.returncode == 0
    assert not os.path.exists(health_path(env))


def test_a_queue_with_only_a_delivered_finished_note_is_still_empty(env):
    """done/blocked WITH notified_at set is not open work -- the prefilter's own SQL must agree with
    python's open_agent_notes()."""
    conn = sqlite3.connect(env["WA_SQLITE_PATH"])
    conn.execute("insert into wa_agent_notes (id, status, notified_at) values (1, 'done', 'sometime')")
    conn.commit()
    conn.close()
    proc = run(env)
    assert proc.returncode == 0
    assert not os.path.exists(health_path(env))


# --- lock: ticks never overlap ------------------------------------------------------------------

def test_a_held_lock_makes_a_concurrent_tick_exit_0_without_running_the_worker(env, tmp_path):
    add_pending_row(env)
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    lock_path = state_dir / "agent_note_cron.lock"
    marker = tmp_path / "worker_ran"
    fake = write_fake_worker(tmp_path, f"touch {marker}\nexit 0\n")
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}

    with open(lock_path, "w") as lockfile:
        import fcntl
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        proc = run(env)
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)

    assert proc.returncode == 0
    assert not marker.exists(), "the worker must not run while another tick holds the lock"


# --- non-empty queue invokes the worker -----------------------------------------------------

def test_a_non_empty_queue_invokes_the_worker_from_the_repo_dir_with_a_timestamped_log(env, tmp_path):
    add_pending_row(env)
    marker = tmp_path / "worker_ran"
    fake = write_fake_worker(tmp_path, f'echo "cwd=$(pwd)" > {marker}\nexit 0\n')
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}

    proc = run(env)

    assert proc.returncode == 0
    assert marker.exists()
    assert marker.read_text().strip() == f"cwd={env['WA_AGENT_NOTE_REPO_DIR']}"
    log = open(os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "worker.log"), encoding="utf-8").read()
    assert "tick start (queue count=1)" in log and "tick end rc=0" in log
    assert log.count("[20") >= 2   # two ISO-ish timestamp brackets, start and end


def test_a_queue_with_only_an_undelivered_completion_note_still_invokes_the_worker(env, tmp_path):
    """Round-1 review, nonblocking test GAP found by mutation (not itself a bug -- the review confirmed
    the SQL as written is 'a correct superset of open_agent_notes()'): narrowing the shell prefilter to
    ``where status='pending'`` survived all 21 pre-existing cron tests, because none of them seeds a
    queue whose only open row is a finished-but-undelivered completion (done/blocked, notified_at null)
    with no pending/in_progress row alongside it. AC#9/item B (independent completion retries) depends
    on this branch of the superset actually being COUNTED here, not merely present in the SQL text --
    otherwise a tick with only a stuck completion to retry would never even start python."""
    conn = sqlite3.connect(env["WA_SQLITE_PATH"])
    conn.execute("insert into wa_agent_notes (id, status, notified_at) values (1, 'done', null)")
    conn.commit()
    conn.close()
    marker = tmp_path / "worker_ran"
    fake = write_fake_worker(tmp_path, f"touch {marker}\nexit 0\n")
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}

    proc = run(env)

    assert proc.returncode == 0
    assert marker.exists()
    log = open(os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "worker.log"), encoding="utf-8").read()
    assert "tick start (queue count=1)" in log


def test_a_queue_with_only_an_in_progress_note_still_invokes_the_worker(env, tmp_path):
    """Round-1 review, nonblocking test gap: same gap as above for the OTHER half of the superset -- an
    in_progress row with no pending row alongside it (the review's own 'stale in_progress branch').
    The shell prefilter has no claimed_at/staleness column to check at all (that refinement is
    store.py's own job, python-side, once the worker is already running) -- at THIS level, status
    'in_progress' alone must already be enough to count as open work worth starting python for."""
    conn = sqlite3.connect(env["WA_SQLITE_PATH"])
    conn.execute("insert into wa_agent_notes (id, status, notified_at) values (1, 'in_progress', null)")
    conn.commit()
    conn.close()
    marker = tmp_path / "worker_ran"
    fake = write_fake_worker(tmp_path, f"touch {marker}\nexit 0\n")
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}

    proc = run(env)

    assert proc.returncode == 0
    assert marker.exists()
    log = open(os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "worker.log"), encoding="utf-8").read()
    assert "tick start (queue count=1)" in log


def test_the_workers_own_exit_code_is_now_propagated_by_the_wrapper(env, tmp_path):
    """TASK-303 item C, round-1 review, fixed as a plain bug: the wrapper used to always exit 0
    regardless of the worker's own exit code (the shell group's own exit status was always its LAST
    command's, the closing echo). Cron mail and a human both need the real signal."""
    add_pending_row(env)
    fake = write_fake_worker(tmp_path, "exit 7\n")
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}
    proc = run(env)
    assert proc.returncode == 7
    log = open(os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "worker.log"), encoding="utf-8").read()
    assert "tick end rc=7" in log


# --- crash visibility: a worker that exits non-zero without writing its own health (item C) ------

def test_a_worker_that_crashes_without_writing_health_gets_a_wrapper_fallback(env, tmp_path):
    """The exact scenario the round-1 review reproduced: an import-time crash (a malformed .env line,
    say) exits non-zero having never reached write_health() at all. The wrapper must not leave the
    PRIOR tick's stale health.json in place (which the next tick's own prefilter could see as "nothing
    open" and silently report ok:true straight over the crash)."""
    add_pending_row(env)
    fake = write_fake_worker(tmp_path, "exit 3\n")   # writes nothing to health.json
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}
    proc = run(env)
    assert proc.returncode == 3
    h = read_health(env)
    assert h["ok"] is False
    assert "rc=3" in h["problem"] and "without a health write" in h["problem"]


def test_a_worker_that_did_write_its_own_health_is_never_overwritten_by_the_fallback(env, tmp_path):
    body = ('mkdir -p "$WA_AGENT_NOTE_STATE_DIR"\n'
           'printf \'{"ok": false, "at": "2026-09-25T10:00:00+00:00", "problem": "decode failed: boom", '
           '"note_id": 1, "card_id": null, "completion_retries": [], "orphaned_in_progress": []}\' '
           '> "$WA_AGENT_NOTE_STATE_DIR/health.json"\nexit 1\n')
    add_pending_row(env)
    fake = write_fake_worker(tmp_path, body)
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}
    proc = run(env)
    assert proc.returncode == 1
    h = read_health(env)
    assert h["problem"] == "decode failed: boom"   # the worker's own, specific message survives


def test_a_clean_rc0_tick_that_writes_no_new_health_restores_the_prior_one(env, tmp_path):
    """A worker that quietly lost a claim race (rc=0, writes nothing new -- a legitimate, non-crash
    outcome) must not erase the PREVIOUS tick's own health.json."""
    add_pending_row(env)
    state_dir = pathlib.Path(env["WA_AGENT_NOTE_STATE_DIR"])
    state_dir.mkdir(exist_ok=True)
    prior = ('{"ok": true, "at": "2026-09-25T09:00:00+00:00", "problem": null, "note_id": null, '
            '"card_id": null, "completion_retries": [], "orphaned_in_progress": []}')
    (state_dir / "health.json").write_text(prior, encoding="utf-8")
    fake = write_fake_worker(tmp_path, "exit 0\n")   # writes nothing new
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}
    proc = run(env)
    assert proc.returncode == 0
    assert read_health(env)["at"] == "2026-09-25T09:00:00+00:00"


# --- NO log truncation (item E) -----------------------------------------------------------------

def test_the_log_is_never_trimmed_regardless_of_size(env, tmp_path):
    """TASK-303 item E, Ivan 2026-09-25 (CLAUDE.md 'no safety nets' -- growth is negligible, per the
    card's own decisions): the wrapper used to silently discard everything before a trim point once
    worker.log passed a size ceiling. That ceiling and the trimming code are both gone."""
    add_pending_row(env)
    state_dir = pathlib.Path(env["WA_AGENT_NOTE_STATE_DIR"])
    state_dir.mkdir(exist_ok=True)
    (state_dir / "worker.log").write_text("x" * 20000, encoding="utf-8")
    fake = write_fake_worker(tmp_path, "exit 0\n")
    env = {**env, "WA_AGENT_NOTE_WORKER_CMD": str(fake)}

    run(env)

    size = os.path.getsize(state_dir / "worker.log")
    assert size >= 20000, "the pre-existing 20000-byte log must survive untrimmed"
    assert "x" * 20000 in (state_dir / "worker.log").read_text(encoding="utf-8")


def test_an_idle_tick_outside_the_window_writes_nothing_to_the_log(env, tmp_path):
    """TASK-303 item E: 'idle ticks (outside the window, empty queue) write nothing to the log.'"""
    env = {**env, "WA_AGENT_NOTE_TEST_HOUR": "23"}
    run(env)
    assert not os.path.exists(os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "worker.log"))


def test_an_idle_tick_with_an_empty_queue_writes_nothing_to_the_log(env, tmp_path):
    run(env)   # default env: inside the window, empty queue
    assert not os.path.exists(os.path.join(env["WA_AGENT_NOTE_STATE_DIR"], "worker.log"))
