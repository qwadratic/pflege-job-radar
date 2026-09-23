"""tools/task95_replay.py's run_id_for: the run_id used for IB.enqueue/IB.reset must come from the
jsonl actually passed, not be hardcoded (2026-09-21 review). Harmless against the /tmp scratch db the
CLI defaults to, but with --db pointing at the real data/inbox.sqlite a wrong run_id mislabels real
rows on enqueue and, worse, IB.reset(run_id=<wrong>) unmarks a real run's rows that have nothing to
do with this replay."""
import pytest

from tools.task95_replay import run_id_for


def test_run_id_is_parsed_from_the_jsonl_filename():
    assert run_id_for("crawl_output/run_108.jsonl") == 108
    assert run_id_for("crawl_output/run_96.jsonl") == 96


def test_explicit_run_id_wins_over_the_filename():
    assert run_id_for("crawl_output/run_108.jsonl", explicit=42) == 42


def test_an_unparseable_filename_with_no_explicit_run_id_fails_loudly_instead_of_defaulting():
    """No invented fallback (e.g. silently reusing 108) -- a filename that doesn't carry a run_id
    must refuse to guess."""
    with pytest.raises(SystemExit):
        run_id_for("/tmp/not_a_run_file.jsonl")
