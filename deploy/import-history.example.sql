-- Queries file for app/wa/luna/import_history.py (TASK-102, TASK-105): one worked set for the old WhatsApp recruiting
-- system on tasker-dispatcher-01 (sales_brain.sqlite). Written from that system's code only (candidate_whatsapp_store.py
-- DDL, candidate_agent_card.py card slots, candidate_whatsapp_ad_leads.py / candidate_bayern_housing_offer.py
-- metadata writes, manager_crm/service.py attachment classes, sales_brain/placement stages; opt_outs:
-- candidate_lifecycle.py, candidate_job_wohnung_outreach.py, candidate_suppression.py, sales_brain/schema.sql);
-- no data was read.
-- Column contract: the module docstring. Every query gets :phone (+E.164) and :phone_digits.
--
-- Run (dry-run first; the service user needs the read grants in docs/rollout-runbook.md):
--   .venv/bin/python -m app.wa.luna.import_history --db /opt/clinic-dispatcher/var/sales_brain.sqlite \
--     --queries deploy/import-history.example.sql --source clinic-dispatcher \
--     --media-root /opt/clinic-dispatcher/data/private/candidate_whatsapp_media \
--     --media-root /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media \
--     --phones-file <campaign phone list> [--apply]
--
-- Person records of a phone: every candidate a WhatsApp message of that number is linked to
-- (candidate_whatsapp_messages.phone_e164 is canonical E.164), plus candidates whose metadata phone/primary_phone/
-- answers.phone is that number written as +E.164 or digits. Old-system card: candidates.metadata_json.wa_agent.slots.

-- query: facts
WITH meta AS (SELECT id, CASE WHEN json_valid(metadata_json) THEN metadata_json END AS m FROM candidates),
cand AS (
  SELECT candidate_id AS id FROM candidate_whatsapp_messages WHERE phone_e164 = :phone AND candidate_id IS NOT NULL
  UNION
  SELECT id FROM meta WHERE :phone_digits IN (
    replace(replace(replace(coalesce(json_extract(m, '$.phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.primary_phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.answers.phone'), ''), '+', ''), ' ', ''), '-', ''))
)
SELECT 'candidates:' || c.id AS source_ref,
       -- apply_qualification_to_candidate writes answers.region and region; the agent card keeps slots.region
       coalesce(json_extract(c.m, '$.wa_agent.slots.region'), json_extract(c.m, '$.answers.region'),
                json_extract(c.m, '$.region')) AS region,
       NULL AS city,   -- the old card has no city slot (city_size and clinic_pref are not a city)
       -- answers.departments is a list; only a single department is a department_pref the board filter reads
       CASE WHEN json_type(c.m, '$.answers.departments') = 'array'
             AND json_array_length(json_extract(c.m, '$.answers.departments')) = 1
            THEN json_extract(c.m, '$.answers.departments[0]') END AS department_pref,
       CASE json_extract(c.m, '$.wa_agent.slots.qualification_path')
            WHEN 'urkunde' THEN 'urkunde' WHEN 'defizit' THEN 'defizit'
            WHEN 'pending_after_pruefung' THEN 'kenntnispruefung' WHEN 'reject' THEN 'reject' END AS qualification_path,
       json_extract(c.m, '$.wa_agent.slots.qualification_ok') AS qualification_ok,   -- true/false/null
       json_extract(c.m, '$.wa_agent.slots.urkunde_status') AS urkunde_status,
       -- housing_known defaults to false on every old card: only true is a known fact
       CASE WHEN json_extract(c.m, '$.wa_agent.slots.housing_known') = 1 THEN 1 END AS housing_known,
       CASE WHEN json_extract(c.m, '$.wa_agent.slots.people_count') > 0
            THEN json_extract(c.m, '$.wa_agent.slots.people_count') END AS people_count
FROM meta c WHERE c.id IN (SELECT id FROM cand)
ORDER BY c.id

-- query: placement
WITH meta AS (SELECT id, CASE WHEN json_valid(metadata_json) THEN metadata_json END AS m FROM candidates),
cand AS (
  SELECT candidate_id AS id FROM candidate_whatsapp_messages WHERE phone_e164 = :phone AND candidate_id IS NOT NULL
  UNION
  SELECT id FROM meta WHERE :phone_digits IN (
    replace(replace(replace(coalesce(json_extract(m, '$.phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.primary_phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.answers.phone'), ''), '+', ''), ' ', ''), '-', ''))
)
-- A clinic case row is written on submission (placement/service.py, clinic_interview_state.py insert status 'submitted').
SELECT 'candidate_clinic_cases:' || cc.id AS source_ref, cc.clinic_key AS clinic, cc.status AS status, NULL AS stage,
       cc.contract_start_date AS contract_start_date, cc.updated_at AS updated_at, 1 AS submitted,
       CASE WHEN cc.register_signed = 1 OR cc.contract_start_date IS NOT NULL THEN 1 ELSE 0 END AS placed
FROM candidate_clinic_cases cc WHERE cc.candidate_id IN (SELECT id FROM cand)
UNION ALL
-- Candidate-level stage (sales_brain/placement/stages.py): case stages mean submitted, contract_signed/paid_closed placed.
SELECT 'candidate_recruitment_state:' || rs.id, NULL, NULL, rs.placement_stage, NULL, rs.updated_at,
       CASE WHEN rs.placement_stage IN ('submitted_waiting_clinic', 'interview_coordinating', 'interview_scheduled',
                                        'post_interview_trial_decision', 'trial_coordinating', 'trial_scheduled',
                                        'post_trial_contract_decision', 'contract_signing', 'contract_signed',
                                        'paid_closed') THEN 1 ELSE 0 END,
       CASE WHEN rs.placement_stage IN ('contract_signed', 'paid_closed') THEN 1 ELSE 0 END
FROM candidate_recruitment_state rs WHERE rs.candidate_id IN (SELECT id FROM cand)

-- query: messages
-- Every WhatsApp message of the number, linked to a candidate or not; direction is 'inbound'/'outbound' there.
SELECT 'candidate_whatsapp_messages:' || wamid AS source_ref,
       CASE direction WHEN 'inbound' THEN 'in' WHEN 'outbound' THEN 'out' END AS direction,
       message_type AS kind, coalesce(body, caption) AS body, occurred_at AS at
FROM candidate_whatsapp_messages WHERE phone_e164 = :phone
ORDER BY occurred_at, id

-- query: documents
WITH meta AS (SELECT id, CASE WHEN json_valid(metadata_json) THEN metadata_json END AS m FROM candidates),
cand AS (
  SELECT candidate_id AS id FROM candidate_whatsapp_messages WHERE phone_e164 = :phone AND candidate_id IS NOT NULL
  UNION
  SELECT id FROM meta WHERE :phone_digits IN (
    replace(replace(replace(coalesce(json_extract(m, '$.phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.primary_phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.answers.phone'), ''), '+', ''), ' ', ''), '-', ''))
)
SELECT 'candidate_attachments:' || a.id AS source_ref,
       CASE
         -- outbound media (clinic cards, packs the bot sent) are not the candidate's documents
         WHEN (CASE WHEN json_valid(a.metadata_json) THEN json_extract(a.metadata_json, '$.direction') END)
              = 'outbound' THEN 'skip'
         WHEN msg.direction = 'outbound' THEN 'skip'
         WHEN a.source_system IN ('meta_whatsapp_cloud', 'telegram_bot') THEN 'candidate'
         WHEN a.source_system IN ('owner_telegram', 'owner_telegram_pdf') THEN 'forwarded'
         WHEN a.source_system = 'manager_crm_standardized_cv' THEN 'derived_cv'
         ELSE 'skip'   -- clinic_inbound_packet, manager_crm_inbound_clinic_packet, anything new
       END AS origin,
       -- relative: under a --media-root (resolve_media_file tries both roots); absolute: standardized CVs, packets
       a.storage_path AS path, a.original_filename, a.mime_type, a.sha256, a.created_at AS sent_at,
       CASE WHEN json_valid(a.metadata_json) THEN json_extract(a.metadata_json, '$.crm_doc_class') END AS old_class,
       CASE WHEN json_valid(a.metadata_json) THEN json_extract(a.metadata_json, '$.crm_doc_type') END AS old_type,
       coalesce(CASE WHEN json_valid(a.metadata_json) THEN json_extract(a.metadata_json, '$.media_id') END,
                msg.media_id) AS media_id,
       a.source_system AS source_system
FROM candidate_attachments a
LEFT JOIN candidate_whatsapp_messages msg
       ON msg.id = (SELECT m.id FROM candidate_whatsapp_messages m
                    WHERE m.phone_e164 = :phone AND (m.attachment_id = a.id OR m.wamid = a.external_ref)
                    ORDER BY m.id LIMIT 1)
WHERE a.candidate_id IN (SELECT id FROM cand)
   OR a.id IN (SELECT attachment_id FROM candidate_whatsapp_messages WHERE phone_e164 = :phone
               AND attachment_id IS NOT NULL)
ORDER BY a.id

-- query: opt_outs
-- TASK-105: opt-outs and declines the old system keeps outside the chat text (a typed Stopp is read from the
-- messages query). Every row stops the campaign for this phone and marks our card declined (docs/whatsapp.md).
WITH meta AS (SELECT id, updated_at, CASE WHEN json_valid(metadata_json) THEN metadata_json END AS m FROM candidates),
cand AS (
  -- (candidate, the phone value that links it): messages of the number, else a metadata phone as +E.164 or digits
  SELECT DISTINCT candidate_id AS id, phone_e164 AS phone FROM candidate_whatsapp_messages
  WHERE phone_e164 = :phone AND candidate_id IS NOT NULL
  UNION ALL
  SELECT id, CASE :phone_digits
               WHEN replace(replace(replace(coalesce(json_extract(m, '$.phone'), ''), '+', ''), ' ', ''), '-', '')
                 THEN json_extract(m, '$.phone')
               WHEN replace(replace(replace(coalesce(json_extract(m, '$.primary_phone'), ''), '+', ''), ' ', ''), '-', '')
                 THEN json_extract(m, '$.primary_phone')
               ELSE json_extract(m, '$.answers.phone') END
  FROM meta WHERE :phone_digits IN (
    replace(replace(replace(coalesce(json_extract(m, '$.phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.primary_phone'), ''), '+', ''), ' ', ''), '-', ''),
    replace(replace(replace(coalesce(json_extract(m, '$.answers.phone'), ''), '+', ''), ' ', ''), '-', ''))
  AND id NOT IN (SELECT candidate_id FROM candidate_whatsapp_messages WHERE phone_e164 = :phone
                 AND candidate_id IS NOT NULL)
),
-- candidate_classification.normalize_phone: digits, a leading 49 dropped when longer than 10, one leading 0
-- dropped, the last 11 digits
digits AS (
  SELECT id, value, reason, source_system, created_at, raw_payload_json, channel_type,
         replace(replace(replace(replace(replace(replace(replace(value, '+', ''), ' ', ''), '-', ''), '(', ''), ')', ''),
                 '/', ''), '.', '') AS d,
         replace(replace(replace(replace(replace(replace(replace(
           coalesce(CASE WHEN json_valid(raw_payload_json) THEN coalesce(json_extract(raw_payload_json, '$.entity_id'),
                                                                        json_extract(raw_payload_json, '$.scope_id'))
                    END, ''), '+', ''), ' ', ''), '-', ''), '(', ''), ')', ''), '/', ''), '.', '') AS e
  FROM suppression_list
),
strip49 AS (
  SELECT *, CASE WHEN d LIKE '49%' AND length(d) > 10 THEN substr(d, 3) ELSE d END AS d1,
            CASE WHEN e LIKE '49%' AND length(e) > 10 THEN substr(e, 3) ELSE e END AS e1,
            CASE WHEN :phone_digits LIKE '49%' AND length(:phone_digits) > 10 THEN substr(:phone_digits, 3)
                 ELSE :phone_digits END AS p1
  FROM digits
),
normalized AS (
  SELECT *, substr(CASE WHEN d1 LIKE '0%' THEN substr(d1, 2) ELSE d1 END, -11) AS dn,
            substr(CASE WHEN e1 LIKE '0%' THEN substr(e1, 2) ELSE e1 END, -11) AS en,
            substr(CASE WHEN p1 LIKE '0%' THEN substr(p1, 2) ELSE p1 END, -11) AS pn
  FROM strip49
)
-- candidate_lifecycle.persist_candidate_lifecycle: metadata.lifecycle {status closed, reason declined_opt_out =
-- "candidate declined / stop / polite close already sent", closed_at, reopen_after}; reopening writes status open,
-- and _meta_lifecycle_state reads a closed block whose reopen_after has passed as open. Other closed reasons
-- (employed_elsewhere, no_recognition_path, hired_or_placed, test_candidate, explicit_closed_flag) are not read.
SELECT 'candidates:' || c.id || ':lifecycle' AS source_ref, 'opt_out' AS kind,
       coalesce(json_extract(c.m, '$.lifecycle.closed_at'), json_extract(c.m, '$.lifecycle.updated_at'), c.updated_at)
         AS at,
       'lifecycle closed ' || coalesce(json_extract(c.m, '$.lifecycle.reason'), json_extract(c.m, '$.lifecycle_reason'))
         || coalesce(' (source ' || json_extract(c.m, '$.lifecycle.source') || ')', '')
         || coalesce('; evidence ' || json_extract(c.m, '$.lifecycle.evidence'), '') AS reason,
       k.phone AS phone
FROM meta c JOIN cand k ON k.id = c.id
WHERE lower(coalesce(json_extract(c.m, '$.lifecycle.status'), json_extract(c.m, '$.lifecycle_status'), '')) = 'closed'
  AND coalesce(json_extract(c.m, '$.lifecycle.reason'), json_extract(c.m, '$.lifecycle_reason')) = 'declined_opt_out'
  AND NOT coalesce(julianday(json_extract(c.m, '$.lifecycle.reopen_after')) <= julianday('now'), 0)
UNION ALL
-- candidate_bayern_housing_offer.apply_offer_reply_to_meta: the No button on the Job+Wohnung template writes
-- metadata.bayern_housing_offer {status declined, declined true, replied_at}; a later yes sets status interested.
SELECT 'candidates:' || c.id || ':bayern_housing_offer', 'decline',
       coalesce(json_extract(c.m, '$.bayern_housing_offer.replied_at'), c.updated_at),
       'Job+Wohnung template answered No (' ||
         coalesce(json_extract(c.m, '$.bayern_housing_offer.template_name'), 'no template name') || ')',
       k.phone
FROM meta c JOIN cand k ON k.id = c.id
WHERE json_extract(c.m, '$.bayern_housing_offer.status') = 'declined'
UNION ALL
-- candidate_job_wohnung_outreach.mark_outreach_declined: the blast table (no candidate card) keeps status
-- declined for good once the phone answered No; phone_key = digits of the canonical E.164 number.
SELECT 'job_wohnung_outreach:' || o.id, 'decline', coalesce(o.replied_at, o.updated_at),
       'job_wohnung_outreach declined (' || coalesce(o.template_name, 'no template name') || ')', o.phone_e164
FROM job_wohnung_outreach o
WHERE o.status = 'declined' AND (o.phone_key = :phone_digits OR o.phone_e164 = :phone)
UNION ALL
-- sales_brain/placement: a manager call with outcome not_interested derives placement_stage withdrawn
-- (stages.MANAGER_CALL_WITHDRAWN_OUTCOMES).
SELECT 'candidate_recruitment_state:' || rs.id || ':withdrawn', 'decline', rs.updated_at,
       'placement stage withdrawn (next action ' || coalesce(rs.next_action, 'none') || ')', k.phone
FROM candidate_recruitment_state rs JOIN cand k ON k.id = rs.candidate_id
WHERE rs.placement_stage = 'withdrawn'
UNION ALL
-- candidate_suppression.find_phone_suppression_matches: a suppression_list row blocks the phone channel when
-- payload channels is phone/all or channel_type is phone/all/*/empty, matched on normalize_phone of value or of
-- payload entity_id/scope_id. Every workspace and scope is read (the old check reads the candidate workspace
-- plus global rows): a do-not-contact on this number anywhere in the old system counts.
SELECT 'suppression_list:' || n.id, 'opt_out', n.created_at,
       'suppression ' || n.reason || coalesce(' (' || n.source_system || ')', ''), n.value
FROM normalized n
WHERE (lower(trim(coalesce(CASE WHEN json_valid(n.raw_payload_json) THEN json_extract(n.raw_payload_json, '$.channels')
                                END, ''))) IN ('phone', 'all')
       OR lower(trim(coalesce(n.channel_type, ''))) IN ('phone', 'all', '*', ''))
  AND n.pn <> '' AND (n.dn = n.pn OR n.en = n.pn)
