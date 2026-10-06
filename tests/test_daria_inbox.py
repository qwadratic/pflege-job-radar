"""tools/daria_inbox.py: the token cache is read under a lock and saved back when a read refreshed it (Ivan, 2026-10-06)."""
import importlib.util
import os
import stat
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "daria_inbox.py"


class FakeCache:
    def __init__(self):
        self.text, self.has_state_changed = None, False

    def deserialize(self, text):
        self.text = text

    def serialize(self):
        return self.text


class FakeApp:
    """acquire_token_silent refreshes the "old" cache into "new" (a changed state), fails on "stale", else reuses the token."""

    def __init__(self, client_id, authority, token_cache):
        self.cache = token_cache

    def get_accounts(self):
        return [{"username": "Daria.S@pflege-connect.work"}]

    def acquire_token_silent(self, scopes, account):
        if self.cache.text == "stale":
            return {"error": "invalid_grant", "error_description": "the refresh token expired"}
        if self.cache.text == "old":
            self.cache.text, self.cache.has_state_changed = "new", True
        return {"access_token": "tok"}


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(SerializableTokenCache=FakeCache, PublicClientApplication=FakeApp))
    spec = importlib.util.spec_from_file_location("daria_inbox_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cache = tmp_path / "cache.json"
    cache.write_text("old")
    cache.chmod(0o600)
    env = tmp_path / ".env"
    env.write_text(f"MICROSOFT_GRAPH_AUTHORITY=https://login.microsoftonline.com/common\nMICROSOFT_GRAPH_CLIENT_ID=cid\n"
                   f"MICROSOFT_GRAPH_SCOPES=openid Mail.Read offline_access\nMICROSOFT_GRAPH_MSAL_CACHE={cache}\n")
    monkeypatch.setattr(mod, "ENV", str(env))
    mod.cache_path = cache
    return mod


def test_a_refreshed_token_is_saved_back_atomically_with_mode_600(inbox):
    before = inbox.cache_path.stat()
    assert inbox.token() == "tok"
    after = inbox.cache_path.stat()
    assert inbox.cache_path.read_text() == "new" and stat.S_IMODE(after.st_mode) == 0o600 and after.st_uid == before.st_uid
    assert after.st_ino != before.st_ino                                      # a new file renamed over it, never half written
    assert sorted(p.name for p in inbox.cache_path.parent.iterdir()) == [".env", "cache.json", "cache.json.lock"]
    assert inbox.token() == "tok" and inbox.cache_path.read_text() == "new"     # the next read starts from the saved state


def test_a_cache_the_read_did_not_change_is_left_alone(inbox):
    inbox.cache_path.write_text("fresh")
    ino = inbox.cache_path.stat().st_ino
    assert inbox.token() == "tok" and inbox.cache_path.read_text() == "fresh" and inbox.cache_path.stat().st_ino == ino


def test_a_stale_cache_fails_loudly_and_is_not_touched(inbox):
    inbox.cache_path.write_text("stale")
    with pytest.raises(SystemExit, match="no Graph token for daria.s@pflege-connect.work: invalid_grant the refresh token expired"):
        inbox.token()
    assert inbox.cache_path.read_text() == "stale"


def test_a_cache_the_group_can_write_or_a_symlink_is_refused(inbox, tmp_path):
    inbox.cache_path.chmod(0o664)
    with pytest.raises(SystemExit, match="must be a regular file that the running user owns and only the owner can write"):
        inbox.token()
    inbox.cache_path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(inbox.cache_path)
    env = Path(inbox.ENV)
    env.write_text(env.read_text().replace(f"MSAL_CACHE={inbox.cache_path}", f"MSAL_CACHE={link}"))
    with pytest.raises(SystemExit, match="cannot open the MSAL cache named in .env"):
        inbox.token()
    assert inbox.cache_path.read_text() == "old"


def test_the_authority_must_be_microsoft(inbox):
    env = Path(inbox.ENV)
    env.write_text(env.read_text().replace("https://login.microsoftonline.com/common", "https://evil.example/common"))
    with pytest.raises(SystemExit, match="must start with https://login.microsoftonline.com/"):
        inbox.token()
