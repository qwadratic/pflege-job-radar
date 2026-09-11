"""Bake the real administrative outline of Bayern (and its seven Regierungsbezirke) for the public map.

Source: BKG "Verwaltungsgebiete 1:2 500 000" (VG2500), the official German boundary dataset. The map
used to draw a silhouette derived from where municipalities are, which is honest about population and
wrong about geography. This is the actual boundary.

The script downloads the GeoPackage fresh on every run (like tools/build_geo_table.py does for the
municipality table), reads it with stdlib sqlite3, parses the GeoPackage geometry blobs (a small header
followed by standard WKB) with struct, inverse-projects UTM zone 32N -> WGS84 with stdlib math, drops
the rings that are too small to see, simplifies with Douglas-Peucker, and writes

    data/geo/bayern_vg2500.json

No pyproj, no GDAL, no shapely: the whole pipeline is ~120 lines of stdlib.

Usage: .venv/bin/python tools/build_geo_outline.py [--tolerance 0.004]
"""
import argparse
import io
import json
import math
import pathlib
import sqlite3
import struct
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "geo" / "bayern_vg2500.json"
URL = "https://daten.gdz.bkg.bund.de/produkte/vg/vg2500/aktuell/vg2500_01-01-2026.utm32s.gpkg.zip"
LICENCE = "Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)"
# BKG's Nutzungsbedingungen: the source note must appear wherever the geometry is shown, "BKG" and the
# licence must be links, and because Douglas-Peucker edits the geometry a Veränderungshinweis is required.
ATTRIBUTION = {
    "year": "2026",
    "bkg_url": "https://www.bkg.bund.de",
    "licence_label": "dl-de/by-2-0",
    "licence_url": "https://www.govdata.de/dl-de/by-2-0",
    "sources_url": "https://sgx.geodatenzentrum.de/web_public/gdz/datenquellen/datenquellen_vg_nuts.pdf",
    "changed_de": "Geometrie vereinfacht",
    "changed_en": "geometry simplified",
}

# ---- GeoPackage / WKB ---------------------------------------------------------------------------

def gpkg_wkb(blob):
    """Strip the GeoPackage binary header: magic 'GP', version, flags, srs_id, optional envelope."""
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry blob")
    env = (blob[3] >> 1) & 7
    return blob[8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]:]


def wkb_polygons(wkb):
    """-> list[list[ring]] for a (Multi)Polygon; ring 0 of each polygon is the outer ring."""
    order = "<" if wkb[0] == 1 else ">"
    typ = struct.unpack_from(order + "I", wkb, 1)[0]
    off, out = 5, []

    def one(off):
        n_rings = struct.unpack_from(order + "I", wkb, off)[0]
        off += 4
        rings = []
        for _ in range(n_rings):
            n = struct.unpack_from(order + "I", wkb, off)[0]
            off += 4
            flat = struct.unpack_from(order + "%dd" % (n * 2), wkb, off)
            off += 16 * n
            rings.append(list(zip(flat[0::2], flat[1::2])))
        return rings, off

    if typ == 6:                                             # MultiPolygon
        n_parts = struct.unpack_from(order + "I", wkb, off)[0]
        off += 4
        for _ in range(n_parts):
            off += 5                                         # each member repeats byte order + type
            rings, off = one(off)
            out.append(rings)
    elif typ == 3:                                           # Polygon
        rings, off = one(off)
        out.append(rings)
    else:
        raise ValueError(f"unexpected WKB type {typ}")
    return out


# ---- ETRS89 / UTM 32N -> WGS84 ------------------------------------------------------------------
# GRS80 (ETRS89) and WGS84 differ by centimetres; irrelevant at 1:2 500 000. Snyder's inverse
# transverse Mercator series, accurate to well under a metre inside a zone.
A, F = 6378137.0, 1 / 298.257222101
K0, FE, LON0 = 0.9996, 500000.0, math.radians(9.0)          # zone 32
E2 = 2 * F - F * F
EP2 = E2 / (1 - E2)


def utm32_to_wgs84(east, north):
    m = north / K0
    mu = m / (A * (1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = EP2 * c * c, t * t
    n1 = A / math.sqrt(1 - E2 * s * s)
    r1 = A * (1 - E2) / (1 - E2 * s * s) ** 1.5
    d = (east - FE) / (n1 * K0)
    phi = phi1 - (n1 * t / r1) * (d ** 2 / 2
                                  - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * EP2) * d ** 4 / 24
                                  + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lam = LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * EP2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lam), math.degrees(phi)


# ---- simplify -----------------------------------------------------------------------------------

def dp(pts, tol):
    if len(pts) < 3:
        return pts
    ax, ay = pts[0]
    bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy) or 1e-12
    far, fd = 0, -1.0
    for i, (px, py) in enumerate(pts[1:-1], 1):
        d = abs(dy * px - dx * py + bx * ay - by * ax) / n
        if d > fd:
            far, fd = i, d
    if fd <= tol:
        return [pts[0], pts[-1]]
    return dp(pts[:far + 1], tol)[:-1] + dp(pts[far:], tol)


def simplify_ring(ring, tol):
    """A closed ring has no two endpoints to measure against, so cut it at its farthest point first."""
    ring = ring[:-1] if ring[0] == ring[-1] else ring[:]
    far = max(range(len(ring)), key=lambda i: math.dist(ring[0], ring[i]))
    out = dp(ring[:far + 1], tol)[:-1] + dp(ring[far:] + ring[:1], tol)[:-1]
    return [(round(x, 4), round(y, 4)) for x, y in out]


def ring_area(ring):
    """Shoelace, in square degrees — only ever compared against other rings, never reported."""
    s = 0.0
    for i, (x, y) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x * y2 - x2 * y
    return abs(s) / 2


# ---- build --------------------------------------------------------------------------------------

def fetch_gpkg():
    with urllib.request.urlopen(URL, timeout=180) as r:
        buf = io.BytesIO(r.read())
    z = zipfile.ZipFile(buf)
    name = next(n for n in z.namelist() if n.endswith(".gpkg"))
    tmp = pathlib.Path(tempfile.mkdtemp()) / "vg2500.gpkg"
    tmp.write_bytes(z.read(name))
    return tmp


def shape_of(blob, tol, min_area):
    """-> list of simplified WGS84 rings, biggest first, tiny islands dropped and counted."""
    rings, dropped = [], 0
    for poly in wkb_polygons(gpkg_wkb(blob)):
        for ring in poly:
            ll = [utm32_to_wgs84(x, y) for x, y in ring]
            if ring_area(ll) < min_area:
                dropped += 1
                continue
            rings.append(simplify_ring(ll, tol))
    rings.sort(key=ring_area, reverse=True)
    return rings, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tolerance", type=float, default=0.004, help="Douglas-Peucker tolerance in degrees")
    ap.add_argument("--min-area", type=float, default=0.0004, help="drop rings smaller than this (square degrees)")
    a = ap.parse_args()

    # A known point, round-tripped, so a broken projection fails here and not on the page.
    lon, lat = utm32_to_wgs84(691_000.0, 5_334_000.0)
    assert abs(lat - 48.1354) < 0.01 and abs(lon - 11.5750) < 0.01, f"projection is wrong: {lon},{lat}"

    gpkg = fetch_gpkg()
    db = sqlite3.connect(gpkg)
    # AGS 09 = Bayern; GF 9 = the land area. The GF 8 row is Bayern's share of the Bodensee and would
    # render as a detached blob off the south-west corner.
    land = db.execute("select geom from vg2500_lan where AGS='09' and GF=9").fetchone()
    if not land:
        raise SystemExit("no AGS=09/GF=9 row in vg2500_lan — the layer, the keying or the Geofaktor changed upstream")
    rings, dropped = shape_of(land[0], a.tolerance, a.min_area)
    bez = {}
    for gen, geom in db.execute("select GEN,geom from vg2500_rbz where LKZ='BY' order by GEN"):
        r, _ = shape_of(geom, a.tolerance, a.min_area)
        bez[gen] = r
    if len(bez) != 7:
        raise SystemExit(f"expected 7 Bavarian Regierungsbezirke, got {len(bez)}: {sorted(bez)}")

    pts = [p for r in rings for p in r]
    doc = {
        "_doc": "Real administrative outline of Bayern and its Regierungsbezirke, WGS84 lon/lat, "
                "simplified for a ~700px map. Built by tools/build_geo_outline.py — do not hand-edit.",
        "source": "BKG VG2500 (Verwaltungsgebiete 1:2 500 000), Gebietsstand 01-01-2026",
        "source_url": URL,
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "tolerance_deg": a.tolerance,
        "bbox": [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)],
        "bayern": rings,
        "regierungsbezirke": bez,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: Bayern {len(rings)} ring(s), {sum(len(r) for r in rings)} points "
          f"({dropped} sub-{a.min_area}deg2 ring(s) dropped); "
          + ", ".join(f"{k} {sum(len(r) for r in v)}" for k, v in bez.items())
          + f"; {OUT.stat().st_size / 1024:.1f} kB")


if __name__ == "__main__":
    main()
