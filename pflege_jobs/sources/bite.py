"""B-ITE (BITE GmbH) Jobs-API widget adapter — source employer_ats.

Recipe (decoded from static.b-ite.com/jobs-api/loader-v1 + api-v5):
  1. Detect on any career page: <script src="…static.b-ite.com/jobs-api/loader-v1/…"> (often wrapped by a consent manager as
     data-ccm-loader-src / type="text/x-ccm-loader") and/or an element with data-bite-jobs-api-listing="{customer}:{listing}".
  2. Fetch the customer listing bundle: https://cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js — it embeds key:"<40 hex>".
  3. POST https://jobs.b-ite.com/api/v1/postings/search  {"key": key, "locale": "de", "page": {"num": 1000}}  -> jobPostings[]
     (title, url, applyUrl, startsOn/endsOn/createdOn/modifiedOn, address{city, postCode, latitude, longitude},
      employmentType[], custom.berufsgruppe[], custom.befristung, custom.umfang, custom.einstiegsdatum).
  4. Description: GET <url>/raw (HTML) — optional, needed for housing/tariff/requirements enrichment.
No consent, no browser, no HTML scraping of the clinic site itself.
"""
import json
import re
from datetime import datetime, timezone

import requests

from .. import config as C
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm,
                        enrich_description, fuzzy_key, qualification_hint)
from .career_crawl import _strip, in_bavaria

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 pflege-jobs-crawler"
FINGERPRINT = re.compile(r"static\.b-ite\.com/jobs-api|data-bite-jobs-api-listing|jobs\.b-ite\.com|cs-assets\.b-ite\.com", re.I)
LISTING_ATTR = re.compile(r'data-bite-jobs-api-listing=["\']([^"\':#]+):([^"\'#]+)', re.I)
BERUF_PFLEGE = re.compile(r"pflege|nursing", re.I)
BERUF_AUSB = re.compile(r"ausbildung|studium|praktik", re.I)


def detect(html: str):
    """-> {'customer':..., 'listing':...} if the page embeds the B-ITE widget, else None (True if fingerprint but no attrs)."""
    if not html or not FINGERPRINT.search(html):
        return None
    m = LISTING_ATTR.search(html)
    return {"customer": m.group(1), "listing": m.group(2)} if m else {"customer": None, "listing": None}


def api_key(customer: str, listing: str, session=None):
    s = session or requests.Session()
    r = s.get(f"https://cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js", headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    m = re.search(r'key:"([0-9a-f]{40})"', r.text) or re.search(r'\b([0-9a-f]{40})\b', r.text)
    if not m:
        raise RuntimeError("no API key in listing bundle" + (" (niiid recruiting-assistant variant)" if "niiid" in r.text else ""))
    return m.group(1)


def search(key: str, session=None, locale="de", page_num=1000):
    s = session or requests.Session()
    r = s.post("https://jobs.b-ite.com/api/v1/postings/search", headers={"User-Agent": UA, "Content-Type": "application/json"},
               json={"key": key, "locale": locale, "page": {"num": page_num}}, timeout=60)
    r.raise_for_status()
    return r.json()


def posting_html(url: str, session=None):
    s = session or requests.Session()
    try:
        r = s.get(url.rstrip("/") + "/raw", headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 200 and len(r.text) > 200:
            return r.text
        r = s.get(url, headers={"User-Agent": UA}, timeout=30)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def to_observation(jp: dict, seed: dict, towns, desc_html=None) -> dict:
    a = jp.get("address") or {}
    emp = (jp.get("employer") or {}).get("name") or seed.get("name") or ""   # API employer first: listings can span many facilities
    e_class, e_rule = classify_employer(emp)
    title = jp.get("title") or ""
    bg = (jp.get("custom") or {}).get("berufsgruppe") or []
    role, rule = classify_role(title, "", "AUSBILDUNG" if any(BERUF_AUSB.search(x) for x in bg) else "")
    desc = _strip(desc_html) if desc_html else None
    enr = {("enr_" + k): v for k, v in enrich_description(desc or "").items()}
    et = jp.get("employmentType") or []
    befr = (jp.get("custom") or {}).get("befristung")
    obs = {
        "source_id": SOURCE_ID, "source_ref": jp["url"], "source_url": jp["url"], "observed_at": datetime.now(timezone.utc).isoformat(),
        "title": title, "employer_name": emp, "employer_name_norm": employer_norm(emp),
        "employer_class": e_class, "employer_class_rule": e_rule,   # clinic attribution happens via registry match, not the seed
        "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None,
        "alle_berufe": [x for x in bg if isinstance(x, str)],
        "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""),
        "department_hint": department_hint(f"{title} {(jp.get('custom') or {}).get('untertitel') or ''}"),
        "department_raw": (jp.get("custom") or {}).get("untertitel") or None,
        "city": a.get("city"), "plz": a.get("postCode"), "region": "BAYERN", "lat": a.get("latitude"), "lon": a.get("longitude"),
        "in_bavaria": in_bavaria(a.get("city"), a.get("postCode"), None, towns),
        "n_locations": 1, "locations": json.dumps([{"adresse": {"ort": a.get("city"), "plz": a.get("postCode")}}], ensure_ascii=False),
        "employment_types": [t for t, k in (("vollzeit", "full_time"), ("teilzeit", "part_time"), ("minijob", "mini")) if k in et],
        "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None,
        "contract": {"01": "BEFRISTET", "02": "UNBEFRISTET"}.get(befr[0] if isinstance(befr, list) and befr else befr),
        "fixed_term_months": None, "start_date": None,
        "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": (jp.get("custom") or {}).get("umfang"),
        "first_published": (jp.get("activatedOn") or jp.get("startsOn") or jp.get("createdOn") or "")[:10] or None,
        "last_modified": jp.get("modifiedOn"), "valid_until": (jp.get("endsOn") or "")[:10] or None,
        "external_url": jp.get("applyUrl") or jp["url"], "description": (desc or "")[:20000] or None,
        **enr, "details_fetched_at": datetime.now(timezone.utc).isoformat() if desc else None, "details_error": None,
        "fuzzy_key": fuzzy_key(title, emp, a.get("city")), "content_hash": content_hash(title, emp, a.get("city"), jp.get("modifiedOn")),
        "payload": json.dumps({"bite": {k: v for k, v in jp.items() if k not in ("custom",)}, "bite_custom": {k: v for k, v in (jp.get("custom") or {}).items() if k != "keyfacts_renderer"},
                               "crawl": {"seed": seed.get("career"), "kez": seed.get("kez"), "parse": "bite_api"}}, ensure_ascii=False),
    }
    return obs


def crawl(seed: dict, towns, with_descriptions=True, log=print):
    """seed: {name, kez, career (page with the widget), customer?, listing?}"""
    s = requests.Session()
    cust, lst = seed.get("customer"), seed.get("listing")
    if not cust:
        r = s.get(seed["career"], headers={"User-Agent": UA}, timeout=40)
        d = detect(r.text)
        if not d or not d.get("customer"):
            return [], {"error": "no B-ITE listing attribute on page"}
        cust, lst = d["customer"], d["listing"]
    key = api_key(cust, lst, s)
    data = search(key, s)
    jps = data.get("jobPostings") or []
    out, stats = [], {"customer": cust, "listing": lst, "total": len(jps), "pflege": 0, "non_bavaria": 0}
    for jp in jps:
        o = to_observation(jp, seed, towns, None)                       # cheap pass: classify + locate first
        if o["role_class"] == "nicht_pflege":
            continue
        if o["in_bavaria"] is False:
            stats["non_bavaria"] += 1; continue
        if o["in_bavaria"] is None and seed.get("bavaria_only_operator", False):   # default: need positive Bavaria evidence
            o["in_bavaria"] = True
        if o["in_bavaria"] is None:
            continue
        if with_descriptions:                                            # enrich survivors only
            html = posting_html(jp["url"], s)
            if html: o = to_observation(jp, seed, towns, html); o["in_bavaria"] = True
        stats["pflege"] += 1; out.append(o)
    log(f"{seed['name'][:36]:<36} B-ITE {cust}:{lst} total {len(jps)} -> Pflege/BY {len(out)} (non-BY {stats['non_bavaria']})")
    return out, stats
