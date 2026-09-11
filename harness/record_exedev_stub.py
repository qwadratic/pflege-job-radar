"""Record what the "Sign in with exe.dev" door actually does today, against the real live app
(localhost:8501, real auth) -- not the mocked web/ static server tools/uieval.py serves.

Reuses tools/uieval.py's OVERLAY/UI/_mp4/_sheet, but drives a context pointed at the live app
instead of _serve()+?mock=1, because this scenario needs real /login, real /pro gate, real
/api/auth/login and a real (probably 404) /__exe.dev/login response.

Two clips:
  exedev-stub             -- /login page: no exe.dev link exists there today (checked below).
  exedev-stub-inline-gate -- /pro while logged out: the inline renderGate() card's exe.dev link.

Artefacts: web/skill/reviews/eval/<scenario>/{run.mp4,sheet.jpg,steps.json}
"""
import json
import pathlib
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.uieval import OVERLAY, UI, _mp4, _sheet, OUT

BASE = "http://localhost:8501"


def _record(name, url, scenario, viewport, touch=False, tiles=6, cols=3):
    from playwright.sync_api import sync_playwright

    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=viewport, has_touch=touch, is_mobile=touch, locale="de-DE",
                                  device_scale_factor=1, record_video_dir=str(out / "_raw"),
                                  record_video_size=viewport)
        ctx.clear_cookies()
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.add_init_script(OVERLAY)
        page.goto(BASE + url)
        ui = UI(page, touch)
        ui.t0 = time.time()
        page.wait_for_timeout(700)
        scenario(ui, page)
        page.wait_for_timeout(500)
        ctx.close()
        browser.close()
    webm = next((out / "_raw").glob("*.webm"))
    _mp4(webm, out / "run.mp4", 390)
    _sheet(webm, out / "sheet.jpg", tiles, cols, 240)
    (out / "steps.json").write_text(json.dumps({"steps": ui.log, "page_errors": errors,
                                                "viewport": viewport, "touch": touch, "url": url},
                                                ensure_ascii=False, indent=1))
    shutil.rmtree(out / "_raw", ignore_errors=True)
    print(f"{name}: wrote {out}")
    return out


def sc_login_page(ui, page):
    """/login (web/login.template.html) has no exe.dev entry point at all today -- confirmed by
    grepping the served HTML before writing this script. Record that plainly instead of guessing
    a selector that would never match."""
    ui.say("fresh /login, unauthenticated")
    ui.wait(600)
    ui.say('looking for a "Sign in with exe.dev" link')
    ui.wait(500)
    has_link = page.locator('a[href*="__exe.dev"]').count() > 0
    if not has_link:
        ui.say("none found: /login has only the password form, no exe.dev door")
        ui.wait(800)
        return
    ui.say("found it, clicking")
    link = page.locator('a[href*="__exe.dev"]').first
    box = link.bounding_box()
    ui.tap(xy=(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2))
    deadline = time.time() + 6
    while time.time() < deadline and page.url.rstrip("/") == (BASE + "/login"):
        page.wait_for_timeout(200)
    if time.time() >= deadline:
        ui.say("no response within 6s")
    else:
        ui.say(f"landed on {page.url}")
    ui.wait(600)


def sc_inline_gate(ui, page):
    """Meant to reach the client-side renderGate() card (web/pro.template.html ~line 1381:
    '#gate a.btn.p' exe.dev link, href /__exe.dev/login?redirect=/pro). But app/auth.py's
    AuthMiddleware (GATED_PAGES incl. "/pro") now 303-redirects an unauthenticated GET /pro to
    /login?next=/pro server-side, before the SPA script that builds #gate ever runs (confirmed:
    curl -I /pro -> 303 Location: /login?next=/pro; #gate never appears in a live browser).
    renderGate() is dead code for a direct navigation today. Record that reality."""
    ui.say("fresh /pro, unauthenticated")
    ui.wait(600)
    if page.locator("#gate").count() == 0:
        ui.say(f"redirected server-side to {page.url} -- #gate never rendered, no inline exe.dev link to click")
        ui.wait(900)
        return
    ui.say('inline gate card found: "Sign in with exe.dev" link')
    sel = "#gate a.btn.p"
    ui.tap(sel)
    deadline = time.time() + 6
    start_url = page.url
    while time.time() < deadline and page.url == start_url:
        page.wait_for_timeout(200)
    if time.time() >= deadline:
        ui.say("no response within 6s")
    else:
        ui.say(f"landed on {page.url}")
    ui.wait(600)


def main():
    r1 = _record("exedev-stub", "/login", sc_login_page, {"width": 1280, "height": 800})
    r2 = _record("exedev-stub-inline-gate", "/pro", sc_inline_gate, {"width": 1280, "height": 800})
    for out in (r1, r2):
        steps = json.loads((out / "steps.json").read_text())
        print(out.name, "page_errors:", steps["page_errors"])
        for t, txt in steps["steps"]:
            print(f"  {t}s  {txt}")


if __name__ == "__main__":
    main()
