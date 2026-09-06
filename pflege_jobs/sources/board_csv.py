"""pflege-board.exe.xyz snapshot adapter (source `employer_ats`, precedence 2).

Input: CSV with columns clinic, city, department, job_title, qualification, pay_grade, pay_text, housing,
housing_quote, employment_type, requirements_must, experience_required, confidence, confidence_why, job_url.
Identity: job_url (source_ref). No dates in the snapshot -> first_published stays null; precedence merge
takes dates from Arbeitsagentur when the posting is linked. lat/lon: looked up from a city->coords table
built from Arbeitsagentur observations (same pipeline), so the map works for imported rows too."""
import csv
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .. import config as C
from ..classify import (classify_employer, classify_role, content_hash, department_hint,
                        employer_norm, fuzzy_key, qualification_hint)

SOURCE_CODE = "employer_ats"
SOURCE_ID = C.SOURCES[SOURCE_CODE]["source_id"]
QUAL_MAP = {"pflegefachkraft": "pflegefachkraft", "fachweiterbildung": "fachpflege", "ota_ata": "ota_ata",
            "leitung": "leitung", "hilfskraft": "pflegehelfer"}
HOUSING_MAP = {"ja": True, "nein": False}
UNASSIGNED = "— nicht zugeordnet —"


def city_coords_from(observations):
    """city -> (lat, lon) from existing observations (first seen wins)."""
    m = {}
    for o in observations:
        if o.get("city") and o.get("lat") is not None:
            m.setdefault(o["city"].strip().lower(), (o["lat"], o["lon"]))
    return m


def _employment(t):
    t = (t or "").lower()
    out = []
    if "voll" in t: out.append("vollzeit")
    if "teil" in t: out.append("teilzeit")
    if "minijob" in t or "geringf" in t: out.append("minijob")
    return out


def _grade(g):
    g = re.sub(r"\s", "", (g or "").upper())
    return g or None


def to_observation(row: dict, coords: dict, observed_at=None) -> dict:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    url = (row.get("job_url") or "").strip()
    clinic = (row.get("clinic") or "").strip()
    if not clinic or clinic == UNASSIGNED:
        clinic = urlparse(url).netloc.replace("www.", "")           # employer unknown -> domain as name
        e_class, e_rule = "unknown", "board:unassigned"
    else:
        e_class, e_rule = classify_employer(clinic)
        if e_class != "clinic":                                    # board assigned it to a clinic -> trust, record why
            e_class, e_rule = "clinic", f"board_assigned|{e_rule}"
    title = (row.get("job_title") or "").strip()
    q = (row.get("qualification") or "").strip()
    if q in QUAL_MAP:
        role, role_rule = QUAL_MAP[q], f"board:{q}"
    else:
        role, role_rule = classify_role(title, "")
    city = (row.get("city") or "").strip() or None
    lat, lon = coords.get((city or "").lower(), (None, None))
    housing = HOUSING_MAP.get((row.get("housing") or "").strip().lower())
    dept_raw = (row.get("department") or "").strip() or None
    obs = {
        "source_id": SOURCE_ID, "source_ref": url, "source_url": url, "observed_at": observed_at,
        "title": title, "employer_name": clinic, "employer_name_norm": employer_norm(clinic),
        "employer_class": e_class, "employer_class_rule": e_rule, "aa_kundennummer_hash": None,
        "offer_kind": "ARBEIT", "hauptberuf": None, "alle_berufe": [],
        "role_class": role, "role_rule": role_rule,
        "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(f"{title} {dept_raw or ''}"),
        "department_raw": dept_raw,
        "city": city, "plz": None, "region": "BAYERN" if city else None, "lat": lat, "lon": lon,
        "in_bavaria": True, "n_locations": 1 if city else 0, "locations": json.dumps([{"adresse": {"ort": city, "region": "BAYERN"}}], ensure_ascii=False),
        "employment_types": _employment(row.get("employment_type")), "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None,
        "contract": None, "fixed_term_months": None, "start_date": None,
        "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
        "first_published": None, "last_modified": None, "valid_until": None,
        "external_url": url, "description": None,
        "enr_pay_grade": _grade(row.get("pay_grade")), "enr_pay_text": (row.get("pay_text") or "").strip() or None,
        "enr_requirements": (row.get("requirements_must") or "").strip()[:600] or None,
        "enr_experience": (row.get("experience_required") or "").strip()[:120] or None,
        "enr_housing": housing, "enr_housing_evidence": (row.get("housing_quote") or "").strip()[:200] or None,
        "enr_tariff": _tariff(row.get("pay_text")), "enr_contact_emails": None, "enr_language_req": None,
        "enr_bonus": None, "enr_childcare": None, "enr_anerkennung_mentioned": None,
        "details_fetched_at": None, "details_error": None,
        "fuzzy_key": fuzzy_key(title, clinic, city),
        "content_hash": content_hash(title, clinic, city, dept_raw, row.get("pay_grade"), row.get("housing")),
        "payload": json.dumps({**row, "_source": "pflege-board.exe.xyz snapshot"}, ensure_ascii=False),
    }
    return obs


def _tariff(pay_text):
    from ..classify import enrich_description
    return enrich_description(pay_text or "").get("tariff") if pay_text else None


def load_csv(path, coords):
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)]
    seen, out = set(), []
    for r in rows:
        o = to_observation(r, coords)
        if not o["source_ref"] or o["source_ref"] in seen:
            continue
        seen.add(o["source_ref"]); out.append(o)
    return out
