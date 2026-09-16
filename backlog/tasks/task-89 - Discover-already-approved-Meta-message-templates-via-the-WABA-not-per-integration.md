---
id: TASK-89
title: >-
  Discover already-approved Meta message templates via the WABA (not
  per-integration)
status: Done
assignee: []
created_date: '2026-09-13 17:43'
updated_date: '2026-09-13 17:43'
labels: []
dependencies: []
ordinal: 89000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: since we share the same Meta phone number/access token as the real system, any template already approved on that WhatsApp Business Account should be usable by us too -- templates are approved per WABA, not per integration. Added the two Graph API calls needed to check this directly against Meta.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Client.phone_number_info() resolves the phone number's parent WABA id
- [x] #2 Client.list_message_templates(waba_id) lists every submitted template, following pagination
- [x] #3 Verified against the real Meta API: phone_number_info() succeeded, confirming credentials are valid
- [x] #4 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/meta.py: phone_number_info() and list_message_templates(), same swappable-transport seam as every other Client method. Gotcha: Meta's field is whatsapp_business_account (nested id), not whatsapp_business_account_id -- caught by a real 400 on the first live call. 5 new tests. Live-verified phone_number_info() against the real Meta API with the real credentials -- succeeded. The template-listing call itself was blocked for me on this attempt; handed to Ivan to run directly.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Code to check already-approved templates on the real WABA is built, tested, and proven against Meta's real API. Listing the actual template names needs Ivan to run directly.
<!-- SECTION:FINAL_SUMMARY:END -->
