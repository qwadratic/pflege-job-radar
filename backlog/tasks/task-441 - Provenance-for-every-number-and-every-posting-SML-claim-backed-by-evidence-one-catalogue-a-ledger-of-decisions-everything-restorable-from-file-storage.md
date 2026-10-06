---
id: TASK-441
title: >-
  Provenance for every number and every posting: SML claim backed by evidence,
  one catalogue, a ledger of decisions, everything restorable from file storage
status: To Do
assignee: []
created_date: '2026-10-06 11:06'
labels:
  - registry
  - provenance
  - architecture
  - data-quality
dependencies: []
priority: medium
ordinal: 318000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06, architecture. (1) SML index: every clinic carries a size claim S/M/L, produced by an algorithm that depends on the clinic type (tags, TASK-431.6): acute and Reha have their own rules and are not pooled (2.71 versus 0.42 postings per 100 beds). The claim exists for ALL clinics; where no evidence exists the claim says so with a reason, it is never blank. (2) Claim and evidence: one catalogue of every number that counts as evidence of a clinic's size. A claim (S/M/L, beds) is derived from evidence rows. An evidence row holds the value, the kind of number (approved beds, set-up beds, planned, day places, social places), the source, how it was collected (document, web page, web search), source URL, date seen, and an agent note (free text: who or what found it, quote, locator, confidence). A document is collected like a web page where possible (downloaded by URL) and marked: state or operator document, from where, downloaded when, hash. The `clinic_numbers` proposal of TASK-431.5 is the first version of this catalogue. (3) Provenance chain through the posting pipeline, same discipline as for registry numbers: fetch, parse, enrich, classify, link to clinic, verify. Each step records what it read (source, fetch time, content hash) and what decided (rule or model, version); raw first (TASK-11). (4) Reconstruction: the database can be rebuilt from file storage. The mirror on Bunny (TASK-197) holds every source page and document the registry and the pipeline read, not only clinic boards; the CI runner puts the missing sources on the file storage. Known gap on 2026-10-06: the Krankenhausplan PDFs (data/registry/krankenhausplan_2024.pdf, _2025.pdf, _2026.pdf) are git-ignored (*.pdf) and exist only on the disk of the VM; the Destatis xlsx and reha_bavaria.csv are in the repo. Plus one ledger of decisions, automatic (rule, version, input hash, output) and manual (TASK-174 ledger, TASK-180 corrections table). Builds on TASK-174, TASK-180, TASK-197, TASK-11, TASK-431.5, TASK-431.6. First deliverable is design and inventory, not code. Suggested order of delivery, smallest verifiable unit first: clinic_numbers for beds, then the SML claim for all 651, then documents on the file storage, then provenance of the posting pipeline.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Inventory: every source the registry and the posting pipeline read, the field or table it feeds, where its bytes live (repo, VM disk only, Bunny mirror) and whether the database can be restored from it
- [ ] #2 Schema for claim, evidence and agent note, consistent with the corrections table (TASK-180), and how automatic decisions are ledgered
- [ ] #3 SML claim defined per clinic type and produced for all 651 clinics, each with evidence or an explicit no-evidence reason; counts per type, none blank
- [ ] #4 Restore plan: every source missing from the file storage listed with who uploads it (CI runner or tool) and a restore drill that reads the storage only, never a live site
- [ ] #5 Ivan approves the schema and the order of delivery before any database change
<!-- AC:END -->
