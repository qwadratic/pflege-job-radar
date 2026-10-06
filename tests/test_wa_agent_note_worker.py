"""app/wa/luna/agent_note_worker.py: every branch of one run_once() tick, and the pure text-building
helpers (decode prompt, card description, hand-off message/prompt). Offline only: a fake subprocess
runner stands in for `claude`, a fake backlog adapter stands in for the `backlog` CLI, a fixed clock
stands in for wall time -- nothing here spawns either real CLI, matching agent_note_worker.py's own
"injectable seams" design.
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.wa import api as WAPI
from app.wa import config as C
from app.wa import store as ST
from app.wa.luna import agent_note_worker as W
from app.wa.luna import agent_notes as AN

PHONE = "+4915550200001"
NOW = datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)

DECODE_OK = {"title_en": "Check password reset button", "request_en": "Please check whether the "
            "password reset button works.", "kind": "action", "urgency": "today",
            "acceptance_en": ["Button confirmed working, or a bug filed"], "questions_en": []}
HANDOFF_OK = {"delivered": True, "matches": 1, "peer_names": ["wa-harness"], "error": None}


class FakeMeta:
    """The rail this actually runs on (the phone bridge): no 24h free-form window."""
    requires_freeform_window = False

    def __init__(self):
        self.sent = []

    def send_text(self, to_e164, body):
        self.sent.append((to_e164, body))
        return f"wamid.out.{len(self.sent)}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    """A tmp database plus a fake WhatsApp client, so _send_completion's real code path (not a mock
    of it) runs in every test that reaches a completion send."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", "555000111")
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    meta = FakeMeta()
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: meta)
    return meta


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path / "state"


def _note(c, wamid="wamid.1", phone=PHONE, body="Проверь пожалуйста кнопку сброса пароля", kind="text"):
    return ST.record_agent_note(c, wamid, phone, body, kind)


class FakeCLI:
    """Stands in for subprocess.run for BOTH claude -p calls: dispatches on whether the --json-schema
    argument's properties include title_en (decode) or delivered (hand-off). Each of ``decode``/
    ``handoff`` is either a dict (wrapped into a successful --output-format json envelope), a
    subprocess.CompletedProcess (returned as-is, for a non-zero-exit/unparseable-stdout scenario), or
    an exception instance (raised, for a timeout/FileNotFoundError scenario). None means "must not be
    called this test" -- calling it raises AssertionError."""

    def __init__(self, *, decode=None, handoff=None):
        self.decode = decode
        self.handoff = handoff
        self.calls = []

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None, cwd=None, env=None):
        self.calls.append({"argv": list(argv), "input": input, "cwd": cwd, "timeout": timeout, "env": env})
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        is_decode = "title_en" in schema["properties"]
        outcome = self.decode if is_decode else self.handoff
        if outcome is None:
            raise AssertionError(f"{'decode' if is_decode else 'hand-off'} claude -p must not run "
                                 f"in this test")
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, subprocess.CompletedProcess):
            return outcome
        envelope = {"is_error": False, "result": json.dumps(outcome), "structured_output": outcome}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(envelope), stderr="")


class FakeBacklog:
    """``existing`` stands in for the REAL BacklogAdapter's own two-step list+view CLI dance
    (find_card_by_wamid): a fake never spawns the CLI, so it just returns the id it was told to,
    unconditionally, when it should simulate crash recovery finding an already-adopted card. A real
    review-round bug (round-1 finding #1) was that title-prefix matching alone would adopt a card for
    the WRONG wamid -- ``wrong_wamid_existing`` lets a test simulate exactly that: a candidate the
    title prefix would match, but whose wamid does not, so find_card_by_wamid must return None."""

    def __init__(self, *, existing=None, wrong_wamid_existing=None, create_result="TASK-999"):
        self.existing = existing
        self.wrong_wamid_existing = wrong_wamid_existing
        self.create_result = create_result
        self.created = []
        self.find_calls = []

    def find_card_by_wamid(self, *, prefix, wamid):
        self.find_calls.append({"prefix": prefix, "wamid": wamid})
        if self.wrong_wamid_existing:
            return None   # simulates a title-prefix candidate whose own wamid line does not match
        return self.existing

    def create_card(self, *, title, description, acceptance, priority):
        self.created.append({"title": title, "description": description, "acceptance": acceptance,
                            "priority": priority})
        return self.create_result


class ExplodingBacklog:
    """Both methods raise -- for a test proving the worker must not touch the backlog CLI at all."""

    def find_card_by_wamid(self, *, prefix, wamid):
        raise AssertionError("find_card_by_wamid must not be called in this test")

    def create_card(self, **kw):
        raise AssertionError("create_card must not be called in this test")


class ExplodingCLIThatMustNotRun:
    """Stands in for the subprocess runner when NO claude -p call should happen this run."""

    def __call__(self, *a, **k):
        raise AssertionError("no claude -p call is made in this test")


def _health(state_dir_path):
    return json.loads((state_dir_path / "health.json").read_text(encoding="utf-8"))


def _run(state_dir_path, **kw):
    kw.setdefault("clock", lambda: NOW)
    kw.setdefault("target", "wa-harness")
    return W.run_once(state_dir_path=state_dir_path, **kw)


# --- (a) nothing open -----------------------------------------------------------------------------

def test_nothing_open_writes_a_healthy_status_and_touches_neither_cli(wa, state_dir):
    rc = _run(state_dir, runner=FakeCLI(), backlog=ExplodingBacklog())
    assert rc == 0
    h = _health(state_dir)
    assert h == {"ok": True, "at": NOW.isoformat(), "problem": None, "note_id": None, "card_id": None,
                "completion_retries": [], "orphaned_in_progress": []}


# --- pending -> handed_off, the full happy path ---------------------------------------------------

def test_pending_note_is_decoded_carded_and_handed_off_in_one_run(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
    cli = FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK)
    bl = FakeBacklog()

    rc = _run(state_dir, runner=cli, backlog=bl)

    assert rc == 0
    assert _health(state_dir) == {"ok": True, "at": NOW.isoformat(), "problem": None,
                                  "note_id": row["id"], "card_id": "TASK-999",
                                  "completion_retries": [], "orphaned_in_progress": []}
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["status"] == "handed_off"
    assert fresh["task_id"] == "TASK-999"
    assert fresh["handed_off_at"] is not None
    assert fresh["claimed_at"] is None
    assert "created card TASK-999" in fresh["progress"]
    assert "handed off to wa-harness (card TASK-999)" in fresh["progress"]
    # exactly one card, with the priority "today"->"medium" maps to, and the decoded acceptance list
    assert len(bl.created) == 1
    assert bl.created[0]["title"] == f"Operator note #{row['id']}: {DECODE_OK['title_en']}"
    assert bl.created[0]["priority"] == "medium"
    assert bl.created[0]["acceptance"] == DECODE_OK["acceptance_en"]
    assert row["body"] in bl.created[0]["description"]   # the verbatim Russian is quoted in the card
    # both claude -p calls ran with cwd = the state dir (no project settings/hooks load)
    assert all(call["cwd"] == str(state_dir) for call in cli.calls)
    assert len(cli.calls) == 2


def test_the_decode_call_runs_restricted_with_no_tools_and_the_handoff_call_only_listagents_sendmessage(wa, state_dir):
    cli = FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK)
    with ST.db() as c:
        _note(c)
    _run(state_dir, runner=cli, backlog=FakeBacklog())
    decode_argv, handoff_argv = cli.calls[0]["argv"], cli.calls[1]["argv"]
    assert "--restricted" in decode_argv
    assert decode_argv[decode_argv.index("--tools") + 1] == ""
    assert "--restricted" in handoff_argv   # confirmed empirically to still allow the two named tools
    assert handoff_argv[handoff_argv.index("--tools") + 1] == "ListAgents SendMessage"
    assert handoff_argv[handoff_argv.index("--allowedTools") + 1] == "ListAgents SendMessage"
    for argv in (decode_argv, handoff_argv):
        assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
        # TASK-303 item E (Ivan, 2026-09-25: "лимит бюджета снять" -- the CLI has no token cap to use
        # instead): no dollar cap on either call, not even a leftover flag.
        assert "--max-budget-usd" not in argv


def test_neither_claude_call_inherits_this_processs_full_environment(wa, state_dir, monkeypatch):
    """TASK-303 item E/C: both children get env={HOME, PATH} only -- never this process's real
    OPENAI_API_KEY/mailbox passwords/Meta token (app/wa/envfile.py loads those for the SERVICE's own
    use, not for a claude -p child to inherit)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-reach-the-child")
    monkeypatch.setenv("HOME", "/home/claude")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    cli = FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK)
    with ST.db() as c:
        _note(c)
    _run(state_dir, runner=cli, backlog=FakeBacklog())
    for call in cli.calls:
        assert call["env"] == {"HOME": "/home/claude", "PATH": "/usr/bin:/bin"}
        assert "OPENAI_API_KEY" not in call["env"]


def test_the_handoff_message_carries_no_operator_derived_text(wa, state_dir):
    """AC#7: the message SendMessage is asked to deliver is composed by code -- an id, a card id, and
    fixed command templates -- never the note's own text, verbatim or decoded."""
    secret_marker = "УНИКАЛЬНАЯ_ОПЕРАТОРСКАЯ_ФРАЗА_МАРКЕР"
    with ST.db() as c:
        row = _note(c, body=f"Проверь {secret_marker} пожалуйста")
    decoded_with_marker_in_english = {**DECODE_OK, "request_en": f"about {secret_marker} please",
                                      "title_en": secret_marker[:80]}
    cli = FakeCLI(decode=decoded_with_marker_in_english, handoff=HANDOFF_OK)
    _run(state_dir, runner=cli, backlog=FakeBacklog())
    handoff_prompt = cli.calls[1]["input"]
    assert secret_marker not in handoff_prompt
    assert f"Operator note #{row['id']}" in handoff_prompt
    assert "TASK-999" in handoff_prompt
    assert "backlog task view TASK-999 --plain" in handoff_prompt
    assert f"--done {row['id']}" in handoff_prompt and f"--blocked {row['id']}" in handoff_prompt


# --- decode failure ---------------------------------------------------------------------------

def test_decode_failure_releases_the_note_and_never_creates_a_card(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
    cli = FakeCLI(decode=subprocess.TimeoutExpired(cmd=["claude"], timeout=120))
    bl = FakeBacklog()   # find_card_by_wamid (crash recovery) runs and finds nothing -- normal
    rc = _run(state_dir, runner=cli, backlog=bl)

    assert rc == 1
    assert bl.created == []
    h = _health(state_dir)
    assert h["ok"] is False and "decode failed" in h["problem"]
    assert h["note_id"] == row["id"] and h["card_id"] is None
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["status"] == "pending"
    assert fresh["task_id"] is None
    assert "decode failed" in fresh["progress"]
    with ST.db() as c:
        assert row["id"] in [r["id"] for r in ST.open_agent_notes(c)]   # claimable again next tick


@pytest.mark.parametrize("bad_envelope, why", [
    (subprocess.CompletedProcess(["claude"], 1, stdout="", stderr="boom"), "non-zero exit"),
    (subprocess.CompletedProcess(["claude"], 0, stdout="not json", stderr=""), "unparseable stdout"),
    (subprocess.CompletedProcess(["claude"], 0,
                                 stdout=json.dumps({"is_error": True, "result": "budget exceeded"}),
                                 stderr=""), "is_error envelope (e.g. budget hit)"),
    (subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"is_error": False, "result": "{}"}),
                                 stderr=""), "no structured_output key at all"),
])
def test_every_decode_failure_shape_is_treated_the_same_way(wa, state_dir, bad_envelope, why):
    with ST.db() as c:
        _note(c)
    bl = FakeBacklog()   # crash-recovery search runs first and finds nothing, as on any fresh note
    rc = _run(state_dir, runner=FakeCLI(decode=bad_envelope), backlog=bl)
    assert rc == 1, why
    assert _health(state_dir)["ok"] is False
    assert "decode failed" in _health(state_dir)["problem"], why
    assert bl.created == [], why


def test_a_decoded_object_missing_a_required_field_is_also_a_decode_failure(wa, state_dir):
    """--json-schema should stop this at the CLI, but the worker double-checks the shape itself
    (matching agent_note_gate.py/refusal.py's own always-recheck convention) rather than trusting it
    blindly."""
    with ST.db() as c:
        _note(c)
    broken = {k: v for k, v in DECODE_OK.items() if k != "urgency"}
    bl = FakeBacklog()
    rc = _run(state_dir, runner=FakeCLI(decode=broken), backlog=bl)
    assert rc == 1
    assert bl.created == []
    assert "decode failed" in _health(state_dir)["problem"]


# --- hand-off failure -> pending, card kept ------------------------------------------------------

def test_handoff_failure_releases_to_pending_but_keeps_the_card(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
    cli = FakeCLI(decode=DECODE_OK, handoff={"delivered": False, "matches": 0, "peer_names": [],
                                             "error": "no exact match"})
    rc = _run(state_dir, runner=cli, backlog=FakeBacklog())

    assert rc == 1
    h = _health(state_dir)
    assert h["ok"] is False and "handoff failed" in h["problem"] and "no exact match" in h["problem"]
    assert h["card_id"] == "TASK-999"
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["status"] == "pending"
    assert fresh["task_id"] == "TASK-999"       # kept -- AC#8
    assert fresh["claimed_at"] is None
    assert "handoff failed" in fresh["progress"]


def test_handoff_with_several_matches_is_also_a_failure_never_a_guess(wa, state_dir):
    """AC#5: zero or SEVERAL matches is a failed hand-off, never a guess at which one to use."""
    with ST.db() as c:
        _note(c)
    cli = FakeCLI(decode=DECODE_OK, handoff={"delivered": False, "matches": 2,
                                             "peer_names": ["wa-harness", "wa-harness"], "error": None})
    rc = _run(state_dir, runner=cli, backlog=FakeBacklog())
    assert rc == 1
    assert "matches=2" in _health(state_dir)["problem"]


def test_handoff_delivered_false_even_with_matches_one_does_not_count_as_success(wa, state_dir):
    """delivered is the model's own claim that it actually sent -- matches==1 alone is not enough."""
    with ST.db() as c:
        _note(c)
    cli = FakeCLI(decode=DECODE_OK, handoff={"delivered": False, "matches": 1,
                                             "peer_names": ["wa-harness"], "error": "SendMessage failed"})
    rc = _run(state_dir, runner=cli, backlog=FakeBacklog())
    assert rc == 1
    with ST.db() as c:
        notes = ST.open_agent_notes(c)
    assert notes and notes[0]["status"] == "pending"


# --- retry after a failed hand-off skips decode and card creation ---------------------------------

def test_retry_after_a_failed_handoff_skips_decode_and_reuses_the_existing_card(wa, state_dir, monkeypatch):
    with ST.db() as c:
        row = _note(c)
    # End state of a prior run that got as far as creating the card, then failed the hand-off.
    with ST.db() as c:
        ST.claim_agent_note(c, row["id"])
        ST.set_agent_note_task(c, row["id"], "TASK-500")
        ST.release_agent_note(c, row["id"])

    def boom(*a, **k):
        raise AssertionError("decode_note must not run on a retry with task_id already set")
    monkeypatch.setattr(W, "decode_note", boom)
    cli = FakeCLI(handoff=HANDOFF_OK)   # decode=None: FakeCLI itself would also raise if asked
    rc = _run(state_dir, runner=cli, backlog=ExplodingBacklog())

    assert rc == 0
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["status"] == "handed_off" and fresh["task_id"] == "TASK-500"
    assert len(cli.calls) == 1   # only the hand-off call


# --- crash after card creation adopts the existing card --------------------------------------------

def test_crash_recovery_adopts_an_existing_card_by_exact_title_prefix_without_decoding_again(wa, state_dir, monkeypatch):
    with ST.db() as c:
        row = _note(c)   # task_id is null: as if the process died right after create_card returned

    def boom(*a, **k):
        raise AssertionError("decode_note must not run when crash recovery finds the card")
    monkeypatch.setattr(W, "decode_note", boom)
    bl = FakeBacklog(existing="TASK-777")
    cli = FakeCLI(handoff=HANDOFF_OK)

    rc = _run(state_dir, runner=cli, backlog=bl)

    assert rc == 0
    assert bl.find_calls == [{"prefix": f"Operator note #{row['id']}: ", "wamid": row["wamid"]}]
    assert bl.created == []   # never created a second one
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["task_id"] == "TASK-777" and fresh["status"] == "handed_off"
    assert "recovered existing card TASK-777" in fresh["progress"]


def test_crash_recovery_does_not_adopt_a_title_prefix_match_with_the_wrong_wamid(wa, state_dir, monkeypatch):
    """TASK-303 item A3, round-1 review finding #1: title prefix alone is not enough. A candidate that
    matches the id-based title prefix but whose own card does not carry THIS note's wamid line must be
    treated as no match at all -- decode + create proceeds, exactly as if find_card_by_wamid found
    nothing, rather than silently adopting an unrelated note's card."""
    with ST.db() as c:
        row = _note(c)
    bl = FakeBacklog(wrong_wamid_existing="TASK-666", create_result="TASK-1000")
    cli = FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK)

    rc = _run(state_dir, runner=cli, backlog=bl)

    assert rc == 0
    assert len(bl.created) == 1   # decode + create DID run -- the wrong-wamid candidate was rejected
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["task_id"] == "TASK-1000"   # the freshly-created card, never TASK-666


def test_a_crash_recovery_search_failure_is_a_release_not_a_silent_fresh_create(wa, state_dir, monkeypatch):
    """A flaky `backlog task list` must not risk minting a duplicate card -- treated as a full failure
    of this run, same as a decode failure, rather than silently falling through to create a new one."""
    with ST.db() as c:
        _note(c)

    def boom(*a, **k):
        raise AssertionError("must not create a card when the crash-recovery search itself failed")
    monkeypatch.setattr(W, "decode_note", boom)

    class FlakyBacklog(ExplodingBacklog):
        def find_card_by_wamid(self, *, prefix, wamid):
            raise W.BacklogError("backlog task list exited 1: locked")
    rc = _run(state_dir, runner=FakeCLI(), backlog=FlakyBacklog())
    assert rc == 1
    assert "crash-recovery card search failed" in _health(state_dir)["problem"]


# --- attempts cap -> blocked + completion ----------------------------------------------------------

def test_attempts_cap_closes_the_note_as_blocked_and_sends_the_russian_completion(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
        for _ in range(C.AGENT_NOTE_ATTEMPTS_CAP):
            ST.claim_agent_note(c, row["id"])
            ST.release_agent_note(c, row["id"])
    assert row["id"]

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())

    assert rc == 1
    h = _health(state_dir)
    assert h["ok"] is False and "attempts cap reached" in h["problem"]
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["status"] == "blocked"
    assert fresh["notified_at"] is not None
    assert (wa.sent and wa.sent[0][0] == PHONE)
    body = wa.sent[0][1]
    assert body.startswith(f"[агент] Заметка #{row['id']} — {AN.BLOCKED_WORD}")


def test_attempts_cap_names_the_card_in_the_russian_reason_when_one_exists(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
        ST.claim_agent_note(c, row["id"])
        ST.set_agent_note_task(c, row["id"], "TASK-321")
        ST.release_agent_note(c, row["id"])
        for _ in range(C.AGENT_NOTE_ATTEMPTS_CAP - 1):
            ST.claim_agent_note(c, row["id"])
            ST.release_agent_note(c, row["id"])

    _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert "TASK-321" in fresh["blocked_reason"]
    assert "TASK-321" in fresh["outcome_needed"]


def test_attempts_cap_reason_counts_the_attempts_that_actually_ran_not_the_claim_that_tripped_it(wa, state_dir):
    """Round-1 review, nonblocking, fixed as a plain bug: attempts==CAP+1 by the time the cap check
    runs (the tripping claim never itself ran a pipeline attempt), so the Russian text must say CAP
    (5), not CAP+1 (6)."""
    with ST.db() as c:
        row = _note(c)
        for _ in range(C.AGENT_NOTE_ATTEMPTS_CAP):
            ST.claim_agent_note(c, row["id"])
            ST.release_agent_note(c, row["id"])

    _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert f"{C.AGENT_NOTE_ATTEMPTS_CAP} раз подряд" in fresh["blocked_reason"]
    assert f"{C.AGENT_NOTE_ATTEMPTS_CAP + 1} раз" not in fresh["blocked_reason"]
    assert "attempts cap reached (%s)" % C.AGENT_NOTE_ATTEMPTS_CAP in _health(state_dir)["problem"]


def test_attempts_cap_needed_text_has_no_doubled_nuzhno_prefix(wa, state_dir):
    """Round-1 review, nonblocking, fixed as a plain bug: completion_note's own template already
    prepends 'Нужно: ' to the needed field -- the old _attempts_cap_needed did too, so the operator
    read 'Нужно: Нужно: открыть сессию...'."""
    with ST.db() as c:
        row = _note(c)
        for _ in range(C.AGENT_NOTE_ATTEMPTS_CAP):
            ST.claim_agent_note(c, row["id"])
            ST.release_agent_note(c, row["id"])

    _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
    assert wa.sent
    body = wa.sent[0][1]
    assert "Нужно: Нужно:" not in body
    assert "Нужно: открыть сессию wa-harness" in body


def test_attempts_cap_reason_does_not_specifically_blame_the_hand_off(wa, state_dir):
    """Round-1 review, nonblocking: the old text always said the HAND-OFF failed N times, even when
    every failure was actually a decode or card-creation failure this module never tracked per-stage.
    The reason text must not make a claim this module cannot actually verify."""
    with ST.db() as c:
        row = _note(c)
        for _ in range(C.AGENT_NOTE_ATTEMPTS_CAP):
            ST.claim_agent_note(c, row["id"])
            ST.release_agent_note(c, row["id"])

    _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert "Передача" not in fresh["blocked_reason"]   # no longer claims specifically "the hand-off"


def test_the_cap_th_attempt_still_runs_the_pipeline_not_blocked_one_early(wa, state_dir):
    """Round-1 review, nonblocking test GAP found by mutation (not itself a bug -- the review confirmed
    the code is already correct): changing ``row['attempts'] > C.AGENT_NOTE_ATTEMPTS_CAP`` to ``>=``
    survived all 48 store/worker tests, because every attempts-cap test above seeds exactly CAP prior
    attempts before its own real tick -- that tick is always the CAP+1-th claim, never the CAP-th one.
    Seeding CAP-1 prior attempts instead means THIS test's own tick is what makes attempts==CAP, so a
    ``>=`` mutant wrongly blocks it one attempt early while the real ``>`` correctly still runs it."""
    with ST.db() as c:
        row = _note(c)
        for _ in range(C.AGENT_NOTE_ATTEMPTS_CAP - 1):
            ST.claim_agent_note(c, row["id"])
            ST.release_agent_note(c, row["id"])

    rc = _run(state_dir, runner=FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK), backlog=FakeBacklog())

    assert rc == 0
    h = _health(state_dir)
    assert h["ok"] is True
    assert "attempts cap" not in (h["problem"] or "")
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["attempts"] == C.AGENT_NOTE_ATTEMPTS_CAP
    assert fresh["status"] == "handed_off"


# --- BacklogAdapter itself (not the FakeBacklog stand-in every test above uses) -----------------------

def test_backlog_adapter_itself_builds_argv_and_parses_the_real_cli_shapes(tmp_path):
    """Round-1 review, nonblocking test gap: create_card and find_card_by_wamid (the argv, the JSON
    'tasks' parsing, the title-prefix match, the --plain task-id regex) had no test exercising
    BacklogAdapter's OWN code against a fake subprocess runner -- every worker-level test above swaps in
    FakeBacklog instead, which bypasses this class entirely and only proves run_once()'s own branching.
    Response shapes below match what a real `backlog` CLI actually printed this session (create's
    --plain two-line output with no --json form; list/view --json's schemaVersion/kind/tasks and
    task.description envelopes -- see this class's own docstring)."""
    calls = []

    def fake_runner(argv, capture_output=True, text=True, timeout=None, cwd=None):
        calls.append(list(argv))
        if argv[:3] == ["backlog", "task", "create"]:
            return subprocess.CompletedProcess(argv, 0,
                stdout="Creating task...\nTask TASK-777 - Some title\n", stderr="")
        if argv[:3] == ["backlog", "task", "list"]:
            body = {"schemaVersion": 1, "kind": "tasks",
                   "tasks": [{"id": "TASK-777", "title": "[operator] Note #9 -- Some title"}]}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(body), stderr="")
        if argv[:3] == ["backlog", "task", "view"]:
            body = {"task": {"id": "TASK-777", "description": "body\nNote wamid: wamid.abc\nmore"}}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(body), stderr="")
        raise AssertionError(f"unexpected argv: {argv}")

    adapter = W.BacklogAdapter(cwd=tmp_path, runner=fake_runner)

    task_id = adapter.create_card(title="[operator] Note #9 -- Some title", description="body",
                                  acceptance=["ok"], priority="high")
    assert task_id == "TASK-777"
    assert calls[0][:3] == ["backlog", "task", "create"]
    assert "-l" in calls[0] and "operator-note" in calls[0]
    assert "--project" in calls[0] and "whatsapp" in calls[0]

    found = adapter.find_card_by_wamid(prefix="[operator] Note #9", wamid="wamid.abc")
    assert found == "TASK-777"
    assert calls[1][:4] == ["backlog", "task", "list", "--labels"]
    assert calls[2][:3] == ["backlog", "task", "view"]

    not_found = adapter.find_card_by_wamid(prefix="[operator] Note #9", wamid="wamid.SOMETHING-ELSE")
    assert not_found is None   # title prefix matched, but the wamid line inside the description did not


# --- finished-undelivered -> completion retried, backlog/claude never touched --------------------

def test_a_finished_undelivered_note_only_retries_the_completion_send(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
        ST.finish_agent_note(c, row["id"], "done", "Сделал X", "", "Ничего")
        assert ST.agent_note(c, row["id"])["notified_at"] is None   # never sent yet

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())

    assert rc == 0
    assert _health(state_dir)["ok"] is True
    assert wa.sent and "готово" in wa.sent[0][1]
    with ST.db() as c:
        assert ST.agent_note(c, row["id"])["notified_at"] is not None


def test_a_finished_undelivered_blocked_note_retries_with_the_blocked_word(wa, state_dir):
    with ST.db() as c:
        row = _note(c)
        ST.finish_agent_note(c, row["id"], "blocked", "", "не смог", "нужна помощь", blocked_reason="не смог")

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
    assert rc == 0
    assert wa.sent and "заблокировано" in wa.sent[0][1] and "не смог" in wa.sent[0][1]


def test_a_completion_retry_that_fails_again_is_reported_and_stays_retryable(wa, state_dir, monkeypatch):
    class Failing(FakeMeta):
        def send_text(self, to_e164, body):
            raise RuntimeError("bridge unreachable")
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: Failing())
    with ST.db() as c:
        row = _note(c)
        ST.finish_agent_note(c, row["id"], "done", "x", "", "")

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
    assert rc == 1
    assert "retry completion send failed" in _health(state_dir)["problem"]
    with ST.db() as c:
        assert ST.agent_note(c, row["id"])["notified_at"] is None   # still retryable next tick


# --- item B: undelivered completions retry independently, never block each other or the pipeline ---

def test_one_failing_completion_retry_does_not_stop_a_second_from_being_retried_the_same_tick(wa, state_dir, monkeypatch):
    """The exact bug both round-1 lenses flagged: the OLD design took rows[0] as THE note for the
    whole tick, so a stuck completion at the head of the queue stopped every other note -- including
    another finished note's own completion -- from being looked at at all."""
    calls = {"n": 0}

    class HalfFailing(FakeMeta):
        def send_text(self, to_e164, body):
            calls["n"] += 1
            if "Заметка #1" in body or to_e164 == "+4915550200099":
                raise RuntimeError("bridge unreachable for this one")
            return super().send_text(to_e164, body)
    meta = HalfFailing()
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: meta)
    with ST.db() as c:
        failing = ST.record_agent_note(c, "wamid.fail", "+4915550200099", "x", "text")
        ST.finish_agent_note(c, failing["id"], "done", "x", "", "")
        ok = ST.record_agent_note(c, "wamid.ok", PHONE, "y", "text")
        ST.finish_agent_note(c, ok["id"], "done", "y", "", "")

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())

    assert rc == 1   # one of the two failed -> the tick is not clean
    h = _health(state_dir)
    retries = {r["note_id"]: r for r in h["completion_retries"]}
    assert retries[failing["id"]]["ok"] is False
    assert retries[ok["id"]]["ok"] is True   # the OTHER note's completion still went out this tick
    with ST.db() as c:
        assert ST.agent_note(c, failing["id"])["notified_at"] is None
        assert ST.agent_note(c, ok["id"])["notified_at"] is not None


def test_an_undelivered_completion_does_not_block_a_separately_pending_note_the_same_tick(wa, state_dir, monkeypatch):
    """The other half of item B: a stuck completion must not keep a FRESH pending note from being
    decoded and handed off in the very same tick it is stuck in."""
    class AlwaysFailing(FakeMeta):
        def send_text(self, to_e164, body):
            raise RuntimeError("bridge unreachable")
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: AlwaysFailing())
    with ST.db() as c:
        stuck = ST.record_agent_note(c, "wamid.stuck", PHONE, "x", "text")
        ST.finish_agent_note(c, stuck["id"], "done", "x", "", "")
        pending = ST.record_agent_note(c, "wamid.pending2", PHONE, "please decode me", "text")
    cli = FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK)

    rc = _run(state_dir, runner=cli, backlog=FakeBacklog())

    assert rc == 1   # the stuck completion still makes the tick unhealthy overall
    with ST.db() as c:
        assert ST.agent_note(c, stuck["id"])["notified_at"] is None   # still stuck
        fresh_pending = ST.agent_note(c, pending["id"])
    assert fresh_pending["status"] == "handed_off"   # but the pending note went through anyway
    assert fresh_pending["task_id"] == "TASK-999"
    h = _health(state_dir)
    assert h["completion_retries"] and h["completion_retries"][0]["ok"] is False


def test_completion_retries_have_no_cap_across_many_ticks(wa, state_dir, monkeypatch):
    """Ivan, 2026-09-25: 'no cap on completion retries' (AC#8's own 'until delivered'). Run many ticks
    against a completion that always fails -- it must still be retried on the last one, never given up
    on the way the pipeline's own attempts cap gives up on a note."""
    class AlwaysFailing(FakeMeta):
        def send_text(self, to_e164, body):
            raise RuntimeError("bridge unreachable")
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: AlwaysFailing())
    with ST.db() as c:
        row = ST.record_agent_note(c, "wamid.forever", PHONE, "x", "text")
        ST.finish_agent_note(c, row["id"], "done", "x", "", "")

    attempts_far_beyond_the_pipeline_cap = C.AGENT_NOTE_ATTEMPTS_CAP * 3
    for _ in range(attempts_far_beyond_the_pipeline_cap):
        rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())
        assert rc == 1
    with ST.db() as c:
        fresh = ST.agent_note(c, row["id"])
    assert fresh["notified_at"] is None       # still never delivered
    assert fresh["status"] == "done"          # never force-closed the way the pipeline's cap would


# --- item C: an orphaned (not-yet-stale) in_progress note is visible, never hidden behind ok:true ---

def test_a_live_in_progress_note_is_reported_as_an_orphan_not_hidden_as_ok_true(wa, state_dir):
    """Round-1 review reproduction: a crashed prior tick leaves a note in_progress with a fresh
    (not-yet-stale) claimed_at. Since claimable_agent_notes() excludes it and there is nothing else
    open, the OLD code found 'nothing open' and wrote ok:true straight over the crash."""
    with ST.db() as c:
        row = _note(c)
        ST.claim_agent_note(c, row["id"])   # claimed_at = now -- simulates a crashed prior tick

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())

    assert rc == 1
    h = _health(state_dir)
    assert h["ok"] is False
    assert h["orphaned_in_progress"] and h["orphaned_in_progress"][0]["note_id"] == row["id"]
    assert str(row["id"]) in h["problem"] or row["id"] == h["note_id"]


def test_a_stale_in_progress_note_is_claimed_normally_not_reported_as_an_orphan(wa, state_dir):
    """The complement: once a claim goes stale it is ordinary claimable work again (a real crash
    recovery via the pipeline's own retry), never an 'orphan' -- those are two different situations."""
    with ST.db() as c:
        row = _note(c)
        ST.claim_agent_note(c, row["id"])
        # store.py's staleness cutoff is real wall-clock time (no injected clock there), so this must
        # be relative to datetime.now(), not the fixture's fake NOW used only for health timestamps.
        really_stale = datetime.now(timezone.utc) - timedelta(seconds=ST.AGENT_NOTE_STALE_SEC + 60)
        c.execute("update wa_agent_notes set claimed_at=? where id=?",
                 (really_stale.replace(microsecond=0).isoformat(), row["id"]))
        c.commit()
    cli = FakeCLI(decode=DECODE_OK, handoff=HANDOFF_OK)

    rc = _run(state_dir, runner=cli, backlog=FakeBacklog())

    assert rc == 0
    h = _health(state_dir)
    assert h["orphaned_in_progress"] == []
    assert h["note_id"] == row["id"] and h["card_id"] == "TASK-999"


# --- item E: token usage is logged, success or failure -------------------------------------------

def _envelope_with_usage(structured_output, *, cost=0.0110076, in_tok=2, out_tok=4, cache_c=2715, cache_r=518):
    return json.dumps({"is_error": False, "result": json.dumps(structured_output),
                       "structured_output": structured_output, "total_cost_usd": cost,
                       "usage": {"input_tokens": in_tok, "output_tokens": out_tok,
                                 "cache_creation_input_tokens": cache_c, "cache_read_input_tokens": cache_r}})


def test_decode_usage_is_logged_to_the_progress_trail_on_success(wa, state_dir):
    with ST.db() as c:
        row = _note(c)

    class UsageCLI(FakeCLI):
        def __call__(self, argv, **kw):
            proc = super().__call__(argv, **kw)
            schema = json.loads(argv[argv.index("--json-schema") + 1])
            if "title_en" in schema["properties"]:
                return subprocess.CompletedProcess(argv, 0, stdout=_envelope_with_usage(DECODE_OK), stderr="")
            return proc
    _run(state_dir, runner=UsageCLI(decode=DECODE_OK, handoff=HANDOFF_OK), backlog=FakeBacklog())
    with ST.db() as c:
        trail = ST.agent_note(c, row["id"])["progress"]
    assert "decode usage: in=2 out=4 cache_creation=2715 cache_read=518 cost_usd=0.0110076" in trail


def test_handoff_usage_is_logged_to_the_progress_trail_on_failure_too(wa, state_dir):
    """Item E: 'each claude call's token usage' -- including a call that ultimately failed the note,
    since the CALL itself still spent real tokens."""
    with ST.db() as c:
        row = _note(c)

    class UsageCLI(FakeCLI):
        def __call__(self, argv, **kw):
            schema = json.loads(argv[argv.index("--json-schema") + 1])
            if "delivered" in schema["properties"]:
                bad = {"delivered": False, "matches": 0, "peer_names": [], "error": "no exact match"}
                return subprocess.CompletedProcess(argv, 0, stdout=_envelope_with_usage(bad, cost=0.02),
                                                   stderr="")
            return super().__call__(argv, **kw)
    _run(state_dir, runner=UsageCLI(decode=DECODE_OK, handoff={}), backlog=FakeBacklog())
    with ST.db() as c:
        trail = ST.agent_note(c, row["id"])["progress"]
    assert "handoff usage: in=2 out=4 cache_creation=2715 cache_read=518 cost_usd=0.02" in trail
    assert "handoff failed" in trail


# --- lost claim -------------------------------------------------------------------------------

def test_a_lost_claim_race_exits_quietly_with_no_health_write(wa, state_dir, monkeypatch):
    with ST.db() as c:
        _note(c)
    monkeypatch.setattr(ST, "claim_agent_note", lambda c, note_id: False)

    rc = _run(state_dir, runner=ExplodingCLIThatMustNotRun(), backlog=ExplodingBacklog())

    assert rc == 0
    assert not (state_dir / "health.json").exists()


# --- pure text-building helpers ------------------------------------------------------------------

def test_build_decode_prompt_labels_a_voice_note_and_adds_the_transcription_caveat():
    note = {"id": 1, "body": "текст", "kind": "audio", "phone": PHONE, "created_at": NOW.isoformat()}
    prompt = W.build_decode_prompt(note, [], {}, [])
    assert "transcribed voice note" in prompt
    assert "garbled" in prompt
    assert "текст" in prompt


def test_build_decode_prompt_does_not_call_a_typed_note_a_voice_note():
    note = {"id": 1, "body": "текст", "kind": "text", "phone": PHONE, "created_at": NOW.isoformat()}
    prompt = W.build_decode_prompt(note, [], {}, [])
    assert "transcribed voice note" not in prompt


def test_build_card_description_quotes_the_verbatim_russian_and_the_decoded_request():
    note = {"id": 5, "wamid": "wamid.5", "body": "Первая строка\nВторая строка", "kind": "text",
           "phone": PHONE, "created_at": NOW.isoformat()}
    desc = W.build_card_description(note, DECODE_OK, [], {}, [])
    assert "> Первая строка" in desc and "> Вторая строка" in desc
    assert DECODE_OK["request_en"] in desc
    assert "## Origin" in desc and f"#{note['id']}" in desc
    assert "Note wamid: wamid.5" in desc   # TASK-303 item A3: crash recovery matches on this line


def test_card_json_and_messages_are_truncated_to_their_stated_limits():
    huge_card = {"slots": {"x": "y" * 5000}}
    text = W._format_card_json(huge_card)
    assert len(text) <= W._CARD_JSON_MAX

    long_messages = [{"at": NOW.isoformat(), "direction": "in", "kind": "text", "body": "z" * 1000}]
    rendered = W._format_messages(long_messages)
    assert len(rendered.splitlines()[0]) <= W._MESSAGE_BODY_MAX + 40   # + the "[at] dir kind: " prefix


def test_handoff_prompt_forbids_sending_to_anyone_but_the_exact_target():
    prompt = W.build_handoff_prompt("wa-harness", "the message")
    assert "'wa-harness'" in prompt
    assert "exact match" in prompt
    assert "the message" in prompt


def test_priority_mapping_matches_the_card_exactly():
    assert W._PRIORITY_BY_URGENCY == {"now": "high", "today": "medium", "whenever": "low"}


# --- envelope parsing and validation --------------------------------------------------------------

def test_extract_structured_output_prefers_the_native_dict_not_the_json_string():
    envelope = {"is_error": False, "result": json.dumps({"a": 1}), "structured_output": {"a": 1}}
    proc = subprocess.CompletedProcess(["x"], 0, stdout=json.dumps(envelope), stderr="")
    assert W._extract_structured_output(proc) == {"a": 1}


def test_extract_structured_output_raises_on_is_error_envelope():
    envelope = {"is_error": True, "result": "budget exceeded"}
    proc = subprocess.CompletedProcess(["x"], 0, stdout=json.dumps(envelope), stderr="")
    with pytest.raises(RuntimeError, match="budget exceeded"):
        W._extract_structured_output(proc)


def test_extract_structured_output_raises_on_missing_structured_output():
    envelope = {"is_error": False, "result": "{}"}
    proc = subprocess.CompletedProcess(["x"], 0, stdout=json.dumps(envelope), stderr="")
    with pytest.raises(RuntimeError, match="structured_output"):
        W._extract_structured_output(proc)


def test_validate_handoff_rejects_a_bool_disguised_as_the_matches_int():
    """isinstance(True, int) is True in Python -- a real footgun for a schema that says "integer"."""
    with pytest.raises(RuntimeError):
        W._validate_handoff({"delivered": True, "matches": True, "peer_names": [], "error": None})


def test_parse_created_task_id_matches_the_real_backlog_task_create_plain_output():
    """The exact shape observed live, 2026-09-25 build: 'File: ...\\n\\nTask TASK-304 - title\\n===...'"""
    stdout = ("File: /home/claude/repo/pflege-board/backlog/tasks/task-304 - x.md\n\n"
             "Task TASK-304 - zzz-scratch-probe-delete-me\n==========\n")
    assert W._parse_created_task_id(stdout) == "TASK-304"


def test_parse_created_task_id_returns_none_for_unrecognized_output():
    assert W._parse_created_task_id("nothing recognizable here") is None
