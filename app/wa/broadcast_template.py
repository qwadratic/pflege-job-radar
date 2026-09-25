"""The fixed first-touch broadcast, frozen in code (Ivan, 2026-09-24).

WHY THIS FILE EXISTS. This text was never in the repository. Every phone-rail broadcast so far was
sent by hand with ``tools/wa_bridge.py broadcast --body '...'``, so the only surviving copy of the
approved wording lived in the WhatsApp threads on the handset. On 2026-09-24 an agent asked to
re-send "the test broadcast", could not find a template anywhere in the code, and wrote its own
German opener instead of stopping to ask. It went to one of the two test numbers before the run was
stopped. Ivan: "у нас шаблон фиксированный" -- so here it is, in the one place that makes inventing
a replacement impossible rather than merely discouraged.

PROVENANCE, and it matters: the strings below were read back off the handset itself
(``tools/wa_bridge.py read``) from the two messages actually delivered at 12:52 and 12:57 UTC that
day, character for character including the blank line between header and body. They were NOT
reconstructed from memory or from a test fixture -- tests/test_wa_luna_campaign.py carries a
similar-looking but DIFFERENT header ("Neue Pflegestellen in Bayern"), which is exactly the kind of
near-miss that makes a from-memory copy dangerous.

Sent verbatim, never paraphrased, for the same reason as the locked phrases in
app/wa/luna/prompts.py: this is a cold first contact with someone who did not ask to be written to,
and its wording is an approved claim about why we have their number, not a sentence a model or an
operator gets to improve on the day. The ONLY thing that varies is the greeting name.
"""

HEADER = "Neue Stellen in Bayern für Pflegekräfte"

#: One ``{name}`` placeholder and nothing else. Anything else that needs saying is a different
#: message, not a variation of this one.
BODY_NAMED = ("Hallo, {name}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir "
              "viele neue Stellen in Bayern. Haben Sie noch Interesse?")

#: Ivan, 2026-09-24: "может быть случай когда имя неизвестно. тогда просто поздороваемся." A lead
#: list without a first name is normal, and refusing to write to those people would be a worse
#: outcome than greeting them plainly. This is a SPECIFIED second wording, not a fallback that
#: quietly repairs a missing value: the sentence after the greeting is byte-identical to the named
#: one, so the two cannot drift apart, and nothing here guesses or invents a name.
#: "Hallo!" rather than "Hallo." -- a bare full stop reads clipped in German ahead of a warm
#: sentence, and this is a cold first contact where the opening word is most of the impression.
BODY_PLAIN = ("Hallo! Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele "
              "neue Stellen in Bayern. Haben Sie noch Interesse?")


def render(name=None):
    """-> the exact message to send to one recipient: the named wording when a name is known, the
    plain greeting when it is not. Never invents, abbreviates or guesses a name."""
    name = str(name or "").strip()
    body = BODY_NAMED.format(name=name) if name else BODY_PLAIN
    return f"{HEADER}\n\n{body}"
