"""Publish built dashboard + skill bundle into pflege_jobs.assets via the ingest function (no SQL needed).
Usage: set -a; . ./.env; set +a; python web/publish.py"""
import json, os, pathlib, requests
root = pathlib.Path(__file__).resolve().parent.parent
assets = [{"key": "dashboard", "content": (root / "web" / "pro.html").read_text(encoding="utf-8"), "content_type": "text/html; charset=utf-8"}]
sk = root / "web" / "skill" / "pflege-jobs.skill.md"
if sk.exists():
    assets.append({"key": "skill", "content": sk.read_text(encoding="utf-8"), "content_type": "text/markdown; charset=utf-8"})
r = requests.post(os.environ["PFLEGE_INGEST_URL"], json={"assets": assets}, timeout=120,
                  headers={"Authorization": f"Bearer {os.environ['SUPABASE_ANON_KEY']}", "apikey": os.environ["SUPABASE_ANON_KEY"], "x-ingest-secret": os.environ["PFLEGE_INGEST_SECRET"]})
print(r.status_code, r.text[:200])
