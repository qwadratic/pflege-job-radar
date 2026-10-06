"""The measurement of TASK-431.9 (see tools/place_experiment.py). Reads the pulled tables and the replayed boards, writes numbers and
CSVs, writes nothing to the DB. Every kind of evidence and every weight is named in pflege_jobs/place_conf.py."""
import collections
import csv
import glob
import gzip
import json
import os
import re
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pflege_jobs import geo, place_conf as P          # noqa: E402
from tools import fill_clinic_plz as F                # noqa: E402

REVIEW = os.path.join(ROOT, "data", "registry", "plz_review.csv")
# the rule that wrote a PLZ (tools/fill_clinic_plz.py, TASK-431.7) -> the kind of evidence it stands for
RULE_KIND = {"rhv_id": "rhv_id", "dk_source": "dk_source", "khv_domain": "khv_domain", "khv_only_site_in_municipality": "khv_only_site",
             "khv_municipality_one_plz": "khv_one_plz", "khv_name_overlap": "khv_name_overlap", "imprint_khv_site": "imprint_plz",
             "imprint": "imprint_plz", "klinikradar": "klinikradar", "posting_modal": "posting_modal"}
TITLE_CITY = re.compile(r"(?:\bin|\bbei|\bam|\bim|\bfür|[|–]|\s-)\s+([A-ZÄÖÜ][\wäöüß.\-]+(?:\s+(?:an der|am|a\.\s?d\.|i\.\s?d\.|im|in der|bei|ob der)\s+[A-ZÄÖÜ][\wäöüß.]+|\s+[A-ZÄÖÜ][\wäöüß.\-]+)?)")


def jl(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def load_data(d):
    return jl(f"{d}/clinics.jsonl.gz"), jl(f"{d}/postings.jsonl.gz"), jl(f"{d}/observations.jsonl.gz")


# ---------------------------------------------------------------------------------------------------------------------------------
# A. the place of a clinic
# ---------------------------------------------------------------------------------------------------------------------------------
def clinic_side(gaz, clinics):
    """-> ({clinic_id: [Claim]}, {clinic_id: info}). Evidence of the written PLZ is rebuilt: the corrections table is closed to the anon
    key (RLS, no policy), so the rule is taken from plz_review.csv (the 117 verdicts of TASK-431.7, URL and quote there) or by running
    tools/fill_clinic_plz.py's own proposal and keeping the rule whose PLZ equals the written one (the 532 of the first fill)."""
    khv, rhv = F.load_sites()
    prop = {o["clinic_id"]: o for o in F.propose(clinics, khv, rhv)}
    verdict = {r["clinic_id"]: r for r in csv.DictReader(open(REVIEW, newline="", encoding="utf-8")) if r["verdict"] == "ACCEPT"}
    claims, info = {}, {}
    for c in clinics:
        cid, plz = c["clinic_id"], (c.get("plz") or "").strip() or None
        cl, why = [], None
        if plz:
            v, o = verdict.get(cid), prop.get(cid)
            if v and v["evidenced_plz"].strip() == plz:
                kind, why = RULE_KIND[v["proposed_rule"]], f"verdict {v['proposed_rule']} ({v['evidence_kinds']}) {v['source_url']}"
            elif o and o["new_plz"] == plz and o["rule"] in RULE_KIND:
                kind, why = RULE_KIND[o["rule"]], f"fill rule {o['rule']}: {o['site']}"
            else:
                kind, why = None, "no rule reproduces the written PLZ"
            info[cid] = {"plz": plz, "evidence": why, "kind": kind}
            if kind:
                cl.append(P.Claim(kind, plz, None, why))
            else:
                cl.append(P.Claim("klinikradar", plz, None, "written, evidence not reproducible: lowest independent kind"))
        else:
            info[cid] = {"plz": None, "evidence": None, "kind": None}
        town_geo = geo.clinic_centroid(c.get("town"), None, c.get("landkreis"))
        ars = town_geo.ars if town_geo and town_geo.ars else None
        if ars:
            cl.append(P.Claim("registry_town", None, None, f"{c.get('town')} ({town_geo.rule})", ars=ars))
        elif c.get("town"):
            cl.append(P.Claim("registry_town", None, c["town"], "town string, no single municipality by geo.clinic_centroid"))
        codes = geo.kreis_codes(c.get("landkreis"))
        if len(codes) == 1:
            cl.append(P.Claim("registry_landkreis", None, None, str(c.get("landkreis")), kreis=next(iter(codes))))
        # further sites of the same municipality in the directory: other PLZ the clinic may have (OR), never the main one
        a8 = F._ags8(c) if re.fullmatch(r"\d{5}", cid) else None
        others = sorted({s["plz"] for s in khv.get(a8, [])} - {plz}) if a8 else []
        for op in others:
            cl.append(P.Claim("khv_other_site", op, None, "KHV site of the same municipality"))
        info[cid]["other_plz"] = others
        info[cid]["ars"] = ars
        claims[cid] = cl
    return claims, info


# ---------------------------------------------------------------------------------------------------------------------------------
# B. the place of a posting
# ---------------------------------------------------------------------------------------------------------------------------------
def host(u):
    return (urlparse(u or "").netloc or "").lower().removeprefix("www.")


def load_replay(replay_dir):
    """-> (boards {board_id: status}, {url: [replay row]}) from the per-board files; a status other than ok keeps its reason."""
    boards, by_url = {}, collections.defaultdict(list)
    for sf in sorted(glob.glob(f"{replay_dir}/*.status.json")):
        st = json.load(open(sf))
        boards[st["board_id"]] = st
        rows = sf[:-len(".status.json")]
        if st.get("result") == "ok" and os.path.exists(rows):
            for r in jl(rows):
                for u in {r.get("url"), r.get("page"), r.get("ref")} - {None}:
                    by_url[u].append(r)
    return boards, by_url


def title_city(gaz, title):
    out = []
    for m in TITLE_CITY.finditer(title or ""):
        res = gaz.resolve(m.group(1))
        if res.status in ("unique", "ambiguous", "outside_bavaria"):
            out.append(m.group(1))
    return out[:1]


def struct_kind(city_source, plz, city):
    if city_source == "seed":
        return "seed_stamp"
    if city_source == "url":
        return "title_or_url_city"
    return "structured_plz_city" if plz else "structured_city"


def posting_claims(gaz, posting, obs_list, replay_rows):
    """Every version of the place of one posting: (kind, plz, city, source). The stored structured fields of every observation, the
    description's own mentions, a place in the title, and -- when the posting's page is in a replayed board -- the raw page place
    (JSON-LD / microdata / Einsatzort / PLZ Ort) and the adapter's own location."""
    cl, seen = [], set()

    def add(kind, plz, city, src):
        plz = P.clean_plz(plz)
        city = (city or None) if not isinstance(city, list) else (city[0] if city else None)
        if isinstance(city, str):
            city = city.strip() or None
        if not (plz or city):
            return
        k = (kind, plz, (city or "").lower())
        if k not in seen:
            seen.add(k)
            cl.append((kind, plz, city, src))

    for o in obs_list:
        pl = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        cs = pl.get("city_source")
        add(struct_kind(cs, P.clean_plz(o.get("plz")), o.get("city")), o.get("plz"), o.get("city"), f"obs {o['observation_id']} city_source={cs}")
        for l in o.get("locations") or []:
            a = (l or {}).get("adresse") or {}
            if (a.get("ort"), a.get("plz")) != (o.get("city"), o.get("plz")):
                add(struct_kind(cs, P.clean_plz(a.get("plz")), a.get("ort")), a.get("plz"), a.get("ort"), f"obs {o['observation_id']} locations")
    for kind, plz, city in P.text_places(posting.get("description")):
        add(kind, plz, city, "description text")
    for c in title_city(gaz, posting.get("title")):
        add("title_or_url_city", None, c, "title")
    for r in replay_rows:
        pp = r.get("page_place")
        if pp:
            add({"jsonld": "jsonld_address", "microdata": "jsonld_address", "einsatzort": "text_einsatzort", "plz_ort": "text_plz_ort"}[pp["src"]],
                pp.get("plz"), pp.get("city"), f"mirror page {pp['src']} ({r['board_id']})")
        for l in r.get("loc") or []:
            if r.get("city_source") == "seed":
                add("seed_stamp", l.get("plz"), l.get("city"), f"adapter loc seed ({r['board_id']})")
            else:
                add(struct_kind(None, P.clean_plz(l.get("plz")), l.get("city")), l.get("plz"), l.get("city"), f"adapter loc ({r['board_id']})")
    return cl
