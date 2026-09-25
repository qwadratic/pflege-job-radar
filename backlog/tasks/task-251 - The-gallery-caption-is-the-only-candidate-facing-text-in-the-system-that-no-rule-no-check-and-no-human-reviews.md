---
id: TASK-251
title: >-
  The gallery caption is the only candidate-facing text in the system that no
  rule, no check and no human reviews
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 198000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/tools_server.py:1067. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: A blurb paragraph containing the clinic's own website, a pay range, or a bed count the board cannot back is fetched and sent verbatim as the caption of the highest-attention message in the conversation. The link ban that offer.py calls a guarantee ("the way to make that a guarantee rather than a rule is to keep the link out") does not hold on a text path that never goes through the model.

WHAT IT COSTS: One unreviewed generated paragraph can break the rules the rest of the funnel spends a thousand lines enforcing — a candidate handed the clinic's own careers page applies directly and the lead is gone — and nothing in the thread records what was said.

PROPOSED DIRECTION (not a decision): Run the caption through at least the link ban and the German-only rule before it reaches the rail, and record it as an outbound row. If the expose text cannot be trusted to a mechanical check, use the branch that already exists: return it as {sent: false, presentation_text} and let the model write it in its own words, where every rule applies.

VERIFICATION NOTES: CONFIRMED. tools_server.py:1067 takes caption = presentation["text_de"] from GET /api/clinics/{id}/expose and hands it to send_gallery at 1090 with no transformation. That text is the Firecrawl-researched clinic blurb (app/runs.py:5,486-497, surfaced by app/main.py:306) — generated prose, from a pipeline the tool's own docstring calls mid-rollout (TASK-223). Every model-authored bubble goes through grounding.check_reply (grounding.py:1470) and the bubble-shape guard; prompts.py bans naming another brand or site, quoting a salary figure and sending a URL. The caption passes none of them — note board_api_get's _without_urls (tools_server.py:924) is applied to that tool's payloads only, never to the expose fetch. It is also written to no wa_messages row, so no operator reading the thread later sees what was said (same root as the send-discipline finding). What the finder cannot show, and neither can I without the blurb corpus, is a live blurb that actually carries a URL or a pay figure — so this is a structural hole whose firing rate depends on data I cannot read from here.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERDICT: FIX (partial scope -- link ban only, per the sceptic's argument).

Verified reachable two ways: read the code path and confirmed the TASK-251 finder's own
repro shape by writing a test that fails against the pre-fix tree (caption with a URL sent
verbatim, BR.Client() constructed, send_gallery called, {"sent": True}) and passes after
the fix. tools_server.py:1067 (`caption = presentation.get("text_de")...`) reaches
BR.Client().send_gallery(..., caption=caption) at ~1104 with no transformation.
grounding.check_reply's LINK gate (has_link, grounding.py:1336, TASK-144) is only ever
invoked from luna_brain.py on the model's own text_bubbles -- the caption never becomes
one, so it skips LINK entirely. board_api_get's _without_urls (tools_server.py:924-931)
strips URLs only from that tool's own dict handlers, never from _fetch_clinic_expose (a
separate urllib call). No other code path catches this.

Stale part of the finding: this working tree already carries an uncommitted TASK-250 fix
(same file) that calls ST.record_outbound(...) right after send_gallery, with its own
passing test. So "nothing in the thread records what was said" is no longer true --
the "unreviewed text reaches the candidate uncensored" half is what's still live.

Fix applied (app/wa/luna/tools_server.py, show_clinic_photos): after the TASK-250
AUTOSEND/rail gates and before the download/stage/send_gallery block, `from . import
grounding as GR` (sibling module, both in app/wa/luna/ -- confirmed no circular import:
grounding.py's own `from . import tools_server as TS` is function-local, not module-scope)
and `if GR.has_link(caption): return {"sent": False, "presentation_text": caption}` --
reusing the exact branch the no-photo case already returns, so the model writes the
caption in its own words next turn, where check_reply's LINK gate actually applies.
Photos are not sent captionless in this branch -- that's a bigger behavior change than
the finding calls for; reusing the existing no-send shape is the minimal fix.

Scope explicitly NOT extended to German-only or salary/bed-count checks: grep confirms
no mechanical check for either exists anywhere in this codebase for ANY text, model-
authored or not (German is prompt-only, prompts.py:185; no salary/Gehalt/EUR regex
exists in grounding.py at all). The caption isn't uniquely deficient on those two axes --
it's a systemic gap shared with ordinary model bubbles. Building a language classifier
would be new machinery (and, per standing project convention, that's a Haiku-tier model
call, not a hand-rolled phrase list) -- a separate, larger task, not a reuse of an
existing gate. Scoped this fix to the link ban only, matching "fix the finding, not the
neighbourhood."

Blast radius: app/wa/luna/tools_server.py (show_clinic_photos + one new function-local
import) and tests/test_wa_luna_tools.py (one new test,
test_show_clinic_photos_refuses_to_send_a_caption_carrying_a_link, which fails on the
pre-fix tree and passes post-fix). No changes to the send path, ledger, dispatcher/adb
driver, or any other tool.

Test run (narrow): .venv/bin/python -m pytest tests/test_wa_luna_tools.py -q -> 131
passed. Full suite intentionally not run here (one verification pass over the whole
batch happens separately). Not committed -- diff left for review.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/tools_server.py:1179-1180 adds a link-ban gate before show_clinic_photos ever calls BR.Client().send_gallery with a fetched caption; already in committed HEAD. tests/test_wa_luna_tools.py:1123 covers it and the whole 149-test file passes ().
<!-- SECTION:FINAL_SUMMARY:END -->
