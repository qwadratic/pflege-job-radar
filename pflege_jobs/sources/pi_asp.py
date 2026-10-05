"""P&I LOGA bewerber-web (GWT) adapter -- used by Helios (pi-asp.de), Sana Oberfranken/Regiomed
(logaallin.regiomed-kliniken.de), BRK München and Klinikum Ingolstadt (wirkzvin.pi-asp.de). Seed:
{name, host, companyEid, param?, pin_line?, ad_on_page?, default: {kez, town, plz?, employer?},
sites: {regex-on-"title pin-line": {kez, town, plz?, employer?}}}. A site with "kez": null is a unit
the board lists that is no registry site; "pin_line": "unit" says the pin line names an org unit,
not a town (both TASK-178, see crawl()); "ad_on_page": false says the tenant's position page is the
application form only, so no text is read from it (TASK-184: Helios, see below).

Live re-verification 2026-09-10 (TASK-39), each finding reproduced directly against the real boards:

- Every posting is a <tbody> the LIST already renders in full: title, a second line (Helios: a
  free-text internal reference like "OA Gyn", not a taxonomy; regiomed: a real department taxonomy
  like "Aerztlicher Dienst"/"Pflegedienst"), and a location line (pin icon + city, sometimes also a
  calendar icon + date). Reading that DOM is exposed for every row and far more reliable than the
  previous title/body regex guess against `seed["sites"]`.
- A title click opens the position in one of two ways, and crawl() reads whichever happens
  (live 2026-10-01, TASK-184: regiomed 77/77 and wirkzvin 62/62 rows popup, Helios 94/94 and BRK München 20/20 form):
  - regiomed and wirkzvin: the click window.open()s a POPUP, ".../bewerber-web?company=*-FIRMA-ID&...
    #position,id=<uuid>,popup=y", and leaves this window's URL alone. The popup is the ad: rich-text
    blocks, then the application form. TASK-39 watched only this window's URL, saw "nothing" and
    concluded the regiomed click is dead (so did the later wirkzvin seed); both boards stored no text
    and a '#title=<slug>' ref, which merged two vacancies sharing a title into one posting. The
    position id in the popup URL is the stable ref; the description is what the popup shows above its
    application form (AD_JS), read once the form has rendered -- before that the page holds only its
    header line.
  - Helios and BRK München: the click navigates this window to the position ('#position,id=<uuid>'),
    then the page is taken off again (history back). BRK's page is the same ad-then-form page a popup
    shows, read the same way. Helios's is the application form ONLY (the ad is on Helios's own site):
    its seeds say "ad_on_page": false, no text is read, the row keeps its position id and no description --
    the stored "description" of these rows used to be the form's labels (and, for 4 Helios rows, a 79-char
    header line read before the form had rendered).
  A click that opens neither is no "dead board" and ends nothing early: that row has no stable ref, is
  not stored, and is counted in the stats' "error" (every row is still tried).
- employmentType (Vollzeit/Teilzeit) is not exposed anywhere checked: 0 of 43+90 sampled list rows
  carry a type icon, and the application-form text never mentions it either. Cross-board source gap,
  also flagged for the Oracle phase.

department_raw carries the source's own second line unlabelled -- vendor field names never become
our labels, so Helios's free-text codes and regiomed's real taxonomy both land there as-is, with no
attempt to remap either into our own section/role taxonomy.
"""
import json, re
from datetime import datetime, timezone
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm, enrich_description, fuzzy_key, qualification_hint)
from .career_crawl import in_bavaria
from .. import config as C
from .career_crawl import UA, _strip

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
_DATE_RX = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
# Each posting's title label (tr[0] of its <tbody>). One selector for both the list read and the
# click targets in crawl(), so list row i is click target i by construction.
TITLE_LABEL = "tbody > tr:first-child > td > div.LG-Label"
# A position page is rich-text blocks (the ad) followed by the application form. The form is the last thing
# the page renders: once one of its controls exists the ad above it is complete. The ad is the text of every
# block above the first one holding a control. (Not "above the first .LG-BoxPanel": BRK München puts empty
# boxes between its ad blocks.)
FORM_CONTROL = "input, select, textarea"
FORM_WAIT = f".BW-WebPositionPage :is({FORM_CONTROL})"
POSITION_ID = re.compile(r"position,id=([0-9a-f\-]{20,})")
AD_JS = r"""(controls) => {
    const ad = [];
    for (const el of document.querySelector('.BW-WebPositionPage').children) {
        if (el.matches(controls) || el.querySelector(controls)) break;
        ad.push(el.innerText);
    }
    return ad.join('\n');
}"""


def _parse_pi_date(raw):
    m = _DATE_RX.match((raw or "").strip())
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _list_rows(pg):
    """Title/department-line/city/date for every posting, straight from the rendered list -- see
    module docstring. One <tbody> per posting: tr[0]=title, tr[1]=dept line, tr[2]=pin(+cal) line.

    Every such row is read. TASK-178: a "(m/w/d) in the title" test here skipped rows as if they
    were not postings -- 38 of the 65 on the Klinikum Ingolstadt board (Flexpool, Famulatur,
    Hospitation, Praktisches Jahr, ...) and 3 of 18 on BRK München. On all 6 seeded boards every
    such <tbody> sits in the vendor's own clickable posting row (div.B3-Web-Responsive-Row,
    role=button): 255 rows, no non-posting one (live 2026-09-29)."""
    return pg.evaluate(r"""(sel) => Array.from(document.querySelectorAll(sel)).map(el => {
        const trs = Array.from(el.closest('tbody').children);
        const locRow = trs[2] || null;
        let city = null, date = null;
        if (locRow) {
            const pin = locRow.querySelector('[data-uin="ic-pinlocation"]');
            const cal = locRow.querySelector('[data-uin="ic-calendaralt"]');
            if (pin && pin.closest('.items')) city = (pin.closest('.items').querySelector('.LG-Label') || {}).innerText || null;
            if (cal && cal.closest('.items')) date = (cal.closest('.items').querySelector('.LG-Label') || {}).innerText || null;
        }
        return {title: trs[0].innerText.trim(), dept: trs[1] ? trs[1].innerText.trim() : null, city, date};
    })""", TITLE_LABEL)


def _open_position(pg, ctx, i, board_url, save, read_ad):
    """Click list row i and read the position page it opens -> (position id, ad text or None). The ad is not read
    when the seed says the tenant's position page holds none ("ad_on_page": false). See the module docstring for
    the two ways a board opens the page. Raises when the click opens no position page or its form never renders."""
    from playwright.sync_api import TimeoutError as PWTimeout
    it = pg.locator(TITLE_LABEL).nth(i)
    it.evaluate("el => el.scrollIntoView({block: 'center'})")
    try:
        with ctx.expect_page(timeout=3000) as opened:     # a popup board opens the position within ~0.3 s
            it.click(timeout=6000)
        pop = opened.value
    except PWTimeout:
        pop = None
    if pop:
        try:
            pop.wait_for_selector(FORM_WAIT, state="attached", timeout=15000)
            pm = POSITION_ID.search(pop.url)
            if not pm:
                raise RuntimeError(f"the popup is no position page ({pop.url[-60:]})")
            ad = pop.evaluate(AD_JS, FORM_CONTROL) if read_ad else None
            save(board_url, f"{board_url}#position,id={pm.group(1)}", pop.content(), 200, "text/html")
        finally:
            pop.close()
        return pm.group(1), ad
    pm = POSITION_ID.search(pg.url)
    if not pm:
        raise RuntimeError(f"the click opened no position page (this window: ...{pg.url[-40:]})")
    try:
        if read_ad:
            pg.wait_for_selector(FORM_WAIT, state="attached", timeout=15000)
            ad = pg.evaluate(AD_JS, FORM_CONTROL)
        else:
            ad = None
            try: pg.wait_for_load_state("networkidle", timeout=6000)
            except Exception: pass
        save(board_url, f"{board_url}#position,id={pm.group(1)}", pg.content(), 200, "text/html")
    finally:
        pg.go_back(); pg.wait_for_timeout(1200)  # only real navigations push history
        try: pg.wait_for_load_state("networkidle", timeout=6000)
        except Exception: pass
    return pm.group(1), ad


def crawl(seed, towns, log=print):
    from playwright.sync_api import sync_playwright
    from tests.adapter_contract import save
    # TASK-117: brkm.pi-asp.de (BRK München) renders 0 rows through the query param every other seeded
    # tenant uses ("?companyEid=...") -- live-verified 2026-09-23 it needs "?company=..." instead (same
    # P&I product, this tenant's deployment just names the param differently). Optional per-seed
    # override, defaulting to the param every existing seed (Helios, Regiomed) already relies on.
    param = seed.get("param", "companyEid")
    url = f"https://{seed['host']}/bewerber-web/?{param}={seed['companyEid']}"
    rows, failed, stats = [], [], {"listed": 0, "opened": 0, "pflege": 0}
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
        for i, m in enumerate(meta):
            title = m["title"]
            try:
                pid, ad = _open_position(pg, ctx, i, url, save, seed.get("ad_on_page", True))
            except Exception as e:
                # no position id = no stable ref to store the row under; the board's error says so
                log(f"  position page of '{title[:50]}' did not open: {str(e)[:80]}")
                failed.append(title)
                continue
            stats["opened"] += 1
            site = seed.get("default", {})
            for rx, s in (seed.get("sites") or {}).items():
                if re.search(rx, title + " " + (m["city"] or ""), re.I): site = s; break
            emp = site.get("employer") or seed["name"]
            # TASK-178: wirkzvin.pi-asp.de (Klinikum Ingolstadt) puts the org unit behind the pin icon
            # ("Zentral OP PO40", "Alten- und Pflegeheim"), never a town -- its seed says so with
            # "pin_line": "unit": the town comes from the seed site, the unit stays in the payload.
            unit, pin = (m["city"], None) if seed.get("pin_line") == "unit" else (None, m["city"])
            city = pin.split(",")[0].strip() if pin else site.get("town")
            plz = site.get("plz")
            desc = _strip(ad)[:20000] if ad else None
            role, rule = classify_role(title, "", desc=desc)
            enr = {("enr_" + k): v for k, v in enrich_description(desc or "").items()}
            e_class, e_rule = classify_employer(emp)
            ref = f"{url}#position,id={pid}"
            now = datetime.now(timezone.utc).isoformat()
            rows.append({
                "source_id": SOURCE_ID, "source_ref": ref, "source_url": ref, "observed_at": now, "title": title,
                "employer_name": emp, "employer_name_norm": employer_norm(emp), "employer_class": e_class, "employer_class_rule": e_rule,
                "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None, "alle_berufe": [],
                "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, "", desc), "department_hint": department_hint(title, desc), "department_raw": m["dept"],
                "city": city, "plz": plz, "region": None, "lat": None, "lon": None,
                "in_bavaria": in_bavaria(city, plz, None, towns), "n_locations": 1,
                "locations": json.dumps([{"adresse": {"ort": city, "plz": plz}}], ensure_ascii=False),
                "employment_types": [t for t, k in (("vollzeit", "vollzeit"), ("teilzeit", "teilzeit")) if k in (desc or "").lower()],
                "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": "UNBEFRISTET" if "unbefristet" in (desc or "").lower() else None,
                "fixed_term_months": None, "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
                "first_published": _parse_pi_date(m["date"]), "last_modified": None, "valid_until": None, "external_url": ref, "description": desc, **enr,
                "details_fetched_at": now if desc else None, "details_error": None,
                "fuzzy_key": fuzzy_key(title, emp, city), "content_hash": content_hash(title, emp, city, (desc or "")[:200]),
                "payload": json.dumps({"crawl": {"seed": url, "kez": site.get("kez"), "parse": "pi_asp_browser"}, "pi": {"companyEid": seed["companyEid"], "position_id": pid, "unit": unit}}, ensure_ascii=False),
                "_kez": site.get("kez"),
                # A seed site with "kez": null is no registry site (TASK-178: Alten- und Pflegeheim Klinikum
                # Ingolstadt GmbH): an empty board pool, so the drain's board fallback cannot file its
                # postings under the board's only clinic. app/crawl.py keeps an adapter's own pool.
                **({} if site.get("kez") else {"_board": []}),
            })
            stats["pflege"] += 1
        b.close()
    if failed:
        stats["error"] = (f"position page did not open for {len(failed)} of {stats['listed']} listed rows, "
                          f"not stored (no position id): " + "; ".join(t[:60] for t in failed[:3]))
    log(f"{seed['name'][:34]:<34} companyEid {seed['companyEid']} listed {stats['listed']} opened {stats['opened']} -> Pflege {stats['pflege']}")
    return rows, stats
