import json, os, tempfile

import pytest
import requests

from pflege_jobs.sources.inbox import jobposting_to_obs
from pflege_jobs.sinks import SqlSink, CsvSink, employers_from

# One inbox row as every crawler / the Firecrawl agent emits it (kind=jobposting).
ROW = {"inbox_id": 1, "kind": "jobposting", "source_host": "karriere.klinikum-nuernberg.de", "collector": "vendor-adapters-test",
       "source_url": "https://karriere.klinikum-nuernberg.de/job/1?x'y",
       "payload": {"title": "Pflegefachkraft (m/w/d) OP", "org": "Klinikum Nürnberg", "employmentType": "FULL_TIME",
                   "datePosted": "2026-08-31", "url": "https://karriere.klinikum-nuernberg.de/job/1?x'y",
                   "loc": [{"city": "Nürnberg", "plz": "90419", "region": "Bayern"}],
                   "description": "Wir suchen Sie. Personalwohnung vorhanden. Vergütung nach TVöD-K. Kontakt: pd@klinik.de"}}
TOWNS = {"nürnberg"}


def obs(**over):
    return {**jobposting_to_obs({**ROW, "payload": {**ROW["payload"], **over.pop("payload", {})}}, TOWNS), **over}


def test_to_observation():
    o = obs()
    assert o["source_id"] == 20 and o["employer_class"] == "clinic" and o["role_class"] == "pflegefachkraft" and o["department_hint"] == "OP"
    assert o["in_bavaria"] and o["plz"] == "90419" and o["employment_types"] == ["vollzeit"] and o["first_published"] == "2026-08-31"
    assert o["enr_housing"] is True and o["enr_tariff"] == "TVöD" and o["enr_contact_emails"] == ["pd@klinik.de"]
    assert json.loads(o["payload"])["inbox"]["collector"] == "vendor-adapters-test"


def test_firecrawl_collector_maps_to_source_25():
    assert jobposting_to_obs({**ROW, "collector": "firecrawl-agent"}, TOWNS)["source_id"] == 25
    assert jobposting_to_obs({**ROW, "collector": None}, TOWNS)["source_id"] == 20


def test_section_labels_confirm_nursing_and_recover_a_gate_only_failure():
    # Real smartrecruiters survey example: "Dauernachtwache (m/w/d)" tagged dept "Pflegedienst" --
    # today (no section_labels key) it fails classify_role's pflege_gate outright; once
    # crawlers/vendor_adapters.py's row() payload carries the job's own department label, this same
    # inbox row must classify as nursing instead of being silently dropped.
    o = obs(payload={"title": "Dauernachtwache (m/w/d)", "section_labels": ["Pflegedienst"]})
    assert o["role_class"] == "pflegefachkraft"
    assert o["role_rule"] == "pflegefachkraft:dauernachtwache"


def test_no_section_labels_falls_back_to_todays_gate_unchanged():
    o = obs(payload={"title": "Dauernachtwache (m/w/d)", "section_labels": []})
    assert o["role_class"] == "nicht_pflege" and o["role_rule"] == "no_pflege_token"


def test_sql_sink_escapes_quotes():
    o = obs()
    d = tempfile.mkdtemp()
    r = SqlSink(d, batch=1).write([o])
    sql = open(os.path.join(d, "020_observations_0000.sql"), encoding="utf-8").read()
    assert "x''y" in sql and "$j$" in sql and "on conflict (source_id, source_ref)" in sql
    assert r["employers"] == 1


def test_csv_sink():
    o = obs(); d = tempfile.mkdtemp()
    assert CsvSink(d).write([o])["observations"] == 1
    assert employers_from([o])[0]["name_norm"] == "klinikum nürnberg"


def test_sinks_drop_nicht_pflege():
    from pflege_jobs.sinks import only_pflege
    rows=[{"role_class":"pflegefachkraft"},{"role_class":"nicht_pflege"}]
    assert len(only_pflege(rows))==1 and len(only_pflege(rows, keep_non_pflege=True))==2


def test_sinks_drop_trainees_interns_and_assistants():
    """Certified nursing staff only: Ausbildung/Azubi, Praktikum/Werkstudent/FSJ and Pflegehelfer/Assistenz never reach the DB."""
    from pflege_jobs.sinks import only_pflege
    rows = [{"role_class": rc} for rc in
            ("pflegefachkraft", "fachpflege", "pflegehelfer", "praxisanleitung", "leitung",
             "apn_experte", "hebamme", "ota_ata", "sonstige_pflege",
             "ausbildung", "werkstudent_praktikum", "nicht_pflege")]
    kept = {r["role_class"] for r in only_pflege(rows)}
    assert kept == {"pflegefachkraft", "fachpflege", "praxisanleitung", "leitung",
                    "apn_experte", "hebamme", "ota_ata", "sonstige_pflege"}
    assert not kept & {"ausbildung", "werkstudent_praktikum", "nicht_pflege", "pflegehelfer"}
    assert len(only_pflege(rows, keep_non_pflege=True)) == len(rows)   # inspection path is unfiltered


def test_edge_sink_write_drops_trainees(monkeypatch):
    """The gate is in EdgeSink.write itself, so every adapter inherits it."""
    from pflege_jobs.sinks import EdgeSink
    monkeypatch.setenv("PFLEGE_INGEST_URL", "http://x"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k"); monkeypatch.setenv("PFLEGE_INGEST_SECRET", "s")
    sink = EdgeSink(); posted = []
    sink._post = lambda body: (posted.append(body), {"observations": len(body.get("observations", [])), "employers": 0, "resolve": {}})[1]
    o = obs()
    stats = sink.write([o, {**o, "source_ref": "azubi", "role_class": "ausbildung"},
                        {**o, "source_ref": "fsj", "role_class": "werkstudent_praktikum"}], resolve=False)
    assert stats["dropped_non_pflege"] == 2
    sent = [r for b in posted for r in b.get("observations", [])]
    assert [r["role_class"] for r in sent] == ["pflegefachkraft"]


class _Resp:
    def __init__(self, status, text="err"):
        self.status_code, self.text = status, text

    def json(self):
        return {"ok": True}


def test_edge_sink_post_breaks_immediately_on_4xx(monkeypatch):
    """A permanent client error (403/413/...) must not be retried three times with sleeps in between --
    only 5xx and transport exceptions get retried."""
    from pflege_jobs.sinks import EdgeSink
    monkeypatch.setenv("PFLEGE_INGEST_URL", "http://x"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k"); monkeypatch.setenv("PFLEGE_INGEST_SECRET", "s")
    sink = EdgeSink()
    calls = []
    monkeypatch.setattr("pflege_jobs.sinks.requests.post", lambda *a, **k: (calls.append(1), _Resp(403))[1])
    sleeps = []
    monkeypatch.setattr("pflege_jobs.sinks.time.sleep", lambda s: sleeps.append(s))
    with pytest.raises(RuntimeError, match="403"):
        sink._post({"x": 1})
    assert len(calls) == 1 and sleeps == []


def test_edge_sink_post_retries_5xx_then_succeeds(monkeypatch):
    from pflege_jobs.sinks import EdgeSink
    monkeypatch.setenv("PFLEGE_INGEST_URL", "http://x"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k"); monkeypatch.setenv("PFLEGE_INGEST_SECRET", "s")
    sink = EdgeSink()
    responses = [_Resp(500), _Resp(200)]
    calls = []
    def fake_post(*a, **k):
        calls.append(1)
        return responses.pop(0)
    monkeypatch.setattr("pflege_jobs.sinks.requests.post", fake_post)
    monkeypatch.setattr("pflege_jobs.sinks.time.sleep", lambda s: None)
    assert sink._post({"x": 1}) == {"ok": True}
    assert len(calls) == 2


def test_edge_sink_post_retries_transport_exception(monkeypatch):
    from pflege_jobs.sinks import EdgeSink
    monkeypatch.setenv("PFLEGE_INGEST_URL", "http://x"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k"); monkeypatch.setenv("PFLEGE_INGEST_SECRET", "s")
    sink = EdgeSink()
    calls = []
    def fake_post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("boom")
        return _Resp(200)
    monkeypatch.setattr("pflege_jobs.sinks.requests.post", fake_post)
    monkeypatch.setattr("pflege_jobs.sinks.time.sleep", lambda s: None)
    assert sink._post({"x": 1}) == {"ok": True}
    assert len(calls) == 2
