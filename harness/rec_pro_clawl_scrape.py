"""Record "pro-clawl-scrape": authenticated, live app, Clawl tab -- target form, mode selector, plan
preview (GET /api/crawl/plan only, never POST /api/crawl), coverage table.

Standalone driver against the real localhost:8501 app (real DB, real auth) -- NOT tools/uieval.py's
own record()/_serve(), which serves web/ statically with ?mock=1. Reuses its OVERLAY/UI/_mp4/_sheet.

Run: .venv/bin/python harness/rec_pro_clawl_scrape.py
"""
import json
import shutil
import sys
import time
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.uieval import OVERLAY, UI, _mp4, _sheet, _ffprobe_duration, OUT  # noqa: E402

BASE = "http://localhost:8501"
NAME = "pro-clawl-scrape"


def scenario(ui, page):
    ui.say("signed in as root, on the operator dashboard")
    ui.wait(500)
    ui.say("open the Clawl / Scrape tab")
    ui.tap('#nav a[href="#/clawl"]')
    page.wait_for_selector(".card.hud")
    ui.wait(500)

    ui.say("target scope: all / district / town / hospital / ATS vendor")
    seg = page.locator(".seg[role=group] button[data-v]")
    for v in ["regierungsbezirk", "city", "clinic", "ats_type"]:
        btn = f'.seg[role=group] button[data-v="{v}"]'
        if page.locator(btn).count():
            ui.tap(btn)
            ui.wait(450)
    ui.say("back to scope: all hospitals (the neutral state)")
    ui.tap('.seg[role=group] button[data-v="all"]')
    ui.wait(400)

    ui.say("mode selector: auto / adapter / Firecrawl, each with its explanatory subtext")
    ui.tap(".card.hud .pick .pk-t")
    page.wait_for_selector(".card.hud .pick .dd .o")
    ui.wait(900)
    ui.say("pick auto: try the adapter, fall back to Firecrawl")
    ui.tap('.card.hud .pick .dd .o[id$="-o0"]')
    ui.wait(400)

    ui.say("max Firecrawl credits + fetch-detail-pages toggle sit next to the mode")
    ui.wait(600)

    ui.say("Show plan: GET /api/crawl/plan only -- a free read of the gate, no run fired")
    ui.tap(".card.hud button.btn.n")
    page.wait_for_selector("#plan-dlg-body .gaterow", timeout=8000)
    ui.wait(1200)
    ui.say("stopping here: the dialog's Run button (confirm) is the one that POSTs /api/crawl and spends credits")
    ui.wait(1400)
    ui.tap("#plan-dlg-body .row .btn:not(.n)")  # Cancel, not confirm
    ui.wait(500)

    ui.say("scroll to the coverage-by-adapter table")
    page.evaluate("document.querySelector('.card.hud').scrollIntoView({block:'start'})")
    ui.wait(300)
    page.locator("text=Coverage").first.scroll_into_view_if_needed()
    ui.wait(700)
    ui.say("adapter rows vs the Firecrawl row: coverage % is what an operator triages next")
    ui.wait(1600)


def main():
    from playwright.sync_api import sync_playwright

    out = OUT / NAME
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    viewport = {"width": 1440, "height": 900}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=viewport, locale="de-DE", device_scale_factor=1,
                                   record_video_dir=str(out / "_raw"), record_video_size=viewport)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.add_init_script(OVERLAY)

        # Real login: /login page's user/pass fields, real POST /api/auth/login.
        page.goto(BASE + "/login")
        page.wait_for_timeout(700)
        page.fill("#user", "root")
        page.fill("#pass", "toor")
        page.click("#go")
        page.wait_for_url(BASE + "/pro", timeout=8000)
        page.wait_for_timeout(600)

        ui = UI(page, touch=False)
        ui.t0 = time.time()
        page.wait_for_timeout(500)
        scenario(ui, page)
        page.wait_for_timeout(500)
        ctx.close()
        browser.close()

    webm = next((out / "_raw").glob("*.webm"))
    _mp4(webm, out / "run.mp4", 480)
    _sheet(webm, out / "sheet.jpg", 12, 4, 260)
    (out / "steps.json").write_text(json.dumps({"steps": ui.log, "page_errors": errors,
                                                 "viewport": viewport, "touch": False,
                                                 "url": "/pro#/clawl"}, ensure_ascii=False, indent=1))
    shutil.rmtree(out / "_raw", ignore_errors=True)

    dur = _ffprobe_duration(out / "run.mp4")
    print(f"artefact: {out}")
    print(f"mp4 duration: {dur:.2f}s")
    print(f"captioned steps: {len(ui.log)}")
    print(f"page errors: {errors}")
    for t, c in ui.log:
        print(f"  {t:>6.2f}s  {c}")


if __name__ == "__main__":
    main()
