"""Candidate <-> clinic scoring (docs/autopilot.md, feature 5): a transparent weighted sum, meant to be read.

    role class 30 · region/radius 25 (same town 25, same Regierungsbezirk 15) · qualification 15 ·
    department overlap 10 · German level 10 (B2+ full, B1 half) · Anerkennung 10

Clinics and postings are registry rows (app.data snapshot shape, cached by seed.py in the autopilot SQLite).
Reasons are short German strings so the UI can print them next to the score.
"""
from collections import defaultdict
from datetime import datetime, timedelta

W_ROLE, W_REGION, W_QUALI, W_DEPT, W_GERMAN, W_ANERK = 30, 25, 15, 10, 10, 10

ROLE_LABEL = {"pflegefachkraft": "Pflegefachkraft", "fachpflege": "Fachpflege", "pflegehelfer": "Pflegehelfer", "ota_ata": "OTA/ATA",
              "leitung": "Leitung", "praxisanleitung": "Praxisanleitung", "apn_experte": "APN/Pflegeexperte", "hebamme": "Hebamme",
              "sonstige_pflege": "Sonstige Pflege"}
# role classes that are close enough to count half
ROLE_NEAR = {("pflegefachkraft", "fachpflege"), ("fachpflege", "pflegefachkraft"), ("pflegefachkraft", "praxisanleitung"),
             ("pflegefachkraft", "sonstige_pflege"), ("pflegehelfer", "sonstige_pflege"), ("leitung", "pflegefachkraft")}
# registry Fachrichtung code -> department vocabulary of the postings / candidates
FACH_TO_DEPT = {"INN": "Innere Medizin", "CHI": "Chirurgie/Orthopädie", "PSY": "Psychiatrie", "PSO": "Psychiatrie", "KJP": "Psychiatrie",
                "KIN": "Pädiatrie/Neonatologie", "KCH": "Pädiatrie/Neonatologie", "NEU": "Neurologie", "GUG": "Geburtshilfe", "GYN": "Geburtshilfe",
                "HD": "Dialyse/Nephrologie", "HCH": "Kardiologie", "NCH": "Chirurgie/Orthopädie", "URO": "Chirurgie/Orthopädie",
                "STR": "Onkologie", "NUK": "Onkologie", "MKG": "OP", "AUG": "OP", "HNO": "OP"}
GERMAN_RANK = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
ANERK_FACTOR = {"granted": 1.0, "not_needed": 1.0, "applied": 0.5, "deficit_notice": 0.25, "none": 0.0}
ANERK_LABEL = {"granted": "Anerkennung erteilt", "not_needed": "keine Anerkennung nötig", "applied": "Anerkennung beantragt",
               "deficit_notice": "Defizitbescheid", "none": "Anerkennung fehlt"}


def _quali_ok(cand_q, job_q):
    if not job_q or job_q == "generalistisch":
        return True
    q = (cand_q or "").lower()
    return {"GuK": "krankenpfleg", "GKiK": "kinderkrankenpfleg", "Altenpflege": "altenpfleg"}.get(job_q, job_q.lower()) in q


def _clinic_depts(clinic, jobs):
    d = {j.get("department_hint") for j in jobs if j.get("department_hint")}
    for code in clinic.get("fachrichtungen") or []:
        if code in FACH_TO_DEPT:
            d.add(FACH_TO_DEPT[code])
    return d


def score(candidate, clinic, jobs_of_clinic):
    """-> (score 0-100, reasons[]). jobs_of_clinic: open postings of this clinic (may be empty)."""
    jobs = list(jobs_of_clinic or [])
    reasons, s = [], 0.0
    role = candidate.get("role_class") or "pflegefachkraft"
    # role class 30
    same = [j for j in jobs if j.get("role_class") == role]
    near = [j for j in jobs if (role, j.get("role_class")) in ROLE_NEAR]
    if same:
        s += W_ROLE
        reasons.append(f"{ROLE_LABEL.get(role, role)} gesucht ({len(same)} offen)")
    elif near:
        s += W_ROLE / 2
        reasons.append("verwandte Rolle ausgeschrieben")
    elif not jobs and (clinic.get("jobs_open") or 0) > 0:
        s += W_ROLE / 3
        reasons.append("offene Stellen (Klasse unbekannt)")
    # region 25
    ctown, ktown = (candidate.get("city") or "").strip().lower(), (clinic.get("town") or "").strip().lower()
    if ctown and ctown == ktown:
        s += W_REGION
        reasons.append("gleicher Ort")
    elif candidate.get("region") and candidate.get("region") == clinic.get("regierungsbezirk"):
        s += 15
        reasons.append("gleicher Regierungsbezirk")
    elif candidate.get("region") == "Ausland":
        reasons.append("Kandidat im Ausland")
    # qualification 15
    hints = {j.get("qualification_hint") for j in jobs}
    if not jobs:
        s += W_QUALI / 2
    elif any(_quali_ok(candidate.get("qualification"), h) for h in hints):
        s += W_QUALI
        reasons.append("Qualifikation passt")
    else:
        reasons.append("Qualifikation passt nicht zur Ausschreibung")
    # departments 10
    overlap = set(candidate.get("departments") or []) & _clinic_depts(clinic, jobs)
    if overlap:
        s += W_DEPT
        reasons.append(f"{sorted(overlap)[0]} gesucht")
    # German 10
    lvl = GERMAN_RANK.get((candidate.get("german_level") or "").upper(), 0)
    if lvl >= 4:
        s += W_GERMAN
        reasons.append(f"Deutsch {candidate.get('german_level')}")
    elif lvl == 3:
        s += W_GERMAN / 2
        reasons.append("Deutsch B1 (Nachweis B2 fehlt)")
    else:
        reasons.append("Deutschniveau unter B1")
    # Anerkennung 10
    an = candidate.get("anerkennung_status") or "none"
    s += W_ANERK * ANERK_FACTOR.get(an, 0)
    reasons.append(ANERK_LABEL.get(an, an))
    return int(round(min(100, s))), reasons


def jobs_by_clinic(jobs):
    by = defaultdict(list)
    for j in jobs or []:
        if j.get("clinic_id"):
            by[str(j["clinic_id"])].append(j)
    return by


def rank(candidate, clinics, jobs, n=5, min_score=1):
    """Top-N clinics for a candidate: [{clinic_id, name, town, score, reasons, posting_id, posting_title}]."""
    by = jobs_by_clinic(jobs)
    out = []
    for cl in clinics:
        cj = by.get(str(cl["clinic_id"]), [])
        sc, why = score(candidate, cl, cj)
        if sc < min_score:
            continue
        post = next((j for j in cj if j.get("role_class") == candidate.get("role_class")), cj[0] if cj else None)
        out.append({"clinic_id": str(cl["clinic_id"]), "name": cl.get("name"), "town": cl.get("town"), "score": sc, "reasons": why,
                    "posting_id": post.get("posting_id") if post else None, "posting_title": post.get("title") if post else None})
    out.sort(key=lambda m: (-m["score"], m["name"] or ""))
    return out[:n]


def filter_candidates(candidates, criteria):
    crit = criteria or {}
    lvl_min = GERMAN_RANK.get((crit.get("german_level_min") or "").upper(), 0)
    depts = set(crit.get("departments") or [])
    out = []
    for c in candidates:
        if crit.get("role_class") and c.get("role_class") != crit["role_class"]:
            continue
        if crit.get("region") and c.get("region") != crit["region"]:
            continue
        if crit.get("qualification") and crit["qualification"].lower() not in (c.get("qualification") or "").lower():
            continue
        if lvl_min and GERMAN_RANK.get((c.get("german_level") or "").upper(), 0) < lvl_min:
            continue
        if crit.get("anerkennung") and c.get("anerkennung_status") != crit["anerkennung"]:
            continue
        if depts and not depts & set(c.get("departments") or []):
            continue
        out.append(c)
    return out


def throttle_counts(matches, policy, sim_now=None):
    """-> (active, weekly, max_c, max_k). active[candidate_id] = concurrent profiles out right now;
    weekly[str(clinic_id)] = profiles sent to that clinic in the last 7 days. The single source of truth for the
    two throttles (max_concurrent_profiles_per_candidate, max_profiles_per_clinic_per_week) – used by cohort_preview()
    at preview time and rechecked by engine.match_send()/cohort_send() right before they'd actually send."""
    matches = matches or []
    max_c = int(policy.get("max_concurrent_profiles_per_candidate") or 3)
    max_k = int(policy.get("max_profiles_per_clinic_per_week") or 5)
    active = defaultdict(int)
    weekly = defaultdict(int)
    week_ago = (datetime.fromisoformat(sim_now) - timedelta(days=7)) if sim_now else None
    for m in matches:
        if m.get("status") in ("sent", "clinic_interested", "interview"):
            active[m["candidate_id"]] += 1
        if m.get("status") in ("sent", "clinic_interested", "interview", "placed", "declined_by_clinic") and m.get("created_at"):
            if week_ago is None or datetime.fromisoformat(m["created_at"]) >= week_ago:
                weekly[str(m["clinic_id"])] += 1
    return active, weekly, max_c, max_k


def cohort_preview(criteria, candidates, clinics, jobs, policy, matches=None, sim_now=None, max_clinics=8):
    """Cluster candidates by the criteria; pick clinics with open postings for that role class in that region.
    Throttles: max_concurrent_profiles_per_candidate (active matches), max_profiles_per_clinic_per_week (matches sent in the last 7 days)."""
    crit = criteria or {}
    active, weekly, max_c, max_k = throttle_counts(matches, policy, sim_now)
    pool = filter_candidates(candidates, crit)
    cands, throttled_c = [], []
    for c in pool:
        (throttled_c if active[c["id"]] >= max_c else cands).append(c)
    role = crit.get("role_class")
    by = jobs_by_clinic(jobs)
    out_clinics, throttled_k = [], []
    for cl in clinics:
        cid = str(cl["clinic_id"])
        cj = by.get(cid, [])
        open_role = [j for j in cj if not role or j.get("role_class") == role]
        if not open_role:
            continue
        if crit.get("region") and cl.get("regierungsbezirk") != crit["region"]:
            continue
        if not cands:
            sc = 0
        else:
            sc = int(round(sum(score(c, cl, cj)[0] for c in cands) / len(cands)))
        row_ = {"clinic_id": cid, "name": cl.get("name"), "town": cl.get("town"), "open_postings": len(open_role), "score": sc,
                "profiles_this_week": weekly[cid]}
        (throttled_k if weekly[cid] >= max_k else out_clinics).append(row_)
    out_clinics.sort(key=lambda r: (-r["score"], -r["open_postings"]))
    return {"candidates": cands, "clinics": out_clinics[:max_clinics],
            "throttled": {"candidates": [c["id"] for c in throttled_c], "clinics": [k["clinic_id"] for k in throttled_k],
                          "max_concurrent_profiles_per_candidate": max_c, "max_profiles_per_clinic_per_week": max_k}}
