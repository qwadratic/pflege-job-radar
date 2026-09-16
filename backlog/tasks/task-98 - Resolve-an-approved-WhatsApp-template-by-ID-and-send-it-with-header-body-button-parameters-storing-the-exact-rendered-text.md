---
id: TASK-98
title: >-
  Resolve an approved WhatsApp template by ID and send it with
  header/body/button parameters, storing the exact rendered text
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 14:24'
updated_date: '2026-09-16 14:13'
labels: []
dependencies: []
type: feature
ordinal: 98000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-14: a colleague registers a campaign template and gives us its ID; we send it ourselves. app/wa/meta.py:send_template only takes a name + language + body params: it cannot look a template up by ID, cannot fill header or button (quick-reply payload / URL) parameters, and nothing records the text the candidate actually received. Ivan asked that nothing is lost or unsupported.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 a client call resolves a template ID to name, language, status, category and components (read-only Graph GET) and callers can require status APPROVED
- [x] #2 send_template sends header (text/media), body and button parameters for any component layout Meta allows, and fails loudly naming the missing variable when a required parameter is not supplied
- [x] #3 a render function produces the exact header/body/footer/button text the recipient sees from components + parameters, used for the stored outbound row
- [x] #4 offline tests cover lookup, every component type, missing parameters and rendering; a read-only live lookup of an existing approved template succeeds; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. meta.py Client: get_template(template_id, require_approved) = read-only GET /{id}?fields=id,name,language,status,category,sub_category,parameter_format,components; find_template(waba_id, name, language) via list_message_templates (new optional fields/name query args, exact client-side match, zero or several matches raise); require_approved(definition).
2. Pure module functions: one walker over the definition (HEADER TEXT/IMAGE/VIDEO/DOCUMENT/LOCATION/PRODUCT, GIF rejected as MM-API-only, BODY positional/named incl. currency/date_time values, FOOTER, BUTTONS QUICK_REPLY/URL/OTP/COPY_CODE/FLOW/CATALOG/MPM/SPM/PHONE_NUMBER/VOICE_CALL, LIMITED_TIME_OFFER, CAROUSEL cards, CALL_PERMISSION_REQUEST, send-only tap_target_configuration) that collects every missing/extra/invalid variable and builds Meta send components + rendered view together. validate_template_params -> problems, build_template_components / render_template raise TemplateParamsError(problems) before any POST; unknown component/button types fail loudly.
3. send_template(to, template_name=None, language=None, params=None, *, definition=None): legacy name+lang+body-list path unchanged; definition path validates, checks name/language match, posts built components.
4. render_template -> {name, language, header, limited_time_offer, body, footer, buttons[{type,text,payload/url/phone_number/code}], carousel, call_permission_request, tap_target_configuration, text(flat)}.
5. tests/test_wa_meta_templates.py: lookup (GET only, fields, approved gate, find by name+lang), every component type, named/positional, missing/extra, rendering, legacy signature.
6. docs/whatsapp.md paragraph; read-only live lookup + render of recruitment_job_wohnung_pflege_v2_de (GET only).

Repair round 2 (final verifier 2026-09-14): meta.Client.phone_number_info default fields = display_phone_number,verified_name (the set Graph v25.0 answers on the phone-number node with this token; whatsapp_business_account gave #100 live), docstring says the WABA id comes from META_WHATSAPP_WABA_ID, callers can still pass fields; test the default URL and an explicit field list; docs line.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-14: meta.py: get_template (GET /{id}?fields=id,name,language,status,category,sub_category,parameter_format,components), find_template (list with fields+name, exact client-side match; Meta's name filter is a substring match, confirmed live), ensure_approved / require_approved=True, list_message_templates(fields=, name=) with unchanged default URL. One walker (_TemplateWalk) binds values to the definition and yields Meta send components, the recipient view and every problem: header TEXT/IMAGE/VIDEO/DOCUMENT/LOCATION/PRODUCT (GIF rejected: Marketing Messages API only), body POSITIONAL/NAMED incl. currency/date_time, footer, buttons QUICK_REPLY/URL/OTP/COPY_CODE/FLOW/CATALOG/MPM/SPM/PHONE_NUMBER/VOICE_CALL, LIMITED_TIME_OFFER, CAROUSEL, CALL_PERMISSION_REQUEST, send-only tap_target_configuration; unknown component/button types are problems. validate_template_params / build_template_components / render_template; send_template(to, definition=, params=) raises TemplateParamsError before any POST; legacy send_template(to, name, lang, [body]) unchanged. tests/test_wa_meta_templates.py 33 tests. docs/whatsapp.md paragraph after TASK-70.
Live read-only 2026-09-14 (GET only, transport refused anything else): phone_number_info -> #100 nonexisting field whatsapp_business_account (token), used META_WHATSAPP_WABA_ID; find_template + get_template for recruitment_job_wohnung_pflege_v2_de (1061710426317037) and recruitment_bayern_stellen_interesse_de (1791710088522158): APPROVED, POSITIONAL, text header, body {{1}}, 2 quick replies; find == get; rendered with dummy params. All 29 WABA templates validate without unsupported types (incl. 2 CALL_PERMISSION_REQUEST templates).

Found live 2026-09-14: Client.phone_number_info default fields include whatsapp_business_account, which Graph v25.0 rejects on the phone-number node: (#100) Tried accessing nonexisting field (whatsapp_business_account). display_phone_number,verified_name works. The WABA id (<WABA id>) must come from config/env or another supported lookup; fix the default and any caller relying on it. REAL CAMPAIGN TEMPLATE (approved 2026-09-14, read-only lookup on WABA <WABA id>): id 1791710088522158, name recruitment_bayern_stellen_interesse_de, language de, MARKETING, parameter_format POSITIONAL. HEADER text "Neue Stellen in Bayern für Pflegekräfte"; BODY "Hallo, {{1}}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele neue Stellen in Bayern. Haben Sie noch Interesse?" ({{1}} = candidate name); QUICK_REPLY buttons "Ja, ich habe Interesse" and "Nein, kein Interesse" (a tap arrives as type=button with that text/payload). The No button is a decline (TASK-101: fixed ack once, then silence); the Yes button is interest in Bayern (TASK-100). Test sends at 15:46 UTC to two test numbers (old-system test candidate id 14 and Ivan) were accepted by Meta; they were not recorded in wa.sqlite. Phone number verified_name is "Valentyn NDT".

Repair round 2 2026-09-14 (final verifier problem 4): meta.Client.phone_number_info default fields are now display_phone_number,verified_name (the old default whatsapp_business_account failed the whole GET live with #100); docstring points to META_WHATSAPP_WABA_ID; explicit fields still pass through. tests/test_wa_harness.py test_phone_number_info_asks_only_for_fields_the_phone_number_node_answers (exact default URL, GET, no body; explicit field list) replaces the WABA-id test, which asserted the rejected field. No live call made (not needed: the working field set was recorded live in the note above). Docs: TASK-98 paragraph.

Final verification 2026-09-14 20:45-20:57 UTC (after 4-lens review, adversarial verify, fixer + 2 repair rounds): offline suite 1474 passed, 126 skipped, 0 failed. pflege-wa.service restarted 21:22 UTC on this tree; health webhook_ready/outbound_ready/luna_ready true, threads 200. Nothing sent; campaign not run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Templates can be resolved by ID (get_template/find_template, require_approved) and sent with any header/body/button parameters; missing or extra variables fail before any POST; render_template yields exactly what the recipient sees for the stored row. Verified by 33 offline tests, a read-only live lookup and render of the real campaign template 1791710088522158, and the full offline suite.
<!-- SECTION:FINAL_SUMMARY:END -->
