"""Mechanics registry: every rule-based step of the pipeline, described for humans and callable for the Settings page.

A *mechanic* = a small deterministic function (or family) + the `patterns.json` section it reads + a DE/EN
explanation of what it does and where it is applied. The backend (`GET /api/mechanics`) renders the source
(`inspect.getsource` on `functions`), runs `try_(inputs)` for the "try it" box and executes `tests/test_mech_<id>.py`.
Nothing here changes behaviour: the registry only points at the functions the pipeline already uses.
"""
import csv
import os
import re
from dataclasses import dataclass, field

from . import config as C
from . import classify as K
from . import registry as R
from . import verify as V
from .sources import career_crawl as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_CSV = os.path.join(ROOT, "data", "registry", "clinics.csv")


@dataclass
class Mechanic:
    id: str
    title: dict                       # {"de","en"}
    description: dict                 # {"de","en"} 3–6 lines
    patterns_section: str             # key in patterns.json ("" = none)
    functions: list                   # callables, shown as source
    inputs: list                      # [{"name","label":{de,en},"example"}]
    try_: object                      # callable(inputs: dict) -> {"result":..., "rule":...}
    stage: str = ""                   # where in the pipeline it runs
    test_file: str = field(default="")

    def __post_init__(self):
        self.test_file = self.test_file or f"tests/test_mech_{self.id}.py"

    def as_dict(self, with_source=False):
        import inspect
        fns = []
        for f in self.functions:
            d = {"name": f.__name__, "module": f.__module__, "doc": (inspect.getdoc(f) or "")}
            if with_source:
                try:
                    d["source"] = inspect.getsource(f)
                except (OSError, TypeError):
                    d["source"] = ""
            fns.append(d)
        return {"id": self.id, "title": self.title, "description": self.description, "stage": self.stage,
                "patterns_section": self.patterns_section, "functions": fns, "inputs": self.inputs,
                "test_file": self.test_file}

    def run(self, inputs):
        return self.try_({i["name"]: inputs.get(i["name"], "") for i in self.inputs})


# ---------------------------------------------------------------- try_ implementations
def _try_employer(i):
    cls, rule = K.classify_employer(i["name"])
    return {"result": {"employer_class": cls, "name_norm": K.employer_norm(i["name"])}, "rule": rule}


def _try_role(i):
    role, rule = K.classify_role(i["title"], i.get("hauptberuf", ""), i.get("offer_kind", ""))
    return {"result": {"role_class": role, "excluded": role in C.EXCLUDED_ROLE_CLASSES}, "rule": rule}


def _try_qualification(i):
    q = K.qualification_hint(i["title"], i.get("hauptberuf", ""))
    return {"result": {"qualification_hint": q}, "rule": q}


def _try_department(i):
    d = K.department_hint(i["title"])
    return {"result": {"department_hint": d}, "rule": d}


def _try_enrichment(i):
    e = K.enrich_description(i["description"])
    fired = [k for k, v in e.items() if v not in (None, False, [], "")]
    return {"result": e, "rule": ",".join(fired) or None}


def _try_dedupe(i):
    key = K.fuzzy_key(i["title"], i["employer"], i.get("city", ""))
    return {"result": {"fuzzy_key": key, "employer_norm": K.employer_norm(i["employer"]),
                       "title_norm": K.norm_text(i["title"])}, "rule": "sha1(title_norm|employer_norm|city)"}


_MATCHER = None


def _matcher():
    global _MATCHER
    if _MATCHER is None:
        rows = list(csv.DictReader(open(REGISTRY_CSV, encoding="utf-8")))
        for r in rows:
            r["beds"] = int(r["beds"]) if (r.get("beds") or "").isdigit() else None
        _MATCHER = R.Matcher(rows)
    return _MATCHER


def _try_clinic_link(i):
    m = _matcher().match(i["employer"], i.get("city", ""))
    if not m:
        return {"result": {"clinic_id": None, "score": None}, "rule": None}
    cid, rule, score = m
    site = next((c for c in _matcher().clinics if c["clinic_id"] == cid), {})
    return {"result": {"clinic_id": cid, "clinic_name": site.get("name"), "town": site.get("town"), "score": score}, "rule": rule}


_TOWNS = None


def _towns():
    global _TOWNS
    if _TOWNS is None:
        _TOWNS = {K.norm_text(r["town"]) for r in csv.DictReader(open(REGISTRY_CSV, encoding="utf-8")) if r.get("town")}
    return _TOWNS


def _try_bavaria(i):
    plz = (i.get("plz") or "").strip() or None
    v = CC.in_bavaria(i.get("city") or None, plz, i.get("region") or None, _towns())
    why = ("region" if i.get("region") else "plz" if plz else "town list") if v is not None else "undecidable"
    return {"result": {"in_bavaria": v}, "rule": why}


def _try_verify(i):
    code = int(i["status_code"]) if str(i.get("status_code", "")).strip().isdigit() else 200
    st, http, note = V.decide(code, i.get("body", ""), i.get("title", ""))
    return {"result": {"verify_status": st, "http": http, "title_tokens": V._title_tokens(i.get("title", ""))}, "rule": note or st}


def cv_profile(text):
    """CV text -> profile dict using patterns.json `cv` (skills, experience, languages) + the role/department/qualification
    classifiers on the lines that look like job titles. Pure: no registry, no network (the app adds cities + matching)."""
    cv = (C.PATTERNS or {}).get("cv") or {}
    low = K.norm_text(text)
    prof = {"roles": [], "departments": [], "qualifications": [], "experience_years": None, "languages": [], "skills": []}
    q = K.qualification_hint("", text[:4000])
    if q:
        prof["qualifications"].append(q)
    for line in text.splitlines():
        ln = line.strip()
        if 4 < len(ln) < 120 and re.search(r"pfleg|nurse|krankenschwester|hebamme|\bota\b|\bata\b|leitung|praxisanleit", ln, re.I):
            role, _ = K.classify_role(ln, "")
            if role not in C.EXCLUDED_ROLE_CLASSES and role != "sonstige_pflege" and role not in prof["roles"]:
                prof["roles"].append(role)
    if not prof["roles"] and re.search(r"pfleg|nurs", low):
        prof["roles"].append("pflegefachkraft")
    for s in cv.get("skills", []):
        try:
            if re.search(s["re"], low, re.I):
                prof["skills"].append(s["tag"])
        except re.error:
            continue
    dh = K.department_hint(text[:4000])
    if dh:
        prof["departments"].append(dh)
    for tag in prof["skills"]:
        d = K.department_hint(tag)
        if d and d not in prof["departments"]:
            prof["departments"].append(d)
    try:
        yrs = [int(m.group(1)) for m in re.finditer(cv.get("experience_years", r"(\d{1,2})\s*(?:jahre?|years?)"), low, re.I) if int(m.group(1)) <= 45]
        prof["experience_years"] = max(yrs) if yrs else None
    except re.error:
        pass
    try:
        for m in re.finditer(cv.get("languages", r"$^"), low, re.I):
            g = [x for x in m.groups() if x]
            lang = next((x for x in g if x in ("deutsch", "german", "englisch", "english")), None)
            lvl = next((x for x in g if re.fullmatch(r"[abc][12]", x)), None)
            if lang:
                tag = ("Deutsch" if lang in ("deutsch", "german") else "Englisch") + (" " + lvl.upper() if lvl else "")
                if tag not in prof["languages"]:
                    prof["languages"].append(tag)
    except re.error:
        pass
    return prof


def _try_cv(i):
    p = cv_profile(i["text"])
    return {"result": p, "rule": ",".join(p["skills"] + p["roles"]) or None}


# ---------------------------------------------------------------- registry
def _t(de, en):
    return {"de": de, "en": en}


REGISTRY = [
    Mechanic("employer_class", _t("Arbeitgeber-Klassifikation", "Employer classification"),
             _t("Ordnet einen Arbeitgebernamen als clinic / non_clinic / unknown ein. Zwei Regexlisten aus patterns.json → employer: "
                "clinic (Klinik, Krankenhaus, Uniklinikum, Ketten, Träger) und non_clinic (Altenhilfe, ambulant, Wohnen, Agentur…). "
                "Treffer nur in einer Liste → diese Klasse. Treffer in beiden: schwache Gruppe (verband, sonstige) verliert gegen clinic, "
                "starke Gruppe → unknown (nie raten). Kein Treffer → unknown = unklassifiziert, nicht „kein Krankenhaus“. "
                "Läuft in `cli inbox` für jede Beobachtung; die Regel steht in employers.class_rule. Manuelle Overrides (class_source=manual) überleben Re-Runs.",
                "Classifies an employer name as clinic / non_clinic / unknown. Two regex lists from patterns.json → employer: "
                "clinic (Klinik, Krankenhaus, university hospital, chains, operators) and non_clinic (elderly care, outpatient, housing, agencies…). "
                "A hit in only one list → that class. Hits in both: a weak group (verband, sonstige) loses to clinic, a strong group → unknown (never guess). "
                "No hit → unknown = unclassified, not \"not a hospital\". Runs in `cli inbox` for every observation; the rule is stored in employers.class_rule. "
                "Manual overrides (class_source=manual) survive re-runs."),
             "employer", [K.classify_employer, K.employer_norm],
             [{"name": "name", "label": _t("Arbeitgebername", "Employer name"), "example": "Klinikum Nürnberg gGmbH"}],
             _try_employer, stage="inbox → observations"),
    Mechanic("role_class", _t("Rollen-Klassifikation", "Role classification"),
             _t("Titel (+ Berufsbezeichnung) → eine von 12 role_class. Reihenfolge: 1) Pflege-Gate (patterns.role.pflege_gate) — ohne Pflege-Token ist es nicht_pflege; "
                "2) nicht_pflege-Regex (Arzt, MFA, Rettungsdienst…) gewinnt, außer der Titel trägt ein starkes Pflege-Token; 3) offer_kind AUSBILDUNG/PRAKTIKUM; "
                "4) geordnete Regeln (werkstudent, ausbildung, hebamme, ota_ata, praxisanleitung, leitung, apn_experte, fachpflege, pflegehelfer, pflegefachkraft) — erster Treffer gewinnt; "
                "5) Fallback sonstige_pflege. Klassen in excluded_role_classes werden beim Import verworfen (nur erfahrene Pflege). Regel → postings.role_rule.",
                "Title (+ occupation) → one of 12 role_class values. Order: 1) nursing gate (patterns.role.pflege_gate) — no nursing token means nicht_pflege; "
                "2) the nicht_pflege regex (physician, MFA, paramedic…) wins unless the title carries a strong nursing token; 3) offer_kind AUSBILDUNG/PRAKTIKUM; "
                "4) ordered rules (werkstudent, ausbildung, hebamme, ota_ata, praxisanleitung, leitung, apn_experte, fachpflege, pflegehelfer, pflegefachkraft) — first hit wins; "
                "5) fallback sonstige_pflege. Classes in excluded_role_classes are refused at ingest (experienced nursing only). Rule → postings.role_rule."),
             "role", [K.classify_role],
             [{"name": "title", "label": _t("Stellentitel", "Job title"), "example": "Fachkrankenpfleger Intensiv (m/w/d)"},
              {"name": "hauptberuf", "label": _t("Berufsbezeichnung (optional)", "Occupation (optional)"), "example": "Gesundheits- und Krankenpfleger/in"},
              {"name": "offer_kind", "label": _t("Angebotsart (ARBEIT/AUSBILDUNG)", "Offer kind (ARBEIT/AUSBILDUNG)"), "example": "ARBEIT"}],
             _try_role, stage="inbox → observations"),
    Mechanic("qualification", _t("Qualifikations-Hinweis", "Qualification hint"),
             _t("Erkennt aus Berufsbezeichnung + Titel die geforderte Ausbildung: GKiK (Kinderkrankenpflege), GuK, Altenpflege, generalistisch. "
                "Geordnete Regexliste patterns.qualification, erster Treffer. null = im Titel nicht genannt, nicht „keine“. Wird für Filter und CV-Matching genutzt.",
                "Derives the required licence from occupation + title: GKiK (paediatric), GuK, Altenpflege, generalistisch. Ordered regex list "
                "patterns.qualification, first hit. null = not stated in the title, not \"none\". Used by filters and the CV matcher."),
             "qualification", [K.qualification_hint],
             [{"name": "title", "label": _t("Stellentitel", "Job title"), "example": "Pflegefachkraft (m/w/d)"},
              {"name": "hauptberuf", "label": _t("Berufsbezeichnung", "Occupation"), "example": "Gesundheits- und Kinderkrankenpfleger/in"}],
             _try_qualification, stage="inbox → observations"),
    Mechanic("department", _t("Fachbereichs-Hinweis", "Department hint"),
             _t("Titel → einer von 17 Fachbereichen (Intensiv/IMC, Anästhesie, OP, Notaufnahme, Psychiatrie …). Geordnete Regexliste patterns.department, "
                "erster Treffer; Reihenfolge entscheidet bei Mehrfachtreffern („Intensiv“ vor „Innere“). null = nicht genannt. department_raw ist dagegen der Originaltext der Karriereseite. "
                "Filter „Fachbereich“, CV-Matching (Skills → Fachbereich) und die Chips in der Jobliste hängen daran.",
                "Title → one of 17 departments (Intensiv/IMC, Anästhesie, OP, Notaufnahme, Psychiatrie …). Ordered regex list patterns.department, first hit; "
                "order decides on multiple hits (\"Intensiv\" before \"Innere\"). null = not stated. department_raw is the career site's own wording. "
                "The department filter, CV matching (skills → department) and the job-list chips depend on it."),
             "department", [K.department_hint],
             [{"name": "title", "label": _t("Stellentitel", "Job title"), "example": "Pflegefachkraft Intensivstation (m/w/d)"}],
             _try_department, stage="inbox → observations"),
    Mechanic("enrichment", _t("Text-Anreicherung (enr_*)", "Description enrichment (enr_*)"),
             _t("Liest aus dem Anzeigentext: Wohnraum (+ Beleg-Phrase), Tarif (TVöD, TV-L, AVR…), explizite Entgeltgruppe (P8, KR8, EG13) mit Satz, "
                "Anforderungs-Abschnitt, Erfahrungssatz, Kontakt-E-Mails, Sprachniveau (A2–C1 nahe „Deutsch“), Willkommensprämie, Kita, Anerkennung. "
                "Alle Regexe in patterns.enrichment. false = nicht erwähnt, null = kein Text geholt. Läuft nur, wo eine Beschreibung vorliegt (Karriereseiten-Zeilen).",
                "Reads from the ad text: housing (+ evidence phrase), tariff (TVöD, TV-L, AVR…), explicit pay grade (P8, KR8, EG13) with its sentence, "
                "requirements section, experience sentence, contact e-mails, language level (A2–C1 near \"Deutsch\"), welcome bonus, childcare, recognition. "
                "All regexes in patterns.enrichment. false = not mentioned, null = no text fetched. Runs only where a description exists (career-site rows)."),
             "enrichment", [K.enrich_description],
             [{"name": "description", "label": _t("Anzeigentext", "Ad text"),
               "example": "Ihr Profil: abgeschlossene Ausbildung als Pflegefachkraft, 2 Jahre Berufserfahrung. Wir bieten Personalwohnungen, Vergütung nach TVöD-K P 8. Kontakt: pflege@klinik.de"}],
             _try_enrichment, stage="inbox → observations"),
    Mechanic("dedupe_key", _t("Dublettenschlüssel (fuzzy_key)", "Dedupe key (fuzzy_key)"),
             _t("Zwei Beobachtungen aus verschiedenen Quellen sind derselbe Job, wenn sha1(normalisierter Titel | employer_norm | Stadt) gleich ist. "
                "Titel-Normalisierung: Kleinschreibung, (m/w/d)-Varianten, Satzzeichen und Vollzeit/Teilzeit/ab sofort/(un)befristet entfernt. employer_norm streicht Rechtsformen (GmbH, gGmbH, e.V. …). "
                "Innerhalb einer Quelle zählt nie der fuzzy_key, sondern (source_id, source_ref) — URL-Varianten werden von canonical_ref zusammengeführt. "
                "Verwendet in resolve_postings() und `cli link-cross`.",
                "Two observations from different sources are the same job when sha1(normalised title | employer_norm | city) matches. "
                "Title normalisation: lowercase, (m/w/d) variants, punctuation and vollzeit/teilzeit/ab sofort/(un)befristet removed. employer_norm strips legal forms (GmbH, gGmbH, e.V. …). "
                "Within one source identity is (source_id, source_ref), never the fuzzy key — URL variants are folded by canonical_ref. "
                "Used by resolve_postings() and `cli link-cross`."),
             "", [K.fuzzy_key, K.employer_norm, K.norm_text],
             [{"name": "title", "label": _t("Titel", "Title"), "example": "Pflegefachkraft (m/w/d) Vollzeit"},
              {"name": "employer", "label": _t("Arbeitgeber", "Employer"), "example": "Klinikum Nürnberg gGmbH"},
              {"name": "city", "label": _t("Stadt", "City"), "example": "Nürnberg"}],
             _try_dedupe, stage="resolve / link-cross"),
    Mechanic("clinic_link", _t("Klinik-Zuordnung (KeZ)", "Clinic linking (KeZ)"),
             _t("Hängt ein Posting an einen Krankenhausplan-Standort. Sechs geordnete, konservative Regeln: R1 exakter Name, R2 Träger (eindeutig oder Ort = Stadt), "
                "R3 Token-Überlappung mit Standortname + gleicher Ort, R4 dasselbe gegen den Träger, R5 lockere Überlappung, wenn der Ort nur einen Standort hat, "
                "R6 mehrere Standorte desselben Trägers in einer Stadt → größter (Betten) und als ambiguous markiert. Überlebt mehr als ein Kandidat eine Regel, wird nicht verlinkt. "
                "Kein Regex — Tokenmengen, Stoppwörter und Aliase (LMU, TUM, FAU) in registry.py. Läuft in `cli link-clinics`; Regel → postings.clinic_match_rule.",
                "Attaches a posting to a Krankenhausplan site. Six ordered, conservative rules: R1 exact name, R2 operator (unique, or site town = city), "
                "R3 token overlap with the site name + same town, R4 the same against the operator, R5 loose overlap when the town has one site only, "
                "R6 several sites of one operator in one town → largest (beds), flagged ambiguous. If more than one candidate survives a rule, nothing is linked. "
                "No regex — token sets, stop words and aliases (LMU, TUM, FAU) live in registry.py. Runs in `cli link-clinics`; rule → postings.clinic_match_rule."),
             "", [R.Matcher.match, R.toks, R.city_key],
             [{"name": "employer", "label": _t("Arbeitgeber (wie im Posting)", "Employer (as posted)"), "example": "Klinikum Fürth Personalabteilung"},
              {"name": "city", "label": _t("Stadt", "City"), "example": "Fürth"}],
             _try_clinic_link, stage="link-clinics"),
    Mechanic("bavaria_filter", _t("Bayern-Filter", "Bavaria filter"),
             _t("Karriereportale von Ketten listen bundesweit; nur bayerische Standorte werden gespeichert. Entscheidung je Standort: addressRegion (Bayern/BY → ja, anderes Bundesland → nein), "
                "sonst PLZ-Bereiche (63[7-9]xx, 8xxxx, 881[3-7]x Lindau, 89[2-5]xx Neu-Ulm, 9[0-7]xxx), sonst Ortsname gegen die Registry-Ortsliste; bekannte Nicht-Bayern-Städte → nein. "
                "null = unentscheidbar → verworfen, außer der Träger ist nur in Bayern aktiv (bavaria_only_operator). Läuft im Karriereseiten-Crawler vor dem Speichern.",
                "Chain career portals list nationwide; only Bavarian locations are stored. Per location: addressRegion (Bayern/BY → yes, another state → no), "
                "else postal-code ranges (63[7-9]xx, 8xxxx, 881[3-7]x Lindau, 89[2-5]xx Neu-Ulm, 9[0-7]xxx), else town name against the registry town list; known non-Bavarian cities → no. "
                "null = undecidable → dropped, unless the operator is Bavaria-only (bavaria_only_operator). Runs in the career-site crawler before saving."),
             "", [CC.in_bavaria],
             [{"name": "city", "label": _t("Ort", "Town"), "example": "Neu-Ulm"},
              {"name": "plz", "label": _t("PLZ", "Postal code"), "example": "89231"},
              {"name": "region", "label": _t("addressRegion", "addressRegion"), "example": ""}],
             _try_bavaria, stage="career crawl"),
    Mechanic("verify_title", _t("Live-Prüfung (verify)", "Liveness check (verify)"),
             _t("Jede offene Stelle wird per HTTP erneut geholt. Entscheidung: 404/410 → gone (Posting läuft ab), 401/403/429 → blocked (Bot-Wand, Mensch kann öffnen), "
                "5xx/Transportfehler → error, 200 → live nur wenn eines von bis zu drei signifikanten Titel-Wörtern (ohne „Pflegefachkraft“, „Gesundheits“, „Krankenpfleger“) im Body vorkommt; "
                "200 ohne Titel + Phrase „nicht mehr verfügbar“ → gone, sonst error (JS-Seite/Liste). Läuft in `cli verify` (≤ 6 Worker) nach jedem Crawl für die neuen Postings.",
                "Every open posting is re-fetched over HTTP. Decision: 404/410 → gone (posting expires), 401/403/429 → blocked (bot wall, a human can open it), "
                "5xx/transport error → error, 200 → live only if one of up to three significant title words (excluding \"Pflegefachkraft\", \"Gesundheits\", \"Krankenpfleger\") appears in the body; "
                "200 without the title plus a \"no longer available\" phrase → gone, otherwise error (JS page / list). Runs in `cli verify` (≤ 6 workers) after every crawl for the new postings."),
             "", [V.decide, V._title_tokens],
             [{"name": "title", "label": _t("Titel", "Title"), "example": "Pflegefachkraft (m/w/d) Intensivstation Nürnberg"},
              {"name": "status_code", "label": _t("HTTP-Status", "HTTP status"), "example": "200"},
              {"name": "body", "label": _t("Seitentext", "Page text"), "example": "Willkommen auf der Intensivstation in Nürnberg – jetzt bewerben"}],
             _try_verify, stage="verify"),
    Mechanic("cv_profile", _t("CV-Profil", "CV profile"),
             _t("Lebenslauf-Text → Profil: Qualifikation und Rollen über dieselben Klassifikatoren wie bei Stellen (auf Zeilen, die nach Jobtitel aussehen), "
                "Skills aus patterns.cv.skills (Intensiv, Anästhesie, OP … je ein DE+EN-Regex), Fachbereiche aus Skills, Berufsjahre (patterns.cv.experience_years, größter Wert), "
                "Sprachen mit Niveau (patterns.cv.languages). Die App ergänzt Städte aus der Registry und bewertet Jobs: Rolle 40, Fachbereich/Skills 30, Stadt/Bezirk 20, Aktualität 10. Kein LLM nötig.",
                "CV text → profile: qualification and roles via the same classifiers used for jobs (on lines that look like job titles), "
                "skills from patterns.cv.skills (Intensiv, Anästhesie, OP … one DE+EN regex each), departments from skills, years of experience (patterns.cv.experience_years, max), "
                "languages with level (patterns.cv.languages). The app adds cities from the registry and scores jobs: role 40, department/skills 30, city/bezirk 20, freshness 10. No LLM required."),
             "cv", [cv_profile],
             [{"name": "text", "label": _t("Lebenslauf-Text", "CV text"),
               "example": "Gesundheits- und Krankenpflegerin, 6 Jahre Intensivstation und Anästhesie, Beatmung, Deutsch C1, München"}],
             _try_cv, stage="app (CV upload)"),
]

BY_ID = {m.id: m for m in REGISTRY}


def get(mid):
    return BY_ID[mid]


def describe(with_source=False):
    return [m.as_dict(with_source) for m in REGISTRY]


if __name__ == "__main__":
    import json
    for m in REGISTRY:
        print(m.id, "->", json.dumps(m.run({i["name"]: i["example"] for i in m.inputs}), ensure_ascii=False)[:160])
