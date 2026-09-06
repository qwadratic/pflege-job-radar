"""Firecrawl agent source: request shape (always credit-capped) and answer -> inbox row conversion."""
import json

import pytest

from pflege_jobs.sources import firecrawl_agent as FA

CLINIC = {"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum Ingolstadt", "town": "Ingolstadt", "operator": "kbo-Heckscher-Klinikum gGmbH",
          "website": "https://kbo-heckscher-klinikum.de", "careers_url": "https://kbo-heckscher-klinikum.de/arbeiten-bei-uns"}

ANSWER = {"portal_url": "https://kbo-heckscher-klinikum.de/arbeiten-bei-uns/stellen", "notes": "list is plain HTML",
          "jobs": [
              {"title": "Pflegefachkraft (m/w/d) Kinder- und Jugendpsychiatrie", "url": "https://kbo-heckscher-klinikum.de/jobs/123", "city": "Ingolstadt",
               "plz": "85049", "department": "KJP Station 3", "employment_type": "Vollzeit/Teilzeit", "contract": "unbefristet",
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
    r2 = rows[1]["payload"]
    assert r2["loc"][0]["city"] == "Ingolstadt" and r2["loc"][0]["plz"] is None      # falls back to the registry town
    assert r2["datePosted"] is None and "TEMPORARY" in r2["employmentType"]
    json.dumps(rows)                                        # serialisable for the inbox POST


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
