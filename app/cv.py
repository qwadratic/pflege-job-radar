"""CV -> profile -> ranked jobs. Works without an LLM: the same regexes that classify job titles
(pflege_jobs.classify, driven by patterns.json) read the CV, plus a `cv` pattern section for experience,
languages and skill tags. An OpenAI-compatible LLM (env LLM_API_BASE) may refine the profile when reachable."""
import io
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

from . import config as A
from . import data as D

_FALLBACK_CV = {
    "experience_years": r"(\d{1,2})\s*(?:\+\s*)?(?:jahre|jahren|years?)\b",
    "languages": r"\b(deutsch|german|englisch|english)\b[^\n.]{0,40}?\b([abc][12])\b|\b([abc][12])\b[^\n.]{0,20}?\b(deutsch|german|englisch|english)\b",
    "skills": [
        {"tag": "Intensiv", "re": r"intensiv|\bicu\b|\bimc\b|intermediate care|critical care"},
        {"tag": "Anästhesie", "re": r"an[aä]sthesie|anesthe|anaesthe|narkose"},
        {"tag": "OP", "re": r"\bop\b|operationssaal|operating (room|theatre)|\bota\b|zentral-?op"},
        {"tag": "Notaufnahme", "re": r"notaufnahme|emergency|\bzna\b|\ber\b"},
        {"tag": "Dialyse", "re": r"dialyse|dialysis|nephrolog"},
        {"tag": "Onkologie", "re": r"onkolog|oncolog|chemotherap|palliativ|palliative"},
        {"tag": "Pädiatrie", "re": r"p[aä]diatr|kinderkrankenp|neonatolog|kinderklinik"},
        {"tag": "Psychiatrie", "re": r"psychiatr|psychosomat|mental health"},
        {"tag": "Geriatrie", "re": r"geriatr|altenpflege|elderly|gerontolog"},
        {"tag": "Kardiologie", "re": r"kardiolog|cardiolog|herzkatheter|\bchest pain\b"},
        {"tag": "Neurologie", "re": r"neurolog|stroke unit|schlaganfall"},
        {"tag": "Chirurgie", "re": r"chirurg|surgical|surgery|orthop[aä]d|unfallchirurg"},
        {"tag": "Innere Medizin", "re": r"innere medizin|internal medicine|internistisch"},
        {"tag": "Geburtshilfe", "re": r"geburtshilfe|hebamme|midwife|kreißsaal|kreisssaal|obstetric"},
        {"tag": "Praxisanleitung", "re": r"praxisanleit|clinical instructor|mentor"},
        {"tag": "Leitung", "re": r"stationsleit|pflegedienstleit|\bpdl\b|leitung|head nurse|nurse manager|team lead"},
        {"tag": "Beatmung", "re": r"beatmung|ventilat|weaning"},
        {"tag": "Wundmanagement", "re": r"wundmanage|wound care|wundexpert"},
        {"tag": "Endoskopie", "re": r"endoskop|endoscop"},
        {"tag": "Ambulanz", "re": r"ambulan|outpatient|tagesklinik|\bmvz\b"},
        {"tag": "Reha", "re": r"\breha\b|rehabilitation"},
        {"tag": "Hygiene", "re": r"hygiene|infection control"},
    ],
}
_QUAL = [("GuK", r"gesundheits-?\s*und\s*krankenpfleg|krankenschwester|krankenpfleger|registered nurse|\brn\b|\bbsc\.? nursing|bachelor of nursing|pflegefachfrau|pflegefachmann|pflegefachperson"),
         ("GKiK", r"kinderkrankenpfleg|pediatric nurse|paediatric nurse"),
         ("Altenpflege", r"altenpfleger|altenpflegerin|geriatric nurse"),
         ("Fachweiterbildung", r"fachweiterbildung|fachkrankenpfleg|fachpfleg|specialist nurse|critical care nurse"),
         ("Pflegehelfer", r"pflegehelfer|pflegeassist|nursing assistant|krankenpflegehelfer|\bcna\b"),
         ("Anerkennung", r"anerkennung|berufsanerkennung|recognition of (my )?(nursing )?(qualification|diploma)|kenntnisprüfung|defizitbescheid")]
_ROLE_FROM_QUAL = {"GuK": "pflegefachkraft", "GKiK": "pflegefachkraft", "Altenpflege": "pflegefachkraft", "Fachweiterbildung": "fachpflege",
                   "Pflegehelfer": "pflegehelfer"}
_SKILL_TO_DEPT = {"Intensiv": "Intensiv/IMC", "Anästhesie": "Anästhesie", "OP": "OP", "Notaufnahme": "Notaufnahme", "Dialyse": "Dialyse/Nephrologie",
                  "Onkologie": "Onkologie", "Pädiatrie": "Pädiatrie/Neonatologie", "Psychiatrie": "Psychiatrie", "Geriatrie": "Geriatrie",
                  "Kardiologie": "Kardiologie", "Neurologie": "Neurologie", "Chirurgie": "Chirurgie/Orthopädie", "Innere Medizin": "Innere Medizin",
                  "Geburtshilfe": "Geburtshilfe", "Ambulanz": "Ambulanz/Tagesklinik", "Reha": "Reha"}


def _fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def extract_text(filename, blob):
    name = (filename or "").lower()
    if name.endswith(".pdf") or blob[:5] == b"%PDF-":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(blob)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    if name.endswith(".docx") or blob[:2] == b"PK":
        import docx
        d = docx.Document(io.BytesIO(blob))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    for enc in ("utf-8", "latin-1"):
        try:
            return blob.decode(enc)
        except Exception:
            continue
    return ""


def _cv_patterns():
    try:
        from pflege_jobs import config as C
        cv = (getattr(C, "PATTERNS", None) or {}).get("cv") or {}
    except Exception:
        cv = {}
    return {**_FALLBACK_CV, **{k: v for k, v in cv.items() if v}}


def profile_from_text(text):
    from pflege_jobs.classify import classify_role, department_hint, norm_text
    pats = _cv_patterns()
    low = text.lower()
    prof = {"roles": [], "departments": [], "qualifications": [], "cities": [], "experience_years": None, "languages": [], "skills": [], "keywords": []}
    # qualifications -> roles
    for q, rx in _QUAL:
        if re.search(rx, low, re.I):
            prof["qualifications"].append(q)
    for q in prof["qualifications"]:
        r = _ROLE_FROM_QUAL.get(q)
        if r and r not in prof["roles"]:
            prof["roles"].append(r)
    # role keywords via the job-title classifier applied to each line that looks like a job line
    for line in text.splitlines():
        ln = line.strip()
        if 4 < len(ln) < 120 and re.search(r"pfleg|nurse|krankenschwester|hebamme|\bota\b|\bata\b|leitung|praxisanleit", ln, re.I):
            role, rule = classify_role(ln, "")
            if role and role not in ("nicht_pflege", "ausbildung", "werkstudent_praktikum", "sonstige_pflege") and role not in prof["roles"]:
                prof["roles"].append(role)
    if not prof["roles"] and re.search(r"pfleg|nurs", low):
        prof["roles"].append("pflegefachkraft")
    # skills
    for s in pats["skills"]:
        try:
            if re.search(s["re"], low, re.I):
                prof["skills"].append(s["tag"])
        except re.error:
            continue
    for s in prof["skills"]:
        d = _SKILL_TO_DEPT.get(s)
        if d and d not in prof["departments"]:
            prof["departments"].append(d)
    dh = department_hint(text[:4000])
    if dh and dh not in prof["departments"]:
        prof["departments"].append(dh)
    # experience
    try:
        yrs = [int(m.group(1)) for m in re.finditer(pats["experience_years"], low, re.I) if int(m.group(1)) <= 45]
        prof["experience_years"] = max(yrs) if yrs else None
    except re.error:
        pass
    if prof["experience_years"] is None:
        years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-2]\d)\b", text)]
        if len(years) >= 2:
            prof["experience_years"] = min(30, max(years) - min(years)) or None
    # languages
    try:
        for m in re.finditer(pats["languages"], low, re.I):
            g = [x for x in m.groups() if x]
            lang = next((x for x in g if x in ("deutsch", "german", "englisch", "english")), None)
            lvl = next((x for x in g if re.fullmatch(r"[abc][12]", x)), None)
            if lang:
                tag = ("Deutsch" if lang in ("deutsch", "german") else "Englisch") + (" " + lvl.upper() if lvl else "")
                if tag not in prof["languages"]:
                    prof["languages"].append(tag)
    except re.error:
        pass
    # cities: registry towns + job cities mentioned in the text
    snap = D.snapshot()
    towns = {c["town"] for c in snap["clinics"] if c.get("town")} | {j["city"] for j in snap["jobs"] if j.get("city")}
    ftext = _fold(text)
    for t in sorted(towns, key=len, reverse=True):
        if len(t) >= 4 and re.search(r"(?<![a-z])" + re.escape(_fold(t)) + r"(?![a-z])", ftext):
            prof["cities"].append(t)
        if len(prof["cities"]) >= 8:
            break
    # bezirk of the cities mentioned
    bez = {c["regierungsbezirk"] for c in snap["clinics"] if c.get("town") in prof["cities"] and c.get("regierungsbezirk")}
    prof["regierungsbezirke"] = sorted(bez)
    prof["keywords"] = sorted({w for w in re.findall(r"[a-zäöüß]{6,}", low) if w in _KEYWORDS})[:30]
    return prof


_KEYWORDS = {"pflegefachkraft", "krankenpfleger", "krankenschwester", "intensivpflege", "anästhesie", "notaufnahme", "stationsleitung", "praxisanleitung",
             "pflegedienstleitung", "wundmanagement", "palliativ", "dialyse", "onkologie", "kardiologie", "neurologie", "psychiatrie", "geriatrie",
             "pädiatrie", "beatmung", "hygiene", "qualitätsmanagement", "fachweiterbildung", "anerkennung", "kenntnisprüfung", "vollzeit", "teilzeit",
             "nachtdienst", "schichtdienst", "wohnung", "tvöd", "bachelor", "master"}


def _llm_refine(text, prof):
    """Optional: ask an OpenAI-compatible endpoint to tidy the profile. Silent on any failure."""
    base = A.LLM_API_BASE.rstrip("/")
    if not base:
        return prof, False
    try:
        if requests.get(base + "/models", timeout=5).status_code >= 400:
            return prof, False
        body = {"model": A.LLM_MODEL, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": "Extract a nursing candidate profile as JSON with keys roles (subset of pflegefachkraft,fachpflege,pflegehelfer,praxisanleitung,leitung,apn_experte,hebamme,ota_ata), departments (German ward names), qualifications, cities (German towns the candidate lives in or prefers), experience_years (int), languages (like 'Deutsch B2'), skills (short tags). Only JSON."},
                             {"role": "user", "content": text[:12000]}]}
        r = requests.post(base + "/chat/completions", json=body, timeout=40, headers={"Authorization": "Bearer " + A.__dict__.get("LLM_API_KEY", "none")})
        if r.status_code >= 300:
            return prof, False
        j = json.loads(r.json()["choices"][0]["message"]["content"])
        out = dict(prof)
        for k in ("roles", "departments", "qualifications", "cities", "languages", "skills"):
            if isinstance(j.get(k), list) and j[k]:
                out[k] = list(dict.fromkeys(list(prof.get(k) or []) + [str(x) for x in j[k]]))[:12]
        if isinstance(j.get("experience_years"), int) and not prof.get("experience_years"):
            out["experience_years"] = j["experience_years"]
        return out, True
    except Exception:
        return prof, False


_ROLE_NEAR = {"pflegefachkraft": {"fachpflege": 0.6, "sonstige_pflege": 0.5, "praxisanleitung": 0.4, "pflegehelfer": 0.3},
              "fachpflege": {"pflegefachkraft": 0.7, "sonstige_pflege": 0.4, "apn_experte": 0.4},
              "pflegehelfer": {"pflegefachkraft": 0.3, "sonstige_pflege": 0.4},
              "leitung": {"praxisanleitung": 0.5, "pflegefachkraft": 0.4, "apn_experte": 0.5},
              "praxisanleitung": {"pflegefachkraft": 0.6, "leitung": 0.4},
              "apn_experte": {"fachpflege": 0.6, "leitung": 0.5, "pflegefachkraft": 0.4},
              "hebamme": {}, "ota_ata": {"fachpflege": 0.4}}


def match(prof, limit=50):
    snap = D.snapshot()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    roles, depts, cities, bez = set(prof.get("roles") or []), set(prof.get("departments") or []), {c.lower() for c in prof.get("cities") or []}, set(prof.get("regierungsbezirke") or [])
    skills = [s.lower() for s in prof.get("skills") or []]
    out = []
    for j in snap["jobs"]:
        score, why = 0.0, []
        r = j.get("role_class")
        if r in roles:
            score += 40; why.append(j.get("role_label") or r)
        else:
            near = max((_ROLE_NEAR.get(x, {}).get(r, 0) for x in roles), default=0)
            if near:
                score += 40 * near; why.append(f"~{j.get('role_label') or r}")
        d = j.get("department_hint")
        title_low = ((j.get("title") or "") + " " + (j.get("department_raw") or "")).lower()
        if d and d in depts:
            score += 30; why.append(d)
        else:
            hits = [s for s in skills if s in title_low]
            if hits:
                score += min(30, 12 * len(hits)); why += hits[:2]
            elif depts and not d:
                score += 6                                        # generic ward, no contradiction
        city = (j.get("city") or "").lower(); town = (j.get("clinic_town") or "").lower()
        if cities and (city in cities or town in cities):
            score += 20; why.append(j.get("city") or j.get("clinic_town"))
        elif bez and j.get("regierungsbezirk") in bez:
            score += 10; why.append(j.get("regierungsbezirk"))
        elif not cities:
            score += 5
        if j.get("fresh"):
            score += 10; why.append("neu")
        elif (j.get("first_published") or j.get("first_seen") or "")[:10] >= cutoff:
            score += 5
        if j.get("verify_status") == "live":
            score += 2
        if score >= 25:
            row = dict(j); row["score"] = int(round(min(score, 100))); row["why"] = why[:5]
            out.append(row)
    out.sort(key=lambda x: (-x["score"], not x.get("fresh"), x.get("title") or ""))
    return out[:limit]


def analyse(filename=None, blob=None, text=None, limit=50):
    txt = text if text is not None else extract_text(filename, blob or b"")
    txt = (txt or "").strip()
    if len(txt) < 20:
        raise ValueError("no readable text in the CV (scanned PDF? try DOCX or plain text)")
    prof = profile_from_text(txt)
    prof, used_llm = _llm_refine(txt, prof)
    return {"profile": prof, "matches": match(prof, limit), "used_llm": used_llm, "chars": len(txt)}
