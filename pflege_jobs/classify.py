"""Deterministic classification + normalization. Pure functions, unit-tested.
Every derived value carries the rule that produced it (auditable by agents)."""
import hashlib
import re
import unicodedata
from . import config as C

def _compile():
    """(Re)compile every pattern from config; called at import and by config.reload()."""
    g = globals()
    g["_CLINIC"] = [(n, C.rx(p)) for n, p in C.CLINIC_PATTERNS]
    g["_NONCLINIC"] = [(n, C.rx(p)) for n, p in C.NON_CLINIC_PATTERNS]
    g["_LEGAL"] = C.rx(C.LEGAL_FORMS)
    g["_PFLEGE"] = C.rx(C.PFLEGE_TOKEN)
    g["_NICHT"] = C.rx(C.NICHT_PFLEGE)
    g["_STRONG_T"] = C.rx(C.STRONG_PFLEGE_TITLE)
    g["_ROLES"] = [(n, C.rx(p)) for n, p in C.ROLE_RULES]
    g["_QUAL"] = [(n, C.rx(p)) for n, p in C.QUALIFICATION_HINT]
    g["_DEPT"] = [(n, C.rx(p)) for n, p in C.DEPARTMENT_HINT]
    g["_HOUSING"] = C.rx(C.HOUSING)
    g["_TARIFF"] = [(n, C.rx(p)) for n, p in C.TARIFF]
    g["_EMAIL"] = re.compile(C.EMAIL)
    g["_PAY"], g["_PAYTXT"], g["_REQH"], g["_REQS"], g["_EXP"] = C.rx(C.PAY_GRADE), C.rx(C.PAY_TEXT), C.rx(C.REQ_HEAD), C.rx(C.REQ_STOP), C.rx(C.EXPERIENCE)
    g["_LANG"], g["_BONUS"], g["_CHILD"], g["_ANERK"] = C.rx(C.LANGUAGE_REQ), C.rx(C.BONUS), C.rx(C.CHILDCARE), C.rx(C.ANERKENNUNG)


_compile()


def norm_text(s: str) -> str:
    """lowercase, unicode-normalize, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"\s+", " ", s).strip()


def employer_norm(name: str) -> str:
    """Identity key for employers: strip legal forms/punctuation. Conservative: does NOT
    merge 'Klinikum Nürnberg' with 'Klinikum Nürnberg Personalabteilung' (phase 2: registry)."""
    s = norm_text(name)
    s = _LEGAL.sub(" ", s)
    s = re.sub(r"[^\w\säöüß-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify_employer(name: str):
    """-> (employer_class, rule). clinic | non_clinic | unknown. Conflict -> unknown (never guess)."""
    s = norm_text(name)
    c = next((n for n, r in _CLINIC if r.search(s)), None)
    nc = next((n for n, r in _NONCLINIC if r.search(s)), None)
    if c and not nc:
        return "clinic", f"clinic:{c}"
    if nc and not c:
        return "non_clinic", f"non_clinic:{nc}"
    if c and nc:
        if nc in C.WEAK_NON_CLINIC_GROUPS:
            return "clinic", f"clinic_over_weak:{c}|{nc}"
        return "unknown", f"conflict:{c}|{nc}"
    return "unknown", "no_match"


def classify_role(title: str, hauptberuf: str = "", offer_kind: str = ""):
    """-> (role_class, rule). Evaluated on title + hauptberuf; offer_kind AUSBILDUNG forces ausbildung."""
    s = norm_text(f"{title} || {hauptberuf}")
    if not _PFLEGE.search(s):                      # gate first: Ausbildung Elektroniker is not nursing
        return "nicht_pflege", "no_pflege_token"
    if _NICHT.search(s) and not _STRONG_T.search(norm_text(title)):
        return "nicht_pflege", f"nicht_pflege:{_NICHT.search(s).group(0)}"
    if offer_kind == "AUSBILDUNG":
        return "ausbildung", "offer_kind:AUSBILDUNG"
    if offer_kind == "PRAKTIKUM_TRAINEE":
        return "werkstudent_praktikum", "offer_kind:PRAKTIKUM_TRAINEE"
    for name, r in _ROLES:
        m = r.search(s)
        if m:
            return name, f"{name}:{m.group(0)}"
    return C.ROLE_FALLBACK, "fallback"


def qualification_hint(title: str, hauptberuf: str = ""):
    s = norm_text(f"{hauptberuf} || {title}")
    return next((n for n, r in _QUAL if r.search(s)), None)


def department_hint(title: str):
    s = norm_text(title)
    return next((n for n, r in _DEPT if r.search(s)), None)


def fuzzy_key(title: str, employer: str, city: str) -> str:
    """Cross-source linking key (NOT identity within a source): normalized title | employer_norm | city."""
    t = norm_text(title)
    t = re.sub(r"\((m|w|d|x|i|\s|/|\*|:)+\)", " ", t)  # strip (m/w/d)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\b(vollzeit|teilzeit|ab sofort|unbefristet|befristet)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    raw = f"{t}|{employer_norm(employer)}|{norm_text(city or '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def content_hash(*parts) -> str:
    return hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def enrich_description(desc: str) -> dict:
    """Phase-2 enrichment from posting body. Returns only detected values (None = not found)."""
    if not desc:
        return {}
    s = norm_text(desc)
    tariff = next((n for n, r in _TARIFF if r.search(s)), None)
    housing_m = _HOUSING.search(s)
    emails = sorted(set(e.lower() for e in _EMAIL.findall(desc)))
    lang = _LANG.search(s)
    pay = _PAY.search(s)
    grade = None
    if pay:
        g = pay.group(1) or pay.group(4) or ""
        grade = re.sub(r"\s", "", g.upper()).replace("P0", "P").replace("KR0", "KR").replace("EG0", "EG")
    pt = _PAYTXT.search(desc)
    req = None
    h = _REQH.search(s)
    if h:
        tail = desc[h.end():h.end() + 1200]
        stop = _REQS.search(norm_text(tail))
        req = re.sub(r"\s+", " ", tail[: stop.start() if stop else 600]).strip(" :-–*#\n")[:600] or None
    ex = _EXP.search(desc)
    return {
        "pay_grade": grade,
        "pay_text": re.sub(r"\s+", " ", pt.group(0)).strip()[:200] if pt else None,
        "requirements": req,
        "experience": ex.group(1)[:120] if ex else None,
        "housing": bool(housing_m),
        "housing_evidence": housing_m.group(0) if housing_m else None,
        "tariff": tariff,
        "contact_emails": emails or None,
        "language_req": lang.group(0)[:60] if lang else None,
        "bonus": bool(_BONUS.search(s)),
        "childcare": bool(_CHILD.search(s)),
        "anerkennung_mentioned": bool(_ANERK.search(s)),
    }
