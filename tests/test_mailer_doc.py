"""tools/mailer_doc.py: one body, a text part with aligned rows and an HTML part with real tables."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mailer_doc import Doc, Quote, Table  # noqa: E402


def test_a_table_is_aligned_in_text_and_a_real_table_in_html():
    t = Table(["Время", "Клиника", "Кому"], [["09:02", "Klinik A", "a@x.de"], ["10:15", "Klinik Bamberg", "b@x.de"]], title="Уйдёт:")
    assert t.text() == ("Уйдёт:\n  Время  Клиника         Кому\n  09:02  Klinik A        a@x.de\n  10:15  Klinik Bamberg  b@x.de")
    page = t.html()
    assert page.count("<table") == 1 and page.count("<tr>") == 3 and page.count("<th ") == 3 and ">Klinik Bamberg</td>" in page
    assert page.startswith('<p style="margin:0 0 4px"><b>Уйдёт:</b></p><table')


def test_a_table_row_of_the_wrong_length_fails_loudly():
    with pytest.raises(ValueError, match="does not have 2 cells"):
        Table(["a", "b"], [["only one"]])


def test_cells_and_text_are_escaped_and_a_letter_is_set_off_unchanged():
    d = Doc("Письмо <b>не тег</b> & ещё", Table(None, [["От", "A & B <a@x.de>"]]), Quote("Betreff: x\n\n  Zeile <1>\n"))
    assert d.text() == "Письмо <b>не тег</b> & ещё\n\n  От  A & B <a@x.de>\n\nBetreff: x\n\n  Zeile <1>\n"
    page = d.html()
    assert "Письмо &lt;b&gt;не тег&lt;/b&gt; &amp; ещё" in page and "A &amp; B &lt;a@x.de&gt;" in page and "&lt;1&gt;" in page
    assert "white-space:pre-wrap" in page and "<th " not in page


def test_a_paragraph_keeps_its_lines_lists_become_lists_and_bold_and_code_work():
    page = Doc("Строка один\nстрока два\n\n# Заголовок\n- a **жирно**\n- b `код`\n\n1. первый\n2. второй").html()
    assert "Строка один<br>строка два" in page and "<b>Заголовок</b>" in page
    assert "<ul" in page and "<li>a <b>жирно</b></li><li>b <code>код</code></li>" in page and "<ol" in page and page.count("<li>") == 4


def test_a_markdown_table_in_a_paragraph_becomes_a_table_and_a_short_row_is_filled():
    page = Doc("До\n| A | B |\n|---|:--|\n| 1 | 2 |\n| 3 |\n\nПосле").html()
    assert re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", page) == ["A", "B", "1", "2", "3", ""]
    assert "<p" in page and "До" in page and "После" in page


def test_the_html_page_is_a_whole_document():
    page = Doc("x").html()
    assert page.startswith("<!doctype html><html><head><meta charset=\"utf-8\"></head><body") and page.rstrip().endswith("</body></html>")
