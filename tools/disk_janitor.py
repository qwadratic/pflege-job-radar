#!/usr/bin/env python3
"""tools/disk_janitor.py -- TASK-315 AC#10: VPS disk janitor.

WHY THIS EXISTS. 2026-09-29: this VPS's root disk filled to 100% (freed, at
the time, by hand-moving 600+ MB of subagent session transcripts under
~/.claude/projects/ to the Mac mini). Nothing watches for that happening
again -- this script is that watch, run periodically from cron
(disk_janitor_cron.sh) so the box never has to hit 100% before a human
notices.

Ivan's standing rule (see memory note "Free disk by moving to the Mac
mini"): when something has to go, MOVE it to the mini and leave a note,
never silent deletion. The one exception is pure, trivially-regenerable
caches (pip wheels, a headless-chrome cache dir, pytest tmp dirs, the
claude-cli's own node cache) -- those may be deleted outright, but even a
deletion gets a note, in ~/.tmp/DISK-NOTES.txt.

WHAT IT DOES, per run:
  1. Always: reports the sizes of /var/log/syslog*, /var/log/asterisk and
     /opt -- report only, never touched. That's the colleague's
     infrastructure (see memory note "Colleague owns clinic services");
     this script only ever *reads* stat() sizes there, nothing else.
  2. If free space on / is >= --min-free-mb (default 3000): stop there.
     Sizes are still reported in the log line; nothing is deleted or
     moved. This is the common case on every tick.
  3. If free space is below the threshold:
     a. Delete the pure-cache directories listed in CACHE_RELATIVE_DIRS,
        under $HOME, if present. Note every deletion.
     b. Move to the mini: files inside any */subagents/ directory under
        ~/.claude/projects/ that are older than SUBAGENT_STALE_DAYS (3
        days), and whole top-level session entries (a `<uuid>.jsonl` file
        or a `<uuid>/` directory, directly under a project dir) that have
        gone untouched for SESSION_STALE_DAYS (14+ days). A project dir
        whose name contains "luna-sessions", anything under a project's
        own memory/ subdir, and anything touched in the last
        SUBAGENT_STALE_DAYS days are never candidates. "Untouched" for a
        directory means the newest mtime of any file inside it -- one
        recently-touched file inside is enough to keep the whole entry.
     c. Move to the mini: old Claude Code binaries under
        ~/.local/share/claude/versions/ that are neither the newest (by
        mtime) nor currently in use by any running process (cross-checked
        against every /proc/*/exe). Verified by sha256 on both sides
        before the local copy is removed.

SAFETY. This script never touches /opt, /var/log, journald, this repo's
own data/ directory, its .venv, or anything outside the paths named
above -- see CLAUDE.md's "No safety nets": nothing here is an invented
cap, every threshold above is the one Ivan specified, and a move that
fails (bad ssh, rsync error, sha256 mismatch) leaves the local copy
exactly where it was and is recorded as a problem in health.json, never
silently swallowed.

TESTABILITY. All real-world side effects go through injectable seams on
JanitorConfig: `runner` (the subprocess.run-shaped callable used for every
ssh/rsync invocation -- tests fake it and never shell out for real),
`disk_free_mb_fn` (queries free space on --root-path), `now_fn` (the
clock), and `running_exes_fn` (the /proc/*/exe scan for the version
mover). `run_janitor(cfg)` is the pure orchestration function tests call
directly; `main()` is only the CLI/argv/health-file/log-file wrapper
around it.

Usage:
  python tools/disk_janitor.py [--min-free-mb N] [--apply]
                                [--home DIR] [--remote-host HOST]
                                [--backup-root PATH]

Dry run by default (no --apply): scans and reports what it WOULD do
(`skipped` in the result/health.json) but deletes nothing, moves nothing,
writes no note file, and never invokes the runner (no ssh/rsync calls at
all). Pass --apply to actually act.
"""
from __future__ import annotations

import argparse
import glob as globmod
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Constants (Ivan's explicit rules from TASK-315 AC#10 -- not invented caps)
# ---------------------------------------------------------------------------

DEFAULT_MIN_FREE_MB = 3000
DEFAULT_REMOTE_HOST = "macmini"
DEFAULT_BACKUP_ROOT = "vps-backup"
SUBAGENT_STALE_DAYS = 3
SESSION_STALE_DAYS = 14
LUNA_EXCLUDE_SUBSTRING = "luna-sessions"
MEMORY_DIR_NAME = "memory"
SUBAGENTS_DIR_NAME = "subagents"

# Pure caches: trivially regenerated, safe to delete outright (still noted).
CACHE_RELATIVE_DIRS = (
    ".tmp/pytest-of-claude",
    ".cache/claude-cli-nodejs",
    ".cache/google-chrome-headless",
    ".cache/pip",
)


def _default_runner(cmd, **kwargs):  # pragma: no cover -- real ssh/rsync, never exercised in tests
    return subprocess.run(cmd, **kwargs)


def _default_disk_free_mb(path: str) -> int:  # pragma: no cover -- real disk, tests inject their own
    usage = shutil.disk_usage(path)
    return usage.free // (1024 * 1024)


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_running_exes() -> Set[str]:  # pragma: no cover -- real /proc, tests inject their own
    targets: Set[str] = set()
    proc = Path("/proc")
    if not proc.exists():
        return targets
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            targets.add(os.readlink(entry / "exe"))
        except OSError:
            continue
    return targets


@dataclass
class JanitorConfig:
    """All paths, thresholds and side-effect seams for one run.

    Every path below defaults to the real production location but can be
    pointed at a tmp dir by a test; every side effect (shelling out,
    reading free disk space, reading the clock, listing running
    processes) goes through one of the *_fn/runner callables so tests
    never touch the real host.
    """

    home: Path
    min_free_mb: int = DEFAULT_MIN_FREE_MB
    apply: bool = False
    root_path: str = "/"
    remote_host: str = DEFAULT_REMOTE_HOST
    backup_root: str = DEFAULT_BACKUP_ROOT

    claude_projects_dir: Optional[Path] = None
    claude_versions_dir: Optional[Path] = None
    state_dir: Optional[Path] = None

    report_syslog_glob: str = "/var/log/syslog*"
    report_asterisk_dir: Path = field(default_factory=lambda: Path("/var/log/asterisk"))
    report_opt_dir: Path = field(default_factory=lambda: Path("/opt"))

    runner: Callable = field(default=_default_runner)
    disk_free_mb_fn: Callable[[str], int] = field(default=_default_disk_free_mb)
    now_fn: Callable[[], datetime] = field(default=_default_now)
    running_exes_fn: Callable[[], Set[str]] = field(default=_default_running_exes)

    def __post_init__(self) -> None:
        if self.claude_projects_dir is None:
            self.claude_projects_dir = self.home / ".claude" / "projects"
        if self.claude_versions_dir is None:
            self.claude_versions_dir = self.home / ".local" / "share" / "claude" / "versions"
        if self.state_dir is None:
            self.state_dir = self.home / ".local" / "state" / "disk-janitor"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _older_than(mtime_epoch: float, now: datetime, days: int) -> bool:
    return (now.timestamp() - mtime_epoch) >= days * 86400


def _path_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        try:
            return path.stat().st_size / (1024 * 1024)
        except OSError:
            return 0.0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += (Path(dirpath) / fn).stat().st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def _glob_total_mb(pattern: str) -> float:
    total = 0.0
    for fp in globmod.glob(pattern):
        total += _path_size_mb(Path(fp))
    return total


# ---------------------------------------------------------------------------
# 1. pure caches (delete)
# ---------------------------------------------------------------------------


def _find_cache_targets(cfg: JanitorConfig) -> List[Path]:
    return [cfg.home / rel for rel in CACHE_RELATIVE_DIRS if (cfg.home / rel).exists()]


def _delete_caches(cfg: JanitorConfig, targets: List[Path], notes: List[str]) -> float:
    deleted_mb = 0.0
    for t in targets:
        size_mb = _path_size_mb(t)
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink()
        deleted_mb += size_mb
        notes.append(f"[{_iso(cfg.now_fn())}] DELETE cache {t} ({size_mb:.1f} MB) -- pure cache, safe to regenerate")
    return deleted_mb


# ---------------------------------------------------------------------------
# 2. ~/.claude/projects/ transcripts (move)
# ---------------------------------------------------------------------------


def _iter_project_dirs(cfg: JanitorConfig):
    if not cfg.claude_projects_dir.exists():
        return
    for entry in sorted(cfg.claude_projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        if LUNA_EXCLUDE_SUBSTRING in entry.name:
            continue
        yield entry


def _newest_mtime(path: Path) -> Optional[float]:
    if path.is_file():
        try:
            return path.stat().st_mtime
        except OSError:
            return None
    newest: Optional[float] = None
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                m = (Path(dirpath) / fn).stat().st_mtime
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    if newest is None:
        try:
            newest = path.stat().st_mtime
        except OSError:
            return None
    return newest


def _find_stale_subagent_files(cfg: JanitorConfig) -> List[Path]:
    """Files inside any */subagents/ dir, older than SUBAGENT_STALE_DAYS.

    memory/ is never descended into (checked by path membership, not just
    by name, so a stray directory named "memory" elsewhere is not
    special-cased by accident).
    """
    now = cfg.now_fn()
    out: List[Path] = []
    for project_dir in _iter_project_dirs(cfg):
        memory_dir = project_dir / MEMORY_DIR_NAME
        for subagents_dir in sorted(project_dir.rglob(SUBAGENTS_DIR_NAME)):
            if not subagents_dir.is_dir():
                continue
            try:
                subagents_dir.relative_to(memory_dir)
                continue  # lives inside memory/ -- excluded
            except ValueError:
                pass
            for fp in sorted(p for p in subagents_dir.rglob("*") if p.is_file()):
                try:
                    mtime = fp.stat().st_mtime
                except OSError:
                    continue
                if _older_than(mtime, now, SUBAGENT_STALE_DAYS):
                    out.append(fp)
    return out


def _find_stale_session_entries(cfg: JanitorConfig) -> List[Path]:
    """Whole top-level session files/dirs untouched for SESSION_STALE_DAYS+.

    "Top-level" = a direct child of a project dir, other than its own
    memory/ or subagents/ subdirs.
    """
    now = cfg.now_fn()
    out: List[Path] = []
    for project_dir in _iter_project_dirs(cfg):
        for entry in sorted(project_dir.iterdir()):
            if entry.name in (MEMORY_DIR_NAME, SUBAGENTS_DIR_NAME):
                continue
            newest = _newest_mtime(entry)
            if newest is None:
                continue
            if _older_than(newest, now, SESSION_STALE_DAYS):
                out.append(entry)
    return out


def _prune_empty_dirs(path: Path) -> None:
    if not path.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(path, topdown=False):
        d = Path(dirpath)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _move_to_mini(cfg: JanitorConfig, local_path: Path, notes: List[str]) -> Tuple[bool, float, Optional[str]]:
    """rsync one file or directory (whole) to
    macmini:<backup_root>/claude-transcripts/<path relative to
    ~/.claude/projects>, then prune any now-empty local directory husk.

    A failure at either the ssh mkdir or the rsync step leaves the local
    copy exactly where it was -- nothing is removed until rsync itself
    reports success (--remove-source-files removes source FILES on
    success; a source directory's now-empty husk is pruned separately by
    _prune_empty_dirs, never removed on failure).
    """
    size_mb = _path_size_mb(local_path)
    rel = local_path.relative_to(cfg.claude_projects_dir)
    remote_dir = f"{cfg.backup_root}/claude-transcripts/{rel.parent}".rstrip("/.")
    if str(rel.parent) == ".":
        remote_dir = f"{cfg.backup_root}/claude-transcripts"

    mkdir_res = cfg.runner(["ssh", cfg.remote_host, "mkdir", "-p", remote_dir], capture_output=True, text=True)
    if mkdir_res.returncode != 0:
        return False, size_mb, f"ssh mkdir -p {remote_dir} failed: {(mkdir_res.stderr or '').strip()}"

    rsync_res = cfg.runner(
        ["rsync", "-a", "--checksum", "--remove-source-files", str(local_path), f"{cfg.remote_host}:{remote_dir}/"],
        capture_output=True,
        text=True,
    )
    if rsync_res.returncode != 0:
        return False, size_mb, f"rsync {local_path} failed: {(rsync_res.stderr or '').strip()}"

    if local_path.is_dir():
        _prune_empty_dirs(local_path)

    notes.append(f"[{_iso(cfg.now_fn())}] MOVE {local_path} -> {cfg.remote_host}:{remote_dir}/ ({size_mb:.1f} MB)")
    return True, size_mb, None


# ---------------------------------------------------------------------------
# 3. old Claude Code binaries (move, sha256-verified)
# ---------------------------------------------------------------------------


def _list_version_entries(cfg: JanitorConfig) -> List[Path]:
    if not cfg.claude_versions_dir.exists():
        return []
    return sorted(cfg.claude_versions_dir.iterdir())


def _is_in_use(entry: Path, running_exes: Set[str]) -> bool:
    entry_resolved = str(entry.resolve()).rstrip("/")
    for exe in running_exes:
        if exe == entry_resolved or exe.startswith(entry_resolved + "/"):
            return True
    return False


def _find_old_versions(cfg: JanitorConfig) -> List[Path]:
    entries = _list_version_entries(cfg)
    if len(entries) <= 1:
        return []
    newest = max(entries, key=lambda p: p.stat().st_mtime)
    running = cfg.running_exes_fn()
    return [e for e in entries if e != newest and not _is_in_use(e, running)]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_file_list(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file())


def _move_version_to_mini(cfg: JanitorConfig, entry: Path, notes: List[str]) -> Tuple[bool, float, Optional[str]]:
    """Copy (never --remove-source-files here -- verify first, remove
    after), sha256 every file on both sides, only THEN remove the local
    copy. A mismatch or any failed step leaves the local copy in place.
    """
    size_mb = _path_size_mb(entry)
    remote_dir = f"{cfg.backup_root}/claude-code-versions"

    mkdir_res = cfg.runner(["ssh", cfg.remote_host, "mkdir", "-p", remote_dir], capture_output=True, text=True)
    if mkdir_res.returncode != 0:
        return False, size_mb, f"ssh mkdir -p {remote_dir} failed: {(mkdir_res.stderr or '').strip()}"

    rsync_res = cfg.runner(
        ["rsync", "-a", "--checksum", str(entry), f"{cfg.remote_host}:{remote_dir}/"],
        capture_output=True,
        text=True,
    )
    if rsync_res.returncode != 0:
        return False, size_mb, f"rsync {entry} failed: {(rsync_res.stderr or '').strip()}"

    for lf in _local_file_list(entry):
        local_hash = _sha256_file(lf)
        rel = lf.relative_to(entry.parent)
        remote_path = f"{remote_dir}/{rel}"
        hash_res = cfg.runner(["ssh", cfg.remote_host, "sha256sum", remote_path], capture_output=True, text=True)
        if hash_res.returncode != 0 or not (hash_res.stdout or "").strip():
            return False, size_mb, f"remote sha256 unavailable for {remote_path}"
        remote_hash = hash_res.stdout.split()[0]
        if remote_hash != local_hash:
            return False, size_mb, f"sha256 mismatch for {lf.name}: local {local_hash} != remote {remote_hash}"

    if entry.is_dir():
        shutil.rmtree(entry)
    else:
        entry.unlink()

    notes.append(
        f"[{_iso(cfg.now_fn())}] MOVE version {entry.name} -> {cfg.remote_host}:{remote_dir}/ "
        f"({size_mb:.1f} MB, sha256-verified)"
    )
    return True, size_mb, None


# ---------------------------------------------------------------------------
# note files
# ---------------------------------------------------------------------------


def _append_lines(path: Path, lines: List[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for line in lines:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run_janitor(cfg: JanitorConfig) -> dict:
    """Pure(-ish) orchestration: every side effect goes through cfg's
    injectable seams, so this is what tests call directly.
    """
    now = cfg.now_fn()
    free_before = cfg.disk_free_mb_fn(cfg.root_path)

    report_only = {
        "var_log_syslog_mb": round(_glob_total_mb(cfg.report_syslog_glob), 1),
        "var_log_asterisk_mb": round(_path_size_mb(cfg.report_asterisk_dir), 1),
        "opt_mb": round(_path_size_mb(cfg.report_opt_dir), 1),
    }

    result = {
        "ok": True,
        "at": _iso(now),
        "apply": cfg.apply,
        "free_mb_before": free_before,
        "free_mb_after": free_before,
        "deleted_mb": 0.0,
        "moved_mb": 0.0,
        "moved_files": 0,
        "skipped": [],
        "problem": None,
        "report_only": report_only,
    }

    if free_before >= cfg.min_free_mb:
        return result  # above threshold: sizes reported above, nothing else happens.

    disk_notes: List[str] = []
    moved_notes: List[str] = []
    problems: List[str] = []

    cache_targets = _find_cache_targets(cfg)
    if cfg.apply:
        result["deleted_mb"] += _delete_caches(cfg, cache_targets, disk_notes)
    else:
        result["skipped"].extend(f"cache:{t}" for t in cache_targets)

    for fp in _find_stale_subagent_files(cfg):
        if not cfg.apply:
            result["skipped"].append(f"subagent:{fp}")
            continue
        ok, size_mb, problem = _move_to_mini(cfg, fp, moved_notes)
        if ok:
            result["moved_mb"] += size_mb
            result["moved_files"] += 1
        else:
            problems.append(problem)

    for entry in _find_stale_session_entries(cfg):
        if not cfg.apply:
            result["skipped"].append(f"session:{entry}")
            continue
        ok, size_mb, problem = _move_to_mini(cfg, entry, moved_notes)
        if ok:
            result["moved_mb"] += size_mb
            result["moved_files"] += 1
        else:
            problems.append(problem)

    for entry in _find_old_versions(cfg):
        if not cfg.apply:
            result["skipped"].append(f"version:{entry}")
            continue
        ok, size_mb, problem = _move_version_to_mini(cfg, entry, moved_notes)
        if ok:
            result["moved_mb"] += size_mb
            result["moved_files"] += 1
        else:
            problems.append(problem)

    if cfg.apply:
        _append_lines(cfg.home / ".tmp" / "DISK-NOTES.txt", disk_notes)
        _append_lines(cfg.claude_projects_dir / "MOVED-TO-MACMINI.txt", moved_notes)
        result["free_mb_after"] = cfg.disk_free_mb_fn(cfg.root_path)

    if problems:
        result["ok"] = False
        result["problem"] = "; ".join(problems)

    result["deleted_mb"] = round(result["deleted_mb"], 1)
    result["moved_mb"] = round(result["moved_mb"], 1)
    return result


# ---------------------------------------------------------------------------
# CLI / health / log
# ---------------------------------------------------------------------------


def _write_health(cfg: JanitorConfig, result: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    health = {
        "ok": result["ok"],
        "at": result["at"],
        "free_mb_before": result["free_mb_before"],
        "free_mb_after": result["free_mb_after"],
        "deleted_mb": result["deleted_mb"],
        "moved_mb": result["moved_mb"],
        "moved_files": result["moved_files"],
        "skipped": result["skipped"],
        "problem": result["problem"],
    }
    tmp = cfg.state_dir / "health.json.tmp"
    final = cfg.state_dir / "health.json"
    tmp.write_text(json.dumps(health, indent=2, sort_keys=True) + "\n")
    tmp.replace(final)


def _log_line(result: dict) -> str:
    ro = result["report_only"]
    return (
        f"[{result['at']}] apply={result['apply']} ok={result['ok']} "
        f"free_before={result['free_mb_before']}MB free_after={result['free_mb_after']}MB "
        f"deleted={result['deleted_mb']}MB moved={result['moved_mb']}MB moved_files={result['moved_files']} "
        f"skipped={len(result['skipped'])} "
        f"report_only(syslog={ro['var_log_syslog_mb']}MB asterisk={ro['var_log_asterisk_mb']}MB opt={ro['opt_mb']}MB) "
        f"problem={result['problem']}"
    )


def _append_log(cfg: JanitorConfig, line: str) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "janitor.log", "a") as f:
        f.write(line + "\n")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VPS disk janitor (TASK-315 AC#10). Dry run by default.")
    p.add_argument("--min-free-mb", type=int, default=DEFAULT_MIN_FREE_MB)
    p.add_argument("--apply", action="store_true", help="Actually delete/move. Default is dry run.")
    p.add_argument("--home", default=None, help="Override $HOME (mainly for manual testing).")
    p.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    p.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, cfg: Optional[JanitorConfig] = None) -> int:
    if cfg is None:
        args = parse_args(argv)
        home = Path(args.home) if args.home else Path(os.environ.get("HOME", str(Path.home())))
        cfg = JanitorConfig(
            home=home,
            min_free_mb=args.min_free_mb,
            apply=args.apply,
            remote_host=args.remote_host,
            backup_root=args.backup_root,
        )
    result = run_janitor(cfg)
    _write_health(cfg, result)
    _append_log(cfg, _log_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
