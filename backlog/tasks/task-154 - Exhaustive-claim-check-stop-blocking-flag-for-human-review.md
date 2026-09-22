---
id: TASK-154
title: 'Exhaustive-claim check: stop blocking, flag for human review'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 08:08'
updated_date: '2026-09-22 08:26'
labels: []
dependencies: []
ordinal: 162000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Round 4 (2026-09-22) of the exhaustive-claim rule in app/wa/luna/grounding.py failed in both directions on live turns: it silenced a fully true Straubing sentence and its corrective rewrite (both parts of the two-branch fix died), and separately let three fabricated distribution claims through once the preposition set was enumerated. Root cause is not a regex bug: the rule tries to recognise an open-ended claim shape in free German, and German always has another way to say it, so blocking on it is structurally unsound. Decision: this check stops blocking and becomes a flag -- reply still reaches the candidate, thread is marked for human review with what the rule suspected. The asymmetry argument: when the rule is wrong in the silencing direction the candidate gets a dead-end holding message and leaves; when it is wrong in the permissive direction the candidate sees one clinic where several matched and can ask a follow-up. The four other guards (invented clinic names, unsupported figures, non-live postings, the five-position cap) stay blocking and are untouched by this change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 check_reply no longer raises on an exhaustive-claim match; the reply still reaches the candidate
- [x] #2 A detected exhaustive claim is recorded on the thread (the way an escalation is recorded today) with the suspected sentence, so a human can review it
- [x] #3 Dead machinery serving only the blocking decision is deleted: the preposition enumeration, the distribution regex, and the scoped-counts plumbing added only for it; anything other rules still use is kept
- [x] #4 The four blocking guards (invented clinic name, unsupported figure, non-live posting, five-position cap with truthful remainder) are read before and after and still fire unchanged
- [x] #5 grounding.py module docstring explains why this check is a flag and the other four are blocks, with the asymmetry argument
- [x] #6 tests/test_wa_luna_dialog_rules.py: the round-4 Straubing sentence reaches the candidate and leaves a flag; 'nur diese 5 Kliniken in Bayern' against a true 278 reaches the candidate but is flagged (test name says plainly it is no longer blocked); invented clinic name still BLOCKED; unsupported figure still BLOCKED; sixth position still BLOCKED; non-live posting still never reaches the model
- [x] #7 tests/test_wa_luna_dialog_rules.py run alone passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. grounding.py: merge _EXHAUSTIVE_RE back to one shape -- drop the negative lookahead that routed
   "alle bei/beim/im/in NOUN" to a separate path (the preposition enumeration). Detection alone no
   longer needs the split since truth is no longer verified.
2. Delete _EXHAUSTIVE_DISTRIB_RE, _CLINIC_NOUN_RE, _POSITION_NOUN_RE, _distribution_subject,
   _distribution_claimed, _exhaustive_distribution_claim and their comment blocks; delete
   _POSITION_TOTAL_TOOLS/_CLINIC_TOTAL_TOOLS and the subject_found/(city,subject)-tuple plumbing in
   turn_evidence (keep the flat per-city counts_by_city[city] used by _false_counts's marker scoping
   -- round-3, unrelated rule, still blocking).
3. check_reply: add an optional `flagged` out-list param. Replace the two raise blocks (direct
   exhaustive claim + distribution claim) with one: compute claim = _exhaustive_claim(text) if
   remaining > 0, and if truthy append it to `flagged` instead of raising. Everything else in
   check_reply (NO INVENTION, COUNT figure check, STALE, VOLUME cap, remainder-disclosure, BRANCHES)
   is untouched.
4. luna_brain.py _checked_reply: pass flagged=[] into GR.check_reply, carry it through the returned
   dict. turn(): where checked["escalate_reason"] is recorded onto card._escalated/_escalate_reason
   today, add the same recording for checked["flagged"] (reply already sent; thread flagged for a
   human with the suspected sentence).
5. Update the module docstring: rule 3's bullet, and a new ROUND 5 paragraph with the asymmetry
   argument (why this one flags, why the other four still block).
6. tests/test_wa_luna_dialog_rules.py: convert every existing test that asserted the exhaustive-claim
   rule BLOCKS (direct "alle/nur diese ... Kliniken" shape and the round-4 Straubing distribution
   shape) into a test that asserts it reaches the candidate and is recorded in `flagged`. Delete the
   two round-4 tests that pinned the deleted scoped-evidence verification. Add/confirm: Straubing
   sentence flagged not blocked; "nur diese 5 Kliniken" against a true 278 flagged not blocked;
   invented clinic name still BLOCKED; unsupported figure still BLOCKED; sixth position still
   BLOCKED; non-live posting still never reaches the model. Leave the remainder-disclosure and
   BRANCHES tests (different code path) unchanged.
7. Read the four blocking guards before and after (git diff) to confirm untouched. Run only
   tests/test_wa_luna_dialog_rules.py, report the result line.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented exactly per plan. grounding.py: _EXHAUSTIVE_RE's negative-lookahead preposition split
removed (merges "alle Kliniken" and "alle bei/beim/im/in Klinik X" back into one detection shape);
_EXHAUSTIVE_DISTRIB_RE/_distribution_subject/_distribution_claimed/_exhaustive_distribution_claim and
_POSITION_TOTAL_TOOLS/_CLINIC_TOTAL_TOOLS plus the (city,subject)-tuple plumbing in turn_evidence
deleted. check_reply gained an optional `flagged` out-list param; the two former raise blocks for the
exhaustive/distribution claim collapse into one non-raising append to `flagged`. The four blocking
guards (STALE, NO INVENTION, VOLUME, COUNT's figure check) and the remainder-disclosure/BRANCHES
block are byte-for-byte unchanged -- confirmed by direct before/after reading (captured in this
session's transcript) since grounding.py is a new untracked file with no git baseline to diff against.
Module docstring: rule 3's bullet updated, ROUND 4's dangling references to deleted symbols fixed,
new ROUND 5 paragraph added with the decision, what was deleted and why, and the asymmetry argument
(silencing costs the whole answer + a stalled thread; over-flagging costs one extra candidate turn).
luna_brain.py: _checked_reply passes flagged=[] into GR.check_reply and threads it through its
returned dict; turn() records it on the card next to where escalate_reason already is
(card._escalated/_escalate_reason, the same fields app/wa/api.py uses for unread media), only when
escalate_reason itself is unset (the two paths are mutually exclusive: escalate_reason only fires on
the two-strikes blocked-and-held path, flagged only survives on a reply that actually went out).
Tests: tests/test_wa_luna_dialog_rules.py rewritten where sentences relied on the old blocking
behaviour (test names now say "reaches the candidate"/"flagged" instead of "rejected"/"blocked");
deleted the two round-4 tests that pinned the now-deleted scoped-evidence verification; added a new
ROUND 5 section with the Straubing flag test, the audit-D "true 278" flag test, a bonus
still-detected-but-flagged false distribution claim, an integration test proving app/wa/luna_brain.py
records the flag on the card while sending the reply unmodified, and four narrow re-pins of the four
guards this round leaves blocking (NO INVENTION/COUNT/VOLUME/STALE).
Verified: `PFLEGE_TESTS_OFFLINE=1 python -m pytest tests/test_wa_luna_dialog_rules.py -q` ->
162 passed in 2.51s (grew from the pre-round-5 151 passing by 11: 4 rewritten in place, 2 deleted,
9 added -- net +11 across the file, some pre-existing tests also renamed in place without changing
count). pyflakes clean on all three touched files. Did not run the full suite or touch git, per the
task's explicit instructions (lane-test-only; another workflow owns bridge/**, api.py, choices.py).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Took the exhaustive-claim check ("these are all there are" / "alle beim Klinikum X") off the
blocking path in app/wa/luna/grounding.py. It now only appends the suspected sentence to
check_reply's new `flagged` out-list; app/wa/luna_brain.py records that on the thread's card the
same way an escalation is recorded today (card._escalated/_escalate_reason) and sends the reply
unmodified. Deleted the machinery that existed only to verify this check's truth before blocking on
it: the round-4 preposition split in _EXHAUSTIVE_RE, _EXHAUSTIVE_DISTRIB_RE and its
subject/claimed/claim helpers, and the (city, subject)-tuple entries in turn_evidence's
counts_by_city (the flat per-city entries round 3 built for the COUNT rule's own approximation-
marker scoping are untouched). The four blocking guards (invented clinic names, unsupported
figures, non-live postings, the five-position cap with its remainder-and-branches obligation) are
unchanged. Module docstring documents the decision and the asymmetry argument (silencing a true
reply costs the candidate their whole answer and stalls the thread; a missed false claim costs one
extra follow-up turn) so it will not be "restored" by a future round.
Verified: tests/test_wa_luna_dialog_rules.py run alone -> 162 passed in 2.51s, including the
Straubing sentence reaching the candidate with a flag, "nur diese 5 Kliniken" against a true 278
reaching the candidate but flagged, and the four guards still raising (NO INVENTION/COUNT/VOLUME/
STALE). pyflakes clean on all three touched files.
<!-- SECTION:FINAL_SUMMARY:END -->
