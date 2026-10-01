"""data/backfill_registry_corrections.py (TASK-175): a deviation of the DB from the Krankenhausplan 2026 parse is
explained by the git history of clinics.csv only when the history says where the DB value came from and the 2026
PDF cell itself still states it. Cells are frozen pdfplumber text from krankenhausplan_2026.pdf (KeZ in brackets)."""
import pytest

from data import backfill_registry_corrections as B

INN = "InnKlinikum Altötting\nAltötting\nInnKlinikum gKU\nAltötting und\nMühldorf\nEIN-Krankenhaus im\nSinne des KHG mit\n17102"  # [17101]
GEBO = "Bezirkskrankenhaus\nBayreuth\nBayreuth\nKU\nGesundheitseinrichtun\ndes Bezirks\nOberfranken (GeBO)\nEIN-Krankenhaus im"  # [46203]
NEA = "Klinik Neustadt a.d.\nAisch\nNeustadt a.d. Aisch\nKU Kliniken des\nLandkreises"                             # [57501]
MURNAU = "Klinikum Garmisch-\nPartenkirchen -\nAußenstelle Murnau-\nMurnau\nKlinikum Garmisch-\nPartenkirchen GmbH"  # [18003]
FIRST = ("391d82f", "2026-09-06T08:44:21+00:00", "Public dataset of open nursing jobs at Bavarian hospitals")


def test_whole_lines_state_a_value_name_at_the_head_operator_at_the_tail():
    assert B.states(INN, "name", "InnKlinikum Altötting") == []
    assert B.states(INN, "town", "Altötting") == []
    assert B.states(INN, "operator", "InnKlinikum gKU Altötting und Mühldorf") == []
    assert B.states(INN, "name", "Altötting") is None                      # a town is not the name
    assert B.states(INN, "operator", "InnKlinikum gKU") is None             # an operator runs to the block's end
    assert B.states(NEA, "town", "Neustadt a.d. Aisch") == []
    assert B.states(NEA, "town", "Neustadt") is None                        # a word inside the name is no town


def test_a_word_cut_at_the_column_edge_still_states_the_full_word():
    assert B.states(GEBO, "operator", "KU Gesundheitseinrichtungen des Bezirks Oberfranken (GeBO)") == [
        ("Gesundheitseinrichtun", "Gesundheitseinrichtungen")]
    assert B.states(GEBO, "operator", "KU Gesundheitseinrichtungxx des Bezirks Oberfranken (GeBO)") == [
        ("Gesundheitseinrichtun", "Gesundheitseinrichtungxx")]       # a cut word says nothing about its tail
    assert B.states(GEBO, "name", "Bezirkskranken-haus Bayreuth") is None   # a hyphen inside a word is not a cut
    assert B.states(INN, "name", "InnKlinikumXY Altötting") is None         # only the word that ends a line is cut


def test_line_break_hyphens_join_like_the_parser_and_a_trailing_hyphen_glues_the_next_line():
    assert B.states(MURNAU, "name", "Klinikum Garmisch-Partenkirchen - Außenstelle Murnau-Murnau") == []
    assert B.states(MURNAU, "name", "Klinikum Garmisch-Partenkirchen - Außenstelle Murnau") is None


def _dev(cid, field, db, src):
    return {"source": "krankenhausplan_2026", "id": cid, "field": field, "db": db, "source_value": src, "explained": False}


def test_a_value_from_the_first_build_is_a_parse_error_only_when_the_2026_cell_states_it():
    row, why = B.explain(_dev("17101", "town", "Altötting", "Mühldorf"), [(*FIRST, "Altötting")], INN, "Altötting")
    assert why is None and (row["code"], row["task"], row["old"], row["new"], row["at"]) == (
        "parse_error", "TASK-175", "Mühldorf", "Altötting", FIRST[1])
    assert "krankenhausplan.parse (2025): town = 'Altötting'" in row["evidence"]
    row, why = B.explain(_dev("57501", "town", "Neustadt", "Neustadt a.d. Aisch"), [(*FIRST, "Neustadt")], NEA, "Neustadt")
    assert row is None and "does not state it word for word" in why


def test_a_hand_edit_is_explained_by_its_commit_and_task():
    events = [(*FIRST, "Klinikum Bamberg - Betriebsstätte am Bruderwald-Bamberg Soz"),
              ("38287cc", "2026-09-21T07:59:44+00:00", "Recover 401 postings", "Klinikum Bamberg - Betriebsstätte am Bruderwald")]
    row, why = B.explain(_dev("46170", "name", "Klinikum Bamberg - Betriebsstätte am Bruderwald", "x"), events,
                         "Klinikum Bamberg -\nBetriebsstätte am\nBruderwald-\nBamberg\nSozialstiftung\nBamberg", None)
    assert why is None and (row["task"], row["old"], row["at"]) == (
        "TASK-57", "Klinikum Bamberg - Betriebsstätte am Bruderwald-Bamberg Soz", "2026-09-21T07:59:44+00:00")
    with pytest.raises(KeyError):                                            # a hand edit with no known task
        B.explain(_dev("46170", "name", "N", "x"), [(*FIRST, "M"), ("0000000", "2026-09-30", "s", "N")], "N", None)


def test_values_the_history_does_not_explain_stay_unexplained():
    row, why = B.explain(_dev("76111", "operator", None, "Hessing Stiftung"), [(*FIRST, "Hessing Stiftung")], "x", None)
    assert row is None and why.startswith("not the value clinics.csv last committed ('Hessing Stiftung')")
    row, why = B.explain(_dev("17202", "landkreis", "Landkreis Berchtesgadene r Land", "Landkreis Berchtesgadener Land"),
                         [(*FIRST, "Landkreis Berchtesgadene r Land")], "x", None)
    assert row is None and "not read from the Krankenhaus/Standort cell" in why
    assert B.triage(_dev("17202", "landkreis", "Landkreis Berchtesgadene r Land", "Landkreis Berchtesgadener Land")).startswith("repair")
    assert B.triage(_dev("76111", "operator", None, "Hessing Stiftung")).startswith("fill")
    assert B.triage(_dev("17605", "name", "VAMED Klinik Kipfenberg", "VITREA Klinik Kipfenberg")).startswith("review")
