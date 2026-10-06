"""The measurement of TASK-431.9: board stamps, spelling resolution, and the match of every linked posting against the clinic it is
linked to today. Reads the pulled tables and the replayed boards (tools/place_experiment.py), writes numbers and CSVs, no DB write."""
import collections
import csv
import json
import os
import re

from pflege_jobs import place_conf as P
from tools.place_analysis import clinic_side, host, load_data, load_replay, posting_claims

OWN_KINDS = ("text_einsatzort", "text_plz_ort", "title_or_url_city")
STRUCT_KINDS = ("structured_plz_city", "structured_city", "jsonld_address")


def own_places(claims):
    """The places a posting names independently of its structured field: text mentions and a place in the title."""
    return [{"city": c, "plz": p} for k, p, c, _ in claims if k in OWN_KINDS]


def stamp_key(gaz, plz, city):
    p = P.clean_plz(plz)
    if p:
        return p
    r = gaz.resolve(city) if city else None
    return f"city:{r.munis[0].ars}" if r and r.status == "unique" else None


def rule_group(rule):
    return "unlinked" if not rule else re.sub(r":.*", "", rule)


def fmt_claims(cl):
    return "; ".join(f"{k}:{p or '-'}/{c or '-'}" for k, p, c, *_ in cl)


def fmt_cclaims(cl):
    return "; ".join(f"{x.kind}:{x.plz or '-'}/{x.city or x.ars or x.kreis or '-'}" for x in cl)


def wcsv(out_dir, name, rows, fields=None):
    rows = list(rows)
    fields = fields or (list(rows[0]) if rows else [])
    with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def attach_replay(postings, obs_by, by_url):
    """posting_id -> the replay rows of its page (the stored url, external url or source ref of any observation is the key)."""
    out = {}
    for po in postings:
        keys = {po.get("external_url")} | {u for o in obs_by[po["posting_id"]] for u in (o.get("source_url"), o.get("external_url"), o.get("source_ref"))}
        seen, rows = set(), []
        for k in keys - {None}:
            for r in by_url.get(k, ()):
                if id(r) not in seen:
                    seen.add(id(r))
                    rows.append(r)
        out[po["posting_id"]] = rows
    return out


def find_stamps(gaz, boards, by_board, pclaims, prep):
    """{(board, value, mode): 'stamp' | 'suspect'}; mode 'stored' = the structured field of the stored observations, 'page' = the raw
    JSON-LD / microdata place of the mirrored page against what the adapter and the text say."""
    n_munis = {}
    for b, st in boards.items():
        ms = set()
        for c in st.get("clinics") or []:
            ms |= {m.ars for m in gaz.resolve(c.get("town")).munis} or {c.get("town")}
        n_munis[b] = len(ms)
    stamps = {}
    for b, pids in by_board.items():
        rows, rows2 = [], []
        for pid in pids:
            first = next((c for c in pclaims[pid] if c[0] in ("structured_plz_city", "structured_city")), None)
            if first:
                rows.append({"id": pid, "plz": first[1], "city": first[2], "own": own_places(pclaims[pid])})
            for r in prep[pid][:1]:
                pp = r.get("page_place")
                if pp and pp["src"] in ("jsonld", "microdata"):
                    rows2.append({"id": pid, "plz": pp.get("plz"), "city": pp.get("city"),
                                  "own": own_places(pclaims[pid]) + [{"city": l.get("city"), "plz": l.get("plz")} for l in r.get("loc") or []]})
        for k, v in P.board_stamps(gaz, rows, n_munis.get(b)).items():
            stamps[(b, k, "stored")] = v
        for k, v in P.board_stamps(gaz, rows2, n_munis.get(b)).items():
            stamps[(b, k, "page")] = v
    return stamps


def measure(gaz, clinics, postings, obs, boards, by_url, out_dir, suspect_is_stamp=True):          # noqa: C901 -- one measurement, read top to bottom
    clinic_by = {c["clinic_id"]: c for c in clinics}
    cclaims, cinfo = clinic_side(gaz, clinics)
    obs_by = collections.defaultdict(list)
    for o in obs:
        obs_by[o["posting_id"]].append(o)
    prep = attach_replay(postings, obs_by, by_url)
    pclaims, board_of = {}, {}
    for po in postings:
        pid = po["posting_id"]
        first = obs_by[pid][0].get("source_url") if obs_by[pid] else None
        board_of[pid] = prep[pid][0]["board_id"] if prep[pid] else "host:" + host(first or po.get("external_url"))
        pclaims[pid] = posting_claims(gaz, po, obs_by[pid], prep[pid])
    by_board = collections.defaultdict(list)
    for pid, b in board_of.items():
        by_board[b].append(pid)

    # -- D. board stamps; a structured claim whose value is flagged on its board is a stamp, not the posting's place
    stamps = find_stamps(gaz, boards, by_board, pclaims, prep)
    n_rekind = collections.Counter()
    for pid, cl in pclaims.items():
        new = []
        for k, plz, city, src in cl:
            flagged = stamps.get((board_of[pid], stamp_key(gaz, plz, city), "stored")) or stamps.get((board_of[pid], stamp_key(gaz, plz, city), "page"))
            if flagged and k in STRUCT_KINDS and (flagged == "stamp" or suspect_is_stamp):
                n_rekind[flagged] += 1
                k, src = "board_stamp", src + f" [{flagged}]"
            new.append((k, plz, city, src))
        pclaims[pid] = new

    # -- C. spelling: every distinct place string of the structured claims, weighted by occurrences
    cities = collections.Counter()
    for cl in pclaims.values():
        for k, plz, city, src in cl:
            if city and k in STRUCT_KINDS + ("board_stamp", "seed_stamp"):
                cities[(city, P.clean_plz(plz))] += 1
    res_stat, rule_stat, unresolved, consistent, per_str = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), {}
    for (city, plz), n in cities.items():
        r = gaz.resolve(city, plz)
        res_stat[r.status] += n
        rule_stat[(r.status, r.rule)] += n
        if r.status in ("unresolved", "non_place", "region"):
            unresolved[city] += n
        if r.status == "unique" and plz:
            info = gaz.plz_info(plz)
            consistent["plz_in_municipality" if plz in gaz.plz_of_muni.get(r.munis[0].ars, ()) else "plz_in_kreis_only" if r.munis[0].kreis in info.kreise
                       else "plz_unknown" if not info.valid else "plz_elsewhere"] += n
        per_str[city] = r
    typo_plz = collections.Counter((P.clean_plz(p), c) for cl in pclaims.values() for k, p, c, _ in cl if P.clean_plz(p) and not gaz.plz_info(p).valid)
    town_stat = collections.Counter(gaz.resolve(c["town"]).status for c in clinics if c.get("town"))

    # -- E. every linked posting against the clinic it is linked to
    cls_sig = {cid: [(c, gaz.claim_signature(c)) for c in cl] for cid, cl in cclaims.items()}
    by_ars, by_plz = collections.defaultdict(set), collections.defaultdict(set)
    for cid, sl in cls_sig.items():
        for c, sg in sl:
            for a in sg[2]:
                by_ars[a].add(cid)
            if sg[0]:
                by_plz[sg[0]].add(cid)

    def claims_of(pid):
        return [P.Claim(k, plz, city, src) for k, plz, city, src in pclaims[pid]]

    def candidates(pc):
        out = set()
        for c in pc:
            if c.kind in P.STAMP_KINDS:
                continue
            sg = gaz.claim_signature(c)
            for a in sg[2]:
                out |= by_ars.get(a, set())
            if sg[0]:
                out |= by_plz.get(sg[0], set())
        return out

    cat_by_rule, results, dis_rows, conf_sum = collections.defaultdict(collections.Counter), {}, [], collections.defaultdict(list)
    for po in postings:
        pid, cid, rule = po["posting_id"], po.get("clinic_id"), po.get("clinic_match_rule")
        if not cid:
            continue
        pc = claims_of(pid)
        m = P.match(gaz, pc, cclaims[cid])
        results[pid] = m
        for g in (rule_group(rule), "ALL linked"):
            cat_by_rule[g][m.category] += 1
            conf_sum[g].append(m.confidence)
        if m.category == "disagree":
            alts = sorted(((mm.confidence, oc, mm.level) for oc in candidates(pc) for mm in [P.match(gaz, pc, cclaims[oc])] if mm.category == "agree"), reverse=True)
            c = clinic_by[cid]
            dis_rows.append({
                "posting_id": pid, "rule": rule, "status": po["status"], "title": (po["title"] or "")[:90], "url": po.get("external_url"),
                "clinic_id": cid, "clinic_name": c["name"], "clinic_town": c["town"], "clinic_plz": c["plz"], "clinic_landkreis": c["landkreis"],
                "posting_place": fmt_claims(pclaims[pid]), "clinic_place": fmt_cclaims(cclaims[cid]),
                "decisive": f"{m.best[0].kind}:{m.best[0].plz or '-'}/{m.best[0].city or '-'}" if m.best else "", "conflict": int(m.conflict),
                "better_clinic": alts[0][1] if alts else "", "better_level": alts[0][2] if alts else "", "better_conf": round(alts[0][0], 3) if alts else "",
                "n_better": len(alts)})
    unl = collections.Counter()
    for po in postings:
        if po.get("clinic_id"):
            continue
        pc = claims_of(po["posting_id"])
        if not [c for c in pc if c.kind not in P.STAMP_KINDS and (gaz.claim_signature(c)[2] or gaz.claim_signature(c)[3])]:
            unl["no_own_place"] += 1
            continue
        agree = {c for c in candidates(pc) if P.match(gaz, pc, cclaims[c]).category == "agree"}
        unl["own_place_no_clinic_there" if not agree else "one_clinic_there" if len(agree) == 1 else "several_clinics_there"] += 1

    # -- write
    wcsv(out_dir, "disagreements.csv", sorted(dis_rows, key=lambda r: (r["rule"] or "", r["clinic_id"], r["posting_id"])))
    wcsv(out_dir, "unresolved_cities.csv", [{"city": c, "n": n, "status": per_str[c].status if c in per_str else ""} for c, n in unresolved.most_common()])
    wcsv(out_dir, "board_stamps.csv", [{"board": b, "value": k, "mode": mode, "verdict": v,
                                        "postings": sum(1 for pid in by_board[b] if any(stamp_key(gaz, p, c) == k for _, p, c, _ in pclaims[pid]))}
                                       for (b, k, mode), v in sorted(stamps.items())])
    wcsv(out_dir, "clinic_places.csv", [{"clinic_id": cid, "name": clinic_by[cid]["name"], "town": clinic_by[cid]["town"], "plz": i["plz"], "kind": i["kind"],
                                         "evidence": i["evidence"], "other_plz": " ".join(i["other_plz"]), "ars": i["ars"]} for cid, i in sorted(cinfo.items())])
    wcsv(out_dir, "boards_status.csv", [{"board": b, "result": s.get("result"), "rows": s.get("rows"), "seconds": s.get("seconds"),
                                         "html_pages_for_rows": s.get("html_pages_for_rows"), "page_place": s.get("page_place"), "error": (s.get("error") or "")[:300]}
                                        for b, s in sorted(boards.items())])
    cat_rows = [{"rule": g, "n": sum(c.values()), **{k: c.get(k, 0) for k in ("agree", "agree_weak", "disagree", "unknown")},
                 "mean_confidence": round(sum(conf_sum[g]) / max(len(conf_sum[g]), 1), 3)}
                for g, c in sorted(cat_by_rule.items(), key=lambda kv: -sum(kv[1].values()))]
    wcsv(out_dir, "per_rule.csv", cat_rows)
    wcsv(out_dir, "posting_match.csv", [{"posting_id": pid, "clinic_id": po["clinic_id"], "rule": po["clinic_match_rule"], "category": results[pid].category,
                                         "level": results[pid].level, "confidence": round(results[pid].confidence, 4), "conflict": int(results[pid].conflict),
                                         "board": board_of[pid]} for po in postings for pid in [po["posting_id"]] if pid in results])
    summary = {
        "gazetteer": {"municipalities": len(gaz.munis), "plz": len(gaz._plz)},
        "replay": {"boards_in_status": len(boards), "result": dict(collections.Counter(s.get("result") for s in boards.values())),
                   "rows": sum(s.get("rows") or 0 for s in boards.values()), "urls": len(by_url),
                   "postings_with_replay_row": sum(1 for v in prep.values() if v), "postings_with_page_place": sum(1 for v in prep.values() if any(r.get("page_place") for r in v)),
                   "postings": len(postings)},
        "clinics": {"n": len(clinics), "with_plz": sum(1 for i in cinfo.values() if i["plz"]), "kinds": dict(collections.Counter(i["kind"] for i in cinfo.values() if i["plz"])),
                    "with_other_plz": sum(1 for i in cinfo.values() if i["other_plz"]), "town_status": dict(town_stat)},
        "spelling": {"occurrences": sum(cities.values()), "distinct_strings": len(per_str), "status": dict(res_stat),
                     "rule": {f"{a}/{b}": n for (a, b), n in rule_stat.most_common()}, "plz_consistency_of_unique": dict(consistent),
                     "top_unresolved": unresolved.most_common(25), "typo_plz_total": sum(typo_plz.values()), "typo_plz_top": [[p, c, n] for (p, c), n in typo_plz.most_common(25)]},
        "stamps": {"flagged": {f"{b}|{k}|{m}": v for (b, k, m), v in stamps.items()}, "rekinded_claims": dict(n_rekind), "suspect_is_stamp": suspect_is_stamp},
        "match": {"per_rule": cat_rows},
        "unlinked": dict(unl),
    }
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), ensure_ascii=False, indent=1, default=str)
    return summary, pclaims, cclaims, results, board_of


def run(data_dir, replay_dir, geonames, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    gaz = P.Gazetteer(geonames=geonames)
    clinics, postings, obs = load_data(data_dir)
    boards, by_url = load_replay(replay_dir)
    print(f"gazetteer {len(gaz.munis)} municipalities, {len(gaz._plz)} PLZ; replay boards {len(boards)}, urls {len(by_url)}", flush=True)
    summary = measure(gaz, clinics, postings, obs, boards, by_url, out_dir)[0]
    print(json.dumps({k: summary[k] for k in ("replay", "clinics", "stamps", "unlinked")}, ensure_ascii=False, indent=1, default=str)[:5000])
    for r in summary["match"]["per_rule"][:25]:
        print(r)
