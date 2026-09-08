---
id: decision-1
title: Mark Rotkreuzklinik Würzburg (66303) as closed ahead of Krankenhausplan update
date: '2026-09-07 12:14'
status: deferred
---
## Context

clinic_id 66303 (Rotkreuzklinik Würzburg) is listed active (`status='Plan-KH'`) in both the
Krankenhausplan 2026 PDF (Stand 1.1.2026) and our `clinics` table — a 100% match with the source
document, verified 2026-09-06. External web search (2026-09-07) found multiple press sources
(medconweb, kma-online, t-online, bibliomedmanager) reporting the clinic filed for
Schutzschirmverfahren (Sept 2025) and ceased operations 2026-03-25/04-01, with staff absorbed by
Klinikum Würzburg Mitte. The official Plan predates the closure and has not been updated yet, so a
literal PDF-match check will keep saying this clinic is fine.

## Decision

Deferred, not applied. Proposed: set `status='nicht_mehr_im_plan'` (same value used for sites the
Krankenhausplan itself drops) with a `source` note in the existing house style, e.g.
`"... | insolvent, Betrieb eingestellt 2026-04-01, noch nicht im Krankenhausplan Stand 1.1.2026 vermerkt"`.
Not pushed yet because it deliberately diverges from the authoritative source document — needs an
explicit go-ahead rather than a silent data patch. Longer-term this wants its own field
(`operational_status` / `closed_at`) separate from `status`, so `status` can stay a pure mirror of
the Plan and closures-ahead-of-the-Plan don't get conflated with plan-status changes — blocked on
DDL access (see access-issue note in the same session).

## Consequences

Until resolved, 66303 keeps surfacing in `/api/clinics` as an active, routable clinic even though it
cannot accept applications. Any job postings still resolving to this KeZ should be treated as stale.

