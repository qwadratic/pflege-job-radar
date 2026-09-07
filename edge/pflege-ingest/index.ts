// pflege-ingest: authenticated bulk upsert into pflege_jobs.* over the project's own DB connection.
// RENDERED from index.template.ts by edge/build_ingest.py — column lists come from pflege_jobs/schema.py.
// Auth: Supabase JWT (verify_jwt=true; the anon key is a valid JWT) AND x-ingest-secret header.
// Body: { employers?, observations?, verify?, clinics?, clinic_links?, merges?, inbox_ack?, inbox_purge?, resolve?, supersede_aa?, expire_days?, assets?, crawl_run? }
import postgres from "npm:postgres@3.4.5";

// Set PFLEGE_INGEST_SECRET as a function secret; rotate it there.
// No literal fallback: a missing secret must fail closed and loudly, not silently accept writes
// with a value that is now public in git history. Set PFLEGE_INGEST_SECRET on the function.
const SECRET = Deno.env.get("PFLEGE_INGEST_SECRET");
if (!SECRET) console.error("PFLEGE_INGEST_SECRET is not set — every write will be rejected");
const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 2, idle_timeout: 20, prepare: false });

const OBS_COLS_DEF = `source_id smallint, source_ref text, source_url text, observed_at timestamptz, title text, employer_name text, employer_name_norm text, employer_class text, employer_class_rule text, aa_kundennummer_hash text, offer_kind text, hauptberuf text, alle_berufe text[], role_class text, role_rule text, qualification_hint text, department_hint text, city text, plz text, region text, lat double precision, lon double precision, in_bavaria boolean, n_locations int, locations jsonb, employment_types text[], shift_night_weekend boolean, homeoffice boolean, quereinstieg boolean, contract text, fixed_term_months int, start_date date, salary_min numeric, salary_max numeric, salary_unit text, salary_note text, first_published date, last_modified timestamptz, valid_until date, external_url text, description text, department_raw text, enr_pay_grade text, enr_pay_text text, enr_requirements text, enr_experience text, enr_housing boolean, enr_housing_evidence text, enr_tariff text, enr_contact_emails text[], enr_language_req text, enr_bonus boolean, enr_childcare boolean, enr_anerkennung_mentioned boolean, details_fetched_at timestamptz, details_error text, fuzzy_key text, content_hash text, payload jsonb`;
const EMP_COLS_DEF = `name_norm text, name_display text, employer_class text, class_rule text, aa_kundennummer_hashes text[]`;
const VERIFY_COLS_DEF = `posting_id bigint, verify_status text, verify_http int, verified_at timestamptz, verify_note text`;
const CLINIC_COLS_DEF = `clinic_id text, name text, town text, operator text, landkreis text, regierungsbezirk text, status text, versorgungsstufe text, traegerart text, beds int, day_places int, fachrichtungen text, parse_quality text, source text, website text, careers_url text, ats_type text`;
const LINK_COLS_DEF = `posting_id bigint, clinic_id text, clinic_match_rule text, clinic_match_score numeric`;
const CLINIC_COLS = CLINIC_COLS_DEF.split(",").map((c) => c.trim().split(/\s+/)[0]);
const IDENTITY = "source_id,source_ref";
const OBS_COLS = OBS_COLS_DEF.split(",").map((c) => c.trim().split(/\s+/)[0]);
const OBS_SET = OBS_COLS.filter((c) => !IDENTITY.split(",").includes(c)).map((c) => `${c}=excluded.${c}`).join(", ");
const MAX_ROWS = 500;

function parseJson(rows: any[], keys: string[]) {
  for (const r of rows) for (const k of keys) if (typeof r[k] === "string") { try { r[k] = JSON.parse(r[k]); } catch { /* keep as text */ } }
  return rows;
}
const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } });

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return json({ ok: false, error: "POST only" }, 405);
  if (req.headers.get("x-ingest-secret") !== SECRET) return json({ ok: false, error: "forbidden" }, 403);
  const out: Record<string, unknown> = {};
  try {
    const body = await req.json();
    for (const k of ["employers", "observations", "verify", "assets", "clinics", "clinic_links", "merges", "inbox_ack"]) {
      if (Array.isArray(body[k]) && body[k].length > MAX_ROWS) return json({ ok: false, error: `${k}: max ${MAX_ROWS} rows per call` }, 413);
    }
    if (body.employers?.length) {
      const r = await sql.unsafe(
        `insert into pflege_jobs.employers (name_norm,name_display,employer_class,class_rule,aa_kundennummer_hashes)
         select name_norm,name_display,employer_class,class_rule,coalesce(aa_kundennummer_hashes,'{}') from json_to_recordset($1::json) as t(${EMP_COLS_DEF})
         on conflict (name_norm) do update set name_display=excluded.name_display,
           employer_class=case when pflege_jobs.employers.class_source='keyword_rule' then excluded.employer_class else pflege_jobs.employers.employer_class end,
           class_rule=case when pflege_jobs.employers.class_source='keyword_rule' then excluded.class_rule else pflege_jobs.employers.class_rule end,
           aa_kundennummer_hashes=(select coalesce(array_agg(distinct x),'{}') from unnest(pflege_jobs.employers.aa_kundennummer_hashes || excluded.aa_kundennummer_hashes) x),
           last_seen=now()`, [sql.json(body.employers)]);
      out.employers = r.count;
    }
    if (body.observations?.length) {
      const rows = parseJson(body.observations, ["payload", "locations"]);
      const r = await sql.unsafe(
        `insert into pflege_jobs.posting_observations (${OBS_COLS.join(",")})
         select ${OBS_COLS.join(",")} from json_to_recordset($1::json) as t(${OBS_COLS_DEF})
         on conflict (${IDENTITY}) do update set ${OBS_SET}`, [sql.json(rows)]);
      out.observations = r.count;
    }
    if (body.verify?.length) {
      // web-liveness results: live | gone | blocked | error. 'gone' also expires the posting.
      const r = await sql.unsafe(
        `update pflege_jobs.postings p set verify_status=t.verify_status, verify_http=t.verify_http, verified_at=coalesce(t.verified_at, now()),
                verify_note=t.verify_note, status=case when t.verify_status='gone' then 'expired' when t.verify_status='live' then 'open' else p.status end, updated_at=now()
           from json_to_recordset($1::json) as t(${VERIFY_COLS_DEF}) where p.posting_id=t.posting_id`, [sql.json(body.verify)]);
      out.verify = r.count;
    }
    if (body.clinics?.length) {
      const r = await sql.unsafe(
        // ats_type / careers_url are discovered asynchronously (crawlers/ats_discover2.py) while the
        // registry CSV that supplies the other columns still has them blank. A plain
        // `col = excluded.col` therefore erased every discovered label on the next link-clinics run.
        // Discovery-owned columns keep the stored value unless the caller actually sends a new one.
        `insert into pflege_jobs.clinics (${CLINIC_COLS.join(",")}) select ${CLINIC_COLS.join(",")} from json_to_recordset($1::json) as t(${CLINIC_COLS_DEF})
         on conflict (clinic_id) do update set ${CLINIC_COLS.filter((c) => c !== "clinic_id").map((c) =>
            ["ats_type", "careers_url"].includes(c)
              ? `${c}=coalesce(nullif(excluded.${c},''), pflege_jobs.clinics.${c})`
              : `${c}=excluded.${c}`).join(", ")}`, [sql.json(body.clinics)]);
      out.clinics = r.count;
    }
    if (body.clinic_links?.length) {
      const r = await sql.unsafe(
        `update pflege_jobs.postings p set clinic_id=t.clinic_id, clinic_match_rule=t.clinic_match_rule, clinic_match_score=t.clinic_match_score, updated_at=now()
           from json_to_recordset($1::json) as t(${LINK_COLS_DEF}) where p.posting_id=t.posting_id`, [sql.json(body.clinic_links)]);
      out.clinic_links = r.count;
    }
    if (body.merges?.length) {
      // cross-source dedupe: move observations of src posting into dst, keep earliest first_seen, delete src. Idempotent.
      const r = await sql.unsafe(
        `with m as (select src, dst from json_to_recordset($1::json) as t(src bigint, dst bigint) where src<>dst
                     and exists (select 1 from pflege_jobs.postings where posting_id=src) and exists (select 1 from pflege_jobs.postings where posting_id=dst)),
              mv as (update pflege_jobs.posting_observations o set posting_id=m.dst from m where o.posting_id=m.src returning m.src),
              fs as (update pflege_jobs.postings d set first_seen=least(d.first_seen, s.first_seen), clinic_id=coalesce(d.clinic_id, s.clinic_id) from m join pflege_jobs.postings s on s.posting_id=m.src where d.posting_id=m.dst returning 1),
              del as (delete from pflege_jobs.postings p where p.posting_id in (select src from m) returning 1)
         select (select count(*) from del) as merged`, [sql.json(body.merges)]);
      out.merges = r[0]?.merged;
    }
    if (body.inbox_ack?.length) {
      const r = await sql.unsafe(`update pflege_jobs.inbox i set processed_at=now(), process_note=t.note from json_to_recordset($1::json) as t(inbox_id bigint, note text) where i.inbox_id=t.inbox_id`, [sql.json(body.inbox_ack)]);
      out.inbox_ack = r.count;
    }
    if (body.inbox_purge) {
      // Guards live HERE, in the SQL, not in whatever the caller sends: processed_at is not null
      // (never touch a row still queued for cmd_inbox) and process_note not like 'ats set:%' (the
      // ats-discover2-v1 probes are the only record of how each clinics.ats_type/careers_url was
      // set -- never delete them, even if a caller's kinds/collectors filter would otherwise match).
      // before/kinds/collectors/notes_like narrow the candidate set further; all optional. max_rows
      // is a hard cap, clamped server-side -- a caller cannot raise it past 1000.
      const p = body.inbox_purge;
      const maxRows = Math.max(1, Math.min(Number(p.max_rows) || 1000, 1000));
      const r = await sql.unsafe(
        `with victims as (
           select inbox_id from pflege_jobs.inbox
            where processed_at is not null
              and process_note not like 'ats set:%'
              and ($1::timestamptz is null or received_at < $1::timestamptz)
              and ($2::text[] is null or kind = any($2::text[]))
              and ($3::text[] is null or collector = any($3::text[]))
              and ($4::text is null or process_note ilike $4::text)
            order by inbox_id
            limit $5::int
         ), deleted as (delete from pflege_jobs.inbox where inbox_id in (select inbox_id from victims) returning 1)
         select count(*)::int as n from deleted`,
        [p.before ?? null, p.kinds ?? null, p.collectors ?? null, p.notes_like ?? null, maxRows]);
      out.inbox_purge = r[0]?.n ?? 0;
    }
    if (body.resolve) {
      await sql`update pflege_jobs.postings p set fuzzy_key = o.fuzzy_key from pflege_jobs.posting_observations o
                where o.posting_id = p.posting_id and o.fuzzy_key is not null and o.fuzzy_key <> p.fuzzy_key`;
      const r = await sql`select * from pflege_jobs.resolve_postings()`;
      out.resolve = r[0];
    }
    if (body.supersede_aa) {
      const r = await sql`select pflege_jobs.supersede_aa_at_covered_sites() as n`;
      out.superseded_aa = r[0].n;
    }
    if (body.expire_days) {
      const r = await sql`select pflege_jobs.mark_expired(${body.expire_days}::int) as n`;
      out.expired = r[0].n;
    }
    if (body.assets?.length) {
      const r = await sql.unsafe(
        `insert into pflege_jobs.assets (key, content, content_type)
         select key, content, coalesce(content_type,'text/html; charset=utf-8') from json_to_recordset($1::json) as t(key text, content text, content_type text)
         on conflict (key) do update set content=excluded.content, content_type=excluded.content_type, updated_at=now()`, [sql.json(body.assets)]);
      out.assets = r.count;
    }
    if (body.crawl_run) {
      const c = body.crawl_run;
      const r = await sql`insert into pflege_jobs.crawl_runs (source_id, started_at, finished_at, n_fetched, n_new, n_updated, n_expired, slice_counts, notes)
        values (${c.source_id}, ${c.started_at ?? new Date().toISOString()}, ${c.finished_at ?? new Date().toISOString()}, ${c.n_fetched ?? null}, ${c.n_new ?? null}, ${c.n_updated ?? null}, ${c.n_expired ?? null}, ${sql.json(c.slice_counts ?? {})}, ${c.notes ?? null}) returning run_id`;
      out.run_id = r[0].run_id;
    }
    return json({ ok: true, ...out });
  } catch (e) {
    return json({ ok: false, error: String((e as Error)?.message ?? e) }, 500);
  }
});
