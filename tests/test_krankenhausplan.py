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
