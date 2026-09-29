"""tools/disk_janitor.py (TASK-315 AC#10): VPS disk janitor.

Every real-world side effect the module can perform goes through an
injectable seam on JanitorConfig (`runner` for every ssh/rsync call,
`disk_free_mb_fn`, `now_fn`, `running_exes_fn`) -- these tests build a
JanitorConfig pointed entirely at tmp_path and a FakeRunner that never
shells out, and call run_janitor(cfg) directly (the pure orchestration
function `main()` is only a thin CLI/health/log wrapper around). Nothing
here ever touches the real $HOME, ~/.claude/projects, the real disk-free
reading, or a real ssh/rsync binary.
"""
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.disk_janitor as DJ

DAY = 86400


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Records every command it was called with; never shells out. Scripted
    per-command-prefix failures let a test make exactly one ssh/rsync step
    fail without touching the filesystem.
    """

    def __init__(self):
        self.calls = []
        self.fail_prefixes = []  # list of (argv_prefix_tuple, FakeCompletedProcess)
        self.sha256_by_remote_path = {}  # remote path -> hash string, for "ssh ... sha256sum <path>"

    def fail_on(self, prefix, result):
        self.fail_prefixes.append((tuple(prefix), result))

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        for prefix, result in self.fail_prefixes:
            if tuple(cmd[: len(prefix)]) == prefix:
                return result
        if len(cmd) >= 3 and cmd[0] == "ssh" and cmd[2] == "sha256sum":
            remote_path = cmd[3]
            h = self.sha256_by_remote_path.get(remote_path)
            if h is None:
                return FakeCompletedProcess(returncode=1, stderr="no such file")
            return FakeCompletedProcess(returncode=0, stdout=f"{h}  {remote_path}\n")
        if cmd[0] == "rsync" and "--remove-source-files" in cmd:
            # Stand in for what a REAL rsync --remove-source-files would do to the
            # local side (never touches "the remote" -- there is none here): remove
            # every FILE under the source, exactly like real rsync, leaving any
            # now-empty directory husk for the module's own _prune_empty_dirs to
            # clean up. This is the one place the fake needs to touch the
            # filesystem at all, so that "moved" vs. "left in place" is a real,
            # observable difference in a test -- everything else about rsync
            # (the actual network copy) is irrelevant to what disk_janitor.py
            # decides to do next.
            src = Path(cmd[-2])
            if src.is_file():
                src.unlink()
            elif src.is_dir():
                for p in sorted(src.rglob("*"), key=lambda x: -len(x.parts)):
                    if p.is_file():
                        p.unlink()
        return FakeCompletedProcess(returncode=0, stdout="", stderr="")


def _set_mtime(path: Path, days_ago: float):
    t = time.time() - days_ago * DAY
    import os

    os.utime(path, (t, t))


def make_cfg(tmp_path, **overrides):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    runner = overrides.pop("runner", None) or FakeRunner()
    now = overrides.pop("now", None)
    now_fn = (lambda: now) if now is not None else (lambda: datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc))
    free_mb = overrides.pop("free_mb", 1000)
    kwargs = dict(
        home=home,
        runner=runner,
        disk_free_mb_fn=lambda path: free_mb,
        now_fn=now_fn,
        running_exes_fn=lambda: set(),
        report_syslog_glob=str(tmp_path / "nope-syslog*"),
        report_asterisk_dir=tmp_path / "nope-asterisk",
        report_opt_dir=tmp_path / "nope-opt",
    )
    kwargs.update(overrides)
    return DJ.JanitorConfig(**kwargs), runner


# ---------------------------------------------------------------------------
# 1. dry run touches nothing
# ---------------------------------------------------------------------------


def test_dry_run_touches_nothing(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=False, min_free_mb=3000)
    cache_dir = cfg.home / ".cache" / "pip"
    cache_dir.mkdir(parents=True)
    (cache_dir / "wheel.whl").write_bytes(b"x" * 1000)

    project = cfg.claude_projects_dir / "myproj"
    (project / "subagents").mkdir(parents=True)
    old_sub = project / "subagents" / "run1.jsonl"
    old_sub.write_text("{}")
    _set_mtime(old_sub, days_ago=10)

    old_session = project / "abc-uuid.jsonl"
    old_session.write_text("{}")
    _set_mtime(old_session, days_ago=30)

    versions_dir = cfg.claude_versions_dir
    versions_dir.mkdir(parents=True)
    old_v = versions_dir / "1.0.0"
    old_v.mkdir()
    (old_v / "claude").write_text("old binary")
    _set_mtime(old_v / "claude", days_ago=60)
    new_v = versions_dir / "2.0.0"
    new_v.mkdir()
    (new_v / "claude").write_text("new binary")

    before_snapshot = {p: p.stat().st_mtime for p in tmp_path.rglob("*")}

    result = DJ.run_janitor(cfg)

    assert result["deleted_mb"] == 0
    assert result["moved_mb"] == 0
    assert result["moved_files"] == 0
    assert runner.calls == []  # no ssh/rsync ever invoked in dry run
    assert cache_dir.exists()
    assert old_sub.exists()
    assert old_session.exists()
    assert old_v.exists()
    assert not (cfg.home / ".tmp" / "DISK-NOTES.txt").exists()
    assert not (cfg.claude_projects_dir / "MOVED-TO-MACMINI.txt").exists()
    after_snapshot = {p: p.stat().st_mtime for p in tmp_path.rglob("*")}
    assert before_snapshot == after_snapshot
    # it still reports what it WOULD have done, for visibility
    assert any(s.startswith("cache:") for s in result["skipped"])
    assert any(s.startswith("subagent:") for s in result["skipped"])
    assert any(s.startswith("session:") for s in result["skipped"])
    assert any(s.startswith("version:") for s in result["skipped"])


# ---------------------------------------------------------------------------
# 2. above threshold does nothing
# ---------------------------------------------------------------------------


def test_above_threshold_does_nothing(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=5000, apply=True, min_free_mb=3000)
    cache_dir = cfg.home / ".cache" / "pip"
    cache_dir.mkdir(parents=True)
    (cache_dir / "wheel.whl").write_bytes(b"x" * 1000)

    result = DJ.run_janitor(cfg)

    assert result["ok"] is True
    assert result["deleted_mb"] == 0
    assert result["moved_mb"] == 0
    assert result["moved_files"] == 0
    assert result["skipped"] == []
    assert runner.calls == []
    assert cache_dir.exists()  # even though apply=True, threshold was never crossed
    assert "report_only" in result  # sizes are still reported
    assert not (cfg.home / ".tmp" / "DISK-NOTES.txt").exists()


# ---------------------------------------------------------------------------
# 3. caches deleted + noted
# ---------------------------------------------------------------------------


def test_caches_deleted_and_noted(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)
    pip_cache = cfg.home / ".cache" / "pip"
    pip_cache.mkdir(parents=True)
    (pip_cache / "wheel.whl").write_bytes(b"x" * (2 * 1024 * 1024))
    pytest_cache = cfg.home / ".tmp" / "pytest-of-claude"
    pytest_cache.mkdir(parents=True)
    (pytest_cache / "junk").write_bytes(b"x" * (1024 * 1024))
    # a cache NOT in the list must be left alone
    other = cfg.home / ".cache" / "not-a-janitor-target"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("keep")

    result = DJ.run_janitor(cfg)

    assert not pip_cache.exists()
    assert not pytest_cache.exists()
    assert other.exists()
    assert result["deleted_mb"] > 0
    notes = (cfg.home / ".tmp" / "DISK-NOTES.txt").read_text()
    assert "DELETE cache" in notes
    assert str(pip_cache) in notes
    assert str(pytest_cache) in notes


# ---------------------------------------------------------------------------
# 4. old subagent files moved; recent, luna-sessions, memory untouched
# ---------------------------------------------------------------------------


def test_stale_subagent_files_moved_recent_and_excluded_untouched(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)

    project = cfg.claude_projects_dir / "myproj"
    (project / "subagents").mkdir(parents=True)
    stale = project / "subagents" / "old-run.jsonl"
    stale.write_text("{}")
    _set_mtime(stale, days_ago=10)

    recent = project / "subagents" / "new-run.jsonl"
    recent.write_text("{}")
    _set_mtime(recent, days_ago=1)

    luna_project = cfg.claude_projects_dir / "myproj-luna-sessions"
    (luna_project / "subagents").mkdir(parents=True)
    luna_stale = luna_project / "subagents" / "old-run.jsonl"
    luna_stale.write_text("{}")
    _set_mtime(luna_stale, days_ago=10)

    memory_stale = project / "memory" / "subagents" / "old-run.jsonl"
    memory_stale.parent.mkdir(parents=True)
    memory_stale.write_text("{}")
    _set_mtime(memory_stale, days_ago=10)

    result = DJ.run_janitor(cfg)

    assert not stale.exists()
    assert recent.exists()
    assert luna_stale.exists()
    assert memory_stale.exists()
    assert result["moved_files"] == 1
    assert result["ok"] is True

    notes = (cfg.claude_projects_dir / "MOVED-TO-MACMINI.txt").read_text()
    assert str(stale) in notes
    assert str(luna_stale) not in notes
    assert str(memory_stale) not in notes

    rsync_calls = [c for c in runner.calls if c[0] == "rsync"]
    assert len(rsync_calls) == 1
    assert rsync_calls[0][-1] == "macmini:vps-backup/claude-transcripts/myproj/subagents/"


def test_stale_session_entry_moved_whole(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)
    project = cfg.claude_projects_dir / "myproj"
    project.mkdir(parents=True)
    old_session = project / "abc-uuid.jsonl"
    old_session.write_text("{}")
    _set_mtime(old_session, days_ago=20)

    recent_session = project / "def-uuid.jsonl"
    recent_session.write_text("{}")
    _set_mtime(recent_session, days_ago=1)

    # a directory-shaped session, stale as a whole
    old_dir_session = project / "ghi-uuid"
    old_dir_session.mkdir()
    (old_dir_session / "attachment.bin").write_bytes(b"x" * 100)
    _set_mtime(old_dir_session / "attachment.bin", days_ago=20)
    _set_mtime(old_dir_session, days_ago=20)

    # a directory-shaped session with ONE recently touched file inside stays untouched as a whole
    mixed_dir_session = project / "jkl-uuid"
    mixed_dir_session.mkdir()
    old_file = mixed_dir_session / "old.bin"
    old_file.write_bytes(b"x" * 100)
    _set_mtime(old_file, days_ago=20)
    new_file = mixed_dir_session / "new.bin"
    new_file.write_bytes(b"y" * 10)
    _set_mtime(new_file, days_ago=1)

    result = DJ.run_janitor(cfg)

    assert not old_session.exists()
    assert recent_session.exists()
    assert not old_dir_session.exists()
    assert mixed_dir_session.exists()
    assert old_file.exists()
    assert new_file.exists()
    assert result["moved_files"] == 2  # old_session + old_dir_session


# ---------------------------------------------------------------------------
# 5. a running or newest claude-code version is never moved
# ---------------------------------------------------------------------------


def test_running_or_newest_version_never_moved(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)
    versions_dir = cfg.claude_versions_dir
    versions_dir.mkdir(parents=True)

    oldest = versions_dir / "1.0.0"
    oldest.mkdir()
    (oldest / "claude").write_text("v1")
    _set_mtime(oldest / "claude", days_ago=90)
    _set_mtime(oldest, days_ago=90)

    running = versions_dir / "1.5.0"
    running.mkdir()
    (running / "claude").write_text("v1.5")
    _set_mtime(running / "claude", days_ago=45)
    _set_mtime(running, days_ago=45)

    newest = versions_dir / "2.0.0"
    newest.mkdir()
    (newest / "claude").write_text("v2")
    # newest mtime left as "now"

    def running_exes():
        return {str((running / "claude").resolve())}

    cfg.running_exes_fn = running_exes
    runner.sha256_by_remote_path = {
        "vps-backup/claude-code-versions/1.0.0/claude": DJ._sha256_file(oldest / "claude"),
    }

    result = DJ.run_janitor(cfg)

    assert not oldest.exists()  # neither newest nor running -> moved
    assert running.exists()  # in use -> never moved
    assert newest.exists()  # newest -> never moved
    assert result["moved_files"] == 1
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# 6. rsync failure leaves source in place, health/result ok:false
# ---------------------------------------------------------------------------


def test_rsync_failure_leaves_source_in_place_and_reports_not_ok(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)
    project = cfg.claude_projects_dir / "myproj"
    (project / "subagents").mkdir(parents=True)
    stale = project / "subagents" / "old-run.jsonl"
    stale.write_text("{}")
    _set_mtime(stale, days_ago=10)

    runner.fail_on(["rsync"], FakeCompletedProcess(returncode=12, stderr="rsync: connection unexpectedly closed"))

    result = DJ.run_janitor(cfg)

    assert stale.exists()  # never removed -- rsync itself failed
    assert result["ok"] is False
    assert result["problem"] is not None
    assert "rsync" in result["problem"]
    assert result["moved_files"] == 0
    assert result["moved_mb"] == 0


def test_ssh_mkdir_failure_also_leaves_source_in_place(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)
    project = cfg.claude_projects_dir / "myproj"
    (project / "subagents").mkdir(parents=True)
    stale = project / "subagents" / "old-run.jsonl"
    stale.write_text("{}")
    _set_mtime(stale, days_ago=10)

    runner.fail_on(["ssh"], FakeCompletedProcess(returncode=255, stderr="ssh: connect to host macmini port 22: Connection refused"))

    result = DJ.run_janitor(cfg)

    assert stale.exists()
    assert result["ok"] is False
    assert "mkdir" in result["problem"]
    rsync_calls = [c for c in runner.calls if c[0] == "rsync"]
    assert rsync_calls == []  # never even attempted once mkdir failed


# ---------------------------------------------------------------------------
# health.json / log line via main()
# ---------------------------------------------------------------------------


def test_main_writes_health_and_log(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=5000, apply=False, min_free_mb=3000)
    rc = DJ.main(cfg=cfg)
    assert rc == 0
    health_path = cfg.state_dir / "health.json"
    log_path = cfg.state_dir / "janitor.log"
    assert health_path.exists()
    assert log_path.exists()
    import json

    health = json.loads(health_path.read_text())
    assert set(health.keys()) == {
        "ok",
        "at",
        "free_mb_before",
        "free_mb_after",
        "deleted_mb",
        "moved_mb",
        "moved_files",
        "skipped",
        "problem",
    }
    assert health["ok"] is True
    log_line = log_path.read_text().strip()
    assert "report_only(" in log_line


def test_version_sha256_mismatch_is_a_problem_and_keeps_local_copy(tmp_path):
    cfg, runner = make_cfg(tmp_path, free_mb=100, apply=True, min_free_mb=3000)
    versions_dir = cfg.claude_versions_dir
    versions_dir.mkdir(parents=True)
    oldest = versions_dir / "1.0.0"
    oldest.mkdir()
    (oldest / "claude").write_text("v1")
    _set_mtime(oldest / "claude", days_ago=90)
    _set_mtime(oldest, days_ago=90)
    newest = versions_dir / "2.0.0"
    newest.mkdir()
    (newest / "claude").write_text("v2")

    runner.sha256_by_remote_path = {"vps-backup/claude-code-versions/1.0.0/claude": "deadbeef" * 8}

    result = DJ.run_janitor(cfg)

    assert oldest.exists()
    assert result["ok"] is False
    assert "sha256 mismatch" in result["problem"]
