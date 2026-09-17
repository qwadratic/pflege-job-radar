"""Inbox intake -> observations. Rows come from crawler adapters or agents posting directly to
pflege_jobs.inbox (anon insert). kind='jobposting' = JSON-LD JobPosting captured on a detail page; kind='listing' = job links seen
on a list page (title + href; no details). Source: collector 'firecrawl*' -> firecrawl_agent (25); everything else -> employer_ats (20)."""
import json, re
from datetime import datetime, timezone
from urllib.parse import urlparse
from .. import config as C
from .. import section
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm, enrich_description, fuzzy_key, qualification_hint, norm_text)
from .career_crawl import _canon_town, city_from_url, in_bavaria, _strip

def _source_id(collector):
    return C.SOURCES["firecrawl_agent"]["source_id"] if (collector or "").lower().startswith("firecrawl") else C.SOURCES["employer_ats"]["source_id"]


# A staging/preview/test deployment is not a place real vacancies live: its postings are copies that
# 404 or drift independently of the real portal. LMU Klinikum's registry careers_url pointed at
# referral-portal-staging.lmu-klinikum.de (and at a single posting rather than a listing), which put
# 77 rows in the table and was still feeding 5 a night on 2026-09-17 -- the registry entry is fixed,
# this keeps any other one from entering the same way.
NON_PROD_HOST = re.compile(r"(^|[.\-])(staging|preview|testing|sandbox|dev|qa)([.\-]|$)", re.I)


def jobposting_to_obs(row, towns):
    p = row["payload"]; host = row["source_host"]; url = p.get("url") or row["source_url"]
    org = p.get("org") if isinstance(p.get("org"), str) else (p.get("org") or {}).get("name") if isinstance(p.get("org"), dict) else None
    emp = org or host
    e_class, e_rule = classify_employer(emp)
    title = _strip(p.get("title") or ""); desc = _strip(p.get("description") or "")
    # A malformed upstream row can carry a one-item list instead of a scalar per field (seen live
    # 2026-09-09, inbox_id 13576: {"plz": ["97318"], "city": ["Kitzingen"]}) -- normalize once, here,
    # before anything downstream (in_bavaria, fuzzy_key, content_hash, the locations json below) sees
    # city/plz/region, rather than defending each caller separately.
    def _scalar(v):
        return (v[0] if v else None) if isinstance(v, list) else v
    locs = [{**x, "city": _scalar(x.get("city")), "plz": _scalar(x.get("plz")), "region": _scalar(x.get("region"))} for x in (p.get("loc") or [])]
    l = next((x for x in locs if in_bavaria(x.get("city"), x.get("plz"), x.get("region"), towns)), locs[0] if locs else {})
    # The URL is the source naming the job's location, and it outranks a city the crawler inherited
    # from the seed clinic -- the only signal that catches the AMEOS shape, where the page states no
    # location at all and the row arrives carrying the seed clinic's Bavarian town while its own URL
    # says Oberhausen/Haldensleben/Eutin (22 of 26 new rows in one night, measured 2026-09-17).
    # A slug that is not a placeable city ("-in-teilzeit") is ignored by city_from_url itself.
    url_city = city_from_url(url, towns)
    city_src = "page"
    if url_city and _canon_town(url_city) != _canon_town(l.get("city")):
        if p.get("city_source") == "seed" or in_bavaria(url_city, None, None, towns) is False:
            l = {**l, "city": url_city, "plz": None, "region": None}
            city_src = "url"
    elif p.get("city_source") == "seed":
        city_src = "seed"
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
        "city": l.get("city"), "plz": l.get("plz"), "region": l.get("region"), "lat": None, "lon": None,
        "in_bavaria": in_bavaria(l.get("city"), l.get("plz"), l.get("region"), towns), "n_locations": len(locs) or 1,
        "locations": json.dumps([{"adresse": {"ort": x.get("city"), "plz": x.get("plz")}} for x in locs], ensure_ascii=False),
        "employment_types": [t for t, k in (("vollzeit", "FULL_TIME"), ("teilzeit", "PART_TIME")) if k in et.upper()],
        "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": "BEFRISTET" if re.search(r"TEMPORARY", et, re.I) else None,
        "fixed_term_months": None, "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
        "first_published": (p.get("datePosted") or "")[:10] or None, "last_modified": None, "valid_until": (p.get("validThrough") or "")[:10] or None,
        "external_url": url, "description": desc[:20000] or None, **enr, "details_fetched_at": now if desc else None, "details_error": None,
        "fuzzy_key": fuzzy_key(title, emp, l.get("city")), "content_hash": content_hash(title, emp, l.get("city"), desc[:200]),
        # provenance: the board this row was fetched from bounds which registry site it can belong to
        "_board": p.get("board_clinic_ids") or None,
        # ...and whether the employer was read off the posting or copied from the seed clinic, which
        # decides whether the Matcher may use it for an exact-identity match at all
        "_emp_inherited": p.get("org_source") == "seed",
        "payload": json.dumps({"inbox": {"inbox_id": row["inbox_id"], "collector": row.get("collector"), "page": p.get("page"), "host": host},
                               "crawl": {"seed": p.get("page"), "parse": "collector-jsonld"},
                               "city_source": city_src, "employer_source": p.get("org_source") or "page"}, ensure_ascii=False),
    }
