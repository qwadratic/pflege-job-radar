"""Record the "simple-home" scenario against the real, live app (not the mock static serve).

Reuses tools/uieval.py's OVERLAY/UI/_mp4/_sheet machinery but drives http://localhost:8501 directly,
with real auth/DB, since this scenario needs the public Simple site as actually served.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.uieval import OVERLAY, UI, _mp4, _sheet, OUT  # noqa: E402

BASE = "http://localhost:8501"
NAME = "simple-home"


def scenario(ui, page):
    ui.say("landing on the Simple site")
    ui.wait(500)

    ui.say("switching to English")
    ui.tap('.lang button[data-lang="en"]')
    ui.wait(500)

    ui.say("reading the hero: what this site is for")
    x, y = ui.where("#hero h1")
    ui.move(x, y)
    ui.wait(700)

    ui.say("checking the hospital count")
    ui.tap(".kpis .kpi:nth-child(1)")
    ui.wait(400)
    ui.say("checking today's open-job count")
    ui.tap(".kpis .kpi:nth-child(2)")
    ui.wait(400)
    ui.say("checking how many jobs are new in the last 7 days")
    ui.tap(".kpis .kpi:nth-child(3)")
    ui.wait(400)
    ui.say("checking how many towns are covered")
    ui.tap(".kpis .kpi:nth-child(4)")
    ui.wait(400)

    ui.say("typing a search: Pflegefachkraft München")
    box = page.locator(".bigq input[type=search]").first
    x, y = ui.where(".bigq input[type=search]")
    ui.move(x, y)
    box.click()
    for ch in "Pflegefachkraft München":
        box.type(ch, delay=70)
        ui.wait(20)
    ui.wait(600)

    ui.say("clicking 'See all jobs'")
    ui.tap("a.cta")
    page.wait_for_load_state("networkidle")
    ui.wait(600)

    ui.say("scanning the jobs list")
    page.wait_for_selector(".list a.row", timeout=8000)
    ui.wait(500)

    ui.say("opening the first job's details")
    ui.tap(".list a.row >> nth=0")
    page.wait_for_load_state("networkidle")
    ui.wait(800)

    ui.say("reading the job details: hospital, town, department, pay")
    ui.wait(700)

    ui.say("going back to the jobs list")
    page.go_back()
    page.wait_for_load_state("networkidle")
    ui.wait(500)

    ui.say("opening 'Hospitals' to browse hospitals instead")
    ui.tap('#nav a[href="#/"]')
    page.wait_for_load_state("networkidle")
    ui.wait(600)
    page.wait_for_selector(".list a.row.pic", timeout=8000)

    ui.say("opening the first hospital's details")
    ui.tap(".list a.row.pic >> nth=0")
    page.wait_for_load_state("networkidle")
    ui.wait(900)

    ui.say("reading the hospital details: operator, district, level, jobs there")
    ui.wait(900)


def main():
    from playwright.sync_api import sync_playwright

    out = OUT / NAME
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    errors = []
    viewport = {"width": 1280, "height": 800}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=viewport, record_video_dir=str(out / "_raw"),
                                   record_video_size=viewport)
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
    import json
    (out / "steps.json").write_text(json.dumps({"steps": ui.log, "page_errors": errors,
                                                 "viewport": viewport, "touch": False, "url": BASE + "/"},
                                                ensure_ascii=False, indent=1))
    shutil.rmtree(out / "_raw", ignore_errors=True)
    sizes = {p.name: p.stat().st_size for p in sorted(out.iterdir())}
    print(f"{NAME}: " + ", ".join(f"{k} {v // 1024}kB" for k, v in sizes.items()))
    if errors:
        print("  page errors:", errors[:5])
    return out


if __name__ == "__main__":
    main()
