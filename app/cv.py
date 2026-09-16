"""CV -> profile -> ranked jobs. Works without an LLM: the same regexes that classify job titles
(pflege_jobs.classify, driven by patterns.json) read the CV, plus a `cv` pattern section for experience,
languages and skill tags. An OpenAI-compatible LLM (env LLM_API_BASE) may refine the profile when reachable.

analyse_llm() is a second, independent extraction path (TASK-65): the `claude` CLI reads the CV text
(+ optional chat history) and reasons the profile directly, instead of regex. Same {profile, matches}
output shape as analyse() -- both call the same deterministic match() once a profile exists, so the
compared thing is extraction quality, not job-matching scoring. See evals/cv/run.py --path=llm and
evals/cv/README.md for the measured comparison and which path is recommended.

extract_text_vision()/VisionClient (TASK-67) is a third extraction primitive, for images and
scanned (text-layer-less) PDFs: the same `claude` CLI subprocess pattern, but asking the model to
read a FILE via its own built-in Read tool instead of reasoning over text on stdin. analyse_candidate()
(TASK-67) is the WhatsApp-harness entry point: analyse_llm() plus this candidate's own chat history
(app.wa.store.history), for cv_text/urkunde_text the harness extracted from their uploads."""
import io
import json
import os
import pathlib
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
    prof["regierungsbezirke"] = _regierungsbezirke_for_cities(prof["cities"])
    prof["keywords"] = sorted({w for w in re.findall(r"[a-zäöüß]{6,}", low) if w in _KEYWORDS})[:30]
    return prof


def _regierungsbezirke_for_cities(cities):
    """Regierungsbezirk of each already-recognised registry town (used by `match()`'s bezirk
    fallback when a job's own city isn't an exact hit). Factored out of profile_from_text so
    analyse_llm's LLM-derived `cities` (free-text extraction, not cross-checked against the
    registry while being read) gets the same deterministic geography lookup, rather than asking
    the model to know Bavarian Regierungsbezirke by heart."""
    snap = D.snapshot()
    return sorted({c["regierungsbezirk"] for c in snap["clinics"] if c.get("town") in (cities or []) and c.get("regierungsbezirk")})


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


# --- LLM-driven profile extraction (TASK-65: compared against profile_from_text above, not a --------
# --- silent fallback for it -- see evals/cv/README.md for the measured result) ----------------------
#
# Calls the real `claude` CLI in non-interactive print mode, the same subprocess pattern as
# app/wa/luna_brain.py:Client (see that module's docstring for the reasoning): --system-prompt as a
# full replacement of the CLI's own persona, --restricted --tools "" so nothing but the model's own
# answer can come back, the payload over stdin, --output-format json with the `result` field parsed
# as the model's own JSON. Unlike luna_brain's Client this is one-shot -- a CV read has no multi-turn
# conversation to resume, so there is no --session-id/--resume.
#
# Env-overridable so a caller can point this at a different binary/model/effort without a code
# change, same convention as app/wa/config.py's WA_LUNA_* names.
_LLM_CLAUDE_BIN = os.environ.get("CV_LLM_CLAUDE_BIN", "claude").strip() or "claude"
_LLM_MODEL = os.environ.get("CV_LLM_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"
_LLM_EFFORT = os.environ.get("CV_LLM_EFFORT", "medium").strip() or "medium"
_LLM_TIMEOUT_SEC = int(os.environ.get("CV_LLM_TIMEOUT_SEC", "60") or "60")

_PROFILE_LIST_KEYS = ("roles", "departments", "qualifications", "cities", "languages", "skills")
_ROLE_VOCAB = ("pflegefachkraft", "fachpflege", "pflegehelfer", "praxisanleitung", "leitung", "apn_experte", "hebamme", "ota_ata")


def _department_vocab():
    """The same closed department_hint vocabulary live postings are tagged with (patterns.json's
    `department` section) -- giving the model this list, instead of letting it invent ward names,
    is what makes its `departments` output comparable to a job's own `department_hint` in match()."""
    from pflege_jobs import config as C2
    return [name for name, _ in C2.DEPARTMENT_HINT]


def _llm_system_prompt():
    quals = ", ".join(q for q, _ in _QUAL)
    depts = ", ".join(_department_vocab())
    skills = ", ".join(s["tag"] for s in _cv_patterns()["skills"])
    roles = ", ".join(_ROLE_VOCAB)
    return f"""You read a nursing/Pflege candidate's CV text (German and/or English, may mix both, may
be informal, listy, or have unusual phrasing) plus optional prior WhatsApp chat history with the same
candidate, and extract a structured candidate profile. Base every field ONLY on what the text actually
says -- never invent a role, department, city, or qualification the text does not evidence. If nothing
evidences a field, use an empty list (or null for experience_years), do not guess.

Output nothing but a single JSON object -- no markdown code fence, no commentary before or after it --
with exactly these keys:
{{
  "roles": [subset of: {roles}],
  "departments": [subset of: {depts}],
  "qualifications": [subset of: {quals}],
  "cities": [German town/city names the candidate lives in, has worked in, or is looking for work in],
  "experience_years": <integer total years of nursing-relevant experience, or null>,
  "languages": [e.g. "Deutsch C1", "Englisch B2" -- only where a level or fluency is actually stated],
  "skills": [subset of: {skills} -- ward/specialty skills evidenced by actual work history, not a
             department merely named as a wish]
}}

Qualification vocabulary (these tags classify the training/exam level, not seniority):
- GuK: the 3-year Gesundheits- und Krankenpfleger(in) / generalist Pflegefachkraft training (German or
  an equivalent foreign nursing degree) -- includes Pflegefachfrau/-mann/-person, registered nurse,
  BSc Nursing.
- GKiK: pediatric-nurse-specific training (Kinderkrankenpflege).
- Altenpflege: elderly-care nurse training (Altenpfleger/in).
- Fachweiterbildung: a post-qualification specialist course (Fachkrankenpflege/Fachweiterbildung, e.g.
  intensive-care or anaesthesia specialisation) on top of an existing Fachkraft qualification.
- Pflegehelfer: a HELPER-level qualification (0-1 year), NOT the 3-year Fachkraft. This tag also
  covers the easily-confused "Pflegefachhelfer"/"Pflegefachassistent" -- it contains the word "Fach"
  but is still the 1-year helper level, distinct from Pflegefachkraft/GuK. Never tag a Pflegefachhelfer
  as GuK, and never put "pflegefachkraft" in roles for a Pflegefachhelfer/Pflegefachassistent CV --
  the correct role for that qualification is "pflegehelfer".
- Anerkennung: any point on the foreign-qualification-recognition path -- Anerkennung in progress or
  granted (Urkunde), Defizitbescheid (received or pending), Kenntnisprüfung (passed or pending).

Role vocabulary: pflegefachkraft (examined generalist nurse / GuK / Altenpfleger, Fachkraft level),
fachpflege (a Fachkraft with a Fachweiterbildung specialty, e.g. intensive-care/anaesthesia nurse),
pflegehelfer (helper level, not Fachkraft), praxisanleitung (clinical instructor/mentor role), leitung
(Stationsleitung/Pflegedienstleitung/team lead), apn_experte (advanced practice/Pflegeexperte), hebamme
(midwife), ota_ata (OP/anaesthesia technical assistant -- a 3-year examined, Fachkraft-equivalent role
despite the word "Assistent" in its name).

If chat_history is present in the input, it is prior conversation with this same candidate -- use it
only to disambiguate or supplement the CV text (e.g. a city or qualification mentioned in chat but not
on the CV), never to override something the CV text clearly states differently."""


def _parse_llm_json_object(text):
    """Parse the model's reply text as the one JSON object it was told to return. Same normalization
    luna_brain.py's _parse_reply_json applies, for the same reason (a model sometimes wraps JSON in a
    markdown fence, or narrates a stray sentence around it, despite --tools "" and an explicit
    instruction not to): try as-is, then with a ```...``` fence stripped, then the substring between
    the first `{` and the last `}`. Anything else wrong with the text still raises rather than
    guessing at a shape."""
    stripped = re.sub(r"^```(?:json)?\s*\n?|\n?```\s*$", "", text.strip())
    for candidate in (text, stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(stripped[start:end + 1])
    raise json.JSONDecodeError("no JSON object found", stripped, 0)


def _validate_llm_profile(out):
    if not isinstance(out, dict):
        raise RuntimeError(f"claude -p's profile extraction was not a JSON object: {out!r}")
    missing = [k for k in _PROFILE_LIST_KEYS + ("experience_years",) if k not in out]
    if missing:
        raise RuntimeError(f"claude -p's profile extraction is missing keys {missing}: {out!r}")
    for k in _PROFILE_LIST_KEYS:
        if not isinstance(out[k], list):
            raise RuntimeError(f"claude -p's profile field {k!r} must be a list, got {out[k]!r}")
    if out["experience_years"] is not None and not isinstance(out["experience_years"], int):
        raise RuntimeError(f"claude -p's experience_years must be an int or null, got {out['experience_years']!r}")
    return out


class LLMClient:
    """Runs one CV-profile extraction through the `claude` CLI's non-interactive print mode. Fails
    loudly (raises) on any bad response -- a missing binary, a timeout, a non-zero exit, stdout that
    is not the expected JSON envelope, or a `result` that is not the expected profile JSON -- rather
    than silently falling back to the deterministic extractor: this is a comparison path (see
    evals/cv/run.py --path=llm), not a production fallback chain (CLAUDE.md, "no safety nets").

    ``call=`` is swappable so tests never spawn a subprocess -- same seam as
    app/wa/luna_brain.py:Client(reply=...) and app/wa/meta.py:Client(transport=...).
    """

    def __init__(self, call=None):
        self._call = call or self._live_call

    def _live_call(self, system_text, user_text):
        import subprocess

        try:
            proc = subprocess.run(
                [_LLM_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
                 "--model", _LLM_MODEL, "--effort", _LLM_EFFORT, "--system-prompt", system_text],
                input=user_text, capture_output=True, text=True, timeout=_LLM_TIMEOUT_SEC,
            )
        except FileNotFoundError:
            raise RuntimeError(f"{_LLM_CLAUDE_BIN!r} is not on PATH -- CV.analyse_llm needs the "
                              f"Claude Code CLI installed and authenticated on this host")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude -p did not answer within {_LLM_TIMEOUT_SEC}s")
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p did not return JSON on stdout: {exc}: {proc.stdout[:300]!r}")
        if envelope.get("is_error"):
            raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(f"claude -p returned no result text: {envelope!r}")
        try:
            return _parse_llm_json_object(result)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p's result text was not the expected JSON object: {exc}: {result[:300]!r}")

    def profile(self, system_text, user_text):
        return _validate_llm_profile(self._call(system_text, user_text))


# --- document-type classification (TASK-81) -----------------------------------------------------
# A real, qualification-relevant gap the real reference system already closes
# (candidate_document_vision.py's doc_type taxonomy + explicit Helfer/Fachkraft discrimination):
# a Pflegehelfer/-fachhelfer/-fachassistent-level certificate must never read as satisfying a
# Fachkraft qualification path. A separate, small, cheap classification call rather than folding
# this into profile_from_text_llm -- that call reasons over merged CV+Urkunde text at consent time
# (analyse_candidate), long after a single upload needs its own type known (app/wa/api.py's media
# intake, TASK-67).

# auslaendisches_diplom (TASK-96 review 2026-09-14): a home-country nursing diploma used to come back as
# urkunde/fachkraft (live: Ukrainian "Nurse, Junior Specialist", Philippine BSN, Indian GNM), and the
# documents gate (app/wa/luna_brain.py:_is_qualification_document) counted it as the German Urkunde.
DOC_TYPES = ("urkunde", "auslaendisches_diplom", "lebenslauf", "defizitbescheid", "aufenthaltstitel", "dienstplan",
             "other")
CERTIFICATE_LEVELS = ("fachkraft", "helfer", "unknown")

_CLASSIFY_SYSTEM_PROMPT = f"""You read the transcribed text of a single document a nursing/Pflege
candidate sent over WhatsApp (a CV, a certificate, or something else) and classify it. Base the
classification only on what the text actually says.

Output nothing but a single JSON object -- no markdown fence, no commentary before or after it --
with exactly these keys:
{{
  "document_type": one of {list(DOC_TYPES)},
  "certificate_level": one of {list(CERTIFICATE_LEVELS)}
}}

document_type:
- "urkunde": a GERMAN nursing licence issued by a German authority -- the "Urkunde über die Erlaubnis zum
  Führen der Berufsbezeichnung" (Pflegefachfrau/Pflegefachmann, Gesundheits- und (Kinder-)Krankenpfleger(in),
  Altenpfleger(in)), whether the nurse trained in Germany or had a foreign qualification recognised there --
  or a German certificate for a helper-level title (see certificate_level).
- "auslaendisches_diplom": a nursing diploma, degree, licence or registration issued OUTSIDE Germany (a
  home-country nursing diploma, a Bachelor of Nursing, a nursing-council registration), in any language or as a
  translation, even when it calls itself a certificate or Urkunde. It is not a German recognition document.
- "lebenslauf": a CV/resume.
- "defizitbescheid": an official notice of a recognition deficiency (Defizitbescheid).
- "aufenthaltstitel": a residence permit/visa document.
- "dienstplan": a shift schedule/roster.
- "other": anything else, or if genuinely unclear.

certificate_level (only meaningful when document_type is "urkunde"; "unknown" otherwise):
- "fachkraft": a full 3-year Pflegefachkraft-level title (Pflegefachfrau/Pflegefachmann, Gesundheits- und
  (Kinder-)Krankenpfleger(in), Altenpfleger(in)) -- the level this board needs.
- "helfer": a HELPER-level certificate (Pflegehelfer, Pflegefachhelfer, Pflegefachassistent -- note
  "Pflegefachhelfer" contains the word "Fach" but is still helper level, NOT Fachkraft).
- "unknown": cannot tell from the text, or document_type is not "urkunde"."""


def classify_document(text, client=None):
    """-> {"document_type": ..., "certificate_level": ...} for one already-transcribed document.
    Raises loudly on a bad response (missing binary, timeout, non-JSON, an out-of-vocabulary
    value) -- same discipline as every other LLM call in this module, no silent "unknown" fallback
    manufactured here that the caller could mistake for a real classification."""
    cl = client or LLMClient()
    out = cl._call(_CLASSIFY_SYSTEM_PROMPT, json.dumps({"document_text": text[:8000]}, ensure_ascii=False))
    if not isinstance(out, dict) or out.get("document_type") not in DOC_TYPES \
            or out.get("certificate_level") not in CERTIFICATE_LEVELS:
        raise RuntimeError(f"claude -p's document classification was not the expected shape: {out!r}")
    return {"document_type": out["document_type"], "certificate_level": out["certificate_level"]}


def profile_from_text_llm(text, chat_history=None, client=None):
    """Same output shape as profile_from_text (roles/departments/qualifications/cities/
    experience_years/languages/skills/keywords/regierungsbezirke), but reasoning over the raw CV
    text (+ optional chat history) via the `claude` CLI instead of regex extraction. `keywords` is
    always empty (nothing downstream reads it -- profile_from_text only populates it for the
    /api/cv debug view); `regierungsbezirke` is still derived deterministically from the LLM's own
    `cities` output via `_regierungsbezirke_for_cities` -- reasoning about Bavarian administrative
    geography is not part of what this comparison is measuring."""
    system_text = _llm_system_prompt()
    user_text = json.dumps({"cv_text": text[:12000], "chat_history": chat_history or []}, ensure_ascii=False)
    cl = client or LLMClient()
    out = cl.profile(system_text, user_text)
    prof = {k: list(dict.fromkeys(str(x) for x in out[k]))[:20] for k in _PROFILE_LIST_KEYS}
    prof["experience_years"] = out["experience_years"]
    prof["keywords"] = []
    prof["regierungsbezirke"] = _regierungsbezirke_for_cities(prof["cities"])
    return prof


def analyse_llm(filename=None, blob=None, text=None, chat_history=None, limit=50, client=None):
    """Same {profile, matches, used_llm, chars} shape as analyse() -- LLM-driven extraction
    (profile_from_text_llm) instead of regex extraction, then the SAME deterministic match() this
    module already uses: job-matching scoring is unrelated to this comparison, only profile
    extraction is (see evals/cv/README.md)."""
    txt = text if text is not None else extract_text(filename, blob or b"")
    txt = (txt or "").strip()
    if len(txt) < 20:
        raise ValueError("no readable text in the CV (scanned PDF? try DOCX or plain text)")
    prof = profile_from_text_llm(txt, chat_history=chat_history, client=client)
    return {"profile": prof, "matches": match(prof, limit), "used_llm": True, "chars": len(txt)}


# --- vision text extraction: images, and scanned (text-layer-less) PDFs (TASK-67) ------------------
#
# extract_text() has no signal for "scanned PDF, no text layer" vs. "genuinely empty" -- pdfplumber
# silently returns "" either way -- and has nothing at all for a bare image. This is the fallback for
# both: instead of a second, separate model call format, it reuses the exact same `claude` CLI
# subprocess pattern as LLMClient above, but asks the model to read a FILE via its own built-in Read
# tool rather than reasoning over text handed to it on stdin.
#
# A small spike (documented in TASK-67's backlog notes, not repeated here) confirmed this works, with
# two things that are NOT obvious from the CLI's own --help:
#   1. `--tools ""` (used by LLMClient/luna_brain.Client to silence every built-in tool) also removes
#      the Read tool -- with it, the model reports it has no way to open a local file at all. This
#      path needs `--restricted` alone (drops command/code-execution/WebFetch, keeps Read/Glob/Grep).
#   2. Even with Read available, it is confined to the CLI's cwd plus whatever `--add-dir` grants --
#      an arbitrary absolute path outside both is refused. So the downloaded bytes are written to a
#      throwaway, single-file temp directory, and THAT directory is both the `--add-dir` grant and the
#      subprocess cwd. The cwd matters as much as the grant (TASK-95 review 2026-09-14): with the
#      service's cwd (the repo root) inherited, a probe Read a stored original under data/wa_documents/
#      (data/wa.sqlite and .env sit in the same tree). `--no-session-persistence` keeps the CLI from writing a
#      session transcript (the document's text) under ~/.claude/projects/<that temp dir>/ per call.
# Confirmed live: a plain PNG with rendered text, and the same content re-saved as a one-page PDF
# with no text layer (a stand-in for a scanned Urkunde) were both read back correctly this way, and a
# blank image correctly produced the NO_TEXT_FOUND sentinel below -- so the CLI-first path is used
# here, per the user's stated CLI-first preference; the Anthropic SDK multimodal fallback the plan
# allows for is not needed and is not implemented.
_VISION_CLAUDE_BIN = os.environ.get("CV_VISION_CLAUDE_BIN", "").strip() or _LLM_CLAUDE_BIN
_VISION_MODEL = os.environ.get("CV_VISION_MODEL", "").strip() or _LLM_MODEL
_VISION_EFFORT = os.environ.get("CV_VISION_EFFORT", "").strip() or _LLM_EFFORT
_VISION_TIMEOUT_SEC = int(os.environ.get("CV_VISION_TIMEOUT_SEC", "") or _LLM_TIMEOUT_SEC)

_NO_TEXT_TOKEN = "NO_TEXT_FOUND"


class NoReadableText(RuntimeError):
    """The model read the file and reported no legible text (``NO_TEXT_FOUND``: blank, too dark, blurry, a photo
    without text). A final result for that file, not a transient failure: reading it again gives the same answer."""

_VISION_SYSTEM_PROMPT = f"""You are given the path to a single image or PDF file: a nursing
candidate's CV or Urkunde/qualification certificate sent over WhatsApp, possibly a photographed or
scanned document. Use your file-reading tool to open the exact path named in the user message, then
transcribe ALL text visible in it as plain text, in reading order -- names, qualification titles,
dates, institutions, stamps, everything legible. Do not summarize, translate, describe the layout, or
comment on it.

If the file has no legible text at all (blank, too dark, illegible), reply with exactly the single
token {_NO_TEXT_TOKEN} and nothing else.

Output nothing but the transcription (or that one token) -- no preamble, no markdown fence, no
commentary before or after it."""


class VisionClient:
    """Runs one image/scanned-document transcription through the `claude` CLI's non-interactive
    print mode, using its own built-in Read tool to view the file (see the module-level note
    above) -- not the Anthropic SDK: the CLI-first path was confirmed to work for this repo's
    purposes. Fails loudly (raises) on any bad response, same convention as LLMClient; a
    NO_TEXT_FOUND reply is validated by the caller (extract_text_vision), not here, since this
    class also gets used directly in tests with a fake ``call``.

    ``call=`` is swappable so tests never spawn a subprocess -- same seam as LLMClient/meta.Client.
    """

    def __init__(self, call=None):
        self._call = call or self._live_call

    def _live_call(self, file_path):
        import subprocess

        add_dir = str(pathlib.Path(file_path).resolve().parent)
        prompt = f"Read the file at {file_path} and transcribe it as instructed."
        try:
            proc = subprocess.run(
                [_VISION_CLAUDE_BIN, "-p", "--restricted", "--add-dir", add_dir, "--no-session-persistence",
                 "--output-format", "json", "--model", _VISION_MODEL, "--effort", _VISION_EFFORT,
                 "--system-prompt", _VISION_SYSTEM_PROMPT, prompt],
                capture_output=True, text=True, timeout=_VISION_TIMEOUT_SEC, cwd=add_dir,
            )
        except FileNotFoundError:
            raise RuntimeError(f"{_VISION_CLAUDE_BIN!r} is not on PATH -- CV vision extraction "
                              f"needs the Claude Code CLI installed and authenticated on this host")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude -p did not answer within {_VISION_TIMEOUT_SEC}s")
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p did not return JSON on stdout: {exc}: {proc.stdout[:300]!r}")
        if envelope.get("is_error"):
            raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(f"claude -p returned no result text: {envelope!r}")
        return result.strip()

    def transcribe(self, file_path):
        return self._call(file_path)


def extract_text_vision(blob, suffix=".png", client=None):
    """Image bytes (or a scanned, text-layer-less PDF's bytes) -> transcribed text, via
    VisionClient. Writes ``blob`` to a throwaway, single-file temp directory (removed afterwards
    either way) so the CLI's --add-dir grant and cwd never expose more than this one file.

    Raises loudly -- never returns silently-empty text -- when the model reports no readable text
    (``NO_TEXT_FOUND``) or answers with nothing usable: a document that fails vision extraction is
    not the same thing as an empty CV (CLAUDE.md, "no invented safety nets"); the caller decides
    what a failed intake means for the conversation, this function never papers over it.
    """
    import tempfile

    cl = client or VisionClient()
    with tempfile.TemporaryDirectory(prefix="cv_vision_") as tmp:
        path = pathlib.Path(tmp) / ("upload" + (suffix or ""))
        path.write_bytes(blob)
        text = cl.transcribe(str(path))
    stripped = (text or "").strip()
    if stripped.upper() == _NO_TEXT_TOKEN:
        raise NoReadableText("vision extraction found no readable text in the image/scanned document")
    if not stripped:
        raise RuntimeError("vision extraction found no readable text in the image/scanned document (empty reply)")
    return text


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


def analyse_candidate(phone, conn, cv_text=None, urkunde_text=None, limit=50, chat_limit=50, client=None):
    """CV/Urkunde intake for a WhatsApp candidate (TASK-67) -- the same analyse_llm() extraction
    path TASK-65 measured as the winner over the deterministic regex path (evals/cv/README.md),
    but folding in this thread's own chat history (app.wa.store.history) alongside whatever
    cv_text/urkunde_text the harness has already extracted from their uploads (app/wa/api.py's
    media intake merges those onto the Luna card), so the model reasons over everything the
    candidate has told Valentina, not one document read in isolation.

    ``conn`` is an already-open app.wa.store connection: the caller (inside app/wa/api.py's own
    ``ST._lock``/``ST.db()`` block) already holds one, so this never opens or locks a second one.
    The public ``/api/cv`` upload endpoint (app/main.py) has no phone and no history -- it calls
    analyse_llm()/analyse() directly and is completely untouched by this function's existence.
    """
    from .wa import store as ST

    parts = []
    if (cv_text or "").strip():
        parts.append("--- CV ---\n" + cv_text.strip())
    if (urkunde_text or "").strip():
        parts.append("--- Urkunde ---\n" + urkunde_text.strip())
    text = "\n\n".join(parts)
    history = ST.history(conn, phone, limit=chat_limit) if phone else []
    chat_history = [{"direction": r["direction"], "text": r["body"]} for r in history if (r.get("body") or "").strip()]
    return analyse_llm(text=text, chat_history=chat_history, limit=limit, client=client)
