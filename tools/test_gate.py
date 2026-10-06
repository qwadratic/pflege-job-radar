#!/usr/bin/env python3
"""tools/test_gate.py -- versioned git-hook test gate (2026-10-05, Ivan: "тесты гонять норм, можно
прекомит хук"), plus `pre-deploy` and `nightly` CLI subcommands for the deploy pipeline and for the
LLM lane's own schedule (2026-10-05, Ivan: "run the LLM tests only before deploying, not on every
push"; 2026-10-06, Ivan: "избыточно раним LLM-тесты" -- see below, the LLM lane moved off pre-deploy
too).

Two hooks share this module: githooks/pre-commit (fast, skips when nothing staged can affect code)
and githooks/pre-push (the FAST push gate: tests the pushed commit itself in a scratch worktree,
never the working tree, offline WA lane only). Both are thin shims -- see githooks/pre-commit and
githooks/pre-push -- so the logic here is what gets versioned and tested (tests/test_test_gate.py).
install_githooks.sh points core.hooksPath at this checkout's githooks/ by absolute path, and the
shims exec whichever
tools/test_gate.py sits next to it -- so every linked worktree runs the SAME hooks file, and this
script is told which worktree's content to test via --worktree (the index for pre-commit, the pushed
sha's own scratch checkout for pre-push).

The LLM lane (Luna persona/e2e + CV classification, 73 tests, ~44 min of real Opus calls) does NOT
run on pre-push any more. Until 2026-10-05 it did, whenever the pushed range touched an LLM-relevant
path -- Ivan found that too slow for every push ("run the LLM tests only before deploying, not on
every push") and a failed 44-minute run's output was being thrown away with the scratch dir on top of
that, forcing a blind rerun. Both were fixed on 2026-10-05 by moving the LLM lane to a separate
`pre-deploy` subcommand, run by hand or by the deploy pipeline, never by a git hook, which then ran
the offline lane always and the LLM lane whenever deployed..target touched LLM_RELEVANT_PREFIXES (or
PFLEGE_GATE_LLM=1/0 overrode it).

2026-10-06 (Ivan): even gated on path relevance, the LLM lane on pre-deploy still ran too often --
most deploys touch *something* under app/wa/ or app/data.py, so in practice it fired on nearly every
one. Decision: `pre-deploy --target <sha>` now runs the OFFLINE lane ONLY, always -- a deploy no
longer waits on the LLM lane at all, and touching an LLM-relevant path no longer has any effect there.
PFLEGE_GATE_LLM=1 still forces the LLM lane there explicitly, for the rare case of wanting it inline
with a deploy; PFLEGE_GATE_LLM=0 is accepted too, for symmetry, though it changes nothing since
skipping is already the default. The LLM lane instead gets its OWN schedule: `tools/test_gate.py
nightly [--target REV]` (target defaults to origin/main after a git fetch), run once a day at the END
of the workday (18:00 Europe/Vienna, Mon-Fri) by tools/llm_lane_cron.sh -- never more than once a day,
and skipped outright for a sha that already has ANY recorded LLM-lane result (passed or failed; see
stamp handling below), so a sha nightly already judged is never re-judged for free -- `PFLEGE_GATE_LLM=1
tools/test_gate.py pre-deploy --target <sha>` re-judges one explicitly (see cmd_nightly's own comment).
`pre-deploy` prints, before its lanes run, one informational line naming the target sha's own LLM-lane
stamp (passed/FAILED-with-log-path/not judged) and the last nightly.tsv row -- informational only,
never a gate: a deploy never waits on the LLM lane either way (see cmd_pre_deploy).

Opus review, same date: once PFLEGE_GATE_LLM became the only lever pre-deploy reads, the 2026-10-05
path-relevance machinery (LLM_RELEVANT_PREFIXES, touches_llm_paths, llm_lane_decision) never drove a
decision again -- removed outright, this paragraph is now the only record that it ever existed. The
changed-path COUNT pre-deploy printed alongside it (determine_changed_paths, changed_paths_in_range,
commits_in_range, is_new_branch, and the `--deployed`/`--git_run` plumbing that fed them) was reader
information only, nothing downstream ever branched on it -- removed too (grep confirmed the only
caller was this module itself; the deploy runbook calls only `pre-deploy --target <sha>`, never
`--deployed`). `pre-deploy` keeps only `--target`.

Either command, on a lane failure, saves that lane's full stdout to
~/.local/state/pflege-gate/logs/<sha>-<lane>.log (never swept, unlike the /dev/shm scratch dirs below)
and prints the path, so a failed LLM lane's output survives to be read instead of re-spending the
~44 minutes just to see it again. `nightly` always saves that log, pass or fail (a passing run is
still worth re-reading without paying the ~44 minutes again) and always appends one summary line --
date, sha, result (passed/failed/skipped), duration, log path -- to
~/.local/state/pflege-gate/nightly.tsv, a skip included, so that file is a complete day-by-day record
of the decision, not just the days the lane actually ran.

Measured 2026-10-05 on this box, offline WA lane set (tests/test_wa_*.py tests/test_bridge_*.py
tests/test_app_wa_proxy.py tests/test_auth.py tests/test_app_api.py, -m "not llm and not network"):
2560 collected, 57.25s pytest-internal / ~59s wall. pytest-xdist is NOT installed in .venv, so this
is a serial number. 59s is comfortably under the ~90s-per-commit budget Ivan set, so pre-commit runs
the FULL lane with -x -q rather than a staged-path -> test-file mapping -- simpler, and it still
catches a break anywhere in the lane, not just in files that look related. Materializing the staged
index onto /dev/shm (see "No safety nets" below) adds some of its own time on top of that; pre-commit
prints the measured figure on every run rather than a one-off guess in this docstring.

Past baseline (fixed, not special-cased): 28 of the offline tests used to fail -- 27 in
tests/test_wa_luna_brain.py and 1 in tests/test_wa_harness.py -- because they read the live clinic
registry via PostgREST without an apikey. f877321 fixed the real bug: 7 tests had forgotten to
request their own `luna`/`wa` stub fixture. This gate does not special-case registry failures: a
commit or push that hits them is reported like any other failure.

Environment hazard (observed 2026-10-05, see memory "disk-full-incident-2026-10-01"): when free disk
drops below ~3000MB, tools/disk_janitor_cron.sh (every 15 min) deletes ~/.tmp/pytest-of-claude
wholesale as a "pure cache", including a *currently running* pytest's basetemp -- a full-lane run hit
this once and came back with ~1800 FileNotFoundErrors instead of the usual failures. The workaround
(same one the disk-full incident used): point pytest's basetemp at /dev/shm instead of the default
~/.tmp/pytest-of-<user>, which the janitor does not touch.

/dev/shm hygiene (Opus review B2): pytest never removes a --basetemp you pass it; it only deletes it
at the *start* of the next session with the same path. A fixed basetemp therefore either (a) grows
without bound across pushed shas, or (b) collides between two concurrent runs (two worktrees
committing at once) and one deletes the other's tmp dir mid-run. So every lane run here gets its OWN
basetemp under /dev/shm/pflege-gate/run-<pid>-<uuid>/, removed in a `finally` the moment that lane
ends -- never shared, never left behind on the happy path. The only leftovers are from a run that got
SIGKILLed before its `finally` could execute; main() sweeps those at the start of every hook
invocation, but only a leftover whose owning pid is no longer alive (os.kill(pid, 0) ->
ProcessLookupError) -- a live concurrent run's directory is never touched by someone else's cleanup.
ScratchWorktree (pre-push's checkout of the pushed sha) gets the same treatment (Opus review M4).

Design: all IO (subprocess, filesystem, time) is behind small injectable seams so
tests/test_test_gate.py can exercise the decision logic -- path classification, the LLM trigger,
stamp handling, pre-push/pre-deploy sha-range computation, the scratch worktree and lane
orchestration -- without
depending on a particular live pytest/git outcome where a fake will do (ScratchWorktree and the index
materialization are still exercised against a real throwaway git repo under /dev/shm, since that is
what they wrap). CLAUDE.md "No safety nets": every failure below is loud (non-zero exit, printed test
ids or skip reasons), nothing is silently downgraded to a pass -- including a lane that reports 0
tests or an unexpected skip (M2), and including the staged index itself: pre-commit tests exactly
what `git diff --cached` would commit, materialized via `git checkout-index` into a scratch dir, never
the working tree and never another session's untracked files sitting alongside it (Opus review m2).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

# --------------------------------------------------------------------------------------------------
# Constants Ivan fixed for this box (hard rules in the task that opened this gate).

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_VENV_PYTHON = Path(
    os.environ.get("PFLEGE_GATE_PYTHON", "/home/claude/repo/pflege-board/.venv/bin/python")
)

# Vars a stray inherited shell can carry that would make a test reach the live rail (Ivan,
# 2026-09-25 -- see tests/conftest.py's own copy of this list and memory
# "feedback-tests-never-reach-live-rail"). The hooks unset these before every pytest invocation, and
# the lane runners below strip them again from whatever env dict they are handed.
LIVE_CREDENTIAL_VARS = (
    "WA_TRANSPORT", "WA_BRIDGE_URL", "WA_BRIDGE_TOKEN", "WA_BRIDGE_INBOUND_TOKEN",
    "WA_BRIDGE_PHONE_NUMBER_ID", "WA_AUTOSEND", "META_WHATSAPP_ACCESS_TOKEN",
)

# git exports these into a hook's environment so the hook's own git calls (checkout-index, diff
# --cached, rev-parse) land on the right worktree/index without being told explicitly -- but no lane
# test here runs git, and a pytest subprocess that inherited GIT_INDEX_FILE/GIT_DIR from the hook
# would silently read or write *this commit's* index if anything under test ever shells out to git
# (Opus review nit). Stripped only from the env passed to pytest; left alone for the git calls that
# need them (materialize_staged_index in particular must see the hook's real GIT_INDEX_FILE).
GIT_ENV_VARS_FOR_PYTEST_SCRUB = (
    "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

OFFLINE_MARKER_EXPR = "not llm and not network"
LLM_MARKER_EXPR = "llm"

# The offline WA lane: fixed filenames plus the two globs, resolved against whatever `tests/` the
# commit/push being tested actually has (test_sip_guard_watch.py only exists after 5db4c4f).
OFFLINE_LANE_GLOBS = ("tests/test_wa_*.py", "tests/test_bridge_*.py")
OFFLINE_LANE_FIXED = (
    "tests/test_app_wa_proxy.py",
    "tests/test_auth.py",
    "tests/test_app_api.py",
    "tests/test_sip_guard_watch.py",
    "tests/test_test_gate.py",
)

# The LLM lane: Luna persona/e2e + CV classification -- the 7 files actually marked `llm` as of this
# commit (73 tests). Listed explicitly (not discovered by a repo-wide -m llm collection) because a
# repo-wide collection trips on unrelated collection errors in files this gate has no business
# running (test_completeness_dvinci.py etc. need live network/env this gate does not set up).
LLM_LANE_FILES = (
    "tests/test_cv_classify_document.py",
    "tests/test_cv_eval_cases_llm.py",
    "tests/test_cv_intake.py",
    "tests/test_wa_luna_campaign_personas.py",
    "tests/test_wa_luna_e2e_funnel.py",
    "tests/test_wa_luna_import_reuse_personas.py",
    "tests/test_wa_luna_personas.py",
)

CLAUDE_OAUTH_TOKEN_FILE = Path.home() / ".config" / "pflege-ci" / "claude-oauth-token"

STAMP_DIR = Path.home() / ".local" / "state" / "pflege-gate"

# A failed lane's full stdout, kept here (never under /dev/shm, never swept) so it survives past the
# run that produced it -- a failed LLM lane used to lose its only copy of the output when its scratch
# dir was removed, costing a ~44-minute rerun just to see it again (Ivan, 2026-10-05).
LOG_DIR = STAMP_DIR / "logs"

# One summary line per `nightly` invocation -- including a skip -- appended by _append_nightly_tsv.
NIGHTLY_TSV = STAMP_DIR / "nightly.tsv"

# Everything this gate ever writes to /dev/shm (lane basetemps, pre-commit's materialized index tree,
# pre-push's scratch worktrees) lives under one root so a single sweep at hook start can find and
# clear every kind of dead-pid leftover (Opus review B2/M4).
GATE_SCRATCH_ROOT = Path("/dev/shm/pflege-gate")
SCRATCH_WORKTREE_ROOT = GATE_SCRATCH_ROOT / "worktrees"

ZERO_OIDS = frozenset({"0" * 40, "0" * 64})  # sha1 and sha256 null OIDs


# --------------------------------------------------------------------------------------------------
# /dev/shm scratch dirs: unique per run, swept of dead-pid leftovers at hook start, never a live run's.

def _new_run_dir(root: Path = GATE_SCRATCH_ROOT) -> Path:
    """A fresh, unique scratch directory for one lane run or one staged-index checkout. Never reused
    across runs (Opus review B2) -- the caller creates it, uses it, and removes it in a `finally`."""
    return root / f"run-{os.getpid()}-{uuid.uuid4().hex}"


def _pid_is_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # exists, just not ours to signal -- alive
    return False


def _pid_from_prefixed_dir_name(name: str, prefix: str) -> int | None:
    """Parse the pid out of a `"<prefix><pid>-<rest>"` scratch-dir name, or None if it doesn't match
    that shape (never guess -- an unparsable name is left alone, not swept)."""
    if not name.startswith(prefix):
        return None
    pid_str, _, _rest = name[len(prefix):].partition("-")
    try:
        return int(pid_str)
    except ValueError:
        return None


def _cleanup_dead_run_dirs(root: Path = GATE_SCRATCH_ROOT) -> None:
    """At hook start, remove only run-<pid>-<uuid> leftovers whose owning pid is no longer alive --
    e.g. a lane killed mid-run (SIGKILL skips the `finally`). A live concurrent run's directory is
    never touched, because its pid still answers os.kill(pid, 0)."""
    if not root.exists():
        return
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        pid = _pid_from_prefixed_dir_name(entry.name, "run-")
        if pid is not None and _pid_is_dead(pid):
            shutil.rmtree(entry, ignore_errors=True)


# --------------------------------------------------------------------------------------------------
# Pre-commit: is this worktree mid merge/cherry-pick/revert/rebase?

def _git_dir_for(worktree: Path) -> Path:
    """This worktree's OWN git-dir (e.g. .git/worktrees/<name> for a linked worktree, never the
    shared git-common-dir) -- MERGE_HEAD/CHERRY_PICK_HEAD/REVERT_HEAD/rebase-* live here per worktree."""
    out = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=worktree,
                          capture_output=True, text=True, check=True).stdout.strip()
    p = Path(out)
    return p if p.is_absolute() else worktree / p


def merge_in_progress(git_dir: Path) -> str | None:
    """Name of the operation in progress in this worktree, or None. Pre-commit skips while one of
    these is open (concluding a conflicted merge/cherry-pick/revert can't otherwise finish without
    --no-verify, which agents never pass -- Opus review m1); pre-push still gates the resulting
    commit regardless. `--amend` is an ordinary commit -- none of these files exist for it, so it
    gates as usual."""
    if (git_dir / "MERGE_HEAD").exists():
        return "a merge"
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return "a cherry-pick"
    if (git_dir / "REVERT_HEAD").exists():
        return "a revert"
    if (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir():
        return "a rebase"
    return None


# --------------------------------------------------------------------------------------------------
# Pre-commit: path classification.

def is_docs_only_path(path: str) -> bool:
    """A path that cannot affect code: backlog/, docs/, or any *.md file anywhere -- except
    skill/SKILL.md, which is Luna's own tool documentation (app/wa/luna/tools_server.py serves it
    verbatim to the live model, and tests/test_wa_luna_tools.py asserts on its content), so a change
    there is code-affecting (Opus review m2)."""
    if path == "skill/SKILL.md":
        return False
    return path.startswith("backlog/") or path.startswith("docs/") or path.endswith(".md")


def any_code_affecting(paths: Iterable[str]) -> bool:
    """True if at least one staged path could affect code (i.e. is not docs-only)."""
    return any(not is_docs_only_path(p) for p in paths)


# --------------------------------------------------------------------------------------------------
# Pre-commit: materialize the staged index (not the working tree) into a scratch dir.

def materialize_staged_index(worktree: Path, dest: Path) -> int:
    """`git checkout-index -a -f --prefix=<dest>/` writes exactly what is in the INDEX right now --
    the staged content, including a staged edit whose working-tree copy has since been edited again,
    and never an untracked file lying around from another session (checkout-index only ever writes
    blobs that are actually indexed). Runs with cwd=worktree and the caller's own environment
    untouched, so it honors GIT_INDEX_FILE/GIT_DIR exactly as git set them for this hook invocation
    (Opus review m2). Returns how many files were written; dest must not already exist."""
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "checkout-index", "-a", "-f", f"--prefix={dest}/"],
                    cwd=worktree, check=True, capture_output=True, text=True)
    return sum(1 for p in dest.rglob("*") if p.is_file())


# --------------------------------------------------------------------------------------------------
# Pre-push: parsing stdin. `is_delete` is still read by cmd_pre_push; sha-range/changed-path
# computation (commits_in_range, changed_paths_in_range, determine_changed_paths, is_new_branch) was
# removed 2026-10-06 along with the path-relevance LLM trigger it fed -- see the module docstring.

@dataclasses.dataclass(frozen=True)
class RefUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def parse_pre_push_stdin(text: str) -> list[RefUpdate]:
    """Pre-push hooks get one line per ref on stdin: "<local ref> <local sha> <remote ref> <remote
    sha>". Blank lines (a trailing newline) are ignored; anything else malformed is a loud error."""
    updates = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"malformed pre-push stdin line: {line!r}")
        updates.append(RefUpdate(*parts))
    return updates


def is_delete(ref: RefUpdate) -> bool:
    return ref.local_sha in ZERO_OIDS


# --------------------------------------------------------------------------------------------------
# resolve_commit is still used by pre-deploy/nightly to key a stamp by the FULL sha, never a symbolic
# rev (10-05 review, MAJOR 1). main_checkout_root/main_checkout_head existed only to default
# pre-deploy's now-removed `--deployed` to the main checkout's own HEAD -- removed with it 2026-10-06
# (grep-confirmed no other caller; see the module docstring).

def resolve_commit(rev: str, cwd: Path) -> str:
    """Full sha of the commit ``rev`` names in ``cwd`` (a sha, short sha, branch, tag or HEAD). Raises
    when it names no commit. pre-deploy keys its stamp and scratch checkout by this sha: an unresolved
    "HEAD" or branch name would reuse an old stamp for a different commit (10-05 review, MAJOR 1)."""
    proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                          cwd=cwd, capture_output=True, text=True)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        raise RuntimeError(f"{rev!r} does not name a commit in {cwd}")
    return sha


# --------------------------------------------------------------------------------------------------
# Stamps: ~/.local/state/pflege-gate/<sha>.json records which lanes ran and whether they passed, so a
# remote with two push URLs (or a re-push of the same sha) does not re-run the gate.

def stamp_path(sha: str, stamp_dir: Path = STAMP_DIR) -> Path:
    return stamp_dir / f"{sha}.json"


def read_stamp(sha: str, stamp_dir: Path = STAMP_DIR) -> dict | None:
    p = stamp_path(sha, stamp_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write_stamp(
    sha: str, lanes: dict, stamp_dir: Path = STAMP_DIR, *, failed_parent_pid: int | None = None,
) -> Path:
    stamp_dir.mkdir(parents=True, exist_ok=True)
    p = stamp_path(sha, stamp_dir)
    payload = {"sha": sha, "lanes": lanes, "stamped_at": time.time()}
    if failed_parent_pid is not None:
        # Opus review m4: a FAILED stamp additionally records which `git push` process (its pid, the
        # hook's parent) produced it, so the SECOND push-URL firing of that same push -- git runs
        # pre-push once per URL -- can recognize its own just-failed sha and refuse immediately
        # instead of re-running everything a second time for a push that is rejected either way. A
        # later, genuinely new `git push` of the same sha has a different parent pid and is not
        # covered by this -- it re-runs normally.
        payload["failed_parent_pid"] = failed_parent_pid
    p.write_text(json.dumps(payload, indent=2) + "\n")
    return p


def write_stamp_merged(
    sha: str, new_lanes: dict, stamp_dir: Path = STAMP_DIR, *, failed_parent_pid: int | None = None,
) -> Path:
    """Re-reads the stamp right before writing and merges only `new_lanes` -- this run's OWN lane
    keys -- onto whatever is on disk at that moment, never onto the (possibly stale) copy the caller
    read earlier. _run_lanes_for_sha and cmd_nightly both read a stamp near the top (to decide whether
    to skip, or whether an LLM result already exists) and only run their own lane(s) afterwards, which
    can take tens of minutes -- plenty of time for a concurrent writer (nightly stamping "llm" while a
    push's own pre-push stamps "offline" for the same sha, say) to land its own key in between. Writing
    the caller's stale copy wholesale would silently erase that concurrent write; this re-read-then-
    merge makes the two writes additive instead of last-one-wins (Opus review: stamp race)."""
    current = read_stamp(sha, stamp_dir=stamp_dir)
    lanes = dict(current.get("lanes", {})) if current else {}
    lanes.update(new_lanes)
    return write_stamp(sha, lanes, stamp_dir=stamp_dir, failed_parent_pid=failed_parent_pid)


def stamp_covers(stamp: dict | None, need_llm: bool) -> bool:
    """True if an existing stamp already satisfies what this push needs, so the gate can skip
    re-running entirely. A lane that is recorded as having failed never counts as covering -- only a
    recorded pass does, so a fixed-and-repushed commit (same sha is impossible after a real fix, but
    a forced re-push of the same sha after flakiness is not) gets another chance."""
    if stamp is None:
        return False
    lanes = stamp.get("lanes", {})
    offline = lanes.get("offline", {})
    if not offline.get("passed"):
        return False
    if need_llm and not lanes.get("llm", {}).get("passed"):
        return False
    return True


def stamp_failed_this_push(stamp: dict | None, parent_pid: int) -> bool:
    """True if `stamp` records this sha's offline lane as FAILED, stamped by the SAME `git push`
    process that is asking again (its pid, the hook's own parent, matches `parent_pid`) -- the second
    push-URL firing of a push already known to fail. A FAILED stamp from a different parent pid (an
    earlier, separate `git push` of the same sha) does not count: that push re-runs from scratch."""
    if stamp is None:
        return False
    offline = stamp.get("lanes", {}).get("offline", {})
    if offline.get("passed"):
        return False
    return stamp.get("failed_parent_pid") == parent_pid


# --------------------------------------------------------------------------------------------------
# Running a lane. `runner` is injected (tests/test_test_gate.py never spawns a real pytest).

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def default_runner(cmd: Sequence[str], cwd: Path, env: dict) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(cmd), cwd=cwd, env=env, capture_output=True, text=True)


@dataclasses.dataclass
class LaneResult:
    name: str
    passed: bool
    duration_s: float
    returncode: int
    failing_tests: list[str]
    stdout: str


def _existing_offline_lane_paths(repo_dir: Path) -> list[str]:
    paths: list[str] = []
    for pattern in OFFLINE_LANE_GLOBS:
        paths.extend(sorted(str(p.relative_to(repo_dir)) for p in repo_dir.glob(pattern)))
    for fixed in OFFLINE_LANE_FIXED:
        if (repo_dir / fixed).exists() and fixed not in paths:
            paths.append(fixed)
    if not paths:
        raise RuntimeError(f"offline WA lane resolved to zero test files under {repo_dir} -- "
                            "a repo layout change broke the glob/fixed list in tools/test_gate.py")
    return paths


def _parse_failing_tests(stdout: str) -> list[str]:
    ids = []
    for line in stdout.splitlines():
        if line.startswith("FAILED "):
            ids.append(line[len("FAILED "):].split(" - ", 1)[0].strip())
        elif line.startswith("ERROR "):
            ids.append(line[len("ERROR "):].split(" - ", 1)[0].strip())
    return ids


def _parse_junit_report(path: Path) -> tuple[int, int, list[tuple[str, str]]]:
    """(total tests, skipped count, [(test_id, reason), ...]) from a pytest --junitxml report. A
    missing report is itself a loud failure, not an implicit 0/0 -- pytest writes one even when every
    test is skipped, so its absence means the run never got that far (Opus review M2, no safety nets)."""
    if not path.exists():
        raise RuntimeError(f"LLM lane produced no junit report at {path} -- pytest did not run as "
                            "expected (no silent skip)")
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") or ([root] if root.tag == "testsuite" else [])
    total = sum(int(s.get("tests", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    skip_details: list[tuple[str, str]] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            skip_el = case.find("skipped")
            if skip_el is not None:
                test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
                reason = skip_el.get("message") or skip_el.get("type") or "no reason given"
                skip_details.append((test_id, reason))
    return total, skipped, skip_details


def run_offline_lane(
    repo_dir: Path, *, fail_fast: bool, runner: Runner = default_runner, base_env: dict | None = None,
    scratch_root: Path = GATE_SCRATCH_ROOT,
) -> LaneResult:
    paths = _existing_offline_lane_paths(repo_dir)
    env = dict(base_env if base_env is not None else os.environ)
    for var in LIVE_CREDENTIAL_VARS:
        env.pop(var, None)
    for var in GIT_ENV_VARS_FOR_PYTEST_SCRUB:
        env.pop(var, None)
    basetemp = _new_run_dir(scratch_root)
    basetemp.mkdir(parents=True, exist_ok=True)
    cmd = [str(MAIN_VENV_PYTHON), "-m", "pytest", "-m", OFFLINE_MARKER_EXPR, "-q",
           "--basetemp", str(basetemp)]
    if fail_fast:
        cmd.append("-x")
    cmd.extend(paths)
    try:
        started = time.monotonic()
        proc = runner(cmd, cwd=repo_dir, env=env)
        duration = time.monotonic() - started
        return LaneResult("offline", proc.returncode == 0, duration, proc.returncode,
                           _parse_failing_tests(proc.stdout), proc.stdout)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


def run_llm_lane(
    repo_dir: Path, *, runner: Runner = default_runner, base_env: dict | None = None,
    token_file: Path = CLAUDE_OAUTH_TOKEN_FILE, scratch_root: Path = GATE_SCRATCH_ROOT,
) -> LaneResult:
    if not token_file.exists():
        raise RuntimeError(
            f"LLM lane needs CLAUDE_CODE_OAUTH_TOKEN and {token_file} is missing -- no silent skip "
            "(CLAUDE.md: no safety nets); this runs from nightly (tools/llm_lane_cron.sh) or a forced "
            "pre-deploy, never gated by PFLEGE_GATE_LLM=0 -- fix the token/PATH"
        )
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError(f"{token_file} is empty -- no silent skip")

    env = dict(base_env if base_env is not None else os.environ)
    for var in LIVE_CREDENTIAL_VARS:
        env.pop(var, None)
    for var in GIT_ENV_VARS_FOR_PYTEST_SCRUB:
        env.pop(var, None)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token

    # Opus review M2: every LLM-lane file skips itself when `claude` is not on PATH, so a non-login
    # shell (cron, `ssh host 'git push ...'`) that never gets ~/.local/bin on PATH gets "all skipped"
    # -> pytest exit 0 -> stamped llm.passed=True without ever running a real test.
    claude_bin = env.get("WA_LUNA_CLAUDE_BIN", "claude")
    resolved = shutil.which(claude_bin, path=env.get("PATH", ""))
    if not resolved:
        raise RuntimeError(
            f"LLM lane needs {claude_bin!r} to resolve on the PATH it will hand pytest "
            f"({env.get('PATH', '')!r}) and it did not -- no silent skip (CLAUDE.md: no safety nets); "
            "this runs from nightly (tools/llm_lane_cron.sh) or a forced pre-deploy, never gated by "
            "PFLEGE_GATE_LLM=0 -- fix PATH or WA_LUNA_CLAUDE_BIN"
        )

    basetemp = _new_run_dir(scratch_root)
    basetemp.mkdir(parents=True, exist_ok=True)
    # The Luna tests spawn `claude` with a cwd inside the basetemp, and the CLI files every transcript
    # under <config dir>/projects/<cwd slug>. With the default ~/.claude that leaked one dir per test
    # per run (1110 dirs / 235 MB by 2026-10-05). A throwaway config dir inside the run dir goes away
    # with it; auth comes from CLAUDE_CODE_OAUTH_TOKEN, so the CLI needs nothing else in there.
    claude_config = basetemp / "claude-config"
    claude_config.mkdir()
    env["CLAUDE_CONFIG_DIR"] = str(claude_config)
    junit_path = basetemp / "junit.xml"
    cmd = [str(MAIN_VENV_PYTHON), "-m", "pytest", "-m", LLM_MARKER_EXPR, "-q",
           "--basetemp", str(basetemp), f"--junitxml={junit_path}", *LLM_LANE_FILES]
    try:
        started = time.monotonic()
        proc = runner(cmd, cwd=repo_dir, env=env)
        duration = time.monotonic() - started
        passed = proc.returncode == 0
        failing = _parse_failing_tests(proc.stdout)
        if passed:
            total, skipped, skip_details = _parse_junit_report(junit_path)
            if total == 0:
                passed = False
                print("gate: llm lane FAILED -- junit report shows 0 tests collected")
            elif skipped:
                passed = False
                print(f"gate: llm lane FAILED -- {skipped} test(s) skipped instead of run:")
                for test_id, reason in skip_details:
                    print(f"  {test_id}: {reason}")
        return LaneResult("llm", passed, duration, proc.returncode, failing, proc.stdout)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# --------------------------------------------------------------------------------------------------
# Scratch worktree for pre-push: test THE PUSHED COMMIT, never the working tree.

class ScratchWorktree:
    """git worktree add --detach <path> <sha>, removed on exit (also on failure/exception). The path
    encodes the owning pid so a leftover from a killed run (SIGKILL skips __exit__) can be told apart
    from a live concurrent one (Opus review M4)."""

    def __init__(self, sha: str, root: Path = SCRATCH_WORKTREE_ROOT, repo_root: Path = REPO_ROOT):
        self.sha = sha
        self.root = root
        self.repo_root = repo_root
        self.pid = os.getpid()
        self.path = root / f"pid-{self.pid}-{sha}"

    def __enter__(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        # Clears a stale "missing but already registered" administrative entry from a worktree whose
        # directory is already gone -- without this, `add` below fails opaquely for exactly that sha.
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True, text=True)
        if self.path.exists():
            shutil.rmtree(self.path)
        proc = subprocess.run(["git", "worktree", "add", "--detach", str(self.path), self.sha],
                               cwd=self.repo_root, capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            raise RuntimeError(
                f"git worktree add --detach {self.path} {self.sha} failed (exit {proc.returncode}) "
                "-- see git's stderr above"
            )
        return self.path

    def __exit__(self, *exc_info) -> None:
        subprocess.run(["git", "worktree", "remove", "--force", str(self.path)],
                        cwd=self.repo_root, capture_output=True, text=True)
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True, text=True)


def _cleanup_dead_scratch_worktrees(root: Path = SCRATCH_WORKTREE_ROOT, repo_root: Path = REPO_ROOT) -> None:
    """At hook start, remove scratch worktrees under `root` left behind by a killed pre-push (its
    `finally`/__exit__ never ran) -- but only ones whose owning pid is dead; a live concurrent run's
    worktree is left alone (Opus review M4)."""
    if not root.exists():
        return
    any_removed = False
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        pid = _pid_from_prefixed_dir_name(entry.name, "pid-")
        if pid is None or not _pid_is_dead(pid):
            continue
        subprocess.run(["git", "worktree", "remove", "--force", str(entry)],
                        cwd=repo_root, capture_output=True, text=True)
        if entry.exists():
            shutil.rmtree(entry, ignore_errors=True)
        any_removed = True
    if any_removed:
        subprocess.run(["git", "worktree", "prune"], cwd=repo_root, capture_output=True, text=True)


# --------------------------------------------------------------------------------------------------
# CLI: pre-commit. Tests the STAGED INDEX of `worktree`, never its working tree.

def cmd_pre_commit(worktree: Path) -> int:
    git_dir = _git_dir_for(worktree)
    op = merge_in_progress(git_dir)
    if op is not None:
        print(f"pre-commit: skipped -- {op} is in progress in this worktree (pre-push still gates "
              "the resulting commit)")
        return 0

    proc = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACDMR"],
                           cwd=worktree, capture_output=True, text=True, check=True)
    staged = [line for line in proc.stdout.splitlines() if line]
    if not staged:
        print("pre-commit: nothing staged, nothing to test")
        return 0
    if not any_code_affecting(staged):
        print(f"pre-commit: skipped -- all {len(staged)} staged path(s) are docs/backlog/*.md only")
        return 0

    run_dir = _new_run_dir()
    tree_dir = run_dir / "tree"
    try:
        print(f"pre-commit: {len(staged)} staged path(s) can affect code -- materializing the staged "
              "index (not the working tree) before running the offline WA lane")
        started = time.monotonic()
        n_files = materialize_staged_index(worktree, tree_dir)
        elapsed = time.monotonic() - started
        print(f"pre-commit: materialized {n_files} file(s) from the staged index in {elapsed:.2f}s")
        result = run_offline_lane(tree_dir, fail_fast=True)
        print(f"pre-commit: offline lane {'passed' if result.passed else 'FAILED'} "
              f"in {result.duration_s:.1f}s")
        if not result.passed:
            print("pre-commit: failing test(s):")
            for test_id in result.failing_tests:
                print(f"  {test_id}")
            print(result.stdout[-4000:])
            return 1
        return 0
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


# --------------------------------------------------------------------------------------------------
# A lane's full stdout, saved somewhere that outlives this run's scratch cleanup.

def _save_lane_log(sha: str, lane: LaneResult, *, log_dir: Path = LOG_DIR) -> Path:
    """Write `lane`'s full stdout to <log_dir>/<sha>-<lane.name>.log and return the path.
    _run_lanes_for_sha (pre-commit/pre-push/pre-deploy) calls this only for a FAILED lane -- a passed
    lane's output there is uninteresting and the stamp already records the pass. cmd_nightly (2026-
    10-06) calls it for EVERY run, pass or fail, since a nightly LLM-lane run is rare (once a day at
    most) and its own output is worth keeping either way, not just when it fails. Unlike the /dev/shm
    scratch dirs, log_dir is never swept by anything in this module, so the file is still there after
    the run (and its ScratchWorktree/basetemp) is gone."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{sha}-{lane.name}.log"
    path.write_text(lane.stdout)
    return path


# --------------------------------------------------------------------------------------------------
# nightly.tsv: one summary line per `nightly` invocation, a skip included, so the file is a complete
# day-by-day record of the decision rather than only the days the lane actually ran.

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_nightly_tsv(
    path: Path, *, sha: str, result: str, duration_s: float, log_path: str,
    today: Callable[[], str] = _today_utc,
) -> None:
    """Append one tab-separated row -- date, sha, result, duration_s, log_path -- to `path`, creating
    it (and its parent dir) if needed. No header row, matching this repo's other hand-appended tsv
    (tools/status_docs_publish.py's tokens.tsv): a human or a `cut -f` reads it, never a
    csv.DictReader. `today` is injectable so a test can pin the date column. `result` is one of
    "passed"/"failed" (a fresh run), "skipped-passed"/"skipped-failed" (the sha already carried that
    verdict -- Opus review: a RED sha's skip must say so, carrying the earlier log path in `log_path`,
    not an empty one, so the day's row is never mistaken for "nothing to report"), or "crashed" (any
    exception inside `cmd_nightly`, re-raised after this row is written -- `log_path` then holds the
    exception's class and message, since there is no lane log to point to)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{today()}\t{sha}\t{result}\t{duration_s:.1f}\t{log_path}\n")


def _last_nightly_tsv_line(path: Path) -> str | None:
    """The last row of nightly.tsv, verbatim, or None if the file is missing or has no rows yet --
    cmd_pre_deploy prints this as part of its informational (never gating) LLM-lane visibility line."""
    if not path.exists():
        return None
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    return lines[-1] if lines else None


def _last_nightly_status_line(path: Path, *, today: Callable[[], str] = _today_utc) -> str:
    """The last nightly.tsv row WITH ITS AGE in days, or a plain statement that there is none: a cron
    that was never installed (or died) must not read like a quiet one. The age is today's UTC date
    minus the row's own date column."""
    row = _last_nightly_tsv_line(path)
    if row is None:
        return ("last nightly.tsv row: NONE -- no nightly run has ever written a row "
                "(is tools/llm_lane_cron.sh in the crontab?)")
    fmt = "%Y-%m-%d"
    age = (datetime.strptime(today(), fmt) - datetime.strptime(row.split("\t", 1)[0], fmt)).days
    return f"last nightly.tsv row ({age} day(s) old): {row}"


def _llm_stamp_status_line(sha: str, *, stamp_dir: Path = STAMP_DIR, log_dir: Path = LOG_DIR) -> str:
    """"passed" / "FAILED (<log path>)" / "not judged" for `sha`'s own recorded LLM-lane result --
    the text cmd_pre_deploy prints before its lanes run (informational only, never a gate: a deploy
    never waits on this). The log path, when there is one, is logs/<sha>-llm.log exactly as
    _save_lane_log names it -- nightly (the only place that writes an LLM-lane stamp) always saves
    that log, pass or fail."""
    stamp = read_stamp(sha, stamp_dir=stamp_dir)
    llm = stamp.get("lanes", {}).get("llm") if stamp else None
    if llm is None:
        return "not judged"
    if llm.get("passed"):
        return "passed"
    return f"FAILED ({log_dir / f'{sha}-llm.log'})"


# --------------------------------------------------------------------------------------------------
# CLI: pre-push and pre-deploy share this lane runner (and its stamps). `label` is only cosmetic
# (which command's name prefixes the printed lines); the decision logic is identical either way.

def _run_lanes_for_sha(
    sha: str, need_llm: bool, llm_reason: str, *,
    parent_pid: int | None = None,
    stamp_dir: Path = STAMP_DIR,
    log_dir: Path = LOG_DIR,
    scratch_factory: Callable[[str], "object"] = ScratchWorktree,
    offline_lane: Callable[..., LaneResult] = run_offline_lane,
    llm_lane: Callable[..., LaneResult] = run_llm_lane,
    label: str = "pre-push",
) -> int:
    pid = parent_pid if parent_pid is not None else os.getppid()
    stamp = read_stamp(sha, stamp_dir=stamp_dir)
    if stamp_covers(stamp, need_llm):
        lanes = ", ".join(sorted(stamp.get("lanes", {})))
        print(f"{label}: {sha[:12]} already verified (stamped lanes: {lanes}) -- not re-running")
        return 0
    if stamp_failed_this_push(stamp, pid):
        # Opus review m4: git already ran this hook once for this sha against the OTHER push URL in
        # this same `git push`, and it failed -- the push is rejected either way, so don't spend the
        # offline lane (or, worse, real LLM tokens) running it all again for nothing.
        print(f"{label}: {sha[:12]} already FAILED earlier in this push (pid {pid}) -- "
              "not re-running; fix and push again")
        return 1

    # Opus review (stamp race): ONLY this run's own lane key(s), never a copy of `stamp` above --
    # that read can be tens of minutes stale by the time a lane finishes, and write_stamp_merged below
    # re-reads fresh at write time so a concurrent writer's own key is merged in, not clobbered.
    lanes_result: dict[str, dict] = {}
    overall_ok = True

    with scratch_factory(sha) as scratch:
        offline = offline_lane(scratch, fail_fast=False)
        print(f"{label}: offline lane {'passed' if offline.passed else 'FAILED'} "
              f"({offline.duration_s:.1f}s)")
        lanes_result["offline"] = {"passed": offline.passed, "duration_s": offline.duration_s}

        llm_skip_reason: str | None = None
        if not offline.passed:
            overall_ok = False
            print(f"{label}: failing test(s):")
            for test_id in offline.failing_tests:
                print(f"  {test_id}")
            log_path = _save_lane_log(sha, offline, log_dir=log_dir)
            print(f"{label}: full offline lane output saved to {log_path}")
            if need_llm:
                llm_skip_reason = "offline lane failed"  # m4: never spend the LLM lane on a dead push
        elif need_llm:
            print(f"{label}: LLM lane triggered -- {llm_reason}")
            llm = llm_lane(scratch)
            print(f"{label}: llm lane {'passed' if llm.passed else 'FAILED'} ({llm.duration_s:.1f}s)")
            lanes_result["llm"] = {"passed": llm.passed, "duration_s": llm.duration_s}
            if not llm.passed:
                overall_ok = False
                print(f"{label}: failing test(s):")
                for test_id in llm.failing_tests:
                    print(f"  {test_id}")
                log_path = _save_lane_log(sha, llm, log_dir=log_dir)
                print(f"{label}: full llm lane output saved to {log_path}")
        else:
            llm_skip_reason = llm_reason

        if llm_skip_reason is not None:
            print(f"{label}: LLM lane SKIPPED -- {llm_skip_reason}")

    if overall_ok:
        write_stamp_merged(sha, lanes_result, stamp_dir=stamp_dir)
    else:
        write_stamp_merged(sha, lanes_result, stamp_dir=stamp_dir, failed_parent_pid=pid)
    return 0 if overall_ok else 1


# --------------------------------------------------------------------------------------------------
# CLI: pre-push -- the FAST push gate. Offline lane only, always: the LLM lane never runs here
# (Ivan, 2026-10-05) and has its own once-a-day schedule now (2026-10-06, see cmd_nightly) -- neither
# a touched LLM-relevant path nor PFLEGE_GATE_LLM has any effect on pre-push.

def cmd_pre_push(
    worktree: Path, *,
    stdin_text: str | None = None,
    parent_pid: int | None = None,
    run_lanes: Callable[..., int] = _run_lanes_for_sha,
) -> int:
    text = sys.stdin.read() if stdin_text is None else stdin_text
    updates = parse_pre_push_stdin(text)
    if not updates:
        print("pre-push: no refs on stdin, nothing to test")
        return 0

    pid = parent_pid if parent_pid is not None else os.getppid()
    llm_reason = ("pre-push never runs the LLM lane -- it runs on its own nightly schedule instead "
                  "(tools/test_gate.py nightly, via tools/llm_lane_cron.sh)")

    for ref in updates:
        if is_delete(ref):
            print(f"pre-push: {ref.remote_ref} is a delete -- skipping")
            continue
        rc = run_lanes(ref.local_sha, False, llm_reason, parent_pid=pid)
        if rc:
            # The push is rejected as a whole; testing the remaining refs cannot change that.
            print(f"pre-push: {ref.local_ref} failed -- not testing the remaining refs of this push")
            return rc
    return 0


# --------------------------------------------------------------------------------------------------
# CLI: pre-deploy -- the DEPLOY gate. Always the offline lane, ONLY the offline lane by default
# (Ivan, 2026-10-06: the LLM lane moved off pre-deploy entirely, onto its own once-a-day schedule --
# see cmd_nightly and the module docstring's 2026-10-06 entry). PFLEGE_GATE_LLM=1 still forces the LLM
# lane here too, for the rare case of wanting it inline with a deploy; path relevance (what used to
# drive this on 2026-10-05) no longer has any effect, and neither does `--deployed` -- removed, along
# with the changed-path count it fed, once grep confirmed nothing downstream read either (module
# docstring). Run by hand or by the deploy pipeline -- never by a git hook, so there is no stdin to
# parse and no push-URL double-firing to dedupe; the parent-pid fail-stamp dedup in _run_lanes_for_sha
# still applies harmlessly (a second pre-deploy invocation with a different pid just re-runs, which is
# correct).
#
# Opus review (visibility): before the lanes run, prints one informational line naming the TARGET
# sha's own recorded LLM-lane result (passed / FAILED with its log path / not judged) and the last
# nightly.tsv row -- informational only, never a gate, exactly like the rest of this function: Ivan,
# 2026-10-06, "deploy does not wait for LLM tests". This just answers "is the thing I'm about to
# deploy LLM-clean" without making the deploy wait on an answer.

def cmd_pre_deploy(
    worktree: Path, target: str, *,
    env: dict | None = None,
    stamp_dir: Path = STAMP_DIR,
    log_dir: Path = LOG_DIR,
    nightly_tsv: Path = NIGHTLY_TSV,
    parent_pid: int | None = None,
    run_lanes: Callable[..., int] = _run_lanes_for_sha,
    resolve: Callable[[str], str] | None = None,
) -> int:
    env = env if env is not None else os.environ
    if resolve is None:
        def resolve(rev: str) -> str:
            return resolve_commit(rev, worktree)

    target = resolve(target)

    llm_status = _llm_stamp_status_line(target, stamp_dir=stamp_dir, log_dir=log_dir)
    print(f"pre-deploy: LLM lane on {target[:12]}: {llm_status}")
    print(f"pre-deploy: {_last_nightly_status_line(nightly_tsv)}")

    # 2026-10-06: offline lane only, by default -- no path-based LLM trigger any more. PFLEGE_GATE_LLM=1
    # still forces the LLM lane here too; PFLEGE_GATE_LLM=0 is accepted for symmetry, though it changes
    # nothing since skipping is already the default.
    if env.get("PFLEGE_GATE_LLM") == "1":
        need_llm, llm_reason = True, "PFLEGE_GATE_LLM=1 (forced run)"
    else:
        need_llm, llm_reason = False, (
            "pre-deploy runs the offline lane only by default (Ivan, 2026-10-06) -- the LLM lane "
            "runs nightly instead (tools/test_gate.py nightly, via tools/llm_lane_cron.sh); set "
            "PFLEGE_GATE_LLM=1 to force it here too"
        )

    pid = parent_pid if parent_pid is not None else os.getpid()
    return run_lanes(target, need_llm, llm_reason, parent_pid=pid, label="pre-deploy")


# --------------------------------------------------------------------------------------------------
# CLI: nightly -- the LLM lane's new (and only) home (Ivan, 2026-10-06, see the module docstring).
# Tests ONLY the LLM lane -- the offline lane already runs on every pre-commit/pre-push and does not
# need a nightly repeat -- in a scratch worktree of the target sha (same ScratchWorktree pre-deploy
# uses), and skips entirely once that sha has ANY recorded LLM-lane result, pass or fail: a sha
# nightly already judged stays judged, even if it failed, so a fix lands on a NEW sha and gets its
# own fresh nightly run rather than re-spending ~44 minutes re-judging one already on record. Run by
# tools/llm_lane_cron.sh, once a day -- never by a git hook.
#
# Opus review (red sha): a skip is per sha, not a clean slate -- the skipped sha carries whatever
# verdict it already had. The tsv row says so explicitly ("skipped-passed"/"skipped-failed", never a
# bare "skipped" that would read as "nothing to report"), with `log_path` pointing at the EARLIER run's
# own log (logs/<sha>-llm.log -- _save_lane_log's name for it, which cmd_nightly always writes, pass or
# fail), and the exit code tracks the verdict too: 0 while the sha is green, 1 while it is still red,
# so a monitor reading only `rc` (not the tsv) still sees a red sha as red on every later tick, not
# just the one that first judged it. To re-judge a specific sha on purpose (overwrite its stamp with a
# fresh LLM-lane run instead of skipping): `PFLEGE_GATE_LLM=1 tools/test_gate.py pre-deploy --target
# <sha>` -- pre-deploy ignores the nightly stamp's skip logic entirely and always runs when forced.
#
# Opus review (crash): any exception raised anywhere in this function -- including one from `fetch`,
# `resolve`, or the LLM lane itself -- is recorded as a "crashed" tsv row (log_path holds the
# exception's class and message, there being no lane log to point to) before it is re-raised
# unchanged (CLAUDE.md: no safety nets, failures stay loud). Without this, a crash before the lane
# ever got to write a row would leave that day's nightly.tsv silent about it -- indistinguishable from
# "cron never fired" -- rather than a mark of exactly what broke. `sha` is "unknown" when the crash
# happens before resolve() ever runs (e.g. `git fetch` itself failing).

def cmd_nightly(
    worktree: Path, target: str | None = None, *,
    stamp_dir: Path = STAMP_DIR,
    log_dir: Path = LOG_DIR,
    nightly_tsv: Path = NIGHTLY_TSV,
    fetch: Callable[[], None] | None = None,
    resolve: Callable[[str], str] | None = None,
    scratch_factory: Callable[[str], "object"] = ScratchWorktree,
    llm_lane: Callable[..., LaneResult] = run_llm_lane,
    today: Callable[[], str] = _today_utc,
) -> int:
    sha: str | None = None
    try:
        if resolve is None:
            def resolve(rev: str) -> str:
                return resolve_commit(rev, worktree)

        if target is None:
            # Default target: origin/main AFTER a fetch -- the latest merged code, of which the
            # deployed main checkout's HEAD is always an ancestor. An explicit --target skips the
            # fetch: the caller named a specific rev, already resolvable as-is.
            if fetch is None:
                def fetch() -> None:
                    proc = subprocess.run(["git", "fetch", "origin"], cwd=worktree,
                                           capture_output=True, text=True)
                    if proc.returncode != 0:
                        print(proc.stderr, file=sys.stderr)
                        raise RuntimeError(
                            f"git fetch origin failed (exit {proc.returncode}) -- see git's stderr above"
                        )
            fetch()
            target = "origin/main"
        sha = resolve(target)

        stamp = read_stamp(sha, stamp_dir=stamp_dir)
        existing_llm = stamp.get("lanes", {}).get("llm") if stamp else None
        if existing_llm is not None:
            passed = bool(existing_llm.get("passed"))
            earlier = "passed" if passed else "failed"
            earlier_log = log_dir / f"{sha}-llm.log"
            print(f"nightly: {sha[:12]} already has an LLM lane result ({earlier}) -- skipping "
                  f"(re-judge with: PFLEGE_GATE_LLM=1 tools/test_gate.py pre-deploy --target {sha[:12]})")
            _append_nightly_tsv(nightly_tsv, sha=sha, result=f"skipped-{earlier}", duration_s=0.0,
                                 log_path=str(earlier_log), today=today)
            return 0 if passed else 1

        print(f"nightly: {sha[:12]} has no recorded LLM lane result yet -- running it")
        with scratch_factory(sha) as scratch:
            llm = llm_lane(scratch)
        log_path = _save_lane_log(sha, llm, log_dir=log_dir)
        print(f"nightly: llm lane {'passed' if llm.passed else 'FAILED'} ({llm.duration_s:.1f}s) -- "
              f"full output saved to {log_path}")
        if not llm.passed:
            print("nightly: failing test(s):")
            for test_id in llm.failing_tests:
                print(f"  {test_id}")

        write_stamp_merged(sha, {"llm": {"passed": llm.passed, "duration_s": llm.duration_s}},
                            stamp_dir=stamp_dir)
        _append_nightly_tsv(nightly_tsv, sha=sha, result="passed" if llm.passed else "failed",
                             duration_s=llm.duration_s, log_path=str(log_path), today=today)
        return 0 if llm.passed else 1
    except Exception as exc:
        _append_nightly_tsv(nightly_tsv, sha=sha or "unknown", result="crashed", duration_s=0.0,
                             log_path=f"{type(exc).__name__}: {exc}", today=today)
        raise


# --------------------------------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="test_gate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pre-commit", "pre-push", "pre-deploy", "nightly"):
        p = sub.add_parser(name)
        p.add_argument("--worktree", type=Path, default=REPO_ROOT,
                        help="the worktree whose content to test (passed by the shim as the one git "
                             "invoked the hook for -- defaults to this checkout when omitted)")
        if name == "pre-deploy":
            p.add_argument("--target", required=True,
                            help="the commit about to be deployed (any rev; resolved to its full sha) -- "
                                 "tested in a scratch worktree")
        if name == "nightly":
            p.add_argument("--target", default=None,
                            help="the commit to run the LLM lane on (any rev; resolved to its full "
                                 "sha) -- default: origin/main after a git fetch")
    args = parser.parse_args(argv)

    _cleanup_dead_run_dirs()
    if args.command == "pre-commit":
        return cmd_pre_commit(args.worktree)
    if args.command == "pre-push":
        _cleanup_dead_scratch_worktrees()
        return cmd_pre_push(args.worktree)
    if args.command == "pre-deploy":
        _cleanup_dead_scratch_worktrees()
        return cmd_pre_deploy(args.worktree, args.target)
    if args.command == "nightly":
        _cleanup_dead_scratch_worktrees()
        return cmd_nightly(args.worktree, args.target)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
