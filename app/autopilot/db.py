"""SQLite store for the autopilot PoC (docs/autopilot.md, section "Data model").

One file, data/autopilot.sqlite, regenerable from app.autopilot.seed. Arrays and objects live in TEXT
columns as JSON; use j()/uj(). Every table has an integer id except the registry-keyed clinic columns.
Times are ISO-8601 strings in Europe/Berlin (the engine's virtual clock, policy.sim_now, not wall time).
"""
import json
import sqlite3
import threading

from .. import config as A

SQLITE_PATH = A.DATA_DIR / "autopilot.sqlite"
_lock = threading.RLock()

SCHEMA = """
create table if not exists candidates (
  id integer primary key, name text not null, initials text, phone text, email text,
  language text default 'de', german_level text, origin_country text,
  city text, plz text, region text, radius_km integer default 30,
  role_class text, departments text default '[]', qualification text, experience_years integer,
  anerkennung_status text default 'none', work_permit text, employment_type text, shifts text default '[]',
  start_from text, salary_expectation integer,
  source_campaign_id integer, landing_page_id integer, consent_at text,
  stage text not null default 'new', stage_changed_at text, lost_reason text, owner text,
  tags text default '[]', created_at text not null
);
create table if not exists documents (
  id integer primary key, candidate_id integer not null, kind text not null, status text not null default 'missing',
  received_at text, note text
);
create table if not exists conversations (
  id integer primary key, kind text not null, candidate_id integer, clinic_thread_id integer,
  channel text not null, account_id integer,
  mode text not null default 'luna', state text not null default 'greeting', next_actor text default 'us',
  sla_due_at text, unread integer default 0, last_message_at text, last_preview text,
  language text default 'de', tags text default '[]', pause_reason text, opened_at text, closed_at text
);
create table if not exists messages (
  id integer primary key, conversation_id integer not null, dir text not null, author text not null,
  text text not null, at text not null, status text not null default 'sent', template_id integer,
  attachments text default '[]', meta text default '{}'
);
create table if not exists clinic_threads (
  id integer primary key, clinic_id text not null, clinic_name text, contact_name text, contact_email text,
  mailbox_id integer, cohort_id integer, state text not null default 'draft',
  followup_attempt integer default 0, followup_max integer default 3, next_followup_at text,
  candidate_ids text default '[]', proposed_slots text default '[]', agreed_slot text, round integer default 1,
  last_at text, created_at text
);
create table if not exists matches (
  id integer primary key, candidate_id integer not null, clinic_id text not null, posting_id integer,
  score real not null, reasons text default '[]', status text not null default 'proposed', cohort_id integer, created_at text
);
create table if not exists cohorts (
  id integer primary key, name text not null, criteria text default '{}', candidate_ids text default '[]',
  clinic_ids text default '[]', status text not null default 'draft', created_at text, sent_at text
);
create table if not exists interviews (
  id integer primary key, candidate_id integer not null, clinic_id text not null, clinic_thread_id integer,
  at text, format text default 'video', round integer default 1, status text not null default 'proposed', feedback text
);
create table if not exists approvals (
  id integer primary key, kind text not null, risk text not null, reason text, context text default '{}',
  draft text, suggested text default '{}', status text not null default 'pending',
  created_at text, decided_at text, decided_by text, remember integer default 0
);
create table if not exists queue (
  id integer primary key, due_at text not null, kind text not null, target text default '{}', reason text,
  status text not null default 'scheduled', attempt integer default 0, created_by text default 'luna', result text
);
create table if not exists templates (
  id integer primary key, channel text not null, lang text not null, stage text, name text not null,
  subject text, body text not null, variables text default '[]', version integer default 1,
  uses integer default 0, reply_rate real, active integer default 1
);
create table if not exists accounts (
  id integer primary key, kind text not null, name text not null, identifier text, provider text,
  status text not null default 'connected', quality text default 'green', daily_cap integer, used_today integer default 0,
  warmup_stage text, last_error text, last_ok_at text, routing text default '{}'
);
create table if not exists campaigns (
  id integer primary key, name text not null, platform text default 'meta', objective text default 'messages',
  status text not null default 'active', daily_budget real, spend_total real default 0, spend_7d real default 0,
  impressions integer default 0, clicks integer default 0, leads integer default 0, qualified integer default 0,
  interviews integer default 0, placed integer default 0, adsets text default '[]', ads text default '[]', created_at text
);
create table if not exists landing_pages (
  id integer primary key, slug text not null, url text, title text, language text default 'de',
  campaign_ids text default '[]', variants text default '[]', wa_deeplink text, status text default 'live'
);
create table if not exists events (
  id integer primary key, at text not null, actor text not null, kind text not null, target text default '{}', detail text
);
create table if not exists policy (key text primary key, value text not null);
create index if not exists conv_kind_mode on conversations(kind, mode);
create index if not exists conv_next on conversations(next_actor, sla_due_at);
create index if not exists msg_conv on messages(conversation_id, at);
create index if not exists queue_due on queue(status, due_at);
create index if not exists appr_status on approvals(status, risk);
create index if not exists match_cand on matches(candidate_id);
create index if not exists match_clinic on matches(clinic_id);
create index if not exists ev_at on events(at);
"""

JSON_COLS = {
    "candidates": ("departments", "shifts", "tags"),
    "conversations": ("tags",),
    "messages": ("attachments", "meta"),
    "clinic_threads": ("candidate_ids", "proposed_slots"),
    "matches": ("reasons",),
    "cohorts": ("criteria", "candidate_ids", "clinic_ids"),
    "approvals": ("context", "suggested"),
    "queue": ("target",),
    "templates": ("variables",),
    "accounts": ("routing",),
    "campaigns": ("adsets", "ads"),
    "landing_pages": ("campaign_ids", "variants"),
    "events": ("target",),
}

DEFAULT_POLICY = {
    "mode": "auto",                       # off | assist | auto
    "quiet_hours": [8, 20], "tz": "Europe/Berlin",
    "max_msgs_per_candidate_per_day": 3,
    "cadence_candidate_hours": [24, 72, 168],
    "cadence_clinic_business_days": [3, 7, 14],
    "max_concurrent_profiles_per_candidate": 3,
    "max_profiles_per_clinic_per_week": 5,
    "risk_rules": {"low": "auto", "medium": "approve", "high": "approve"},
    "escalation_triggers": ["salary", "visa_legal", "asks_for_human", "complaint"],
    "auto_pause_on": ["wa_quality_red", "campaign_cpl_spike"],
    "sim_now": "2026-09-07T09:00:00+02:00",
}


def j(v):
    return json.dumps(v, ensure_ascii=False)


def uj(v, default=None):
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def db():
    A.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, db() as c:
        c.executescript(SCHEMA)
        for k, v in DEFAULT_POLICY.items():
            c.execute("insert or ignore into policy(key,value) values(?,?)", (k, j(v)))


def row(table, r):
    """sqlite3.Row -> dict with JSON columns decoded."""
    if r is None:
        return None
    d = dict(r)
    for col in JSON_COLS.get(table, ()):
        if col in d:
            d[col] = uj(d[col], [] if col.endswith("s") or col in ("attachments", "reasons", "variants", "adsets", "ads") else {})
    return d


def rows(table, cur):
    return [row(table, r) for r in cur.fetchall()]


def insert(c, table, d):
    d = dict(d)
    for col in JSON_COLS.get(table, ()):
        if col in d and not isinstance(d[col], str):
            d[col] = j(d[col])
    cols = ",".join(d)
    q = ",".join("?" for _ in d)
    return c.execute(f"insert into {table}({cols}) values({q})", tuple(d.values())).lastrowid


def update(c, table, id_, d):
    d = dict(d)
    for col in JSON_COLS.get(table, ()):
        if col in d and not isinstance(d[col], str):
            d[col] = j(d[col])
    sets = ",".join(f"{k}=?" for k in d)
    c.execute(f"update {table} set {sets} where id=?", (*d.values(), id_))


def get(c, table, id_):
    return row(table, c.execute(f"select * from {table} where id=?", (id_,)).fetchone())


def policy(c=None):
    own = c is None
    c = c or db()
    try:
        p = {r["key"]: uj(r["value"]) for r in c.execute("select key,value from policy")}
    finally:
        if own:
            c.close()
    return {**DEFAULT_POLICY, **p}


def set_policy(c, key, value):
    c.execute("insert into policy(key,value) values(?,?) on conflict(key) do update set value=excluded.value", (key, j(value)))


def event(c, at, actor, kind, target=None, detail=None):
    return insert(c, "events", {"at": at, "actor": actor, "kind": kind, "target": target or {}, "detail": detail})


def counts(c=None):
    own = c is None
    c = c or db()
    try:
        return {t: c.execute(f"select count(*) from {t}").fetchone()[0]
                for t in ("candidates", "documents", "conversations", "messages", "clinic_threads", "matches", "cohorts",
                          "interviews", "approvals", "queue", "templates", "accounts", "campaigns", "landing_pages", "events")}
    finally:
        if own:
            c.close()
