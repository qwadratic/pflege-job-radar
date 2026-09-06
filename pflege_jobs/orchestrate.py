"""Long-running pipeline orchestrator. Idempotent stages, per-stage timeouts, checkpoints, crawl_runs logging.

  python -m pflege_jobs.orchestrate --stages all            # daily job (~40 min)
  python -m pflege_jobs.orchestrate --stages aa,verify      # subset
  python -m pflege_jobs.orchestrate --list

Stages (in order):
  aa          Arbeitsagentur pull + clinic details + load + resolve
  ats         B-ITE, softgarden, rexx, d.vinci, mein-check-in, group portals (requests-based; resumable checkpoints)
  browser     P&I (Helios), Playwright seeds (js_seeds)       [needs chromium]
  inbox       browser-collector / egress rows -> observations
  egress      StepStone via Claude web_fetch                  [needs ANTHROPIC_API_KEY; capped by EGRESS_MAX_PAGES]
  link        registry links (link-clinics) + cross-source merge (link-cross)
  verify      web-liveness of open postings (<=6 workers), then AA-supersede at covered sites, then expire(7)
  publish     refresh dashboard assets (skill/html) if changed
Every stage writes a crawl_runs row (source_id 30/20/40 or null) with counts and notes; failures don't stop later stages.
"""
import argparse, json, os, subprocess, sys, time, traceback
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGES = ["aa", "ats", "browser", "inbox", "egress", "link", "verify", "publish"]


def sh(cmd, timeout, env=None):
    """Run a subprocess with timeout; return (rc, tail of output)."""
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
        out = (p.stdout + p.stderr)[-4000:]
        return p.returncode, out, round(time.time() - t)
    except subprocess.TimeoutExpired as e:
        return 124, f"timeout after {timeout}s\n" + ((e.stdout or "") + (e.stderr or ""))[-2000:] if isinstance(e.stdout, str) else "timeout", round(time.time() - t)


def log_run(source_id, notes, counts=None):
    try:
        from .sinks import EdgeSink
        EdgeSink()._post({"crawl_run": {"source_id": source_id, "notes": notes[:2000], "slice_counts": counts or {}}})
    except Exception as e:
        print("crawl_run log failed:", e)


def stage_aa(a):
    rc, out, s = sh("python -m pflege_jobs.cli run --sink edge --details clinic --inp data/raw.json", 1500)
    log_run(30, f"aa: rc={rc} {s}s\n{out[-800:]}"); return rc


def stage_ats(a):
    cmds = ["python data/run_bite.py", "python data/run_softgarden.py 100", "python data/run_ats.py rexx 120", "python data/run_ats.py dvinci 130",
            "python data/run_ats.py mein-check-in 100", "SEEDS=data/registry/extra_seeds.json python data/run_crawl.py 0 9 150",
            "python data/run_crawl.py 0 18 100", "python data/run_feeds.py",
            "SEEDS=data/registry/helix_seeds.json python data/run_crawl.py 0 3 60", "SEEDS=data/registry/misc_seeds.json python data/run_crawl.py 0 2 80"]
    rcs = []
    for c in cmds:
        rc, out, s = sh(c, 1500); rcs.append(rc); print(f"[ats] {c[:60]} rc={rc} {s}s")
    rc, out, s = sh("python data/load_all_crawls.py", 900)
    log_run(20, f"ats: rcs={rcs} load rc={rc}\n{out[-800:]}"); return max(rcs + [rc])


def stage_browser(a):
    rc1, o1, s1 = sh("python data/run_pi_all.py", 1500)
    rc2, o2, s2 = sh("python data/run_browser_crawl.py \"Kliniken Nordoberpfalz|LA-REGIO Kliniken (Klinikum Landshut)|Klinikum St. Marien Amberg|Klinikum Aschaffenburg-Alzenau|München Klinik\" 60", 1500)
    rc3, o3, s3 = sh("python data/load_all_crawls.py --only browser", 600)
    log_run(20, f"browser: pi rc={rc1} js rc={rc2} load rc={rc3}\n{o3[-600:]}"); return max(rc1, rc2, rc3)


def stage_inbox(a):
    rc, out, s = sh("python -m pflege_jobs.cli inbox", 900); log_run(20, f"inbox: rc={rc}\n{out[-600:]}"); return rc


def stage_egress(a):
    rc, out, s = sh("EGRESS_DISCOVER=0 python crawlers/claude_egress.py stepstone-matrix", 3600)
    rc2, out2, s2 = sh("python crawlers/load_crawl_output.py", 900) if os.environ.get("SUPABASE_ANON_KEY") else (0, "skipped: no SUPABASE_ANON_KEY", 0)
    log_run(40, f"egress stepstone-matrix: rc={rc} load rc={rc2}\n{out[-500:]}"); return max(rc, rc2)


def stage_link(a):
    rc1, o1, s1 = sh("python -m pflege_jobs.cli link-clinics", 900)
    rc2, o2, s2 = sh("python -m pflege_jobs.cli link-cross", 900)
    log_run(None, f"link: clinics rc={rc1} cross rc={rc2}\n{o1[-300:]}\n{o2[-300:]}"); return max(rc1, rc2)


def stage_verify(a):
    rc, out, s = sh("python -m pflege_jobs.cli verify --workers 5", 2400)
    try:
        from .sinks import EdgeSink
        sink = EdgeSink()
        import requests as rq
        # supersede + expire via SQL functions exposed through the ingest function's expire op + a tiny RPC-less path:
        r = sink._post({"supersede_aa": True, "expire_days": 7}); out += f"\nsuperseded_aa: {r.get('superseded_aa')} expired: {r.get('expired')}"
    except Exception as e:
        out += f"\nexpire failed: {e}"
    log_run(None, f"verify: rc={rc} {s}s\n{out[-600:]}"); return rc


def stage_publish(a):
    rc, out, s = sh("python web/build.py && python web/publish.py", 300); log_run(None, f"publish rc={rc}\n{out[-300:]}"); return rc


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--stages", default="all"); ap.add_argument("--list", action="store_true"); a = ap.parse_args()
    if a.list: print("\n".join(STAGES)); return
    wanted = STAGES if a.stages == "all" else [s for s in a.stages.split(",") if s in STAGES]
    started = datetime.now(timezone.utc).isoformat(); results = {}
    for st in wanted:
        print(f"\n===== stage {st} @ {datetime.now(timezone.utc).isoformat()}")
        try: results[st] = globals()[f"stage_{st}"](a)
        except Exception:
            traceback.print_exc(); results[st] = 1
    log_run(None, f"orchestrate {started}: {json.dumps(results)}", results)
    print("\nRESULT", results); sys.exit(0 if all(v == 0 for v in results.values()) else 1)


if __name__ == "__main__":
    main()
