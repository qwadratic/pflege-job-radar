"""CI selection (tools/ci_scope.py) and the workflow it feeds (.github/workflows/tests.yml): two jobs, `offline` and `adapters`.

`offline` runs what a change touches without the mirror, no secrets, fork PRs included; `adapters` pulls the mirror and runs
`-m mirror`. Which of them runs is decided here, in Python, where it is tested, not in YAML conditionals.
"""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools import ci_scope as CS

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

SRC = {
    "test_geo.py": "",
    "test_web_leads.py": "",
    "test_ontology.py": 'ROOT / "docs" ... open("web/pro.html")',
    "test_adapter_completeness.py": "import pytest\n\npytestmark = pytest.mark.mirror\n",
    "test_completeness_dvinci.py": "import pytest\n\n# reads real boards\npytestmark = pytest.mark.mirror\n",
    "test_completeness_pi_asp.py": "import pytest\n",                       # fixtures only: not a mirror module
    "test_mirror_bunny.py": "# a mention: pytestmark = pytest.mark.mirror (not an assignment)\n",
    "test_list_form.py": "import pytest\n\npytestmark = [pytest.mark.completeness, pytest.mark.mirror]\n",
    "test_list_multiline.py": "import pytest\n\npytestmark = [\n    pytest.mark.slow,\n    pytest.mark.mirror,\n]\n",
    "test_list_other.py": "import pytest\n\npytestmark = [pytest.mark.completeness, pytest.mark.slow]\n",     # a list without the mirror mark
    "test_tuple_form.py": "import pytest\n\npytestmark = (pytest.mark.mirror, pytest.mark.slow)\n",
}


# --------------------------------------------------------------------------------------------- which tests `offline` runs
def test_mirror_modules_are_the_ones_that_assign_the_marker_at_module_level():
    assert CS.mirror_only(SRC) == {"test_adapter_completeness.py", "test_completeness_dvinci.py", "test_list_form.py", "test_list_multiline.py",
                                   "test_tuple_form.py"}


def test_the_list_form_of_the_marker_counts_and_a_list_without_it_does_not():
    assert {"test_list_form.py", "test_list_multiline.py", "test_tuple_form.py"} <= CS.mirror_only(SRC)
    assert "test_list_other.py" not in CS.mirror_only(SRC)
    assert CS.offline(["tests/test_list_form.py"], SRC) == set() and CS.offline(["tests/test_list_other.py"], SRC) == {"test_list_other.py"}


def test_offline_leaves_out_the_modules_that_need_the_mirror_so_pytest_never_ends_with_nothing_collected():
    """pytest -m "not mirror" over a file that is all mirror tests collects nothing and exits 5: the job would fail."""
    assert CS.offline(["tests/test_completeness_dvinci.py"], SRC) == set()
    assert CS.offline(["tests/test_completeness_dvinci.py", "tests/test_geo.py"], SRC) == {"test_geo.py"}
    assert CS.offline(["tests/test_completeness_pi_asp.py"], SRC) == {"test_completeness_pi_asp.py"}
    assert CS.offline(["docs/deploy.md"], SRC) == {"test_ontology.py"}
    assert CS.offline(["pflege_jobs/classify.py"], SRC) == "ALL"


# what the two invented mirror modules import; test_geo.py imports a tool and the harness, which no mirror module does
CLO = {"test_adapter_completeness.py": {"tests/test_adapter_completeness.py", "tests/adapter_contract.py", "tests/mirror.py", "app/data.py"},
       "test_completeness_dvinci.py": {"tests/test_completeness_dvinci.py", "tools/registry_build.py"},
       "test_geo.py": {"tests/test_geo.py", "tools/status_page.py", "app/wa/status_docs.py", "app/main.py", "app/wa_proxy.py"}}


# --------------------------------------------------------------------------------------------- when `adapters` runs
@pytest.mark.parametrize("changed", [
    ["crawlers/vendor_adapters.py"], ["crawlers/x/y.py"], ["tests/mirror.py"], ["tests/adapter_harness.py"], ["tests/adapter_contract.py"],
    ["tests/test_completeness_dvinci.py"], ["tests/test_completeness_pi_asp.py"], ["tests/test_adapter_completeness.py"],
    ["tests/test_verify_pi_loga_live.py"], ["tools/mirror.py"], ["pytest.ini"], ["requirements.txt"], [".github/workflows/tests.yml"],
    ["pflege_jobs/sources/bite.py"], ["tests/conftest.py"], ["conftest.py"],
    ["app/data.py"], ["tools/registry_build.py"],          # Python a mirror module imports, through any chain
    ["sql/015_source_supplement.sql"], ["edge/pflege-ingest/index.ts"], ["LICENSE"], ["data/new_thing.bin"],   # not Python, no leaf claims it
    ["data/registry/pi_seeds.json"], ["data/registry/taxonomy.json"], ["data/geo/ambiguous_stems.txt"], ["data/geo/kreise.geojson"],   # read by path at run time
    ["docs/deploy.md", "crawlers/a.py"], ["tools/status_page.py", "data/registry/taxonomy.json"], ["data/registry/reha_bavaria.csv", "data/registry/bite_seeds.json"],
])
def test_adapters_run_when_the_change_is_something_the_mirror_tests_can_see(changed):
    assert CS.adapters(changed, SRC, CLO) is True


@pytest.mark.parametrize("changed", [
    ["docs/deploy.md"], ["web/pro.template.html"], ["README.md"], ["tests/test_geo.py"], ["backlog/tasks/task-1 - x.md"], [],
    ["tools/status_page.py"], ["tools/wa_status_message.py"], ["app/wa_proxy.py"], ["app/wa/status_docs.py"], ["app/main.py"],   # Python no mirror module imports
    ["tools/status_page.py", "tests/test_status_page.py", "docs/auth.md"],
    ["data/registry/reha_bavaria.csv"], ["data/registry/plz_review.csv", "data/registry/krankenhausverzeichnis_24.xlsx"],   # sheets only offline tests read
])
def test_adapters_stay_out_of_a_change_that_cannot_reach_them(changed):
    assert CS.adapters(changed, SRC, CLO) is False


def test_in_this_repository_the_board_and_the_harness_do_not_start_adapters_and_the_crawler_core_does():
    """The real import closures, not the invented ones above: the day's own cases. On 2026-10-06 four pull requests that touched
    only a page generator, a message builder and a board route each waited 33 minutes for the adapters job."""
    src, clo = CS._sources(), CS.import_closures()
    assert len(CS.mirror_only(src)) >= 8
    for path in ("tools/status_page.py", "tools/wa_status_message.py", "app/wa_proxy.py", "app/wa/status_docs.py", "app/wa/luna_brain.py"):
        assert CS.adapters([path], src, clo) is False, path
    for path in ("crawlers/vendor_adapters.py", "pflege_jobs/geo.py", "tests/mirror.py", "tests/adapter_contract.py", "tools/mirror.py"):
        assert CS.adapters([path], src, clo) is True, path
    for path in sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "data" / "registry").glob("*.json")) + ["data/geo/ambiguous_stems.txt"]:
        assert CS.adapters([path], src, clo) is True, path                          # every seed file there is, by its real name
    for path in sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "data" / "registry").glob("*.csv")):
        assert CS.adapters([path], src, clo) is False and CS.offline([path], src, clo) == "ALL", path


# --------------------------------------------------------------------------------------------- the command line the workflow calls
def run_cli(*args, stdin=""):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "ci_scope.py"), *args], cwd=ROOT, input=stdin, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.parametrize("job,event,stdin,want", [
    ("offline", "push", "docs/deploy.md\n", "ALL"), ("offline", "push", "", "ALL"),       # a push to main: offline runs everything
    ("adapters", "push", "docs/deploy.md\n", "no"), ("adapters", "push", "backlog/tasks/task-1 - x.md\n", "no"), ("adapters", "push", "", "no"),
    ("adapters", "push", "crawlers/vendor_adapters.py\n", "yes"), ("adapters", "push", "backlog/tasks/task-1 - x.md\ntests/mirror.py\n", "yes"),
    ("adapters", "schedule", "docs/deploy.md\n", "yes"), ("offline", "schedule", "docs/deploy.md\n", "ALL"),   # the nightly run asks nobody
    ("adapters", "workflow_dispatch", "", "yes"), ("adapters", "full", "", "yes"),
    ("offline", "pull_request", "crawlers/vendor_adapters.py\n", "ALL"), ("adapters", "pull_request", "crawlers/vendor_adapters.py\n", "yes"),
    ("adapters", "pull_request", "tests/mirror.py\n", "yes"), ("adapters", "pull_request", "docs/deploy.md\n", "no"),
    ("offline", "pull_request", "tests/test_completeness_dvinci.py\n", "NONE"), ("adapters", "pull_request", "tests/test_completeness_dvinci.py\n", "yes"),
    ("offline", "pull_request", "tests/test_geo.py\n", "tests/test_geo.py"),
    ("offline", "pull_request", "", "NONE"), ("adapters", "pull_request", "", "no"),
    ("offline", "workflow_dispatch", "", "ALL"),                                           # anything that is not a pull request is a full run
])
def test_the_command_line_answers_for_each_job_and_event(job, event, stdin, want):
    assert run_cli("--job", job, "--event", event, stdin=stdin) == want


def test_the_command_line_keeps_its_old_form_for_the_offline_scope():
    assert run_cli(stdin="tests/test_geo.py\n") == "tests/test_geo.py"
    assert run_cli(stdin="pflege_jobs/classify.py\n") == "ALL"


def test_the_self_test_passes():
    assert run_cli("--self-test") == "ok"


# --------------------------------------------------------------------------------------------- the workflow
@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps_text(job):
    return "\n".join(str(s.get("run", "")) + " " + str(s.get("uses", "")) for s in job["steps"])


def test_a_pull_request_drops_its_superseded_runs_but_a_push_to_main_always_runs_to_the_end(wf):
    assert wf["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_the_workflow_has_two_jobs_on_push_to_main_and_every_pull_request(wf):
    assert set(wf["jobs"]) == {"offline", "adapters"}
    triggers = wf[True] if True in wf else wf["on"]            # PyYAML reads the key `on` as the boolean True
    assert triggers["push"]["branches"] == ["main"] and "pull_request" in triggers
    assert triggers["pull_request"] in (None, {}), "a pull_request filter (paths, branches) would skip the jobs silently"
    assert len(triggers["schedule"]) == 1 and "workflow_dispatch" in triggers      # the nightly full run, and the button


def test_a_push_hands_adapters_its_own_changes_and_becomes_a_full_run_when_they_cannot_be_read(wf):
    scope = wf["jobs"]["adapters"]["steps"][1]
    assert scope["env"] == {"EVENT": "${{ github.event_name }}", "BASE": "${{ github.base_ref }}",
                            "BEFORE": "${{ github.event.before }}", "AFTER": "${{ github.sha }}"}
    assert 'git diff --name-only "$BEFORE" "$AFTER" > "$RUNNER_TEMP/changed.txt" || EVENT=full' in scope["run"]
    assert scope["run"].index("|| EVENT=full") < scope["run"].index('--event "$EVENT"')


def test_offline_needs_no_secret_and_no_pull_and_leaves_out_mirror_network_and_llm_tests(wf):
    job = wf["jobs"]["offline"]
    text = steps_text(job)
    assert "secrets." not in yaml.dump(job) and "secrets." not in yaml.dump(wf.get("env", {}))     # nothing a fork PR would lack
    assert "mirror.py pull" not in text and "BUNNY" not in text
    assert '-m "not mirror and not network and not llm"' in text and "--timeout=300" in text
    assert "--job offline" in text and job["timeout-minutes"] == 60


def test_adapters_pull_the_mirror_with_the_read_only_key_then_run_the_mirror_tests(wf):
    job = wf["jobs"]["adapters"]
    assert job["timeout-minutes"] == 60
    runs = [str(s.get("run", "")) for s in job["steps"]]
    pull = next(i for i, r in enumerate(runs) if "tools/mirror.py pull" in r)
    pytest_step = next(i for i, r in enumerate(runs) if "pytest" in r)
    assert pull < pytest_step
    env = job["steps"][pull]["env"]
    assert env == {"BUNNY_MIRROR_ZONE": "${{ secrets.BUNNY_MIRROR_ZONE }}", "BUNNY_MIRROR_RO_KEY": "${{ secrets.BUNNY_MIRROR_RO_KEY }}"}
    assert "RW_KEY" not in WORKFLOW.read_text(encoding="utf-8")                 # the write key is never in CI
    assert "-m mirror" in runs[pytest_step] and "--timeout=900" in runs[pytest_step]
    assert "continue-on-error" not in WORKFLOW.read_text(encoding="utf-8")        # a failed pull must fail the job


def test_adapters_job_is_decided_by_ci_scope_not_by_a_yaml_condition_on_paths(wf):
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--job adapters" in text
    assert "paths:" not in text and "github.event.pull_request.head.repo" not in text   # no fork special case: it fails loudly at the pull
    for s in wf["jobs"]["adapters"]["steps"]:
        if "if" in s:
            assert "steps.scope.outputs.adapters == 'yes'" in s["if"], s


def test_a_pull_without_the_secrets_fails_loudly_naming_them():
    """A fork PR gets empty secrets: the pull step must fail by itself, before any request."""
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("BUNNY_MIRROR")}
    env["BUNNY_MIRROR_ZONE"] = ""                      # what an unset secret expands to in an `env:` block
    env["BUNNY_MIRROR_RO_KEY"] = ""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "mirror.py"), "pull"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "BUNNY_MIRROR_ZONE" in r.stderr and "BUNNY_MIRROR_RO_KEY" in r.stderr
