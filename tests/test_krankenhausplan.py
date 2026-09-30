"""pflege_jobs.sources.krankenhausplan: the 'Krankenhaus / Standort' cell (TASK-183), validate() (TASK-136), counts
(TASK-167), Bezirk headings (TASK-178), the Landkreis carry (TASK-175).

Cells are frozen from data/registry/krankenhausplan_2026.pdf (51. Fortschreibung) as parse() reads them, captured
2026-09-30: pdfplumber words with keep_blank_chars=True from the row's crop (the name column's left edge to the page
edge) as (text, x0, x1, top), kept up to the VSt column. No PDF parsing at test time.
"""
import pytest

from pflege_jobs.sources import krankenhausplan as K

NAME_X1, STATUS_X1 = 217.59, 248.88          # column edges on p.52-144 (p.57: 217.57 / 248.86, p.256: 217.64 / 248.93)


def _words(rows):
    return [{"text": t, "x0": x0, "x1": x1, "top": top} for t, x0, x1, top in rows]


# 18102 p.59. Name, Standort and Träger are paragraphs (lines 9.7pt apart, 19.4pt between), the EIN-Krankenhaus note a
# 4th. The known-town guess read name 'Psychosomatische Klinik Windach Windach a.' and town 'Ammersee'.
C18102 = [
    ('Plan-', 220.0, 240.51, 372.37), ('F', 251.31, 255.9, 372.37), ('Psychosomatische ', 127.61, 203.26, 373.87),
    ('KH', 220.0, 231.55, 382.09), ('Klinik Windach', 127.61, 186.48, 383.59), (' ', 127.61, 130.42, 393.32),
    ('Windach a. ', 127.61, 175.08, 403.04), ('Ammersee', 127.61, 170.48, 412.76), (' ', 127.61, 130.42, 422.48),
    ('Psychosomatische ', 127.61, 203.26, 432.21), ('Klinik GmbH & Co. ', 127.61, 204.86, 441.93),
    ('Windach/Ammersee ', 127.61, 211.08, 451.65), ('KG', 127.63, 139.36, 461.37),
    ('EIN-Krankenhaus im ', 127.63, 213.66, 479.09), ('Sinne des KHG mit ', 127.63, 206.16, 488.81),
    ('16256', 127.63, 153.03, 498.54)]
# 18872 p.256: the site names its town twice (TASK-136's shape); the guess cut the name to 'Benedictus Krankenhaus'.
C18872 = [
    ('Vertra', 220.0, 245.0, 145.74), ('-', 251.31, 254.94, 145.74), ('Benedictus ', 127.61, 174.18, 147.24),
    ('gs-KH', 220.0, 244.32, 155.46), ('Krankenhaus ', 127.61, 182.84, 156.96), ('Feldafing', 127.61, 163.98, 166.68),
    (' ', 127.61, 130.42, 176.41), ('Feldafing', 127.61, 163.98, 186.14), (' ', 127.61, 130.42, 195.86),
    ('Benedictus ', 127.61, 174.18, 205.58), ('Krankenhaus ', 127.61, 182.83, 215.3),
    ('Feldafing GmbH & ', 127.61, 203.18, 225.02), ('Co. KG', 127.61, 155.5, 234.75)]
# 18007 p.58: 'Berufsgenossenschaftliche' runs 16pt past the name column into the Status column (1.5pt above 'Plan-').
# pdfplumber's cell text cut it: name 'Berufsgenossenschaftl', Status 'icPhlaen -\nKH' (tails interleaved with 'Plan-').
C18007 = [
    ('Plan-', 220.0, 240.5, 238.27), ('II', 251.3, 258.02, 238.27), ('Berufsgenossenschaftliche ', 127.61, 236.37, 239.77),
    ('KH', 220.0, 231.54, 248.0), ('Unfallklinik', 127.61, 171.59, 249.5), (' ', 127.61, 130.42, 259.22),
    ('Murnau', 127.61, 157.72, 268.94), (' ', 127.61, 130.42, 278.66), ('BG Klinikum Murnau ', 127.61, 212.55, 288.38),
    ('gGmbH', 127.61, 157.56, 298.1)]
# 17702 p.52 / 18003 p.57: the name paragraph ends in a dash; gluing every '-' line to the next one swallowed the
# Standort ('Dorfen-Dorfen', 'Murnau-Murnau'). 'Partenkirchen -' has no space char after its dash (the bracketing
# '-Außenstelle Murnau-', printed inline in 17702's 'Erding -Außenstelle'), 56404's 'Hallerwiese - ' has one.
C17702 = [
    ('Plan-', 220.0, 240.51, 294.6), ('I', 251.31, 254.67, 294.6), ('Klinikum Landkreis ', 127.61, 206.49, 296.1),
    ('KH', 220.0, 231.55, 304.32), ('Erding -Außenstelle ', 127.61, 209.05, 305.82), ('Dorfen-', 127.61, 158.28, 315.54),
    (' ', 127.61, 130.42, 325.26), ('Dorfen', 127.61, 154.65, 334.99), (' ', 127.63, 130.44, 344.71),
    ('Landkreis Erding', 127.63, 194.65, 354.43), ('EIN-Krankenhaus im ', 127.63, 213.66, 372.15),
    ('Sinne des KHG mit ', 127.63, 206.16, 381.87), ('17701', 127.63, 153.03, 391.59)]
C18003 = [
    ('Plan-', 220.0, 240.51, 146.5), ('II', 251.31, 258.03, 146.5), ('Klinikum Garmisch-', 127.61, 206.53, 147.99),
    ('KH', 220.0, 231.55, 156.22), ('Partenkirchen -', 127.61, 189.41, 157.71), ('Außenstelle Murnau-', 127.61, 210.68, 167.43),
    (' ', 127.61, 130.42, 177.15), ('Murnau', 127.61, 157.72, 186.88), (' ', 127.63, 130.44, 196.6),
    ('Klinikum Garmisch-', 127.63, 206.55, 206.32), ('Partenkirchen GmbH', 127.63, 210.76, 216.04),
    ('EIN-Krankenhaus im ', 127.63, 213.66, 233.76), ('Sinne des KHG mit ', 127.63, 206.16, 243.49),
    ('18001', 127.63, 153.03, 253.21)]
C56404 = [
    ('Plan-', 220.0, 240.51, 284.88), ('I', 251.31, 254.67, 284.88), ('Klinik Hallerwiese - ', 127.61, 207.34, 286.38),
    ('KH', 220.0, 231.55, 294.61), ('Cnopfsche ', 127.61, 171.84, 296.11), ('Kinderklinik', 127.61, 174.64, 305.83),
    (' ', 127.61, 130.42, 315.55), ('Nürnberg', 127.61, 165.25, 325.27), (' ', 127.61, 130.42, 334.99),
    ('DIAKONEO KdöR', 127.61, 195.5, 344.71)]
# 18002 p.56: 'für Kinder- und' is one line; its hyphen is text, not a line break ('Kinderund' before TASK-183).
C18002 = [
    ('Plan-', 220.0, 240.51, 391.83), ('F', 251.31, 255.9, 391.83), ('Deutsches Zentrum ', 127.61, 209.34, 393.32),
    ('KH', 220.0, 231.55, 401.55), ('für Kinder- und ', 127.61, 191.98, 403.04), ('Jugendrheumatologie', 127.61, 213.92, 412.77),
    (' ', 127.61, 130.42, 422.49), ('Garmisch-', 127.61, 169.0, 432.22), ('Partenkirchen', 127.61, 182.97, 441.94),
    (' ', 127.61, 130.42, 451.66), ('Kinderklinik ', 127.61, 177.44, 461.38), ('Garmisch-', 127.61, 169.0, 471.1),
    ('Partenkirchen ', 127.61, 185.78, 480.81), ('gGmbH', 127.61, 157.56, 490.54)]
# 26103 p.81: 'Kinderklinik' is two font subsets, 'linik' set 0.8pt lower than 'Kinderk' and ' St. '.
C26103 = [
    ('Plan-', 220.0, 240.5, 145.75), ('F', 251.3, 255.9, 145.75), ('LA-Regio Kliniken ', 127.61, 200.83, 147.13),
    ('KH', 220.0, 231.54, 155.47), ('Kinderk', 127.61, 158.27, 156.85), (' St. ', 174.69, 191.83, 156.85),
    ('linik', 158.31, 174.69, 157.62), ('Marien Landshut', 127.61, 194.08, 166.57), ('Landshut', 127.61, 164.32, 186.14),
    ('LA-Regio Kliniken ', 127.61, 200.83, 205.45), ('gKU, AdöR des ', 127.61, 190.1, 215.18),
    ('Landkreises und der ', 127.61, 211.81, 224.9), ('Stadt Landshut', 127.61, 188.71, 234.62)]


def test_name_standort_and_traeger_are_the_paragraphs_of_the_cell():
    assert K._cell(_words(C18102), NAME_X1, STATUS_X1) == (
        "Psychosomatische Klinik Windach", "Windach a. Ammersee", "Psychosomatische Klinik GmbH & Co. Windach/Ammersee KG",
        "Plan-KH")
    assert K._cell(_words(C18872), 217.64, 248.93) == (
        "Benedictus Krankenhaus Feldafing", "Feldafing", "Benedictus Krankenhaus Feldafing GmbH & Co. KG", "Vertrags-KH")


def test_a_word_past_the_name_column_stays_whole_and_out_of_the_status():
    assert K._cell(_words(C18007), NAME_X1, STATUS_X1) == (
        "Berufsgenossenschaftliche Unfallklinik", "Murnau", "BG Klinikum Murnau gGmbH", "Plan-KH")


def test_a_name_ending_in_a_dash_keeps_its_standort_and_lines_join_with_the_pdfs_own_spaces():
    assert K._cell(_words(C17702), NAME_X1, STATUS_X1) == (
        "Klinikum Landkreis Erding -Außenstelle Dorfen-", "Dorfen", "Landkreis Erding", "Plan-KH")
    assert K._cell(_words(C18003), 217.57, 248.86) == (
        "Klinikum Garmisch-Partenkirchen -Außenstelle Murnau-", "Murnau", "Klinikum Garmisch-Partenkirchen GmbH", "Plan-KH")
    assert K._cell(_words(C56404), NAME_X1, STATUS_X1) == (
        "Klinik Hallerwiese - Cnopfsche Kinderklinik", "Nürnberg", "DIAKONEO KdöR", "Plan-KH")


def test_a_suspended_hyphen_inside_a_line_is_text():
    assert K._cell(_words(C18002), NAME_X1, STATUS_X1) == (
        "Deutsches Zentrum für Kinder- und Jugendrheumatologie", "Garmisch-Partenkirchen",
        "Kinderklinik Garmisch-Partenkirchen gGmbH", "Plan-KH")
    # pdfplumber cell text (Landkreis column, p.56 KeZ 18001): only a hyphen at a line break joins the lines
    assert K._clean("Landkreis\nGarmisch-\nPartenkirchen") == "Landkreis Garmisch-Partenkirchen"
    assert K._clean("für Kinder- und\nJugendrheumatologie") == "für Kinder- und Jugendrheumatologie"


def test_one_line_set_in_two_font_subsets_is_one_line():
    assert K._cell(_words(C26103), NAME_X1, STATUS_X1)[0] == "LA-Regio Kliniken Kinderklinik St. Marien Landshut"


def test_a_cell_that_is_not_name_standort_traeger_paragraphs_fails_loudly():
    # krankenhausplan_2025.pdf p.76 KeZ 26205: the 50. Fortschreibung set this cell without paragraph gaps
    c26205_2025 = [
        ('Bezirkskrankenhaus ', 109.32, 182.72, 114.4), ('Plan-KH', 188.4, 216.66, 114.4), ('F', 223.8, 227.86, 114.4),
        ('Passau - Fachklinik ', 109.32, 179.39, 123.64), ('für ', 109.32, 121.76, 132.88),
        ('Erwachsenenpsychiat', 109.32, 185.18, 142.12), ('rie und ', 109.32, 136.67, 151.36),
        ('Psychotherapie', 109.32, 163.06, 160.6), ('Passau', 109.32, 133.98, 169.83), ('Träger', 109.32, 132.47, 179.07),
        ('Bezirk Niederbayern', 109.32, 180.96, 188.31), ('EIN-Krankenhaus im ', 109.32, 185.22, 197.55),
        ('Sinne des KHG mit ', 109.32, 178.57, 206.79), ('27105', 109.32, 132.02, 216.03)]
    with pytest.raises(ValueError):
        K._cell(_words(c26205_2025), 186.78, 222.18)
    with pytest.raises(ValueError):                      # a 4th paragraph that is not the EIN-Krankenhaus note
        K._cell(_words(C56404 + [("Nachtrag", 127.61, 170.0, 364.15)]), NAME_X1, STATUS_X1)


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
    rows = [{"clinic_id": "16101", "landkreis": "Kreisfreie Stadt Ingolstadt"}, {"clinic_id": "16201", "landkreis": "-"}]
    with pytest.raises(ValueError):
        K._carry_landkreis(rows)
    with pytest.raises(ValueError):
        K._carry_landkreis([{"clinic_id": "16102", "landkreis": ""}])


def test_parse_reads_each_row_from_its_own_cells(monkeypatch):
    """parse() over a stand-in for pdfplumber: p.122's two frozen rows. 47102's blank Landkreis takes 47101's; their
    'Krankenhausgesellschaft' runs 12pt past the name column, so a crop that ends at the column edge -- what
    pdfplumber's cell text is -- would cut it ('Krankenhausgesellsch', tail 'aft' in the Status cell)."""
    class _Crop:
        def __init__(self, words):
            self.words = words

        def extract_words(self, **kw):
            return self.words

    class _Row:
        def __init__(self, top, bottom):
            self.cells = [(18.13, top, 92.4, bottom), (92.4, top, 123.7, bottom), (123.7, top, 217.59, bottom),
                          (217.59, top, 248.88, bottom)]

    class _Table:
        def __init__(self, rows, texts):
            self.rows, self.texts = rows, texts

        def extract(self):
            return self.texts

    class _Page:
        width = 841.89

        def __init__(self, text, tables=(), words=()):
            self.text, self.tables, self.words = text, list(tables), words

        def extract_text(self):
            return self.text

        def find_tables(self):
            return self.tables

        def crop(self, bbox):                       # like pdfplumber: a word crossing the right edge loses its chars there
            x0, top, x1, bottom = bbox
            out = []
            for t, wx0, wx1, wtop in self.words:
                if top <= wtop < bottom and x0 <= wx0 < x1:
                    keep = len(t) if wx1 <= x1 else int(len(t) * (x1 - wx0) / (wx1 - wx0))
                    out.append({"text": t[:keep], "x0": wx0, "x1": min(wx1, x1), "top": wtop})
            return _Crop(out)

    w47101 = [
        ('Plan-', 220.0, 240.5, 145.75), ('I', 251.3, 254.66, 145.75), ('Juraklinik Scheßlitz', 127.61, 204.54, 147.25),
        ('KH', 220.0, 231.54, 155.47), (' ', 127.61, 130.42, 156.97), ('Scheßlitz', 127.61, 163.75, 166.69),
        (' ', 127.61, 130.42, 176.41), ('Gem. ', 127.61, 152.06, 186.14), ('Krankenhausgesellschaft ', 127.61, 230.02, 195.86),
        ('des Landkreises ', 127.61, 194.59, 205.58), ('Bamberg mbH', 127.61, 185.39, 215.3),
        ('EIN-Krankenhaus im ', 127.61, 213.64, 233.02), ('Sinne des KHG mit ', 127.61, 206.14, 242.75),
        ('47102', 127.61, 153.01, 252.47)]
    w47102 = [
        ('Plan-', 220.0, 240.5, 267.44), ('I', 251.3, 254.66, 267.44), ('Steigerwaldklinik ', 127.61, 198.75, 268.94),
        ('KH', 220.0, 231.54, 277.16), ('Burgebrach', 127.61, 173.71, 278.66), (' ', 127.61, 130.42, 288.38),
        ('Burgebrach', 127.61, 173.71, 298.11), (' ', 127.61, 130.42, 307.83), ('Gem. ', 127.61, 152.06, 317.55),
        ('Krankenhausgesellschaft ', 127.61, 230.02, 327.27), ('des Landkreises ', 127.61, 194.59, 336.99),
        ('Bamberg mbH', 127.61, 185.39, 346.71), ('EIN-Krankenhaus im ', 127.62, 213.65, 364.43),
        ('Sinne des KHG mit ', 127.62, 206.15, 374.15), ('47101', 127.62, 153.02, 383.87)]
    texts = [  # pdfplumber's table text of the two rows (the name and Status cells as the old parser read them)
        ["Landkreis\nBamberg", "47101", "Juraklinik Scheßlitz\nScheßlitz\nGem.\nKrankenhausgesellsch\ndes Landkreises\n"
         "Bamberg mbH\nEIN-Krankenhaus im\nSinne des KHG mit\n47102", "Plan-\nKH\naft", "I", "Ö", "130", "0", "", "", "", "",
         "CHI, INN"],
        ["", "47102", "Steigerwaldklinik\nBurgebrach\nBurgebrach\nGem.\nKrankenhausgesellsch\ndes Landkreises\n"
         "Bamberg mbH\nEIN-Krankenhaus im\nSinne des KHG mit\n47101", "Plan-\nKH\naft", "I", "Ö", "128", "8", "", "", "", "",
         "CHI, INN, PSO"]]
    pages = [_Page("Stand: 1. Januar 2026 (51. Fortschreibung)"),
             _Page("Oberfranken\nLandkreis / KeZ Krankenhaus Status VSt.",
                   [_Table([_Row(141.99, 262.96), _Row(262.96, 395.07)], texts)], w47101 + w47102)]
    monkeypatch.setattr(K.pdfplumber, "open", lambda path: type("PDF", (), {"pages": pages})())
    rows = K.parse("krankenhausplan_2026.pdf")
    op = "Gem. Krankenhausgesellschaft des Landkreises Bamberg mbH"
    assert [{k: r[k] for k in ("clinic_id", "name", "town", "operator", "landkreis", "regierungsbezirk", "status", "beds",
                                "day_places", "fachrichtungen")} for r in rows] == [
        {"clinic_id": "47101", "name": "Juraklinik Scheßlitz", "town": "Scheßlitz", "operator": op,
         "landkreis": "Landkreis Bamberg", "regierungsbezirk": "Oberfranken", "status": "Plan-KH", "beds": 130,
         "day_places": 0, "fachrichtungen": "CHI|INN"},
        {"clinic_id": "47102", "name": "Steigerwaldklinik Burgebrach", "town": "Burgebrach", "operator": op,
         "landkreis": "Landkreis Bamberg", "regierungsbezirk": "Oberfranken", "status": "Plan-KH", "beds": 128,
         "day_places": 8, "fachrichtungen": "CHI|INN|PSO"}]
