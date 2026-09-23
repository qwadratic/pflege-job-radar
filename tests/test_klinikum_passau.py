"""Klinikum Passau's bespoke listing-page parser (pflege_jobs/sources/klinikum_passau.py).

TASK-73 AC12: _parse used to bound each posting's own markup with a fixed html[start:start+8000]
slice ("one posting's own markup never runs longer than this") instead of the next posting's own
<li class="job_<id>"> -- already false live 2026-09-18 (3 of 17 postings exceeded it), and any
posting missing its optional vacancy-from/vacancy-file tags (its own docstring: both optional) had
no terminator of its own for DESC_RX to stop at, so the window let its description run forward into
the next posting's own header/date instead.
"""
import os
import sys
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources import klinikum_passau as kp  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "board_samples" / "klinikum_passau_offene_stellen_sample.html"


def test_parse_does_not_bleed_a_posting_without_its_own_terminator_into_the_next_one():
    # job_1 has neither an optional "vacancy-from" date nor a PDF attachment -- DESC_RX has no
    # terminator of its own to stop at, so the pre-fix fixed window let it run into job_2's markup.
    html = (
        '<h2 class="job_group_headline">Pflege</h2><ul class="job_group">'
        '<li class="job_1"><span class="vacancy-header">Pflegefachkraft A'
        '<a class="vacancy-externalurl" href="https://x/1">Copy to Clipboard</a></span>'
        '<div class="vacancy-description">' + "A" * 500 + '</div></li>'
        '<li class="job_2"><span class="vacancy-header">Pflegefachkraft B'
        '<a class="vacancy-externalurl" href="https://x/2">Copy to Clipboard</a></span>'
        '<div class="vacancy-description">Kurzbeschreibung B</div>'
        '<span class="vacancy-from">Stelle frei ab:</span>01.03.2026</li></ul>')
    jobs = kp._parse(html, "https://klinikum-passau.de/karriere")
    by_id = {j["jobid"]: j for j in jobs}
    assert by_id["1"]["title"] == "Pflegefachkraft A"
    assert not by_id["1"]["description"] or "Pflegefachkraft B" not in by_id["1"]["description"]
    assert by_id["2"]["title"] == "Pflegefachkraft B"
    assert by_id["2"]["valid_from"] == "2026-03-01"


def test_parse_captures_a_description_longer_than_the_old_8000_char_window():
    long_desc = "Beschreibung. " * 700   # ~9800 chars, past the old fixed window
    html = (
        '<li class="job_9"><span class="vacancy-header">Pflegefachkraft C'
        '<a class="vacancy-externalurl" href="https://x/9">Copy to Clipboard</a></span>'
        '<div class="vacancy-description">' + long_desc + '</div>'
        '<span class="vacancy-from">Stelle frei ab:</span>15.04.2026</li>')
    jobs = kp._parse(html, "https://klinikum-passau.de/karriere")
    assert len(jobs) == 1
    assert jobs[0]["description"] and jobs[0]["description"].startswith("Beschreibung.")
    assert len(jobs[0]["description"]) > 8000
    assert jobs[0]["valid_from"] == "2026-04-15"


def test_parse_still_bounds_the_last_posting_by_end_of_page():
    html = ('<li class="job_1"><span class="vacancy-header">Pflegefachkraft A'
            '<a class="vacancy-externalurl" href="https://x/1">Copy to Clipboard</a></span>'
            '<div class="vacancy-description">Kurz</div></li>')
    jobs = kp._parse(html, "https://klinikum-passau.de/karriere")
    assert len(jobs) == 1 and jobs[0]["title"] == "Pflegefachkraft A"


def test_parse_on_a_real_saved_page_reads_job_count_title_department_and_permalink():
    # tests/fixtures/board_samples/klinikum_passau_offene_stellen_sample.html: a real contiguous
    # slice of the live board (fetched 2026-09-18) covering 3 postings across 2 department groups,
    # including job_2685 -- the >8000-char posting with neither an optional vacancy-from nor a
    # vacancy-file tag ahead of it, the exact shape that used to bleed into the next posting.
    html = FIXTURE.read_text(encoding="utf-8")
    jobs = kp._parse(html, "https://www.klinikum-passau.de/beruf-karriere/offene-stellen")
    assert len(jobs) == 3
    by_id = {j["jobid"]: j for j in jobs}
    assert by_id["2763"]["title"] == ("Examinierte Gesundheits- und Krankenpfleger für die "
                                       "Dialyseabteilung (m/w/d) in Voll- und Teilzeit")
    assert by_id["2763"]["department"] == "Pflege"
    assert by_id["2763"]["url"] == "https://www.klinikum-passau.de/jobs/?jobid=2763&type=2500"
    assert by_id["2685"]["department"] == "Pflege"
    assert "Praktikumsplätze" not in (by_id["2683"]["description"] or "")  # no bleed from job_2685
    assert by_id["2683"]["department"] == "Medizinisch-technischer Dienst"
    assert by_id["2683"]["url"] == "https://www.klinikum-passau.de/jobs/?jobid=2683&type=2500"


def test_is_klinikum_passau_fingerprints_the_real_saved_page():
    resp = type("R", (), {"ok": True, "text": FIXTURE.read_text(encoding="utf-8")})()
    assert kp.is_klinikum_passau(resp)
