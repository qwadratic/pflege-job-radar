"""TASK-103 offline: the campaign sender (app/wa/luna/campaign.py). tmp SQLite, Meta as a fake transport behind the
real meta.Client, a frozen clock with a recording sleep, a fake Luna model. Synthetic personas and numbers only."""
import csv
import hashlib
import hmac
import json
import pathlib
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import router as ROUTER
from app.wa import routing as R
from app.wa import store as ST
from app.wa.luna import campaign as CAMP
from app.wa.luna import followups as FU
from app.wa.luna import import_history as IH

REAL_REPORT_DIR = CAMP.DEFAULT_REPORT_DIR

APP_SECRET = "test-app-secret-103"
PHONE_ID = "103000111"
CAMPAIGN = "bayern-test-2026-09"
TEMPLATE_ID = "1030000000000001"
BERLIN = ZoneInfo("Europe/Berlin")
LEAD_A, LEAD_B, LEAD_C = "+4915550103001", "+4915550103002", "+4915550103003"
KNOWN = "+4915550103009"   # in the known-phones file: the real system's candidate

TEMPLATE = {"id": TEMPLATE_ID, "name": "synthetic_bayern_interesse_de", "language": "de", "status": "APPROVED",
            "category": "MARKETING", "parameter_format": "POSITIONAL", "components": [
                {"type": "HEADER", "format": "TEXT", "text": "Neue Stellen in Bayern"},
                {"type": "BODY", "text": "Hallo, {{1}}. Sie hatten sich bei uns beworben. Haben Sie noch Interesse?",
                 "example": {"body_text": [["Frau Muster"]]}},
                {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ja, ich habe Interesse"},
                                                {"type": "QUICK_REPLY", "text": "Nein, kein Interesse"}]}]}


class Crash(BaseException):
    """Stands in for the process dying mid-POST: nothing after it runs."""


class Graph:
    """Fake Meta transport behind the real meta.Client: GET answers the template; POST answers a wamid unless
    ``fail[<digits>]`` holds an exception; ``before_post(body)`` runs first."""

    def __init__(self, template=None):
        self.template = dict(template or TEMPLATE)
        self.calls, self.fail, self.before_post = [], {}, None

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "url": url, "body": body})
        if method == "GET":
            assert f"/{self.template['id']}?" in url
            return dict(self.template)
        if self.before_post:
            self.before_post(body)
        if body["to"] in self.fail:
            raise self.fail[body["to"]]
        return {"messages": [{"id": f"wamid.camp.{body['to']}.{len(self.posts)}"}]}

    @property
    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]

    def client(self):
        return M.Client(transport=self, access_token="tok", phone_number_id=PHONE_ID)


class Clock:
    def __init__(self, start):
        self.now, self.sleeps = start, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        assert len(self.sleeps) < 100, "the run keeps waiting"
        self.now += timedelta(seconds=seconds)


class FakeText:
    def __init__(self):
        self.sent = []

    def send_text(self, to_e164, body):
        self.sent.append((to_e164, body))
        return f"wamid.reply.{len(self.sent)}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "db" / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "APP_SECRET", APP_SECRET)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(C, "QUIET_HOURS_START", 0)
    monkeypatch.setattr(C, "QUIET_HOURS_END", 0)
    known = tmp_path / "known-phones.txt"
    known.write_text(KNOWN + "\n")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known))
    monkeypatch.setattr(C, "REAL_SYSTEM_WEBHOOK_URL", "https://real-system.example/webhook")
    monkeypatch.setattr(CAMP, "DEFAULT_REPORT_DIR", tmp_path / "reports")
    return tmp_path


def _leads(tmp, rows, name="leads.csv"):
    path = tmp / name
    if name.endswith(".json"):
        path.write_text(json.dumps(rows))
        return path
    columns = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return path


def _lead(phone, name="Frau Beispiel"):
    return {"phone": phone, "body.1": name}


_runs = []


def _run(tmp, graph, leads, *extra, clock=None, window="00-24"):
    clock = clock or Clock(datetime.now(timezone.utc))
    report = tmp / f"report-{len(_runs)}-{time.monotonic_ns()}.json"
    _runs.append(report)
    argv = ["--campaign-id", CAMPAIGN, "--template-id", TEMPLATE_ID, "--leads", str(leads), "--report", str(report),
            "--window", window, *extra]
    code = CAMP.main(argv, client=graph.client(), clock=clock, sleep=clock.sleep)
    return code, json.loads(report.read_text())


def _status(tmp):
    report = tmp / f"status-{time.monotonic_ns()}.json"
    assert CAMP.main(["--campaign-id", CAMPAIGN, "--status", "--report", str(report)]) == 0
    return json.loads(report.read_text())


def _by_phone(report):
    return {p["phone"] or p["raw_phone"]: p for p in report["phones"]}


def _own(phone, owner, reason, since="2026-01-01T00:00:00+00:00"):
    with R.db() as c:
        c.execute("insert into wa_ownership (phone, owner, reason, since) values (?,?,?,?)", (phone, owner, reason, since))
        c.commit()
    c.close()


def _ownership(phone):
    c = R.db()
    try:
        return R.ownership(c, phone)
    finally:
        c.close()


def _seed_thread(phone, stopped=False, **slots):
    c = ST.db()
    try:
        t = ST.thread(c, phone)
        t["slots"].update(slots)
        if stopped:
            t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.save_thread(c, t)
    finally:
        c.close()


def _claim(phone):
    with CAMP.live_db() as c:
        return ST.campaign_send(c, CAMPAIGN, phone)


def _messages(phone):
    c = ST.db()
    try:
        return ST.messages_for(c, phone)
    finally:
        c.close()


def _envelope(value):
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "4915550100000",
                                                      "phone_number_id": PHONE_ID}, **value}}]}]}


def _route(value, meta_client=None):
    body = json.dumps(_envelope(value)).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    forwarded = []
    out = ROUTER.route_webhook(body, signature, meta_client=meta_client or FakeText(),
                               forward=lambda b, h: forwarded.append(json.loads(b)))
    WAPI.wait_for_background(timeout=60)
    return out, forwarded


def _db_files(tmp):
    """The database bytes plus the size of its WAL. SQLite itself may create the empty -wal/-shm lock files when a
    read-only connection opens a WAL database nobody else has open; no data goes into them."""
    wal = tmp / "db" / "wa.sqlite-wal"
    return hashlib.sha256((tmp / "db" / "wa.sqlite").read_bytes()).hexdigest(), wal.stat().st_size if wal.exists() else 0


# --- lead file, variables, dry-run -------------------------------------------------------------------------------

def test_the_dry_run_plans_every_lead_and_writes_nothing(wa):
    _own(LEAD_B, "us", "new_lead")
    _seed_thread(LEAD_B, stopped=True)
    _seed_thread(LEAD_C, declined=True, declined_at="2026-09-10T10:00:00+00:00")
    c = ST.db()
    c.close()
    before = _db_files(wa)
    leads = _leads(wa, [_lead(LEAD_A), _lead(LEAD_B), _lead(LEAD_C), _lead(KNOWN), {"phone": "abc", "body.1": "X"},
                        {"phone": "+4915550103004", "body.1": ""}, _lead("015550103001"),
                        {"phone": "+4915550103005", "body.1": "Frau Eins"}, {"phone": "+4915550103005", "body.1": "Frau Zwei"}])
    graph = Graph()
    code, report = _run(wa, graph, leads)
    assert code == CAMP.EXIT_ATTENTION
    assert [c["method"] for c in graph.calls] == ["GET"]
    assert _db_files(wa) == before
    rows = report["phones"]
    assert [(r["line"], r["action"]) for r in rows] == [
        (2, "send"), (3, "skip_stopped"), (4, "skip_declined"), (5, "send"), (6, "invalid_phone"),
        (7, "variables_error"), (8, "duplicate"), (9, "conflicting_duplicate"), (10, "conflicting_duplicate")]
    by_line = {r["line"]: r for r in rows}
    assert by_line[2]["rendered_text"] == ("Neue Stellen in Bayern\n\nHallo, Frau Beispiel. Sie hatten sich bei uns "
                                           "beworben. Haben Sie noch Interesse?\n\n[Ja, ich habe Interesse]\n"
                                           "[Nein, kein Interesse]")
    assert by_line[2]["state"]["owner"] == {"recorded": False, "would_route_to": "us", "reason": "new_lead"}
    assert by_line[5]["state"]["owner"] == {"recorded": False, "would_route_to": "them",
                                            "reason": "known_to_real_system"}
    assert by_line[3]["state"]["owner"]["owner"] == "us" and by_line[3]["state"]["thread"]["stopped"] is True
    assert by_line[7]["problems"] == ["body: missing variable {{1}}"]
    assert by_line[8]["phone"] == LEAD_A and by_line[8]["phone_rewritten"] is True
    assert "line 2" in by_line[8]["reason"]
    assert "lines [9, 10]" in by_line[9]["reason"]
    assert report["plan_totals"]["send"] == 2 and report["template"]["status"] == "APPROVED"


def test_a_dry_run_without_a_database_creates_none(wa):
    code, report = _run(wa, Graph(), _leads(wa, [_lead(LEAD_A)]))
    assert code == CAMP.EXIT_OK and report["phones"][0]["action"] == "send"
    assert not (wa / "db").exists()


def test_values_come_from_the_lead_file_and_params_and_are_never_invented(wa):
    leads = _leads(wa, [{"phone": LEAD_A, "body": {"1": "Herr Test"}, "name": "not a template value"},
                        {"phone": LEAD_B, "body.1": "Frau Test", "buttons.0.payload": "own"},
                        {"phone": LEAD_C, "body.2": "Frau Zu Viel", "body.1": "Frau Test"}], name="leads.json")
    params = json.dumps({"buttons": {"0": {"payload": f"{CAMPAIGN}:yes"}, "1": {"payload": f"{CAMPAIGN}:no"}}})
    graph = Graph()
    code, report = _run(wa, graph, leads, "--params", params, "--send")
    rows = _by_phone(report)
    assert report["lead_file"]["ignored_columns"] == ["name"]
    assert rows[LEAD_A]["action"] == "send" and rows[LEAD_A]["result"]["status"] == "sent"
    assert rows[LEAD_B]["problems"] == ["buttons.0.payload: set in --params and in the lead file"]
    assert rows[LEAD_C]["problems"] == ["body: extra variable {{2}} not in template"]
    assert code == CAMP.EXIT_ATTENTION
    [post] = graph.posts
    assert post["body"]["to"] == LEAD_A[1:]
    assert post["body"]["template"] == {"name": TEMPLATE["name"], "language": {"code": "de"}, "components": [
        {"type": "body", "parameters": [{"type": "text", "text": "Herr Test"}]},
        {"type": "button", "sub_type": "quick_reply", "index": 0, "parameters": [{"type": "payload", "payload": f"{CAMPAIGN}:yes"}]},
        {"type": "button", "sub_type": "quick_reply", "index": 1, "parameters": [{"type": "payload", "payload": f"{CAMPAIGN}:no"}]}]}


def test_a_template_that_is_not_approved_stops_before_anything(wa):
    graph = Graph({**TEMPLATE, "status": "PAUSED"})
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    assert code == CAMP.EXIT_CONFIG and "PAUSED, not APPROVED" in report["error"]
    assert graph.posts == [] and not (wa / "db").exists()


def test_send_needs_autosend(wa, monkeypatch):
    monkeypatch.setattr(C, "AUTOSEND", False)
    graph = Graph()
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    assert code == CAMP.EXIT_CONFIG and "WA_AUTOSEND" in report["error"] and graph.calls == []


def test_one_campaign_id_is_one_template(wa):
    _run(wa, Graph(), _leads(wa, [_lead(LEAD_A)]), "--send")
    other = Graph({**TEMPLATE, "id": "1030000000000002"})
    argv = ["--campaign-id", CAMPAIGN, "--template-id", "1030000000000002", "--leads", str(wa / "leads.csv"),
            "--report", str(wa / "other.json"), "--window", "00-24"]
    assert CAMP.main(argv, client=other.client(), clock=Clock(datetime.now(timezone.utc))) == CAMP.EXIT_CONFIG
    assert "one campaign id is one template" in json.loads((wa / "other.json").read_text())["error"]
    assert other.posts == []


# --- send: claim, flip, record ----------------------------------------------------------------------------------

def test_send_flips_ownership_posts_and_records_the_thread_row_card_and_claim(wa):
    _route({"messages": [{"id": "wamid.old.1", "from": KNOWN[1:], "type": "text", "text": {"body": "Hallo"}}]})
    assert _ownership(KNOWN)["owner"] == "them"
    before = _ownership(KNOWN)
    graph = Graph()
    code, report = _run(wa, graph, _leads(wa, [_lead(KNOWN, "Frau Bekannt")]), "--send")
    assert code == CAMP.EXIT_OK and report["finished"] is True
    [post] = graph.posts
    wamid = f"wamid.camp.{KNOWN[1:]}.1"
    claim = _claim(KNOWN)
    assert (claim["state"], claim["attempts"], claim["wamid"], claim["template_id"]) == ("sent", 1, wamid, TEMPLATE_ID)
    assert (claim["prior_owner"], claim["prior_reason"], claim["prior_since"]) == ("them", "known_to_real_system",
                                                                                    before["since"])
    assert claim["variables"] == {"body": {"1": "Frau Bekannt"}}
    assert _ownership(KNOWN)["owner"] == "us" and _ownership(KNOWN)["reason"] == f"campaign:{CAMPAIGN}"
    [row] = _messages(KNOWN)
    assert (row["direction"], row["kind"], row["wamid"]) == ("out", "template", wamid)
    assert row["body"].startswith("Neue Stellen in Bayern\n\nHallo, Frau Bekannt.")
    assert row["meta"] == {"action": "campaign", "campaign_id": CAMPAIGN, "template_id": TEMPLATE_ID,
                           "template": TEMPLATE["name"], "language": "de", "variables": {"body": {"1": "Frau Bekannt"}},
                           "buttons": [{"type": "QUICK_REPLY", "text": "Ja, ich habe Interesse", "payload": None},
                                       {"type": "QUICK_REPLY", "text": "Nein, kein Interesse", "payload": None}]}
    c = ST.db()
    t = ST.thread(c, KNOWN)
    assert t["last_outbound_at"] == claim["sent_at"] == row["at"] and t["last_inbound_at"] is None
    assert t["slots"]["campaign"] == {"campaign_id": CAMPAIGN, "template_name": TEMPLATE["name"], "language": "de",
                                      "rendered_text": row["body"], "buttons": row["meta"]["buttons"],
                                      "sent_at": claim["sent_at"], "wamid": wamid}
    assert ST.reply_turn_claim_state(c, KNOWN, f"campaign:{CAMPAIGN}") == "campaign_sent"
    assert c.execute("select fingerprint from wa_nudge_claims where phone=?", (KNOWN,)).fetchall()[0][0] == \
        f"campaign:{CAMPAIGN}:1"
    c.close()


def test_a_rerun_skips_sent_phones_and_posts_nothing(wa):
    leads = _leads(wa, [_lead(LEAD_A), _lead(LEAD_B)])
    graph = Graph()
    assert _run(wa, graph, leads, "--send")[0] == CAMP.EXIT_OK
    code, report = _run(wa, graph, leads, "--send")
    assert code == CAMP.EXIT_OK and len(graph.posts) == 2
    assert {r["action"] for r in report["phones"]} == {"already_sent"}
    assert len(_messages(LEAD_A)) == 1


def test_a_phone_is_claimed_once_per_campaign(wa):
    with CAMP.live_db() as c:
        claim = lambda cid="c1", **kw: ST.claim_campaign_send(c, cid, LEAD_A, TEMPLATE, {"body": ["X"]}, "text", None,
                                                              ST.now_iso(), **kw)
        assert claim() == 1
        c.commit()
        with pytest.raises(RuntimeError, match="in_progress, not claimable"):
            claim()
        ST.finish_campaign_send(c, "c1", LEAD_A, "failed", ST.now_iso(), error="HTTP 400")
        assert claim() == 2
        ST.finish_campaign_send(c, "c1", LEAD_A, "uncertain", ST.now_iso(), error="timeout")
        with pytest.raises(RuntimeError, match="uncertain, not claimable"):
            claim()
        assert claim(retry_uncertain=True) == 3
        ST.finish_campaign_send(c, "c1", LEAD_A, "sent", ST.now_iso(), wamid="wamid.x")
        with pytest.raises(RuntimeError, match="sent, not claimable"):
            claim(retry_uncertain=True)
        with pytest.raises(RuntimeError, match="no in_progress claim"):
            ST.finish_campaign_send(c, "c1", LEAD_A, "sent", ST.now_iso(), wamid="wamid.y")
        assert claim("c2") == 1
        c.commit()


def test_a_meta_rejection_restores_the_prior_owner_and_a_rerun_retries_only_it(wa):
    _own(KNOWN, "them", "known_to_real_system", since="2026-02-02T00:00:00+00:00")
    graph = Graph()
    payload = {"error": {"message": "(#131026) Message undeliverable", "type": "OAuthException", "code": 131026}}
    graph.fail[KNOWN[1:]] = M.MetaError("Meta HTTP 400", status_code=400, payload=payload)
    graph.fail[LEAD_B[1:]] = M.MetaError("Meta HTTP 400", status_code=400, payload=payload)
    leads = _leads(wa, [_lead(KNOWN), _lead(LEAD_A), _lead(LEAD_B)])
    code, report = _run(wa, graph, leads, "--send")
    assert code == CAMP.EXIT_ATTENTION
    rows = _by_phone(report)
    assert rows[KNOWN]["result"]["status"] == "failed" and rows[KNOWN]["result"]["ownership_restore"] == "restored"
    claim = _claim(KNOWN)
    assert (claim["state"], claim["error_code"], claim["error_payload"]) == ("failed", "131026", payload)
    assert "rejected (HTTP 400)" in claim["error"] and claim["wamid"] is None
    assert _ownership(KNOWN) == {"phone": KNOWN, "owner": "them", "reason": "known_to_real_system",
                                 "since": "2026-02-02T00:00:00+00:00"}
    assert _ownership(LEAD_B) is None            # no record before: none after
    assert _messages(KNOWN) == []
    c = ST.db()
    assert c.execute("select 1 from wa_threads where phone=?", (KNOWN,)).fetchone() is None
    assert "131026" in ST.recent_send_failure(c, KNOWN)["error"]
    assert ST.reply_turn_claim_state(c, KNOWN, f"campaign:{CAMPAIGN}") == "campaign_failed"
    c.close()

    graph.fail.clear()
    code, report = _run(wa, graph, leads, "--send")
    assert code == CAMP.EXIT_OK
    assert [p["body"]["to"] for p in graph.posts] == [KNOWN[1:], LEAD_A[1:], LEAD_B[1:], KNOWN[1:], LEAD_B[1:]]
    rows = _by_phone(report)
    assert (rows[KNOWN]["action"], rows[LEAD_A]["action"]) == ("retry_failed", "already_sent")
    claim = _claim(KNOWN)
    assert (claim["state"], claim["attempts"], claim["prior_owner"]) == ("sent", 2, "them")


@pytest.mark.parametrize("error", [M.MetaError("Meta network error: timed out"),
                                   M.MetaError("Meta HTTP 503", status_code=503, payload={"error": {"code": 1}}),
                                   M.MetaError("Meta accepted the call but returned no message id", payload={}),
                                   TimeoutError("read timed out")])
def test_an_unknown_send_outcome_is_uncertain_keeps_ownership_and_is_never_resent_blindly(wa, error):
    _own(KNOWN, "them", "known_to_real_system")
    graph = Graph()
    graph.fail[KNOWN[1:]] = error
    leads = _leads(wa, [_lead(KNOWN)])
    code, report = _run(wa, graph, leads, "--send")
    assert code == CAMP.EXIT_ATTENTION and report["phones"][0]["result"]["status"] == "uncertain"
    assert _claim(KNOWN)["state"] == "uncertain" and _ownership(KNOWN)["reason"] == f"campaign:{CAMPAIGN}"
    graph.fail.clear()

    code, report = _run(wa, graph, leads, "--send")
    assert code == CAMP.EXIT_ATTENTION and report["phones"][0]["action"] == "uncertain"
    assert len(graph.posts) == 1

    code, report = _run(wa, graph, leads, "--send", "--retry-uncertain")
    assert code == CAMP.EXIT_OK and report["phones"][0]["result"]["status"] == "sent"
    claim = _claim(KNOWN)
    assert (claim["attempts"], claim["prior_owner"], claim["prior_reason"]) == (2, "them", "known_to_real_system")


def test_a_crash_between_flip_and_post_result_is_reported_uncertain_and_waits_for_its_own_claim(wa):
    graph = Graph()
    graph.fail[LEAD_A[1:]] = Crash()
    leads = _leads(wa, [_lead(LEAD_A)])
    with pytest.raises(Crash):
        _run(wa, graph, leads, "--send")
    assert _claim(LEAD_A)["state"] == "in_progress" and _ownership(LEAD_A)["reason"] == f"campaign:{CAMPAIGN}"
    graph.fail.clear()
    # Meta did take it: its status webhook arrives for a wamid we never recorded
    _route({"statuses": [{"id": "wamid.unrecorded", "status": "sent", "timestamp": "1789400000",
                          "recipient_id": LEAD_A[1:]}]})

    code, report = _run(wa, graph, leads, "--send")
    assert code == CAMP.EXIT_ATTENTION and report["phones"][0]["action"] == "uncertain"
    status = _status(wa)
    [row] = status["phones"]
    assert row["state"] == "in_progress"
    assert row["statuses_since_claim_without_message"] == [
        {"wamid": "wamid.unrecorded", "status": "sent", "at": "2026-09-14T15:33:20+00:00", "category": None}]

    code, report = _run(wa, graph, leads, "--send", "--retry-uncertain", "--no-wait")
    assert code == CAMP.EXIT_NOT_FINISHED and report["stopped"]["reason"] == "turn_in_flight"
    c = ST.db()   # the crashed run's reply-turn claim goes stale
    c.execute("update wa_reply_turn_claims set claimed_at=? where turn_key=?",
              ((datetime.now(timezone.utc) - timedelta(seconds=ST.STALE_CLAIM_SECONDS + 1)).isoformat(),
               f"campaign:{CAMPAIGN}"))
    c.commit()
    c.close()
    code, report = _run(wa, graph, leads, "--send", "--retry-uncertain")
    assert code == CAMP.EXIT_OK and len(graph.posts) == 2 and _claim(LEAD_A)["state"] == "sent"


def _stale_campaign_claim():
    c = ST.db()
    c.execute("update wa_reply_turn_claims set claimed_at=? where turn_key=?",
              ((datetime.now(timezone.utc) - timedelta(seconds=ST.STALE_CLAIM_SECONDS + 1)).isoformat(),
               f"campaign:{CAMPAIGN}"))
    c.commit()
    c.close()


def _mark_sent(tmp, graph, *pairs):
    report = tmp / f"mark-{time.monotonic_ns()}.json"
    argv = ["--campaign-id", CAMPAIGN, "--template-id", TEMPLATE_ID, "--report", str(report)]
    for pair in pairs:
        argv += ["--mark-sent", pair]
    return CAMP.main(argv, client=graph.client()), json.loads(report.read_text())


def test_an_uncertain_send_that_went_out_is_marked_sent_and_the_reply_gets_the_campaign_context(wa, monkeypatch):
    """Review 2026-09-14 repro: the POST timed out, Meta delivered it anyway; with no way to record it, the candidate's
    'Ja, ich habe Interesse' reached Luna with card.campaign None and replies_to found=false."""
    graph = Graph()
    graph.fail[LEAD_A[1:]] = TimeoutError("timed out")
    _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    assert _claim(LEAD_A)["state"] == "uncertain"
    real = "wamid.went.out"
    _route({"statuses": [
        {"id": real, "status": "sent", "timestamp": "1789400000", "recipient_id": LEAD_A[1:],
         "pricing": {"billable": True, "pricing_model": "PMP", "category": "marketing"}},
        {"id": "wamid.old.receipt", "status": "read", "timestamp": "1789400001", "recipient_id": LEAD_A[1:],
         "conversation": {"id": "c-old", "origin": {"type": "utility"}}}]})
    [row] = _status(wa)["phones"]
    assert [(u["wamid"], u["category"]) for u in row["statuses_since_claim_without_message"]] == [
        (real, "marketing"), ("wamid.old.receipt", "utility")]

    code, report = _mark_sent(wa, graph, f"{LEAD_A}=wamid.old.receipt", f"{LEAD_A}=wamid.nothing")
    assert code == CAMP.EXIT_ATTENTION and [r["status"] for r in report["results"]] == ["refused", "refused"]
    assert "category ['utility']" in report["results"][0]["reason"]
    assert "no status of wamid.nothing" in report["results"][1]["reason"]
    code, report = _mark_sent(wa, graph, f"{LEAD_A}={real}")
    assert code == CAMP.EXIT_OK and report["results"] == [
        {"phone": LEAD_A, "wamid": real, "status": "marked_sent", "sent_at": "2026-09-14T15:33:20+00:00"}]
    claim = _claim(LEAD_A)
    assert (claim["state"], claim["wamid"], claim["sent_at"]) == ("sent", real, "2026-09-14T15:33:20+00:00")
    [out] = _messages(LEAD_A)
    assert (out["wamid"], out["kind"], out["body"]) == (real, "template", claim["rendered_text"])
    assert _thread_slots(LEAD_A)["campaign"]["wamid"] == real
    assert _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")[1]["phones"][0]["action"] == "already_sent"
    assert len(graph.posts) == 1

    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    _route({"messages": [{"id": "wamid.in.yes", "from": LEAD_A[1:], "type": "button", "context": {"id": real},
                          "button": {"text": "Ja, ich habe Interesse", "payload": "Ja, ich habe Interesse"}}]})
    [payload] = model.payloads
    assert payload["card"]["campaign"]["wamid"] == real and payload["reply_context"]["replies_to"]["found"] is True
    assert [o["wamid"] for o in payload["outbound_since_last_turn"]] == [real]


def test_mark_sent_refuses_a_claim_that_may_still_be_running_or_is_already_sent(wa):
    graph = Graph()
    graph.fail[LEAD_A[1:]] = Crash()
    with pytest.raises(Crash):
        _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    _route({"statuses": [{"id": "wamid.x", "status": "sent", "timestamp": "1789400000", "recipient_id": LEAD_A[1:]}]})
    code, report = _mark_sent(wa, graph, f"{LEAD_A}=wamid.x")
    assert code == CAMP.EXIT_ATTENTION and "may still be running" in report["results"][0]["reason"]
    _stale_campaign_claim()
    assert _mark_sent(wa, graph, f"{LEAD_A}=wamid.x")[0] == CAMP.EXIT_OK
    code, report = _mark_sent(wa, graph, f"{LEAD_A}=wamid.x")
    assert code == CAMP.EXIT_ATTENTION and "(sent)" in report["results"][0]["reason"]


def test_the_webhook_worker_waits_while_a_campaign_send_is_in_flight(wa, monkeypatch):
    model = Model(monkeypatch, _out())
    graph = Graph()
    held = {}

    def reply_meanwhile(body):
        c = ST.db()
        held["in_flight"] = ST.claim_in_flight(c, LEAD_A)
        c.close()
        _route({"messages": [{"id": "wamid.in.early", "from": LEAD_A[1:], "type": "text", "text": {"body": "Hallo?"}}]})
        c = ST.db()
        held["pending"] = ST.inbound_is_pending(c, "wamid.in.early")
        c.close()

    graph.before_post = reply_meanwhile
    code, _report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    assert code == CAMP.EXIT_OK and held == {"in_flight": True, "pending": True}
    assert _thread_slots(LEAD_A)["campaign"]["wamid"] == f"wamid.camp.{LEAD_A[1:]}.1"
    replies = FakeText()
    [result] = WAPI.process_phones([LEAD_A], client=replies)     # catch-up's pass 1 once the send is recorded
    assert result["status"] == "sent" and replies.sent
    [payload] = model.payloads
    assert payload["card"]["campaign"]["campaign_id"] == CAMPAIGN


# --- skips ------------------------------------------------------------------------------------------------------

def test_stopped_declined_and_opted_out_phones_are_never_sent(wa):
    for phone in (LEAD_A, LEAD_B, LEAD_C, KNOWN):
        _own(phone, "us", "new_lead")
    _seed_thread(LEAD_A, stopped=True)
    _seed_thread(LEAD_B, declined=True, declined_at="2026-09-10T10:00:00+00:00")
    WAPI.accept_payload(_envelope({"user_preferences": [
        {"wa_id": LEAD_C[1:], "detail": "User requested to stop marketing messages", "category": "marketing_messages",
         "value": "stop", "timestamp": 1789400000}]}))
    WAPI.accept_payload(_envelope({"statuses": [
        {"id": "wamid.earlier", "status": "failed", "timestamp": "1789400000", "recipient_id": KNOWN[1:],
         "errors": [{"code": 131050, "title": "Unable to deliver the message: the user stopped marketing messages"}]}]}))
    graph = Graph()
    code, report = _run(wa, graph, _leads(wa, [_lead(p) for p in (LEAD_A, LEAD_B, LEAD_C, KNOWN)]), "--send")
    rows = _by_phone(report)
    assert [rows[p]["action"] for p in (LEAD_A, LEAD_B, LEAD_C, KNOWN)] == \
        ["skip_stopped", "skip_declined", "skip_opted_out", "skip_opted_out"]
    assert rows[LEAD_C]["state"]["opted_out"]["source"] == "user_preferences"
    assert rows[KNOWN]["state"]["opted_out"] == {"source": "failed_status", "code": "131050", "wamid": "wamid.earlier",
                                                 "timestamp": "1789400000"}
    assert code == CAMP.EXIT_OK and graph.posts == [] and report["finished"] is True
    assert [_claim(p) for p in (LEAD_A, LEAD_B, LEAD_C, KNOWN)] == [None] * 4

    WAPI.accept_payload(_envelope({"user_preferences": [
        {"wa_id": LEAD_C[1:], "category": "marketing_messages", "value": "resume", "timestamp": 1789500000}]}))
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_C)]), "--send")
    assert report["phones"][0]["result"]["status"] == "sent"


def test_a_stop_between_the_plan_and_the_send_is_honoured(wa):
    graph = Graph()

    def stop_b(body):
        if body["to"] == LEAD_A[1:]:
            _seed_thread(LEAD_B, stopped=True)

    graph.before_post = stop_b
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A), _lead(LEAD_B)]), "--send")
    rows = _by_phone(report)
    assert rows[LEAD_B]["action"] == "send" and rows[LEAD_B]["result"]["status"] == "skip_stopped"
    assert [p["body"]["to"] for p in graph.posts] == [LEAD_A[1:]] and _ownership(LEAD_B) is None


# --- pacing and window ------------------------------------------------------------------------------------------

def _berlin(*args):
    return datetime(*args, tzinfo=BERLIN).astimezone(timezone.utc)


def _phones(n):
    return [f"+49155501031{i:02d}" for i in range(n)]


def _claimed_at():
    with CAMP.live_db() as c:
        return [r["claimed_at"] for r in ST.campaign_sends(c, CAMPAIGN)]


def test_batches_follow_the_size_and_interval(wa):
    clock = Clock(_berlin(2026, 9, 15, 10, 0))
    code, report = _run(wa, Graph(), _leads(wa, [_lead(p) for p in _phones(23)]), "--send", clock=clock, window="09-21")
    assert code == CAMP.EXIT_OK
    assert clock.sleeps == [720.0, 720.0]
    t0 = clock.now - timedelta(minutes=24)
    assert _claimed_at() == [CAMP._iso(t0)] * 10 + [CAMP._iso(t0 + timedelta(minutes=12))] * 10 + \
        [CAMP._iso(t0 + timedelta(minutes=24))] * 3


def test_outside_the_window_the_run_waits_for_the_next_start(wa):
    clock = Clock(_berlin(2026, 9, 15, 20, 50))
    code, _report = _run(wa, Graph(), _leads(wa, [_lead(p) for p in _phones(15)]), "--send", clock=clock,
                         window="09-21")
    assert code == CAMP.EXIT_OK
    assert clock.sleeps == [720.0, (_berlin(2026, 9, 16, 9, 0) - _berlin(2026, 9, 15, 21, 2)).total_seconds()]
    assert _claimed_at() == ["2026-09-15T18:50:00+00:00"] * 10 + ["2026-09-16T07:00:00+00:00"] * 5

    early = Clock(_berlin(2026, 9, 17, 6, 30))
    _run(wa, Graph(), _leads(wa, [_lead(LEAD_A)]), "--send", clock=early, window="09-21")
    assert early.sleeps == [9000.0] and _claim(LEAD_A)["claimed_at"] == "2026-09-17T07:00:00+00:00"


def test_no_wait_exits_outside_the_window_and_between_batches_and_a_later_run_continues(wa):
    leads = _leads(wa, [_lead(p) for p in _phones(12)])
    graph = Graph()
    code, report = _run(wa, graph, leads, "--send", "--no-wait", clock=Clock(_berlin(2026, 9, 15, 22, 0)),
                        window="09-21")
    assert code == CAMP.EXIT_NOT_FINISHED and graph.posts == []
    assert report["stopped"] == {"reason": "outside_window", "next_at": "2026-09-16T07:00:00+00:00",
                                 "detail": "outside the window 09:00-21:00 Europe/Berlin"}

    code, report = _run(wa, graph, leads, "--send", "--no-wait", clock=Clock(_berlin(2026, 9, 16, 9, 0)),
                        window="09-21")
    assert code == CAMP.EXIT_NOT_FINISHED and len(graph.posts) == 10
    assert report["stopped"]["reason"] == "batch_interval" and report["stopped"]["next_at"] == "2026-09-16T07:12:00+00:00"

    code, report = _run(wa, graph, leads, "--send", "--no-wait", clock=Clock(_berlin(2026, 9, 16, 9, 12)),
                        window="09-21")
    assert code == CAMP.EXIT_OK and report["finished"] is True and len(graph.posts) == 12
    assert len({p["body"]["to"] for p in graph.posts}) == 12


def test_a_problem_lead_does_not_hide_that_a_no_wait_run_stopped_before_the_list_was_done(wa):
    """Repair 2026-09-14 (final verifier): one invalid lead turned every --no-wait stop into exit 1."""
    leads = _leads(wa, [{"phone": "abc", "body.1": "X"}, *(_lead(p) for p in _phones(12))])
    graph = Graph()
    code, report = _run(wa, graph, leads, "--send", "--no-wait", clock=Clock(_berlin(2026, 9, 16, 9, 0)),
                        window="09-21")
    assert code == CAMP.EXIT_NOT_FINISHED and len(graph.posts) == 10
    assert report["plan_totals"] == {"invalid_phone": 1, "send": 12} and report["stopped"]["reason"] == "batch_interval"

    code, report = _run(wa, graph, leads, "--send", "--no-wait", clock=Clock(_berlin(2026, 9, 16, 9, 12)),
                        window="09-21")
    assert code == CAMP.EXIT_ATTENTION and report["finished"] is True and len(graph.posts) == 12
    assert report["phones"][0]["action"] == "invalid_phone"


def test_an_interrupted_run_resumes_without_resending_and_keeps_the_pace(wa):
    leads = _leads(wa, [_lead(p) for p in _phones(12)])
    graph = Graph()
    clock = Clock(_berlin(2026, 9, 15, 10, 0))

    def interrupt(seconds):
        raise KeyboardInterrupt

    report_path = wa / "interrupted.json"
    argv = ["--campaign-id", CAMPAIGN, "--template-id", TEMPLATE_ID, "--leads", str(leads), "--report",
            str(report_path), "--send"]
    assert CAMP.main(argv, client=graph.client(), clock=clock, sleep=interrupt) == CAMP.EXIT_INTERRUPTED
    assert json.loads(report_path.read_text())["interrupted"] is True and len(graph.posts) == 10

    later = Clock(_berlin(2026, 9, 15, 10, 1))
    code, report = _run(wa, graph, leads, "--send", clock=later, window="09-21")
    assert code == CAMP.EXIT_OK and later.sleeps == [660.0]
    assert len(graph.posts) == 12 and len({p["body"]["to"] for p in graph.posts}) == 12
    assert report["plan_totals"] == {"already_sent": 10, "send": 2}


def _slow_import(monkeypatch, clock, minutes, calls):
    """A history import that takes ``minutes`` on the run's clock when applied (document reads, Meta downloads)."""
    class Source:
        label = "old-system"

        def close(self):
            pass

    monkeypatch.setattr(IH.Source, "open", classmethod(lambda cls, *a, **kw: Source()))

    def import_phone(source, phone, apply=False, client=None):
        calls.append((CAMP._iso(clock.now), apply))
        if apply:
            clock.now += timedelta(minutes=minutes)
        return {"phone": phone, "source": source.label, "applied": apply, "found": False, "facts": {},
                "placement": [], "messages": {"found": 0}, "documents": [], "not_recoverable": 0}

    monkeypatch.setattr(IH, "import_phone", import_phone)
    return ["--import-history-db", "/synthetic/old.sqlite", "--import-history-queries", "q.sql",
            "--import-history-source", "old-system"]


def test_the_window_is_checked_again_after_the_history_import(wa, monkeypatch):
    """Review 2026-09-14: the import ran at 20:58 Berlin, took 5 minutes, and the template went out at 21:03."""
    clock, calls, graph = Clock(_berlin(2026, 9, 15, 20, 58)), [], Graph()
    history = _slow_import(monkeypatch, clock, 5, calls)
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send", *history, clock=clock, window="09-21")
    assert code == CAMP.EXIT_OK and report["phones"][0]["result"]["status"] == "sent"
    assert clock.sleeps == [(_berlin(2026, 9, 16, 9, 0) - _berlin(2026, 9, 15, 21, 3)).total_seconds()]
    assert calls == [("2026-09-15T18:58:00+00:00", True), ("2026-09-16T07:00:00+00:00", True)]
    assert _claim(LEAD_A)["claimed_at"] == "2026-09-16T07:05:00+00:00" and len(graph.posts) == 1   # after the import


def test_no_wait_stops_when_the_window_closes_during_the_history_import(wa, monkeypatch):
    clock, calls, graph = Clock(_berlin(2026, 9, 15, 20, 59)), [], Graph()
    history = _slow_import(monkeypatch, clock, 3, calls)
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send", "--no-wait", *history, clock=clock,
                        window="09-21")
    assert code == CAMP.EXIT_NOT_FINISHED and graph.posts == []
    assert report["stopped"] == {"reason": "outside_window", "next_at": "2026-09-16T07:00:00+00:00",
                                 "detail": "outside the window 09:00-21:00 Europe/Berlin"}
    assert _claim(LEAD_A) is None and _ownership(LEAD_A) is None


def test_a_stale_claim_next_to_a_live_one_does_not_put_the_retry_in_the_past(wa):
    """Review 2026-09-14: min(claimed_at) over every in_progress claim included a crashed 'import:old' claim; the
    run then retried the phone without sleeping for as long as the live turn ran."""
    now = datetime.now(timezone.utc)
    c = ST.db()
    for key, at in (("import:old", now - timedelta(hours=2)), ("wamid.in.live", now)):
        c.execute("insert into wa_reply_turn_claims (phone, turn_key, state, claimed_at, updated_at) "
                  "values (?,?,'in_progress',?,?)", (LEAD_A, key, at.replace(microsecond=0).isoformat(),
                                                     at.replace(microsecond=0).isoformat()))
    c.commit()
    assert ST.claim_in_flight(c, LEAD_A)
    run_clock = _berlin(2026, 9, 15, 10, 0)
    until = CAMP._in_flight_until(c, LEAD_A, run_clock)
    c.close()
    assert run_clock + timedelta(seconds=ST.STALE_CLAIM_SECONDS - 5) < until <= \
        run_clock + timedelta(seconds=ST.STALE_CLAIM_SECONDS)


def test_window_arithmetic_follows_local_time():
    w = CAMP.Window("09-21", "Europe/Berlin")
    assert w.is_open(_berlin(2026, 9, 15, 9, 0)) and not w.is_open(_berlin(2026, 9, 15, 21, 0))
    assert w.next_open(_berlin(2026, 10, 24, 22, 0)) == _berlin(2026, 10, 25, 9, 0)   # DST ends that night
    assert CAMP._iso(w.next_open(_berlin(2026, 10, 24, 22, 0))) == "2026-10-25T08:00:00+00:00"
    for bad in ("21-09", "9", "09-25"):
        with pytest.raises(ValueError):
            CAMP.Window(bad, "Europe/Berlin")


# --- status, replies, Luna ----------------------------------------------------------------------------------------

def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Schön! Haben Sie die deutsche Urkunde schon?"],
            "rationale": "", "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


class Model:
    """Stands in for the claude CLI: answers with the queued outputs, records every payload."""

    def __init__(self, monkeypatch, *outs):
        self.outs, self.payloads = list(outs), []
        real = LB.Client
        monkeypatch.setattr(LB, "Client", lambda: real(reply=self._reply))

    def _reply(self, system, user, session_id):
        self.payloads.append(json.loads(user))
        return self.outs.pop(0), session_id or "session-103"


def _thread_slots(phone):
    c = ST.db()
    try:
        return ST.thread(c, phone)["slots"]
    finally:
        c.close()


def test_a_reply_to_the_campaign_reaches_luna_with_the_campaign_context(wa, monkeypatch):
    forwarded_before = _route({"messages": [{"id": "wamid.old.2", "from": KNOWN[1:], "type": "text",
                                             "text": {"body": "Hallo alter Bot"}}]})[1]
    assert len(forwarded_before) == 1 and _ownership(KNOWN)["owner"] == "them"

    graph = Graph()
    assert _run(wa, graph, _leads(wa, [_lead(KNOWN, "Frau Bekannt")]), "--send")[0] == CAMP.EXIT_OK
    wamid = f"wamid.camp.{KNOWN[1:]}.1"
    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    replies = FakeText()
    out, forwarded = _route({"messages": [{"id": "wamid.in.yes", "from": KNOWN[1:], "type": "button",
                                           "context": {"from": "4915550100000", "id": wamid},
                                           "button": {"text": "Ja, ich habe Interesse", "payload": "Ja, ich habe Interesse"}}]},
                            meta_client=replies)
    assert forwarded == [] and out["us"]["handled"] == 1
    [payload] = model.payloads
    assert payload["card"]["campaign"]["campaign_id"] == CAMPAIGN and payload["card"]["campaign"]["wamid"] == wamid
    assert [(o["kind"], o["action"], o["wamid"]) for o in payload["outbound_since_last_turn"]] == \
        [("template", "campaign", wamid)]
    assert payload["reply_context"]["is_template_button"] is True
    assert payload["reply_context"]["replies_to"]["wamid"] == wamid and payload["reply_context"]["replies_to"]["found"]
    assert replies.sent == [(KNOWN, "Schön! Haben Sie die deutsche Urkunde schon?")]
    assert _thread_slots(KNOWN)["region"] == "Bayern"


def test_status_shows_delivery_statuses_errors_and_replies(wa, monkeypatch):
    graph = Graph()
    _run(wa, graph, _leads(wa, [_lead(LEAD_A), _lead(LEAD_B), _lead(LEAD_C)]), "--send")
    graph.fail[LEAD_C[1:]] = M.MetaError("Meta HTTP 400", status_code=400,
                                         payload={"error": {"code": 131026, "message": "undeliverable"}})
    wamid_a, wamid_b = _claim(LEAD_A)["wamid"], _claim(LEAD_B)["wamid"]
    _route({"statuses": [{"id": wamid_a, "status": "sent", "timestamp": "1789400000", "recipient_id": LEAD_A[1:]},
                         {"id": wamid_a, "status": "delivered", "timestamp": "1789400005", "recipient_id": LEAD_A[1:]},
                         {"id": wamid_b, "status": "failed", "timestamp": "1789400003", "recipient_id": LEAD_B[1:],
                          "errors": [{"code": 131042, "title": "Business eligibility payment issue",
                                      "error_data": {"details": "unsettled payments"}}]}]})
    Model(monkeypatch, _out(decline=True, decline_reason="kein Interesse"))
    _route({"messages": [{"id": "wamid.in.no", "from": LEAD_A[1:], "type": "button", "context": {"id": wamid_a},
                          "button": {"text": "Nein, kein Interesse", "payload": "Nein, kein Interesse"}}]})

    report = _status(wa)
    rows = {r["phone"]: r for r in report["phones"]}
    assert rows[LEAD_A]["delivery"] == {"status": "delivered", "at": "2026-09-14T15:33:25+00:00", "errors": []}
    assert rows[LEAD_A]["replies"]["count"] == 1 and rows[LEAD_A]["replies"]["to_the_template"] is True
    assert rows[LEAD_A]["replies"]["button_taps"][0]["button_id"] == "tpl:Nein, kein Interesse"
    assert rows[LEAD_A]["declined"] is True and rows[LEAD_A]["stage"] == "declined"
    assert rows[LEAD_B]["delivery"]["status"] == "failed"
    assert rows[LEAD_B]["delivery"]["errors"] == [{"code": 131042, "title": "Business eligibility payment issue",
                                                   "details": "unsettled payments"}]
    assert report["totals"]["delivery"] == {"delivered": 1, "failed": 1, "none": 1}
    assert report["totals"]["error_codes"] == {"delivery 131042": 1}
    assert report["totals"]["replied"] == 1 and report["totals"]["declined"] == 1
    c = ST.db()
    assert "131042" in ST.recent_send_failure(c, LEAD_B)["error"]
    c.close()


def test_a_thumbs_up_on_the_template_is_a_reply_luna_answers(wa, monkeypatch):
    """Review 2026-09-14 repro: a reaction through the router was stored raw only (--status replies 0, no turn)."""
    graph = Graph()
    _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    wamid = _claim(LEAD_A)["wamid"]
    model = Model(monkeypatch, _out(card_patch={"region": "Bayern"}))
    replies = FakeText()
    out, forwarded = _route({"messages": [{"id": "wamid.in.thumb", "from": LEAD_A[1:], "type": "reaction",
                                           "reaction": {"message_id": wamid, "emoji": "👍"}}]}, meta_client=replies)
    assert forwarded == [] and out["us"]["handled"] == 1 and out["us"]["skipped"] == 0
    [payload] = model.payloads
    assert payload["reply_context"]["replies_to"]["wamid"] == wamid and payload["card"]["campaign"]["wamid"] == wamid
    assert replies.sent == [(LEAD_A, "Schön! Haben Sie die deutsche Urkunde schon?")]
    [row] = _status(wa)["phones"]
    assert row["replies"]["count"] == 1 and row["replies"]["to_the_template"] is True
    assert row["replies"]["last_text"] == "👍"


def test_a_template_meta_reports_undelivered_is_reported_not_resent_and_goes_out_under_a_new_campaign(wa):
    """Review 2026-09-14: a later failed status (131049) left the claim 'sent', so re-runs planned already_sent."""
    graph = Graph()
    _run(wa, graph, _leads(wa, [_lead(LEAD_A), _lead(LEAD_B)]), "--send")
    wamid_a = _claim(LEAD_A)["wamid"]
    _route({"statuses": [{"id": wamid_a, "status": "failed", "timestamp": "1789400003", "recipient_id": LEAD_A[1:],
                          "errors": [{"code": 131049, "title": "This message was not delivered to maintain healthy "
                                                               "ecosystem engagement."}]}]})
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A), _lead(LEAD_B)]), "--send")
    rows = _by_phone(report)
    assert code == CAMP.EXIT_ATTENTION and len(graph.posts) == 2
    assert (rows[LEAD_A]["action"], rows[LEAD_B]["action"]) == ("delivery_failed", "already_sent")
    assert "131049" in rows[LEAD_A]["reason"] and "new --campaign-id" in rows[LEAD_A]["reason"]

    retry = wa / "retry.json"
    argv = ["--campaign-id", CAMPAIGN + "-retry", "--template-id", TEMPLATE_ID, "--leads",
            str(_leads(wa, [_lead(LEAD_A)], name="retry.csv")), "--report", str(retry), "--window", "00-24", "--send"]
    assert CAMP.main(argv, client=graph.client(), clock=Clock(datetime.now(timezone.utc)), sleep=lambda s: None) == \
        CAMP.EXIT_OK
    assert [p["body"]["to"] for p in graph.posts] == [LEAD_A[1:], LEAD_B[1:], LEAD_A[1:]]
    assert _thread_slots(LEAD_A)["campaign"]["campaign_id"] == CAMPAIGN + "-retry"


class MediaText(FakeText):
    def media_url(self, media_id):
        return {"url": f"https://media.example/{media_id}", "mime_type": "audio/ogg"}

    def download_media(self, url):
        return b"OggS synthetic voice note"


def test_a_voice_note_reply_shows_as_unread_media_in_status(wa):
    graph = Graph()
    _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    replies = MediaText()
    _route({"messages": [{"id": "wamid.in.voice", "from": LEAD_A[1:], "type": "audio",
                          "audio": {"id": "media-voice", "mime_type": "audio/ogg"}}]}, meta_client=replies)
    assert replies.sent == [(LEAD_A, WAPI.MEDIA_REPLY)]
    report = _status(wa)
    [row] = report["phones"]
    assert [(u["wamid"], u["kind"]) for u in row["unread_media"]] == [("wamid.in.voice", "audio")]
    assert row["replies"]["count"] == 1 and report["totals"]["unread_media"] == 1


def test_status_of_a_rejected_send_names_the_code(wa):
    graph = Graph()
    graph.fail[LEAD_A[1:]] = M.MetaError("Meta HTTP 400", status_code=400,
                                         payload={"error": {"code": 131026, "message": "undeliverable"}})
    _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send")
    report = _status(wa)
    assert report["phones"][0]["state"] == "failed" and report["totals"]["error_codes"] == {"send 131026": 1}


# --- follow-ups, history import ---------------------------------------------------------------------------------

def test_follow_ups_leave_a_campaign_non_responder_alone_even_with_earlier_messages(wa, monkeypatch):
    c = ST.db()
    ST.record_inbound(c, LEAD_A, "wamid.early.in", "Hallo")
    ST.record_outbound(c, LEAD_A, "wamid.early.out", "Hallo zurück")
    t = ST.thread(c, LEAD_A)
    t["last_inbound_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
    t["last_outbound_at"] = t["last_inbound_at"]
    ST.save_thread(c, t)
    c.close()
    _run(wa, Graph(), _leads(wa, [_lead(LEAD_A)]), "--send", clock=Clock(datetime.now(timezone.utc)))
    monkeypatch.setattr(C, "FOLLOWUP_TIER_MINUTES", [0])
    nudges = FakeText()
    assert FU.run(client=nudges) == [] and nudges.sent == []

    c = ST.db()
    t = ST.thread(c, LEAD_A)
    del t["slots"]["campaign"]   # the same thread without the campaign card would be nudged
    ST.save_thread(c, t)
    c.close()
    assert [r["status"] for r in FU.run(client=nudges)] == ["sent"]


def test_history_import_is_previewed_in_the_dry_run_and_applied_before_the_claim(wa, monkeypatch):
    calls, graph = [], Graph()

    class Source:
        label = "old-system"

        def close(self):
            calls.append("close")

    monkeypatch.setattr(IH.Source, "open", classmethod(
        lambda cls, db, queries, label, roots=(): calls.append(("open", db, queries, label, list(roots))) or Source()))

    def import_phone(source, phone, apply=False, client=None):
        claim = None
        if apply:
            with CAMP.live_db() as c:
                claim = ST.campaign_send(c, CAMPAIGN, phone)
        calls.append(("import", phone, apply, claim, len(graph.posts), client is not None))
        return {"phone": phone, "source": source.label, "applied": apply, "found": True,
                "facts": {"known": {"region": "Bayern"}, "conflicting": {}, "absent": [], "imported": {"region": "Bayern"},
                          "kept": {}},
                "placement": [], "messages": {"found": 3}, "documents": [{"action": "would_import"}],
                "not_recoverable": 0}

    monkeypatch.setattr(IH, "import_phone", import_phone)
    history = ["--import-history-db", "/synthetic/old.sqlite", "--import-history-queries", "q.sql",
               "--import-history-source", "old-system", "--import-history-media-root", "/synthetic/media"]
    leads = _leads(wa, [_lead(LEAD_A)])
    code, report = _run(wa, graph, leads, *history)
    assert code == CAMP.EXIT_OK
    assert report["phones"][0]["history_import"]["facts_imported"] == {"region": "Bayern"}
    assert calls == [("open", "/synthetic/old.sqlite", "q.sql", "old-system", ["/synthetic/media"]),
                     ("import", LEAD_A, False, None, 0, False), "close"]
    calls.clear()
    code, report = _run(wa, graph, leads, *history, "--send")
    assert code == CAMP.EXIT_OK and report["phones"][0]["result"]["history_import"]["applied"] is True
    assert calls[1:] == [("import", LEAD_A, True, None, 0, True), "close"]
    assert _claim(LEAD_A)["state"] == "sent"


def test_a_stop_during_the_history_import_is_honoured_in_the_claim_transaction(wa, monkeypatch):
    class Source:
        label = "old-system"

        def close(self):
            pass

    monkeypatch.setattr(IH.Source, "open", classmethod(lambda cls, *a, **kw: Source()))

    def import_phone(source, phone, apply=False, client=None):
        if apply:
            _seed_thread(phone, stopped=True)      # the candidate wrote Stopp while the import ran
        return {"phone": phone, "source": source.label, "applied": apply, "found": False, "facts": {},
                "placement": [], "messages": {"found": 0}, "documents": [], "not_recoverable": 0}

    monkeypatch.setattr(IH, "import_phone", import_phone)
    graph = Graph()
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send", "--import-history-db", "/synthetic/old.sqlite",
                        "--import-history-queries", "q.sql", "--import-history-source", "old-system")
    assert report["phones"][0]["result"]["status"] == "skip_stopped"
    assert graph.posts == [] and _claim(LEAD_A) is None and _ownership(LEAD_A) is None


def test_a_stopp_in_the_old_systems_history_is_never_sent(wa, monkeypatch):
    """Review 2026-09-14: decide() saw only our own stop signals; a lead who wrote Stopp to the old bot was planned."""
    class Source:
        label = "old-system"

        def close(self):
            pass

    monkeypatch.setattr(IH.Source, "open", classmethod(lambda cls, *a, **kw: Source()))
    stop = [{"source_ref": "msg:9", "at": "2026-08-01T10:00:00+00:00", "body": "Stopp"}]
    applied = []

    def import_phone(source, phone, apply=False, client=None):
        applied.append(apply)
        return {"phone": phone, "source": source.label, "applied": apply, "found": True, "facts": {},
                "placement": [], "messages": {"found": 4}, "stop_messages": stop if phone == LEAD_A else [],
                "documents": [], "not_recoverable": 0}

    monkeypatch.setattr(IH, "import_phone", import_phone)
    history = ["--import-history-db", "/synthetic/old.sqlite", "--import-history-queries", "q.sql",
               "--import-history-source", "old-system"]
    graph, leads = Graph(), _leads(wa, [_lead(LEAD_A), _lead(LEAD_B)])
    code, report = _run(wa, graph, leads, *history)
    rows = _by_phone(report)
    assert (rows[LEAD_A]["action"], rows[LEAD_B]["action"]) == ("skip_opted_out", "send")
    assert rows[LEAD_A]["reason"] == "Stopp in the imported history (old-system): 2026-08-01T10:00:00+00:00 'Stopp'"
    code, report = _run(wa, graph, leads, *history, "--send")
    rows = _by_phone(report)
    assert (rows[LEAD_A]["result"]["status"], rows[LEAD_B]["result"]["status"]) == ("skip_opted_out", "sent")
    assert [p["body"]["to"] for p in graph.posts] == [LEAD_B[1:]]
    assert _claim(LEAD_A) is None and _ownership(LEAD_A) is None


def test_an_unreadable_history_source_stops_the_run(wa, monkeypatch):
    def refuse(cls, *a, **kw):
        raise IH.SourceAccessError("cannot read source database /synthetic/old.sqlite")

    monkeypatch.setattr(IH.Source, "open", classmethod(refuse))
    graph = Graph()
    code, report = _run(wa, graph, _leads(wa, [_lead(LEAD_A)]), "--send", "--import-history-db", "/synthetic/old.sqlite",
                        "--import-history-queries", "q.sql", "--import-history-source", "old-system")
    assert code == CAMP.EXIT_CONFIG and "cannot read source database" in report["error"] and graph.posts == []


def test_the_routing_restore_leaves_a_changed_record_alone(wa):
    c = R.db()
    prior = R.flip_to_us_for_campaign(c, LEAD_A, CAMPAIGN, "2026-09-15T08:00:00+00:00")
    assert prior is None
    R.flip_to_us_on_reopen(c, LEAD_A)
    assert R.restore_after_campaign_failure(c, LEAD_A, CAMPAIGN, prior) == "changed"
    assert R.ownership(c, LEAD_A)["reason"] == "reopened_by_us"
    c.close()


def test_the_default_report_lives_outside_the_repo_and_is_private(wa, monkeypatch):
    code = CAMP.main(["--campaign-id", CAMPAIGN, "--template-id", TEMPLATE_ID, "--leads",
                      str(_leads(wa, [_lead(LEAD_A)])), "--window", "00-24"], client=Graph().client())
    assert code == CAMP.EXIT_OK
    [path] = list((wa / "reports").glob(f"{CAMPAIGN}-dry-run-*.json"))
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct(path.parent.stat().st_mode & 0o777) == "0o700"
    repo = pathlib.Path(CAMP.__file__).resolve().parents[3]
    assert (repo / "app" / "wa" / "luna" / "campaign.py").exists()
    assert repo != REAL_REPORT_DIR and repo not in REAL_REPORT_DIR.parents
