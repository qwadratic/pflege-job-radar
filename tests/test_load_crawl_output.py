# TASK-154: crawlers/load_crawl_output.py is a top-level script (no `if __name__` guard, no importable
# entrypoint) that runs its whole pipeline on import -- draining the inbox, calling `pflege_jobs.cli`
# via subprocess, sleeping. Importing it directly in a test would execute all of that for real. Instead
# this extracts just the `q()`/`snapshot()` function source (the part before the executable script
# tail starting at `rows = []`) and execs it in an isolated namespace with a fake `requests` module, so
# the test runs against the ACTUAL file text (a mutation to the real file is what flips these red) with
# zero network/DB/subprocess side effects.
import os

_PATH = os.path.join(os.path.dirname(__file__), "..", "crawlers", "load_crawl_output.py")


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class FakeRequests:
    """Stands in for the `requests` module: records every GET, returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self._responses.pop(0)


def _load(fake_requests):
    src = open(_PATH, encoding="utf-8").read()
    start = src.index("def q(")
    end = src.index("\nrows = []")
    block = src[start:end]
    ns = {"U": "https://example.test", "H": {"apikey": "x"}, "requests": fake_requests}
    exec(compile(block, "load_crawl_output_extract", "exec"), ns)
    return ns["q"], ns["snapshot"]


def test_q_raises_on_non_200_instead_of_returning_the_error_body():
    # The original bug: a 57014 statement-timeout error body is a dict ({"code": "57014", ...}), and
    # q() used to hand that straight back as if it were row data -- the caller then iterated it as a
    # list of dicts and crashed with "TypeError: string indices must be integers, not 'str'" on the
    # first character of "code". q() must refuse before that happens.
    fake = FakeRequests([FakeResponse(status_code=500, json_data={"code": "57014", "message": "canceling statement due to statement timeout"}, text='{"code": "57014"}')])
    q, _snapshot = _load(fake)
    try:
        q("postings?select=posting_id")
        assert False, "q() must raise on a non-200 response, not return the error body"
    except RuntimeError as e:
        assert "500" in str(e)


def test_snapshot_queries_postings_not_v_postings_and_parses_the_embedded_shape():
    # AC#1 regression pin: snapshot()'s posting_id query must no longer touch v_postings (whose
    # employer_class-filtered form re-triggers the linked_towns CTE, confirmed live to cost ~965ms /
    # 8675 buffers vs ~6ms / 1566 buffers for the same result set read from the base `postings` table).
    fake = FakeRequests([
        FakeResponse(status_code=200, json_data=[
            {"posting_id": 101, "role_classes": {"is_pflege": True}},
            {"posting_id": 102, "role_classes": {"is_pflege": True}},
        ]),
        FakeResponse(status_code=200, json_data=[{"clinic_id": 1, "ats_type": "softgarden"}]),
    ])
    _q, snapshot = _load(fake)
    snap = snapshot()
    assert snap["live"] == {101, 102}
    assert snap["ats"] == {1: "softgarden"}
    postings_call = fake.calls[0]
    assert postings_call.startswith("https://example.test/rest/v1/postings?"), postings_call
    assert "v_postings" not in postings_call
    assert "employer_class" not in postings_call
    assert "source_codes" not in postings_call
    assert "clinic_id=not.is.null" in postings_call


if __name__ == "__main__":
    test_q_raises_on_non_200_instead_of_returning_the_error_body()
    test_snapshot_queries_postings_not_v_postings_and_parses_the_embedded_shape()
    print("OK")
