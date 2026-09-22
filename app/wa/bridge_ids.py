"""Turn-scoped deterministic ``client_msg_id`` for the phone rail (TASK-114).

On this rail the id WE mint is the only id an outbound message ever has: the lane behind the
executor has no message identifier of any kind (decision-8 item 5), so nothing comes back to key a
send on. That makes the key a correctness device, not bookkeeping -- it is what stops one answer
being delivered twice.

THE FAILURE CHAIN THIS EXISTS TO FIX, verified in this tree:

1. a send raises -- ``app/wa/api.py:950`` (``wamid = cl.send_text(...)`` inside ``_send``,
   ``app/wa/api.py:920``);
2. ``send_and_record`` records ``wa_send_failures`` and re-raises -- ``app/wa/api.py:973-974``;
3. the caller finishes the reply-turn claim as ``skipped_error`` -- ``app/wa/api.py:885``;
4. ``app/wa/store.py:311-318`` documents ``skipped_error`` as **reclaimable**: "those all mean no
   reply decision was made yet, so a later retry (catch-up) must still be allowed to try";
5. ``deploy/pflege-wa-catchup.timer`` re-drives the turn three minutes later --
   ``app/wa/luna/catchup.py`` -> ``API.process_phones`` -> ``process_owed_turn``
   (``app/wa/api.py:708``, whose ``turn_key`` is that inbound message's own id);
6. the brain runs again (``app/wa/api.py:860`` luna, ``:867`` deterministic) and produces **new**
   bubbles. A per-call random key would make them a second delivery to a real candidate.

On the Cloud API the window between pressing send and knowing is milliseconds. On a phone-mediated
send it is 20-60 s and is the dominant failure class, so the key has to be a pure function of the
turn: same phone, same inbound message, same action, same bubble index -> same key, in any process,
after any restart, forever. The executor's ledger then applies first-body-wins (TASK-130).

``turn_key`` is a REQUIRED, EXPLICIT argument and has no default on purpose. On the Meta rail it is
the inbound wamid that already scopes the reply-turn claim (``app/wa/store.py:305-309``); on this
rail there is no provider id at all, so it is the inbound id we mint ourselves (TASK-131). Either
way it belongs to the caller who knows which message is being answered -- a default here would
silently key two different turns the same.

``bubble_index`` is first-class, not a detail: one bubble is one HTTP call and one flock
acquisition, so a two-bubble reply whose second bubble fails never re-sends the first.

KNOWN RESIDUAL, stated not guarded: if a regenerated reply has MORE bubbles than the first attempt,
the extra index is a new key and sends, so the candidate sees a fragment. Rare, additive, accepted
(TASK-114); it is measured, not defended against.
"""
import hashlib
import re

# "wab" = WhatsApp bridge, "o" = outbound. The inbound half (TASK-131) mints "wab.i." / "wab.m."
# ids into the same id space, which is why the direction is in the prefix.
CONVERSATIONAL_PREFIX = "wab.o."
CAMPAIGN_PREFIX = "wab.o.camp."
# Half a sha256, hex. Long enough that a collision is not a thing that happens, short enough to read
# in a log line next to a Meta wamid.
DIGEST_CHARS = 32

_E164_RE = re.compile(r"\+\d+\Z")


def require_text(value, what):
    """A key component that must be there. Empty is a caller bug, never something to fill in."""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string, got {type(value).__name__}: {value!r}")
    if not value.strip():
        raise ValueError(f"{what} is empty -- a client_msg_id cannot be keyed on a missing {what}")
    if value != value.strip():
        raise ValueError(f"{what} has surrounding whitespace: {value!r} (it would key two spellings apart)")
    return value


def require_e164(phone):
    """E.164 as this repo canonicalizes it (``app/wa/phones.py``). Not normalized here: silently
    rewriting '0170...' to '+49170...' would hand the same message two different keys depending on
    which caller spelled it -- the whole point of the key is that it cannot drift."""
    require_text(phone, "phone")
    if not _E164_RE.match(phone):
        raise ValueError(f"phone {phone!r} is not E.164 (+ then digits) -- canonicalize it with "
                         f"app.wa.phones.canonicalize_phone before minting a key")
    return phone


def require_index(value, what, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be an int, got {type(value).__name__}: {value!r}")
    if value < minimum:
        raise ValueError(f"{what} is {value}, below {minimum}")
    return value


def reply_key(*, phone, turn_key, action, bubble_index):
    """-> the ``client_msg_id`` for one bubble of one conversational turn.

    ``"wab.o." + sha256("phone|turn_key|action|bubble_index")[:32]``. Every argument is
    keyword-only and required: this is the identity of a message to a real person, and a positional
    mix-up between ``turn_key`` and ``action`` would silently produce a different, equally
    plausible-looking key.
    """
    phone = require_e164(phone)
    turn_key = require_text(turn_key, "turn_key")
    action = require_text(action, "action")
    bubble_index = require_index(bubble_index, "bubble_index", 0)
    material = f"{phone}|{turn_key}|{action}|{bubble_index}"
    return CONVERSATIONAL_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def campaign_key(*, campaign_id, phone, attempt):
    """-> the ``client_msg_id`` for one campaign attempt: ``wab.o.camp.<campaign_id>.<phone>.<attempt>``.

    Readable rather than hashed, because every component is already a natural key the campaign
    holds: ``wa_campaign_sends`` is unique on (campaign_id, phone, attempt) and
    ``store.claim_campaign_send`` (``app/wa/store.py:929``) hands back the 1-based attempt number
    before the POST. That gives idempotency across process death -- strictly better than what the
    Cloud API gives us today -- and lets an operator read the campaign, the recipient and the
    attempt straight off a ledger row on the executor.
    """
    campaign_id = require_text(campaign_id, "campaign_id")
    phone = require_e164(phone)
    attempt = require_index(attempt, "attempt", 1)
    return f"{CAMPAIGN_PREFIX}{campaign_id}.{phone}.{attempt}"
