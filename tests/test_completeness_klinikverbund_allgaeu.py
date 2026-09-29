"""TASK-144: karriere.klinikverbund-allgaeu.de and (historically) karriere-im.klinikverbund-allgaeu.de
are JS-rendered SPAs -- the detail page's raw HTML has no real <h1> and no JobPosting JSON-LD, so
career_crawl.Crawler._heuristic() falls back to `anchor` (the listing card's own link text). This
board's card markup puts title + employer + city + start date + employment type in ONE <a>, each on
its own line after _strip()'s <br>/</p>/</div> -> "\\n" conversion -- confirmed live 2026-09-23/24 on
https://karriere.klinikverbund-allgaeu.de/karriere-detail/Kempten/.../1581. Storing that whole blob as
the title (instead of just its first line) is what corrupted these postings' titles. Offline, no
network -- a trimmed copy of the real detail page's shape.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources.career_crawl import Crawler  # noqa: E402

SEED = {"name": "Klinikum Kempten", "kez": "76301", "career": "https://karriere.klinikverbund-allgaeu.de/", "town": "Kempten"}

# Real shape: no <h1>, no JobPosting JSON-LD, generic <title>, but the page has "bewerben" (the
# _heuristic() gate) via its own application button.
DETAIL_JS_SPA = """
<html><head><title>Karriere Detail - Klinikverbund Allgäu</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite","name":"Klinikverbund Allgäu"}</script>
</head><body><div id="app"></div><button>Jetzt bewerben</button></body></html>
"""

MULTI_LINE_ANCHOR = ("Pflegefachkraft (m/w/d) für unsere neonatologische Intensivstation\n"
                     "Klinikverbund Allgäu gGmbH\nKempten\nab sofort\nVollzeit; Teilzeit")


def test_heuristic_takes_only_the_first_line_of_a_multi_field_card_anchor():
    cr = Crawler(towns={"kempten"})
    row = cr._heuristic(DETAIL_JS_SPA, "https://karriere.klinikverbund-allgaeu.de/karriere-detail/Kempten/x/1581", SEED, anchor=MULTI_LINE_ANCHOR)
    assert row["title"] == "Pflegefachkraft (m/w/d) für unsere neonatologische Intensivstation"
    assert "gGmbH" not in row["title"] and "Vollzeit" not in row["title"]


def test_heuristic_still_uses_a_plain_single_line_anchor_unchanged():
    # Regression pin: the common case (anchor is already just the title) must not be touched by the
    # multi-line split -- str.split("\n", 1)[0] on a string with no newline returns it unchanged.
    cr = Crawler(towns={"kempten"})
    row = cr._heuristic(DETAIL_JS_SPA, "https://karriere.klinikverbund-allgaeu.de/karriere-detail/Kempten/x/1581", SEED,
                         anchor="Pflegefachkraft (m/w/d) für die Endoskopie")
    assert row["title"] == "Pflegefachkraft (m/w/d) für die Endoskopie"


def test_heuristic_without_an_anchor_at_all_still_falls_back_to_title_tag():
    # No anchor, no <h1>, no job-shaped signal anywhere -- falls back to the generic <title> text (an
    # existing behaviour this fix must not change; the generic title still won't look like a real job,
    # but that is a pre-existing, separate limitation of a boardless-anchor detail fetch).
    cr = Crawler(towns={"kempten"})
    row = cr._heuristic(DETAIL_JS_SPA, "https://karriere.klinikverbund-allgaeu.de/karriere-detail/Kempten/x/1581", SEED)
    assert row["title"] == "Karriere Detail - Klinikverbund Allgäu"
