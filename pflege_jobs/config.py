"""Central config: sources, precedence, classification rules.

The regexes live in pflege_jobs/patterns.json (editable from the app's Settings page); this module
loads them into the module-level names classify.py has always used, so a rule change is a JSON edit
plus reload() -- no code change. Env PFLEGE_PATTERNS overrides the path."""
import json
import os
import re

# --- Sources & precedence (lower = more authoritative). Mirrors pflege_jobs.sources in DB.
SOURCES = {
    "krankenhausplan":  {"source_id": 10, "precedence": 1, "kind": "registry"},
    "employer_ats":     {"source_id": 20, "precedence": 2, "kind": "employer_ats"},
    "firecrawl_agent":  {"source_id": 25, "precedence": 2, "kind": "employer_ats"},
}

PATTERNS_PATH = os.environ.get("PFLEGE_PATTERNS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json")
PATTERNS = {}

# --- Employer classification (clinic vs non-clinic). Both lists checked.
# Conflict matrix: clinic token + WEAK non-clinic group (verband, sonstige) -> clinic
#                  clinic token + STRONG non-clinic group (altenhilfe, ambulant, wohnen, agentur) -> unknown
WEAK_NON_CLINIC_GROUPS = set()
CLINIC_PATTERNS = []            # [(name, regex), ...]
NON_CLINIC_PATTERNS = []
LEGAL_FORMS = ""

# --- Role classification: order matters (first match wins), evaluated on title + hauptberuf.
PFLEGE_TOKEN = ""               # gate: no nursing token at all -> nicht_pflege
STRONG_PFLEGE_TITLE = ""        # a strong token in the *title* overrides a nicht_pflege hit
NICHT_PFLEGE = ""
ROLE_RULES = []                 # [(role_class, regex), ...]
ROLE_FALLBACK = "sonstige_pflege"
# --- Intake policy: which role_classes are allowed into the database at all (experienced nursing only).
# Enforced in pflege_jobs.sinks.only_pflege() (all sinks) and pflege_jobs.cli.cmd_inbox().
EXCLUDED_ROLE_CLASSES = set()

QUALIFICATION_HINT = []
DEPARTMENT_HINT = []

# --- Description enrichment
HOUSING = ""; TARIFF = []; PAY_GRADE = ""; PAY_TEXT = ""; REQ_HEAD = ""; REQ_STOP = ""; EXPERIENCE = ""
EMAIL = ""; LANGUAGE_REQ = ""; BONUS = ""; CHILDCARE = ""; ANERKENNUNG = ""


def validate(p):
    """Every regex must compile; every section classify.py reads must exist. Raises ValueError."""
    def chk(rx, where):
        try:
            re.compile(rx, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"{where}: bad regex {rx!r}: {e}")
    for k in ("employer", "role", "qualification", "department", "enrichment"):
        if k not in p:
            raise ValueError(f"patterns: missing section {k!r}")
    for grp in ("clinic", "non_clinic"):
        for i, x in enumerate(p["employer"][grp]):
            chk(x["re"], f"employer.{grp}[{i}]")
    chk(p["employer"].get("legal_forms", ""), "employer.legal_forms")
    for k in ("pflege_gate", "nicht_pflege", "strong_pflege"):
        chk(p["role"][k], f"role.{k}")
    for i, x in enumerate(p["role"]["rules"]):
        chk(x["re"], f"role.rules[{i}]")
    for sec in ("qualification", "department"):
        for i, x in enumerate(p[sec]):
            chk(x["re"], f"{sec}[{i}]")
    e = p["enrichment"]
    for k, v in e.items():
        if isinstance(v, str):
            chk(v, f"enrichment.{k}")
        else:
            for i, x in enumerate(v):
                chk(x["re"], f"enrichment.{k}[{i}]")
    for k, v in (p.get("cv") or {}).items():
        if isinstance(v, str):
            chk(v, f"cv.{k}")
        else:
            for i, x in enumerate(v):
                chk(x["re"], f"cv.{k}[{i}]")
    return p


def _apply(p):
    g = globals()
    g["PATTERNS"] = p
    g["CLINIC_PATTERNS"] = [(x["name"], x["re"]) for x in p["employer"]["clinic"]]
    g["NON_CLINIC_PATTERNS"] = [(x["name"], x["re"]) for x in p["employer"]["non_clinic"]]
    g["WEAK_NON_CLINIC_GROUPS"] = set(p["employer"].get("weak_non_clinic_groups", []))
    g["LEGAL_FORMS"] = p["employer"]["legal_forms"]
    r = p["role"]
    g["PFLEGE_TOKEN"], g["NICHT_PFLEGE"], g["STRONG_PFLEGE_TITLE"] = r["pflege_gate"], r["nicht_pflege"], r["strong_pflege"]
    g["ROLE_RULES"] = [(x["role_class"], x["re"]) for x in r["rules"]]
    g["ROLE_FALLBACK"] = r.get("fallback", "sonstige_pflege")
    g["EXCLUDED_ROLE_CLASSES"] = set(p.get("excluded_role_classes", ["nicht_pflege", "ausbildung", "werkstudent_praktikum"]))
    g["QUALIFICATION_HINT"] = [(x["hint"], x["re"]) for x in p["qualification"]]
    g["DEPARTMENT_HINT"] = [(x["hint"], x["re"]) for x in p["department"]]
    e = p["enrichment"]
    g["HOUSING"], g["PAY_GRADE"], g["PAY_TEXT"] = e["housing"], e["pay_grade"], e["pay_text"]
    g["REQ_HEAD"], g["REQ_STOP"], g["EXPERIENCE"], g["EMAIL"] = e["req_head"], e["req_stop"], e["experience"], e["email"]
    g["LANGUAGE_REQ"], g["BONUS"], g["CHILDCARE"], g["ANERKENNUNG"] = e["language"], e["bonus"], e["childcare"], e["anerkennung"]
    g["TARIFF"] = [(x["label"], x["re"]) for x in e["tariff"]]


def load(path=None):
    with open(path or PATTERNS_PATH, encoding="utf-8") as f:
        return validate(json.load(f))


def reload(path=None):
    """Re-read patterns.json (or `path`) and recompile classify's regexes. Returns the dict."""
    p = load(path)
    _apply(p)
    from . import classify
    classify._compile()
    return p


def save(p, path=None):
    """Validate, then write atomically. Callers should reload() afterwards."""
    validate(p)
    path = path or PATTERNS_PATH
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


_apply(load())


def rx(p): return re.compile(p, re.IGNORECASE)
