"""Feed-based ATS adapters (no scraping): Personio XML, SmartRecruiters public API. Source employer_ats (20)."""
import json, re, xml.etree.ElementTree as ET
from datetime import datetime, timezone
import requests
from .. import config as C
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm, enrich_description, fuzzy_key, qualification_hint)
from .career_crawl import _strip, in_bavaria, UA

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]


def _obs(url, title, emp, city, plz, region, desc, published, dept, extra, towns, seed):
    e_class, e_rule = classify_employer(emp)
    role, rule = classify_role(title, "")
    enr = {("enr_" + k): v for k, v in enrich_description(desc or "").items()}
    now = datetime.now(timezone.utc).isoformat()
    return {
        "source_id": SOURCE_ID, "source_ref": url, "source_url": url, "observed_at": now, "title": title,
        "employer_name": emp, "employer_name_norm": employer_norm(emp), "employer_class": e_class, "employer_class_rule": e_rule,
        "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None, "alle_berufe": [],
        "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(f"{title} {dept or ''}"),
        "department_raw": dept, "city": city, "plz": plz, "region": "BAYERN", "lat": extra.get("lat"), "lon": extra.get("lon"),
        "in_bavaria": in_bavaria(city, plz, region, towns), "n_locations": 1,
        "locations": json.dumps([{"adresse": {"ort": city, "plz": plz, "region": region}}], ensure_ascii=False),
        "employment_types": extra.get("employment_types", []), "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None,
        "contract": extra.get("contract"), "fixed_term_months": None, "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
        "first_published": (published or "")[:10] or None, "last_modified": None, "valid_until": None, "external_url": url,
        "description": (desc or "")[:20000] or None, **enr, "details_fetched_at": now if desc else None, "details_error": None,
        "fuzzy_key": fuzzy_key(title, emp, city), "content_hash": content_hash(title, emp, city, (desc or "")[:200]),
        "payload": json.dumps({"crawl": {"seed": seed.get("career"), "kez": seed.get("kez"), "parse": extra.get("parse", "feed")}, "feed": extra.get("raw", {})}, ensure_ascii=False),
    }


def personio(seed, towns, log=print):
    """seed: {name, slug (xxx.jobs.personio.de|.com host), kez?, town?}"""
    host = seed["slug"]
    r = requests.get(f"https://{host}/xml?language=de", headers={"User-Agent": UA}, timeout=40); r.raise_for_status()
    root = ET.fromstring(r.content); out = []
    for pos in root.iter("position"):
        g = lambda t: (pos.findtext(t) or "").strip()
        title = g("name"); office = g("office"); url = f"https://{host}/job/{g('id')}"
        desc = " ".join(_strip(d.findtext("value") or "") for d in pos.iter("jobDescription"))
        dept = g("department") or None
        et = g("schedule").lower(); emp_types = [t for t, k in (("vollzeit", "full"), ("teilzeit", "part")) if k in et]
        from ..classify import norm_text
        city = office if office and norm_text(office).split(",")[0] in towns else seed.get("town")
        o = _obs(url, title, seed["name"], city, None, None, desc, g("createdAt"), dept,
                 {"employment_types": emp_types, "parse": "personio_xml", "raw": {"id": g("id"), "office": office, "schedule": et, "seniority": g("seniority")}}, towns, seed)
        if o["role_class"] == "nicht_pflege" or o["in_bavaria"] is False: continue
        if o["in_bavaria"] is None and seed.get("bavaria_only_operator", True): o["in_bavaria"] = True
        if o["in_bavaria"]: out.append(o)
    log(f"{seed['name'][:34]:<34} personio {host} -> Pflege/BY {len(out)}")
    return out


def smartrecruiters(seed, towns, log=print, with_details=True):
    """seed: {name, company (SmartRecruiters companyIdentifier), site_map: {city-regex: {kez, employer}}}"""
    comp = seed["company"]; out = []; offset = 0; total = None
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    while True:
        d = s.get(f"https://api.smartrecruiters.com/v1/companies/{comp}/postings", params={"limit": 100, "offset": offset}, timeout=40).json()
        total = total or d.get("totalFound", 0); items = d.get("content", [])
        for p in items:
            loc = p.get("location") or {}; city = loc.get("city"); region = loc.get("region")
            if region and region.upper() not in ("BY", "BAYERN", "BAVARIA") and city and in_bavaria(city, None, region, towns) is False: continue
            title = p.get("name") or ""
            if classify_role(title, "")[0] == "nicht_pflege": continue
            emp = seed["name"]
            for rx, site in (seed.get("site_map") or {}).items():
                if re.search(rx, city or "", re.I): emp = site.get("employer", emp); break
            desc = None
            if with_details:
                try:
                    det = s.get(p["ref"], timeout=40).json(); secs = (det.get("jobAd") or {}).get("sections") or {}
                    desc = " ".join(_strip((secs.get(k) or {}).get("text") or "") for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
                except Exception: pass
            url = f"https://jobs.smartrecruiters.com/{comp}/{p['id']}"
            o = _obs(url, title, emp, city, None, region, desc, p.get("releasedDate"), (p.get("department") or {}).get("label"),
                     {"lat": float(loc["latitude"]) if loc.get("latitude") else None, "lon": float(loc["longitude"]) if loc.get("longitude") else None,
                      "employment_types": [{"Full-time": "vollzeit", "Part-time": "teilzeit"}.get((p.get("typeOfEmployment") or {}).get("label"), "")] if p.get("typeOfEmployment") else [],
                      "parse": "smartrecruiters_api", "raw": {"id": p["id"], "refNumber": p.get("refNumber"), "fullLocation": loc.get("fullLocation")}}, towns, seed)
            o["employment_types"] = [x for x in o["employment_types"] if x]
            if o["in_bavaria"] is False: continue
            if o["in_bavaria"] is None: continue          # nationwide operator: positive evidence only
            out.append(o)
        offset += len(items)
        if not items or offset >= total: break
    log(f"{seed['name'][:34]:<34} smartrecruiters {comp} total {total} -> Pflege/BY {len(out)}")
    return out


def talention(seed, towns, log=print):
    """seed: {name, host (e.g. jobs.ebel-kliniken.com), kez?, town?}. POST /talention/api/3.2/job (public, paginated)."""
    host = seed["host"]; out = []; off = 0; total = None
    s = requests.Session(); s.headers.update({"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    while True:
        body = {"filter": {}, "pagination": {"max": 50, "offset": off}, "sorter": {"sort": "createdDate", "order": "desc"}, "exactMatch": True}
        d = s.post(f"https://{host}/talention/api/3.2/job", json=body, timeout=40).json()
        res = d.get("results") or []; total = total or d.get("total") or d.get("totalCount") or len(res)
        for j in res:
            title = j.get("title") or ""
            if classify_role(title, "")[0] == "nicht_pflege": continue
            from ..classify import norm_text
            loc = j.get("location") or ""
            m = re.match(r"(\d{5})\s+(.+)", loc); plz, city = (m.group(1), m.group(2)) if m else (None, None)
            if not city:                                        # "Weiden, Bayern, Deutschland" / "Kinderklinik am Klinikum Weiden" / free text
                first = loc.split(",")[0].strip()
                if norm_text(first) in towns: city = first
                else:
                    hit = next((t for t in sorted(towns, key=len, reverse=True) if len(t) > 4 and re.search(r"\b" + re.escape(t) + r"\b", norm_text(loc))), None)
                    city = hit.title() if hit else seed.get("town")
            props = {p.get("property"): p.get("value") for p in (j.get("campaignProperties") or []) if isinstance(p, dict)}
            pub = j.get("enabledDate") or j.get("createdDate")
            pub = "-".join(reversed(pub.split("."))) if pub and re.match(r"\d\d\.\d\d\.\d{4}", pub) else None
            o = _obs(j.get("url"), title, seed["name"], city, plz, None, None, pub, (props.get("bereich") or [None])[0] if isinstance(props.get("bereich"), list) else None,
                     {"parse": "talention_api", "raw": {"id": j.get("id"), "location": j.get("location"), "props": props}}, towns, seed)
            if o["in_bavaria"] is False: continue
            if o["in_bavaria"] is None and seed.get("bavaria_only_operator", False): o["in_bavaria"] = True
            if o["in_bavaria"]: out.append(o)
        off += len(res)
        if not res or off >= (total or 0) or off > 1000: break
    log(f"{seed['name'][:34]:<34} talention {host} total {total} -> Pflege/BY {len(out)}")
    return out
