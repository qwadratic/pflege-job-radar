"""Registry ↔ tests ↔ patterns consistency."""
import os
import pytest
from pflege_jobs import mechanics as M
from pflege_jobs import config as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_ids_unique_and_ten():
    ids = [m.id for m in M.REGISTRY]
    assert len(ids) == len(set(ids)) == 10


@pytest.mark.parametrize("m", M.REGISTRY, ids=lambda m: m.id)
def test_every_mechanic_has_test_file_and_texts(m):
    assert os.path.exists(os.path.join(ROOT, m.test_file)), m.test_file
    for k in ("de", "en"):
        assert m.title[k] and len(m.description[k]) > 80
        for i in m.inputs:
            assert i["label"][k]
    assert m.patterns_section in ("", *C.PATTERNS.keys())
    assert m.functions and all(callable(f) for f in m.functions)


@pytest.mark.parametrize("m", M.REGISTRY, ids=lambda m: m.id)
def test_examples_run(m):
    out = m.run({i["name"]: i["example"] for i in m.inputs})
    assert set(out) == {"result", "rule"}


def test_describe_with_source():
    d = M.describe(with_source=True)
    assert all(f["source"] for m in d for f in m["functions"])
