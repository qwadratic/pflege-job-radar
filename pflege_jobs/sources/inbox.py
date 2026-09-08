"""Inbox intake -> observations. Rows come from crawler adapters or agents posting directly to
pflege_jobs.inbox (anon insert). kind='jobposting' = JSON-LD JobPosting captured on a detail page; kind='listing' = job links seen
on a list page (title + href; no details). Source: collector 'firecrawl*' -> firecrawl_agent (25); everything else -> employer_ats (20)."""
import json, re
from datetime import datetime, timezone
from urllib.parse import urlparse
from .. import config as C
from .. import section
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm, enrich_description, fuzzy_key, qualification_hint, norm_text)
from .career_crawl import in_bavaria, _strip

def _source_id(collector):
    return C.SOURCES["firecrawl_agent"]["source_id"] if (collector or "").lower().startswith("firecrawl") else C.SOURCES["employer_ats"]["source_id"]


def jobposting_to_obs(row, towns):
    p = row["payload"]; host = row["source_host"]; url = p.get("url") or row["source_url"]
    org = p.get("org") if isinstance(p.get("org"), str) else (p.get("org") or {}).get("name") if isinstance(p.get("org"), dict) else None
    emp = org or host
    e_class, e_rule = classify_employer(emp)
    title = _strip(p.get("title") or ""); desc = _strip(p.get("description") or "")
    locs = p.get("loc") or []
    l = next((x for x in locs if in_bavaria(x.get("city"), x.get("plz"), x.get("region"), towns)), locs[0] if locs else {})
    # Structural signal from the vendor's own category/department taxonomy, set by
    # crawlers/vendor_adapters.py's section-aware crawl_* functions when the job's own label is
    # known (personio/smartrecruiters/dvinci/rexx/mein-check-in/wp_jobs) -- see pflege_jobs/section.py.
    nursing_section_confirmed = section.job_confirmed_nursing(p.get("section_labels"))
    role, rule = classify_role(title, "", nursing_section_confirmed=nursing_section_confirmed)
    enr = {("enr_" + k): v for k, v in enrich_description(desc).items()}
    et = p.get("employmentType"); et = " ".join(et) if isinstance(et, list) else (et or "")
    now = datetime.now(timezone.utc).isoformat()
    return {
        "source_id": _source_id(row.get("collector")), "source_ref": url, "source_url": url, "observed_at": now,
        "title": title, "employer_name": emp, "employer_name_norm": employer_norm(emp), "employer_class": e_class, "employer_class_rule": e_rule,
        "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None, "alle_berufe": [],
        "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(title), "department_raw": None,
        "city": l.get("city"), "plz": l.get("plz"), "region": "BAYERN", "lat": None, "lon": None,
        "in_bavaria": in_bavaria(l.get("city"), l.get("plz"), l.get("region"), towns), "n_locations": len(locs) or 1,
        "locations": json.dumps([{"adresse": {"ort": x.get("city"), "plz": x.get("plz")}} for x in locs], ensure_ascii=False),
        "employment_types": [t for t, k in (("vollzeit", "FULL_TIME"), ("teilzeit", "PART_TIME")) if k in et.upper()],
        "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": "BEFRISTET" if re.search(r"TEMPORARY", et, re.I) else None,
        "fixed_term_months": None, "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
        "first_published": (p.get("datePosted") or "")[:10] or None, "last_modified": None, "valid_until": (p.get("validThrough") or "")[:10] or None,
        "external_url": url, "description": desc[:20000] or None, **enr, "details_fetched_at": now if desc else None, "details_error": None,
        "fuzzy_key": fuzzy_key(title, emp, l.get("city")), "content_hash": content_hash(title, emp, l.get("city"), desc[:200]),
        "payload": json.dumps({"inbox": {"inbox_id": row["inbox_id"], "collector": row.get("collector"), "page": p.get("page"), "host": host}, "crawl": {"seed": p.get("page"), "parse": "collector-jsonld"}}, ensure_ascii=False),
    }
