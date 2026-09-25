"""The frozen first-touch broadcast (app/wa/broadcast_template.py, Ivan 2026-09-24).

The point of these tests is not that the renderer works -- it is four lines -- but that the WORDING
cannot drift. The text lived only in the WhatsApp threads on the handset until an agent, unable to
find it in the repository, wrote its own German opener and sent it to a test number. The two
expected strings below are the bytes read back off the handset from the messages actually delivered
that day, so a well-meaning edit to the constants fails here instead of going to a stranger.
"""
import pytest

from app.wa import broadcast_template as BT

# Read off the handset (tools/wa_bridge.py read) 2026-09-24: the messages delivered at 12:52 and
# 12:57 UTC. Written out in full rather than built from BT's own constants -- a test that composes
# the expected value the same way the code does cannot detect a change to either.
IVAN = ("Neue Stellen in Bayern für Pflegekräfte\n"
        "\n"
        "Hallo, Ivan. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele "
        "neue Stellen in Bayern. Haben Sie noch Interesse?")
VALENTYN = ("Neue Stellen in Bayern für Pflegekräfte\n"
            "\n"
            "Hallo, Valentyn. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir "
            "viele neue Stellen in Bayern. Haben Sie noch Interesse?")


def test_the_wording_is_exactly_what_the_handset_shows():
    assert BT.render("Ivan") == IVAN
    assert BT.render("Valentyn") == VALENTYN


def test_the_header_is_its_own_line_followed_by_a_blank_one():
    """WhatsApp renders the header as a bold first line only when the blank line is really there."""
    header, blank, body = BT.render("Ivan").split("\n", 2)
    assert header == BT.HEADER and blank == "" and body.startswith("Hallo, Ivan.")


def test_the_name_is_the_only_thing_that_varies():
    a, b = BT.render("Ivan"), BT.render("Valentyn")
    assert a.replace("Ivan", "X") == b.replace("Valentyn", "X")


# Ivan, 2026-09-24: a lead list without a first name is normal ("может быть случай когда имя
# неизвестно. тогда просто поздороваемся"), so the nameless form is a second approved wording, not a
# repair. Written out in full here for the same reason as the two above.
NO_NAME = ("Neue Stellen in Bayern für Pflegekräfte\n"
           "\n"
           "Hallo! Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele "
           "neue Stellen in Bayern. Haben Sie noch Interesse?")


@pytest.mark.parametrize("name", [None, "", "   ", "\n"])
def test_an_unknown_name_just_greets(name):
    assert BT.render(name) == NO_NAME


def test_the_nameless_form_differs_from_the_named_one_only_in_the_greeting():
    """The sentence after the greeting has to stay byte-identical, or the two wordings drift apart
    and a lead list with half its names missing sends two different pitches."""
    assert BT.render("Ivan").split(". ", 1)[1] == BT.render().split("! ", 1)[1]


def test_nothing_ever_invents_a_name():
    """The one thing worse than no name is a wrong one."""
    assert "Hallo," not in BT.render()
    assert BT.render().count("Hallo") == 1


# --- the command-line path ----------------------------------------------------------------------

def _tool():
    import importlib.util
    import pathlib
    import sys
    path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "wa_bridge.py"
    spec = importlib.util.spec_from_file_location("wa_bridge_tool", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("wa_bridge_tool", module)
    spec.loader.exec_module(module)
    return module


def test_template_mode_renders_every_recipient_from_their_name():
    rows = [{"line": 1, "to": "+436704048778", "body": None, "name": "Ivan"},
            {"line": 2, "to": "+4366493036780", "body": None, "name": "Valentyn"},
            {"line": 3, "to": "+491709990589", "body": None, "name": None}]
    items = _tool().plan_broadcast(rows, None, template=True)
    assert [i["body"] for i in items] == [IVAN, VALENTYN, NO_NAME], (
        "a recipient with no name still gets the approved wording, just the plain greeting")


def test_template_mode_refuses_a_recipient_who_also_carries_their_own_body():
    """Not "the template wins" and not "the body wins": a file carrying both was written by someone
    who expected one of them to win, and guessing which is how the wrong text reaches a stranger."""
    rows = [{"line": 1, "to": "+436704048778", "body": "etwas anderes", "name": "Ivan"}]
    with pytest.raises(ValueError, match="which --template would override"):
        _tool().plan_broadcast(rows, None, template=True)


def test_without_template_mode_nothing_changes():
    rows = [{"line": 1, "to": "+436704048778", "body": "eigener Text", "name": "Ivan"},
            {"line": 2, "to": "+4366493036780", "body": None, "name": "Valentyn"}]
    items = _tool().plan_broadcast(rows, "gemeinsamer Text")
    assert [i["body"] for i in items] == ["eigener Text", "gemeinsamer Text"]
