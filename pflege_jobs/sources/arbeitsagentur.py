"""Bundesagentur für Arbeit Jobsuche API adapter.
Search: v6 (verified 2026-09-05; v4/v5 search return 403). Details: v4 (v6 details 403).
Yields `observation` dicts in the canonical shape used by all sources (see sql/001_schema.sql)."""
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from .. import config as C
from ..classify import (classify_employer, classify_role, content_hash, department_hint,
                        employer_norm, fuzzy_key, qualification_hint, enrich_description)

SOURCE_CODE = "arbeitsagentur"
SOURCE_ID = C.SOURCES[SOURCE_CODE]["source_id"]
_H = {"X-API-Key": C.AA_API_KEY, "User-Agent": "pflege-jobs-pipeline/0.1 (research; contact via repo)"}


class AAClient:
    def __init__(self, session=None, sleep=0.25, timeout=60, retries=4):
        self.s = session or requests.Session()
        self.sleep, self.timeout, self.retries = sleep, timeout, retries

    def _get(self, url, params=None):
        last = None
        for i in range(self.retries):
            try:
                r = self.s.get(url, headers=_H, params=params, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (i + 1)); continue
                r.raise_for_status()
            except (requests.RequestException, ValueError) as e:
                last = e; time.sleep(1.5 * (i + 1))
        raise RuntimeError(f"AA request failed: {url} {params} {last}")

    def search_slice(self, slice_def: dict, wo=C.AA_REGION, size=C.AA_PAGE_SIZE, max_pages=400):
        """Iterate all pages of one slice. Yields raw records (dict)."""
        params = {k: v for k, v in slice_def.items() if k != "name"}
        params.update({"wo": wo, "size": size, "pav": "false"})
        page, total = 1, None
        while page <= max_pages:
            params["page"] = page
            d = self._get(f"{C.AA_SEARCH_BASE}/jobs", params)
            if total is None:
                total = int(d.get("maxErgebnisse") or 0)
            rows = d.get("ergebnisliste") or []
            for r in rows:
                yield r
            if not rows or page * size >= total:
                break
            page += 1
            time.sleep(self.sleep)

    def count(self, slice_def: dict, wo=C.AA_REGION) -> int:
        params = {k: v for k, v in slice_def.items() if k != "name"}
        params.update({"wo": wo, "size": 1, "page": 1, "pav": "false"})
        return int(self._get(f"{C.AA_SEARCH_BASE}/jobs", params).get("maxErgebnisse") or 0)

    def details(self, refnr: str) -> dict:
        enc = base64.b64encode(refnr.encode()).decode()
        return self._get(f"{C.AA_DETAILS_BASE}/jobdetails/{enc}")


def fetch_all(client: AAClient, slices=C.AA_SLICES, log=print):
    """Union of slices deduped on referenznummer. Returns (records, slice_counts)."""
    seen, out, counts = {}, [], {}
    for sl in slices:
        n = 0
        for r in client.search_slice(sl):
            n += 1
            ref = (r.get("referenznummer") or "").strip()   # AA sometimes pads refs with whitespace
            if not ref:
                continue
            r["referenznummer"] = ref
            if ref in seen:
                seen[ref]["_slices"].append(sl["name"])
            else:
                r["_slices"] = [sl["name"]]
                seen[ref] = r
                out.append(r)
        counts[sl["name"]] = n
        log(f"slice {sl['name']}: {n} rows, unique so far {len(out)}")
    return out, counts


def _primary_location(r):
    locs = r.get("stellenlokationen") or []
    bav = [l for l in locs if (l.get("adresse") or {}).get("region") == "BAYERN"]
    return (bav[0] if bav else (locs[0] if locs else {})), bool(bav)


def _employment_types(r):
    t = []
    if r.get("arbeitszeitVollzeit"): t.append("vollzeit")
    if any(r.get(k) for k in ("arbeitszeitTeilzeitVormittag", "arbeitszeitTeilzeitNachmittag",
                              "arbeitszeitTeilzeitAbend", "arbeitszeitTeilzeitFlexibel")): t.append("teilzeit")
    if r.get("istGeringfuegigeBeschaeftigung"): t.append("minijob")
    return t


def to_observation(r: dict, observed_at=None) -> dict:
    """Map one raw AA record to the canonical observation shape."""
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    loc, in_bav = _primary_location(r)
    adr = loc.get("adresse") or {}
    firma = r.get("firma") or ""
    e_class, e_rule = classify_employer(firma)
    offer_kind = r.get("stellenangebotsart") or "ARBEIT"
    role, role_rule = classify_role(r.get("stellenangebotsTitel") or "", r.get("hauptberuf") or "", offer_kind)
    sal_unit = None
    if r.get("gehaltsspanneVon") or r.get("gehaltsspanneBis"):
        sal_unit = (r.get("artDerVerguetung") or r.get("verguetungsangabe") or "").lower() or None
    ref = r["referenznummer"].strip()
    obs = {
        "source_id": SOURCE_ID,
        "source_ref": ref,
        "source_url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}",
        "observed_at": observed_at,
        "title": r.get("stellenangebotsTitel") or r.get("hauptberuf"),   # AA rows may lack a title
        "employer_name": firma or None,
        "employer_name_norm": employer_norm(firma) if firma else None,
        "employer_class": e_class, "employer_class_rule": e_rule,
        "aa_kundennummer_hash": r.get("arbeitgeberKundennummerHash"),
        "offer_kind": offer_kind,
        "hauptberuf": r.get("hauptberuf"),
        "alle_berufe": r.get("alleBerufe") or [],
        "role_class": role, "role_rule": role_rule,
        "qualification_hint": qualification_hint(r.get("stellenangebotsTitel") or "", r.get("hauptberuf") or ""),
        "department_hint": department_hint(r.get("stellenangebotsTitel") or ""),
        "city": adr.get("ort"), "plz": adr.get("plz"), "region": adr.get("region"),
        "lat": loc.get("breite"), "lon": loc.get("laenge"),
        "in_bavaria": in_bav,
        "n_locations": len(r.get("stellenlokationen") or []),
        "locations": json.dumps(r.get("stellenlokationen") or [], ensure_ascii=False),
        "employment_types": _employment_types(r),
        "shift_night_weekend": r.get("arbeitszeitSchichtNachtWochenende"),
        "homeoffice": r.get("homeofficemoeglich"),
        "quereinstieg": r.get("quereinstiegGeeignet"),
        "contract": r.get("vertragsdauer"),
        "fixed_term_months": r.get("befristungInMonaten"),
        "start_date": (r.get("eintrittszeitraum") or {}).get("von"),
        "salary_min": r.get("gehaltsspanneVon"), "salary_max": r.get("gehaltsspanneBis"),
        "salary_unit": sal_unit, "salary_note": r.get("verguetungsangabe"),
        "first_published": r.get("datumErsteVeroeffentlichung"),
        "last_modified": r.get("aenderungsdatum"),
        "valid_until": (r.get("veroeffentlichungszeitraum") or {}).get("bis"),
        "external_url": r.get("externeURL"),
        "description": None,
        "department_raw": None,
        "fuzzy_key": fuzzy_key(r.get("stellenangebotsTitel") or r.get("hauptberuf") or "", firma, adr.get("ort")),
        "content_hash": content_hash(r.get("stellenangebotsTitel"), firma, adr.get("plz"), adr.get("ort"),
                                     r.get("aenderungsdatum"), r.get("vertragsdauer"), r.get("hauptberuf")),
        "payload": json.dumps({k: v for k, v in r.items() if k != "_slices"} | {"_slices": r.get("_slices")}, ensure_ascii=False),
    }
    return obs


def enrich_with_details(client: AAClient, observations, workers=4, log=print, only_refs=None):
    """Fetch v4 jobdetails for observations (optionally subset) and merge description + enrichment.
    Mutates observations in place; returns number enriched."""
    todo = [o for o in observations if (only_refs is None or o["source_ref"] in only_refs)]
    done = 0
    def work(o):
        try:
            d = client.details(o["source_ref"])
        except Exception as e:  # keep going; record failure
            return o, None, str(e)
        return o, d, None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, o) for o in todo]
        for f in as_completed(futs):
            o, d, err = f.result()
            if d is None:
                o["details_error"] = err; continue
            desc = d.get("stellenangebotsBeschreibung")
            o["description"] = desc
            o.update({("enr_" + k): v for k, v in enrich_description(desc or "").items()})
            o["details_fetched_at"] = datetime.now(timezone.utc).isoformat()
            done += 1
            if done % 200 == 0:
                log(f"details {done}/{len(todo)}")
    return done
