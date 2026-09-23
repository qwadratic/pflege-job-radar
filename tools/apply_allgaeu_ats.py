"""Выровнять ats_type=umantis у трёх клиник Klinikverbund Allgaeu (76301, 77801, 77802).

careers_url НЕ трогаем: измерено 2026-09-22, старый хост с umantis даёт 92 observations,
новый -- 10. Подробности и все четыре замера в заметках TASK-86.

Снимает бэкап текущих строк в backups/, пишет через EdgeSink, читает обратно и печатает
итог по каждой клинике. Идемпотентно: повторный запуск ничего не меняет.

  set -a; source .env; set +a && .venv/bin/python tools/apply_allgaeu_ats.py
"""
import datetime, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A                                    # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC                      # noqa: E402
from pflege_jobs.sinks import EdgeSink                          # noqa: E402

TARGETS = {"76301": "umantis", "77801": "umantis", "77802": "umantis"}

live = A.rest_get("clinics", {"select": "*", "clinic_id": f"in.({','.join(TARGETS)})"})
if len(live) != len(TARGETS):
    sys.exit(f"ожидалось {len(TARGETS)} строк, получено {len(live)}: {[r.get('clinic_id') for r in live]}")

stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = os.path.join(A.ROOT if hasattr(A, "ROOT") else ".", "backups", f"allgaeu_ats_before_{stamp}.json")
os.makedirs(os.path.dirname(backup), exist_ok=True)
with open(backup, "w", encoding="utf-8") as f:
    json.dump(live, f, ensure_ascii=False, indent=1)
print(f"бэкап: {backup}")

rows = []
for r in live:
    cid = str(r["clinic_id"])
    print(f"  {cid} {str(r.get('name'))[:34]:34} ats_type {r.get('ats_type')!r} -> {TARGETS[cid]!r}  (careers_url не меняем)")
    # CLINIC_SPEC is a list of (column, pg_type) pairs, not bare column names
    rows.append({col: r.get(col) for col, _ in CLINIC_SPEC} | {"clinic_id": cid, "ats_type": TARGETS[cid]})

n = EdgeSink().write_clinics(rows)
print(f"записано строк: {n}")

back = A.rest_get("clinics", {"select": "clinic_id,name,careers_url,ats_type", "clinic_id": f"in.({','.join(TARGETS)})"})
ok = True
for r in sorted(back, key=lambda x: str(x["clinic_id"])):
    good = r.get("ats_type") == TARGETS[str(r["clinic_id"])]
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {r['clinic_id']} ats_type={r.get('ats_type')!r} careers_url={r.get('careers_url')!r}")
print("итог:", "все три строки выровнены" if ok else "НЕ ВСЁ ПРИМЕНИЛОСЬ, смотри выше")
