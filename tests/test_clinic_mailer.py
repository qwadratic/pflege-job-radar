"""tools/clinic_mailer.py: cadence arithmetic, rendering and the send guard (no network)."""
import fcntl
import json
import re
import os
import sys
import threading
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import clinic_mailer as M  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "t0.txt").write_text("Betreff: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nText zu [BEREICH].\n")
    (tmp_path / "t1.txt").write_text("Betreff: Re: Pflegekraft für [BEREICH]\n\n[ANREDE],\n\nNachfrage.\n")
    (tmp_path / "recipients.json").write_text(json.dumps([
        {"id": "1", "clinic": "Klinik A", "to": ["pd@a.example"], "cc": ["st@a.example"], "vars": {"ANREDE": "Sehr geehrte Frau A", "BEREICH": "Intensivstation"}}]))
    (tmp_path / "allowlist.txt").write_text("# test\ncat1@example.org\ncat2@example.org\n")
    conf = {"campaign": "c", "sender": "me@example.org", "sender_name": "Me", "tz": "Europe/Berlin",
            "window": {"weekdays": [1, 2, 3, 4, 5], "from": "08:00", "to": "16:00"}, "holidays": ["2026-10-02"],
            "pause_seconds": [0, 0], "stop_on": ["reply", "stop", "bounce"], "halt_on": ["bounce", "stop"], "watch_folders": ["INBOX"],
            "watch_via": "daria-inbox", "watch_overlap_minutes": 10,
            "cadence": [{"step": "initial", "template": "t0.txt"}, {"step": "fu1", "after": "4bd", "in_thread": True, "template": "t1.txt"}],
            "recipients": "recipients.json", "allowlist": "allowlist.txt", "ledger": "ledger.jsonl", "batches": "b", "approvals": "a"}
    (tmp_path / "c.json").write_text(json.dumps(conf))
    return tmp_path / "c.json"


def plain(m):
    """The text part of a mail to the operators (every one of them is multipart/alternative)."""
    return m.get_body(("plain",)).get_content()


def has_row(text, *cells):
    """A line of `text` holds `cells` in order, aligned: two spaces or more between them (the last one may be a prefix)."""
    return any(re.fullmatch(r"\s*" + r"\s{2,}".join(map(re.escape, cells)) + r".*", line) for line in text.split("\n"))


def at(s):
    return datetime.fromisoformat(s + "+02:00")


ANSWER_OTHER = {"pattern": "other", "addresses": [], "names": [], "phones": [], "already_ours": [], "scope": None, "quote": "", "why": "test"}


class Reader:
    """The model that reads clinic answers: the first rule whose keyword is in the mail text gives its JSON, else "other";
    "boom" in the text breaks it while `broken`. Anything but the answer prompt goes to the real ask_claude."""

    def __init__(self, real):
        self.real, self.rules, self.asked, self.broken = real, [], [], True

    def says(self, key, **fields):
        self.rules.append((key, {**ANSWER_OTHER, **fields}))

    def ask(self, cfg, system, payload):
        if system != M.ANSWER_SYSTEM:
            return self.real(cfg, system, payload)
        self.asked.append(payload)
        if self.broken and "boom" in payload["text"]:
            raise M.MailerError("classifier: claude exited 1: overloaded")
        out = next((f for k, f in self.rules if k in payload["text"]), ANSWER_OTHER)
        return dict(out), json.dumps(out)


@pytest.fixture(autouse=True)
def reader(monkeypatch):
    r = Reader(M.ask_claude)
    monkeypatch.setattr(M, "ask_claude", r.ask)
    return r


def test_business_days_skip_weekend_and_holiday(cfg):
    c = M.load_config(cfg)
    # Mon 28.09 + 4 business days, Fri 02.10 is a holiday -> Mon 05.10, time of day kept
    assert M.add_after(c, at("2026-09-28T09:00"), "4bd") == at("2026-10-05T09:00")
    assert M.add_after(c, at("2026-09-28T09:00"), "10m") == at("2026-09-28T09:10")


def test_next_step_waits_for_cadence_and_stops_on_reply(cfg):
    c = M.load_config(cfg)
    assert M.next_due(c, {"id": "1", "clinic": "Klinik A"}, []) == (0, None)
    sent = [{"event": "sent", "recipient_id": "1", "step": "initial", "sent_at": "2026-09-28T09:00:00+02:00", "message_id": "<m1@x>"}]
    assert M.next_due(c, {"id": "1", "clinic": "Klinik A"}, sent) == (1, at("2026-10-05T09:00"))
    reply = {"event": "inbound", "recipient_id": "1", "kind": "reply", "ts": "2026-09-29T10:00:00+02:00"}
    assert M.next_due(c, {"id": "1", "clinic": "Klinik A"}, sent + [reply])[0] is None
    auto = dict(reply, kind="auto_reply")
    assert M.next_due(c, {"id": "1", "clinic": "Klinik A"}, sent + [auto])[0] == 1


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
    (cfg.parent / "dnc.json").write_text(json.dumps([{"match": "a.example", "clinic": "Klinik A", "by": "V", "date": "2026-09-28", "why": "x"}]))
    cfg.write_text(json.dumps(json.loads(cfg.read_text()) | {"do_not_contact": "dnc.json"}))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    assert M.suppressed(c, ["pd@a.example", "St@A.example", "x@b.example"]) == {"pd@a.example", "St@A.example"}
    bid, report = M.plan(c, at("2026-09-29T09:00"))
    assert bid is None and "not planned" in report[0]


def test_do_not_contact_entry_with_replace_with_swaps_the_address_instead_of_blocking(cfg):
    """LMU, 05.10: write to HR.PflegeTeam@..., not to pflegestellen@... any more."""
    (cfg.parent / "dnc.json").write_text(json.dumps([{"match": "pd@a.example", "replace_with": ["neu@a.example", "ST@a.example"], "clinic": "Klinik A",
                                                      "by": "Klinik A", "date": "2026-10-05", "why": "asked to write elsewhere"}]))
    cfg.write_text(json.dumps(json.loads(cfg.read_text()) | {"do_not_contact": "dnc.json"}))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    assert M.suppressed(c, ["pd@a.example"]) == set() and M.replacements(c, ["PD@a.example", "x@b.example"]) == {"PD@a.example": ["neu@a.example", "ST@a.example"]}
    bid, report = M.plan(c, at("2026-09-29T09:00"))
    item = json.loads((c["batches"] / f"{bid}.json").read_text())["items"][0]
    assert (item["to"], item["cc"]) == (["neu@a.example", "ST@a.example"], []) and "replaces pd@a.example with neu@a.example, ST@a.example" in report[0]


def test_a_scheduled_batch_carries_a_follow_up_that_falls_due_before_its_start(fcfg):
    """Ivan, 2026-10-02/05: a wave's plan carries every follow-up. A clinic written to on Tue has its fu1 due days later;
    a scheduled plan made before that, for a start after it, holds fu1 and fu2, and a start before it still leaves it out."""
    c = fcfg
    M.append_ledger(c, {"event": "sent", "batch_id": "b0", "recipient_id": "1", "clinic": "Klinik A", "step": "initial", "to": ["pd@a.example"],
                        "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    due = M.next_due(c, json.loads(c["recipients"].read_text())[0], M.read_ledger(c))[1]
    now, day = at("2026-09-30T09:00"), due.replace(hour=0, minute=0, second=0, microsecond=0)
    bid, report = M.plan(c, now, only=["1"])
    assert bid is None and f"fu1 due {due:%Y-%m-%d %H:%M}" in report[0]
    bid, report = M.plan(c, now, only=["1"], announce_at=day.replace(hour=7), start_at=day.replace(hour=8))
    assert bid is None and f"fu1 due {due:%Y-%m-%d %H:%M}" in report[0]
    bid, report = M.plan(c, now, only=["1"], announce_at=day.replace(hour=9), start_at=day.replace(hour=11, minute=30))
    assert [i["step"] for i in json.loads((c["batches"] / f"{bid}.json").read_text())["items"]] == ["fu1", "fu2"]


def test_plan_only_selected_recipients(cfg):
    rec = json.loads((cfg.parent / "recipients.json").read_text())
    (cfg.parent / "recipients.json").write_text(json.dumps(rec + [dict(rec[0], id="2", clinic="Klinik B", to=["pd@b.example"], cc=[])]))
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
    conf["signature"] = {"text": "Daria\nBeispiel Group", "image": "sig.png", "alt": "Daria · Beispiel Group", "width": 460, "height": 130}
    cfg.write_text(json.dumps(conf))
    rec = json.loads((d / "recipients.json").read_text())
    rec[0]["vars"].update({"STELLEN": "– Stelle A & B\n  https://a.example/x?y=1&z=2", "KP": "1"})
    rec[0]["html_vars"] = {"STELLEN": '– <a href="https://a.example/x?y=1&amp;z=2">Stelle A &amp; B</a>'}
    (d / "recipients.json").write_text(json.dumps(rec))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    bid, _ = M.plan(c, at("2026-09-28T09:00"))
    it = json.loads((c["batches"] / f"{bid}.json").read_text())["items"][0]
    assert it["body"].endswith("Mit freundlichen Grüßen\nDaria\nBeispiel Group\n") and "https://a.example/x?y=1&z=2" in it["body"]
    assert '<a href="https://a.example/x?y=1&amp;z=2">Stelle A &amp; B</a>' in it["html"] and f'src="cid:{c["signature"]["cid"]}"' in it["html"]
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
    c["ledger"].write_text(json.dumps({"event": "sent", "recipient_id": "1", "step": "initial", "to": ["pd@a.example"], "cc": [],
                                       "sent_at": "2026-09-28T09:00:00+02:00", "message_id": "<m1@x>"}) + "\n")
    reply = b"From: pd@a.example\r\nTo: me@example.org\r\nSubject: AW: Pflegekraft\r\nMessage-ID: <r1@a.example>\r\nIn-Reply-To: <m1@x>\r\n\r\nJa, gerne.\r\n"
    urls, fetched = [], []
    fake = types.SimpleNamespace(GRAPH="https://graph.example/v1.0", get_token_for=lambda user, g: ("tok", None),
                                 graph_get=lambda url, tok: urls.append(url) or {"value": [{"id": "A1", "internetMessageId": "<r1@a.example>"}]})
    monkeypatch.setitem(sys.modules, "email_dump_graph", fake)
    monkeypatch.setattr(M, "load_env", lambda: GRAPH_ENV)
    monkeypatch.setattr(M, "graph_raw", lambda url, tok: fetched.append(url) or reply)
    monkeypatch.setattr(M, "RAW", {})
    assert [(e["kind"], e["recipient_id"], e["matched_by"]) for e in M.watch(c)] == [("reply", "1", "thread")]
    assert "/me/mailFolders/inbox/messages" in urls[0] and "receivedDateTime%20ge%202026-09-27T22%3A00%3A00Z" in urls[0]
    assert fetched == ["https://graph.example/v1.0/me/messages/A1/$value"]
    assert M.watch(c) == [] and len(fetched) == 1          # already in the ledger: listed, not downloaded again
    assert M.next_due(c, {"id": "1", "clinic": "Klinik A"}, M.read_ledger(c))[0] is None  # the reply ends the sequence


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
    assert it["to"] == ["cat1@example.org"] and it["cc"] == ["cat2@example.org"] and it["real_to"] == ["pd@a.example"]
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
        "notify": OPS, "forward": {k: OPS for k in M.FORWARD_KINDS},
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

    def to_ops_of(self, address):
        return [(t, m) for t, m in self.sent if m["To"] == address]

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
    assert len(w.to_ops()) == 2 and "рассылка завершена" in done["Subject"] and "Писем ушло: 3 из 3" in plain(done)
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
    assert "Рассылка отменена" in plain(reply) and reply["Subject"] == "Re: Рассылка c"
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
    replies = [plain(m) for _, m in w.to_ops()[1:]]
    assert "Убрано: Klinik B — больше ничего не уйдёт" in replies[0] and "Рассылка остановлена. Писем ушло: 1; не уйдёт: 1" in replies[1]
    assert [e["recipient_id"] for e in w.events("skip")] == ["2"] and len(w.events("halt")) == 1
    assert has_row(replies[1], "Klinik B", "ничего не уйдёт: убрано по письму op1@example.org")
    assert has_row(replies[1], "Klinik C", "ничего не уйдёт: рассылка остановлена")


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
    replies = [plain(m) for _, m in w.to_ops()[1:-1]]
    assert len(replies) == 3 and "Статус ниже, ничего не изменено" in replies[0] and has_row(replies[0], "Klinik A", "первое письмо вт 29.09 10:0")
    assert "такой команды письмом нет, нужен оператор" in replies[1] and "не похоже на команду" in replies[2]
    assert [e["intent"] for e in w.events("command")] == ["status", "other_command", "not_command", "auto_reply"]


def test_classifier_failure_halts_and_only_the_status_check_tells_the_operators(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    w.inbox.append(mail("op1@example.org", "?", "boom", datetime.fromisoformat(batch["items"][0]["send_at"]) + timedelta(seconds=5), "<b1@op>"))
    with pytest.raises(M.MailerError, match="could not read the mail from op1@example.org"):
        M.send(scfg, w.bid, live=False)
    assert len(w.to_clinics()) == 1
    assert [e["intent"] for e in w.events("command")] == ["classifier_failed"] and [h["kind"] for h in w.events("halt")] == ["error"]
    assert not any("прервана" in m["Subject"] for _, m in w.to_ops()) and w.events("notice") == []     # the dying process mails nothing
    grace = timedelta(minutes=15)
    w.now += timedelta(minutes=14)
    assert M.unnoticed_halts(scfg, grace) == []                      # a restart inside the grace is never mailed
    w.now += timedelta(minutes=2)
    (bid, halt), = M.unnoticed_halts(scfg, grace)
    title, text = M.halt_notice(halt)
    assert title == "рассылка прервана" and "overloaded" in text
    M.notify(scfg, {"ADDRESS": "x"}, batch, "halt", title, text, halt_ts=halt["ts"])
    assert "рассылка прервана" in w.to_ops()[-1][1]["Subject"] and M.unnoticed_halts(scfg, grace) == []        # told once


def test_a_halted_batch_that_a_later_plan_carries_is_not_mailed(scfg, monkeypatch):
    """05.10: a halted batch is replaced by a new plan; the status check mails a halt only when its letters are left."""
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    first = w.plan()
    w.now = at("2026-09-29T08:41:00")
    bid2, _ = M.plan(scfg, w.now, announce_at=at("2026-09-29T09:50:00"))
    assert bid2 > first["batch_id"]
    M.append_ledger(scfg, {"event": "halt", "batch_id": first["batch_id"], "kind": "error", "reason": "x"})
    M.append_ledger(scfg, {"event": "halt", "batch_id": bid2, "kind": "error", "reason": "y"})
    w.now = at("2026-09-29T09:10:00")
    assert [b for b, _ in M.unnoticed_halts(scfg, timedelta(minutes=15))] == [bid2]


def test_bounce_halts_the_batch(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    first = datetime.fromisoformat(batch["items"][0]["send_at"])
    w.inbox.append(mail("MAILER-DAEMON@example.org", "Undelivered Mail", "pd1@example.org: user unknown", first + timedelta(seconds=40), "<nd@x>"))
    with pytest.raises(M.MailerError, match="HALT: bounce"):
        M.send(scfg, w.bid, live=False)
    assert len(w.to_clinics()) == 1 and [h["kind"] for h in w.events("halt")] == ["delivery"]
    assert not any("остановлена" in m["Subject"] for _, m in w.to_ops())
    w.now += timedelta(minutes=16)
    (bid, halt), = M.unnoticed_halts(scfg, timedelta(minutes=15))
    assert M.halt_notice(halt)[0] == "рассылка остановлена" and "новым планом" in M.halt_notice(halt)[1]
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


def test_watch_keeps_a_clinic_answer_and_the_digest_mails_it_once_per_address(scfg, monkeypatch):
    """Ivan, 2026-10-05: clinic answers reach the operators once a day, in one mail with a table and the originals."""
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    M.append_ledger(scfg, {"event": "sent", "batch_id": w.bid, "recipient_id": "1", "clinic": "Klinik A", "step": "initial",
                           "to": ["pd@a.example"], "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    w.inbox.append(mail("pd@a.example", "AW: Pflegekraft", "Schicken Sie uns Ihre Konditionen.", at("2026-09-29T10:03:00"), "<r1@a.example>", In_Reply_To="<m1@x>"))
    w.inbox.append(mail("pd@a.example", "Automatische Antwort: Pflegekraft", "Ich bin nicht im Haus.", at("2026-09-29T10:04:00"), "<r2@a.example>",
                        In_Reply_To="<m1@x>", Auto_Submitted="auto-replied"))
    assert [e["kind"] for e in M.watch(scfg)] == ["reply", "auto_reply"]
    assert not w.to_ops() and not w.to_ops_of(OPS[0])                          # nothing goes out as it arrives
    reply, auto = w.events("inbound")
    assert reply["digest_to"] == OPS and "digest_to" not in auto and (scfg["ledger"].parent / reply["eml"]).exists()
    assert M.watch(scfg) == []                                                  # seen
    assert M.digest([scfg, scfg]) == {OPS[0]: 1, OPS[1]: 1}                     # one mail per address, the same ledger twice counts once
    (t, sent), = w.to_ops_of(OPS[1])
    d = _email.message_from_bytes(sent.as_bytes(), policy=_email.policy.default)           # as the operator receives it
    assert d["Subject"] == "Ответы клиник за 29.09.2026: 1" and d["Auto-Submitted"] == "auto-generated"
    html_part = d.get_body(("html",)).get_content()
    assert "<table" in html_part and "Klinik A" in html_part and "Schicken Sie uns Ihre Konditionen." in html_part
    assert "Klinik A" in d.get_body(("plain",)).get_content()
    (orig,) = [a for a in d.iter_attachments() if a.get_content_type() == "message/rfc822"]
    assert orig.get_content()["Message-ID"] == "<r1@a.example>"
    assert M.digest([scfg]) == {} and len(w.to_ops_of(OPS[1])) == 1             # carried: not mailed again
    assert [(e["to"], e["imap_message_ids"]) for e in w.events("digested")] == [(OPS[0], ["<r1@a.example>"]), (OPS[1], ["<r1@a.example>"])]


def answers_world(scfg, monkeypatch, to, cc, clinic="Klinik A"):
    """A scheduled campaign whose first letter to `clinic` (recipient 1) went to `to`, cc `cc`, and an empty do-not-contact table."""
    scfg["do_not_contact"] = scfg["ledger"].parent / "dnc.json"
    scfg["do_not_contact"].write_text("[]\n")
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    M.append_ledger(scfg, {"event": "sent", "batch_id": w.bid, "recipient_id": "1", "clinic": clinic, "step": "initial",
                           "to": to, "cc": cc, "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    return w


def table(c):
    return json.loads(c["do_not_contact"].read_text())


def answer(w, frm, text, mid="<r1@x>", subject="AW: Pflegekraft", **headers):
    w.inbox.append(mail(frm, subject, text, at("2026-09-29T10:03:00"), mid, In_Reply_To="<m1@x>", **headers))


def test_a_terms_request_changes_nothing_but_the_label_and_the_forward(scfg, monkeypatch, reader):
    """02.10: Ilmtalklinik (Anna Berger) asks for the terms."""
    w = answers_world(scfg, monkeypatch, ["anna.berger@klinikverbund.example"], [], "Ilmtalklinik Pfaffenhofen")
    answer(w, "Anna Berger <anna.berger@klinikverbund.example>", "Sehr geehrte Damen und Herren,\n\nschicken Sie mir bitte Ihre Konditionen.\n\nAnna Berger\n")
    reader.says("Konditionen", pattern="terms_request", quote="schicken Sie mir bitte Ihre Konditionen.")
    (ev,) = M.watch(scfg)
    assert ev["kind"] == "reply" and ev["pattern"] == "terms_request" and ev["actions"] == [] and ev["digest_to"] == OPS
    assert table(scfg) == [] and w.sent == [] and reader.asked[0]["our_addresses"] == ["anna.berger@klinikverbund.example"]
    M.digest([scfg])
    d = _email.message_from_bytes(w.to_ops_of(OPS[0])[0][1].as_bytes(), policy=_email.policy.default)
    assert "клиника ответила · клиника просит условия" in d.get_body(("plain",)).get_content()


def test_a_redirect_makes_the_target_the_recipient_and_mutes_the_sender(scfg, monkeypatch, reader):
    """05.10: LMU (pflegestellen@) says to write to HR.PflegeTeam@ and to stop writing to the Pflegestellen address."""
    w = answers_world(scfg, monkeypatch, ["pflegestellen@uniklinik.example"], [], "LMU Klinikum München")
    answer(w, "pflegestellen@uniklinik.example", "Sehr geehrte Damen und Herren,\n\nbitte wenden Sie sich in dieser Angelegenheit an "
           "HR.PflegeTeam@uniklinik.example. Bitte schreiben Sie nicht mehr an die Pflegestellen-Adresse und löschen Sie sie.\n")
    reader.says("HR.PflegeTeam", pattern="redirect", addresses=["HR.PflegeTeam@uniklinik.example"],
                quote="bitte wenden Sie sich in dieser Angelegenheit an HR.PflegeTeam@uniklinik.example.")
    (ev,) = M.watch(scfg)
    (row,) = table(scfg)
    assert row["match"] == "pflegestellen@uniklinik.example" and row["replace_with"] == ["HR.PflegeTeam@uniklinik.example"]
    assert row["reason"] == "redirect" and row["by"] == "pflegestellen@uniklinik.example" and row["clinic"] == "LMU Klinikum München"
    assert row["date"] == M.now_in(scfg).strftime("%Y-%m-%d") == "2026-09-29" and "HR.PflegeTeam@uniklinik.example." in row["why"]
    assert M.suppressed(scfg, ["pflegestellen@uniklinik.example"]) == set()                     # muted by a swap, not blocked
    assert M.swap_addresses(["pflegestellen@uniklinik.example"], M.replacements(scfg, ["pflegestellen@uniklinik.example"])) == ["HR.PflegeTeam@uniklinik.example"]
    assert w.sent == [] and "записал в таблицу: писать на HR.PflegeTeam@uniklinik.example" in ev["actions"][0]["ru"]
    M.digest([scfg])
    html_part = _email.message_from_bytes(w.to_ops_of(OPS[1])[0][1].as_bytes(), policy=_email.policy.default).get_body(("html",)).get_content()
    assert "клиника просит писать другому адресату: HR.PflegeTeam@uniklinik.example" in html_part and "Сделано: записал в таблицу" in html_part


def test_an_out_of_office_with_a_substitute_address_moves_the_cc_to_the_main_place(scfg, monkeypatch, reader):
    """29.09/02.10: Julia Koch (To) is away, Anna Berger (Cc) is her substitute."""
    w = answers_world(scfg, monkeypatch, ["julia.koch@klinikverbund.example"], ["anna.berger@klinikverbund.example"], "Ilmtalklinik Pfaffenhofen")
    answer(w, "julia.koch@klinikverbund.example", "Ich bin nicht im Haus. Mails werden nicht weitergeleitet. Bitte wenden Sie sich an meine Vertretung "
           "Anna Berger (anna.berger@klinikverbund.example) oder an das Sekretariat.", subject="Abwesenheit", Auto_Submitted="auto-replied")
    reader.says("Vertretung", pattern="out_of_office", addresses=["anna.berger@klinikverbund.example"], names=["Anna Berger"],
                quote="Bitte wenden Sie sich an meine Vertretung Anna Berger (anna.berger@klinikverbund.example)")
    (ev,) = M.watch(scfg)
    assert ev["kind"] == "auto_reply" and ev["digest_to"] == OPS and reader.asked[0]["automatic"] is True
    (row,) = table(scfg)
    assert row["by"] == "julia.koch@klinikverbund.example, out-of-office auto-reply" and row["reason"] == "redirect"
    swaps = M.replacements(scfg, ["julia.koch@klinikverbund.example", "anna.berger@klinikverbund.example"])
    assert M.swap_addresses(["julia.koch@klinikverbund.example"], swaps) == ["anna.berger@klinikverbund.example"]
    assert "stopped" not in M.next_due(scfg, {"id": "1", "clinic": "Klinik A"}, M.read_ledger(scfg))[1]       # an out of office ends no sequence


def test_names_without_an_address_count_when_one_is_ours_and_otherwise_change_nothing(scfg, monkeypatch, reader):
    """29.09/02.10: Markus Lang (Cc) names four colleagues, Frau Roth is our main recipient; and a stranger's name."""
    w = answers_world(scfg, monkeypatch, ["lena.roth@kliniken-see.example"], ["markus.lang@kliniken-see.example"], "Klinikum Starnberg")
    answer(w, "markus.lang@kliniken-see.example", "Ich bin nicht im Haus. Bitte wenden Sie sich an meine Kollegen Herr Brandt, "
           "Herr Vogel, Frau Kraus und Frau Roth.", Auto_Submitted="auto-replied")
    reader.says("Kollegen", pattern="out_of_office", names=["Brandt", "Vogel", "Kraus", "Roth"], already_ours=["lena.roth@kliniken-see.example"],
                quote="Bitte wenden Sie sich an meine Kollegen Herr Brandt, Herr Vogel, Frau Kraus und Frau Roth.")
    M.watch(scfg)
    (row,) = table(scfg)
    assert row["match"] == "markus.lang@kliniken-see.example" and row["replace_with"] == ["lena.roth@kliniken-see.example"]
    swaps = M.replacements(scfg, ["lena.roth@kliniken-see.example", "markus.lang@kliniken-see.example"])
    assert M.swap_addresses(["lena.roth@kliniken-see.example", "markus.lang@kliniken-see.example"], swaps) == ["lena.roth@kliniken-see.example"]
    answer(w, "lena.roth@kliniken-see.example", "Ich bin nicht im Haus. Meine Vertretung ist Frau Meier, Tel. 089 123456.", mid="<r2@x>",
           Auto_Submitted="auto-replied")
    reader.says("Frau Meier", pattern="out_of_office", names=["Meier"], phones=["089 123456"], quote="Meine Vertretung ist Frau Meier, Tel. 089 123456.")
    ev = M.watch(scfg)[0]
    assert len(table(scfg)) == 1 and ev["digest_to"] == OPS                       # a name and a phone number: forwarded, the table unchanged


def test_an_out_of_office_that_names_nobody_stays_in_the_ledger_only(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    answer(w, "pd@a.example", "Ich bin bis 12.10. im Urlaub.", Auto_Submitted="auto-replied")
    reader.says("Urlaub", pattern="out_of_office", quote="Ich bin bis 12.10. im Urlaub.")
    (ev,) = M.watch(scfg)
    assert ev["pattern"] == "out_of_office" and "digest_to" not in ev and "eml" not in ev and table(scfg) == []
    assert M.digest([scfg]) == {}


def test_an_opt_out_blocks_the_address_it_names_or_the_whole_clinic_only_when_it_says_so(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], ["st@a.example"])
    answer(w, "pd@a.example", "Bitte streichen Sie uns aus Ihrem Verteiler.")
    reader.says("Verteiler", pattern="opt_out", scope="address", quote="Bitte streichen Sie uns aus Ihrem Verteiler.")
    M.watch(scfg)
    (row,) = table(scfg)
    assert row["match"] == "pd@a.example" and row["reason"] == "opt_out" and "replace_with" not in row and M.suppressed(scfg, ["pd@a.example", "st@a.example"]) == {"pd@a.example"}
    answer(w, "pd@a.example", "Wir wünschen keinerlei Kontakt zu Ihnen, für das ganze Haus.", mid="<r2@x>")
    reader.says("keinerlei Kontakt", pattern="opt_out", scope="clinic", quote="Wir wünschen keinerlei Kontakt zu Ihnen, für das ganze Haus.")
    M.watch(scfg)
    assert [r["match"] for r in table(scfg)] == ["pd@a.example", "st@a.example"]            # pd@ was there, st@ is added
    assert [a["added"] for a in w.events("inbound")[1]["actions"]] == [False, True]


def test_a_classifier_that_cannot_read_an_answer_halts_and_the_answer_is_read_again_on_resume(scfg, monkeypatch, reader):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    first = datetime.fromisoformat(batch["items"][0]["send_at"])
    w.inbox.append(mail(batch["items"][0]["to"][0], "AW: Pflegekraft", "boom Konditionen bitte", first + timedelta(seconds=5), "<r1@x>"))
    with pytest.raises(M.MailerError, match="HALT: could not read the answer from pd.@example.org .*overloaded"):
        M.send(scfg, w.bid, live=False)
    assert w.events("inbound") == [] and [h["kind"] for h in w.events("halt")] == ["error"]
    reader.broken = False
    reader.says("Konditionen", pattern="terms_request", quote="boom Konditionen bitte")
    M.send(scfg, w.bid, live=False)                                              # the same command resumes and reads it again
    (ev,) = w.events("inbound")
    assert ev["pattern"] == "terms_request" and [h["kind"] for h in w.events("halt")] == ["error"] and len(w.events("resumed")) == 1
    assert len(w.to_clinics()) == 3                                              # the halt cost no letter: the other two went after the resume


def test_what_the_classifier_says_it_found_must_be_in_the_mail(scfg, monkeypatch, reader):
    raw = mail("pd@a.example", "AW", "Bitte wenden Sie sich an Frau Roth.", None, "<r1@x>")[2]
    reader.says("Frau Roth", pattern="redirect", addresses=["roth@a.example"], quote="Bitte wenden Sie sich an Frau Roth.")
    with pytest.raises(M.MailerError, match="the address 'roth@a.example' is not in the mail"):
        M.classify_answer(scfg, raw, "pd@a.example", "Klinik A", ["pd@a.example"], False)
    reader.rules.clear()
    reader.says("Frau Roth", pattern="redirect", already_ours=["roth@a.example"], quote="Bitte wenden Sie sich an Frau Roth.")
    with pytest.raises(M.MailerError, match="not one of our addresses"):
        M.classify_answer(scfg, raw, "pd@a.example", "Klinik A", ["pd@a.example"], False)
    reader.rules.clear()
    reader.says("Frau Roth", pattern="redirect", names=["Roth"], quote="Bitte rufen Sie Frau Roth an.")
    with pytest.raises(M.MailerError, match="the quote .* is not in the mail"):
        M.classify_answer(scfg, raw, "pd@a.example", "Klinik A", ["pd@a.example"], False)
    reader.rules.clear()
    reader.says("Frau Roth", pattern="opt_out", quote="Bitte wenden Sie sich an Frau Roth.")
    with pytest.raises(M.MailerError, match="opt_out without a scope"):
        M.classify_answer(scfg, raw, "pd@a.example", "Klinik A", ["pd@a.example"], False)


def test_a_redirect_from_an_address_we_never_wrote_to_changes_no_table(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    answer(w, "sekretariat@a.example", "Bitte wenden Sie sich an kontakt@a.example.", subject="AW: Pflegekraft")
    reader.says("kontakt@a.example", pattern="redirect", addresses=["kontakt@a.example"], quote="Bitte wenden Sie sich an kontakt@a.example.")
    (ev,) = M.watch(scfg)
    assert table(scfg) == [] and "не один из адресов" in ev["actions"][0]["ru"] and ev["digest_to"] == OPS


def test_a_redirect_written_after_planning_reaches_the_letter_at_send_time(scfg, monkeypatch):
    """Ivan, 2026-10-05: an out-of-office's substitute gets the letters already planned; the approval covers the planned
    addresses, the ledger keeps both, and a test copy to an allowlist address is not swapped."""
    scfg["do_not_contact"] = scfg["ledger"].parent / "dnc.json"
    scfg["do_not_contact"].write_text("[]\n")
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    (scfg["approvals"] / f"{w.bid}.pending.json").rename(scfg["approvals"] / f"{w.bid}.json")
    scfg["do_not_contact"].write_text(json.dumps([{"match": "pd2@example.org", "replace_with": ["new2@example.org"], "reason": "redirect"}]))
    M.send(scfg, w.bid, live=True)
    letters = {m["Subject"] + m["To"]: m for _, m in w.sent if not m["To"] == ", ".join(OPS)}
    assert sorted(m["To"] for m in letters.values()) == ["new2@example.org", "pd1@example.org", "pd3@example.org"]
    sent = {e["recipient_id"]: e for e in w.events("sent")}
    assert sent["2"]["to"] == ["new2@example.org"] and sent["2"]["planned_to"] == ["pd2@example.org"] and "planned_to" not in sent["1"]
    assert [it["to"] for it in batch["items"] if it["recipient_id"] == "2"] == [["pd2@example.org"]]      # the batch file is untouched
    it = {"to": ["pd2@example.org"], "cc": ["x@example.org"]}
    assert M.routed(scfg, it, live=False) is it                                  # an allowlist copy keeps its addresses


def test_a_swap_at_send_time_moves_a_cc_to_the_main_place_without_a_duplicate(scfg):
    scfg["do_not_contact"] = scfg["ledger"].parent / "dnc.json"
    scfg["do_not_contact"].write_text(json.dumps([{"match": "julia@a.example", "replace_with": ["anna@a.example"], "reason": "redirect"}]))
    out = M.routed(scfg, {"to": ["julia@a.example"], "cc": ["anna@a.example", "other@a.example"]}, live=True)
    assert out["to"] == ["anna@a.example"] and out["cc"] == ["other@a.example"] and out["planned_to"] == ["julia@a.example"] and out["planned_cc"] == ["anna@a.example", "other@a.example"]


def test_a_block_written_after_planning_stops_the_letter_at_send_time(scfg, monkeypatch):
    """Ivan, 2026-10-05: an opt-out written after planning must stop the letter that is already approved. The clinic's other
    letters of the batch go with it, the others go on, the operators are told, nothing halts."""
    scfg["do_not_contact"] = scfg["ledger"].parent / "dnc.json"
    scfg["do_not_contact"].write_text("[]\n")
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    batch = w.plan()
    (scfg["approvals"] / f"{w.bid}.pending.json").rename(scfg["approvals"] / f"{w.bid}.json")
    scfg["do_not_contact"].write_text(json.dumps([{"match": "pd2@example.org", "reason": "opt_out", "clinic": "Klinik B", "by": "Klinik B"}]))
    M.send(scfg, w.bid, live=True)
    assert sorted(to for _, to in w.to_clinics()) == ["pd1@example.org", "pd3@example.org"]
    (blocked,) = w.events("blocked")
    assert (blocked["recipient_id"], blocked["addresses"], blocked["step"]) == ("2", ["pd2@example.org"], "initial")
    assert {e["recipient_id"] for e in w.events("sent")} == {"1", "3"} and w.events("halt") == []
    assert [it["to"] for it in batch["items"] if it["recipient_id"] == "2"] == [["pd2@example.org"]]      # the approved batch is untouched
    notice = next(e for e in w.events("notice") if e["kind"] == "blocked")
    assert notice["recipient_id"] == "2" and "Klinik B" in notice["title"]
    text = next(plain(m) for _, m in w.to_ops() if "письмо не ушло: Klinik B" in m["Subject"])
    assert "pd2@example.org в списке блокировки" in text and has_row(text, "Klinik B", "ничего не уйдёт: адрес в списке блокировки: pd2@example.org")
    assert has_row(text, "Klinik A", "первое письмо ушло")


def test_the_send_time_block_check_drops_a_blocked_cc_stops_a_blocked_swap_target_and_spares_test_copies(scfg):
    scfg["do_not_contact"] = scfg["ledger"].parent / "dnc.json"
    scfg["do_not_contact"].write_text(json.dumps([
        {"match": "b@a.example", "reason": "opt_out"}, {"match": "t@a.example", "reason": "opt_out"},
        {"match": "old@a.example", "replace_with": ["t@a.example"], "reason": "redirect"}]))
    it = {"recipient_id": "1", "clinic": "Klinik A", "step": "initial", "to": ["a@a.example"], "cc": ["b@a.example", "c@a.example"]}
    out = M.routed(scfg, it, live=True)
    assert out["to"] == ["a@a.example"] and out["cc"] == ["c@a.example"] and out["planned_cc"] == ["b@a.example", "c@a.example"] and out["planned_to"] == ["a@a.example"]
    with pytest.raises(M.Blocked) as e:                                          # the address a redirect sends to is checked too
        M.routed(scfg, {**it, "to": ["old@a.example"], "cc": []}, live=True)
    assert e.value.addresses == ["t@a.example"]
    with pytest.raises(M.Blocked) as e:
        M.routed(scfg, {**it, "to": ["B@a.example"], "cc": []}, live=True)            # a different case is the same address
    assert M.routed(scfg, {**it, "to": ["b@a.example"]}, live=False)["to"] == ["b@a.example"]    # an allowlist copy keeps its address
    clean = {**it, "cc": ["c@a.example"]}
    assert M.routed(scfg, clean, live=True) is clean


def test_a_blocked_letter_of_a_simple_batch_is_skipped_loudly_and_the_others_go(cfg, monkeypatch, capsys):
    recs = json.loads((cfg.parent / "recipients.json").read_text())
    recs.append({**recs[0], "id": "2", "clinic": "Klinik B", "to": ["pd@b.example"], "cc": []})
    (cfg.parent / "recipients.json").write_text(json.dumps(recs))
    (cfg.parent / "dnc.json").write_text("[]\n")
    cfg.write_text(json.dumps(json.loads(cfg.read_text()) | {"do_not_contact": "dnc.json"}))
    c = M.load_config(cfg)
    c["suppression_db"] = None
    sent = []
    for name, fn in (("smtp_send", lambda box, msg: sent.append(msg) or {}), ("mailbox", lambda a: {"ADDRESS": a}),
                     ("check_inbox", lambda cfg, box: None), ("watch", lambda cfg, box=None: []), ("in_window", lambda cfg, t: True),
                     ("sleep", lambda s: None)):
        monkeypatch.setattr(M, name, fn)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    bid, _ = M.plan(c, at("2026-09-28T09:00"))
    (c["approvals"] / f"{bid}.pending.json").rename(c["approvals"] / f"{bid}.json")
    (cfg.parent / "dnc.json").write_text(json.dumps([{"match": "pd@a.example", "reason": "opt_out"}]))      # written after planning
    M.send(c, bid, live=True)
    assert [m["To"] for m in sent] == ["pd@b.example"]
    (ev,) = [e for e in M.read_ledger(c) if e["event"] == "blocked"]
    assert (ev["recipient_id"], ev["addresses"]) == ("1", ["pd@a.example"]) and not [e for e in M.read_ledger(c) if e["event"] == "halt"]
    assert "BLOCKED 1 Klinik A initial: pd@a.example is on a suppression list, not sent" in capsys.readouterr().out


def test_a_second_process_for_the_same_batch_fails_at_once(scfg, monkeypatch):
    """Ivan, 2026-10-05: a double start would send the letters twice; the lock goes with the process."""
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    path = scfg["batches"] / f"{w.bid}.json"
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)                                              # the first process
    with pytest.raises(M.MailerError, match=f"batch {w.bid} is already running"):
        M.send(scfg, w.bid, live=False)
    assert w.sent == [] and w.events("halt") == []                              # it did not touch the batch
    os.close(fd)                                                                # the first process ended or was killed
    M.send(scfg, w.bid, live=False)
    assert len(w.to_clinics()) == 3


def test_a_digest_that_fails_keeps_its_answers_for_the_next_one(scfg, monkeypatch):
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    M.append_ledger(scfg, {"event": "sent", "batch_id": w.bid, "recipient_id": "1", "clinic": "Klinik A", "step": "initial",
                           "to": ["pd@a.example"], "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    w.inbox.append(mail("pd@a.example", "AW: Pflegekraft", "Gerne.", at("2026-09-29T10:03:00"), "<r1@a.example>", In_Reply_To="<m1@x>"))
    M.watch(scfg)
    monkeypatch.setattr(M, "smtp_send", lambda box, msg: {OPS[0]: (550, b"no")})
    with pytest.raises(M.MailerError, match="SMTP refused the digest"):
        M.digest([scfg])
    assert w.events("digested") == []
    monkeypatch.setattr(M, "smtp_send", w.smtp)
    assert M.digest([scfg]) == {OPS[0]: 1, OPS[1]: 1}


def test_two_watches_of_one_ledger_run_one_after_the_other(scfg, monkeypatch):
    """Two batches of one campaign poll the same inbox: the second watch waits and finds the answer already logged."""
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    M.append_ledger(scfg, {"event": "sent", "batch_id": w.bid, "recipient_id": "1", "clinic": "Klinik A", "step": "initial",
                           "to": ["pd@a.example"], "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    w.inbox.append(mail("pd@a.example", "AW: Pflegekraft", "Danke.", at("2026-09-29T10:03:00"), "<r1@a.example>", In_Reply_To="<m1@x>"))
    got = []
    with open(scfg["ledger"].with_name(scfg["ledger"].name + ".watch.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)                              # the other process is in its watch
        t = threading.Thread(target=lambda: got.append(M.watch(scfg)))
        t.start()
        t.join(0.5)
        assert t.is_alive() and not got
    t.join(5)
    assert [e["kind"] for e in got[0]] == ["reply"] and len(w.events("inbound")) == 1


def test_notices_go_to_the_notify_list_and_answers_by_kind_to_the_forward_list(scfg, monkeypatch):
    """Ivan, 2026-10-05: Valentyn hears of no start, round or halt, only of clinic answers nobody has handled yet; a batch
    planned while he was on its notices keeps none for him."""
    scfg["notify"], scfg["forward"] = OPS[:1], {k: OPS[:1] for k in M.FORWARD_KINDS} | {"reply": OPS, "unmatched": OPS}
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    bid, _ = M.plan(scfg, w.now, announce_at=at("2026-09-29T09:00:00"))
    batch = json.loads((scfg["batches"] / f"{bid}.json").read_text())
    assert batch["announce"]["to"] == OPS[:1] and batch["operators"] == OPS[:1]
    M.notify(scfg, {"ADDRESS": "x"}, {**batch, "operators": OPS}, "halt", "рассылка прервана", "x")
    (t, sent), = w.sent
    assert sent["To"] == OPS[0] and w.events("notice")[0]["to"] == OPS[:1]
    M.append_ledger(scfg, {"event": "sent", "batch_id": bid, "recipient_id": "1", "clinic": "Klinik A", "step": "initial",
                           "to": ["pd@a.example"], "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    w.inbox.append(mail("pd@a.example", "AW: Pflegekraft", "Konditionen?", at("2026-09-29T10:03:00"), "<r1@a.example>", In_Reply_To="<m1@x>"))
    w.inbox.append(mail("pd@a.example", "AW: Pflegekraft", "Bitte keine weiteren Mails.", at("2026-09-29T10:04:00"), "<r2@a.example>", In_Reply_To="<m1@x>"))
    assert [e["kind"] for e in M.watch(scfg)] == ["reply", "stop"]
    assert [e["digest_to"] for e in w.events("inbound")] == [OPS, OPS[:1]]
    assert M.digest([scfg]) == {OPS[0]: 2, OPS[1]: 1}


def test_an_undeliverable_operator_mail_is_no_clinic_bounce_and_goes_to_the_other_operator(scfg, monkeypatch):
    """05.10: Valentyn's address bounced; the report quoted a forwarded clinic answer, so wave 1 took it for a clinic's
    bounce and halted. Now it is its own kind, never matched to a clinic and never mailed back to the dead address."""
    w = World(scfg, monkeypatch, "2026-09-29T08:40:00")
    w.plan()
    M.append_ledger(scfg, {"event": "sent", "batch_id": w.bid, "recipient_id": "1", "clinic": "Klinik A", "step": "initial",
                           "to": ["pd@a.example"], "cc": [], "sent_at": "2026-09-29T10:01:30+02:00", "message_id": "<m1@x>"})
    w.now = at("2026-09-29T10:05:00")
    w.inbox.append(mail("postmaster@outlook.example", "Undeliverable: [daria] Klinik A: AW", f"Your message to {OPS[1]} couldn't be delivered. "
                        "Recipient Unknown\n\nOriginal headers:\nReferences: <m1@x>\n", at("2026-09-29T10:03:00"), "<ndr1@o>"))
    (ev,) = M.watch(scfg)
    assert (ev["kind"], ev["recipient_id"]) == ("operator_undeliverable", None) and ev["digest_to"] == [OPS[0]]
    assert M.digest([scfg]) == {OPS[0]: 1}
    (t, sent), = w.to_ops_of(OPS[0])
    assert "наше письмо оператору не доставлено" in sent.get_body(("plain",)).get_content()
    assert "stopped" not in M.next_due(scfg, {"id": "1", "clinic": "Klinik A"}, M.read_ledger(scfg))[1]   # the clinic is not stopped by it


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
    assert w.events("notice") == []                                  # a restart is no news


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


def test_last_step_ends_one_clinics_sequence_early(fcfg):
    recs = json.loads(fcfg["recipients"].read_text())
    recs[1]["last_step"] = "initial"
    fcfg["recipients"].write_text(json.dumps(recs))
    random.seed(3)
    bid, report = M.plan(fcfg, at("2026-09-28T17:00"), announce_at=at("2026-09-28T18:00:00"), start_at=at("2026-09-29T09:00:00"))
    items = json.loads((fcfg["batches"] / f"{bid}.json").read_text())["items"]
    assert [(it["recipient_id"], it["step"]) for it in items] == [
        ("1", "initial"), ("2", "initial"), ("3", "initial"), ("1", "fu1"), ("3", "fu1"), ("1", "fu2"), ("3", "fu2")]
    assert "+  2 Klinik B: initial to pd2@example.org" in report
    ledger = [{"event": "sent", "recipient_id": "2", "step": "initial", "sent_at": "2026-09-29T09:03:00+02:00"}]
    assert M.next_due(fcfg, recs[1], ledger) == (None, "sequence complete")
    recs[1]["last_step"] = "fu9"
    with pytest.raises(M.MailerError, match="last_step 'fu9' is not a cadence step"):
        M.last_step(fcfg, recs[1])


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
    assert "Не уйдёт:\n" in fu1 and has_row(fu1, "Klinik B", "клиника ответила") and "Тема: Re: Pflegekraft für Intensivstation" in fu1
    assert "Фоллоу-ап 1 — пн 05.10, с 09:0" in fu1 and ", по одному:" in fu1
    fu2 = next(c for _, s, c in ops if "фоллоу-ап 2 пн 12.10" in s)
    assert has_row(fu2, "Klinik C", "убрано по письму op2@example.net") and has_row(fu2, "Klinik B", "клиника ответила")
    assert "Фоллоу-ап 2 — пн 12.10, в 09:0" in fu2 and "по одному" not in fu2       # one letter: a time, not a span
    assert "рассылка завершена" in ops[-1][1] and "Писем ушло: 6 из 9" in ops[-1][2]
    assert has_row(ops[-1][2], "Klinik A", "первое письмо ушло вт 29.09 09:0") and "; фоллоу-ап 1 ушёл пн 05.10 09:0" in ops[-1][2]
    assert [e["intent"] for e in w.events("command")] == ["skip"] and w.events("halt") == []


def cells(html_text):
    """The tables of an HTML part as lists of rows of cell texts."""
    unesc = lambda c: re.sub(r"<[^>]+>", "", c).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return [[[unesc(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row)] for row in re.findall(r"<tr>(.*?)</tr>", table)]
            for table in re.findall(r"<table.*?</table>", html_text)]


def test_every_mail_to_the_operators_has_a_text_part_and_an_html_part_with_real_tables(fcfg, monkeypatch):
    """Ivan, 2026-10-05: operator mail is HTML, its tables are tables; the text part has the same rows aligned."""
    w = World(fcfg, monkeypatch, "2026-09-28T17:50:00", intents={"не отправляй в klinik c": ("skip", ["3"])})
    w.plan(announce="2026-09-28T18:00:00", start="2026-09-29T09:00:00")
    w.inbox.append(mail("pd2@example.org", "AW: Pflegekraft für Intensivstation", "Danke, wir melden uns.", at("2026-09-30T10:00:00"), "<r2@b>"))
    w.inbox.append(mail("op2@example.net", "Klinik C", "Не отправляй в Klinik C", at("2026-10-06T12:00:00"), "<s3@op>"))
    M.send(fcfg, w.bid, live=False)
    mails = [m for _, m in w.to_ops()]
    assert len(mails) >= 6
    for m in mails:
        assert m.get_content_type() in ("multipart/alternative", "multipart/mixed"), m["Subject"]
        assert m.get_body(("plain",)).get_content_type() == "text/plain" and m.get_body(("html",)).get_content_type() == "text/html", m["Subject"]
    report = next(m for m in mails if "фоллоу-ап 1 пн 05.10" in m["Subject"] and "предварительный отчёт" in m["Subject"])
    tables = cells(report.get_body(("html",)).get_content())
    assert tables[0][0] == ["Время", "Клиника", "Кому", "Копия"]                        # who goes when
    assert [r[1:3] for r in tables[0][1:]] == [["Klinik A", "pd1@example.org"], ["Klinik C", "pd3@example.org"]]
    assert tables[1] == [["Клиника", "Почему"], ["Klinik B", tables[1][1][1]]] and "клиника ответила" in tables[1][1][1]   # who does not and why
    assert re.search(r"^ +09:0\d {2,}Klinik A {2,}pd1@example\.org", plain(report), re.M)    # the same rows aligned in the text part
    assert has_row(plain(report), "Клиника", "Почему") and "Тема: Re: Pflegekraft" in plain(report)
    notice = next(m for m in mails if "ушло 3 из 3" in m["Subject"])
    state = cells(notice.get_body(("html",)).get_content())[0]
    assert state[0] == ["Клиника", "Письма"] and [r[0] for r in state[1:]] == ["Klinik A", "Klinik B", "Klinik C"]
    assert has_row(plain(notice), "Клиника", "Письма") and "<" not in plain(notice).replace("<r", "")
    reply = next(m for m in mails if m["Subject"] == "Re: Klinik C")                       # the answer to a command
    assert cells(reply.get_body(("html",)).get_content())[0][0] == ["Клиника", "Письма"]
    announce = mails[0]
    assert [p.get_content_type() for p in announce.iter_attachments()] == ["application/pdf"]
    assert cells(announce.get_body(("html",)).get_content())[0][0] == ["Время", "Клиника", "Кому"]


def test_a_stop_between_rounds_cancels_every_follow_up(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-28T17:50:00")
    w.plan(announce="2026-09-28T18:00:00", start="2026-09-29T09:00:00")
    w.inbox.append(mail("op1@example.org", "Re: план", "стоп", at("2026-10-01T15:00:00"), "<st@op>"))
    M.send(fcfg, w.bid, live=False)
    assert len(clinic_letters(w)) == 3 and w.events("report") == []
    reply = plain(w.to_ops()[-1][1])
    assert "Рассылка остановлена. Писем ушло: 3; не уйдёт: 6, фоллоу-апы тоже." in reply
    assert has_row(reply, "Klinik A", "первое письмо ушло вт 29.09 09:0") and "дальше ничего не уйдёт: рассылка остановлена" in reply
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
    assert [e["kind"] for e in w.events("notice")] == ["round"] and "той же командой" in M.halt_notice(h)[1]
    w.now = at("2026-10-01T09:30:00")
    M.send(fcfg, w.bid, live=False)
    assert len(clinic_letters(w)) == 9 and len(w.events("announced")) == 1
    assert [e["kind"] for e in w.events("notice")] == ["round", "round", "done"]


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


# ---------- redirect letters (Ivan, 2026-10-05: the classifier writes the entry and the letter goes too) ----------

def recipients(c):
    return json.loads(c["recipients"].read_text())


def test_a_redirect_to_a_new_address_makes_a_recipient_and_ends_the_old_sequence(scfg, monkeypatch, reader):
    """05.10: LMU's Pflegestellen box says to write to HR.PflegeTeam@, an address we never wrote to."""
    w = answers_world(scfg, monkeypatch, ["pd1@example.org"], [], "LMU Klinikum München")
    answer(w, "pd1@example.org", "Bitte wenden Sie sich an HR.PflegeTeam@uniklinik.example.")
    reader.says("HR.PflegeTeam", pattern="redirect", addresses=["HR.PflegeTeam@uniklinik.example"], quote="Bitte wenden Sie sich an HR.PflegeTeam@uniklinik.example.")
    (ev,) = M.watch(scfg)
    old, new = [r for r in recipients(scfg) if r["id"] in ("1", "1r1")]
    assert new["to"] == ["HR.PflegeTeam@uniklinik.example"] and new["cc"] == [] and new["redirect_of"] == "1" and new["clinic"] == old["clinic"]
    assert new["vars"]["ANREDE"] == "Sehr geehrte Damen und Herren" and new["vars"]["BEREICH"] == old["vars"]["BEREICH"] and old["vars"]["ANREDE"] == "Sehr geehrte Frau A"
    letter = ev["actions"][1]
    assert letter["do"] == "redirect_letter" and letter["recipient"] == "1r1" and "первое письмо на HR.PflegeTeam@uniklinik.example" in letter["ru"]
    (red,) = w.events("redirected")
    assert red["recipient_id"] == "1" and red["to_recipient"] == "1r1" and red["kind"] == "redirected"
    assert M.next_due(scfg, old, M.read_ledger(scfg))[1].startswith("stopped: redirected")           # a letter to the old address would double it
    assert M.next_due(scfg, new, M.read_ledger(scfg)) == (0, None)
    assert w.sent == []                                                                              # the desk sends it, in the window


def test_a_redirect_to_an_address_we_already_wrote_to_makes_no_letter(scfg, monkeypatch, reader):
    """Starnberg: the substitute is already our main recipient; Ilmtalklinik: she is in Cc."""
    w = answers_world(scfg, monkeypatch, ["julia.koch@klinikverbund.example"], ["anna.berger@klinikverbund.example"], "Ilmtalklinik Pfaffenhofen")
    answer(w, "julia.koch@klinikverbund.example", "Ich bin nicht im Haus. Vertretung: anna.berger@klinikverbund.example", Auto_Submitted="auto-replied")
    reader.says("Vertretung", pattern="out_of_office", addresses=["anna.berger@klinikverbund.example"], quote="Vertretung: anna.berger@klinikverbund.example")
    (ev,) = M.watch(scfg)
    assert [r["id"] for r in recipients(scfg)] == ["1", "2", "3"] and w.events("redirected") == []
    assert ev["actions"][1] == {"do": "redirect_letter", "recipient": None, "to": [], "ru": "нового письма нет: на этот адрес мы уже писали"}
    assert M.next_due(scfg, recipients(scfg)[0], M.read_ledger(scfg))[1] == "sequence complete"     # not "stopped: redirected": the old sequence goes on
    assert not [e for e in M.read_ledger(scfg) if e["event"] == "redirected"]


def test_a_redirect_to_a_blocked_address_makes_no_letter(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd1@example.org"], [])
    scfg["do_not_contact"].write_text(json.dumps([{"match": "x@optout.example", "reason": "opt_out"}]))
    answer(w, "pd1@example.org", "Bitte schreiben Sie an x@optout.example.")
    reader.says("optout", pattern="redirect", addresses=["x@optout.example"], quote="Bitte schreiben Sie an x@optout.example.")
    (ev,) = M.watch(scfg)
    assert [r["id"] for r in recipients(scfg)] == ["1", "2", "3"] and "в списке блокировки: x@optout.example" in ev["actions"][1]["ru"]


def test_the_same_redirect_read_twice_makes_one_recipient(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd1@example.org"], [])
    assert M.redirect_letter(scfg, "1", ["new@a.example"])["id"] == "1r1"
    again = M.redirect_letter(scfg, "1", ["new@a.example"])
    assert again["id"] is None and "уже сделан" in again["ru"] and [r["id"] for r in recipients(scfg)] == ["1", "2", "3", "1r1"]
    assert len(w.events("redirected")) == 1


def test_a_redirect_that_was_interrupted_before_its_ledger_event_is_finished_not_doubled(scfg, monkeypatch):
    """The recipient was written, the ledger refused the event (a root-owned file and a user's run): the next call writes the event."""
    w = answers_world(scfg, monkeypatch, ["pd1@example.org"], [])
    real = M.append_ledger
    monkeypatch.setattr(M, "append_ledger", lambda c, e: (_ for _ in ()).throw(PermissionError("ledger")) if e["event"] == "redirected" else real(c, e))
    with pytest.raises(PermissionError):
        M.redirect_letter(scfg, "1", ["new@a.example"])
    monkeypatch.setattr(M, "append_ledger", real)
    assert [r["id"] for r in recipients(scfg)] == ["1", "2", "3", "1r1"] and w.events("redirected") == []
    assert "уже сделан" in M.redirect_letter(scfg, "1", ["new@a.example"])["ru"]
    assert [r["id"] for r in recipients(scfg)] == ["1", "2", "3", "1r1"] and [e["to_recipient"] for e in w.events("redirected")] == ["1r1"]


def test_a_redirect_recipient_is_not_planned_into_a_waves_batch(fcfg, monkeypatch):
    World(fcfg, monkeypatch, "2026-09-29T08:40:00")
    M.redirect_letter(fcfg, "1", ["new@a.example"])
    bid, report = M.plan(fcfg, at("2026-09-29T08:40:00"), announce_at=at("2026-09-29T09:00:00"), only=["2", "3"])
    assert {it["recipient_id"] for it in json.loads((fcfg["batches"] / f"{bid}.json").read_text())["items"]} == {"2", "3"}
    bid, report = M.plan(fcfg, at("2026-09-29T08:40:00"))
    assert any("1r1" in line and "redirect_letters" in line for line in report)
    assert "1r1" not in {it["recipient_id"] for it in json.loads((fcfg["batches"] / f"{bid}.json").read_text())["items"]}


def test_redirect_letters_go_in_the_window_each_step_once_and_the_follow_up_in_its_own_thread(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-29T07:00:00")
    M.append_ledger(fcfg, {"event": "sent", "batch_id": "b0", "recipient_id": "1", "clinic": "Klinik A", "step": "initial", "to": ["pd1@example.org"],
                           "cc": [], "sent_at": "2026-09-29T07:00:00+02:00", "message_id": "<m0@x>"})
    assert M.redirect_letters(fcfg) is None                                  # no redirect recipient yet
    M.redirect_letter(fcfg, "1", ["new@a.example"])
    assert M.redirect_letters(fcfg) is None and w.sent == []                 # 07:00: outside the window
    w.now = at("2026-09-29T09:00:00")                                        # a round minute: the letter waits for an odd one
    bid = M.redirect_letters(fcfg)
    (t, first), = w.sent
    assert t.minute % 5 and first["To"] == "new@a.example" and first["Subject"] == "Pflegekraft für Intensivstation" and not first["In-Reply-To"]
    assert plain(first).startswith("Sehr geehrte Damen und Herren,") and (fcfg["approvals"] / f"{bid}.json").exists()
    assert [(e["recipient_id"], e["step"], e["to"]) for e in w.events("sent") if e["recipient_id"] == "1r1"] == [("1r1", "initial", ["new@a.example"])]
    assert [(e["recipient_id"], e["step"]) for e in w.events("redirect_attempt")] == [("1r1", "initial")]
    assert M.redirect_letters(fcfg) is None and len(w.sent) == 1             # the follow-up is not due: 3 business days
    w.now = at("2026-10-05T10:00:00")                                        # 29.09 + 3 business days, 02.10 a holiday
    M.redirect_letters(fcfg)
    (_, fu1) = w.sent[1]
    assert fu1["To"] == "new@a.example" and fu1["In-Reply-To"] == first["Message-ID"] and fu1["Subject"].startswith("Re:")
    assert M.redirect_letters(fcfg) is None


def test_a_failed_redirect_letter_is_raised_recorded_and_not_tried_again(fcfg, monkeypatch):
    w = World(fcfg, monkeypatch, "2026-09-29T09:01:00")
    M.append_ledger(fcfg, {"event": "sent", "batch_id": "b0", "recipient_id": "1", "clinic": "Klinik A", "step": "initial", "to": ["pd1@example.org"],
                           "cc": [], "sent_at": "2026-09-29T07:00:00+02:00", "message_id": "<m0@x>"})
    M.redirect_letter(fcfg, "1", ["new@a.example"])

    def refuse(box, msg):
        return {"new@a.example": (550, b"no such user")}
    monkeypatch.setattr(M, "smtp_send", refuse)
    with pytest.raises(M.MailerError, match="SMTP refused"):
        M.redirect_letters(fcfg)
    assert [(e["recipient_id"], e["step"]) for e in w.events("redirect_attempt")] == [("1r1", "initial")]
    assert M.redirect_letters(fcfg) is None                                  # once per (recipient, step); the failure is for the operators


# ---------- watch via daria-inbox: incremental reads (Ivan, 2026-10-05: no root for the batches) ----------

def helper_world(scfg, monkeypatch, overlap=10):
    """A campaign read through the helper; `reads` records every `since` it is asked for, `inbox` is what it returns."""
    scfg["watch_overlap_minutes"] = overlap
    if overlap is None:
        del scfg["watch_overlap_minutes"]
    real_inbox_messages = M.inbox_messages
    w = answers_world(scfg, monkeypatch, ["pd1@example.org"], [])
    monkeypatch.setattr(M, "inbox_messages", real_inbox_messages)               # the World fakes it; these tests go through the dispatch
    w.reads = []
    inbox = []

    def helper(cfg, since, seen):
        w.reads.append(since)
        yield from inbox
    monkeypatch.setattr(M, "helper_messages", helper)
    w.helper_inbox = inbox
    return w


def test_a_helper_watch_reads_from_the_campaign_start_once_and_then_only_the_overlap(scfg, monkeypatch):
    w = helper_world(scfg, monkeypatch)
    M.watch(scfg)
    assert w.reads == [at("2026-09-29T00:00:00")]                                            # the first read: everything
    assert json.loads(M.watched_state(scfg).read_text()) == {"read_from": "2026-09-29T10:05:00+02:00"}
    w.now = at("2026-09-29T10:20:00")
    M.watch(scfg)
    assert w.reads[1] == at("2026-09-29T09:55:00")                                           # last read began 10:05, minus 10 minutes
    w.now = at("2026-09-29T10:21:00")
    M.watch(scfg)
    assert w.reads[2] == at("2026-09-29T10:10:00")


def test_a_failed_helper_watch_does_not_move_the_read_position(scfg, monkeypatch):
    w = helper_world(scfg, monkeypatch)
    M.watch(scfg)
    before = M.watched_state(scfg).read_text()
    w.now = at("2026-09-29T11:00:00")

    def broken(cfg, since, seen):
        raise M.MailerError("daria-inbox exited 1: HTTP 404")
        yield
    monkeypatch.setattr(M, "helper_messages", broken)
    with pytest.raises(M.MailerError, match="HTTP 404"):
        M.watch(scfg)
    assert M.watched_state(scfg).read_text() == before


def test_a_helper_watch_without_an_overlap_in_the_config_fails_loudly(scfg, monkeypatch):
    helper_world(scfg, monkeypatch, overlap=None)
    with pytest.raises(M.MailerError, match="watch_overlap_minutes"):
        M.watch(scfg)
    assert not M.watched_state(scfg).exists()


def test_a_config_that_reads_the_mailbox_any_other_way_than_daria_inbox_is_refused(cfg):
    """Ivan, 2026-10-05: the rule is that the mailbox is read only through daria-inbox."""
    for via in ("imap", "graph", None):
        conf = json.loads(cfg.read_text())
        conf.pop("watch_via")
        if via:
            conf["watch_via"] = via
        cfg.write_text(json.dumps(conf))
        with pytest.raises(M.MailerError, match=r'"watch_via" must be "daria-inbox".*got ' + re.escape(repr(via))):
            M.load_config(cfg)


def test_a_failing_helper_is_tried_again_three_times_and_the_last_failure_is_raised(monkeypatch):
    """05.10: a message moved to Junk between the helper's list and its fetch answers 404 and ends the helper; it took the desk
    down at 16:59 and a batch at 19:47."""
    calls, naps = [], []

    def run(cmd, **kw):
        calls.append(cmd)
        if len(calls) <= fails:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="Traceback ...\nurllib.error.HTTPError: HTTP Error 404: Not Found")
        return types.SimpleNamespace(returncode=0, stdout='{"a": 1}\n', stderr="")
    monkeypatch.setattr(M.subprocess, "run", run)
    monkeypatch.setattr(M, "sleep", naps.append)
    since = at("2026-10-05T10:00:00")
    fails = 3
    assert M.helper_output(since) == '{"a": 1}\n' and len(calls) == 4 and naps == [M.READ_RETRY_SECONDS] * 3
    assert calls[0] == ["sudo", "-n", M.DARIA_INBOX, "--since", "2026-10-05T10:00:00+02:00"]
    calls.clear(), naps.clear()
    fails = 4
    with pytest.raises(M.MailerError, match=r"(?s)exited 1 on 4 tries in a row: .*HTTP Error 404"):
        M.helper_output(since)
    assert len(calls) == 4 and len(naps) == 3


# ---------- mail from no campaign: the sweep ----------

def stranger(w, frm, text, mid, subject="Anfrage", t="2026-09-29T10:03:00", **headers):
    w.inbox.append(mail(frm, subject, text, at(t), mid, **headers))


def test_a_mail_from_an_address_and_domain_no_letter_went_to_is_classified_logged_and_forwarded(scfg, monkeypatch, reader):
    """Ivan, 2026-10-05: if a clinic writes from some other domain, we read it too; everything goes through the classifier.
    Before, `_watch` dropped such a mail: not in the thread, not from an address or domain we wrote to."""
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    stranger(w, "Personalabteilung <personal@klinikverbund-nord.example>", "Wir haben Interesse an Ihrer Pflegekraft.\n", "<s1@x>")
    assert M.watch(scfg) == []                                         # a campaign's watch leaves it
    (ev,) = M.sweep([scfg])
    assert (ev["kind"], ev["recipient_id"], ev["pattern"], ev["sweep"], ev["digest_to"]) == ("unmatched", None, "other", True, OPS)
    assert ev["from"] == "personal@klinikverbund-nord.example" and reader.asked[-1]["clinic"] is None and reader.asked[-1]["our_addresses"] == []
    assert M.digest([scfg]) == {OPS[0]: 1, OPS[1]: 1}
    d = _email.message_from_bytes(w.to_ops_of(OPS[0])[0][1].as_bytes(), policy=_email.policy.default)
    assert "письмо не привязано к нашим письмам" in d.get_body(("plain",)).get_content()
    assert M.sweep([scfg]) == [] and M.watch(scfg) == []              # read once


def test_advertising_is_classified_and_logged_but_not_forwarded(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    stranger(w, "steve@seo-leads.example", "Boost your pipeline today. Unsubscribe here.\n", "<s2@x>", subject="Re: quick question")
    reader.says("Boost your pipeline", pattern="unrelated", quote="Boost your pipeline today.")
    (ev,) = M.sweep([scfg])
    assert (ev["kind"], ev["pattern"], ev["actions"], ev["digest_to"] if "digest_to" in ev else None) == ("unrelated", "unrelated", [], None)
    assert "eml" not in ev and M.digest([scfg]) == {} and w.sent == [] and table(scfg) == []


def test_the_sweep_leaves_what_a_watch_takes_and_the_operators_and_the_boxs_own_mail(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    stranger(w, "x@elsewhere.example", "Danke, Konditionen?\n", "<t1@x>", In_Reply_To="<m1@x>")         # in the letter's thread
    stranger(w, "hr@a.example", "Guten Tag\n", "<t2@x>")                                                      # a domain we wrote to
    stranger(w, OPS[0], "stopp\n", "<t3@x>")                                                             # an operator's command
    stranger(w, "me@example.org", "our own\n", "<t4@x>")
    stranger(w, "postmaster@outlook.example", f"Your message to {OPS[1]} couldn't be delivered.\n", "<t5@x>", subject="Undeliverable: x")
    stranger(w, "unknown@elsewhere.example", "Hallo\n", "<t6@x>")
    assert [e["from"] for e in M.sweep([scfg])] == ["unknown@elsewhere.example"]
    assert sorted((e["kind"], e["from"]) for e in M.watch(scfg)) == [("operator_undeliverable", "postmaster@outlook.example"),
                                                                       ("reply", "x@elsewhere.example"), ("unmatched", "hr@a.example")]


def test_a_sweep_starts_when_it_first_runs_and_goes_on_from_the_last_read_less_the_overlap(scfg, monkeypatch):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    stranger(w, "old@elsewhere.example", "Vor Stunden\n", "<o1@x>", t="2026-09-29T08:00:00")           # before the first sweep
    stranger(w, "new@elsewhere.example", "Jetzt\n", "<o2@x>", t="2026-09-29T10:00:00")
    assert [e["from"] for e in M.sweep([scfg])] == ["new@elsewhere.example"]                           # now 10:05, overlap 10 min
    state = json.loads(M.swept_state(scfg).read_text())
    assert state == {"read_from": "2026-09-29T10:05:00+02:00"}
    w.now = at("2026-09-29T10:30:00")
    stranger(w, "later@elsewhere.example", "Spaeter\n", "<o3@x>", t="2026-09-29T10:16:00")
    stranger(w, "before@elsewhere.example", "Davor\n", "<o4@x>", t="2026-09-29T09:50:00")             # older than the last read (10:05) less 10 min
    assert [e["from"] for e in M.sweep([scfg])] == ["later@elsewhere.example"]


def test_a_mail_the_classifier_cannot_read_stops_the_sweep_and_is_read_again(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    stranger(w, "a@elsewhere.example", "Hallo eins\n", "<c1@x>")
    stranger(w, "b@elsewhere.example", "boom\n", "<c2@x>")
    stranger(w, "c@elsewhere.example", "Hallo drei\n", "<c3@x>")
    with pytest.raises(M.MailerError, match=r"could not read the mail from b@elsewhere.example .*overloaded"):
        M.sweep([scfg])
    assert not M.swept_state(scfg).exists()
    assert [e["from"] for e in w.events("inbound")] == ["a@elsewhere.example"]                          # the one before it stays logged
    reader.broken = False
    assert [e["from"] for e in M.sweep([scfg])] == ["b@elsewhere.example", "c@elsewhere.example"]


def test_an_opt_out_from_a_sender_we_never_wrote_to_blocks_the_addresses_it_names(scfg, monkeypatch, reader):
    w = answers_world(scfg, monkeypatch, ["pd@a.example"], [])
    stranger(w, "hr@klinikverbund-nord.example", "Bitte streichen Sie pd@a.example aus Ihrem Verteiler.\n", "<p1@x>")
    reader.says("streichen", pattern="opt_out", addresses=["pd@a.example"], scope="address", quote="Bitte streichen Sie pd@a.example aus Ihrem Verteiler.")
    (ev,) = M.sweep([scfg])
    assert ev["kind"] == "unmatched" and ev["actions"][0]["do"] == "opt_out" and [r["match"] for r in table(scfg)] == ["pd@a.example"]
