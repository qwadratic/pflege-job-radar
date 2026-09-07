"""Sinks: where observations go. All accept the canonical observation dicts.
- CsvSink: data/observations.csv + data/employers.csv (inspection, git-diffable)
- SqlSink: batched UPSERT .sql files (for psql users / Supabase SQL editor)
- EdgeSink: POST batches to the `pflege-ingest` Supabase Edge Function (DB connection inside)
(The v0.1 staging-table path was removed in migration 007: its column list could silently drift.)
"""
import csv
import json
import os
import time

import requests

from .schema import OBS_COLUMNS, ARRAY_COLUMNS, JSON_COLUMNS  # single source of truth
from . import config as C


def only_pflege(observations, keep_non_pflege=False):
    """Intake gate: only experienced, qualified nursing roles are stored.

    Drops every role_class in config.EXCLUDED_ROLE_CLASSES — non-nursing roles plus trainees
    (ausbildung) and interns/working students/volunteers (werkstudent_praktikum). Rows are still
    classified first, so the reason a row was dropped stays reconstructible from its role_rule.
    `keep_non_pflege=True` bypasses the gate (used by inspection sinks / tests only).
    """
    return observations if keep_non_pflege else [o for o in observations if o.get("role_class") not in C.EXCLUDED_ROLE_CLASSES]


def employers_from(observations):
    """Aggregate employer rows from observations (name_norm identity)."""
    emp = {}
    for o in observations:
        k = o.get("employer_name_norm")
        if not k:
            continue
        e = emp.setdefault(k, {"name_norm": k, "name_display": o["employer_name"], "employer_class": o["employer_class"],
                               "class_rule": o["employer_class_rule"], "aa_kundennummer_hashes": set()})
        if o.get("aa_kundennummer_hash"):
            e["aa_kundennummer_hashes"].add(o["aa_kundennummer_hash"])
    return [{**e, "aa_kundennummer_hashes": sorted(e["aa_kundennummer_hashes"])} for e in emp.values()]


class CsvSink:
    def __init__(self, out_dir):
        self.out_dir = out_dir; os.makedirs(out_dir, exist_ok=True)

    def write(self, observations, keep_non_pflege=False):
        observations = only_pflege(observations, keep_non_pflege)
        emps = employers_from(observations)
        with open(os.path.join(self.out_dir, "employers.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["name_norm", "name_display", "employer_class", "class_rule", "aa_kundennummer_hashes"])
            w.writeheader()
            for e in emps:
                w.writerow({**e, "aa_kundennummer_hashes": "|".join(e["aa_kundennummer_hashes"])})
        with open(os.path.join(self.out_dir, "observations.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=OBS_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for o in observations:
                row = dict(o)
                for c in ARRAY_COLUMNS:
                    if isinstance(row.get(c), list): row[c] = "|".join(row[c])
                w.writerow(row)
        return {"employers": len(emps), "observations": len(observations)}


def _sql_lit(v, col):
    if v is None:
        return "NULL"
    if col in ARRAY_COLUMNS:
        if not v: return "'{}'::text[]"
        return "ARRAY[" + ",".join(_sql_lit(x, None) for x in v) + "]::text[]"
    if col in JSON_COLUMNS:
        return "$j$" + (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) + "$j$::jsonb"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


class SqlSink:
    """Emits UPSERT SQL files in batches (idempotent). Same statements the edge function runs."""
    def __init__(self, out_dir, batch=250):
        self.out_dir, self.batch = out_dir, batch; os.makedirs(out_dir, exist_ok=True)

    def write(self, observations, keep_non_pflege=False):
        observations = only_pflege(observations, keep_non_pflege)
        emps = employers_from(observations)
        files = []
        with open(os.path.join(self.out_dir, "010_employers.sql"), "w", encoding="utf-8") as f:
            for i in range(0, len(emps), self.batch):
                vals = ",\n".join("(%s,%s,%s,%s,%s)" % (_sql_lit(e["name_norm"], None), _sql_lit(e["name_display"], None),
                                  _sql_lit(e["employer_class"], None), _sql_lit(e["class_rule"], None),
                                  _sql_lit(e["aa_kundennummer_hashes"], "aa_kundennummer_hashes")) for e in emps[i:i + self.batch])
                f.write(EMPLOYER_UPSERT.replace("__VALUES__", vals) + "\n")
        files.append("010_employers.sql")
        cols = [c for c in OBS_COLUMNS]
        for bi, i in enumerate(range(0, len(observations), self.batch)):
            name = f"020_observations_{bi:04d}.sql"
            with open(os.path.join(self.out_dir, name), "w", encoding="utf-8") as f:
                vals = ",\n".join("(" + ",".join(_sql_lit(o.get(c), c) for c in cols) + ")" for o in observations[i:i + self.batch])
                f.write(OBS_UPSERT.replace("__COLS__", ",".join(cols)).replace("__VALUES__", vals)
                        .replace("__SET__", ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("source_id", "source_ref"))) + "\n")
            files.append(name)
        with open(os.path.join(self.out_dir, "030_resolve.sql"), "w") as f:
            f.write("select * from pflege_jobs.resolve_postings();\n")
        files.append("030_resolve.sql")
        return {"employers": len(emps), "observations": len(observations), "files": files}


EMPLOYER_UPSERT = """insert into pflege_jobs.employers (name_norm,name_display,employer_class,class_rule,aa_kundennummer_hashes) values
__VALUES__
on conflict (name_norm) do update set name_display=excluded.name_display,
  employer_class=case when pflege_jobs.employers.class_source='keyword_rule' then excluded.employer_class else pflege_jobs.employers.employer_class end,
  class_rule=case when pflege_jobs.employers.class_source='keyword_rule' then excluded.class_rule else pflege_jobs.employers.class_rule end,
  aa_kundennummer_hashes=(select coalesce(array_agg(distinct x),'{}') from unnest(pflege_jobs.employers.aa_kundennummer_hashes || excluded.aa_kundennummer_hashes) x),
  last_seen=now();"""
OBS_UPSERT = """insert into pflege_jobs.posting_observations (__COLS__) values
__VALUES__
on conflict (source_id, source_ref) do update set __SET__;"""


class EdgeSink:
    """POSTs to the pflege-ingest edge function. Env: PFLEGE_INGEST_URL, SUPABASE_ANON_KEY, PFLEGE_INGEST_SECRET."""
    def __init__(self, url=None, anon_key=None, secret=None, batch=200, timeout=120):
        self.url = url or os.environ["PFLEGE_INGEST_URL"]
        self.anon = anon_key or os.environ["SUPABASE_ANON_KEY"]
        self.secret = secret or os.environ["PFLEGE_INGEST_SECRET"]
        self.batch, self.timeout = batch, timeout

    def _post(self, body):
        h = {"Authorization": f"Bearer {self.anon}", "apikey": self.anon, "x-ingest-secret": self.secret,
             "Content-Type": "application/json"}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_exc = None
        for i in range(3):
            try:
                r = requests.post(self.url, headers=h, data=data, timeout=self.timeout)
            except requests.exceptions.RequestException as e:
                last_exc = e
                if i < 2:
                    time.sleep(2 * (i + 1))
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code >= 500 and i < 2:
                time.sleep(2 * (i + 1))
                continue
            raise RuntimeError(f"ingest failed {r.status_code}: {r.text[:300]}")
        raise RuntimeError(f"ingest failed: {last_exc}")

    def write(self, observations, resolve=True, log=print, keep_non_pflege=False):
        n_in = len(observations); observations = only_pflege(observations, keep_non_pflege)
        # Deduplicate by (source_id, source_ref) — last wins — to avoid ON CONFLICT errors within a batch
        seen = {};
        for o in observations:
            seen[(o.get("source_id"), o.get("source_ref"))] = o
        observations = list(seen.values())
        emps = employers_from(observations)
        stats = {"employers": 0, "observations": 0, "dropped_non_pflege": n_in - len(observations)}
        for i in range(0, len(emps), self.batch):
            stats["employers"] += self._post({"employers": emps[i:i + self.batch]}).get("employers", 0)
        cols = OBS_COLUMNS
        for i in range(0, len(observations), self.batch):
            rows = [{c: o.get(c) for c in cols} for o in observations[i:i + self.batch]]
            stats["observations"] += self._post({"observations": rows}).get("observations", 0)
            log(f"ingested {min(i + self.batch, len(observations))}/{len(observations)}")
        if resolve:
            stats["resolve"] = self._post({"resolve": True}).get("resolve")
        return stats

    def write_clinics(self, rows, log=print):
        """POST full CLINIC_SPEC dicts to the ingest function's `clinics` op, in batches (MAX_ROWS 500
        server-side, self.batch client-side). Callers must send every column (schema.CLINIC_SPEC) with
        only the intended fields changed -- the edge function's upsert does `col=excluded.col` for every
        column except ats_type/careers_url (coalesce(nullif(excluded.col,''), stored)), so a partial dict
        nulls the rest. Returns the number of rows the server reports as upserted."""
        n = 0
        for i in range(0, len(rows), self.batch):
            batch = rows[i:i + self.batch]
            n += self._post({"clinics": batch}).get("clinics", 0)
            log(f"clinics upserted {min(i + self.batch, len(rows))}/{len(rows)}")
        return n

