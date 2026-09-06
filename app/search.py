"""Fuzzy search over clinics, jobs and cities (rapidfuzz). Typo-tolerant, accent-insensitive."""
import unicodedata

from rapidfuzz import fuzz, process

from . import data as D


def _fold(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return s.replace("ss", "s")


def _score(q, text):
    return max(fuzz.WRatio(q, text), fuzz.partial_ratio(q, text) if len(q) >= 4 else 0)


def search(q, limit=15):
    q = _fold(q).strip()
    if not q:
        return {"clinics": [], "jobs": [], "cities": []}
    snap = D.snapshot()
    clinics = []
    for c in snap["clinics"]:
        text = _fold(" ".join(str(x) for x in (c["name"], c.get("town"), c.get("operator"), c.get("landkreis"), c["clinic_id"]) if x))
        s = _score(q, text)
        if s >= 60:
            clinics.append({"clinic_id": c["clinic_id"], "name": c["name"], "town": c.get("town"), "ats_type": c.get("ats_type"),
                            "jobs_open": c["jobs_open"], "regierungsbezirk": c.get("regierungsbezirk"), "score": round(s)})
    clinics.sort(key=lambda x: (-x["score"], -x["jobs_open"]))
    jobs = []
    for j in snap["jobs"]:
        text = _fold(" ".join(str(x) for x in (j.get("title"), j.get("employer"), j.get("city"), j.get("department_raw"), j.get("clinic_name")) if x))
        s = _score(q, text)
        if s >= 65:
            jobs.append({"posting_id": j["posting_id"], "title": j.get("title"), "employer": j.get("employer"), "city": j.get("city"),
                         "clinic_id": j.get("clinic_id"), "role_class": j.get("role_class"), "fresh": j.get("fresh"), "score": round(s)})
    jobs.sort(key=lambda x: (-x["score"], not x["fresh"]))
    towns = {c["town"] for c in snap["clinics"] if c.get("town")} | {j["city"] for j in snap["jobs"] if j.get("city")}
    cities = [{"v": name, "score": round(s)} for name, s, _ in process.extract(q, {t: _fold(t) for t in towns}, scorer=fuzz.WRatio, limit=limit) if s >= 70]
    return {"clinics": clinics[:limit], "jobs": jobs[:limit * 2], "cities": cities}
