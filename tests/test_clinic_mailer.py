"""tools/clinic_mailer.py: cadence arithmetic, rendering and the send guard (no network)."""
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import clinic_mailer as M  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "t0.txt").write_text("Betreff: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nText zu [BEREICH].\n")
    (tmp_path / "t1.txt").write_text("Betreff: Re: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nNachfrage.\n")
    (tmp_path / "recipients.json").write_text(json.dumps([
        {"id": "1", "clinic": "Klinik A", "to": ["pd@a.de"], "cc": ["st@a.de"], "vars": {"ANREDE": "Sehr geehrte Frau A", "BEREICH": "Intensivstation"}}]))
    (tmp_path / "allowlist.txt").write_text("# test\ncat1@example.org\ncat2@example.org\n")
    conf = {"campaign": "c", "sender": "me@example.org", "sender_name": "Me", "tz": "Europe/Berlin",
            "window": {"weekdays": [1, 2, 3, 4, 5], "from": "08:00", "to": "16:00"}, "holidays": ["2026-10-02"],
            "pause_seconds": [0, 0], "stop_on": ["reply", "stop", "bounce"], "halt_on": ["bounce", "stop"], "watch_folders": ["INBOX"],
            "cadence": [{"step": "initial", "template": "t0.txt"}, {"step": "fu1", "after": "4bd", "in_thread": True, "template": "t1.txt"}],
            "recipients": "recipients.json", "allowlist": "allowlist.txt", "ledger": "ledger.jsonl", "batches": "b", "approvals": "a"}
    (tmp_path / "c.json").write_text(json.dumps(conf))
    return tmp_path / "c.json"


def at(s):
    return datetime.fromisoformat(s + "+02:00")


def test_business_days_skip_weekend_and_holiday(cfg):
    c = M.load_config(cfg)
    # Mon 28.09 + 4 business days, Fri 02.10 is a holiday -> Mon 05.10, time of day kept
    assert M.add_after(c, at("2026-09-28T09:00"), "4bd") == at("2026-10-05T09:00")
    assert M.add_after(c, at("2026-09-28T09:00"), "10m") == at("2026-09-28T09:10")


def test_next_step_waits_for_cadence_and_stops_on_reply(cfg):
    c = M.load_config(cfg)
    assert M.next_due(c, "1", []) == (0, None)
    sent = [{"event": "sent", "recipient_id": "1", "step": "initial", "sent_at": "2026-09-28T09:00:00+02:00", "message_id": "<m1@x>"}]
    assert M.next_due(c, "1", sent) == (1, at("2026-10-05T09:00"))
    reply = {"event": "inbound", "recipient_id": "1", "kind": "reply", "ts": "2026-09-29T10:00:00+02:00"}
    assert M.next_due(c, "1", sent + [reply])[0] is None
    auto = dict(reply, kind="auto_reply")
    assert M.next_due(c, "1", sent + [auto])[0] == 1


def test_plan_renders_follow_up_in_thread(cfg):
    c = M.load_config(cfg)
    c["suppression_db"] = None
    c["ledger"].write_text(json.dumps({"event": "sent", "recipient_id": "1", "step": "initial",
                                       "sent_at": "2026-09-28T09:00:00+02:00", "message_id": "<m1@x>"}) + "\n")
    assert M.plan(c, at("2026-10-02T09:00"))[0] is None          # not due yet
    bid, _ = M.plan(c, at("2026-10-05T09:00"))
    it = json.loads((c["batches"] / f"{bid}.json").read_text())["items"][0]
    assert (it["step"], it["subject"], it["in_reply_to"]) == ("fu1", "Re: Pflegekraft für Intensivstation", "<m1@x>")


def test_do_not_contact_domain_blocks_to_and_cc(cfg):
    (cfg.parent / "dnc.json").write_text(json.dumps([{"match": "a.de", "clinic": "Klinik A", "by": "V", "date": "2026-09-28", "why": "x"}]))
    cfg.write_text(json.dumps(json.loads(cfg.read_text()) | {"do_not_contact": "dnc.json"}))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    assert M.suppressed(c, ["pd@a.de", "St@A.de", "x@b.de"]) == {"pd@a.de", "St@A.de"}
    bid, report = M.plan(c, at("2026-09-29T09:00"))
    assert bid is None and "not planned" in report[0]


def test_plan_only_selected_recipients(cfg):
    rec = json.loads((cfg.parent / "recipients.json").read_text())
    (cfg.parent / "recipients.json").write_text(json.dumps(rec + [dict(rec[0], id="2", clinic="Klinik B", to=["pd@b.de"], cc=[])]))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    bid, report = M.plan(c, at("2026-09-29T09:00"), only=["2"])
    assert [it["recipient_id"] for it in json.loads((c["batches"] / f"{bid}.json").read_text())["items"]] == ["2"]
    assert "1 other recipients not looked at" in report[-1]
    with pytest.raises(M.MailerError, match="no recipient with id"):
        M.plan(c, at("2026-09-29T09:00"), only=["3"])


def test_unfilled_placeholder_fails(cfg):
    c = M.load_config(cfg)
    with pytest.raises(M.MailerError, match="placeholders"):
        M.render(c["cadence"][0], {"id": "1", "vars": {"ANREDE": "x"}})


def test_guard_allowlist_approval_and_edits(cfg):
    c = M.load_config(cfg)
    c["suppression_db"] = None
    bid, _ = M.plan(c, at("2026-09-28T09:00"))
    batch = json.loads((c["batches"] / f"{bid}.json").read_text())
    with pytest.raises(M.MailerError, match="not in the allowlist"):
        M.guard(c, batch, live=False)
    with pytest.raises(M.MailerError, match="no approval"):
        M.guard(c, batch, live=True)
    pending = c["approvals"] / f"{bid}.pending.json"
    pending.rename(c["approvals"] / f"{bid}.json")
    M.guard(c, batch, live=True)
    batch["items"][0]["body"] += "edited"
    with pytest.raises(M.MailerError, match="edited after planning"):
        M.guard(c, batch, live=True)


def test_html_signature_and_per_recipient_attachment(cfg):
    d = cfg.parent
    (d / "t0.txt").write_text("Betreff: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nStellen:\n[STELLEN]\n\nMit freundlichen Grüßen\n")
    (d / "sig.png").write_bytes(b"\x89PNG fake image")
    (d / "kp").mkdir()
    (d / "kp" / "p_1.pdf").write_bytes(b"%PDF-1.4 clinic 1")
    conf = json.loads(cfg.read_text())
    conf["cadence"][0]["attachments"] = [{"path": "kp/p_[KP].pdf", "name": "Profil.pdf"}]
    conf["signature"] = {"text": "Daria\nNDT Group", "image": "sig.png", "alt": "Daria · NDT Group", "width": 460, "height": 130}
    cfg.write_text(json.dumps(conf))
    rec = json.loads((d / "recipients.json").read_text())
    rec[0]["vars"].update({"STELLEN": "– Stelle A & B\n  https://a.de/x?y=1&z=2", "KP": "1"})
    rec[0]["html_vars"] = {"STELLEN": '– <a href="https://a.de/x?y=1&amp;z=2">Stelle A &amp; B</a>'}
    (d / "recipients.json").write_text(json.dumps(rec))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    bid, _ = M.plan(c, at("2026-09-28T09:00"))
    it = json.loads((c["batches"] / f"{bid}.json").read_text())["items"][0]
    assert it["body"].endswith("Mit freundlichen Grüßen\nDaria\nNDT Group\n") and "https://a.de/x?y=1&z=2" in it["body"]
    assert '<a href="https://a.de/x?y=1&amp;z=2">Stelle A &amp; B</a>' in it["html"] and f'src="cid:{c["signature"]["cid"]}"' in it["html"]
    assert [a["name"] for a in it["attachments"]] == ["Profil.pdf"] and it["attachments"][0]["path"].endswith("kp/p_1.pdf")
    parts = {p.get_content_type(): p for p in M.build_message(c, it).walk()}
    assert {"text/plain", "text/html", "image/png", "application/pdf"} <= set(parts)
    assert parts["image/png"]["Content-ID"] == f"<{c['signature']['cid']}>" and parts["application/pdf"].get_filename() == "Profil.pdf"
    (d / "sig.png").write_bytes(b"\x89PNG another image")
    with pytest.raises(M.MailerError, match="changed after planning"):
        M.build_message(c, it)
    rec[0]["vars"]["KP"] = "2"
    (d / "recipients.json").write_text(json.dumps(rec))
    with pytest.raises(M.MailerError, match="does not exist"):
        M.plan(M.load_config(cfg) | {"suppression_db": None}, at("2026-09-28T09:00"))


GRAPH_ENV = {"MICROSOFT_GRAPH_MSAL_CACHE": "/root-only/cache.json", "MICROSOFT_GRAPH_CLIENT_ID": "cid",
             "MICROSOFT_GRAPH_AUTHORITY": "https://login.example", "MICROSOFT_GRAPH_SCOPES": "Mail.Read offline_access"}


def test_graph_watch_logs_reply_by_thread_and_skips_known(cfg, monkeypatch):
    c = M.load_config(cfg)
    c.update({"watch_via": "graph", "watch_folders": ["inbox"]})
    c["ledger"].write_text(json.dumps({"event": "sent", "recipient_id": "1", "step": "initial", "to": ["pd@a.de"], "cc": [],
                                       "sent_at": "2026-09-28T09:00:00+02:00", "message_id": "<m1@x>"}) + "\n")
    reply = b"From: pd@a.de\r\nTo: me@example.org\r\nSubject: AW: Pflegekraft\r\nMessage-ID: <r1@a.de>\r\nIn-Reply-To: <m1@x>\r\n\r\nJa, gerne.\r\n"
    urls, fetched = [], []
    fake = types.SimpleNamespace(GRAPH="https://graph.example/v1.0", get_token_for=lambda user, g: ("tok", None),
                                 graph_get=lambda url, tok: urls.append(url) or {"value": [{"id": "A1", "internetMessageId": "<r1@a.de>"}]})
    monkeypatch.setitem(sys.modules, "email_dump_graph", fake)
    monkeypatch.setattr(M, "load_env", lambda: GRAPH_ENV)
    monkeypatch.setattr(M, "graph_raw", lambda url, tok: fetched.append(url) or reply)
    monkeypatch.setattr(M, "RAW", {})
    assert [(e["kind"], e["recipient_id"], e["matched_by"]) for e in M.watch(c)] == [("reply", "1", "thread")]
    assert "/me/mailFolders/inbox/messages" in urls[0] and "receivedDateTime%20ge%202026-09-27T22%3A00%3A00Z" in urls[0]
    assert fetched == ["https://graph.example/v1.0/me/messages/A1/$value"]
    assert M.watch(c) == [] and len(fetched) == 1          # already in the ledger: listed, not downloaded again
    assert M.next_due(c, "1", M.read_ledger(c))[0] is None  # the reply ends the sequence


def test_graph_watch_without_root_says_sudo(cfg, monkeypatch):
    def denied(user, g):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setitem(sys.modules, "email_dump_graph", types.SimpleNamespace(GRAPH="x", get_token_for=denied, graph_get=None))
    monkeypatch.setattr(M, "load_env", lambda: GRAPH_ENV)
    c = M.load_config(cfg)
    with pytest.raises(M.MailerError, match="sudo -E"):
        M.graph_token(c)


def test_no_watch_is_refused_live(cfg):
    c = M.load_config(cfg)
    with pytest.raises(M.MailerError, match="allowlist tests only"):
        M.send(c, "any", live=True, watch_inbox=False)


def test_test_campaign_redirects_to_allowlist(cfg):
    c = M.load_config(cfg)
    c["suppression_db"] = None
    c["redirect_to_allowlist"] = True
    bid, _ = M.plan(c, at("2026-09-28T09:00"))
    batch = json.loads((c["batches"] / f"{bid}.json").read_text())
    it = batch["items"][0]
    assert it["to"] == ["cat1@example.org"] and it["cc"] == ["cat2@example.org"] and it["real_to"] == ["pd@a.de"]
    M.guard(c, batch, live=False)


# ---------- scheduled batches: announcement, send times, operator commands ----------

from datetime import timedelta  # noqa: E402
from email.message import EmailMessage  # noqa: E402
import email as _email  # noqa: E402
import random  # noqa: E402
import signal  # noqa: E402
import urllib.error  # noqa: E402

OPS = ["op1@example.org", "op2@example.net"]


def scheduled_config(cfg, monkeypatch, cadence):
    """Three clinics, window 08:00-12:00, announcement template, operators and a classifier; the plan PDF is a stub."""
    d = cfg.parent
    (d / "announce.txt").write_text("Betreff: Рассылка [KAMPAGNE]: план на [DATUM]\n\nАнонс в [ANKUENDIGUNG], письма [ERSTER]–[LETZTER]:\n[PLAN]\n\n"
                                    "[ABLAUF]\n\nКоманды до [ENDE].\n\n[NOTIZEN]\n")
    (d / "notes.txt").write_text("Почему так:\n- причина один\n- причина два\n")
    (d / "t2.txt").write_text("Betreff: Re: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nLetzte Nachfrage.\n")
    (d / "recipients.json").write_text(json.dumps([
        {"id": str(k), "clinic": f"Klinik {n}", "to": [f"pd{k}@example.org"], "cc": [],
         "vars": {"ANREDE": f"Sehr geehrte Frau {n}", "BEREICH": "Intensivstation"}} for k, n in ((1, "A"), (2, "B"), (3, "C"))]))
    (d / "allowlist.txt").write_text("\n".join(["pd1@example.org", "pd2@example.org", "pd3@example.org"] + OPS) + "\n")
    conf = json.loads(cfg.read_text()) | {
        "window": {"weekdays": [1, 2, 3, 4, 5], "from": "08:00", "to": "12:00"}, "pause_seconds": [90, 180],
        "operators": [o.upper() if k == 0 else o for k, o in enumerate(OPS)], "command_poll_seconds": 60,
        "announce": {"window_minutes": 60, "template": "announce.txt", "notes": "notes.txt"},
        "classifier": {"model": "claude-haiku-4-5", "claude_bin": "/bin/false", "run_as": "claude", "timeout_seconds": 5},
        "watch_folders": ["inbox"], "cadence": cadence}
    cfg.write_text(json.dumps(conf))

    def fake_pdf(c, batch, rows, notes, example, info, pdf_path):
        pdf_path.write_bytes(b"%PDF-1.4 plan " + ",".join(r["recipient_id"] for r in rows).encode())
        return pdf_path, {}
    monkeypatch.setattr(M.mailer_announce, "render_pdf", fake_pdf)
    c = M.load_config(cfg)
    c["suppression_db"] = None
    return c


@pytest.fixture
def scfg(cfg, monkeypatch):
    """A scheduled campaign with first letters only."""
    return scheduled_config(cfg, monkeypatch, [{"step": "initial", "template": "t0.txt"}])


@pytest.fixture
def fcfg(cfg, monkeypatch):
    """A scheduled campaign with two follow-ups, 3 and 5 business days apart (Fri 02.10 is a holiday), polled hourly."""
    c = scheduled_config(cfg, monkeypatch, [{"step": "initial", "template": "t0.txt"},
                                            {"step": "fu1", "after": "3bd", "in_thread": True, "template": "t1.txt"},
                                            {"step": "fu2", "after": "5bd", "in_thread": True, "template": "t2.txt"}])
    c["command_poll_seconds"] = 3600
    return c


def test_send_times_are_odd_minutes_with_pauses_inside_the_window(scfg):
    for seed in range(300):
        random.seed(seed)
        times = M.schedule(scfg, at("2026-09-29T10:00:00"), 10)
        assert times[0] - at("2026-09-29T10:00:00") >= timedelta(seconds=90)
        assert all(t.minute % 5 and t.microsecond == 0 and M.in_window(scfg, t) for t in times)
        assert all(timedelta(seconds=90) <= b - a <= timedelta(seconds=180) for a, b in zip(times, times[1:]))
    with pytest.raises(M.MailerError, match="outside the send window"):
        M.schedule(scfg, at("2026-09-29T11:50:00"), 10)
    with pytest.raises(M.MailerError, match="widen pause_seconds"):
        M.after_pause(at("2026-09-29T10:04:30"), 30, 60)            # 10:05:00-10:05:30: only a round minute


def test_scheduled_plan_announces_and_approval_covers_times(scfg):
    random.seed(1)
    bid, report = M.plan(scfg, at("2026-09-28T17:00"), announce_at=at("2026-09-29T09:00:00"))
    batch = json.loads((scfg["batches"] / f"{bid}.json").read_text())
    ann = batch["announce"]
    assert ann["to"] == OPS and batch["operators"] == OPS and ann["send_at"] == "2026-09-29T09:00:00+02:00"
    assert ann["subject"] == "Рассылка c: план на вт 29.09.2026" and "Klinik B — pd2@example.org" in ann["body"]
    assert "<li>причина один</li>" in ann["html"] and ann["attachments"][0]["name"] == "Plan_c_2026-09-29.pdf"
    assert all(it["send_at"] > "2026-09-29T10:01:29" for it in batch["items"]) and any("announcement" in r for r in report)
    txt = (scfg["batches"] / f"{bid}.txt").read_text()
    assert txt.startswith("=== 0. Анонс · announce") and txt.count("Send at: ") == 4
    ap = json.loads((scfg["approvals"] / f"{bid}.pending.json").read_text())
    assert ap["announce"]["to"] == OPS and [i["send_at"] for i in ap["items"]] == [i["send_at"] for i in batch["items"]]
    (scfg["approvals"] / f"{bid}.pending.json").rename(scfg["approvals"] / f"{bid}.json")
    M.guard(scfg, batch, live=True)
    moved = json.loads(json.dumps(batch))
    moved["items"][1]["send_at"] = "2026-09-29T10:07:07+02:00"
    with pytest.raises(M.MailerError, match="does not match"):
        M.guard(scfg, moved, live=True)
    with pytest.raises(M.MailerError, match="not in the future"):
        M.plan(scfg, at("2026-09-29T09:00"), announce_at=at("2026-09-29T09:00:00"))
    scfg["allowlist"].write_text("pd1@example.org\npd2@example.org\npd3@example.org\n")
    with pytest.raises(M.MailerError, match="not in the allowlist"):
        M.guard(scfg, batch, live=False)


def mail(frm, subject, text, t, mid, **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, "me@example.org", subject, mid
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(text)
    return t, "inbox", m.as_bytes()


class World:
    """A fake clock, SMTP server, inbox and classifier around one scheduled batch."""

    def __init__(self, c, monkeypatch, start, intents=None):
        self.c, self.now, self.sent, self.inbox, self.asked = c, at(start), [], [], []
        self.fail_at, self.broken = None, True          # the inbox fails once at fail_at; "boom" breaks the classifier
        self.intents = {"стоп": ("stop", []), "не отправляй в klinik b": ("skip", ["2"]), "статус": ("status", []),
                        "какой статус рассылки?": ("status", []), "перенеси": ("other_command", []), "спасибо": ("not_command", [])}
        self.intents.update(intents or {})
        for name, fn in (("now_in", lambda cfg: self.now), ("sleep", self.sleep), ("smtp_send", self.smtp),
                         ("inbox_messages", self.messages), ("classify_command", self.classify),
                         ("mailbox", lambda a: {"ADDRESS": a}), ("check_inbox", lambda cfg, box: None)):
            monkeypatch.setattr(M, name, fn)

    def sleep(self, s):
        assert s >= 0
        self.now += timedelta(seconds=s)

    def smtp(self, box, msg):
        self.sent.append((self.now, msg))
        return {}

    def messages(self, cfg, box, since, seen):
        if self.fail_at and self.now >= self.fail_at:
            self.fail_at = None
            raise urllib.error.URLError("network down")
        out = []
        for t, folder, raw in self.inbox:
            mid = _email.message_from_bytes(raw)["Message-ID"]
            if since <= t <= self.now and mid not in seen:
                out.append((folder, raw))
        return out

    def classify(self, cfg, subject, text, clinics, left=None):
        self.asked.append(text)
        if self.broken and "boom" in text:
            raise M.MailerError("classifier: claude exited 1: overloaded")
        intent, ids = next((v for k, v in self.intents.items() if text.lower().startswith(k)), ("not_command", []))
        return {"intent": intent, "recipient_ids": ids, "unknown_ids": [], "why": "test"}

    def plan(self, announce="2026-09-29T09:00:00", start=None):
        random.seed(7)
        bid, _ = M.plan(self.c, at("2026-09-28T17:00"), announce_at=at(announce), start_at=start and at(start))
        self.bid = bid
        self.batch = json.loads((self.c["batches"] / f"{bid}.json").read_text())
        return self.batch

    def to_clinics(self):
        return [(t, m["To"]) for t, m in self.sent if m["To"].startswith("pd")]

    def to_ops(self):
        return [(t, m) for t, m in self.sent if m["To"] == ", ".join(OPS)]

    def events(self, kind):
        return [e for e in M.read_ledger(self.c) if e["event"] == kind]


def test_scheduled_send_announces_at_nine_and_sends_on_time(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    M.send(scfg, w.bid, live=False)
    ann_t, ann = w.to_ops()[0]
    assert ann_t == at("2026-09-29T09:00:00") and ann["Subject"].startswith("Рассылка c: план")
    assert [p.get_filename() for p in ann.iter_attachments()] == ["Plan_c_2026-09-29.pdf"]
    assert [(t.isoformat(), to) for t, to in w.to_clinics()] == [(it["send_at"], it["to"][0]) for it in batch["items"]]
    done_t, done = w.to_ops()[-1]
    assert len(w.to_ops()) == 2 and "рассылка завершена" in done["Subject"] and "Писем ушло: 3 из 3" in done.get_content()
    assert done["Auto-Submitted"] == "auto-generated" and [e["kind"] for e in w.events("notice")] == ["done"]
    assert w.asked == ["Какой статус рассылки?"]                      # the start-up self-check only


def test_stop_before_the_first_letter_cancels(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    w.inbox.append(mail("Valentyn <op2@example.net>", "Re: Рассылка c", "Стоп, отменяй.\n\n> old quoted plan\n", at("2026-09-29T09:20:00"), "<c1@op>"))
    w.inbox.append(mail("op1@example.org", "стоп", "стоп", at("2026-09-29T08:59:00"), "<early@op>"))     # before the announcement
    M.send(scfg, w.bid, live=False)
    assert w.to_clinics() == []
    (t, reply), = w.to_ops()[1:]
    assert t < at("2026-09-29T09:22:00") and reply["In-Reply-To"] == "<c1@op>" and reply["To"] == ", ".join(OPS)
    assert "Рассылка отменена" in reply.get_content() and reply["Subject"] == "Re: Рассылка c"
    assert w.asked[1:] == ["Стоп, отменяй."]                           # quoted history cut, the early mail not read
    (h,) = w.events("halt")
    assert h["by"] == "op2@example.net" and h["command_id"] == "<c1@op>"
    with pytest.raises(M.MailerError, match="was halted"):
        M.send(scfg, w.bid, live=False)


def test_stop_during_the_batch_halts_and_skip_drops_one_clinic(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    first = datetime.fromisoformat(batch["items"][0]["send_at"])
    w.inbox.append(mail("op1@example.org", "Klinik B", "Не отправляй в Klinik B", first - timedelta(minutes=5), "<s1@op>"))
    w.inbox.append(mail("op1@example.org", "стоп", "стоп", first + timedelta(seconds=30), "<s2@op>"))
    M.send(scfg, w.bid, live=False)
    assert [to for _, to in w.to_clinics()] == ["pd1@example.org"]
    replies = [m.get_content() for _, m in w.to_ops()[1:]]
    assert "Убрано: Klinik B — больше ничего не уйдёт" in replies[0] and "Рассылка остановлена. Писем ушло: 1; не уйдёт: 1" in replies[1]
    assert [e["recipient_id"] for e in w.events("skip")] == ["2"] and len(w.events("halt")) == 1
    assert "Klinik B — ничего не уйдёт: убрано по письму op1@example.org" in replies[1]
    assert "Klinik C — ничего не уйдёт: рассылка остановлена" in replies[1]


def test_status_other_command_and_chat_change_nothing(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    for k, text in enumerate(["статус?", "перенеси всё на 11", "спасибо, отлично"]):
        w.inbox.append(mail("op2@example.net", "план", text, at("2026-09-29T09:10:00") + timedelta(minutes=k), f"<q{k}@op>"))
    w.inbox.append(mail("op1@example.org", "Automatische Antwort: план", "Bin nicht da", at("2026-09-29T09:15:00"), "<auto@op>",
                        Auto_Submitted="auto-replied"))
    w.inbox.append(mail("stranger@example.com", "стоп", "стоп", at("2026-09-29T09:16:00"), "<x@y>"))
    M.send(scfg, w.bid, live=False)
    assert len(w.to_clinics()) == 3
    replies = [m.get_content() for _, m in w.to_ops()[1:-1]]
    assert len(replies) == 3 and "Статус ниже, ничего не изменено" in replies[0] and "Klinik A — первое письмо вт 29.09 10:0" in replies[0]
    assert "такой команды письмом нет, нужен оператор" in replies[1] and "не похоже на команду" in replies[2]
    assert [e["intent"] for e in w.events("command")] == ["status", "other_command", "not_command", "auto_reply"]


def test_classifier_failure_halts_and_tells_the_operators(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    w.inbox.append(mail("op1@example.org", "?", "boom", datetime.fromisoformat(batch["items"][0]["send_at"]) + timedelta(seconds=5), "<b1@op>"))
    with pytest.raises(M.MailerError, match="could not read the mail from op1@example.org"):
        M.send(scfg, w.bid, live=False)
    assert len(w.to_clinics()) == 1
    t, notice = w.to_ops()[-1]
    assert "рассылка прервана" in notice["Subject"] and "overloaded" in notice.get_content()
    assert [e["intent"] for e in w.events("command")] == ["classifier_failed"] and [h["kind"] for h in w.events("halt")] == ["error"]


def test_bounce_halts_the_batch(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    first = datetime.fromisoformat(batch["items"][0]["send_at"])
    w.inbox.append(mail("MAILER-DAEMON@example.org", "Undelivered Mail", "pd1@example.org: user unknown", first + timedelta(seconds=40), "<nd@x>"))
    with pytest.raises(M.MailerError, match="HALT: bounce"):
        M.send(scfg, w.bid, live=False)
    assert len(w.to_clinics()) == 1 and "рассылка остановлена" in w.to_ops()[-1][1]["Subject"]
    assert [h["kind"] for h in w.events("halt")] == ["delivery"]
    with pytest.raises(M.MailerError, match="was halted"):          # a bounce is not resumed: only a new plan continues
        M.send(scfg, w.bid, live=False)


def test_start_too_late_missed_and_failed_self_check(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T09:05:00")
    w.plan()
    with pytest.raises(M.MailerError, match="too late"):
        M.send(scfg, w.bid, live=False)
    w.now = at("2026-09-29T08:59:59")
    w.intents["какой статус рассылки?"] = ("stop", [])
    with pytest.raises(M.MailerError, match="self-check"):
        M.send(scfg, w.bid, live=False)
    assert w.sent == []
    M.append_ledger(scfg, {"event": "announced", "batch_id": w.bid, "sent_at": "2026-09-29T09:00:00+02:00"})
    w.now = at("2026-09-29T10:30:00")
    with pytest.raises(M.MailerError, match="has passed"):
        M.send(scfg, w.bid, live=False)


def test_a_late_letter_waits_out_a_round_minute(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    batch["items"][0]["send_at"] = "2026-09-29T10:05:10+02:00"       # allowlist mode: the approval is not read
    (scfg["batches"] / f"{w.bid}.json").write_text(json.dumps(batch))
    M.send(scfg, w.bid, live=False)
    t = next(t for t, to in w.to_clinics() if to == "pd1@example.org")
    assert t.strftime("%H:%M") == "10:06" and 1 <= t.second <= 21


def test_fresh_text_cuts_quotes_and_the_sender_line(scfg):
    for body in ("стоп\n\nOn Tue, 29 Sep 2026 at 09:00, Daria <me@example.org> wrote:\n> план\n",
                 "стоп\r\n________________________________\r\nVon: Daria <me@example.org>\r\n",
                 "стоп\n\nвт, 29 сент. 2026 г. в 09:00, Daria <me@example.org>:\n> план\n"):
        _, _, raw = mail("op1@example.org", "Re: план", body, None, "<f@op>")
        assert M.fresh_text(scfg, _email.message_from_bytes(raw, policy=M.email.policy.default)) == "стоп"


def test_classifier_parses_the_cli_envelope(scfg, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        out = {"type": "result", "is_error": False,
               "result": '```json\n{"intent": "skip", "recipient_ids": ["2", "99"], "why": "убрать B"}\n```'}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
    monkeypatch.setattr(M.subprocess, "run", run)
    monkeypatch.setenv("CLAUDECODE", "1")
    clinics = [{"id": "1", "clinic": "Klinik A"}, {"id": "2", "clinic": "Klinik B"}]
    assert M.classify_command(scfg, "s", "без B", clinics) == {"intent": "skip", "recipient_ids": ["2"], "unknown_ids": ["99"], "why": "убрать B"}
    cmd, kw = calls[0]
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5" and "CLAUDECODE" not in kw["env"] and '"без B"' in kw["input"]
    assert {"--restricted", "--strict-mcp-config", "--disable-slash-commands"} <= set(cmd) and cmd[cmd.index("--tools") + 1] == ""
    monkeypatch.setattr(M.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout='{"result": "{\\"intent\\": \\"dance\\"}"}', stderr=""))
    with pytest.raises(M.MailerError, match="unexpected answer"):
        M.classify_command(scfg, "s", "x", clinics)


def test_watch_never_logs_an_operators_mail_as_a_clinic_answer(scfg, monkeypatch):
    """A test batch redirects letters to allowlist boxes that can also be operators: their commands stay commands."""
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    M.append_ledger(scfg, {"event": "sent", "batch_id": w.bid, "recipient_id": "1", "step": "initial", "to": ["op1@example.org"],
                           "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    w.inbox.append(mail("op1@example.org", "Re: план", "статус", at("2026-09-29T10:03:00"), "<o1@op>", In_Reply_To="<m1@x>"))
    assert M.watch(scfg) == []


def test_the_mail_the_classifier_could_not_read_is_read_again_on_resume(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    w.inbox.append(mail("op1@example.org", "?", "boom стоп", at("2026-09-29T09:30:00"), "<b2@op>"))
    with pytest.raises(M.MailerError, match="could not read"):
        M.send(scfg, w.bid, live=False)
    w.broken, w.now = False, at("2026-09-29T09:40:00")
    w.intents["boom стоп"] = ("stop", [])
    M.send(scfg, w.bid, live=False)                                  # resumes: the unread stop is read and obeyed
    assert w.to_clinics() == [] and [h["kind"] for h in w.events("halt")] == ["error", "operator"]
    assert [e["intent"] for e in w.events("command")] == ["classifier_failed", "stop"]
    assert [e["kind"] for e in w.events("notice")] == ["halt", "resumed"]


def test_sigterm_and_sighup_end_a_batch_like_ctrl_c():
    for sig in (signal.SIGTERM, signal.SIGHUP):
        with pytest.raises(SystemExit, match=sig.name):
            M.ended(sig, None)


# ---------- scheduled batches with follow-ups ----------

def clinic_letters(w):
    return [(t, m["To"], m["Subject"], m["In-Reply-To"], m["References"]) for t, m in w.sent if m["To"].startswith("pd")]


def test_plan_carries_the_follow_ups_and_a_report_before_each_round(fcfg):
    random.seed(3)
    bid, report = M.plan(fcfg, at("2026-09-28T17:00"), announce_at=at("2026-09-28T18:00:00"), start_at=at("2026-09-29T09:00:00"))
    batch = json.loads((fcfg["batches"] / f"{bid}.json").read_text())
    items = batch["items"]
    assert [(it["recipient_id"], it["step"]) for it in items[:3]] == [("1", "initial"), ("2", "initial"), ("3", "initial")]
    assert [it["round"] for it in items] == [0] * 3 + [1] * 3 + [2] * 3
    first = {it["recipient_id"]: datetime.fromisoformat(it["send_at"]) for it in items if it["round"] == 0}
    assert all(at("2026-09-29T09:01:29") < t < at("2026-09-29T09:10:00") for t in first.values())
    for it in items[3:]:            # Fri 02.10 is a holiday: +3bd is Mon 05.10, +5bd more is Mon 12.10, same time of day
        t = datetime.fromisoformat(it["send_at"])
        assert t.date().isoformat() == ("2026-10-05" if it["step"] == "fu1" else "2026-10-12")
        assert t.time() == first[it["recipient_id"]].time() and it["in_thread"] and "in_reply_to" not in it
    assert batch["start_at"] == "2026-09-29T09:00:00+02:00"
    assert [(r["step"], r["send_at"]) for r in batch["reports"]] == [("fu1", "2026-10-05T08:00:00+02:00"),
                                                                      ("fu2", "2026-10-12T08:00:00+02:00")]
    ann = batch["announce"]
    assert ann["send_at"] == "2026-09-28T18:00:00+02:00" and "Фоллоу-ап 1 — пн 05.10" in ann["body"] and "вам в 08:00" in ann["body"]
    assert "Команды до пн 12.10" in ann["body"]
    ap = json.loads((fcfg["approvals"] / f"{bid}.pending.json").read_text())
    assert ap["reports"] == batch["reports"] and ap["start_at"] == batch["start_at"] and len(ap["items"]) == 9
    assert "fu2: 3 letters Mon 12.10" in "\n".join(report)
    txt = (fcfg["batches"] / f"{bid}.txt").read_text()
    assert "=== Reports before the follow-up rounds" in txt and txt.count("In-Reply-To: the clinic's letter before this one") == 6
    with pytest.raises(M.MailerError, match="less than 1:00:00 after the announcement"):
        M.plan(fcfg, at("2026-09-28T17:00"), announce_at=at("2026-09-29T08:30:00"), start_at=at("2026-09-29T09:00:00"))


def test_follow_ups_go_by_themselves_in_thread_with_a_report_before_each_round(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-28T17:50:00", intents={"не отправляй в klinik c": ("skip", ["3"])})
    batch = w.plan(announce="2026-09-28T18:00:00", start="2026-09-29T09:00:00")
    w.inbox.append(mail("pd2@example.org", "AW: Pflegekraft für Intensivstation", "Danke, wir melden uns.", at("2026-09-30T10:00:00"), "<r2@b>"))
    w.inbox.append(mail("op2@example.net", "Klinik C", "Не отправляй в Klinik C", at("2026-10-06T12:00:00"), "<s3@op>"))
    M.send(fcfg, w.bid, live=False)
    assert [(to, subject.startswith("Re:")) for _, to, subject, _, _ in clinic_letters(w)] == [
        ("pd1@example.org", False), ("pd2@example.org", False), ("pd3@example.org", False),   # Tue 29.09
        ("pd1@example.org", True), ("pd3@example.org", True),                                   # Mon 05.10: Klinik B answered
        ("pd1@example.org", True)]                                                              # Mon 12.10: Klinik C taken out
    planned = {(it["recipient_id"], it["step"]): it["send_at"] for it in batch["items"]}
    sent = {(e["recipient_id"], e["step"]): e for e in w.events("sent")}
    assert all(e["sent_at"] == planned[k] for k, e in sent.items())
    assert sent[("1", "fu1")]["in_reply_to"] == sent[("1", "initial")]["message_id"]
    assert sent[("1", "fu2")]["in_reply_to"] == sent[("1", "fu1")]["message_id"]
    assert clinic_letters(w)[-1][4] == f'{sent[("1", "initial")]["message_id"]} {sent[("1", "fu1")]["message_id"]}'
    ops = [(t, m["Subject"], m.get_body(preferencelist=("plain",)).get_content()) for t, m in w.to_ops()]
    assert ops[0][0] == at("2026-09-28T18:00:00") and "план" in ops[0][1]
    assert [e["round"] for e in w.events("notice") if e["kind"] == "round"] == [0, 1]
    assert "первые письма — ушло 3 из 3" in ops[1][1]
    assert "Дальше фоллоу-ап 1: предварительный отчёт пн 05.10 08:00, письма с пн 05.10 09:0" in ops[1][2]
    assert [(e["step"], e["sent_at"], e["going"]) for e in w.events("report")] == [
        ("fu1", "2026-10-05T08:00:00+02:00", ["1", "3"]), ("fu2", "2026-10-12T08:00:00+02:00", ["1"])]
    fu1 = next(c for _, s, c in ops if "фоллоу-ап 1 пн 05.10" in s)
    assert "Не уйдёт:\n  Klinik B — клиника ответила" in fu1 and "Тема: Re: Pflegekraft für Intensivstation" in fu1
    assert "Фоллоу-ап 1 — пн 05.10, с 09:0" in fu1 and ", по одному:" in fu1
    fu2 = next(c for _, s, c in ops if "фоллоу-ап 2 пн 12.10" in s)
    assert "Klinik C — убрано по письму op2@example.net" in fu2 and "Klinik B — клиника ответила" in fu2
    assert "Фоллоу-ап 2 — пн 12.10, в 09:0" in fu2 and "по одному" not in fu2       # one letter: a time, not a span
    assert "рассылка завершена" in ops[-1][1] and "Писем ушло: 6 из 9" in ops[-1][2]
    assert "Klinik A — первое письмо ушло вт 29.09 09:0" in ops[-1][2] and "; фоллоу-ап 1 ушёл пн 05.10 09:0" in ops[-1][2]
    assert [e["intent"] for e in w.events("command")] == ["skip"] and w.events("halt") == []


def test_a_stop_between_rounds_cancels_every_follow_up(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-28T17:50:00")
    w.plan(announce="2026-09-28T18:00:00", start="2026-09-29T09:00:00")
    w.inbox.append(mail("op1@example.org", "Re: план", "стоп", at("2026-10-01T15:00:00"), "<st@op>"))
    M.send(fcfg, w.bid, live=False)
    assert len(clinic_letters(w)) == 3 and w.events("report") == []
    reply = w.to_ops()[-1][1].get_content()
    assert "Рассылка остановлена. Писем ушло: 3; не уйдёт: 6, фоллоу-апы тоже." in reply
    assert "Klinik A — первое письмо ушло вт 29.09 09:0" in reply and "дальше ничего не уйдёт: рассылка остановлена" in reply
    assert "Команды — письмом" not in reply                          # the process ends: no command list
    assert [e["kind"] for e in w.events("notice")] == ["round"]      # no "done": the stop's answer said it all
    with pytest.raises(M.MailerError, match="was halted"):
        M.send(fcfg, w.bid, live=False)


def test_an_error_halt_is_resumed_by_the_same_command(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-28T17:50:00")
    w.plan(announce="2026-09-28T18:00:00", start="2026-09-29T09:00:00")
    w.fail_at = at("2026-10-01T03:00:00")
    with pytest.raises(urllib.error.URLError):
        M.send(fcfg, w.bid, live=False)
    (h,) = w.events("halt")
    assert h["kind"] == "error" and "network down" in h["reason"]
    notice = w.to_ops()[-1][1]
    assert "рассылка прервана" in notice["Subject"] and "той же командой" in notice.get_content()
    w.now = at("2026-10-01T09:30:00")
    M.send(fcfg, w.bid, live=False)
    assert len(clinic_letters(w)) == 9 and len(w.events("announced")) == 1
    assert [e["kind"] for e in w.events("notice")] == ["round", "halt", "resumed", "round", "done"]


def test_a_resume_after_a_missed_send_time_needs_a_new_plan(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-28T17:50:00")
    w.plan(announce="2026-09-28T18:00:00", start="2026-09-29T09:00:00")
    w.fail_at = at("2026-10-05T08:30:00")                           # after the fu1 report, before its letters
    with pytest.raises(urllib.error.URLError):
        M.send(fcfg, w.bid, live=False)
    w.now = at("2026-10-05T10:00:00")
    with pytest.raises(M.MailerError, match="has passed"):
        M.send(fcfg, w.bid, live=False)
    assert len(clinic_letters(w)) == 3 and [e["event"] for e in M.read_ledger(fcfg) if e["event"] in ("halt", "resumed")] == ["halt"]


def test_a_non_ascii_subject_keeps_its_spaces_on_the_wire(scfg):
    """policy.default would split this subject into two encoded words right at the space before "письма", and a reader
    drops the space between encoded words: the operators saw "первыеписьма"."""
    import email.generator
    import io
    for subject in ("Рассылка nurse79-e2e: первые письма — ушло 3 из 3", "Pflegekraft für Ihre Intensivstation",
                    "Re: Рассылка nurse79: окончательный план, первые письма вт 29.09.2026"):
        for m in (M.operator_mail(scfg, {"operators": OPS}, subject, "x\n"),
                  M.build_message(scfg, {"to": ["pd1@example.org"], "cc": [], "subject": subject, "body": "x\n", "attachments": []})):
            buf = io.BytesIO()
            email.generator.BytesGenerator(buf).flatten(m, linesep="\r\n")      # what smtplib.send_message writes
            assert str(_email.message_from_bytes(buf.getvalue(), policy=_email.policy.default)["Subject"]) == subject
