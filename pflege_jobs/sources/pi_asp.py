"""P&I LOGA bewerber-web (GWT) adapter -- used by Helios (pi-asp.de) and Sana Oberfranken/Regiomed
(logaallin.regiomed-kliniken.de). Seed: {name, host, companyEid, sites: {regex-on-title-or-city:
{kez, town}}, default: {kez, town}}

Live re-verification 2026-09-10 (TASK-39), each finding reproduced directly against the real boards:

- Every posting is a <tbody> the LIST already renders in full: title, a second line (Helios: a
  free-text internal reference like "OA Gyn", not a taxonomy; regiomed: a real department taxonomy
  like "Aerztlicher Dienst"/"Pflegedienst"), and a location line (pin icon + city, sometimes also a
  calendar icon + date). Reading that DOM is exposed for every row and far more reliable than the
  previous title/body regex guess against `seed["sites"]` -- especially for regiomed, where the
  click below never fires, so the old code's `body` used for that regex was always "".
- Clicking a title on Helios does NOT open a job description -- it jumps straight into the
  application FORM (Anrede/Vorname/Lebenslauf...); its only free text is one line ("Bewerbung auf
  die Stellenausschreibung "<title>" <ref> in <city>"). That's the closest thing to a description
  this source exposes there, and is still captured (real content, not invented).
- Clicking a title on the regiomed wildcard board (companyEid=%2a) does NOTHING -- confirmed live
  with console/network/DOM diffing: zero requests, zero DOM bytes changed, zero URL change, even
  with raw mouse coordinates and dblclick, from the item's natural on-load position (no scrolling
  needed or at fault). %2a is not a guess either: www.sana.de/karriere/coburg/ itself links to this
  exact URL. No employer-scoped companyEid exists to try instead. So regiomed rows genuinely carry
  no description -- a source gap, flagged for the Oracle phase, not papered over (the previous code
  stored the *entire list's* rendered body as every single row's "description" here, since an inert
  click leaves `body` holding whatever was already on screen -- a real bug this version fixes by
  only trusting `body` when the click actually navigated to a `#position,id=` hash).
- employmentType (Vollzeit/Teilzeit) is not exposed anywhere checked: 0 of 43+90 sampled list rows
  carry a type icon, and the application-form text never mentions it either. Cross-board source gap,
  also flagged for the Oracle phase.
- After 3 consecutive clicks land no `#position,id=` hash, the rest of that board's clicks are
  skipped (list data for every row is already captured without clicking -- see above); this is a
  same-run efficiency decision once the board has proven its click is dead, not a row cap: every
  listed posting is still returned.

department_raw carries the source's own second line unlabelled -- vendor field names never become
our labels, so Helios's free-text codes and regiomed's real taxonomy both land there as-is, with no
attempt to remap either into our own section/role taxonomy.
"""
import json, re
from datetime import datetime, timezone
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm, enrich_description, fuzzy_key, qualification_hint)
from .. import config as C
from .career_crawl import UA, _strip

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
GM = re.compile(r"\((?:m|w|d)/(?:m|w|d)/(?:m|w|d)\)", re.I)
_DATE_RX = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")


def _parse_pi_date(raw):
    m = _DATE_RX.match((raw or "").strip())
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _list_rows(pg):
    """Title/department-line/city/date for every posting, straight from the rendered list -- see
    module docstring. One <tbody> per posting: tr[0]=title, tr[1]=dept line, tr[2]=pin(+cal) line."""
    return pg.evaluate(r"""() => {
        const marker = /\((m|w|d)\/(m|w|d)\/(m|w|d)\)/i;
        const labels = Array.from(document.querySelectorAll('.LG-Label.col-style'));
        const seen = new Set(); const out = [];
        for (const el of labels) {
            const tb = el.closest('tbody');
            if (!tb || seen.has(tb)) continue;
            seen.add(tb);
            const trs = Array.from(tb.children);
            const title = trs[0] ? trs[0].innerText.trim() : '';
            if (!marker.test(title)) continue;
            const dept = trs[1] ? trs[1].innerText.trim() : null;
            const locRow = trs[2] || null;
            let city = null, date = null;
            if (locRow) {
                const pin = locRow.querySelector('[data-uin="ic-pinlocation"]');
                const cal = locRow.querySelector('[data-uin="ic-calendaralt"]');
                if (pin && pin.closest('.items')) city = (pin.closest('.items').querySelector('.LG-Label') || {}).innerText || null;
                if (cal && cal.closest('.items')) date = (cal.closest('.items').querySelector('.LG-Label') || {}).innerText || null;
            }
            out.push({title, dept, city, date});
        }
        return out;
    }""")


def crawl(seed, towns, log=print):
    from playwright.sync_api import sync_playwright
    from tests.adapter_contract import save
    url = f"https://{seed['host']}/bewerber-web/?companyEid={seed['companyEid']}"
    rows, stats = [], {"listed": 0, "opened": 0, "pflege": 0, "dead_click": False}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(user_agent=UA, ignore_https_errors=True, locale="de-DE", viewport={"width": 1280, "height": 2400})
        pg = ctx.new_page()
        pg.goto(url, wait_until="networkidle", timeout=60000); pg.wait_for_timeout(3000)
        for _ in range(5):
            pg.mouse.wheel(0, 4000); pg.wait_for_timeout(500)
        save(url, pg.url, pg.content(), 200, "text/html")
        meta = _list_rows(pg)
        stats["listed"] = len(meta)
        clickable = pg.locator("tbody > tr:first-child > td > div.LG-Label").filter(has_text=GM)
        dead_streak = 0
        for i, m in enumerate(meta):
            title = m["title"]
            body, pid = "", None
            if dead_streak < 3:
                try:
                    it = clickable.nth(i)
                    it.evaluate("el => el.scrollIntoView({block: 'center'})")
                    it.click(timeout=6000); pg.wait_for_timeout(1500)
                    try: pg.wait_for_load_state("networkidle", timeout=6000)
                    except Exception: pass
                    hash_ = pg.url.split("#", 1)[1] if "#" in pg.url else ""
                    pm = re.search(r"id=([0-9a-f\-]{20,})", hash_)
                    if pm:
                        pid = pm.group(1); dead_streak = 0
                        body = pg.inner_text("body")
                        save(url, f"{url}#{hash_}", pg.content(), 200, "text/html")
                        stats["opened"] += 1
                        pg.go_back(); pg.wait_for_timeout(1200)  # only real navigations push history
                        try: pg.wait_for_load_state("networkidle", timeout=6000)
                        except Exception: pass
                        clickable = pg.locator("tbody > tr:first-child > td > div.LG-Label").filter(has_text=GM)
                    else:
                        dead_streak += 1
                        if dead_streak == 3:
                            stats["dead_click"] = True
                            log(f"  {seed['name'][:34]}: title click opens nothing on this board "
                                f"(companyEid={seed['companyEid']}) -- confirmed dead after 3 tries, "
                                f"no description available from this source")
                except Exception as e:
                    log(f"  click failed for '{title[:50]}': {str(e)[:60]}"); dead_streak += 1
            site = seed.get("default", {})
            for rx, s in (seed.get("sites") or {}).items():
                if re.search(rx, title + " " + (m["city"] or ""), re.I): site = s; break
            emp = site.get("employer") or seed["name"]
            city = m["city"].split(",")[0].strip() if m["city"] else site.get("town")
            plz = site.get("plz")
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
                "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(title), "department_raw": m["dept"],
                "city": city, "plz": plz, "region": "BAYERN", "lat": None, "lon": None, "in_bavaria": True, "n_locations": 1,
                "locations": json.dumps([{"adresse": {"ort": city, "plz": plz}}], ensure_ascii=False),
                "employment_types": [t for t, k in (("vollzeit", "vollzeit"), ("teilzeit", "teilzeit")) if k in (desc or "").lower()],
                "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": "UNBEFRISTET" if "unbefristet" in (desc or "").lower() else None,
                "fixed_term_months": None, "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
                "first_published": _parse_pi_date(m["date"]), "last_modified": None, "valid_until": None, "external_url": ref, "description": desc, **enr,
                "details_fetched_at": now if desc else None, "details_error": None,
                "fuzzy_key": fuzzy_key(title, emp, city), "content_hash": content_hash(title, emp, city, (desc or "")[:200]),
                "payload": json.dumps({"crawl": {"seed": url, "kez": site.get("kez"), "parse": "pi_asp_browser"}, "pi": {"companyEid": seed["companyEid"], "position_id": pid}}, ensure_ascii=False),
                "_kez": site.get("kez"),
            })
            stats["pflege"] += 1
        b.close()
    log(f"{seed['name'][:34]:<34} companyEid {seed['companyEid']} listed {stats['listed']} opened {stats['opened']} -> Pflege {stats['pflege']}")
    return rows, stats
