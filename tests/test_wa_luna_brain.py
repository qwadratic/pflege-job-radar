"""The Claude-driven WhatsApp brain (app/wa/luna_brain.py, WA_BRAIN=luna): the code-enforced
gates (opt-out, qualification reject, out-of-scope region), the CLI call contract, and the
webhook wiring. No network and no `claude` subprocess: the model is a fake ``reply`` callable
injected into ``luna_brain.Client``, same seam as ``app/wa/meta.py``'s fake transport.
"""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST

LEAD = "+491701234567"


def _jobs():
    return [
        {"posting_id": 1, "title": "Pflegefachkraft Intensivstation", "role_class": "pflegefachkraft",
         "department_hint": "Intensiv/IMC", "city": "München", "clinic_town": "München",
         "regierungsbezirk": "Oberbayern", "clinic_name": "Klinikum München Nord",
         "employer": "Klinikum München Nord", "employment_types": ["vollzeit"],
         "enr_housing": True, "verify_status": "live", "status": "open",
         "first_published": "2026-09-01", "fresh": True, "source_url": "https://example.org/job/1"},
        {"posting_id": 2, "title": "Pflegefachkraft OP", "role_class": "pflegefachkraft",
         "department_hint": "OP", "city": "Würzburg", "clinic_town": "Würzburg",
         "regierungsbezirk": "Unterfranken", "clinic_name": "Klinikum Würzburg",
         "employer": "Klinikum Würzburg", "employment_types": ["teilzeit"],
         "enr_housing": False, "verify_status": "live", "status": "open",
         "first_published": "2026-09-02", "fresh": True, "source_url": "https://example.org/job/2"},
    ]


@pytest.fixture()
def luna(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    return {"slots": {}, "asked": []}


def _out(**kw):
    """A minimally valid reply_turn dict, overridable per test."""
    base = {"action": "reply_now_conversational", "bubbles": ["Hallo 🙂"], "rationale": "",
            "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


def fake_client(out_or_fn):
    """A Client whose reply() returns a fixed dict, or calls a function of (system, user)."""
    fn = out_or_fn if callable(out_or_fn) else (lambda system, user: out_or_fn)
    return LB.Client(reply=fn)


# --- gates enforced in code, never reaching the model ----------------------------------------

def test_stop_never_reaches_the_model(luna):
    calls = []
    d = LB.turn("STOP", luna, client=fake_client(lambda s, u: calls.append(1) or _out()))
    assert d["stopped"] is True and d["bubbles"] == [] and calls == []


def test_out_of_scope_region_never_reaches_the_model(luna):
    calls = []
    d = LB.turn("ich suche in Berlin", luna, client=fake_client(lambda s, u: calls.append(1) or _out()))
    assert calls == [], "a locked out-of-scope reply needs no model call"
    assert d["action"] == "out_of_scope_region"
    assert "Bayern" in d["bubbles"][0] and d["slots"]["region"] == "berlin"


def test_out_of_scope_region_is_whole_word(luna):
    assert LB.named_non_bavaria_land("ich mag Hessendorf") is None
    assert LB.named_non_bavaria_land("NRW-Fan-Artikel") is None
    assert LB.named_non_bavaria_land("ich suche in Hessen") == "hessen"
    assert LB.named_non_bavaria_land("Baden-Württemberg bitte") == "baden-württemberg"


def test_a_region_already_on_the_card_is_not_re_gated(luna):
    """Once Bayern is recorded, a later off-topic mention of another Land must not derail the chat."""
    luna["slots"]["region"] = "Bayern"
    d = LB.turn("mein Bruder wohnt in Hessen", luna, client=fake_client(_out(bubbles=["ok"])))
    assert d["action"] != "out_of_scope_region"


# --- the qualification-reject gate: locked wording, not the model's own phrasing --------------

def test_first_disqualification_uses_the_locked_reject_text_not_the_models_wording(luna):
    model_said = "Wir können es trotzdem versuchen, kein Problem!"
    out = _out(bubbles=[model_said], card_patch={"qualification_ok": False, "qualification_path": "reject"})
    d = LB.turn("ich bin Pflegehelferin", luna, client=fake_client(out))
    assert d["bubbles"] == [LB.P.REJECT_BODY_DE]
    assert model_said not in d["bubbles"]
    assert d["action"] == "explain_not_placeable"
    assert d["slots"]["qualification_ok"] is False


def test_disqualification_is_only_overridden_once(luna):
    """Already-rejected + still rejected must not keep clobbering the model's own wording."""
    luna["slots"]["qualification_ok"] = False
    out = _out(bubbles=["Wie besprochen können wir Ihnen leider nicht helfen."],
               card_patch={"qualification_ok": False})
    d = LB.turn("und jetzt?", luna, client=fake_client(out))
    assert d["bubbles"] == out["bubbles"]


# --- a normal turn: the model decides, the harness only supplies state -----------------------

def test_a_normal_turn_updates_the_card_from_card_patch(luna):
    out = _out(bubbles=["Welche Region interessiert Sie?"],
               card_patch={"region": "Bayern", "role_verdict": "unclear"})
    d = LB.turn("Hallo", luna, client=fake_client(out))
    assert d["slots"]["region"] == "Bayern" and d["slots"]["role_verdict"] == "unclear"
    assert d["bubbles"] == out["bubbles"]
    assert d["stopped"] is False


def test_no_send_clears_the_bubbles_but_still_updates_the_card(luna):
    out = _out(bubbles=["would have said something"], no_send=True, card_patch={"region": "Bayern"})
    d = LB.turn("ok danke", luna, client=fake_client(out))
    assert d["bubbles"] == []
    assert d["slots"]["region"] == "Bayern"


def test_escalation_is_recorded_on_the_card_and_still_sends_the_next_ask(luna):
    out = _out(bubbles=["Gute Frage, das gebe ich weiter. Und wo suchen Sie?"],
               escalate_to_manager=True, escalate_reason="asked about visa specifics")
    d = LB.turn("wie ist das mit dem Visum?", luna, client=fake_client(out))
    assert d["slots"]["_escalated"] is True
    assert d["slots"]["_escalate_reason"] == "asked about visa specifics"
    assert d["bubbles"] == out["bubbles"], "escalating must not mean going silent"


def test_the_model_receives_the_market_snapshot_and_scoreboard(luna):
    seen = {}

    def capture(system_text, user_text):
        seen["system"] = system_text
        seen["user"] = json.loads(user_text)
        return _out()

    luna["slots"] = {"city": "München"}
    LB.turn("Intensivstation bitte", luna, client=fake_client(capture))
    assert seen["user"]["market_snapshot"]["open_jobs"] == 2
    assert any(c["city"] == "München" for c in seen["user"]["market_snapshot"]["consult"])
    assert seen["user"]["requirement_scoreboard"]["region"] == "open"
    assert seen["user"]["latest_inbound"] == "Intensivstation bitte"
    assert "Valentina" in seen["system"]
    assert "NDT" not in seen["system"], "the vendored prompt must not carry the source's company name"


def test_market_snapshot_matches_only_once_city_and_role_or_department_are_known():
    empty = LB.market_snapshot({})
    assert empty["matches"] == [] and empty["consult"] != []
    narrowed = LB.market_snapshot({"city": "München", "qualification_path": "urkunde"})
    assert narrowed["matches"] and all(m["city"] == "München" for m in narrowed["matches"])


def test_requirement_scoreboard_reflects_the_card():
    assert LB.requirement_scoreboard({})["qualification"] == "open"
    assert LB.requirement_scoreboard({"qualification_path": "urkunde"})["qualification"] == "satisfied"
    assert LB.requirement_scoreboard({"qualification_path": "reject"})["qualification"] == "blocked"
    assert LB.requirement_scoreboard({"region": "Bayern"})["region"] == "satisfied"


# --- output validation: fail loudly, do not guess ---------------------------------------------

def test_missing_required_keys_raises(luna):
    bad = {"bubbles": ["hi"]}  # no action, no escalate_to_manager, no no_send, no card_patch
    with pytest.raises(RuntimeError, match="missing required keys"):
        LB.turn("Hallo", luna, client=fake_client(bad))


def test_card_patch_must_be_an_object(luna):
    bad = _out(card_patch="not an object")
    with pytest.raises(RuntimeError, match="card_patch must be an object"):
        LB.turn("Hallo", luna, client=fake_client(bad))


def test_too_many_bubbles_is_rejected(luna):
    out = _out(bubbles=["one", "two", "three"])
    with pytest.raises(AssertionError, match="style rule"):
        LB.turn("Hallo", luna, client=fake_client(out))


def test_an_empty_bubble_is_rejected(luna):
    out = _out(bubbles=[""])
    with pytest.raises(AssertionError, match="empty bubble"):
        LB.turn("Hallo", luna, client=fake_client(out))


# --- the fence-stripping and validation helpers, directly ---------------------------------------

def test_strip_fence_removes_a_markdown_json_fence():
    fenced = '```json\n{"a": 1}\n```'
    assert LB._strip_fence(fenced) == '{"a": 1}'
    assert LB._strip_fence('{"a": 1}') == '{"a": 1}'


def test_validate_passes_through_a_well_formed_reply():
    out = _out()
    assert LB._validate(out) is out


# --- the CLI subprocess call itself: mocked at the subprocess boundary ------------------------

class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_live_reply_builds_the_expected_command_and_parses_result(monkeypatch):
    captured = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured["cmd"], captured["input"], captured["timeout"] = cmd, input, timeout
        return _FakeCompleted(stdout=json.dumps({"is_error": False, "result": json.dumps(_out())}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = LB.Client()
    out = client._live_reply("SYSTEM TEXT", "USER TEXT")
    assert out["action"] == "reply_now_conversational"
    cmd = captured["cmd"]
    assert cmd[0] == C.LUNA_CLAUDE_BIN and "-p" in cmd and "--restricted" in cmd
    assert "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "SYSTEM TEXT"
    assert captured["input"] == "USER TEXT", "the user payload goes over stdin, not argv"
    assert captured["timeout"] == C.LUNA_TIMEOUT_SEC


def test_live_reply_strips_a_markdown_fence_around_the_result(monkeypatch):
    fenced_result = "```json\n" + json.dumps(_out()) + "\n```"
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _FakeCompleted(stdout=json.dumps({"is_error": False, "result": fenced_result})))
    out = LB.Client()._live_reply("s", "u")
    assert out["action"] == "reply_now_conversational"


def test_live_reply_raises_when_the_cli_is_not_installed(monkeypatch):
    def raise_not_found(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", raise_not_found)
    with pytest.raises(RuntimeError, match="not on PATH"):
        LB.Client()._live_reply("s", "u")


def test_live_reply_raises_on_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(RuntimeError, match="did not answer within"):
        LB.Client()._live_reply("s", "u")


def test_live_reply_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(returncode=1, stderr="boom"))
    with pytest.raises(RuntimeError, match="exited 1"):
        LB.Client()._live_reply("s", "u")


def test_live_reply_raises_when_stdout_is_not_json(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(stdout="not json"))
    with pytest.raises(RuntimeError, match="did not return JSON"):
        LB.Client()._live_reply("s", "u")


def test_live_reply_raises_when_the_cli_itself_reports_an_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _FakeCompleted(stdout=json.dumps({"is_error": True, "result": "quota exceeded"})))
    with pytest.raises(RuntimeError, match="reported an error"):
        LB.Client()._live_reply("s", "u")


def test_live_reply_raises_when_result_is_missing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(stdout=json.dumps({"is_error": False})))
    with pytest.raises(RuntimeError, match="no result text"):
        LB.Client()._live_reply("s", "u")


# --- wired into the webhook, end to end, brain selected by config -----------------------------

def test_webhook_uses_the_luna_brain_when_selected(luna, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    calls = []

    class FakeMeta:
        def __init__(self):
            self.sent = []

        def send_text(self, to_e164, body):
            self.sent.append(body)
            return f"wamid.out.{len(self.sent)}"

        def send_buttons(self, to_e164, body, buttons):
            return self.send_text(to_e164, body)

    def fake_reply(system, user):
        calls.append(json.loads(user))
        return _out(bubbles=["Hallo, hier antwortet die Luna-Brain 🙂"],
                    card_patch={"region": "Bayern"})

    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "t")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", "p")
    # app/wa/luna_brain.py:turn() constructs its own Client() when none is passed in (api.py
    # does not pass one) -- capture the real class before patching, so the replacement below
    # does not call itself.
    RealClient = LB.Client
    monkeypatch.setattr(LB, "Client", lambda: RealClient(reply=fake_reply))

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "p"},
        "messages": [{"id": "wamid.1", "from": "491701234567", "type": "text",
                     "text": {"body": "Hallo"}}]}}]}]}
    wa = FakeMeta()
    out = WAPI.handle_payload(payload, client=wa)
    assert out["results"][0]["status"] == "sent"
    assert wa.sent == ["Hallo, hier antwortet die Luna-Brain 🙂"]
    assert calls, "the luna brain must have been the one consulted, not the deterministic ladder"
    with ST.db() as c:
        assert ST.thread(c, LEAD)["slots"]["region"] == "Bayern"


def test_webhook_default_config_still_uses_the_deterministic_brain(luna):
    assert C.BRAIN == "deterministic", "the default must not have flipped for every other test"
