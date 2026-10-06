"""tools/test_gate.py: path classification, the LLM trigger, stamp handling, pre-push sha-range
computation from stdin (new-branch, ordinary-update and delete refs included), the scratch worktree,
the staged-index materialization, and the merge/cherry-pick skip. Lane running and git plumbing are
exercised through injected fakes wherever a fake will do (per the task's own requirement that this
suite never spawns a real pytest-in-pytest); ScratchWorktree and materialize_staged_index wrap real
git machinery closely enough that their tests run it for real, against a throwaway repo under
pytest's own tmp_path (this gate's own lane runs pytest with --basetemp under /dev/shm, so that is
where tmp_path lands too).
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import test_gate as G

ZERO40 = "0" * 40


# --- helpers: a real throwaway git repo -------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_throwaway_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def _commit_file(repo, name, content, message):
    full = repo / name
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                           capture_output=True, text=True).stdout.strip()


# --- path classification (pre-commit) --------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("backlog/tasks/task-1.md", True),
    ("docs/whatsapp.md", True),
    ("docs/sub/dir/file.txt", True),
    ("README.md", True),
    ("app/wa/api.py", False),
    ("tests/test_wa_router.py", False),
    ("backlog.py", False),  # not the backlog/ directory
    ("skill/SKILL.md", False),  # served to the live model -- not docs (Opus review m2)
])
def test_is_docs_only_path(path, expected):
    assert G.is_docs_only_path(path) is expected


def test_any_code_affecting_true_when_one_path_is_code():
    assert G.any_code_affecting(["docs/a.md", "app/wa/api.py"]) is True


def test_any_code_affecting_false_when_everything_is_docs_or_backlog():
    assert G.any_code_affecting(["backlog/tasks/t1.md", "docs/x.md", "CHANGES.md"]) is False


def test_any_code_affecting_false_on_empty_list():
    assert G.any_code_affecting([]) is False


def test_any_code_affecting_true_for_skill_md_alone():
    assert G.any_code_affecting(["skill/SKILL.md"]) is True


# --- pre-commit: merge/cherry-pick/revert/rebase skip (m1) -----------------------------------------

def test_merge_in_progress_detects_merge_head(tmp_path):
    (tmp_path / "MERGE_HEAD").write_text("deadbeef\n")
    assert G.merge_in_progress(tmp_path) == "a merge"


def test_merge_in_progress_detects_cherry_pick_head(tmp_path):
    (tmp_path / "CHERRY_PICK_HEAD").write_text("deadbeef\n")
    assert G.merge_in_progress(tmp_path) == "a cherry-pick"


def test_merge_in_progress_detects_revert_head(tmp_path):
    (tmp_path / "REVERT_HEAD").write_text("deadbeef\n")
    assert G.merge_in_progress(tmp_path) == "a revert"


def test_merge_in_progress_detects_rebase_merge_dir(tmp_path):
    (tmp_path / "rebase-merge").mkdir()
    assert G.merge_in_progress(tmp_path) == "a rebase"


def test_merge_in_progress_detects_rebase_apply_dir(tmp_path):
    (tmp_path / "rebase-apply").mkdir()
    assert G.merge_in_progress(tmp_path) == "a rebase"


def test_merge_in_progress_none_for_ordinary_state(tmp_path):
    assert G.merge_in_progress(tmp_path) is None


def test_merge_in_progress_none_during_amend_like_state(tmp_path):
    # --amend is an ordinary commit -- none of the in-progress markers exist for it.
    (tmp_path / "COMMIT_EDITMSG").write_text("amended message\n")
    assert G.merge_in_progress(tmp_path) is None


def test_git_dir_for_returns_this_repos_own_absolute_git_dir(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    git_dir = G._git_dir_for(repo)
    assert git_dir == repo / ".git"
    assert git_dir.is_absolute()


# --- pre-commit: materializing the staged index (m2) ------------------------------------------------

def test_materialize_staged_index_captures_staged_not_working_tree_content(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    _commit_file(repo, "a.txt", "committed\n", "initial")

    (repo / "a.txt").write_text("staged-version\n")
    _git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("working-tree-edit-never-staged\n")  # NOT git add'ed
    (repo / "untracked.txt").write_text("from another session\n")  # never indexed

    dest = tmp_path / "materialized"
    n = G.materialize_staged_index(repo, dest)

    assert (dest / "a.txt").read_text() == "staged-version\n"
    assert not (dest / "untracked.txt").exists()
    assert n == 1


def test_materialize_staged_index_creates_nested_directories(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    (repo / "app" / "wa").mkdir(parents=True)
    (repo / "app" / "wa" / "api.py").write_text("x = 1\n")
    _git(repo, "add", "app/wa/api.py")

    dest = tmp_path / "materialized"
    n = G.materialize_staged_index(repo, dest)

    assert (dest / "app" / "wa" / "api.py").read_text() == "x = 1\n"
    assert n == 1


def test_materialize_staged_index_excludes_a_staged_delete(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    _commit_file(repo, "gone.txt", "bye\n", "initial")
    _git(repo, "rm", "-q", "gone.txt")  # staged delete -- not in the index any more

    dest = tmp_path / "materialized"
    G.materialize_staged_index(repo, dest)
    assert not (dest / "gone.txt").exists()


# --- LLM-lane trigger --------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "app/wa/luna_brain.py",
    "app/wa/luna/campaign.py",
    "app/wa/api.py",
    "app/wa/slots.py",
    "app/wa/store.py",
    "app/data.py",
    "app/cv.py",
    "app/cv_extra.py",
    "prompts/system.txt",
    "evals/cv/cases.json",
    "skill/SKILL.md",
    "tests/test_wa_luna_e2e_funnel.py",
    "tests/test_cv_classify_document.py",
])
def test_touches_llm_paths_true_for_relevant_paths(path):
    assert G.touches_llm_paths([path]) is True


@pytest.mark.parametrize("path", [
    "app/wa/bridge.py",
    "app/wa/config.py",
    "tests/test_wa_router.py",
    "docs/whatsapp.md",
])
def test_touches_llm_paths_false_for_irrelevant_paths(path):
    assert G.touches_llm_paths([path]) is False


def test_touches_llm_paths_false_for_empty_set():
    assert G.touches_llm_paths([]) is False


def test_llm_lane_decision_env_force_run_wins_even_without_relevant_paths():
    run, reason = G.llm_lane_decision([], {"PFLEGE_GATE_LLM": "1"})
    assert run is True
    assert "forced" in reason


def test_llm_lane_decision_env_force_skip_wins_even_with_relevant_paths():
    run, reason = G.llm_lane_decision(["app/cv.py"], {"PFLEGE_GATE_LLM": "0"})
    assert run is False
    assert "forced" in reason


def test_llm_lane_decision_by_path_when_env_unset():
    run, reason = G.llm_lane_decision(["app/wa/luna/campaign.py"], {})
    assert run is True
    assert "LLM-relevant" in reason


def test_llm_lane_decision_skip_when_no_relevant_path_and_env_unset():
    run, reason = G.llm_lane_decision(["app/wa/bridge.py"], {})
    assert run is False
    assert "no LLM-relevant" in reason


def test_llm_lane_decision_runs_when_change_set_unknown():
    run, reason = G.llm_lane_decision(None, {})
    assert run is True
    assert "unknown" in reason


# --- pre-push stdin parsing --------------------------------------------------------------------------

def test_parse_pre_push_stdin_single_existing_branch_update():
    # Real git order: "<local ref> <local sha1> <remote ref> <remote sha1>" -- local is the commit
    # being pushed (new), remote is what is already there (old).
    text = "refs/heads/main newsha2222222222222222222222222222222222 refs/heads/main " \
           "oldsha1111111111111111111111111111111111\n"
    updates = G.parse_pre_push_stdin(text)
    assert len(updates) == 1
    u = updates[0]
    assert u.local_ref == "refs/heads/main"
    assert u.local_sha == "newsha2222222222222222222222222222222222"
    assert u.remote_ref == "refs/heads/main"
    assert u.remote_sha == "oldsha1111111111111111111111111111111111"


def test_parse_pre_push_stdin_multiple_refs():
    text = (
        "refs/heads/a sha_a1111111111111111111111111111111111111 refs/heads/a "
        "sha_a0000000000000000000000000000000000000\n"
        "refs/heads/b sha_b1111111111111111111111111111111111111 refs/heads/b "
        "sha_b0000000000000000000000000000000000000\n"
    )
    updates = G.parse_pre_push_stdin(text)
    assert [u.local_ref for u in updates] == ["refs/heads/a", "refs/heads/b"]


def test_parse_pre_push_stdin_ignores_trailing_blank_lines():
    text = f"refs/heads/x {ZERO40} refs/heads/x {ZERO40}\n\n"
    updates = G.parse_pre_push_stdin(text)
    assert len(updates) == 1


def test_parse_pre_push_stdin_rejects_malformed_line():
    with pytest.raises(ValueError, match="malformed"):
        G.parse_pre_push_stdin("only three fields\n")


def test_is_delete_true_when_local_sha_is_zero():
    ref = G.RefUpdate("refs/heads/x", ZERO40, "refs/heads/x", "a" * 40)
    assert G.is_delete(ref) is True
    assert G.is_new_branch(ref) is False


def test_is_new_branch_true_when_remote_sha_is_zero_and_not_a_delete():
    ref = G.RefUpdate("refs/heads/x", "a" * 40, "refs/heads/x", ZERO40)
    assert G.is_new_branch(ref) is True
    assert G.is_delete(ref) is False


def test_is_new_branch_false_for_ordinary_update():
    ref = G.RefUpdate("refs/heads/x", "a" * 40, "refs/heads/x", "b" * 40)
    assert G.is_new_branch(ref) is False
    assert G.is_delete(ref) is False


def test_commits_in_range_empty_for_a_delete():
    ref = G.RefUpdate("refs/heads/x", ZERO40, "refs/heads/x", "a" * 40)
    calls = []

    def fake_git_run(args):
        calls.append(args)
        return ["should-never-be-reached"]

    assert G.commits_in_range(ref, fake_git_run) == []
    assert calls == []  # a delete never shells out


def test_commits_in_range_existing_branch_uses_dotdot_range():
    ref = G.RefUpdate("refs/heads/x", "new1", "refs/heads/x", "old1")
    seen = {}

    def fake_git_run(args):
        seen["args"] = args
        return ["c1", "c2", ""]

    out = G.commits_in_range(ref, fake_git_run)
    assert seen["args"] == ["rev-list", "old1..new1"]
    assert out == ["c1", "c2"]  # blank line dropped


def test_commits_in_range_new_branch_excludes_remotes():
    ref = G.RefUpdate("refs/heads/x", "new1", "refs/heads/x", ZERO40)
    seen = {}

    def fake_git_run(args):
        seen["args"] = args
        return ["c1"]

    out = G.commits_in_range(ref, fake_git_run)
    assert seen["args"] == ["rev-list", "new1", "--not", "--remotes"]
    assert out == ["c1"]


def test_changed_paths_in_range_unions_every_commit_diff_tree():
    ref = G.RefUpdate("refs/heads/x", "new1", "refs/heads/x", "old1")

    def fake_git_run(args):
        if args[0] == "rev-list":
            return ["c1", "c2"]
        assert args[0] == "diff-tree"
        commit = args[-1]
        return {"c1": ["app/a.py", "app/b.py"], "c2": ["app/b.py", "docs/x.md"]}[commit]

    paths = G.changed_paths_in_range(ref, fake_git_run)
    assert paths == {"app/a.py", "app/b.py", "docs/x.md"}


def test_changed_paths_in_range_empty_when_no_commits():
    ref = G.RefUpdate("refs/heads/x", "same1", "refs/heads/x", "same1")

    def fake_git_run(args):
        return []

    assert G.changed_paths_in_range(ref, fake_git_run) == set()


# --- pre-push: determine_changed_paths (m3) ----------------------------------------------------------

def test_determine_changed_paths_ordinary_update_diffs_remote_sha_dot_dot_local_sha():
    ref = G.RefUpdate("refs/heads/x", "new1", "refs/heads/x", "old1")
    seen = {}

    def fake_git_run(args):
        seen["args"] = args
        return ["app/a.py", "", "app/merge_resolution.py"]

    paths, reason = G.determine_changed_paths(ref, fake_git_run)
    assert seen["args"] == ["diff", "--name-only", "old1", "new1"]
    assert paths == {"app/a.py", "app/merge_resolution.py"}
    assert "diff" in reason


def test_determine_changed_paths_new_branch_keeps_merge_base_logic():
    ref = G.RefUpdate("refs/heads/x", "new1", "refs/heads/x", ZERO40)

    def fake_git_run(args):
        if args[0] == "rev-list":
            return ["c1"]
        assert args[0] == "diff-tree"
        return ["app/new.py"]

    paths, reason = G.determine_changed_paths(ref, fake_git_run)
    assert paths == {"app/new.py"}
    assert "new branch" in reason


def test_determine_changed_paths_unknown_when_remote_sha_not_present_locally():
    ref = G.RefUpdate("refs/heads/x", "new1", "refs/heads/x", "old1")

    def fake_git_run(args):
        raise subprocess.CalledProcessError(128, args)

    paths, reason = G.determine_changed_paths(ref, fake_git_run)
    assert paths is None
    assert "unknown" in reason
    assert "old1"[:12] in reason or "old1" in reason


# --- stamp handling -----------------------------------------------------------------------------------

def test_write_then_read_stamp_round_trips(tmp_path):
    sha = "deadbeef" * 5
    G.write_stamp(sha, {"offline": {"passed": True, "duration_s": 1.2}}, stamp_dir=tmp_path)
    stamp = G.read_stamp(sha, stamp_dir=tmp_path)
    assert stamp["sha"] == sha
    assert stamp["lanes"]["offline"]["passed"] is True


def test_read_stamp_missing_returns_none(tmp_path):
    assert G.read_stamp("nosuchsha", stamp_dir=tmp_path) is None


def test_stamp_path_is_named_by_sha(tmp_path):
    p = G.stamp_path("abc123", stamp_dir=tmp_path)
    assert p.name == "abc123.json"
    assert p.parent == tmp_path


def test_stamp_covers_false_when_no_stamp():
    assert G.stamp_covers(None, need_llm=False) is False


def test_stamp_covers_true_when_offline_passed_and_llm_not_needed():
    stamp = {"lanes": {"offline": {"passed": True}}}
    assert G.stamp_covers(stamp, need_llm=False) is True


def test_stamp_covers_false_when_offline_failed():
    stamp = {"lanes": {"offline": {"passed": False}}}
    assert G.stamp_covers(stamp, need_llm=False) is False


def test_stamp_covers_false_when_llm_needed_but_not_stamped():
    stamp = {"lanes": {"offline": {"passed": True}}}
    assert G.stamp_covers(stamp, need_llm=True) is False


def test_stamp_covers_true_when_llm_needed_and_both_lanes_passed():
    stamp = {"lanes": {"offline": {"passed": True}, "llm": {"passed": True}}}
    assert G.stamp_covers(stamp, need_llm=True) is True


def test_stamp_covers_false_when_llm_needed_and_llm_failed():
    stamp = {"lanes": {"offline": {"passed": True}, "llm": {"passed": False}}}
    assert G.stamp_covers(stamp, need_llm=True) is False


def test_write_stamp_creates_the_directory(tmp_path):
    target = tmp_path / "nested" / "state"
    assert not target.exists()
    G.write_stamp("sha1", {"offline": {"passed": True}}, stamp_dir=target)
    assert target.exists()
    assert json.loads((target / "sha1.json").read_text())["sha"] == "sha1"


def test_write_stamp_records_failed_parent_pid_when_given(tmp_path):
    G.write_stamp("sha1", {"offline": {"passed": False}}, stamp_dir=tmp_path, failed_parent_pid=4242)
    stamp = G.read_stamp("sha1", stamp_dir=tmp_path)
    assert stamp["failed_parent_pid"] == 4242


def test_write_stamp_omits_failed_parent_pid_when_not_given(tmp_path):
    G.write_stamp("sha1", {"offline": {"passed": True}}, stamp_dir=tmp_path)
    stamp = G.read_stamp("sha1", stamp_dir=tmp_path)
    assert "failed_parent_pid" not in stamp


# --- stamp handling: the m4 fail-stamp ---------------------------------------------------------------

def test_stamp_failed_this_push_false_when_no_stamp():
    assert G.stamp_failed_this_push(None, 123) is False


def test_stamp_failed_this_push_false_when_offline_passed():
    stamp = {"lanes": {"offline": {"passed": True}}, "failed_parent_pid": 123}
    assert G.stamp_failed_this_push(stamp, 123) is False


def test_stamp_failed_this_push_true_when_same_parent_pid():
    stamp = {"lanes": {"offline": {"passed": False}}, "failed_parent_pid": 123}
    assert G.stamp_failed_this_push(stamp, 123) is True


def test_stamp_failed_this_push_false_when_different_parent_pid():
    stamp = {"lanes": {"offline": {"passed": False}}, "failed_parent_pid": 123}
    assert G.stamp_failed_this_push(stamp, 999) is False


# --- /dev/shm dead-pid sweeps (B2) ---------------------------------------------------------------------

def test_pid_is_dead_true_for_a_pid_that_almost_certainly_does_not_exist():
    assert G._pid_is_dead(2**30) is True


def test_pid_is_dead_false_for_our_own_pid():
    assert G._pid_is_dead(os.getpid()) is False


def test_cleanup_dead_run_dirs_removes_only_dead_pid_leftovers(tmp_path):
    dead = tmp_path / f"run-{2**30}-abc"
    dead.mkdir()
    live = tmp_path / f"run-{os.getpid()}-def"
    live.mkdir()
    unrelated = tmp_path / "not-a-run-dir"
    unrelated.mkdir()

    G._cleanup_dead_run_dirs(tmp_path)

    assert not dead.exists()
    assert live.exists()
    assert unrelated.exists()


def test_cleanup_dead_run_dirs_noop_when_root_missing(tmp_path):
    G._cleanup_dead_run_dirs(tmp_path / "does-not-exist")  # must not raise


# --- lane running, with an injected runner (no real pytest sub-run) ---------------------------------

def _fake_proc(returncode, stdout=""):
    class P:
        pass
    p = P()
    p.returncode = returncode
    p.stdout = stdout
    return p


def test_run_offline_lane_passes_through_a_zero_exit(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    calls = []

    def fake_runner(cmd, cwd, env):
        calls.append((cmd, cwd))
        return _fake_proc(0, "3 passed in 0.1s\n")

    result = G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner,
                                 scratch_root=tmp_path / "scratch")
    assert result.passed is True
    assert result.name == "offline"
    assert result.failing_tests == []
    cmd, cwd = calls[0]
    assert cwd == tmp_path
    assert "-x" in cmd
    assert str(tmp_path / "tests" / "test_wa_x.py") not in cmd  # paths are relative, not absolute
    assert "tests/test_wa_x.py" in cmd


def test_run_offline_lane_parses_failing_test_ids(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    stdout = (
        "FAILED tests/test_wa_x.py::test_one - AssertionError\n"
        "FAILED tests/test_wa_x.py::test_two\n"
        "1 failed, 1 passed in 0.2s\n"
    )

    def fake_runner(cmd, cwd, env):
        return _fake_proc(1, stdout)

    result = G.run_offline_lane(tmp_path, fail_fast=False, runner=fake_runner,
                                 scratch_root=tmp_path / "scratch")
    assert result.passed is False
    assert result.failing_tests == ["tests/test_wa_x.py::test_one", "tests/test_wa_x.py::test_two"]


def test_run_offline_lane_strips_live_credential_vars_before_running(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    seen_env = {}

    def fake_runner(cmd, cwd, env):
        seen_env.update(env)
        return _fake_proc(0, "")

    base_env = {"WA_TRANSPORT": "bridge", "WA_BRIDGE_TOKEN": "secret", "PATH": "/usr/bin"}
    G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner, base_env=base_env,
                        scratch_root=tmp_path / "scratch")
    assert "WA_TRANSPORT" not in seen_env
    assert "WA_BRIDGE_TOKEN" not in seen_env
    assert seen_env["PATH"] == "/usr/bin"


def test_run_offline_lane_strips_git_plumbing_vars_before_running(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    seen_env = {}

    def fake_runner(cmd, cwd, env):
        seen_env.update(env)
        return _fake_proc(0, "")

    base_env = {"GIT_DIR": "/repo/.git", "GIT_INDEX_FILE": "/repo/.git/index", "PATH": "/usr/bin"}
    G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner, base_env=base_env,
                        scratch_root=tmp_path / "scratch")
    assert "GIT_DIR" not in seen_env
    assert "GIT_INDEX_FILE" not in seen_env
    assert seen_env["PATH"] == "/usr/bin"


def test_run_offline_lane_raises_loudly_when_no_test_files_resolve(tmp_path):
    (tmp_path / "tests").mkdir()
    with pytest.raises(RuntimeError, match="zero test files"):
        G.run_offline_lane(tmp_path, fail_fast=True, runner=lambda *a, **k: _fake_proc(0),
                            scratch_root=tmp_path / "scratch")


# --- B2: unique, self-cleaning basetemp per lane run -------------------------------------------------

def test_run_offline_lane_basetemp_exists_during_the_run_and_is_removed_after(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    seen = {}

    def fake_runner(cmd, cwd, env):
        bt = Path(cmd[cmd.index("--basetemp") + 1])
        seen["basetemp"] = bt
        assert bt.exists()  # created before the lane runs
        return _fake_proc(0, "")

    G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner, scratch_root=tmp_path / "scratch")
    assert not seen["basetemp"].exists()  # removed in the finally


def test_run_offline_lane_basetemp_removed_even_when_runner_raises(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    seen = {}

    def fake_runner(cmd, cwd, env):
        seen["basetemp"] = Path(cmd[cmd.index("--basetemp") + 1])
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner, scratch_root=tmp_path / "scratch")
    assert not seen["basetemp"].exists()


def test_run_offline_lane_basetemp_is_unique_per_call(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wa_x.py").write_text("")
    seen = []

    def fake_runner(cmd, cwd, env):
        seen.append(cmd[cmd.index("--basetemp") + 1])
        return _fake_proc(0, "")

    G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner, scratch_root=tmp_path / "scratch")
    G.run_offline_lane(tmp_path, fail_fast=True, runner=fake_runner, scratch_root=tmp_path / "scratch")
    assert seen[0] != seen[1]


# --- run_llm_lane helpers ------------------------------------------------------------------------------

def _dummy_executable(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _junit_path_from_cmd(cmd):
    for arg in cmd:
        if arg.startswith("--junitxml="):
            return Path(arg[len("--junitxml="):])
    raise AssertionError(f"no --junitxml= in {cmd!r}")


def _write_junit(path, total, skipped, skip_details=()):
    cases = "".join(
        f'<testcase classname="{tid.rsplit("::", 1)[0]}" name="{tid.rsplit("::", 1)[-1]}">'
        f'<skipped message="{reason}"/></testcase>'
        for tid, reason in skip_details
    )
    path.write_text(
        f'<testsuites><testsuite tests="{total}" skipped="{skipped}">{cases}</testsuite></testsuites>'
    )


def test_run_llm_lane_fails_loudly_when_token_file_missing(tmp_path):
    missing = tmp_path / "no-such-token"
    with pytest.raises(RuntimeError, match="missing"):
        G.run_llm_lane(tmp_path, runner=lambda *a, **k: _fake_proc(0), token_file=missing)


def test_run_llm_lane_fails_loudly_when_token_file_empty(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("   \n")
    with pytest.raises(RuntimeError, match="empty"):
        G.run_llm_lane(tmp_path, runner=lambda *a, **k: _fake_proc(0), token_file=token_file)


def test_run_llm_lane_puts_the_token_in_env_and_never_logs_it(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("sekrit-token-value\n")
    claude_bin = _dummy_executable(tmp_path / "claude")
    seen_env = {}

    def fake_runner(cmd, cwd, env):
        seen_env.update(env)
        assert "sekrit-token-value" not in cmd  # never on the command line either
        _write_junit(_junit_path_from_cmd(cmd), total=73, skipped=0)
        return _fake_proc(0, "73 passed in 1s\n")

    result = G.run_llm_lane(tmp_path, runner=fake_runner,
                             base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin)},
                             token_file=token_file, scratch_root=tmp_path / "scratch")
    assert result.passed is True
    assert seen_env["CLAUDE_CODE_OAUTH_TOKEN"] == "sekrit-token-value"


def test_run_llm_lane_uses_a_throwaway_claude_config_dir_inside_the_run_dir(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")
    scratch = tmp_path / "scratch"
    seen = {}

    def fake_runner(cmd, cwd, env):
        seen["config"] = Path(env["CLAUDE_CONFIG_DIR"])
        seen["existed"] = seen["config"].is_dir()
        _write_junit(_junit_path_from_cmd(cmd), total=1, skipped=0)
        return _fake_proc(0, "")

    G.run_llm_lane(tmp_path, runner=fake_runner,
                    base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin), "CLAUDE_CONFIG_DIR": "/home/x/.claude"},
                    token_file=token_file, scratch_root=scratch)
    assert seen["existed"] and scratch in seen["config"].parents
    assert not seen["config"].exists()  # removed with the run dir


def test_run_llm_lane_strips_live_credential_vars(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")
    seen_env = {}

    def fake_runner(cmd, cwd, env):
        seen_env.update(env)
        _write_junit(_junit_path_from_cmd(cmd), total=1, skipped=0)
        return _fake_proc(0, "")

    base_env = {"WA_AUTOSEND": "1", "PATH": "/usr/bin", "WA_LUNA_CLAUDE_BIN": str(claude_bin)}
    G.run_llm_lane(tmp_path, runner=fake_runner, base_env=base_env, token_file=token_file,
                    scratch_root=tmp_path / "scratch")
    assert "WA_AUTOSEND" not in seen_env
    assert seen_env["PATH"] == "/usr/bin"


def test_run_llm_lane_targets_exactly_the_known_llm_files(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")
    seen_cmd = []

    def fake_runner(cmd, cwd, env):
        seen_cmd.extend(cmd)
        _write_junit(_junit_path_from_cmd(cmd), total=73, skipped=0)
        return _fake_proc(0, "")

    G.run_llm_lane(tmp_path, runner=fake_runner, base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin)},
                    token_file=token_file, scratch_root=tmp_path / "scratch")
    for f in G.LLM_LANE_FILES:
        assert f in seen_cmd
    assert "-m" in seen_cmd and "llm" in seen_cmd


# --- M2: the claude-bin resolution and the junit-report completeness check --------------------------

def test_run_llm_lane_fails_loudly_when_claude_bin_does_not_resolve_on_path(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    with pytest.raises(RuntimeError, match="resolve"):
        G.run_llm_lane(tmp_path, runner=lambda *a, **k: _fake_proc(0), token_file=token_file,
                        base_env={"WA_LUNA_CLAUDE_BIN": "no-such-claude-binary-xyz", "PATH": ""},
                        scratch_root=tmp_path / "scratch")


def test_run_llm_lane_fails_when_everything_is_skipped(tmp_path, capsys):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")

    def fake_runner(cmd, cwd, env):
        _write_junit(_junit_path_from_cmd(cmd), total=73, skipped=73,
                     skip_details=[("tests.test_cv_intake::test_a", "claude not on PATH")])
        return _fake_proc(0, "73 skipped in 1s\n")  # pytest exits 0 when everything is skipped

    result = G.run_llm_lane(tmp_path, runner=fake_runner,
                             base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin)},
                             token_file=token_file, scratch_root=tmp_path / "scratch")
    assert result.passed is False
    assert "test_a" in capsys.readouterr().out


def test_run_llm_lane_fails_when_junit_reports_zero_tests_collected(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")

    def fake_runner(cmd, cwd, env):
        _write_junit(_junit_path_from_cmd(cmd), total=0, skipped=0)
        return _fake_proc(0, "no tests ran\n")

    result = G.run_llm_lane(tmp_path, runner=fake_runner,
                             base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin)},
                             token_file=token_file, scratch_root=tmp_path / "scratch")
    assert result.passed is False


def test_run_llm_lane_raises_loudly_when_junit_report_missing(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")

    def fake_runner(cmd, cwd, env):
        return _fake_proc(0, "")  # never wrote the junit report

    with pytest.raises(RuntimeError, match="junit"):
        G.run_llm_lane(tmp_path, runner=fake_runner, base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin)},
                        token_file=token_file, scratch_root=tmp_path / "scratch")


def test_run_llm_lane_still_fails_on_a_genuine_test_failure(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("tok\n")
    claude_bin = _dummy_executable(tmp_path / "claude")

    def fake_runner(cmd, cwd, env):
        # a real failure never even reaches the junit completeness check
        return _fake_proc(1, "FAILED tests/test_cv_intake.py::test_a - AssertionError\n")

    result = G.run_llm_lane(tmp_path, runner=fake_runner,
                             base_env={"WA_LUNA_CLAUDE_BIN": str(claude_bin)},
                             token_file=token_file, scratch_root=tmp_path / "scratch")
    assert result.passed is False
    assert result.failing_tests == ["tests/test_cv_intake.py::test_a"]


# --- main_checkout_root / main_checkout_head, against a real throwaway git repo + linked worktree ---

def _add_linked_worktree(repo, worktree_path, sha):
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree_path), sha],
                    cwd=repo, check=True, capture_output=True, text=True)


def test_main_checkout_root_from_the_main_checkout_itself_is_itself(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    _commit_file(repo, "a.txt", "one\n", "first")
    assert G.main_checkout_root(repo) == repo.resolve()


def test_main_checkout_root_from_a_linked_worktree_is_the_main_checkout(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    worktree = tmp_path / "linked-worktree"
    _add_linked_worktree(repo, worktree, sha1)
    assert G.main_checkout_root(worktree) == repo.resolve()


def test_main_checkout_head_reads_the_main_checkouts_head_not_the_worktrees(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    sha2 = _commit_file(repo, "a.txt", "two\n", "second")
    # a linked worktree detached at the OLDER sha -- main_checkout_head must still report sha2, the
    # main checkout's own HEAD, not whatever this worktree happens to have checked out.
    worktree = tmp_path / "linked-worktree"
    _add_linked_worktree(repo, worktree, sha1)
    assert G.main_checkout_head(worktree) == sha2
    assert G.main_checkout_head(repo) == sha2


# --- M4 / ScratchWorktree, against a real throwaway git repo ----------------------------------------

def test_scratch_worktree_checks_out_the_given_sha(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    _commit_file(repo, "a.txt", "two\n", "second")

    with G.ScratchWorktree(sha1, root=tmp_path / "worktrees", repo_root=repo) as path:
        checked_out = path
        assert (path / "a.txt").read_text() == "one\n"
    assert not checked_out.exists()  # removed on exit


def test_scratch_worktree_removed_even_on_exception(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    captured = {}

    with pytest.raises(ValueError):
        with G.ScratchWorktree(sha1, root=tmp_path / "worktrees", repo_root=repo) as path:
            captured["path"] = path
            raise ValueError("boom")
    assert not captured["path"].exists()


def test_scratch_worktree_path_encodes_owning_pid(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    sw = G.ScratchWorktree(sha1, root=tmp_path / "worktrees", repo_root=repo)
    assert sw.path.name == f"pid-{os.getpid()}-{sha1}"


def test_scratch_worktree_reusable_after_a_clean_exit(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    worktrees_root = tmp_path / "worktrees"

    with G.ScratchWorktree(sha1, root=worktrees_root, repo_root=repo):
        pass
    with G.ScratchWorktree(sha1, root=worktrees_root, repo_root=repo) as path2:
        assert (path2 / "a.txt").read_text() == "one\n"


def test_scratch_worktree_prints_git_stderr_on_add_failure(tmp_path, capsys):
    repo = _make_throwaway_repo(tmp_path / "repo")
    with pytest.raises(RuntimeError, match="git worktree add"):
        with G.ScratchWorktree("not-a-real-sha", root=tmp_path / "worktrees", repo_root=repo):
            pass
    assert capsys.readouterr().err  # git's own stderr was printed, not swallowed


def test_cleanup_dead_scratch_worktrees_removes_dead_pid_leftover(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    worktrees_root = tmp_path / "worktrees"
    worktrees_root.mkdir(parents=True)

    # Simulate a killed run: __enter__'s effects exist (dir + registration), __exit__ never ran.
    dead_pid = 2**30
    leftover = worktrees_root / f"pid-{dead_pid}-{sha1}"
    subprocess.run(["git", "worktree", "add", "--detach", str(leftover), sha1],
                    cwd=repo, check=True, capture_output=True, text=True)
    assert leftover.exists()

    G._cleanup_dead_scratch_worktrees(root=worktrees_root, repo_root=repo)
    assert not leftover.exists()

    # the registration is gone too -- a fresh add at the very same sha-derived path now works
    with G.ScratchWorktree(sha1, root=worktrees_root, repo_root=repo) as path:
        assert path.name == f"pid-{os.getpid()}-{sha1}"


def test_cleanup_dead_scratch_worktrees_leaves_a_live_pids_worktree_alone(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    worktrees_root = tmp_path / "worktrees"
    with G.ScratchWorktree(sha1, root=worktrees_root, repo_root=repo) as path:
        G._cleanup_dead_scratch_worktrees(root=worktrees_root, repo_root=repo)
        assert path.exists()  # our own pid is alive -- never swept mid-use


def test_cleanup_dead_scratch_worktrees_noop_when_root_missing(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    G._cleanup_dead_scratch_worktrees(root=tmp_path / "does-not-exist", repo_root=repo)  # no raise


# --- _run_lanes_for_sha, with fakes (m4 fail-stamp) --------------------------------------------------

def _fake_lane_result(name, passed, duration_s=0.1, failing_tests=()):
    return G.LaneResult(name, passed, duration_s, 0 if passed else 1, list(failing_tests), "")


class _FakeScratch:
    """Stands in for ScratchWorktree: no git, no filesystem -- just a context manager."""

    def __init__(self, sha):
        self.sha = sha

    def __enter__(self):
        return Path(f"/fake/scratch/{self.sha}")

    def __exit__(self, *exc_info):
        return False


def test_run_lanes_for_sha_skips_llm_lane_when_offline_failed(tmp_path, capsys):
    calls = []

    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", False, failing_tests=["tests/x.py::t"])

    def llm_lane(scratch):
        calls.append("llm")
        return _fake_lane_result("llm", True)

    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=True, llm_reason="touches luna",
        parent_pid=111, stamp_dir=tmp_path, log_dir=tmp_path, scratch_factory=_FakeScratch,
        offline_lane=offline_lane, llm_lane=llm_lane,
    )
    assert rc == 1
    assert calls == []  # the LLM lane never ran (Opus review m4)
    assert "offline lane failed" in capsys.readouterr().out


def test_run_lanes_for_sha_runs_llm_lane_when_offline_passed_and_needed(tmp_path):
    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", True)

    def llm_lane(scratch):
        return _fake_lane_result("llm", True)

    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=True, llm_reason="touches luna",
        parent_pid=111, stamp_dir=tmp_path, scratch_factory=_FakeScratch,
        offline_lane=offline_lane, llm_lane=llm_lane,
    )
    assert rc == 0
    stamp = G.read_stamp("deadbeef", stamp_dir=tmp_path)
    assert stamp["lanes"]["offline"]["passed"] is True
    assert stamp["lanes"]["llm"]["passed"] is True
    assert "failed_parent_pid" not in stamp


def test_run_lanes_for_sha_skips_llm_lane_for_a_path_reason_when_offline_passed(tmp_path, capsys):
    calls = []

    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", True)

    def llm_lane(scratch):
        calls.append("llm")
        return _fake_lane_result("llm", True)

    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=False, llm_reason="no LLM-relevant path in the pushed range",
        parent_pid=111, stamp_dir=tmp_path, scratch_factory=_FakeScratch,
        offline_lane=offline_lane, llm_lane=llm_lane,
    )
    assert rc == 0
    assert calls == []
    assert "no LLM-relevant path" in capsys.readouterr().out


def test_run_lanes_for_sha_writes_failed_parent_pid_on_failure(tmp_path):
    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", False)

    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=False, llm_reason="no LLM-relevant path",
        parent_pid=222, stamp_dir=tmp_path, log_dir=tmp_path, scratch_factory=_FakeScratch,
        offline_lane=offline_lane, llm_lane=lambda scratch: _fake_lane_result("llm", True),
    )
    assert rc == 1
    stamp = G.read_stamp("deadbeef", stamp_dir=tmp_path)
    assert stamp["failed_parent_pid"] == 222


def test_run_lanes_for_sha_second_push_url_fails_immediately_without_rerunning(tmp_path):
    calls = []

    def offline_lane(scratch, *, fail_fast):
        calls.append("ran")
        return _fake_lane_result("offline", False)

    kwargs = dict(stamp_dir=tmp_path, log_dir=tmp_path, scratch_factory=_FakeScratch,
                  offline_lane=offline_lane, llm_lane=lambda s: _fake_lane_result("llm", True))

    # first push-URL firing: runs for real, fails, stamps failed_parent_pid=333
    rc1 = G._run_lanes_for_sha("deadbeef", need_llm=False, llm_reason="x", parent_pid=333, **kwargs)
    assert rc1 == 1
    assert calls == ["ran"]

    # second push-URL firing of the SAME `git push` (same parent pid): fails immediately, no re-run
    rc2 = G._run_lanes_for_sha("deadbeef", need_llm=False, llm_reason="x", parent_pid=333, **kwargs)
    assert rc2 == 1
    assert calls == ["ran"]


def test_run_lanes_for_sha_a_later_new_push_of_the_same_sha_reruns_from_scratch(tmp_path):
    calls = []

    def offline_lane(scratch, *, fail_fast):
        calls.append("ran")
        return _fake_lane_result("offline", False)

    kwargs = dict(stamp_dir=tmp_path, log_dir=tmp_path, scratch_factory=_FakeScratch,
                  offline_lane=offline_lane, llm_lane=lambda s: _fake_lane_result("llm", True))

    G._run_lanes_for_sha("deadbeef", need_llm=False, llm_reason="x", parent_pid=333, **kwargs)
    # a brand-new `git push` (different parent pid) is not covered by the earlier FAILED stamp
    G._run_lanes_for_sha("deadbeef", need_llm=False, llm_reason="x", parent_pid=444, **kwargs)
    assert calls == ["ran", "ran"]


def test_run_lanes_for_sha_skips_entirely_when_an_already_passed_stamp_covers(tmp_path):
    calls = []
    G.write_stamp("deadbeef", {"offline": {"passed": True}}, stamp_dir=tmp_path)

    def offline_lane(scratch, *, fail_fast):
        calls.append("ran")
        return _fake_lane_result("offline", True)

    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=False, llm_reason="x", parent_pid=1, stamp_dir=tmp_path,
        scratch_factory=_FakeScratch, offline_lane=offline_lane,
        llm_lane=lambda s: _fake_lane_result("llm", True),
    )
    assert rc == 0
    assert calls == []  # a PASSED stamp still short-circuits exactly as before (unchanged behavior)


# --- _run_lanes_for_sha: failed-lane log file (Ivan, 2026-10-05 -- a failed 44-min LLM lane's output
# used to be lost with the scratch dir, forcing a blind rerun) ---------------------------------------

def test_run_lanes_for_sha_saves_offline_lane_log_on_failure_and_prints_path(tmp_path, capsys):
    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", False, failing_tests=["tests/x.py::t"])

    log_dir = tmp_path / "logs"
    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=False, llm_reason="x", parent_pid=1, stamp_dir=tmp_path,
        log_dir=log_dir, scratch_factory=_FakeScratch, offline_lane=offline_lane,
        llm_lane=lambda s: _fake_lane_result("llm", True),
    )
    assert rc == 1
    log_path = log_dir / "deadbeef-offline.log"
    assert log_path.exists()
    assert f"full offline lane output saved to {log_path}" in capsys.readouterr().out


def test_run_lanes_for_sha_saves_llm_lane_log_on_failure_and_prints_path(tmp_path, capsys):
    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", True)

    def llm_lane(scratch):
        return G.LaneResult("llm", False, 0.1, 1, ["tests/llm_x.py::t"], "the real llm stdout\n")

    log_dir = tmp_path / "logs"
    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=True, llm_reason="touches luna", parent_pid=1, stamp_dir=tmp_path,
        log_dir=log_dir, scratch_factory=_FakeScratch, offline_lane=offline_lane, llm_lane=llm_lane,
    )
    assert rc == 1
    log_path = log_dir / "deadbeef-llm.log"
    assert log_path.read_text() == "the real llm stdout\n"
    assert f"full llm lane output saved to {log_path}" in capsys.readouterr().out


def test_run_lanes_for_sha_does_not_save_a_log_for_a_passing_lane(tmp_path):
    def offline_lane(scratch, *, fail_fast):
        return _fake_lane_result("offline", True)

    log_dir = tmp_path / "logs"
    rc = G._run_lanes_for_sha(
        "deadbeef", need_llm=False, llm_reason="x", parent_pid=1, stamp_dir=tmp_path,
        log_dir=log_dir, scratch_factory=_FakeScratch, offline_lane=offline_lane,
        llm_lane=lambda s: _fake_lane_result("llm", True),
    )
    assert rc == 0
    assert not log_dir.exists()


# --- cmd_pre_push, with fakes -- offline lane only, LLM lane moved to pre-deploy -------------------

def test_cmd_pre_push_runs_lanes_for_each_non_delete_ref_in_order():
    seen_shas = []

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid):
        seen_shas.append(sha)
        return 0

    text = (
        "refs/heads/a sha_a1111111111111111111111111111111111111 refs/heads/a "
        "sha_a0000000000000000000000000000000000000\n"
        "refs/heads/b sha_b1111111111111111111111111111111111111 refs/heads/b "
        "sha_b0000000000000000000000000000000000000\n"
    )
    rc = G.cmd_pre_push(Path("/unused"), stdin_text=text, parent_pid=999, run_lanes=fake_run_lanes)
    assert rc == 0
    assert seen_shas == ["sha_a1111111111111111111111111111111111111",
                          "sha_b1111111111111111111111111111111111111"]


def test_cmd_pre_push_skips_delete_refs_without_running_lanes():
    text = f"refs/heads/x {ZERO40} refs/heads/x oldsha1111111111111111111111111111111111\n"
    calls = []
    rc = G.cmd_pre_push(Path("/unused"), stdin_text=text,
                        parent_pid=1, run_lanes=lambda *a, **k: calls.append(a) or 0)
    assert rc == 0
    assert calls == []


def test_cmd_pre_push_nonzero_exit_when_any_ref_fails():
    text = (
        "refs/heads/a newa1111111111111111111111111111111111111 refs/heads/a "
        "olda0000000000000000000000000000000000000\n"
        "refs/heads/b newb1111111111111111111111111111111111111 refs/heads/b "
        "oldb0000000000000000000000000000000000000\n"
    )
    results = iter([0, 1])
    rc = G.cmd_pre_push(Path("/unused"), stdin_text=text,
                        parent_pid=1, run_lanes=lambda *a, **k: next(results))
    assert rc == 1


def test_cmd_pre_push_no_refs_on_stdin_returns_zero_without_running_lanes():
    def fail_if_called(*a, **k):
        raise AssertionError("run_lanes should not run with no refs")

    rc = G.cmd_pre_push(Path("/unused"), stdin_text="", parent_pid=1, run_lanes=fail_if_called)
    assert rc == 0


def test_cmd_pre_push_never_requests_the_llm_lane():
    """pre-push dropped the LLM-lane trigger entirely (Ivan, 2026-10-05): it no longer looks at
    touched paths or PFLEGE_GATE_LLM at all -- need_llm is unconditionally False, every time. The
    LLM lane now only runs via pre-deploy."""
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid):
        seen["need_llm"] = need_llm
        seen["reason"] = llm_reason
        return 0

    text = "refs/heads/a newa1111111111111111111111111111111111111 refs/heads/a " \
           "olda0000000000000000000000000000000000000\n"
    G.cmd_pre_push(Path("/unused"), stdin_text=text, parent_pid=1, run_lanes=fake_run_lanes)
    assert seen["need_llm"] is False
    assert "pre-deploy" in seen["reason"]


# --- cmd_pre_deploy, with fakes -- offline lane only by default (Ivan, 2026-10-06: the LLM lane
# moved off pre-deploy too, onto cmd_nightly below) -------------------------------------------------

def _same_rev(rev):
    """Stands in for resolve_commit where the test's fake shas are already "full"."""
    return rev


def test_cmd_pre_deploy_never_runs_llm_lane_without_force_even_when_paths_are_llm_relevant():
    """2026-10-06: pre-deploy runs the offline lane ONLY by default -- a diff touching an
    LLM-relevant path (app/data.py) has no effect any more without PFLEGE_GATE_LLM=1."""
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid, label):
        seen["sha"] = sha
        seen["need_llm"] = need_llm
        seen["reason"] = llm_reason
        seen["label"] = label
        return 0

    rc = G.cmd_pre_deploy(
        Path("/unused"), "target1111111111111111111111111111111111", "deployed222222222222222222222",
        env={}, git_run=lambda args: ["app/data.py"], parent_pid=1, resolve=_same_rev, run_lanes=fake_run_lanes,
    )
    assert rc == 0
    assert seen["sha"] == "target1111111111111111111111111111111111"
    assert seen["need_llm"] is False
    assert "PFLEGE_GATE_LLM=1" in seen["reason"]
    assert seen["label"] == "pre-deploy"


def test_cmd_pre_deploy_never_runs_llm_lane_without_force_when_nothing_is_llm_relevant_either():
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid, label):
        seen["need_llm"] = need_llm
        seen["reason"] = llm_reason
        return 0

    rc = G.cmd_pre_deploy(
        Path("/unused"), "target1111111111111111111111111111111111", "deployed222222222222222222222",
        env={}, git_run=lambda args: ["app/wa/bridge.py"], parent_pid=1, resolve=_same_rev, run_lanes=fake_run_lanes,
    )
    assert rc == 0
    assert seen["need_llm"] is False
    assert "offline lane only by default" in seen["reason"]


def test_cmd_pre_deploy_pflege_gate_llm_1_forces_the_llm_lane():
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid, label):
        seen["need_llm"] = need_llm
        seen["reason"] = llm_reason
        return 0

    rc = G.cmd_pre_deploy(
        Path("/unused"), "target1111111111111111111111111111111111", "deployed222222222222222222222",
        env={"PFLEGE_GATE_LLM": "1"}, git_run=lambda args: ["app/wa/bridge.py"], parent_pid=1, resolve=_same_rev,
        run_lanes=fake_run_lanes,
    )
    assert rc == 0
    assert seen["need_llm"] is True
    assert "forced" in seen["reason"]


def test_cmd_pre_deploy_pflege_gate_llm_0_is_a_no_op_since_skip_is_already_the_default():
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid, label):
        seen["need_llm"] = need_llm
        return 0

    rc = G.cmd_pre_deploy(
        Path("/unused"), "target1111111111111111111111111111111111", "deployed222222222222222222222",
        env={"PFLEGE_GATE_LLM": "0"}, git_run=lambda args: ["app/data.py"], parent_pid=1, resolve=_same_rev,
        run_lanes=fake_run_lanes,
    )
    assert rc == 0
    assert seen["need_llm"] is False


def test_cmd_pre_deploy_defaults_deployed_to_the_main_checkouts_head_when_omitted():
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid, label):
        return 0

    def fake_get_main_checkout_head(worktree):
        seen["worktree"] = worktree
        return "mainheadsha11111111111111111111111111111"

    diffed = {}

    def fake_git_run(args):
        diffed["args"] = args
        return []

    rc = G.cmd_pre_deploy(
        Path("/some/worktree"), "target1111111111111111111111111111111111", None,
        env={}, git_run=fake_git_run, get_main_checkout_head=fake_get_main_checkout_head,
        parent_pid=1, resolve=_same_rev, run_lanes=fake_run_lanes,
    )
    assert rc == 0
    assert seen["worktree"] == Path("/some/worktree")
    assert "mainheadsha11111111111111111111111111111" in diffed["args"]


def test_cmd_pre_deploy_propagates_the_lane_runners_exit_code():
    rc = G.cmd_pre_deploy(
        Path("/unused"), "target1111111111111111111111111111111111", "deployed222222222222222222222",
        env={}, git_run=lambda args: [], parent_pid=1, resolve=_same_rev,
        run_lanes=lambda *a, **k: 1,
    )
    assert rc == 1


def test_resolve_commit_turns_head_a_branch_and_a_short_sha_into_the_full_sha(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha = _commit_file(repo, "a.txt", "one\n", "first")
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, check=True,
                            capture_output=True, text=True).stdout.strip()
    for rev in ("HEAD", branch, sha[:7], sha):
        assert G.resolve_commit(rev, repo) == sha


def test_resolve_commit_raises_on_a_rev_that_names_no_commit(tmp_path):
    repo = _make_throwaway_repo(tmp_path / "repo")
    _commit_file(repo, "a.txt", "one\n", "first")
    with pytest.raises(RuntimeError, match="does not name a commit"):
        G.resolve_commit("no-such-branch", repo)


def test_cmd_pre_deploy_keys_lanes_by_the_full_sha_when_given_symbolic_revs(tmp_path):
    """10-05 review MAJOR 1: `--target HEAD` used to reach the stamp/scratch checkout as the string
    "HEAD", so a stamp written for one commit vouched for any later HEAD."""
    repo = _make_throwaway_repo(tmp_path / "repo")
    sha1 = _commit_file(repo, "a.txt", "one\n", "first")
    sha2 = _commit_file(repo, "app/data.py", "x = 1\n", "second")
    seen = {}

    def fake_run_lanes(sha, need_llm, llm_reason, *, parent_pid, label):
        seen["sha"] = sha
        seen["need_llm"] = need_llm
        return 0

    rc = G.cmd_pre_deploy(repo, "HEAD", "HEAD~1", env={}, parent_pid=1, run_lanes=fake_run_lanes)
    assert rc == 0
    assert seen["sha"] == sha2
    # HEAD~1..HEAD touches app/data.py, but that no longer matters (2026-10-06): the sha-keying fix
    # under test is unrelated to the LLM-lane trigger, which is now PFLEGE_GATE_LLM-only.
    assert seen["need_llm"] is False
    assert sha1 != sha2


# --- _save_lane_log ----------------------------------------------------------------------------------

def test_save_lane_log_writes_stdout_and_returns_the_path(tmp_path):
    lane = G.LaneResult("offline", False, 1.2, 1, ["tests/x.py::t"], "some pytest output\n")
    path = G._save_lane_log("deadbeef", lane, log_dir=tmp_path / "logs")
    assert path == tmp_path / "logs" / "deadbeef-offline.log"
    assert path.read_text() == "some pytest output\n"


# --- _append_nightly_tsv -------------------------------------------------------------------------

def test_append_nightly_tsv_writes_one_tab_separated_row_with_no_header(tmp_path):
    path = tmp_path / "nightly.tsv"
    G._append_nightly_tsv(path, sha="deadbeef", result="passed", duration_s=12.3,
                           log_path="/x/deadbeef-llm.log", today=lambda: "2026-10-06")
    assert path.read_text() == "2026-10-06\tdeadbeef\tpassed\t12.3\t/x/deadbeef-llm.log\n"


def test_append_nightly_tsv_appends_rather_than_overwrites(tmp_path):
    path = tmp_path / "nightly.tsv"
    G._append_nightly_tsv(path, sha="sha1", result="passed", duration_s=1.0, log_path="a",
                           today=lambda: "2026-10-06")
    G._append_nightly_tsv(path, sha="sha2", result="failed", duration_s=2.0, log_path="b",
                           today=lambda: "2026-10-07")
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert lines[0] == "2026-10-06\tsha1\tpassed\t1.0\ta"
    assert lines[1] == "2026-10-07\tsha2\tfailed\t2.0\tb"


def test_append_nightly_tsv_creates_the_parent_directory(tmp_path):
    path = tmp_path / "nested" / "state" / "nightly.tsv"
    G._append_nightly_tsv(path, sha="s", result="passed", duration_s=0.0, log_path="",
                           today=lambda: "2026-10-06")
    assert path.exists()


# --- cmd_nightly, with fakes -- the LLM lane's new (and only) home (Ivan, 2026-10-06) ----------------

def test_cmd_nightly_skips_when_a_passed_llm_result_is_already_stamped(tmp_path, capsys):
    G.write_stamp("deadbeef", {"llm": {"passed": True, "duration_s": 5.0}}, stamp_dir=tmp_path)
    calls = []

    def llm_lane(scratch):
        calls.append("ran")
        return _fake_lane_result("llm", True)

    tsv = tmp_path / "nightly.tsv"
    rc = G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=tmp_path / "logs",
        nightly_tsv=tsv, resolve=_same_rev, scratch_factory=_FakeScratch, llm_lane=llm_lane,
        today=lambda: "2026-10-06",
    )
    assert rc == 0
    assert calls == []  # never re-run -- a stamped result, pass or fail, is never re-judged for free
    out = capsys.readouterr().out
    assert "deadbeef"[:12] in out and "passed" in out
    assert tsv.read_text() == "2026-10-06\tdeadbeef\tskipped\t0.0\t\n"


def test_cmd_nightly_skips_when_a_failed_llm_result_is_already_stamped(tmp_path, capsys):
    G.write_stamp("deadbeef", {"llm": {"passed": False, "duration_s": 5.0}}, stamp_dir=tmp_path)
    calls = []

    def llm_lane(scratch):
        calls.append("ran")
        return _fake_lane_result("llm", True)

    rc = G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=tmp_path / "logs",
        nightly_tsv=tmp_path / "nightly.tsv", resolve=_same_rev, scratch_factory=_FakeScratch,
        llm_lane=llm_lane, today=lambda: "2026-10-06",
    )
    assert rc == 0
    assert calls == []  # a FAILED result also counts as already judged -- nightly never re-runs it
    assert "failed" in capsys.readouterr().out


def test_cmd_nightly_runs_stamps_and_writes_the_tsv_line_when_no_result_yet(tmp_path):
    def llm_lane(scratch):
        return G.LaneResult("llm", True, 12.3, 0, [], "73 passed in 12.3s\n")

    tsv = tmp_path / "nightly.tsv"
    log_dir = tmp_path / "logs"
    rc = G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=log_dir, nightly_tsv=tsv,
        resolve=_same_rev, scratch_factory=_FakeScratch, llm_lane=llm_lane, today=lambda: "2026-10-06",
    )
    assert rc == 0
    stamp = G.read_stamp("deadbeef", stamp_dir=tmp_path)
    assert stamp["lanes"]["llm"] == {"passed": True, "duration_s": 12.3}
    assert tsv.read_text() == f"2026-10-06\tdeadbeef\tpassed\t12.3\t{log_dir / 'deadbeef-llm.log'}\n"


def test_cmd_nightly_saves_the_log_on_a_pass_too(tmp_path):
    """Ivan, 2026-10-06: nightly saves the lane log every time, pass or fail -- unlike
    _run_lanes_for_sha's own _save_lane_log calls, which only fire on a failure."""
    def llm_lane(scratch):
        return G.LaneResult("llm", True, 12.3, 0, [], "73 passed in 12.3s\n")

    log_dir = tmp_path / "logs"
    G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=log_dir,
        nightly_tsv=tmp_path / "nightly.tsv", resolve=_same_rev, scratch_factory=_FakeScratch,
        llm_lane=llm_lane, today=lambda: "2026-10-06",
    )
    log_path = log_dir / "deadbeef-llm.log"
    assert log_path.read_text() == "73 passed in 12.3s\n"


def test_cmd_nightly_saves_the_log_and_exits_nonzero_on_a_failure(tmp_path):
    def llm_lane(scratch):
        return G.LaneResult("llm", False, 3.0, 1, ["tests/x.py::t"], "FAILED tests/x.py::t\n")

    tsv = tmp_path / "nightly.tsv"
    log_dir = tmp_path / "logs"
    rc = G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=log_dir, nightly_tsv=tsv,
        resolve=_same_rev, scratch_factory=_FakeScratch, llm_lane=llm_lane, today=lambda: "2026-10-06",
    )
    assert rc == 1
    stamp = G.read_stamp("deadbeef", stamp_dir=tmp_path)
    assert stamp["lanes"]["llm"]["passed"] is False
    log_path = log_dir / "deadbeef-llm.log"
    assert log_path.read_text() == "FAILED tests/x.py::t\n"
    assert tsv.read_text() == f"2026-10-06\tdeadbeef\tfailed\t3.0\t{log_path}\n"


def test_cmd_nightly_preserves_an_existing_offline_lane_stamp_when_adding_llm(tmp_path):
    """The sha may already carry an offline-lane stamp from an earlier pre-push -- cmd_nightly adds
    its own "llm" key without clobbering it."""
    G.write_stamp("deadbeef", {"offline": {"passed": True, "duration_s": 60.0}}, stamp_dir=tmp_path)

    def llm_lane(scratch):
        return G.LaneResult("llm", True, 5.0, 0, [], "ok\n")

    rc = G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=tmp_path / "logs",
        nightly_tsv=tmp_path / "nightly.tsv", resolve=_same_rev, scratch_factory=_FakeScratch,
        llm_lane=llm_lane, today=lambda: "2026-10-06",
    )
    assert rc == 0
    stamp = G.read_stamp("deadbeef", stamp_dir=tmp_path)
    assert stamp["lanes"]["offline"]["passed"] is True
    assert stamp["lanes"]["llm"]["passed"] is True


def test_cmd_nightly_default_target_fetches_then_resolves_origin_main(tmp_path):
    seen = {}

    def fake_fetch():
        seen["fetched"] = True

    def fake_resolve(rev):
        seen["resolved_rev"] = rev
        return "mainsha1111111111111111111111111111111111"

    G.write_stamp("mainsha1111111111111111111111111111111111", {"llm": {"passed": True}},
                  stamp_dir=tmp_path)

    rc = G.cmd_nightly(
        Path("/unused"), None, stamp_dir=tmp_path, log_dir=tmp_path / "logs",
        nightly_tsv=tmp_path / "nightly.tsv", fetch=fake_fetch, resolve=fake_resolve,
        scratch_factory=_FakeScratch, llm_lane=lambda s: _fake_lane_result("llm", True),
        today=lambda: "2026-10-06",
    )
    assert rc == 0
    assert seen["fetched"] is True
    assert seen["resolved_rev"] == "origin/main"


def test_cmd_nightly_explicit_target_skips_the_fetch(tmp_path):
    seen = {"fetch_called": False}

    def fake_fetch():
        seen["fetch_called"] = True

    G.write_stamp("deadbeef", {"llm": {"passed": True}}, stamp_dir=tmp_path)

    rc = G.cmd_nightly(
        Path("/unused"), "deadbeef", stamp_dir=tmp_path, log_dir=tmp_path / "logs",
        nightly_tsv=tmp_path / "nightly.tsv", fetch=fake_fetch, resolve=_same_rev,
        scratch_factory=_FakeScratch, llm_lane=lambda s: _fake_lane_result("llm", True),
        today=lambda: "2026-10-06",
    )
    assert rc == 0
    assert seen["fetch_called"] is False
