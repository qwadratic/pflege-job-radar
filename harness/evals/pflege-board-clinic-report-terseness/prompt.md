You're reporting the result of a Bavarian hospital-registry data-cleanup pass to the person who
asked for it. Here is the raw finding set — report it back to them.

- 407 clinics total in the registry (Bayerischer Krankenhausplan 2026, 51. Fortschreibung).
- Structured fields (beds, day_places, fachrichtungen, versorgungsstufe, traegerart,
  regierungsbezirk, status) matched the source PDF exactly for all 407 rows — 0 diffs.
- 28 rows had garbled name/town/operator fields from a known PDF-parsing edge case (Vertrags-KH
  table cells with no `Träger` separator line); all 28 were hand-verified against the source PDF
  pages and corrected.
- 8 clinics are correctly marked `nicht_mehr_im_plan` (dropped from the 2026 plan) — re-verified,
  no changes needed.
- 18 of the 28 corrected rows turned out to be duplicate KeZ (clinic-id) entries for a clinic that
  already exists under a different KeZ — the source PDF itself annotates these
  "Zugleich Plan-KH siehe Teil II Abschnitt A; KeZ NNNNN".
- 121 active clinics had no careers_url on file. 71 were resolved from data already in the
  registry (same operator, or the duplicate-KeZ twin, already had one on file). 47 more were found
  by web search and verified live. 3 have none: two are too small to have a formal careers page
  (a 4-bed sleep lab, a single-physician practice), one (clinic_id 66303, Rotkreuzklinik Würzburg)
  turned out to have closed in an insolvency in March/April 2026 — after the PDF's Stand 1.1.2026
  cutoff, so the plan itself doesn't reflect it yet.
- Of the 118 clinics that got a careers_url, 69 were fingerprinted to a known ATS vendor
  (softgarden, typo3_jobs, rexx, umantis, talention, bite, pi_asp, mein-check-in, concludis,
  dvinci, helix, personio, smartrecruiters, coveto) via plain HTTP probing. 49 needed a
  JS-rendering pass (Playwright) because they run no vendor at all — self-hosted job listings.

Write the report.
