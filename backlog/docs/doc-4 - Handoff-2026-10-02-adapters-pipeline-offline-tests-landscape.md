---
id: doc-4
title: 'Handoff 2026-10-02: adapters, pipeline, offline tests, landscape'
type: guide
created_date: '2026-10-02 07:17'
updated_date: '2026-10-02 07:18'
---
## Where things stand (2026-10-02 ~07:30 UTC, session 663542db)

### Code (nothing of the new work is on origin/main yet)
- `main` = 111e8f7 (Ivan's WhatsApp-leads PR #2 on top of the TASK-186 classifier aa1448c; the classifier is on disk in the live tree but the running services are older: they start from the tree and need a restart).
- Branch `wip/task-185-186-adapters-pipeline` (origin; local worktree `.claude/worktrees/integration`, branch `worktree-integration`) = main + 28 commits, tip 02851b7:
  - commits 1-7 agent A3: P&I popup read with stable `position,id` refs, Helios `ad_on_page:false`, BRK ad read, BITE `place_field`, Oracle CE REST adapter (`_oracle_cx_rows`);
  - commits 8-9 agent A2: place read per posting, `payload.city_source` marker on every seed stamp, kbo/abalz/nuernberg body text, `crawl_issue incomplete` only for pages the walk needed, `_sites`, geo `in_bavaria` False-only gate (tip of this part = **64a16be**, the ADAPTER DEPLOY COMMIT);
  - commits 10-28 agent A1: Matcher/pipeline (stamped city dropped before every rule, R6 never by bed count, R_jd_text needs the town, rule (ii) single-clinic board attaches only if no foreign place on the board, stale links cleared, link stage reads persisted stamps, registry_lint, `tools/replay_matcher.py`, change sets).
  - Full suite `-m "not network"`: only the known env/flake failures (PFLEGE_INGEST_URL, FIRECRAWL_API_KEY, missing sqlite tables, Playwright timing).
- Branch `wip/task-197-mirror` (worktree `agent-aba79e1b41c5c3b98`, 20 commits on main 111e8f7, tree clean): offline tests. `tests/mirror.py` store + replay (requests, urllib, Playwright), `tools/mirror.py` record/add/diff/status, permanent network guard in `tests/conftest.py`, completeness suite and every former network test on the mirror, rule in CLAUDE.md. Mirror data is local and git-ignored: `/home/exedev/repo/data/mirror` (202 MB so far, first fill NOT finished; disk was 95% used, 1.4 GB free). Remaining: finish the first fill (check `df` before each board, stop under 1 GB free), report per-board failures and named gaps, decide on deleting `crawl_snapshots/` (1.6 GB, owner's call).
- TASK-184 landscape page (artifact https://claude.ai/artifact/GV2Epn1pEbv3dQiwxLBHwn): sections 1-5, 9-10 done; method text written (`make_method.py`); sections 6-8 (requirement/benefit combos, pay, conditions) wait for the atoms extractor in `/home/exedev/.claude/jobs/663542db/tmp/landscape/atoms/` (atoms.py 135 atoms, test_atoms.py, atoms_by_posting.json exist; precision re-read round unfinished; then `atoms_summary.json`, rebuild with `build_report.py` + `build_artifact.py`, screenshots light/dark/phone, republish to the same path).

### Ivan's decisions of 2026-10-02
1. Deploy adapters (A2, A3, classifier) after crawl run 227 (adapter, started 03:00) and verify run 228 end (about 09:55 UTC): push 64a16be to origin/main, `git merge --ff-only` in /home/exedev/repo, `sudo systemctl restart pflege-web pflege-hunter`. Never restart while a crawl runs (`select run_id,status from crawl_runs order by 1 desc limit 3` in data/app.sqlite).
2. A1 pipeline: zero tolerance kept (no threshold). Deploy it after one real crawl with the new adapters: regenerate the sets from real rows with `tools/replay_matcher.py` (the sets in `data/relink_task185_*.json` were built from A2's recordings; 36 of 59 pins are provisional), then push, restart.
3. Close 332 postings: kp24 181, duplicates 72, UKA 41, same-URL 13, foreign Helios 9, not-a-vacancy 16 (files in job dir `tmp/package/`, built by `make_package.py`; each is `tools/apply_posting_changes.py <file> --dry-run`, then `--push --by "claude session 663542db (Ivan approved 2026-10-02: 3)"`). Held: Helios 27 (TASK-196), klinikverbund 19, tx_contrast 32. After the first crawl on the new P&I adapter: `data/pi_ref_switch_task184_set.json` (44).
4. Clinic 66103 `careers_url` to '' (`tmp/package/clinic_66103_careers_url.json`, `tools/apply_clinic_corrections.py`).
5. Classifier relabel: `data/relabel_task186_backfill.py --push --by ...` (125 role relabels; skip the 41 department hints), after the restart.
6. StepStone 892: keep until the first Altenpflege operator phase (TASK-189). 7. Altenpflege decisions D1-D6: postponed.
- Sana routing (9 clinics to one Oracle CX url, `tmp/package/clinics_sana_oracle_routing_9.json`): only together with or after the A1 pipeline.
- Ivan 2026-10-02: the multi-agent workflows (atoms, mirror) continue in the next session; this one should be forked to a cloud session.

### Open follow-ups filed
TASK-187 (sitemap max_maps), 188, 190-193, 194 (P&I form lead-in), 195 (verify by position id), 196 (Helios GWT ad), 197 (offline tests), 198 (registry data), 199 (out-of-Bavaria rows).

### Rules to keep
No safety nets (no invented caps/fallbacks/thresholds); tests never touch live sites (mirror); data writes only with Ivan's approval and exact counts, via the apply tools (backup, read-back, `corrections` rows with a reason code); commit to main, scan for secrets/PII before any push (public repo); Russian + terse with Ivan.
