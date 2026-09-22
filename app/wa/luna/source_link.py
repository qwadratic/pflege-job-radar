"""TASK-150/151: the original ad behind what a TEST thread was told, and nothing else.

Ivan, 2026-09-21, for the acceptance phase only: his business partner has to be able to check that
the vacancies this bot names are real. So a message that names a posting carries a footnote offering
the original ad, and when the candidate asks for it they get the ad's own URL.

TEST THREADS ONLY -- ``wa_threads.is_test``, set by app/wa/luna/test_threads.py's CLI and never by a
turn. A production thread gets no footnote and no URL, ever: Ivan's standing rule is that a real
candidate never receives a board link, and app/wa/luna/offer.py keeps ``external_url`` out of the
payload the model writes from so that stays structural rather than a rule the model could forget.

ASSEMBLED HERE, NEVER BY THE MODEL. The footnote text is fixed and the URLs are looked up by
posting_id off the board snapshot, so the model neither writes nor sees a link: it cannot leak one
into a production thread, and it cannot invent one for a test thread either. app/wa/luna_brain.py
appends the footnote AFTER app/wa/luna/grounding.py:check_reply has passed the model's own text (the
LINK rule still holds on everything the model wrote), and appends the answer to the follow-up ask to
whatever the model wrote that turn.

THE ASK NEVER TAKES THE TURN (TASK-151). It used to: ``asks_for_source`` matched "original" and
"anzeige" as bare substrings next to a question mark, so "Brauchen Sie das Original meiner Urkunde?"
-- the single gate this funnel exists to close -- was answered with a list of job links and the model
was never called. The detector now needs an explicit request for the AD, and even then the model
still writes the turn: the links are appended to its answer, not substituted for it.

WHICH POSTING. Only a posting the reply actually named, via the clinic name grounding already
resolved it to (grounding.turn_evidence's ``postings``). An aggregate ("414 Stellen in München")
names no posting, so there is no original to offer and no footnote -- offering one would be a promise
this code could not keep. A named posting whose board row carries no URL is kept and SAID, not
silently dropped (CLAUDE.md: no silent fallbacks).

WHAT THE URL IS, HONESTLY. It is ``external_url``: the public address of the ad this row was read
from. For roughly three in ten live postings that address is the clinic's recruiting vendor
(softgarden, SmartRecruiters, mein-check-in, umantis) or a portal subdomain, because that is where
the clinic itself publishes the ad -- the board records no separate "clinic's own site" URL for any
posting (``postings`` has no ``source_url`` column; ``posting_observations.source_url`` is the same
address but for query parameters, measured over 150 live rows on 2026-09-21). So the message says
what it is offering rather than claiming a clinic homepage it cannot produce. It is never a URL on
our own board: every one of these comes off the scraped ad.

STILL LIVE AT SERVE TIME. The stored links are re-resolved against the live board before they go
out, exactly like grounding.py's STALE rule for a clinic name: this feature exists so Ivan's partner
can confirm a vacancy is real, and handing him a posting the verifier has since marked gone is the
one failure that breaks it.
"""
import re

from .. import brain as B
from .board_vocabulary import clinic_name_of

# Appended to the last bubble of a test-thread message that named at least one posting. Says it is a
# test aid in so many words, so nobody reading a test transcript mistakes it for production wording.
FOOTNOTE_DE = "\n\n(Test) Original-Anzeige gewünscht? Schreiben Sie einfach „Link“."

# The follow-up ask. "Link" on its own is one, because that is literally what the footnote asks for.
# The plural "Links" needs a request next to it: in German "links" is also "on the left", and "Ich
# wohne links vom Bahnhof" used to be read as an ask.
_LINK_WORD_RE = re.compile(r"\blink\b", re.I)
_LINKS_WORD_RE = re.compile(r"\blinks\b", re.I)
# The ad, as a NOUN and on a word boundary: "anzeigen" is the verb "to show" ("Können Sie mir die
# Stellen anzeigen?") and is not a request for the ad.
_SOURCE_NOUN_RE = re.compile(r"\b(anzeige|stellenanzeige|ausschreibung|inserat|original)\b", re.I)
# "Original" in its DOCUMENT sense -- the one this funnel actually talks about. A message that also
# names a document is asking about paperwork, not about the ad.
_DOCUMENT_RE = re.compile(r"urkunde|lebenslauf|dokument|zeugnis|kopie|unterlage|bescheinigung|"
                          r"diplom|pass\b|beglaubig|anerkennung", re.I)
# An explicit request to be SENT or SHOWN something. No bare "?", "ja", "wo" or "welche": those
# matched most of the funnel's own questions.
_ASK_RE = re.compile(r"\bschick\w*|\bsend\w*|\bzeig\w*|\bgib\b|\bgeben\s+sie\s+mir\b|"
                     r"\bh(ä|a)tte\w*\b|\bbitte\b|\bbekomm\w*|\bkrieg\w*|\bwo\s+(finde|kann)\b", re.I)

NO_ORIGINAL_DE = "keine Original-Anzeige hinterlegt"
GONE_DE = "steht nicht mehr auf dem Board"


def asks_for_source(text):
    """Is this message asking for the original ad of what we just named?"""
    text = str(text or "")
    if _LINK_WORD_RE.search(text):
        return True
    if _LINKS_WORD_RE.search(text) and _ASK_RE.search(text):
        return True
    noun = _SOURCE_NOUN_RE.search(text)
    if not noun or not _ASK_RE.search(text):
        return False
    # "Schicken Sie mir bitte das Original meiner Urkunde" names a document, not an ad.
    return not (noun.group(1).casefold() == "original" and _DOCUMENT_RE.search(text))


def _url(row):
    """The ad's own public URL -- ``external_url``. Empty string when the row carries none.

    There is no second candidate: see WHAT THE URL IS, HONESTLY in the module docstring. The
    ``source_url`` preference this function used to document never fired once in production, because
    no snapshot row has that key."""
    return str((row or {}).get("external_url") or "").strip()


def sources_for(named, postings):
    """-> [{clinic, posting_id, url}] for the clinics this reply named that resolve to a posting, in
    the order they were named. ``url`` is "" when the board records no original for that posting.

    ``postings`` is grounding.turn_evidence's {folded clinic name: posting_id}: the posting the house
    was named FROM, so the link is the ad the candidate was actually told about rather than some
    other opening of the same clinic."""
    from .grounding import fold

    by_id = {row.get("posting_id"): row for row in B.jobs_for({})}
    out, seen = [], set()
    for name in named:
        posting_id = postings.get(fold(name))
        if posting_id is None or posting_id in seen:
            continue
        seen.add(posting_id)
        row = by_id.get(posting_id)
        out.append({"clinic": clinic_name_of(row or {}) or name, "posting_id": posting_id,
                    "url": _url(row)})
    return out


def live_sources(stored):
    """-> (the stored entries the board still confirms, the ones it no longer does).

    Read at SERVE time, not at store time. ``sources_for`` filtered on live rows when the footnote
    was written; a later turn is a later board, and this feature is worthless if it hands Ivan's
    partner a posting ``get_posting`` itself would refuse."""
    live = {row.get("posting_id") for row in B.jobs_for({})}
    return ([s for s in stored or [] if s.get("posting_id") in live],
            [s for s in stored or [] if s.get("posting_id") not in live])


def link_bubble(live, gone=()):
    """The code-assembled answer to the ask: one line per posting, and what it is.

    Every line says something. A posting with no recorded ad URL and a posting the board has since
    dropped are both named out loud rather than left out, so the count of lines always matches the
    count of houses the message named (AC#5, CLAUDE.md: no silent fallbacks)."""
    lines = ["Klar — hier die Original-Anzeigen (nur in diesem Test-Chat; das ist die veröffentlichte "
             "Anzeige, bei vielen Häusern auf deren Bewerbungsportal):"]
    for s in live:
        lines.append(f"{s['clinic']}: {s['url']}" if s.get("url")
                     else f"{s['clinic']}: {NO_ORIGINAL_DE}")
    for s in gone or []:
        lines.append(f"{s['clinic']}: {GONE_DE}")
    return "\n".join(lines)
