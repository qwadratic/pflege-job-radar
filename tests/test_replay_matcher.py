"""tools/replay_matcher.py (TASK-185): the replay is the real cmd_inbox over a copy of a run's raw rows, offline, and the
change set it proposes loads in tools/apply_posting_changes.py. Synthetic rows and registry; the PostgREST reads (clinics,
postings, posting_observations, and the rows the nightly link stage matches) are stubbed, the local queue is a temp file."""
import json
import sys
import types

import pytest

from pflege_jobs import config as C, inbox_db as IB
from tools import replay_matcher as RM
from tools.apply_posting_changes import load_changes

ALPHA = {"clinic_id": "1", "name": "Alpha Klinik", "town": "Alphastadt", "operator": None, "beds": 100}
BETA = {"clinic_id": "2", "name": "Beta Klinik", "town": "Betastadt", "operator": None, "beds": 50}
SRC = C.SOURCES["employer_ats"]["source_id"]
BOARD = "https://karriere.alpha.example/stellen"


def row(slug, title, city, stamped, description="", org="Alpha Klinik"):
    """A wp_jobs raw row of Alpha Klinik's board; stamped: its place and employer are the seed clinic's own copy."""
    url = f"{BOARD}/{slug}"
    payload = {"title": title, "org": org, "loc": [{"city": city, "plz": None, "region": None}], "url": url, "page": url,
               "description": description, "board_url": BOARD, "board_clinic_ids": ["1"]}
    if stamped:
        payload.update(org_source="seed", city_source="seed")
    return {"kind": "jobposting", "collector": "vendor-wp_jobs-v1", "source_host": "karriere.alpha.example", "source_url": url, "payload": payload}


def posting(pid, slug, clinic_id, rule, status="open"):
    return {"posting_id": pid, "title": slug, "city": "Alphastadt", "status": status, "external_url": f"{BOARD}/{slug}",
            "clinic_id": clinic_id, "clinic_match_rule": rule}


@pytest.fixture
def replay(tmp_path, monkeypatch):
    queue = str(tmp_path / "inbox.sqlite")
    IB.enqueue([row("own", "Pflegefachkraft (m/w/d)", "Alphastadt", stamped=False),
                row("stamped", "Pflegefachkraft (m/w/d) Intensiv", "Alphastadt", stamped=True),
                row("manual", "Pflegefachkraft (m/w/d) Nacht", "Alphastadt", stamped=True),
                row("closed", "Pflegefachkraft (m/w/d) Tag", "Alphastadt", stamped=True),
                row("elsewhere", "Pflegefachkraft (m/w/d) Gamma", "Gammastadt", stamped=False, org="Gamma Haus"),
                row("stale", "Pflegefachkraft (m/w/d) Delta", "Gammastadt", stamped=False, org="Gamma Haus"),
                row("wrong", "Pflegefachkraft (m/w/d) Station", "Alphastadt", stamped=False)], run_id=7, path=queue)
    postings = [posting(101, "own", "1", "R0_board"),                         # evidence stands: unchanged
                posting(102, "stamped", "1", "R0_board"),                     # the board names Gammastadt: no evidence -> unlink
                posting(103, "manual", "1", "manual"),                        # a hand-made link is never touched
                posting(104, "closed", "1", "R0_board", status="expired"),    # not open: counted, not proposed
                posting(105, "elsewhere", "1", "R0_board"),                   # names Gammastadt, no clinic there -> unlink
                posting(106, "wrong", "2", "R3_tokens"),                      # names Alphastadt: relink to Alpha Klinik
                posting(107, "gone", "1", "R0_board"),                        # in no run: not replayed
                posting(108, "stale", "1", "R0_board"),                       # the page names Gammastadt, the stored employer is still Alpha's
                posting(109, "lagging-alphastadt", "2", "R3_tokens"),         # in no run; the stored employer is Alpha's, so only the link stage reaches Alpha
                                                                              # (and its title names Alphastadt: a word is no reason to propose what a stage reaches)
                posting(110, "orphan", "2", "R3_tokens")]                     # in no run, and the stored employer is a stranger's: no stage matches it
    observations = [{"observation_id": i, "posting_id": p["posting_id"], "source_id": SRC, "source_ref": p["external_url"]}
                    for i, p in enumerate(postings, 1) if p["posting_id"] not in (107, 109, 110)]
    tables = {"clinics": [ALPHA, BETA], "postings": postings, "posting_observations": observations}
    monkeypatch.setattr(RM, "get_all", lambda table, select, order: tables[table])
    # what the link stage reads: the employer and city stored with each posting (the stamps come from the replayed payloads)
    stored_as = {105: ("Gamma Haus", "Gammastadt"), 110: ("Gamma Haus", "Gammastadt")}

    def link_rows(rq, url, H):
        rows = []
        for p in postings:
            employer, city = stored_as.get(p["posting_id"], ("Alpha Klinik", "Alphastadt"))
            rows.append({"posting_id": p["posting_id"], "title": p["title"], "city": city, "employer": employer, "employer_class": "clinic",
                         "clinic_match_rule": p["clinic_match_rule"], "employer_inherited": False, "city_inherited": False})
        return rows
    monkeypatch.setattr(RM.cli, "link_rows", link_rows)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    def run(*extra, pins=False):
        files = {n: tmp_path / f"{n}.json" for n in ("set", "pipeline", "pins", "report")}
        argv = ["replay_matcher.py", "--runs", "7", "--inbox", queue, "--out", str(files["set"]), "--pipeline-out", str(files["pipeline"]),
                "--report", str(files["report"]), *extra]
        monkeypatch.setattr(sys, "argv", argv + (["--pins-out", str(files["pins"])] if pins else []))
        RM.main()
        read = lambda f: json.loads(files[f].read_text()) if files[f].exists() else None
        return types.SimpleNamespace(tool=read("set"), pipeline=read("pipeline"), pins=read("pins"), path=files["set"],
                                     report={r["posting_id"]: r for r in read("report")}, files=files)
    return run


def acts(changes):
    return {c["posting_id"]: (c["action"], c.get("clinic_id"), c.get("lock")) for c in changes}


def test_the_replay_proposes_the_changes_the_pipeline_would_make_to_the_stored_links(replay):
    r = replay()
    # 102, 105 and 106 are what the nightly drain makes by itself when it loads the postings again: they are not the tool's to make
    assert r.tool == []
    assert acts(r.pipeline) == {102: ("unlink", None, False), 105: ("unlink", None, False), 106: ("relink", "1", None)}
    assert {pid: x["change"] for pid, x in r.report.items()} == {
        101: "same", 102: "unlink", 103: "manual", 104: "unlink", 105: "unlink", 106: "relink", 107: "not_replayed", 108: "same", 109: "relink",
        110: "not_replayed"}
    why = r.pipeline[0]["_why"]
    assert why["code"] == "wrong_clinic" and why["task"] == "TASK-185" and why["evidence"][0].startswith(f"{BOARD}/stamped (replay of run 7")
    assert len(load_changes(str(r.files["pipeline"]), {"wrong_clinic"})) == 3         # the file format apply_posting_changes reads


def test_a_wrong_link_nothing_in_the_pipeline_repairs_is_the_tools_and_a_pin_only_when_asked_with_the_judges_reading(replay, tmp_path, capsys):
    verdict = lambda issue: {"issue": issue, "body_says": "names Gammastadt, contact Frau Beispiel, Tel. 0123 456789", "evidence": "Standort: Gammastadt"}
    judge = tmp_path / "judge.json"
    judge.write_text(json.dumps({"101": verdict("ok"), "107": verdict("site_mismatch"), "108": verdict("site_mismatch"),
                                 "109": verdict("site_mismatch"), "110": verdict("site_mismatch")}))
    # 107 is in no run and the link stage links it to its stored clinic, 108 is unmatched by the drain but linked again by the link stage,
    # 109 would be moved by the link stage to a clinic nothing in the posting names: a lock is the only thing that keeps the NULL.
    # 110 is in no run and no stage matches it: the NULL stays without one, and only the tool can clear it.
    r = replay("--verdicts", str(judge))
    assert acts(r.tool) == {110: ("unlink", None, False)}
    assert acts(r.pipeline) == {102: ("unlink", None, False), 105: ("unlink", None, False), 106: ("relink", "1", None)}
    assert r.pins is None and "pins (lock=true): 3 -> not written (pass --pins-out)" in capsys.readouterr().out
    assert "no nightly stage matches it" in r.tool[0]["_why"]["reason"]
    r = replay("--verdicts", str(judge), pins=True)
    by_id = {c["posting_id"]: c for c in r.pins}
    assert acts(r.pins) == {107: ("unlink", None, True), 108: ("unlink", None, True), 109: ("unlink", None, True)}
    assert acts(r.tool) == {110: ("unlink", None, False)}
    assert "no replayed run" in by_id[107]["_why"]["reason"] and "link stage" in by_id[108]["_why"]["reason"] and "link stage" in by_id[109]["_why"]["reason"]
    assert "the judge read: names Gammastadt, contact [contact removed], [contact removed]" in by_id[107]["_why"]["evidence"][0]   # the file is public
    assert len(load_changes(str(r.files["pins"]), {"wrong_clinic"})) == 3 and len(load_changes(str(r.path), {"wrong_clinic"})) == 1


def test_an_overlay_replaces_the_rows_of_the_boards_it_holds_and_the_replay_shows_the_state_after_that_crawl(replay, tmp_path, capsys):
    # A newer crawl of Alpha's board (the adapter now reads the place from the page: no stamp) lists two of its postings. The
    # stamped one, unlinked on the old rows, is attached on the new one; "own" and "stale" are no longer listed, so no run judges them,
    # not even the older run 6 that still holds an old row of "own".
    IB.enqueue([row("own", "Pflegefachkraft (m/w/d)", "Alphastadt", stamped=False)], run_id=6, path=str(tmp_path / "inbox.sqlite"))
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps([row("stamped", "Pflegefachkraft (m/w/d) Intensiv", "Alphastadt", stamped=False),
                                   row("wrong", "Pflegefachkraft (m/w/d) Station", "Alphastadt", stamped=False)]))
    r = replay("--overlay", str(overlay))
    assert "overlay: 2 rows of 1 boards replace those boards' rows in run 7" in capsys.readouterr().out
    assert {pid: x["change"] for pid, x in r.report.items() if pid in (101, 102, 106, 108)} == {
        101: "not_replayed", 102: "same", 106: "relink", 108: "not_replayed"}
    assert r.tool == [] and acts(r.pipeline) == {106: ("relink", "1", None)}


def test_a_held_posting_is_left_out_of_every_change_file(replay):
    r = replay("--hold", "102")
    assert sorted(acts(r.pipeline)) == [105, 106] and r.tool == []


def test_names_quotes_the_text_around_the_word_that_names_the_site_and_not_its_sibling():
    harlaching, schwabing = {"name": "München Klinik Harlaching", "town": "München"}, {"name": "München Klinik Schwabing", "town": "München"}
    assert "Harlaching" in RM.names("Werden Sie Teil unserer Stationen in Harlaching.", harlaching, schwabing)
    assert RM.names("Werden Sie Teil unserer Stationen in Harlaching.", schwabing, harlaching) is None


def test_names_quotes_the_town_before_a_word_of_the_name_that_any_ad_for_the_specialty_contains():
    kinderklinik = {"name": "Alpenklinik Santa Maria für Kinder und Jugendliche", "town": "Bad Hindelang"}
    text = "Wir suchen für Kinder und Jugendliche eine Pflegefachkraft (m/w/d) für unsere Station mit Schichtdienst und guter Bezahlung in Vollzeit. Arbeitsort ist Bad Hindelang."
    assert "Hindelang" in RM.names(text, kinderklinik, {})


def test_names_reads_a_three_letter_word_that_tells_two_sites_apart():
    sued, nord = {"name": "Klinikum Nürnberg - Betriebsstätte Süd", "town": "Nürnberg"}, {"name": "Klinikum Nürnberg - Betriebsstätte Nord", "town": "Nürnberg"}
    assert "Campus Süd" in RM.names("Standort: Klinikum Nürnberg | Campus Süd Arbeitszeitmodell: Voll- oder Teilzeit", sued, nord)
    assert RM.names("Standort: Klinikum Nürnberg | Campus Süd Arbeitszeitmodell: Voll- oder Teilzeit", nord, sued) is None


def test_a_quote_carries_no_e_mail_address_phone_number_or_named_contact():
    q = RM.names("Stelle in Alphastadt, Mobil +49 961 123456, 0961 654321, Frau Beispiel (beispiel@alpha.example, Tel. 0961 111222)", ALPHA, BETA)
    assert "Alphastadt" in q and not any(x in q for x in ("Beispiel", "@", "123456", "654321", "111222"))


def test_newest_runs_finds_the_latest_other_run_that_holds_a_row_of_the_url(tmp_path):
    queue = str(tmp_path / "inbox.sqlite")
    for run in (5, 6, 7):
        IB.enqueue([row("a", "A", "Alphastadt", False)], run_id=run, path=queue)
    IB.enqueue([row("b", "B", "Alphastadt", False)], run_id=4, path=queue)
    assert RM.newest_runs(queue, [f"{BOARD}/a", f"{BOARD}/b", f"{BOARD}/none"], skip=[7]) == {6: {"karriere.alpha.example"}, 4: {"karriere.alpha.example"}}
