"""Offline pin for the "asked again" regexes of the real-model persona tests (those files are marked ``llm`` and cannot run
in the offline lane, so a mistake in a pattern would only show as a flaky persona failure).

HOUSING_RE (tests/test_wa_luna_import_reuse_personas.py) guards "an imported housing answer is not asked again". Its
first form, ``wohnung[^?]*\\?``, ran across sentence ends: a statement that only mentions "Wohnung" followed by an unrelated
question in the next sentence or bubble (the test joins the bubbles with a space) counted as a housing question and failed
a correct reply. All sentences below are invented."""
import pytest

from tests.test_wa_luna_import_reuse_personas import HOUSING_RE


@pytest.mark.parametrize("said", [
    "Brauchen Sie eine Wohnung?",
    "Haben Sie schon eine Wohnung in Bayern gefunden?",
    "Wie ist denn Ihre Wohnungssituation im Moment?",
    "Wie viele Personen ziehen mit Ihnen um?",
    "Gut zu wissen. Suchen Sie auch eine Wohnung für die ganze Familie?",   # the question is the last sentence
    "Sie hatten Interesse an Bayern. Brauchen Sie eine Wohnung?",             # an earlier sentence without it
])
def test_a_housing_question_matches(said):
    assert HOUSING_RE.search(said)


@pytest.mark.parametrize("said", [
    # a Wohnung in one sentence, an unrelated "?" in the next: two sentences, no housing question
    "Die Stelle in Hamburg nennt keine Wohnung. Dürfen wir Ihre Unterlagen verwenden?",
    "Die Stelle in Hamburg bietet eine Wohnung! Passt das zu Ihren Wünschen?",
    # the same across two bubbles joined by the test's own " " (and by a newline)
    " ".join(["Die Anzeige erwähnt eine Wohnung.", "Möchten Sie sich bewerben?"]),
    "\n".join(["Die Anzeige erwähnt eine Wohnung", "Möchten Sie sich bewerben?"]),
    # no housing word at all
    "Haben Sie noch Interesse an einer Stelle in Bayern?",
])
def test_a_statement_that_only_names_the_flat_does_not_match(said):
    assert not HOUSING_RE.search(said)
