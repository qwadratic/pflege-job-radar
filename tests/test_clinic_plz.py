"""TASK-431 step 1, item 5: the clinic PLZ on the repo side. The column exists (pflege_jobs.clinics.plz, 0 of 651 filled);
this makes every writer able to carry it: CLINIC_SPEC, the ingest function's upsert, the RHV sync.
tools/fill_clinic_plz.py (the fill from the Krankenhausverzeichnis) is tested in tests/test_fill_clinic_plz.py."""
import csv
import pathlib
import re

from data.sync_rhv_reha import OUT_CSV, parse
from pflege_jobs.schema import CLINIC_SPEC, EMP_SPEC, IDENTITY, LINK_SPEC, OBS_SPEC, VERIFY_SPEC, pg_record_def

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_clinic_spec_carries_plz():
    assert ("plz", "text") in CLINIC_SPEC
    assert len(CLINIC_SPEC) == 18


def test_the_committed_ingest_function_is_the_render_of_template_and_schema():
    # edge/build_ingest.py renders index.ts; a column added to the schema without re-rendering would be deployed without it.
    tpl = (ROOT / "edge" / "pflege-ingest" / "index.template.ts").read_text()
    rendered = (tpl.replace("__OBS_COLS_DEF__", pg_record_def(OBS_SPEC)).replace("__EMP_COLS_DEF__", pg_record_def(EMP_SPEC))
                .replace("__VERIFY_COLS_DEF__", pg_record_def(VERIFY_SPEC)).replace("__CLINIC_COLS_DEF__", pg_record_def(CLINIC_SPEC))
                .replace("__LINK_COLS_DEF__", pg_record_def(LINK_SPEC)).replace("__IDENTITY__", ",".join(IDENTITY)))
    index = (ROOT / "edge" / "pflege-ingest" / "index.ts").read_text()
    assert index == rendered
    assert re.search(r"const CLINIC_COLS_DEF = `[^`]*\bplz text\b", index)


def test_the_clinics_upsert_keeps_a_stored_plz_when_the_caller_sends_none():
    # Registry rows from the plan PDF, the Diakoneo list and the careers discovery carry no PLZ; a plain `plz=excluded.plz` would
    # blank every filled one on their next write -- same as ats_type/careers_url, which are coalesced for that reason.
    for name in ("index.template.ts", "index.ts"):
        text = (ROOT / "edge" / "pflege-ingest" / name).read_text()
        m = re.search(r"\[((?:\"[a-z_]+\",?\s*)+)\]\.includes\(c\)\s*\?\s*`\$\{c\}=coalesce\(nullif\(excluded\.\$\{c\},''\), pflege_jobs\.clinics\.\$\{c\}\)`", text)
        assert m, name
        assert re.findall(r'"([a-z_]+)"', m.group(1)) == ["ats_type", "careers_url", "plz"], name


def test_the_rhv_sync_keeps_the_plz_column_of_the_workbook():
    rows = parse()
    assert len(rows) == 229
    assert all(re.fullmatch(r"\d{5}", r["plz"]) for r in rows)
    by_id = {r["clinic_id"]: r for r in rows}
    assert (by_id["RH1847"]["town"], by_id["RH1847"]["plz"]) == ("Bad Tölz", "83646")        # Rehaklinik FRISIA Munkert, RHV_2024 PLZ 83646


def test_the_committed_reha_csv_has_the_plz_column_and_equals_the_parse():
    with open(ROOT / OUT_CSV, newline="", encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert list(got[0]) == [k for k, _ in CLINIC_SPEC]
    assert [(r["clinic_id"], r["plz"]) for r in got] == [(r["clinic_id"], r["plz"]) for r in parse()]
