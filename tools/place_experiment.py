"""TASK-431.9 experiment driver: place evidence of postings and clinics, match confidence, comparison with today's links.
ADDITIVE and OFFLINE: it imports existing code, nothing imports it; it writes no DB row and calls no ingest endpoint; every page comes
from the mirror (tests/mirror.py replay, never tools/mirror.py record); a network guard refuses any non-local socket or DNS lookup.

  replay-board <board_id> --out FILE        replay one mirrored board, write the place evidence of its rows (jsonl.gz) + FILE.status.json
  replay-all   --out-dir DIR [--timeout S]  every board of data/mirror/INDEX.json, smallest first, one subprocess each; a board that
                                            hits the timeout is recorded as truncated, never as success
  pull         --out-dir DIR                DB READS through app.config.rest_get_all (clinics, postings, posting_observations)
  analyze      --data DIR --replay DIR --geonames FILE --out DIR       the whole measurement, report numbers as JSON + CSVs

Run with the repo's .venv python; `pull` needs the env (SUPABASE_URL, SUPABASE_ANON_KEY) and is the only subcommand that opens a socket.
"""
import argparse
import collections
import concurrent.futures
import csv
import gzip
import json
import os
import re
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOCAL = {"localhost", "127.0.0.1", "::1"}


def install_network_guard():
    """Refuse every non-local connect and DNS lookup (tests/conftest.py does the same for a test) and remember what was refused."""
    hits = []
    real = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex, "gai": socket.getaddrinfo}

    def _connect(self, address, *a):
        if isinstance(address, tuple) and address[0] not in LOCAL:
            hits.append(f"connect {address[0]}")
            raise OSError(f"network guard: {address[0]}")
        return real["connect"](self, address, *a)

    def _connect_ex(self, address, *a):
        if isinstance(address, tuple) and address[0] not in LOCAL:
            hits.append(f"connect {address[0]}")
            return 111
        return real["connect_ex"](self, address, *a)

    def _gai(host, *a, **k):
        if isinstance(host, str) and host not in LOCAL and not re.fullmatch(r"[\d.]+|[0-9a-f:]+", host):
            hits.append(f"dns {host}")
            raise socket.gaierror(socket.EAI_NONAME, f"network guard: {host}")
        return real["gai"](host, *a, **k)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = _connect, _connect_ex, _gai
    return hits


# ---------------------------------------------------------------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------------------------------------------------------------
def _html_pages(board_id):
    """{url: (status, body bytes)} of the HTML answers the mirror holds for the board (first 200 answer per URL)."""
    import gzip as _gz
    from tests import mirror as M
    st = M.Store.load_shared(board_id)
    out = {}
    for r in st.rows():
        if r.status == 200 and r.body_sha and r.url not in out and (r.method == "GET"):
            out[r.url] = r
    return st, out


def _body(st, row):
    b = st.body(row.body_sha)
    if b[:2] == b"\x1f\x8b":
        import zlib
        b = zlib.decompress(b, 16 + zlib.MAX_WBITS)
    return b


def _loc_list(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    out = []
    for l in v or []:
        a = (l or {}).get("adresse") or l or {}
        out.append({"city": a.get("ort") or a.get("city"), "plz": a.get("plz"), "region": a.get("region")})
    return out


def replay_board(board_id, out):
    if os.environ.get("MIRROR_RECORD") == "1":
        sys.exit("MIRROR_RECORD=1: this tool never records")
    hits = install_network_guard()
    from tests import adapter_harness as H
    from pflege_jobs import place_conf as P, verify as V
    board = next((b for b in H.indexed_boards() if b["board_id"] == board_id), None)
    if board is None:
        sys.exit(f"board {board_id!r} is not in the mirror index")
    t0 = time.time()
    status = {"board_id": board_id, "kind": board["kind"], "vendor": board["vendor"], "url": board["url"], "clinics": [
        {"clinic_id": c["clinic_id"], "town": c.get("town")} for c in board["clinics"]]}
    stats = {}
    try:
        rows, _calls = H.run_adapter(board, stats=stats)
    except BaseException as e:           # MirrorMiss included: a finding, listed in the report, never a reason to fetch
        status.update(result="miss" if type(e).__name__ == "MirrorMiss" else "error", error=f"{type(e).__name__}: {str(e)[:600]}",
                      seconds=round(time.time() - t0, 1), guard_hits=hits[:5])
        json.dump(status, open(out + ".status.json", "w"), ensure_ascii=False)
        return
    st, pages = _html_pages(board_id)
    n_html = n_page_place = 0
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for row in rows:
            if board["kind"] == "vendor":
                p = row.get("payload") or {}
                loc = [{"city": l.get("city"), "plz": l.get("plz"), "region": l.get("region")} for l in (p.get("loc") or [])]
                url, page, title, org, desc, cs = row.get("source_url") or p.get("url"), p.get("page") or p.get("url"), p.get("title"), p.get("org"), p.get("description"), None
            else:
                loc = _loc_list(row.get("locations")) or [{"city": row.get("city"), "plz": row.get("plz"), "region": row.get("region")}]
                url, page, title, org, desc = row.get("source_url"), row.get("external_url") or row.get("source_url"), row.get("title"), row.get("employer_name"), row.get("description")
                pl = row.get("payload")
                cs = pl.get("city_source") if isinstance(pl, dict) else None
            pg = pages.get(page) or pages.get(url)
            pe = None
            if pg is not None and "html" in (pg.headers and next((v for k, v in pg.headers if k.lower() == "content-type"), "") or "").lower():
                n_html += 1
                city, plz, src = V.extract_location(_body(st, pg).decode("utf-8", "replace"))
                if city or plz:
                    n_page_place += 1
                    pe = {"city": city, "plz": plz, "src": src}
            f.write(json.dumps({"board_id": board_id, "url": url, "page": page, "ref": row.get("source_ref"), "title": title, "org": org, "loc": loc,
                                "city_source": cs, "page_place": pe, "text": P.text_places(desc)}, ensure_ascii=False) + "\n")
    status.update(result="ok", rows=len(rows), seconds=round(time.time() - t0, 1), html_pages_for_rows=n_html, page_place=n_page_place,
                  adapter_error=stats.get("error"), guard_hits=hits[:5])
    json.dump(status, open(out + ".status.json", "w"), ensure_ascii=False)


def replay_all(out_dir, timeout, workers, only=None):
    from tests import mirror as M
    idx = M.read_index()["boards"]
    ids = sorted(idx, key=lambda b: (idx[b]["pages"], b))
    if only:
        ids = [b for b in ids if re.search(only, b)]
    os.makedirs(out_dir, exist_ok=True)

    def one(bid):
        out = os.path.join(out_dir, bid.replace("/", "_") + ".jsonl.gz")
        if os.path.exists(out + ".status.json"):
            return bid, "done-before"
        t0 = time.time()
        try:
            subprocess.run([sys.executable, os.path.abspath(__file__), "replay-board", bid, "--out", out], timeout=timeout, check=False,
                           stdout=subprocess.DEVNULL, stderr=open(out + ".stderr", "w"))
        except subprocess.TimeoutExpired:
            json.dump({"board_id": bid, "result": "truncated", "error": f"budget of {timeout} s hit, replay stopped", "seconds": round(time.time() - t0, 1),
                       "pages": idx[bid]["pages"]}, open(out + ".status.json", "w"))
            return bid, "truncated"
        if not os.path.exists(out + ".status.json"):
            json.dump({"board_id": bid, "result": "error", "error": "subprocess ended without a status (killed?)", "seconds": round(time.time() - t0, 1)},
                      open(out + ".status.json", "w"))
        return bid, json.load(open(out + ".status.json")).get("result")

    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        for bid, res in ex.map(one, ids):
            print(bid, res, flush=True)


# ---------------------------------------------------------------------------------------------------------------------------------
# DB reads
# ---------------------------------------------------------------------------------------------------------------------------------
def pull(out_dir):
    from app import config as A
    os.makedirs(out_dir, exist_ok=True)
    for name, table, page in (("clinics", "clinics", 1000), ("postings", "postings", 500), ("observations", "posting_observations", 300)):
        rows = A.rest_get_all(table, {"select": "*", "order": {"clinics": "clinic_id", "postings": "posting_id", "observations": "observation_id"}[name]}, page=page)
        with gzip.open(os.path.join(out_dir, name + ".jsonl.gz"), "wt", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(name, len(rows))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("replay-board")
    a.add_argument("board_id")
    a.add_argument("--out", required=True)
    b = sub.add_parser("replay-all")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--timeout", type=int, required=True, help="seconds per board; a board that hits it is recorded as truncated")
    b.add_argument("--workers", type=int, default=2)
    b.add_argument("--only", help="regex on the board id")
    c = sub.add_parser("pull")
    c.add_argument("--out-dir", required=True)
    d = sub.add_parser("analyze")
    d.add_argument("--data", required=True)
    d.add_argument("--replay", required=True)
    d.add_argument("--geonames", required=True)
    d.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "replay-board":
        replay_board(args.board_id, args.out)
    elif args.cmd == "replay-all":
        replay_all(args.out_dir, args.timeout, args.workers, args.only)
    elif args.cmd == "pull":
        pull(args.out_dir)
    elif args.cmd == "analyze":
        from tools import place_measure
        place_measure.run(args.data, args.replay, args.geonames, args.out)


if __name__ == "__main__":
    main()
