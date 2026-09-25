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
    g["_TASKH"], g["_TASKSTOP"] = C.rx(C.TASK_HEAD), C.rx(C.TASK_STOP)
    g["_LANG"], g["_BONUS"], g["_CHILD"], g["_ANERK"] = C.rx(C.LANGUAGE_REQ), C.rx(C.BONUS), C.rx(C.CHILDCARE), C.rx(C.ANERKENNUNG)


_compile()

# A gender-neutral suffix ("(m/w/d)", ":in", bare "m/w/d", "Pfleger/in") is the same structural "this
# is a real job title" signal crawlers.vendor_adapters/career_crawl already use on the crawl side to
# decide whether classify_role's catch-all fallback is looking at an actual posting title or just
# prose that mentions "Pflege". Shared via pflege_jobs/posting_signal.py (TASK-123/126: this used to be
# a fourth independently-drifted copy, missing the bare-slash suffix form -- posting_signal.py is a
# leaf module with no downstream dependents, so importing it here creates no cycle).
from .posting_signal import GENDER_MARKER as _POSTING_SHAPED  # noqa: E402

# Real, live-surveyed (2026-09-23, TASK-126) prefixes for a speculative "apply even with no open
# vacancy" invitation, never a genuine open posting: "Initiativbewerbung"/"Initiativbewerbungen"
# (survey: wolfartklinik.de, kbo-iak.de, martha-maria.de, klinikum-passau.de, bkh-landshut.de, ~15
# more) and "Blitzbewerbung" (kbo-iak.de, kbo-lmk.de). Every real example puts the word FIRST, then
# optionally the role/department it's soliciting for ("Blitzbewerbung Pflegefachkräfte (m/w/d)",
# "Initiativbewerbungen Assistenzärzte (m/w/d)") -- confirmed live: kbo-iak.de/kbo-lmk.de's own
# "Blitzbewerbung Pflegefachkräfte (m/w/d)" carries a real gender marker and, unchecked, matches
# _ROLES' plain substring rule below exactly like a genuine open Pflegefachkraft posting would.
# Anchored at the start (not a bare word-boundary search) since no surveyed board ever buries the
# word mid-title -- a genuine posting title happening to mention "Initiativbewerbung" in its own body
# text elsewhere is not read by this check at all (title only).
_SPECULATIVE_APPLICATION_RX = re.compile(r"^\s*(initiativbewerbung(?:en)?|blitzbewerbung)\b", re.I)

_JOB_URL_HOST_RX = re.compile(r"^https?://([^/]+)", re.I)

# TASK-142: a tariff name immediately preceded/followed by one of these phrases is being used as a
# comparison benchmark ("angelehnt an TVöD", "TVöD angelehnt", "über dem Tarif der TVöD-K"), not stated
# as the posting's own applied tariff -- see enrich_description()'s _TARIFF selection below.
_TARIFF_CMP_PRE = re.compile(r"(über dem tarif|orientiert (?:sich )?an|angelehnt an|in anlehnung an|vergleichbar mit|analog (?:zu|zum|zur))\s*(der|dem|des)?\s*$", re.I)
_TARIFF_CMP_POST = re.compile(r"^\s*(angelehnt|orientiert)\b", re.I)

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

    Checked before all of that: a speculative-application title ("Initiativbewerbung Pflegefachkraft
    (m/w/d)", "Blitzbewerbung Ärzte (m/w/d)") is never a genuine open posting, no matter which role it
    names or whether a section confirms it -- the _ROLES loop below matches on a bare substring
    (TASK-126: "Blitzbewerbung Pflegefachkräfte (m/w/d)", confirmed live on kbo-iak.de/kbo-lmk.de,
    would otherwise classify as a real pflegefachkraft posting).
    """
    if _SPECULATIVE_APPLICATION_RX.search(title or ""):
        return "nicht_pflege", "speculative_application"
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


def qualification_hint(title: str, hauptberuf: str = "", desc: str = ""):
    """-> the first matching required licence/qualification (GKiK, GuK, Altenpflege, generalistisch), or
    None when nothing matched -- never a guess. Shares department_hint's TASK-97 fix (TASK-104): the
    scanned text used to be title + hauptberuf only (hauptberuf is the Arbeitsagentur occupation code,
    not posting body text -- a real signal when present, but most sources never populate it), missing
    the qualification whenever a posting states it only in its own Aufgaben/Profil text ("Wir suchen
    eine examinierte Pflegefachkraft für unsere Intensivstation" with a generic title like "Pflegekraft
    (m/w/d)"). Now also scans `desc`'s own TASKS section (Aufgaben/Tätigkeiten, via TASK_HEAD/TASK_STOP)
    and PROFIL section (Ihr Profil/Anforderungen, via REQ_HEAD/REQ_STOP) through extract_section() --
    the exact same two sections and the same reused helper department_hint() scans, never the rest of
    the body (page tail/menus/contact text stays excluded for the same reason TASK-97 excluded it there).

    Deliberately kept single-value (first match), unlike department_hint's TASK-97 multi-label change:
    _QUAL's 4 patterns (GKiK/GuK/Altenpflege/generalistisch) name alternative licence types a real
    person holds one of, not independently-combinable specialties a posting can genuinely require
    several of at once -- there is no live evidence (unlike Intensiv+Anästhesie for department_hint)
    of a posting correctly requiring two different licences simultaneously."""
    tasks = extract_section(desc, _TASKH, _TASKSTOP)
    profil = extract_section(desc, _REQH, _REQS)
    s = norm_text(" || ".join(x for x in (hauptberuf, title, tasks, profil) if x))
    return next((n for n, r in _QUAL if r.search(s)), None)


def extract_section(desc: str, head_rx, stop_rx, window: int = 1200, max_len: int = 600):
    """desc's own text between head_rx's first match and the next stop_rx boundary (or `window` chars,
    whichever comes first), collapsed to one line and trimmed to `max_len`. None when head_rx never
    matches desc at all -- that is the whole safety property this gives callers: a posting whose body
    never states a real "Ihr Profil"/"Ihre Aufgaben"-shaped heading yields no text to scan, rather than
    a naive caller falling back to the full page (menus, phone directories, "verfügt über"
    hospital-wide boilerplate -- TASK-97).

    Pulled out of enrich_description()'s inline "Ihr Profil" slicing (REQ_HEAD/REQ_STOP) so a second
    caller (department_hint(), TASK-97's Aufgaben/Tätigkeiten section) can reuse the identical
    boundary-extraction logic instead of duplicating it -- and so TASK-104/TASK-107 have one named
    function to call instead of copying this slicing a third and fourth time.

    head_rx/stop_rx are matched against norm_text(desc) (lowercased) to find offsets, but the returned
    text is sliced out of the ORIGINAL `desc` at those offsets -- norm_text's whitespace-collapsing can
    shift offsets by a few characters on irregular whitespace; this is the same trade-off
    enrich_description's requirements extraction has always made, not a new one."""
    if not desc:
        return None
    h = head_rx.search(norm_text(desc))
    if not h:
        return None
    tail = desc[h.end():h.end() + window]
    stop = stop_rx.search(norm_text(tail))
    text = re.sub(r"\s+", " ", tail[: stop.start() if stop else max_len]).strip(" :-–*#\n")[:max_len]
    return text or None


def department_hint(title: str, desc: str = ""):
    """-> every distinct department/specialty this posting names, "|"-joined in patterns.json's
    declared department order, or None when nothing matched -- never just the first hit (TASK-97 fixed
    the old next(...)-based single value: a real posting for Intensiv AND Anästhesie used to lose one
    of the two). Joined the same way clinics.fachrichtungen is (no department name contains "|"):
    keeping this a plain string keeps every existing single-department caller/test unchanged
    (`department_hint(title) == "Intensiv/IMC"` still holds whenever exactly one department matches)
    and keeps the DB column a plain "text" field -- the smaller schema diff than migrating it to a real
    array type. Callers that need the individual labels split on "|" themselves (app/data.py's
    snapshot build does, for the facet/filter/search layer -- see docs there).

    Scanned text = title + this posting's own TASKS section (Aufgaben/Tätigkeiten/Ihre Aufgaben/Das
    erwartet Sie, via TASK_HEAD/TASK_STOP) + its own PROFIL section (Ihr Profil/Anforderungen/..., via
    REQ_HEAD/REQ_STOP, the same section enrich_description() already extracts as "requirements") --
    both through extract_section(), never the rest of the body. A department name is genuinely stated
    in the title or one of those two sections; everywhere else on a career-site page (menus, phone
    directories per department, "verfügt über ... eine Stroke Unit" hospital-wide descriptions,
    initiative-application department lists) shares the same vocabulary without being THIS posting's
    own department (TASK-97, live-confirmed 2026-09-24: 'Neurologie & Stroke Unit Sekretariat
    08141/99-6103' -- a phone-directory line entirely outside any Aufgaben/Profil section on that same
    posting's page -- and 'verfügt über ... eine Chest Pain Unit, eine Stroke Unit, eine Akutgeriatrie'
    -- a hospital-wide intro paragraph on a Gynäkologie/Geburtshilfe posting, posting_id 10767 -- both
    produce no department label here, confirmed live)."""
    tasks = extract_section(desc, _TASKH, _TASKSTOP)
    profil = extract_section(desc, _REQH, _REQS)
    s = norm_text(" || ".join(x for x in (title, tasks, profil) if x))
    return "|".join(n for n, r in _DEPT if r.search(s)) or None


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
    # patterns.json's declared _TARIFF list order still decides ties (it deliberately puts specific AVR
    # variants before the generic "AVR (unspecified)" catch-all, so the more specific label wins when
    # both match the same "AVR ..." word -- live-checked 2026-09-24 on 10 open postings whose
    # description states the specific variant, e.g. "AVR-Caritas", well AFTER an earlier generic "AVR"
    # mention elsewhere in the same text: position alone would wrongly pick the vaguer one). The one
    # exception: a match immediately next to a comparison phrase ("angelehnt an", "orientiert an",
    # "in Anlehnung an", "ueber dem Tarif der/des", "vergleichbar mit", "analog zu/zum/zur") names a
    # tariff the posting is being compared AGAINST, not applied under, and is skipped in favor of the
    # next (still list-order) candidate -- confirmed live on 7 open postings across 2 clinics where this
    # exact pattern (a real tariff stated first, a second, unrelated one used only as a comparison
    # benchmark) produced a traegerart-implausible result: "unser Haustarif liegt immer garantiert ueber
    # dem Tarif der TVoeD-K" (posting 5701, Dr. Lubos Kliniken, traegerart=privat, used to report
    # "TVoeD" instead of "Haustarif") and "Verguetung nach ... Caritasverband (AVR) ... (TVoeD
    # angelehnt)" (posting 6604, Waldkrankenhaus St. Marien, traegerart=freigemeinnuetzig, used to
    # report "TVoeD" instead of "AVR Caritas"). If every match is comparison-adjacent (never observed
    # live), falls back to the first list-order match rather than losing the field to None.
    _hits = [(i, n, m) for i, (n, r) in enumerate(_TARIFF) if (m := r.search(s))]
    _direct = [(i, n, m) for i, n, m in _hits if not _TARIFF_CMP_PRE.search(s[max(0, m.start() - 40):m.start()])
               and not _TARIFF_CMP_POST.search(s[m.end():m.end() + 20])]
    tariff = (_direct or _hits)[0][1] if _hits else None
    housing_m = _HOUSING.search(s)
    emails = sorted(set(e.lower() for e in _EMAIL.findall(desc)))
    lang = _LANG.search(s)
    pay = _PAY.search(s)
    grade = None
    if pay:
        g = pay.group(1) or pay.group(4) or ""
        grade = re.sub(r"\s", "", g.upper()).replace("P0", "P").replace("KR0", "KR").replace("EG0", "EG")
    pt = _PAYTXT.search(desc)
    req = extract_section(desc, _REQH, _REQS)
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
