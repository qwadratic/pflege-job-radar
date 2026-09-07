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
from .. import section
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm,
                        enrich_description, fuzzy_key, qualification_hint)
from .career_crawl import _strip, in_bavaria

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 pflege-jobs-crawler"
FINGERPRINT = re.compile(r"static\.b-ite\.com/jobs-api|data-bite-jobs-api-listing|jobs\.b-ite\.com|cs-assets\.b-ite\.com", re.I)
LISTING_ATTR = re.compile(r'data-bite-jobs-api-listing=["\']([^"\':#]+):([^"\'#]+)', re.I)
BERUF_AUSB = re.compile(r"ausbildung|studium|praktik", re.I)

# Section-first signal (see pflege_jobs/section.py): a b-ite tenant's postings carry an arbitrary bag
# of custom.<field> values, and -- on tenants that use it -- one of those fields is a department/area
# taxonomy that includes a nursing bucket. The field NAME is tenant-specific and not documented (real
# tenants surveyed 2026-09 used "custom_field1", "api_bereich"; others used none at all), so rather than
# hard-coding a field name we scan every custom field returned across a tenant's own postings and treat
# one as a taxonomy candidate only if its values are short and repeat across postings (a real department
# enum), not a long, near-unique-per-job field (a freeform description/benefits blob that might just
# happen to mention "Pflege" in prose). This mirrors the "mine the nursing section first" contract:
# postings tagged with the matched label are the candidate set that gets classified/enriched; if no
# tenant field looks like a taxonomy at all, or none of its values name the nursing section, every
# posting is still classified exactly as before (unchanged fallback).
_TAXONOMY_MAX_LABEL_LEN = 60
_TAXONOMY_MIN_DISTINCT = 3   # a real department/area enum has several buckets, not just one or two --
                             # guards against a small board coincidentally having a low-cardinality
                             # non-taxonomy field (see _TAXONOMY_NAME_DENYLIST below for a real example)
# Field-name patterns seen on real b-ite tenants that are short/repeated (so would otherwise pass the
# shape heuristic below) but are NOT a department/category taxonomy -- e.g. "vorschaubild" (thumbnail
# image slug) on krankenhaus-naturheilweisen.de takes the value "pflegekraft_blutdruckmessung" (a stock
# photo of a nurse) on postings spanning several unrelated departments (Physiotherapeut, Praktikant),
# while the genuine nursing "Stationsleitung" posting there carries no vorschaubild value at all.
_TAXONOMY_NAME_DENYLIST = re.compile(r"bild|foto|photo|image|picture|video|icon", re.I)


def _taxonomy_labels(jps, key):
    """-> flat list[str] of every custom[key] value across jps (list- or scalar-valued)."""
    labels = []
    for jp in jps:
        v = (jp.get("custom") or {}).get(key)
        if isinstance(v, str):
            labels.append(v)
        elif isinstance(v, list):
            labels.extend(x for x in v if isinstance(x, str))
    return labels


def find_nursing_taxonomy(jps):
    """Scan every custom{} field across a tenant's fetched postings for an enum-shaped (short,
    repeated) taxonomy whose values include a nursing-section label. -> (field_key, nursing_label) or
    (None, None) if no field looks like a usable taxonomy, or none of its values name nursing."""
    keys = set()
    for jp in jps:
        keys.update((jp.get("custom") or {}).keys())
    for key in sorted(keys):
        if _TAXONOMY_NAME_DENYLIST.search(key):
            continue  # e.g. "vorschaubild"/"vorschaubild_ga" -- a thumbnail-image slug, not a taxonomy
        labels = _taxonomy_labels(jps, key)
        if not labels or any(len(x) > _TAXONOMY_MAX_LABEL_LEN for x in labels):
            continue  # long values -- a freeform text field, not a category enum
        distinct = set(labels)
        if len(distinct) < _TAXONOMY_MIN_DISTINCT or len(distinct) > max(2, len(labels) * 0.5):
            continue  # too few buckets to be a real department enum, or too many to be a repeated one
        nursing_label = section.pick_nursing_category(sorted(distinct))
        if nursing_label:
            return key, nursing_label
    return None, None


def _has_label(jp, key, label):
    v = (jp.get("custom") or {}).get(key)
    if isinstance(v, list):
        return label in v
    return v == label


def detect(html: str):
    """-> list of {'customer':..., 'listing':...} candidates embedded in the page (possibly several
    data-bite-jobs-api-listing mounts on one page, e.g. a chatbot 'niiid' mount alongside the real
    listing), or [] if the page has no fingerprint/no attrs at all. A page can carry more than one
    bundle and only some resolve to a real API key (see api_key), so callers must try each in turn
    rather than trusting the first match."""
    if not html or not FINGERPRINT.search(html):
        return []
    return [{"customer": m.group(1), "listing": m.group(2)} for m in LISTING_ATTR.finditer(html)]


def api_key(customer: str, listing: str, session=None):
    """-> the 40-hex API key, or None if this bundle doesn't carry one (e.g. a chatbot/recruiting-
    assistant mount like Artemed's 'niiid' listing, which ships createClient({key:""}))."""
    s = session or requests.Session()
    try:
        r = s.get(f"https://cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js", headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    m = re.search(r'key:"([0-9a-f]{40})"', r.text) or re.search(r'\b([0-9a-f]{40})\b', r.text)
    return m.group(1) if m else None


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


def to_observation(jp: dict, seed: dict, towns, desc_html=None, section_confirmed=False) -> dict:
    a = jp.get("address") or {}
    emp = (jp.get("employer") or {}).get("name") or seed.get("name") or ""   # API employer first: listings can span many facilities
    e_class, e_rule = classify_employer(emp)
    title = jp.get("title") or ""
    bg = (jp.get("custom") or {}).get("berufsgruppe") or []
    role, rule = classify_role(title, "", "AUSBILDUNG" if any(BERUF_AUSB.search(x) for x in bg) else "",
                               nursing_section_confirmed=section_confirmed)
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
    candidates = [{"customer": cust, "listing": lst}] if cust else None
    if candidates is None:
        r = s.get(seed["career"], headers={"User-Agent": UA}, timeout=40)
        candidates = [d for d in detect(r.text) if d.get("customer")]
        if not candidates:
            return [], {"error": "no B-ITE listing attribute on page"}
    key = None
    for d in candidates:
        key = api_key(d["customer"], d["listing"], s)
        if key:
            cust, lst = d["customer"], d["listing"]
            break
    if not key:
        return [], {"error": "no B-ITE listing bundle on page carried a usable API key "
                              f"(tried {len(candidates)}: {[(d['customer'], d['listing']) for d in candidates]})"}
    data = search(key, s)
    jps = data.get("jobPostings") or []
    tax_key, nursing_label = find_nursing_taxonomy(jps)
    # Section-first (surveyed 2026-09, revised): find_nursing_taxonomy()/`_has_label` used to also
    # hard-restrict which postings got processed AT ALL -- but on two real boards (Augustinum, DONAU-
    # ISAR Landau) that dropped genuine certified-nursing postings filed under a different custom-
    # field bucket ("paedagogische_einrichtungen" containing "Pflegefachfrau (m/w/d)",
    # "medizinisch-technische-berufe" containing "Pflegefachmann/-frau für die IMC"), a real, measured
    # loss. classify_role() below is a cheap title-only check that already runs before any per-job
    # detail fetch, so there is no fetch-cost reason to pre-filter `jps` at all -- process every
    # posting, and pass each one's own label match (reusing `_has_label`, not recomputing it) as
    # classify.classify_role's nursing_section_confirmed signal instead.
    matched = sum(1 for jp in jps if tax_key and _has_label(jp, tax_key, nursing_label)) if tax_key else None
    out, stats = [], {"customer": cust, "listing": lst, "total": len(jps), "pflege": 0, "non_bavaria": 0,
                       "section_field": tax_key, "section_label": nursing_label, "section_matched": matched}
    for jp in jps:
        section_confirmed = bool(tax_key) and _has_label(jp, tax_key, nursing_label)
        o = to_observation(jp, seed, towns, None, section_confirmed=section_confirmed)  # cheap pass: classify + locate first
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
            if html: o = to_observation(jp, seed, towns, html, section_confirmed=section_confirmed); o["in_bavaria"] = True
        stats["pflege"] += 1; out.append(o)
    sec = f" section={tax_key}={nursing_label!r} matched={matched}/{len(jps)}" if tax_key else ""
    log(f"{seed['name'][:36]:<36} B-ITE {cust}:{lst} total {len(jps)}{sec} -> Pflege/BY {len(out)} (non-BY {stats['non_bavaria']})")
    return out, stats
