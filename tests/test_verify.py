from pflege_jobs.verify import _title_tokens, GONE_MARKERS
def test_title_tokens_skip_generic():
    assert _title_tokens("Pflegefachkraft (m/w/d) Intensivstation Nürnberg") == ["intensivstation", "nürnberg"]
def test_gone_marker():
    assert GONE_MARKERS.search("Diese Stelle ist leider nicht mehr verfügbar.")

def test_bavaria_plz_ranges():
    from pflege_jobs.sources.career_crawl import in_bavaria
    assert in_bavaria(None, "88709", None, set()) is False      # Meersburg (BW)
    assert in_bavaria(None, "89073", None, set()) is False      # Ulm (BW)
    assert in_bavaria(None, "89231", None, set()) is True       # Neu-Ulm (BY)
    assert in_bavaria(None, "88131", None, set()) is True       # Lindau (BY)
    assert in_bavaria(None, "63739", None, set()) is True and in_bavaria(None, "97080", None, set()) is True

def test_bavaria_generic_prefix_and_region_codes():
    from pflege_jobs.sources.career_crawl import in_bavaria
    assert in_bavaria("Bad Oeynhausen", None, "NW", {"bad kissingen"}) is False
    assert in_bavaria("Bad Oeynhausen", None, None, {"bad kissingen", "bad"}) is None
    assert in_bavaria("Tutzing", None, "BY", set()) is True
