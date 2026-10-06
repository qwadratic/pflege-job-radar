"""Client identity loader (TASK-162): app/wa/config.py:client() and its four local copies in tools/
(daria_desk.py and the three email scripts, which do not import the app package). Every copy must fail
loudly on a bad file -- a string where a domain list belongs would otherwise turn into a set of single
characters. The email scripts run their whole pipeline at import, so their loader is lifted out with ast
and run on its own."""
import ast
import json
import os
import pathlib

import pytest

from app.wa import config as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL_COPIES = ["tools/daria_desk.py", "tools/email_index.py", "tools/email_ledgers.py", "tools/email_stage2b.py"]
GOOD = {"name": "Testfirma", "own_mail_domains": ["example.org"], "partner_mail_domains": ["partner.example"]}
BAD = [
    ("{not json", "not valid JSON"),
    (json.dumps(["Testfirma"]), 'no non-empty "name" key'),
    (json.dumps({**GOOD, "own_mail_domains": "example.org"}), '"own_mail_domains" must be a list of domain strings'),
    (json.dumps({**GOOD, "partner_mail_domains": ["partner.example", ""]}),
     '"partner_mail_domains" must be a list of domain strings'),
    (json.dumps({"name": "Testfirma", "own_mail_domains": ["example.org"]}),
     '"partner_mail_domains" must be a list of domain strings'),
]


def _tool_loader(rel):
    """-> that file's own _client_config function, compiled alone (no module-level code runs)."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_client_config")
    ns = {"os": os, "json": json, "Path": pathlib.Path, "REPO": ROOT}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), rel, "exec"), ns)
    return ns["_client_config"]


LOADERS = [("app", lambda: C.client())] + [(rel, _tool_loader(rel)) for rel in TOOL_COPIES]


@pytest.mark.parametrize("label,load", LOADERS, ids=[label for label, _ in LOADERS])
@pytest.mark.parametrize("text,message", BAD, ids=["bad_json", "not_an_object", "domains_a_string",
                                                   "empty_domain", "domains_missing"])
def test_every_loader_raises_naming_the_path(tmp_path, monkeypatch, label, load, text, message):
    path = tmp_path / "wa-client.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("WA_CLIENT_CONFIG", str(path))
    with pytest.raises(RuntimeError, match=message) as exc_info:
        load()
    assert str(path) in str(exc_info.value)


@pytest.mark.parametrize("label,load", LOADERS, ids=[label for label, _ in LOADERS])
def test_every_loader_reads_a_good_file_and_raises_on_a_missing_one(tmp_path, monkeypatch, label, load):
    path = tmp_path / "wa-client.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    monkeypatch.setenv("WA_CLIENT_CONFIG", str(path))
    assert load() == GOOD

    missing = tmp_path / "nowhere.json"
    monkeypatch.setenv("WA_CLIENT_CONFIG", str(missing))
    with pytest.raises(RuntimeError, match="client config not found"):
        load()


def test_the_committed_example_passes_every_loader(monkeypatch):
    monkeypatch.setenv("WA_CLIENT_CONFIG", str(ROOT / "config" / "wa-client.example.json"))
    for _, load in LOADERS:
        assert load()["name"] == "Beispiel Group"
