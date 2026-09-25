"""The operators' own handsets are exempt from the fuse (Ivan, 2026-09-24: "для тестовых юзеров
давай полностью убираем любые лимиты и governor").

Two halves, and the second matters as much as the first: an exempt number must not be REFUSED, and
its sends must not be COUNTED against the people the fuse actually protects. The evening this was
written, first_touches_today stood at 10/10 with all ten sent to the two test handsets -- the next
morning's real campaign would have been refused by a budget nobody real had spent.
"""
import datetime
import pathlib
import tempfile
from datetime import timezone

import pytest

from bridge import errors as E
from bridge import governor as G
from bridge import ledger as L

TEST_PHONE = "+436704048778"
OTHER_TEST = "+4366493036780"
REAL = "+491709990589"

# 03:00 Berlin on a Thursday: outside active hours (9-20), which refuses every governed send
# whatever its kind, so one timestamp exercises the strictest branch.
NIGHT = datetime.datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
# 12:00 Berlin, a Thursday: inside the window, so caps and gaps are what decide.
DAY = datetime.datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def led():
    return L.Ledger(str(pathlib.Path(tempfile.mkdtemp()) / "ledger.sqlite"))


def _gov(led, ungoverned=(TEST_PHONE, OTHER_TEST)):
    return G.Governor(led, per_number_daily_cap=30, ungoverned=ungoverned)


def _spend(led, phone, kind, at, n=1):
    """n sends in the 'attempting' state, which is what count_spent counts as spent -- begin() puts a
    row there by itself, so nothing further is needed to make the budget see them."""
    for i in range(n):
        led.begin(f"wab.o.{phone}.{kind}.{at.isoformat()}.{i}", phone=phone, kind=kind,
                  body_sha256="0" * 64, body_len=9, now=at)


# --- 1. the exemption ---------------------------------------------------------------------------

def test_a_test_handset_is_granted_outside_the_active_window(led):
    """No quiet hours, no Sunday: nobody holding the phone on purpose is woken by it."""
    grant = _gov(led).check(now=NIGHT, phone=TEST_PHONE, kind=G.FIRST_TOUCH)
    assert grant.next_slot_at == L.utc(NIGHT), "nothing to wait for, so the next slot is now"


def test_a_real_number_is_still_refused_at_the_same_moment(led):
    """The fuse is untouched for everyone else -- that is the whole point of naming the numbers."""
    with pytest.raises(E.BridgeRefusal, match="outside the active window"):
        _gov(led).check(now=NIGHT, phone=REAL, kind=G.FIRST_TOUCH)


def test_no_cap_and_no_gap_applies_to_a_test_handset(led):
    """Well past every limit: 40 sends today, 20 of them first touches, the last one a second ago."""
    _spend(led, TEST_PHONE, G.FIRST_TOUCH, DAY, n=20)
    _spend(led, TEST_PHONE, "reply", DAY, n=20)
    gov = _gov(led)
    assert gov.check(now=DAY, phone=TEST_PHONE, kind=G.FIRST_TOUCH).effective_min_gap_sec == 0.0
    assert gov.check(now=DAY, phone=TEST_PHONE, kind="reply") is not None


@pytest.mark.parametrize("spelling", ["+436704048778", "436704048778", "0043670 4048778",
                                      "+43-670-4048778"])
def test_the_exemption_does_not_care_how_the_number_was_typed(led, spelling):
    assert _gov(led).is_ungoverned(spelling) is True


def test_a_national_trunk_zero_is_not_silently_stripped(led):
    """0670... is an Austrian national spelling whose country this module cannot know. Refusing to
    guess means it stays GOVERNED -- the safe direction -- rather than matching the wrong person."""
    assert _gov(led).is_ungoverned("0670 4048778") is False


def test_nothing_is_exempt_by_default(led):
    """An unconfigured deploy is fully governed: the list is opt-in, never a default."""
    assert _gov(led, ungoverned=()).is_ungoverned(TEST_PHONE) is False


# --- 2. the budget, which is the half that bites later --------------------------------------------

def test_test_traffic_does_not_spend_a_real_candidates_first_touch_allowance(led):
    """The live incident. Ten first touches to the test handsets used to read as 10/10 spent."""
    _spend(led, TEST_PHONE, G.FIRST_TOUCH, DAY, n=10)
    grant = _gov(led).check(now=DAY, phone=REAL, kind=G.FIRST_TOUCH)
    assert grant.first_touches_today == 0
    assert grant.spent_today == 0


def test_test_traffic_does_not_park_a_real_first_touch_behind_the_global_gap(led):
    """The gap between first touches exists because strangers see one number wake up. A message to
    an operator's own handset is not that, and must not cost a real candidate four minutes."""
    _spend(led, TEST_PHONE, G.FIRST_TOUCH, DAY)
    assert _gov(led).check(now=DAY, phone=REAL, kind=G.FIRST_TOUCH) is not None


def test_a_real_candidates_own_traffic_still_counts_normally(led):
    """The exclusion is by number, not a hole in the counter. Spent an hour earlier so the global
    first-touch gap -- which genuinely does apply between two real strangers -- is not what decides."""
    earlier = DAY - datetime.timedelta(hours=1)
    _spend(led, REAL, G.FIRST_TOUCH, earlier, n=3)
    assert _gov(led).check(now=DAY, phone="+491709990590",
                           kind=G.FIRST_TOUCH).first_touches_today == 3


def test_health_reports_test_traffic_separately_instead_of_hiding_it(led):
    """"0 of 10 spent" while the handset sent thirty messages tonight would be a health view that
    lies. The numbers the fuse decides on, plus the test traffic, named."""
    _spend(led, TEST_PHONE, G.FIRST_TOUCH, DAY, n=7)
    _spend(led, REAL, G.FIRST_TOUCH, DAY, n=2)
    quota = _gov(led).quota(DAY)
    assert quota["first_touches_today"] == 2
    assert quota["ungoverned_sent_today"] == 7
    assert quota["ungoverned_numbers"] == 2
