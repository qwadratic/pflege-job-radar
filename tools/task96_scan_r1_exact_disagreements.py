"""TASK-96 AC#1: for every distinct (employer_name, city) pair among open postings' observations,
replay pflege_jobs.registry.Matcher._match_content's R1_exact rung directly (same helpers, not a
reimplementation) and record every case where a UNIQUE employer-name hit gets refused by
other_town_disagrees -- then, for each refusal, whether the disagreeing city's OTHER clinic shares
an operator-string match with the refused candidate.

  set -a; source .env; set +a && .venv/bin/python tools/task96_scan_r1_exact_disagreements.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A  # noqa: E402
from pflege_jobs.registry import Matcher, employer_norm, city_key, _town_match  # noqa: E402

clinics = A.rest_get_all("clinics", {"select": "*"})
m = Matcher([dict(c) for c in clinics])

open_ids = [r["posting_id"] for r in A.rest_get_all("v_postings", {"select": "posting_id", "status": "eq.open"})]
print(f"{len(open_ids)} open posting(s)")

pairs = {}
BATCH = 400
for i in range(0, len(open_ids), BATCH):
    chunk = open_ids[i:i + BATCH]
    obs = A.rest_get_all("posting_observations", {
        "select": "posting_id,employer_name,city",
        "posting_id": "in.(" + ",".join(str(x) for x in chunk) + ")"})
    for o in obs:
        en_raw = o.get("employer_name") or ""
        city = o.get("city") or ""
        pairs.setdefault((en_raw, city), []).append(o["posting_id"])

print(f"{len(pairs)} distinct (employer_name, city) pair(s)")

refusals = []
for (en_raw, city), pids in pairs.items():
    en = employer_norm(en_raw)
    if not en:
        continue
    ck = city_key(city)
    cand = m.by_name.get(en) or []
    via = "by_name"
    if len(cand) != 1:
        cand2 = m.by_op.get(en) or []
        if len(cand2) == 1:
            cand, via = cand2, "by_op"
    if len(cand) != 1:
        continue
    c0 = cand[0]
    own_town = city_key(c0.get("town"))
    if not ck or _town_match(own_town, ck):
        continue
    others = [x for x in m._by_town(ck) if x["clinic_id"] != c0["clinic_id"]]
    if not others:
        continue
    op_a = (c0.get("operator") or "").strip()
    op_match = any((x.get("operator") or "").strip() == op_a and op_a for x in others)
    refusals.append({
        "employer_name": en_raw, "city": city, "via": via,
        "refused_clinic_id": c0["clinic_id"], "refused_clinic_name": c0.get("name"), "refused_clinic_town": c0.get("town"),
        "disagreeing_clinic_ids": [x["clinic_id"] for x in others], "disagreeing_clinic_names": [x.get("name") for x in others],
        "operator_string_matches": op_match, "n_postings": len(pids), "posting_ids": pids[:5],
    })

print(f"\n{len(refusals)} R1_exact/R2_operator refusal(s) (other_town_disagrees=True)")
op_match_n = sum(1 for r in refusals if r["operator_string_matches"])
print(f"  operator string MATCHES the disagreeing clinic: {op_match_n}")
print(f"  operator string DIFFERS: {len(refusals) - op_match_n}")

out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups", "task96-r1exact-disagreements-2026-09-23.json")
os.makedirs(os.path.dirname(out_path), exist_ok=True)
json.dump(refusals, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\nwrote {out_path}")

for r in refusals:
    print(f"  {r['refused_clinic_id']} {r['refused_clinic_name']!r} ({r['refused_clinic_town']}) "
          f"employer={r['employer_name']!r} city={r['city']!r} -> disagrees with {r['disagreeing_clinic_ids']} "
          f"op_match={r['operator_string_matches']} n_postings={r['n_postings']}")
