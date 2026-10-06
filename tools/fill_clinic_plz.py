"""Fill pflege_jobs.clinics.plz from the Krankenhausverzeichnis (TASK-431.2): the registry has 0 of 651 PLZ, the official
directory (data/registry/krankenhausverzeichnis_24.xlsx, Statistische Aemter des Bundes und der Laender, Stand 31.12.2024)
states one per site. Dry run by default: it prints what it would set and writes nothing.

Per clinic (the match rule is recorded with every proposal):
  rhv_id                        RH<id> is the RHV's own site id: exact
  dk_source                     DK<n> (Diakoneo list): the PLZ in the address of its `source` text
  khv_only_site_in_municipality KeZ site: the KHV lists one site in the clinic's municipality (town + Landkreis, pflege_jobs.geo)
  khv_domain                    several sites there, exactly one has the clinic's website domain
  khv_name_overlap              several sites there, exactly one shares >= 60 percent of the name tokens (registry.toks/overlap)
  khv_municipality_one_plz      several sites there, the clinic cannot be told apart, but all of them have the same PLZ
Anything else is listed as unresolved with its reason (ambiguous sites in a big city, no KHV row, ...): no PLZ is guessed.
A clinic that already has a PLZ is never changed: equal is `unchanged`, different is `conflict` and is listed.

  .venv/bin/python tools/fill_clinic_plz.py                         # reads the live clinics (read-only), prints the proposals
  .venv/bin/python tools/fill_clinic_plz.py --clinics rows.json     # same, from a JSON list of clinic rows (offline)
  .venv/bin/python tools/fill_clinic_plz.py --csv proposals.csv     # also writes every clinic's outcome as CSV
  .venv/bin/python tools/fill_clinic_plz.py --apply --by "<who>"    # the write: tools/apply_clinic_corrections.py --push (backup,
                                                                     # read-back, a pflege_jobs.corrections row per clinic)
The write needs the ingest function with the plz column deployed (edge/pflege-ingest) and the reason code from
sql/015_source_supplement.sql in pflege_jobs.correction_reasons.
"""
import argparse
import collections
import csv
import datetime
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pflege_jobs import geo                                   # noqa: E402
from pflege_jobs.registry import city_key, overlap, toks      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "data", "registry", "krankenhausverzeichnis_24.xlsx")
LAND_BAYERN = "09"
REASON_CODE = "source_supplement"
NAME_OVERLAP_MIN = 0.6          # the overlap coefficient of the audit (TASK-431.2) that separated sites in a town


def _sheet(wb, name):
    rows = list(wb[name].iter_rows(values_only=True))
    header = list(rows[2])
    return [dict(zip(header, r)) for r in rows[3:]]


def load_sites(xlsx=XLSX):
    """-> (KHV sites of Bavaria by municipality key, RHV sites of Bavaria by clinic_id). The municipality key is the
    8-digit AGS: '09' + Kreis + Gemeinde, as the workbook writes them."""
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    khv = collections.defaultdict(list)
    for r in _sheet(wb, " KHV_2024"):
        if r["Land"] == LAND_BAYERN:
            khv[LAND_BAYERN + r["Kreis"] + r["Gemeinde"]].append({
                "id": r["KH_ID_Pseudo"], "name": r["KH_Name"] or "", "site": r["Standortname"] or "", "plz": str(r["PLZ"]).strip(),
                "web": r["Internet"] or ""})
    rhv = {}
    for r in _sheet(wb, "RHV_2024"):
        if r["Land"] == LAND_BAYERN:
            rhv["RH" + str(r["RH_ID_Pseudo"])] = {"id": r["RH_ID_Pseudo"], "name": r["RH_Name"] or "", "plz": str(r["PLZ"]).strip()}
    return dict(khv), rhv


def _domain(url):
    u = (url or "").strip().lower()
    if not u:
        return ""
    host = urlparse(u if "//" in u else "http://" + u).netloc.replace("www.", "")
    return ".".join(host.split(".")[-2:])


def _ags8(clinic):
    """The municipality of the clinic's town within its Landkreis, as the AGS8 key of the workbook; None when the geo table names none."""
    g = geo.clinic_centroid(clinic.get("town"), None, clinic.get("landkreis"))
    return g.ars[:5] + g.ars[9:12] if g and g.ars else None


def _match_khv(clinic, sites):
    """-> (rule, site) or (None, why): the KHV site of the clinic among `sites`, the ones in its municipality."""
    if len(sites) == 1:
        return "khv_only_site_in_municipality", sites[0]
    town_toks = set(city_key(clinic.get("town")).split())
    mine = (toks(clinic.get("name")) | toks(clinic.get("operator"))) - town_toks
    dom = _domain(clinic.get("website"))
    scored = sorted(((int(bool(dom) and _domain(s["web"]) == dom), overlap(mine, toks(s["name"] + " " + s["site"]) - town_toks), i, s)
                     for i, s in enumerate(sites)), key=lambda t: t[:2], reverse=True)
    best = scored[0][:2]
    top = [t[3] for t in scored if t[:2] == best]
    if best[0] == 1 and len(top) == 1:
        return "khv_domain", top[0]
    if best[1] >= NAME_OVERLAP_MIN and len(top) == 1:
        return "khv_name_overlap", top[0]
    plzs = {s["plz"] for s in sites}
    if len(plzs) == 1:
        return "khv_municipality_one_plz", sites[0]
    return None, f"ambiguous: {len(sites)} KHV sites, {len(plzs)} PLZ in the municipality"


def propose_one(clinic, khv, rhv):
    """-> {"rule", "plz", "site"} or {"why"}."""
    cid = clinic["clinic_id"]
    if cid.startswith("RH"):
        r = rhv.get(cid)
        return {"rule": "rhv_id", "plz": r["plz"], "site": f"RHV {r['id']} {r['name']}"} if r else {"why": "no RHV row with this id"}
    if cid.startswith("DK"):
        m = re.search(r"Adresse:[^)]*?\b(\d{5})\s+[A-ZÄÖÜ]", clinic.get("source") or "")
        return {"rule": "dk_source", "plz": m.group(1), "site": "address in the source text"} if m else {"why": "no PLZ in the address of the source text"}
    if not re.fullmatch(r"\d{5}", cid):
        return {"why": "clinic_id is neither a KeZ, an RH nor a DK id"}
    ags8 = _ags8(clinic)
    if not ags8:
        return {"why": f"town {clinic.get('town')!r} in {clinic.get('landkreis')!r} names no single municipality"}
    sites = khv.get(ags8, [])
    if not sites:
        return {"why": f"no KHV site in the municipality {ags8}"}
    rule, found = _match_khv(clinic, sites)
    if rule is None:
        return {"why": found}
    return {"rule": rule, "plz": found["plz"], "site": f"KHV {found['id']} {found['site'] or found['name']}"}


def propose(clinics, khv, rhv):
    """One outcome per clinic, in clinic_id order: status fill | unchanged | conflict | unresolved, with the rule, the PLZ and the site."""
    out = []
    for c in sorted(clinics, key=lambda c: c["clinic_id"]):
        p = propose_one(c, khv, rhv)
        old = (c.get("plz") or "").strip()
        if "why" in p:
            status, new = "unresolved", ""
        else:
            new = p["plz"]
            status = "fill" if not old else "unchanged" if old == new else "conflict"
        out.append({"clinic_id": c["clinic_id"], "town": c.get("town") or "", "old_plz": old, "new_plz": new, "status": status,
                    "rule": p.get("rule", ""), "site": p.get("site", ""), "why": p.get("why", "")})
    return out


RULES = ("rhv_id", "dk_source", "khv_domain", "khv_only_site_in_municipality", "khv_name_overlap", "khv_municipality_one_plz",
         "imprint_khv_site", "imprint", "klinikradar", "posting_modal")                     # the last four: --verdicts, evidence of TASK-431.7


def outcomes_from_verdicts(clinics, path):
    """TASK-431.7: the ACCEPT rows of the review file, one outcome per row with its own evidence (page URL, quote, date read).
    The PLZ written is the evidenced one, not the weaker rule's candidate. A clinic that already has a PLZ is unchanged or conflict, never fill."""
    by = {c["clinic_id"]: c for c in clinics}
    out = []
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        if r["verdict"] != "ACCEPT":
            continue
        if r["clinic_id"] not in by:
            sys.exit(f"{path}: clinic {r['clinic_id']} is not in the registry")
        old = (by[r["clinic_id"]].get("plz") or "").strip()
        new = r["evidenced_plz"].strip()
        quote = f" '{r['imprint_quote']}'" if r["imprint_quote"] else ""
        out.append({"clinic_id": r["clinic_id"], "town": r["town"], "old_plz": old, "new_plz": new, "rule": r["proposed_rule"], "site": r["source_url"], "why": "",
                    "status": "fill" if not old else "unchanged" if old == new else "conflict",
                    "reason": f"clinics.plz was not written by the first fill (TASK-431.2); the review of TASK-431.7 found independent evidence that states it, rule {r['proposed_rule']}",
                    "evidence": [f"{r['source_url']}:{quote} (read {r['seen_at']})"]})
    return sorted(out, key=lambda o: o["clinic_id"])


def hold(outcomes, rules):
    """A copy of the outcomes in which a would-be fill by a rule outside `rules` becomes status 'held': reported, not written."""
    unknown = set(rules) - set(RULES)
    if unknown:
        sys.exit(f"unknown rule(s) {sorted(unknown)}; the rules are {', '.join(RULES)}")
    return [{**o, "status": "held"} if o["status"] == "fill" and o["rule"] not in rules else dict(o) for o in outcomes]


def corrections(outcomes, task):
    """The file tools/apply_clinic_corrections.py reads: one {plz, _why} per clinic to fill."""
    return {o["clinic_id"]: {"plz": o["new_plz"], "_why": {
        "code": REASON_CODE, "task": task,
        "reason": o.get("reason") or f"clinics.plz was never filled (0 of 651); the Krankenhausverzeichnis 2024 states it, matched by rule {o['rule']}",
        "evidence": o.get("evidence") or [f"data/registry/krankenhausverzeichnis_24.xlsx (Statistische Aemter, Stand 31.12.2024): {o['site']} -> PLZ {o['new_plz']}"]}}
        for o in outcomes if o["status"] == "fill"}


CSV_FIELDS = ["clinic_id", "town", "old_plz", "new_plz", "status", "rule", "site", "why"]


def report(outcomes, out=None):
    out = out or sys.stdout
    by =collections.Counter(o["status"] for o in outcomes)
    rules = collections.Counter(o["rule"] for o in outcomes if o["status"] in ("fill", "held", "unchanged", "conflict"))
    print(f"{len(outcomes)} clinics: " + ", ".join(f"{k} {by[k]}" for k in ("fill", "held", "unchanged", "conflict", "unresolved") if k != "held" or by[k]), file=out)
    for rule, n in rules.most_common():
        print(f"  {rule:32} {n}", file=out)
    for o in outcomes:
        if o["status"] in ("fill", "conflict", "held"):
            print(f"  {o['status']:8} {o['clinic_id']:7} {o['town'][:24]:24} {o['old_plz'] or '-':>5} -> {o['new_plz']}  {o['rule']}  {o['site'][:60]}", file=out)
    why = collections.Counter(re.sub(r"\d+", "N", o["why"]) for o in outcomes if o["status"] == "unresolved")
    for o in outcomes:
        if o["status"] == "unresolved":
            print(f"  unresolved {o['clinic_id']:7} {o['town'][:24]:24} {o['why']}", file=out)
    for w, n in why.most_common():
        print(f"  unresolved because: {w}: {n}", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clinics", help="JSON list of clinic rows instead of the live table")
    ap.add_argument("--xlsx", default=XLSX)
    ap.add_argument("--csv", help="write every clinic's outcome to this CSV file")
    ap.add_argument("--apply", action="store_true", help="write the PLZ through tools/apply_clinic_corrections.py --push")
    ap.add_argument("--by", help="who writes (required with --apply)")
    ap.add_argument("--task", default="TASK-431", help="the task the correction rows name")
    ap.add_argument("--rules", help="comma list: write only the fills made by these rules, report the others as held (default: all rules)")
    ap.add_argument("--verdicts", help="the review file (data/registry/plz_review.csv, TASK-431.7): fill from its ACCEPT rows with their own evidence instead of matching the directory")
    a = ap.parse_args(argv)
    if a.apply and not (a.by or "").strip():
        sys.exit("--apply needs --by: pflege_jobs.corrections records who made the change")

    if a.clinics:
        clinics = json.load(open(a.clinics, encoding="utf-8"))
    else:
        from app import config as A
        clinics = A.rest_get_all("clinics", {"select": "*", "order": "clinic_id"})
    khv, rhv = load_sites(a.xlsx)
    outcomes = outcomes_from_verdicts(clinics, a.verdicts) if a.verdicts else propose(clinics, khv, rhv)
    if a.rules:
        outcomes = hold(outcomes, [r.strip() for r in a.rules.split(",") if r.strip()])
    report(outcomes)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(outcomes)
        print(f"wrote {a.csv}")
    todo = corrections(outcomes, a.task)
    if not a.apply:
        print(f"\ndry run: nothing written; --apply --by <who> would set {len(todo)} PLZ")
        return
    if not todo:
        sys.exit("nothing to fill")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(ROOT, "backups", f"fill_clinic_plz_{stamp}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(todo, f, ensure_ascii=False, indent=1)
    print(f"corrections file: {path}")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "apply_clinic_corrections.py"), path, "--push", "--by", a.by.strip()], check=True)


if __name__ == "__main__":
    main()
