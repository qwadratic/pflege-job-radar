"""One number, one do-not-contact decision, across every thread and both rails (TASK-113, TASK-137).

WHY A NEW TABLE. Opt-out is learned today from three places, and all three stay exactly as they are:
``slots.is_stop`` (whole-word STOP vocabulary), read by both brains and turned into
``wa_threads.stopped`` in ``api.process_owed_turn``; and ``campaign.marketing_opt_out``, which reads
Meta-only signals (a ``user_preferences`` webhook, a failed status 131050). What none of them can do
is carry a refusal beyond the thread it arrived in. ``wa_threads.stopped`` is one column of one row
that every card save rewrites and ``luna.purge_test_history`` resets to 0, and the Meta signals do not
exist on the phone rail at all -- a handset produces no 131050 and no webhook. Cold first contact now
runs on that rail (Ivan, 2026-09-21; plan 2026-09-21 §4), so the only opt-out signal it can ever give
us is the word the candidate types, and that word has to outlive the thread it was typed in.

``wa_suppressions`` (declared in ``store.SCHEMA``, created by ``store.db()`` like every other table of
that file) is the single writer of record: one row per canonical phone, with the reason, the lane that
heard it, the verbatim trigger text and the timestamp. The key is ``phones.canonicalize_phone``, so
'0170...', '+49170...' and '0049170...' are one human and one row. First write wins -- the first
refusal is the record; a later ``suppress`` of the same number never overwrites its trigger or its time.

THE CHOKE POINTS, both of which raise rather than skip:
- ``api.send_and_record`` -- the only entrypoint to ``api._send``, so it covers every free-form send
  in the repo: the webhook reply, a catch-up reply, ``_media_ack``, ``_send_reopen_template`` and the
  ``luna.followups`` nudge, on whichever rail ``transport.get_client`` resolves. One point instead of
  five call sites, and a sixth send path cannot appear without going through it.
- ``campaign.send_one`` -- the campaign template POST, which never touches ``api._send``.
``luna.followups`` additionally never sees a suppressed number at all: ``store.candidate_phones``, the
pool it sweeps, filters them out the same way it already filters stopped threads.

WHY IT RAISES. A skipped send that returns a status a caller can read as success is how an opt-out gets
overtaken by a queued reply. ``SuppressedRecipient`` is a ``MetaError`` with an HTTP 4xx so
``campaign.send_one`` classifies it through its existing branch (campaign.py:654-658) as a permanent
``failed`` attempt -- ownership restored, never resent -- instead of ``uncertain``, which
``--retry-uncertain`` would send again.

NOT HERE, ON PURPOSE (TASK-137, later): the salted sha256 digest export that lets a suppression travel
to the mini without a plaintext do-not-contact list in a shared home, and the read-only import of the
colleague's ``sales_brain.suppression_list`` (filtered to ``channel_type in ('whatsapp','phone','sms')``
-- 0 rows today, all 378 are e-mail). Also not here: a backfill of phones already opted out via 131050.
It is not needed for that signal to keep working -- ``campaign.marketing_opt_out`` still reads it
directly, so no existing opt-out is lost -- and a migration that mixes the two records is a decision
someone has to take, not a default.
"""
from . import meta as M
from . import phones as P
from . import store as ST

# suppression.reason values this repo writes itself. A reason is free text: an import or an operator
# entry will carry its own wording, and rejecting an unknown one would only lose the record.
REASON_STOP = "inbound stop token"

# payload error code of SuppressedRecipient. Our own slug: a forged Meta numeric code would claim
# Meta said something it never said (TASK-113).
ERROR_CODE = "wab-suppressed"


class SuppressedRecipient(M.MetaError):
    """Raised instead of sending to a number on the do-not-contact list.

    Subclasses ``meta.MetaError`` deliberately. ``campaign.send_one`` decides in exactly one place
    whether a failed send may ever be retried: a ``MetaError`` carrying an HTTP 4xx is permanent
    (attempt ``failed``, ownership restored, no resend), anything else is ``uncertain`` -- it may have
    reached the candidate, and ``--retry-uncertain`` would post it again. A suppression is the most
    permanent refusal this system has, so it rides that existing classifier with status 403 rather
    than adding a second branch to it.

    The message names the reason, the lane and the time, never the number and never the words the
    candidate typed: it ends up in ``wa_send_failures`` and in campaign reports.
    """

    def __init__(self, record):
        super().__init__(
            f"suppressed recipient: {record['reason']} recorded {record['at']} on the {record['lane']} rail -- "
            f"no message may be sent to this number on any rail",
            status_code=403,
            payload={"error": {"code": ERROR_CODE, "message": record["reason"], "lane": record["lane"],
                               "at": record["at"]}})
        self.record = record


def _key(phone):
    """The row key: one identity per human. Raises on a number that canonicalizes to nothing rather
    than writing an empty key nothing would ever match."""
    key = P.canonicalize_phone(phone)
    if not key:
        raise ValueError(f"cannot suppress or check {type(phone).__name__} {phone!r}: it canonicalizes to no number")
    return key


def suppress(c, phone, reason, lane, trigger_text=None, at=None):
    """Record that this number must never be messaged again, on any rail. -> the row of record.

    Idempotent and first-write-wins: calling it again for the same human (in any of the four number
    spellings) leaves the original reason, lane, trigger and timestamp in place and returns them, so
    a re-delivered webhook or a second STOP cannot rewrite when the candidate first refused.

    ``trigger_text`` is the verbatim inbound text that triggered this -- the audit artefact for a
    §7 Abs. 3 UWG / §174 TKG question ("what exactly did they write, and when"). It is candidate
    content: it is stored, never logged and never printed.
    """
    c.execute("insert into wa_suppressions (phone, reason, lane, trigger_text, at) values (?,?,?,?,?) "
              "on conflict(phone) do nothing", (_key(phone), reason, lane, trigger_text, at or ST.now_iso()))
    c.commit()
    return suppression(c, phone)


def suppression(c, phone):
    """This number's suppression record, or None."""
    row = c.execute("select phone, reason, lane, trigger_text, at from wa_suppressions where phone=?",
                    (_key(phone),)).fetchone()
    return dict(row) if row is not None else None


def is_suppressed(c, phone):
    return suppression(c, phone) is not None


def suppressions(c):
    """Every suppression, newest first -- the read helper for reporting and for an operator asking
    "who is on the list, and why". Carries phone numbers and the verbatim words candidates typed:
    it belongs in an owner-only response or a 0600 report, never in a log line."""
    rows = c.execute("select phone, reason, lane, trigger_text, at from wa_suppressions "
                     "order by at desc, phone").fetchall()
    return [dict(r) for r in rows]


def assert_not_suppressed(c, phone):
    """Raise ``SuppressedRecipient`` if this number is on the list. The check every send path makes."""
    record = suppression(c, phone)
    if record is not None:
        raise SuppressedRecipient(record)
