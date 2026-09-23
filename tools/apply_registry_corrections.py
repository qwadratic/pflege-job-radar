"""Применяет подтверждённые правки careers_url/ats_type из TASK-86 (раунд "20 измеренных клиник",
2026-09-22) к живой таблице pflege_jobs.clinics через EdgeSink().write_clinics().

Источник решений: заметка в backlog task 86 ("Свод по 20 клиникам", 2026-09-22) -- там же
измерения (_seed_obs/_vendor_rows на текущем и предложенном значениях) и обоснование по каждой
строке. Здесь -- только финальные целевые значения полей, которые реально меняются; остальные
16 колонок CLINIC_SPEC берутся из живой строки как есть (перезаписываются тем же значением, не
трогаются по смыслу).

НЕ включены в этот скрипт (не входят в TARGETS ниже) -- из 20 измеренных клиник:
  do_not_apply (предложение хуже текущего или ломает адаптер):
    37202  Sana Cham        -- proposed (jobs.sana.de Oracle HCM, JS-SPA) = 0 против текущих 29
    16233  Sana München     -- proposed (jobs.sana.de Oracle HCM, JS-SPA) = 0 против текущих 17
    57408  Sana Rummelsberg -- proposed (jobs.sana.de Oracle HCM, JS-SPA) = 0 против текущих 32
           (все три Sana: crawl_oracle не умеет читать hcmRestApi-эндпойнт, нужен отдельный адаптер)
    77406  Bezirkskliniken Schwaben (jobProfiles=Pflegedienst facet) -- proposed = 24 против
           текущих 56, чистый регресс, ats_type тут ни при чём (self_hosted и '' роутятся одинаково)
  could_not_measure:
    56404  Diakoneo -- и старый, и новый careers_url дают 0 через plain-HTTP адаптер (страница
           рендерится клиентским JS, метод вслепую на обоих URL, сравнивать нечем)
  уже применено отдельно, вне этой партии:
    76301  Klinikverbund Allgaeu Kempten -- исправлено вручную этим утром (только ats_type=umantis,
           careers_url НЕ менялся) через tools/apply_allgaeu_ats.py, см. заметки TASK-86. Не трогать.

Снимает бэкап живых строк в backups/ перед записью, пишет ТОЛЬКО перечисленные ниже 15 строк,
читает обратно и печатает результат по каждой. Идемпотентно: повторный запуск ничего не меняет,
если применилось один раз.

  set -a; source .env; set +a && .venv/bin/python tools/apply_registry_corrections.py
"""
import datetime, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A                                    # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC                      # noqa: E402
from pflege_jobs.sinks import EdgeSink                          # noqa: E402

# Только поля, которые реально меняются для каждой клиники (остальные CLINIC_SPEC-колонки идут
# из живой строки без изменений). careers_url/ats_type в апстриме идут через
# coalesce(nullif(excluded.col,''), stored) (edge/pflege-ingest/index.template.ts:75-76) -- поле,
# отсутствующее здесь для клиники, просто не переопределяется (берётся текущее живое значение).
TARGETS = {
    # apply_as_is -- только careers_url, ats_type не участвует (у всех и до, и после -- '')
    "66101": {"careers_url": "https://jobs.klinikum-ab-alz.de/Jobs"},                                                       # 62 -> 62
    "76201": {"careers_url": "https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote?selection3=3"},       # 1 -> 11
    "76203": {"careers_url": "https://jobs.bezirkskliniken-schwaben.de/Jobs"},                                              # 56 -> 56
    "16201": {"careers_url": "https://www.muenchen-klinik.de/stellenmarkt/"},                                              # 70 -> 70
    "16203": {"careers_url": "https://www.muenchen-klinik.de/stellenmarkt/"},                                              # 70 -> 70
    "16214": {"careers_url": "https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15"},  # 15 -> 11 (не регресс: 11 -- это и есть Pflege-only, проверено живьём по фильтру категорий на странице)
    "76114": {"careers_url": "https://jobs.bezirkskliniken-schwaben.de/"},                                                  # 56 -> 56
    "36202": {"careers_url": "https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote"},                    # 32 -> 32
    "77901": {"careers_url": "https://dongku.de/stellenangebote/"},                                                        # 40 -> 40
    # apply_corrected -- только careers_url; ats_type НЕ трогаем (остаётся typo3_jobs, см. заметку:
    # careers_url/ats_type коалесятся отдельно, blank у ats_type тут не требуется и не посылается)
    "16107": {"careers_url": "https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobLocation%3Akbo-Donau-Altm%C3%BChl-Kliniken"},  # 110 (сетевой борд kbo целиком, см. group_portal_for) -> 4 (реально своя площадка)
    "18712": {"careers_url": "https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobSite%3Akbo-Inn-Salzach-Klinikum+Wasserburg+am+Inn"},  # 110 (тот же сетевой борд) -> 12 (своя площадка; вердикт скорректирован с do_not_apply, см. заметку TASK-86)
    "17704": {"careers_url": "https://kbo.de/karriere/jobs"},                                                              # 110 -> 110 (без изменений по сути, но избавляет от мёртвой обёрточной страницы)
    "66301": {"careers_url": "https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/uebersicht-aller-stellen.html"},   # 1 -> 49 (ats_type НЕ ставим softgarden -- та же ловушка, что у 76301: URL+softgarden вместе дают 0)
    "27501": {"careers_url": "https://karriere.ge-passau.de/stellen/"},                                                    # 22 -> 22
    # apply_corrected -- только ats_type; careers_url НЕ трогаем (общая kbo-обёртка остаётся, но
    # seeded-адаптер umantis обходит group_portal_for, который на vendor-пути подменял её на весь
    # сетевой борд kbo.de)
    "18402": {"ats_type": "umantis"},                                                                                      # careers_url остаётся https://kbo-iak.de/kbo-karriere/stellenangebote-pflege; 10 (было 110 сетевого борда) -> корректная своя площадка
}

live = A.rest_get("clinics", {"select": "*", "clinic_id": f"in.({','.join(TARGETS)})"})
if len(live) != len(TARGETS):
    sys.exit(f"ожидалось {len(TARGETS)} строк, получено {len(live)}: {sorted(str(r.get('clinic_id')) for r in live)}")

stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = os.path.join(str(A.ROOT), "backups", f"registry_corrections_before_{stamp}.json")
os.makedirs(os.path.dirname(backup), exist_ok=True)
with open(backup, "w", encoding="utf-8") as f:
    json.dump(live, f, ensure_ascii=False, indent=1)
print(f"бэкап: {backup}")

rows = []
for r in sorted(live, key=lambda x: str(x["clinic_id"])):
    cid = str(r["clinic_id"])
    changes = TARGETS[cid]
    diff = "; ".join(f"{col} {r.get(col)!r} -> {new!r}" for col, new in changes.items())
    print(f"  {cid} {str(r.get('name'))[:34]:34} {diff}")
    # CLINIC_SPEC is a list of (column, pg_type) pairs, not bare column names
    rows.append({col: r.get(col) for col, _ in CLINIC_SPEC} | {"clinic_id": cid} | changes)

n = EdgeSink().write_clinics(rows)
print(f"записано строк: {n}")

back = A.rest_get("clinics", {"select": "clinic_id,name,careers_url,ats_type", "clinic_id": f"in.({','.join(TARGETS)})"})
ok = True
for r in sorted(back, key=lambda x: str(x["clinic_id"])):
    cid = str(r["clinic_id"])
    changes = TARGETS[cid]
    good = all(r.get(col) == new for col, new in changes.items())
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {cid} careers_url={r.get('careers_url')!r} ats_type={r.get('ats_type')!r}")
print("итог:", "все 15 строк применились" if ok else "НЕ ВСЁ ПРИМЕНИЛОСЬ, смотри выше")
