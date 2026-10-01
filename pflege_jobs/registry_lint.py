"""Registry lint: catch a careers_url that is not the clinic's own board -- a single job-detail page, a
posting's own page, or a page of an aggregator -- before it ever gets crawled.

TASK-86: 47601 (.../karriere/job/3bc89d91-7c4e-485d-ba7f-260fc7a5a378/) and
77901 (.../stellenangebote/gku-donau-ries-.../gesundheits-und-krankenpfleger-
.../) both slipped into the registry as a single posting standing in for the
whole board -- each produces exactly one junk row instead of a real crawl.
TASK-185: 66103's careers_url is an aggregator page (krankenpflegejobs24.de), so the whole aggregator's
postings were crawled as that clinic's board and filed under it; 77902/77903 carry one posting's url.
Offline, no network: runs over clinic rows already loaded elsewhere (`python -m pflege_jobs.registry_lint` lints the live table).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Hosts that list OTHER employers' postings. indeed.com and stepstone.de are the aggregator hosts
# data/purge_retired_sources.py purges (source 40); krankenpflegejobs24.de and meinestadt.de are the two
# further ones the registry or the pipeline met (66103's careers_url, TASK-185). A clinic's careers_url on
# one of them is that aggregator's page, not the clinic's board.
AGGREGATOR_HOSTS = ("indeed.com", "stepstone.de", "krankenpflegejobs24.de", "meinestadt.de")

# Each pattern matches a URL *path* shape known to name one specific posting
# rather than a board. Keep this list short and specific -- a board URL that
# merely contains "/stellenangebote/" with ONE slug (a category or "view all"
# page) must not match, only two nested slugs (category/specific-posting).
#
# job-slug: "/job/<anything>/" is a single-posting permalink on every vendor
# seen in this registry (Helios/Oracle, mvt-zentrum and gkg-bamberg's WordPress
# sites). A UUID slug (47601/67201/67601) is just one instance of that shape --
# matching UUID-only missed the human-readable-slug instances, live on 16268
# and 47102 (.../job/leitung-finanzen-medizincontrolling-m-w-d/ and
# .../job/stationshilfen-m-w-in-teilzeit-oder-auf-minijob-basis/).
JOB_DETAIL_SHAPES = [
    ("job-slug", re.compile(r"/job/[a-z0-9][a-z0-9-]*(?:/|$)", re.I)),
    ("stellenangebote-slug-slug",
     re.compile(r"/stellenangebote/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*(?:/|$)", re.I)),
]


@dataclass
class LintFinding:
    clinic_id: str
    shape: str
    careers_url: str
    name: str = ""

    def __str__(self):
        return f"clinic {self.clinic_id} {self.name!r}: careers_url is {self.shape}: {self.careers_url}"


def check_careers_url(careers_url: str) -> str | None:
    """Return the matched job-detail-page shape name, or None if the URL
    does not look like a single posting."""
    if not careers_url:
        return None
    for name, pattern in JOB_DETAIL_SHAPES:
        if pattern.search(careers_url):
            return name
    return None


def lint_rows(rows, posting_urls=frozenset()) -> list[LintFinding]:
    """rows: iterable of dicts each with at least clinic_id/careers_url (live pflege_jobs.clinics rows; name
    is carried into the finding). posting_urls: the external_url of postings (any status) -- a careers_url
    equal to one is that posting's own page, whatever its shape."""
    findings = []
    for row in rows:
        url = row.get("careers_url") or ""
        host = (urlparse(url).hostname or "").lower()
        shape = check_careers_url(url) or (
            "aggregator-host" if any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS) else
            "posting-url" if url in posting_urls else None)
        if shape:
            findings.append(LintFinding(row.get("clinic_id", ""), shape, url, row.get("name") or ""))
    return findings


if __name__ == "__main__":
    import sys
    from app import config as A
    findings = lint_rows(A.rest_get_all("clinics", {"select": "clinic_id,name,careers_url", "order": "clinic_id.asc"}),
                         {p["external_url"] for p in A.rest_get_all("postings", {"select": "external_url", "order": "posting_id.asc"})})
    if not findings:
        print("no job-detail-shaped, aggregator or posting-url careers_url found")
    for f in findings:
        print(f)
    sys.exit(f"{len(findings)} clinic(s) whose careers_url is not a board" if findings else 0)
