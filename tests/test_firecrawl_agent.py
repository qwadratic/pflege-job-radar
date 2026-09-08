"""Firecrawl agent source: request shape (always credit-capped), answer -> inbox row conversion, both balance pools
(credits + Extract tokens), the free-daily-run ledger and the before/after credit delta run_agent() charges.
No network: every HTTP call goes through the fake _Session; FA.agent_runs_today is stubbed so the real app.sqlite
is never read (the ledger tests use a temp SQLite)."""
import json
import sqlite3

import pytest

from pflege_jobs.sources import firecrawl_agent as FA

_REAL_AGENT_RUNS_TODAY = FA.agent_runs_today          # captured before the autouse stub below replaces it


@pytest.fixture(autouse=True)
def _no_ledger(monkeypatch):
    """run_agent() asks the app ledger how many runs happened today; keep that off the real data/app.sqlite."""
    monkeypatch.setattr(FA, "agent_runs_today", lambda: 0)

CLINIC = {"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum Ingolstadt", "town": "Ingolstadt", "operator": "kbo-Heckscher-Klinikum gGmbH",
          "website": "https://kbo-heckscher-klinikum.de", "careers_url": "https://kbo-heckscher-klinikum.de/arbeiten-bei-uns"}

ANSWER = {"portal_url": "https://kbo-heckscher-klinikum.de/arbeiten-bei-uns/stellen", "notes": "list is plain HTML", "blocked_reason": "",
          "jobs": [
              {"title": "Pflegefachkraft (m/w/d) Kinder- und Jugendpsychiatrie", "url": "https://kbo-heckscher-klinikum.de/jobs/123", "city": "Ingolstadt",
               "plz": "85049", "department": "KJP Station 3", "seniority": "fachkraft", "employment_type": "Vollzeit/Teilzeit", "contract": "unbefristet",
               "published": "2026-09-01", "description": "Sie betreuen ...", "requirements": "examinierte Pflegefachkraft", "tariff_or_salary": "TVöD P8",
               "contact_email": "bewerbung@kbo.de"},
              {"title": "Stationsleitung (m/w/d)", "url": "https://kbo-heckscher-klinikum.de/jobs/124", "contract": "befristet", "published": "September 2026"},
              {"title": "Pflegefachkraft (m/w/d) Kinder- und Jugendpsychiatrie", "url": "https://kbo-heckscher-klinikum.de/jobs/123"},   # duplicate url
              {"title": "no url", "url": ""},
          ]}


def test_jobs_to_inbox_rows():
    rows = FA.jobs_to_inbox_rows(ANSWER, CLINIC)
    assert len(rows) == 2                                   # duplicate + url-less dropped
    r = rows[0]
    assert r["kind"] == "jobposting" and r["collector"] == "firecrawl-agent" and r["client_id"] == "firecrawl-agent-16104"
    assert r["source_host"] == "kbo-heckscher-klinikum.de" and r["source_url"] == "https://kbo-heckscher-klinikum.de/jobs/123"
    p = r["payload"]
    assert p["org"] == CLINIC["name"] and p["loc"] == [{"city": "Ingolstadt", "plz": "85049", "region": "BAYERN"}]
    assert p["datePosted"] == "2026-09-01" and "FULL_TIME" in p["employmentType"] and "PART_TIME" in p["employmentType"]
    assert "Anforderungen: examinierte" in p["description"] and "TVöD P8" in p["description"]
    assert p["department"] == "KJP Station 3" and p["page"] == ANSWER["portal_url"]
    assert p["seniority"] == "fachkraft"
    r2 = rows[1]["payload"]
    assert r2["loc"][0]["city"] == "Ingolstadt" and r2["loc"][0]["plz"] is None      # falls back to the registry town
    assert r2["datePosted"] is None and "TEMPORARY" in r2["employmentType"]
    assert r2["seniority"] == "unknown"                     # job had no seniority field at all
    json.dumps(rows)                                        # serialisable for the inbox POST


def test_jobs_prompt_keeps_every_hard_rule():
    p = FA._jobs_prompt(CLINIC)
    assert CLINIC["name"] in p and "Bavaria" in p
    for must in ("Pflegefachkraft", "Gesundheits- und Krankenpfleger", "Praxisanleitung", "Stationsleitung",
                 "Hebamme", "OTA/ATA", "Pflegeexperte", "Pflegehelfer", "Ausbildung", "Praktikum", "Werkstudent",
                 "FSJ/BFD", "physicians", "MFA", "therapists", "admin", "logistics", "outside Bavaria",
                 "blocked_reason", "leitung", "fachkraft", "experte"):
        assert must in p, f"missing {must!r} from jobs prompt"


def test_jobs_schema_has_seniority_and_blocked_reason():
    props = FA.JOBS_SCHEMA["properties"]["jobs"]["items"]["properties"]
    assert set(FA.JOBS_SCHEMA["properties"]["jobs"]["items"]["properties"]["seniority"]["enum"]) == set(FA.SENIORITY)
    assert "blocked_reason" in FA.JOBS_SCHEMA["properties"]
    assert "department" in props


def test_build_request_is_capped():
    b = FA.build_request("p", FA.JOBS_SCHEMA, urls=["https://x", None], max_credits=25)
    assert b["maxCredits"] == 25 and b["urls"] == ["https://x"] and b["schema"] is FA.JOBS_SCHEMA and b["model"]
    with pytest.raises(ValueError):
        FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=0)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b, self.text = status, body, json.dumps(body)

    def json(self):
        return self._b


CREDIT_USAGE = {"success": True, "data": {"remainingCredits": 379, "planCredits": 8000,
                                           "billingPeriodStart": "2026-08-19T20:01:50.000Z", "billingPeriodEnd": "2026-09-19T20:01:50.000Z"}}
TOKEN_USAGE = {"success": True, "data": {"remainingTokens": 5685, "planTokens": 120000,
                                          "billingPeriodStart": "2026-08-19T20:01:50.000Z", "billingPeriodEnd": "2026-09-19T20:01:50.000Z"}}
CREDIT_HIST = {"success": True, "periods": [{"startDate": "2026-08-01T00:00:00.000Z", "endDate": "2026-09-01T00:00:00.000Z", "creditsUsed": 6694},
                                            {"startDate": "2026-09-01T00:00:00.000Z", "endDate": None, "creditsUsed": 71}]}
TOKEN_HIST = {"success": True, "periods": [{"startDate": "2026-08-01T00:00:00.000Z", "endDate": "2026-09-01T00:00:00.000Z", "tokensUsed": 100410},
                                           {"startDate": "2026-09-01T00:00:00.000Z", "endDate": None, "tokensUsed": 1065}]}


class _Session:
    """Fake HTTP: submit returns a job id, first agent poll is processing, second completed (creditsUsed 17).
    /team/credit-usage answers the current credit balance, /team/token-usage the Extract-token balance; both
    drop (by `cost` / `token_cost`) once the job completes -- so the before/after deltas run_agent() measures
    are exactly those, independent of the API's creditsUsed."""
    def __init__(self, cost=17, balance=379, api_credits=17, token_cost=0, tokens=5685):
        self.posts, self.gets, self.cost, self.balance, self.api_credits = [], 0, cost, balance, api_credits
        self.token_cost, self.tokens = token_cost, tokens

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(200, {"success": True, "id": "job-1", "status": "processing"})

    def _team(self, url):
        if url.endswith("/team/credit-usage"):
            return _Resp(200, {"success": True, "data": {**CREDIT_USAGE["data"], "remainingCredits": self.balance}})
        if url.endswith("/team/token-usage"):
            return _Resp(200, {"success": True, "data": {**TOKEN_USAGE["data"], "remainingTokens": self.tokens}})
        if url.endswith("/team/credit-usage/historical"):
            return _Resp(200, CREDIT_HIST)
        if url.endswith("/team/token-usage/historical"):
            return _Resp(200, TOKEN_HIST)
        return None

    def get(self, url, headers=None, timeout=None):
        t = self._team(url)
        if t is not None:
            return t
        self.gets += 1
        if self.gets == 1:
            return _Resp(200, {"success": True, "status": "processing"})
        self.balance -= self.cost
        self.tokens -= self.token_cost
        return _Resp(200, {"success": True, "status": "completed", "data": ANSWER, "creditsUsed": self.api_credits})


def test_run_jobs_agent_polls_and_converts(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    s = _Session()
    res = FA.run_jobs_agent(CLINIC, max_credits=30, log=lambda *_: None, session=s)
    url, body = s.posts[0]
    assert url.endswith("/agent") and body["maxCredits"] == 30 and body["urls"] == [CLINIC["careers_url"], CLINIC["website"]]
    assert "Pflege" in body["prompt"] and CLINIC["name"] in body["prompt"] and body["schema"]["required"] == ["jobs"]
    assert res["credits_used"] == 17 and len(res["rows"]) == 2
    assert res["credits_api"] == 17 and res["credits_delta"] == 17 and res["job_id"] == "job-1"
    assert res["credits_before"] == 379 and res["credits_after"] == 362
    assert res["tokens_before"] == 5685 and res["tokens_after"] == 5685 and res["tokens_delta"] == 0   # a run that moved no tokens
    assert res["run_number_today"] == 1 and res["free_run"] is True


def test_run_career_agent(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            return _Resp(200, {"success": True, "status": "completed", "creditsUsed": 9,
                               "data": {"careers_url": CLINIC["careers_url"], "portal_url": "https://x.softgarden.io/de/vacancies", "ats_vendor": "SoftGarden",
                                        "listing_technology": "html", "filters": [{"name": "Berufsgruppe", "values": ["Pflege", "Ärzte"]}], "visible_job_count": 12}})
    res = FA.run_career_agent(CLINIC, max_credits=20, log=lambda *_: None, session=S())
    assert res["credits_used"] == 9 and res["profile"]["ats_vendor"] == "softgarden" and FA.ats_type_for(res["profile"]) == "softgarden"
    assert FA.ats_type_for({"ats_vendor": "other"}) is None


def test_failed_job_raises(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            return _Resp(200, {"success": False, "status": "failed", "error": "cancelled"})
    with pytest.raises(RuntimeError):
        FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=5, log=lambda *_: None, session=S())


def test_failed_job_is_agentfailed_and_carries_credits(monkeypatch):
    """A 'failed' status must not vanish from the local budget ledger: AgentFailed carries creditsUsed
    when the API reports it, else falls back to max_credits (over-charging the ledger is safe)."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)

    class SWithCredits(_Session):
        def get(self, url, headers=None, timeout=None):
            return _Resp(200, {"success": False, "status": "failed", "error": "cancelled", "creditsUsed": 12})
    with pytest.raises(FA.AgentFailed) as ei:
        FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=lambda *_: None, session=SWithCredits())
    assert ei.value.credits_used == 12

    class SNoCredits(_Session):
        def get(self, url, headers=None, timeout=None):
            return _Resp(200, {"success": False, "status": "failed", "error": "cancelled"})
    with pytest.raises(FA.AgentFailed) as ei2:
        FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=lambda *_: None, session=SNoCredits())
    assert ei2.value.credits_used == 30                      # fallback to max_credits


def test_build_request_passes_webhook_through():
    hook = {"url": "https://x/webhook", "headers": {"X-Pflege-Webhook-Secret": "s"}, "metadata": {"clinic_id": "1"}}
    b = FA.build_request("p", FA.JOBS_SCHEMA, max_credits=10, webhook=hook)
    assert b["webhook"] == hook
    b2 = FA.build_request("p", FA.JOBS_SCHEMA, max_credits=10)
    assert "webhook" not in b2


def test_check_webhook_short_circuits_polling(monkeypatch):
    """If check_webhook reports the job done, run_agent must not fall back to an HTTP poll for that tick."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            if "/agent/" in url:
                raise AssertionError("should not poll the API once the webhook already answered")
            return super().get(url, headers, timeout)                 # balance reads are not polls
    s = S()
    hit = {"status": "completed", "data": ANSWER, "creditsUsed": 5}

    def webhook(job_id):
        s.balance -= 5                                                # the account was charged by the time the event landed
        return hit
    data, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=10, log=lambda *_: None, session=s, check_webhook=webhook)
    assert used == 5 and data == ANSWER and raw["_cost"]["credits_delta"] == 5


def test_timeout_is_agentfailed_with_max_credits(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    t = [0]

    def fake_time():
        t[0] += 1000
        return t[0]
    monkeypatch.setattr(FA.time, "time", fake_time)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            return _Resp(200, {"success": True, "status": "processing"})
    with pytest.raises(FA.AgentFailed) as ei:
        FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=8, timeout=5, log=lambda *_: None, session=S())
    assert ei.value.credits_used == 8


# --- two pools + free allowance in credits() -----------------------------------------------------------
def test_credits_carries_both_pools_and_free_runs(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    fc = FA.credits(session=_Session())
    assert fc["remaining"] == 379 and fc["plan"] == 8000 and fc["period_end"] == "2026-09-19T20:01:50.000Z"
    assert fc["tokens_remaining"] == 5685 and fc["tokens_plan"] == 120000
    assert fc["credits_used_hist"] == 71 and fc["tokens_used_hist"] == 1065          # the open (endDate null) period
    assert fc["hist_period_start"] == "2026-09-01T00:00:00.000Z" and fc["hist_period_end"] is None
    assert fc["free_runs_per_day"] == 5 and fc["agent_runs_today"] == 0 and fc["free_runs_left_today"] == 5
    assert "spent_tokens_by_app" in fc and "spent_tokens_by_app_7d" in fc
    assert "error" not in fc and "tokens_error" not in fc and "hist_error" not in fc


def test_credits_tolerates_a_dead_token_endpoint(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA, "agent_runs_today", lambda: 3)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            if "token-usage" in url:
                raise ConnectionError("boom")
            return super().get(url, headers, timeout)
    fc = FA.credits(session=S())
    assert fc["remaining"] == 379 and fc["tokens_remaining"] is None and fc["tokens_plan"] is None
    assert "boom" in fc["tokens_error"] and "boom" in fc["hist_error"] and fc["credits_used_hist"] == 71 and fc["tokens_used_hist"] is None
    assert fc["free_runs_left_today"] == 2
    lean = FA.credits(session=_Session(), tokens=False, historical=False)
    assert "tokens_remaining" not in lean and "credits_used_hist" not in lean and lean["remaining"] == 379


def test_credits_unknown_ledger_means_unknown_free_runs(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA, "agent_runs_today", lambda: None)
    fc = FA.credits(session=_Session())
    assert fc["agent_runs_today"] is None and fc["free_runs_left_today"] is None and FA.free_runs_left_today() is None


# --- the ledger: agent_runs_today() counts today's UTC submissions only ------------------------------------
@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    from app import config as A
    from app import runs as R
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setattr(FA, "agent_runs_today", _REAL_AGENT_RUNS_TODAY)   # the real one, on the temp DB
    R.init()
    return R


def test_agent_runs_today_counts_only_todays_utc_submissions(ledger):
    R = ledger
    from datetime import datetime, timedelta, timezone
    assert FA.agent_runs_today() == 0
    R.add_usage("jobs", "77406", 0, 28, job_id="job-a")                 # accepted submission today
    R.add_usage("career", "77406", 0, 29, job_id="job-b")               # career agent counts too
    R.add_usage("jobs", "77406", 12, 30)                                 # legacy row without a job id: not a submission
    R.add_usage("webhook", None, 3, 31, job_id="job-c")                  # webhook with no clinic: not an agent submission of ours
    assert FA.agent_runs_today() == 2 and FA.free_runs_left_today() == 3
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    with R.db() as c:
        c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id,job_id) values(?,?,?,?,?,?)", (yesterday, "jobs", "1", 0, 1, "job-old"))
    assert FA.agent_runs_today() == 2                                    # yesterday's row is outside the UTC day
    last_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    with R.db() as c:
        c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id,job_id) values(?,?,?,?,?,?)", (last_midnight, "jobs", "1", 0, 1, "job-midnight"))
    assert FA.agent_runs_today() == 3                                    # midnight itself is today


def test_add_usage_keyed_by_job_id_updates_instead_of_duplicating(ledger):
    R = ledger
    R.add_usage("jobs", "77406", 0, 28, job_id="job-a")                 # on_submit: nothing measured yet
    R.add_usage("jobs", "77406", 25, 28, job_id="job-a", tokens=405)    # poll result with both deltas
    R.add_usage("jobs", "77406", 25, 28, job_id="job-a")                # webhook for the same job: no token figure, must not erase it
    with R.db() as c:
        rows = c.execute("select credits, tokens, job_id from firecrawl_usage").fetchall()
    assert [tuple(r) for r in rows] == [(25, 405, "job-a")] and R.usage_total() == 25 and FA.agent_runs_today() == 1
    assert R.tokens_total() == 405 and R.tokens_total(days=7) == 405 and R.tokens_total(hours=1) == 405
    R.add_usage("jobs", "77406", 0, 29, job_id="job-b", tokens=None)    # unmeasured run counts 0 tokens, not an error
    R.add_usage("career", "77406", 27, 30, job_id="job-c", tokens=12)
    assert R.tokens_total() == 417 and FA.tokens_spent_by_app(days=7) == 417


def test_job_id_column_is_added_to_an_old_ledger(tmp_path, monkeypatch):
    from app import config as A
    from app import runs as R
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as c:                                     # the table as it shipped before job_id existed
        c.execute("create table firecrawl_usage (id integer primary key autoincrement, at text, kind text, clinic_id text, credits integer, run_id integer)")
        c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id) values('2026-09-07T21:16:43+00:00','jobs','77406',0,28)")
    monkeypatch.setattr(A, "SQLITE_PATH", path)
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    R.init()                                                             # idempotent
    with sqlite3.connect(path) as c:
        cols = [r[1] for r in c.execute("pragma table_info(firecrawl_usage)")]
        assert "job_id" in cols and "tokens" in cols and c.execute("select count(*) from firecrawl_usage").fetchone()[0] == 1
    assert R.tokens_total() == 0                                         # the legacy row has no token figure
    R.add_usage("jobs", "77406", 0, 31, job_id="job-new", tokens=81)
    assert R.tokens_total() == 81 and R.usage_total() == 0


def test_add_usage_on_an_unmigrated_ledger_migrates_first(tmp_path, monkeypatch):
    """A process that writes usage without ever calling init() (old schema on disk) must not crash on the new columns."""
    from app import config as A
    from app import runs as R
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as c:
        c.execute("create table firecrawl_usage (id integer primary key autoincrement, at text, kind text, clinic_id text, credits integer, run_id integer)")
    monkeypatch.setattr(A, "SQLITE_PATH", path)
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.add_usage("jobs", "77406", 27, 35, job_id="job-35", tokens=405)
    with sqlite3.connect(path) as c:
        assert [tuple(r) for r in c.execute("select job_id, credits, tokens from firecrawl_usage")] == [("job-35", 27, 405)]
    assert R.tokens_total() == 405 and R.agent_runs_today() == 1


# --- run_agent(): balance delta is what gets charged; free-run label in the log ----------------------------
def test_run_agent_charges_balance_delta_not_api_creditsused(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    logged = []
    s = _Session(cost=250, api_credits=0)                                # API says 0 (free allowance), balance says 250
    data, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=300, log=logged.append, session=s)
    assert used == 250 and raw["_cost"]["api_credits_used"] == 0 and raw["_cost"]["credits_delta"] == 250
    assert raw["_cost"]["credits_before"] == 379 and raw["_cost"]["credits_after"] == 129 and raw["_cost"]["job_id"] == "job-1"
    assert any("free daily run 1/5" in l for l in logged) and any("DISAGREE" in l for l in logged)


def test_run_agent_records_token_delta_next_to_credit_delta(monkeypatch):
    """2026-09-08: a batch of five runs moved the Extract pool 5685 -> 5280 and nobody could say which run did it.
    Both pools are read before submit and after the terminal status; both deltas land in the result and the log."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    logged = []
    res = FA.run_jobs_agent(CLINIC, max_credits=40, log=logged.append, session=_Session(cost=27, api_credits=27, token_cost=405))
    assert res["credits_used"] == 27 and res["credits_delta"] == 27
    assert res["tokens_before"] == 5685 and res["tokens_after"] == 5280 and res["tokens_delta"] == 405
    assert any("credits delta 27, tokens delta 405" in l for l in logged)
    raw = res["raw"]["_cost"]
    assert raw["tokens_before"] == 5685 and raw["tokens_after"] == 5280 and raw["tokens_delta"] == 405


def test_run_agent_token_delta_unreadable_or_rising_is_none(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            if url.endswith("/team/token-usage"):
                raise ConnectionError("token endpoint down")
            return super().get(url, headers, timeout)
    logged = []
    _, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=logged.append, session=S(cost=3, api_credits=3))
    assert used == 3 and raw["_cost"]["credits_delta"] == 3                       # the credit side is unaffected
    assert raw["_cost"]["tokens_before"] is None and raw["_cost"]["tokens_after"] is None and raw["_cost"]["tokens_delta"] is None
    assert any("credits delta 3, tokens delta unknown" in l for l in logged)
    _, _, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=lambda *_: None, session=_Session(cost=0, api_credits=0, token_cost=-120000))
    assert raw["_cost"]["tokens_delta"] is None and raw["_cost"]["tokens_after"] == 125685   # pool went up: period reset, not measurable


def test_run_agent_sixth_run_is_billable_in_the_log(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    monkeypatch.setattr(FA, "agent_runs_today", lambda: 5)
    logged = []
    _, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=logged.append, session=_Session(cost=0, api_credits=0))
    assert used == 0 and raw["_cost"]["run_number_today"] == 6 and raw["_cost"]["free_run"] is False
    assert any("billable run" in l for l in logged) and not any("free daily run" in l for l in logged)
    monkeypatch.setattr(FA, "agent_runs_today", lambda: 4)
    logged.clear()
    _, _, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=logged.append, session=_Session(cost=0, api_credits=0))
    assert raw["_cost"]["run_number_today"] == 5 and raw["_cost"]["free_run"] is True and any("free daily run 5/5" in l for l in logged)


def test_run_agent_falls_back_to_api_credits_when_balance_unreadable(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    monkeypatch.setattr(FA, "agent_runs_today", lambda: None)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            if "/team/" in url:
                raise ConnectionError("no balance")
            return super().get(url, headers, timeout)
    logged = []
    _, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=logged.append, session=S(api_credits=17))
    assert used == 17 and raw["_cost"]["credits_delta"] is None and raw["_cost"]["credits_before"] is None
    assert raw["_cost"]["run_number_today"] is None and raw["_cost"]["free_run"] is None
    assert any("treat as billable" in l for l in logged)


def test_run_agent_ignores_a_balance_that_went_up(monkeypatch):
    """Period reset / top-up between the two reads: the delta is meaningless, charge the API number."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    _, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=lambda *_: None, session=_Session(cost=-7621, api_credits=3))
    assert used == 3 and raw["_cost"]["credits_delta"] is None and raw["_cost"]["credits_after"] == 8000


def test_run_agent_calls_on_submit_with_the_job_id(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    seen = []
    res = FA.run_jobs_agent(CLINIC, max_credits=30, log=lambda *_: None, session=_Session(), on_submit=seen.append)
    assert seen == ["job-1"] and res["job_id"] == "job-1"

    def boom(job_id):
        raise RuntimeError("ledger down")
    res = FA.run_jobs_agent(CLINIC, max_credits=30, log=lambda *_: None, session=_Session(), on_submit=boom)   # never fatal
    assert res["credits_used"] == 17


def test_failed_job_carries_job_id_and_delta(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)

    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            if "/agent/" in url:
                self.balance -= 9
                self.tokens -= 40
                return _Resp(200, {"success": False, "status": "failed", "error": "cancelled"})
            return super().get(url, headers, timeout)
    with pytest.raises(FA.AgentFailed) as ei:
        FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=30, log=lambda *_: None, session=S())
    assert ei.value.job_id == "job-1" and ei.value.credits_delta == 9 and ei.value.credits_used == 30   # ledger charge stays conservative
    assert ei.value.tokens_delta == 40
