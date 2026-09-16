"""Campaign sender (TASK-103): we send one approved Meta template to a lead list ourselves, Luna answers the replies.

Usage:
    python -m app.wa.luna.campaign --campaign-id SLUG --template-id ID --leads FILE.csv|FILE.json
        [--params JSON|@FILE] [--send [--retry-uncertain]] [--no-wait]
        [--batch-size 10] [--batch-interval-min 12] [--window 09-21] [--tz Europe/Berlin] [--report PATH]
        [--import-history-db DB --import-history-queries SQL --import-history-source LABEL
         [--import-history-media-root DIR ...]]
    python -m app.wa.luna.campaign --campaign-id SLUG --status [--report PATH]
    python -m app.wa.luna.campaign --campaign-id SLUG --template-id ID --mark-sent PHONE=WAMID [...]

Load .env first (set -a; . ./.env; set +a): Meta token and phone number id, WA_AUTOSEND, data paths.

LEAD FILE. CSV with a header row, or a JSON list of objects. ``phone`` is required per lead. Template values use
the keys of app/wa/meta.py (comment above TEMPLATE_FIELDS) as dotted paths: ``body.1`` ({{1}}), ``header.1``,
``buttons.0.payload``, ``body.first_name`` (NAMED); JSON may nest them instead ({"body": {"1": "Frau X"}}). An empty
value counts as not given. Other columns are listed as ignored, their values are not used. ``--params`` gives values
for every lead (e.g. quick-reply payloads); a key set both there and in the lead file fails that lead. No value is
ever invented: a missing, extra or invalid variable fails that lead in the plan (meta.validate_template_params).

DRY-RUN (default). Resolves the template by id (GET, must be APPROVED), canonicalizes phones (unparseable,
duplicate and conflicting duplicate leads reported, none dropped silently), validates and renders each lead, and
plans each phone from a read-only in-memory copy of data/wa.sqlite: owner (wa_ownership, else the
WA_REAL_SYSTEM_PHONES_FILE check), thread stage/ball, stopped, declined, marketing opt-out (user_preferences stop,
failed status 131050), this and other campaigns' claims, history import preview (import_history dry-run). Writes
no database row, sends nothing; only the report file.

--send (needs WA_AUTOSEND=1). Per phone, in lead-file order:
1. history import --apply when --import-history-* is given (before the claim: its card merge refuses while any
   claim of the phone is in flight); a Stopp in the imported history skips the phone (skip_opted_out);
2. one transaction: the window re-checked (the import can take minutes; closed -> the phone waits for the next start),
   eligibility re-checked, no turn in flight, claim wa_campaign_sends
   in_progress (+ nudge claim 'campaign:<id>:<attempt>', reply-turn claim 'campaign:<id>' so the webhook worker
   waits), prior owner recorded,
   ownership flipped to us (routing.flip_to_us_for_campaign, reason 'campaign:<id>');
3. POST the template (definition + params);
4. wamid -> one transaction: thread, outbound row kind=template with the rendered text and meta, last_outbound_at,
   card.campaign (store.record_campaign_send), claim sent. HTTP 4xx -> claim failed with code/payload,
   wa_send_failures, ownership restored. Network error, timeout, HTTP 5xx, 2xx without wamid -> claim uncertain,
   wa_send_failures, ownership stays with us. A crash after step 2 leaves in_progress, reported as uncertain.
Re-running continues: sent is skipped, failed is claimed again, uncertain/in_progress is never resent without
--retry-uncertain (check --status first); one that did go out (a status for a wamid we never recorded, with the
template's category) is recorded with --mark-sent PHONE=WAMID (mark_sent). A sent template Meta later reported
failed is planned delivery_failed, never resent under the same campaign id. Stopped, declined and opted-out phones are
never sent.

PACING. At most --batch-size claims of this campaign within any --batch-interval-min (counted from claimed_at in
the database, so a restart keeps the pace), only inside --window local hours of --tz. Outside the window the run
waits for the next start, between batches it waits for the interval (both logged); --no-wait exits 3 instead, a
later run continues. Defaults are Ivan's values (2026-09-14): about 10 every 10-15 minutes, 09:00-21:00
Europe/Berlin.

--status. Read-only: per phone claim state, attempts, wamid, sync error, latest delivery status and its errors
(wa_message_statuses), statuses seen since an uncertain claim, replies since the send (count, button taps, replies
to the template), stage, declined, owner; totals.

REPORT. stdout plus a JSON file (--report, default ~/pflege-campaign-reports/<campaign>-<mode>-<UTC>.json, 0600;
it holds phone numbers and names), rewritten after every send.

EXIT. 0 done; 1 a send of this run needs attention (failed, uncertain, import error), or a dry-run or finished
send run whose plan holds a lead needing attention (invalid lead, variables, duplicate conflict, uncertain, delivery
failed); 2 configuration or access error (template not approved, token, AUTOSEND, source access); 3 --no-wait stopped
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
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config as C
from .. import meta as M
from .. import routing as R
from .. import store as ST
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

SENDABLE = ("send", "retry_failed", "retry_uncertain")
PROBLEM_ACTIONS = ("invalid_phone", "variables_error", "conflicting_duplicate", "uncertain", "import_error",
                   "delivery_failed")
PROBLEM_RESULTS = ("failed", "uncertain", "import_error")
EXIT_OK, EXIT_ATTENTION, EXIT_CONFIG, EXIT_NOT_FINISHED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class CampaignError(RuntimeError):
    """Configuration or input that stops the whole run (exit 2)."""


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
    c.executescript(ST.SCHEMA + R.SCHEMA + ST.CAMPAIGN_SCHEMA)
    ST._migrate(c)


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
        c.executescript(ST.CAMPAIGN_SCHEMA)
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
    keys = ("campaign_id", "state", "attempts", "template_id", "template_name", "wamid", "error", "error_code",
            "prior_owner", "prior_reason", "prior_since", "ownership_restore", "claimed_at", "sent_at", "finished_at")
    return {k: row[k] for k in keys}


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
                  "stopped_reason": t["stopped_reason"], "declined": bool(card.get("declined")),
                  "declined_at": card.get("declined_at"), "already_placed": bool(card.get("already_placed")),
                  "campaign_on_card": card.get("campaign"), "prior_contact": bool(card.get("prior_contact")),
                  "prior_placement": card.get("prior_placement"), "last_inbound_at": t["last_inbound_at"],
                  "last_outbound_at": t["last_outbound_at"], "messages": ST.last_message_id(c, phone) > 0,
                  "unread_media": card.get("_unread_media", [])}
    this = _claim_view(ST.campaign_send(c, campaign_id, phone))
    if this:
        this["delivery"] = delivery_view(c, this["wamid"])
    return {"owner": ownership_view(c, phone), "thread": thread, "opted_out": marketing_opt_out(c, phone),
            "this_campaign": this,
            "other_campaigns": [_claim_view(r) for r in ST.campaign_sends_for_phone(c, phone)
                                if r["campaign_id"] != campaign_id]}


def decide(state, retry_uncertain):
    """-> (action, reason) for a phone with a valid lead."""
    this, thread = state["this_campaign"], state["thread"] or {}
    if this and this["state"] == "sent" and (this.get("delivery") or {}).get("status") == "failed":
        # Meta accepted the POST, then reported it undelivered (e.g. 131049 marketing limit, 131026, 131042). Not
        # resent under this campaign id: whether and when to retry is a decision (a new --campaign-id sends it).
        codes = ", ".join(str(e["code"]) for e in this["delivery"]["errors"]) or "no code"
        return "delivery_failed", (f"sent {this['sent_at']} wamid {this['wamid']}, Meta reported it undelivered "
                                   f"({codes}); resend only under a new --campaign-id")
    if this and this["state"] == "sent":
        return "already_sent", f"sent {this['sent_at']} wamid {this['wamid']}"
    if this and this["state"] in ("in_progress", "uncertain") and not retry_uncertain:
        return "uncertain", (f"claim {this['state']} since {this['claimed_at']}: it may have gone out; check "
                             f"--status, then --retry-uncertain" + (f" ({this['error']})" if this["error"] else ""))
    if thread.get("stopped"):
        return "skip_stopped", f"stopped ({thread['stopped_reason']})"
    if thread.get("declined"):
        return "skip_declined", f"declined {thread['declined_at']}"
    if state["opted_out"]:
        return "skip_opted_out", f"marketing opt-out: {state['opted_out']}"
    if this and this["state"] in ("in_progress", "uncertain"):
        return "retry_uncertain", f"--retry-uncertain: claim {this['state']} since {this['claimed_at']}"
    if this and this["state"] == "failed":
        return "retry_failed", f"attempt {this['attempts']} failed: {this['error']}"
    return "send", None


# --- plan -------------------------------------------------------------------------------------------------------

def _import_view(report):
    facts = report.get("facts") or {}
    return {"found": report["found"], "applied": report["applied"], "facts_imported": facts.get("imported"),
            "facts_kept": facts.get("kept"), "facts_conflicting": facts.get("conflicting"),
            "placement_records": len(report.get("placement") or []),
            "placed": any(p.get("placed") for p in report.get("placement") or []),
            "messages": report.get("messages"), "stop_messages": report.get("stop_messages") or [],
            "documents": dict(collections.Counter(d["action"] for d in report.get("documents") or [])),
            "not_recoverable": report.get("not_recoverable", 0), "report": report}


def plan(c, campaign_id, leads, retry_uncertain, source=None):
    """Adds state, action and reason (and the history import preview) to every lead with a valid phone."""
    for lead in leads:
        if lead["phone"] is None:
            continue
        lead["state"] = phone_state(c, campaign_id, lead["phone"])
        if lead["action"] is None:
            lead["action"], lead["reason"] = decide(lead["state"], retry_uncertain)
        if source is not None and lead["action"] in SENDABLE:
            try:
                lead["history_import"] = _import_view(IH.import_phone(source, lead["phone"], apply=False))
            except IH.SourceAccessError:
                raise
            except Exception as exc:
                lead["history_import"] = {"error": f"{type(exc).__name__}: {exc}"}
                lead["action"], lead["reason"] = "import_error", lead["history_import"]["error"]
                continue
            if lead["history_import"]["stop_messages"]:
                lead["action"], lead["reason"] = "skip_opted_out", _history_stop_reason(source, lead["history_import"])
    return leads


def _history_stop_reason(source, view):
    first = view["stop_messages"][0]
    return f"Stopp in the imported history ({source.label}): {first['at']} {first['body']!r}"


def check_template_consistency(c, campaign_id, definition):
    other = sorted({r["template_id"] for r in ST.campaign_sends(c, campaign_id)} - {str(definition["id"])})
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


def send_one(campaign_id, definition, lead, client, clock, retry_uncertain, window, source=None):
    """Import (optional), claim + flip, POST, record. -> result {status, ...}; status one of the decide() skip
    actions, deferred (a turn is in flight: retry_after; or reason outside_window: the window closed while the
    history import ran, retry_after = the next window start), import_error, sent, failed, uncertain. The window is
    checked again inside the claim transaction: an import with document reads can take minutes (review
    2026-09-14: a template went out at 21:03 Berlin). Raises on a database failure and lets KeyboardInterrupt
    through (the claim then stays in_progress = uncertain)."""
    phone = lead["phone"]
    with live_db() as c:
        action, reason = decide(phone_state(c, campaign_id, phone), retry_uncertain)
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
        if result["history_import"]["stop_messages"]:
            return {**result, "status": "skip_opted_out", "reason": _history_stop_reason(source, result["history_import"])}

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
            action, reason = decide(state, retry_uncertain)
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
                # our own earlier flip is still in place (uncertain retry): keep the owner recorded before it
                prior = ({"owner": this["prior_owner"], "reason": this["prior_reason"], "since": this["prior_since"]}
                         if this["prior_owner"] else None)
            attempt = ST.claim_campaign_send(c, campaign_id, phone, definition, lead["params"],
                                             lead["rendered"]["text"], prior, claimed_at,
                                             retry_uncertain=retry_uncertain)
            c.commit()
        except BaseException:
            c.rollback()
            raise
    result.update(attempt=attempt, claimed_at=claimed_at, prior_owner=prior)

    try:
        wamid = client.send_template(phone, definition=definition, params=lead["params"])
    except M.MetaError as exc:
        status_code = exc.status_code
        rejected = isinstance(exc, M.TemplateParamsError) or (status_code is not None and 400 <= status_code < 500)
        return {**result, **_record_unsent(campaign_id, definition, phone, prior, clock(), exc,
                                           "failed" if rejected else "uncertain")}
    except Exception as exc:
        return {**result, **_record_unsent(campaign_id, definition, phone, prior, clock(), exc, "uncertain")}

    sent_at = _iso(clock())
    with ST._lock, live_db() as c:
        c.commit()
        c.execute("begin immediate")
        try:
            ST.record_campaign_send(c, phone, wamid, lead["rendered"], campaign_id, sent_at=sent_at,
                                    template_id=str(definition["id"]), variables=lead["params"], commit=False)
            ST.finish_campaign_send(c, campaign_id, phone, "sent", sent_at, wamid=wamid)
            c.commit()
        except BaseException:
            c.rollback()
            print(f"CRITICAL: {phone} template accepted by Meta as {wamid} but not recorded; the claim stays "
                  f"in_progress (uncertain)", file=sys.stderr, flush=True)
            raise
    return {**result, "status": "sent", "wamid": wamid, "sent_at": sent_at}


def _record_unsent(campaign_id, definition, phone, prior, now, exc, state):
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
            ST.finish_campaign_send(c, campaign_id, phone, state, at, error=error, error_code=code,
                                    error_payload=payload, ownership_restore=restore)
            c.execute("insert into wa_send_failures (phone, error, at) values (?,?,?)", (phone, error, ST.now_iso()))
            c.commit()
        except BaseException:
            c.rollback()
            raise
    return {"status": state, "error": error, "error_code": code, "http_status": http, "ownership_restore": restore}


def run_send(campaign_id, definition, leads, client, window, batch_size, interval, retry_uncertain, no_wait,
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
        result = send_one(campaign_id, definition, lead, client, clock, retry_uncertain, window, source=source)
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
    ST.reconcile_campaign_send(c, campaign_id, phone, wamid, sent_at)
    return {"phone": phone, "wamid": wamid, "status": "marked_sent", "sent_at": sent_at}


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


def status_rows(c, campaign_id):
    rows = []
    for r in ST.campaign_sends(c, campaign_id):
        phone = r["phone"]
        delivery = delivery_view(c, r["wamid"])
        unlinked = []
        if r["state"] in ("in_progress", "uncertain"):
            unlinked = [{"wamid": s["wamid"], "status": s["status"], "at": _ts_iso(s["timestamp"]),
                         "category": status_category(s)}
                        for s in ST.message_statuses_for(c, phone)
                        if s["received_at"] >= r["claimed_at"] and ST.message_by_wamid(c, s["wamid"]) is None]
        replies = [m for m in ST.messages_for(c, phone, direction="in") if r["sent_at"] and m["at"] >= r["sent_at"]]
        state = phone_state(c, campaign_id, phone)
        thread = state["thread"] or {}
        rows.append({
            "phone": phone, **_claim_view(r), "delivery": delivery, "statuses_since_claim_without_message": unlinked,
            "replies": {"count": len(replies), "first_at": replies[0]["at"] if replies else None,
                        "last_at": replies[-1]["at"] if replies else None,
                        "last_text": replies[-1]["body"] if replies else None,
                        "button_taps": [{"text": m["body"], "button_id": m["meta"].get("button_id"), "at": m["at"]}
                                        for m in replies if m["kind"] == "button"],
                        "to_the_template": any(m["meta"].get("reply_to_wamid") == r["wamid"] for m in replies)},
            "unread_media": [u for u in thread.get("unread_media", []) if r["sent_at"] and u["received_at"] >= r["sent_at"]],
            "stage": thread.get("stage"), "declined": thread.get("declined", False),
            "stopped": thread.get("stopped", False), "opted_out": state["opted_out"], "owner": state["owner"]})
    return rows


def status_totals(rows):
    codes = collections.Counter()
    for r in rows:
        if r["error_code"]:
            codes[f"send {r['error_code']}"] += 1
        for e in (r["delivery"] or {}).get("errors") or []:
            codes[f"delivery {e['code']}"] += 1
    return {"phones": len(rows), "state": dict(collections.Counter(r["state"] for r in rows)),
            "delivery": dict(collections.Counter((r["delivery"] or {}).get("status") or "none"
                                                 for r in rows if r["state"] == "sent")),
            "replied": sum(r["replies"]["count"] > 0 for r in rows), "declined": sum(bool(r["declined"]) for r in rows),
            "unread_media": sum(bool(r["unread_media"]) for r in rows),
            "stopped": sum(bool(r["stopped"]) for r in rows), "opted_out": sum(bool(r["opted_out"]) for r in rows),
            "error_codes": dict(codes)}


def _print_status(campaign_id, rows, totals):
    _say(f"campaign {campaign_id}: {totals['phones']} phone(s) claimed")
    for r in rows:
        d = r["delivery"] or {}
        errs = ",".join(str(e["code"]) for e in d.get("errors") or [])
        _say(f"  {r['phone']}: {r['state']} (attempt {r['attempts']})"
             + (f" {r['wamid']}" if r["wamid"] else "")
             + (f" delivery {d['status']} {d['at']}" + (f" errors {errs}" if errs else "") if d else "")
             + (f" send error {r['error_code']}" if r["error_code"] else "")
             + (f" statuses since claim without message: {len(r['statuses_since_claim_without_message'])}"
                if r["statuses_since_claim_without_message"] else "")
             + f" replies {r['replies']['count']}" + (" declined" if r["declined"] else "")
             + (f" unread media {len(r['unread_media'])} (a colleague must look)" if r["unread_media"] else "")
             + (" stopped" if r["stopped"] else "") + (f" stage {r['stage']}" if r["stage"] else ""))
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
                + (" declined" if thread["declined"] else "") if thread else "; no thread")
             + (f"; this campaign {state['this_campaign']['state']}" if state.get("this_campaign") else "")
             + (f"; other campaigns {[o['campaign_id'] + ':' + o['state'] for o in state['other_campaigns']]}"
                if state.get("other_campaigns") else ""))
        imp = lead.get("history_import")
        if imp:
            _say("    history import " + (imp["error"] if "error" in imp else
                                          f"found={imp['found']} facts={imp['facts_imported']} "
                                          f"documents={imp['documents']} not_recoverable={imp['not_recoverable']}"))
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
        client = client or M.Client()
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
    client = client or M.Client()
    if args.send and (not client.access_token or not client.phone_number_id):
        return fail("--send needs META_WHATSAPP_ACCESS_TOKEN and META_WHATSAPP_PHONE_NUMBER_ID")
    try:
        definition = client.get_template(args.template_id, require_approved=True)
    except M.MetaError as exc:
        return fail(f"template {args.template_id}: {exc} {json.dumps(exc.payload, ensure_ascii=False) if exc.payload else ''}")
    if str(definition.get("id")) != str(args.template_id):
        return fail(f"Meta returned template id {definition.get('id')!r} for {args.template_id!r}")
    interval = timedelta(minutes=args.batch_interval_min)
    report.update(
        template={k: definition.get(k) for k in ("id", "name", "language", "status", "category", "parameter_format")},
        settings={"batch_size": args.batch_size, "batch_interval_min": args.batch_interval_min,
                  "window": window.describe(), "retry_uncertain": args.retry_uncertain, "no_wait": args.no_wait,
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
            plan(c, args.campaign_id, leads, args.retry_uncertain, source=None if args.send else source)
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
                               args.retry_uncertain, args.no_wait, clock, sleep, source=source, on_progress=progress)
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
