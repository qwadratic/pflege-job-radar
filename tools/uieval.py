"""Record what an interaction actually looks like, so it can be watched and judged instead of guessed at.

Playwright drives a real browser but its input is synthetic: nothing is drawn where the pointer is, so a
recording of a scripted session shows things happening for no visible reason. This module injects a
visible cursor, a tap ripple and a caption bar into the page, records the session to video, and renders
two artefacts:

  * an mp4, downscaled, for a human to watch;
  * a contact sheet (evenly spaced frames, each stamped with its timestamp) for a vision model to read
    in one image instead of paying for a video.

Usage:
    .venv/bin/python tools/uieval.py --list
    .venv/bin/python tools/uieval.py map-mobile
    .venv/bin/python tools/uieval.py map-mobile --keep-webm --tiles 16
    .venv/bin/python tools/uieval.py map-mobile --publish        # opt in to the public /skill tree

Artefacts land in eval_out/<scenario>/ at the repo root -- outside the served tree, and gitignored.
Watch them as local files (eval_out/index.html).

--publish writes into web/skill/reviews/eval/<scenario>/ instead, which the app serves to anybody:
GET /skill/{name:path} in app/main.py has no session dependency, so everything under web/skill/ is
world-readable. Type that flag by hand, per run, and only for a page that is already public.

NEVER publish a recording of a page behind the login (/pro, /deck, anything reached with
UIEVAL_SESSION). /skill/ is public and unauthenticated: publishing such a recording hands an
anonymous caller a screen capture of the authenticated dashboard and defeats the 303 login gate for
every frame that was recorded. Recordings of gated pages stay in eval_out/.
"""
import argparse
import http.server
import functools
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
# Default output is outside web/ on purpose: web/skill/ is served unauthenticated (app/main.py
# GET /skill/{name:path}), so anything written under PUBLISHED is world-readable the moment it exists.
# Recordings of any page behind the login must never be published there. --publish flips OUT to
# PUBLISHED for one run; the sibling drivers (tools/record_pro_nav.py, harness/record_*.py) import OUT
# and therefore always write to eval_out/.
OUT = ROOT / "eval_out"
PUBLISHED = WEB / "skill" / "reviews" / "eval"

# The overlay lives in the page, not in the video pipeline, because only the page knows where the
# viewport actually is after a scroll. requestAnimationFrame re-attaches it: the SPA calls
# replaceChildren() on <main> constantly, and anything parented to <body> would be swept away.
OVERLAY = r"""
(() => {
  // add_init_script runs before the document exists, so the API is defined first and only paints once
  // there is a documentElement to paint into. The SPA calls replaceChildren() constantly, so the nodes
  // re-attach themselves on every frame instead of trusting that they stayed where they were put.
  const NS = window.__uieval = { x: -99, y: -99, cap: "", ready: false };
  const CSS = `
  #uie-cur{position:fixed;z-index:2147483647;pointer-events:none;left:0;top:0;width:0;height:0}
  #uie-cur i{position:absolute;left:-17px;top:-17px;width:34px;height:34px;border:2px solid #202D85;
    border-radius:50%;background:rgba(32,45,133,.14);box-shadow:0 0 0 2px rgba(255,255,255,.8)}
  #uie-cur b{position:absolute;left:-3px;top:-3px;width:6px;height:6px;border-radius:50%;background:#202D85}
  #uie-rip{position:fixed;z-index:2147483646;pointer-events:none;left:0;top:0;width:0;height:0}
  #uie-rip span{position:absolute;width:0;height:0;border:3px solid #9DE146;border-radius:50%;
    transform:translate(-50%,-50%);animation:uie-r .55s ease-out forwards}
  @keyframes uie-r{from{width:10px;height:10px;opacity:1}to{width:110px;height:110px;opacity:0}}
  #uie-cap{position:fixed;z-index:2147483645;left:0;right:0;bottom:0;pointer-events:none;
    font:600 13px/1.35 system-ui,sans-serif;background:rgba(35,32,30,.88);color:#fff;padding:7px 10px;
    display:flex;gap:10px;align-items:baseline}
  #uie-cap em{color:#9DE146;font-style:normal;font-variant-numeric:tabular-nums}
  #uie-cap s{text-decoration:none;color:#C9C6C2;margin-left:auto;font-weight:400}`;
  let cur, rip, cap, keep = [];
  const t0 = performance.now();
  const boot = () => {
    if (!document.documentElement) { setTimeout(boot, 4); return; }
    const style = document.createElement("style"); style.textContent = CSS;
    const mk = (id, html) => { const n = document.createElement("div"); n.id = id; n.innerHTML = html; return n; };
    cur = mk("uie-cur", "<i></i><b></b>"); rip = mk("uie-rip", "");
    cap = mk("uie-cap", "<em>0.0s</em><span></span><s></s>");
    keep = [style, cur, rip, cap];
    NS.ready = true;
    const frame = () => {
      for (const n of keep) if (!n.isConnected) document.documentElement.appendChild(n);
      cur.style.transform = `translate(${NS.x}px,${NS.y}px)`;
      cap.querySelector("em").textContent = ((performance.now() - t0) / 1000).toFixed(1) + "s";
      cap.querySelector("span").textContent = NS.cap;
      cap.querySelector("s").textContent = Math.round(window.scrollY) + "px scrolled";
      requestAnimationFrame(frame);
    };
    frame();
  };
  boot();
  NS.move = (x, y) => { NS.x = x; NS.y = y; };
  NS.say = (t) => { NS.cap = t; };
  NS.ripple = (x, y) => { if (!rip) return; const s = document.createElement("span");
    s.style.left = x + "px"; s.style.top = y + "px"; rip.appendChild(s); setTimeout(() => s.remove(), 620); };
})();
"""


class UI:
    """A scripted hand. Every method both drives the real input and moves the thing you can see."""

    def __init__(self, page, touch):
        self.page, self.touch, self.log = page, touch, []

    def say(self, text):
        self.log.append((round(self._t(), 2), text))
        self.page.evaluate("t=>window.__uieval && window.__uieval.say(t)", text)

    def _t(self):
        return time.time() - self.t0

    def where(self, selector):
        box = self.page.locator(selector).first.bounding_box()
        if not box:
            raise RuntimeError(f"no box for {selector}")
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def move(self, x, y, ms=420):
        """Glide, so the recording shows the hand travelling instead of teleporting."""
        steps = max(2, int(ms / 16))
        x0, y0 = self.page.evaluate("[window.__uieval.x, window.__uieval.y]")
        if x0 < 0:
            x0, y0 = x, y
        for i in range(1, steps + 1):
            k = i / steps
            k = k * k * (3 - 2 * k)                                  # ease in-out, so it reads as deliberate
            self.page.evaluate("p=>window.__uieval.move(p[0],p[1])", [x0 + (x - x0) * k, y0 + (y - y0) * k])
            self.page.wait_for_timeout(16)

    def tap(self, selector=None, xy=None, ms=420):
        x, y = xy if xy else self.where(selector)
        self.move(x, y, ms)
        self.page.evaluate("p=>window.__uieval.ripple(p[0],p[1])", [x, y])
        if self.touch:
            self.page.touchscreen.tap(x, y)
        else:
            self.page.mouse.click(x, y)
        self.page.wait_for_timeout(120)

    def drag(self, x1, y1, x2, y2, ms=600):
        self.move(x1, y1, 300)
        if self.touch:
            self.page.touchscreen.tap(x1, y1)                        # fallback: chromium touch drag needs CDP
        self.page.mouse.move(x1, y1)
        self.page.mouse.down()
        steps = max(2, int(ms / 16))
        for i in range(1, steps + 1):
            k = i / steps
            self.page.evaluate("p=>window.__uieval.move(p[0],p[1])", [x1 + (x2 - x1) * k, y1 + (y2 - y1) * k])
            self.page.mouse.move(x1 + (x2 - x1) * k, y1 + (y2 - y1) * k)
            self.page.wait_for_timeout(16)
        self.page.mouse.up()

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def key(self, key, times=1, ms=260):
        """A keystroke, captioned, so a keyboard-only run is watchable too."""
        for _ in range(times):
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(ms)

    def type(self, text, delay=60):
        self.page.keyboard.type(text, delay=delay)
        self.page.wait_for_timeout(120)

    def check(self, label, js):
        """Record a hard fact beside the pixels: steps.json carries the verdict, the sheet carries the look."""
        ok = bool(self.page.evaluate(js))
        self.log.append((round(self._t(), 2), ("PASS " if ok else "FAIL ") + label))
        return ok

    def note(self, label, js):
        """Record a value (scroll offset, focused element, slide number) instead of a verdict."""
        v = self.page.evaluate(js)
        self.log.append((round(self._t(), 2), f"{label}={v}"))
        return v

    def shot(self, selector, name):
        """A close-up of one element, for anything that must be read rather than watched."""
        self.page.locator(selector).first.screenshot(path=str(self.out / name))


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve():
    handler = functools.partial(_Quiet, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _ffprobe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def _mp4(webm, mp4, width):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm),
                    "-vf", f"scale={width}:-2:flags=lanczos,fps=15", "-c:v", "libx264", "-crf", "30",
                    "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)


def _sheet(webm, sheet, tiles, cols, width):
    """Evenly spaced frames, each stamped with its own timestamp, tiled into one image.

    Reading one 300 kB PNG costs a vision model a fraction of what a video costs, and for judging an
    interaction the frames are what matter -- the motion between them is inferable from the stamps.
    """
    dur = _ffprobe_duration(webm)
    tmp = sheet.parent / "_frames"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for i in range(tiles):
        ts = dur * (i + 0.5) / tiles
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", str(webm),
                        "-frames:v", "1", "-vf", f"scale={width}:-2:flags=lanczos", str(tmp / f"{i:02d}.png")], check=True)
        subprocess.run(["convert", str(tmp / f"{i:02d}.png"), "-background", "#23201E", "-fill", "#9DE146",
                        "-pointsize", "20", "label:" + f"{ts:.1f}s", "-gravity", "center", "-append",
                        str(tmp / f"{i:02d}.png")], check=True)
    subprocess.run(["montage", *[str(p) for p in sorted(tmp.glob("*.png"))], "-tile", f"{cols}x",
                    "-geometry", "+4+4", "-background", "#8C8985", "-quality", "82", str(sheet)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)


def _gallery():
    """One page listing every recording, so the artefacts are watchable in a browser as
    eval_out/index.html instead of as a pile of file paths. Under --publish the same page is served at
    https://pflege-board.exe.xyz/skill/reviews/eval/ -- to everybody, logged in or not."""
    runs = sorted(d for d in OUT.iterdir() if d.is_dir())
    body = []
    for d in runs:
        steps = json.loads((d / "steps.json").read_text()) if (d / "steps.json").exists() else {}
        lines = " · ".join(f"{t}s {txt}" for t, txt in steps.get("steps", [])[:40])
        body.append(f"""<section><h2>{d.name}</h2>
<p class=m>{steps.get('viewport',{}).get('width','?')}×{steps.get('viewport',{}).get('height','?')} ·
{'touch' if steps.get('touch') else 'mouse'} · {steps.get('url','')}
{'· <b>page errors: ' + str(len(steps['page_errors'])) + '</b>' if steps.get('page_errors') else ''}</p>
<video src="{d.name}/run.mp4" controls loop muted playsinline></video>
<p class=s>{lines}</p>
<img src="{d.name}/sheet.jpg" alt="contact sheet">
</section>""")
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>UI eval recordings</title>"
        "<style>body{background:#14161b;color:#e7e5e2;font:14px/1.6 ui-monospace,monospace;margin:0;padding:24px;max-width:1100px}"
        "h1{font-size:20px}h2{font-size:15px;margin:0 0 4px}section{border-top:1px solid #2a2e37;padding:22px 0}"
        "video{width:340px;max-width:100%;border:1px solid #2a2e37;display:block;margin:10px 0}"
        "img{width:100%;border:1px solid #2a2e37}.m{color:#9aa0aa;margin:0}.s{color:#9DE146;font-size:12px}</style>"
        "<h1>UI eval recordings</h1><p class=m>Generated by tools/uieval.py. Disposable — delete when the topic closes.</p>"
        "<p class=m>Default location is eval_out/ (not served). Anything here after --publish is public: "
        "never a page behind the login.</p>"
        + "".join(body), encoding="utf-8")


def record(name, scenario, url, viewport, touch=True, tiles=12, cols=4, keep_webm=False, sheet_width=240,
           mp4_width=390, origin=None, cookies=None):
    """origin: drive a running server (the app on 127.0.0.1) instead of the static copy of web/.
    cookies: seeded into the jar before the first navigation, for a page that lives behind the session."""
    from playwright.sync_api import sync_playwright

    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    srv, base = (None, origin) if origin else _serve()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport=viewport, has_touch=touch, is_mobile=touch, locale="de-DE",
                                      device_scale_factor=1, record_video_dir=str(out / "_raw"),
                                      record_video_size=viewport)
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            page.add_init_script(OVERLAY)
            page.goto(base + url)
            ui = UI(page, touch)
            ui.t0 = time.time()
            ui.out = out
            page.wait_for_timeout(900)
            scenario(ui, page)
            page.wait_for_timeout(600)
            ctx.close()                                              # flushes the video file
            browser.close()
    finally:
        if srv:
            srv.shutdown()
    webm = next((out / "_raw").glob("*.webm"))
    _mp4(webm, out / "run.mp4", mp4_width)
    _sheet(webm, out / "sheet.jpg", tiles, cols, sheet_width)
    (out / "steps.json").write_text(json.dumps({"steps": ui.log, "page_errors": errors,
                                                "viewport": viewport, "touch": touch, "url": url}, ensure_ascii=False, indent=1))
    if keep_webm:
        shutil.move(str(webm), str(out / "run.webm"))
    shutil.rmtree(out / "_raw", ignore_errors=True)
    _gallery()
    sizes = {p.name: p.stat().st_size for p in sorted(out.iterdir())}
    print(f"{name}: " + ", ".join(f"{k} {v//1024}kB" for k, v in sizes.items()))
    if errors:
        print("  page errors:", errors[:3])
    return out


# ---- scenarios ----------------------------------------------------------------------------------

def sc_map_mobile(ui, page):
    """A thumb playing with the map: tap three towns in a row, then look for a way to the results."""
    ui.say("landing")
    ui.wait(400)
    ui.say("scroll to the map")
    page.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'center'})")
    ui.wait(700)
    for i, town in enumerate(["München", "Nürnberg", "Passau"]):
        sel = f'.mp .d[aria-label^="{town}"]'
        if page.locator(sel).count() == 0:
            continue
        ui.say(f"tap {town}")
        try:
            ui.tap(sel)
        except RuntimeError:
            ui.say(f"{town} is off-screen after the last tap")
            continue
        ui.wait(700)
    ui.say("where did the page go?")
    ui.wait(800)


def sc_map_desktop(ui, page):
    ui.say("hover the dots")
    page.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'center'})")
    ui.wait(600)
    for town in ["München", "Nürnberg", "Würzburg"]:
        sel = f'.mp .d[aria-label^="{town}"]'
        if page.locator(sel).count() == 0:
            continue
        x, y = ui.where(sel)
        ui.say(f"hover {town}")
        ui.move(x, y)
        page.hover(sel)
        ui.wait(500)
    ui.say("select two towns")
    for town in ["München", "Passau"]:
        sel = f'.mp .d[aria-label^="{town}"]'
        if page.locator(sel).count():
            ui.tap(sel)
            ui.wait(500)


def sc_hero(ui, page):
    """Sit on the hero, untouched: the title flips at 10.4s and every 4.4s after, the line under the
    lead wipes at 12.6s. A pointerdown would settle both, so the scenario never touches the page."""
    ui.say("first screen, untouched")
    ui.wait(27000)



# --- verification pass, 2026-09-10: footnote, hero disclosure, /login, /deck -----------------------
# These four drive the pages a reviewer was asked to look at with eyes. They differ from the scenarios
# above in that they also record verdicts (ui.check) into steps.json: a contact sheet shows what a frame
# looked like, it cannot show whether an element was focused or whether a dot was announced.
APP = os.environ.get("UIEVAL_APP", "http://127.0.0.1:8611")     # a running app/, for the pages the static copy cannot serve


def _session_cookie():
    """The server sets its session cookie Secure, so a browser will not send it back over plain http.
    UIEVAL_SESSION carries a value minted by app.auth.create_session; it is dropped into the jar without
    the flag, which is the only way to look at a gated page on a local http origin."""
    v = os.environ.get("UIEVAL_SESSION")
    if not v:
        raise SystemExit("set UIEVAL_SESSION (see tools/uieval.py:_session_cookie)")
    return [{"name": "pj_session", "value": v, "domain": "127.0.0.1", "path": "/",
             "httpOnly": True, "secure": False, "sameSite": "Lax"}]


ATTR = ".mapbox .attr"


def sc_refs(ui, page):
    """The BKG credit: visible next to the map, reachable from the marker, unfoldable, and returnable from."""
    ui.say("scroll to the map")
    page.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'center'})")
    ui.wait(800)
    ui.check("visible tier names BKG", f"/BKG/.test(document.querySelector('{ATTR}').textContent)")
    ui.check("visible tier links the licence deed",
             f"!!document.querySelector('{ATTR} a[href*=\"govdata.de/dl-de/by-2-0\"]')")
    ui.check("visible tier states the change", f"/ver\u00e4ndert/i.test(document.querySelector('{ATTR}').textContent)")
    ui.check("marker is visible next to the map",
             f"(r=>r.width>0&&r.top>0&&r.bottom<innerHeight)(document.querySelector('{ATTR} .noteref').getBoundingClientRect())")
    ui.check("marker hit target >= 24x24",
             f"(r=>r.width>=24&&r.height>=24)(document.querySelector('{ATTR} .noteref').getBoundingClientRect())")
    ui.note("visible tier", f"document.querySelector('{ATTR}').textContent.trim()")
    ui.shot(ATTR, "attr.png")
    ui.shot(".mapbox", "mapbox.png")
    ui.note("list rows before the jump", "document.querySelectorAll('#view .list > *').length")
    ui.say("tap the [1] marker")
    ui.tap(f"{ATTR} .noteref")
    ui.wait(120)
    ui.note("note top right after the jump", "Math.round(document.getElementById('fn-geo').getBoundingClientRect().top)")
    ui.wait(1400)
    ui.check("note is :target", "!!document.querySelector('#fn-geo:target')")
    ui.check("note is focused", "document.activeElement && document.activeElement.id==='fn-geo'")
    ui.check("note landed in the viewport",
             "(r=>r.top>=0&&r.top<innerHeight)(document.getElementById('fn-geo').getBoundingClientRect())")
    ui.note("note top once the page settled", "Math.round(document.getElementById('fn-geo').getBoundingClientRect().top)")
    ui.note("list rows after the jump", "document.querySelectorAll('#view .list > *').length")
    # The jump puts the paged list's sentinel inside its 500px root margin, so 20 more rows load above the
    # note and push it back off screen. Everything below only makes sense once the note is back in view.
    ui.say("harness: put the note back on screen by hand")
    page.evaluate("document.getElementById('fn-geo').scrollIntoView({block:'center'})")
    ui.wait(900)
    ui.check("Read more forced open on the jump", "document.querySelector('#fn-geo details').open")
    ui.check("full deed inside the note",
             "!!document.querySelector('#fn-geo details a[href*=\"govdata.de/dl-de/by-2-0\"]')")
    ui.check("dataset URI inside the note", "!!document.querySelector('#fn-geo details a[href*=\"vg2500\"]')")
    ui.check("one language rendered, not both",
             "getComputedStyle(document.querySelector('#fn-geo details div[lang=\"en\"]')).display==='none'")
    ui.shot("#fn-geo", "note.png")
    ui.say("close the expander")
    ui.tap("#fn-geo summary")
    ui.wait(600)
    ui.check("expander closes", "!document.querySelector('#fn-geo details').open")
    ui.say("open it again")
    ui.tap("#fn-geo summary")
    ui.wait(700)
    ui.check("expander reopens", "document.querySelector('#fn-geo details').open")
    ui.say("back via the backlink")
    ui.tap("#fn-geo .backlink")
    ui.wait(1200)
    ui.check("focus returned to the marker", "document.activeElement && document.activeElement.id==='fnref-geo'")
    ui.check("marker back in the viewport",
             "(r=>r.top>=0&&r.bottom<=innerHeight)(document.getElementById('fnref-geo').getBoundingClientRect())")
    ui.check("note is no longer :target", "!document.querySelector('#fn-geo:target')")
    ui.wait(500)


CTL = ".mapbox .ctl"


def sc_disclose(ui, page):
    """S0 -> S1 -> S2 -> S3 and all the way back, watching the scroll offset on every reveal."""
    page.evaluate("localStorage.removeItem('pf.home')")
    page.reload()
    page.wait_for_timeout(1200)
    ui.say("scroll to the map")
    page.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'center'})")
    ui.wait(900)
    ui.check("S0: no city chip yet", f"!document.querySelector('{CTL} .sel')")
    ui.check("S0: flap is still a clock", "document.querySelectorAll('#flap .c').length>1")
    y0 = ui.note("scrollY before the first dot", "Math.round(scrollY)")
    ui.say("tap M\u00fcnchen")
    ui.tap('.mp .d[aria-label^="M\u00fcnchen"]')
    ui.wait(1100)
    ui.note("scrollY after the first dot", "Math.round(scrollY)")
    ui.check("nothing jumped under the finger", f"Math.abs(scrollY-{y0})<=2")
    ui.check("S1: city chip revealed", f"!!document.querySelector('{CTL} .sel button')")
    ui.check("S1: + Stadt revealed", f"[...document.querySelectorAll('{CTL} button')].some(b=>/Stadt|Town/.test(b.textContent))")
    ui.check("S1: Umkreis revealed", f"[...document.querySelectorAll('{CTL} button')].some(b=>/^Umkreis$|^Radius$/.test(b.textContent.trim()))")
    ui.check("S1: flap froze into a readout", "document.querySelectorAll('#flap .c').length===1")
    ui.note("flap reads", "document.getElementById('flap').textContent.trim()")
    ui.note("status line", "document.querySelector('.mapbox .say').textContent")
    # A mouse click legitimately focuses the dot it landed on; what must not happen is focus jumping to
    # one of the controls the click revealed.
    ui.check("reveal did not steal focus",
             "document.activeElement===document.body||document.activeElement.classList.contains('d')")
    ui.check("S3: fach chip row exists", "!!document.querySelector('.fachrow')")
    ui.note("fachrow offset from the viewport top",
            "document.querySelector('.fachrow')?Math.round(document.querySelector('.fachrow').getBoundingClientRect().top):null")
    ui.shot(".mapbox", "s1.png")
    y1 = ui.note("scrollY before the second dot", "Math.round(scrollY)")
    ui.say("tap N\u00fcrnberg")
    ui.tap('.mp .d[aria-label^="N\u00fcrnberg"]')
    ui.wait(1100)
    ui.check("second dot did not move the page", f"Math.abs(scrollY-{y1})<=2")
    ui.note("chips now", f"[...document.querySelectorAll('{CTL} .sel button')].map(b=>b.textContent).join(' | ')")
    ui.note("status line", "document.querySelector('.mapbox .say').textContent")
    y2 = ui.note("scrollY before Umkreis", "Math.round(scrollY)")
    ui.say("open Umkreis")
    ui.tap(f'{CTL} button:text-is("Umkreis")')
    ui.wait(1000)
    ui.check("Umkreis did not move the page", f"Math.abs(scrollY-{y2})<=2")
    ui.check("S2: range revealed", f"!!document.querySelector('{CTL} input[type=range]')")
    ui.check("S2: km readout revealed", f"!!document.querySelector('{CTL} output')")
    ui.check("S2: undo revealed", f"[...document.querySelectorAll('{CTL} button')].some(b=>/entfernen|Remove radius/.test(b.textContent))")
    ui.check("S2: dots outside the radius dimmed", "document.querySelectorAll('.mp .d.dim').length>0")
    ui.note("range aria-valuetext", f"document.querySelector('{CTL} input[type=range]').getAttribute('aria-valuetext')")
    ui.note("status line", "document.querySelector('.mapbox .say').textContent")
    ui.shot(".mapbox", "s2.png")
    ui.say("widen the radius with the keyboard")
    page.focus(f"{CTL} input[type=range]")
    ui.key("ArrowRight", 3, ms=320)
    ui.wait(900)
    ui.note("range now", f"document.querySelector('{CTL} input[type=range]').value+' / '+document.querySelector('{CTL} output').textContent")
    ui.note("status line", "document.querySelector('.mapbox .say').textContent")
    ui.say("scroll down to the specialisation chips")
    page.evaluate("document.querySelector('.fachrow').scrollIntoView({block:'center'})")
    ui.wait(900)
    ui.shot(".fachrow", "s3.png")
    ui.say("pick a specialisation")
    ui.tap(".fachrow .chips button, .fachrow button")
    ui.wait(1100)
    ui.note("chip pressed", "(b=>b?b.getAttribute('aria-pressed')+' '+b.textContent:'none')(document.querySelector('.fachrow button[aria-pressed=true]'))")
    ui.note("status line", "document.querySelector('.mapbox .say').textContent")
    ui.check("S3 has an undo (the chip toggles off)", "!!document.querySelector('.fachrow button[aria-pressed=true]')")
    ui.say("back to the map to undo everything")
    page.evaluate("document.querySelector('.mapbox').scrollIntoView({block:'center'})")
    ui.wait(800)
    ui.say("remove the radius")
    ui.tap(f'{CTL} button:has-text("entfernen")')
    ui.wait(900)
    ui.check("radius gone", f"!document.querySelector('{CTL} input[type=range]')")
    ui.check("dimming gone", "document.querySelectorAll('.mp .d.dim').length===0")
    ui.note("status line", "document.querySelector('.mapbox .say').textContent")
    ui.say("remove both towns")
    ui.tap(f"{CTL} .sel button")
    ui.wait(800)
    ui.tap(f"{CTL} .sel button")
    ui.wait(900)
    ui.check("S0 again: no chips", f"!document.querySelector('{CTL} .sel')")
    ui.check("focus went to the picker", "document.activeElement && document.activeElement.classList.contains('pk-t')")
    ui.check("fach row withdrawn with its precondition", "!document.querySelector('.fachrow')")
    ui.note("flap after the undo", "document.getElementById('flap').textContent.trim()")
    ui.check("flap stays a readout, it does not start rotating again", "document.querySelectorAll('#flap .c').length===1")
    ui.wait(600)


def sc_login(ui, page):
    """Keyboard only: tab to the form, get it wrong, read the error, then get it right."""
    ui.say("landing on /login")
    ui.wait(900)
    ui.check("default-credentials banner is up", "!document.getElementById('warn').hidden")
    ui.check("banner is an alert", "document.getElementById('warn').getAttribute('role')==='alert'")
    ui.note("banner", "document.getElementById('warn').textContent.trim().slice(0,120)")
    ui.shot("#warn", "banner.png")
    ui.say("Tab through the page")
    for i in range(7):
        ui.key("Tab", ms=300)
        ui.note(f"tab {i+1}",
                "(a=>a.tagName.toLowerCase()+(a.id?'#'+a.id:'')+(a.getAttribute&&a.getAttribute('data-lang')?'['+a.getAttribute('data-lang')+']':''))(document.activeElement)")
        if page.evaluate("document.activeElement.id==='user'"):
            break
    ui.check("tab reached the user field", "document.activeElement.id==='user'")
    ui.say("type a wrong pair")
    ui.type("root")
    ui.key("Tab", ms=250)
    ui.check("tab reached the password field", "document.activeElement.id==='pass'")
    ui.type("nope")
    ui.say("submit with Enter")
    ui.key("Enter", ms=1400)
    ui.wait(900)
    ui.check("an error is shown", "document.getElementById('err').textContent.trim().length>0")
    ui.check("the error is an alert", "document.getElementById('err').getAttribute('role')==='alert'")
    ui.note("error text", "document.getElementById('err').textContent.trim()")
    ui.check("the error does not say which half was wrong",
             "!/user|Benutzer/i.test(document.getElementById('err').textContent) || /oder|or/.test(document.getElementById('err').textContent)")
    ui.check("the submit button came back", "!document.getElementById('go').disabled")
    ui.check("focus was not stolen by the error", "document.activeElement.id==='pass'")
    ui.shot(".card", "error.png")
    ui.say("now the right pair")
    page.fill("#pass", "")
    page.focus("#pass")
    ui.type("toor")
    ui.key("Enter", ms=1500)
    ui.wait(2200)
    ui.note("where the browser ended up", "location.pathname+location.search")
    ui.note("cookie stored?", "document.cookie.includes('pj_session')")
    ui.wait(600)


def sc_deck(ui, page):
    """Arrow navigation, then the print layout."""
    ui.wait(700)
    ui.check("the deck itself was served, not the login page", "!!document.getElementById('deck')")
    ui.note("slides", "document.querySelectorAll('#deck .slide').length")
    ui.note("counter", "document.getElementById('count').textContent.trim()")
    ui.check("prev is disabled on slide 1", "document.getElementById('prev').disabled")
    ui.say("ArrowRight x3")
    ui.key("ArrowRight", 3, ms=800)
    ui.wait(700)
    ui.note("counter", "document.getElementById('count').textContent.trim()")
    ui.check("counter reached 4", "document.getElementById('count').textContent.trim().startsWith('4 ')")
    ui.check("slide 4 is at the top of the viewport",
             "(r=>Math.abs(r.top)<8)(document.querySelectorAll('#deck .slide')[3].getBoundingClientRect())")
    ui.say("ArrowLeft")
    ui.key("ArrowLeft", 1, ms=900)
    ui.wait(600)
    ui.note("counter", "document.getElementById('count').textContent.trim()")
    ui.say("End")
    ui.key("End", 1, ms=1100)
    ui.wait(700)
    ui.note("counter", "document.getElementById('count').textContent.trim()")
    ui.check("next is disabled on the last slide", "document.getElementById('next').disabled")
    ui.check("the last slide is not clipped by the fixed bar",
             "(r=>r.top>=-2)(document.querySelectorAll('#deck .slide')[document.querySelectorAll('#deck .slide').length-1].getBoundingClientRect())")
    ui.say("Home")
    ui.key("Home", 1, ms=1100)
    ui.wait(600)
    ui.note("counter", "document.getElementById('count').textContent.trim()")
    ui.say("the nav buttons")
    ui.tap("#next")
    ui.wait(800)
    ui.note("counter after the next button", "document.getElementById('count').textContent.trim()")
    ui.check("a slide wider than the viewport does not widen the page",
             "document.documentElement.scrollWidth<=document.documentElement.clientWidth+1")
    ui.say("print preview")
    page.emulate_media(media="print")
    ui.wait(900)
    page.pdf(path=str(ui.out / "deck.pdf"), format="A4", print_background=True,
             margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
    ui.check("print hides the fixed nav", "getComputedStyle(document.querySelector('.nav')).display==='none'")
    ui.check("print hides the fixed bar", "getComputedStyle(document.querySelector('.bar')).display==='none'")
    page.emulate_media(media="screen")
    ui.wait(500)


SCENARIOS = {
    "map-mobile": (sc_map_mobile, "/index.html?mock=1&lang=de#/", {"width": 390, "height": 844}, True),
    "map-desktop": (sc_map_desktop, "/index.html?mock=1&lang=de#/", {"width": 1280, "height": 800}, False),
    "hero-mobile": (sc_hero, "/index.html?mock=1&lang=de#/", {"width": 390, "height": 844}, True),
    "hero-desktop": (sc_hero, "/index.html?mock=1&lang=de#/", {"width": 1280, "height": 800}, False),
    "refs-mobile": (sc_refs, "/index.html?mock=1&lang=de#/", {"width": 390, "height": 844}, True),
    "refs-desktop": (sc_refs, "/index.html?mock=1&lang=de#/", {"width": 1280, "height": 800}, False),
    "disclose-mobile": (sc_disclose, "/index.html?mock=1&lang=de#/", {"width": 390, "height": 844}, True),
    "disclose-desktop": (sc_disclose, "/index.html?mock=1&lang=de#/", {"width": 1280, "height": 800}, False),
    "login-mobile": (sc_login, "/login?next=/pro", {"width": 390, "height": 844}, True, {"origin": APP}),
    "login-desktop": (sc_login, "/login?next=/pro", {"width": 1280, "height": 800}, False, {"origin": APP}),
    "deck-desktop": (sc_deck, "/deck", {"width": 1280, "height": 800}, False, {"origin": APP, "cookies": _session_cookie}),
    "deck-mobile": (sc_deck, "/deck", {"width": 390, "height": 844}, True, {"origin": APP, "cookies": _session_cookie}),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", nargs="?", help="scenario name, or 'all'")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--tiles", type=int, default=12)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--keep-webm", action="store_true")
    ap.add_argument("--publish", action="store_true",
                    help="write to web/skill/reviews/eval/ instead of eval_out/. That tree is served to "
                         "anonymous callers, so never use it for a page behind the login.")
    a = ap.parse_args()
    if a.publish:
        global OUT
        OUT = PUBLISHED
    if a.list or not a.scenario:
        print("\n".join(f"{k:16s} {v[2]['width']}x{v[2]['height']} {'touch' if v[3] else 'mouse'}" for k, v in SCENARIOS.items()))
        return
    names = list(SCENARIOS) if a.scenario == "all" else a.scenario.split(",")
    for n in names:
        fn, url, vp, touch, *rest = SCENARIOS[n]
        kw = rest[0] if rest else {}
        if callable(kw.get("cookies")):
            kw = {**kw, "cookies": kw["cookies"]()}
        record(n, fn, url, vp, touch, tiles=a.tiles, cols=a.cols, keep_webm=a.keep_webm, **kw)


if __name__ == "__main__":
    main()
