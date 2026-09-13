"""Reporting-only view over the Luna card + message history (TASK-71): a stage label and a "ball"
(whose turn it is to act), derived from state luna_brain.py already tracks rather than new columns
to keep in sync. Used by the migration script and the dry-run shadow tool (TASK-72) -- nothing in
the live turn() path needs either of these, they exist purely so a human (or a report) can see
where a thread stands without reading the raw card.
"""
from .. import store as ST
from ..luna_brain import requirement_scoreboard

STAGES = ("new_lead", "not_placeable", "qualifying", "documents_in", "ready", "consented")


def stage_for(card):
    """One label from STAGES, derived from the same card fields requirement_scoreboard() already
    reads -- new_lead (nothing known yet) through consented (anonymized-send agreed). Does not
    track anything past consented (interview scheduling, clinic submission): this harness's funnel
    intentionally stops there today (VENDORED.md), so there is nothing further to label yet."""
    card = card or {}
    if card.get("qualification_ok") is False:
        return "not_placeable"
    if card.get("anonymous_send_consent"):
        return "consented"
    board = requirement_scoreboard(card)
    if all(v == "satisfied" for k, v in board.items() if k != "handoff_consent"):
        return "ready"
    if card.get("cv_text") or card.get("urkunde_text"):
        return "documents_in"
    if card.get("qualification_path"):
        return "qualifying"
    return "new_lead"


def ball_for(conn, phone):
    """us: the candidate's last message has no reply behind it yet (a reply is owed). them: we
    already answered. none: no messages at all, or the thread opted out."""
    row = conn.execute(
        "select direction from wa_messages where phone=? order by id desc limit 1", (phone,)).fetchone()
    if row is None:
        return "none"
    return "us" if row["direction"] == "in" else "them"


def report_row(conn, phone):
    """{phone, stage, ball, requirement_scoreboard} for one thread -- the shape a dry-run report or
    a migration sanity check reads. None if no thread exists for this phone yet -- checked before
    calling ST.thread(), which would otherwise create one (by design, for a real inbound message;
    not appropriate for a read-only report that must not create rows just by looking)."""
    row = conn.execute("select 1 from wa_threads where phone=?", (phone,)).fetchone()
    if row is None:
        return None
    t = ST.thread(conn, phone)
    return {"phone": phone, "stage": stage_for(t["slots"]), "ball": ball_for(conn, phone),
            "requirement_scoreboard": requirement_scoreboard(t["slots"]), "stopped": t["stopped"]}
