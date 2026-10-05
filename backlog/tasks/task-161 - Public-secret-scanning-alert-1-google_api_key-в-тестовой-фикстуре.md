---
id: TASK-161
title: 'Public secret-scanning alert #1: google_api_key в тестовой фикстуре'
status: Done
assignee: []
created_date: '2026-09-25 15:38'
updated_date: '2026-10-05 15:40'
labels:
  - security
dependencies: []
priority: medium
ordinal: 161000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
GitHub secret-scanning alert открыт с даты создания репо: https://github.com/qwadratic/pflege-job-radar/security/secret-scanning/1 (secret_type: google_api_key). Ключ похож на сторонний Google Maps API key в тестовой фикстуре, не боевой секрет проекта. Проверить, откуда ключ, при необходимости заменить фикстуру плейсхолдером и закрыть alert (dismiss как false positive/used in tests), либо ротировать ключ если он реальный.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Источник ключа в фикстуре установлен
- [x] #2 Alert закрыт (dismissed или ключ заменён на плейсхолдер)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-05 (pflege-clawl). Source: GitHub secret-scanning alert 1 (google_api_key) points at tests/fixtures/board_samples/muenchen_klinik_detail_0..3_sample.html line 276, first seen in commit 14cacc4. It is the data-api attribute on the body tag of four recorded pages of the Muenchen Klinik careers site: the website's own public Google Maps key, a third-party key, not a project secret. No code or test reads data-api (search over tests, pflege_jobs, crawlers, app). Replaced in all four files by REDACTED-GOOGLE-API-KEY; a search over all tracked files for the key prefix is now empty; tests/test_vendor_adapters.py 85 passed. The old string stays in the repository history (Ivan 2026-10-05: no history rewrite) and alert 1 stays open on GitHub until someone dismisses it (Security, Secret scanning, close as used in tests); AC 2 allows replacement.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Source established: a third-party public Maps key inside four recorded Muenchen Klinik pages. Replaced by a placeholder in all four fixtures; the Muenchen Klinik adapter tests pass (85). Alert 1 on GitHub still needs a manual dismissal; the repository history keeps the old string by decision.
<!-- SECTION:FINAL_SUMMARY:END -->
