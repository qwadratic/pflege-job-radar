"""Review before delete (TASK-230, Ivan 2026-09-23): "перед удалением пересматривать чтобы
убеждаться что удаляем либо хєппи, либо уже резолвнутые issue" -- before a postmortem screenshot or
recording is deleted for being older than SCREENSHOT_RETENTION_DAYS, this module decides whether
what it documents actually has a known-good outcome. Age past the cutoff is necessary but never
sufficient on its own.

THE THREE WAYS AN ARTEFACT BECOMES SAFE TO DELETE:
  1. HAPPY -- the phone_ops row it belongs to finished OP_DONE, AND (for the two kinds where
     OP_DONE alone does not settle it) whatever it did confirms the thing it was for: a `reconcile`
     left no `indeterminate` verdict, a `send_photos`/`send_gallery`/`send_document` read back an
     actual delivery tick. Adversarial review (2026-09-23) found OP_DONE alone insufficient for
     both -- `Executor.reconcile` never raises on `indeterminate`, and the media-send methods never
     gate on a tick the way `send()` does -- so those two are the ones this module still has to
     look inside the result for.
  2. AUTO-RESOLVED -- it belongs to a `send` (the only phone_ops kind that mints a client_msg_id),
     and that send's own outbound ledger row has since reached SENT/ABSENT/NOT_ATTEMPTED -- a tick
     was read, or a reconcile scan settled it. This is the SAME state machine bridge/ledger.py's
     own sweep() already trusts for outbound rows; nothing new is invented here, just read.
  3. MANUALLY RESOLVED -- a human looked at the artefact (op_id names every file for it) and called
     `resolve_op` (POST /v1/ops/<id>/resolve, tools/wa_bridge.py `ops resolve`). This is the only
     path for a failed op that never minted a client_msg_id at all (clear_chat, delete_chat,
     read_thread, send_photos/gallery/document -- TASK-131's own mechanism-proof sends have no
     idempotency key to reconcile against).

Anything that is none of the three is HELD, however old -- logged as `retention_held` so a growing
backlog is visible in the journal, never silently kept or silently dropped. Ivan, 2026-09-23:
"там на макмини вроде, достаточно места" -- there is room on the mini for this to be the default.
"""
from __future__ import annotations

import re

from . import ledger as L

#: op_id, as bridge/dispatcher.py::mint_op_id() writes it: "op." + 24 hex chars.
_OP_ID = r"op\.[0-9a-f]{24}"
SCREENSHOT_OP_RE = re.compile(rf"^({_OP_ID})_")
RECORDING_OP_RE = re.compile(rf"^({_OP_ID})\.mp4$")

#: outbound.state values that mean "this send's fate is known and it is not still open" -- the same
#: set bridge/ledger.py::unresolved() excludes (it names ATTEMPTING and UNCONFIRMED explicitly;
#: this is every OTHER state).
SAFE_OUTBOUND_STATES = frozenset({L.SENT, L.ABSENT, L.NOT_ATTEMPTED})


def _op_id_from_screenshot(path):
    m = SCREENSHOT_OP_RE.match(path.name)
    return m.group(1) if m else None


def _op_id_from_recording(path):
    m = RECORDING_OP_RE.match(path.name)
    return m.group(1) if m else None


#: send_photos/send_gallery/send_document (TASK-131, "mechanism proof, not production ready") never
#: gate on a delivery tick the way send() does -- Executor.send_photos/gallery/document return
#: normally with whatever tick string the driver read back, even "" (no tick drawn yet). OP_DONE for
#: these three therefore means only "the driver call returned", not "WhatsApp confirmed delivery" --
#: unlike every other kind, where OP_DONE really is the end of the question.
def _media_send_confirmed(kind, result):
    if kind == "send_photos":
        sent = result.get("sent") or []
        return bool(sent) and all(item.get("tick") for item in sent)
    if kind in ("send_gallery", "send_document"):
        return bool(result.get("tick"))
    return True


def _reconcile_settled(result):
    """Executor.reconcile()/._scan() is explicitly three-valued and never raises (bridge/
    executor.py) -- an 'indeterminate' verdict means the send it was checking is exactly as open
    as before it ran, so a reconcile op reaching OP_DONE is not itself the happy outcome; every
    verdict it returned has to be one that closes the question."""
    return all(r.get("verdict") != "indeterminate" for r in (result or []))


def classify_op_artifact(ledger, op_id):
    """-> ("delete"|"hold", reason) for a debug-capture screenshot or recording named after
    ``op_id`` (TASK-228's own filename convention)."""
    row = ledger.op_status(op_id)
    if row is None:
        # phone_ops rows outlive these files by design (LEDGER_RETENTION_DAYS=30 vs 14 days of
        # artefacts) -- reaching here means something deleted the row out from under its own
        # artefact, which is a bug elsewhere, not a reason to guess here.
        return "hold", "op_id not found in phone_ops"
    if row["state"] == L.OP_DONE:
        kind = row.get("kind")
        if kind == "reconcile":
            if _reconcile_settled(row.get("result")):
                return "delete", "op done"
            return "hold", "reconcile left an indeterminate verdict"
        if not _media_send_confirmed(kind, row.get("result") or {}):
            return "hold", "op done but no delivery tick was confirmed"
        return "delete", "op done"
    if row["state"] != L.OP_FAILED:
        return "hold", f"op still {row['state']}"
    client_msg_id = ((row.get("args") or {}).get("req") or {}).get("client_msg_id")
    if client_msg_id:
        # A send's own client_msg_id is the ONLY signal that ever settles it -- never fall through
        # to resolved_at here, or a human resolving the op (meant for the no-client_msg_id kinds
        # below) could paper over a send whose outbound row is still genuinely open.
        entry = ledger.get(client_msg_id)
        if entry is not None and entry.state in SAFE_OUTBOUND_STATES:
            return "delete", f"outbound resolved: {entry.state}"
        return "hold", f"outbound still {entry.state if entry is not None else 'missing'}"
    if row.get("resolved_at"):
        return "delete", "manually resolved"
    return "hold", "failed and not yet resolved"


def classify_escalation_shot(ledger, path, shot_index):
    """-> ("delete"|"hold", reason) for an old-style escalation shot (bridge/adb_driver.py::
    escalation_shot, no op_id in its filename). ``shot_index`` is
    ``ledger.escalation_shot_index()``, passed in so a sweep over many files builds it once."""
    client_msg_id = shot_index.get(str(path))
    if client_msg_id is None:
        return "hold", "no escalation_shot journal entry for this file"
    entry = ledger.get(client_msg_id)
    if entry is None:
        return "hold", "client_msg_id not found in outbound"
    if entry.state in SAFE_OUTBOUND_STATES:
        return "delete", f"outbound resolved: {entry.state}"
    return "hold", f"outbound still {entry.state}"


def _unrecognized_recording(ledger, path):
    # Recordings are ALWAYS TASK-228 debug-capture artefacts (op_id-named) -- there is no
    # old-style, non-op_id recording the way there is for screenshots (escalation shots predate
    # screen recording entirely), so a filename this module does not recognise is held, never
    # guessed at.
    return "hold", "unrecognised recording filename"


def _sweep_one_kind(driver, ledger, now, *, list_candidates, op_id_of, classify_unrecognized, days):
    shot_index = ledger.escalation_shot_index()
    deleted, held = 0, []
    for path in list_candidates(now, days=days):
        op_id = op_id_of(path)
        if op_id:
            verdict, reason = classify_op_artifact(ledger, op_id)
        else:
            verdict, reason = classify_unrecognized(ledger, path, shot_index)
        if verdict == "delete":
            # Deleted the instant it is classified (TASK-230 fix), not batched to the end of the
            # whole directory's worth of DB reads: a resend (catchup.py, a human retry -- both
            # legitimate on a RESENDABLE/ABSENT/NOT_ATTEMPTED row) can reopen the very outbound row
            # that made a file look safe, and every file this loop has already classified but not
            # yet deleted sits exposed to that for as long as the batch takes to finish.
            deleted += driver.delete_paths([path])
        else:
            held.append((path, reason))
    for path, reason in held:
        ledger.note(now, "retention_held", None, path=str(path), reason=reason)
    return {"deleted": deleted, "held": len(held)}


def review_and_sweep(executor, now, *, screenshot_days, recording_days):
    """-> {"screenshots": {"deleted", "held"}, "recordings": {"deleted", "held"}}. The one entry
    point bridge/server.py::maintenance_once calls -- replaces the old age-only
    sweep_screenshots/sweep_recordings."""
    driver, ledger = executor.driver, executor.ledger
    screenshots = _sweep_one_kind(
        driver, ledger, now, list_candidates=driver.list_screenshot_candidates,
        op_id_of=_op_id_from_screenshot,
        classify_unrecognized=lambda ledger_, path, shot_index: classify_escalation_shot(
            ledger_, path, shot_index),
        days=screenshot_days)
    recordings = _sweep_one_kind(
        driver, ledger, now, list_candidates=driver.list_recording_candidates,
        op_id_of=_op_id_from_recording,
        classify_unrecognized=lambda ledger_, path, shot_index: _unrecognized_recording(ledger_, path),
        days=recording_days)
    return {"screenshots": screenshots, "recordings": recordings}
