"""tools/daria_desk.py and tools/daria_tools.py: one reader of the operators' mail for every batch (TASK-345.12.1),
Daria's scoped toolset (TASK-345.12.2). No network, no CLI: the classifier, SMTP and Daria's answer are fakes."""
import importlib.util
import email
import json
import queue
import re
import random
import shlex
import sqlite3
import sys
import types
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import clinic_mailer as M  # noqa: E402
import daria_desk as D  # noqa: E402
from mailer_doc import STYLE  # noqa: E402

STYLE_TD = STYLE["td"]

OPS = ["op1@example.org", "op2@example.net"]
CMD = "cmd@example.com"      # an operator's second mailbox: may command, is never written to
NOW = datetime.fromisoformat("2026-10-02T09:30:00+02:00")


def plain(m):
    """The text part of a mail to the operators (every one of them is multipart/alternative)."""
    return m.get_body(("plain",)).get_content()


def campaign(root, name, ids, monkeypatch):
    """A scheduled campaign (initial + fu1 3bd) in desk mode, planned and announced; returns (config path, batch id)."""
    d = root / name
    d.mkdir()
    (d / "t0.txt").write_text("Betreff: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nText.\n")
    (d / "t1.txt").write_text("Betreff: Re: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nNachfrage.\n")
    (d / "announce.txt").write_text("Betreff: Рассылка [KAMPAGNE]: план на [DATUM]\n\n[PLAN]\n\n[ABLAUF]\n\n[NOTIZEN]\n")
    (d / "notes.txt").write_text(f"Заметки {name}\n")
    (d / "recipients.json").write_text(json.dumps([
        {"id": i, "clinic": f"Klinik {i.upper()}", "to": [f"pd-{i}@example.org"], "cc": [],
         "vars": {"ANREDE": "Sehr geehrte Damen und Herren", "BEREICH": "Intensivstation"}} for i in ids]))
    (d / "allowlist.txt").write_text("\n".join([f"pd-{i}@example.org" for i in ids] + OPS) + "\n")
    conf = {"campaign": name, "sender": "me@example.org", "sender_name": "Daria", "tz": "Europe/Berlin",
            "window": {"weekdays": [1, 2, 3, 4, 5], "from": "08:00", "to": "12:00"}, "holidays": [], "pause_seconds": [90, 180],
            "stop_on": ["reply", "stop", "bounce"], "halt_on": ["bounce", "stop"], "watch_folders": ["inbox"],
            "watch_via": "daria-inbox", "watch_overlap_minutes": 10,
            "cadence": [{"step": "initial", "template": "t0.txt"}, {"step": "fu1", "after": "3bd", "in_thread": True, "template": "t1.txt"}],
            "recipients": "recipients.json", "allowlist": "allowlist.txt", "ledger": "ledger.jsonl", "batches": "b", "approvals": "a",
            "operators": OPS, "notify": OPS, "forward": {k: OPS for k in M.FORWARD_KINDS}, "command_poll_seconds": 60, "announce": {"window_minutes": 60, "template": "announce.txt", "notes": "notes.txt"},
            "classifier": {"model": "haiku", "claude_bin": "/bin/false", "run_as": "claude", "timeout_seconds": 5},
            "desk": {"heartbeat": "../heartbeat.json", "max_age_seconds": 600, "poll_seconds": 60}}
    (d / "c.json").write_text(json.dumps(conf))
    monkeypatch.setattr(M.mailer_announce, "render_pdf", lambda c, b, rows, n, ex, info, p: (p.write_bytes(b"%PDF"), (p, {}))[1])
    cfg = M.load_config(d / "c.json")
    cfg["suppression_db"] = None
    random.seed(len(name))
    bid, _ = M.plan(cfg, datetime.fromisoformat("2026-10-01T17:00:00+02:00"),
                    announce_at=datetime.fromisoformat("2026-10-02T09:00:00+02:00"),
                    start_at=datetime.fromisoformat("2026-10-02T10:00:00+02:00"))
    M.append_ledger(cfg, {"event": "announced", "batch_id": bid, "to": OPS, "message_id": f"<ann-{name}@me>",
                          "sent_at": "2026-10-02T09:00:05+02:00"})
    return d / "c.json", bid


class Desk:
    """Two announced batches on one sender box, a desk config, a fake clock, SMTP and classifier."""

    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setattr(M, "now_in", lambda cfg: NOW)
        self.c1, self.b1 = campaign(tmp_path, "w1", ["a1", "a2"], monkeypatch)
        self.c2, self.b2 = campaign(tmp_path, "w2", ["b1", "b2", "b3"], monkeypatch)
        conf = {"sender": "me@example.org", "sender_name": "Daria", "tz": "Europe/Berlin", "operators": [OPS[0].upper(), OPS[1]], "command_only": [CMD.upper()], "notify": OPS,
                "watch_via": "daria-inbox", "campaigns": [{"config": "w1/c.json", "label": "волна 1"}, {"config": "w2/c.json", "label": "волна 2"}],
                "ledger": "desk.jsonl", "heartbeat": "heartbeat.json", "session_dir": "sessions", "start": "2026-10-01T00:00:00+02:00",
                "poll_seconds": 60, "digest_at": "17:00", "halt_grace_minutes": 15, "tmux": {"session": "dsk-mailing", "view": ["nurse79", "dsk-mailing"]}, "overlap_minutes": 5, "brain": str(tmp_path / "brain.sqlite"), "docs": {"notes": "w2/notes.txt"},
                "classifier": {"model": "haiku", "claude_bin": "/bin/false", "run_as": "claude", "timeout_seconds": 5},
                "answerer": {"model": "sonnet", "effort": "high", "claude_bin": "/bin/false", "run_as": "claude", "timeout_seconds": 900,
                             "python": "/usr/bin/python3", "mcp_timeout_ms": 60000}}
        (tmp_path / "desk.json").write_text(json.dumps(conf))
        self.d = D.load(tmp_path / "desk.json")
        monkeypatch.setattr(D, "now", lambda d: NOW)
        self.sent, self.intents = [], {}
        monkeypatch.setattr(M, "smtp_send", lambda box, msg: self.sent.append(msg) or {})
        monkeypatch.setattr(M, "sweep", lambda cfgs, box: [])         # the box is never read for real (see test_clinic_mailer)
        monkeypatch.setattr(D, "run_claude", self.classifier)
        self.jobs = queue.Queue()

    def classifier(self, c, args, stdin, cwd, what, extra_env=None):
        q = json.loads(stdin)
        self.seen_batches = [b["key"] for b in q["batches"]]
        intent, batch_ids, ids = self.intents.get(q["text"], ("not_command", [], []))
        return json.dumps({"intent": intent, "batch_ids": batch_ids, "recipient_ids": ids, "why": "тест"})

    def mail(self, text, mid, **headers):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"], m["Message-ID"] = OPS[1], "me@example.org", "Re: Рассылка", mid
        for k, v in headers.items():
            m[k.replace("_", "-")] = v
        m.set_content(text)
        D.handle(self.d, {}, self.jobs, "inbox", m, mid, OPS[1])

    def halts(self, path):
        return [e for e in M.read_ledger(M.load_config(path)) if e["event"] == "halt"]


@pytest.fixture
def desk(tmp_path, monkeypatch):
    return Desk(tmp_path, monkeypatch)


def test_a_mail_in_one_waves_thread_goes_to_that_wave_only(desk):
    bs = D.active(desk.d)
    assert {b["key"] for b in bs} == {desk.b1, desk.b2}
    assert [b["key"] for b in D.route(desk.d, ["<x@y>", "<ann-w2@me>"], bs)] == [desk.b2]
    assert {b["key"] for b in D.route(desk.d, ["<unknown@y>"], bs)} == {desk.b1, desk.b2}
    assert {b["key"] for b in D.route(desk.d, [], bs)} == {desk.b1, desk.b2}


def test_a_stop_in_one_waves_thread_halts_only_that_wave_and_is_answered_once(desk):
    desk.intents["стоп"] = ("stop", [], [])
    desk.mail("стоп\n\n> старый план", "<s1@op>", In_Reply_To="<ann-w2@me>", References="<ann-w2@me>")
    assert desk.seen_batches == [desk.b2]
    assert desk.halts(desk.c1) == [] and [h["by"] for h in desk.halts(desk.c2)] == [OPS[1]]
    (r,) = desk.sent
    assert r["In-Reply-To"] == "<s1@op>" and r["To"] == ", ".join(OPS) and r["Auto-Submitted"] == "auto-replied"
    body = plain(r)
    assert "волна 2: Рассылка отменена" in body and "волна 1" not in body.split("Команды")[0]
    (ans,) = [e for e in D.read_ledger(desk.d) if e["event"] == "answer"]
    assert ans["batch_ids"] == [desk.b2] and ans["kind"] == "stop"
    assert [b["key"] for b in D.active(desk.d)] == [desk.b1]       # a stopped batch is over


def test_a_stop_outside_any_thread_names_its_wave_or_stops_all(desk):
    desk.intents["стоп волна 1"] = ("stop", [desk.b1], [])
    desk.mail("стоп волна 1", "<s2@op>")
    assert set(desk.seen_batches) == {desk.b1, desk.b2}
    assert len(desk.halts(desk.c1)) == 1 and desk.halts(desk.c2) == []
    desk.intents["стоп всё"] = ("stop", [], [])
    desk.mail("стоп всё", "<s3@op>")
    assert desk.seen_batches == [desk.b2] and len(desk.halts(desk.c2)) == 1
    desk.intents["стоп ещё раз"] = ("stop", [], [])
    desk.mail("стоп ещё раз", "<s4@op>")
    assert "ни одна рассылка не идёт" in plain(desk.sent[-1])


def test_a_skip_acts_on_the_wave_that_holds_the_clinic(desk):
    desk.intents["не отправляй в Klinik B2"] = ("skip", [], ["b2", "zz"])
    desk.mail("не отправляй в Klinik B2", "<k1@op>")
    skips = [e for e in M.read_ledger(M.load_config(desk.c2)) if e["event"] == "skip"]
    assert [e["recipient_id"] for e in skips] == ["b2"]
    assert not [e for e in M.read_ledger(M.load_config(desk.c1)) if e["event"] == "skip"]
    assert "волна 2: Убрано: Klinik B2" in plain(desk.sent[-1])
    (m,) = [e for e in D.read_ledger(desk.d) if e["event"] == "mail"]
    assert m["unknown_ids"] == ["zz"] and m["recipient_ids"] == ["b2"]


def test_a_question_is_answered_by_daria_once_and_survives_a_restart(desk, monkeypatch):
    desk.mail("Почему Бамберг в списке?", "<q1@op>", In_Reply_To="<ann-w1@me>")
    assert desk.sent == [] and desk.jobs.qsize() == 1
    assert [e["message_id"] for e in D.unanswered(desk.d)] == ["<q1@op>"]          # a restart queues it again
    asked = []
    monkeypatch.setattr(D, "ask", lambda d, q: (asked.append(q), ("Бамберг: в 2023 отказ, см. brain.", Path("/x")))[1])
    desk.jobs.put(None)
    D.answer_worker(desk.d, {}, desk.jobs)
    assert asked[0]["text"] == "Почему Бамберг в списке?" and asked[0]["desk"] == "в письме нет команды рассылки"
    (r,) = desk.sent
    assert r["In-Reply-To"] == "<q1@op>" and plain(r).startswith("Бамберг: в 2023 отказ") and "Daria\n" in plain(r)
    assert D.unanswered(desk.d) == [] and "<q1@op>" in D.handled(desk.d)
    desk.mail("А Форххайм?", "<q2@op>", In_Reply_To=r["Message-ID"], References=f"<q1@op> {r['Message-ID']}")
    assert desk.seen_batches == [desk.b1]                       # a desk answer about wave 1 keeps the thread on wave 1
    mail = desk.jobs.get()
    assert D.question_of(desk.d, mail)["thread"] == [{"from": OPS[1], "text": "Почему Бамберг в списке?",
                                                     "daria": "Бамберг: в 2023 отказ, см. brain."}]


def test_an_answer_with_a_markdown_table_goes_out_as_html_with_a_real_table_and_the_rows_in_the_text(desk, monkeypatch):
    """Ivan, 2026-10-05: tables in operator mail are tables."""
    desk.mail("Что завтра уйдёт?", "<q9@op>", In_Reply_To="<ann-w1@me>")
    answer = ("Завтра уйдёт **3 письма**:\n\n| Клиника | Время |\n| --- | --- |\n| Klinik A | 09:02 |\n| Klinik B & Co | 09:05 |\n\n"
              "Что ещё:\n- фоллоу-ап в пятницу\n- отчёт в 08:00")
    monkeypatch.setattr(D, "ask", lambda d, q: (answer, Path("/x")))
    desk.jobs.put(None)
    D.answer_worker(desk.d, {}, desk.jobs)
    (r,) = desk.sent
    assert r.get_content_type() == "multipart/alternative"
    assert "| Klinik A | 09:02 |" in plain(r) and "**3 письма**" in plain(r)             # the text part keeps the rows as they are
    page = r.get_body(("html",)).get_content()
    assert re.findall(r"<tr>(.*?)</tr>", page)[1:] == [
        f'<td style="{STYLE_TD}">Klinik A</td><td style="{STYLE_TD}">09:02</td>',
        f'<td style="{STYLE_TD}">Klinik B &amp; Co</td><td style="{STYLE_TD}">09:05</td>']
    assert "<th " in page and "<b>3 письма</b>" in page and page.count("<li>") == 2 and "| ---" not in page


def test_a_correction_mail_cannot_do_becomes_a_task_and_a_failed_answer_is_told(desk, monkeypatch):
    desk.intents["перенеси волну 2 на понедельник"] = ("other_command", [], [])
    desk.mail("перенеси волну 2 на понедельник", "<c1@op>")
    mail = desk.jobs.get()
    assert "заведи задачу" in D.question_of(desk.d, mail)["desk"]

    def broken(d, q):
        raise M.MailerError("Daria's answer: no answer within 900 s")
    monkeypatch.setattr(D, "ask", broken)
    desk.jobs.put(mail)
    desk.jobs.put(None)
    D.answer_worker(desk.d, {}, desk.jobs)
    assert "Не смогла ответить на это письмо: Daria's answer: no answer within 900 s" in plain(desk.sent[-1])
    assert D.unanswered(desk.d) == []


def test_a_desk_error_is_logged_and_mailed_before_the_desk_exits(desk, monkeypatch):
    """An error outside the reading of the mail (an inbox that cannot be read is retried, see below) still ends the desk."""
    desk.intents["Какой статус рассылки?"] = ("status", [], [])
    monkeypatch.setattr(M, "mailbox", lambda a: {"ADDRESS": a})
    monkeypatch.setattr(D, "operator_mail", lambda d, since, seen: iter(()))

    def status_check(d, box):
        raise M.MailerError("halt notice failed: token expired")
    monkeypatch.setattr(D, "status_check", status_check)
    with pytest.raises(M.MailerError, match="token expired"):
        D.run(desk.d)
    (stop,) = [e for e in D.read_ledger(desk.d) if e["event"] == "desk_stopped"]
    assert stop["reason"] == "halt notice failed: token expired"
    (m,) = desk.sent
    assert m["Subject"] == "Дарья не читает почту операторов" and "token expired" in plain(m) and m["To"] == ", ".join(OPS)
    assert json.loads(desk.d["heartbeat"].read_text())["ts"] == NOW.isoformat(timespec="seconds")


def failing_inbox(monkeypatch, fails):
    """D.operator_mail that raises `fails` times (the helper's retries are over, see test_clinic_mailer) and then reads nothing."""
    calls, naps = [], []

    def inbox(d, since, seen):
        calls.append(since)
        if len(calls) <= fails:
            raise M.MailerError("daria-inbox exited 1: HTTP 404")
        return iter(())
    monkeypatch.setattr(D, "operator_mail", inbox)
    monkeypatch.setattr(M, "sleep", naps.append)
    return calls, naps


def test_an_inbox_that_stays_unreadable_is_mailed_once_and_the_rest_of_the_desk_goes_on(desk, monkeypatch):
    """Ivan, 2026-10-05: three retries, a notice, and the desk keeps running; the heartbeat is the one thing it must not fake."""
    calls, naps = failing_inbox(monkeypatch, 99)
    ran = []
    monkeypatch.setattr(D, "status_check", lambda d, box: ran.append("status"))
    monkeypatch.setattr(D, "digest_due", lambda d, t: True)
    monkeypatch.setattr(M, "digest", lambda cfgs, box: ran.append("digest") or {})
    since = NOW - timedelta(minutes=30)
    assert D.poll(desk.d, {}, desk.jobs, since, False) == (since, True) and ran == ["status", "digest"]
    (m,) = [m for m in desk.sent if m["Subject"] == "Дарья не может прочитать почту операторов"]
    assert m["To"] == ", ".join(OPS) and "4 раза подряд: daria-inbox exited 1: HTTP 404" in plain(m)
    assert not desk.d["heartbeat"].exists()                                         # a stop by mail would go unread
    for _ in range(2):                                                              # the outage goes on: no second mail
        assert D.poll(desk.d, {}, desk.jobs, since, True) == (since, True)
    assert len(desk.sent) == 1 and ran == ["status", "digest"] * 3
    assert [e["event"] for e in D.read_ledger(desk.d) if e["event"].startswith("read_")] == ["read_error"]


def test_the_desk_reads_again_after_an_outage_from_where_it_stopped(desk, monkeypatch):
    calls, naps = failing_inbox(monkeypatch, 0)
    monkeypatch.setattr(D, "status_check", lambda d, box: None)
    since = NOW - timedelta(minutes=30)
    new_since, failing = D.poll(desk.d, {}, desk.jobs, since, True)
    assert failing is False and calls == [since] and new_since == NOW - timedelta(minutes=desk.d["overlap_minutes"])
    assert [e["event"] for e in D.read_ledger(desk.d) if e["event"] == "read_recovered"] == ["read_recovered"]
    assert json.loads(desk.d["heartbeat"].read_text())["since"] == since.isoformat(timespec="seconds")


def test_the_digest_goes_out_once_a_day_from_digest_at(desk, monkeypatch):
    """Ivan, 2026-10-05: the clinics' answers reach the operators once a day; a day with a digest logged is done."""
    t = D.now(desk.d).replace(hour=16, minute=59)
    assert not D.digest_due(desk.d, t) and D.digest_due(desk.d, t.replace(hour=17, minute=0))
    D.log(desk.d, {"event": "digest", "date": f"{t:%Y-%m-%d}", "answers": {}})
    assert not D.digest_due(desk.d, t.replace(hour=18)) and D.digest_due(desk.d, (t + timedelta(days=1)).replace(hour=17))
    desk.intents["Какой статус рассылки?"] = ("status", [], [])
    monkeypatch.setattr(M, "mailbox", lambda a: {"ADDRESS": a})
    monkeypatch.setattr(D, "digest_due", lambda d, t: True)
    got = []
    monkeypatch.setattr(M, "digest", lambda cfgs, box: got.append(cfgs) or {OPS[0]: 2})
    monkeypatch.setattr(D, "operator_mail", lambda d, since, seen: iter(()))

    def end_of_round(seconds):
        raise M.MailerError("stop here")
    monkeypatch.setattr(D, "time", types.SimpleNamespace(sleep=end_of_round))       # the main loop's sleep only, not the threads'
    with pytest.raises(M.MailerError, match="stop here"):
        D.run(desk.d)
    assert got == [[desk.d["campaigns"][0]["cfg"], desk.d["campaigns"][1]["cfg"]]]
    (e,) = [e for e in D.read_ledger(desk.d) if e["event"] == "digest" and e["answers"]]
    assert e["answers"] == {OPS[0]: 2}


def test_the_status_check_mails_only_a_halt_that_stays_and_only_once(desk, monkeypatch):
    """Ivan, 2026-10-05: no notice of an interruption or a restart, only of what is still broken after the grace."""
    monkeypatch.setattr(M, "mailbox", lambda a: {"ADDRESS": a})
    c1, c2 = (c["cfg"] for c in desk.d["campaigns"])
    M.append_ledger(c1, {"event": "halt", "batch_id": desk.b1, "kind": "error", "reason": "network down"})
    M.append_ledger(c2, {"event": "halt", "batch_id": desk.b2, "kind": "error", "reason": "SIGTERM: the process was told to end"})
    D.status_check(desk.d, {"ADDRESS": "x"})
    assert desk.sent == []                                                    # just halted: a restart may follow
    monkeypatch.setattr(M, "now_in", lambda cfg: NOW + timedelta(minutes=10))
    M.append_ledger(c2, {"event": "resumed", "batch_id": desk.b2, "halt_ts": NOW.isoformat(timespec="seconds"), "halt_reason": "x"})
    monkeypatch.setattr(M, "now_in", lambda cfg: NOW + timedelta(minutes=16))
    D.status_check(desk.d, {"ADDRESS": "x"})
    (m,) = desk.sent                                                          # the resumed wave is not mailed
    assert m["Subject"].endswith("рассылка прервана") and "network down" in plain(m) and m["To"] == ", ".join(OPS)
    D.status_check(desk.d, {"ADDRESS": "x"})
    assert len(desk.sent) == 1                                                # once


def test_daria_has_no_shell_no_file_tools_and_no_phone_rail(desk, tmp_path):
    args = D.answer_args(desk.d, tmp_path / "mcp.json")
    assert args[args.index("--tools") + 1] == ""
    allowed = set(args[args.index("--allowedTools") + 1].split(","))
    assert allowed == {f"mcp__jobs__{t}" for t in D.BOARD_TOOLS} | {f"mcp__daria__{t}" for t in D.DARIA_TOOLS}
    denied = set(args[args.index("--disallowedTools") + 1].split(","))
    for t in ("show_clinic_photos", "send_updated_cv", "read_history", "read_document", "find_stored_cv", "match_cv_to_postings"):
        assert f"mcp__jobs__{t}" not in allowed and f"mcp__jobs__{t}" in denied
    assert "--strict-mcp-config" in args and not any("Bash" in a or "bypass" in a or "dangerously" in a for a in args)


def test_a_desk_mode_batch_reads_no_operator_mail_and_halts_on_a_stale_heartbeat(desk, monkeypatch):
    cfg = M.load_config(desk.c2)
    batch = json.loads((cfg["batches"] / f"{desk.b2}.json").read_text())
    with pytest.raises(M.MailerError, match="the desk has never run"):
        M.check_desk(cfg)
    D.stamp(desk.d, NOW)
    monkeypatch.setattr(M, "handle_commands", lambda *a: pytest.fail("a desk-mode batch read the operators' mail"))
    monkeypatch.setattr(M, "watch", lambda cfg, box: [])
    clock = [NOW]
    monkeypatch.setattr(M, "now_in", lambda cfg: clock[0])
    monkeypatch.setattr(M, "sleep", lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)))
    assert M.poll_until(cfg, {}, batch, NOW, NOW + timedelta(minutes=5)) is True
    with pytest.raises(M.MailerError, match="has not read the operators' mail for 1[0-9] min"):
        M.poll_until(cfg, {}, batch, NOW, NOW + timedelta(minutes=30))
    assert "отвечает Дарья" in M.commands_help(cfg)


# ---------- tools/daria_tools.py ----------

@pytest.fixture
def tools(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    db = tmp_path / "brain.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE companies (id INTEGER, name TEXT)")
    con.executemany("INSERT INTO companies VALUES (?, ?)", [(k, f"Klinik {k} " + "x" * 50) for k in range(200)])
    con.commit()
    con.close()
    monkeypatch.setenv("DARIA_BRAIN", str(db))
    monkeypatch.setenv("DARIA_LOG", str(tmp_path / "calls.jsonl"))
    spec = importlib.util.spec_from_file_location("daria_tools", Path(__file__).resolve().parent.parent / "tools" / "daria_tools.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    return T


def test_brain_is_read_only_and_a_big_result_says_truncated(tools, monkeypatch):
    r = tools.brain_query("SELECT id FROM companies WHERE id < 3")
    assert r == {"columns": ["id"], "rows": [[0], [1], [2]], "rows_total": 3, "truncated": False}
    for sql in ("INSERT INTO companies VALUES (999, 'x')", "DELETE FROM companies", "ATTACH DATABASE ':memory:' AS m"):
        with pytest.raises(tools.ToolError):
            tools.brain_query(sql)
    assert tools.brain_query("SELECT count(*) FROM companies")["rows"] == [[200]]
    monkeypatch.setattr(tools, "RESULT_BUDGET_BYTES", 1000)
    r = tools.brain_query("SELECT * FROM companies")
    assert r["truncated"] is True and r["rows_total"] == 200 and 0 < len(r["rows"]) < 200
    assert [t["table"] for t in tools.brain_tables()] == ["companies"]


def test_desk_log_is_newest_first_from_since_and_a_big_log_says_truncated(tools, tmp_path, monkeypatch):
    led = tmp_path / "desk.jsonl"
    led.write_text("".join(json.dumps({"ts": f"2026-10-0{d}T09:00:00+02:00", "event": "mail", "text": "x" * 300}) + "\n"
                           for d in (1, 2, 3)))
    monkeypatch.setenv("DARIA_DESK_LEDGER", str(led))
    r = tools.desk_log("2026-10-02")
    assert [e["ts"][:10] for e in r["events"]] == ["2026-10-03", "2026-10-02"] and r["events_total"] == 2 and not r["truncated"]
    monkeypatch.setattr(tools, "RESULT_BUDGET_BYTES", 500)
    r = tools.desk_log()
    assert r["truncated"] is True and r["events_total"] == 3 and [e["ts"][:10] for e in r["events"]] == ["2026-10-03"]


def test_the_pipeline_reads_and_writes_only_project_daria(tools, monkeypatch):
    calls = []

    def backlog(*args):
        calls.append(args)
        if args[:2] == ("task", "view") and "--json" in args:
            return json.dumps({"schemaVersion": 1, "kind": "task", "task": {"id": args[2], "project": "daria" if args[2] == "TASK-1" else "board"}})
        return "ok"
    monkeypatch.setattr(tools, "_backlog", backlog)
    assert tools.pipeline_view("TASK-1") == "ok"
    with pytest.raises(tools.ToolError, match="not in project daria"):
        tools.pipeline_view("TASK-2")
    tools.pipeline_create("Plan wave 3", "Valentyn asked by mail on 2026-10-02", ["plan exists"], painless=True)
    create = calls[-1]
    assert create[:3] == ("task", "create", "Plan wave 3") and create[create.index("--project") + 1] == "daria"
    assert create[create.index("-l") + 1] == "daria,painless" and create[create.index("--ac") + 1] == "plan exists"
    with pytest.raises(tools.ToolError, match="acceptance criterion"):
        tools.pipeline_create("x", "y", [])
    assert tools.pipeline_list("To Do") == "ok" and calls[-1][calls[-1].index("--project") + 1] == "daria"


# ---------- the mailing tools: tmux windows and fixed commands (TASK-345.12.10) ----------

class FakeTmux:
    """tmux and ps as fakes: sessions with windows, each window with the processes in it; a window that was started
    runs its shell line, which writes the output file the way the real command would."""

    def __init__(self, tools, tmp_path):
        self.tools, self.calls, self.sessions = tools, [], {}      # session -> {window: [process args]}
        self.exit_status = 0

    def tmux(self, *args):
        self.calls.append(args)
        if args[0] == "list-sessions":
            if not self.sessions:
                raise self.tools.ToolError("tmux list-sessions exited 1: no server running on /tmp/tmux-1003/default")
            return "\n".join(self.sessions) + "\n"
        if args[0] == "list-panes":
            return "".join(f"{s}\t{w}\t{1000 + i}\t0\n" for s, ws in self.sessions.items() for i, w in enumerate(ws))
        if args[0] in ("new-window", "new-session"):
            session = args[args.index("-s") + 1] if args[0] == "new-session" else args[args.index("-t") + 1].rstrip(":")
            name, line = args[args.index("-n") + 1], args[-1]
            self.sessions.setdefault(session, {})[name] = [line]
            words = shlex.split(line.split(" && ", 1)[1].split(" 2>&1;")[0])
            out = Path(words[words.index(">>") + 1])
            if "tools/clinic_mailer.py plan" in line:
                out.write_text(f"+  a1 Klinik A1: initial to pd-a1@example.org\nbatch w1-x: read {out.parent}/w1-x.txt\nexit status: {self.exit_status}\n")
            return ""
        if args[0] == "capture-pane":
            return "line one\nline two\n"
        return ""

    def ps(self):
        out, pid = [(1, 0, "tmux: server")], 1000
        for ws in self.sessions.values():
            for w, procs in ws.items():
                out.append((pid, 1, "sh -c " + procs[0]))
                out += [(pid + 1, pid, "python3 -u tools/clinic_mailer.py send /x/campaign.json " + w.removeprefix("send-") + " --live")] if w.startswith(("w1-", "w2-")) else []
                pid += 10
        return out


@pytest.fixture
def mailing(tools, desk, tmp_path, monkeypatch):
    for k, v in {"DARIA_CAMPAIGNS": json.dumps({c["cfg"]["campaign"]: c["path"] for c in desk.d["campaigns"]}),
                 "DARIA_DESK_CONFIG": str(desk.d["_path"]), "DARIA_TMUX": json.dumps(desk.d["tmux"]), "DARIA_ASKED_BY": OPS[0],
                 "DARIA_TZ": "Europe/Berlin", "DARIA_DESK_LEDGER": str(desk.d["ledger"]), "DARIA_REPO": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)
    fake = FakeTmux(tools, tmp_path)
    monkeypatch.setattr(tools, "_tmux", fake.tmux)
    monkeypatch.setattr(tools, "_processes", lambda: fake.ps())
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    fake.tools_, fake.desk = tools, desk
    return fake


def audited(desk):
    return [e for e in D.read_ledger(desk.d) if e["event"] == "mailing_tool"]


def test_the_mailing_state_shows_a_planned_batch_that_is_not_announced_yet_and_whether_it_is_approved(desk):
    """05.10: Daria could not see the follow-up batch planned for Friday, because only announced batches were listed."""
    cfg = desk.d["campaigns"][0]["cfg"]
    batch = json.loads((cfg["batches"] / f"{desk.b1}.json").read_text())
    batch["batch_id"] = "w1-planned"
    (cfg["batches"] / "w1-planned.json").write_text(json.dumps(batch))
    text = D.mailing_state_text(desk.d)
    assert f"Пакет {desk.b1} (идёт)" in text and "Пакет w1-planned (запланирован, ещё не анонсирован, анонс" in text
    assert "одобрение оператора: нет, ждёт" in text
    (cfg["approvals"] / "w1-planned.json").write_text("{}")
    assert "одобрение оператора: есть" in D.mailing_state_text(desk.d)
    M.append_ledger(cfg, {"event": "halt", "batch_id": "w1-planned", "kind": "error", "reason": "SIGTERM"})
    assert "Пакет w1-planned (остановлен до анонса: error, SIGTERM;" in D.mailing_state_text(desk.d)


def test_the_mailing_windows_are_listed_and_read_only_for_her_sessions(tools, mailing, desk):
    mailing.sessions = {"nurse79": {"w1-fu2": ["x"]}, "dsk-mailing": {}, "somebody-else": {"secret": ["x"]}}
    wins = tools.mailing_windows()
    assert [(w["session"], w["window"]) for w in wins] == [("nurse79", "w1-fu2")] and "w1-fu2" in wins[0]["processes"][-1]
    assert tools.mailing_window_tail("nurse79", "w1-fu2", 5) == {"text": "line one\nline two\n"}
    with pytest.raises(tools.ToolError, match="no window somebody-else:secret"):
        tools.mailing_window_tail("somebody-else", "secret")
    (log, _), = [(e, e) for e in audited(desk) if e["tool"] == "mailing_window_tail" and "error" not in e]
    assert log["by"] == OPS[0] and log["command"][:2] == ["tmux", "capture-pane"] and log["result"] == "2 lines"
    assert any(e.get("error") and "somebody-else" in e["error"] for e in audited(desk))


def test_a_batchs_output_file_is_read_by_campaign_and_batch_id(tools, mailing, desk):
    cfg = desk.d["campaigns"][0]["cfg"]
    (cfg["ledger"].parent / f"send-{desk.b1}.out").write_text("".join(f"line {k}\n" for k in range(100)))
    r = tools.send_output_tail("w1", desk.b1, 3)
    assert r["text"] == "line 97\nline 98\nline 99" and r["truncated"] is False
    for bad in ("../c", "nurse79-nope", ""):
        with pytest.raises(tools.ToolError, match="has no batch"):
            tools.send_output_tail("w1", bad)
    with pytest.raises(tools.ToolError, match="no campaign 'w9'"):
        tools.send_output_tail("w9", desk.b1)


def test_plan_batch_runs_the_fixed_plan_command_in_her_session_and_returns_its_output(tools, mailing, desk):
    r = tools.plan_batch("w1", "2026-10-09T10:32:00+02:00", "2026-10-09T11:34:00+02:00", ["a1"])
    assert r["finished"] is True and r["exit_status"] == 0 and "batch w1-x: read" in r["output"]
    new, = [c for c in mailing.calls if c[0] == "new-session"]
    assert new[new.index("-s") + 1] == "dsk-mailing"                               # her session did not exist: made
    line = new[-1]
    assert " && python3 -u tools/clinic_mailer.py plan " in line and f"{desk.d['campaigns'][0]['path']} --announce-at 2026-10-09T10:32:00+02:00 --start-at 2026-10-09T11:34:00+02:00 --only a1" in line
    assert "sudo" not in line and ">> " in line and 'echo "exit status: $?"' in line
    ev, = [e for e in audited(desk) if e["tool"] == "plan_batch"]
    assert ev["by"] == OPS[0] and ev["command"][3:5] == ["plan", desk.d["campaigns"][0]["path"]] and ev["result"]["exit_status"] == 0
    tools.plan_batch("w1", "2026-10-09T10:32:00+02:00")
    assert [c[0] for c in mailing.calls if c[0].startswith("new-")] == ["new-session", "new-window"]       # the session is there now


def test_a_command_outside_the_list_or_with_foreign_arguments_is_refused(tools, mailing, desk):
    """No tool takes a shell line, an option or a path: every argument is checked against the desk's own campaigns, batches and ids."""
    expected = {"mailing_windows", "mailing_window_tail", "send_output_tail", "plan_batch", "start_batch", "stop_batch", "start_desk"}
    assert expected <= set(D.DARIA_TOOLS) and all(callable(getattr(tools, t)) for t in expected)
    with pytest.raises(tools.ToolError, match="no campaign"):
        tools.plan_batch("w1; rm -rf /", "2026-10-09T10:32:00+02:00")
    with pytest.raises(tools.ToolError, match="not an ISO time"):
        tools.plan_batch("w1", "tomorrow; reboot")
    with pytest.raises(tools.ToolError, match="needs a UTC offset"):
        tools.plan_batch("w1", "2026-10-09T10:32:00")
    with pytest.raises(tools.ToolError, match="no recipient ids"):
        tools.plan_batch("w1", "2026-10-09T10:32:00+02:00", only=["a1", "--now"])
    with pytest.raises(tools.ToolError, match="has no batch"):
        tools.start_batch("w1", "x --live; echo")
    assert not [c for c in mailing.calls if c[0].startswith("new-")]                # none of them reached tmux
    assert len([e for e in audited(desk) if "error" in e]) == 5                      # and each refusal is in the ledger


def test_start_batch_needs_the_operators_approval_and_runs_the_send_command_in_a_window_named_for_the_batch(tools, mailing, desk):
    cfg = desk.d["campaigns"][1]["cfg"]
    with pytest.raises(tools.ToolError, match="has no approval"):
        tools.start_batch("w2", desk.b2)
    (cfg["approvals"] / f"{desk.b2}.pending.json").rename(cfg["approvals"] / f"{desk.b2}.json")
    r = tools.start_batch("w2", desk.b2)
    assert r == {"window": desk.b2, "output_file": str(cfg["ledger"].parent / f"send-{desk.b2}.out")}
    new, = [c for c in mailing.calls if c[0] == "new-session"]
    assert new[new.index("-n") + 1] == desk.b2
    assert f"python3 -u tools/clinic_mailer.py send {desk.d['campaigns'][1]['path']} {desk.b2} --live >> " in new[-1]
    ev, = [e for e in audited(desk) if e["tool"] == "start_batch" and "error" not in e]
    assert ev["command"][:4] == ["python3", "-u", "tools/clinic_mailer.py", "send"] and ev["result"]["window"] == desk.b2


def test_stop_batch_sends_ctrl_c_to_the_window_that_runs_the_batch_in_any_mailing_session(tools, mailing, desk):
    mailing.sessions = {"nurse79": {f"{desk.b1}": ["x"]}, "dsk-mailing": {desk.b2: ["y"]}}
    mailing.ps = lambda: [(1, 0, "tmux"), (1000, 1, "sh -c cd /r && python3 -u tools/clinic_mailer.py send /c/campaign.json " + desk.b1 + " --live >> o 2>&1"),
                          (1001, 1000, "python3 -u tools/clinic_mailer.py send /c/campaign.json " + desk.b1 + " --live"),
                          (1010, 1, "sh -c cd /r && python3 -u tools/clinic_mailer.py send /c/campaign.w2.json " + desk.b2 + " --live")]
    r = tools.stop_batch("w1", desk.b1)
    assert r["window"] == f"nurse79:{desk.b1}" and r["sent"] == "C-c"
    assert ("send-keys", "-t", f"nurse79:{desk.b1}", "C-c") in mailing.calls
    mailing.sessions = {}
    with pytest.raises(tools.ToolError, match="no window runs a send process"):
        tools.stop_batch("w1", desk.b1)


def test_a_call_that_cannot_be_written_to_the_desk_ledger_does_not_run(tools, mailing, desk, monkeypatch):
    """05.10: the ledger was root's and her tool server runs as another user: the first live answer got an empty error."""
    monkeypatch.setenv("DARIA_DESK_LEDGER", str(desk.d["ledger"].parent / "no-such-dir" / "desk.jsonl"))
    with pytest.raises(tools.ToolError, match="cannot write the desk ledger"):
        tools.start_desk()
    assert not [c for c in mailing.calls if c[0].startswith("new-")]


def test_start_batch_and_start_desk_are_refused_while_the_process_runs(tools, mailing, desk):
    cfg = desk.d["campaigns"][1]["cfg"]
    (cfg["approvals"] / f"{desk.b2}.pending.json").rename(cfg["approvals"] / f"{desk.b2}.json")
    mailing.ps = lambda: [(1, 0, "tmux"), (50, 1, f"python3 -u tools/clinic_mailer.py send data/x/campaign.w2.json {desk.b2} --live"),
                          (60, 1, "python3 -u tools/daria_desk.py run data/email-analysis/desk/daria.json")]
    with pytest.raises(tools.ToolError, match=f"{desk.b2} already runs"):
        tools.start_batch("w2", desk.b2)
    with pytest.raises(tools.ToolError, match="a desk already runs"):
        tools.start_desk()
    assert not [c for c in mailing.calls if c[0].startswith("new-")]
    mailing.ps = lambda: [(1, 0, "tmux"), (50, 1, f"python3 -u tools/clinic_mailer.py send data/x/campaign.w2.json {desk.b2}-other --live")]
    tools.start_batch("w2", desk.b2)                                            # another batch's process is no reason to refuse


def test_a_second_desk_fails_before_it_logs_or_mails_anything(desk, monkeypatch):
    import fcntl
    import os
    monkeypatch.setattr(M, "mailbox", lambda a: pytest.fail("the second desk went on"))
    fd = os.open(desk.d["_path"], os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    with pytest.raises(M.MailerError, match="the desk is already running"):
        D.run(desk.d)
    os.close(fd)
    assert not [e for e in D.read_ledger(desk.d) if e["event"] in ("desk_started", "desk_stopped")] and desk.sent == []


def test_start_desk_runs_the_desk_command_in_a_window_called_desk(tools, mailing, desk):
    r = tools.start_desk()
    assert r["window"] == "desk" and r["output_file"] == str(desk.d["_path"].parent / "desk.out")
    new, = [c for c in mailing.calls if c[0] == "new-session"]
    assert f"python3 -u tools/daria_desk.py run {desk.d['_path']} >> " in new[-1]


def test_the_answer_config_tells_the_tools_who_asked_and_what_exists(desk, tmp_path, monkeypatch):
    """Needs neither the repo's .env nor the OS user `run_as`: a fresh clone and CI have neither."""
    monkeypatch.setattr(M, "load_env", lambda: {"SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_ANON_KEY": "test"})
    monkeypatch.setattr(D.pwd, "getpwnam", lambda name: type("Pw", (), {"pw_dir": f"/home/{name}"})())
    path = D.mcp_config(desk.d, tmp_path / "run", OPS[1])
    env = json.loads(path.read_text())["mcpServers"]["daria"]["env"]
    assert env["DARIA_ASKED_BY"] == OPS[1] and env["DARIA_TZ"] == "Europe/Berlin"
    assert json.loads(env["DARIA_CAMPAIGNS"]) == {"w1": str(desk.c1), "w2": str(desk.c2)} and json.loads(env["DARIA_TMUX"])["session"] == "dsk-mailing"
    assert env["DARIA_DESK_CONFIG"] == str(desk.d["_path"]) and env["HOME"] == "/home/claude"
    jobs = json.loads(path.read_text())["mcpServers"]["jobs"]["env"]
    assert jobs["SUPABASE_URL"] == "http://127.0.0.1:9" and jobs["SUPABASE_ANON_KEY"] == "test"


def test_a_failed_redirect_letter_is_logged_and_mailed_and_does_not_stop_the_desk(desk, monkeypatch):
    """Ivan, 2026-10-05: the desk sends the letters that clinic redirects made; one wave failing must not stop the other
    or the reading of the operators' mail."""
    calls = []

    def redirect_letters(cfg):
        calls.append(cfg["campaign"])
        if cfg["campaign"] == "w2":
            raise M.MailerError("SMTP refused ['pa@x.example']")
        return "w1-20261002-0930"
    monkeypatch.setattr(M, "redirect_letters", redirect_letters)
    D.redirect_letters(desk.d, {})
    assert calls == ["w1", "w2"]
    ev = [e for e in D.read_ledger(desk.d) if e["event"] in ("redirect_letters", "redirect_error")]
    assert [(e["event"], e["campaign"]) for e in ev] == [("redirect_letters", "w1"), ("redirect_error", "w2")] and "SMTP refused" in ev[1]["reason"]
    (m,) = desk.sent
    assert m["Subject"] == "Письмо на новый адрес не ушло: волна 2" and m["To"] == ", ".join(OPS) and "SMTP refused ['pa@x.example']" in plain(m)


def test_the_desk_reads_every_campaigns_answers_and_the_rest_and_mails_a_read_that_stays_failing_once(desk, monkeypatch):
    """Ivan, 2026-10-05: a batch process was the only reader of the clinics' answers, so one waited for the next process;
    and a mail from an address no letter went to was dropped."""
    watched, swept = [], []

    def watch(cfg, box):
        watched.append(cfg["campaign"])
        if cfg["campaign"] == "w1":
            raise M.MailerError("daria-inbox exited 1 on 4 tries in a row: HTTP 404")
        return []
    monkeypatch.setattr(M, "watch", watch)
    monkeypatch.setattr(M, "sweep", lambda cfgs, box: swept.append([c["campaign"] for c in cfgs]) or [])
    failing = D.watch_campaigns(desk.d, {}, set())
    assert failing == {"w1"} and watched == ["w1", "w2"] and swept == [["w1", "w2"]]
    (m,) = desk.sent
    assert m["Subject"] == "Дарья не может прочитать почту: волна 1" and m["To"] == ", ".join(OPS)
    assert "4 раза подряд: daria-inbox exited 1 on 4 tries in a row: HTTP 404" in plain(m)
    assert D.watch_campaigns(desk.d, {}, failing) == {"w1"} and len(desk.sent) == 1          # the outage goes on: no second mail
    monkeypatch.setattr(M, "watch", lambda cfg, box: [])
    assert D.watch_campaigns(desk.d, {}, failing) == set()
    assert [(e["event"], e["campaign"]) for e in D.read_ledger(desk.d) if e["event"].startswith("watch_")] == [("watch_error", "w1"), ("watch_recovered", "w1")]


def test_a_failing_sweep_is_mailed_once_and_does_not_stop_the_watches(desk, monkeypatch):
    watched = []
    monkeypatch.setattr(M, "watch", lambda cfg, box: watched.append(cfg["campaign"]) or [])

    def sweep(cfgs, box):
        raise M.MailerError("could not read the mail from x@y.example ('Hallo'): classifier: claude exited 1")
    monkeypatch.setattr(M, "sweep", sweep)
    failing = D.watch_campaigns(desk.d, {}, set())
    assert failing == {D.SWEEP} and watched == ["w1", "w2"]
    (m,) = desk.sent
    assert m["Subject"] == "Дарья не может прочитать почту: входящие вне рассылок" and "could not read the mail from x@y.example" in plain(m)


def test_a_desk_config_that_reads_the_mailbox_any_other_way_than_daria_inbox_is_refused(desk):
    conf = json.loads(desk.d["_path"].read_text())
    conf["watch_via"] = "graph"
    desk.d["_path"].write_text(json.dumps(conf))
    with pytest.raises(M.MailerError, match='"watch_via" must be "daria-inbox".*got .graph.'):
        D.load(desk.d["_path"])


def test_a_command_only_address_commands_but_is_never_written_to(desk, monkeypatch):
    """Ivan, 2026-10-06: Valentyn's second mailbox may command the mailings; every answer goes to the operators' main mailboxes."""
    raws = []
    for frm, mid in ((OPS[1], "<a@op>"), (CMD, "<b@cmd>"), ("clinic@example.de", "<c@clinic>")):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, "me@example.org", "Re: Рассылка", mid
        m.set_content("статус")
        raws.append(("inbox", m.as_bytes()))
    monkeypatch.setattr(M, "inbox_messages", lambda cfg, box, since, seen: iter(raws))
    assert [mid for _, _, mid, _ in D.operator_mail(desk.d, NOW, set())] == ["<a@op>", "<b@cmd>"]
    desk.intents["статус"] = ("status", [], [])
    cmd_mail = email.message_from_bytes(raws[1][1], policy=email.policy.default)
    D.handle(desk.d, {}, desk.jobs, "inbox", cmd_mail, "<b@cmd>", CMD)
    (r,) = desk.sent
    assert r["To"] == ", ".join(OPS) and not r["Cc"] and CMD not in r.as_string()
    assert CMD in D.help_text(desk.d) and OPS[0] in D.help_text(desk.d)
