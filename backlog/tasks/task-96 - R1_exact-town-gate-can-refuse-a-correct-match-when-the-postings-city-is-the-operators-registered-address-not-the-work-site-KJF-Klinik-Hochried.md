---
id: TASK-96
title: >-
  R1_exact town gate can refuse a correct match when the posting's city is the
  operator's registered address, not the work site (KJF Klinik Hochried)
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-22 04:53'
updated_date: '2026-09-22 05:28'
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
- [ ] #1 Scan the live registry for how often this pattern recurs beyond clinic 18006: for every R1_exact other_town_disagrees refusal, record whether the disagreeing candidate's operator field string-matches the matched candidate's. Correction (2026-09-22, third follow-up): this AC originally pre-labeled the 'does not share operator' bucket as 'correctly refused (Starnberg/RoMed-shaped)' -- that is false on live data: the motivating case (18006 vs. 76110) is ALSO an operator-string mismatch, not a match, the same shape this AC pre-labeled as correct. Do not use 'shares operator string' vs. 'does not' as a proxy for 'correctly refused' vs. 'false negative' when executing this criterion -- classify each refusal on its own merits instead.
- [ ] #2 Decide, with evidence, whether AC#1's per-posting location extraction (vendor_adapters.py / sources/*.py) removes this false disagreement by supplying the real Einsatzort instead of the operator's registered city, or whether a narrower registry.py-side signal is safe and worth adding
- [ ] #3 Any fix ships without regressing the RoMed Rosenheim/Bad Aibling refusal or any other currently-correct same-operator disagreement in tests/test_mech_clinic_link.py
- [ ] #4 Re-run confirms clinic 18006 (KJF Klinik Hochried)'s open postings keep resolving to 18006, not None
<!-- AC:END -->
