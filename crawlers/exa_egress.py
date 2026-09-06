"""Exa-as-egress crawler for StepStone (and any walled listing/detail page). Uses the Exa API /contents endpoint with live crawl:
text + page links. Unlike Claude's fetcher, ?page=N pagination works, and links let us (a) attach the real detail URL to each card and
(b) fingerprint the employer's ATS from the apply link on detail pages.

  export EXA_API_KEY=... SUPABASE_URL=... SUPABASE_ANON_KEY=...
  python crawlers/exa_egress.py stepstone-matrix                      # cities x keywords x pages (EXA_PAGES=3)
  python crawlers/exa_egress.py stepstone-matrix "Augsburg|Regensburg" "pflegefachkraft|krankenpfleger" 2
  python crawlers/exa_egress.py detail <stepstone job urls...>         # full text (enrichment) + ATS discovery
Cost: 1 Exa contents call per page (batched 10 per request).
"""
import json, os, re, sys, time
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawlers.claude_egress import (parse_stepstone_text, parse_stepstone_detail, fingerprint_ats, stepstone_matrix_urls, CITIES, KEYWORDS, post_inbox, CID)

EXA = "https://api.exa.ai/contents"


def exa_contents(urls, max_chars=80000, links=300):
    """-> {url: {"text":..., "links":[...]}}"""
    out = {}
    for i in range(0, len(urls), 10):
        batch = urls[i:i + 10]
        r = requests.post(EXA, headers={"x-api-key": os.environ["EXA_API_KEY"], "Content-Type": "application/json"},
                          json={"urls": batch, "text": {"maxCharacters": max_chars}, "livecrawl": "always", "extras": {"links": links}}, timeout=180)
        r.raise_for_status()
        for res in r.json().get("results", []):
            out[res.get("url") or res.get("id")] = {"text": res.get("text") or "", "links": (res.get("extras") or {}).get("links") or []}
        time.sleep(0.5)
    return out


def attach_detail_urls(cards, links):
    """StepStone detail URLs encode title/city/employer in the slug: pick the link whose slug shares most tokens with the card."""
    det = [l for l in links if "/stellenangebote--" in l]
    for c in cards:
        toks = set(re.sub(r"[^a-z0-9]+", " ", (c["title"] + " " + c["org"]).lower()).split()) - {"m", "w", "d", "in", "fuer", "die", "der", "und"}
        best, score = None, 0
        for l in det:
            lt = set(re.sub(r"[^a-z0-9]+", " ", l.lower()).split()); s = len(toks & lt)
            if s > score: best, score = l, s
        if best and score >= max(3, len(toks) // 2): c["url"] = best; c.pop("listing_only", None)
    return cards


def run_matrix(cities, kws, pages):
    urls = []
    for base in stepstone_matrix_urls(cities, kws):
        urls += [base] + [f"{base}?page={p}" for p in range(2, pages + 1)]
    print(f"{len(urls)} listing pages"); seen, total = set(), 0
    for i in range(0, len(urls), 10):
        got = exa_contents(urls[i:i + 10])
        for u, d in got.items():
            cards = [c for c in attach_detail_urls(parse_stepstone_text(d["text"], u), d["links"]) if c["url"] not in seen]
            seen.update(c["url"] for c in cards)
            rows = [{"kind": "jobposting", "source_host": "www.stepstone.de", "source_url": c["url"], "payload": c, "collector": "exa-egress-v1", "client_id": CID} for c in cards]
            n = post_inbox(rows); total += n; print(f"{u[-60:]:<60} cards {len(cards)} posted {n}")
    print("inbox rows posted:", total)


def run_detail(urls):
    got = exa_contents(urls, max_chars=40000); total = 0
    for u, d in got.items():
        j = parse_stepstone_detail(d["text"], u); rows = []
        if j: rows.append({"kind": "jobposting", "source_host": "www.stepstone.de", "source_url": u, "payload": j, "collector": "exa-egress-v1", "client_id": CID})
        ext = [l for l in d["links"] if "stepstone" not in l.lower()]; ats, apply_url = fingerprint_ats(ext)
        if ats:
            rows.append({"kind": "probe", "source_host": "www.stepstone.de", "source_url": u, "collector": "exa-egress-ats-discovery-v1", "client_id": CID,
                         "payload": {"probe": "ats_discovery", "employer": (j or {}).get("org"), "ats": ats, "apply_url": apply_url, "external_links": ext[:20], "clinic_id": None}})
        total += post_inbox(rows); print(f"{u[-60:]:<60} detail {'ok' if j else '-'} ats {ats or '-'}")
    print("inbox rows posted:", total)


if __name__ == "__main__":
    a = sys.argv[1:] or ["stepstone-matrix"]
    if a[0] == "stepstone-matrix":
        cities = a[1].split("|") if len(a) > 1 and a[1] else CITIES; kws = a[2].split("|") if len(a) > 2 and a[2] else KEYWORDS
        run_matrix(cities, kws, int(a[3]) if len(a) > 3 else int(os.environ.get("EXA_PAGES", "3")))
    elif a[0] == "detail": run_detail(a[1:])
