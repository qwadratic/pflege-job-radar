---
id: TASK-153
title: >-
  Matcher R2_operator has no town-disagreement guard on its single-candidate
  branch, unlike R1_exact
status: Done
assignee:
  - '@claude'
created_date: '2026-09-24 11:34'
updated_date: '2026-09-28 07:06'
labels: []
dependencies: []
ordinal: 153000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-24 while resolving TASK-103's Augustinum gGmbH postings. pflege_jobs/registry.py's Matcher.R2_operator rule (~line 207) matches an employer to a single candidate clinic by operator name alone, with no check that the posting's own city disagrees with that candidate's town -- unlike its R1_exact sibling (~line 199), which does guard on town disagreement. Live evidence: Augustinum gGmbH has exactly one Krankenhausplan clinic in the registry (München). R2_operator's single-candidate branch fires for it regardless of the posting's actual city, so 11 real postings from OTHER Augustinum facilities (Bischofswiesen -- since fixed to RH2100 by TASK-148's Reha rollout --, Unterschleißheim, Oberschleißheim, Bad Tölz, plus 2 rows with no real city at all: 'Deutschland'/'Deutschlandweit') were wrongly attributed to the München clinic. These are Werkstätten/Tagesstätten disability-care roles and remote roles, a different facility category from the München hospital entirely -- not a close call, a clear wrong-city false positive R1_exact's guard exists specifically to prevent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 R2_operator's single-candidate branch gets the same town-disagreement guard R1_exact already has -- a posting whose city clearly disagrees with the sole operator-matched candidate's town does not auto-match via this rule (falls through to unknown/R3, same as R1_exact's own behavior)
- [x] #2 Regression test pinning both the fixed case (Augustinum Bischofswiesen/other-town rows no longer wrongly stick to München) and the legitimate case (a single-candidate operator match with an agreeing or unknown city still matches -- this rule must not become useless)
- [x] #3 Live re-check: the 11 wrongly-attributed Augustinum postings identified 2026-09-24 (clinic_id=16... München's actual id, cross-check via SUPABASE_DB_POOLER_URL) get cleared/reattributed (Bischofswiesen ones should land on RH2100 per TASK-148; Deutschland/Deutschlandweit and the other non-Bavaria-facility rows should end up unmatched, not silently stuck)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read R1_exact's exact town-disagreement mechanism (pflege_jobs/registry.py lines ~197-202: other_town_disagrees = en not in CITY_UNRELIABLE_EMPLOYERS and ck and not _town_match(own_town, ck) and any(x['clinic_id'] != c[0]['clinic_id'] for x in self._by_town(ck))).
2. Mirror it verbatim into R2_operator's single-candidate branch (self.by_op lookup, ~line 206) -- same helpers, same refusal condition (a proven OTHER registered clinic town, not any known-but-unregistered city). On disagreement, do not return -- fall through to the c>1 branch (skipped), then same-town R3/R4/R5, then _match_jd, exactly like R1_exact's own fallthrough.
3. Add a regression test in tests/test_mech_clinic_link.py mirroring test_r1_exact_falls_through_on_a_known_disagreeing_city's exact structure: agreeing city still matches, a city naming a DIFFERENT real registry town is refused, a known city naming no registry town at all is NOT evidence (still matches), unknown city (None) still matches.
4. Mutation-test: save registry.py to /tmp, revert the new guard to the original one-line return, confirm the new test goes RED, restore from the /tmp copy (never git), diff -q byte-identical, confirm GREEN again.
5. Run the full test suite (not just the touched file) to catch collateral regressions.
6. Live re-check (AC#3): query live clinics+postings via SUPABASE_URL/ANON_KEY (paginated). Do NOT assume the original 11 postings/wrong clinic from 2026-09-24 -- re-derive with the FIXED Matcher against current live registry state, since TASK-148's Reha rollout added RH2100 (Bischofswiesen) with an operator string that normalizes to the SAME employer_norm as "Augustinum gGmbH" ('augustinum'), which may have already shifted which clinic the bug points at.
7. Back up every currently-R2_operator row for the Augustinum gGmbH employer to backups/ as JSON before any write.
8. For rows where the fixed Matcher's own re-derivation naturally changes the clinic (real evidence, e.g. München falling through to R3_tokens/16217, or a real disagreeing registry town making it fall through to unmatched): push the genuinely re-derived clinic_id/rule/score -- no 'manual' lock needed, a future link-clinics run will independently agree.
9. For rows where the fixed Matcher's OWN conservative gate still can't refuse (a disagreeing city that names no registered Krankenhausplan town anywhere -- Unterschleißheim/Oberschleißheim/Deutschland/Deutschlandweit), which AC#3 explicitly says must end up unmatched: verify directly against the currently-assigned clinic's own town via the same _town_match() helper, and if it genuinely disagrees, push clinic_id=null with clinic_match_rule='manual' (the registry's own documented override mechanism, so a future link-clinics run's non-manual re-derivation can't silently re-break it back to the wrong clinic).
10. Push corrections through the same clinic_links ingest endpoint cmd_link_clinics itself uses (EdgeSink._post({'clinic_links': [...]})) -- the established write path for posting->clinic assignment.
11. Verify live after the push: re-query every corrected posting_id and confirm clinic_id/clinic_match_rule match what was pushed.
12. Report exact row counts (how many corrected, how many already-correct/untouched) and the backup file path.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
REGRESSION FOUND AND FIXED (2026-09-28), same systemic bug as TASK-163's follow-up:

Live spot-check (prompted by TASK-163's own regression) found the 5 manual-locked postings from
this task (7011, 7012, 7014, 12712, 12713 -- Unterschleißheim/Oberschleißheim/Deutschland/
Deutschlandweit, no registered Krankenhausplan town) had been silently reverted back to
clinic_id=RH2100/clinic_match_rule=R2_operator, updated_at=2026-09-28T07:02:23Z -- almost certainly
by a scheduled full-scope crawl (the "Daily full pass" schedule, cron 0 3 * * *, scope=all) re-observing
these postings and re-matching them via the same unguarded path TASK-163 root-caused: cli.py's
_process_rows/cmd_inbox clinic_links push had no check against an existing clinic_match_rule='manual'
before overwriting it.

Root-cause fix already landed as part of TASK-163's follow-up (pflege_jobs/cli.py: new
manual_posting_ids() check at the clinic_links push chokepoint, tests in
tests/test_cli_manual_override.py, mutation-tested). That fix protects these 5 postings from any
future re-crawl too -- not Augustinum-specific.

Re-applied the null/manual override to all 5 postings via EdgeSink clinic_links push at
2026-09-28T07:06:05.646Z (live-verified: all 5 back to clinic_id=null/clinic_match_rule=manual).
Not re-verified yet against a deliberate re-crawl of RH2100/Augustinum specifically (TASK-163's own
re-crawl of clinics 56404/56406 already proved the fix holds in general), but the fix is
clinic-agnostic so no further clinic-specific proof was done here.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
R2_operator's single-candidate branch (pflege_jobs/registry.py) now carries the exact same other_town_disagrees town-guard R1_exact already had, mirrored verbatim (same helpers, same refusal condition, same fallthrough to R3/R4/R5/_match_jd). New regression test tests/test_mech_clinic_link.py::test_r2_operator_falls_through_on_a_known_disagreeing_city pins both the fixed case (a disagreeing REGISTERED town refuses) and the legitimate cases (agreeing city, known-but-unregistered city, unknown city all still match) -- 24/24 in the file, 1525 passed/18 skipped/0 failed full suite. Mutation-tested: reverted to the exact prior one-line return, confirmed RED, restored byte-identical from a /tmp copy (diff -q), confirmed GREEN.

Live re-check (AC#3) found the bug had already SHIFTED same-day: TASK-148's Reha rollout added RH2100 (Bischofswiesen) with an operator string normalizing to the same 'augustinum' bucket as employer 156's 'Augustinum gGmbH', so by the time of this check R2_operator was wrongly funneling all 14 of employer_id=156's R2_operator postings to RH2100/Bischofswiesen instead of München (same defect, opposite wrong clinic; re-derived independently rather than assumed, per task instruction). Backed up all 14 pre-write rows to backups/task153-augustinum-r2operator-2026-09-24.json. Corrected 11, left 3 untouched (already-correct Bischofswiesen rows, live-verified not assumed): 5 München postings (5578/5580/5581/7022/7023) re-derived by the fixed Matcher itself to 16217 via R3_tokens; 1 Bad Tölz posting (12938) re-derived to unmatched; 5 postings with no registered Krankenhausplan town at all (Unterschleißheim x2: 7011/7014, Oberschleißheim: 7012, Deutschland: 12712, Deutschlandweit: 12713) manually nulled with clinic_match_rule='manual' since the algorithm's own accepted conservatism (same as R1_exact's) can't refuse a disagreeing-but-unregistered city on its own -- 'manual' prevents a future link-clinics run from silently re-breaking these back to RH2100. All 11 pushed via the same clinic_links ingest endpoint cmd_link_clinics itself uses and verified live 11/11 immediately after. Final live state confirmed by direct re-query.
<!-- SECTION:FINAL_SUMMARY:END -->
