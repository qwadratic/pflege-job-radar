"""Klinikum Passau (clinic_id 26201, 660 beds): a bespoke TYPO3 extension
(typo3conf/ext/klinikumpassau_joboffers) renders every open posting fully inline on one listing
page -- no JSON-LD, no per-job detail page with real content (the "detail" link is just
?jobid=<id>&type=2500 back onto the same listing), and every posting also links an official PDF
Stellenausschreibung as a download attachment. Confirmed live 2026-09-11: crawl_wp_jobs's generic
JOB_PATH/JSON-LD scan finds none of this, because the real title+description sit as plain text
inside <span class="vacancy-header">/<div class="vacancy-description"> on the listing page itself
-- there is nothing to fetch beyond that one page, and no PDF needs opening.

  fingerprint  typo3conf/ext/klinikumpassau_joboffers in the page (unique to this one tenant --
               not wired into crawl_wp_jobs's generic delegation, this clinic gets its own ats_type)
  structure    <h2 class="job_group_headline">DEPARTMENT</h2> groups a <ul class="job_group">; each
               <li class="job_<id>"> holds <span class="vacancy-header">TITLE<a class=
               "vacancy-externalurl" href="PERMALINK">Copy to Clipboard</a></span> then
               <div class="vacancy-description">HTML BODY</div>, optionally <span class=
               "vacancy-from">Stelle frei ab:DATE</span> and one or more <div class="vacancy-file">
               PDF attachment links.
"""
import re
from .career_crawl import in_bavaria
from ..classify import employer_norm, classify_role, classify_employer, department_hint, qualification_hint, \
    enrich_description, fuzzy_key, content_hash
from .. import config as C
from datetime import datetime, timezone

FINGERPRINT_RX = re.compile(r"klinikumpassau_joboffers", re.I)
GROUP_RX = re.compile(r'<h2 class="job_group_headline[^"]*"[^>]*>\s*(.*?)\s*</h2>', re.S)
JOB_START_RX = re.compile(r'<li class="job_(\d+)">')
HEADER_RX = re.compile(r'<span class="vacancy-header\s*">(.*?)</span>', re.S)
PERMALINK_RX = re.compile(r'<a class="vacancy-externalurl" href="([^"]+)"', re.S)
ANCHOR_STRIP_RX = re.compile(r'<a class="vacancy-externalurl".*?</a>', re.S)
DESC_RX = re.compile(r'<div class="vacancy-description">(.*?)(?=<span class="vacancy-from"|<div class="vacancy-file"|<a class="vacancy-btn")', re.S)
FROM_RX = re.compile(r'<span class="vacancy-from">.*?frei ab:</span>\s*(\d{2})\.(\d{2})\.(\d{4})', re.S)
STRIP_TAG_RX = re.compile(r"<[^>]+>")


def _txt(html):
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(STRIP_TAG_RX.sub(" ", html or ""))).strip() or None


def is_klinikum_passau(resp):
    return bool(resp and resp.ok and FINGERPRINT_RX.search(resp.text))


def _parse(html, page_url):
    """-> list of {jobid, title, description, department, url, valid_from}."""
    groups = [(m.start(), _txt(m.group(1))) for m in GROUP_RX.finditer(html)]
    jobs = []
    for m in JOB_START_RX.finditer(html):
        jobid, start = m.group(1), m.start()
        dept = next((g for pos, g in reversed(groups) if pos <= start), None)
        block = html[start:start + 8000]  # one posting's own markup never runs longer than this
        hm = HEADER_RX.search(block)
        if not hm:
            continue
        title = _txt(ANCHOR_STRIP_RX.sub("", hm.group(1)))
        if not title:
            continue
        dm = DESC_RX.search(block)
        description = _txt(dm.group(1)) if dm else None
        pm = PERMALINK_RX.search(block)
        url = pm.group(1).replace("&amp;", "&") if pm else f"{page_url}?jobid={jobid}&type=2500"
        fm = FROM_RX.search(block)
        valid_from = f"{fm.group(3)}-{fm.group(2)}-{fm.group(1)}" if fm else None
        jobs.append({"jobid": jobid, "title": title, "description": description,
                     "department": dept, "url": url, "valid_from": valid_from})
    return jobs


def crawl(c, towns, log=None):
    """c: clinic dict (registry row). Returns (observations, stats) -- same (obs, stats) shape as
    app.crawl._seed_obs's other seeded-kind branches (bite.crawl, ats_seeds builders, ...)."""
    import requests
    from .career_crawl import UA
    url = (c.get("careers_url") or "").strip()
    if not url:
        return [], {"error": "no careers_url"}
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if not r.ok:
        return [], {"error": f"HTTP {r.status_code}"}
    jobs = _parse(r.text, url)
    now = datetime.now(timezone.utc).isoformat()
    town = c.get("town")
    obs = []
    for j in jobs:
        title, desc = j["title"], j["description"] or ""
        e_class, e_rule = classify_employer(c["name"])
        section_confirmed = bool(j["department"] and "pflege" in j["department"].lower())
        role, rule = classify_role(title, "", nursing_section_confirmed=section_confirmed)
        enr = {("enr_" + k): v for k, v in enrich_description(desc).items()}
        obs.append({
            "source_id": C.SOURCES["employer_ats"]["source_id"], "source_ref": j["url"], "source_url": j["url"], "observed_at": now,
            "title": title, "employer_name": c["name"], "employer_name_norm": employer_norm(c["name"]),
            "employer_class": e_class, "employer_class_rule": e_rule,
            "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT",
            "hauptberuf": None, "alle_berufe": [],
            "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""),
            "department_hint": j["department"] or department_hint(title), "department_raw": j["department"],
            "city": town, "plz": None, "region": "BAYERN", "lat": None, "lon": None,
            "in_bavaria": in_bavaria(town, None, "BAYERN", towns), "n_locations": 1,
            "locations": "[]", "employment_types": [], "shift_night_weekend": None, "homeoffice": None,
            "quereinstieg": None, "contract": None, "fixed_term_months": None,
            "start_date": j["valid_from"], "salary_min": None, "salary_max": None, "salary_unit": None,
            "salary_note": None, "first_published": None, "last_modified": None, "valid_until": None,
            "external_url": j["url"], "description": desc[:20000] or None, **enr,
            "details_fetched_at": now, "details_error": None,
            "fuzzy_key": fuzzy_key(title, c["name"], town), "content_hash": content_hash(title, c["name"], town, desc[:200]),
            "_board": [c["clinic_id"]],
            "payload": "{}",
        })
    return obs, {"total": len(jobs)}
