"""Render a single career-page URL with Playwright and fingerprint its ATS vendor.

For the JS-heavy portals that crawlers/ats_discover2.py's plain-requests angles (sitemap,
"Bewerben" link) cannot see through: no vendor markup exists until the page has executed its JS
(SPA job boards, lazy-rendered lists, consent-walled iframes).

  python crawlers/render_probe.py <url>          # prints one JSON object to stdout
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.ats_discover2 import fingerprint, ATS  # noqa: E402
from crawlers.portals import fetch_page, dismiss_consent, autoscroll, _close_browser  # noqa: E402


def probe(url):
    page, ctx = None, None
    try:
        page, ctx = fetch_page(url, wait_ms=4000, timeout_ms=25000)
        dismiss_consent(page)
        autoscroll(page, rounds=4, step=4000, wait_ms=800)
        html = page.content()
        final_url = page.url
        hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)")
        ats, ev = fingerprint(html, final_url)
        if not ats:
            ats, ev = fingerprint("", " ".join(hrefs[:400]))
        return {"url": url, "final_url": final_url, "ats_type": ats, "evidence": ev,
                "n_links": len(hrefs), "error": None}
    except Exception as e:
        return {"url": url, "final_url": None, "ats_type": None, "evidence": None,
                "n_links": 0, "error": str(e)[:200]}
    finally:
        try:
            if page: page.close()
            if ctx: ctx.close()
        except Exception:
            pass


if __name__ == "__main__":
    result = probe(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
    _close_browser()
