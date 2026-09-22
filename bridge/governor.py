"""The fuse on the phone side: the last thing between a bug and a real person (TASK-127, TASK-130).

Division of labour, unchanged from the plan: our VPS owns the SCHEDULE (campaign.py's Window and
its claimed_at-counted batching, which already survives restarts). This owns a FLOOR and a FUSE --
it may always send slower than asked and never faster, and it refuses past its own cap. The request
cannot raise a cap or shorten a gap; that is the point. A server-side bug that asks for 500 sends
at 03:00 gets 500 refusals, not 500 messages.

THE CONSTANTS ARE THE COLLEAGUE'S, ADOPTED WHOLE (decision-8: they are stricter than our ADDENDUM
asked for on every axis, and they are the ones already running on that handset -- one handset, one
policy, the stricter one). Read read-only from ~/wa-phone-outreach/apps/wa_phone at commit
ea82a51:

    config.py PACING = {
        "timezone": "Europe/Berlin",
        "active_hours": (9, 20),
        "chars_per_sec": (3.2, 5.5),
        "pause_before_send": (0.8, 2.5),
        "pause_after_open_chat": (1.5, 4.0),
        "gap_between_first_touches": (240, 600),   # log-uniform, humanize.jitter_gap
        "gap_between_bubbles": (4, 12),
        "max_first_touches_per_day": 10,
        "max_first_touches_per_hour": 4,
        "reply_think_time": (20, 90),
    }
    humanize.within_active_hours(): returns False on weekday() == 6 -- Sunday is blocked.

TWO HOLES IN THEIRS THAT WE DO NOT INHERIT (TASK-127):
  1. Their daily cap is GLOBAL and counted on a UTC day while pacing runs Europe/Berlin, so the
     window resets at 02:00 local in summer. Ours is counted on the pacing timezone day.
  2. Their quiet hours guard first touches ONLY -- within_active_hours is referenced once, inside
     the first_touch branch, and an outbound reply at 07:53 Europe/Berlin was observed live. Here
     quiet hours apply to EVERY outbound, reply included.

ONE CONSTANT IS NOT THEIRS AND IS NOT OURS TO INVENT: a per-recipient daily cap. Their config has
no such number and CLAUDE.md forbids inventing one, so ``per_number_daily_cap`` is a required
constructor argument and the server refuses to start without WA_BRIDGE_PER_NUMBER_DAILY_CAP. Ivan
or TASK-127 names it; this module will not.

WHY THE GAP CHECK USES THE LOW END OF THE JITTER RANGE: a fuse whose threshold is redrawn at
random can be beaten by asking again until the draw is short. So the refusal threshold is the
range's floor (240 s between first touches, 4 s between bubbles to one person) and the jitter lives
in ``next_slot_at``, which is advice to the caller's scheduler, not a permission.

NOTHING HERE SLEEPS. A paced refusal is a 429 rail_parked with next_slot_at: the caller reschedules
and ownership is restored. Sleeping inside a request would hold an HTTP connection for minutes and
hide the pacing from every observer.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import errors as E
from . import ledger as L

FIRST_TOUCH = "first_touch"
REPLY = "reply"
KINDS = (FIRST_TOUCH, REPLY)

#: The whole constraint vocabulary ``check`` below reads, and the type each value is read as. It is
#: a closed list on purpose (TASK-147 review): a constraint this fuse does not read is one the
#: caller believes it asked for, and ``min_gap_sec: 3600`` on a run the governor paces by
#: ``min_gap_ms`` is a campaign an operator thinks is hourly going out at the 4-second floor. Same
#: rule as server.py::_chat_args on the destructive routes -- an unknown key is a refusal, never a
#: silently ignored one.
CONSTRAINT_TYPES = {"daily_cap": int, "first_touch_daily_cap": int, "min_gap_ms": (int, float),
                    "respect_quiet_hours": bool}


def validate_constraints(requested, *, what="constraints"):
    """-> the constraints, or raise ``invalid_request`` naming the key. Call it at the BOUNDARY.

    ``check`` interprets these values with ``int()`` and ``float()``, which is where a null or a
    string becomes a TypeError far from the request that carried it -- a 500 on the send route, and
    on the broadcast route an item that can never resolve and sits at the head of the queue (the
    ledger orders by run then position) blocking every other run behind it. So the values are read
    for what they are here, before anything is stored or attempted.
    """
    if not isinstance(requested, dict):
        raise E.invalid_request(f"{what} must be an object of executor constraints")
    unknown = sorted(set(requested) - set(CONSTRAINT_TYPES))
    if unknown:
        raise E.invalid_request(
            f"{what}: unknown key(s) {unknown}; this governor reads "
            f"{sorted(CONSTRAINT_TYPES)} and would have ignored the rest")
    for key, value in requested.items():
        want = CONSTRAINT_TYPES[key]
        # bool is an int in Python and 'daily_cap: true' is not a cap anyone meant.
        if isinstance(value, bool) != (want is bool) or not isinstance(value, want):
            raise E.invalid_request(
                f"{what}.{key} must be {'a boolean' if want is bool else 'a number'}, "
                f"got {type(value).__name__}")
    return requested


@dataclass(frozen=True)
class Pacing:
    """Their numbers. Change one and you are changing the house rule on a shared handset."""

    timezone: str = "Europe/Berlin"
    active_hours: tuple = (9, 20)
    block_sunday: bool = True
    first_touch_gap_sec: tuple = (240, 600)
    bubble_gap_sec: tuple = (4, 12)
    first_touches_per_day: int = 10
    first_touches_per_hour: int = 4


MINI_FLOOR = Pacing()


@dataclass(frozen=True)
class Grant:
    """What the fuse allows, in the caller's own units, for the health and 200 bodies.

    The counts are as of the fuse check, i.e. before the send being granted was written. That is
    the honest reading: they are what the decision was taken on.
    """

    kind: str
    spent_today: int
    spent_today_this_number: int
    first_touches_today: int
    effective_min_gap_sec: float
    effective_per_number_cap: int
    effective_first_touch_daily_cap: int
    next_slot_at: str


class Governor:
    def __init__(self, ledger, *, per_number_daily_cap, pacing=MINI_FLOOR, rng=None):
        if per_number_daily_cap is None:
            raise RuntimeError(
                "per_number_daily_cap has no default: their config.py has no per-recipient cap and "
                "CLAUDE.md forbids inventing one. Set WA_BRIDGE_PER_NUMBER_DAILY_CAP (TASK-127).")
        self.ledger = ledger
        self.pacing = pacing
        self.cap = int(per_number_daily_cap)
        self.tz = ZoneInfo(pacing.timezone)
        self.rng = rng or random.Random()

    # --- clock ----------------------------------------------------------------------------------
    def local(self, now):
        return now.astimezone(self.tz)

    def day_bounds(self, now):
        """The pacing-timezone day, not the UTC day. TASK-127 hole #1."""
        start = self.local(now).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)

    def window_open(self, now):
        local = self.local(now)
        if self.pacing.block_sunday and local.weekday() == 6:
            return False
        lo, hi = self.pacing.active_hours
        return lo <= local.hour < hi

    def window_opens_at(self, now):
        """The next moment the window is open, jittered like their seconds_until_active."""
        lo, _ = self.pacing.active_hours
        local = self.local(now)
        candidate = local.replace(hour=lo, minute=self.rng.randint(3, 40), second=0, microsecond=0)
        while candidate <= local or (self.pacing.block_sunday and candidate.weekday() == 6):
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    def jittered_gap(self, kind):
        """Log-uniform, as humanize.jitter_gap does it: mostly short, sometimes long."""
        lo, hi = self.pacing.first_touch_gap_sec if kind == FIRST_TOUCH else self.pacing.bubble_gap_sec
        return math.exp(self.rng.uniform(math.log(lo), math.log(hi)))

    def gap_floor(self, kind):
        return float((self.pacing.first_touch_gap_sec if kind == FIRST_TOUCH
                      else self.pacing.bubble_gap_sec)[0])

    # --- the fuse --------------------------------------------------------------------------------
    def check(self, *, now, phone, kind, requested=None):
        """-> Grant, or raise. The only caller is executor.send, and it calls this before begin()."""
        if kind not in KINDS:
            raise E.invalid_request(
                f"trace.action must be one of {KINDS}, got {kind!r}: an unclassified send cannot "
                "be paced, and pacing is not optional on a shared consumer account.")
        requested = validate_constraints(requested or {})
        if requested.get("respect_quiet_hours") is False:
            # The contract carries this flag; on this rail it has exactly one legal value. A caller
            # that asks to bypass the window is refused rather than quietly ignored, so the
            # misunderstanding surfaces in a test run and not in a candidate's notification at 03:00.
            raise E.invalid_request(
                "constraints.respect_quiet_hours=false is not honoured on the phone rail: the "
                "window is a shared-handset house rule, not a per-request option")
        day_start, day_end = self.day_bounds(now)
        spent_today = self.ledger.count_spent(day_start, day_end)
        spent_number = self.ledger.count_spent(day_start, day_end, phone=phone)
        first_touches = self.ledger.count_spent(day_start, day_end, kind=FIRST_TOUCH)

        # slower-only reconciliation of what the caller asked for
        cap_number = min(self.cap, int(requested.get("daily_cap", self.cap)))
        cap_first_touch = min(self.pacing.first_touches_per_day,
                              int(requested.get("first_touch_daily_cap",
                                                self.pacing.first_touches_per_day)))
        min_gap = max(self.gap_floor(kind), float(requested.get("min_gap_ms", 0)) / 1000.0)

        def grant(next_at):
            return Grant(kind=kind, spent_today=spent_today, spent_today_this_number=spent_number,
                         first_touches_today=first_touches, effective_min_gap_sec=min_gap,
                         effective_per_number_cap=cap_number,
                         effective_first_touch_daily_cap=cap_first_touch,
                         next_slot_at=L.utc(next_at))

        # 1. Quiet hours and Sunday, for EVERY outbound including replies (TASK-127 hole #2).
        if not self.window_open(now):
            opens = self.window_opens_at(now)
            raise E.rail_parked(
                "outside the active window "
                f"{self.pacing.active_hours[0]}-{self.pacing.active_hours[1]} "
                f"{self.pacing.timezone}" + (", Sunday blocked" if self.local(now).weekday() == 6 else ""),
                next_slot_at=L.utc(opens), window="closed", **_grant_detail(grant(opens)))

        # 2. Per-recipient day. The one number nobody has named yet; the executor refuses to run
        #    without it rather than pick one here.
        if spent_number >= cap_number:
            nxt = day_end + timedelta(seconds=self.rng.randint(0, 900))
            raise E.rail_parked(
                f"per-number daily cap reached ({spent_number}/{cap_number}) for this recipient",
                next_slot_at=L.utc(nxt), cap="per_number", **_grant_detail(grant(nxt)))

        # 3. Their global first-touch caps: 10 a day, 4 an hour (rolling, which is the stricter
        #    reading of a per-hour limit at the boundary).
        if kind == FIRST_TOUCH:
            if first_touches >= cap_first_touch:
                nxt = day_end + timedelta(seconds=self.rng.randint(0, 900))
                raise E.rail_parked(
                    f"first-touch daily cap reached ({first_touches}/{cap_first_touch})",
                    next_slot_at=L.utc(nxt), cap="first_touch_daily", **_grant_detail(grant(nxt)))
            hour_ago = now - timedelta(hours=1)
            per_hour = self.ledger.count_spent(hour_ago, now, kind=FIRST_TOUCH)
            if per_hour >= self.pacing.first_touches_per_hour:
                nxt = now + timedelta(seconds=self.jittered_gap(FIRST_TOUCH))
                raise E.rail_parked(
                    f"first-touch hourly cap reached ({per_hour}/{self.pacing.first_touches_per_hour})",
                    next_slot_at=L.utc(nxt), cap="first_touch_hourly", **_grant_detail(grant(nxt)))

        # 4. The gap. Global between first touches (they are different people seeing one number
        #    wake up); per recipient between bubbles.
        last = (self.ledger.last_spent_at(kind=FIRST_TOUCH) if kind == FIRST_TOUCH
                else self.ledger.last_spent_at(phone=phone))
        if last is not None:
            since = (now - _parse(last)).total_seconds()
            if since < min_gap:
                nxt = now + timedelta(seconds=max(self.jittered_gap(kind), min_gap - since))
                raise E.rail_parked(
                    f"min gap not elapsed: {since:.1f}s of {min_gap:.0f}s since the last send",
                    next_slot_at=L.utc(nxt), reason="min_gap", **_grant_detail(grant(nxt)))

        return grant(now + timedelta(seconds=self.jittered_gap(kind)))

    def quota(self, now, *, phone=None):
        """The health block. Read-only; it never refuses and never draws a decision."""
        day_start, day_end = self.day_bounds(now)
        out = {"window_open": self.window_open(now),
               "timezone": self.pacing.timezone,
               "active_hours": list(self.pacing.active_hours),
               "sent_today": self.ledger.count_spent(day_start, day_end),
               "first_touches_today": self.ledger.count_spent(day_start, day_end, kind=FIRST_TOUCH),
               "first_touch_daily_cap": self.pacing.first_touches_per_day,
               "per_number_daily_cap": self.cap,
               "day_starts_at": L.utc(day_start)}
        if phone is not None:
            out["sent_today_this_number"] = self.ledger.count_spent(day_start, day_end, phone=phone)
        return out


def _grant_detail(grant):
    return {"spent_today": grant.spent_today,
            "spent_today_this_number": grant.spent_today_this_number,
            "first_touches_today": grant.first_touches_today}


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
