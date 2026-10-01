"""pflege_jobs.cli: a manually-detached posting (clinic_match_rule='manual') must survive a live
inbox drain that re-observes and re-matches it. registry.py's own contract says manual overrides are
"untouched by re-runs", and cmd_link_clinics already filters its input rows on that field -- but the
live crawl's inbox processing (_process_rows -> cmd_inbox's clinic_links push) had no such check, so a
re-crawl of an already-corrected clinic silently overwrote the override with a fresh match
(2026-09-28, clinic 56404/56406, posting 7063 and 9 others). No network: get()/EdgeSink stubbed."""
import argparse

import pytest

from pflege_jobs import cli


class _Resp:
    def __init__(self, data): self._d = data
    def json(self): return self._d


# --- manual_posting_ids: the new live-DB check ---------------------------------------------------

def test_manual_posting_ids_returns_only_ids_flagged_manual():
    def fake_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/rest/v1/postings")
        assert params["clinic_match_rule"] == "eq.manual"
        assert params["posting_id"] == "in.(100,101,102)"
        return _Resp([{"posting_id": 100}])   # only 100 is manual, live

    assert cli.manual_posting_ids(fake_get, "https://db", {}, [100, 101, 102]) == {100}


def test_manual_posting_ids_chunks_large_id_lists():
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["posting_id"])
        return _Resp([])

    cli.manual_posting_ids(fake_get, "https://db", {}, [1, 2, 3], chunk=2)
    assert calls == ["in.(1,2)", "in.(3)"]


def test_manual_posting_ids_empty_input_makes_no_call():
    def fake_get(*a, **kw):
        raise AssertionError("should not be called")

    assert cli.manual_posting_ids(fake_get, "https://db", {}, []) == set()


# --- cmd_inbox: the push must drop links for postings the live DB marks manual -------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(cli, "_live_clinics", lambda url, H: [])     # an empty registry, no network


@pytest.fixture(autouse=True)
def _local_queue(tmp_path, monkeypatch):
    monkeypatch.setattr("pflege_jobs.inbox_db.PATH", str(tmp_path / "inbox.sqlite"))


class _FakeSink:
    posted = []
    def __init__(self, *a, **kw): pass
    def _post(self, body):
        _FakeSink.posted.append(body)
        return {}


def _args():
    return argparse.Namespace(no_ack=False, max_batches=10,
                               inbox_db=None, reprocess_run=None, reprocess_all=False)


def test_cmd_inbox_skips_the_link_push_for_a_posting_marked_manual_live(monkeypatch):
    obs_kept = {"source_id": 1, "source_ref": "https://a.de/1", "_kez": "56404", "_rule": "R6_ambiguous_sites:56404,56406"}
    obs_manual = {"source_id": 1, "source_ref": "https://a.de/2", "_kez": "56404", "_rule": "R6_ambiguous_sites:56404,56406"}

    def drain(a, url, H, m, towns, **kw):
        kw["stats"]["wrote"] = True
        kw["link_candidates"].extend([obs_kept, obs_manual])
        return 0

    _FakeSink.posted = []
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)
    monkeypatch.setattr(cli, "_drain_local_once", drain)
    monkeypatch.setattr(cli, "lookup_posting_ids",
                         lambda get, url, H, obs: {(1, "https://a.de/1"): 7063, (1, "https://a.de/2"): 7064})
    # 7064 is the one a human already detached (clinic_match_rule='manual' live) -- the re-match must not overwrite it
    monkeypatch.setattr(cli, "manual_posting_ids", lambda get, url, H, ids: {7064} & set(ids))
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)

    cli.cmd_inbox(_args())

    [links_body] = [b for b in _FakeSink.posted if "clinic_links" in b]
    posting_ids = {l["posting_id"] for l in links_body["clinic_links"]}
    assert posting_ids == {7063}   # 7064 (manual) excluded, 7063 still linked
