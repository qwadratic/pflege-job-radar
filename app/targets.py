"""Targets: the one shape a crawl or a schedule points at.

    {"scope": "all" | "regierungsbezirk" | "city" | "clinic" | "ats_type" | "landkreis" | "board" | "job",
     "values": ["Regensburg", ...]}

The old request form (scope + comma-separated value) is still accepted; `parse()` normalises both.
"""
from . import data as D

SCOPES = ("all", "regierungsbezirk", "city", "clinic", "ats_type", "landkreis", "board", "job")


def parse(body):
    """body: {"target": {...}} or {"scope", "value"|"values"}. Returns {"scope", "values"} or raises ValueError."""
    t = body.get("target") if isinstance(body.get("target"), dict) else body
    scope = (t.get("scope") or "clinic").strip()
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    vals = t.get("values")
    if vals is None:
        vals = t.get("value")
    if isinstance(vals, str):
        vals = [v.strip() for v in vals.split(",") if v.strip()]
    vals = [str(v).strip() for v in (vals or []) if str(v).strip()]
    if scope != "all" and not vals:
        raise ValueError("values required for scope " + scope)
    return {"scope": scope, "values": [] if scope == "all" else vals}


def value_string(target):
    """Storage form on a run row (scope + one string)."""
    return ",".join(target["values"]) if target["values"] else "all"


def clinics_for(target):
    cs = D.clinics()
    scope, vals = target["scope"], target.get("values") or []
    low = {v.lower() for v in vals}
    if scope == "all":
        return [c for c in cs if c.get("status") != "nicht_mehr_im_plan"]
    if scope == "clinic":
        return [c for c in cs if c["clinic_id"] in set(vals)]
    if scope == "city":
        return [c for c in cs if (c.get("town") or "").lower() in low]
    if scope == "regierungsbezirk":
        return [c for c in cs if (c.get("regierungsbezirk") or "").lower() in low]
    if scope == "landkreis":
        return [c for c in cs if (c.get("landkreis") or "").lower() in low]
    if scope == "ats_type":
        # "firecrawl" = every site that has no working adapter (unlabelled, unsupported vendor or bot-walled); "" = no label
        blank = "" in vals or "none" in low
        fire = "firecrawl" in low
        return [c for c in cs if (c.get("ats_type") or "").lower() in low or (blank and not c.get("ats_type")) or (fire and c.get("fetch") == "firecrawl")]
    if scope == "board":
        return [c for c in cs if (c.get("board") or "").lower() in low or (c.get("careers_url") or "").lower() in low]
    if scope == "job":
        from . import config as A
        out = []
        for v in vals:
            j = next((x for x in D.jobs() if str(x["posting_id"]) == v), None)
            if not j:
                full = A.rest_get("v_postings", {"select": "posting_id,clinic_id", "posting_id": f"eq.{int(v)}"})
                j = full[0] if full else None
            if j and j.get("clinic_id"):
                out += [c for c in cs if c["clinic_id"] == j["clinic_id"]]
        return out
    raise ValueError(f"unknown scope {scope}")


def summary(target, lang="de"):
    scope, vals = target["scope"], target.get("values") or []
    names = {"de": {"all": "alle Kliniken", "regierungsbezirk": "Regierungsbezirk", "city": "Stadt", "clinic": "Klinik", "ats_type": "ATS",
                    "landkreis": "Landkreis", "board": "Board", "job": "Stelle"},
             "en": {"all": "all hospitals", "regierungsbezirk": "district", "city": "city", "clinic": "hospital", "ats_type": "ATS",
                    "landkreis": "county", "board": "board", "job": "job"}}[lang if lang in ("de", "en") else "de"]
    if scope == "all":
        return names["all"]
    if scope == "clinic":
        by = D.snapshot()["by_clinic"]
        vals = [by[v]["name"] if v in by else v for v in vals]
    shown = ", ".join(vals[:3]) + (f" +{len(vals) - 3}" if len(vals) > 3 else "")
    return f"{names[scope]}: {shown}"
