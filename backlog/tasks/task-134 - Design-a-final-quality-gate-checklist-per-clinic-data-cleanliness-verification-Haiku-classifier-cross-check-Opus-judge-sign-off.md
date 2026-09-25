---
id: TASK-134
title: >-
  Design a final-quality-gate checklist: per-clinic data cleanliness
  verification, Haiku-classifier cross-check, Opus judge sign-off
status: To Do
assignee: []
created_date: '2026-09-23 14:44'
updated_date: '2026-09-25 00:10'
labels:
  - db-quality
dependencies: []
priority: low
type: spike
ordinal: 134000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's brief, 2026-09-23 (verbatim intent, not yet scoped/designed): a checklist to run as the FINAL quality pass once the current remediation backlog (TASK-81/96/99/100/117/118/126/127/129/130/131/132/133 family, adapter fixes, etc.) is largely done -- a per-clinic 'is this record clean' verification, not a one-off audit script.\n\nExplicit requirements from the brief:\n1. LLM-assisted, but classification calls should max out at Haiku (cheap/fast) for the bulk mechanical checks -- cost discipline, not Opus/Sonnet for routine classification.\n2. One methodology component: benchmark this codebase's own deterministic code classifiers (classify_role, department_hint, qualification_hint, employer_norm/employer_class, etc.) AGAINST a Haiku-based classification of the same input, on a sample -- the code classifiers should perform AT LEAST as well as the Haiku baseline; where they don't, that is itself a finding.\n3. A final adjudication layer: one, at most two, Opus judges running at maximum reasoning effort, for whatever needs the highest-quality sign-off (ambiguous cases the Haiku/code layers disagree on, or a final go/no-go per clinic) -- deliberately capped at 1-2 judge calls, not a big fan-out, to control cost.\n4. Deliberately deferred: 'this is just an idea for now, for the very end' -- do NOT start building this yet. This task exists so the idea isn't lost, not as a currently-actionable ticket.\n\nNot yet decided (needs scoping when this is picked up): what exactly counts as 'clinic cleanliness' (registry field plausibility per TASK-133's lint? posting-to-clinic attribution correctness per the TASK-81 family? board-coverage completeness per TASK-88? some combination?), what the Haiku-vs-code-classifier comparison sample size/methodology should be, what the Opus judges actually adjudicate and on what evidence, and how results get surfaced (a report, a new /api/coverage-style verdict, a one-off script).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Scope decided: which existing quality dimensions (registry field validity, clinic attribution correctness, board coverage completeness, classifier accuracy, other) the checklist actually covers, and which are explicitly out of scope for this pass
- [ ] #2 Haiku-vs-code-classifier benchmark methodology defined: sample size/selection, which code classifiers are compared, pass/fail criteria
- [ ] #3 Opus judge step defined: what it adjudicates, what evidence it sees, capped at 1-2 calls per unit under review (not per-clinic unbounded fan-out)
- [ ] #4 A worked example run against a small sample (e.g. 5-10 clinics) before committing to running it registry-wide
<!-- AC:END -->
