"""Tests for app/wa/status_docs.py (public token-gated status documents, Ivan 2026-10-06) and
tools/status_docs_publish.py (the local publish tool). All offline: WA_STATUS_DOCS_HOME is always
monkeypatched to a tmp_path, never the real ~/.local/state/pflege-status.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import asgi
from app.wa import config as C
from app.wa import status_docs as SD
from tools import status_docs_publish as PUB

READ_TOKEN = "test-read-token"
WRITE_TOKEN = "test-write-token"
RH = {"Authorization": f"Bearer {READ_TOKEN}"}
WH = {"Authorization": f"Bearer {WRITE_TOKEN}"}
PUBLIC_BASE = "https://test.example/s/"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """One tmp sqlite (unused by this module, but asgi's startup hook still needs it), both pro
    tokens set, and WA_STATUS_DOCS_HOME pointed at a tmp dir -- never the real home dir."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setenv("WA_API_TOKEN", READ_TOKEN)
    monkeypatch.setenv("WA_API_WRITE_TOKEN", WRITE_TOKEN)
    monkeypatch.setenv("WA_SALES_BRAIN_PATH", str(tmp_path / "no-such-sales-brain.sqlite"))
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(tmp_path / "status-home"))
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", PUBLIC_BASE)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    return tmp_path


@pytest.fixture()
def client(env):
    """``with`` runs asgi.py's startup hook -- see tests/test_wa_pro_api.py's own fixture for why
    this is load-bearing."""
    with TestClient(asgi.app) as c:
        yield c


def _src(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir()
    for fname, content in files.items():
        (d / fname).write_text(content)
    return d


def _publish(tmp_path, slug="2026-10-06-test-candidate", files=None, idx=0):
    """Publishes through the real tool (never a raw mkdir) and returns the minted token."""
    files = files or {"index.html": "<html>index</html>"}
    src = _src(tmp_path, f"src-{slug}-{idx}", files)
    url = PUB.publish(slug, str(src))
    assert url.startswith(PUBLIC_BASE)
    return url[len(PUBLIC_BASE):].rstrip("/")


# --- routes: auth ------------------------------------------------------------------------------

def test_status_503_when_no_token_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.delenv("WA_API_TOKEN", raising=False)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(tmp_path / "status-home"))
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    r = TestClient(asgi.app).get("/api/wa/pro/status/" + "a" * 22 + "/")
    assert r.status_code == 503


def test_status_401_without_bearer(client, tmp_path):
    token = _publish(tmp_path)
    assert client.get(f"/api/wa/pro/status/{token}/").status_code == 401


def test_status_401_with_wrong_bearer(client, tmp_path):
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# --- routes: index (both no-slash and trailing-slash, explicit, never a redirect) --------------

def test_index_variants_200_no_redirect_with_read_token(client, tmp_path):
    token = _publish(tmp_path)
    for path in (f"/api/wa/pro/status/{token}", f"/api/wa/pro/status/{token}/",
                 f"/api/wa/pro/status/{token}/index.html"):
        r = client.get(path, headers=RH, follow_redirects=False)
        assert r.status_code == 200, path
        assert r.text == "<html>index</html>"


def test_index_200_with_write_token_too(client, tmp_path):
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/", headers=WH, follow_redirects=False)
    assert r.status_code == 200


def test_detail_html_200(client, tmp_path):
    token = _publish(tmp_path, files={"index.html": "<html>i</html>", "detail.html": "<html>d</html>"})
    r = client.get(f"/api/wa/pro/status/{token}/detail.html", headers=RH)
    assert r.status_code == 200
    assert r.text == "<html>d</html>"
    assert r.headers["content-type"].startswith("text/html")


def test_pdf_200_with_pdf_content_type(client, tmp_path):
    token = _publish(tmp_path, files={"index.html": "<html>i</html>", "housing-quote.pdf": "%PDF-fake"})
    r = client.get(f"/api/wa/pro/status/{token}/housing-quote.pdf", headers=RH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


def test_response_headers_present_on_200(client, tmp_path):
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/", headers=RH)
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-cache"


# --- routes: 404s --------------------------------------------------------------------------------

def test_unknown_well_formed_token_is_404(client):
    r = client.get("/api/wa/pro/status/" + "a" * 22 + "/", headers=RH)
    assert r.status_code == 404


@pytest.mark.parametrize("token", [
    "a" * 10,            # too short
    "a" * 23,             # one char too many
    "a" * 21 + ".",       # right length, one char outside the allow-list
])
def test_malformed_tokens_are_404(client, token):
    r = client.get(f"/api/wa/pro/status/{token}/", headers=RH)
    assert r.status_code == 404


@pytest.mark.parametrize("name", ["x.html", ".hidden", "index.htm", "report.PDF"])
def test_bad_names_are_404(client, tmp_path, name):
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/{name}", headers=RH)
    assert r.status_code == 404


def test_dotdot_encoded_slash_path_is_404(client, tmp_path):
    """../index.html, sent as an encoded %2e%2e%2f path, decodes to a path with one MORE segment
    than any of the three specific routes match (token, "..", "index.html") -- without the
    catch-all route (review item 3) this used to fall straight through to Starlette's own
    unauthenticated {"detail": "Not Found"}, never reaching _authorize or this module's own 404
    body. Now it gets exactly the shared body, same as every other failure in this module."""
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/%2e%2e%2findex.html", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_symlinked_file_is_404(client, tmp_path):
    token = _publish(tmp_path)
    target = C.status_docs_home() / "www" / token / "index.html"
    real = tmp_path / "elsewhere.html"
    real.write_text("<html>elsewhere</html>")
    target.unlink()
    target.symlink_to(real)
    r = client.get(f"/api/wa/pro/status/{token}/", headers=RH)
    assert r.status_code == 404


def test_symlinked_token_dir_is_404(client, tmp_path):
    token = _publish(tmp_path)
    www = C.status_docs_home() / "www"
    real_dir = www / token
    elsewhere = tmp_path / "elsewhere-dir"
    real_dir.rename(elsewhere)
    (www / token).symlink_to(elsewhere, target_is_directory=True)
    r = client.get(f"/api/wa/pro/status/{token}/", headers=RH)
    assert r.status_code == 404


def test_all_404_bodies_are_identical(client, tmp_path):
    token = _publish(tmp_path)
    responses = [
        client.get("/api/wa/pro/status/" + "a" * 22 + "/", headers=RH),
        client.get(f"/api/wa/pro/status/{token}/x.html", headers=RH),
        client.get(f"/api/wa/pro/status/{token}/.hidden", headers=RH),
        client.get(f"/api/wa/pro/status/{token}/index.htm", headers=RH),
        client.get(f"/api/wa/pro/status/{token}/report.PDF", headers=RH),
        client.get("/api/wa/pro/status/" + "a" * 10 + "/", headers=RH),
    ]
    assert all(r.status_code == 404 for r in responses)
    assert len({r.text for r in responses}) == 1


# --- routes: 404s, with the offending file/dir really ON DISK (review item 1) ------------------
#
# Every test above asks for a file that never existed on disk at all, so removing the check it
# claims to cover (TOKEN_RE, allowed_name, the length bound inside PDF_NAME_RE, ...) would not turn
# it red: _serve's final "not path.is_file()" would still answer 404 for an unrelated reason. Each
# test below puts the exact offending entry on disk first, so only the one check named in the test
# name can be refusing it.

def test_bad_name_on_disk_is_404_name_check_catches_it(client, tmp_path):
    """x.html sitting right there in a real, valid token's own directory -- only allowed_name can
    be refusing this; there is no 'no such file' excuse left."""
    token = _publish(tmp_path)
    (C.status_docs_home() / "www" / token / "x.html").write_text("<html>should never serve</html>")
    r = client.get(f"/api/wa/pro/status/{token}/x.html", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_bad_name_case_pdf_on_disk_is_404(client, tmp_path):
    """report.PDF (uppercase suffix) really on disk -- PDF_NAME_RE is lowercase-only, and that is
    the only thing standing between this file and a 200."""
    token = _publish(tmp_path)
    (C.status_docs_home() / "www" / token / "report.PDF").write_text("%PDF-fake")
    r = client.get(f"/api/wa/pro/status/{token}/report.PDF", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_pdf_name_one_past_the_length_bound_is_404_even_on_disk(client, tmp_path):
    """PDF_NAME_RE is ``[a-z0-9][a-z0-9._-]{0,80}\\.pdf`` -- one mandatory leading character plus
    at most 80 more, so 81 more than the mandatory first one is already one past the bound (82
    characters before ".pdf" in total). The file really exists on disk, so only that length check
    can be refusing it."""
    token = _publish(tmp_path)
    bad_name = "a" + "a" * 81 + ".pdf"   # stem = 82 chars; PDF_NAME_RE allows at most 81
    (C.status_docs_home() / "www" / token / bad_name).write_text("%PDF-fake")
    r = client.get(f"/api/wa/pro/status/{token}/{bad_name}", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


@pytest.mark.parametrize("prefix", [".new-", ".old-"])
def test_dot_prefixed_temp_dir_is_never_servable_as_a_token(client, tmp_path, prefix):
    """tools/status_docs_publish.py's own atomic-swap siblings (a leftover .new-<token> or
    .old-<token>, e.g. from a crash mid-publish) must never be reachable as a token -- even with a
    real index.html inside, and even requested literally as that dot-prefixed name. TOKEN_RE has no
    '.' in its character class, so this is refused before the filesystem is ever touched for it."""
    token = _publish(tmp_path)
    www = C.status_docs_home() / "www"
    dotdir_name = f"{prefix}{token}"
    (www / dotdir_name).mkdir()
    (www / dotdir_name / "index.html").write_text("<html>leftover, never served</html>")
    r = client.get(f"/api/wa/pro/status/{dotdir_name}/", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_23_char_token_dir_is_404_even_with_index_html_on_disk(client, tmp_path):
    """A directory one character longer than TOKEN_RE allows, with a real index.html inside --
    only the length check in TOKEN_RE can be refusing this."""
    token23 = "a" * 23
    www = C.status_docs_home() / "www"
    www.mkdir(parents=True, exist_ok=True)
    (www / token23).mkdir()
    (www / token23 / "index.html").write_text("<html>should never serve</html>")
    r = client.get(f"/api/wa/pro/status/{token23}/", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_dotdot_token_traversal_is_404_even_with_a_real_file_one_level_up(client, tmp_path):
    """status_docs_home()/index.html sits one level above www/. A token of '..' would, if TOKEN_RE
    did not refuse it first, make _serve's own ``home / "www" / token`` resolve to ``home`` itself
    and serve exactly this file. Sent as an encoded %2e%2e so no HTTP client collapses the path
    before it ever reaches this process -- TOKEN_RE refuses it outright, before any path is even
    built from it."""
    _publish(tmp_path)   # makes sure status_docs_home()/www/ already exists
    (C.status_docs_home() / "index.html").write_text("<html>never reachable this way</html>")
    r = client.get("/api/wa/pro/status/%2e%2e/index.html", headers=RH)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


# --- routes: auth runs before ANY filesystem check (review item 2a) ----------------------------
#
# {token} is a reusable placeholder, filled in with a real, freshly published token (index.html
# only -- detail.html deliberately left unpublished) by the test itself.

_NO_BEARER_CASES = {
    "unknown-token-no-slash":    "/api/wa/pro/status/" + "u" * 22,
    "unknown-token-slash":       "/api/wa/pro/status/" + "u" * 22 + "/",
    "unknown-token-name":        "/api/wa/pro/status/" + "u" * 22 + "/index.html",
    "malformed-token-no-slash":  "/api/wa/pro/status/too-short",
    "malformed-token-slash":     "/api/wa/pro/status/too-short/",
    "malformed-token-name":      "/api/wa/pro/status/too-short/index.html",
    "bad-name":                  "/api/wa/pro/status/{token}/x.html",
    "missing-detail-html":       "/api/wa/pro/status/{token}/detail.html",
    "existing-file-no-slash":    "/api/wa/pro/status/{token}",
    "existing-file-slash":       "/api/wa/pro/status/{token}/",
    "existing-file-name":        "/api/wa/pro/status/{token}/index.html",
}


@pytest.mark.parametrize("path_template", _NO_BEARER_CASES.values(), ids=list(_NO_BEARER_CASES))
def test_401_without_bearer_before_any_filesystem_check(client, tmp_path, path_template):
    """No bearer at all: an unknown token, a malformed token, a bad name, a token missing
    detail.html, and an otherwise-servable existing file all answer the exact same 401 -- on every
    route shape (no-slash, trailing-slash, /{name}). A 404 anywhere here would mean the token (or
    the filesystem) was consulted before auth, which would let a prober learn whether a token
    exists without ever presenting a bearer; a 200 on the existing-file cases would mean auth was
    skipped for that route shape entirely."""
    token = _publish(tmp_path)
    r = client.get(path_template.format(token=token))
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid or missing bearer token"}


# --- routes: no redirect, no framework-generic 404, anywhere under this prefix (review item 3) --

def test_trailing_slash_after_name_is_404_not_a_307(client, tmp_path):
    """.../{token}/index.html/ matches none of the three specific routes (all declared with no
    extra trailing slash) -- without the catch-all route, Starlette's own redirect_slashes would
    answer a 307 whose Location names this harness's own host, before auth ever runs."""
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/index.html/", headers=RH, follow_redirects=False)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_trailing_slash_after_name_is_401_without_bearer(client, tmp_path):
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}/index.html/", follow_redirects=False)
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid or missing bearer token"}


def test_double_trailing_slash_is_404_not_a_307(client, tmp_path):
    """.../{token}// -- same undeclared-shape problem as the single extra trailing slash above."""
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}//", headers=RH, follow_redirects=False)
    assert r.status_code == 404
    assert r.json() == {"detail": SD._NOT_FOUND}


def test_double_trailing_slash_is_401_without_bearer(client, tmp_path):
    token = _publish(tmp_path)
    r = client.get(f"/api/wa/pro/status/{token}//", follow_redirects=False)
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid or missing bearer token"}


# --- tool: status_docs_publish.py -----------------------------------------------------------------

def test_publish_mints_a_22_char_token_prints_url_mode_700_600(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", PUBLIC_BASE)
    src = _src(tmp_path, "src1", {"index.html": "<html>v1</html>"})

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith(f"URL: {PUBLIC_BASE}")
    token = out[len(f"URL: {PUBLIC_BASE}"):].rstrip("/")
    assert len(token) == 22

    assert oct(status_home.stat().st_mode & 0o777) == oct(0o700)
    assert oct((status_home / "www").stat().st_mode & 0o777) == oct(0o700)
    tokens_path = status_home / "tokens.tsv"
    assert oct(tokens_path.stat().st_mode & 0o777) == oct(0o600)
    assert tokens_path.read_text() == f"2026-10-06-anna\t{token}\n"
    assert (status_home / "www" / token / "index.html").read_text() == "<html>v1</html>"


def test_publish_second_time_reuses_token_and_replaces_content(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", PUBLIC_BASE)
    src1 = _src(tmp_path, "src1", {"index.html": "<html>v1</html>"})
    PUB.main(["2026-10-06-anna", str(src1)])
    url1 = capsys.readouterr().out.strip()

    src2 = _src(tmp_path, "src2", {"index.html": "<html>v2</html>", "detail.html": "<html>d2</html>"})
    rc = PUB.main(["2026-10-06-anna", str(src2)])
    assert rc == 0
    url2 = capsys.readouterr().out.strip()

    assert url1 == url2   # same token reused
    token = url2[len(f"URL: {PUBLIC_BASE}"):].rstrip("/")
    token_dir = status_home / "www" / token
    assert (token_dir / "index.html").read_text() == "<html>v2</html>"
    assert (token_dir / "detail.html").read_text() == "<html>d2</html>"
    leftover_dot_dirs = [p.name for p in (status_home / "www").iterdir() if p.name.startswith(".")]
    assert leftover_dot_dirs == []
    # exactly one entry in www/ -- the live token dir, nothing else
    assert [p.name for p in (status_home / "www").iterdir()] == [token]


def test_publish_different_slug_gets_a_different_token(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", PUBLIC_BASE)
    src_a = _src(tmp_path, "srca", {"index.html": "a"})
    src_b = _src(tmp_path, "srcb", {"index.html": "b"})
    PUB.main(["2026-10-06-anna", str(src_a)])
    url_a = capsys.readouterr().out.strip()
    PUB.main(["2026-10-06-boris", str(src_b)])
    url_b = capsys.readouterr().out.strip()
    assert url_a != url_b


def test_publish_missing_index_html_exits_1_and_publishes_nothing(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-noindex", {"detail.html": "<html>d</html>"})

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 1
    assert "index.html" in capsys.readouterr().err
    assert not status_home.exists()


def test_publish_extra_file_exits_1_naming_it_and_publishes_nothing(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-extra", {"index.html": "<html>i</html>", "notes.txt": "x"})

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 1
    assert "notes.txt" in capsys.readouterr().err
    assert not status_home.exists()


def test_publish_subdir_exits_1_naming_it_and_publishes_nothing(tmp_path, monkeypatch, capsys):
    """Named extra.pdf, not extra_dir (review item 2c): extra_dir's own name would already fail
    allowed_name on its own, so a test built on it cannot tell whether the dedicated subdirectory
    check is the thing refusing it. extra.pdf matches allowed_name's own pattern -- only the
    ``entry.is_dir()`` check can be refusing this one."""
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-subdir", {"index.html": "<html>i</html>"})
    (src / "extra.pdf").mkdir()

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 1
    assert "extra.pdf" in capsys.readouterr().err
    assert not status_home.exists()


def test_publish_dotfile_exits_1_naming_it_and_publishes_nothing(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-dotfile", {"index.html": "<html>i</html>", ".DS_Store": "x"})

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 1
    assert ".DS_Store" in capsys.readouterr().err
    assert not status_home.exists()


def test_publish_symlink_exits_1_naming_it_and_publishes_nothing(tmp_path, monkeypatch, capsys):
    """Named detail.html, not sneaky.html (review item 2b): sneaky.html's own name would already
    fail allowed_name on its own, so a test built on it cannot tell whether the dedicated symlink
    check is the thing refusing it. detail.html passes allowed_name -- only the ``entry.is_symlink()``
    check can be refusing this one."""
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-symlink", {"index.html": "<html>i</html>"})
    real = tmp_path / "real.html"
    real.write_text("x")
    (src / "detail.html").symlink_to(real)

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 1
    assert "detail.html" in capsys.readouterr().err
    assert not status_home.exists()


def test_publish_bad_slug_exits_1(tmp_path, monkeypatch):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-badslug", {"index.html": "x"})

    rc = PUB.main(["not-a-valid-slug", str(src)])
    assert rc == 1
    assert not status_home.exists()


def test_publish_second_time_cleans_a_leftover_dot_dir_from_a_crashed_run(tmp_path, monkeypatch, capsys):
    """Simulates a crash mid-swap on a previous publish of this exact slug (review item 4): a
    leftover .old-<token> and .new-<token> sibling, each left sitting in www/ with content inside,
    as a killed process might leave behind. The NEXT publish of the same slug must clean both up --
    not just succeed around them."""
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", PUBLIC_BASE)
    src1 = _src(tmp_path, "src1", {"index.html": "<html>v1</html>"})
    PUB.main(["2026-10-06-anna", str(src1)])
    url1 = capsys.readouterr().out.strip()
    token = url1[len(f"URL: {PUBLIC_BASE}"):].rstrip("/")

    www = status_home / "www"
    (www / f".old-{token}").mkdir()
    (www / f".old-{token}" / "stale.txt").write_text("leftover from a crashed run")
    (www / f".new-{token}").mkdir()
    (www / f".new-{token}" / "stale.txt").write_text("leftover from a crashed run")

    src2 = _src(tmp_path, "src2", {"index.html": "<html>v2</html>"})
    rc = PUB.main(["2026-10-06-anna", str(src2)])
    assert rc == 0
    capsys.readouterr()

    assert [p.name for p in www.iterdir()] == [token]   # both leftovers gone, only the live dir left
    assert (www / token / "index.html").read_text() == "<html>v2</html>"


def test_publish_malformed_token_in_tokens_tsv_exits_1_loudly_no_silent_remint(tmp_path, monkeypatch, capsys):
    """A hand-edited or otherwise corrupted tokens.tsv row (review item 8) -- a token that does not
    fullmatch TOKEN_RE -- must stop the publish loudly, naming the slug and the offending line,
    never mint a fresh token to paper over it (CLAUDE.md "no safety nets")."""
    status_home = tmp_path / "status-home"
    status_home.mkdir(mode=0o700)
    bad_line = "2026-10-06-anna\tnot-a-valid-token\n"
    (status_home / "tokens.tsv").write_text(bad_line)
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    src = _src(tmp_path, "src-badtoken", {"index.html": "<html>i</html>"})

    rc = PUB.main(["2026-10-06-anna", str(src)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "2026-10-06-anna" in err
    assert "not-a-valid-token" in err
    # no silent re-mint: tokens.tsv is untouched
    assert (status_home / "tokens.tsv").read_text() == bad_line
    # nothing was published: www/ exists (created before the token is read) but holds nothing
    assert list((status_home / "www").iterdir()) == []


def test_publish_list_output(tmp_path, monkeypatch, capsys):
    status_home = tmp_path / "status-home"
    monkeypatch.setenv("WA_STATUS_DOCS_HOME", str(status_home))
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", PUBLIC_BASE)
    src = _src(tmp_path, "src-list", {"index.html": "x"})
    PUB.main(["2026-10-06-anna", str(src)])
    url = capsys.readouterr().out.strip()[len("URL: "):]

    rc = PUB.main(["--list"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == f"2026-10-06-anna\t{url}"
