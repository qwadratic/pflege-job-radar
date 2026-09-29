---
id: TASK-74
title: >-
  tools/reverify_and_clean.py: disabled placeability check, wrong relinks,
  non-durable city writes
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:11'
updated_date: '2026-09-18 20:11'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. (1) cmd_verify (:101) and cmd_firecrawl (:131) call verify_all/verify_one without towns=, so extract_location _placeable validation is disabled and unplaceable Einsatzort/Standort captures ("Karte", "Klinikum Nuernberg", "Juliusspital") count as TRUSTED_LOC evidence. Last production state (/tmp/reverify, 2026-09-16): 101 of 164 einsatzort rows on still-live postings are unplaceable, concentrated on mein-check-in.de (36), karriere.klinikum-nuernberg.de (16), kwm-klinikum.de (15), anregiomed.de (13). (2) cmd_relink (:248) nulls a postings clinic_id on any page-city vs clinic-town disagreement, so a group board whose JSON-LD carries the operator head-office address (kbo.de -> Muenchen on every posting) or an Einsatzort label that is itself a clinic name ("Klinikum Nuernberg") destroys a correct clinic link, while rows that do have a registry clinic in the stated town are orphaned instead of relinked. Already executed: 29 postings cleared by the 2026-09-16 22:24 relink --write run and all 29 are still clinic_id null today; 13 of the 29 lost a correct link (7x kbo.de Garmisch-Partenkirchen, 2x Wolfratshausen, 4x karriere.klinikum-nuernberg.de), 11 more should have been relinked to a different clinic instead of unlinked. This tool run already accounts for ~15% of the 198 currently-unlinked open postings. (3) apply --write-city (:285) PATCHes postings.city/plz directly, but postings.city is a golden field resolve_postings() re-derives from posting_observations on every ingest (the inbox drain app/crawl.py:760 runs after every crawl with resolve=True) -- so the only documented manual city-correction lane is silently undone at the next crawl. (4) apply --delete-non-bavaria (:299) writes its pre-delete dump from a 15-column COLS row set and never dumps the posting_observations it deletes on the next line -- already executed 2026-09-16, 973 postings + all observations deleted, 943 have no full-column backup and 963 have no observation copy anywhere; same code path governs every future non-Bavaria purge. (5) cmd_firecrawl (:129) builds its work list from verified.json but indexes postings.json, so any posting id kept by verified.json after a delete/refetch generation raises KeyError, killing the phase and discarding Firecrawl verdicts already paid for -- documented invocation crashes at row 41 of 50 after 40 credits, saving only 25. See /tmp/crawler_review_2026-09-18.md "tools/reverify_and_clean.py" section for full evidence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 cmd_verify and cmd_firecrawl pass towns=towns() (the helper the tool already defines) into verify_all/verify_one so _placeable validation is active for every einsatzort/plz_ort/jsonld capture
- [x] #2 cmd_relink only clears a postings clinic_id when the page city is placeable AND the registry has no clinic in that town; otherwise it leaves the existing link and reports the disagreement instead of nulling it; it passes board=[old_clinic_id] and employer_inherited into Matcher.match so relink decisions use the same ladder the production pipeline uses
- [x] #3 apply --write-city writes the corrected city/plz somewhere resolve_postings() does not overwrite on the next ingest (e.g. a posting_observations row or a dedicated override column), so a manual correction survives the next crawl
- [x] #4 apply --delete-non-bavaria selects the full row (SELECT *, matching data/purge_retired_sources.py backup pattern) and also dumps the matching posting_observations rows before deleting either, and writes the dump outside the volatile default /tmp/reverify path or the user is warned it is volatile
- [x] #5 cmd_firecrawl filters its work list to ids present in postings.json before indexing (todo = [p for p in done.values() if p[verify_status] in (blocked,error) and p[posting_id] in post]) and reports how many were dropped as stale, so a stale id no longer crashes the phase
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. AC1: pass towns=towns() into cmd_verify's verify_all() call and cmd_firecrawl's verify_one() call
   (computed once before cmd_firecrawl's loop) so extract_location's einsatzort/plz_ort placeability
   check (_placeable) is active on every reverify run, same as production callers.
2. AC5: in cmd_firecrawl, filter todo to ids present in postings.json BEFORE any post[...] indexing
   (root cause, once, not a per-callsite try/except) and print how many stale ids were dropped.
3. AC2: rewrite cmd_relink to decide every posting through registry.Matcher.match(employer, page_city,
   board=[old_clinic_id], employer_inherited=<echo-of-old-clinic heuristic>) instead of a bespoke
   city-string compare. Require the page city to pass _placeable() before any decision. Only clear
   (null) the link when Matcher's own ladder (content rules + board=[old]) returns nothing AND
   Matcher._by_town(city_key(page_city)) is empty (no clinic anywhere in that town); otherwise record
   it as a 'disagreement' and leave the existing link untouched. Confirm a proposed relink's town via
   registry.city_key/_town_match (not norm_text) so a registry town with a trailing qualifier
   ('Weiden i.d. Oberpfalz') still matches the bare posting city 'Weiden'.
4. AC3: add postings.city_override/plz_override columns (sql/001_schema.sql) that resolve_postings()
   coalesces ahead of the crawl-derived city/plz on every run. apply --write-city now PATCHes both the
   live city/plz (immediate visibility) and the *_override columns (durability across the next crawl).
5. AC4: apply --delete-non-bavaria now fetches select=* for both postings and posting_observations
   (chunked by id, same shape as data/purge_retired_sources.py) and dumps both to a new BACKUPS
   constant (the repo's own backups/ dir, durable) before any delete call runs; aborts loudly if a
   backup fetch itself comes back malformed instead of deleting blind.
6. Add tests/test_reverify_and_clean.py: one behavioral test per AC (mocked requests/EdgeSink,
   isolated STATE/BACKUPS dirs via monkeypatch, no live DB). Mutation-tested by git-stashing the fix
   and confirming every new test fails against the pre-fix file, then restored.
7. Delete the now-dead `post = _load('postings.json')` line at the top of cmd_apply, orphaned once its
   only caller (the delete-non-bavaria backup) was rewritten to fetch full rows live instead.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC1 -- cmd_verify's verify_all() call and cmd_firecrawl's verify_one() call now pass towns=towns()
(tools/reverify_and_clean.py). Evidence: tests/test_reverify_and_clean.py::
test_cmd_verify_passes_towns_so_placeability_is_enforced and ::
test_cmd_firecrawl_drops_stale_ids_and_passes_towns both capture the towns kwarg the tool actually
passed and assert it equals towns() (a real, non-empty Bavarian town set, e.g. contains 'münchen').
Mutation test: git stash of tools/reverify_and_clean.py back to the committed (pre-fix) file makes
both tests fail (towns kwarg missing/None); restored and green again.

AC2 -- cmd_relink rewritten to call registry.Matcher.match(employer, page_city, board=[old_clinic_id],
employer_inherited=<echo heuristic>) and to gate clearing on _placeable(page_city) plus
Matcher._by_town(city_key(page_city)) being empty; a disagreement (candidate town has a clinic but the
ladder can't pick it) is reported in a new 'disagreements' list and never pushed to the DB. A proposed
relink's town is confirmed with registry.city_key/_town_match, not norm_text. Evidence:
tests/test_reverify_and_clean.py::test_cmd_relink_routes_through_matcher_ladder builds a 5-clinic
fixture and dry-ran the real Matcher against it first (not hand-guessed) to fix expected rule/id
outcomes before writing assertions: (101) employer text echoes the OLD clinic's own operator (the
kbo.de shared-board shape) -- employer_inherited routes past R1/R2 and R4_tokens_op finds the real
site the page names -> relinked; (102) page town has zero registry clinics -> cleared; (103) page town
has two clinics the ladder cannot disambiguate -> left linked, reported as a disagreement, NOT pushed;
(104) page city 'Karte' is not placeable -> no action at all; (105) registry town 'Weiden i.d.
Oberpfalz' vs bare posting city 'Weiden' -> R0_board's canonical town match confirms the existing link
(no action) -- the old crude norm_text() compare would have wrongly flagged this as a disagreement and
either cleared or reported it. Also asserted the --write push only ever contains posting_ids 101/102
(cleared/changed), never 103/104/105. Mutation test: fails against the pre-fix cmd_relink (git stash),
confirmed, then restored.

AC3 -- added postings.city_override/plz_override (sql/001_schema.sql); resolve_postings() now does
city = coalesce(p.city_override, g->>'city') (same shape for plz). apply --write-city's PATCH body now
sets both city/plz (immediate visibility) and city_override/plz_override (durability). Evidence:
tests/test_reverify_and_clean.py::test_write_city_sets_override_columns_resolve_postings_prefers
asserts the exact PATCH body, pins the exact new SQL text (column decls + both coalesce expressions),
and runs a Python mirror of the new coalesce formula proving a stale re-crawl asserting the OLD city
cannot undo the override. tests/test_schema_resolve_postings.py's existing static-analysis suite
(TASK-73) still passes unchanged (104/104) -- the new columns/comment do not break its column or
assignment parsing. NOT verified against a live Postgres -- no DB access is permitted for this task.
The SQL was hand-traced (UPDATE...SET referencing the row's own pre-image via alias `p`, the exact
pattern already used two lines below for first_seen) rather than executed; honestly leaving this as the
one caveat on an otherwise offline-test-pinned AC.

AC4 -- apply --delete-non-bavaria now GETs select=* for both postings and posting_observations
(chunked by id, mirroring data/purge_retired_sources.py's page()+backup-before-delete shape) and dumps
both to a new BACKUPS module constant (repo backups/ dir -- the same durable location
data/purge_retired_sources.py and data/backup_postings.py already write to), strictly before any
delete call runs; raises SystemExit (loud, not silent) if a backup fetch comes back malformed instead
of deleting blind. Evidence: tests/test_reverify_and_clean.py::
test_delete_non_bavaria_backs_up_full_rows_before_deleting asserts select=* on both tables, that both
JSONL dumps exist and contain every id including a field (`description`) the old 15-column COLS slice
never carried, and that both GETs happened before either DELETE; ::
test_backups_dir_is_durable_not_the_volatile_tmp_state_default pins that the real BACKUPS constant is
outside any tmp path. Mutation test: both fail against the pre-fix code (git stash: old code used the
stale 15-col postings.json dict and never touched posting_observations at all), confirmed, then
restored.

AC5 -- cmd_firecrawl now filters todo to ids present in postings.json BEFORE any post[...] indexing
(root cause fixed once, not per call site) and prints how many stale ids were dropped. Evidence:
tests/test_reverify_and_clean.py::test_cmd_firecrawl_drops_stale_ids_and_passes_towns seeds
verified.json with posting_id 999 absent from postings.json; pre-fix this raises KeyError inside the
loop (confirmed via the same git-stash mutation test used for AC1); post-fix it prints "dropping 1
stale id(s) not in postings.json" and completes, leaving the stale row present but untouched in the
saved verified.json (999 is not silently dropped from state, just not re-verified this run).

Also removed the now-dead `post = _load('postings.json')` line at the top of cmd_apply -- it was only
ever read by the old delete-non-bavaria code this AC4 fix replaced; pyflakes confirmed it unused after
the rewrite. Separately noted, NOT fixed (pre-existing, not one of this task's 5 ACs, scope discipline):
cmd_relink still does `post = _load('postings.json')` and never reads it either (pyflakes: "local
variable 'post' is assigned to but never used") -- this was already true in the committed file before
any of this task's changes; left untouched.

Full offline suite (.venv/bin/python -m pytest -m "not network" -q), run to completion from scratch
after all fixes: 1 failed, 1200 passed, 1 skipped, 1195 deselected, 385.75s. The one failure is exactly
the pre-flagged tests/test_completeness_wp_jobs.py::
test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row (TASK-75 AC#1) -- nothing
else regressed. No live Supabase access was used or attempted for any AC (none permitted for this
task); every fix is verified offline against a real, hand-checked Matcher/registry fixture or a direct
SQL-text pin, never against a live DB write.

Re-audit 2026-09-18 (adversarial reviewer marked not-approved but returned no specific problem list --
independently re-checked every AC against the current diff rather than trusting the notes below).

Mutation test re-executed personally (not just re-read): backed up the working tree's
tools/reverify_and_clean.py + sql/001_schema.sql, ran `git checkout HEAD -- <both files>` to revert
to the pre-fix committed content, re-ran tests/test_reverify_and_clean.py -- all 6 tests failed
exactly as claimed (KeyError indexing `post` on a stale id; AttributeError, module has no attribute
'BACKUPS'; FileNotFoundError from the old cmd_apply reading postings.json before city-fix; missing
`towns` kwarg on the captured verify_all/verify_one calls). Restored both files from the backup;
`git diff --stat` after restore matches before (sql/001_schema.sql +39/-var, tools/reverify_and_clean.py
+120/-var, same as pre-audit); all 6 tests green again.

Re-ran the full offline suite from scratch (not reused from the prior pass): 1 failed, 1200 passed, 1
skipped, 1195 deselected, 383.10s. Same single pre-existing failure,
tests/test_completeness_wp_jobs.py::test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row
(TASK-75 AC#1) -- nothing else regressed.

Additional cross-checks this pass did NOT rely on the notes for:
- Confirmed _placeable(city, plz, towns) and verify_one(..., towns=None)/verify_all(..., towns=None)'s
  real signatures in pflege_jobs/verify.py match cmd_verify/cmd_firecrawl/cmd_relink's actual call
  sites (kwarg names, argument order) -- not just that the notes said so.
- Confirmed registry.Matcher.match(employer, city, board=None, description=None,
  employer_inherited=False)/_by_town/_town_match/city_key all exist with the exact signatures
  cmd_relink calls, and that app/crawl.py's production verify call (app/crawl.py:578-580) already
  passes towns=towns(), i.e. cmd_verify/cmd_firecrawl now match that same production shape.
- Grepped every writer of postings.city/plz repo-wide (edge/pflege-ingest/index.ts's 3 UPDATE
  statements, data/repair_split_merged.py's patch() bodies, sql/001_schema.sql): resolve_postings()
  is the only other writer of city/plz besides this tool, and it is the one now coalescing through
  city_override/plz_override -- confirmed no second silent-overwrite path exists that AC3 could still
  be undone by.
- Confirmed backups/ is already gitignored (.gitignore:18) and matches the exact convention
  data/purge_retired_sources.py (BK=.../backups) and data/backup_postings.py already use -- not a
  new, untested location.
- pyflakes tools/reverify_and_clean.py: only the one pre-existing 'post' unused-var warning in
  cmd_relink, and confirmed via the diff that exact line was never touched by any of this task's
  changes (present, unmodified, in both the pre-fix and post-fix version).
- Specifically interrogated cmd_relink's `_town_match(city_key(by_town.get(new)...), city_key(page_city))`
  guard on the Matcher-returned candidate before accepting a 'changes' relink, suspecting it might be
  exactly the kind of bespoke/invented logic AC2 warns against layering on top of the ladder. Traced
  it: every Matcher content rule except R1_exact/R2_operator (single global exact name/operator match)
  already enforces a town check internally, and _match_board's own R0_board branch enforces it too --
  so the guard is a structural no-op for R3/R4/R5/R6/board matches (provably, since they can't
  disagree with page_city and still return) and only ever filters the R1_exact/R2_operator case, which
  is precisely where registry.py's own _match_board docstring already documents a real, previously
  confirmed live failure (karriere.ameos.eu's org-defaulting bug stamping one clinic's exact name onto
  postings for other, unrelated towns). Removing the guard would reopen that exact documented failure
  mode for cmd_relink specifically; it does not change the outcome for any of the 5 scenarios the new
  test exercises (all currently-tested relinks already go through town-restricted rules, so the guard
  is verified inert there and only fires on the untested edge case it exists to close). Left unchanged.

No concrete defect found in a full line-by-line re-read of the diff (tools/reverify_and_clean.py,
sql/001_schema.sql, the registry.py/verify.py hunks it depends on) plus the cross-checks above. No
code changed this session; all 5 AC checks and the prior final summary stand as accurate.

Third pass 2026-09-18 (harness re-invoked again with "not-approved, no specific problem list"). Did not trust the prior two passes' notes -- independently re-derived evidence for every AC from the current diff and a live Matcher trace.

- Re-ran the mutation test personally: cp'd both changed files (tools/reverify_and_clean.py, sql/001_schema.sql) to /tmp, `git checkout HEAD -- <both>`, confirmed md5sum changed, ran tests/test_reverify_and_clean.py -> all 6 FAILED (confirmed via the "short test summary info" listing all 6), restored both files from the /tmp copies, md5sum matched the pre-revert hashes exactly, `git diff --stat` matched (sql +39/-var, tool +120/-var, same as before revert), re-ran -> 6 passed again.
- Isolated WHY test_cmd_relink_routes_through_matcher_ladder fails pre-fix, not just that it fails: reverted the same 2 files again and ran that one test alone with full output. Pre-fix printed "relink: 1 posting(s) point at the wrong clinic, 4 linked to a clinic in another town with no better match" and "wrote 5 link(s)" -- i.e. pre-fix code wrongly clears posting 103 (Bad Aibling, 2 real candidates the ladder can't disambiguate -- should be a disagreement, left linked), 104 (unplaceable "Karte" -- should be no action at all) AND 105 (Weiden vs registry "Weiden i.d. Oberpfalz" qualifier mismatch -- should be no action, the link is already correct), on top of the one legitimate clear (102) and the one legitimate relink (101). That is the real, concrete over-clearing bug AC2 describes, not merely the KeyError from the missing "disagreements" key (which also fires in the full run). Confirms the test is a genuine behavioral regression guard, not a vacuous/crash-only one.
- Hand-traced pflege_jobs/registry.py's actual Matcher ladder (toks/STOP/overlap/_by_town/_match_content/_match_board) against all 5 fixture postings myself, symbol by symbol, rather than re-reading the earlier notes' claimed rule names. Got the same rule/outcome the code and tests produce for each: 101 -> R4_tokens_op -> TARGET1 (employer_inherited=True correctly blocks R2_operator_town's operator-string shortcut; note OLD1 and TARGET1 happen to share operator "Generic Gruppe" in this fixture, so R2_operator_town would ALSO reach TARGET1 pre-fix for 101 specifically -- posting 101 alone does not discriminate pre/post-fix, postings 102/103/104/105 are what actually prove the fix, see above). 102: no candidate in Wolfratshausen -> cleared. 103: two Bad Aibling candidates neither R3/R4/loose can pick -> disagreement. 104: _placeable("Karte", None, towns()) is false -> no action. 105: _match_board's single-pool branch's _town_match("weiden oberpfalz", "weiden") prefix rule confirms WEID1 == old -> no action.
- Re-read pflege_jobs/verify.py's _placeable/verify_one/verify_all and pflege_jobs/registry.py's Matcher.match/_by_town/_town_match/city_key/by_id signatures directly and diffed them against every call site in tools/reverify_and_clean.py myself -- kwarg names/positions match everywhere (towns=, board=, employer_inherited=, city_key/_town_match in the new relink guard).
- pyflakes tools/reverify_and_clean.py: exactly one warning, the pre-existing (unmodified by this diff) "post" unused-var at line 246 in cmd_relink -- no new unused imports/vars from this task's changes (employer_norm, _placeable, Matcher, _town_match, city_key are all used).
- Grepped every writer of postings.city/plz repo-wide again: data/repair_split_merged.py only reads city and writes via the ingest function's resolve, never PATCHes city itself; data/sync_krankenhausplan_2026.py only reads it; edge/pflege-ingest/index.ts has no city/plz UPDATE outside resolve_postings(). resolve_postings() (now coalescing through city_override/plz_override) and this tool's --write-city remain the only two writers.
- Confirmed EdgeSink's "clinic_links" batch key that cmd_relink writes to is a pre-existing production endpoint -- edge/pflege-ingest/index.ts and index.template.ts both already route body.clinic_links through json_to_recordset; not a new/invented endpoint added by this task.
- Re-ran the full offline suite from scratch a third time: 1 failed, 1200 passed, 1 skipped, 1195 deselected, 386.19s -- identical to both prior runs; the one failure is still tests/test_completeness_wp_jobs.py::test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row (TASK-75 AC#1, pre-existing, unrelated). Nothing else regressed.

One residual edge case checked and deliberately left unchanged (pre-existing, not one of the 5 documented defects, not introduced by this diff): when Matcher's content rules that do NOT internally require a town match on a single global name hit (R1_exact/R2_operator, as opposed to their _town variants) return a `new` clinic in a DIFFERENT town than page_city, cmd_relink's town-match guard silently takes no action -- no change, no clear, no disagreement record. This is the same silent-no-action behavior the pre-fix code's norm_text-equality guard already had on this exact branch, so it is not a regression, and it matches registry.py's own documented "no match beats a wrong match" design (the AMEOS org-defaulting guard). Widening cmd_relink's reporting to cover this specific case would be scope beyond AC2's literal text (the disagreement path AC2 and the tests cover is specifically "not new, old, a town candidate exists"); recording it here rather than silently expanding scope to "fix" something outside the 5 documented defects.

No concrete defect found in this third independent pass. No code changed this session. All 5 AC checks and the final summary stand as accurate; status remains Done.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed all 5 defects in tools/reverify_and_clean.py from the 2026-09-18 crawler review. (1) cmd_verify
and cmd_firecrawl now pass towns=towns() into verify_all/verify_one, re-enabling extract_location's
placeability check for einsatzort/plz_ort captures. (2) cmd_relink now decides every posting through
registry.Matcher.match(employer, page_city, board=[old_clinic_id], employer_inherited=...) -- the same
ladder app/crawl.py uses -- instead of a bespoke city-string compare; it only clears a link when the
page city is placeable AND the registry has zero clinics in that town, otherwise it leaves the link
alone and reports a "disagreement"; a proposed relink's town is confirmed via the canonical
city_key/_town_match helpers, not norm_text. (3) postings.city_override/plz_override (new columns,
sql/001_schema.sql) let resolve_postings() prefer a manual correction over the crawl-derived value on
every future ingest; apply --write-city now writes both the live columns and the override columns.
(4) apply --delete-non-bavaria now backs up the FULL row (select=*) of both postings AND
posting_observations to a new durable BACKUPS dir (repo backups/, not /tmp) before deleting either,
and aborts loudly if that backup fetch itself fails. (5) cmd_firecrawl filters its worklist to ids
present in postings.json before indexing, so a stale id reports as dropped instead of raising KeyError
mid-run.

Verified with a new tests/test_reverify_and_clean.py (6 tests, one per AC plus one constant-level
check), every network/DB call mocked, run against a hand-checked Matcher fixture (dry-ran the real
Matcher first to fix expected outcomes, not hand-guessed). Every new test was mutation-tested: git
stash reverted the two changed files to their committed pre-fix content, all 6 tests failed exactly as
expected (KeyError, missing towns kwarg, wrong relink/clear/disagreement classification, missing
backup columns), then the stash was restored and all tests went green again. Full offline suite
(pytest -m "not network"): 1 failed, 1200 passed, 1 skipped, 1195 deselected -- the one failure is the
pre-flagged, unrelated tests/test_completeness_wp_jobs.py::
test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row (TASK-75 AC#1).

Honest caveat on AC3: the SQL side (coalesce(p.city_override, ...)) was hand-traced and offline-pinned
(exact SQL text + a Python mirror of its semantics) but never executed against a live Postgres --
no DB access is permitted for this task, and none was used or attempted for any AC. Files changed:
tools/reverify_and_clean.py, sql/001_schema.sql, tests/test_reverify_and_clean.py (new).

Re-audit 2026-09-18: an adversarial reviewer pass marked this not-approved with no specific findings.
Independently re-verified all 5 ACs against the current diff (not the notes): re-executed the
mutation test myself (git checkout HEAD on both changed files, confirmed all 6
tests/test_reverify_and_clean.py tests fail against the pre-fix content, restored, confirmed green),
re-ran the full offline suite from scratch (1 failed, 1200 passed, 1 skipped, 1195 deselected, 383s --
same single pre-existing TASK-75 AC#1 failure, nothing else), and separately traced every
postings.city/plz writer repo-wide, every Matcher/_placeable/verify_one/verify_all call site, and the
one intentionally-non-obvious guard in cmd_relink (its extra town check on the Matcher-returned
candidate, which closes a real, already-documented AMEOS org-defaulting failure mode rather than being
invented bespoke logic). No concrete defect found; no code changed this session.

Re-audit #2 (2026-09-18, third pass): adversarial reviewer again returned not-approved with no specific findings. Independently re-verified from scratch: personally re-ran the git-checkout-HEAD mutation test on both changed files (all 6 tests fail pre-fix incl. a concrete over-clearing bug on postings 103/104/105, not just a KeyError; pass post-fix), hand-traced the Matcher ladder arithmetic for all 5 relink fixture cases myself, re-diffed every call site against verify.py/registry.py's real signatures, grepped repo-wide for any other postings.city/plz writer and confirmed EdgeSink's clinic_links endpoint is pre-existing, and re-ran the full offline suite from scratch (1 failed, 1200 passed, 1 skipped, 1195 deselected -- same single pre-existing TASK-75 AC#1 failure). No concrete defect found; no code changed. All 5 ACs and status Done stand.
<!-- SECTION:FINAL_SUMMARY:END -->
