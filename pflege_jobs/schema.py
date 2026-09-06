"""Single source of truth for the observation column spec.
Python sinks import OBS_COLUMNS; edge/build_ingest.py renders the same spec into the Deno function.
Order matters (insert order). Keep in sync with sql/001_schema.sql + migrations."""
OBS_SPEC = [
    ("source_id", "smallint"), ("source_ref", "text"), ("source_url", "text"), ("observed_at", "timestamptz"), ("title", "text"),
    ("employer_name", "text"), ("employer_name_norm", "text"), ("employer_class", "text"), ("employer_class_rule", "text"),
    ("aa_kundennummer_hash", "text"), ("offer_kind", "text"), ("hauptberuf", "text"), ("alle_berufe", "text[]"),
    ("role_class", "text"), ("role_rule", "text"), ("qualification_hint", "text"), ("department_hint", "text"),
    ("city", "text"), ("plz", "text"), ("region", "text"), ("lat", "double precision"), ("lon", "double precision"),
    ("in_bavaria", "boolean"), ("n_locations", "int"), ("locations", "jsonb"),
    ("employment_types", "text[]"), ("shift_night_weekend", "boolean"), ("homeoffice", "boolean"), ("quereinstieg", "boolean"),
    ("contract", "text"), ("fixed_term_months", "int"), ("start_date", "date"),
    ("salary_min", "numeric"), ("salary_max", "numeric"), ("salary_unit", "text"), ("salary_note", "text"),
    ("first_published", "date"), ("last_modified", "timestamptz"), ("valid_until", "date"),
    ("external_url", "text"), ("description", "text"), ("department_raw", "text"),
    ("enr_pay_grade", "text"), ("enr_pay_text", "text"), ("enr_requirements", "text"), ("enr_experience", "text"),
    ("enr_housing", "boolean"), ("enr_housing_evidence", "text"), ("enr_tariff", "text"), ("enr_contact_emails", "text[]"),
    ("enr_language_req", "text"), ("enr_bonus", "boolean"), ("enr_childcare", "boolean"), ("enr_anerkennung_mentioned", "boolean"),
    ("details_fetched_at", "timestamptz"), ("details_error", "text"), ("fuzzy_key", "text"), ("content_hash", "text"), ("payload", "jsonb"),
]
OBS_COLUMNS = [c for c, _ in OBS_SPEC]
ARRAY_COLUMNS = {c for c, t in OBS_SPEC if t.endswith("[]")}
JSON_COLUMNS = {c for c, t in OBS_SPEC if t == "jsonb"}
IDENTITY = ("source_id", "source_ref")
EMP_SPEC = [("name_norm", "text"), ("name_display", "text"), ("employer_class", "text"), ("class_rule", "text"), ("aa_kundennummer_hashes", "text[]")]
CLINIC_SPEC = [("clinic_id", "text"), ("name", "text"), ("town", "text"), ("operator", "text"), ("landkreis", "text"), ("regierungsbezirk", "text"),
    ("status", "text"), ("versorgungsstufe", "text"), ("traegerart", "text"), ("beds", "int"), ("day_places", "int"), ("fachrichtungen", "text"),
    ("parse_quality", "text"), ("source", "text"), ("website", "text"), ("careers_url", "text"), ("ats_type", "text")]
LINK_SPEC = [("posting_id", "bigint"), ("clinic_id", "text"), ("clinic_match_rule", "text"), ("clinic_match_score", "numeric")]
VERIFY_SPEC = [("posting_id", "bigint"), ("verify_status", "text"), ("verify_http", "int"), ("verified_at", "timestamptz"), ("verify_note", "text")]

def pg_record_def(spec): return ", ".join(f"{c} {t}" for c, t in spec)
