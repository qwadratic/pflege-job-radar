"""TASK-121/TASK-122: turn a typed reply back into the button id app/wa/brain.py and
app/wa/luna_brain.py already expect from a genuine tap.

Reply buttons do not exist on a phone rail (app/wa/bridge.py:send_buttons renders the titles as
numbered text instead), so nothing on that rail can ever set ``button_id`` by tapping. Without this
module a typed "1" or "ja" reaches the brain as ordinary free text: it costs one turn and, worse, it
can never close the anonymous-send consent gate, which app/wa/luna_brain.py grants exclusively on
``button_id == CONSENT_YES_ID``.

Built server side, in code, as CLAUDE.md requires for anything decided rather than merely worded:
never by asking the model. Wired at the one place both the webhook worker and catchup.py go through
(``app/wa/api.py:process_owed_turn``), so both callers are covered by the same recovery.

THE OFFER IS READ, NOT RECONSTRUCTED. ``app/wa/api.py``'s ``_send`` already records the exact set of
buttons it rendered on the outbound row (``kind='buttons'``, ``meta={"action":..., "buttons":...}``)
-- this module reads that row rather than re-deriving the question from the card, which would drift
the moment either side changed independently of the other.

THREE TIERS, MOST TO LEAST CERTAIN: the ordinal ("1", "1.", "1)", "(1)"); the exact rendered title,
case- and diacritic-folded (``fold``) so "Prüfung" and "Pruefung" land the same; a unique title
prefix of at least ``MIN_PREFIX_LEN`` characters. Two buttons matching at any tier is treated as no
match at all -- an ambiguous echo answers nothing rather than guessing. A fourth, weaker tier -- a
small German yes/no keyword map -- applies only when the offer has exactly two buttons; a longer
list has no ambiguity-safe way to read a bare "ja" as one specific choice among many.

HARD GATES. The offer considered is the thread's newest message of any kind and only when that
message actually IS a buttons offer: a text bubble sent after it (a media ack, a second thought)
retires the offer exactly as a newer inbound message would, because in both cases the button
question is no longer the newest thing said. TTL is measured between the offer and this reply, not
wall-clock "now" -- a catch-up run long after an outage must not retroactively expire an offer the
candidate answered within minutes.

AN UNMATCHED REPLY IS NOT AN ERROR. ``recover_button_id`` returns None and the caller passes the
reply through to the brain as ordinary text, which is exactly what happens today without this module.

CONSENT IS THE ONE EXCEPTION (TASK-122, plan ADDENDUM item 5, Ivan 2026-09-21). A false consent
match is the one unacceptable error in this whole design, so a resolved match against
``luna_brain.CONSENT_YES_ID``/``CONSENT_NO_ID`` is only honoured when ``WA_BRIDGE_SYNTHETIC_CONSENT``
is on AND the match came from the ordinal or exact-title tier -- a prefix or a keyword guess never
sets consent, flag on or off. The "explicit confirmation turn" the task describes is structural
rather than a second round trip: the hard gates above already mean a match only ever comes from the
one reply that is genuinely the newest thing said since the offer went out, so this IS that turn.
The verbatim typed token is stored in the matched inbound message's own meta (``button_recovery``)
as the audit artefact either way -- the same record serves the general "which tier matched" bookkeeping
and the consent-specific audit trail.

``luna_brain`` is imported lazily, inside the function, and only when ``WA_BRAIN=luna`` -- the same
laziness ``app/wa/api.py`` already practises, so a deterministic-brain deployment never pays for
importing it.
"""
import json
import re
import unicodedata
from datetime import datetime, timedelta

from .. import config as C
from .. import store as ST

# TASK-121: an offer answered more than this long after it was made is stale -- read against the
# reply's own timestamp, not against whenever a delayed catch-up run happens to execute.
OFFER_TTL = timedelta(hours=48)
# A shorter typed prefix is too likely to collide with an unrelated button by accident.
MIN_PREFIX_LEN = 4

TIER_ORDINAL = "ordinal"
TIER_EXACT_TITLE = "exact_title"
TIER_PREFIX = "prefix"
TIER_KEYWORD = "keyword"
# TASK-122: the only tiers certain enough to grant consent -- an ordinal or the rendered title
# itself is an unambiguous echo of what was actually offered; a prefix or a keyword is a guess.
CONSENT_GRADE_TIERS = (TIER_ORDINAL, TIER_EXACT_TITLE)

_ORDINAL_RE = re.compile(r"^\(?\s*(\d{1,2})\s*[.)]?\s*\)?$")

_UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

# TASK-121: the keyword map applies only when the offer has exactly two buttons, and reads the
# choice by POSITION -- buttons[0] for a "yes" word, buttons[-1] for a "no" word -- because every
# two-button offer in this codebase already lists the affirmative option first (CONSENT_BUTTONS in
# app/wa/luna_brain.py; housing:ja/nein and ho:ja/nein in app/wa/brain.py) and neither the button id
# nor the title carries a uniform yes/no marker a matcher could read instead (ho:nein's own title is
# "Noch nicht", which does not contain "nein" at all). A future two-button offer that lists the
# negative option first would be misread by this tier; nothing in this codebase does that today.
YES_WORDS = frozenset({"ja", "gerne", "ok", "passt"})
NO_WORDS = frozenset({"nein", "nee", "kein interesse"})


def fold(text):
    """Casefold, German-transliterate the umlauts (ae/oe/ue/ss), strip any diacritic NFKD still
    leaves and any punctuation, collapse whitespace. ``fold("Prüfung") == fold("Pruefung")`` and
    ``fold("JA GERNE") == fold("Ja, gerne")``."""
    s = unicodedata.normalize("NFC", text or "").casefold()
    s = s.translate(_UMLAUT_FOLD)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _NON_WORD_RE.sub(" ", s)
    return " ".join(s.split())


def _match_ordinal(text, buttons):
    m = _ORDINAL_RE.match((text or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return buttons[n - 1]["id"] if 1 <= n <= len(buttons) else None


def _match_exact_title(folded, buttons):
    hits = {b["id"] for b in buttons if fold(b.get("title")) == folded}
    return next(iter(hits)) if len(hits) == 1 else None


def _match_prefix(folded, buttons):
    if len(folded) < MIN_PREFIX_LEN:
        return None
    hits = {b["id"] for b in buttons if fold(b.get("title")).startswith(folded)}
    return next(iter(hits)) if len(hits) == 1 else None


def _match_keyword(folded, buttons):
    if len(buttons) != 2:
        return None
    if folded in YES_WORDS:
        return buttons[0]["id"]
    if folded in NO_WORDS:
        return buttons[-1]["id"]
    return None


def match(text, buttons):
    """The offer actually made (a list of ``{"id", "title"}`` dicts) against a typed reply.
    -> ``(button_id, tier)``, or ``(None, None)`` when nothing resolves unambiguously."""
    button_id = _match_ordinal(text, buttons)
    if button_id:
        return button_id, TIER_ORDINAL
    folded = fold(text)
    if not folded:
        return None, None
    button_id = _match_exact_title(folded, buttons)
    if button_id:
        return button_id, TIER_EXACT_TITLE
    button_id = _match_prefix(folded, buttons)
    if button_id:
        return button_id, TIER_PREFIX
    button_id = _match_keyword(folded, buttons)
    if button_id:
        return button_id, TIER_KEYWORD
    return None, None


def _offer_before(c, phone, before_id):
    """This phone's wa_messages row immediately before ``before_id``, or None. Deliberately "the
    newest message of any kind" rather than "the newest kind='buttons' row": a bubble sent after the
    offer retires it exactly like a newer inbound message would -- in both cases the button question
    is no longer the newest thing said in the thread."""
    row = c.execute("select * from wa_messages where phone=? and id<? order by id desc limit 1",
                    (phone, before_id)).fetchone()
    return dict(row) if row else None


def _live_offer(c, phone, target):
    prev = _offer_before(c, phone, target["id"])
    if prev is None or prev["direction"] != "out" or prev["kind"] != "buttons":
        return None
    offered_at = datetime.fromisoformat(prev["at"])
    replied_at = datetime.fromisoformat(target["at"])
    if replied_at - offered_at > OFFER_TTL:
        return None
    buttons = json.loads(prev["meta"] or "{}").get("buttons") or []
    if not buttons:
        return None
    return {"wamid": prev["wamid"], "buttons": buttons}


def _record(c, wamid, offer, button_id, tier, text):
    """The matched tier and the verbatim typed token, on the inbound message itself -- AC#6's
    general bookkeeping and TASK-122 AC#4's consent audit artefact are the same write."""
    row = c.execute("select meta from wa_messages where wamid=? and direction='in'", (wamid,)).fetchone()
    if row is None:
        raise RuntimeError(f"choices.recover_button_id: no inbound wa_messages row for {wamid!r}")
    meta = {**json.loads(row["meta"] or "{}"),
            "button_recovery": {"tier": tier, "button_id": button_id, "offer_wamid": offer["wamid"], "token": text}}
    c.execute("update wa_messages set meta=? where wamid=?", (json.dumps(meta, ensure_ascii=False), wamid))
    c.commit()


def recover_button_id(c, phone, wamid, text):
    """-> the button id a genuine tap would have produced for this typed reply, or None when the
    reply does not resolve against a live offer (not an error -- the caller passes it to the brain
    as ordinary free text, exactly as happens today without this module).

    ``wamid`` is the inbound message this reply arrived as (``process_owed_turn``'s ``turn_key``,
    which is always a stored inbound wa_messages row for both of its callers).
    """
    if not (text or "").strip():
        return None
    target = ST.message_by_wamid(c, wamid)
    if target is None or target["direction"] != "in":
        raise RuntimeError(f"choices.recover_button_id: {wamid!r} is not a stored inbound message")
    offer = _live_offer(c, phone, target)
    if offer is None:
        return None
    button_id, tier = match(text, offer["buttons"])
    if button_id is None:
        return None
    if C.BRAIN == "luna":
        from .. import luna_brain as LB   # lazy: only touched once a match against a live offer exists
        if button_id in (LB.CONSENT_YES_ID, LB.CONSENT_NO_ID):
            if not C.SYNTHETIC_CONSENT or tier not in CONSENT_GRADE_TIERS:
                return None
    _record(c, wamid, offer, button_id, tier, text)
    return button_id
