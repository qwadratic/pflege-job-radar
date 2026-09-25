---
id: TASK-106
title: >-
  Evaluate LLM-based structured extraction for department/requirements vs
  TASK-97's regex/section approach
status: To Do
assignee: []
created_date: '2026-09-22 16:27'
updated_date: '2026-09-25 00:10'
labels:
  - research
dependencies: []
references:
  - pflege_jobs/classify.py
  - app/cv.py
  - docs/campaign.md
  - docs/firecrawl.md
  - TASK-97
  - TASK-104
  - TASK-105
type: spike
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 follow-up to a request to check what options exist to raise data quality for candidate-clinic matching (department/Fachbereich + requirements signal pulled from a posting's body). TASK-97 already measured the problem (department_hint is title-only/single-match; naive whole-body classification was measured to pollute the facet with false positives from nav/contacts/hospital-wide facility descriptions) and picked one fix: title plus targeted sections (Aufgaben/Taetigkeiten/Profil), regex-based, multi-label. That is a reasonable default, but no task in this backlog evaluates the other broad option class: an LLM extraction pass.

The pattern already exists and works in this codebase. app/cv.py's _llm_refine() calls an OpenAI-compatible endpoint (env LLM_API_BASE/LLM_MODEL, wired through the live POST /api/cv endpoint, app/main.py:302) with a JSON-schema prompt to extract a structured candidate profile (departments, qualifications, experience_years, languages) from free CV text, falling back silently to the regex-only profile on any failure. The same shape of call -- one JSON-schema prompt per posting description, run once at ingestion and cached by the posting's existing content_hash so an unchanged posting is never re-billed -- could produce a multi-label department list and a short requirements summary directly from a posting's body, without hand-tuning section-boundary regexes.

This is a genuine trade, not a strictly-better option. classify.py's module docstring states its design principle: 'every derived value carries the rule that produced it (auditable by agents)' -- a regex match's rule is the pattern itself; an LLM's output carries no such rule unless the prompt is also made to return the source span it grounded on. The pipeline already tracks LLM/Firecrawl cost per posting explicitly (docs/campaign.md, docs/firecrawl.md: max_usd_per_posting tightened to $0.10) -- a natural, already-established yardstick to measure a per-posting LLM extraction call against, across however many open postings would need one (~2500-3000 at current scale) plus every future re-crawl of a changed posting.

Not proposing to build either option here -- Ivan wants the options laid out before committing to one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A written comparison exists covering, for both TASK-97's regex/section extraction and an LLM extraction pass reusing the app/cv.py _llm_refine() pattern: what each takes to build, ongoing per-posting operating cost (LLM approach measured against the existing $0.10/posting Firecrawl bar in docs/campaign.md), and how false-positive risk would be checked for each
- [ ] #2 Decision recorded: regex/section only (TASK-97 as already scoped), LLM as a fallback only where the regex/section approach leaves a posting unlabeled, or LLM as the primary method -- for department extraction and, separately, for requirements extraction -- with the reason for each
- [ ] #3 If any LLM scope is chosen, a follow-up implementation task is filed with the same false-positive sample-check discipline TASK-97's AC#4 already set (a numbered manual sample, not an assumption) and an explicit per-posting cost cap, before any code is written
<!-- AC:END -->
