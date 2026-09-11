---
id: TASK-19
title: >-
  Adapter = one script plus parameters: explicit variables, helper zoo,
  board-to-helper mapping
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
labels:
  - harvester
dependencies: []
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's central design goal (2026-09-09): every adapter is the same script; only parameters differ. Today crawlers/vendor_adapters.py already shares get(), row() and parse_job_page(); the twelve crawl_* functions differ in listing URL, pagination parameter, job-link regex, detail parse mode and include/reject paths, and those differences sit as literals inside each function or in GROUP_PORTALS and ats_seeds.BUILDERS. A parameter may itself be a function: a small zoo of pure helpers for real exceptions (Waldhausklinik's div.faqAccCard accordion, München Klinik's jobLocation.name site attribution), each taking explicit inputs and optionally a context, no globals. Inside the engine a plain mapping from board or family to its helpers. Ivan explicitly does not want an object hierarchy yet -- modules and pure functions with clear parameter sets; OOP is a later option if it earns its place. The seeded family (bite, softgarden, pi_asp) needs its own seed and session and returns observations, so its wrapper is app/crawl.py _seed_obs, not a one-liner (critique finding).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One engine loop runs every family; a family is a data record of variables plus optional helper functions, with no per-vendor branch in the loop
- [ ] #2 Every literal that differs between vendors today is a named variable on the family record
- [ ] #3 Helpers are pure functions with explicit parameters and are covered by one test each
<!-- AC:END -->
