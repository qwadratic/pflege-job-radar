"""app/wa/envfile.py: the systemd-style EnvironmentFile parser TASK-303 needs because a naive
`set -a; . ./.env; set +a` breaks on this repo's own KNOWN_PHONES_SOURCE_QUERY line (an unquoted SQL
string with spaces). Every test here is offline: temp files only, no real .env touched, nothing
spawned.
"""
import pytest

from app.wa import envfile as ENV


def test_key_value_lines_parse_in_order():
    text = "A=1\nB=two\nC=three words here\n"
    assert ENV.parse_env_text(text) == [("A", "1"), ("B", "two"), ("C", "three words here")]


def test_blank_lines_and_hash_comments_are_skipped():
    text = "\n# a comment\nA=1\n   \n  # indented comment\nB=2\n"
    assert ENV.parse_env_text(text) == [("A", "1"), ("B", "2")]


def test_a_value_with_spaces_and_its_own_equals_sign_is_not_word_split():
    """The exact real-world shape this module exists for: KNOWN_PHONES_SOURCE_QUERY, an unquoted SQL
    string with spaces and its own '=' in a WHERE clause -- a naive shell source breaks on this line
    (tools/wa_bridge_env.sh's own comment documents the breakage)."""
    text = "KNOWN_PHONES_SOURCE_QUERY=select distinct phone from leads where status='active'\n"
    assert ENV.parse_env_text(text) == [
        ("KNOWN_PHONES_SOURCE_QUERY", "select distinct phone from leads where status='active'")]


def test_double_and_single_quotes_are_stripped_one_layer():
    text = 'A="hello world"\nB=\'also spaces here\'\nC=bare\n'
    assert ENV.parse_env_text(text) == [("A", "hello world"), ("B", "also spaces here"), ("C", "bare")]


def test_a_quote_that_does_not_match_front_and_back_is_left_alone():
    text = 'A="unterminated\n'
    assert ENV.parse_env_text(text) == [("A", '"unterminated')]


def test_no_shell_expansion_of_any_kind():
    text = "A=$HOME\nB=`whoami`\nC=~/foo\nD=*.txt\n"
    assert ENV.parse_env_text(text) == [("A", "$HOME"), ("B", "`whoami`"), ("C", "~/foo"),
                                        ("D", "*.txt")]


def test_surrounding_whitespace_outside_quotes_is_trimmed_but_not_inside():
    text = "  A  =  value with edges  \nB=\"  keeps inner spaces  \"\n"
    assert ENV.parse_env_text(text) == [("A", "value with edges"), ("B", "  keeps inner spaces  ")]


def test_a_line_with_no_equals_sign_raises_naming_the_line_number():
    with pytest.raises(ValueError, match="line 2"):
        ENV.parse_env_text("A=1\nnot an assignment\nB=2\n")


def test_an_empty_key_raises():
    with pytest.raises(ValueError):
        ENV.parse_env_text("=novalue\n")


# --- TASK-303 item C (Ivan, 2026-09-25): errors name the file/line/key, never the raw line or a value --

def test_a_missing_equals_error_never_includes_the_raw_line_text():
    """The round-1 review's own reproduction: a malformed line can BE a secret value that continued
    onto its own line (a wrapped Meta access token). The old message embedded the whole raw line."""
    secret = "continued-secret-EAAG-FAKE-TOKEN-xyz"
    with pytest.raises(ValueError) as exc_info:
        ENV.parse_env_text(f"A=1\n  {secret}\nB=2\n", path="/some/file.env")
    message = str(exc_info.value)
    assert secret not in message
    assert "line 2" in message


def test_an_empty_key_error_never_includes_the_raw_line_text():
    secret_value = "super-secret-token-value-abcxyz"
    with pytest.raises(ValueError) as exc_info:
        ENV.parse_env_text(f"={secret_value}\n", path="/some/file.env")
    assert secret_value not in str(exc_info.value)


def test_a_parse_error_names_the_file_path_when_one_is_given():
    with pytest.raises(ValueError, match=r"/some/file\.env: line 1"):
        ENV.parse_env_text("not an assignment\n", path="/some/file.env")


def test_a_parse_error_with_no_path_given_names_no_file():
    """A direct caller with no file (e.g. this test suite) gets no path segment rather than a
    placeholder that could be mistaken for a real one."""
    with pytest.raises(ValueError, match=r"^line 1") as exc_info:
        ENV.parse_env_text("not an assignment\n")
    assert ":" not in str(exc_info.value).split("line 1")[0]


def test_load_env_file_names_the_real_path_in_a_parse_error(tmp_path):
    f = tmp_path / "bad.env"
    f.write_text("GOOD=1\nnot an assignment\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 2") as exc_info:
        ENV.load_env_file(f, {})
    assert str(f) in str(exc_info.value)


def test_apply_env_never_overrides_an_already_set_variable():
    """setdefault semantics: a variable already in the environment (the real shell's own export, or
    an earlier file in the same load) is left exactly as it was."""
    environ = {"A": "existing"}
    newly_set = ENV.apply_env([("A", "new"), ("B", "fresh")], environ)
    assert environ == {"A": "existing", "B": "fresh"}
    assert newly_set == ["B"]


def test_load_env_file_missing_file_sets_nothing_and_does_not_raise(tmp_path):
    environ = {}
    assert ENV.load_env_file(tmp_path / "does-not-exist.env", environ) == []
    assert environ == {}


def test_load_env_file_reads_and_applies_a_real_file(tmp_path):
    f = tmp_path / "x.env"
    f.write_text("FOO=bar\nBAZ=\"qux quux\"\n", encoding="utf-8")
    environ = {}
    assert ENV.load_env_file(f, environ) == ["FOO", "BAZ"]
    assert environ == {"FOO": "bar", "BAZ": "qux quux"}


def test_load_env_file_a_malformed_line_raises_and_therefore_sets_nothing_at_all(tmp_path):
    """A partially-valid file must not partially apply -- the caller (a whole service startup) should
    see the failure, not silently run on half its configuration."""
    f = tmp_path / "bad.env"
    f.write_text("GOOD=1\nnot an assignment\n", encoding="utf-8")
    environ = {}
    with pytest.raises(ValueError):
        ENV.load_env_file(f, environ)
    assert environ == {}


def test_service_env_paths_default_and_env_override(monkeypatch):
    monkeypatch.delenv("WA_ENV_FILE_PATH", raising=False)
    monkeypatch.delenv("WA_RAIL_ENV_FILE_PATH", raising=False)
    assert ENV.service_env_paths() == (ENV.DEFAULT_ENV_FILE, ENV.DEFAULT_RAIL_ENV_FILE)
    monkeypatch.setenv("WA_ENV_FILE_PATH", "/tmp/custom.env")
    monkeypatch.setenv("WA_RAIL_ENV_FILE_PATH", "/tmp/custom-rail.env")
    assert ENV.service_env_paths() == ("/tmp/custom.env", "/tmp/custom-rail.env")


def test_load_service_env_files_loads_both_in_order_and_env_never_overrides_env(tmp_path, monkeypatch):
    """.env loads before rail.env (tools/wa_bridge_env.sh's own order) -- a key in both keeps .env's
    value, proving load order rather than assuming it."""
    env_file = tmp_path / "a.env"
    rail_file = tmp_path / "rail.env"
    env_file.write_text("SHARED=from-env\nONLY_ENV=1\n", encoding="utf-8")
    rail_file.write_text("SHARED=from-rail\nONLY_RAIL=1\n", encoding="utf-8")
    monkeypatch.setenv("WA_ENV_FILE_PATH", str(env_file))
    monkeypatch.setenv("WA_RAIL_ENV_FILE_PATH", str(rail_file))
    environ = {}
    keys = ENV.load_service_env_files(environ)
    assert environ == {"SHARED": "from-env", "ONLY_ENV": "1", "ONLY_RAIL": "1"}
    assert keys == ["SHARED", "ONLY_ENV", "ONLY_RAIL"]


def test_load_service_env_files_missing_files_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_ENV_FILE_PATH", str(tmp_path / "nope.env"))
    monkeypatch.setenv("WA_RAIL_ENV_FILE_PATH", str(tmp_path / "nope-rail.env"))
    environ = {}
    assert ENV.load_service_env_files(environ) == []
    assert environ == {}
