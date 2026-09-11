"""One-off driver: records the 'pro-nav' scenario against the real live app (localhost:8501, real
auth), reusing tools/uieval.py's OVERLAY/UI/_mp4/_sheet machinery but with its own context driver
since uieval.record() serves web/ statically with ?mock=1, which is wrong here.

Nav verified in web/pro.template.html:492 -- NAV=[/ (n_clinics), /jobs, /clawl, /billing],
NAV2=[/docs, /settings]. "For agents" (#btn-agents) opens a dialog, not a route, so it is a bonus
step, not a nav tab.
"""
import json
import pathlib
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.uieval import OVERLAY, UI, _mp4, _sheet, OUT

BASE = "http://localhost:8501"
NAME = "pro-nav"

TABS = [
    ("#nav a[href='#/']", "Hospitals / plan -- the clinic board and map"),
    ("#nav a[href='#/jobs']", "Jobs -- open nursing postings"),
    ("#nav a[href='#/clawl']", "Clawl / Scrape -- crawl runs and coverage"),
    ("#nav a[href='#/billing']", "Billing -- spend chart and credits"),
    ("#nav2 a[href='#/docs']", "Docs -- project documentation"),
    ("#nav2 a[href='#/settings']", "Settings -- feature flags and config"),
]


def scenario(ui, page):
    ui.wait(900)
    for sel, caption in TABS:
        ui.say(caption)
        ui.tap(sel)
        ui.wait(600)
        # if a fetch just kicked off (e.g. billing chart), wait for the busy class to clear
        try:
            page.wait_for_selector(".bl-body.busy", state="detached", timeout=4000)
        except Exception:
            pass
        ui.wait(1400)


def main():
    from playwright.sync_api import sync_playwright

    out = OUT / NAME
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    viewport = {"width": 1280, "height": 800}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=viewport, locale="de-DE", device_scale_factor=1,
                                   record_video_dir=str(out / "_raw"), record_video_size=viewport)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.add_init_script(OVERLAY)  # must precede the first goto, or it never attaches

        # login first (not recorded): real /login page, root/toor
        page.goto(BASE + "/login")
        page.fill("#user", "root")
        page.fill("#pass", "toor")
        page.click("#go")
        page.wait_for_load_state("networkidle")

        page.goto(BASE + "/pro#/")
        page.wait_for_selector("#nav a", timeout=15000)
        page.wait_for_function("window.__uieval && window.__uieval.ready", timeout=5000)
        ui = UI(page, touch=False)
        ui.t0 = time.time()
        page.wait_for_timeout(900)  # caption timer starts here, after landing on /pro
        scenario(ui, page)
        page.wait_for_timeout(600)
        ctx.close()
        browser.close()

    webm = next((out / "_raw").glob("*.webm"))
    _mp4(webm, out / "run.mp4", 960)
    _sheet(webm, out / "sheet.jpg", 12, 4, 320)
    (out / "steps.json").write_text(json.dumps(
        {"steps": ui.log, "page_errors": errors, "viewport": viewport, "touch": False, "url": "/pro#/"},
        ensure_ascii=False, indent=1))
    shutil.rmtree(out / "_raw", ignore_errors=True)
    sizes = {p.name: p.stat().st_size for p in sorted(out.iterdir())}
    print(f"{NAME}: " + ", ".join(f"{k} {v//1024}kB" for k, v in sizes.items()))
    if errors:
        print("  page errors:", errors[:5])
    return out


if __name__ == "__main__":
    main()
