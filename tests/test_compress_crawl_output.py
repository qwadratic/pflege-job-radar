"""tools/compress_crawl_output.py: finished runs are gzipped without losing a line, busy runs and named ids stay plain, odd cases are loud (Ivan 2026-10-08)."""
import gzip
import sqlite3

import pytest

from tools import compress_crawl_output as C

LINES = [b'{"kind":"jobposting","payload":{"title":"Pflegefachkraft"}}\n', b'{"kind":"observation"}\n']


@pytest.fixture
def env(tmp_path):
    out = tmp_path / "crawl_output"
    out.mkdir()
    db = tmp_path / "app.sqlite"
    c = sqlite3.connect(db)
    c.execute("create table crawl_runs (run_id integer primary key, status text)")
    for rid, st in ((1, "done"), (2, "running"), (3, "queued"), (4, "failed"), (5, "done"), (7, "done")):
        c.execute("insert into crawl_runs values (?, ?)", (rid, st))
    c.commit()
    c.close()
    for rid in (1, 2, 3, 4, 5, 6):
        (out / f"run_{rid}.jsonl").write_bytes(b"".join(LINES))
    return out, db


def args(env, *extra):
    out, db = env
    return ["--dir", str(out), "--db", str(db), *extra]


def test_dry_run_changes_nothing(env, capsys):
    assert C.main(args(env)) == 1  # run 6 is not in crawl_runs: a problem, still reported
    assert sorted(p.name for p in env[0].iterdir()) == [f"run_{i}.jsonl" for i in range(1, 7)]
    assert "would gzip run_1.jsonl" in capsys.readouterr().out


def test_finished_runs_are_gzipped_with_every_line_busy_ones_stay_plain(env):
    C.main(args(env, "--apply", "--skip", "5"))
    names = sorted(p.name for p in env[0].iterdir())
    assert names == ["run_1.jsonl.gz", "run_2.jsonl", "run_3.jsonl", "run_4.jsonl.gz", "run_5.jsonl", "run_6.jsonl"]
    assert gzip.open(env[0] / "run_1.jsonl.gz", "rb").read() == b"".join(LINES)


def test_unknown_run_and_existing_gz_are_problems_nothing_is_overwritten(env, capsys):
    (env[0] / "run_7.jsonl").write_bytes(b"".join(LINES))
    (env[0] / "run_7.jsonl.gz").write_bytes(b"older gz, not ours to replace")
    assert C.main(args(env, "--apply")) == 1
    err = capsys.readouterr().err
    assert "run_6.jsonl: run 6 is not in crawl_runs" in err and "run_7.jsonl.gz exists beside it" in err
    assert (env[0] / "run_7.jsonl.gz").read_bytes() == b"older gz, not ours to replace"
    assert (env[0] / "run_6.jsonl").exists() and (env[0] / "run_7.jsonl").exists()


def test_a_second_run_is_idempotent(env):
    (env[0] / "run_6.jsonl").unlink()
    assert C.main(args(env, "--apply")) == 0
    assert C.main(args(env, "--apply")) == 0


def test_a_read_back_that_differs_keeps_the_plain_dump(env, monkeypatch):
    (env[0] / "run_6.jsonl").unlink()
    monkeypatch.setattr(C, "same_lines", lambda plain, gz: False)
    assert C.main(args(env, "--apply")) == 1
    assert (env[0] / "run_1.jsonl").exists() and not (env[0] / "run_1.jsonl.gz").exists() and not (env[0] / "run_1.jsonl.gz.tmp").exists()


def test_same_lines_sees_a_missing_last_line(tmp_path):
    plain, gz = tmp_path / "a.jsonl", tmp_path / "a.jsonl.gz"
    plain.write_bytes(b"".join(LINES))
    with gzip.open(gz, "wb") as g:
        g.write(LINES[0])
    assert C.same_lines(str(plain), str(gz)) is False
