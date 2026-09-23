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

# A gender-neutral suffix ("(m/w/d)", ":in", bare "m/w/d") is the same structural "this is a real job
# title" signal career_crawl.JOB_TEXT and vendor_adapters.GENDER already use on the crawl side --
# reused here (not imported: those live downstream of this module) to decide whether classify_role's
# catch-all fallback is looking at an actual posting title or just prose that mentions "Pflege".
_POSTING_SHAPED = re.compile(
    r"\((?:m|w|d|x|i|gn|a)\s?[/|*]\s?(?:m|w|d|x|i|gn|a)(?:\s?[/|*]\s?(?:m|w|d|x|i|gn|a))?\)"
    r"|\b[mwd]/[mwd]/[mwdx]\b|[:*]in\b", re.I)

_JOB_URL_HOST_RX = re.compile(r"^https?://([^/]+)", re.I)

# Same-vendor identity of a job URL, extracted from the vendor's own job id -- used ONLY to build the
# posting_observations dedup key (source_ref; unique(source_id, source_ref), sql/001_schema.sql:112),
# never as a value shown to a job seeker: callers keep external_url/source_url as the real, as-crawled
# link. `host_scoped` True means the vendor's own id is only unique WITHIN one tenant/host (a small
# per-tenant sequential -- two different tenants can and do reuse the same integer for an unrelated
# job) and must be combined with the host to avoid folding two different clinics' postings together.
# Shapes confirmed live 2026-09-22, one job stored 2-3 times under exactly these before this fix:
#   dvinci     <tenant>.dvinci-(easy|hr).com/de/jobs/<id> vs .../de/jobs/<id>/<slug>
#              (Bamberg sozialstiftung-bamberg.dvinci-easy.com, Fuerth jobs.klinikum-fuerth.de,
#              Neumarkt klinikum-neumarkt.dvinci-hr.com: 5 duplicate ids each)
#   helix      <host>/<unit>/jobad?prj=<id> -- the SAME prj re-posted under different unit paths on
#              one host (bezirk-unterfranken.helixjobs.com: prj=2618P680 under both /tzbu/ and
#              /bkhwerneck/, prj=2618P779 under both /bkhwerneck/ and /KPPPM/)
#   umantis    <host>/Vacancies/<id>/... -- ANregiomed Ansbach carries both a vanity domain
#              (www.anregiomed.de) and the raw recruitingapp-5511.de.umantis.com tenant host
#   softgarden <any host>/job(s)/<id>/... -- softgarden's own id is a platform-wide sequential, not
#              per-tenant (Klinikum Bayreuth ids are 8 digits), so it is deliberately NOT host-scoped
#              -- that is what lets a vanity domain and the *.softgarden.io host fold together
#   b-ite      <host>/(de/)?jobposting/<hex-id>/... -- id is a hash (confirmed live: 41 hex chars,
#              not the 40 a first read of the id suggests -- matched by length range, not a fixed
#              count), never host-scoped either
_CANON_JOB_ID_RULES = (
    ("dvinci", re.compile(r"/de/jobs/(\d+)", re.I), True),
    ("bite", re.compile(r"/jobposting/([0-9a-f]{20,})(?:/|$)", re.I), False),
    ("helix", re.compile(r"[?&]prj=([A-Za-z0-9]+)", re.I), True),
    ("umantis", re.compile(r"/Vacancies/(\d+)", re.I), True),
    ("softgarden", re.compile(r"/jobs?/(\d{6,})(?:[/?#]|$)", re.I), False),
)


def canonical_job_url(url: str) -> str:
    """-> a stable per-vendor-job-id identity string for `url` (see _CANON_JOB_ID_RULES), or `url`
    itself unchanged when no known vendor shape matches -- always a usable string, never None."""
    if not url:
        return url
    for vendor, rx, host_scoped in _CANON_JOB_ID_RULES:
        m = rx.search(url)
        if m:
            if host_scoped:
                hm = _JOB_URL_HOST_RX.match(url)
                return f"{vendor}:{hm.group(1).lower() if hm else ''}:{m.group(1)}"
            return f"{vendor}:{m.group(1).lower()}"
    return url


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


def classify_role(title: str, hauptberuf: str = "", offer_kind: str = "", nursing_section_confirmed: bool = False):
    """-> (role_class, rule). Evaluated on title + hauptberuf; offer_kind AUSBILDUNG forces ausbildung.

    nursing_section_confirmed: True when the job's OWN vendor-provided category/department label
    positively confirms it already sits in the nursing section of its board (see
    pflege_jobs.section.job_confirmed_nursing) -- a structural signal, stronger than a free-text
    keyword gate. Scope, grounded in the 2026-09 survey of real dept-tagged titles:
      - Step 1 (the pflege_gate token requirement) is SKIPPED when True: a section-confirmed posting
        must not be dropped just because its title alone carries no nursing keyword. Real, observed
        titles that failed ONLY this gate: dvinci "Hygienefachkraft (m/w/d)" and "Advanced Practice
        Nurses (m/w/d)" (dept "Pflege- und Funktionsdienst"/"02 Pflegedienst"), dvinci "Gerontofachkraft
        (w/m/d)" (falls through to the sonstige_pflege fallback below, which is fine -- it is a kept,
        non-excluded class), and smartrecruiters "Dauernachtwache (m/w/d)" (dept "Pflegedienst").
      - Step 2 (the nicht_pflege/strong_pflege check) is left UNCHANGED regardless of this flag: the
        survey found zero real examples of it wrongly excluding a genuinely-nursing section-confirmed
        title, and found real examples of it correctly excluding a competing non-nursing occupation
        (rexx "Medizinische Fachangestellte (m/w/d) für den OP in München", "Kodierfachkraft (m/w/d)
        für DRG/PEPP") even inside a confirmed "Pflege, Patientenmanagement & Dokumentation"/
        "Pflegedienst" bucket -- both HR groupings that also carry MFAs, Kodierfachkräfte,
        Physiotherapeuten etc. Leaving step 2 active is what keeps those correctly excluded even
        though step 1 no longer blocks them on the way in.
      - Steps 3 (offer_kind AUSBILDUNG/PRAKTIKUM_TRAINEE) and 4 (the _ROLES loop, which is what
        detects pflegehelfer) always run unchanged: "still filter helpers/learners" does not relax.
    """
    s = norm_text(f"{title} || {hauptberuf}")
    if not _PFLEGE.search(s):                      # gate first: Ausbildung Elektroniker is not nursing
        if not nursing_section_confirmed:
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
    # sonstige_pflege is a real, kept catch-all -- but only for a title that is itself shaped like a
    # job posting (carries a gender-neutral marker: "(m/w/d)", ":in", ...). A bare pflege_gate token
    # match with no _ROLES hit and no marker is not evidence of a nursing ROLE, only that a
    # conjugated form of "pflegen" occurs somewhere in the text (TASK-84: classify_role('PFLEGEN
    # KÖNNEN.') used to return sonstige_pflege/fallback, which is how 31 Klinikum Memmingen news
    # headlines became open postings). "Betreuungskräfte (m/w/d) gesucht" -- no _ROLES hit, see
    # pflegehelfer's umlaut-plural gap in tests/test_mech_role_class.py -- keeps falling back here
    # because it DOES carry the marker; so does the section-confirmed "Gerontofachkraft (w/m/d)"
    # (tests/test_classify_section.py).
    if _POSTING_SHAPED.search(title or ""):
        return C.ROLE_FALLBACK, "fallback"
    return "nicht_pflege", "fallback_no_posting_signal"


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
