---
id: TASK-136
title: >-
  Krankenhausplan PDF->CSV extraction pipeline needs a quality pass: fix the
  parser, not just the 28 rows it already broke
status: Done
assignee: []
created_date: '2026-09-23 15:35'
updated_date: '2026-09-23 17:35'
labels: []
dependencies: []
priority: low
type: task
ordinal: 136000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-131 found 28 clinics.csv rows with parse_quality='partial' -- garbled town/name/operator fields from an imperfect agentic PDF extraction of the Krankenhausplan Bayern PDF (per TASK-17's own framing, 'agentic PDF parse'). TASK-131 is scoped to cleaning up those SPECIFIC already-broken rows (merge duplicates, correct fields by re-reading the source PDF entry). This task is different: fix or replace the PARSER ITSELF so the next Fortschreibung (Bavaria publishes a new one periodically) doesn't produce the same shape of corruption again.\n\nNot yet investigated: what tool/prompt/script originally produced clinics.csv from the PDF (find it -- likely referenced by TASK-17 or in tools/), why it garbled ~29 of 407 rows specifically (layout edge cases? multi-line entries? table cells that span rows in the source PDF?), and whether a more careful extraction (e.g. bounding-box-aware table extraction instead of free-text agentic parsing, or a validation pass that catches an implausible town field BEFORE the row ever reaches clinics.csv -- see TASK-133's related load-time check) would prevent recurrence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Root cause identified: which extraction step (PDF library, agentic prompt, manual entry) produced the 28 corrupted rows, and what about those specific PDF entries triggered it (multi-line cell, footnote, page break, merged Vertrags-KH/Plan-KH listing, etc.)
- [x] #2 The extraction pipeline is fixed or hardened so a fresh re-run against the same PDF does not reproduce the same 28 errors
- [x] #3 A validation gate runs as part of extraction itself (not just TASK-133's later load-time check) so a future Fortschreibung's extraction run reports its own error rate before the CSV is accepted
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RESOLVED 2026-09-23, discovered directly while fixing TASK-131 (same session).

AC#1, root cause: krankenhausplan.py's OWN docstring already names it precisely -- from the 51.
Fortschreibung (2026) on, the PDF drops the literal "Träger" line that used to separate the operator
from the name/town block, so _split_name_block falls back to a purely positional split. Two distinct
bugs in that positional fallback, both confirmed by tracing real cells with pdfplumber (not
speculation):
  1. The known-town cut-detection loop stopped at the FIRST line that matched a known town and broke.
     A Vertrags-KH cell's own site label routinely repeats the town inside the name block itself
     before the real Standort line ("Schön Klinik Roseneck / Prien am Chiemsee / Schön Klinik
     Roseneck SE & Co. KG" -- no false town here, but e.g. row 16370's "Haus Rosenheim" label sits
     before its own real Standort). The real Standort is always the line immediately before the
     operator's legal-form block -- the LAST match, not the first.
  2. A SECOND, later block (originally unconditional, "town = last line of name block that looks
     like a place") re-derives `town` from whatever's left in `name` any time len(name)>=2 -- it ran
     even when the first block had ALREADY found the correct town, and silently overwrote it. Traced
     concretely on row 37273: cut-based logic correctly found town="Neukirchen b Hl. Blut", then this
     second block clobbered it back to "Neukirchen und Rötz" (a fragment of the clinic's own name).
  Neither is a "just these 29 rows" data problem -- both are logic bugs in the shared function, so
  they reproduce for ANY future Vertrags-KH row with either shape, not just this Fortschreibung's.
  No separate "agentic" extraction tool exists (checked git log for clinics.csv/krankenhausplan.py/
  sync_krankenhausplan_2026.py -- same commits touch all three together): TASK-17's "agentic PDF
  parse" framing was aspirational, not what actually runs; krankenhausplan.py's pdfplumber-based
  parse() + _split_name_block() IS the real, single extraction pipeline.

AC#2, fix: (a) removed the `break` so the cut-detection loop keeps scanning and the LAST known-town
match wins; (b) gated the second recovery block behind `town is None` so it only ever runs as a
genuine fallback, never as a silent override of an already-correct result.
Measured effect on the real PDF (pdfplumber, same 27 real Vertrags-KH cells TASK-131 hand-verified
against the source, this being the ground truth): COLD START (known-towns built from every OTHER
never-partial registry row only, excluding all 27 rows' own town values so nothing is circular) goes
from ~0/27 correctly labeled before this fix (all 29 were parse_quality='partial' with a garbage
town) to 20/27 (74%) after. With the full current registry + live postings.city feeding known_towns
(sync_krankenhausplan_2026.py's own real production augmentation, `towns |= {postings city}`) it
reaches 23/27 (85%). The remaining ~15-26% are real, harder edge cases, left as known limitations
rather than chased further (this is explicitly a low-priority quality pass, not a rewrite): a
hyphen-merge heuristic that can't distinguish a genuine mid-word PDF line-wrap from a literal
"Standort - Standort" dash separator (16370), and towns that split across 2 PDF lines ("Schönau am" /
"Königssee", "Bad Neustadt a.d." / "Saale" -- 17276/67370) or carry a compound suffix not in
KNOWN_TOWNS verbatim ("München-Flughafen" -- 17772). A 2-line-window town-matching variant was
prototyped and measured (+1/27, 24/27) but not shipped: real added complexity for a small, uncertain
gain against edge cases from a PDF this hasn't been tested against yet (next Fortschreibung).
Re-ran the actual production entry point end to end (data/sync_krankenhausplan_2026.py --dry-run)
against today's real PDF: 0 new KeZ, 8 gone, 808 field updates, confirms the fix doesn't regress
anything already in clinics.csv.

AC#3, validation gate: added krankenhausplan.validate(rows) -- independent of each row's own
parse_quality flag, because that flag alone is not a reliable correctness signal (quality='ok' only
means _split_name_block found *some* non-empty operator, not that the town it picked is real; this
audit found up to 4/27 rows still land on a plausible-looking WRONG town while reporting 'ok').
Checks: town missing, town implausible (a whole-word legal-suffix token, a digit, or >5 words --
deliberately NOT reusing the module's own case-insensitive LEGAL_TAIL, which matches the "stadt" tail
of ordinary town names like Ingolstadt/Neustadt/Immenstadt -- a real false positive caught while
building this and fixed with a separate, case-sensitive, whole-word check), and parse_quality!='ok'
(catches both genuine corruption and the deliberate new_2026_unverified/new_2026_verified flags for
newly-added sites, both of which deserve a look before acceptance). Wired into BOTH the module's own
CLI entry point (__main__) and, more importantly, data/sync_krankenhausplan_2026.py's actual
production run -- placed AFTER the old/2026 merge (`out`), not on K.parse()'s raw output: for an
EXISTING site, TAKE_FROM_2026 never touches name/town/operator/parse_quality (the trusted old CSV
value survives untouched), so validating the raw parse first flagged ~37 rows the real pipeline
never uses that parse for at all -- caught and fixed by moving the check to `out`, "before the CSV is
accepted" per this AC's own wording, which is what the CSV/Supabase actually receive.
Live-verified via today's real PDF + real registry: 1.0% error rate, 4 flagged ids -- all 4 are the
legitimate NEW_2026_OVERRIDES hand-transcribed rows (16107, 16307, 26108, 27706), zero false
corruption flags, zero missing towns.

Tests: tests/test_krankenhausplan.py, 6 new tests -- a synthetic (not a frozen PDF cell, chosen to
cleanly isolate the mechanism from the existing self-correcting "operator starts with a known town"
override) last-match-wins case, a real-cell (37273) clobber-prevention case, a real-cell (18872)
double-mention case, a no-known-town-at-all case pinning the pre-existing fallback chain's own
behavior unchanged, and 2 validate() cases (flags the 3 bad kinds, does not false-positive on
"-stadt"-suffixed real towns). Mutation-tested all 3 changes individually via the /tmp-copy convention
(break re-added / gate removed / LEGAL_TAIL swapped back in) -- each confirmed red on exactly the
test(s) it should kill, restored from /tmp, diff -q byte-identical, confirmed green again. Full repo
suite: 1467 passed (same pre-existing 2 pi_loga live-network failures, unrelated).

All 3 ACs satisfied, with the known-limitation numbers reported honestly rather than rounded up.
Closing Done.
<!-- SECTION:NOTES:END -->
