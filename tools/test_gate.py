#!/usr/bin/env python3
"""tools/test_gate.py -- versioned git-hook test gate (2026-10-05, Ivan: "тесты гонять норм, можно
прекомит хук"), plus a `pre-deploy` CLI subcommand for the deploy pipeline (also 2026-10-05, Ivan:
"run the LLM tests only before deploying, not on every push").

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
that, forcing a blind rerun. Both are fixed by moving the LLM lane to a separate `pre-deploy`
subcommand, run by hand or by the deploy pipeline, never by a git hook: `tools/test_gate.py
pre-deploy --target <sha> [--deployed <sha>]` (deployed defaults to the MAIN checkout's HEAD, via
main_checkout_root -- never this invocation's own worktree, which may be a dev worktree like this
one). pre-deploy diffs deployed..target the same way pre-push used to diff a push's old..new sha,
runs the offline lane always, and the LLM lane only when that diff touches LLM_RELEVANT_PREFIXES (or
PFLEGE_GATE_LLM=1/0 overrides it) -- same llm_lane_decision/touches_llm_paths logic, same stamps under
~/.local/state/pflege-gate/. Either command, on a lane failure, now also saves that lane's full stdout
to ~/.local/state/pflege-gate/logs/<sha>-<lane>.log (never swept, unlike the /dev/shm scratch dirs
below) and prints the path, so a failed LLM lane's output survives to be read instead of re-spending
the ~44 minutes just to see it again.

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

# Paths whose change should pull in the LLM lane on pre-deploy (prefixes, matched against repo-root-
# relative git paths with "/" separators). Opus review m2: the e2e funnel (traced by actually
# importing tests/test_wa_luna_e2e_funnel.py and diffing sys.modules, 2026-10-05) drives
# app/wa/api.py (document ingestion onto the card), app/wa/slots.py and app/wa/store.py (the slot
# state machine and thread persistence the brain reads/writes every turn), app/data.py (the postings
# snapshot the brain scores against) and skill/SKILL.md (NOT docs here -- app/wa/luna/tools_server.py
# serves it verbatim to the live model, and tests/test_wa_luna_tools.py asserts on its content). The
# same trace also reaches app/wa/config.py, app/wa/brain.py, app/autopilot/matching.py, app/crawl.py,
# app/config.py and several app/wa/{bridge,bridge_ids,meta,phones,stt,suppression,transport}.py
# modules; none of those are added here because every one of them is deterministic infra/config with
# no live-model dependency of its own, already fully exercised by the *offline* lane's own test files
# (test_bridge_*.py, test_wa_stt.py, test_wa_suppression.py, ...) -- a break there fails the offline
# lane regardless of whether the LLM lane also runs, so forcing the slow+costly LLM lane on every
# touch of (say) app/wa/phones.py would buy nothing.
LLM_RELEVANT_PREFIXES = (
    "app/wa/luna_brain.py",
    "app/wa/luna/",
    "app/wa/api.py",
    "app/wa/slots.py",
    "app/wa/store.py",
    "app/data.py",
    "app/cv.py",
    "app/cv_",  # any sibling app/cv_*.py module
    "prompts/",
    "evals/",
    "skill/SKILL.md",
) + LLM_LANE_FILES

CLAUDE_OAUTH_TOKEN_FILE = Path.home() / ".config" / "pflege-ci" / "claude-oauth-token"

STAMP_DIR = Path.home() / ".local" / "state" / "pflege-gate"

# A failed lane's full stdout, kept here (never under /dev/shm, never swept) so it survives past the
# run that produced it -- a failed LLM lane used to lose its only copy of the output when its scratch
# dir was removed, costing a ~44-minute rerun just to see it again (Ivan, 2026-10-05).
LOG_DIR = STAMP_DIR / "logs"

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
# LLM-lane trigger.

def touches_llm_paths(paths: Iterable[str]) -> bool:
    for p in paths:
        if any(p == pre or p.startswith(pre) for pre in LLM_RELEVANT_PREFIXES):
            return True
    return False


def llm_lane_decision(paths: Iterable[str] | None, env: dict) -> tuple[bool, str]:
    """Whether the LLM lane should run, and why. PFLEGE_GATE_LLM in env overrides path detection.
    `paths=None` means the change set is unknown (a force push whose previous remote tip this clone
    never had -- Opus review m3): run the LLM lane rather than guess it is irrelevant."""
    forced = env.get("PFLEGE_GATE_LLM")
    if forced == "1":
        return True, "PFLEGE_GATE_LLM=1 (forced run)"
    if forced == "0":
        return False, "PFLEGE_GATE_LLM=0 (forced skip)"
    if paths is None:
        return True, "change set unknown (base sha not present locally) -- running the LLM lane"
    paths = list(paths)
    if touches_llm_paths(paths):
        return True, "changed range touches an LLM-relevant path"
    return False, "no LLM-relevant path in the pushed range"


# --------------------------------------------------------------------------------------------------
# Pre-push: parsing stdin, sha-range computation. `git_run` is injected so tests never shell out.

GitRun = Callable[[Sequence[str]], list[str]]


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


def is_new_branch(ref: RefUpdate) -> bool:
    return not is_delete(ref) and ref.remote_sha in ZERO_OIDS


def commits_in_range(ref: RefUpdate, git_run: GitRun) -> list[str]:
    """Commits newly introduced by this ref update. A delete introduces none. A new branch's range is
    "whatever isn't already on some remote-tracking ref" (handles a push that forks off an existing
    branch without needing to know the default branch's name)."""
    if is_delete(ref):
        return []
    if is_new_branch(ref):
        out = git_run(["rev-list", ref.local_sha, "--not", "--remotes"])
    else:
        out = git_run(["rev-list", f"{ref.remote_sha}..{ref.local_sha}"])
    return [line for line in out if line]


def changed_paths_in_range(ref: RefUpdate, git_run: GitRun) -> set[str]:
    """Union of paths touched by every commit in this ref's range, via `diff-tree` per commit. This
    is the merge-base range logic kept for a brand-new branch, which has no single remote sha to diff
    against. For an ordinary update, prefer determine_changed_paths below: `diff-tree` on a merge
    commit alone prints nothing, so a merge's own conflict resolution would otherwise never trigger
    the LLM lane (Opus review m3)."""
    paths: set[str] = set()
    for commit in commits_in_range(ref, git_run):
        lines = git_run(["diff-tree", "--no-commit-id", "--name-only", "-r", commit])
        paths.update(line for line in lines if line)
    return paths


def determine_changed_paths(ref: RefUpdate, git_run: GitRun) -> tuple[set[str] | None, str]:
    """Paths touched by this ref update, for the LLM-lane trigger, and why. A brand-new branch keeps
    the merge-base `diff-tree` union above (no single remote sha to diff against). An ordinary update
    diffs remote_sha..local_sha directly with `git diff --name-only`, so a merge's own resolution
    counts (Opus review m3) -- not just each parent's separate diff-tree, which is empty for the
    merge commit itself. A force push whose remote_sha this clone never fetched makes that diff fail;
    the change set is then unknown, not empty, and the caller should run the LLM lane to be safe."""
    if is_new_branch(ref):
        return changed_paths_in_range(ref, git_run), "new branch (merge-base range)"
    try:
        lines = git_run(["diff", "--name-only", ref.remote_sha, ref.local_sha])
    except subprocess.CalledProcessError:
        return None, f"remote sha {ref.remote_sha[:12]} not present locally -- change set unknown"
    return {line for line in lines if line}, "diff remote_sha..local_sha"


def real_git_run(args: Sequence[str], cwd: Path = REPO_ROOT) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.splitlines()


# --------------------------------------------------------------------------------------------------
# Pre-deploy: finding "the main checkout" from any worktree.

def main_checkout_root(worktree: Path) -> Path:
    """The MAIN checkout's root directory -- never `worktree` itself when `worktree` is a linked
    worktree (e.g. a dev worktree like this one) -- derived exactly the way githooks/pre-commit and
    githooks/pre-push derive it in shell: `dirname -- "$(git rev-parse --path-format=absolute
    --git-common-dir)")`. git's common dir is shared by every worktree and always lives inside the
    main checkout's own .git, so this resolves correctly whether `worktree` IS the main checkout (its
    .git IS the common dir) or a linked worktree (whose .git file points at
    <main>/.git/worktrees/<name>, and --git-common-dir follows that back to <main>/.git). Never
    hardcoded, so this keeps working if the main checkout ever moves."""
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=worktree, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(out).parent


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


def main_checkout_head(worktree: Path) -> str:
    """HEAD of the MAIN checkout (see main_checkout_root), for pre-deploy's `--deployed` default: the
    sha currently live is whatever the main checkout -- the one production actually runs from -- is
    sitting on, not this invocation's own (possibly unrelated) worktree."""
    main_checkout = main_checkout_root(worktree)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main_checkout,
                          capture_output=True, text=True, check=True).stdout.strip()
    return out


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
            f"LLM lane needs CLAUDE_CODE_OAUTH_TOKEN and {token_file} is missing -- "
            "no silent skip (CLAUDE.md: no safety nets); fix the token file or pass PFLEGE_GATE_LLM=0"
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
            "fix PATH or WA_LUNA_CLAUDE_BIN, or pass PFLEGE_GATE_LLM=0"
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
# A failed lane's full stdout, saved somewhere that outlives this run's scratch cleanup.

def _save_lane_log(sha: str, lane: LaneResult, *, log_dir: Path = LOG_DIR) -> Path:
    """Write `lane`'s full stdout to <log_dir>/<sha>-<lane.name>.log and return the path. Called only
    for a FAILED lane -- a passed lane's output is uninteresting and the stamp already records the
    pass. Unlike the /dev/shm scratch dirs, log_dir is never swept by anything in this module, so the
    file is still there after the run (and its ScratchWorktree/basetemp) is gone."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{sha}-{lane.name}.log"
    path.write_text(lane.stdout)
    return path


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

    lanes_result: dict[str, dict] = dict(stamp.get("lanes", {})) if stamp else {}
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
        write_stamp(sha, lanes_result, stamp_dir=stamp_dir)
    else:
        write_stamp(sha, lanes_result, stamp_dir=stamp_dir, failed_parent_pid=pid)
    return 0 if overall_ok else 1


# --------------------------------------------------------------------------------------------------
# CLI: pre-push -- the FAST push gate. Offline lane only, always: the LLM lane moved to pre-deploy
# below (Ivan, 2026-10-05), so neither a touched LLM-relevant path nor PFLEGE_GATE_LLM=1 has any
# effect here any more -- that override now only does something on pre-deploy.

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
    llm_reason = "pre-push never runs the LLM lane -- it moved to pre-deploy (Ivan, 2026-10-05)"

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
# CLI: pre-deploy -- the DEPLOY gate. Always the offline lane; the LLM lane too when deployed..target
# touches an LLM-relevant path (same trigger pre-push used to apply on every push, moved here instead
# -- Ivan, 2026-10-05: "run the LLM tests only before deploying, not on every push"). Run by hand or
# by the deploy pipeline -- never by a git hook, so there is no stdin to parse and no push-URL
# double-firing to dedupe; the parent-pid fail-stamp dedup in _run_lanes_for_sha still applies
# harmlessly (a second pre-deploy invocation with a different pid just re-runs, which is correct).

def cmd_pre_deploy(
    worktree: Path, target: str, deployed: str | None = None, *,
    env: dict | None = None,
    git_run: GitRun | None = None,
    get_main_checkout_head: Callable[[Path], str] = main_checkout_head,
    parent_pid: int | None = None,
    run_lanes: Callable[..., int] = _run_lanes_for_sha,
    resolve: Callable[[str], str] | None = None,
) -> int:
    env = env if env is not None else os.environ
    if git_run is None:
        def git_run(args: Sequence[str]) -> list[str]:
            return real_git_run(args, cwd=worktree)
    if resolve is None:
        def resolve(rev: str) -> str:
            return resolve_commit(rev, worktree)

    target = resolve(target)
    if deployed is None:
        deployed = get_main_checkout_head(worktree)
        print(f"pre-deploy: --deployed not given -- using the main checkout's HEAD {deployed[:12]}")
    deployed = resolve(deployed)

    # Reuses determine_changed_paths (same function pre-push used to call for its own old..new sha
    # range) via a synthetic ref: deployed -> target is an ordinary update as far as that function is
    # concerned (is_new_branch is false whenever `deployed` is a real, non-zero sha, which it always
    # is here -- either given explicitly or read as the main checkout's own HEAD).
    synthetic_ref = RefUpdate("pre-deploy-target", target, "pre-deploy-deployed", deployed)
    paths, path_reason = determine_changed_paths(synthetic_ref, git_run)
    need_llm, llm_reason = llm_lane_decision(paths, env)
    n_paths = "unknown" if paths is None else str(len(paths))
    print(f"pre-deploy: testing {target[:12]} against deployed {deployed[:12]} "
          f"({n_paths} changed path(s), {path_reason})")

    pid = parent_pid if parent_pid is not None else os.getpid()
    return run_lanes(target, need_llm, llm_reason, parent_pid=pid, label="pre-deploy")


# --------------------------------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="test_gate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pre-commit", "pre-push", "pre-deploy"):
        p = sub.add_parser(name)
        p.add_argument("--worktree", type=Path, default=REPO_ROOT,
                        help="the worktree whose content to test (passed by the shim as the one git "
                             "invoked the hook for -- defaults to this checkout when omitted)")
        if name == "pre-deploy":
            p.add_argument("--target", required=True,
                            help="the commit about to be deployed (any rev; resolved to its full sha) -- "
                                 "tested in a scratch worktree")
            p.add_argument("--deployed", default=None,
                            help="the sha currently deployed (default: the MAIN checkout's HEAD, via "
                                 "main_checkout_root)")
    args = parser.parse_args(argv)

    _cleanup_dead_run_dirs()
    if args.command == "pre-commit":
        return cmd_pre_commit(args.worktree)
    if args.command == "pre-push":
        _cleanup_dead_scratch_worktrees()
        return cmd_pre_push(args.worktree)
    if args.command == "pre-deploy":
        _cleanup_dead_scratch_worktrees()
        return cmd_pre_deploy(args.worktree, args.target, args.deployed)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
