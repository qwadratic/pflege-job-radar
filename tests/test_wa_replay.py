"""Offline tests for app/wa/luna/replay.py -- a tiny synthetic sales_brain sqlite in tmp_path, a
fake LB.turn (never the real `claude` CLI, never -m llm), the real scratch store underneath.

Every candidate_id and timestamp below is invented -- none of them is a real sales_brain candidate or
moment; ids live in the 9000s, dates in 2030.
"""
import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import time

import pytest

import app.config as APPC
from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import escalation as ESC
from app.wa.luna import replay as RP
from tests.conftest import _LIVE_CREDENTIALS as CONFTEST_LIVE_CREDENTIALS

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Loaded as a module, never as __main__: its env setup sits behind ``if __name__ == "__main__"``, so
# loading it reads no .env and changes no variable.
_SPEC = importlib.util.spec_from_file_location("wa_replay_cli", ROOT / "tools" / "wa_replay.py")
CLI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CLI)

_COLUMNS = ("id", "candidate_id", "phone_e164", "wamid", "direction", "message_type",
            "body", "caption", "media_filename", "occurred_at")


def _make_sales_brain(path, rows):
    """``rows``: dicts over a subset of _COLUMNS (id/candidate_id/direction/message_type/occurred_at
    required; the rest default to None/''). -> the path, for convenience."""
    conn = sqlite3.connect(path)
    conn.execute(f"create table candidate_whatsapp_messages ({', '.join(_COLUMNS)})")
    for r in rows:
        conn.execute(
            f"insert into candidate_whatsapp_messages ({', '.join(_COLUMNS)}) "
            f"values ({', '.join('?' for _ in _COLUMNS)})",
            tuple(r.get(c) for c in _COLUMNS))
    conn.commit()
    conn.close()
    return str(path)


def _row(id, candidate_id, direction, message_type, occurred_at, body=None, caption=None,
         media_filename=None, phone_e164="+491715550000", wamid=None):
    return {"id": id, "candidate_id": candidate_id, "phone_e164": phone_e164,
            "wamid": wamid or f"wamid.src.{id}", "direction": direction, "message_type": message_type,
            "body": body, "caption": caption, "media_filename": media_filename, "occurred_at": occurred_at}


FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def _fake_main_checkout(tmp_path_factory, monkeypatch):
    """The commit gate runs this file in a ``git checkout-index`` copy with no .git, so nothing here may
    need git in the repo root: the CLI's main-checkout lookup is pointed at a scratch directory."""
    root = tmp_path_factory.mktemp("main-checkout")
    monkeypatch.setattr(CLI, "_main_checkout_root", lambda this_root=None: root)
    return root


@pytest.fixture()
def scratch_dir(tmp_path, monkeypatch):
    """-> a fresh ``out_dir`` for ``RP.replay_candidate``. It points ``config.SQLITE_PATH`` and
    ``config.LUNA_SESSION_DIR`` (and the CLI ``app.config.SQLITE_PATH``) at paths under it for the rest
    of the process, so this fixture first registers an undo to each CURRENT value:
    ``monkeypatch.setattr`` restores it however the test reassigns the attribute afterwards."""
    monkeypatch.setattr(C, "SQLITE_PATH", C.SQLITE_PATH)
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", C.LUNA_SESSION_DIR)
    monkeypatch.setattr(APPC, "SQLITE_PATH", APPC.SQLITE_PATH)   # the CLI points it at the board copy
    monkeypatch.setattr(RP, "_git_sha", lambda repo_dir=None: FAKE_SHA)   # no .git in the gate's copy
    return tmp_path / "out"


def _fake_turn(bubbles=None, action="reply_now_conversational", stopped=False, card_patch=None,
              seen_through_id="__from_context__"):
    """-> a function with turn()'s own signature, recording every call it saw on ``.calls``.

    Also stamps a fake ``_session_id`` onto its own returned card (``f"fake-session-{len(calls)}"``,
    a fresh one every call, unless ``card_patch`` already set one) the way a real CLI turn would --
    so a test can prove CUT AT TURN N: ``_run_turn`` must pop the PRIOR
    call's own stamped id off the card before the NEXT call ever sees it, so ``.calls[n]
    ["session_id"]`` is always ``None`` regardless of how many turns ran before it."""
    calls = []

    def fn(text, thread, button_id=None, client=None, no_send=False):
        ctx = thread.get("turn_context") or {}
        calls.append({"text": text, "button_id": button_id, "client": client, "no_send": no_send,
                      "thread_phone": thread.get("phone"),
                      "session_id": (thread.get("slots") or {}).get("_session_id"),
                      "outbound_since_last_turn": [m["text"] for m in ctx.get("outbound_since_last_turn", [])]})
        card = dict(thread.get("slots") or {})
        card.update(card_patch or {})
        card.setdefault("_session_id", f"fake-session-{len(calls)}")
        out = {"bubbles": list(bubbles) if bubbles is not None else [f"reply #{len(calls)}"],
               "buttons": [], "slots": card, "asked": thread.get("asked") or [], "stopped": stopped,
               "matches": [], "action": action}
        if ctx.get("seen_through_id") is not None:
            out["luna_turn"] = {"at": "2030-01-01T00:00:00+00:00",
                                "seen_through_id": ctx["seen_through_id"], "model_bubbles": out["bubbles"]}
        return out
    fn.calls = calls
    return fn


def _prime_board(monkeypatch):
    """A minimal, non-stale, non-empty ``app.data._snap`` -- just enough live-verified board data for
    ``board_vocabulary()`` (``luna_brain._mcp_config_path`` -> ``_board_vocabulary_path`` ->
    ``board_vocabulary.vocabulary_lines``) to run without ``D.snapshot()`` reaching the network guard
    tests/conftest.py installs around a real Supabase call -- same priming idiom
    tests/test_wa_luna_tools.py's own ``board()`` helper uses, trimmed to one row of each table."""
    job = {"posting_id": 1, "title": "Pflegefachkraft", "role_class": "pflegefachkraft",
           "department_hint": ["Intensiv/IMC"], "city": "München", "clinic_town": "München",
           "regierungsbezirk": "Oberbayern", "clinic_id": "c1", "clinic_name": "Klinikum München",
           "employer": "Klinikum München", "employment_types": ["vollzeit"], "enr_housing": False,
           "enr_housing_evidence": None, "enr_childcare": None, "verify_status": "live",
           "status": "open", "first_published": "2030-01-01", "fresh": True,
           "external_url": "https://example.org/job/1"}
    clinic = {"clinic_id": "c1", "name": "Klinikum München", "town": "München",
              "regierungsbezirk": "Oberbayern", "beds": 800, "jobs_open": 1, "jobs_fresh": 1,
              "jobs_live": 1}
    D._snap.update({"at": time.time(), "jobs": [job], "clinics": [clinic],
                   "by_clinic": {"c1": clinic}, "facets": {}, "taxonomy": {}, "loading": False,
                   "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)


# --- synthetic_phone -----------------------------------------------------------------------------

def test_synthetic_phone_never_equals_a_real_looking_source_phone():
    real = "+491715550099"
    synth = RP.synthetic_phone(9001)
    assert synth != real
    assert synth.startswith("+49000")
    assert RP.synthetic_phone(9001) == RP.synthetic_phone(9001)   # deterministic
    assert RP.synthetic_phone(9001) != RP.synthetic_phone(9002)   # distinct per candidate


# --- display text / placeholders, one per message_type -------------------------------------------

def test_display_text_uses_real_body_when_present():
    row = {"message_type": "text", "body": "Ja, gerne", "caption": None, "media_filename": None}
    text, placeholder = RP._display_text(row)
    assert text == "Ja, gerne"
    assert placeholder is False


@pytest.mark.parametrize("message_type", ["image", "audio", "template", "unsupported", "reaction"])
def test_display_text_placeholder_for_empty_body(message_type):
    row = {"message_type": message_type, "body": "", "caption": "nice one", "media_filename": "abc123.jpg"}
    text, placeholder = RP._display_text(row)
    assert placeholder is True
    assert text == f"[{message_type}] abc123.jpg nice one"


def test_display_text_document_placeholder_keeps_only_the_extension_never_the_filename():
    """PII: a document's real stored filename never reaches the report,
    not even the opaque generated ones this export actually has -- only the extension survives."""
    row = {"message_type": "document", "body": "", "caption": "mein lebenslauf",
           "media_filename": "Lebenslauf_Maria_Mustermann.pdf"}
    text, placeholder = RP._display_text(row)
    assert placeholder is True
    assert text == "[document].pdf mein lebenslauf"
    assert "Lebenslauf_Maria_Mustermann" not in text


def test_display_text_document_with_no_filename_or_caption_at_all():
    row = {"message_type": "document", "body": "", "caption": None, "media_filename": None}
    text, placeholder = RP._display_text(row)
    assert placeholder is True
    assert text == "[document]"


def test_display_text_audio_transcript_is_not_a_placeholder():
    row = {"message_type": "audio", "body": "Ich habe eine Frage zur Wohnung.", "caption": None,
           "media_filename": "note.ogg"}
    text, placeholder = RP._display_text(row)
    assert text == "Ich habe eine Frage zur Wohnung."
    assert placeholder is False


def test_display_text_button_tap_with_no_stored_text_falls_back_to_placeholder():
    row = {"message_type": "button", "body": "", "caption": None, "media_filename": None}
    text, placeholder = RP._display_text(row)
    assert text == "[button]"
    assert placeholder is True


def test_display_text_interactive_reply_uses_its_own_text():
    row = {"message_type": "interactive", "body": "menu_reply:option_a", "caption": None,
           "media_filename": None}
    text, placeholder = RP._display_text(row)
    assert text == "menu_reply:option_a"
    assert placeholder is False


# --- _pending_turn_work ------------------------------------------------------------------------------

def test_pending_turn_work_true_only_for_a_real_inbound_row_outside_no_turn_types():
    assert RP._pending_turn_work([]) is False
    assert RP._pending_turn_work([{"direction": "outbound", "message_type": "text"}]) is False
    assert RP._pending_turn_work([{"direction": "inbound", "message_type": "reaction"}]) is False
    assert RP._pending_turn_work([{"direction": "outbound", "message_type": "unsupported"}]) is False
    assert RP._pending_turn_work([{"direction": "inbound", "message_type": "text"}]) is True
    # a real row anywhere in the tail is enough, even behind rows that would not drive a turn
    assert RP._pending_turn_work([{"direction": "inbound", "message_type": "reaction"},
                                  {"direction": "inbound", "message_type": "text"}]) is True


# --- ENV: tools/wa_replay.py bootstrap ---------------------------------------------------------------

def test_cli_scrubs_exactly_the_credentials_conftest_scrubs_and_sets_no_send():
    assert CLI._LIVE_CREDENTIALS == CONFTEST_LIVE_CREDENTIALS
    environ = {name: "x" for name in CONFTEST_LIVE_CREDENTIALS}
    environ["WA_LUNA_MODEL"] = "keep-me"
    CLI.scrub_live_credentials(environ)
    assert set(environ) == {"WA_LUNA_MODEL", "WA_LUNA_NO_SEND"}
    assert environ["WA_LUNA_NO_SEND"] == "1"


def test_cli_scrubs_before_app_wa_config_is_imported():
    """The bootstrap call sits behind the __main__ guard ABOVE the first app.config / app.wa.config
    import, so it runs before config.py freezes the WA_* variables into constants."""
    src = (ROOT / "tools" / "wa_replay.py").read_text(encoding="utf-8")
    guard = src.index('if __name__ == "__main__":\n    bootstrap_env(')
    assert guard < src.index("\nimport app.config")
    assert guard < src.index("\nfrom app.wa import config")


def _brain_env_file(tmp_path, **pairs):
    path = tmp_path / "brain-settings.txt"   # a synthetic file; no real .env is ever read by a test
    path.write_text("".join(f"{k}={v}\n" for k, v in pairs.items()), encoding="utf-8")
    return path


def test_cli_loads_only_the_three_brain_keys_and_overrides_the_shell(tmp_path):
    env_file = _brain_env_file(tmp_path, WA_LUNA_MODEL="model-x", WA_LUNA_EFFORT="high",
                               SUPABASE_ANON_KEY="anon-x", UNRELATED_SECRET="never-loaded")
    environ = {"WA_LUNA_MODEL": "stale-shell-value"}
    CLI.load_prod_brain_env(env_file, environ)
    assert environ == {"WA_LUNA_MODEL": "model-x", "WA_LUNA_EFFORT": "high", "SUPABASE_ANON_KEY": "anon-x"}


@pytest.mark.parametrize("missing", ["WA_LUNA_MODEL", "WA_LUNA_EFFORT", "SUPABASE_ANON_KEY"])
def test_cli_missing_brain_key_is_a_loud_error_that_names_the_key_not_a_value(tmp_path, missing):
    pairs = {"WA_LUNA_MODEL": "model-x", "WA_LUNA_EFFORT": "high", "SUPABASE_ANON_KEY": "anon-secret"}
    del pairs[missing]
    with pytest.raises(RuntimeError) as exc:
        CLI.load_prod_brain_env(_brain_env_file(tmp_path, **pairs), {})
    assert missing in str(exc.value)
    assert "anon-secret" not in str(exc.value) and "model-x" not in str(exc.value)


def test_cli_empty_brain_key_is_a_loud_error_too(tmp_path):
    env_file = _brain_env_file(tmp_path, WA_LUNA_MODEL="", WA_LUNA_EFFORT="high", SUPABASE_ANON_KEY="k")
    with pytest.raises(RuntimeError, match="WA_LUNA_MODEL"):
        CLI.load_prod_brain_env(env_file, {})


def test_cli_bootstrap_reads_the_env_file_only_for_a_candidate_run(tmp_path):
    missing_file = tmp_path / "does-not-exist.txt"
    environ = {"WA_TRANSPORT": "bridge"}
    CLI.bootstrap_env(["--list"], environ=environ, env_path=missing_file)   # --list never opens it
    assert environ == {"WA_LUNA_NO_SEND": "1"}
    with pytest.raises(FileNotFoundError):
        CLI.bootstrap_env(["--candidate", "9001", "--out", str(tmp_path / "out")], environ={},
                          env_path=missing_file)


def test_cli_bad_invocation_fails_before_any_env_file_is_read(tmp_path):
    never_read = tmp_path / "does-not-exist.txt"   # reading it would raise FileNotFoundError instead
    with pytest.raises(SystemExit, match="/dev/shm"):
        CLI.bootstrap_env(["--candidate", "9001", "--out", str(ROOT / "scratch-replay-out")],
                          environ={}, env_path=never_read)
    with pytest.raises(SystemExit) as exc:
        CLI.bootstrap_env(["--candidate", "9001"], environ={}, env_path=never_read)
    assert exc.value.code == 2


# --- ISOLATION: --out is refused inside a checkout ---------------------------------------------------------

def test_cli_refuses_an_out_dir_inside_this_checkout_and_names_dev_shm():
    with pytest.raises(SystemExit) as exc:
        CLI.check_out_dir(ROOT / "scratch-replay-out")
    assert "/dev/shm" in str(exc.value) and str(ROOT) in str(exc.value)


def test_cli_refuses_an_out_dir_inside_the_main_checkout(_fake_main_checkout):
    with pytest.raises(SystemExit, match="/dev/shm"):
        CLI.check_out_dir(_fake_main_checkout / "data" / "replay-out")


def test_cli_refuses_an_out_dir_that_only_resolves_inside_the_checkout(tmp_path):
    link = tmp_path / "link-into-repo"
    link.symlink_to(ROOT)
    with pytest.raises(SystemExit, match="/dev/shm"):
        CLI.check_out_dir(link / "sub")


def test_cli_accepts_an_out_dir_outside_every_checkout(tmp_path):
    CLI.check_out_dir(tmp_path / "out")


# --- BOARD: read-only backup copy ----------------------------------------------------------------------------

def test_board_copy_is_a_backup_from_a_read_only_source(tmp_path, monkeypatch):
    src = tmp_path / "board-src.sqlite"
    conn = sqlite3.connect(src)
    conn.execute("create table clinic_blurbs (clinic_id text, blurb text)")
    conn.execute("insert into clinic_blurbs values ('c1', 'hello')")
    conn.commit()
    conn.close()
    before = src.read_bytes()
    opened = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(CLI.sqlite3, "connect", lambda *a, **k: opened.append((a, k)) or real_connect(*a, **k))

    out = tmp_path / "out"
    out.mkdir()
    dest = CLI.copy_board_db(src, out)

    assert dest == out / "board.sqlite"
    copy = sqlite3.connect(dest)
    assert copy.execute("select clinic_id, blurb from clinic_blurbs").fetchall() == [("c1", "hello")]
    copy.close()
    assert src.read_bytes() == before                       # the source is untouched
    source_uri = opened[0][0][0]
    assert source_uri.startswith("file:") and "mode=ro" in source_uri and opened[0][1]["uri"] is True


def test_board_copy_of_a_missing_source_is_a_loud_error(tmp_path):
    with pytest.raises(RuntimeError, match="does not exist"):
        CLI.copy_board_db(tmp_path / "nope.sqlite", tmp_path)


# --- LOUD: the CLI's exit code and report line -----------------------------------------------------------------

def _cli_run(tmp_path, scratch_dir, rows, capsys, *extra):
    db = _make_sales_brain(tmp_path / "sb.sqlite", rows)
    board = tmp_path / "board-src.sqlite"
    sqlite3.connect(board).close()
    code = CLI.main(["--candidate", str(rows[0]["candidate_id"]), "--out", str(scratch_dir),
                     "--sales-brain-path", db, "--board-db", str(board), *extra])
    return code, capsys.readouterr().out


def test_cli_clean_run_exits_zero_and_points_the_board_snapshot_at_the_copy(tmp_path, scratch_dir, monkeypatch, capsys):
    monkeypatch.setattr(LB, "turn", _fake_turn())
    code, out = _cli_run(tmp_path, scratch_dir, [
        _row(1, 9020, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9020, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Hi")], capsys)
    assert code == 0
    assert "0 of 1 turns errored" in out
    assert APPC.SQLITE_PATH == scratch_dir.resolve() / "board.sqlite"   # never the live registry
    # the copy holds more than the brain reads (sessions, customers): gone when the run ends
    assert not list(scratch_dir.resolve().glob("board.sqlite*"))


def test_cli_exits_nonzero_and_says_k_of_n_when_a_turn_errored(tmp_path, scratch_dir, monkeypatch, capsys):
    good = _fake_turn()
    calls = {"n": 0}

    def flaky(text, thread, button_id=None, client=None, no_send=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return good(text, thread, button_id=button_id, client=client, no_send=no_send)
    monkeypatch.setattr(LB, "turn", flaky)
    code, out = _cli_run(tmp_path, scratch_dir, [
        _row(1, 9021, "inbound", "text", "2030-08-01T10:00:00+00:00", body="eins"),
        _row(2, 9021, "outbound", "text", "2030-08-01T10:00:05+00:00", body="ok 1"),
        _row(3, 9021, "inbound", "text", "2030-08-01T10:00:10+00:00", body="zwei"),
        _row(4, 9021, "outbound", "text", "2030-08-01T10:00:15+00:00", body="ok 2"),
        _row(5, 9021, "inbound", "text", "2030-08-01T10:00:20+00:00", body="drei"),
        _row(6, 9021, "outbound", "text", "2030-08-01T10:00:25+00:00", body="ok 3")], capsys)
    assert code == 1
    assert "1 of 3 turns errored" in out                      # the run went on past the error to turn 3
    assert calls["n"] == 3


def test_cli_refuses_an_out_dir_inside_the_checkout_before_doing_anything(tmp_path, capsys):
    with pytest.raises(SystemExit, match="/dev/shm"):
        CLI.main(["--candidate", "9022", "--out", str(ROOT / "scratch-replay-out"),
                  "--sales-brain-path", str(tmp_path / "unused.sqlite")])
    assert not (ROOT / "scratch-replay-out").exists()


# --- list_candidates -------------------------------------------------------------------------------

def test_list_candidates_counts_and_dates(tmp_path):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9001, "inbound", "text", "2030-07-01T00:00:00+00:00"),
        _row(2, 9001, "outbound", "text", "2030-07-01T00:00:05+00:00", body="Hallo!"),
        _row(3, 9001, "inbound", "text", "2030-07-02T00:00:00+00:00"),
        _row(4, None, "inbound", "text", "2030-07-03T00:00:00+00:00"),   # unattached, left out
    ])
    rows = RP.list_candidates(db)
    assert rows == [{"candidate_id": 9001, "n_in": 2, "n_out": 1,
                     "first_at": "2030-07-01T00:00:00+00:00", "last_at": "2030-07-02T00:00:00+00:00"}]


# --- replay_candidate: burst handling, actual_reply, session continuity --------------------------

def test_single_message_turn_then_actual_reply_inserted_between_turns(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9002, "inbound", "text", "2030-08-12T09:00:00+00:00", body="Hallo, ich habe Interesse."),
        _row(2, 9002, "outbound", "text", "2030-08-12T09:00:05+00:00", body="Schön, dass Sie da sind!"),
        _row(3, 9002, "inbound", "text", "2030-08-12T09:03:00+00:00", body="Welche Stellen gibt es?"),
    ])
    fake = _fake_turn()
    monkeypatch.setattr(LB, "turn", fake)

    result = RP.replay_candidate(9002, scratch_dir, sales_brain_path=db)
    assert result == {"candidate_id": 9002, "turns_run": 2, "truncated": False, "errors": 0,
                      "jsonl_path": str(scratch_dir.resolve() / "9002.jsonl"),
                      "sqlite_path": str(scratch_dir.resolve() / "9002.sqlite")}

    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert len(lines) == 2
    assert lines[0]["turn"] == 1
    assert lines[0]["turn_kind"] == "single"
    assert lines[0]["inbound"] == [{"text": "Hallo, ich habe Interesse.", "kind": "text",
                                    "at": "2030-08-12T09:00:00+00:00"}]
    assert lines[0]["luna"]["bubbles"] == ["reply #1"]
    assert lines[0]["actual_reply"] == [{"body": "Schön, dass Sie da sind!", "kind": "text",
                                         "at": "2030-08-12T09:00:05+00:00"}]
    assert lines[0]["actual_reply_delay_s"] == 5.0

    assert lines[1]["turn"] == 2
    assert lines[1]["inbound"][0]["text"] == "Welche Stellen gibt es?"
    assert lines[1]["actual_reply"] == []           # end of recorded history, nothing to compare
    assert lines[1]["actual_reply_delay_s"] is None

    # both calls got the no_send guarantee and the synthetic phone, never the real one
    for call in fake.calls:
        assert call["no_send"] is True
        assert call["thread_phone"] == RP.synthetic_phone(9002)
        assert call["thread_phone"] != "+491715550000"

    # AC#3 instrumentation: model/effort/git_sha on every line, timings/tokens null with a note
    # (luna_brain.turn() exposes neither)
    for line in lines:
        assert line["model"] == C.LUNA_MODEL and line["effort"] == C.LUNA_EFFORT
        assert line["git_sha"] == FAKE_SHA
        assert line["timings"] is None and line["tokens"] is None
        assert line["instrumentation_note"]


def test_inbound_burst_joins_into_one_turn_before_the_next_outbound(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9003, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9003, "inbound", "text", "2030-08-01T10:00:02+00:00", body="Ich suche eine Stelle in München"),
        _row(3, 9003, "outbound", "text", "2030-08-01T10:00:30+00:00", body="Klar, hier sind Optionen"),
    ])
    fake = _fake_turn()
    monkeypatch.setattr(LB, "turn", fake)

    RP.replay_candidate(9003, scratch_dir, sales_brain_path=db)

    assert len(fake.calls) == 1                     # one turn for the whole burst, not one per message
    assert fake.calls[0]["text"] == "Hallo\nIch suche eine Stelle in München"

    lines = [json.loads(l) for l in open(scratch_dir / "9003.jsonl", encoding="utf-8")]
    assert len(lines) == 1
    assert lines[0]["turn_kind"] == "burst"
    assert [m["text"] for m in lines[0]["inbound"]] == ["Hallo", "Ich suche eine Stelle in München"]


def test_lunas_bubbles_are_never_recorded_as_outbound(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9004, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9004, "outbound", "text", "2030-08-01T10:00:10+00:00", body="Die echte Antwort"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn(bubbles=["Luna's own made-up reply, never sent"]))

    result = RP.replay_candidate(9004, scratch_dir, sales_brain_path=db)

    conn = sqlite3.connect(result["sqlite_path"])
    conn.row_factory = sqlite3.Row
    bodies = [r["body"] for r in conn.execute("select body from wa_messages order by id")]
    conn.close()
    assert "Luna's own made-up reply, never sent" not in bodies
    assert "Die echte Antwort" in bodies             # the real historical reply is recorded as given


def test_media_placeholder_flag_set_for_document_image_audio_without_body(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9005, "inbound", "document", "2030-08-01T10:00:00+00:00", caption="", media_filename="cv.pdf"),
        _row(2, 9005, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Danke fürs CV"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9005, scratch_dir, sales_brain_path=db)
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert lines[0]["media_placeholder"] is True


def test_reaction_and_unsupported_rows_are_recorded_but_never_trigger_a_turn(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9006, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9006, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Willkommen"),
        _row(3, 9006, "inbound", "reaction", "2030-08-01T10:00:06+00:00"),
        _row(4, 9006, "outbound", "unsupported", "2030-08-01T10:00:07+00:00"),
        _row(5, 9006, "inbound", "text", "2030-08-01T10:00:08+00:00", body="Danke"),
    ])
    fake = _fake_turn()
    monkeypatch.setattr(LB, "turn", fake)
    result = RP.replay_candidate(9006, scratch_dir, sales_brain_path=db)

    assert len(fake.calls) == 2                      # the reaction/unsupported rows did not spawn turns
    conn = sqlite3.connect(result["sqlite_path"])
    conn.row_factory = sqlite3.Row
    n = conn.execute("select count(*) as n from wa_messages").fetchone()["n"]
    conn.close()
    assert n == 5                                    # but every row, including those two, was recorded


def test_reaction_inside_actual_reply_does_not_split_it_and_turn_kind_is_burst(tmp_path, scratch_dir, monkeypatch):
    """ACTUAL REPLY: a reaction sitting BETWEEN two outbound bubbles of the SAME real
    reply is recorded but does not end the actual_reply collection or spawn a bogus turn of its own;
    turn_kind says "burst" for the >1-inbound-message turn and "single" for the next one."""
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9007, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9007, "inbound", "text", "2030-08-01T10:00:02+00:00", body="Ich suche eine Stelle"),
        _row(3, 9007, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Bubble eins"),
        _row(4, 9007, "inbound", "reaction", "2030-08-01T10:00:06+00:00"),
        _row(5, 9007, "outbound", "text", "2030-08-01T10:00:07+00:00", body="Bubble zwei"),
        _row(6, 9007, "inbound", "text", "2030-08-01T10:00:10+00:00", body="Danke"),
    ])
    fake = _fake_turn()
    monkeypatch.setattr(LB, "turn", fake)
    result = RP.replay_candidate(9007, scratch_dir, sales_brain_path=db)

    assert len(fake.calls) == 2                      # the reaction did not spawn a third turn
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert lines[0]["turn_kind"] == "burst"
    assert lines[0]["actual_reply"] == [
        {"body": "Bubble eins", "kind": "text", "at": "2030-08-01T10:00:05+00:00"},
        {"body": "Bubble zwei", "kind": "text", "at": "2030-08-01T10:00:07+00:00"},
    ]
    assert lines[1]["turn_kind"] == "single"

    conn = sqlite3.connect(result["sqlite_path"])
    conn.row_factory = sqlite3.Row
    n = conn.execute("select count(*) as n from wa_messages").fetchone()["n"]
    conn.close()
    assert n == 6                                    # the reaction is still recorded, just skipped


def test_brain_exception_is_recorded_loudly_and_replay_continues(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9008, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9008, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Hi"),
        _row(3, 9008, "inbound", "text", "2030-08-01T10:00:10+00:00", body="Wo ist die Klinik?"),
        _row(4, 9008, "outbound", "text", "2030-08-01T10:00:20+00:00", body="In München"),
    ])
    good = _fake_turn()
    calls = {"n": 0}

    def flaky(text, thread, button_id=None, client=None, no_send=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return good(text, thread, button_id=button_id, client=client, no_send=no_send)
    monkeypatch.setattr(LB, "turn", flaky)

    result = RP.replay_candidate(9008, scratch_dir, sales_brain_path=db)
    assert result["turns_run"] == 2
    # LOUD: the run continues past the error (no invented stop rule) but
    # the caller can tell, without opening the JSONL, that something broke.
    assert result["errors"] == 1
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert "error" in lines[0] and "boom" in lines[0]["error"]
    assert "luna" not in lines[0]
    # the real historical reply after the failed turn is still recorded, loudly or not
    assert lines[0]["actual_reply"] == [{"body": "Hi", "kind": "text", "at": "2030-08-01T10:00:05+00:00"}]
    # the second turn succeeds normally, unaffected by the first one's failure
    assert "error" not in lines[1]
    assert lines[1]["luna"]["bubbles"]


def test_max_turns_truncates_with_a_final_truncated_line(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9009, "inbound", "text", "2030-08-01T10:00:00+00:00", body="eins"),
        _row(2, 9009, "outbound", "text", "2030-08-01T10:00:05+00:00", body="ok 1"),
        _row(3, 9009, "inbound", "text", "2030-08-01T10:00:10+00:00", body="zwei"),
        _row(4, 9009, "outbound", "text", "2030-08-01T10:00:15+00:00", body="ok 2"),
        _row(5, 9009, "inbound", "text", "2030-08-01T10:00:20+00:00", body="drei"),
        _row(6, 9009, "outbound", "text", "2030-08-01T10:00:25+00:00", body="ok 3"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9009, scratch_dir, max_turns=2, sales_brain_path=db)
    assert result["truncated"] is True
    assert result["turns_run"] == 2
    assert result["errors"] == 0
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert len(lines) == 3
    assert lines[-1] == {"candidate_id": 9009, "truncated": True, "turns_run": 2}


def test_no_truncated_line_when_history_ends_exactly_at_max_turns(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9010, "inbound", "text", "2030-08-12T09:00:00+00:00", body="eins"),
        _row(2, 9010, "outbound", "text", "2030-08-12T09:00:05+00:00", body="ok"),
        _row(3, 9010, "outbound", "image", "2030-08-12T09:00:08+00:00"),
        _row(4, 9010, "inbound", "text", "2030-08-12T09:03:00+00:00", body="zwei"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9010, scratch_dir, max_turns=2, sales_brain_path=db)
    assert result["truncated"] is False
    assert result["turns_run"] == 2
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert len(lines) == 2
    assert all("truncated" not in l for l in lines)
    assert lines[0]["actual_reply"] == [
        {"body": "ok", "kind": "text", "at": "2030-08-12T09:00:05+00:00"},
        {"body": "[image]", "kind": "image", "at": "2030-08-12T09:00:08+00:00"},
    ]


def test_stopped_turn_skips_the_brain_on_every_later_burst(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9011, "inbound", "text", "2030-08-01T10:00:00+00:00", body="STOP"),
        _row(2, 9011, "outbound", "text", "2030-08-01T10:00:05+00:00", body="(opt-out ack)"),
        _row(3, 9011, "inbound", "text", "2030-08-01T10:00:10+00:00", body="still writes after stopping"),
        _row(4, 9011, "outbound", "text", "2030-08-01T10:00:15+00:00", body="(no reply either way)"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn(stopped=True))
    result = RP.replay_candidate(9011, scratch_dir, sales_brain_path=db)
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert lines[0]["luna"]["stopped"] is True
    assert lines[1]["skipped_reason"] == "stopped"
    assert "luna" not in lines[1]
    # the inbound/outbound rows after stopping are still recorded, same as production
    assert lines[1]["actual_reply"] == [{"body": "(no reply either way)", "kind": "text",
                                         "at": "2030-08-01T10:00:15+00:00"}]


# --- CUT AT TURN N -----------------------------------------------------------------------------------

def test_session_id_is_dropped_before_every_turn_cut_at_turn_n(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9012, "inbound", "text", "2030-08-01T10:00:00+00:00", body="eins"),
        _row(2, 9012, "outbound", "text", "2030-08-01T10:00:05+00:00", body="ok 1"),
        _row(3, 9012, "inbound", "text", "2030-08-01T10:00:10+00:00", body="zwei"),
        _row(4, 9012, "outbound", "text", "2030-08-01T10:00:15+00:00", body="ok 2"),
        _row(5, 9012, "inbound", "text", "2030-08-01T10:00:20+00:00", body="drei"),
    ])
    fake = _fake_turn()
    monkeypatch.setattr(LB, "turn", fake)
    RP.replay_candidate(9012, scratch_dir, sales_brain_path=db)

    assert len(fake.calls) == 3
    # every turn is a brand-new, memoryless session: _run_turn must have popped whatever
    # _session_id the PRIOR turn's own fake CLI reply stamped onto the card before this turn's own
    # call to LB.turn ever saw it -- never resumed between turns.
    assert [c["session_id"] for c in fake.calls] == [None, None, None]


# --- ISOLATION ---------------------------------------------------------------------------------------

def test_isolation_mcp_config_points_under_out_with_no_send(tmp_path, scratch_dir, monkeypatch):
    """Explicit AC: the written mcp_config carries WA_SQLITE_PATH and WA_LUNA_SESSION_DIR under
    --out, and WA_LUNA_NO_SEND=1."""
    _prime_board(monkeypatch)
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9013, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9013, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Hi"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    RP.replay_candidate(9013, scratch_dir, sales_brain_path=db)

    out_dir = scratch_dir.resolve()
    assert C.SQLITE_PATH == out_dir / "9013.sqlite"
    assert C.LUNA_SESSION_DIR == out_dir / "sessions"

    ready_path = C.LUNA_SESSION_DIR / "tools_ready" / "probe.json"
    config_path = LB._mcp_config_path(ready_path, phone="+490001234", no_send=True, role_class=None)
    assert out_dir in config_path.resolve().parents
    config = json.loads(config_path.read_text(encoding="utf-8"))
    env = config["mcpServers"][LB.MCP_SERVER_NAME]["env"]
    assert env["WA_SQLITE_PATH"] == str(C.SQLITE_PATH)
    assert env["WA_LUNA_SESSION_DIR"] == str(C.LUNA_SESSION_DIR)
    assert env[LB.NO_SEND_ENV] == "1"


# --- TIMESTAMPS --------------------------------------------------------------------------------------

def test_inbound_rows_carry_their_real_historical_timestamp_not_now(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9014, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9014, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Hi"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9014, scratch_dir, sales_brain_path=db)

    conn = sqlite3.connect(result["sqlite_path"])
    conn.row_factory = sqlite3.Row
    at_in = conn.execute("select at from wa_messages where direction='in' order by id").fetchone()["at"]
    conn.close()
    assert at_in == "2030-08-01T10:00:00+00:00"   # the historical moment, never "now" (ST.now_iso())


# --- COLD OPEN ---------------------------------------------------------------------------------------

def test_cold_open_template_is_marked_as_a_campaign_send(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9015, "outbound", "template", "2030-08-01T09:00:00+00:00", body="Hallo! Interesse an einer Stelle?"),
        _row(2, 9015, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Ja, gerne"),
        _row(3, 9015, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Super!"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9015, scratch_dir, sales_brain_path=db)

    conn = sqlite3.connect(result["sqlite_path"])
    conn.row_factory = sqlite3.Row
    first = conn.execute("select meta from wa_messages order by id limit 1").fetchone()
    conn.close()
    assert json.loads(first["meta"])["action"] == "campaign"

    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert lines[0]["turn"] == 1   # the template itself never spawns a turn of its own


# --- ESCALATION --------------------------------------------------------------------------------------

def test_escalation_diff_reports_only_what_this_turn_changed(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9016, "inbound", "text", "2030-08-01T10:00:00+00:00", body="eins"),
        _row(2, 9016, "outbound", "text", "2030-08-01T10:00:05+00:00", body="ok 1"),
        _row(3, 9016, "inbound", "text", "2030-08-01T10:00:10+00:00", body="zwei"),
        _row(4, 9016, "outbound", "text", "2030-08-01T10:00:15+00:00", body="ok 2"),
        _row(5, 9016, "inbound", "text", "2030-08-01T10:00:20+00:00", body="drei"),
        _row(6, 9016, "outbound", "text", "2030-08-01T10:00:25+00:00", body="ok 3"),
    ])
    calls = {"n": 0}

    def fn(text, thread, button_id=None, client=None, no_send=False):
        calls["n"] += 1
        card = dict(thread.get("slots") or {})
        if calls["n"] == 1:
            ESC.record_escalation(card, ESC.PET_POLICY_QUESTION, "dog")
        if calls["n"] == 3:
            ESC.record_flag(card, ESC.EXHAUSTIVE_CLAIM_SUSPECTED)
        return {"bubbles": [f"reply #{calls['n']}"], "buttons": [], "slots": card,
                "asked": thread.get("asked") or [], "stopped": False, "matches": [],
                "action": "reply_now_conversational"}
    monkeypatch.setattr(LB, "turn", fn)

    result = RP.replay_candidate(9016, scratch_dir, sales_brain_path=db)
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]

    # turn 1 added the escalation: every key it wrote, as a before/after
    esc1 = lines[0]["luna"]["escalation"]
    assert set(esc1) == {"escalated", "escalated_at", "escalation_codes", "escalate_reason",
                         "escalate_reason_notes"}
    assert esc1["escalated"] == {"before": None, "after": True}
    assert esc1["escalation_codes"] == {"before": None, "after": [ESC.PET_POLICY_QUESTION]}
    # the SAME escalation is still on the card during turn 2 but is not repeated in its own diff
    assert lines[1]["luna"]["escalation"] == {}
    # turn 3 added only a flag: flag keys appear, the old escalation does not
    esc3 = lines[2]["luna"]["escalation"]
    assert set(esc3) == {"flag_codes", "flags", "flags_notes"}
    assert esc3["flag_codes"] == {"before": None, "after": [ESC.EXHAUSTIVE_CLAIM_SUSPECTED]}


# --- CUT AT TURN N: what turn N+1 reads ---------------------------------------------------------------------

def test_next_turn_sees_the_real_reply_never_lunas_predicted_bubble(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9017, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9017, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Die echte Antwort"),
        _row(3, 9017, "inbound", "text", "2030-08-01T10:00:10+00:00", body="Danke"),
    ])
    fake = _fake_turn(bubbles=["Lunas vorhergesagte Antwort"])
    monkeypatch.setattr(LB, "turn", fake)
    RP.replay_candidate(9017, scratch_dir, sales_brain_path=db)

    assert fake.calls[0]["outbound_since_last_turn"] == []
    # turn 2 is told what the candidate really read since turn 1: the historical reply, not Luna's
    assert fake.calls[1]["outbound_since_last_turn"] == ["Die echte Antwort"]


# --- truncated (only real pending work) --------------------------------------------------------------------

def test_not_truncated_when_what_is_left_after_max_turns_drives_no_turn(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9018, "inbound", "text", "2030-08-01T10:00:00+00:00", body="eins"),
        _row(2, 9018, "outbound", "text", "2030-08-01T10:00:05+00:00", body="ok"),
        _row(3, 9018, "inbound", "reaction", "2030-08-01T10:00:06+00:00"),
        _row(4, 9018, "outbound", "text", "2030-08-01T10:05:00+00:00", body="spaeter noch ein Hinweis"),
        _row(5, 9018, "outbound", "unsupported", "2030-08-01T10:06:00+00:00"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9018, scratch_dir, max_turns=1, sales_brain_path=db)
    assert result["truncated"] is False and result["turns_run"] == 1
    lines = [json.loads(l) for l in open(result["jsonl_path"], encoding="utf-8")]
    assert len(lines) == 1
    # every item keeps its own timestamp, so a judge can split the reply from the later nudge
    assert [(a["body"], a["at"]) for a in lines[0]["actual_reply"]] == [
        ("ok", "2030-08-01T10:00:05+00:00"), ("spaeter noch ein Hinweis", "2030-08-01T10:05:00+00:00")]


def test_truncated_when_an_unconsumed_real_inbound_row_remains(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9019, "inbound", "text", "2030-08-01T10:00:00+00:00", body="eins"),
        _row(2, 9019, "outbound", "text", "2030-08-01T10:00:05+00:00", body="ok"),
        _row(3, 9019, "inbound", "reaction", "2030-08-01T10:00:06+00:00"),
        _row(4, 9019, "inbound", "text", "2030-08-01T10:00:10+00:00", body="zwei"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9019, scratch_dir, max_turns=1, sales_brain_path=db)
    assert result["truncated"] is True and result["turns_run"] == 1


# --- timestamps, both directions ---------------------------------------------------------------------------

def test_outbound_rows_carry_their_historical_timestamp_too(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9023, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9023, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Hi"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9023, scratch_dir, sales_brain_path=db)
    conn = sqlite3.connect(result["sqlite_path"])
    rows = conn.execute("select direction, at from wa_messages order by id").fetchall()
    conn.close()
    assert rows == [("in", "2030-08-01T10:00:00+00:00"), ("out", "2030-08-01T10:00:05+00:00")]


def test_store_record_inbound_without_at_still_stamps_now(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    conn = ST.db()
    assert ST.record_inbound(conn, "+49000000001", "w1", "hi") is True
    assert ST.record_inbound(conn, "+49000000001", "w2", "hi again", at="2030-01-01T00:00:00+00:00") is True
    ats = [r["at"] for r in conn.execute("select at from wa_messages order by id")]
    conn.close()
    assert ats[0] != "2030-01-01T00:00:00+00:00" and ats[0].startswith("20")   # now, unchanged default
    assert ats[1] == "2030-01-01T00:00:00+00:00"


# --- cold-open template ------------------------------------------------------------------------------------

def test_cold_open_template_puts_a_campaign_stub_on_the_card_with_only_known_fields(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9024, "outbound", "template", "2030-08-01T09:00:00+00:00", body="Hallo! Interesse?"),
        _row(2, 9024, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Ja"),
        _row(3, 9024, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Super!"),
    ])
    seen = {}

    def fn(text, thread, button_id=None, client=None, no_send=False):
        seen["campaign"] = (thread.get("slots") or {}).get("campaign")
        return _fake_turn()(text, thread, button_id=button_id, client=client, no_send=no_send)
    monkeypatch.setattr(LB, "turn", fn)
    result = RP.replay_candidate(9024, scratch_dir, sales_brain_path=db)

    # the brain sees the thread as campaign-opened: only what sales_brain gave, nothing invented
    assert seen["campaign"] == {"campaign_id": None, "template_name": None, "language": None,
                                "rendered_text": "Hallo! Interesse?", "buttons": [],
                                "sent_at": "2030-08-01T09:00:00+00:00", "wamid": "replay:9024:1"}
    conn = ST.db()
    assert ST.thread(conn, RP.synthetic_phone(9024))["slots"]["campaign"]["rendered_text"] == "Hallo! Interesse?"
    conn.close()
    assert result["turns_run"] == 1


def test_a_later_template_is_not_a_cold_open_and_an_empty_template_body_stays_a_placeholder(
        tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9025, "inbound", "text", "2030-08-01T10:00:00+00:00", body="Hallo"),
        _row(2, 9025, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Hi"),
        _row(3, 9025, "outbound", "template", "2030-08-09T10:00:00+00:00"),
    ])
    monkeypatch.setattr(LB, "turn", _fake_turn())
    result = RP.replay_candidate(9025, scratch_dir, sales_brain_path=db)
    conn = sqlite3.connect(result["sqlite_path"])
    rows = conn.execute("select body, meta from wa_messages where kind='template'").fetchall()
    card = json.loads(conn.execute("select slots from wa_threads").fetchone()[0])
    conn.close()
    assert rows[0][0] == "[template]"                      # no real wording in sales_brain: never invented
    assert "action" not in json.loads(rows[0][1])
    assert "campaign" not in card


# --- PII: documents ------------------------------------------------------------------------------------------

def test_a_document_filename_never_reaches_the_report_or_the_scratch_store(tmp_path, scratch_dir, monkeypatch):
    db = _make_sales_brain(tmp_path / "sb.sqlite", [
        _row(1, 9026, "inbound", "document", "2030-08-01T10:00:00+00:00",
             media_filename="Lebenslauf_Erika_Beispiel.pdf"),
        _row(2, 9026, "outbound", "text", "2030-08-01T10:00:05+00:00", body="Danke"),
    ])
    fake = _fake_turn()
    monkeypatch.setattr(LB, "turn", fake)
    result = RP.replay_candidate(9026, scratch_dir, sales_brain_path=db)

    report = open(result["jsonl_path"], encoding="utf-8").read()
    assert "[document].pdf" in report and "Erika" not in report and "Lebenslauf" not in report
    assert fake.calls[0]["text"] == "[document].pdf"
    conn = sqlite3.connect(result["sqlite_path"])
    dump = " ".join(str(v) for r in conn.execute("select body, meta from wa_messages") for v in r)
    conn.close()
    assert "Erika" not in dump and "Lebenslauf" not in dump


# --- git: the sha of the code, and the main checkout from a linked worktree ----------------------------------

def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_git_sha_is_the_head_of_the_given_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c1")
    assert RP._git_sha(repo) == _git(repo, "rev-parse", "HEAD")


def test_git_sha_outside_a_repo_is_a_loud_error_not_a_blank(tmp_path):
    with pytest.raises(RuntimeError, match="git rev-parse HEAD failed"):
        RP._git_sha(tmp_path)


def test_main_checkout_root_from_a_linked_worktree_is_the_main_checkout(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "commit", "-q", "--allow-empty", "-m", "c1")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    real = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(real)   # a fresh copy: the autouse fixture patched CLI's own function
    assert real._main_checkout_root(linked).resolve() == main.resolve()
    assert real._main_checkout_root(main).resolve() == main.resolve()
