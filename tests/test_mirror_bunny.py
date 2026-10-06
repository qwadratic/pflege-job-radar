"""tools/mirror.py push and pull: the mirror in a private Bunny Storage Zone (TASK-197).

No real Bunny, no real key: a local http.server thread stands in for the zone (PUT/GET /<zone>/<name>, the AccessKey header
decides, a wrong or read-only key on a PUT is a 401 like the real one). Localhost is the one place the network guard allows.
The base URL is BUNNY_MIRROR_BASE_URL, so the fake is the only thing that is ever talked to.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import tracemalloc
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.test_mirror_tool import T

REPO = Path(__file__).resolve().parent.parent
ZONE, RW, RO = "zone-x", "rw-secret-1111", "ro-secret-2222"


# --------------------------------------------------------------------------------------------- the fake zone
class FakeBunny:
    """A Bunny Storage Zone on loopback. Objects live on disk (a 24 MB archive must not sit in this process's memory)."""

    def __init__(self, folder):
        self.dir = folder
        self.dir.mkdir(parents=True)
        self.log = []  # (method, object name, status) in the order requests were answered
        self.put_status = {}  # object name suffix -> status an upload is answered with instead of being stored
        self.cut_get = set()  # object names whose download is cut off half way
        bunny = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _name(self):
                prefix = f"/{ZONE}/"
                return self.path[len(prefix):] if self.path.startswith(prefix) else None

            def _reply(self, status, body=b"", *, cut=False):
                """`body` is bytes or the Path of a stored object (sent in chunks). `cut`: promise all of it, send half, hang up."""
                bunny.log.append((self.command, self._name(), status))
                size = body.stat().st_size if isinstance(body, Path) else len(body)
                self.send_response(status)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                left = size // 2 if cut else size
                if isinstance(body, Path):
                    with open(body, "rb") as f:
                        while left and (chunk := f.read(min(65536, left))):
                            self.wfile.write(chunk)
                            left -= len(chunk)
                else:
                    self.wfile.write(body[:left])
                if cut:
                    self.close_connection = True

            def _json(self, status, message):
                self._reply(status, json.dumps({"HttpCode": status, "Message": message}).encode())

            def do_PUT(self):
                n = int(self.headers.get("Content-Length", -1))
                if n < 0:  # a chunked upload, which the real zone does not take either
                    return self._json(411, "Length required")
                name = self._name()
                part = bunny.dir / (".part-" + threading.current_thread().name)
                with open(part, "wb") as f:
                    left = n
                    while left:
                        chunk = self.rfile.read(min(65536, left))
                        f.write(chunk)
                        left -= len(chunk)
                if self.headers.get("AccessKey") != RW:
                    part.unlink()
                    return self._json(401, "Authorization has been denied for this request.")
                refused = [st for suffix, st in bunny.put_status.items() if name.endswith(suffix)]
                if refused:
                    part.unlink()
                    return self._json(refused[0], "refused by the test")
                os.replace(part, bunny.dir / name)
                self._json(201, "File uploaded.")

            def do_GET(self):
                if self.headers.get("AccessKey") not in (RW, RO):
                    return self._json(401, "Authorization has been denied for this request.")
                name = self._name()
                if name is None or not (bunny.dir / name).is_file():
                    return self._json(404, "Object Not Found")
                self._reply(200, bunny.dir / name, cut=name in bunny.cut_get)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def names(self):
        return sorted(p.name for p in self.dir.iterdir() if not p.name.startswith(".part-"))

    def archive(self):
        return next(n for n in self.names() if n.endswith(".tar"))

    def read(self, name):
        return (self.dir / name).read_bytes()

    def put_local(self, name, data):  # what the zone holds, set by the test directly
        (self.dir / name).write_bytes(data)

    def puts(self):
        return [name for method, name, _ in self.log if method == "PUT"]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def bunny(tmp_path, monkeypatch):
    b = FakeBunny(tmp_path / "bunny")
    monkeypatch.setenv("BUNNY_MIRROR_BASE_URL", b.url)
    monkeypatch.setenv("BUNNY_MIRROR_ZONE", ZONE)
    monkeypatch.setenv("BUNNY_MIRROR_RW_KEY", RW)
    monkeypatch.setenv("BUNNY_MIRROR_RO_KEY", RO)
    yield b
    b.close()


# --------------------------------------------------------------------------------------------- mirrors on disk
def make_mirror(folder, tag="a", size=2000):
    """INDEX.json, three boards, a .prev, an INDEX.lock and an old copy of an infra snapshot (those live in the repo now, push leaves
    them out): what data/mirror holds. Bytes differ by tag."""
    folder.mkdir(parents=True, exist_ok=True)
    boards = ["wp_jobs__a.de", "rexx__b.de", "oracle__c.de"]
    (folder / "INDEX.json").write_text(json.dumps({"format": 1, "tag": tag, "boards": {b: {} for b in boards}}))
    for b in [*boards, "infra__web-fonts"]:
        (folder / f"{b}.sqlite.xz").write_bytes(hashlib.sha256((tag + b).encode()).digest() * (size // 32))
    (folder / "wp_jobs__a.de.sqlite.xz.prev").write_bytes(b"older recording, never uploaded")
    (folder / "INDEX.lock").write_bytes(b"")
    return folder


def shipped(folder):
    """The part of a mirror directory that travels: INDEX.json and every board *.sqlite.xz (no infra__ snapshot), by name."""
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())
            if p.name == "INDEX.json" or (p.name.endswith(".sqlite.xz") and not p.name.startswith("infra__"))}


def listing(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def use_root(monkeypatch, folder):
    monkeypatch.setenv("MIRROR_ROOT", str(folder))
    return folder


def run(fn):
    return fn(Namespace())


def dies(fn, *words):
    with pytest.raises(SystemExit) as e:
        run(fn)
    msg = str(e.value.code)
    assert all(w in msg for w in words), msg
    assert RW not in msg and RO not in msg
    return msg


def tar_bytes(members):
    """members: (name, data); data None = a symlink to /etc/passwd."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def serve_archive(bunny, archive, name="mirror-20261005-120000-abcdef0.tar"):
    """Put a tar into the zone, with a latest.json that describes exactly these bytes. The old archive goes."""
    (bunny.dir / bunny.archive()).unlink()
    bunny.put_local(name, archive)
    bunny.put_local("latest.json", json.dumps({"archive": name, "sha256": hashlib.sha256(archive).hexdigest(), "bytes": len(archive)}).encode())


def push(monkeypatch, tmp_path, tag="a"):
    src = use_root(monkeypatch, make_mirror(tmp_path / f"src-{tag}", tag))
    run(T.cmd_push)
    return src


def pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch):
    """The zone holds a mirror; this machine's mirror is an older one. -> (its directory, everything in it)."""
    push(monkeypatch, tmp_path, "new")
    dst = use_root(monkeypatch, make_mirror(tmp_path / "dst", "old"))
    return dst, listing(dst)


# --------------------------------------------------------------------------------------------- push, then pull
def test_push_then_pull_gives_identical_files(bunny, tmp_path, monkeypatch):
    src = push(monkeypatch, tmp_path)
    assert (src / "wp_jobs__a.de.sqlite.xz.prev").exists()
    dst = use_root(monkeypatch, tmp_path / "ci" / "data" / "mirror")  # does not exist yet, like on a fresh runner
    run(T.cmd_pull)
    assert shipped(dst) == shipped(src) and len(shipped(dst)) == 4
    assert sorted(p.name for p in dst.iterdir()) == sorted(shipped(src))  # no .prev, no lock, no staging directory left behind


def test_the_archive_is_one_plain_tar_of_index_and_boards_named_by_time_and_sha(bunny, tmp_path, monkeypatch, capsys):
    src = push(monkeypatch, tmp_path)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    names = [n for n in bunny.names() if n != "latest.json"]
    assert len(names) == 1 and re.fullmatch(rf"mirror-\d{{8}}-\d{{6}}-{sha}\.tar", names[0]), names
    with tarfile.open(bunny.dir / names[0], "r:") as tar:  # "r:" = no compression at all
        assert [m.name for m in tar.getmembers()] == ["INDEX.json"] + sorted(n for n in shipped(src) if n != "INDEX.json")
        assert all(m.isreg() for m in tar.getmembers())
        assert (src / "infra__web-fonts.sqlite.xz").exists() and not [m.name for m in tar.getmembers() if "infra" in m.name]  # those are in the repo
    latest = json.loads(bunny.read("latest.json"))
    archive = bunny.read(names[0])
    assert latest["archive"] == names[0] and latest["sha256"] == hashlib.sha256(archive).hexdigest() and latest["bytes"] == len(archive)
    assert latest["boards"] == 3 and latest["repo_sha"] == sha
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(\+00:00|Z)", latest["pushed_at"]), latest["pushed_at"]
    assert set(latest) == {"archive", "sha256", "bytes", "boards", "pushed_at", "repo_sha"}
    out = capsys.readouterr().out  # what it did is printed, a key never
    assert names[0] in out and latest["sha256"] in out and str(len(archive)) in out and RW not in out and RO not in out


def test_latest_json_is_uploaded_after_the_archive(bunny, tmp_path, monkeypatch):
    push(monkeypatch, tmp_path)
    puts = bunny.puts()
    assert len(puts) == 2 and puts[0].endswith(".tar") and puts[1] == "latest.json", puts


def test_a_failed_archive_upload_never_moves_the_pointer(bunny, tmp_path, monkeypatch):
    use_root(monkeypatch, make_mirror(tmp_path / "src"))
    old = b'{"archive": "mirror-20260101-000000-0000000.tar"}'
    bunny.put_local("latest.json", old)
    bunny.put_status = {".tar": 500}
    dies(T.cmd_push, "500", f"{bunny.url}/{ZONE}/mirror-")
    assert "latest.json" not in bunny.puts() and bunny.read("latest.json") == old


@pytest.mark.parametrize("key", [RO, "not-the-key"])
def test_push_with_a_read_only_or_wrong_key_is_a_401_and_stores_nothing(bunny, tmp_path, monkeypatch, key):
    use_root(monkeypatch, make_mirror(tmp_path / "src"))
    monkeypatch.setenv("BUNNY_MIRROR_RW_KEY", key)
    dies(T.cmd_push, "401")
    assert bunny.names() == [] and "latest.json" not in bunny.puts()


@pytest.mark.parametrize("fn,gone", [(T.cmd_push, "BUNNY_MIRROR_ZONE"), (T.cmd_push, "BUNNY_MIRROR_RW_KEY"),
                                     (T.cmd_pull, "BUNNY_MIRROR_ZONE"), (T.cmd_pull, "BUNNY_MIRROR_RO_KEY")])
def test_a_missing_environment_variable_is_named(bunny, tmp_path, monkeypatch, fn, gone):
    use_root(monkeypatch, make_mirror(tmp_path / "src"))
    monkeypatch.delenv(gone)
    dies(fn, gone)
    assert bunny.log == []  # it did not even ask the zone


def test_push_needs_no_read_key_and_pull_no_write_key(bunny, tmp_path, monkeypatch):
    monkeypatch.delenv("BUNNY_MIRROR_RO_KEY")
    src = push(monkeypatch, tmp_path)
    monkeypatch.delenv("BUNNY_MIRROR_RW_KEY")
    monkeypatch.setenv("BUNNY_MIRROR_RO_KEY", RO)
    dst = use_root(monkeypatch, tmp_path / "dst")
    run(T.cmd_pull)
    assert shipped(dst) == shipped(src)


def test_push_of_a_directory_without_a_mirror_says_so(bunny, tmp_path, monkeypatch):
    use_root(monkeypatch, tmp_path / "empty")
    dies(T.cmd_push, "nothing to push", str(tmp_path / "empty"))
    assert bunny.log == []


# --------------------------------------------------------------------------------------------- pull
def test_pull_replaces_what_the_archive_holds_and_leaves_the_rest(bunny, tmp_path, monkeypatch):
    src = push(monkeypatch, tmp_path, "new")
    dst = make_mirror(tmp_path / "dst", "old")
    (dst / "local__only.sqlite.xz").write_bytes(b"recorded here, not pushed yet")
    use_root(monkeypatch, dst)
    run(T.cmd_pull)
    now = shipped(dst)
    assert {k: v for k, v in now.items() if k != "local__only.sqlite.xz"} == shipped(src)
    assert now["local__only.sqlite.xz"] == b"recorded here, not pushed yet"
    assert (dst / "wp_jobs__a.de.sqlite.xz.prev").read_bytes() == b"older recording, never uploaded"


def test_pull_with_a_sha256_mismatch_fails_and_keeps_the_old_mirror(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    name = bunny.archive()
    data = bytearray(bunny.read(name))
    data[len(data) // 2] ^= 0xFF  # same size, one byte differs
    bunny.put_local(name, bytes(data))
    dies(T.cmd_pull, "sha256", name)
    assert listing(dst) == before


def test_pull_with_a_size_mismatch_fails_and_keeps_the_old_mirror(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    latest = json.loads(bunny.read("latest.json"))
    bunny.put_local("latest.json", json.dumps(dict(latest, bytes=latest["bytes"] + 1)).encode())
    dies(T.cmd_pull, "bytes", str(latest["bytes"] + 1), str(latest["bytes"]))
    assert listing(dst) == before


@pytest.mark.parametrize("which", ["latest.json", "the archive"])
def test_pull_http_errors_name_the_status_and_the_url_not_the_key(bunny, tmp_path, monkeypatch, which):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    name = bunny.archive() if which == "the archive" else which
    (bunny.dir / name).unlink()
    dies(T.cmd_pull, "404", f"{bunny.url}/{ZONE}/{name}")
    assert listing(dst) == before


def test_pull_with_a_wrong_read_key_is_a_401(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    monkeypatch.setenv("BUNNY_MIRROR_RO_KEY", "not-the-key")
    dies(T.cmd_pull, "401", f"{bunny.url}/{ZONE}/latest.json")
    assert listing(dst) == before


def test_pull_of_a_latest_json_that_names_no_archive_fails(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    bunny.put_local("latest.json", b'{"sha256": "00"}')
    dies(T.cmd_pull, "latest.json", "archive")
    assert listing(dst) == before


@pytest.mark.parametrize("member", ["../evil.sqlite.xz", "sub/../../evil.sqlite.xz", "ABSOLUTE", "sub/nested.sqlite.xz", "notes.txt", "infra__web-fonts.sqlite.xz"])
def test_a_tar_member_with_an_unsafe_or_unexpected_path_is_refused(bunny, tmp_path, monkeypatch, member):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    member = str(tmp_path / "abs-evil.sqlite.xz") if member == "ABSOLUTE" else member
    serve_archive(bunny, tar_bytes([("INDEX.json", b"{}"), ("fine.sqlite.xz", b"x"), (member, b"evil")]))
    dies(T.cmd_pull, member)
    assert listing(dst) == before  # nothing replaced, not even the members that came before the bad one
    assert not (tmp_path / "evil.sqlite.xz").exists() and not (tmp_path / "abs-evil.sqlite.xz").exists()
    assert not (dst.parent / "evil.sqlite.xz").exists()


def test_a_tar_symlink_member_is_refused(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    serve_archive(bunny, tar_bytes([("INDEX.json", b"{}"), ("link.sqlite.xz", None)]))
    dies(T.cmd_pull, "link.sqlite.xz")
    assert listing(dst) == before


def test_a_tar_without_index_json_is_refused(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    serve_archive(bunny, tar_bytes([("only.sqlite.xz", b"x")]))
    dies(T.cmd_pull, "INDEX.json")
    assert listing(dst) == before


# --------------------------------------------------------------------------------------------- a pull that breaks halfway
def test_a_download_cut_off_half_way_leaves_the_old_mirror_whole(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    bunny.cut_get = {bunny.archive()}
    dies(T.cmd_pull, "GET", ".tar")
    assert listing(dst) == before  # the old mirror is whole and no staging directory is left


def test_a_tar_that_is_cut_off_leaves_the_old_mirror_whole(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    whole = tar_bytes([("INDEX.json", b"{}"), ("a.sqlite.xz", os.urandom(30000)), ("b.sqlite.xz", os.urandom(30000))])
    serve_archive(bunny, whole[:50000])  # sha256 and size are those of the cut file: the download is fine, the tar is not
    dies(T.cmd_pull, "tar")
    assert listing(dst) == before


def test_a_disk_error_while_unpacking_leaves_the_old_mirror_whole(bunny, tmp_path, monkeypatch):
    dst, before = pushed_and_an_old_mirror(bunny, tmp_path, monkeypatch)
    real, calls = shutil.copyfileobj, []

    def full_disk(src, out, *a, **k):
        calls.append(1)
        if len(calls) == 3:  # two members are unpacked, the third does not fit
            raise OSError(28, "No space left on device")
        return real(src, out, *a, **k)
    monkeypatch.setattr(T.shutil, "copyfileobj", full_disk)
    with pytest.raises(OSError, match="No space left"):
        run(T.cmd_pull)
    monkeypatch.setattr(T.shutil, "copyfileobj", real)
    assert len(calls) == 3 and listing(dst) == before


# --------------------------------------------------------------------------------------------- big files do not sit in memory
def test_a_big_mirror_is_streamed_from_and_to_disk(bunny, tmp_path, monkeypatch):
    src = make_mirror(tmp_path / "src")
    (src / "big.sqlite.xz").write_bytes(os.urandom(24_000_000))
    use_root(monkeypatch, src)
    dst = tmp_path / "dst"
    tracemalloc.start()
    try:
        run(T.cmd_push)
        use_root(monkeypatch, dst)
        run(T.cmd_pull)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert shipped(dst) == shipped(src)
    assert peak < 8_000_000, f"{peak / 1e6:.1f} MB held at once for a 24 MB board"


# --------------------------------------------------------------------------------------------- the command line itself
def cli(*args, env):
    return subprocess.run([sys.executable, str(REPO / "tools" / "mirror.py"), *args], cwd=REPO, env=env, capture_output=True, text=True)


def test_the_command_line_round_trip_and_its_exit_codes(bunny, tmp_path):
    src = make_mirror(tmp_path / "src")
    env = {**os.environ, "MIRROR_ROOT": str(src)}
    pushed = cli("push", env=env)
    assert pushed.returncode == 0, pushed.stderr
    env["MIRROR_ROOT"] = str(tmp_path / "dst")
    pulled = cli("pull", env=env)
    assert pulled.returncode == 0, pulled.stderr
    assert shipped(tmp_path / "dst") == shipped(src)
    for out in (pushed.stdout + pushed.stderr, pulled.stdout + pulled.stderr):
        assert RW not in out and RO not in out
    bad = cli("pull", env={k: v for k, v in env.items() if k != "BUNNY_MIRROR_RO_KEY"})
    assert bad.returncode != 0 and "BUNNY_MIRROR_RO_KEY" in bad.stderr and RW not in bad.stderr
