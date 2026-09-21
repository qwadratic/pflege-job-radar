"""sql/001_schema.sql drift regressions (TASK-73 AC5).

pflege_jobs/schema.py's OBS_SPEC is the checked-in source of truth for the observation column set
(its own docstring says so, and edge/pflege-ingest/index.ts is generated from it) -- every one of
those columns must be a real column of pflege_jobs.posting_observations here, and every one that
resolve_postings() folds into the golden `postings` row (i.e. is not one of the per-observation-only
fields it deliberately strips before merging) must both be a `postings` column and be assigned
somewhere in the function body. Before this fix, department_raw/enr_pay_grade/enr_pay_text/
enr_requirements/enr_experience were none of those things.
"""
import pathlib
import re

import pytest

from pflege_jobs.schema import OBS_COLUMNS

ROOT = pathlib.Path(__file__).resolve().parents[1]
SQL = (ROOT / "sql" / "001_schema.sql").read_text(encoding="utf-8")

# The same exclusion list resolve_postings() itself subtracts before merging an observation's fields
# into the golden posting (per-observation identity/bookkeeping columns, not golden-row fields).
NOT_GOLDEN = {"source_id", "source_ref", "source_url", "observed_at", "payload", "locations",
              "content_hash", "fuzzy_key", "details_fetched_at", "details_error", "employer_name",
              "employer_name_norm", "employer_class", "employer_class_rule", "aa_kundennummer_hash"}


def _table_columns(table):
    body = SQL.split(f"create table if not exists pflege_jobs.{table} (", 1)[1].split("\n);", 1)[0]
    cols = set()
    for frag in body.replace("\n", " ").split(","):
        tok = frag.split()
        if len(tok) >= 2 and tok[0].isidentifier() and tok[0] not in ("unique", "primary", "references"):
            cols.add(tok[0])
    return cols


def _resolve_postings_body():
    fn = SQL.split("create or replace function pflege_jobs.resolve_postings()", 1)[1]
    fn = fn.split("$$;", 1)[0]
    return re.sub(r"--[^\n]*", "", fn)          # drop line comments so they can't hide/fake a match


@pytest.mark.parametrize("col", OBS_COLUMNS)
def test_every_obs_spec_column_is_a_posting_observations_column(col):
    assert col in _table_columns("posting_observations"), \
        f"pflege_jobs/schema.py OBS_SPEC has {col!r} but sql/001_schema.sql's posting_observations does not"


@pytest.mark.parametrize("col", [c for c in OBS_COLUMNS if c not in NOT_GOLDEN])
def test_every_golden_column_is_declared_and_assigned_in_resolve_postings(col):
    assert col in _table_columns("postings"), f"{col!r} is not a pflege_jobs.postings column"
    body = _resolve_postings_body()
    assert re.search(rf"\b{re.escape(col)}\s*=", body), \
        f"resolve_postings() never assigns {col!r} -- a replay would silently drop it from every posting"


def test_resolve_postings_does_not_unconditionally_force_status_open():
    """A posting mark_expired() (or any future expiry path) closed must not be silently reopened just
    because resolve_postings() re-ran over its still-on-file observations."""
    body = _resolve_postings_body()
    assert not re.search(r"status\s*=\s*'open'", body), \
        "resolve_postings() unconditionally sets status='open' -- replaying it would reopen every expired posting"
