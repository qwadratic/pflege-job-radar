"""TASK-109 offline: a phone marked as a test number. The flag and its CLI (app/wa/luna/test_threads.py),
the campaign and report exclusions, the reply path still working end to end, and the periodic history wipe
(app/wa/luna/purge_test_history.py) -- rows, document files, the Claude Code session transcript and the card,
never anything of another phone. tmp SQLite plus tmp document/session directories, a fake Meta client and a
fake model, synthetic numbers only; nothing here talks to Meta, the board or the real `claude` CLI.
"""
import json
import pathlib
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import asgi
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import queue as Q
from app.wa import routing as R
from app.wa import store as ST
from app.wa.luna import campaign as CAMP
from app.wa.luna import catchup as CU
from app.wa.luna import purge_test_history as PURGE
from app.wa.luna import reporting as REP
from app.wa.luna import shadow_run as SR
from app.wa.luna import test_threads as TT

PHONE_ID = "109000111"
TEST_PHONE = "+4915550109001"     # the operator's own number
REAL_PHONE = "+4915550109002"     # a real candidate, documents in the same directory tree
CAMPAIGN = "bayern-test-109"

TEMPLATE = {"id": "1090000000000001", "name": "synthetic_test_109_de", "language": "de", "status": "APPROVED",
            "category": "MARKETING", "parameter_format": "POSITIONAL",
            "components": [{"type": "BODY", "text": "Hallo, {{1}}. Haben Sie noch Interesse?",
                            "example": {"body_text": [["Frau Muster"]]}}]}


def _clinics():
    return [{"clinic_id": "c1", "name": "Klinikum München", "town": "München", "regierungsbezirk": "Oberbayern",
             "beds": 800, "jobs_open": 1, "fachrichtungen": []}]


def _jobs():
    return [{"posting_id": 1, "clinic_id": "c1", "role_class": "pflegefachkraft", "department_hint": "Intensiv/IMC",
             "qualification_hint": None, "title": "Pflegefachkraft Intensiv", "clinic_name": "Klinikum München",
             "employer": "Klinikum München", "city": "München", "clinic_town": "München",
             "regierungsbezirk": "Oberbayern", "employment_types": ["vollzeit"], "enr_housing": False,
             "status": "open", "verify_status": "live", "first_published": "2026-09-01"}]


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": _clinics(),
                    "by_clinic": {c["clinic_id"]: c for c in _clinics()}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "db" / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "LUNA_SESSION_STORE", tmp_path / "claude" / "projects")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    return tmp_path


class FakeMeta:
    def __init__(self):
        self.sent = []

    def send_text(self, to_e164, body):
        self.sent.append((to_e164, body))
        return f"wamid.out.{len(self.sent)}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


class Model:
    """Stands in for the claude CLI: answers with the queued outputs in order."""

    def __init__(self, monkeypatch, *outs):
        self.outs, self.payloads = list(outs), []
        real = LB.Client
        monkeypatch.setattr(LB, "Client", lambda: real(reply=self._reply))

    def _reply(self, system, user, session_id):
        self.payloads.append(json.loads(user))
        return self.outs.pop(0), session_id or "session-model"


def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Guten Tag! Wo möchten Sie arbeiten?"],
            "rationale": "", "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


def _deliver(client, phone, wamid, text="Hallo"):
    body = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
        "messages": [{"id": wamid, "from": phone[1:], "type": "text", "text": {"body": text}}]}}]}]}
    return WAPI.handle_payload(body, client=client)["results"]


def _transcript(session_id, folder="-home-repo-data-wa-luna-sessions"):
    """One Claude Code session transcript, where the CLI would write it: a directory named after the cwd
    the session started in, one <session id>.jsonl inside."""
    path = pathlib.Path(C.LUNA_SESSION_STORE) / folder
    path.mkdir(parents=True, exist_ok=True)
    file = path / f"{session_id}.jsonl"
    file.write_text('{"type":"user","message":"Hallo, ich bin Testperson"}\n', encoding="utf-8")
    return file


def _seed(phone, session_id, **card):
    """A thread with something in every table this harness keys by phone, one stored document with its
    real file, its session transcript, an ownership record and a consent-queue entry.
    -> {"document": path of the stored file, "transcript": path of the session transcript}."""
    slots = {"city": "München", "qualification_path": "urkunde", "_session_id": session_id, **card}
    with ST._lock, ST.db() as c:
        t = ST.thread(c, phone)
        t["slots"].update(slots)
        t["turns"], t["last_inbound_at"], t["last_outbound_at"] = 3, ST.now_iso(), ST.now_iso()
        ST.save_thread(c, t)
        ST.record_inbound(c, phone, f"wamid.in.{phone}", "Hallo, ich suche eine Stelle")
        ST.record_outbound(c, phone, f"wamid.out.{phone}", "Guten Tag!")
        ST.record_inbound_pending(c, phone, f"wamid.pending.{phone}", "und noch etwas")
        ST.record_luna_call(c, phone)
        ST.record_send_failure(c, phone, "Meta was unreachable")
        ST.record_followup_sent(c, phone, 0)
        ST.claim_nudge(c, phone, "followup:0:anchor")
        ST.claim_reply_turn(c, phone, f"wamid.in.{phone}")
        ST.finish_reply_turn_claim(c, phone, f"wamid.in.{phone}", "sent")
        ST.record_message_status(c, phone, {"id": f"wamid.out.{phone}", "status": "delivered",
                                            "timestamp": "1789000000", "recipient_id": phone[1:]})
        ST.record_webhook_event(c, phone, "messages", "user_preferences",
                                {"category": "marketing_messages", "value": "resume"})
        ST.record_imported_message(c, phone, "old-system", f"{phone}-1", "in", "text", "früher geschrieben",
                                   "2026-01-01T09:00:00+00:00")
        c.commit()
        path = WAPI._write_original(phone, f"media{phone[-3:]}", b"%PDF-1.4 synthetic", ".pdf")
        ST.record_document(c, phone, f"wamid.doc.{phone}", f"media{phone[-3:]}", "document", "application/pdf",
                           "lebenslauf.pdf", str(path), "sha-" + phone[-3:], 18)
        ST.ensure_campaign_schema(c)
        attempt = ST.claim_campaign_send(c, CAMPAIGN, phone, TEMPLATE, {"body": ["Frau Test"]}, "Hallo, Frau Test.",
                                         None, ST.now_iso())
        ST.finish_campaign_send(c, CAMPAIGN, phone, attempt, "sent", ST.now_iso(), wamid=f"wamid.camp.{phone}")
        c.commit()
    with R.db() as c:
        c.execute("insert into wa_ownership (phone, owner, reason, since) values (?,?,?,?)",
                  (phone, "us", "new_lead", ST.now_iso()))
        c.commit()
    Q.build_queue_entry(phone, slots)
    return {"document": path, "transcript": _transcript(session_id)}


def _thread(phone):
    with ST.db() as c:
        return ST.thread(c, phone)


def _counts(phone):
    with ST.db() as c:
        return PURGE._row_counts(c, phone)


def _files():
    root = pathlib.Path(C.DOCUMENTS_DIR)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _transcripts():
    root = pathlib.Path(C.LUNA_SESSION_STORE)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.jsonl")) if root.exists() else []


def _queue_phones():
    conn = Q.db()
    try:
        return [r["phone"] for r in Q.queue_rows(conn)], [r["phone"] for r in Q.mailing_list_rows(conn)]
    finally:
        conn.close()


def _state():
    """Everything the wipe could possibly change, for a before/after comparison."""
    return {"test": _counts(TEST_PHONE), "real": _counts(REAL_PHONE), "files": _files(),
            "transcripts": _transcripts(), "test_card": _thread(TEST_PHONE)["slots"],
            "real_card": _thread(REAL_PHONE)["slots"], "queue": _queue_phones()}


# --- the flag and its CLI ------------------------------------------------------------------------------------

def test_the_cli_marks_and_unmarks_a_number_and_a_card_save_never_flips_it(wa, capsys):
    assert TT.main(["--mark", TEST_PHONE]) == 0
    t = _thread(TEST_PHONE)
    assert t["is_test"] is True and t["test_marked_at"]
    assert TEST_PHONE in capsys.readouterr().out

    with ST.db() as c:                      # an ordinary turn saving the card leaves the flag alone
        t["slots"]["city"] = "Augsburg"
        ST.save_thread(c, t)
    assert _thread(TEST_PHONE)["is_test"] is True

    assert TT.main(["--list"]) == 0
    assert TEST_PHONE in capsys.readouterr().out

    assert TT.main(["--unmark", TEST_PHONE]) == 0
    after = _thread(TEST_PHONE)
    assert after["is_test"] is False and after["test_marked_at"] is None
    assert after["slots"]["city"] == "Augsburg", "unmarking is not a wipe"
    assert TT.main(["--list"]) == 0 and "0 test number(s)" in capsys.readouterr().out


def test_marking_a_number_that_never_wrote_opens_its_thread_already_flagged(wa):
    assert TT.main(["--mark", "015550109003"]) == 0        # not yet +E.164: canonicalized before it is written
    with ST.db() as c:
        assert ST.test_phones(c) == ["+4915550109003"]


def test_the_cli_refuses_something_that_is_not_a_phone_number(wa, capsys):
    assert TT.main(["--mark", "not a number"]) == 2
    assert "did not canonicalize" in capsys.readouterr().err
    with ST.db() as c:
        assert ST.test_phones(c) == [] and c.execute("select count(*) as n from wa_threads").fetchone()["n"] == 0


def test_get_api_wa_threads_shows_the_flag_and_counts_the_test_numbers(wa):
    _seed(TEST_PHONE, "session-test")
    _seed(REAL_PHONE, "session-real")
    TT.main(["--mark", TEST_PHONE])
    body = TestClient(asgi.app).get("/api/wa/threads").json()
    assert body["total"] == 2 and body["test_threads"] == 1
    assert {r["phone"]: r["is_test"] for r in body["rows"]} == {TEST_PHONE: True, REAL_PHONE: False}
    one = TestClient(asgi.app).get("/api/wa/threads", params={"phone": TEST_PHONE}).json()
    assert one["thread"]["is_test"] is True and one["thread"]["test_marked_at"]


# --- campaign and reports ------------------------------------------------------------------------------------

def _leads(*phones):
    rows = [{"line": i + 2, "raw_phone": p, "variables": {"body.1": "Frau Test"}} for i, p in enumerate(phones)]
    return CAMP.resolve_leads(rows, TEMPLATE, None)


class NeverSends:
    def send_template(self, *args, **kwargs):
        raise AssertionError("a campaign template must never be posted to a test number")


def test_the_campaign_skips_a_test_number_in_the_plan_and_in_the_send(wa):
    TT.main(["--mark", TEST_PHONE])
    leads = _leads(TEST_PHONE, REAL_PHONE)
    with CAMP.live_db() as c:
        planned = CAMP.plan(c, CAMPAIGN, leads, CAMP.Retry())
    assert [lead["action"] for lead in planned] == ["skip_test_number", "send"]
    assert "test number (marked" in planned[0]["reason"] and "test_threads --unmark" in planned[0]["reason"]

    # --send re-decides per phone; every retry flag set, so no path can get past the flag either
    result = CAMP.send_one(CAMPAIGN, TEMPLATE, planned[0], NeverSends(), lambda: datetime.now(timezone.utc),
                           CAMP.Retry(uncertain=True, delivery_failed=True), CAMP.Window("00-24", "Europe/Berlin"))
    assert result["status"] == "skip_test_number"
    with CAMP.live_db() as c:
        assert ST.campaign_attempts(c, CAMPAIGN, TEST_PHONE) == []


def test_reports_and_the_consent_queue_leave_a_test_number_out_but_name_it_when_asked(wa):
    _seed(TEST_PHONE, "session-test")
    _seed(REAL_PHONE, "session-real")
    TT.main(["--mark", TEST_PHONE])
    with ST.db() as c:
        assert REP.report_row(c, TEST_PHONE)["test"] is True
        assert REP.report_row(c, REAL_PHONE)["test"] is False
        assert SR.phones_owed_a_reply(c) == [REAL_PHONE]
    queued, mailing = _queue_phones()
    assert queued == [REAL_PHONE] and mailing == [REAL_PHONE]
    fake = LB.Client(reply=lambda system, user, session_id: (_out(), session_id))
    assert SR.run(phones=[TEST_PHONE], client=fake)[0]["test"] is True, "asked for by name, it is still reported"


def test_a_test_thread_is_answered_exactly_like_a_real_one(wa, monkeypatch):
    TT.main(["--mark", TEST_PHONE])
    Model(monkeypatch, _out(card_patch={"city": "München"}))
    meta = FakeMeta()
    [result] = _deliver(meta, TEST_PHONE, "wamid.in.live")
    assert result["status"] == "sent"
    assert meta.sent == [(TEST_PHONE, "Guten Tag! Wo möchten Sie arbeiten?")]
    t = _thread(TEST_PHONE)
    assert t["slots"]["city"] == "München" and t["is_test"] is True


def test_catch_up_answers_a_test_thread_whose_owed_message_has_no_pending_row(wa, monkeypatch):
    """The second reply path (deploy/pflege-wa-catchup.timer, every 3 minutes): an inbound message with no
    wa_inbound_pending row behind it -- a turn the webhook worker did not finish -- is answered from the owed
    pass. Review 2026-09-16: that pass shares shadow_run.phones_owed_a_reply with the REPORT, and dropping
    test numbers inside the shared query left the operator's own message unanswered on this run and on every
    later one, while a real candidate was retried every three minutes."""
    for phone in (TEST_PHONE, REAL_PHONE):
        with ST._lock, ST.db() as c:
            ST.save_thread(c, ST.thread(c, phone))
            ST.record_inbound(c, phone, f"wamid.owed.{phone}", "Hallo, ich habe eine Frage")
            c.commit()
    TT.main(["--mark", TEST_PHONE])
    with ST.db() as c:
        assert ST.phones_with_pending_inbound(c) == [], "no pending row: only the owed pass can answer these"
        assert SR.phones_owed_a_reply(c) == [REAL_PHONE], "the report still leaves a test number out"
        assert sorted(SR.phones_owed_a_reply(c, include_test=True)) == sorted([TEST_PHONE, REAL_PHONE])

    Model(monkeypatch, _out(), _out())
    meta = FakeMeta()
    results = CU.run(client=meta)
    assert {(r["phone"], r["status"]) for r in results} == {(TEST_PHONE, "sent"), (REAL_PHONE, "sent")}
    assert sorted(to for to, _ in meta.sent) == sorted([TEST_PHONE, REAL_PHONE])


# --- the wipe -------------------------------------------------------------------------------------------------

def _wipe(**kw):
    return PURGE.run(**kw)


def test_the_wipe_deletes_history_documents_files_session_and_card_of_a_test_thread(wa):
    seeded = _seed(TEST_PHONE, "session-test")
    real = _seed(REAL_PHONE, "session-real")
    TT.main(["--mark", TEST_PHONE])
    before = _counts(TEST_PHONE)
    assert set(before) == set(PURGE.CORE_TABLES) | {"wa_campaign_sends", "wa_queue_candidates", "wa_queue_matches"}

    report = _wipe(apply=True)
    [row] = report["phones"]
    assert row["phone"] == TEST_PHONE and row["wiped"] is True and row["problems"] == []
    assert row["deleted"] == before
    assert row["documents"] == [{"doc_id": row["documents"][0]["doc_id"], "path": str(seeded["document"]),
                                 "exists": True, "deleted": True}]
    assert row["session"]["session_id"] == "session-test"
    assert row["session"]["transcripts"] == [str(seeded["transcript"])]
    assert row["directories"] == [{"path": str(pathlib.Path(C.DOCUMENTS_DIR) / TEST_PHONE[1:]), "removed": True}]

    assert _counts(TEST_PHONE) == {}, "every phone-keyed row of the test thread is gone"
    with ST.db() as c:
        assert ST.pending_inbound(c, TEST_PHONE) == [], "a pending row left without its message would raise"
    assert not seeded["document"].exists() and not seeded["transcript"].exists()
    assert not (pathlib.Path(C.DOCUMENTS_DIR) / TEST_PHONE[1:]).exists()
    t = _thread(TEST_PHONE)
    assert t["slots"] == {} and t["asked"] == [] and t["turns"] == 0
    assert t["last_inbound_at"] is None and t["last_outbound_at"] is None
    assert t["is_test"] is True and t["test_marked_at"] and t["stopped"] is False
    with R.db() as c:
        assert R.ownership(c, TEST_PHONE)["owner"] == "us", "the ownership record survives: the next test is ours"
    assert real["document"].exists() and real["transcript"].exists()


def _transcript_exists(session_id):
    return bool(PURGE.session_files(session_id))


def test_a_stopp_typed_during_a_test_does_not_survive_the_wipe(wa):
    _seed(TEST_PHONE, "session-test")
    TT.main(["--mark", TEST_PHONE])
    with ST.db() as c:
        t = ST.thread(c, TEST_PHONE)
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.save_thread(c, t)
    _wipe(apply=True)
    t = _thread(TEST_PHONE)
    assert t["stopped"] is False and t["stopped_reason"] is None and t["is_test"] is True


def test_the_dry_run_reports_what_it_would_delete_and_writes_nothing(wa):
    seeded = _seed(TEST_PHONE, "session-test")
    _seed(REAL_PHONE, "session-real")
    TT.main(["--mark", TEST_PHONE])
    before = _state()

    report = _wipe(apply=False)
    [row] = report["phones"]
    assert report["apply"] is False and row["wiped"] is True
    assert row["deleted"] == before["test"]
    assert [f["path"] for f in row["documents"]] == [str(seeded["document"])]
    assert row["session"]["transcripts"] == [str(seeded["transcript"])]
    assert _state() == before, "a dry run deletes no row and no file"


def test_the_wipe_never_touches_a_thread_that_is_not_marked(wa):
    _seed(TEST_PHONE, "session-test")
    _seed(REAL_PHONE, "session-real")
    TT.main(["--mark", TEST_PHONE])
    before_real = {"counts": _counts(REAL_PHONE), "card": _thread(REAL_PHONE)["slots"],
                   "files": _files(), "queue": _queue_phones()}

    _wipe(apply=True)
    after_real = {"counts": _counts(REAL_PHONE), "card": _thread(REAL_PHONE)["slots"]}
    assert after_real["counts"] == before_real["counts"] and after_real["card"] == before_real["card"]
    assert _files() == [f for f in before_real["files"] if f.startswith(REAL_PHONE[1:])]
    assert _transcript_exists("session-real")
    assert _queue_phones() == before_real["queue"]

    # naming it explicitly is a loud problem, not a wipe
    report = _wipe(apply=True, phones=[REAL_PHONE])
    [row] = report["phones"]
    assert row["wiped"] is False and row["reason"] == "not a test number" and report["problems"] == 1
    assert _counts(REAL_PHONE) == before_real["counts"]
    assert PURGE.main(["--apply", "--phones", REAL_PHONE]) == 1
    assert PURGE.main(["--apply", "--phones", "not a number"]) == 2, "a typo never silently wipes nothing"


def test_a_session_the_store_does_not_hold_is_reported_and_the_rest_is_still_wiped(wa):
    """A card naming a session whose transcript is not under C.LUNA_SESSION_STORE means the conversation
    stays readable on disk somewhere else -- the operator has to hear about it (exit 1)."""
    seeded = _seed(TEST_PHONE, "session-test")
    seeded["transcript"].unlink()
    TT.main(["--mark", TEST_PHONE])

    report = _wipe(apply=True)
    [row] = report["phones"]
    assert row["wiped"] is True and report["problems"] == 1
    assert "session session-test has no transcript under" in row["problems"][0]
    assert _counts(TEST_PHONE) == {} and not seeded["document"].exists()


def test_a_second_wipe_of_the_same_thread_changes_nothing(wa):
    _seed(TEST_PHONE, "session-test")
    TT.main(["--mark", TEST_PHONE])
    _wipe(apply=True)
    state = _state()

    report = _wipe(apply=True)
    [row] = report["phones"]
    assert row["wiped"] is True and row["deleted"] == {} and row["documents"] == []
    assert row["session"] == {"session_id": None, "store": str(C.LUNA_SESSION_STORE), "transcripts": []}
    assert _state() == state


def test_retention_leaves_a_test_thread_that_is_still_being_used_alone(wa):
    _seed(TEST_PHONE, "session-test")
    TT.main(["--mark", TEST_PHONE])
    before = _state()

    report = _wipe(apply=True, older_than_hours=2)
    [row] = report["phones"]
    assert row["wiped"] is False and "is newer than" in row["reason"] and row["problems"] == []
    assert _state() == before

    with ST.db() as c:     # the same thread, last touched three hours ago
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0).isoformat()
        c.execute("update wa_messages set at=? where phone=?", (old, TEST_PHONE))
        c.execute("update wa_documents set received_at=? where phone=?", (old, TEST_PHONE))
        c.execute("update wa_threads set last_inbound_at=?, last_outbound_at=? where phone=?",
                  (old, old, TEST_PHONE))
        c.commit()
    assert _wipe(apply=True, older_than_hours=2)["phones"][0]["wiped"] is True
    assert _counts(TEST_PHONE) == {}


def test_a_thread_whose_turn_is_in_flight_is_left_for_the_next_run(wa):
    _seed(TEST_PHONE, "session-test")
    TT.main(["--mark", TEST_PHONE])
    with ST.db() as c:
        ST.claim_reply_turn(c, TEST_PHONE, "wamid.in.flight")
    before = _state()

    report = _wipe(apply=True)
    [row] = report["phones"]
    assert row["wiped"] is False and "in flight" in row["reason"]
    assert _state() == before


def test_no_other_process_can_claim_a_turn_inside_the_wipes_own_window(wa, monkeypatch):
    """ST._lock is in-process only, and the purge runs as its own systemd unit next to pflege-wa.service and
    the 3-minute catch-up timer. So the in-flight check reads inside the wipe's own `begin immediate`
    transaction (review 2026-09-16): a turn claimed a millisecond after the check would otherwise save the
    pre-wipe card back over the wipe, leaving exactly the card-without-messages state the check exists to
    prevent. Here a second connection -- another process -- tries to claim in that window and cannot."""
    _seed(TEST_PHONE, "session-test")
    TT.main(["--mark", TEST_PHONE])
    attempts = []
    plan = PURGE._document_plan

    def racing_plan(c, phone):        # runs after the in-flight check, before the first delete
        other = sqlite3.connect(C.SQLITE_PATH, timeout=0)
        other.row_factory = sqlite3.Row
        try:
            ST.claim_reply_turn(other, phone, "wamid.in.race")
            attempts.append("claimed")
        except sqlite3.OperationalError as exc:
            attempts.append(str(exc))
        finally:
            other.close()
        return plan(c, phone)

    monkeypatch.setattr(PURGE, "_document_plan", racing_plan)
    [row] = _wipe(apply=True)["phones"]
    assert attempts == ["database is locked"], "the wipe holds the write lock across its own in-flight check"
    assert row["wiped"] is True and _counts(TEST_PHONE) == {}


def test_the_cli_prints_a_summary_and_exits_zero(wa, capsys):
    _seed(TEST_PHONE, "session-test")
    TT.main(["--mark", TEST_PHONE])
    assert PURGE.main([]) == 0
    dry = capsys.readouterr().out
    assert "1 of 1 test thread(s) would be wiped (dry run, nothing was deleted)" in dry and "full wipe" in dry
    assert _counts(TEST_PHONE) != {}

    assert PURGE.main(["--apply", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["apply"] is True and report["phones"][0]["wiped"] is True
    assert _counts(TEST_PHONE) == {}


def test_a_webhook_event_the_harness_could_not_key_to_a_phone_is_wiped_too(wa):
    """Final-verifier finding 2026-09-16: app/wa/api.py stores an unparsed webhook object with phone NULL, so
    the phone-keyed deletes left the test number readable inside its raw payload."""
    _seed(TEST_PHONE, "sess-unkeyed")
    TT.mark(TEST_PHONE, True)
    with ST._lock, ST.db() as c:
        ST.record_webhook_event(c, None, "messages", "unparsed",
                                {"messages": [{"from": TEST_PHONE[1:], "type": "sticker"}]})
        ST.record_webhook_event(c, None, "messages", "unparsed",
                                {"messages": [{"from": "491700000999", "type": "sticker"}]})
        c.commit()

    dry = PURGE.run(apply=False)
    label = "wa_webhook_events (phone null, number in payload)"
    assert dry["phones"][0]["deleted"][label] == 1, dry["phones"][0]["deleted"]

    PURGE.run(apply=True)
    with ST._lock, ST.db() as c:
        rows = c.execute("select raw from wa_webhook_events").fetchall()
    kept = [r["raw"] for r in rows]
    assert len(kept) == 1 and "491700000999" in kept[0]
    assert TEST_PHONE[1:] not in kept[0]


def test_the_session_transcript_is_gone_before_the_card_that_names_it(wa, monkeypatch):
    """The card reset erases _session_id, the only pointer to the transcript, so the unlink has to happen
    inside the same write transaction -- not after the commit (final-verifier finding 2026-09-16)."""
    seeded = _seed(TEST_PHONE, "sess-order")
    TT.mark(TEST_PHONE, True)
    seen = {}
    real_db = ST.db

    def traced_db():
        c = real_db()
        c.set_trace_callback(lambda sql: seen.setdefault("transcript_exists", pathlib.Path(seeded["transcript"]).exists())
                             if "update wa_threads set slots" in sql else None)
        return c

    monkeypatch.setattr(ST, "db", traced_db)
    PURGE.run(apply=True)
    assert seen["transcript_exists"] is False, "the transcript still existed when the card was reset"
    assert not pathlib.Path(seeded["transcript"]).exists()
