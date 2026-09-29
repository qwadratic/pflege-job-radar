---
id: TASK-161
title: 'Public secret-scanning alert #1: google_api_key в тестовой фикстуре'
status: To Do
assignee: []
created_date: '2026-09-25 15:38'
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
- [ ] #1 Источник ключа в фикстуре установлен
- [ ] #2 Alert закрыт (dismissed или ключ заменён на плейсхолдер)
<!-- AC:END -->
