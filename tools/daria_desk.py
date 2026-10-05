#!/usr/bin/env python3
"""Daria's desk: the one reader of the operators' mail on daria.s@pflege-connect.work (TASK-345.12.1, .12.2).

Ivan, 2026-10-01: Daria answers him and Valentyn at any time; stop, skip and status by mail stay; anything else (a
question, a harder correction) is answered by Daria herself with her toolset: sales_brain read-only, the board tools
the WA harness gives Luna, the mailing state, the case documents, and her own backlog project "daria" as a task
pipeline. She has no shell.

Before the desk, every scheduled batch read every operator mail since its own announcement. With two batches on the
same box (nurse79 wave 1 and wave 2) a "стоп" meant for one wave halted both and every mail was answered twice. Now a
batch whose config has "desk" reads no operator mail; it checks this desk's heartbeat and halts when it goes stale.

Commands:
  run CONFIG                 the desk loop. Run as root like a live batch (sudo -E python3 tools/daria_desk.py run
                             CONFIG): it appends stop and skip to the campaigns' root-owned ledgers. Every
                             poll_seconds it stamps the heartbeat, reads the operators' new mail, acts on stop, skip
                             and status at once and answers everything else from a worker thread, one mail at a
                             time. Any error ends the desk after a mail to the operators; the batches halt by
                             themselves once the heartbeat is older than their limit.
  ask CONFIG TEXT [--from A] one question to Daria without mail; the answer is printed, nothing is sent.
  route CONFIG               the active batches and their operator threads, for a look.

Routing: a mail that answers one of a batch's own operator mails (announcement, report, notice, command answer) or a
desk answer about it goes to that batch; any other mail goes to every active batch. A batch is active from its
announcement until its "рассылка завершена" notice or a halt that only a new plan undoes. The classifier sees the
active batches with their clinics; a stop names the batches it means (none named: every one in scope), a skip acts
on the batch that holds each named clinic.

Desk config (JSON; paths relative to it): sender, sender_name, tz, operators, watch_via ("daria-inbox"), campaigns
[{config, label}], ledger, heartbeat, session_dir, start (ISO: mail received before it is not the desk's),
poll_seconds, overlap_minutes, classifier {model, claude_bin, run_as, timeout_seconds}, answerer {model, effort,
claude_bin, run_as, timeout_seconds, python, mcp_timeout_ms}, brain, docs {name: path}.
"""
import argparse
import email
import email.policy
import hashlib
import json
import os
import pwd
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clinic_mailer as M  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BOARD_TOOLS = ("search_postings", "get_posting", "list_clinics", "search_postings_with_housing", "list_clinics_with_housing",
               "list_cities_with_postings", "count_postings", "read_board_docs", "board_api_get", "get_clinic_contact")
PHONE_TOOLS = ("show_clinic_photos", "send_updated_cv", "read_history", "read_document", "find_stored_cv", "match_cv_to_postings")
DARIA_TOOLS = ("brain_tables", "brain_query", "mailing_state", "desk_log", "read_doc", "pipeline_list", "pipeline_view", "pipeline_create",
               "mailing_windows", "mailing_window_tail", "send_output_tail", "plan_batch", "start_batch", "stop_batch", "start_desk")
ALLOWED_TOOLS = tuple(f"mcp__jobs__{t}" for t in BOARD_TOOLS) + tuple(f"mcp__daria__{t}" for t in DARIA_TOOLS)
LOCK = threading.Lock()          # ledger appends and SMTP sends from the main loop and the answer worker


# ---------- config, ledger, heartbeat ----------

def load(path):
    path = Path(path).resolve()
    d = json.loads(path.read_text())
    d["_dir"], d["_path"] = path.parent, path
    for k in ("ledger", "heartbeat", "session_dir"):
        d[k] = (path.parent / d[k]).resolve()
    d["docs"] = {k: str((path.parent / v).resolve()) for k, v in d.get("docs", {}).items()}
    d["tz"] = ZoneInfo(d["tz"])
    d["operators"] = [a.lower() for a in d["operators"]]
    d["digest_at"] = datetime.strptime(d["digest_at"], "%H:%M").time()      # the daily mail of the clinics' answers, local time
    d["halt_grace"] = timedelta(minutes=d["halt_grace_minutes"])           # a halt resumed within it (a restart) is never mailed
    d["notify"] = [a.lower() for a in d["notify"]]               # who is told that the desk stopped (Ivan, 2026-10-05)
    d["campaigns"] = [{"label": c["label"], "path": str((path.parent / c["config"]).resolve()), "cfg": M.load_config(path.parent / c["config"])}
                      for c in d["campaigns"]]
    for c in d["campaigns"]:
        if c["cfg"]["sender"].lower() != d["sender"].lower():
            raise M.MailerError(f"{c['cfg']['campaign']} sends from {c['cfg']['sender']}, not from the desk's {d['sender']}")
    d["session_dir"].mkdir(parents=True, exist_ok=True)
    return d


def now(d):
    return datetime.now(d["tz"])


def read_ledger(d):
    p = d["ledger"]
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def log(d, event):
    event = {"ts": now(d).isoformat(timespec="seconds"), **event}
    with LOCK, d["ledger"].open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def stamp(d, since):
    tmp = d["heartbeat"].with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": now(d).isoformat(timespec="seconds"), "pid": os.getpid(),
                               "since": since.isoformat(timespec="seconds")}))
    os.chmod(tmp, 0o644)
    tmp.replace(d["heartbeat"])


# ---------- batches and routing ----------

def batches_of(c):
    """(batch, finished) for every scheduled batch of a campaign that was announced."""
    cfg = c["cfg"]
    ledger = M.read_ledger(cfg)
    announced = {e["batch_id"] for e in ledger if e["event"] == "announced"}
    out = []
    for bid in sorted(announced):
        batch = json.loads((cfg["batches"] / f"{bid}.json").read_text())
        halt = M.halt_event(ledger, bid)
        done = any(e["event"] == "notice" and e.get("batch_id") == bid and e.get("kind") == "done" for e in ledger)
        out.append((batch, done or bool(halt and halt.get("kind") != "error")))
    return out


def active(d):
    """[{"key", "label", "cfg", "batch"}] for every announced batch that has not ended."""
    out = []
    for c in d["campaigns"]:
        for batch, finished in batches_of(c):
            if not finished:
                out.append({"key": batch["batch_id"], "label": c["label"], "cfg": c["cfg"], "batch": batch})
    return out


def thread_ids(d, bs):
    """{Message-ID: batch ids} of every operator mail a batch or the desk sent about it."""
    ids = {}
    for b in bs:
        for e in M.read_ledger(b["cfg"]):
            if e.get("batch_id") != b["key"]:
                continue
            for k in ("message_id", "reply_message_id"):
                if e["event"] in ("announced", "report", "notice", "command") and e.get(k):
                    ids.setdefault(e[k], set()).add(b["key"])
    for e in read_ledger(d):
        if e.get("reply_message_id") and e.get("batch_ids"):
            ids.setdefault(e["reply_message_id"], set()).update(e["batch_ids"])
    return ids


def refs_of(msg):
    return re.findall(r"<[^>]+>", " ".join(str(msg.get(h) or "") for h in ("In-Reply-To", "References")))


def route(d, refs, bs):
    """The batches a mail is about: those whose operator thread it answers, else all active ones."""
    refs = set(refs)
    ids = thread_ids(d, bs)
    keys = set().union(*(ids[r] for r in refs if r in ids)) if refs & ids.keys() else set()
    hit = [b for b in bs if b["key"] in keys]
    return hit or bs


# ---------- classifier ----------

DESK_SYSTEM = """You sort one email that an operator (Ivan or Valentyn) wrote to Daria, the AI employee who runs email campaigns to clinics from her mailbox. A campaign batch sends every clinic a first letter and then follow-ups in the same thread on later days, until the clinic answers. Several batches can run at once (for example "волна 1" and "волна 2"). Operators write briefly and informally, in Russian, Ukrainian, German or English. You get the subject, the operator's own new words (quoted history is removed) and the batches this mail may concern, each with its label, what is left to send ("left": "first letters and follow-ups", "first letters", "only follow-ups" or "nothing") and its clinics, each with its letters: what went and when, what is planned and when, or why nothing more goes. The list can be empty: then no batch is running.

Decide what the operator wants:
- "stop": stop, cancel, abort, pause or hold a whole mailing, or send nothing more to anyone in it ("стоп", "отмена", "остановить рассылку", "не отправляй", "подожди, не запускай", "stop", "cancel", "abbrechen"). Dropping all follow-ups for everyone in a batch is "stop" when its "left" is "only follow-ups"; while first letters are left it is "other_command".
- "skip": send nothing more to one or more particular clinics while the rest continues ("не отправляй в Weiden", "убери Эрлер", "без Байройта", "Эрлер больше не пиши"). recipient_ids lists the ids of exactly those clinics.
- "status": asks what has been sent or what is still planned in a mailing.
- "other_command": wants any other change to a mailing: other times, other text, other or more recipients, only some steps for everyone, resume or restart after a stop, send now, faster or slower, a new mailing.
- "not_command": everything else: questions, thanks, agreement, comments, requests that are not about changing a mailing.
A message that wants everything stopped is "stop" even if it says more. A message that negates a stop ("не останавливай", "не надо отменять") is not "stop". A request to stop only some clinics is "skip". Only an explicit instruction changes a mailing: a question ("зачем", "почему", "why", "warum"), a doubt or a comment about a clinic is "not_command" even when it names clinics.
batch_ids: for "stop" and "status", the keys of the batches the operator names (by label, wave number, date or clinics); empty when they name none, which means every listed batch.

Answer with one JSON object and nothing else: {"intent": "stop|skip|status|other_command|not_command", "batch_ids": [], "recipient_ids": [], "why": "one short sentence in Russian"}"""


def claude_cmd(c, args):
    cmd = [c["claude_bin"], "-p", *args]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDECODE", "CLAUDE_CODE"))}
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", c["run_as"], "--"] + cmd
        env["HOME"] = pwd.getpwnam(c["run_as"]).pw_dir
    return cmd, env


def run_claude(c, args, stdin, cwd, what, extra_env=None):
    cmd, env = claude_cmd(c, args)
    env.update(extra_env or {})
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=c["timeout_seconds"], env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise M.MailerError(f"{what}: no answer within {c['timeout_seconds']} s")
    except FileNotFoundError as e:
        raise M.MailerError(f"{what}: {e.filename} not found")
    if p.returncode:
        raise M.MailerError(f"{what}: {Path(c['claude_bin']).name} exited {p.returncode}: {(p.stderr or p.stdout).strip()[-400:]}")
    try:
        envelope = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise M.MailerError(f"{what}: not JSON ({e}): {p.stdout[:300]!r}")
    if envelope.get("is_error"):
        raise M.MailerError(f"{what} reported an error: {envelope.get('result')!r}")
    return envelope.get("result") or ""


def batch_view(b):
    return {"key": b["key"], "label": b["label"], "left": M.left_to_send(b["cfg"], b["batch"]),
            "clinics": M.clinic_list(b["cfg"], b["batch"])}


def classify(d, subject, text, bs):
    c = d["classifier"]
    out = run_claude(c, ["--restricted", "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--output-format", "json",
                         "--no-session-persistence", "--model", c["model"], "--system-prompt", DESK_SYSTEM],
                     json.dumps({"subject": subject, "text": text, "batches": [batch_view(b) for b in bs]}, ensure_ascii=False),
                     "/", "classifier")
    found = re.search(r"\{.*\}", out, re.S)
    try:
        r = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        r = None
    if not isinstance(r, dict) or r.get("intent") not in M.COMMAND_INTENTS:
        raise M.MailerError(f"classifier: unexpected answer {out!r}")
    keys = {b["key"] for b in bs}
    clinic_ids = {k["id"] for b in bs for k in M.clinic_list(b["cfg"], b["batch"])}
    return {"intent": r["intent"], "batch_ids": [str(x) for x in r.get("batch_ids") or [] if str(x) in keys],
            "recipient_ids": [str(x) for x in r.get("recipient_ids") or [] if str(x) in clinic_ids],
            "unknown_ids": [str(x) for x in r.get("recipient_ids") or [] if str(x) not in clinic_ids],
            "why": str(r.get("why") or "")}


# ---------- commands ----------

def help_text(d):
    return (f"Команды — письмом на {d['sender']} с адреса {' или '.join(d['operators'])}: «стоп» или «отмена» — остановить "
            "рассылку (можно назвать волну; без названия — все идущие), фоллоу-апы тоже; «не отправлять в <клинику>» — убрать "
            "клинику; «статус». " + M.DESK_HELP)


def reply(d, box, mid, subject, body, batch_ids, kind):
    """One answer to all operators, in the thread of their mail; logged with the batches it concerned. `body` is a string
    or a Doc; the signature is added."""
    doc = M.doc_of(body)
    m = M.operator_mail({"sender": d["sender"], "sender_name": d["sender_name"]}, {"operators": d["operators"]},
                        subject if re.match(r"(?i)^(re|aw|отв):", subject) else f"Re: {subject or 'письмо'}",
                        M.Doc(*[b.rstrip() if isinstance(b, str) else b for b in doc.blocks], "Daria"), in_reply_to=mid, auto="auto-replied")
    with LOCK:
        refused = M.smtp_send(box, m)
    log(d, {"event": "answer", "kind": kind, "message_id": mid, "reply_message_id": m["Message-ID"], "batch_ids": batch_ids,
            "smtp_refused": sorted(refused)})
    if refused:
        raise M.MailerError(f"SMTP refused the answer to {mid} for {sorted(refused)}")
    return m["Message-ID"]


def act(d, c, bs, frm, mid):
    """Do a stop, skip or status on the batches in scope; (Russian result per batch, batch ids touched)."""
    if not bs:
        return ["Сейчас ни одна рассылка не идёт: " + ("останавливать нечего." if c["intent"] == "stop" else
                                                        "убирать нечего." if c["intent"] == "skip" else "статуса нет.")], []
    lines, touched = [], []
    if c["intent"] == "skip":
        for b in bs:
            ids = [i for i in c["recipient_ids"] if i in {k["id"] for k in M.clinic_list(b["cfg"], b["batch"])}]
            if ids:
                with LOCK:
                    lines.append(f"{b['label']}: " + M.do_skip(b["cfg"], b["batch"], {**c, "recipient_ids": ids}, frm, mid))
                touched.append(b["key"])
        if not touched:
            lines.append("Не понял, какую клинику убрать. Ничего не изменено: напишите название клиники как в списке ниже.")
        return lines, touched
    scope = [b for b in bs if b["key"] in c["batch_ids"]] or bs
    for b in scope:
        if c["intent"] == "stop":
            with LOCK:
                lines.append(f"{b['label']}: " + M.do_stop(b["cfg"], b["batch"], frm, mid))
        else:
            lines.append(f"{b['label']}: статус ниже, ничего не изменено.")
        touched.append(b["key"])
    return lines, touched


# ---------- Daria's answer ----------

ANSWER_SYSTEM = """You are Daria (Дарья), an AI employee of NDT Group, a placement agency that places nurses trained abroad with German hospitals on direct hire (Direktvermittlung; the clinic pays a fee when the contract is signed). You work from the mailbox daria.s@pflege-connect.work. Your colleagues: Ivan (the owner, ivan.d.kotelnikov@gmail.com) and Valentyn Vihandt (he talks to the clinics, v.vihandt@ndt-group.agency). One of them wrote to you; your answer goes to both.

How you answer:
- In Russian, short and plain, one idea per sentence: the answer first, then the facts that back it, no more than the question needs. You are a woman: about yourself write "проверила", "нашла", "завела". The first time a German term appears, give its meaning in Russian in brackets.
- Your answer is mailed as HTML and as plain text. Write short paragraphs; a list as lines starting with "- "; rows of one kind (clinics, letters with their times, batches, windows) as a Markdown table with a header row: | Клиника | Время | and a separator row | --- | --- |; **bold** for the one key word. No other Markdown.
- Questions can be about the campaign or about your own desk work (which mails came, what you did and answered); answer both. Look things up with your tools before you answer: the mailing state, your desk log (desk_log), the case documents (read_doc), sales_brain (brain_tables first, then brain_query), the board tools for clinics and vacancies, your pipeline. When one more lookup answers the question fully (names behind a count, the date of a reply), make it instead of saying you did not. Say briefly where a fact comes from. Never guess; if you could not find or check something, say so. If a tool fails, say which and how.
- Your answer goes only to Ivan and Valentyn, so it may give a candidate's name, phone, email or address when the question needs it (Ivan, 2026-10-01). Your pipeline is not private: in pipeline tasks a candidate is named by number only ("candidate 79").
- A mailing changes in two ways. The desk already carried out the mail commands "стоп", "не отправлять в <клинику>" and "статус" if this mail contained one (see "desk"). Besides that you have mailing tools: mailing_windows, mailing_window_tail and send_output_tail show what runs; plan_batch plans a batch (it sends nothing and approves nothing); stop_batch ends a batch's process (start_batch restarts it); start_batch starts or restarts a batch that has its approval; start_desk starts the desk. Use plan_batch when Ivan or Valentyn asks for a plan and stop_batch when either asks to stop; use start_batch and start_desk only when Ivan asks in this mail. Look first (mailing_state, mailing_windows), act, look again a minute later (send_output_tail), and say in the answer what you ran and what the output showed; an error in the output you say plainly. Every live mailing needs Ivan's approval of its exact plan: you never approve one, and you tell the operator which plan waits for it. Anything else the operator wants done (other texts, recipients or times in a plan, research that takes longer, anything that needs code) goes into your pipeline with pipeline_create; give the task id in the answer. Do not promise when a pipeline task gets done or that a mail follows: say only that it is in the pipeline.
- German text for clinics or candidates that you propose is a draft until Ivan approves it word for word; say so.
- No greeting formula, no signature: the desk adds "Daria". End with what happens next, if anything does."""


def mailing_state_text(d):
    """Everything about the desk's campaigns that an answer may need, as one text file."""
    out = [f"Состояние на {M.mailer_announce.when(now(d))}.\n"]
    for c in d["campaigns"]:
        cfg = c["cfg"]
        ledger = M.read_ledger(cfg)
        out.append(f"## {c['label']} — кампания {cfg['campaign']}, отправитель {cfg['sender']}")
        notes = cfg.get("announce", {}).get("notes")
        if notes and Path(notes).exists():
            out.append("Заметки к плану:\n" + Path(notes).read_text(encoding="utf-8").strip())
        recs = json.loads(cfg["recipients"].read_text())
        out.append("Адресаты:")
        for r in recs:
            v = r.get("vars", {})
            out.append(f"- {r['id']} {r['clinic']}: кому {', '.join(r['to'])}" + (f", копия {', '.join(r['cc'])}" if r["cc"] else "")
                       + (f"; тема «{v['BETREFF']}»" if v.get("BETREFF") else "")
                       + (f"; вакансии: {v['STELLEN'].replace(chr(10), ' ')}" if v.get("STELLEN") else ""))
        for batch, finished in batches_of(c):
            out.append(f"Пакет {batch['batch_id']} ({'закончен' if finished else 'идёт'}):\n" + M.state_text(cfg, batch, ledger))
        announced = {e["batch_id"] for e in ledger if e["event"] == "announced"}
        for path in sorted(cfg["batches"].glob("*.json")):
            if path.stem in announced:
                continue
            batch = json.loads(path.read_text())
            ann = batch.get("announce")
            approved = (cfg["approvals"] / f"{path.stem}.json").exists()
            halt = M.halt_event(ledger, path.stem)
            out.append(f"Пакет {path.stem} ("
                       + (f"остановлен до анонса: {halt['kind']}, {halt['reason']}; письма из него идут только новым планом" if halt else
                          "запланирован, ещё не анонсирован" + (f", анонс {M.mailer_announce.when(datetime.fromisoformat(ann['send_at']))}" if ann else "")
                          + f"; одобрение оператора: {'есть' if approved else 'нет, ждёт'}")
                       + "):\n" + M.state_text(cfg, batch, ledger))
        inbound = [e for e in ledger if e["event"] == "inbound"]
        if inbound:
            out.append("Входящие от клиник:")
            out += [f"- {e['ts'][:16]} {e['kind']} от {e['from']} ({e.get('recipient_id')}): {e.get('subject', '')}" for e in inbound]
        out.append("")
    return "\n".join(out)


def mcp_config(d, run_dir, asked_by):
    a = d["answerer"]
    env = M.load_env()
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs_env = {"PYTHONPATH": str(REPO), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "WA_SQLITE_PATH": str(run_dir / "no-wa.sqlite"), "WA_LUNA_SESSION_DIR": str(run_dir / "jobs"),
                "WA_LUNA_TOOLS_READY": str(run_dir / "jobs_ready.json"),
                **{k: env[k] for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if env.get(k)}}
    daria_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": pwd.getpwnam(a["run_as"]).pw_dir,
                 "DARIA_BRAIN": d["brain"], "DARIA_STATE": str(run_dir / "mailing_state.md"), "DARIA_DESK_LEDGER": str(d["ledger"]),
                 "DARIA_DOCS": json.dumps(d["docs"]), "DARIA_REPO": str(REPO), "DARIA_LOG": str(run_dir / "daria_calls.jsonl"),
                 "DARIA_CAMPAIGNS": json.dumps({c["cfg"]["campaign"]: c["path"] for c in d["campaigns"]}), "DARIA_DESK_CONFIG": str(d["_path"]),
                 "DARIA_TMUX": json.dumps(d["tmux"]), "DARIA_ASKED_BY": asked_by, "DARIA_TZ": str(d["tz"])}
    cfg = {"mcpServers": {"jobs": {"command": a["python"], "args": ["-m", "app.wa.luna.tools_server"], "cwd": str(REPO), "env": jobs_env},
                          "daria": {"command": a["python"], "args": [str(REPO / "tools" / "daria_tools.py")], "cwd": str(REPO),
                                    "env": daria_env}}}
    path = run_dir / "mcp.json"
    path.write_text(json.dumps(cfg))
    os.chmod(path, 0o600)
    return path


def answer_args(d, path):
    """--tools "" leaves no built-in tool (no Bash, no file read or write); the MCP tools are exactly ALLOWED_TOOLS.
    The board server also registers Luna's phone-rail and WhatsApp-data tools; --disallowedTools takes them out of
    the model's view (Ivan, 2026-10-01: Daria gets the WA harness's board tools, not the phone)."""
    a = d["answerer"]
    return ["--restricted", "--tools", "", "--mcp-config", str(path), "--strict-mcp-config", "--allowedTools", ",".join(ALLOWED_TOOLS),
            "--disallowedTools", ",".join(f"mcp__jobs__{t}" for t in PHONE_TOOLS),
            "--disable-slash-commands", "--no-session-persistence", "--output-format", "json",
            "--model", a["model"], "--effort", a["effort"], "--system-prompt", ANSWER_SYSTEM]


def ask(d, question):
    """Daria's answer to one operator mail; question = {from, subject, text, date, desk, thread}. Raises on failure."""
    a = d["answerer"]
    run_dir = d["session_dir"] / f"{now(d):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    path = mcp_config(d, run_dir, question["from"])
    (run_dir / "mailing_state.md").write_text(mailing_state_text(d), encoding="utf-8")
    if os.geteuid() == 0:
        uid, gid = pwd.getpwnam(a["run_as"]).pw_uid, pwd.getpwnam(a["run_as"]).pw_gid
        for p in [run_dir, *run_dir.rglob("*")]:
            os.chown(p, uid, gid)
        d["ledger"].touch()
        os.chown(d["ledger"], uid, gid)                  # her mailing tools append to it as that user (the audit trail)
    try:
        text = run_claude(a, answer_args(d, path), json.dumps({"now": M.mailer_announce.when(now(d)), **question}, ensure_ascii=False),
                          str(run_dir), "Daria's answer", {"MCP_TIMEOUT": str(a["mcp_timeout_ms"])})
    finally:
        path.unlink()                               # it holds the board's API key
    if not (run_dir / "jobs_ready.json").exists():
        raise M.MailerError(f"the board tools never started for this answer (no readiness stamp in {run_dir}): the answer "
                            f"was written without the board. Run `{a['python']} -m app.wa.luna.tools_server` to see why")
    return text.strip(), run_dir


def thread_history(d, refs):
    """Earlier questions and Daria's answers in this mail thread, oldest first."""
    refs = set(refs)
    ev = read_ledger(d)
    asked = {e["message_id"]: e for e in ev if e["event"] == "mail"}
    out = []
    for e in ev:
        if e["event"] == "answered" and (e["reply_message_id"] in refs or e["message_id"] in refs):
            q = asked.get(e["message_id"], {})
            out.append({"from": q.get("from"), "text": q.get("text"), "daria": e.get("answer")})
    return out


def question_of(d, mail):
    """What Daria gets for one logged operator mail."""
    desk = ("в письме нет команды рассылки" if mail["intent"] == "not_command" else
            "просьба изменить рассылку, которую письмом не выполнить: заведи задачу в своём пайплайне и назови её номер")
    return {"from": mail["from"], "subject": mail["subject"], "text": mail["text"], "date": mail["date"], "desk": desk,
            "thread": thread_history(d, mail.get("refs", []))}


def unanswered(d):
    """Logged question mails without an answer or a failure notice: queued when the desk ended."""
    ev = read_ledger(d)
    done = {e["message_id"] for e in ev if e["event"] in ("answered", "answer_failed")}
    return [e for e in ev if e["event"] == "mail" and e["intent"] in ("other_command", "not_command") and e["message_id"] not in done]


def answer_worker(d, box, jobs):
    """Answers the queued mails one at a time. A mail counts as handled once it is logged, so a failed answer is
    told to the operators instead of being retried."""
    while True:
        mail = jobs.get()
        if mail is None:
            return
        mid, batch_ids = mail["message_id"], mail["batch_scope"]
        question = question_of(d, mail)
        try:
            text, run_dir = ask(d, question)
            rid = reply(d, box, mid, question["subject"], text + "\n\n" + help_text(d), batch_ids, "answer")
            log(d, {"event": "answered", "message_id": mid, "reply_message_id": rid, "answer": text, "run_dir": str(run_dir)})
            print(f"answered {mid}: {len(text)} chars")
        except Exception as e:
            log(d, {"event": "answer_failed", "message_id": mid, "error": str(e)})
            print(f"ERROR answering {mid}: {e}", file=sys.stderr)
            try:
                reply(d, box, mid, question["subject"], f"Не смогла ответить на это письмо: {e}\n\nИван увидит ошибку в журнале "
                      "стола Дарьи. Команды (стоп, убрать клинику, статус) работают как прежде.", batch_ids, "answer_failed")
            except Exception as ne:
                log(d, {"event": "error_not_told", "message_id": mid, "error": str(ne)})
        finally:
            jobs.task_done()


# ---------- the loop ----------

def digest_due(d, t):
    """True from digest_at on, once a day: the digest is logged as an event of the desk ledger."""
    return t.time() >= d["digest_at"] and not any(e["event"] == "digest" and e["date"] == f"{t:%Y-%m-%d}" for e in read_ledger(d))


def status_check(d, box):
    """Mail the notify list every halt of a batch that is still in force after halt_grace_minutes, once; nothing else
    (Ivan, 2026-10-05: no notice of an interruption or of a restart, only of what stays broken)."""
    for c in d["campaigns"]:
        cfg = c["cfg"]
        for bid, halt in M.unnoticed_halts(cfg, d["halt_grace"]):
            title, text = M.halt_notice(halt)
            M.notify(cfg, box, json.loads((cfg["batches"] / f"{bid}.json").read_text()), "halt", title, text, halt_ts=halt["ts"])


def handled(d):
    """Message-IDs the desk or a batch already answered."""
    seen = {e["message_id"] for e in read_ledger(d) if e["event"] == "mail"}
    for c in d["campaigns"]:
        seen |= {e["message_id"] for e in M.read_ledger(c["cfg"]) if e["event"] == "command" and e.get("intent") != "classifier_failed"}
    return seen


def operator_mail(d, since, seen):
    """(folder, msg, mid, frm) for each new operator mail since `since`."""
    cfg = {"sender": d["sender"], "watch_via": d["watch_via"], "watch_folders": d.get("watch_folders", [])}
    for folder, raw in M.inbox_messages(cfg, None, since, seen):
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        frm = parseaddr(str(msg.get("From") or ""))[1].lower()
        mid = str(msg.get("Message-ID") or "").strip() or "sha256:" + hashlib.sha256(raw).hexdigest()
        if frm in d["operators"] and mid not in seen:
            yield folder, msg, mid, frm


def handle(d, box, jobs, folder, msg, mid, frm):
    subject = str(msg.get("Subject") or "").strip()
    auth = " ".join(re.findall(r"\b(?:spf|dkim|dmarc)=\w+", str(msg.get("Authentication-Results") or "")))
    base = {"message_id": mid, "from": frm, "subject": subject, "date": str(msg.get("Date") or ""), "folder": folder, "auth": auth}
    if M.automatic(msg):
        return log(d, {"event": "mail", **base, "intent": "auto_reply", "result": "not answered: an automatic reply"})
    text = M.fresh_text({"sender": d["sender"]}, msg)
    refs = refs_of(msg)
    bs = route(d, refs, active(d))
    c = classify(d, subject, text, bs)
    mail = log(d, {"event": "mail", **base, "refs": refs, "text": text, "batch_scope": [b["key"] for b in bs], **c})
    if c["intent"] in ("stop", "skip", "status"):
        lines, touched = act(d, c, bs, frm, mid)
        state = [M.state_table(b["cfg"], b["batch"], label=f"{b['label']} ({b['key']})") for b in bs if b["key"] in touched] \
            or [M.state_table(b["cfg"], b["batch"], label=f"{b['label']} ({b['key']})") for b in bs]
        quoted = " ".join(text.split())
        body = M.Doc(f"Письмо от {frm}: «{quoted}»\nПонято как: {c['intent']} ({c['why']})", "\n".join(lines), *state, help_text(d))
        reply(d, box, mid, subject, body, touched, c["intent"])
        print(f"{c['intent']} from {frm}: {' | '.join(lines)}")
        return
    jobs.put(mail)
    print(f"{c['intent']} from {frm}: queued for Daria's answer")


def run(d):
    with M.single_process(d["_path"], "the desk"):
        return _run(d)


def _run(d):
    box = M.mailbox(d["sender"])
    beat = json.loads(d["heartbeat"].read_text()) if d["heartbeat"].exists() else None
    start = datetime.fromisoformat(d["start"])
    since = max(start, datetime.fromisoformat(beat["since"]) - timedelta(minutes=d["overlap_minutes"])) if beat else start
    list(operator_mail(d, now(d), set()))                           # the inbox can be read
    check = classify(d, "статус", "Какой статус рассылки?", active(d))
    if check["intent"] != "status":
        raise M.MailerError(f"classifier self-check: 'Какой статус рассылки?' came back as {check['intent']}, not status")
    jobs = queue.Queue()
    left = unanswered(d)
    for mail in left:
        jobs.put(mail)
    worker = threading.Thread(target=answer_worker, args=(d, box, jobs), daemon=True)
    worker.start()
    log(d, {"event": "desk_started", "pid": os.getpid(), "since": since.isoformat(timespec="seconds"), "requeued": len(left)})
    print(f"desk up: {d['sender']}, operators {', '.join(d['operators'])}, mail since {since:%Y-%m-%d %H:%M}, "
          f"{len(active(d))} active batches, {len(left)} questions still to answer")
    before = {s: signal.signal(s, M.ended) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        while True:
            t0 = now(d)
            stamp(d, since)
            if not worker.is_alive():
                raise M.MailerError("the answer worker died")
            seen = handled(d)
            for folder, msg, mid, frm in operator_mail(d, since, seen):
                handle(d, box, jobs, folder, msg, mid, frm)
            status_check(d, box)
            if digest_due(d, t0):
                answers = M.digest([c["cfg"] for c in d["campaigns"]], box)
                log(d, {"event": "digest", "date": f"{t0:%Y-%m-%d}", "answers": answers})
            since = t0 - timedelta(minutes=d["overlap_minutes"])     # the server's and Exchange's clocks differ
            time.sleep(max(0, d["poll_seconds"] - (now(d) - t0).total_seconds()))
    except BaseException as e:
        reason = str(e) or type(e).__name__
        log(d, {"event": "desk_stopped", "reason": reason, "trace": traceback.format_exc()[-2000:]})
        try:
            m = M.operator_mail({"sender": d["sender"], "sender_name": d["sender_name"]}, {"operators": d["notify"]},
                                "Дарья не читает почту операторов",
                                f"Стол Дарьи остановился: {reason}\n\nПока он не запущен снова, команды и вопросы письмом не "
                                "читаются. Рассылки в режиме стола прервутся сами, когда пульс стола устареет. Иван запускает стол "
                                "той же командой; письма, пришедшие за это время, будут прочитаны.\n\nDaria\n")
            M.smtp_send(box, m)
        except Exception as ne:
            print(f"ERROR: the operators were not told the desk stopped: {ne}", file=sys.stderr)
        raise
    finally:
        for s, h in before.items():
            signal.signal(s, h)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("config")
    p = sub.add_parser("ask"); p.add_argument("config"); p.add_argument("text"); p.add_argument("--from", dest="frm", default="ivan.d.kotelnikov@gmail.com")
    p = sub.add_parser("route"); p.add_argument("config")
    a = ap.parse_args(argv)
    d = load(a.config)
    try:
        if a.cmd == "run":
            run(d)
        elif a.cmd == "ask":
            text, run_dir = ask(d, {"from": a.frm, "subject": "вопрос", "text": a.text, "date": "", "desk": "в письме нет команды рассылки",
                                    "thread": []})
            print(text)
            print(f"\n(run dir {run_dir})", file=sys.stderr)
        else:
            bs = active(d)
            for b in bs:
                print(f"{b['label']}: {b['key']}, left {M.left_to_send(b['cfg'], b['batch'])}")
            for mid, keys in thread_ids(d, bs).items():
                print(f"  {mid} -> {', '.join(sorted(keys))}")
    except M.MailerError as e:
        print("ERROR:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
