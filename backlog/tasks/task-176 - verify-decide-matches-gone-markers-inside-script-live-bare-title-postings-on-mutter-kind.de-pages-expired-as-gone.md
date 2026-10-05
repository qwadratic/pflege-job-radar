---
id: TASK-176
title: >-
  verify decide() matches gone-markers inside <script>: live bare-title postings
  on mutter-kind.de pages expired as gone
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 21:39'
updated_date: '2026-09-29 22:40'
labels:
  - verify-freshness
dependencies: []
priority: high
type: bug
ordinal: 174000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by TASK-172 (coveto group V, defect 1), fix requested by the main session 2026-09-29 (via coordinator) in the same verify.py patch as TASK-173. pflege_jobs/verify.decide() runs GONE_MARKERS over norm_text of the first 400 KB of raw HTML, <script>/<style> content included. For a title with no usable token (TASK-150 path: "Pflegefachkraft (m/w/d)" -> _title_tokens drops "pflegefachkraft" -> toks=[]) any marker anywhere in the HTML decides "gone", and the edge function then expires the posting.

Live evidence 2026-09-29 ~21:40 UTC: postings 15113 (RH1802, maximilian.mutter-kind.de .../pflegefachkraft-(mwd)-6883), 15114 (RH2395, alpenhof.mutter-kind.de ...-7246), 15314 (RH1487, lindenhof.mutter-kind.de ...-7702) are status=expired, verify_status=gone, verify_http=200, note "200, no title token to confirm, but a gone-marker matched", verified_at 2026-09-29 21:14. Re-fetched now: each answers 200 (~48 KB), <title> "Stellenangebot: Pflegefachkraft (m/w/d)", visible text carries the ad ("Verstärkung als Pflegefachkraft (m/w/d) in unserer Klinik Maximilian in Voll- oder Teilzeit ... Arbeitsort: Scheidegg ... Jetzt bewerben"). The ONLY GONE_MARKERS hit is "nicht gefunden" at offset ~28 K inside a <script>: the Next.js RSC payload (self.__next_f.push) ships the site not-found boundary "Die Seite wurde leider nicht gefunden." with every page. A really dead posting on the same host answers HTTP 404 (probed .../pflegefachkraft-(mwd)-1 and .../gibt-es-nicht-424242: 404, visible "nicht gefunden") -> decide() says gone from the status code before reading the body.

Gone verdicts that went through the marker path (read-only DB, all time): 29 -- 20 "200, no title token to confirm, but a gone-marker matched" (2026-09-25..29; 13 of them in the last scheduled verify, run 218, 2026-09-29 09:34 UTC), 3 "200 but title missing + gone marker" (2026-09-27), 6 "... [rendered]" (2026-09-16..18). The 306 gone rows with no note are verify_http 404 (206) / 410 (100): status-code verdicts, not marker ones.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 decide() looks for gone-markers only in the page visible text (script and style content and markup removed); an offline test of the live page shape (bare "Pflegefachkraft (m/w/d)" title, RSC <script> carrying "Die Seite wurde leider nicht gefunden.") returns live and fails on the old code, while the same message in visible text still returns gone
- [x] #2 The live pages of postings 15113, 15114 and 15314 decide live with the fixed code
- [x] #3 Every marker-based gone verdict in the DB is replayed old vs new decide() on a fresh fetch of its page; the verdicts that change and the posting ids that should be reopened are listed (no DB write)
- [x] #4 Mutation test red then byte-identical green; non-network suite 0 failed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. decide(): build the visible text once -- raw[:400000] with <script>/<style> blocks (closed or cut off by the slice) removed, then tags removed, then norm_text -- and run both GONE_MARKERS checks (no-token path and hit==0 path) on it. WALL_MARKERS and title tokens keep reading the full normalized HTML: wall pages are fingerprinted by markup (cf-browser-verification) and many boards carry the posting title only in JSON-LD <script>; stripping that would turn http-rung lives into render/Firecrawl escalations (the cost TASK-173 removes).
2. RED test first in tests/test_verify_escalation.py with a trimmed live-shaped page.
3. Mutation tests (drop the script strip; drop the style strip; scan the full body again).
4. Live: fixed decide on the 3 pages; replay all 29 marker-based gone verdicts old vs new on a fresh fetch (http; the 6 [rendered] ones through the render rung too), list ids to reopen for the main session ledger tool.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (same worktree/patch as TASK-173: /home/exedev/repo/.claude/worktrees/grpUA, NOT committed)
pflege_jobs/verify.py decide(): raw = body[:400000]; `body` (norm_text of raw) still feeds WALL_MARKERS and title tokens; new `visible` = norm_text(markup removed from raw after _NOT_VISIBLE removes <script>/<style> blocks, including one the 400 KB slice cut open: `<(script|style)\b[^>]*>.*?(?:</\1\s*>|\Z)`, re.S|re.I). Both GONE_MARKERS checks (no-token path l.~87, hit==0 path l.~91) scan `visible`. decide() is the only GONE_MARKERS user (grep: verify.py only; decide() callers: verify_url, verify_one http/render/firecrawl rungs, pflege_jobs/mechanics.py:131 Settings try-it box), so every rung and caller gets it.
Why title tokens stay on the full page: many boards carry the posting title only in a JSON-LD <script>; stripping it turns http-rung lives into render/Firecrawl escalations (the cost TASK-173 removes), and no false-live from script-held titles is in evidence. Why markup is removed too: a marker in an attribute (data-*/aria/placeholder i18n strings) is the same invisible-text class; tested (N7).

## Tests (tests/test_verify_escalation.py, section TASK-176; _NEXT_JOB_PAGE = trimmed live shape: title "Stellenangebot: Pflegefachkraft (m/w/d)", visible ad text, RSC <script> self.__next_f.push carrying "Die Seite wurde leider nicht gefunden.")
- test_decide_ignores_a_gone_marker_inside_a_script_of_a_live_posting_page: bare title -> ("live", 200, "200, no title token to confirm, no gone-marker either"); title with tokens absent from the page -> ("error", 200, "200 but title not found ...") (hit==0 path)
- test_decide_ignores_a_gone_marker_in_a_style_block_a_tag_attribute_or_a_script_cut_open_by_the_slice: <style> content:"Seite nicht gefunden" -> live; <h1 data-empty-text="Stelle nicht gefunden"> -> live; closing </script> cut off -> live
- test_decide_still_reads_a_visible_gone_message_as_gone: "<h1>Die Seite wurde leider <b>nicht gefunden</b>.</h1>" -> gone on both paths with the existing notes
RED on old decide(): 2 failed (`assert (gone, 200, ...marker matched) == (live, 200, ...either)` and `assert gone == live`); the visible-gone test passes on old code by design (its teeth: N6).
Mutations (copy in /tmp/grpUA/mut, break, run, restore from copy, rm __pycache__, run, diff -q identical -- all 7 identical, 3 passed after each restore):
 N1 script blocks not stripped -> 2 failed (script test, style/attr/cut test)
 N2 style blocks not stripped -> 1 failed (style/attr/cut)
 N3 cut-open script kept (no |\Z) -> 1 failed (style/attr/cut)
 N4 no-token path scans full body -> 2 failed
 N5 hit==0 path scans full body -> 1 failed (script test, 2nd assertion)
 N6 markup strip also drops text nodes -> 1 failed (visible gone test)
 N7 markup kept (attributes scanned) -> 1 failed (style/attr/cut)
verify test files (escalation, mech_verify_title, nested_jsonld_list, board_membership): 50 passed.

## Live
The 3 named postings, fixed verify_all(firecrawl=False, render on), no push: "http pass done: 3 decided, 0 need a browser"; 15113 live 200 http, 15114 live 200 http, 15314 live 200 http (note "200, no title token to confirm, no gone-marker either"; page Einsatzort Scheidegg / Chieming / Bayerbach).
## Replay of every marker-based gone verdict (29, read-only DB list /tmp/grpUA/gone_marker_verdicts.json)
Pages fetched once 2026-09-29 ~21:55 UTC exactly as the http rung (worktree headers incl. per-host UA) and render() would, saved under /tmp/grpUA/replay/; old decide() (main-tree verify.py == HEAD) vs new decide() on the SAME saved html, ladder emulated as verify_all (http keeps live/gone, else render keeps live/gone, else Firecrawl). Scripts /tmp/grpUA/replay_fetch.py, replay_decide.py; results decide_old.json / decide_new.json.
old ladder: http gone 25, Firecrawl-needed 4 | new ladder: http live 20, http gone 2, Firecrawl-needed 7. 23 change, 6 do not.
Unchanged (6): 10072, 10073 (16202 muenchen-klinik.de: VISIBLE "nicht mehr verfügbar" -> gone both = really gone); 6496 (77705), 6627, 6797 (18402), 6625 (16257) umantis: http 403 today, render error both -> Firecrawl-needed both (their stored gone came from a 2026-09-16/18 [rendered] check; not affected by this fix).
Changed gone -> live (20), every page checked by hand: 200, final url = posting url, <title>/<h1> = the posting title, ad text visible, only marker hits in script/style:
 - 6224, 6225 (16219), 10081, 10082 (16214) karriere.barmherzige.net: marker = WordPress theme CSS selector `body.error404` inside <style> (visible: "... Voll- oder Teilzeit München Jetzt bewerben ... Ihre Aufgaben ...")
 - 15036 (18002) oberland-jobs.de; 14975 (19001) and 14935, 14951, 14952, 14953, 14954, 14955, 14956, 14957, 14958, 15009, 15216 (clinic_id NULL) allgaeuer-jobs.de: markers only in script ("nicht gefunden" x9, "nicht mehr online" x1 per page)
 - 15113 (RH1802), 15114 (RH2395), 15314 (RH1487) mutter-kind.de: RSC not-found boundary in script
Changed gone -> undecided (3): 15045, 15056, 15065 (RH1206) are allgaeuer-jobs.de LIST pages (<h1> "66 Vollzeit Jobs in Füssen", "52 Teilzeit Jobs in Füssen", "82 Jobs in Füssen"), not postings: new verdict "200 but title not found" is the honest one; they should stay expired (phantom list rows), not be reopened.
Last scheduled verify (run 218, 2026-09-29 09:34-09:44 UTC) gone verdicts: 24 = 13 marker-based (all 13 -> live above: 6224, 14935, 14951, 14952, 14953, 14954, 14955, 14956, 14957, 14958, 14975, 15009, 15216) + 10 verify_http 404/410 + 1 redirect-to-PDF, which decide() settles before reading the body -> unchanged by construction. All 306 note-less gone rows are verify_http 404 (206) / 410 (100).
Reopen list for the main session ledger tool (NOT written by me): 6224, 6225, 10081, 10082, 14935, 14951, 14952, 14953, 14954, 14955, 14956, 14957, 14958, 14975, 15009, 15036, 15113, 15114, 15216, 15314 (20). 11 of them have clinic_id NULL (allgaeuer-jobs.de aggregator) -- reopening restores them as open-unattributed; whether that source belongs in the board is a separate call.

Validation (worktree grpUA, env sourced, __pycache__ removed first): `.venv/bin/python -m pytest -p no:cacheprovider -m "not network" -q` -> 1581 passed, 18 skipped, 2099 deselected, 0 failed (7:19); covers TASK-173 + TASK-176 together. Import check: `import pflege_jobs.verify` loads no crawlers.* module; crawlers.vendor_adapters.UA_OVERRIDE is pflege_jobs.user_agent.UA_OVERRIDE (same object); _headers_for returns H itself for a non-overridden host. Patch of ONLY these changes (4 files: crawlers/vendor_adapters.py, NEW pflege_jobs/user_agent.py, pflege_jobs/verify.py, tests/test_verify_escalation.py; +142/-23): /tmp/grpUA/ua.patch (sha256 e14c1bad...), == worktree `git diff`, `git -C /home/exedev/repo apply --check` OK at 21:54 UTC. NOT applied to the main checkout, NOT committed.

2026-09-29 22:35 UTC: the 20 postings the old gone-marker expired were re-checked with the fixed verify.verify_one (http rung): all 20 HTTP 200, 'no gone-marker either' (script /home/exedev/.claude/jobs/663542db/tmp/reverify20.py). Ivan approved the reopen (#3). tools/apply_posting_changes.py reopened all 20 (verify_status gone->live, status expired->open), read back OK for 20/20; 40 rows in pflege_jobs.corrections, code false_gone. Backup backups/apply_posting_changes_reopen20_before_20260929T223459Z.json. Ids: 6224 6225 10081 10082 14935 14951-14958 14975 15009 15036 15113 15114 15216 15314. 15045/15056/15065 stay expired (really gone).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
decide() looked for gone-markers in the whole raw HTML, including <script> and <style>. So a live posting with a bare title (no usable title token) was called gone and expired whenever its page code happened to contain a marker. Three cases were confirmed live:
- the Next.js not-found text "Die Seite wurde leider nicht gefunden." inside a script on *.mutter-kind.de (postings 15113, 15114, 15314)
- the WordPress CSS selector body.error404 on karriere.barmherzige.net
- text inside scripts on allgaeuer-jobs.de and oberland-jobs.de

Fix: decide() now builds the visible text (script and style blocks removed, including one cut off by the 400 KB limit, then the markup removed) and runs both gone-marker checks on that text. The bot-wall and title-token checks still read the whole page, because many boards carry the posting title only in a JSON-LD script.

Verified:
- 3 offline tests; 2 failed on the old code.
- 7 mutations each failed the tests, then the byte-identical restore passed.
- Non-network suite: 1581 passed, 0 failed.
- The 3 named pages verify live over plain HTTP.

Replay of all 29 gone verdicts that came from a marker (fresh fetch, old and new decide() on the same HTML):
- 20 change to live; the ids to reopen are in the notes, 11 of them unattributed allgaeuer-jobs.de rows.
- 3 change to undecided: RH1206 list pages, which should stay expired.
- 6 are unchanged: 2 are really gone, 4 umantis pages answer 403.

Nothing was written to the DB; reopening is done by the main session with the ledger tool. The patch is /tmp/grpUA/ua.patch and is not yet applied to main.
<!-- SECTION:FINAL_SUMMARY:END -->
