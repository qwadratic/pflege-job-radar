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
     A row that will NEVER exist counts here too (TASK-257): executor.py's own ORDER OF OPERATIONS
     puts governor.check and take_phone before ledger.begin, so a rail_parked 429 or a lost flock
     race 503 leaves no row at all -- the same "nothing was typed" fact NOT_ATTEMPTED already
     carries, just never written down. Holding that state more strictly than NOT_ATTEMPTED itself
     would be, is an inconsistency in this state machine, not a safety margin.
  3. MANUALLY RESOLVED -- a human looked at the artefact (op_id names every file for it) and called
     `resolve_op` (POST /v1/ops/<id>/resolve, tools/wa_bridge.py `ops resolve`). This is the only
     path for a failed op that never minted a client_msg_id at all (read_thread,
     send_photos/gallery/document -- TASK-131's own mechanism-proof sends have no idempotency key
     to reconcile against).

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

#: outbound.state values that mean "this send's fate is known and it is not still open" -- owned by
#: bridge/ledger.py (TASK-277: sweep()'s own outbound delete keys on the exact same set now, so a
#: still-open UNCONFIRMED row can never drift out of sync between what this module calls "settled"
#: and what sweep() is willing to remove).
SAFE_OUTBOUND_STATES = L.SAFE_OUTBOUND_STATES


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


#: TASK-265, revised by TASK-277: through the normal review_and_sweep pass this branch should no
#: longer fire for a HELD file at all -- ledger.retire_unreferenced_ops only removes a phone_ops
#: row once THIS SAME pass's own classification says nothing on disk still names it, so a row and
#: a file that is still held now go missing and get reviewed together, never one without the
#: other. What this still catches: artefacts orphaned by ledger.sweep()'s OLD age-only delete
#: before this fix shipped, and any direct caller of classify_op_artifact (its own docstring) that
#: skips review_and_sweep entirely -- both read the same ledger this module does, never a
#: guaranteed-consistent one, so the row can still be legitimately gone here. The file's own mtime
#: is the only signal left to tell that apart from a row that simply never existed.
def _artifact_aged_out(path, now):
    if path is None or now is None:
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # The whole question this answers is "what does the file's own age say", so a file whose
        # age cannot be read answers it with "cannot tell" and the caller falls back to the generic
        # reason. Raising instead would abort the entire hourly pass over every OTHER artefact --
        # the maintenance guard added alongside this would catch it, count it, and hide it.
        return False
    return (now.timestamp() - mtime) / 86400 >= L.LEDGER_RETENTION_DAYS


def classify_op_artifact(ledger, op_id, path=None, now=None):
    """-> ("delete"|"hold", reason) for a debug-capture screenshot or recording named after
    ``op_id`` (TASK-228's own filename convention). ``path``/``now`` let the "row is missing"
    branch below tell LEDGER_RETENTION_DAYS aging it out apart from a row missing for no such
    reason; _sweep_one_kind always passes them, a direct caller that only wants the "never
    existed" case may omit them."""
    row = ledger.op_status(op_id)
    if row is None:
        if _artifact_aged_out(path, now):
            return "hold", (
                "op row aged out past LEDGER_RETENTION_DAYS while this artefact was still held -- "
                "resolve manually or extend retention, not a bug")
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
        if entry is None:
            # No row means ledger.begin() never ran (executor.py's own ORDER OF OPERATIONS puts
            # governor.check and take_phone before begin, on purpose) -- provably nothing was
            # typed, the same fact NOT_ATTEMPTED (a row that DOES exist) already carries and that
            # SAFE_OUTBOUND_STATES above already trusts. Age-gated by the caller (_sweep_one_kind),
            # same as every other "delete" verdict here.
            return "delete", "no outbound row: refused before ledger.begin, nothing was typed"
        if entry.state in SAFE_OUTBOUND_STATES:
            return "delete", f"outbound resolved: {entry.state}"
        return "hold", f"outbound still {entry.state}"
    if row.get("resolved_at"):
        return "delete", "manually resolved"
    return "hold", "failed and not yet resolved"


def classify_escalation_shot(ledger, path, shot_index, now=None):
    """-> ("delete"|"hold", reason) for an old-style escalation shot (bridge/adb_driver.py::
    escalation_shot, no op_id in its filename). ``shot_index`` is
    ``ledger.escalation_shot_index()``, passed in so a sweep over many files builds it once.
    ``now``, when given, lets a missing journal entry be told apart from ledger.sweep()'s own
    `delete from journal where at < ?` cutoff, same reasoning as classify_op_artifact above."""
    client_msg_id = shot_index.get(str(path))
    if client_msg_id is None:
        if _artifact_aged_out(path, now):
            return "hold", (
                "escalation_shot journal row aged out past LEDGER_RETENTION_DAYS while this "
                "artefact was still held -- resolve manually or extend retention, not a bug")
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


def _held_age_sec(path, now):
    """-> how long ago ``path`` was last written, or ``None`` when its mtime cannot be read (same
    "cannot tell" fallback as ``_artifact_aged_out`` above -- a stat race here must not crash the
    whole hourly pass over one file's own age)."""
    try:
        return now.timestamp() - path.stat().st_mtime
    except OSError:
        return None


def _sweep_one_kind(driver, ledger, now, *, list_candidates, op_id_of, classify_unrecognized, days):
    shot_index = ledger.escalation_shot_index()
    deleted, held = 0, []
    for path in list_candidates(now, days=days):
        op_id = op_id_of(path)
        if op_id:
            verdict, reason = classify_op_artifact(ledger, op_id, path, now)
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
            held.append((path, op_id, reason))
    for path, op_id, reason in held:
        ledger.note(now, "retention_held", None, path=str(path), reason=reason)
    return {"deleted": deleted, "held": len(held)}, held


def review_and_sweep(executor, now, *, screenshot_days, recording_days):
    """-> {"screenshots": {"deleted", "held"}, "recordings": {"deleted", "held"},
    "oldest_held_age_sec"}. The one entry point bridge/server.py::maintenance_once calls --
    replaces the old age-only sweep_screenshots/sweep_recordings.

    TASK-277: this pass's own held list is also the only place that knows, right now, which
    op_ids still have a screenshot or recording naming them -- ``ledger.retire_unreferenced_ops``
    below is called with exactly that set, every pass: a row in ``held_op_ids`` keeps its
    ``resolve_op`` escape hatch open no matter how old it is; every other row past
    ``LEDGER_RETENTION_DAYS`` is swept, same as ledger.sweep() used to do for phone_ops directly.
    """
    driver, ledger = executor.driver, executor.ledger
    screenshots, shot_held = _sweep_one_kind(
        driver, ledger, now, list_candidates=driver.list_screenshot_candidates,
        op_id_of=_op_id_from_screenshot,
        classify_unrecognized=lambda ledger_, path, shot_index: classify_escalation_shot(
            ledger_, path, shot_index, now),
        days=screenshot_days)
    recordings, rec_held = _sweep_one_kind(
        driver, ledger, now, list_candidates=driver.list_recording_candidates,
        op_id_of=_op_id_from_recording,
        classify_unrecognized=lambda ledger_, path, shot_index: _unrecognized_recording(ledger_, path),
        days=recording_days)
    held = shot_held + rec_held
    held_op_ids = {op_id for _, op_id, _ in held if op_id}
    ledger.retire_unreferenced_ops(now, held_op_ids)
    ages = [age for path, _, _ in held if (age := _held_age_sec(path, now)) is not None]
    return {"screenshots": screenshots, "recordings": recordings,
            "oldest_held_age_sec": max(ages) if ages else None}
