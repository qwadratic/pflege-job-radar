"""TASK-120: for each clinic, collect candidate building/interior photos from three sources --
(1) Firecrawl image search (richest: pulls from the clinic's own site AND third-party hospital
directories, with width/height known up front, no download needed to pre-filter tiny ones);
(2) Google Maps' own listing photo via Firecrawl (high-confidence: Maps ties it to the actual place);
(3) the clinic's own website, scraped directly (free, supplements the other two). Downloads are
deduplicated by a perceptual hash so near-identical photos (the same shot re-served at different crops/
sizes across sources) don't both survive -- Ivan 2026-09-22: "до трёх [фото], и чтобы не было похожих."

This is the COLLECTION step only -- no Haiku curation, no API, no frontend gallery yet (the rest of
TASK-120). Saves up to MAX_SAVED per clinic; whichever ones a later Haiku pass keeps is a separate step.

Round 2 (Ivan): a bare homepage scrape pulled whatever decorative content the CMS put there -- a
stock-photo hero banner and a news-widget thumbnail both "worked" as far as the script was concerned.
Fix: prefer an "Über uns"/"Standort"-style page over the bare homepage -- German hospital sites
overwhelmingly put real building/campus photos there, not on the landing page.

Round 3 (Ivan): added Google Maps as a source, via Firecrawl scraping a Maps search-results URL (a
plain requests.get() can't render Maps' JS UI). Maps' place card exposes exactly ONE photo without
deeper interaction; the full gallery needs clicking into UI elements whose selectors were tried and are
not reliably scriptable (confirmed live) -- stays a single photo, not several.

Round 4 (Ivan, "раз Firecrawl такой дешёвый"): added Firecrawl's own /search?sources=images endpoint --
a real multi-source image search (~2 credits/call, confirmed live), not fighting Maps' gallery UI at
all. Returns width/height directly, so obviously-too-small results are dropped before ever downloading
them. Combined with a simple 8x8 average-hash (no new dependency -- PIL already here) so a photo the
image search and the website both happen to serve doesn't count twice toward MAX_SAVED.

Saves to data/clinic_photos/<clinic_id>/<NN>.<ext>, one directory per clinic, skipped entirely if it
already has >=1 saved photo (idempotent/resumable -- a background full run can be killed and rerun).
At the end of a full run, zips data/clinic_photos/ into data/clinic_photos.zip.

  set -a; source .env; set +a && .venv/bin/python tools/task120_collect_clinic_photos.py --limit 5
  set -a; source .env; set +a && .venv/bin/python tools/task120_collect_clinic_photos.py            # all clinics, then zips
"""
import argparse
import html
import io
import os
import re
import sys
import time
import zipfile
from urllib.parse import quote, urljoin, urlparse

import requests
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A  # noqa: E402
from app import runs as R  # noqa: E402

UA = "Mozilla/5.0 (compatible; pflege-board photo collector)"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "clinic_photos")
ZIP_PATH = OUT_DIR.rstrip("/") + ".zip"
IMG_RX = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
LINK_RX = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
MAPS_PHOTO_RX = re.compile(r'https://lh\d\.googleusercontent\.com/[^"\'\s)]+')
FIRECRAWL_SCRAPE_API = "https://api.firecrawl.dev/v2/scrape"
FIRECRAWL_SEARCH_API = "https://api.firecrawl.dev/v2/search"
# Never a real building/campus photo: chrome, icons, tracking pixels, common CMS placeholder names.
JUNK_RX = re.compile(r"logo|icon|favicon|sprite|pixel|spacer|avatar|placeholder|\.svg(\?|$)|data:image", re.I)
# German hospital sites overwhelmingly put real building/campus photos on one of these, not the landing
# page (checked live 2026-09-22 across the München Klinik / kbo-Heckscher / Danuvius pilot sites).
ABOUT_PAGE_RX = re.compile(r"über[- ]?uns|ueber[- ]?uns|standort|unternehmen|unsere[- ]klinik|die[- ]klinik|klinikum/?$|campus", re.I)
MIN_BYTES = 15_000       # a real photo, not a thumbnail or a 1x1 tracking pixel
MIN_DIM = 500            # px, either side -- big enough to exclude icons/news-widget thumbnails (a
                          # square 450x450 teaser card was still getting through at 400) without also
                          # excluding a real building photo the CMS center-cropped to a square thumb
                          # (confirmed live 2026-09-22: danuviusklinik.de's own "..._klinik.webp",
                          # 1200x1200 -- shape alone can't tell a cropped real photo from a portrait/
                          # news thumbnail; that judgment is Haiku curation's job, not this heuristic's).
MAX_SAVED = 6            # Ivan: "до трёх [image-search/maps] плюс сайта ещё до трёх"
DUP_HAMMING_MAX = 6      # of 64 bits; <=6 reads as "the same photo again" (same shot, different crop/size)


def _firecrawl_key():
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    return key


def _firecrawl_headers():
    return {"Authorization": "Bearer " + _firecrawl_key(), "Content-Type": "application/json"}


def collect_from_image_search(name, town, session, limit=8, timeout=60):
    """Firecrawl /search?sources=images -> [(url, width, height), ...], pre-filtered to MIN_DIM so
    obviously-tiny results never reach the download step. () on any failure -- never raises, a bad
    search result for one clinic must not kill the whole run."""
    try:
        r = session.post(FIRECRAWL_SEARCH_API, headers=_firecrawl_headers(),
                          json={"query": f"{name} {town}", "sources": ["images"], "limit": limit}, timeout=timeout)
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"    image search failed: {e}")
        return []
    if not d.get("success"):
        print(f"    image search failed: {d.get('error') or d}")
        return []
    out = []
    for im in d.get("data", {}).get("images", []) or []:
        u, w, h = im.get("imageUrl"), im.get("imageWidth") or 0, im.get("imageHeight") or 0
        if u and w >= MIN_DIM and h >= MIN_DIM:
            out.append(u)
    return out


RETRY_AFTER_RX = re.compile(r"retry after (\d+)s", re.I)


def collect_from_maps(name, town, session, timeout=90, max_retries=5):
    """This clinic's Google Maps cover photo (one URL, resized up), or [] if the place wasn't found /
    has no photo / the scrape failed. Firecrawl's per-minute rate limit (429, "Rate limit exceeded...
    please retry after Ns") is a transient condition, not an honest "no photo" -- retried with the
    server's own suggested backoff (capped at max_retries) so a burst of calls doesn't get silently
    recorded as empty results (2026-09-23: an earlier unretried maps-only run hit this on ~46% of
    clinics)."""
    query = quote(f"{name} {town}")
    maps_url = f"https://www.google.com/maps/search/{query}"
    for attempt in range(max_retries + 1):
        try:
            r = session.post(FIRECRAWL_SCRAPE_API, headers=_firecrawl_headers(),
                              json={"url": maps_url, "formats": ["html"], "waitFor": 3000}, timeout=timeout)
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"    Maps fetch failed: {e}")
            return []
        if d.get("success"):
            break
        err = str(d.get("error") or d)
        m = RETRY_AFTER_RX.search(err)
        if m and attempt < max_retries:
            wait = int(m.group(1)) + 1
            print(f"    Maps rate-limited, retrying in {wait}s ({attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        print(f"    Maps scrape failed: {err}")
        return []
    hits = MAPS_PHOTO_RX.findall(d.get("data", {}).get("html", ""))
    if not hits:
        return []
    base = re.sub(r"=w\d+-h\d+[^\"'\s)]*$", "", hits[0])
    return [base + "=w1600-h1200-k-no"]


def _candidate_urls(html_text, base_url):
    urls, seen = [], set()
    for m in IMG_RX.finditer(html_text):
        src = html.unescape(m.group(1)).strip()
        if not src or JUNK_RX.search(src):
            continue
        u = urljoin(base_url, src)
        if u in seen or not u.lower().startswith("http"):
            continue
        seen.add(u)
        urls.append(u)
    return urls


def _about_page_url(html_text, base_url):
    """The first same-host link whose href or anchor text looks like an "about us"/"location" page."""
    host = urlparse(base_url).netloc
    for m in LINK_RX.finditer(html_text):
        href, text = html.unescape(m.group(1)).strip(), re.sub(r"<[^>]+>", "", m.group(2))
        if ABOUT_PAGE_RX.search(href) or ABOUT_PAGE_RX.search(text):
            u = urljoin(base_url, href)
            if urlparse(u).netloc == host:
                return u
    return None


def collect_for_website(website, session, max_candidates=10, timeout=20):
    """website's own homepage (+ its "about us"/"Standort" page, preferred) -> up to max_candidates
    candidate photo URLs, about-page ones first. () if unreachable."""
    try:
        r = session.get(website, headers={"User-Agent": UA}, timeout=timeout)
        if not r.ok:
            return []
    except requests.RequestException as e:
        print(f"    fetch failed: {e}")
        return []
    home_urls = _candidate_urls(r.text, r.url)
    about_urls = []
    about_url = _about_page_url(r.text, r.url)
    if about_url:
        try:
            r2 = session.get(about_url, headers={"User-Agent": UA}, timeout=timeout)
            if r2.ok:
                about_urls = _candidate_urls(r2.text, r2.url)
        except requests.RequestException as e:
            print(f"    about-page fetch failed ({about_url[:70]}): {e}")
    ordered, seen = [], set()
    for u in about_urls + home_urls:      # about-page candidates tried first
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered[: max_candidates * 3]  # over-fetch candidates; the size filter below will drop some


def _ext_for(url, content_type):
    ct_ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get((content_type or "").split(";")[0].strip())
    if ct_ext:
        return ct_ext
    path_ext = os.path.splitext(urlparse(url).path)[1].lower()
    return path_ext if path_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"


def _ahash(content):
    """8x8 average-hash (no new dependency -- PIL already here): resize to 8x8 grayscale, one bit per
    pixel for above/below the mean, packed into a 64-bit int. Two photos of the same shot (re-cropped
    or re-scaled across sources) land a small Hamming distance apart; two different photos don't."""
    with Image.open(io.BytesIO(content)) as im:
        small = im.convert("L").resize((8, 8), Image.LANCZOS)
        px = list(small.getdata())
    avg = sum(px) / len(px)
    bits = 0
    for i, v in enumerate(px):
        if v > avg:
            bits |= 1 << i
    return bits


def _hamming(a, b):
    return bin(a ^ b).count("1")


def _big_enough_photo(content):
    try:
        with Image.open(io.BytesIO(content)) as im:
            w, h = im.size
    except Exception:
        return False
    return w >= MIN_DIM and h >= MIN_DIM


def save_photos(clinic_id, urls, session, max_saved=MAX_SAVED, timeout=20):
    out = os.path.join(OUT_DIR, str(clinic_id))
    os.makedirs(out, exist_ok=True)
    saved, hashes = 0, []
    for u in urls:
        if saved >= max_saved:
            break
        try:
            r = session.get(u, headers={"User-Agent": UA}, timeout=timeout)
            if not r.ok or len(r.content) < MIN_BYTES or not _big_enough_photo(r.content):
                continue
            h = _ahash(r.content)
            if any(_hamming(h, seen) <= DUP_HAMMING_MAX for seen in hashes):
                continue
            hashes.append(h)
            saved += 1
            ext = _ext_for(u, r.headers.get("Content-Type"))
            with open(os.path.join(out, f"{saved:02d}{ext}"), "wb") as f:
                f.write(r.content)
        except requests.RequestException as e:
            print(f"    download failed ({u[:70]}): {e}")
        except Exception as e:                       # a corrupt/undecodable image must not kill the run
            print(f"    skipped ({u[:70]}): {e}")
    return saved


def make_archive():
    if not os.path.isdir(OUT_DIR):
        return None
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for cid in sorted(os.listdir(OUT_DIR)):
            cdir = os.path.join(OUT_DIR, cid)
            if not os.path.isdir(cdir):
                continue
            for fn in sorted(os.listdir(cdir)):
                zf.write(os.path.join(cdir, fn), arcname=f"{cid}/{fn}")
    return ZIP_PATH


def collect_maps_photos(session, limit=None):
    """TASK-120 seed: exactly ONE real Google-Maps cover photo per clinic, into the clinic_photos table
    (app/runs.py) + data/clinic_photos/<clinic_id>/maps.<ext> -- a NEW filename, distinct from the
    01..06 pool the multi-source collector above already saved, so neither run overwrites the other.
    Idempotent/resumable: skips a clinic that already has a 'maps' row."""
    R.init()
    clinics = A.rest_get("clinics", {"select": "clinic_id,name,town", "order": "clinic_id"})
    if limit:
        clinics = clinics[:limit]
    print(f"{len(clinics)} clinic(s) to process (maps-only)")
    got, empty = 0, 0
    for i, c in enumerate(clinics, 1):
        cid, name, town = c["clinic_id"], c["name"], (c.get("town") or "")
        if R.clinic_photo_path(cid):
            print(f"[{i}/{len(clinics)}] {cid} {name[:40]:40} already has a maps photo, skipped")
            got += 1
            continue
        urls = collect_from_maps(name, town, session)
        saved_path = None
        for u in urls:
            try:
                r = session.get(u, headers={"User-Agent": UA}, timeout=20)
                if not r.ok or len(r.content) < MIN_BYTES or not _big_enough_photo(r.content):
                    continue
                out = os.path.join(OUT_DIR, str(cid))
                os.makedirs(out, exist_ok=True)
                ext = _ext_for(u, r.headers.get("Content-Type"))
                saved_path = os.path.join(out, f"maps{ext}")
                with open(saved_path, "wb") as f:
                    f.write(r.content)
                break
            except requests.RequestException as e:
                print(f"    download failed ({u[:70]}): {e}")
        if saved_path:
            R.record_clinic_photo(cid, saved_path, "maps")
            got += 1
            print(f"[{i}/{len(clinics)}] {cid} {name[:40]:40} maps photo saved")
        else:
            empty += 1
            print(f"[{i}/{len(clinics)}] {cid} {name[:40]:40} no maps photo")
    print(f"\nmaps-only done: {got}/{len(clinics)} clinic(s) got a maps photo, {empty} empty")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only process the first N clinics (pilot run)")
    ap.add_argument("--sleep", type=float, default=1.0, help="politeness delay between distinct websites")
    ap.add_argument("--no-archive", action="store_true", help="skip zipping data/clinic_photos/ at the end")
    ap.add_argument("--maps-only", action="store_true",
                     help="TASK-120 seed: one Maps cover photo per clinic into the clinic_photos table, skip the multi-source/Haiku pipeline")
    a = ap.parse_args()

    if a.maps_only:
        collect_maps_photos(requests.Session(), limit=a.limit)
        return

    clinics = A.rest_get("clinics", {"select": "clinic_id,name,town,website", "order": "clinic_id"})
    if a.limit:
        clinics = clinics[: a.limit]
    print(f"{len(clinics)} clinic(s) to process")

    session = requests.Session()
    website_cache = {}     # website url -> candidate photo urls (shared domains fetched once)
    total_saved, total_clinics_with_photos = 0, 0
    for i, c in enumerate(clinics, 1):
        cid, name, town = c["clinic_id"], c["name"], (c.get("town") or "")
        website = (c.get("website") or "").strip()
        out = os.path.join(OUT_DIR, str(cid))
        if os.path.isdir(out) and any(os.scandir(out)):
            print(f"[{i}/{len(clinics)}] {cid} {name[:40]:40} already has photos, skipped")
            continue
        search_urls = collect_from_image_search(name, town, session)
        maps_urls = collect_from_maps(name, town, session)
        website_urls = []
        if website:
            if website not in website_cache:
                website_cache[website] = collect_for_website(website, session)
                time.sleep(a.sleep)
            website_urls = website_cache[website]
        urls = search_urls + maps_urls + website_urls   # richest/most-targeted source first
        if not urls:
            print(f"[{i}/{len(clinics)}] {cid} {name[:40]:40} 0 candidates")
            continue
        saved = save_photos(cid, urls, session)
        total_saved += saved
        if saved:
            total_clinics_with_photos += 1
        print(f"[{i}/{len(clinics)}] {cid} {name[:40]:40} {saved}/{len(urls)} saved "
              f"(search={len(search_urls)}, maps={len(maps_urls)}, site={len(website_urls)})")

    print(f"\ndone: {total_clinics_with_photos}/{len(clinics)} clinic(s) got >=1 photo, {total_saved} file(s) total")
    if not a.no_archive:
        path = make_archive()
        if path:
            print(f"archive: {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    main()
