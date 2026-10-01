"""Replay the Matcher over the raw rows of past nightly runs and propose the stored-link changes it implies
(TASK-185). Reads only: nothing is written to the database or to the queue.

The raw rows come from the local queue (data/inbox.sqlite, opened mode=ro and copied run by run into a temp
queue); the registry, the stored postings and their observation keys come from PostgREST GETs with the anon key
(clinics, postings, posting_observations: ordered, paged, count-checked). Each run is drained by the real
pflege_jobs.cli.cmd_inbox with its sinks and lookups stubbed -- the pools, the board pre-pass, the matching, the
copy-conflict handling and the clearing of unmatched links of the nightly drain -- and the links it would push
are compared with the stored ones. The nightly `link` stage (cmd_link_clinics) follows the drain and pushes every
content match it finds, whatever the drain decided: it is replayed over the stored postings, with the stamps the
replayed rows write, through the same cli.link_rows. A network call from inside a drain raises.

  set -a; source .env; set +a
  .venv/bin/python tools/replay_matcher.py --runs 225 --out data/relink_task185_set.json --report /tmp/replay.json \
      --pipeline-out /tmp/pipeline.json --pins-out data/pins_task185_set.json --verdicts verdicts.json

--runs, newest first: a posting is judged by the first run that observed it (a board the last run did not reach is
judged by an earlier one). --report: every posting with its stored and replayed link. --verdicts: an optional judge
file ({posting_id: {issue, body_says, evidence}}) whose reading is quoted as the evidence of a posting it judged a
site mismatch. --hold: posting ids whose proposed change a reviewer judged wrong; they are left out of every change
file and listed. --overlay: the rows a newer crawl returned for some boards (say, recorded from changed adapters), in
the raw-row format of the queue: they take the place of those boards' rows in the newest run, and the older runs lose
the boards' rows too, so the replay shows the state after that crawl -- a posting the new crawl no longer lists is
judged by no run (not_replayed), as it would be by the next nightly drain.

The changes of the OPEN postings go to three tools/apply_posting_changes.py files, for review; this tool never applies
them. Rows with clinic_match_rule 'manual' are skipped (the nightly code never touches them).
  --out           what only this tool can make, no lock:
                    unlink  the judge read a wrong site, and no run re-evaluates the posting (or its copies disagree, so
                            the drain pushes nothing) and no nightly stage matches it: nothing puts a clinic back
  --pipeline-out  what the fixed pipeline makes by itself the next time the posting is loaded -- not to be applied: it
                  only changes the timing, and a relink by hand writes rule 'manual':
                    unlink  the replayed pipeline (drain + link stage) leaves the posting without a clinic; no lock,
                            which would also keep the row from the right link once an adapter reads its own place
                    relink  the replayed pipeline attaches another clinic on the posting's own place or text; a clinic
                            only the link stage's stored employer and city name is not proposed
  --pins-out      unlink with lock=true (a pin; needs --verdicts): the judge read a wrong site and the final state still
                  carries a clinic nothing supports (the drain still attaches it, its copies disagree, or the link stage
                  links it from the stored employer and city): only 'manual' stops the nightly stages putting it back. A
                  pin also keeps the row from the right link once an adapter reads the posting's own place: replay the
                  state after the adapters ran (--overlay) and pin what still carries the wrong clinic
Apply the files only after the pipeline change is deployed: the old `link-clinics` stage re-creates the unlinked
R1_exact links from the seed name and town the same night.
"""
import argparse
import collections
import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from pflege_jobs import cli, inbox_db as IB
from pflege_jobs.registry import link_postings, toks

TASK = "TASK-185"
# The change set is committed to a public repo: no e-mail address, phone number or named contact person of an ad goes into a quote.
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|(?:\+\d{2}|\b0\d)[\d /()-]{6,}\d|\b(?:Herr|Herrn|Frau|Dr\.|Prof\.)(?:\s+(?:Dr\.|Prof\.))?\s+[A-ZÄÖÜ][\wäöüß-]+|\bTel\.?\s*[\d+(][\d /()-]*")
scrub = lambda s: CONTACT.sub("[contact removed]", s)


def get_all(table, select, order):
    """Every row of a table, ordered, paged, and checked against PostgREST's exact count."""
    url = os.environ["SUPABASE_URL"]
    H = {"apikey": os.environ["SUPABASE_ANON_KEY"], "Accept-Profile": "pflege_jobs", "Prefer": "count=exact"}
    rows, off = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{table}", params={"select": select, "order": order, "limit": 1000, "offset": off},
                         headers=H, timeout=120)
        chunk = cli._rows(r, table)
        total = int(r.headers["content-range"].split("/")[1])
        rows += chunk
        off += len(chunk)
        if len(chunk) < 1000:
            break
    if len(rows) != total:
        raise SystemExit(f"{table}: read {len(rows)} rows, the database counts {total}")
    return rows


def board_key(row):
    """What board a raw row came from: its host and the registry clinics the crawler routed it for (its pool)."""
    p = row.get("payload") or {}
    return row.get("source_host"), tuple(sorted(str(c) for c in (p.get("board_clinic_ids") or p.get("_board") or ())))


def replay_run(run_id, inbox, clinics, hosts=(), overlay=(), add_overlay=False):
    """One run's raw rows (those of `hosts`, if given) through the real cmd_inbox, offline. -> {"run", "date",
    "raw_rows", "decisions"}; a decision, keyed "<source_id>|<source_ref>", is what the drain would push for that
    posting: clinic_id and rule (None: unmatched, the stored link would be cleared), or conflict=True (the copies
    disagree, nothing is pushed). overlay: rows a newer crawl returned for some boards; a board it holds (see
    board_key) loses its rows in this run, and add_overlay puts the overlay's rows in their place."""
    tmp = tempfile.mkdtemp(prefix=f"replay_{run_id}_")
    db = os.path.join(tmp, "queue.sqlite")
    src = sqlite3.connect(f"file:{inbox}?mode=ro", uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    raw = [{"kind": r["kind"], "collector": r["collector"], "client_id": r["client_id"], "source_host": r["source_host"],
            "source_url": r["source_url"], "payload": json.loads(r["payload"]) if r["payload"] else {}}
           for r in src.execute("select kind, collector, client_id, source_host, source_url, payload from inbox"
                                f" where run_id = ?{' and source_host in (%s)' % ','.join('?' * len(hosts)) if hosts else ''}"
                                " order by inbox_id", (run_id, *hosts))]
    date = src.execute("select min(received_at) from inbox where run_id = ?", (run_id,)).fetchone()[0]
    src.close()
    if not raw:
        raise SystemExit(f"run {run_id}: no rows in {inbox}")
    if overlay:
        replaced = {board_key(r) for r in overlay}
        raw = [r for r in raw if board_key(r) not in replaced] + (list(overlay) if add_overlay else [])
        if not raw:
            shutil.rmtree(tmp)
            return {"run": run_id, "date": (date or "")[:10], "raw_rows": 0, "decisions": {}}
    IB.enqueue(raw, run_id=run_id, path=db)

    seen, ids, pushed = {}, {}, {}

    class Sink:
        def __init__(self, *a, **kw): pass
        def write(self, obs, **kw):
            for o in obs:
                seen.setdefault((o["source_id"], o["source_ref"]), []).append(o)
            return {"observations": len(obs)}
        def write_clinics(self, rows, log=print): return len(rows)
        def _post(self, body):
            for l in body.get("clinic_links") or []:
                pushed[l["posting_id"]] = l
            return {}

    def lookup(get, url, H, obs, **kw):
        for o in obs:
            ids.setdefault((o["source_id"], o["source_ref"]), len(ids) + 1)
        return dict(ids)

    def offline(*a, **kw):
        raise RuntimeError("the replay makes no network call")

    import pflege_jobs.sources.inbox as SI
    SI.enrich_description = lambda desc: {}                             # only database columns come out of it; 80% of the cost
    cli._live_clinics = lambda url, H: clinics
    cli.EdgeSink = Sink
    cli.lookup_posting_ids = lookup
    cli.manual_posting_ids = lambda get, url, H, pids: set()
    cli.linked_posting_ids = lambda get, url, H, pids: set(pids)       # every unmatched posting is pushed, to be compared
    cli._drain_once = lambda *a, **kw: 0                                # the Postgres queue is not part of a run
    requests.get = offline
    with redirect_stdout(io.StringIO()):
        cli.cmd_inbox(argparse.Namespace(no_ack=False, max_batches=100_000, inbox_db=db, reprocess_run=None, reprocess_all=False))
    queue = sqlite3.connect(db)
    left = queue.execute("select count(*) from inbox where processed_at is null").fetchone()[0]
    if left:
        raise SystemExit(f"run {run_id}: {left} rows still unprocessed after the drain")
    notes = collections.defaultdict(set)
    for url, note in queue.execute("select source_url, process_note from inbox where process_note like 'loaded%'"):
        notes[url].add(note)
    queue.close()
    shutil.rmtree(tmp)

    decisions = {}
    for key, copies in seen.items():
        link = pushed.get(ids[key])
        decisions[f"{key[0]}|{key[1]}"] = {
            "clinic_id": link["clinic_id"] if link else None, "rule": link["clinic_match_rule"] if link else None,
            "conflict": link is None, "url": copies[0]["source_url"], "title": copies[0].get("title"),
            "description": copies[0].get("description"), "note": " | ".join(sorted(notes.get(copies[0]["source_url"], ()))),
            # what the stored payload carries after this run: the nightly link stage reads it back
            "stamps": [any(cli._marker(o, f) == "seed" for o in copies) for f in ("employer_source", "city_source")]}
    return {"run": run_id, "date": (date or "")[:10], "raw_rows": len(raw), "decisions": decisions}


def newest_runs(inbox, urls, skip):
    """{run_id: {source_host}}: for each url, the newest run outside `skip` that holds a raw row of it."""
    src = sqlite3.connect(f"file:{inbox}?mode=ro", uri=True, timeout=60)
    found = collections.defaultdict(set)
    for u in urls:
        hit = src.execute(f"select run_id, source_host from inbox where source_url = ? and run_id not in ({','.join('?' * len(skip))})"
                          " order by run_id desc limit 1", (u, *skip)).fetchone()
        if hit:
            found[hit[0]].add(hit[1])
    src.close()
    return found


def board(url):
    return urlparse(url or "").netloc


def names(text, clinic, other):
    """A quoted stretch of `text` around the first word that names `clinic` and not `other`: a word of its town, then of its
    name (longest first), the way the Matcher tokenizes them. None when the text has none."""
    words = lambda c: toks(f"{c.get('name') or ''} {c.get('town') or ''}")
    town = toks(clinic.get("town") or "")
    for w in sorted(words(clinic) - words(other), key=lambda w: (w not in town, -len(w))):
        m = re.search(rf"\b{re.escape(w)}\b", text or "", re.I)
        if m:
            return "..." + scrub(" ".join(text[max(0, m.start() - 60):m.end() + 60].split())) + "..."
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, nargs="+", required=True, help="run ids, newest first")
    ap.add_argument("--inbox", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inbox.sqlite"))
    ap.add_argument("--hosts", nargs="*", default=[], help="replay only the raw rows of these source_host values (default: all)")
    ap.add_argument("--out", help="write the changes only this tool can make (apply_posting_changes format) here")
    ap.add_argument("--pipeline-out", help="write the changes the nightly pipeline makes by itself on the postings' next load here (not to be applied)")
    ap.add_argument("--pins-out", help="write the unlink lock=true entries (rule 'manual': no nightly stage touches the row again) here")
    ap.add_argument("--report", help="write every posting with its stored and replayed link here")
    ap.add_argument("--verdicts", help="judge file: {posting_id: {issue, body_says, evidence}}")
    ap.add_argument("--hold", type=int, nargs="*", default=[], help="posting ids to leave out of every change file")
    ap.add_argument("--overlay", help="JSON list of raw rows (kind, collector, client_id, source_host, source_url, payload) a newer crawl returned "
                                      "for some boards: in the first run they replace every row of the boards they hold, and the older runs lose those "
                                      "boards' rows too (a posting the new crawl no longer lists is not re-evaluated by any run)")
    a = ap.parse_args()

    clinics = get_all("clinics", "*", "clinic_id.asc")
    by_id = {str(c["clinic_id"]): c for c in clinics}
    postings = get_all("postings", "posting_id,title,city,status,external_url,clinic_id,clinic_match_rule", "posting_id.asc")
    keys = collections.defaultdict(set)
    for o in get_all("posting_observations", "observation_id,posting_id,source_id,source_ref", "observation_id.asc"):
        if o["posting_id"]:
            keys[o["posting_id"]].add(f"{o['source_id']}|{o['source_ref']}")
    verdicts = json.load(open(a.verdicts, encoding="utf-8")) if a.verdicts else {}

    overlay = json.load(open(a.overlay, encoding="utf-8")) if a.overlay else []

    def replay(run_ids, hosts, newest=False):
        n = len(run_ids)
        with ProcessPoolExecutor(max_workers=min(n, os.cpu_count() or 1)) as pool:
            return list(pool.map(replay_run, run_ids, [a.inbox] * n, [clinics] * n, hosts, [overlay] * n, [newest and i == 0 for i in range(n)]))

    runs = replay(a.runs, [a.hosts] * len(a.runs), newest=True)
    if overlay:
        print(f"overlay: {len(overlay)} rows of {len({board_key(r) for r in overlay})} boards replace those boards' rows in run {a.runs[0]}")
    covered = lambda p: any(k in r["decisions"] for r in runs for k in keys[p["posting_id"]])
    open_urls = [p["external_url"] for p in postings if p["status"] == "open" and p["external_url"] and not covered(p)]
    older = newest_runs(a.inbox, open_urls, a.runs) if not a.hosts else {}
    if older:                       # a board the given runs did not reach: the newest earlier run that has a row of it, board rows only
        extra = sorted(older, reverse=True)
        runs = sorted(runs + replay(extra, [tuple(sorted(older[r])) for r in extra]), key=lambda r: -r["run"])
    for r in runs:
        print(f"run {r['run']} ({r['date']}): {r['raw_rows']} raw rows, {len(r['decisions'])} loaded postings")

    # The nightly `link` stage (cli.cmd_link_clinics) runs after the drain and pushes its own content match for every
    # posting it finds one for: it reads the stored employer and city and the stamps the stored payloads carry, which for
    # a replayed posting are the ones this run's drain just wrote.
    stage = {r["posting_id"]: r for r in cli.link_rows(requests, os.environ["SUPABASE_URL"],
                                                       {"apikey": os.environ["SUPABASE_ANON_KEY"], "Accept-Profile": "pflege_jobs"})}
    decided = {}
    for p in postings:
        run = next((r for r in runs if any(k in r["decisions"] for k in keys[p["posting_id"]])), None)
        ds = [run["decisions"][k] for k in sorted(keys[p["posting_id"]]) if run and k in run["decisions"]]
        decided[p["posting_id"]] = run, ds
        if ds and p["posting_id"] in stage:
            stage[p["posting_id"]].update(employer_inherited=any(d["stamps"][0] for d in ds), city_inherited=any(d["stamps"][1] for d in ds))
    linked = {l["posting_id"]: l for l in link_postings([r for r in stage.values() if r["clinic_match_rule"] != "manual"], [dict(c) for c in clinics])}

    report, proposed, count = [], [], collections.Counter()
    for p in postings:
        stored = p["clinic_id"]
        run, ds = decided[p["posting_id"]]
        conflict = bool(run) and (any(d["conflict"] for d in ds) or len({d["clinic_id"] for d in ds}) > 1)   # the drain pushes nothing: the stored link stays
        inbox = ds[0] if run and not conflict else {}
        lm = linked.get(p["posting_id"])
        new = {**inbox, "inbox_clinic": inbox.get("clinic_id"), "inbox_rule": inbox.get("rule"), "conflict": conflict}
        if lm:
            new.update(clinic_id=lm["clinic_id"], rule=lm["clinic_match_rule"], note=(inbox.get("note") or "")
                       + ("" if lm["clinic_id"] == inbox.get("clinic_id") else f" | link stage: {lm['clinic_match_rule']} -> {lm['clinic_id']}"))
        if p["clinic_match_rule"] == "manual":
            kind = "manual"
        elif inbox or (lm and lm["clinic_id"] != stored):
            kind = "same" if new["clinic_id"] == stored else "unlink" if new["clinic_id"] is None else "link" if stored is None else "relink"
        else:
            kind = "conflict" if conflict else "not_replayed"
        count[(p["status"], kind)] += 1
        # What the set does about it. The fixed pipeline reproduces an unmatched posting by itself (unlink, no lock: a lock
        # would also keep the row from the right link once an adapter reads the posting's own place) and a relink the text or
        # the place supports. Where the judge read a wrong site and the final state still carries a clinic nothing supports
        # -- no run re-evaluates the row, its copies disagree, the drain still attaches it, or the link stage links it from
        # the stored employer and city -- the link is cleared: with lock=true (pin) when a nightly stage would put a clinic
        # back, with lock=false when none does.
        action, wrong = None, bool((verdicts.get(str(p["posting_id"])) or {}).get("issue") == "site_mismatch")
        old, to = by_id.get(str(stored)) or {}, by_id.get(str(new.get("clinic_id"))) or {}
        quote = names(f"{p['title']} {new.get('description') or ''}", to, old) if to else None
        via_stage = "link stage" in (new.get("note") or "")
        holds = bool(inbox.get("clinic_id") or lm)          # a nightly stage puts a clinic on the posting again
        if p["status"] == "open" and kind != "manual":
            if kind == "unlink": action = "unlink"
            elif kind in ("relink", "link") and not via_stage: action = "relink"
            elif wrong and stored and (kind in ("same", "conflict", "not_replayed") or via_stage): action = "pin" if holds else "unlink"
        count[("action", action)] += 1
        report.append({"posting_id": p["posting_id"], "status": p["status"], "board": board(p["external_url"]), "title": p["title"],
                       "url": p["external_url"], "stored_clinic": stored, "stored_rule": p["clinic_match_rule"], "change": kind,
                       "action": action, "run": run and run["run"], "inbox_clinic": inbox.get("clinic_id"), "new_clinic": new.get("clinic_id"),
                       "new_rule": new.get("rule"), "note": new.get("note")})
        if action:
            proposed.append((p, run, new, action, kind, quote))

    for status in sorted({s for s, _ in count if s != "action"}):
        print(f"{status} postings:", dict(sorted((k, n) for (s, k), n in count.items() if s == status)))
    print("proposed actions on open postings:", dict(sorted((k or "-", n) for (s, k), n in count.items() if s == "action")))
    # Who makes the change: the fixed pipeline unmatches a posting or attaches the clinic its place or text names the next time the
    # posting is loaded again ("pipeline": applying it would only change the timing, and a relink by hand would write rule 'manual'),
    # a pin is the lock that keeps a nightly stage from putting the clinic back ("pin"), the rest only this tool can do ("tool").
    who = lambda action, kind: "pin" if action == "pin" else "pipeline" if action == "relink" or kind == "unlink" else "tool"
    print("proposed changes by board:")
    by_board = collections.Counter((board(p["external_url"]), who(action, kind), action) for p, _, _, action, kind, _ in proposed)
    for (b, w, k), n in sorted(by_board.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:>4} {w:8} {k:6} {b}")
    missing = collections.Counter(r["board"] for r in report if r["status"] == "open" and r["change"] == "not_replayed")
    print(f"open postings with no raw row in any run, not replayed: {sum(missing.values())}", dict(missing.most_common(8)))

    if a.report:
        json.dump(report, open(a.report, "w", encoding="utf-8"), ensure_ascii=False)
    if not (a.out or a.pipeline_out or a.pins_out):
        print(f"{len(proposed)} proposed changes (pass --out, --pipeline-out or --pins-out to write them)")
        return
    held = [p["posting_id"] for p, *_ in proposed if p["posting_id"] in a.hold]
    sets = {"tool": [], "pipeline": [], "pin": []}
    for p, run, new, action, kind, quote in proposed:
        if p["posting_id"] in a.hold:
            continue
        old, v = by_id.get(str(p["clinic_id"])) or {}, verdicts.get(str(p["posting_id"]))
        judged = f"the judge read: {scrub(v['body_says'])}" if v and v.get("issue") == "site_mismatch" else None
        filed = f"Filed under {old.get('name')} ({p['clinic_id']}) by {p['clinic_match_rule']}; " if p["clinic_id"] else "Filed under no clinic; "
        if action == "unlink":
            act = {"action": "unlink", "lock": False}
            reason = filed + ("the replayed pipeline finds no evidence that the posting belongs to any registry clinic." if kind == "unlink" else
                              "the judge read the text and it names another site; "
                              + ("its copies disagree, so the drain pushes nothing, and no nightly stage matches it." if run else
                                 "no replayed run re-evaluates the posting and no nightly stage matches it."))
            says = judged or f"no clinic evidence: {new['note'] or 'the replayed rules find none'}"
        elif action == "relink":
            to = by_id[new["clinic_id"]]
            act = {"action": "relink", "clinic_id": new["clinic_id"]}
            reason = filed + f"the replayed pipeline attaches {to.get('name')} ({new['clinic_id']}) by {new['rule']}."
            says = judged or f"names {to.get('name')}, {to.get('town')}" + (f": {quote}" if quote else f" (rule {new['rule']})")
        else:
            act = {"action": "unlink", "lock": True}
            why = ["no replayed run holds a row of it, so the drain never re-evaluates it" if not run else
                   "its copies disagree, so the drain pushes nothing" if new["conflict"] else
                   f"the replayed drain still attaches it to {new['inbox_clinic']} by {new['inbox_rule']}" if new["inbox_clinic"] else None,
                   f"the nightly link stage matches the stored employer and city and links it to {new['clinic_id']} by {new['rule']}"
                   if "link stage" in (new.get("note") or "") else None]
            reason = filed + "the judge read the text and it names another site, and nothing in the fixed pipeline puts that right: " \
                             + "; ".join(w for w in why if w) + ". Pinned to no clinic so the nightly stages leave it; " \
                             "relink by hand once the posting's own place is read."
            says = judged
        where = f"replay of run {run['run']}, {run['date']}" if run else f"stored posting, in no replayed run; {datetime.date.today()}"
        sets[who(action, kind)].append({"posting_id": p["posting_id"], **act,
                                        "_why": {"code": "wrong_clinic", "reason": reason, "task": TASK, "evidence": [f"{p['external_url']} ({where}): {says}"]}})
    for name, path in (("tool", a.out), ("pipeline", a.pipeline_out), ("pin", a.pins_out)):
        if path:
            json.dump(sets[name], open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"changes only this tool can make: {len(sets['tool'])} -> {a.out or 'not written (pass --out)'}; "
          f"made by the nightly pipeline itself: {len(sets['pipeline'])} -> {a.pipeline_out or 'not written (pass --pipeline-out)'}; "
          f"pins (lock=true): {len(sets['pin'])} -> {a.pins_out or 'not written (pass --pins-out)'}; held back by --hold: {held}")


if __name__ == "__main__":
    main()
