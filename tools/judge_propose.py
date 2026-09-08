"""Push a judge branch and propose it as a merge request.

  python tools/judge_propose.py --branch judge/2026-09-08-fix-x --title "Fix x" --body-file body.md [--dry-run]

Order of preference: `gh pr create` when gh is authenticated; otherwise push the branch, write
.judge/mr/<branch>.md (title, body, compare link) and e-mail the owner through the VM mail gateway
(JUDGE_EMAIL, default the repo owner's address). Never touches main. Exit 0 on any successful path.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_WEB = "https://github.com/qwadratic/pflege-job-radar"
OWNER = os.environ.get("JUDGE_EMAIL", "ivan.d.kotelnikov@gmail.com")
GATEWAY = "http://169.254.169.254/gateway/email/send"


def sh(*cmd, check=True, cwd=None):
    r = subprocess.run(cmd, cwd=cwd or ROOT, text=True, capture_output=True)
    if check and r.returncode:
        raise SystemExit(f"{' '.join(cmd)} failed: {r.stderr.strip()[:400]}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.branch in ("main", "master") or not a.branch.startswith("judge/"):
        raise SystemExit("branch must start with judge/")
    body = pathlib.Path(a.body_file).read_text(encoding="utf-8") if pathlib.Path(a.body_file).exists() else a.body_file
    cwd = os.getcwd()                                            # the caller's worktree
    cur = sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).stdout.strip()
    if cur != a.branch:
        raise SystemExit(f"checked out {cur!r}, expected {a.branch!r}")
    if a.dry_run:
        print(json.dumps({"status": "skipped", "dry_run": True, "branch": a.branch}))
        return
    sh("git", "push", "-u", "origin", a.branch, cwd=cwd)
    compare = f"{REPO_WEB}/compare/main...{a.branch}?expand=1"
    gh_ok = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    if gh_ok:
        r = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", a.branch, "--title", a.title, "--body", body],
                           cwd=cwd, text=True, capture_output=True)
        if r.returncode == 0:
            print(json.dumps({"status": "pr_opened", "branch": a.branch, "url": r.stdout.strip()}))
            return
    mr_dir = ROOT / ".judge" / "mr"
    mr_dir.mkdir(parents=True, exist_ok=True)
    md = mr_dir / (a.branch.replace("/", "__") + ".md")
    md.write_text(f"# {a.title}\n\nbranch: `{a.branch}`\ncompare: {compare}\n\n{body}\n", encoding="utf-8")
    try:
        req = urllib.request.Request(GATEWAY, data=json.dumps({"to": OWNER, "subject": f"[judge] MR proposed: {a.title}",
                                     "body": f"{a.title}\n\nbranch {a.branch}\n{compare}\n\n{body}"}).encode(),
                                     headers={"Content-Type": "application/json"})
        mail = json.load(urllib.request.urlopen(req, timeout=15))
    except Exception as e:                                        # mail is best effort
        mail = {"error": str(e)[:120]}
    print(json.dumps({"status": "mr_file", "branch": a.branch, "url": compare, "mr_file": str(md), "mail": mail}))


if __name__ == "__main__":
    main()
