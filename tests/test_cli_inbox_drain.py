"""pflege_jobs.cli.cmd_inbox: max_batches is a loop-safety ceiling now, not a queue-size cap that
silently drops the tail of a real drain (TASK-72 AC#2). No network: os.environ + _drain_once stubbed."""
import argparse
import os

import pytest

from pflege_jobs import cli


@pytest.fixture()
def clinics_csv(tmp_path):
    p = tmp_path / "clinics.csv"
    p.write_text("clinic_id,name,town,operator,landkreis,regierungsbezirk,status,versorgungsstufe,traegerart,"
                 "beds,day_places,fachrichtungen,parse_quality,source,website,careers_url,ats_type\n", encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")


def _args(clinics_csv, max_batches, no_ack=False):
    return argparse.Namespace(clinics=clinics_csv, no_ack=no_ack, max_batches=max_batches)


def test_max_batches_reached_prints_truncated_to_stderr(clinics_csv, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns: 1000)   # never signals "queue empty"
    cli.cmd_inbox(_args(clinics_csv, max_batches=3))
    out = capsys.readouterr()
    assert "TRUNCATED: max_batches=3" in out.err
    assert "inbox drained 3000 rows" in out.out


def test_natural_end_of_queue_prints_no_truncation_warning(clinics_csv, monkeypatch, capsys):
    calls = {"n": 0}

    def drain(a, url, H, m, towns):
        calls["n"] += 1
        return 1000 if calls["n"] < 2 else 400   # queue drains on the 2nd batch

    monkeypatch.setattr(cli, "_drain_once", drain)
    cli.cmd_inbox(_args(clinics_csv, max_batches=100_000))
    out = capsys.readouterr()
    assert "TRUNCATED" not in out.err
    assert "inbox drained 1400 rows" in out.out
