"""Firecrawl agent source: request shape (always credit-capped) and answer -> inbox row conversion."""
import json

import pytest

from pflege_jobs.sources import firecrawl_agent as FA

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


class _Session:
    """Fake HTTP: submit returns a job id, first poll is processing, second completed."""
    def __init__(self):
        self.posts, self.gets = [], 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(200, {"success": True, "id": "job-1", "status": "processing"})

    def get(self, url, headers=None, timeout=None):
        self.gets += 1
        if self.gets == 1:
            return _Resp(200, {"success": True, "status": "processing"})
        return _Resp(200, {"success": True, "status": "completed", "data": ANSWER, "creditsUsed": 17})


def test_run_jobs_agent_polls_and_converts(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(FA.time, "sleep", lambda *_: None)
    s = _Session()
    res = FA.run_jobs_agent(CLINIC, max_credits=30, log=lambda *_: None, session=s)
    url, body = s.posts[0]
    assert url.endswith("/agent") and body["maxCredits"] == 30 and body["urls"] == [CLINIC["careers_url"], CLINIC["website"]]
    assert "Pflege" in body["prompt"] and CLINIC["name"] in body["prompt"] and body["schema"]["required"] == ["jobs"]
    assert res["credits_used"] == 17 and len(res["rows"]) == 2


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
            raise AssertionError("should not poll the API once the webhook already answered")
    hit = {"status": "completed", "data": ANSWER, "creditsUsed": 5}
    data, used, raw = FA.run_agent("p", FA.JOBS_SCHEMA, max_credits=10, log=lambda *_: None, session=S(),
                                    check_webhook=lambda job_id: hit)
    assert used == 5 and data == ANSWER


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
