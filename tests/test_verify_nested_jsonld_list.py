"""pflege_jobs.verify: a JSON-LD address field nested TWO list levels deep still crashes
_walk_jsonld with 'TypeError: unhashable type: list' (TASK-94 AC#3, the verify-crash half).

_scalar() (pflege_jobs/verify.py) unwraps exactly one level of list-nesting -- built for the
one-item-list shape www.komm-ins-klinikland.de ships ({"addressLocality": ["Kitzingen"]}, fixed in
38287cc; see test_verify_escalation.py's
test_extract_location_reads_a_list_valued_jsonld_address_instead_of_crashing). A field nested a
SECOND level ("addressLocality": [["Kitzingen"]]) still lands a list inside the (city, plz) tuple
_walk_jsonld puts into a set at pflege_jobs/verify.py:144
(`distinct = {a for a in addrs if a[0] or a[1]}`), and a tuple containing a list is not hashable.
Both run 109 (2026-09-21) and run 117 (2026-09-22) logged this exact TypeError class from a
mode=verify pass.

This reproduces at the extract_location level (first test below) -- against the CURRENT tree, not
a hypothesis. It does NOT reproduce run 117's whole-pass abort, though: verify_all() already
contains a crash like this to the one row that triggered it (http_one's try/except wraps the
entire verify_one() call, added in 38287cc for run 109), so the second test below -- run against
the real verify_all(), not a mock of verify_one -- shows the pass completing with the other row
intact. See TASK-94's implementation notes for the fix (written out as a diff, not applied here)
and for why run 117's full abort is still unexplained by this or any other production call path.
"""
import pytest

from pflege_jobs.verify import extract_location

_DOUBLE_NESTED_JSONLD = """<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Pflegefachkraft",
 "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress",
   "postalCode": "97318", "addressLocality": [["Kitzingen"]]}}}
</script></head><body>Pflegefachkraft Kitzingen</body></html>"""


def test_extract_location_reads_a_double_nested_jsonld_list():
    """Fixed 2026-09-22: _scalar() unwraps until the value is not a list, instead of stripping
    exactly one layer. A second layer used to leave a list inside the (city, plz) tuple that
    _walk_jsonld puts into a set at pflege_jobs/verify.py:144, which is unhashable."""
    assert extract_location(_DOUBLE_NESTED_JSONLD) == ("Kitzingen", "97318", "jsonld")


def test_extract_location_reads_the_shallower_shapes_unchanged():
    """The one-level list this helper was originally written for, a plain string, and an empty
    list all keep working -- the unwrap loop must not change any of them."""
    assert extract_location(_DOUBLE_NESTED_JSONLD.replace('[["Kitzingen"]]', '["Kitzingen"]')) == ("Kitzingen", "97318", "jsonld")
    assert extract_location(_DOUBLE_NESTED_JSONLD.replace('[["Kitzingen"]]', '"Kitzingen"')) == ("Kitzingen", "97318", "jsonld")
    assert extract_location(_DOUBLE_NESTED_JSONLD.replace('[["Kitzingen"]]', '[]')) == (None, "97318", "jsonld")


def test_verify_all_contains_a_row_level_crash_instead_of_aborting_the_pass(monkeypatch):
    """Independent of the _scalar fix: whatever verify_one() raises on one row must stay on that
    row. This is what makes run 117's whole-pass abort still unexplained -- no row-level crash in
    the HTTP rung can take the pass down, so its cause lies elsewhere (TASK-94)."""
    import pflege_jobs.verify as V

    class _Resp:
        def __init__(self, url, html):
            self.status_code, self.url, self.text, self.headers = 200, url, html, {}

    class _FakeSession:
        def get(self, url, headers=None, timeout=None, allow_redirects=None):
            html = _DOUBLE_NESTED_JSONLD if "boom" in url else "<html>Pflegefachkraft München</html>"
            return _Resp(url, html)

    monkeypatch.setattr(V, "requests", type("R", (), {"Session": lambda: _FakeSession(), "RequestException": Exception}))

    rows = [{"posting_id": 1, "external_url": "https://x.example/boom/1", "source_url": None,
             "title": "Pflegefachkraft Kitzingen"},
            {"posting_id": 2, "external_url": "https://x.example/ok/2", "source_url": None,
             "title": "Pflegefachkraft München"}]

    def _boom(*a, **k):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(V, "extract_location", lambda html, towns=None: _boom() if "Kitzingen" in html else (None, None, None))
    res = V.verify_all(rows, workers=2, log=lambda *a, **k: None, render=False, firecrawl=False)
    by_id = {r["posting_id"]: r for r in res}
    assert len(res) == 2, "the crashing row must not take the other row down with it"
    assert by_id[1]["verify_status"] == "error" and "unhashable" in by_id[1]["verify_note"]
    assert by_id[2]["verify_status"] == "live"
