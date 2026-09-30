"""pflege_jobs.sources.krankenhausplan._split_name_block / validate() -- TASK-136.

Cells are frozen real text from data/registry/krankenhausplan_2026.pdf's own Standort/
Krankenhausträger table column (pdfplumber table extraction, page range 251-270, Teil II Abschnitt A),
captured 2026-09-23 while diagnosing TASK-131's 29 parse_quality='partial' rows. No PDF parsing at
test time: the cells are the fixture, same convention as this session's other frozen-sample tests.
"""
from pflege_jobs.sources import krankenhausplan as K

KNOWN_TOWNS = {
    "rosenheim", "bad reichenhall", "schönau am königssee", "münchen-flughafen", "münchen",
    "planegg", "brannenburg", "prien am chiemsee", "bernau am chiemsee", "oberaudorf", "feldafing",
    "neukirchen b hl. blut", "bad kötzting", "bad rodach", "bad steben", "wirsberg",
    "bad staffelstein", "ebensfeld", "bad windsheim", "alzenau", "bad kissingen",
    "bad neustadt a.d. saale", "ichenhausen", "stiefenhofen", "scheidegg im allgäu",
    "bad wörishofen", "oberstdorf", "rehau",
}


def _fixture(monkeypatch):
    monkeypatch.setattr(K, "KNOWN_TOWNS", set(KNOWN_TOWNS))


# Synthetic, not a frozen PDF cell: isolates the first-vs-last-match choice on its own, with two
# DISTINCT known towns so the existing "operator starts with the real town" self-correction (a few
# lines below the cut in _split_name_block) can't accidentally paper over a wrong first match --
# that self-correction only fires when the line right after the wrong cut is itself a known town,
# which most of the 17/27 real rows this audit found did not satisfy either. Real town names were
# avoided on purpose too: they must not end in "-burg"/"-stadt"/contain "AG" etc., or LEGAL_TAIL's
# own (correct, pre-existing) case-insensitive legal-suffix check excludes them as cut candidates.
def test_last_known_town_match_wins_over_an_earlier_one(monkeypatch):
    monkeypatch.setattr(K, "KNOWN_TOWNS", {"alphadorf", "betadorf"})
    cell = "Reha-Klinik\nAlphadorf\nSome Site Label\nBetadorf\nBetreiber GmbH & Co. KG"
    name, town, operator, quality = K._split_name_block(cell)
    assert name == "Reha-Klinik Alphadorf Some Site Label"
    assert town == "Betadorf"
    assert operator == "Betreiber GmbH & Co. KG"
    assert quality == "ok"


# A second, later block in _split_name_block re-derives `town` from whatever `name` line is left
# over any time len(name) >= 2 -- before TASK-136 it ran unconditionally and silently overwrote an
# already-correct town from the cut-based match above (here: "Neukirchen b Hl. Blut" -> clobbered
# back to "Neukirchen und Rötz", a fragment of the clinic's own name, not a town at all).
def test_the_town_recovery_fallback_does_not_clobber_an_already_found_town(monkeypatch):
    _fixture(monkeypatch)
    cell = "Spezialkliniken\nNeukirchen und Rötz\nNeukirchen b Hl. Blut\nSpezialklinik\nNeukirchen/Rötz\nGmbH & Co.KG"
    name, town, operator, quality = K._split_name_block(cell)
    assert town == "Neukirchen b Hl. Blut"
    assert name == "Spezialkliniken Neukirchen und Rötz"
    assert quality == "ok"


# The double-mention shape recurs even when the town is a SINGLE line repeated exactly, not a
# multi-word site label -- same underlying bug, different cell shape.
def test_double_mentioned_single_word_town_resolves_to_the_real_standort(monkeypatch):
    _fixture(monkeypatch)
    cell = "Benedictus\nKrankenhaus\nFeldafing\nFeldafing\nBenedictus\nKrankenhaus\nFeldafing GmbH &\nCo. KG"
    name, town, operator, quality = K._split_name_block(cell)
    assert town == "Feldafing"
    assert quality == "ok"


# A cell with NO known-town match anywhere still falls through to the pre-existing "no known town"
# heuristics untouched -- the TASK-136 changes must not affect this path at all.
def test_cell_with_no_known_town_still_falls_back_the_same_way_as_before(monkeypatch):
    monkeypatch.setattr(K, "KNOWN_TOWNS", set())
    cell = "Vital-Klinik\nAlzenau\nAlzenau\nVital-Klinik GmbH &\nCo.KG"
    name, town, operator, quality = K._split_name_block(cell)
    # cut stays None with no known town at all, so neither TASK-136 change (the last-match loop, the
    # town-is-None gate on the recovery block) is reached -- this exercises the pre-existing "no known
    # town" chain end to end and pins its result so a future change can't silently alter it.
    assert (name, town, operator, quality) == ("Vital-Klinik Alzenau", "Alzenau", "Vital-Klinik GmbH & Co.KG", "ok")


# --- validate(): error-rate report, independent of the per-row parse_quality flag -----------------
def test_validate_flags_missing_and_implausible_towns_and_non_ok_quality():
    rows = [
        {"clinic_id": "1", "town": "Feldafing", "parse_quality": "ok"},
        {"clinic_id": "2", "town": None, "parse_quality": "ok"},
        {"clinic_id": "3", "town": "Co. KG", "parse_quality": "ok"},
        {"clinic_id": "4", "town": "Feldafing", "parse_quality": "new_2026_unverified"},
    ]
    report = K.validate(rows)
    assert report["town_missing"] == ["2"]
    assert report["town_implausible"] == ["3"]
    assert report["parse_quality_partial"] == ["4"]
    assert report["error_rate"] == round(3 / 4, 3)


# A real German town ending in "-stadt" (Ingolstadt, Neustadt, Immenstadt, ...) must never be flagged
# implausible: the module's own LEGAL_TAIL is case-insensitive so "Stadt\b" matches the "stadt" tail
# of an ordinary town name -- validate() uses its own case-sensitive, whole-word check instead.
def test_validate_does_not_false_positive_on_town_names_ending_in_stadt():
    rows = [{"clinic_id": str(i), "town": t, "parse_quality": "ok"} for i, t in
            enumerate(["Ingolstadt", "Neustadt", "Immenstadt", "Höchstadt", "Neustadt a.d. Aisch"])]
    report = K.validate(rows)
    assert report["town_implausible"] == []
    assert report["error_rate"] == 0.0


# TASK-167: frozen "zugelassene Betten stationär zum 01.01.2026" cells from krankenhausplan_2026.pdf.
# The PDF prints counts >= 1000 with a German thousands dot; the old isdigit() check turned all 7 of
# them (LMU 2.062, Augsburg 1.699, Würzburg 1.523, Erlangen 1.462, Nürnberg Nord 1.276, TUM 1.176,
# Bogenhausen 1.020) into beds=None -- the biggest hospitals fell out of the per-bed metric.
def test_count_cells_read_the_german_thousands_separator():
    assert K._int("2.062") == 2062        # 16290 LMU, p.245
    assert K._int("1.020") == 1020        # 16205 München Klinik Bogenhausen, p.21
    assert K._int("911") == 911           # 46101 Klinikum Bamberg, p.114
    assert K._int("0") == 0               # day-clinic-only sites: 0 beds is the source value
    assert K._int("-") is None
    assert K._int("") is None


def test_count_cell_in_an_unknown_format_fails_loudly():
    import pytest
    for bad in ("1,020", "ca. 50", "10.20"):
        with pytest.raises(ValueError):
            K._int(bad)


# TASK-178: frozen first lines of krankenhausplan_2026.pdf table pages. p.15 opens Teil II Abschnitt A,
# so its Bezirk heading is line 2; reading line 1 only left bezirk=None and skipped the page, and with it
# 16101 Klinikum Ingolstadt (798 beds) and 16102 Privatklinik Dr. Maul.
def test_bezirk_heading_is_found_on_the_page_that_opens_the_part():
    p15 = "Teil II Abschnitt A - Plankrankenhäuser\nOberbayern\nLandkreis / KeZ Krankenhaus Status VSt."
    p16 = "Oberbayern\nLandkreis / KeZ Krankenhaus Status VSt. Träger-zugelassene"
    assert K._bezirk(p15) == "Oberbayern"
    assert K._bezirk(p16) == "Oberbayern"
    assert K._bezirk("Verzeichnis\nder Abkürzungen mit Erläuterungen\n1. KeZ = Kennzahl") is None
    assert K._bezirk("Teil II Abschnitt A\nLandkreis / KeZ\nOberbayern") is None   # only the two heading lines count


# TASK-175: frozen Landkreis cells (pdfplumber column 0) of krankenhausplan_2026.pdf p.15-18. The 51.
# Fortschreibung names the Landkreis on the first row of each group only; the rows below read '-' or ''.
def test_blank_landkreis_cells_take_the_name_from_the_row_above_in_the_same_group():
    rows = [{"clinic_id": k, "landkreis": lk} for k, lk in (
        ("16101", "Kreisfreie Stadt Ingolstadt"), ("16102", "-"), ("16104", "-"),
        ("16201", "Landeshauptstadt München"), ("16202", ""), ("16203", ""))]
    assert [r["landkreis"] for r in K._carry_landkreis(rows)] == [
        "Kreisfreie Stadt Ingolstadt", "Kreisfreie Stadt Ingolstadt", "Kreisfreie Stadt Ingolstadt",
        "Landeshauptstadt München", "Landeshauptstadt München", "Landeshauptstadt München"]


def test_a_blank_landkreis_cell_that_opens_a_group_fails_loudly():
    import pytest
    rows = [{"clinic_id": "16101", "landkreis": "Kreisfreie Stadt Ingolstadt"}, {"clinic_id": "16201", "landkreis": "-"}]
    with pytest.raises(ValueError):
        K._carry_landkreis(rows)
    with pytest.raises(ValueError):
        K._carry_landkreis([{"clinic_id": "16102", "landkreis": ""}])


def test_parse_hands_its_rows_through_the_landkreis_carry(monkeypatch):
    """parse() over a stand-in for pdfplumber (two frozen p.15 rows), so the wiring is pinned without the PDF."""
    class _Page:
        def __init__(self, text, tables):
            self.text, self.tables = text, tables

        def extract_text(self):
            return self.text

        def extract_tables(self):
            return self.tables

    def row(lk, kez, cell):
        return [lk, kez, cell, "Plan-\nKH", "I", "Ö", "798", "-", "", "", "", "", "INN"]
    pages = [_Page("Stand: 1. Januar 2026 (51. Fortschreibung)", []),
             _Page("Teil II Abschnitt A - Plankrankenhäuser\nOberbayern\nLandkreis / KeZ Krankenhaus",
                   [[row("Kreisfreie Stadt\nIngolstadt", "16101", "Klinikum Ingolstadt\nIngolstadt\nKlinikum Ingolstadt GmbH"),
                     row("-", "16102", "Privatklinik Dr. Maul,\nDon Bosconeum\nIngolstadt\nPrivatklinik Dr. Maul GmbH")]])]
    monkeypatch.setattr(K.pdfplumber, "open", lambda path: type("PDF", (), {"pages": pages})())
    rows = K.parse("krankenhausplan_2026.pdf")
    assert [(r["clinic_id"], r["landkreis"], r["regierungsbezirk"]) for r in rows] == [
        ("16101", "Kreisfreie Stadt Ingolstadt", "Oberbayern"), ("16102", "Kreisfreie Stadt Ingolstadt", "Oberbayern")]
