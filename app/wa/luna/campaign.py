"""Campaign sender (TASK-103): we send one approved Meta template to a lead list ourselves, Luna answers the replies.

Usage:
    python -m app.wa.luna.campaign --campaign-id SLUG --template-id ID --leads FILE.csv|FILE.json
        [--params JSON|@FILE] [--send [--retry-uncertain] [--retry-delivery-failed]] [--no-wait]
        [--batch-size 10] [--batch-interval-min 12] [--window 09-21] [--tz Europe/Berlin] [--report PATH]
        (--import-history-db DB --import-history-queries SQL --import-history-source LABEL
         [--import-history-media-root DIR ...] | --override-no-history-source)
    python -m app.wa.luna.campaign --campaign-id SLUG --status [--report PATH]
    python -m app.wa.luna.campaign --campaign-id SLUG --template-id ID --mark-sent PHONE=WAMID [...]

Load .env first (set -a; . ./.env; set +a): Meta token and phone number id, WA_AUTOSEND, data paths.

LEAD FILE. CSV with a header row, or a JSON list of objects. ``phone`` is required per lead. Template values use
the keys of app/wa/meta.py (comment above TEMPLATE_FIELDS) as dotted paths: ``body.1`` ({{1}}), ``header.1``,
``buttons.0.payload``, ``body.first_name`` (NAMED); JSON may nest them instead ({"body": {"1": "Frau X"}}). An empty
value counts as not given. Other columns are listed as ignored, their values are not used. ``--params`` gives values
for every lead (e.g. quick-reply payloads); a key set both there and in the lead file fails that lead. No value is
ever invented: a missing, extra or invalid variable fails that lead in the plan (meta.validate_template_params).

HISTORY SOURCE (TASK-105). Every dry-run and --send reads the old system through the history import
(--import-history-*): its opt_outs records (opt-outs and declines kept outside the chat) and the Stopp messages
of its chat. A phone with any of them is never sent: skip_opted_out (an opt-out or a Stopp) or skip_declined
(declines only), the reason naming each record. --override-no-history-source is the only way to run without the
source; it is printed as a WARNING and recorded in the report (history_source).

DRY-RUN (default). Resolves the template by id (GET, must be APPROVED), canonicalizes phones (unparseable,
duplicate and conflicting duplicate leads reported, none dropped silently), validates and renders each lead, and
plans each phone from a read-only in-memory copy of data/wa.sqlite: owner (wa_ownership, else the
WA_REAL_SYSTEM_PHONES_FILE check), thread stage/ball, stopped, declined, marketing opt-out (user_preferences stop,
failed status 131050), cross-rail suppression (wa_suppressions, TASK-113), this and other campaigns' claims, history
import preview (import_history dry-run, with its opt-out records and chat Stopps). Writes no database row, sends
nothing; only the report file.

--send (needs WA_AUTOSEND=1). Per phone, in lead-file order:
1. history import --apply (before the claim: its card merge refuses while any claim of the phone is in flight);
   an opt-out/decline record or a Stopp in the imported history skips the phone (the import marks the card
   declined);
2. one transaction: the window re-checked (the import can take minutes; closed -> the phone waits for the next start),
   eligibility re-checked, no turn in flight, claim the next attempt in wa_campaign_sends
   in_progress (+ nudge claim 'campaign:<id>:<attempt>', reply-turn claim 'campaign:<id>' so the webhook worker
   waits), prior owner recorded (a retry keeps the owner recorded before the first flip),
   ownership flipped to us (routing.flip_to_us_for_campaign, reason 'campaign:<id>');
3. POST the template (definition + params);
4. wamid -> one transaction: thread, outbound row kind=template with the rendered text and meta, last_outbound_at,
   card.campaign (store.record_campaign_send), attempt sent. HTTP 4xx -> attempt failed with code/payload,
   wa_send_failures, ownership restored. Network error, timeout, HTTP 5xx, 2xx without wamid -> attempt uncertain,
   wa_send_failures, ownership stays with us. A crash after step 2 leaves in_progress, reported as uncertain.
ATTEMPTS (TASK-106). Every claim is its own attempt row (1..n) with its own wamid, error code and delivery status;
a later attempt never overwrites an earlier one. The phone's claim is its latest attempt. Re-running continues: sent
is skipped, failed is claimed again, uncertain/in_progress is never resent without --retry-uncertain (check --status
first); one that did go out (a status for a wamid we never recorded, with the template's category) is recorded with
--mark-sent PHONE=WAMID (mark_sent). A sent template Meta later reported failed (latest status of its wamid, e.g.
131042, 131049, 131026) is planned delivery_failed and not resent; --retry-delivery-failed (Ivan 2026-09-14) resends
it in the same campaign as the next attempt, except to a phone that was stopped, declined, opted out or wrote to us
since the campaign first claimed it (skip_replied). Once any attempt went out (sent, delivered or not), every later
resend (retry_failed, --retry-uncertain) makes the same skip_replied check. Stopped, declined and opted-out phones
are never sent, and neither is a suppressed one (skip_suppressed, app/wa/suppression.py: the do-not-contact list is
keyed on the human and shared by both rails, so a Stopp typed to the phone rail stops a Meta template too; checked
again right before the POST, where it fails the attempt permanently). A phone marked as a test number (TASK-109,
app/wa/luna/test_threads.py) is skip_test_number before any other action, in the dry run and in --send: a campaign
template never lands in an operator's manual test.

PACING. At most --batch-size claims of this campaign within any --batch-interval-min (counted from claimed_at in
the database, so a restart keeps the pace), only inside --window local hours of --tz. Outside the window the run
waits for the next start, between batches it waits for the interval (both logged); --no-wait exits 3 instead, a
later run continues. Defaults are Ivan's values (2026-09-14): about 10 every 10-15 minutes, 09:00-21:00
Europe/Berlin.

--status. Read-only: per phone the latest attempt (state, wamid, send error, latest delivery status and its errors
from wa_message_statuses) and every attempt with the same fields; statuses seen since an uncertain attempt's claim;
replies since the first attempt that may have reached the candidate, each matched to the attempt it answers (the
attempt its context names, else the latest one before it; an attempt Meta reported undelivered reached nobody);
messages since the first claim that answer no attempt (not_answering_an_attempt); stage, declined, owner; totals.

REPORT. stdout plus a JSON file (--report, default ~/pflege-campaign-reports/<campaign>-<mode>-<UTC>.json, 0600;
it holds phone numbers and names), rewritten after every send.

EXIT. 0 done; 1 a send of this run needs attention (failed, uncertain, import error), or a dry-run or finished
send run whose plan holds a lead needing attention (invalid lead, variables, duplicate conflict, uncertain, delivery
failed); 2 configuration or access error (template not approved, token, AUTOSEND, no history source and no override,
source access); 3 --no-wait stopped
before the list was done and no send of this run needs attention (problem leads of the plan are still printed and
reported); 130 interrupted.
"""
import argparse
import collections
import csv
import io
import json
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from typing import NamedTuple
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config as C
from .. import meta as M
from .. import routing as R
from .. import store as ST
from .. import suppression as SUP
from .. import transport as T
from . import import_history as IH
from . import reporting as REP
from . import shadow_run as SR

DEFAULT_BATCH_SIZE = 10            # Ivan 2026-09-14: batches of about 10
DEFAULT_BATCH_INTERVAL_MIN = 12    # Ivan 2026-09-14: every 10-15 minutes
DEFAULT_WINDOW = "09-21"           # Ivan 2026-09-14: only 09:00-21:00
DEFAULT_TZ = "Europe/Berlin"
DEFAULT_REPORT_DIR = pathlib.Path.home() / "pflege-campaign-reports"
MIN_PHONE_DIGITS = 8               # as import_history.py: canonicalize_phone("garbage") -> "+49"
OPT_OUT_STATUS_CODE = "131050"     # Meta: the recipient stopped marketing messages from this business
LIST_PARAM_KEYS = ("carousel", "tap_target_configuration")
OVERRIDE_DETAIL = ("run without the history source: opt-out and decline records of the old system and Stopp "
                   "messages in its chat were not checked for any phone")

SENDABLE = ("send", "retry_failed", "retry_uncertain", "retry_delivery_failed")
PROBLEM_ACTIONS = ("invalid_phone", "variables_error", "conflicting_duplicate", "uncertain", "import_error",
                   "delivery_failed")
PROBLEM_RESULTS = ("failed", "uncertain", "import_error")
EXIT_OK, EXIT_ATTENTION, EXIT_CONFIG, EXIT_NOT_FINISHED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class CampaignError(RuntimeError):
    """Configuration or input that stops the whole run (exit 2)."""


class Retry(NamedTuple):
    """The operator's explicit retry flags: --retry-uncertain, --retry-delivery-failed (TASK-106)."""
    uncertain: bool = False
    delivery_failed: bool = False


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _say(*parts):
    print(*parts, flush=True)


# --- lead file ------------------------------------------------------------------------------------------------

def _flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: value}


def read_leads(path):
    """-> (rows, info). rows: [{line, raw_phone, variables}] in file order, variables = dotted template keys with a
    non-empty value; info: {path, format, rows, ignored_columns}. Raises ValueError for a file that is not a lead
    file (no phone column, duplicate header, stray cells, not a list of objects)."""
    path = pathlib.Path(path)
    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError(f"{path}: a JSON lead file is a list of objects")
        records, fmt = [(i + 1, _flatten(r)) for i, r in enumerate(data)], "json"
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        header = reader.fieldnames or []
        if "phone" not in header:
            raise ValueError(f"{path}: the CSV header needs a 'phone' column, got {header}")
        if len(set(header)) != len(header):
            raise ValueError(f"{path}: duplicate CSV columns in {header}")
        records = []
        for r in reader:
            if None in r:
                raise ValueError(f"{path} line {reader.line_num}: more cells than header columns")
            records.append((reader.line_num, dict(r)))
        fmt = "csv"
    else:
        raise ValueError(f"{path}: a lead file is .csv or .json")
    rows, ignored = [], []
    for line, flat in records:
        variables = {}
        for key, value in flat.items():
            if key == "phone":
                continue
            if key.split(".", 1)[0] not in M._TOP_KEYS:
                if key not in ignored:
                    ignored.append(key)
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            variables[key] = value.strip() if isinstance(value, str) else value
        raw = flat.get("phone")
        rows.append({"line": line, "raw_phone": None if raw is None else str(raw), "variables": variables})
    return rows, {"path": str(path), "format": fmt, "rows": len(rows), "ignored_columns": ignored}


def unflatten(flat):
    """Dotted keys -> the nested values meta.py takes. -> (params, problems)."""
    params, problems = {}, []
    for key in sorted(flat):
        parts = key.split(".")
        if any(not p for p in parts):
            problems.append(f"{key}: empty path segment")
            continue
        node = params
        for i, part in enumerate(parts[:-1]):
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                problems.append(f"{key}: conflicts with {'.'.join(parts[:i + 1])}")
                break
            node = child
        else:
            if parts[-1] in node:
                problems.append(f"{key}: given twice (also as a nested value)")
            else:
                node[parts[-1]] = flat[key]
    for key in LIST_PARAM_KEYS:
        value = params.get(key)
        if isinstance(value, dict):
            if sorted(value) != [str(i) for i in range(len(value))]:
                problems.append(f"{key}: indexes must be 0..{len(value) - 1}, got {sorted(value)}")
            else:
                params[key] = [value[str(i)] for i in range(len(value))]
    return params, problems


def canonical_phone(raw):
    """-> +E.164 or None when the value does not canonicalize to a real-looking number."""
    canon = M.canonicalize_phone(raw) if raw is not None else ""
    return canon if canon and len(canon.lstrip("+")) >= MIN_PHONE_DIGITS else None


def _raw_digits(raw):
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    return digits[2:] if not text.startswith("+") and digits.startswith("00") else digits


def resolve_leads(rows, definition, common):
    """Per lead: canonical phone, merged params, validation problems, rendered view, and a first ``action``
    (invalid_phone / variables_error / duplicate / conflicting_duplicate, else None = plan from state)."""
    common_flat = _flatten(common or {})
    leads, by_phone = [], collections.defaultdict(list)
    for row in rows:
        lead = {"line": row["line"], "raw_phone": row["raw_phone"], "phone": None, "phone_rewritten": False,
                "variables": row["variables"], "params": None, "rendered": None, "problems": [], "action": None,
                "reason": None}
        leads.append(lead)
        phone = canonical_phone(row["raw_phone"])
        if phone is None:
            lead["action"] = "invalid_phone"
            lead["reason"] = f"phone {row['raw_phone']!r} does not canonicalize to a number"
            continue
        lead["phone"], lead["phone_rewritten"] = phone, _raw_digits(row["raw_phone"]) != phone[1:]
        by_phone[phone].append(lead)
        both = sorted(set(common_flat) & set(row["variables"]))
        params, problems = unflatten({**common_flat, **row["variables"]})
        problems = [f"{k}: set in --params and in the lead file" for k in both] + problems
        if not problems:
            problems = M.validate_template_params(definition, params)
        lead["params"] = params
        if problems:
            lead["problems"], lead["action"] = problems, "variables_error"
            lead["reason"] = "; ".join(problems)
        else:
            lead["rendered"] = M.render_template(definition, params)
    for phone, group in by_phone.items():
        if len(group) < 2:
            continue
        lines = [g["line"] for g in group]
        if all(g["variables"] == group[0]["variables"] for g in group):
            for g in group[1:]:
                g["action"], g["reason"] = "duplicate", f"same phone and values as line {group[0]['line']}"
        else:
            for g in group:
                g["action"] = "conflicting_duplicate"
                g["reason"] = f"{phone} is on lines {lines} with different values"
    return leads


# --- window and pacing ------------------------------------------------------------------------------------------

class Window:
    """Local hours [start, end) in one timezone, e.g. '09-21'."""

    def __init__(self, spec, tz):
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", str(spec or "").strip())
        if not m or not 0 <= int(m.group(1)) < int(m.group(2)) <= 24:
            raise ValueError(f"--window {spec!r}: expected HH-HH with 0 <= start < end <= 24")
        self.start, self.end, self.spec, self.tz = int(m.group(1)), int(m.group(2)), spec, ZoneInfo(tz)

    def is_open(self, now):
        return self.start <= now.astimezone(self.tz).hour < self.end

    def next_open(self, now):
        """The next window start after ``now`` (UTC)."""
        local = now.astimezone(self.tz)
        day = local.date() if local.hour < self.start else local.date() + timedelta(days=1)
        return datetime.combine(day, dtime(self.start), tzinfo=self.tz).astimezone(timezone.utc)

    def describe(self):
        return f"{self.start:02d}:00-{self.end:02d}:00 {self.tz.key}"


def next_claim_at(c, campaign_id, now, batch_size, interval):
    """None when a claim may happen now, else when the batch limit allows the next one (UTC)."""
    recent = ST.campaign_claims_after(c, campaign_id, _iso(now - interval))
    if len(recent) < batch_size:
        return None
    return datetime.fromisoformat(recent[len(recent) - batch_size]) + interval


# --- state of one phone -----------------------------------------------------------------------------------------

def _apply_schema(c):
    c.executescript(ST.SCHEMA + R.SCHEMA)
    ST._migrate(c)
    ST.ensure_campaign_schema(c)


@contextmanager
def snapshot_db():
    """data/wa.sqlite opened mode=ro and copied into memory (shadow_run.db_copy); an empty in-memory database when
    the file does not exist. The original is never written."""
    if pathlib.Path(C.SQLITE_PATH).exists():
        with SR.db_copy() as c:
            _apply_schema(c)
            yield c
        return
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        _apply_schema(c)
        yield c
    finally:
        c.close()


@contextmanager
def live_db():
    c = R.db()
    try:
        ST.ensure_campaign_schema(c)
        yield c
    finally:
        c.close()


def ownership_view(c, phone):
    """The recorded owner, or for an undecided phone where route_decision would send it."""
    rec = R.ownership(c, phone)
    if rec:
        return {"recorded": True, **rec}
    try:
        known = R._is_known_to_real_system(phone)
    except RuntimeError as exc:
        return {"recorded": False, "would_route_to": None, "known_phones_error": str(exc)}
    return {"recorded": False, "would_route_to": "them" if known else "us",
            "reason": "known_to_real_system" if known else "new_lead"}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def marketing_opt_out(c, phone):
    """The latest Meta marketing preference signal of this phone when it is a stop, else None: a user_preferences
    webhook (category marketing_messages, value stop/resume) or a failed status with code 131050."""
    signals = []
    for e in ST.webhook_events_for(c, phone):
        raw = e["raw"]
        if e["kind"] == "user_preferences" and isinstance(raw, dict) and raw.get("category") == "marketing_messages":
            signals.append((_int(raw.get("timestamp")), str(raw.get("value") or "").lower(),
                            {"source": "user_preferences", "value": raw.get("value"), "detail": raw.get("detail"),
                             "timestamp": raw.get("timestamp")}))
    for s in ST.message_statuses_for(c, phone, "failed"):
        if any(str(err.get("code")) == OPT_OUT_STATUS_CODE for err in s["errors"] or []):
            signals.append((_int(s["timestamp"]), "stop", {"source": "failed_status", "code": OPT_OUT_STATUS_CODE,
                                                            "wamid": s["wamid"], "timestamp": s["timestamp"]}))
    if not signals:
        return None
    _ts, value, evidence = max(signals, key=lambda s: s[0])
    return evidence if value == "stop" else None


def _claim_view(row):
    if row is None:
        return None
    keys = ("campaign_id", "attempt", "state", "template_id", "template_name", "wamid", "error", "error_code",
            "prior_owner", "prior_reason", "prior_since", "ownership_restore", "claimed_at", "sent_at", "finished_at")
    return {k: row[k] for k in keys}


def reach_anchor(attempt):
    """From when an attempt may have reached the candidate: sent_at of a sent one, claimed_at of one whose POST result
    is unknown (in_progress/uncertain); None for a failed one (Meta refused the POST, nothing went out) and for a sent
    one whose latest delivery status is failed (Meta reported it undelivered; review 2026-09-15: --status credited
    it with a message the candidate wrote without ever seeing it)."""
    if (attempt["delivery"] or {}).get("status") == "failed":
        return None
    return {"sent": attempt["sent_at"], "in_progress": attempt["claimed_at"],
            "uncertain": attempt["claimed_at"]}.get(attempt["state"])


def delivery_view(c, wamid):
    """The latest delivery status of a sent template (wa_message_statuses, the one record), or None."""
    latest = ST.latest_message_status(c, wamid) if wamid else None
    if latest is None:
        return None
    return {"status": latest["status"], "at": _ts_iso(latest["timestamp"]),
            "errors": [{"code": e.get("code"), "title": e.get("title") or e.get("message"),
                        "details": (e.get("error_data") or {}).get("details")} for e in latest["errors"] or []]}


def phone_state(c, campaign_id, phone):
    """Everything the plan and the send decision read about one phone. Never creates a thread."""
    thread = None
    if c.execute("select 1 from wa_threads where phone=?", (phone,)).fetchone():
        t = ST.thread(c, phone)
        card = t["slots"]
        thread = {"stage": REP.stage_for(card), "ball": REP.ball_for(c, phone), "stopped": t["stopped"],
                  "is_test": t["is_test"], "test_marked_at": t["test_marked_at"],
                  "stopped_reason": t["stopped_reason"], "declined": bool(card.get("declined")),
                  "declined_at": card.get("declined_at"), "declined_reason": card.get("declined_reason"),
                  "prior_opt_outs": card.get("prior_opt_outs", []), "already_placed": bool(card.get("already_placed")),
                  "campaign_on_card": card.get("campaign"), "prior_contact": bool(card.get("prior_contact")),
                  "prior_placement": card.get("prior_placement"), "last_inbound_at": t["last_inbound_at"],
                  "last_outbound_at": t["last_outbound_at"], "messages": ST.last_message_id(c, phone) > 0,
                  "unread_media": card.get("_unread_media", [])}
    attempts = [{**_claim_view(a), "delivery": delivery_view(c, a["wamid"])}
                for a in ST.campaign_attempts(c, campaign_id, phone)]
    this = None
    if attempts:
        # the phone's claim = its latest attempt; every attempt, and what the candidate wrote since the first claim
        # (TASK-106: a phone that wrote in the meantime is not resent)
        first = attempts[0]["claimed_at"]
        inbound = [m for m in ST.messages_for(c, phone, direction="in") if m["at"] >= first]
        this = {**attempts[-1], "attempts": attempts, "first_claimed_at": first,
                "inbound_since_first_claim": {"count": len(inbound), "first_at": inbound[0]["at"] if inbound else None,
                                              "last_at": inbound[-1]["at"] if inbound else None}}
    return {"owner": ownership_view(c, phone), "thread": thread, "opted_out": marketing_opt_out(c, phone),
            "suppressed": SUP.suppression(c, phone), "this_campaign": this,
            "other_campaigns": [_claim_view(r) for r in ST.campaign_sends_for_phone(c, phone)
                                if r["campaign_id"] != campaign_id]}


def decide(state, retry):
    """-> (action, reason) for a phone with a valid lead, from its latest attempt in this campaign."""
    this, thread = state["this_campaign"], state["thread"] or {}
    if thread.get("is_test"):
        # TASK-109: an operator's own number, marked with app/wa/luna/test_threads.py. Checked before every
        # other action, so no --retry-* path can post a campaign template into a manual test either.
        return "skip_test_number", (f"test number (marked {thread['test_marked_at']}): campaigns never send to it "
                                    f"(python -m app.wa.luna.test_threads --unmark to make it an ordinary thread)")
    undelivered = bool(this) and this["state"] == "sent" and (this.get("delivery") or {}).get("status") == "failed"
    if undelivered:
        # Meta accepted the POST, then reported it undelivered (e.g. 131049 marketing limit, 131026, 131042). Resent in
        # this campaign only with --retry-delivery-failed (Ivan 2026-09-14: e.g. after a 131042 billing fix).
        codes = ", ".join(str(e["code"]) for e in this["delivery"]["errors"]) or "no code"
        failed = (f"attempt {this['attempt']} sent {this['sent_at']} wamid {this['wamid']}, Meta reported it "
                  f"undelivered ({codes})")
        if not retry.delivery_failed:
            return "delivery_failed", f"{failed}; --retry-delivery-failed resends it in this campaign"
    elif this and this["state"] == "sent":
        return "already_sent", f"attempt {this['attempt']} sent {this['sent_at']} wamid {this['wamid']}"
    if this and this["state"] in ("in_progress", "uncertain") and not retry.uncertain:
        return "uncertain", (f"attempt {this['attempt']} {this['state']} since {this['claimed_at']}: it may have gone "
                             f"out; check --status, then --retry-uncertain" + (f" ({this['error']})" if this["error"]
                                                                               else ""))
    if state["suppressed"]:
        # TASK-113: the cross-thread, cross-lane list. Planned as a skip, exactly like the three below it, so no
        # attempt is ever claimed for a number that refused us; send_one's own check is what makes it
        # unbypassable, and that one raises (a suppressed phone that reached the POST is a permanent failure,
        # never an uncertain one).
        s = state["suppressed"]
        return "skip_suppressed", f"suppressed {s['at']} on the {s['lane']} rail ({s['reason']})"
    if thread.get("stopped"):
        return "skip_stopped", f"stopped ({thread['stopped_reason']})"
    if thread.get("declined"):
        return "skip_declined", f"declined {thread['declined_at']}" + (
            f" ({thread['declined_reason']})" if thread["declined_reason"] else "")
    if state["opted_out"]:
        return "skip_opted_out", f"marketing opt-out: {state['opted_out']}"
    went_out = [a for a in this["attempts"] if a["state"] == "sent"] if this else []
    if went_out:
        # a template of this campaign went out to the phone (an undelivered one too): no resend of any kind on top of
        # what the candidate wrote since (TASK-106 AC#3; review 2026-09-15: a rejected --retry-delivery-failed attempt
        # was then planned retry_failed and posted again into Luna's conversation)
        wrote = this["inbound_since_first_claim"]
        if wrote["count"]:
            latest = failed if undelivered else (
                f"attempt {this['attempt']} {this['state']} after attempt {went_out[-1]['attempt']} sent "
                f"{went_out[-1]['sent_at']} wamid {went_out[-1]['wamid']}")
            return "skip_replied", (f"{latest}; the candidate wrote {wrote['count']} message(s) since this campaign "
                                    f"first claimed the phone at {this['first_claimed_at']} (first {wrote['first_at']}, "
                                    f"last {wrote['last_at']}): not resent")
    if undelivered:
        return "retry_delivery_failed", f"--retry-delivery-failed: {failed}"
    if this and this["state"] in ("in_progress", "uncertain"):
        return "retry_uncertain", (f"--retry-uncertain: attempt {this['attempt']} {this['state']} since "
                                   f"{this['claimed_at']}")
    if this and this["state"] == "failed":
        return "retry_failed", f"attempt {this['attempt']} failed: {this['error']}"
    return "send", None


# --- plan -------------------------------------------------------------------------------------------------------

def _import_view(report):
    facts = report.get("facts") or {}
    return {"found": report["found"], "applied": report["applied"], "facts_imported": facts.get("imported"),
            "facts_kept": facts.get("kept"), "facts_conflicting": facts.get("conflicting"),
            "placement_records": len(report.get("placement") or []),
            "placed": any(p.get("placed") for p in report.get("placement") or []),
            "messages": report.get("messages"), "stop_messages": report["stop_messages"],
            "opt_outs": report["opt_outs"], "contact_blocks": IH.contact_blocks(report),
            "decline_import": report["decline_import"],
            "documents": dict(collections.Counter(d["action"] for d in report.get("documents") or [])),
            "not_recoverable": report.get("not_recoverable", 0), "report": report}


def plan(c, campaign_id, leads, retry, source=None):
    """Adds state, action and reason (and the history import preview) to every lead with a valid phone."""
    for lead in leads:
        if lead["phone"] is None:
            continue
        lead["state"] = phone_state(c, campaign_id, lead["phone"])
        if lead["action"] is None:
            lead["action"], lead["reason"] = decide(lead["state"], retry)
        if source is not None and lead["action"] in SENDABLE:
            try:
                lead["history_import"] = _import_view(IH.import_phone(source, lead["phone"], apply=False))
            except IH.SourceAccessError:
                raise
            except Exception as exc:
                lead["history_import"] = {"error": f"{type(exc).__name__}: {exc}"}
                lead["action"], lead["reason"] = "import_error", lead["history_import"]["error"]
                continue
            skip = history_skip(source, lead["history_import"])
            if skip:
                lead["action"], lead["reason"] = skip
    return leads


def history_skip(source, view):
    """-> (action, reason) when the history source recorded that this phone wants no contact (TASK-105), else None:
    skip_opted_out for any opt-out record or Stopp in its chat, skip_declined when every record is a decline."""
    blocks = view["contact_blocks"]
    if not blocks:
        return None
    action = "skip_declined" if all(b["kind"] == "decline" for b in blocks) else "skip_opted_out"
    return action, f"recorded by the history source {source.label}: " + "; ".join(
        f"{b['kind']} {b['at']} {b['reason']} [{b['source_ref']}]" for b in blocks)


def check_template_consistency(c, campaign_id, definition):
    other = sorted({r["template_id"] for r in ST.campaign_attempts(c, campaign_id)} - {str(definition["id"])})
    if other:
        raise CampaignError(f"campaign {campaign_id} was claimed with template id(s) {other}, not "
                            f"{definition['id']}: one campaign id is one template; use a new --campaign-id")


# --- send -------------------------------------------------------------------------------------------------------

def _in_flight_until(c, phone, now):
    """When the phone's oldest in-flight claim stops blocking (claimed_at + STALE_CLAIM_SECONDS), on the run's
    clock: claims carry real time, so the remaining duration is added to ``now``. Only claims ST.claim_in_flight
    counts (younger than STALE_CLAIM_SECONDS): a stale one next to a live one put retry_after in the past and the
    run retried without sleeping (review 2026-09-14)."""
    real_now = _utc_now()
    cutoff = (real_now - timedelta(seconds=ST.STALE_CLAIM_SECONDS)).replace(microsecond=0).isoformat()
    row = c.execute("select min(claimed_at) as at from wa_reply_turn_claims where phone=? and state='in_progress' "
                    "and claimed_at>?", (phone, cutoff)).fetchone()
    at = datetime.fromisoformat(row["at"]) if row and row["at"] else real_now
    return now + (at + timedelta(seconds=ST.STALE_CLAIM_SECONDS) - real_now)


def _meta_error_parts(exc):
    payload = getattr(exc, "payload", None)
    err = (payload or {}).get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code, payload


def send_one(campaign_id, definition, lead, client, clock, retry, window, source=None):
    """Import (optional), claim + flip, POST, record. -> result {status, ...}; status one of the decide() skip
    actions, deferred (a turn is in flight: retry_after; or reason outside_window: the window closed while the
    history import ran, retry_after = the next window start), import_error, sent, failed, uncertain. The window is
    checked again inside the claim transaction: an import with document reads can take minutes (review
    2026-09-14: a template went out at 21:03 Berlin). Raises on a database failure and lets KeyboardInterrupt
    through (the claim then stays in_progress = uncertain)."""
    phone = lead["phone"]
    with live_db() as c:
        action, reason = decide(phone_state(c, campaign_id, phone), retry)
        if action not in SENDABLE:
            return {"status": action, "reason": reason}
        if ST.claim_in_flight(c, phone):
            return {"status": "deferred", "retry_after": _in_flight_until(c, phone, clock())}
    result = {}
    if source is not None:
        try:
            result["history_import"] = _import_view(IH.import_phone(source, phone, apply=True, client=client))
        except IH.SourceAccessError:
            raise
        except Exception as exc:
            return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        skip = history_skip(source, result["history_import"])
        if skip:
            return {**result, "status": skip[0], "reason": skip[1]}

    with ST._lock, live_db() as c:
        c.commit()
        c.execute("begin immediate")
        try:
            now = clock()
            if not window.is_open(now):
                c.rollback()
                return {**result, "status": "deferred", "reason": "outside_window", "retry_after": window.next_open(now)}
            claimed_at = _iso(now)
            state = phone_state(c, campaign_id, phone)
            action, reason = decide(state, retry)
            if action not in SENDABLE:
                c.rollback()
                return {**result, "status": action, "reason": reason}
            if ST.claim_in_flight(c, phone):
                until = _in_flight_until(c, phone, clock())
                c.rollback()
                return {**result, "status": "deferred", "retry_after": until}
            prior = R.flip_to_us_for_campaign(c, phone, campaign_id, claimed_at)
            this = state["this_campaign"]
            if prior and prior["owner"] == "us" and prior["reason"] == R.campaign_reason(campaign_id) and this:
                # our own earlier flip is still in place (retry of an uncertain or undelivered attempt): keep the
                # owner the previous attempt recorded before it, so a rejected retry restores the pre-campaign owner
                prior = ({"owner": this["prior_owner"], "reason": this["prior_reason"], "since": this["prior_since"]}
                         if this["prior_owner"] else None)
            attempt = ST.claim_campaign_send(c, campaign_id, phone, definition, lead["params"],
                                             lead["rendered"]["text"], prior, claimed_at,
                                             retry_uncertain=retry.uncertain,
                                             retry_delivery_failed=retry.delivery_failed)
            c.commit()
        except BaseException:
            c.rollback()
            raise
    result.update(attempt=attempt, claimed_at=claimed_at, prior_owner=prior)

    try:
        # TASK-113: the campaign's suppression choke point -- the one send path that never touches
        # api.send_and_record. decide() already planned a suppressed phone as skip_suppressed, so reaching
        # this means the number refused us between the plan and the POST (a Stopp while the run was pacing,
        # or during a history import that took minutes). Inside this try on purpose: SuppressedRecipient is a
        # 4xx MetaError, so the classification below records the attempt failed (permanent, ownership
        # restored) rather than uncertain, which --retry-uncertain would post again.
        with live_db() as c:
            SUP.assert_not_suppressed(c, phone)
        wamid = client.send_template(phone, definition=definition, params=lead["params"])
    except M.MetaError as exc:
        status_code = exc.status_code
        rejected = isinstance(exc, M.TemplateParamsError) or (status_code is not None and 400 <= status_code < 500)
        return {**result, **_record_unsent(campaign_id, definition, phone, attempt, prior, clock(), exc,
                                           "failed" if rejected else "uncertain")}
    except Exception as exc:
        return {**result, **_record_unsent(campaign_id, definition, phone, attempt, prior, clock(), exc, "uncertain")}

    sent_at = _iso(clock())
    with ST._lock, live_db() as c:
        c.commit()
        c.execute("begin immediate")
        try:
            ST.record_campaign_send(c, phone, wamid, lead["rendered"], campaign_id, sent_at=sent_at,
                                    template_id=str(definition["id"]), variables=lead["params"], commit=False)
            ST.finish_campaign_send(c, campaign_id, phone, attempt, "sent", sent_at, wamid=wamid)
            c.commit()
        except BaseException:
            c.rollback()
            print(f"CRITICAL: {phone} template accepted by Meta as {wamid} but not recorded; attempt {attempt} "
                  f"stays in_progress (uncertain)", file=sys.stderr, flush=True)
            raise
    return {**result, "status": "sent", "wamid": wamid, "sent_at": sent_at}


def _record_unsent(campaign_id, definition, phone, attempt, prior, now, exc, state):
    code, payload = _meta_error_parts(exc)
    http = getattr(exc, "status_code", None)
    error = (f"campaign {campaign_id} template {definition['name']} "
             f"{'rejected' if state == 'failed' else 'uncertain'}"
             f"{f' (HTTP {http})' if http is not None else ''}: {type(exc).__name__}: {exc}"
             + (f" {json.dumps(payload, ensure_ascii=False)}" if payload else ""))
    at = _iso(now)
    with ST._lock, live_db() as c:
        c.commit()
        c.execute("begin immediate")
        try:
            restore = R.restore_after_campaign_failure(c, phone, campaign_id, prior) if state == "failed" else None
            ST.finish_campaign_send(c, campaign_id, phone, attempt, state, at, error=error, error_code=code,
                                    error_payload=payload, ownership_restore=restore)
            c.execute("insert into wa_send_failures (phone, error, at) values (?,?,?)", (phone, error, ST.now_iso()))
            c.commit()
        except BaseException:
            c.rollback()
            raise
    return {"status": state, "error": error, "error_code": code, "http_status": http, "ownership_restore": restore}


def run_send(campaign_id, definition, leads, client, window, batch_size, interval, retry, no_wait,
             clock, sleep, source=None, on_progress=None):
    """Sends every sendable lead with pacing and window. -> {finished, stopped, results}; each lead gets ``result``."""
    queue = [lead for lead in leads if lead["action"] in SENDABLE]
    out = {"finished": False, "stopped": None}
    while queue:
        now = clock()
        ready = [lead for lead in queue if not lead.get("retry_after") or lead["retry_after"] <= now]
        if not ready:
            until = min(lead["retry_after"] for lead in queue)
            if _wait(until, now, "every remaining phone has a turn in flight", "turn_in_flight", no_wait, sleep, out):
                continue
            return out
        if not window.is_open(now):
            until = window.next_open(now)
            if _wait(until, now, f"outside the window {window.describe()}", "outside_window", no_wait, sleep, out):
                continue
            return out
        with live_db() as c:
            until = next_claim_at(c, campaign_id, now, batch_size, interval)
        if until is not None:
            if _wait(until, now, f"batch of {batch_size} per {interval} reached", "batch_interval", no_wait, sleep,
                     out):
                continue
            return out
        lead = ready[0]
        result = send_one(campaign_id, definition, lead, client, clock, retry, window, source=source)
        if result["status"] == "deferred" and result.get("reason") == "outside_window":
            _say(f"  {lead['phone']}: the window {window.describe()} closed before the claim, next start "
                 f"{_iso(result['retry_after'])}")
            continue   # the loop top waits for the window, or stops with --no-wait
        if result["status"] == "deferred":
            lead["retry_after"] = result["retry_after"]
            _say(f"  {lead['phone']}: a turn is in flight, retried after {_iso(result['retry_after'])}")
            continue
        queue.remove(lead)
        lead.pop("retry_after", None)
        lead["result"] = result
        at = result.get("sent_at") or result.get("claimed_at") or _iso(now)   # the claim/send time, not the loop top
        _say(f"  {at} {lead['phone']} (line {lead['line']}): {result['status']}"
             + (f" {result['wamid']}" if result.get("wamid") else "")
             + (f" -- {result.get('error') or result.get('reason')}" if result.get("error") or result.get("reason")
                else ""))
        if on_progress:
            on_progress()
    out["finished"] = True
    return out


def _wait(until, now, why, stop_reason, no_wait, sleep, out):
    """-> True after sleeping until ``until``; False (and ``out['stopped']`` set) with --no-wait."""
    if no_wait:
        out["stopped"] = {"reason": stop_reason, "next_at": _iso(until), "detail": why}
        _say(f"{why}; --no-wait: stopping, run again after {_iso(until)}")
        return False
    _say(f"{why}; waiting until {_iso(until)}")
    sleep(max(0.0, (until - now).total_seconds()))
    return True


# --- status -----------------------------------------------------------------------------------------------------

def _ts_iso(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat() if str(ts or "").isdigit() else ts


def status_category(status):
    """Meta's pricing/conversation category of a stored status (marketing, utility, service, ...), or None."""
    return (status["pricing"] or {}).get("category") or ((status["conversation"] or {}).get("origin") or {}).get("type")


def mark_sent(c, campaign_id, definition, phone, wamid):
    """--mark-sent: record an in_progress/uncertain claim whose template did go out, from a stored status webhook.
    Checks: the claim exists, is in_progress/uncertain, is no longer in flight (its 'campaign:<id>' claim stale) and
    used this template; ``wamid`` is no stored message; a status of ``wamid`` for this phone arrived since the claim;
    no such status names a category other than the template's (an old-system receipt is not our template); the
    template renders to the claim's text with the claim's values. Then, in the caller's transaction:
    store.record_campaign_send (thread, outbound row, card.campaign) and the claim sent with sent_at = the earliest
    status timestamp. -> {phone, wamid, status: marked_sent, sent_at}; raises CampaignError naming what is missing
    (review 2026-09-14: without this a template that went out stayed uncertain and Luna answered the reply as first
    contact)."""
    claim = ST.campaign_send(c, campaign_id, phone)
    if claim is None or claim["state"] not in ("in_progress", "uncertain"):
        raise CampaignError(f"{phone}: no in_progress/uncertain claim in campaign {campaign_id} "
                            f"({claim['state'] if claim else 'no claim'})")
    if str(claim["template_id"]) != str(definition["id"]):
        raise CampaignError(f"{phone}: claimed with template {claim['template_id']}, not {definition['id']}")
    turn = c.execute("select state, claimed_at from wa_reply_turn_claims where phone=? and turn_key=?",
                     (phone, ST.CAMPAIGN_CLAIM_PREFIX + campaign_id)).fetchone()
    if turn and turn["state"] == "in_progress" and \
            _utc_now() - datetime.fromisoformat(turn["claimed_at"]) < timedelta(seconds=ST.STALE_CLAIM_SECONDS):
        raise CampaignError(f"{phone}: the send claimed at {turn['claimed_at']} may still be running; try again once "
                            f"it is {ST.STALE_CLAIM_SECONDS} s old")
    if ST.message_by_wamid(c, wamid) is not None:
        raise CampaignError(f"{phone}: {wamid} is already a stored message")
    statuses = [s for s in ST.message_statuses_for(c, phone) if s["wamid"] == wamid and s["received_at"] >= claim["claimed_at"]]
    if not statuses:
        raise CampaignError(f"{phone}: no status of {wamid} for this phone since the claim at {claim['claimed_at']}")
    expected = str(definition.get("category") or "").lower()
    other = sorted({status_category(s) for s in statuses} - {None, expected})
    if other:
        raise CampaignError(f"{phone}: statuses of {wamid} name category {other}, the template is {expected!r}: not "
                            f"this template")
    rendered = M.render_template(definition, claim["variables"])
    if rendered["text"] != claim["rendered_text"]:
        raise CampaignError(f"{phone}: template {definition['id']} no longer renders the claimed text")
    sent_at = _iso(datetime.fromtimestamp(min(int(s["timestamp"]) for s in statuses), tz=timezone.utc))
    ST.record_campaign_send(c, phone, wamid, rendered, campaign_id, sent_at=sent_at, template_id=str(definition["id"]),
                            variables=claim["variables"], commit=False)
    ST.reconcile_campaign_send(c, campaign_id, phone, claim["attempt"], wamid, sent_at)
    return {"phone": phone, "wamid": wamid, "status": "marked_sent", "attempt": claim["attempt"], "sent_at": sent_at}


def run_mark_sent(campaign_id, definition, pairs):
    """Each PHONE=WAMID in its own transaction. -> results (marked_sent, or refused with the reason)."""
    results = []
    for pair in pairs:
        raw_phone, _, wamid = pair.partition("=")
        phone, wamid = canonical_phone(raw_phone.strip()), wamid.strip()
        if phone is None or not wamid:
            results.append({"pair": pair, "status": "refused", "reason": "expected PHONE=WAMID"})
            continue
        with ST._lock, live_db() as c:
            c.commit()
            c.execute("begin immediate")
            try:
                results.append(mark_sent(c, campaign_id, definition, phone, wamid))
                c.commit()
            except CampaignError as exc:
                c.rollback()
                results.append({"phone": phone, "wamid": wamid, "status": "refused", "reason": str(exc)})
            except BaseException:
                c.rollback()
                raise
    return results


def match_replies(attempts, inbound):
    """The candidate's messages that answer this campaign, each matched to the attempt it answers (TASK-106): the
    attempt whose wamid its context names (matched_by context), else the latest attempt that may have reached the
    candidate at or before it (reach_anchor; matched_by time). A message before every such attempt, without context
    naming one, answers none (status_rows: not_answering_an_attempt). -> [{message, attempt, matched_by}] in message
    order."""
    by_wamid = {a["wamid"]: a["attempt"] for a in attempts if a["wamid"]}
    anchors = sorted((reach_anchor(a), a["attempt"]) for a in attempts if reach_anchor(a))
    matched = []
    for m in inbound:
        target = m["meta"].get("reply_to_wamid")
        if target in by_wamid:
            matched.append({"message": m, "attempt": by_wamid[target], "matched_by": "context"})
            continue
        before = [n for at, n in anchors if at <= m["at"]]
        if before:
            matched.append({"message": m, "attempt": before[-1], "matched_by": "time"})
    return matched


def _unlinked_statuses(c, phone, attempt):
    """For an attempt whose POST result is unknown: the statuses of this phone received since its claim whose wamid
    is no stored message (evidence it went out when the category is the template's, --mark-sent)."""
    if attempt["state"] not in ("in_progress", "uncertain"):
        return []
    return [{"wamid": s["wamid"], "status": s["status"], "at": _ts_iso(s["timestamp"]), "category": status_category(s)}
            for s in ST.message_statuses_for(c, phone)
            if s["received_at"] >= attempt["claimed_at"] and ST.message_by_wamid(c, s["wamid"]) is None]


def status_rows(c, campaign_id):
    """One row per claimed phone: its latest attempt's fields, every attempt (``attempts``) and the replies matched
    to the attempt they answer."""
    rows = []
    for r in ST.campaign_sends(c, campaign_id):
        phone = r["phone"]
        state = phone_state(c, campaign_id, phone)
        thread, this = state["thread"] or {}, state["this_campaign"]
        inbound = ST.messages_for(c, phone, direction="in")
        matched = match_replies(this["attempts"], inbound)
        matched_ids = {x["message"]["id"] for x in matched}
        # written since the campaign first claimed the phone, but before any attempt could have reached them (after a
        # rejected or undelivered one): not a reply to the campaign
        unanswered = [m for m in inbound if m["at"] >= this["first_claimed_at"] and m["id"] not in matched_ids]
        attempts = []
        for a in this["attempts"]:
            mine = [x for x in matched if x["attempt"] == a["attempt"]]
            attempts.append({**a, "statuses_since_claim_without_message": _unlinked_statuses(c, phone, a),
                             "replies": {"count": len(mine),
                                         "by_context": sum(x["matched_by"] == "context" for x in mine)}})
        latest = attempts[-1]
        replies = [x["message"] for x in matched]
        anchors = [reach_anchor(a) for a in attempts if reach_anchor(a)]
        rows.append({
            "phone": phone, **_claim_view(r), "delivery": latest["delivery"],
            "statuses_since_claim_without_message": latest["statuses_since_claim_without_message"],
            "attempts": attempts,
            "replies": {"count": len(replies), "first_at": replies[0]["at"] if replies else None,
                        "last_at": replies[-1]["at"] if replies else None,
                        "last_text": replies[-1]["body"] if replies else None,
                        "button_taps": [{"text": x["message"]["body"], "button_id": x["message"]["meta"].get("button_id"),
                                         "at": x["message"]["at"], "attempt": x["attempt"]}
                                        for x in matched if x["message"]["kind"] == "button"],
                        "to_the_template": any(x["matched_by"] == "context" for x in matched),
                        "matched": [{"wamid": x["message"]["wamid"], "at": x["message"]["at"],
                                     "kind": x["message"]["kind"], "attempt": x["attempt"],
                                     "matched_by": x["matched_by"]} for x in matched]},
            "not_answering_an_attempt": {"count": len(unanswered),
                                         "first_at": unanswered[0]["at"] if unanswered else None,
                                         "last_at": unanswered[-1]["at"] if unanswered else None},
            "unread_media": [u for u in thread.get("unread_media", []) if anchors and u["received_at"] >= min(anchors)],
            "stage": thread.get("stage"), "declined": thread.get("declined", False),
            "stopped": thread.get("stopped", False), "opted_out": state["opted_out"], "owner": state["owner"]})
    return rows


def status_totals(rows):
    """Per phone (its latest attempt), except ``attempts`` and ``error_codes``, which count every attempt."""
    codes = collections.Counter()
    for r in rows:
        for a in r["attempts"]:
            if a["error_code"]:
                codes[f"send {a['error_code']}"] += 1
            for e in (a["delivery"] or {}).get("errors") or []:
                codes[f"delivery {e['code']}"] += 1
    return {"phones": len(rows), "attempts": sum(len(r["attempts"]) for r in rows),
            "retried": sum(len(r["attempts"]) > 1 for r in rows),
            "state": dict(collections.Counter(r["state"] for r in rows)),
            "delivery": dict(collections.Counter((r["delivery"] or {}).get("status") or "none"
                                                 for r in rows if r["state"] == "sent")),
            "replied": sum(r["replies"]["count"] > 0 for r in rows),
            "wrote_not_answering_an_attempt": sum(r["not_answering_an_attempt"]["count"] > 0 for r in rows),
            "declined": sum(bool(r["declined"]) for r in rows),
            "unread_media": sum(bool(r["unread_media"]) for r in rows),
            "stopped": sum(bool(r["stopped"]) for r in rows), "opted_out": sum(bool(r["opted_out"]) for r in rows),
            "error_codes": dict(codes)}


def _attempt_text(a):
    d = a["delivery"] or {}
    errs = ",".join(str(e["code"]) for e in d.get("errors") or [])
    return (f"{a['state']}" + (f" {a['wamid']}" if a["wamid"] else "")
            + (f" delivery {d['status']} {d['at']}" + (f" errors {errs}" if errs else "") if d else "")
            + (f" send error {a['error_code']}" if a["error_code"] else "")
            + (f" statuses since claim without message: {len(a['statuses_since_claim_without_message'])}"
               if a["statuses_since_claim_without_message"] else ""))


def _print_status(campaign_id, rows, totals):
    _say(f"campaign {campaign_id}: {totals['phones']} phone(s) claimed, {totals['attempts']} attempt(s)")
    for r in rows:
        latest = r["attempts"][-1]
        _say(f"  {r['phone']}: attempt {latest['attempt']} {_attempt_text(latest)}"
             + f" replies {r['replies']['count']}"
             + (f" wrote {r['not_answering_an_attempt']['count']} not answering an attempt"
                if r["not_answering_an_attempt"]["count"] else "")
             + (" declined" if r["declined"] else "")
             + (f" unread media {len(r['unread_media'])} (a colleague must look)" if r["unread_media"] else "")
             + (" stopped" if r["stopped"] else "") + (f" stage {r['stage']}" if r["stage"] else ""))
        for a in r["attempts"]:
            _say(f"    attempt {a['attempt']} claimed {a['claimed_at']}: {_attempt_text(a)}"
                 + f"; replies {a['replies']['count']}"
                 + (f" ({a['replies']['by_context']} quoting it)" if a["replies"]["by_context"] else ""))
    _say(f"totals: {json.dumps(totals, ensure_ascii=False)}")


# --- report -----------------------------------------------------------------------------------------------------

def write_report(path, report):
    """Atomic JSON write, directory 0700, file 0600 (phone numbers, names)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def _lead_view(lead):
    view = {k: lead.get(k) for k in ("line", "raw_phone", "phone", "phone_rewritten", "action", "reason", "problems",
                                     "variables", "params")}
    view["rendered_text"] = lead["rendered"]["text"] if lead.get("rendered") else None
    for key in ("state", "history_import", "result"):
        if key in lead:
            view[key] = lead[key]
    return view


def _print_plan(leads):
    for lead in leads:
        state = lead.get("state") or {}
        owner = state.get("owner") or {}
        thread = state.get("thread")
        owner_text = (f"{owner['owner']} ({owner['reason']})" if owner.get("recorded") else
                      f"none, would route to {owner.get('would_route_to')}" if owner else "-")
        _say(f"line {lead['line']} {lead['phone'] or lead['raw_phone']!s}: {lead['action']}"
             + (f" -- {lead['reason']}" if lead["reason"] else ""))
        if lead["phone"] is None:
            continue
        if lead["phone_rewritten"]:
            _say(f"    phone rewritten from {lead['raw_phone']!r}")
        _say(f"    owner {owner_text}" + (f" [{owner['known_phones_error']}]" if owner.get("known_phones_error") else "")
             + (f"; thread {thread['stage']}/{thread['ball']}" + (" stopped" if thread["stopped"] else "")
                + (" declined" if thread["declined"] else "") + (" test number" if thread["is_test"] else "")
                if thread else "; no thread")
             + (f"; this campaign attempt {state['this_campaign']['attempt']} {state['this_campaign']['state']}"
                if state.get("this_campaign") else "")
             + (f"; other campaigns {[o['campaign_id'] + ':' + o['state'] for o in state['other_campaigns']]}"
                if state.get("other_campaigns") else ""))
        imp = lead.get("history_import")
        if imp:
            _say("    history import " + (imp["error"] if "error" in imp else
                                          f"found={imp['found']} facts={imp['facts_imported']} "
                                          f"documents={imp['documents']} not_recoverable={imp['not_recoverable']}"))
            decision = imp.get("decline_import")
            if decision and (decision["marked"] or decision["not_marked"]):
                _say("    card " + (f"declined by --send's import: {decision['declined_reason']}" if decision["marked"]
                                    else f"not declined by the import: {decision['not_marked']}"))
        if lead.get("rendered"):
            _say("    body: " + (lead["rendered"]["body"] or "").replace("\n", " / "))


def _load_params(value):
    if not value:
        return {}
    text = pathlib.Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
    params = json.loads(text)
    if not isinstance(params, dict):
        raise CampaignError("--params must be a JSON object")
    return params


def main(argv=None, client=None, clock=None, sleep=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign-id", required=True, help="slug; one campaign id is one template")
    ap.add_argument("--template-id", help="Meta template id (must be APPROVED)")
    ap.add_argument("--leads", help="lead file, .csv or .json")
    ap.add_argument("--params", help="JSON object (or @file) of values for every lead, e.g. quick-reply payloads")
    ap.add_argument("--send", action="store_true", help="send; without it nothing is written or sent")
    ap.add_argument("--retry-uncertain", action="store_true", help="also claim uncertain/in_progress phones again")
    ap.add_argument("--retry-delivery-failed", action="store_true",
                    help="resend, as the next attempt in this campaign, phones whose sent template Meta reported "
                         "undelivered (not to phones stopped, declined, opted out or that wrote since)")
    ap.add_argument("--no-wait", action="store_true", help="exit 3 instead of waiting for the window or interval")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--batch-interval-min", type=float, default=DEFAULT_BATCH_INTERVAL_MIN)
    ap.add_argument("--window", default=DEFAULT_WINDOW, help="local send hours HH-HH")
    ap.add_argument("--tz", default=DEFAULT_TZ)
    ap.add_argument("--report", help="JSON report path (default ~/pflege-campaign-reports/...)")
    ap.add_argument("--status", action="store_true", help="delivery statuses and replies of a campaign")
    ap.add_argument("--mark-sent", action="append", default=[], metavar="PHONE=WAMID",
                    help="record an uncertain/in_progress claim as sent from its status webhook (needs --template-id)")
    ap.add_argument("--import-history-db")
    ap.add_argument("--import-history-queries")
    ap.add_argument("--import-history-source")
    ap.add_argument("--import-history-media-root", action="append", default=[])
    ap.add_argument("--override-no-history-source", action="store_true",
                    help="dry-run/send without the history source: the old system's opt-out and decline records "
                         "and chat Stopps are not checked (printed and recorded in the report)")
    args = ap.parse_args(argv)
    clock, sleep = clock or _utc_now, sleep or time.sleep
    started = clock()
    mode = "status" if args.status else "mark-sent" if args.mark_sent else "send" if args.send else "dry-run"
    report_path = pathlib.Path(args.report) if args.report else \
        DEFAULT_REPORT_DIR / f"{args.campaign_id}-{mode}-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    report = {"campaign_id": args.campaign_id, "mode": mode, "generated_at": _iso(started)}

    def fail(message, code=EXIT_CONFIG):
        print(f"ERROR: {message}", file=sys.stderr, flush=True)
        report["error"] = message
        write_report(report_path, report)
        return code

    if not _SLUG.match(args.campaign_id):
        return fail(f"--campaign-id {args.campaign_id!r}: letters, digits, '.', '_', '-' only")
    if args.status:
        with snapshot_db() as c:
            rows = status_rows(c, args.campaign_id)
        report.update(phones=rows, totals=status_totals(rows))
        _print_status(args.campaign_id, rows, report["totals"])
        write_report(report_path, report)
        _say(f"report: {report_path}")
        return EXIT_OK

    if args.mark_sent:
        if not args.template_id or args.leads or args.send:
            return fail("--mark-sent needs --template-id and goes without --leads/--send")
        client = T.get_client(client=client)
        try:
            definition = client.get_template(args.template_id, require_approved=True)
        except M.MetaError as exc:
            return fail(f"template {args.template_id}: {exc}")
        results = run_mark_sent(args.campaign_id, definition, args.mark_sent)
        for r in results:
            _say(f"  {r.get('phone') or r.get('pair')}: {r['status']}" + (f" {r['wamid']} sent_at {r['sent_at']}"
                 if r["status"] == "marked_sent" else f" -- {r['reason']}"))
        report.update(template_id=str(definition["id"]), results=results)
        write_report(report_path, report)
        _say(f"report: {report_path}")
        return EXIT_ATTENTION if any(r["status"] == "refused" for r in results) else EXIT_OK

    history = [args.import_history_db, args.import_history_queries, args.import_history_source]
    if not args.template_id or not args.leads:
        return fail("--template-id and --leads are required (or --status)")
    if any(history) and not all(history):
        return fail("--import-history-db, --import-history-queries and --import-history-source go together")
    if args.import_history_media_root and not args.import_history_db:
        return fail("--import-history-media-root needs --import-history-db")
    if args.override_no_history_source and args.import_history_db:
        return fail("--override-no-history-source goes without --import-history-*")
    if not args.import_history_db and not args.override_no_history_source:
        return fail("every dry-run and send checks the old system's opt-out/decline records and chat Stopps: give "
                    "--import-history-db, --import-history-queries and --import-history-source, or "
                    "--override-no-history-source to run without them (recorded in the report)")
    if args.override_no_history_source:
        report["history_source"] = {"override": True, "detail": OVERRIDE_DETAIL}
        print(f"WARNING: --override-no-history-source: {OVERRIDE_DETAIL}", file=sys.stderr, flush=True)
        _say(f"WARNING: --override-no-history-source: {OVERRIDE_DETAIL}")
    else:
        report["history_source"] = {"override": False, "label": args.import_history_source,
                                    "db": args.import_history_db, "queries": args.import_history_queries}
    if args.batch_size < 1 or args.batch_interval_min <= 0:
        return fail("--batch-size must be >= 1 and --batch-interval-min > 0")
    try:
        window = Window(args.window, args.tz)
        common = _load_params(args.params)
        rows, lead_info = read_leads(args.leads)
    except (ValueError, CampaignError, OSError) as exc:
        return fail(str(exc))
    if args.send and not C.AUTOSEND:
        return fail("--send needs WA_AUTOSEND=1 (without it nothing may reach Meta); load .env first")
    client = T.get_client(client=client)
    if args.send and (not client.access_token or not client.phone_number_id):
        return fail("--send needs META_WHATSAPP_ACCESS_TOKEN and META_WHATSAPP_PHONE_NUMBER_ID")
    try:
        definition = client.get_template(args.template_id, require_approved=True)
    except M.MetaError as exc:
        return fail(f"template {args.template_id}: {exc} {json.dumps(exc.payload, ensure_ascii=False) if exc.payload else ''}")
    if str(definition.get("id")) != str(args.template_id):
        return fail(f"Meta returned template id {definition.get('id')!r} for {args.template_id!r}")
    interval = timedelta(minutes=args.batch_interval_min)
    retry = Retry(uncertain=args.retry_uncertain, delivery_failed=args.retry_delivery_failed)
    report.update(
        template={k: definition.get(k) for k in ("id", "name", "language", "status", "category", "parameter_format")},
        settings={"batch_size": args.batch_size, "batch_interval_min": args.batch_interval_min,
                  "window": window.describe(), "retry_uncertain": args.retry_uncertain,
                  "retry_delivery_failed": args.retry_delivery_failed, "no_wait": args.no_wait,
                  "params": common, "import_history": {"db": args.import_history_db, "source": args.import_history_source,
                                                       "media_roots": args.import_history_media_root}
                  if args.import_history_db else None},
        lead_file=lead_info)
    leads = resolve_leads(rows, definition, common)
    _say(f"campaign {args.campaign_id} [{mode}] template {definition['name']} ({definition['language']}, id "
         f"{definition['id']}, {definition['status']}); {len(leads)} lead(s) from {lead_info['path']}; window "
         f"{window.describe()}; {args.batch_size} per {args.batch_interval_min:g} min")
    if lead_info["ignored_columns"]:
        _say(f"ignored columns (not template values): {lead_info['ignored_columns']}")
    example = next((lead for lead in leads if lead.get("rendered")), None)
    if example:
        _say(f"rendered (line {example['line']}):\n" + "\n".join("  | " + s for s in example["rendered"]["text"].split("\n")))

    source = None
    exit_code = EXIT_OK
    try:
        if args.import_history_db:
            source = IH.Source.open(args.import_history_db, args.import_history_queries, args.import_history_source,
                                    args.import_history_media_root)
        with (live_db() if args.send else snapshot_db()) as c:
            check_template_consistency(c, args.campaign_id, definition)
            plan(c, args.campaign_id, leads, retry, source=None if args.send else source)
        _print_plan(leads)
        report["plan_totals"] = dict(collections.Counter(lead["action"] for lead in leads))
        _say(f"plan: {json.dumps(report['plan_totals'])}")
        report["phones"] = [_lead_view(lead) for lead in leads]
        write_report(report_path, report)
        if any(lead["action"] in PROBLEM_ACTIONS for lead in leads):
            exit_code = EXIT_ATTENTION
        if args.send:
            def progress():
                report["phones"] = [_lead_view(lead) for lead in leads]
                write_report(report_path, report)

            outcome = run_send(args.campaign_id, definition, leads, client, window, args.batch_size, interval,
                               retry, args.no_wait, clock, sleep, source=source, on_progress=progress)
            report.update(finished=outcome["finished"], stopped=outcome["stopped"],
                          send_totals=dict(collections.Counter(lead["result"]["status"] for lead in leads
                                                               if "result" in lead)))
            _say(f"send: {json.dumps(report['send_totals'])}" + ("" if outcome["finished"] else " (not finished)"))
            if any(lead.get("result", {}).get("status") in PROBLEM_RESULTS for lead in leads):
                exit_code = EXIT_ATTENTION
            elif not outcome["finished"]:
                # Problem leads of the plan are printed and reported on every run; they give exit 1 once the list is
                # done. Before that, 3 (repair 2026-09-14: one invalid lead or delivery_failed turned every
                # --no-wait stop into 1, so a run could not tell whether the list was done).
                exit_code = EXIT_NOT_FINISHED
    except (IH.SourceAccessError, CampaignError) as exc:
        report["phones"] = [_lead_view(lead) for lead in leads]
        return fail(str(exc))
    except KeyboardInterrupt:
        report["phones"] = [_lead_view(lead) for lead in leads]
        report["interrupted"] = True
        write_report(report_path, report)
        print("interrupted: a claim in progress stays in_progress and is reported as uncertain on the next run",
              file=sys.stderr, flush=True)
        return EXIT_INTERRUPTED
    except BaseException as exc:
        report["phones"] = [_lead_view(lead) for lead in leads]
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        write_report(report_path, report)
        raise
    finally:
        if source is not None:
            source.close()
    report["phones"] = [_lead_view(lead) for lead in leads]
    write_report(report_path, report)
    _say(f"report: {report_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
