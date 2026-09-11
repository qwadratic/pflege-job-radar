#!/usr/bin/env python3
"""Query pflege_jobs.v_postings (hospital career-site postings only) with simple flags; handles PostgREST paging.
Read-only, no secrets. For fuzzy search, CV matching or triggering crawls use the app API (https://pflege-board.exe.xyz/api).
No personal data: enr_contact_emails is not selected and there is no --email filter. Recruiter addresses are
member-and-up and only the app API redacts them (app/data.py:37) -- the anon key this script carries reads
them un-redacted off PostgREST, which is the hole sql/011_PENDING_anon_scope.sql exists to close. Ask the
app API with a session (GET /api/jobs) instead of taking them out of here.

  python query.py --emp clinic --role fachpflege --dept "Intensiv/IMC" --housing --format md
  python query.py --q nürnberg --days 14 --format csv > out.csv
  python query.py --stats
"""
import argparse, csv, json, os, sys, urllib.parse, urllib.request

URL = os.environ.get("SUPABASE_URL", "https://klkxfvieaxpjlplloljn.supabase.co")
KEY = os.environ.get("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtsa3hmdmllYXhwamxwbGxvbGpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM5MjEwOTgsImV4cCI6MjA4OTQ5NzA5OH0.S0ED1qBUyRDP0YSDVBQ0s_L5_tKdu4jsPsLmyUo1YCk")
COLS = "posting_id,title,role_class,department_hint,department_raw,qualification_hint,employer,employer_class,clinic_id,clinic_name,regierungsbezirk,versorgungsstufe,traegerart,city,plz,lat,lon,employment_types,contract,first_published,last_seen,status,verify_status,verified_at,enr_housing,enr_tariff,enr_pay_grade,source_codes,source_url,external_url"

def get(rel, params):
    q = urllib.parse.urlencode(params, safe="*.,()/{}:")
    req = urllib.request.Request(f"{URL}/rest/v1/{rel}?{q}", headers={"apikey": KEY, "Accept-Profile": "pflege_jobs"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--emp", default="clinic", help="clinic|unknown|non_clinic|all")
    p.add_argument("--role", help="comma list of role_class")
    p.add_argument("--dept"); p.add_argument("--q", help="ilike on title/employer/city")
    p.add_argument("--city"); p.add_argument("--bezirk"); p.add_argument("--vst"); p.add_argument("--traeger"); p.add_argument("--tariff"); p.add_argument("--contract")
    p.add_argument("--housing", action="store_true")
    p.add_argument("--vollzeit", action="store_true"); p.add_argument("--teilzeit", action="store_true")
    p.add_argument("--days", type=int); p.add_argument("--all-roles", action="store_true", help="no is_pflege filter (the DB is experienced-nursing-only anyway)")
    p.add_argument("--include-expired", action="store_true"); p.add_argument("--include-unverified", action="store_true", help="also rows whose web check is blocked/error"); p.add_argument("--limit", type=int, default=100000)
    p.add_argument("--format", default="json", choices=["json", "csv", "md", "count"]); p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        print(json.dumps(get("v_stats", {"order": "n.desc"}), ensure_ascii=False, indent=1)); return
    f = {"select": COLS, "order": "first_published.desc"}
    if a.emp != "all": f["employer_class"] = f"eq.{a.emp}"
    if a.role: f["role_class"] = f"in.({a.role})"
    if not a.all_roles: f["is_pflege"] = "eq.true"
    if not a.include_expired: f["status"] = "eq.open"
    if not a.include_unverified and not a.include_expired: f["verify_status"] = "eq.live"
    if a.dept: f["department_hint"] = f"eq.{a.dept}"
    if a.city: f["city"] = f"ilike.*{a.city}*"
    if a.bezirk: f["regierungsbezirk"] = f"eq.{a.bezirk}"
    if a.vst: f["versorgungsstufe"] = f"eq.{a.vst}"
    if a.traeger: f["traegerart"] = f"eq.{a.traeger}"
    if a.tariff: f["enr_tariff"] = f"eq.{a.tariff}"
    if a.contract: f["contract"] = f"eq.{a.contract}"
    if a.housing: f["enr_housing"] = "is.true"
    if a.vollzeit: f["employment_types"] = "cs.{vollzeit}"
    if a.teilzeit: f["employment_types"] = "cs.{teilzeit}"
    if a.days:
        import datetime; f["first_published"] = "gte." + (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    if a.q: f["or"] = f"(title.ilike.*{a.q}*,employer.ilike.*{a.q}*,city.ilike.*{a.q}*)"
    rows, off = [], 0
    while len(rows) < a.limit:
        chunk = get("v_postings", {**f, "limit": min(1000, a.limit - len(rows)), "offset": off})
        rows += chunk; off += len(chunk)
        if len(chunk) < 1000: break
    if a.format == "count": print(len(rows)); return
    if a.format == "json": print(json.dumps(rows, ensure_ascii=False, indent=1)); return
    cols = COLS.split(",")
    if a.format == "csv":
        w = csv.DictWriter(sys.stdout, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow({k: ("|".join(v) if isinstance(v, list) else v) for k, v in r.items()})
        return
    print(f"| # | Stelle | Arbeitgeber | Ort | Rolle | Veröff. | Merkmale | Link |\n|---|---|---|---|---|---|---|---|")
    for r in rows:
        tags = [t for t, ok in (("✓web", r["verify_status"] == "live"), ("Wohnraum", r["enr_housing"]), (r["enr_pay_grade"], r["enr_pay_grade"]), (r["enr_tariff"], r["enr_tariff"])) if ok]
        print(f"| {r['posting_id']} | {r['title']} | {r['employer']} | {r['city']} | {r['role_class']} | {r['first_published']} | {', '.join(tags)} | {r['source_url']} |")

if __name__ == "__main__":
    main()
