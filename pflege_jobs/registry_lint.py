"""Registry lint: catch a careers_url that is a single job-detail page instead
of a board/listing page, before it ever gets crawled.

TASK-86: 47601 (.../karriere/job/3bc89d91-7c4e-485d-ba7f-260fc7a5a378/) and
77901 (.../stellenangebote/gku-donau-ries-.../gesundheits-und-krankenpfleger-
.../) both slipped into the registry as a single posting standing in for the
whole board -- each produces exactly one junk row instead of a real crawl.
Offline, no network: runs over the CSV rows/fields already loaded elsewhere.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# Each pattern matches a URL *path* shape known to name one specific posting
# rather than a board. Keep this list short and specific -- a board URL that
# merely contains "/stellenangebote/" with ONE slug (a category or "view all"
# page) must not match, only two nested slugs (category/specific-posting).
JOB_DETAIL_SHAPES = [
    ("job-uuid", re.compile(r"/job/" + _UUID + r"(?:/|$)", re.I)),
    ("stellenangebote-slug-slug",
     re.compile(r"/stellenangebote/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*(?:/|$)", re.I)),
]


@dataclass
class LintFinding:
    clinic_id: str
    shape: str
    careers_url: str


def check_careers_url(careers_url: str) -> str | None:
    """Return the matched job-detail-page shape name, or None if the URL
    does not look like a single posting."""
    if not careers_url:
        return None
    for name, pattern in JOB_DETAIL_SHAPES:
        if pattern.search(careers_url):
            return name
    return None


def lint_rows(rows) -> list[LintFinding]:
    """rows: iterable of dicts each with at least clinic_id/careers_url
    (e.g. csv.DictReader over data/registry/clinics.csv, or live clinics.clinics rows)."""
    findings = []
    for row in rows:
        shape = check_careers_url(row.get("careers_url", ""))
        if shape:
            findings.append(LintFinding(row.get("clinic_id", ""), shape, row["careers_url"]))
    return findings


def lint_csv(path: str = "data/registry/clinics.csv") -> list[LintFinding]:
    with open(path, newline="", encoding="utf-8") as f:
        return lint_rows(csv.DictReader(f))


if __name__ == "__main__":
    findings = lint_csv()
    if not findings:
        print("no job-detail-shaped careers_url found")
    for f in findings:
        print(f"{f.clinic_id}\t{f.shape}\t{f.careers_url}")
