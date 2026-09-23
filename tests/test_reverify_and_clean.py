"""tools/reverify_and_clean.py regressions (TASK-74): crawler-review 2026-09-18 found the placeability
check disabled (AC1), cmd_relink destroying correct clinic links instead of routing through the
Matcher ladder (AC2), --write-city being undone by the next crawl (AC3), --delete-non-bavaria's
pre-delete backup missing most of a row and all of posting_observations (AC4), and cmd_firecrawl
raising KeyError on a stale id (AC5). No network, no real Supabase: every requests.get/patch/delete
and EdgeSink call is faked; STATE/BACKUPS point at tmp_path for the run.
"""
import argparse
import json

import requests

import tools.reverify_and_clean as rc


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


class _Ok:
    status_code = 200
    text = ""


class _FakeSink:
    """Captures every EdgeSink._post body instead of hitting the network."""
    posted = []

    def __init__(self, *a, **kw):
        pass

    def _post(self, body):
        _FakeSink.posted.append(body)
        return {k: len(v) for k, v in body.items()}


def _seed(tmp_path, **files):
    for name, obj in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False))


# --- AC1: towns=towns() must reach verify_all/verify_one, or _placeable() never rejects page furniture
def test_cmd_verify_passes_towns_so_placeability_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "STATE", tmp_path)
    _seed(tmp_path, postings=[{"posting_id": 1, "external_url": "https://x.de/job/1", "title": "Pflegefachkraft"}])
    captured = {}

    def fake_verify_all(rows, **kw):
        captured.update(kw)
        return [{"posting_id": 1, "verify_status": "live", "verify_http": 200, "verified_at": "t",
                 "verify_note": "n", "method": "http", "city": "München", "plz": None, "loc_source": "jsonld", "final_url": "u"}]
    monkeypatch.setattr(rc, "verify_all", fake_verify_all)

    rc.cmd_verify(argparse.Namespace(limit=0, workers=3, no_render=True, restart=True))

    assert captured.get("towns") == rc.towns()
    assert "münchen" in captured["towns"]   # towns() actually loaded the real registry, not an empty/None placeholder


# --- AC1 + AC5: cmd_firecrawl must pass towns= AND must not KeyError on an id verified.json has that
# postings.json (fetched in an earlier/later generation) no longer carries.
def test_cmd_firecrawl_drops_stale_ids_and_passes_towns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rc, "STATE", tmp_path)
    _seed(tmp_path,
          postings=[{"posting_id": 1, "external_url": "https://x.de/job/1", "title": "Pflegefachkraft"}],
          verified=[{"posting_id": 1, "verify_status": "blocked", "verify_http": 403, "verify_note": "wall",
                     "method": "http", "city": None, "plz": None, "loc_source": None},
                    {"posting_id": 999, "verify_status": "error", "verify_http": None, "verify_note": "stale",
                     "method": "http", "city": None, "plz": None, "loc_source": None}])
    captured_towns = []

    def fake_verify_one(session, url, title, rungs=(), towns=None):
        captured_towns.append(towns)
        return {"verify_status": "live", "verify_http": 200, "verify_note": "ok [firecrawl]",
                "method": "firecrawl", "city": "Nürnberg", "plz": None, "loc_source": "jsonld", "final_url": url}
    monkeypatch.setattr(rc, "verify_one", fake_verify_one)

    rc.cmd_firecrawl(argparse.Namespace(max=10, host="", keep_only=False))   # pre-fix: KeyError on posting_id 999

    out = capsys.readouterr().out
    assert "dropping 1 stale id" in out
    assert captured_towns == [rc.towns()]                # verify_one called exactly once, for the real id, with towns=
    saved = {r["posting_id"]: r for r in json.loads((tmp_path / "verified.json").read_text())}
    assert saved.keys() == {1, 999}                      # stale row survives untouched, it is just not re-verified
    assert saved[1]["verify_status"] == "live"


# --- AC2: cmd_relink must decide through Matcher.match(board=[old], employer_inherited=...), not a
# bespoke city-string compare, and must only clear a link when NO clinic exists in the page's town.
def test_cmd_relink_routes_through_matcher_ladder(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "STATE", tmp_path)
    _seed(tmp_path, postings=[], verified=[
        {"posting_id": 101, "loc_source": "einsatzort", "city": "Garmisch-Partenkirchen", "plz": None},
        {"posting_id": 102, "loc_source": "jsonld", "city": "Wolfratshausen", "plz": None},
        {"posting_id": 103, "loc_source": "einsatzort", "city": "Bad Aibling", "plz": None},
        {"posting_id": 104, "loc_source": "einsatzort", "city": "Karte", "plz": None},          # unplaceable
        {"posting_id": 105, "loc_source": "jsonld", "city": "Weiden", "plz": None},
    ])
    _seed(tmp_path, plan={"keep": [{"posting_id": pid} for pid in (101, 102, 103, 104, 105)],
                           "delete": [], "city_fix": [], "unknown_bavaria": [], "unverifiable": []})

    # Every posting's stored employer is "Generic Gruppe" -- OLD1's own operator name, copied onto
    # every posting of its shared board, the exact kbo.de shape this fix targets -- except 105, whose
    # blank employer isolates the board rung's own town-canon check (registry.Matcher's own
    # 'Weiden i.d. Oberpfalz' vs 'Weiden' example) from any employer-text effect.
    EMP = {
        101: {"posting_id": 101, "employer": "Generic Gruppe", "clinic_id": "OLD1", "city": "München"},
        102: {"posting_id": 102, "employer": "Generic Gruppe", "clinic_id": "OLD1", "city": "München"},
        103: {"posting_id": 103, "employer": "Generic Gruppe", "clinic_id": "OLD1", "city": "München"},
        104: {"posting_id": 104, "employer": "Generic Gruppe", "clinic_id": "OLD1", "city": "München"},
        105: {"posting_id": 105, "employer": "", "clinic_id": "WEID1", "city": "Weiden"},
    }
    CLINICS = [
        {"clinic_id": "OLD1", "name": "Generic Betriebs GmbH", "town": "München", "operator": "Generic Gruppe", "beds": 100},
        {"clinic_id": "TARGET1", "name": "Kreisklinik Garmisch GmbH", "town": "Garmisch-Partenkirchen", "operator": "Generic Gruppe", "beds": 200},
        {"clinic_id": "AMB1", "name": "Klinik Bad Aibling Nord", "town": "Bad Aibling", "operator": "Kliniken Sued Ost", "beds": 50},
        {"clinic_id": "AMB2", "name": "Klinik Bad Aibling Sued", "town": "Bad Aibling", "operator": "Kliniken Sued Ost", "beds": 60},
        {"clinic_id": "WEID1", "name": "Klinikum Weiden i.d. Oberpfalz", "town": "Weiden i.d. Oberpfalz", "operator": "Kliniken Nordoberpfalz AG", "beds": 300},
    ]

    def fake_get(u, params=None, headers=None, timeout=None):
        if u.endswith("/rest/v1/v_postings"):
            return _Resp(list(EMP.values()))
        if u.endswith("/rest/v1/clinics"):
            return _Resp(CLINICS)
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)
    _FakeSink.posted = []
    monkeypatch.setattr("pflege_jobs.sinks.EdgeSink", _FakeSink)

    rc.cmd_relink(argparse.Namespace(write=True))

    relink = json.loads((tmp_path / "relink.json").read_text())
    changes = {c["posting_id"]: c for c in relink["changes"]}
    cleared = {c["posting_id"] for c in relink["cleared"]}
    disagreements = {c["posting_id"] for c in relink["disagreements"]}

    # 101: employer text is old's own operator (circular) -- with employer_inherited routing past
    # R1/R2, R4_tokens_op finds the real site (TARGET1) the page's own city names.
    assert changes[101]["old"] == "OLD1" and changes[101]["new"] == "TARGET1"
    # 102: no clinic anywhere in Wolfratshausen -- safe to clear.
    assert cleared == {102}
    # 103: Bad Aibling has two candidates the ladder cannot disambiguate -- left linked, reported.
    assert disagreements == {103}
    # 104: "Karte" is not a placeable location -- no evidence, no action at all.
    assert 104 not in changes and 104 not in cleared and 104 not in disagreements
    # 105: 'Weiden' vs registry town 'Weiden i.d. Oberpfalz' -- R0_board's own canonical town match
    # (not a plain string compare) confirms the EXISTING link; also no action.
    assert 105 not in changes and 105 not in cleared and 105 not in disagreements

    pushed = [link for body in _FakeSink.posted for link in body.get("clinic_links", [])]
    pushed_by_id = {l["posting_id"]: l for l in pushed}
    assert set(pushed_by_id) == {101, 102}         # only changes+cleared reach the DB -- 103/104/105 never do
    assert pushed_by_id[101]["clinic_id"] == "TARGET1"
    assert pushed_by_id[102]["clinic_id"] is None


# --- AC3: apply --write-city must write somewhere resolve_postings() does not overwrite.
def test_write_city_sets_override_columns_resolve_postings_prefers(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "STATE", tmp_path)
    _seed(tmp_path, verified=[],
          plan={"city_fix": [{"posting_id": 55, "city_page": "Bamberg", "plz_page": "96047", "city_stored": "Forchheim"}]})
    patched = {}

    def fake_patch(url, params=None, headers=None, json=None, timeout=None):
        patched["body"] = json
        return _Ok()
    monkeypatch.setattr(requests, "patch", fake_patch)

    rc.cmd_apply(argparse.Namespace(write_verify=False, write_city=True, delete_non_bavaria=False))

    assert patched["body"] == {"city": "Bamberg", "city_override": "Bamberg", "plz": "96047", "plz_override": "96047"}

    # Mirror sql/001_schema.sql's resolve_postings(): city/plz = coalesce(*_override, crawl-derived).
    # A stale crawl re-asserting the OLD wrong city must not undo the correction on the next ingest.
    def simulated_resolve_postings(row, crawl_derived_city):
        return row.get("city_override") or crawl_derived_city
    assert simulated_resolve_postings({"city_override": patched["body"]["city_override"]}, "Forchheim") == "Bamberg"

    # Pin the SQL side too: the column exists and resolve_postings() actually coalesces through it --
    # a passing tool-level test alone would not catch the schema half silently regressing.
    sql = (rc.Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql").read_text(encoding="utf-8")
    assert "city_override text" in sql and "plz_override text" in sql
    assert "coalesce(p.city_override, g->>'city')" in sql
    assert "coalesce(p.plz_override, g->>'plz')" in sql


# --- AC4: apply --delete-non-bavaria must back up the FULL row of both tables, outside /tmp, before deleting.
def test_delete_non_bavaria_backs_up_full_rows_before_deleting(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "STATE", tmp_path)
    monkeypatch.setattr(rc, "BACKUPS", tmp_path / "backups")
    _seed(tmp_path, verified=[], plan={"delete": [{"posting_id": 7}, {"posting_id": 8}]})

    full_posts = [{"posting_id": 7, "title": "T7", "status": "open", "clinic_id": "X", "description": "long text 7"},
                  {"posting_id": 8, "title": "T8", "status": "open", "clinic_id": "Y", "description": "long text 8"}]
    full_obs = [{"observation_id": 900, "posting_id": 7, "source_id": 20, "source_ref": "r7"},
                {"observation_id": 901, "posting_id": 8, "source_id": 20, "source_ref": "r8"}]
    gets = []

    def fake_get(u, params=None, headers=None, timeout=None):
        gets.append((u, params))
        if u.endswith("/rest/v1/postings"):
            assert params["select"] == "*"               # AC4: full row, not the old 15-column COLS slice
            return _Resp(full_posts)
        if u.endswith("/rest/v1/posting_observations"):
            assert params["select"] == "*"                # AC4: posting_observations was never dumped at all before
            return _Resp(full_obs)
        raise AssertionError(f"unexpected GET {u}")
    monkeypatch.setattr(requests, "get", fake_get)
    deletes = []
    monkeypatch.setattr(requests, "delete", lambda u, params=None, headers=None, timeout=None: (deletes.append(u), _Ok())[1])

    rc.cmd_apply(argparse.Namespace(write_verify=False, write_city=False, delete_non_bavaria=True))

    assert len(gets) == 2 and len(deletes) == 2   # backup fetched (both tables) strictly before the 2 deletes ran
    dumps = sorted((tmp_path / "backups").glob("reverify_delete_*.jsonl"))
    assert len(dumps) == 2
    posts_dump = next(p for p in dumps if "postings" in p.name)
    obs_dump = next(p for p in dumps if "observations" in p.name)
    dumped_posts = [json.loads(l) for l in posts_dump.read_text().splitlines()]
    dumped_obs = [json.loads(l) for l in obs_dump.read_text().splitlines()]
    assert {r["posting_id"] for r in dumped_posts} == {7, 8}
    assert all(r.get("description") for r in dumped_posts)         # full row -- the old 15-column COLS never carried this
    assert {r["observation_id"] for r in dumped_obs} == {900, 901}  # posting_observations backed up at all


def test_backups_dir_is_durable_not_the_volatile_tmp_state_default():
    assert "tmp" not in rc.BACKUPS.parts
    assert rc.BACKUPS.name == "backups"
