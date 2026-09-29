---
id: TASK-125
title: >-
  kbo-IAK München-Ost umantis board (18402/recruitingapp-5656): /Jobs/All
  session-state scoping may under-read or CompanyID=22 may correctly narrow --
  needs resolving before trusting either count
status: Done
assignee: []
created_date: '2026-09-23 08:22'
updated_date: '2026-09-23 17:48'
labels: []
dependencies:
  - TASK-123
priority: medium
ordinal: 125000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Determine what CompanyID query param scopes to on recruitingapp-5656.de.umantis.com: same legal entity/site as clinic 18402 (München-Ost), or a UI-only department/tab filter that has no bearing on which postings genuinely belong to this clinic
- [x] #2 Confirm whether the 5 postings visible only on an unscoped fresh-session /Jobs/All fetch (ids 3134/3215/3228/3271/3329/3343/3346 minus overlap) already appear under a different registry clinic_id via kbo-iak.de's own wp_jobs-routed board, or are genuinely unclaimed
- [x] #3 Fix career_crawl.py's Crawler so a CompanyID-scoped seed page fetched earlier in the same requests.Session cannot silently narrow a later /Jobs/All fetch in that same session (either fetch /Jobs/All first, or use an isolated session for it) -- only if AC1/AC2 show the narrower scope is wrong
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RESOLVED 2026-09-23, live investigation -- both AC#1 and AC#2 answered empirically, and the answer
makes AC#3's proposed fix unnecessary.

AC#1: CompanyID=22 IS a real legal-entity/site scope, not a UI-only department filter. Confirmed two
ways: (a) fetching /Jobs/1?CompanyID=22 fresh (no prior session state) still returns only 5 vacancies,
same 5 as the "session-pinned" count -- ruling out the session-pinning theory as the actual cause of
the narrower count (CompanyID=22 genuinely scopes the listing itself, with or without prior requests in
the same session); (b) the CompanyID=22 page's own <title> tag reads "kbo-Isar-Amper-Klinikum Stellen"
-- exactly clinic 18402's own operator name (kbo-Isar-Amper-Klinikum gGmbH).

AC#2: fetched each of the 7 "missing" vacancy ids' own detail page directly. All 7 titles: Logopäde,
Psychologischer Psychotherapeut, Dachdecker oder Spengler, Kunsttherapeut, Physiotherapeut, Klinisches
Hauspersonal, Mitarbeiter Reinigungsdienst -- NONE are nursing roles; classify_role would tag every one
nicht_pflege regardless of whether this pipeline ever captures them. Cross-checked against the 5 "kept"
(CompanyID=22-scoped) vacancies too, for contrast: Pflegefachfrau/-mann, Pflegefachhelfer (x2),
Pflegefachkraft - Psychiatrie, Ex-In Genesungsbegleiter -- all genuinely pflege-relevant. Also checked
whether the 7 might already be captured under a sibling clinic_id (this task's own suggested places:
kbo-iak.de's wp_jobs-routed siblings 16251/16252/17803/16257/16263, and separately kbo-Kinderzentrum's
own umantis instance recruitingapp-5545/clinic 16212) -- confirmed no overlap: recruitingapp-5545's own
vacancy id range (400s-700s) shares zero ids with recruitingapp-5656's "missing" set (3000s), and the
6 kbo-iak.de-routed siblings' actual careers_url (https://kbo-iak.de/kbo-karriere/stellenangebote-pflege)
itself embeds the SAME CompanyID=22 umantis scope (confirmed: "CompanyID=22" literally appears in that
page's own markup) -- i.e. those 6 siblings have the identical scope as 18402 today, not a wider one.

Conclusion: there is no real coverage gap. CompanyID=22 correctly captures every genuinely
nursing-relevant posting for this clinic; the "missing" 5-7 are real vacancies but for non-nursing
support roles (allied health, facilities, maintenance) that this pipeline is not meant to surface
regardless of which umantis scope reads them. AC#3's proposed fix (session isolation so /Jobs/All in
the same crawl session can't be narrowed) is therefore NOT NEEDED -- its own wording gates it on "only
if AC1/AC2 show the narrower scope is wrong," and they show the opposite: the narrower CompanyID=22
scope is correct for this pipeline's purpose. No code change made.

All 3 ACs answered with live evidence (#3 answered as "not applicable, confirmed via #1/#2"). Closing
Done.
<!-- SECTION:NOTES:END -->
