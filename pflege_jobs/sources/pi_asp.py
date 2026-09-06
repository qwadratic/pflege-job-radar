"""P&I LOGA bewerber-web (GWT) adapter — used by Helios (pi-asp.de). RPC is encrypted, the host is not walled:
render the company list (#positions), click each entry to obtain the SPA hash (#position,id=<uuid>) and the detail text.
Seed: {name, host, companyEid, sites: {regex-on-title-or-text: {kez, town}}, default: {kez, town}}
"""
import json, re, time
from datetime import datetime, timezone
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm, enrich_description, fuzzy_key, qualification_hint)
from .. import config as C
from .career_crawl import UA, _strip

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
GM = re.compile(r"\((?:m|w|d)/(?:m|w|d)/(?:m|w|d)\)", re.I)


def crawl(seed, towns, max_items=120, log=print):
    from playwright.sync_api import sync_playwright
    url = f"https://{seed['host']}/bewerber-web/?companyEid={seed['companyEid']}"
    rows, stats = [], {"listed": 0, "opened": 0, "pflege": 0}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(user_agent=UA, ignore_https_errors=True, locale="de-DE", viewport={"width": 1280, "height": 2400})
        pg = ctx.new_page()
        pg.goto(url, wait_until="networkidle", timeout=60000); pg.wait_for_timeout(3000)
        for _ in range(5):
            pg.mouse.wheel(0, 4000); pg.wait_for_timeout(500)
        items = pg.locator("text=/\\((m|w|d)\\/(m|w|d)\\/(m|w|d)\\)/i")
        n = min(items.count(), max_items); stats["listed"] = items.count()
        titles = [items.nth(i).inner_text().strip() for i in range(n)]
        for i, title in enumerate(titles):
            if classify_role(title, "")[0] == "nicht_pflege":
                continue
            try:
                items.nth(i).click(timeout=4000); pg.wait_for_timeout(1500)
                try: pg.wait_for_load_state("networkidle", timeout=6000)
                except Exception: pass
                hash_ = pg.url.split("#", 1)[1] if "#" in pg.url else ""
                m = re.search(r"id=([0-9a-f\-]{20,})", hash_)
                pid = m.group(1) if m else None
                body = pg.inner_text("body")
                stats["opened"] += 1
                pg.go_back(); pg.wait_for_timeout(1200)
                try: pg.wait_for_load_state("networkidle", timeout=6000)
                except Exception: pass
                items = pg.locator("text=/\\((m|w|d)\\/(m|w|d)\\/(m|w|d)\\)/i")
            except Exception as e:
                log(f"  click failed for '{title[:50]}': {str(e)[:60]}"); body, pid = "", None
            site = seed.get("default", {})
            for rx, s in (seed.get("sites") or {}).items():
                if re.search(rx, title + " " + body[:3000], re.I): site = s; break
            emp = site.get("employer") or seed["name"]
            city, plz = site.get("town"), site.get("plz")
            desc = _strip(body)[:20000] if body else None
            role, rule = classify_role(title, "")
            enr = {("enr_" + k): v for k, v in enrich_description(desc or "").items()}
            e_class, e_rule = classify_employer(emp)
            ref = f"{url}#position,id={pid}" if pid else f"{url}#title={re.sub(r'[^a-z0-9]+','-',title.lower())[:80]}"
            now = datetime.now(timezone.utc).isoformat()
            rows.append({
                "source_id": SOURCE_ID, "source_ref": ref, "source_url": ref, "observed_at": now, "title": title,
                "employer_name": emp, "employer_name_norm": employer_norm(emp), "employer_class": e_class, "employer_class_rule": e_rule,
                "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None, "alle_berufe": [],
                "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(title), "department_raw": None,
                "city": city, "plz": plz, "region": "BAYERN", "lat": None, "lon": None, "in_bavaria": True, "n_locations": 1,
                "locations": json.dumps([{"adresse": {"ort": city, "plz": plz}}], ensure_ascii=False),
                "employment_types": [t for t, k in (("vollzeit", "vollzeit"), ("teilzeit", "teilzeit")) if k in (desc or "").lower()],
                "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": "UNBEFRISTET" if "unbefristet" in (desc or "").lower() else None,
                "fixed_term_months": None, "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
                "first_published": None, "last_modified": None, "valid_until": None, "external_url": ref, "description": desc, **enr,
                "details_fetched_at": now if desc else None, "details_error": None,
                "fuzzy_key": fuzzy_key(title, emp, city), "content_hash": content_hash(title, emp, city, (desc or "")[:200]),
                "payload": json.dumps({"crawl": {"seed": url, "kez": site.get("kez"), "parse": "pi_asp_browser"}, "pi": {"companyEid": seed["companyEid"], "position_id": pid}}, ensure_ascii=False),
                "_kez": site.get("kez"),
            })
            stats["pflege"] += 1
        b.close()
    log(f"{seed['name'][:34]:<34} companyEid {seed['companyEid']} listed {stats['listed']} opened {stats['opened']} -> Pflege {stats['pflege']}")
    return rows, stats
