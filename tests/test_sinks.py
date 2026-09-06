import json, os, tempfile
from pflege_jobs.sources.arbeitsagentur import to_observation
from pflege_jobs.sinks import SqlSink, CsvSink, employers_from

RAW = {"stellenangebotsart":"ARBEIT","stellenangebotsTitel":"Pflegefachkraft (m/w/d) OP","arbeitszeitVollzeit":True,
 "eintrittszeitraum":{"von":"2026-08-31"},"verguetungsangabe":"KEINE_ANGABEN","vertragsdauer":"UNBEFRISTET",
 "stellenlokationen":[{"adresse":{"plz":"90419","ort":"Nürnberg","region":"BAYERN","land":"DEUTSCHLAND"},"breite":49.4,"laenge":11.0}],
 "veroeffentlichungszeitraum":{"von":"2026-08-31"},"datumErsteVeroeffentlichung":"2026-08-31","aenderungsdatum":"2026-08-31T10:55:55.684",
 "hauptberuf":"Gesundheits- und Krankenpfleger/in","firma":"Klinikum Nürnberg","arbeitgeberKundennummerHash":"abc=","referenznummer":"10000-1-S",
 "alleBerufe":["Pflegefachkraft"],"_slices":["kp_arbeit"], "externeURL":"https://karriere.klinikum-nuernberg.de/x'y"}

def test_to_observation():
    o = to_observation(RAW)
    assert o["employer_class"] == "clinic" and o["role_class"] == "pflegefachkraft" and o["department_hint"] == "OP"
    assert o["in_bavaria"] and o["plz"] == "90419" and o["employment_types"] == ["vollzeit"]
    assert json.loads(o["payload"])["referenznummer"] == "10000-1-S"

def test_sql_sink_escapes_quotes():
    o = to_observation(RAW)
    d = tempfile.mkdtemp()
    r = SqlSink(d, batch=1).write([o])
    sql = open(os.path.join(d, "020_observations_0000.sql"), encoding="utf-8").read()
    assert "x''y" in sql and "$j$" in sql and "on conflict (source_id, source_ref)" in sql
    assert r["employers"] == 1

def test_csv_sink():
    o = to_observation(RAW); d = tempfile.mkdtemp()
    assert CsvSink(d).write([o])["observations"] == 1
    assert employers_from([o])[0]["aa_kundennummer_hashes"] == ["abc="]

def test_sinks_drop_nicht_pflege():
    from pflege_jobs.sinks import only_pflege
    rows=[{"role_class":"pflegefachkraft"},{"role_class":"nicht_pflege"}]
    assert len(only_pflege(rows))==1 and len(only_pflege(rows, keep_non_pflege=True))==2


def test_sinks_drop_trainees_and_interns():
    """Experienced staff only: Ausbildung/Azubi and Praktikum/Werkstudent/FSJ never reach the DB."""
    from pflege_jobs.sinks import only_pflege
    rows = [{"role_class": rc} for rc in
            ("pflegefachkraft", "fachpflege", "pflegehelfer", "praxisanleitung", "leitung",
             "apn_experte", "hebamme", "ota_ata", "sonstige_pflege",
             "ausbildung", "werkstudent_praktikum", "nicht_pflege")]
    kept = {r["role_class"] for r in only_pflege(rows)}
    assert kept == {"pflegefachkraft", "fachpflege", "pflegehelfer", "praxisanleitung", "leitung",
                    "apn_experte", "hebamme", "ota_ata", "sonstige_pflege"}
    assert not kept & {"ausbildung", "werkstudent_praktikum", "nicht_pflege"}
    assert len(only_pflege(rows, keep_non_pflege=True)) == len(rows)   # inspection path is unfiltered


def test_edge_sink_write_drops_trainees(monkeypatch):
    """The gate is in EdgeSink.write itself, so every adapter inherits it."""
    from pflege_jobs.sinks import EdgeSink
    monkeypatch.setenv("PFLEGE_INGEST_URL", "http://x"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k"); monkeypatch.setenv("PFLEGE_INGEST_SECRET", "s")
    sink = EdgeSink(); posted = []
    sink._post = lambda body: (posted.append(body), {"observations": len(body.get("observations", [])), "employers": 0, "resolve": {}})[1]
    o = to_observation(RAW)
    stats = sink.write([o, {**o, "source_ref": "azubi", "role_class": "ausbildung"},
                        {**o, "source_ref": "fsj", "role_class": "werkstudent_praktikum"}], resolve=False)
    assert stats["dropped_non_pflege"] == 2
    sent = [r for b in posted for r in b.get("observations", [])]
    assert [r["role_class"] for r in sent] == ["pflegefachkraft"]
