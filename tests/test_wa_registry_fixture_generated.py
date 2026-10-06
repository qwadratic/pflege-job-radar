"""The committed tests/fixtures/wa_registry/registry_snapshot.json must still have the SAME SHAPE
as the live registry -- the set of fields each table carries, and their JSON value types. This is
the drift guard: it reads the real live registry (tools/wa_registry_fixture.fetch_live(), real
credentials required, see that module's own Usage) and compares its SCHEMA, per table, against the
committed fixture's. It goes red only when the registry itself grows/drops/retypes a column, never
after a plain crawl (new postings, same columns) and never after a regeneration's own PII-scrub
choices for an unchanged row -- unlike the old byte-for-byte version of this test, which went red
after every single crawl because `last_seen`/`verified_at`/`n_observations` move on every row.

Deliberately calls fetch_live(), not generate(): generate() also runs the Haiku name classifier as
part of the PII scrub (see tools/wa_registry_fixture.py's module docstring, B1) -- a schema check has
no business spending a model call, and must keep working even when `claude` is not on PATH.

Marked `network`: unlike every other WA-lane test, this one needs the real live board, so it is
excluded from the offline lane (`-m "not llm and not network"`) and from tests/conftest.py's
`_registry_rest_tripwire` autouse fixture."""
import json

import pytest

from tools import wa_registry_fixture as GEN

pytestmark = pytest.mark.network

TABLES = ("clinics", "v_postings", "postings_housing_evidence")


def _committed():
    path = GEN.FIXTURES_DIR / GEN.FIXTURE_FILE
    assert path.exists(), f"no committed fixture at {path} -- run `python -m tools.wa_registry_fixture --write` first"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_fixture_schema_matches_a_fresh_live_read():
    clinics, postings, evidence_rows = GEN.fetch_live()
    live = {"clinics": clinics, "v_postings": postings, "postings_housing_evidence": evidence_rows}
    fixture = _committed()

    problems = []
    for table in TABLES:
        problems += GEN.diff_schema(live[table], fixture[table], table)
    assert not problems, (
        "registry_snapshot.json's shape no longer matches the live registry -- regenerate with "
        "`python -m tools.wa_registry_fixture --write` and review the diff:\n" + "\n".join(problems)
    )
