"""Headless-browser adapter for JS-rendered career portals (Talention, B-ITE widgets, consent-gated JobFinders, concludis).
Reuses Crawler's parsing/filtering; only page acquisition differs:
  1. open list URL, accept cookie consent if a matching button exists, wait for network idle, scroll, click "mehr laden" (<=6x)
  2. collect anchors (page + iframes) with a gender marker in the text OR a job-like href; also collect JSON XHR payloads (kept in payload for debugging)
  3. detail pages: requests first (JSON-LD), browser render as fallback when the seed says browser_details=true
"""
import json
import re
import time
from urllib.parse import urljoin, urlparse, urldefrag

from .career_crawl import Crawler, JOB_HREF, JOB_TEXT, LINK_BAD, _jsonld_jobpostings, UA

CONSENT = re.compile(r"alle akzeptieren|akzeptieren|zustimmen|einverstanden|accept all|agree|verstanden|ok", re.I)
MORE = re.compile(r"mehr laden|weitere (stellen|anzeigen|laden|ergebnisse)|mehr anzeigen|alle (stellen|anzeigen)|load more|show more|nächste|weiter", re.I)


class BrowserCrawler(Crawler):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.launch(headless=True, args=["--no-sandbox"])
        self._ctx = self._b.new_context(user_agent=UA, ignore_https_errors=True, locale="de-DE", viewport={"width": 1280, "height": 2000})

    def close(self):
        try: self._b.close(); self._pw.stop()
        except Exception: pass

    def render(self, url, interact=True, timeout=60000):
        pg = self._ctx.new_page(); xhr = []
        def on_resp(r):
            try:
                if "json" in r.headers.get("content-type", "") and r.status == 200:
                    t = r.text()
                    if 200 < len(t) < 3_000_000: xhr.append({"url": r.url, "body": t})
            except Exception: pass
        pg.on("response", on_resp)
        try:
            pg.goto(url, wait_until="networkidle", timeout=timeout)
        except Exception:
            try: pg.goto(url, wait_until="domcontentloaded", timeout=timeout)
            except Exception: pg.close(); return None, [], [], url
        if interact:
            for _ in range(2):
                try:
                    btn = pg.get_by_role("button", name=CONSENT).first
                    if btn.count() and btn.is_visible(): btn.click(timeout=2000); pg.wait_for_timeout(1200)
                except Exception: pass
            for _ in range(6):
                try: pg.mouse.wheel(0, 4000); pg.wait_for_timeout(500)
                except Exception: break
            for _ in range(6):
                try:
                    m = pg.get_by_role("button", name=MORE).first
                    if not (m.count() and m.is_visible()):
                        m = pg.get_by_role("link", name=MORE).first
                    if m.count() and m.is_visible(): m.click(timeout=2500); pg.wait_for_timeout(1500)
                    else: break
                except Exception: break
        try: pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        links = []
        for f in pg.frames:
            try:
                links += f.eval_on_selector_all("a[href]", "els=>els.map(a=>[a.href,(a.innerText||a.getAttribute('title')||'').trim().replace(/\\s+/g,' ').slice(0,200)])")
            except Exception: pass
        html = pg.content(); final = pg.url
        pg.close()
        return html, links, xhr, final

    def crawl(self, seed):
        hosts = set(seed.get("hosts") or []) | {urlparse(seed["career"]).netloc}
        stats = {"list_pages": 0, "job_links_found": 0, "job_pages": 0, "jobposting_pages": 0, "heuristic_pages": 0, "dropped_non_bavaria": 0, "dropped_unknown_loc": 0, "dropped_not_pflege": 0, "xhr_json": 0}
        job_links = {}
        for url in [seed["career"]] + list(seed.get("extra_seeds", [])):
            html, links, xhr, final = self.render(url)
            if html is None: continue
            stats["list_pages"] += 1; stats["xhr_json"] += len(xhr)
            hosts.add(urlparse(final).netloc)
            for href, text in links:
                u = urldefrag(href)[0]; p = urlparse(u)
                if p.scheme not in ("http", "https") or LINK_BAD.search(u): continue
                if p.netloc not in hosts and not any(p.netloc.endswith(h.split(".", 1)[-1]) for h in hosts): continue
                if JOB_TEXT.search(text or "") or (JOB_HREF.search(u) and text and not re.fullmatch(r"(mehr|details?|weiter|ansehen|zur stelle|jetzt bewerben)", text.strip(), re.I)):
                    job_links.setdefault(u, text)
            # JSON payloads that look like job lists (title + url keys) -> add links
            for x in xhr:
                try: data = json.loads(x["body"])
                except Exception: continue
                stack = [data]
                while stack:
                    n = stack.pop()
                    if isinstance(n, dict):
                        t = n.get("title") or n.get("titel") or n.get("jobTitle") or n.get("name")
                        u = n.get("url") or n.get("link") or n.get("detailUrl") or n.get("applyUrl")
                        if isinstance(t, str) and isinstance(u, str) and JOB_TEXT.search(t): job_links.setdefault(urljoin(final, u), t)
                        stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
                    elif isinstance(n, list): stack.extend(n)
        stats["job_links_found"] = len(job_links)
        jobs = {}
        for u, anchor in list(job_links.items())[: self.budget]:
            html = None; final = u
            r = self.fetch(u) if not seed.get("browser_details") else None
            if r is not None: html, final = r.text, r.url
            else:
                html, _, _, final = self.render(u, interact=False)
            if not html: continue
            stats["job_pages"] += 1
            jps = _jsonld_jobpostings(html)
            if jps:
                stats["jobposting_pages"] += 1; jobs[final] = self._from_jsonld(jps[0], final, seed)
            else:
                h = self._heuristic(html, final, seed, anchor)
                if h: jobs[final] = h; stats["heuristic_pages"] += 1
        out = []
        for j in jobs.values():
            if j["role_class"] == "nicht_pflege": stats["dropped_not_pflege"] += 1; continue
            if j["in_bavaria"] is False: stats["dropped_non_bavaria"] += 1; continue
            if j["in_bavaria"] is None and seed.get("bavaria_only_operator"): j["in_bavaria"] = True
            if j["in_bavaria"] is None: stats["dropped_unknown_loc"] += 1; continue
            out.append(j)
        return out, stats
