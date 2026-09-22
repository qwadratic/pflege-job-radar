"""Reporting-only view over the Luna card + message history (TASK-71): a stage label and a "ball"
(whose turn it is to act), derived from state luna_brain.py already tracks rather than new columns
to keep in sync. Used by the migration script and the dry-run shadow tool (TASK-72) -- nothing in
the live turn() path needs either of these, they exist purely so a human (or a report) can see
where a thread stands without reading the raw card.

Not to be confused with luna_brain.funnel_stage / card["stage"] (TASK-144), which is the funnel stage
the CONVERSATION resumes from. STAGES below is the operator's coarser label for a whole thread,
including the terminal states a funnel stage has no name for (declined, already_placed).
"""
from .. import store as ST
from ..luna_brain import SCOREBOARD_GATES, requirement_scoreboard

STAGES = ("new_lead", "declined", "already_placed", "not_placeable", "qualifying", "documents_in", "ready",
          "consented")


def stage_for(card):
    """One label from STAGES, derived from the same card fields requirement_scoreboard() already
    reads -- new_lead (nothing known yet) through consented (anonymized-send agreed). Does not
    track anything past consented (interview scheduling, clinic submission): this harness's funnel
    intentionally stops there today (VENDORED.md), so there is nothing further to label yet.
    TASK-101: declined (card.declined, set in code on the model's decline flag) and already_placed
    (card.already_placed without open_to_new_position) come first -- the candidate ended it."""
    card = card or {}
    if card.get("declined"):
        return "declined"
    if card.get("already_placed") and not card.get("open_to_new_position"):
        return "already_placed"
    if card.get("qualification_ok") is False:
        return "not_placeable"
    if card.get("anonymous_send_consent"):
        return "consented"
    board = requirement_scoreboard(card)
    # Gates only (luna_brain.SCOREBOARD_GATES): the scoreboard also carries computed hints --
    # next_objective (TASK-91), stage and stage_since (TASK-144) -- which are not satisfied|open|blocked
    # statuses at all. handoff_consent is skipped on top of that because this function's own "ready"
    # means "everything except consent".
    if all(board[gate] == "satisfied" for gate in SCOREBOARD_GATES if gate != "handoff_consent"):
        return "ready"
    if card.get("cv_text") or card.get("urkunde_text"):
        return "documents_in"
    if card.get("qualification_path"):
        return "qualifying"
    return "new_lead"


def ball_for(conn, phone):
    """us: the candidate's last message has no reply behind it yet (a reply is owed). them: we
    already answered. silent: the candidate wrote last and the brain chose silence for that message
    (claim state ST.NO_SEND_STATE, TASK-101) -- answered, and not waiting on the candidate either, so
    neither catch-up nor a follow-up nudge acts on it. none: no messages at all."""
    row = conn.execute(
        "select direction, wamid from wa_messages where phone=? order by id desc limit 1", (phone,)).fetchone()
    if row is None:
        return "none"
    if row["direction"] != "in":
        return "them"
    return "silent" if ST.reply_turn_claim_state(conn, phone, row["wamid"]) == ST.NO_SEND_STATE else "us"


def report_row(conn, phone):
    """{phone, stage, ball, requirement_scoreboard, stopped, test} for one thread -- the shape a dry-run
    report or a migration sanity check reads. ``test`` is the TASK-109 flag: a number an operator tests
    the live harness with, never a candidate, so any count built from these rows can leave it out.
    None if no thread exists for this phone yet -- checked before
    calling ST.thread(), which would otherwise create one (by design, for a real inbound message;
    not appropriate for a read-only report that must not create rows just by looking)."""
    row = conn.execute("select 1 from wa_threads where phone=?", (phone,)).fetchone()
    if row is None:
        return None
    t = ST.thread(conn, phone)
    return {"phone": phone, "stage": stage_for(t["slots"]), "ball": ball_for(conn, phone),
            "requirement_scoreboard": requirement_scoreboard(t["slots"]), "stopped": t["stopped"],
            "test": t["is_test"]}
