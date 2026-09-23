---
id: TASK-126
title: >-
  classify_role's _ROLES loop pattern-matches on bare substring, so
  'Initiativbewerbung <Rolle> (m/w/d)' speculative-application category links
  get stored as genuine open postings of that role
status: Done
assignee:
  - '@claude'
created_date: '2026-09-23 08:27'
updated_date: '2026-09-23 08:47'
labels: []
dependencies:
  - TASK-123
priority: medium
ordinal: 126000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Survey real Initiativbewerbung/speculative-application category-link phrasing across several live boards (same method as classify.py's existing NICHT_PFLEGE/STRONG_PFLEGE_TITLE surveys) to see how many already carry a gender marker and would pass crawl-layer discovery unchanged
- [x] #2 classify_role (pflege_jobs/classify.py:117) gains a check so an 'Initiativbewerbung'/'Blitzbewerbung'-prefixed (or otherwise clearly speculative-application) title does not silently match a real _ROLES rule as if it were a genuine open vacancy of that role
- [x] #3 Regression test locks in both directions: a genuine 'Pflegefachkraft (m/w/d)' posting still classifies pflegefachkraft, an 'Initiativbewerbung Pflegefachkraft (m/w/d)' category link does not
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Found live 2026-09-23 during TASK-123 AC4 (feeding the crawl layer's FULL unfiltered candidate set into classify_role to verify it does not rely on upstream filtering). On barmherzige-bieten-zukunft.de's real candidate set: 'Initiativbewerbung Pflegefachkraft (VZ/TZ)' -> classify_role returns pflegefachkraft (matches _ROLES' plain substring rule, pflege_jobs/classify.py:151-154, no awareness Initiativbewerbung/speculative-application text is not a genuine vacancy at all). Currently NOT reachable in production for this exact board only because '(VZ/TZ)' carries no gender marker, so crawlers.vendor_adapters.GENDER/career_crawl.JOB_TEXT (now pflege_jobs/posting_signal.GENDER_MARKER) rejects it at discovery time first -- an accident of phrasing, not a designed protection: the SAME board's 'Initiativbewerbung Medizinische/r Fachangestellte/r' (ends in the bare-slash '/r' suffix form) DOES pass GENDER_MARKER today and reaches classify_role (correctly nicht_pflege only because MFA isn't a recognized Pflege token) -- proving Initiativbewerbung category links already slip past crawl-layer discovery on this board. Any board phrasing its own Initiativbewerbung category as e.g. 'Initiativbewerbung Pflegefachkraft (m/w/d)' would pass both layers and be stored as a fake open posting today.

AC1 done: live-surveyed 59 distinct careers_url boards for Initiativbewerbung/Blitzbewerbung phrasing. 20 boards carry it, real title patterns found: plain 'Initiativbewerbung'/'Initiativbewerbungen', 'Initiativbewerbung(en) <Rolle>' (klinikum-passau.de: 'Initiativbewerbungen Assistenzärzte (m/w/d)'), 'Blitzbewerbung <Rolle> (m/w/d)' (kbo-iak.de/kbo-lmk.de: 'Blitzbewerbung Pflegefachkräfte (m/w/d)', 'Blitzbewerbung Ärzte (m/w/d)', 'Blitzbewerbung Therapeuten (m/w/d)', 'Blitzbewerbung Azubis'). Every real example puts the word FIRST. Confirmed this is a LIVE risk, not theoretical: kbo-iak.de/kbo-lmk.de's own 'Blitzbewerbung Pflegefachkräfte (m/w/d)' carries a real gender marker today and would pass GENDER_MARKER at crawl-layer discovery unchanged.

AC2 done: pflege_jobs/classify.py gained _SPECULATIVE_APPLICATION_RX (anchored prefix: initiativbewerbung(en)?|blitzbewerbung) checked first in classify_role, before the pflege_gate -- returns ('nicht_pflege', 'speculative_application') unconditionally. Bonus fix while in this file: _POSTING_SHAPED was itself a 4th independently-drifted copy of the GENDER/JOB_TEXT signal (missing the bare-slash suffix form) -- now imports pflege_jobs.posting_signal.GENDER_MARKER instead (no import cycle: posting_signal.py is a leaf module).

AC3 done: tests/test_mech_role_class.py::test_speculative_application_titles_never_classify_as_a_real_role -- both directions (Initiativbewerbung/Blitzbewerbung + role never classifies as that role; genuine 'Pflegefachkraft (m/w/d)' unaffected). Mutation-tested (removed the early-return -> red, confirmed the exact live kbo-iak.de failure mode; restored -> green, diff -q byte-identical). Full affected-module suite green: 371 passed (test_bite/board_csv/career_crawl_section/classify_section/completeness_wp_jobs/crawl_board_retry/geo/inbox_sqlite_queue/inherited_fields/mech_*/patterns/sinks/vendor_adapters/posting_signal).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added pflege_jobs/classify.py's _SPECULATIVE_APPLICATION_RX gate (checked first in classify_role) so an Initiativbewerbung/Blitzbewerbung-prefixed speculative-application title never classifies as a genuine role, closing a live risk confirmed on kbo-iak.de/kbo-lmk.de ('Blitzbewerbung Pflegefachkräfte (m/w/d)' would have stored as a fake open posting). Also reconciled classify.py's own 4th drifted copy of the gender-marker signal (_POSTING_SHAPED) onto the shared pflege_jobs.posting_signal.GENDER_MARKER from TASK-123. Verified: live 59-board survey (AC1), new regression test mutation-tested red/green, 371 tests across every classify.py-touching module pass.
<!-- SECTION:FINAL_SUMMARY:END -->
