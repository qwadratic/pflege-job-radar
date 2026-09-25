---
id: TASK-147
title: >-
  TASK-144 follow-up: fuzzy_key/resolve_postings not merging the same vacancy
  across a board's different host/URL shapes -- looks systemic, worth a search
  for other affected boards
status: Done
assignee: []
created_date: '2026-09-24 01:06'
updated_date: '2026-09-24 11:31'
labels: []
dependencies: []
ordinal: 147000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-144's own investigation (2026-09-23/24, Klinikverbund Allgäu Kempten/Oberstdorf) found the same real vacancy (umantis Vacancy id 1581) stored as 3 SEPARATE postings across 3 different host/URL shapes for the SAME board over ~7 crawl dates: karriere-im.klinikverbund-allgaeu.de (retired host, bad numeric title), recruitingapp-5556.de.umantis.com (the real umantis app, clean title), and karriere.klinikverbund-allgaeu.de (the client's own branded domain, messy multi-line-card title before TASK-144's heuristic fix). None of the three ever merged into one posting via resolve_postings()/fuzzy_key, even across repeat crawls of the SAME host. TASK-144 fixed the title-corruption symptom (career_crawl.Crawler._heuristic() now takes only the first line of a multi-field card anchor) but explicitly did NOT investigate why the dedup/merge itself fails here -- that is this task. Ivan's steer 2026-09-24: this looks like it could be a systemic pattern (any board reachable through more than one host/URL shape, or any board whose title text varies slightly run-to-run, could have the same silent-duplication problem), worth searching for elsewhere before deciding how big a fix this needs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 fuzzy_key's actual computed value is compared across all 3 of Klinikverbund Allgäu's known duplicate rows (6018/10628/12328 for vacancy 1581, and the other 7 posting_ids from backups/task127_kempten_oberstdorf_20260923T173846Z.json) to pin down exactly why they differ -- title text, employer text, city text, or the fuzzy_key algorithm itself
- [x] #2 A search across the live registry for OTHER boards/clinics showing the same signature (the same clinic_id with multiple open postings whose titles are near-duplicates of each other, or the same clinic linked to more than one distinct source_url host) quantifies whether this is a one-board incident or a broader pattern
- [x] #3 A decision recorded on scope: fix scoped to Klinikverbund Allgäu specifically, or a general fuzzy_key/resolve_postings change, or folded into TASK-141 -- with the evidence from the two ACs above, not a guess
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-24 investigation (all live, read-only against SUPABASE_DB_POOLER_URL; no writes):

AC#1 -- fuzzy_key pinned exactly for all 10 posting_ids.
fuzzy_key(title,employer,city)=sha1(normalized_title|employer_norm|city), pflege_jobs/classify.py:200-208.
- 6018 (karriere-im.klinikverbund-allgaeu.de, bad numeric title '1581' pre-TASK-144-fix) -> fuzzy_key
  38c3307cbe... -- DIFFERS from 10628/12328 purely because of the corrupted title text (employer/city
  identical); title is exactly where 6018 diverges.
- 10628 (recruitingapp-5556.de.umantis.com, real umantis app) and 12328 (karriere.klinikverbund-allgaeu.de,
  client's vanity CMS) both carry the IDENTICAL clean title 'Pflegefachkraft (m/w/d) fuer unsere
  neonatologische Intensivstation' -> IDENTICAL fuzzy_key 5a7cce6290... -- title/employer/city do NOT
  diverge between these two, yet they were never merged. Root cause is NOT fuzzy_key's computation.
- Confirmed via mechanics.py's own docstring (line 256) and schema.IDENTITY=(source_id,source_ref):
  fuzzy_key is explicitly never the within-source merge key. The only code that folds different
  source_ref rows of the same source_id together is pflege_jobs/cli.py:same_source_variant_pairs(),
  via (a) canonical_ref() same-host URL normalization (works: posting 5826 already has 2 merged
  source_ref rows differing only by cHash) and (b) a cross-host "vanity domain" fold gated on
  _ats_job_id(url) matching one of 3 hardcoded vendor regexes (softgarden, personio, smartrecruiters)
  AND fuzzy_key matching. _ATS_JOB_ID_RX has ZERO pattern for umantis -- live-tested, both
  recruitingapp-5556.de.umantis.com/Vacancies/1581 and karriere.klinikverbund-allgaeu.de/.../1581
  return None from _ats_job_id(), so no "ats:"-group ever forms for 10628/12328 despite the identical
  fuzzy_key. That is the exact, confirmed root cause of the Klinikverbund non-merge.
- Other 7 posting_ids (5848,6019,6020,6022,6023,6025; 5826 already partly self-merged via the cHash
  fold) still carry their original bad numeric titles from the retired karriere-im host (2026-09-05
  crawl). Live-checked today (source_ref ILIKE across klinikverbund-allgaeu.de/umantis.com hosts):
  none of them currently has a clean-titled duplicate elsewhere yet -- their real vacancy ids
  (1531/856/1586/2128/2245/2324/2619) have not been re-observed by today's umantis/vanity-CMS crawl
  the way vacancy 1581 was, so they're stuck with a bad title (TASK-144 AC#4's scope) but not (yet)
  also duplicated.

AC#2 -- searched the live registry, not just Klinikverbund. Query: pflege_jobs.postings, status='open',
grouped by (clinic_id, fuzzy_key), host = urlsplit(external_url).netloc.
Result: 9 distinct clinic_ids (16202, 16211, 16235, 16290, 18802, 18813, 76108, 76111, 76301) currently
carry 2+ open postings with a BYTE-IDENTICAL fuzzy_key whose external_url host differs -- 47 such
(clinic_id, fuzzy_key) groups, 189 redundant open posting rows total (34 groups / 38 redundant rows if
clinic 16290's own outlier 138-row cluster under one highly generic recurring title is excluded --
that cluster is itself real evidence of the same host-split, between www.lmu-klinikum.de and
ea-kum1.external-pages-kum1.de, just with an unusually generic title making the count balloon).
Confirmed the gap generalizes past umantis: on smartrecruiters (clinics 76108/18802/16235/18813), the
SAME numeric vendor job id (e.g. 744000146491049) appears on both
jobs.smartrecruiters.com/<company>/<id>-<slug> and api.smartrecruiters.com/v1/companies/<company>/postings/<id>.
_ats_job_id()'s existing smartrecruiters regex (smartrecruiters\.com/[^/]+/(\d{9,})) matches the first
shape but not the second (extra /v1/companies/.../postings/ path depth) -- live-tested, api.* returns
None. So a vendor that ALREADY has vanity-domain regex support in this codebase still leaks duplicates
today, for an independent reason.
Also confirmed architecturally why link-cross's OTHER pass (cmd_link_cross's cross-source title-
similarity fold) can never catch any of this: its own code explicitly skips any pair sharing a
source_code (pflege_jobs/cli.py:353, "# never merge same-source here"), and every one of these hosts --
umantis, smartrecruiters (both shapes), the clinic's own www.*-klinikum.de site, even a 3rd-party board
like www.stepstone.de when it is the registry's careers_url -- all get crawled through career_crawl.py
and share ONE source_id (20, "employer_ats"). "Cross-source" in this pipeline does not mean "cross-
host": these duplicates fall exactly in the gap between both existing merge passes.
Conclusion: Klinikverbund Allgau is not an outlier -- it is the most complete example (title-corruption
+ multi-host + zero vendor-regex coverage all stacked on one board), but the same "identical fuzzy_key,
unmerged, same architectural gap" pattern is confirmed live at 8 OTHER clinics across at least 2 more
vendor families.

AC#3 -- scope decision, evidenced by AC#1/#2 above:
NOT scoped to Klinikverbund Allgau. The gap is in the shared same_source_variant_pairs()/_ats_job_id()
mechanism in pflege_jobs/cli.py that every employer_ats-sourced posting goes through, confirmed broken
for 2 independent reasons (umantis has no pattern at all; smartrecruiters' existing pattern has a path-
depth bug) across 9 clinics.
NOT the same mechanism as TASK-141. TASK-141 targets paraphrased TEXT that differs enough that
fuzzy_key itself misses a match across genuinely different aggregators (a text-similarity problem,
proposed fix = a new looser cross-source similarity pass). This bug is the opposite: fuzzy_key already
matches EXACTLY; the blocker is that the fold pass meant to act on cross-host duplicates within one
source is vendor-regex-gated and incomplete/buggy, not that similarity detection is too strict.
Folding this into TASK-141 would conflate 2 different fixes.
Right home: a general fix to same_source_variant_pairs()/_ats_job_id() in pflege_jobs/cli.py --
broaden/add vendor id-extraction patterns (umantis; smartrecruiters api.* path depth) and/or a vendor-
agnostic fallback (same clinic_id + EXACT fuzzy_key match, tighter-gated than pass 2's Jaccard
heuristic since exact match is already specific) for hosts with no known vendor-id shape at all (proxy
CMSes like karriere.klinikverbund-allgaeu.de/karriere-im...de, or 3rd-party boards like stepstone.de).
NOT implemented here, deliberately: this touches the shared merge function used across the whole
registry (blast radius = every clinic, not just 1 board), needs test coverage across at least the 3
now-confirmed-broken shapes (umantis, smartrecruiters api.*, and the still-unidentified LMU host pair)
before it is safe to ship, and has a real false-positive risk needing design judgement -- clinic
16290's 138-row same-fuzzy_key cluster under one highly generic title could legitimately be many
distinct real vacancies posted with the same boilerplate title rather than 1 vacancy duplicated 138x,
and a blind "same clinic + same fuzzy_key -> merge" rule would need to rule that out first. Per the
finalization guide's standing rule (no follow-up task creation without user approval), no new backlog
task was created -- recommending Ivan approve a dedicated follow-up scoped to
same_source_variant_pairs()/_ats_job_id() in pflege_jobs/cli.py before anyone implements it.

No code changed by this task (investigation + decision only, as the task's own AC shape allows). No DB
writes made -- all queries were SELECT-only against SUPABASE_DB_POOLER_URL.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Root cause pinned live: fuzzy_key(title,employer,city) is never the within-source merge key (schema.IDENTITY=(source_id,source_ref)); the only fold path is cli.same_source_variant_pairs()'s vanity-domain branch, gated on _ats_job_id(url) matching a hardcoded 3-vendor regex list that has zero pattern for umantis -- confirmed 10628 (umantis) and 12328 (client vanity CMS) share an IDENTICAL fuzzy_key yet never merge because _ats_job_id() returns None for both. Live registry search found this is systemic, not Klinikverbund-only: 9 distinct clinics, 47 clinic+fuzzy_key groups, 189 redundant open postings (34 groups/38 rows excluding one generic-title outlier cluster) share an exact fuzzy_key across 2+ hosts unmerged -- including smartrecruiters, where the existing vendor regex has an independent path-depth bug (matches jobs.smartrecruiters.com/<co>/<id> but not api.smartrecruiters.com/v1/companies/<co>/postings/<id>). link-cross's other pass (cross-source title-similarity) architecturally can never help: it explicitly skips same-source_code pairs, and every employer-site/ATS vendor in this pipeline shares one source_id (20, employer_ats) -- cross-source != cross-host here. Scope decision: NOT Klinikverbund-specific (systemic gap in same_source_variant_pairs()/_ats_job_id(), pflege_jobs/cli.py); NOT TASK-141's mechanism (that's a text-similarity gap, this is an exact match blocked by vendor-regex coverage); not implemented here per the task's own instruction (registry-wide blast radius, needs multi-vendor test coverage, real false-positive risk from generic recurring titles) -- recorded as a decision for a dedicated follow-up, not created as a new task per finalization's no-unapproved-follow-ups rule. No code changed, no DB writes; all evidence gathered via read-only SELECTs against SUPABASE_DB_POOLER_URL.
<!-- SECTION:FINAL_SUMMARY:END -->
