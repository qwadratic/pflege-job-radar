"""Paths, env and PostgREST helpers shared by the app modules."""
import os
import pathlib
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WEB_DIR = ROOT / "web"
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
CRAWL_OUT = ROOT / "crawl_output"
SQLITE_PATH = DATA_DIR / "app.sqlite"
PATTERNS_PATH = pathlib.Path(os.environ.get("PFLEGE_PATTERNS", ROOT / "pflege_jobs" / "patterns.json"))
TAXONOMY_PATH = DATA_DIR / "registry" / "taxonomy.json"
CLINICS_CSV = DATA_DIR / "registry" / "clinics.csv"
FALLBACK_DIR = ROOT / "app" / "fallback"
VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY if VENV_PY.exists() else sys.executable)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://klkxfvieaxpjlplloljn.supabase.co").rstrip("/")
ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY") or ANON_KEY
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4-mini")
FRESH_DAYS = 7


def rest_headers(write=False):
    h = {"apikey": SECRET_KEY, "Authorization": "Bearer " + SECRET_KEY, "Accept-Profile": "pflege_jobs"}
    if write:
        h["Content-Profile"] = "pflege_jobs"
        h["Content-Type"] = "application/json"
    return h


def rest_get(path, params=None, timeout=120, retries=2):
    """GET /rest/v1/<path>; raises on PostgREST error objects. Retries 5xx once or twice: v_postings
    computes source_codes per row, and a page occasionally trips the statement timeout."""
    for i in range(retries + 1):
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", params=params, headers=rest_headers(), timeout=timeout)
        if r.status_code < 500 or i == retries:
            break
        time.sleep(2 * (i + 1))
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict) and d.get("message"):
        raise RuntimeError(f"PostgREST: {d.get('message')}")
    return d


def rest_get_all(path, params=None, page=1000, timeout=120):
    """Page through a relation (PostgREST caps at 1000 rows per call)."""
    out, off = [], 0
    params = dict(params or {})
    while True:
        params.update({"limit": page, "offset": off})
        ch = rest_get(path, params, timeout)
        out += ch
        off += len(ch)
        if len(ch) < page:
            return out


def rest_post(path, body, prefer="return=minimal", timeout=120):
    h = rest_headers(write=True)
    h["Prefer"] = prefer
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{path}", json=body, headers=h, timeout=timeout)
    if r.status_code >= 300:
        raise RuntimeError(f"PostgREST {r.status_code}: {r.text[:300]}")
    return r
