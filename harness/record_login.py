"""Record the real login flow against the live app (no mock, no local _serve()).

Reuses tools/uieval.py's OVERLAY/UI/_mp4/_sheet machinery but drives a Playwright context
straight at http://localhost:8501 with real auth, instead of uieval.record()'s static-serve
+ ?mock=1 path (which would skip the server and auth entirely -- wrong for this scenario).

Selectors were read out of the actual templates, not guessed:
  - web/index.template.html:185  ->  header "Pro view" link is `.top .pro` (href="/pro")
  - web/login.template.html      ->  form is `#f`, fields `#user` / `#pass`, submit `#go`
  - web/login.template.html has NO "or / Sign in with exe.dev" link. That link only exists in
    web/pro.template.html's inline JS gate (renderGate(): `a.btn.p` linking to
    /__exe.dev/login?redirect=/pro), which never gets a chance to render here because
    app/auth.py GATED_PAGES server-redirects an anonymous GET /pro straight to /login before
    any HTML is served. So this scenario -- "click Pro view -> lands on /login" -- only ever
    exercises the password form; the exe.dev secondary door is not reachable from it. Recorded
    honestly: no fabricated pointing-at-a-link-that-was-never-there.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.uieval import OVERLAY, UI, _mp4, _sheet, OUT
import json, shutil, time

BASE = "http://localhost:8501"
NAME = "simple-to-login"
VIEWPORT = {"width": 1280, "height": 800}


def scenario(ui, page):
    ui.say("landing page")
    ui.wait(500)
    ui.say("click Pro view ->")
    ui.tap(".top .pro")
    if "/login" not in page.url:
        page.wait_for_url("**/login*", timeout=5000)
    ui.wait(500)
    ui.say("landed on /login (server redirect)")
    ui.wait(500)
    ui.say("point at the username field")
    ui.move(*ui.where("#user"))
    ui.wait(300)
    page.fill("#user", "")
    page.type("#user", "root", delay=70)
    ui.wait(300)
    ui.say("point at the password field")
    ui.move(*ui.where("#pass"))
    ui.wait(300)
    page.fill("#pass", "")
    page.type("#pass", "toor", delay=70)
    ui.wait(300)
    ui.say("no 'sign in with exe.dev' link on this page -- password is the only door here")
    ui.wait(1200)
    ui.say("submit the password form")
    ui.tap("#go")
    if "/pro" not in page.url:
        page.wait_for_url("**/pro*", timeout=8000)
    ui.wait(600)
    ui.say("landed on /pro, authenticated")
    ui.wait(1200)


def main():
    from playwright.sync_api import sync_playwright

    out = OUT / NAME
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=VIEWPORT, locale="de-DE", device_scale_factor=1,
                                   record_video_dir=str(out / "_raw"), record_video_size=VIEWPORT)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.add_init_script(OVERLAY)
        page.goto(BASE + "/")
        ui = UI(page, touch=False)
        ui.t0 = time.time()
        page.wait_for_timeout(900)
        scenario(ui, page)
        page.wait_for_timeout(600)
        ctx.close()
        browser.close()
    webm = next((out / "_raw").glob("*.webm"))
    _mp4(webm, out / "run.mp4", 480)
    _sheet(webm, out / "sheet.jpg", 12, 4, 240)
    (out / "steps.json").write_text(json.dumps({"steps": ui.log, "page_errors": errors,
                                                "viewport": VIEWPORT, "touch": False, "url": "/"},
                                               ensure_ascii=False, indent=1))
    shutil.rmtree(out / "_raw", ignore_errors=True)
    print(f"wrote {out}")
    if errors:
        print("page errors:", errors)


if __name__ == "__main__":
    main()
