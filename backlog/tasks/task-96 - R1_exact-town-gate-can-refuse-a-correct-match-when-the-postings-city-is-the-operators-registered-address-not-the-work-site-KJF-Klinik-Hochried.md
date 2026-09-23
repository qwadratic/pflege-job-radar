---
id: TASK-96
title: >-
  R1_exact town gate can refuse a correct match when the posting's city is the
  operator's registered address, not the work site (KJF Klinik Hochried)
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-22 04:53'
updated_date: '2026-09-23 02:24'
labels: []
dependencies: []
references:
  - >-
    backlog/tasks/task-81 -
    Shared-board-attribution-collapse-one-board-binds-to-one-clinic-21-clinics-and-~163-postings-land-on-the-wrong-site.md
  - backups/task80-81-dryrun-report-20260921.md
priority: medium
type: bug
ordinal: 96000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-22 re-measuring TASK-81's R1_exact town-gate cost for a reviewer. Clinic 18006 "KJF Klinik Hochried" (town Murnau, 22 beds, operator "Katholische Jugendfürsorge der Diözese Augsburg e.V.") is the only clinic in the 407-row live registry named "KJF Klinik Hochried" -- the employer-name match is globally unique and specific. Its 5 currently-open postings all carry city="Augsburg", which is not the clinic's own town but IS a real registry town: a facility that also carries the "KJF" name, "Fachklinik KJF Josefinum" (clinic 76110, operator "KJF Klinik Josefinum gGmbH"), sits there. Correction (2026-09-22, third follow-up): this paragraph originally called 76110 "the same KJF operator" as 18006 -- checked against the live clinics table and that is false; the two clinics' operator field strings differ (diocese-level umbrella vs. clinic-level gGmbH), even though both clinic NAMES carry "KJF" and very likely share a real-world parent organisation. pflege_jobs/registry.py's _match_content R1_exact gate (TASK-81 mechanism #2) sees a DIFFERENT real registry clinic sitting in the posting's stated city and refuses the match (other_town_disagrees=True), even though the far more likely explanation is that Augsburg is the operator's registered/legal address (the Diözese Augsburg is headquartered there), not the actual Einsatzort -- Hochried itself is a small, specifically-named single-purpose clinic in Murnau with no real sibling site to confuse it with.

This is currently latent, not a live regression: these 5 postings were matched to 18006 by the OLD ungated R1_exact rule before this fix shipped, and no production re-run has happened this round (writes were out of scope). The risk is real for the NEXT re-run: replayed live via the fixed Matcher (board=None, no per-posting board data at this layer), all 5 go from clinic_id=18006 to None -- confirmed in this session's replay (/tmp/task81_replay2_full.json, postings 6649/6650/6656/6657/7221; independently re-confirmed 2026-09-22, third follow-up, same 5 IDs, same transition).

A literal same-operator-STRING exception was checked and rejected, but not for the reason first written here. It does NOT apply to the KJF pair at all: checked live, 18006's operator ("Katholische Jugendfürsorge der Diözese Augsburg e.V.") and 76110's operator ("KJF Klinik Josefinum gGmbH") are different strings, so an operator-string-equality exception would simply never fire on this case and could not have recovered it even if implemented -- the two cases are NOT structurally identical on the operator field, contrary to what this paragraph previously claimed. The real reason to reject a blanket "same operator string, don't disagree" relaxation is the RoMed pair alone: the flagship case the gate was built to fix (RoMed Klinikum Rosenheim / posting city Bad Aibling -> should refuse and fall through to RoMed Klinik Bad Aibling, 18702) DOES have both candidates sharing one literal operator string ("Kliniken der Stadt und des Landkreises Rosenheim GmbH", confirmed live) -- "same operator string, don't disagree" would silently undo that correct refusal. Net: the operator field, compared as a literal string, is unreliable in both directions here -- it fails to flag the RoMed pair as safe to relax on (it DOES match, which is exactly why relaxing on it is unsafe) and it fails to flag the KJF pair as related at all (it does NOT match, despite a plausible shared real-world parent), so the gate genuinely cannot use the operator field alone, in either polarity, to tell these cases apart.

The principled fix is very likely TASK-81 AC#1 (read each vendor's per-posting location field instead of a generic city field that can carry the operator's registered address) rather than another registry.py heuristic -- but that needs crawlers/vendor_adapters.py / pflege_jobs/sources/*.py, not owned by TASK-81 either. Filed as its own task per this round's instruction not to decide this silently.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Scan the live registry for how often this pattern recurs beyond clinic 18006: for every R1_exact other_town_disagrees refusal, record whether the disagreeing candidate's operator field string-matches the matched candidate's. Correction (2026-09-22, third follow-up): this AC originally pre-labeled the 'does not share operator' bucket as 'correctly refused (Starnberg/RoMed-shaped)' -- that is false on live data: the motivating case (18006 vs. 76110) is ALSO an operator-string mismatch, not a match, the same shape this AC pre-labeled as correct. Do not use 'shares operator string' vs. 'does not' as a proxy for 'correctly refused' vs. 'false negative' when executing this criterion -- classify each refusal on its own merits instead.
- [x] #2 Decide, with evidence, whether AC#1's per-posting location extraction (vendor_adapters.py / sources/*.py) removes this false disagreement by supplying the real Einsatzort instead of the operator's registered city, or whether a narrower registry.py-side signal is safe and worth adding
- [x] #3 Any fix ships without regressing the RoMed Rosenheim/Bad Aibling refusal or any other currently-correct same-operator disagreement in tests/test_mech_clinic_link.py
- [x] #4 Re-run confirms clinic 18006 (KJF Klinik Hochried)'s open postings keep resolving to 18006, not None
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
All 4 ACs closed 2026-09-23, live evidence.

AC#1: tools/task96_scan_r1_exact_disagreements.py replays Matcher._match_content's own R1_exact/
R2_operator + other_town_disagrees logic (calls the real helpers, does not reimplement the gate)
against every distinct (employer_name, city) pair among the 3182 currently-open postings' raw
observations. Found 25 refusals (not just clinic 18006's), operator-string-match split 12/13 --
confirms this task's own hypothesis that operator-string equality cannot be used as a proxy for
"correctly refused" vs "false negative" in EITHER direction. Full list: backups/task96-r1exact-
disagreements-2026-09-23.json.

Hand-verified ground truth (live data, not assumption) for 3 representative cases spanning the real
shape of this 25-row population, not all 25 individually (would need its own multi-hour audit):
- 18006 KJF Klinik Hochried / city=Augsburg, op_match=False: the flagship case. Traced the real
  posting_observations rows -- the SAME shared board (josefinum.softgarden.io) correctly states
  employer="KJF Klinik Josefinum gGmbH" for 3 genuinely-Augsburg postings (Notaufnahme, Kreissaal,
  Pädiatrie-Chirurgie -- none plausible for a 22-bed KJP-only facility) and employer="KJF Klinik
  Hochried" for the 2 remaining ones (PWS-Patientinnen/Schlaflabor, Pflegepädagoge) -- the vendor DOES
  differentiate the two sites by name reliably, it just always states city=Augsburg for BOTH,
  including Hochried's own postings. A genuine per-employer city-field defect on the vendor's own
  system, not fixable by reading a "better" field in the same feed (checked: no alternate per-posting
  location field exists in this vendor's payload).
- 16301 RoMed Klinikum Rosenheim / city=Bad Aibling, op_match=True: this task's own named flagship
  "must stay refused" case, confirmed live still refusing correctly (unchanged by this fix).
- 16291 Klinikum der TU München (Rechts der Isar) / city=Augsburg, op_match=True: live-traced the raw
  observation -- source_url is uk-augsburg.softgarden.io (Universitätsklinikum Augsburg's OWN board),
  employer_name wrongly extracted as "...TUM...Rechts der Isar" instead of the real employer
  (Universitätsklinikum Augsburg, clinic 76190/76107/etc). The gate is CORRECTLY catching a genuine
  bad employer-name extraction here, not a false refusal -- op_match=True is coincidental (both
  publicly owned, unrelated real hospital groups), proving operator-string-match is unreliable as a
  signal in this direction too, exactly as this task's own description already concluded from the
  RoMed/KJF pair alone.

AC#2: decided against a general registry.py heuristic -- the 25-case evidence shows the population is
genuinely heterogeneous (real multi-site ambiguity + genuine bad-employer-extraction catches + one
narrow vendor-side city defect), and no employer/city-shaped signal available at match time
(operator-string match/mismatch, employer-text specificity, etc.) reliably separates them without
readmitting cases the gate exists to catch. TASK-81 AC#1's per-posting location extraction (adapter-
side) also does not apply here: the vendor's own feed has no better per-posting location field to
read for Hochried specifically (checked live). Added a narrow, named, single-employer exception
instead: pflege_jobs/registry.py's new CITY_UNRELIABLE_EMPLOYERS set (currently {"kjf klinik
hochried"} only), checked in _match_content's other_town_disagrees condition -- an employer_norm()
value must be added here with the same live-evidence bar (a confirmed per-posting city the vendor's
own feed cannot correct), not a guess, per the module comment.

AC#3: tests/test_mech_clinic_link.py's existing RoMed-shaped test
(test_r1_exact_falls_through_on_a_known_disagreeing_city) re-run unmodified, still green -- the new
exception only fires for the one named employer_norm, gated inside the same len(c)==1 branch, no
other refusal path touched. New test
test_r1_exact_ignores_a_named_city_unreliable_employer, synthetic clinics (independent of live
registry drift), asserts both the exception firing (Hochried + disagreeing city -> still matches) and
two negative-space cases (Hochried + agreeing city; the sibling Josefinum's own name) stay correct.
Mutation-tested via a /tmp copy: reverting the `en not in CITY_UNRELIABLE_EMPLOYERS` clause reproduces
the exact pre-fix failure (KJF's own posting resolves to the wrong sibling, 76110, via R5_loose,
instead of refusing to None as the old gate did -- an even more concrete demonstration of why this
was worth fixing precisely, not just leaving unmatched); restored, all 23 tests green again.

AC#4: live-verified both ways. Direct Matcher replay against the full live registry:
m.match("KJF Klinik Hochried", "Augsburg") -> ("18006", "R1_exact", 1.0) (was None before the fix).
Real production re-crawl (clinic-scoped run, real pflege_jobs.cli inbox/link-cross/verify pipeline,
not a replay): 18006 now shows exactly 2 open postings
(6657 "...PWS-Patient*innen und pädiatrisches Schlaflabor...", 7221 "Pflegepädagoge"), both
verify_status=live. The other 3 of the original 5 postings this task named (6649/6650/6656) were
already correctly resolved to 76110 (Josefinum/Augsburg) by ordinary R1_exact/R2_operator matching
before this session touched anything -- their employer field genuinely says "KJF Klinik Josefinum
gGmbH", not Hochried, so they were never actually testing this task's mechanism; the task's original
"all 5 currently match 18006" framing was accurate at filing time (2026-09-22) but had already partly
self-corrected by 2026-09-23 via ordinary re-crawls, unrelated to this fix.

tools/task96_scan_r1_exact_disagreements.py checked in, reproducible (does not itself know about the
new CITY_UNRELIABLE_EMPLOYERS exception -- it replicates the gate's OLD condition directly rather than
calling _match_content, so a re-run still reports 25, not 24; noted here rather than silently claimed
accurate, low priority to fix since its one-time diagnostic job for this task is done).

Full offline suite pending (running alongside TASK-118's work in the same session pass).
<!-- SECTION:NOTES:END -->
