"""TASK-144/146/151: the evidence a reply must stay inside, and the checks that hold it there.

prompts.py has told the model since TASK-91 to name only a clinic a tool call just returned. Nothing
checked it, and a prompt is not a guarantee -- so this module reconstructs what the board tools
actually returned during a turn and refuses a reply that goes beyond it. Ivan's two rules (2026-09-21)
and the three things the audit of 2026-09-21 proved were missing from them:

1. VOLUME -- at most ``offer.OFFER_LIMIT`` POSITIONS in one message. Positions, not name mentions: a
   list of ten jobs without a single clinic name is still ten positions, and it used to pass
   untouched because nothing in it looked like a name (audit A1). A LIST IS NOT A MARKER SHAPE
   (TASK-151): the same ten jobs as prose, one per line, or under letter markers counted as zero.
2. NO INVENTION -- a clinic named in the reply must be one this thread's tools really returned. The
   name has to be the board's own or a prefix of it, never a longer string built around it: writing
   "Kreisklinik Mindelheim Nord" around a real "Kreisklinik Mindelheim" invented a site of a real
   house and put it in the thread's memory as permanently sayable (TASK-151).
3. COUNT / BRANCHES -- when more matched than the message offers, the message says how many more,
   and the offer turn puts both branches (narrow / pool) to the candidate. Prompt-only until
   TASK-144: a reply naming five with neither branch offered was accepted (audit D). These are the
   OFFER turn's rules and key on the positions the message offers -- keying on how many names it
   carried let the same falsehood out whenever the positions were listed without naming a house, and
   forced a factual follow-up about two houses it had already offered to recite a total and both
   branches or die (TASK-151). "Never say these are all there are" rode along in this same rule
   through TASK-144/151 and round 4 below, as a BLOCK -- ROUND 5 took it off the blocking path
   entirely; see there for why.

   ROUND 2 (2026-09-22, an Opus reviewer live on the real board). TASK-151's fix was verified only by
   hand-injecting ``counts`` -- never against a real turn -- and two holes survived it. First, the
   board-wide open-jobs/clinic totals ``market_snapshot`` already computes never reached ``counts``
   on a real turn (they only entered it inside the offer, i.e. once every funnel gate was settled),
   so a true Bavaria-wide answer was rejected as unsupported: ``turn_evidence`` now adds them
   unconditionally. Second, because an approximation marker may anchor on ANY number in ``counts``,
   and ``OF.OFFER_LIMIT`` (the display cap, a constant we chose) was folded into that same set, "rund
   10 offene Stellen" and "gut 5 offene Stellen" passed with zero market evidence -- the cap is a fact
   about the message, not the market: ``_false_counts`` now takes it and the bubble's own position
   counts as STRUCTURAL, matched only exactly, never as an approximation anchor.

   ROUND 3 (2026-09-22, the same reviewer, 14 more live turns): board-supported figures kept going out
   right, but the guard still silenced three TRUE shapes.
   a. ``count_postings`` -- the tool whose docstring says to call it for "wie viele Stellen haben Sie
      in X" -- hit ``replay``'s ``else: continue``: its result is a number and nothing else, so NONE
      of it reached ``counts``, and no OTHER tool contributes a filtered clinic count either. "18
      Stellen, 4 Kliniken" -- the true Augsburg Intensiv/IMC figure, looked up by the model that same
      turn -- was rejected as invention on both numbers. ``replay`` now replays it (``_count_rows``)
      and ``turn_evidence`` merges its numbers into ``counts``.
   b. ``mention_spans``/``_shaped`` read "278 Kliniken" as a NAME, because "Kliniken" is a board head
      word and an unrelated "bieten" elsewhere in the same sentence satisfied the "offers a position"
      half of the detector regardless of what the two-token span itself said -- so NO INVENTION fired
      on a count before COUNT was ever consulted, twice in a row (runs 7/8), for the honest answer to
      "mit wie vielen Kliniken arbeiten Sie". Fixed in the detector: a bare head word directly
      preceded by a number and nothing else in the span is a quantity, never a name (a board name that
      carries a digit puts it AFTER the head -- "Klinikum Augsburg 2").
   c. The exhaustive-claim rule's "nur diese/die" alternative required no object at all, so it fired on
      "nur diese Klinik [in Straubing]" -- true, singular, narrow German -- exactly as it would on "nur
      diese 5 Kliniken", killing a true reply and its corrective rewrite both (run 11). It now requires
      the same kind of PLURAL count noun the claim is actually about, the way "alle ... Kliniken"
      already did.
   d. THE OPEN QUESTION ROUND 2 LEFT UNTOUCHED: "über N" is a floor with no ceiling, and a large
      board-wide number is in ``counts`` on almost every turn now (round 2, a above) -- so "über 40
      Kliniken" about München specifically was accepted on the true, unrelated Bavaria-wide 278.
      DECISION (documented, not silently patched): an approximation marker may anchor only on evidence
      about the SAME SUBJECT as the sentence carrying it. When the sentence names one of the board's
      own cities, the marker may anchor only on ``turn_evidence``'s ``counts_by_city[that city]`` --
      this turn's own tool-call numbers filtered ON that city -- never on the unscoped board-wide
      figures and never on a different city's; a sentence naming no city keeps the old, unscoped
      evidence. EXACT statements are untouched: they already have to equal a real number, which
      coincidence satisfies far less often than an unbounded floor does. See ``_false_counts``.

   ROUND 4 (2026-09-22, the same reviewer, run 15): the last of the four, and the only one left after
   round 3 -- 14 live turns with zero escalations and every SENT figure board-true, one shape still
   died. "In Straubing gibt es aktuell 6 offene Pflegestellen, alle beim Klinikum St. Elisabeth der
   Barmherzigen Brueder" is TRUE (Straubing genuinely has 6 postings, all at that one house) and fully
   discloses its own total -- but the exhaustive-claim rule read the noun AFTER "alle" ("Klinikum") as
   what was being claimed exhaustive and checked it against ``remaining``, a scalar mixing postings-
   listing cutoffs, clinic totals and offer remainders from every filter the turn ran under, with no
   idea which of them (if any) answers "how many clinics does Straubing have" -- here, none of them
   did; the 1 it found was this SAME call's own LISTING_LIMIT cutoff (5 of 6 rows shown), a POSTINGS
   display artifact, not a second clinic. Comparing unlike quantities, exactly as round 2's OFFER_LIMIT
   mistake was, one level up: a claim about CLINICS checked against POSTINGS evidence, or evidence
   gathered under a different filter than the claim's own. FIXED AT THE TIME by splitting the regex --
   "alle NOUN" directly (no preposition, "alle Kliniken") checked against the old unscoped
   ``remaining``; "alle bei/beim/im/in KLINIK" checked separately against scoped (city, subject)
   evidence built for exactly this. ROUND 5 (next) removed that split and the machinery built for it:
   see there for why, and for what replaced it.

   ROUND 5 (2026-09-22, Ivan's decision, not another live-turn patch). The SAME reviewing session
   that produced round 4's fix then ran it live: three more Straubing turns, one escalated to the
   holding message, one needed a correction, one only survived because the model happened not to use
   the word "alle" -- the sentence dying every other time was true, fully disclosed, board-checkable
   ("In Straubing gibt es aktuell 6 offene Stellen, alle am Klinikum St. Elisabeth (Barmherzige
   Brueder) - u. a. Intensiv/IMC, Anaesthesie/ATA, OP und Springerpool", 6 postings, all at that one
   house, both true). Root cause: round 4's regex split named bei/beim/im/in; "alle am Klinikum" uses
   a preposition round 4 never enumerated, so it fell on the DIRECT side and was checked against
   ``remaining`` -- exactly the round-4 bug, reopened by one more preposition, because free German
   has more of them than any enumeration will. Symmetrically, the same enumeration is why three
   FABRICATED distribution claims (a live verifier's own counterexamples, meant to be caught) went
   OUT instead -- "alle am Klinikum" also isn't ``_EXHAUSTIVE_DISTRIB_RE``'s bei/beim/im/in, so
   nothing checked it against the scoped evidence that would have caught the fabrication either. Not
   a bug in this round's regex specifically: TWO independent live rounds (3's plural-noun fix, 4's
   preposition split) each closed the false rejection they found and each, in doing so, either missed
   or reopened a different false pass -- because the check is trying to recognise an open-ended CLAIM
   SHAPE in free-form German, and German always has one more way to phrase it that the pattern has
   not seen yet. No further regex round is expected to converge where the last four did not.

   THE DECISION: this check stops blocking. Detection stays -- a house's postings really can all sit
   at one clinic, and whether a reply CLAIMS that is still useful signal -- but the reply is no
   longer held on it. ``check_reply``'s ``flagged`` out-list collects the sentence the rule
   suspected; app/wa/luna_brain.py records it on the thread's card the same way an escalation is
   recorded today (``card["_escalated"]``/``card["_escalate_reason"]``, see app/wa/api.py's own use
   of the same fields for unread media) -- a human sees the thread and the sentence, and the
   candidate gets the reply either way. Gone with the blocking decision, because they existed only to
   serve it: the preposition enumeration in the regex (folded back into one shape, matching "alle
   NOUN" and "alle bei/beim/im/in NOUN" the same way again -- detection does not need the split,
   only verification did), ``_EXHAUSTIVE_DISTRIB_RE``/``_distribution_subject``/
   ``_distribution_claimed``/``_exhaustive_distribution_claim``, and the (city, subject)-tuple
   entries in ``turn_evidence``'s ``counts_by_city`` built to feed them. The FLAT ``counts_by_city``
   round 3 built for ``_false_counts``'s own, unrelated approximation-marker scoping is untouched --
   that rule still blocks and still needs it.

   THE ASYMMETRY, WHY A FLAG AND NOT A LOOSER BLOCK OR SILENCE. The two ways this check can be wrong
   do not cost the candidate the same thing. Wrong in the SILENCING direction (a true "alle"
   sentence rejected twice): the candidate's real, true answer is withheld and replaced with "a
   colleague will get back to you" -- the thread stalls on a holding message, and a candidate mid
   search has every reason to go elsewhere before a human gets to it. Wrong in the PERMISSIVE
   direction (a false "alle" that should have been caught): the candidate sees one clinic where the
   board actually has two, and can ask a perfectly ordinary follow-up ("gibt es noch andere?") -- a
   cost of one extra turn, not the conversation. That asymmetry is why the fix is to flag, not to
   hunt for a better regex or a looser block: every regex round so far has traded one direction's
   failure for the other's, and the direction it must never fail in silence, not overclaim.

   WHY THIS RULE AND NOT THE OTHER FOUR (NO INVENTION, COUNT's own figure check, STALE, VOLUME/the
   five-position cap with its remainder-and-branches obligation, all still BLOCKING, all untouched by
   this round). Each of those checks a CLOSED fact this harness itself computed: a name either is the
   board's own spelling of a real, currently-live house or it is not; a figure either equals (or
   honestly approximates) a number ``turn_evidence`` put in ``counts`` or it does not; a posting
   either is on the live board or it is not; a message either lists six-or-more items, or fails to
   say how many more matched, or fails to offer both branches, or it does not. Every one of those is
   a comparison against a fixed evidence set the harness already has -- exactly what a check CAN get
   right. The exhaustive-claim rule instead has to recognise THAT a sentence asserts completeness at
   all, in a language with no closed set of ways to say it, before it can compare anything -- a
   different kind of problem, and the one this round stopped trying to solve by blocking.

   ROUND 6 (2026-09-22, F3, same-day verification). Flagging, not blocking, does not retire the
   detection shape's own false positives -- it only changes what they cost. The offer turn's own
   required pool-branch sentence ("...oder darf ich Sie gleich fuer alle dort passenden Stellen
   vormerken?", Ivan's rule (b), BRANCHES) says "alle ... Stellen" and flagged on every turn that
   phrased the pool branch with "vormerken" rather than one of ``_OFFER_SENTENCE_RE``'s other verbs
   (vorschlagen, vorstellen, ...) -- a flag on the happy path, every offer turn, teaches a human to
   ignore flags. Fixed the same way the regex already excludes the pool offer's other phrasings: added
   "vormerk" to ``_OFFER_SENTENCE_RE``. Scoped to the sentence carrying the verb, not the whole reply
   (``_exhaustive_claim`` already reads per sentence), so a genuine exhaustive claim in a different
   sentence of the same reply still flags.
4. LINK -- no URL in an outgoing bubble. offer.py keeps the board's links out of the payload the model
   writes from; this is the other half, on the text, so a link the model wrote from its own memory
   cannot go out either (audit D). Any host shape, not a TLD allowlist (TASK-151).
5. STALE -- a POSTING this thread grounded on an earlier turn is not evidence that it is still open
   now. Turn 2 confirmed a posting as free after the verifier had marked it gone (audit E); keying
   the re-check on the CLINIC let the same sentence out whenever the house kept any other opening
   (TASK-151).

A REJECTED REPLY IS NOT SILENCE (audit C). ``check_reply`` raises for the four BLOCKING rules (NO
INVENTION, COUNT's figure check, STALE, VOLUME with its remainder/BRANCHES obligation);
app/wa/luna_brain.py:_checked_reply catches it, tells the model which rule it broke and lets it write
the turn once more against freshly read evidence, and on a second violation sends
prompts.BLOCKED_REPLY_DE and flags the thread for a human. Before that the raise came straight out of
turn(): nothing was sent at all, and catch-up re-drove the same turn into the same wording. The
exhaustive-claim rule does not raise at all (ROUND 5 below): it appends to ``check_reply``'s
``flagged`` out-list instead, the reply goes out, and the thread is flagged the same way.

SPELLING IS NOT IDENTITY (audit A3/B/C). Every comparison here runs over ``fold``: case, umlauts and
their ae/oe/ue spellings, sharp s, hyphens, spacing and punctuation all fall out of it, on BOTH sides.
Before that, ``Bezirkskrankenhaus Gunzburg`` hid a real house from the cap and ``Klinikum
Munchen-Waldperlach`` -- a fabrication -- went out with zero tool calls in the turn, while the correct
spelling of ``Bezirkskranken-haus Werneck`` (the board record carries a scrape typo) was rejected as an
invention and the candidate got silence.

ONE WRITTEN HOUSE IS ONE POSITION (audit A2). Mentions are deduplicated by WHERE THEY SIT IN THE TEXT,
not by one name containing another: "Klinikum Augsburg" found inside the written "Klinikum Augsburg
Süd" is one position, while "Kreisklinik Mindelheim" and "Klinik Mindelheim" written in two places are
two houses and cost two. 16 such containment pairs exist among the 255 live clinics, and the old
name-containment dedup let a sixth house through the five-position cap with all of them.

WHAT COUNTS AS EVIDENCE. Per turn: the harness's own offer/shortlist (app/wa/luna_brain.py:
market_snapshot, built from the same board rows), plus every row the board tools returned during
this turn, replayed from tools_server's own call log, plus the clinic names this thread has already
had in evidence (app/wa/luna_brain.py:GROUNDED_KEY) THAT THE LIVE BOARD STILL HAS A POSTING FOR. The
per-thread memory is deliberate: the model answers a follow-up about a clinic it named two turns ago
out of its own session memory, and a check that had forgotten the earlier tool result would reject a
true sentence and stall the thread. The liveness re-check is equally deliberate: the memory was a
permanent allowlist, so a house whose postings the verifier had since marked gone stayed sayable, and
"die Stelle ist noch frei" passed. A name that has never been in any tool result on this thread is the
invention this check is for, and that is what it rejects.

DENIABLE NAMES. A house the candidate themself named, and a house this thread grounded before that
the board no longer has, may appear in any sentence that does not CLAIM a vacancy at it ("das
Krankenhaus Coburg-West habe ich leider nicht", "beim Sana Klinikum Coburg ist gerade alles besetzt",
"die Stelle ist inzwischen weg") and costs no position. The same name in a sentence that says the
house is hiring is a claim about a house nobody looked up, which is exactly rule 2. The burden sits
on the sentence OFFERING the position, not on the sentence denying it (TASK-151): requiring a
nicht/kein token in the denial meant ordinary honest German -- there are many ways to say "we have
nothing there" -- was rejected as an invention and the candidate got the holding message.

REPLAY, NOT A SECOND IMPLEMENTATION. tools_server.py logs every call's name and arguments but not
its rows. The rows are recovered by running those same arguments back through tools_server's OWN
filter builders and path handlers against the same in-process board snapshot -- never a
reimplementation of what a tool does (tests/test_wa_luna_dialog_rules.py pins the two to each other,
so a change on that side fails loudly here instead of quietly widening what a reply may say). The
import is inside the function because tools_server imports ``mcp``: the live service must not need
the MCP package installed to answer a turn that calls no tool.

CONCURRENCY. The call log is one file per session directory, not per turn: two turns running at once
interleave in it, and a turn reading from its own start offset can see the other's calls. That makes
the evidence set a superset, never a subset -- it can let a true sentence through, it cannot invent a
rejection.
"""
import json
import re
import unicodedata

from ... import data as D
from .. import brain as B
from .. import config as C
from . import offer as OF
from .board_vocabulary import city_of, clinic_name_of

TOOL_LOG_NAME = "tool_calls.jsonl"

# The SEED head words: the generic kinds of house, matched as a substring of a token so
# "Rotkreuzklinikum", "Schön Klinik", "Sana Kliniken", "St. Anna Stift" and "Uniklinikum" are all
# reached. They are a seed, not the vocabulary (TASK-151): ``board_head_words`` adds every word the
# live board's own clinic names are built from, so the detector grows with the board instead of with
# an edit. Before that this tuple WAS the whole list, and a fabricated house using none of its ten
# stems -- "Universitätsmedizin Augsburg", "Gesundheitszentrum München Nord" -- was invisible to
# detector 2 while detector 1 could not see it either, because it is not on the board.
_CLINIC_STEMS = ("klinik", "krankenhaus", "hospital", "stift", "seniorenzentrum", "seniorenheim",
                 "pflegezentrum", "pflegeheim", "krankenanstalt", "klinikverbund")

# Capitalized words that are never part of a name: the Sie-form possessives (German capitalizes
# them) and the words a reply says in the same breath as a clinic. A span stops at the first of
# these -- without that, "das Krankenhaus Ihrer Wahl" and "im Krankenhaus Vollzeit arbeiten" would
# each read as a clinic nobody ever returned.
# "Standort" and "Betriebsstätte" are the board's own STRUCTURAL words inside a name ("Klinikum
# Bamberg - Betriebsstätte am Bruderwald") and at the same time ordinary German about a place ("Der
# Standort München ist gut angebunden"). A span stops at them; the real board names that carry them
# are still found whole by detector 1, which matches the board record in the text directly.
_NOT_A_NAME = {"ihr", "ihre", "ihrer", "ihres", "ihrem", "ihren", "sie", "ihnen",
               "vollzeit", "teilzeit", "schicht", "wohnung", "unterkunft", "wahl",
               "stelle", "stellen", "position", "pflegefachkraft", "und", "oder",
               "standort", "standorte", "betriebsstätte", "betriebsstaette", "betriebsstätten"}

# German capitalizes the first word of a sentence, so a span often starts with an article, a
# preposition or a quantifier that is not part of the name ("Die Schön Klinik München", "Manche
# Kliniken bieten eine Unterkunft"). They are dropped from the FRONT only, so a failure message
# names the house rather than the sentence. The quantifiers matter more since TASK-151 widened the
# head vocabulary: "Manche Kliniken" would otherwise be a span carrying a head word.
#
# "über/rund/etwa/ca/knapp/fast/gut/mehr" -- _APPROX_MARKER_RE's own words, sentence-initial
# ("Über 278 Kliniken arbeiten wir zusammen.") -- belong here for the same reason: a marker is never
# part of a house's name, and dropping it is what lets the NUMBER-THEN-HEAD count check below see the
# bare "278 Kliniken" shape instead of a three-token span the count check does not recognise (round-3
# audit, 2026-09-22).
_LEAD_WORDS = {"die", "der", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
               "im", "in", "am", "an", "bei", "beim", "zum", "zur", "aus", "auf", "für", "von",
               "wir", "ich", "es", "hier", "dort", "auch", "noch", "dann",
               "manche", "viele", "einige", "mehrere", "andere", "alle", "allen", "unsere",
               "unser", "verschiedene", "weitere", "beide", "jede", "jeder", "jedes", "etwa",
               "leider", "aktuell", "derzeit", "momentan", "gerne", "gern", "damit", "deshalb",
               "außerdem", "ausserdem", "zusätzlich", "zusatzlich", "trotzdem", "natürlich",
               "naturlich", "klar", "ja", "nein", "okay", "super", "danke", "bitte",
               "was", "wie", "wo", "wann", "welche", "welches", "welcher", "wenn", "ob",
               "über", "ueber", "rund", "ca", "knapp", "fast", "gut", "mehr"}

# Company-form tokens a board clinic name carries that say nothing about what kind of house it is.
_LEGAL_FORMS = {"gmbh", "ggmbh", "mbh", "kdor", "kgaa", "gag", "ohg", "kgaa", "se"}

# A name word: capitalized ("München", "St.", "Barmherzige"), or a bare number, since board clinic
# names do carry one ("Klinikum Augsburg 2"). Without the number the detector cut the name short and
# a different house of the same group grounded it by containment.
_NAME_TOKEN = r"(?:[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*|\d+)"
_SPAN_RE = re.compile(rf"\b{_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[\wÄÖÜäöüß]+")


# --- folding: one spelling for both sides ------------------------------------------------------

def _fold_chars(text):
    """The folded characters of ``text`` and, per character, the index it came from in ``text``.

    Case, diacritics, sharp s, the ae/oe/ue spellings of the umlauts, hyphens, spaces and punctuation
    all go; letters and digits stay. Both halves of every comparison in this module run through this,
    which is what makes ``Gunzburg``/``Günzburg`` and ``Bezirkskranken-haus``/``Bezirkskrankenhaus``
    the same house -- the board's own record carries a scrape typo in the second of those, and
    spelling it correctly used to be rejected as an invention (audit C).

    The offsets are what lets a mention be located in the reply, so two houses written in two places
    count as two positions however much one name contains the other (audit A2).
    """
    chars, index = [], []
    for i, ch in enumerate(str(text or "")):
        # casefold first: "ß".casefold() is "ss", and NFKD splits "ü" into "u" + a combining mark.
        for c in unicodedata.normalize("NFKD", ch.casefold()):
            if unicodedata.combining(c) or not c.isalnum():
                continue
            chars.append(c)
            index.append(i)
    out_chars, out_index, k = [], [], 0
    while k < len(chars):
        out_chars.append(chars[k])
        out_index.append(index[k])
        # "nuernberg" -> "nurnberg", the same string "Nürnberg" folds to: this population writes
        # both, and a detector that only knew one of them missed the house entirely.
        k += 2 if (chars[k] in "aou" and k + 1 < len(chars) and chars[k + 1] == "e") else 1
    return "".join(out_chars), out_index


def fold(text):
    """The comparable form of a name or a piece of text -- see ``_fold_chars``."""
    return _fold_chars(text)[0]


def _normalize(text):
    """Whitespace-collapsed, case-folded text. For display and log lines only; every COMPARISON in
    this module uses ``fold``."""
    return _WS_RE.sub(" ", str(text or "")).strip().casefold()


def log_path():
    return C.LUNA_SESSION_DIR / TOOL_LOG_NAME


def log_offset():
    """Where this turn's own calls start in the shared call log. Read before the model runs."""
    path = log_path()
    return path.stat().st_size if path.exists() else 0


def calls_since(offset):
    """The [{tool, args, at}] logged after ``offset`` -- this turn's calls (see CONCURRENCY above)."""
    path = log_path()
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read()
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def _posting_args(args):
    return {"city": args.get("city", ""), "department": args.get("department", ""),
            "role_class": args.get("role_class", ""), "regierungsbezirk": args.get("regierungsbezirk", ""),
            "housing": bool(args.get("housing", False)), "employment_type": args.get("employment_type", ""),
            "q": args.get("q", "")}


def _count_args(args):
    """count_postings takes every ``_posting_args`` filter but ``q`` -- the tool itself has no q=
    parameter, so a logged call never carries one to begin with; this just states that rather than
    silently dropping an unused key."""
    return {"city": args.get("city", ""), "department": args.get("department", ""),
            "role_class": args.get("role_class", ""), "regierungsbezirk": args.get("regierungsbezirk", ""),
            "housing": bool(args.get("housing", False)), "employment_type": args.get("employment_type", "")}


def _count_rows(TS, args):
    """count_postings, replayed (round-3 audit, 2026-09-22): the SAME filtered rows _job_rows would
    hand search_postings for these arguments, then the same scalar counts tools_server.count_postings
    computes over them -- postings, distinct clinics, distinct cities, and the three housing splits.

    THE ONLY TOOL A CLINIC COUNT NARROWER THAN THE WHOLE BOARD EVER COMES FROM. This module's
    ``counts`` used to hold zero clinic counts for any filtered question -- search_postings contributes
    posting totals only -- so a true, filtered "4 Kliniken" was unsayable no matter what the model did.
    The board-wide clinic count already reaches ``counts`` unconditionally (``board_clinic_names()`` in
    ``turn_evidence``); a FILTERED one has no other source, which is exactly why count_postings exists
    and why its docstring tells the model to call it for "wie viele Stellen haben Sie in X" -- but until
    now this replay hit ``else: continue`` for it and its result, a number and nothing else, contributed
    none of it. Live rejection (Opus reviewer, 2026-09-22): 18 postings in 4 clinics, the true Augsburg
    Intensiv/IMC figure the model had just looked up, both read off the reply and both rejected as
    invention because neither ever reached this set.

    THE SAME REFUSAL AS THE LIVE TOOL. count_postings raises when every filter is empty (the board-wide
    total already sits in market_snapshot.open_jobs, so a call like that is refused rather than
    answered) -- a logged call recorded that way has to contribute nothing here too, or replaying it
    would hand the model the ENTIRE live board as evidence for a number tools_server itself never
    returned."""
    counted = _count_args(args)
    if not any(counted.values()):
        raise TS.ToolError("count_postings needs at least one filter -- see tools_server.count_postings")
    rows, _ = TS._job_rows(**counted)
    kinds = [D.housing_kind(r) for r in rows]
    return {"postings": len(rows), "clinics": len({TS.clinic_key(r) for r in rows if TS.clinic_key(r)}),
            "cities": len({TS._city(r) for r in rows if TS._city(r)}),
            "with_housing": sum(1 for k in kinds if k is not None),
            "with_accommodation": kinds.count("accommodation"),
            "with_relocation_support": kinds.count("relocation_support")}


def replay(calls, phone=None):
    """What the logged calls put in front of the model -- [{tool, rows, total, city, counts}], replayed
    through
    tools_server's own query code.

    WHAT THE TOOL HANDED BACK, NOT WHAT THE QUERY MATCHED (TASK-146). The listing tools show the
    first ``tools_server.LISTING_LIMIT`` rows and a total, so those rows are the only ones the model
    ever saw -- and the replay is cut to the same rows, through the same ``_listing``. Replaying
    without the limit used to make every clinic the query matched valid evidence: on a board of 100
    München clinics a reply could name "Klinikum München 63" and invent its housing, employment type
    and start date, and both rules passed, because row 63 was in the matched set even though the
    tool never returned it. The rule this module exists to hold is that a reply cannot name a
    posting the tools did not return, and a matched-but-unshown row was not returned.

    ``total`` is the tool's own total where the tool reports one, so the message can be held to
    saying how many more matched (COUNT).

    ONLY A CALL THAT SUCCEEDED CONTRIBUTES (audit B). The log is written before the query runs, so a
    call that raised is in it too; every branch here re-runs the tool's own code, which raises again
    and contributes nothing. ``match_cv_to_postings`` used to be the exception -- it was replayed as
    "the whole live board", so ONE failed call handed the model all 255 live clinics as evidence and
    contradicted this docstring. It is replayed properly now, through app/cv.py's own ranking over
    this thread's stored CV, and a call that failed live (no CV stored, no thread row, no number)
    fails here in the same place and grounds nothing.

    THE SCALAR TOOLS (round-3 audit, 2026-09-22). count_postings returns no clinic name -- a plain
    dict of numbers -- so it carries nothing for ``rows``/``total``; its evidence is a set of ints on
    the new ``counts`` key instead (see ``_count_rows``), merged into ``turn_evidence``'s ``counts``.
    A tool that names neither a clinic nor a number (a city list, a docs read) still contributes
    nothing, which is correct rather than lenient: there is nothing in its result to ground a name or
    a figure with.

    ``city`` is the raw ``city=`` argument the call itself was filtered on, folded -- what SUBJECT its
    numbers are evidence FOR. "" when the call took no city (board-wide, or filtered some other way):
    an approximation about a specific city may anchor only on a same-city figure (see
    ``_false_counts``), never on a number that happens to be true of a different scope."""
    if not calls:
        return []
    from . import tools_server as TS

    out = []
    for call in calls:
        tool, args = call.get("tool"), dict(call.get("args") or {})
        total, counts = None, None
        city = fold(args.get("city") or "")
        try:
            if tool == "search_postings":
                matched, _ = TS._job_rows(**_posting_args(args))
                rows, total = _shown(matched, TS), len(matched)
            elif tool == "search_postings_with_housing":
                matched, _ = TS._job_rows(city=args.get("city", ""),
                                          department=args.get("department", ""),
                                          regierungsbezirk=args.get("regierungsbezirk", ""),
                                          housing=True)
                rows, total = _shown(matched, TS), len(matched)
            elif tool == "list_clinics_with_housing":
                # Grouped by clinic and cut to its own ``limit``, so the replay groups the same way:
                # the rows are many, the clinics it listed are few.
                housing_rows, _ = TS._job_rows(city=args.get("city", ""),
                                               regierungsbezirk=args.get("regierungsbezirk", ""),
                                               housing=True)
                matched = _first_per_clinic(housing_rows)
                rows, total = matched[:TS._limit(args.get("limit"))], len(matched)
            elif tool == "get_posting":
                rows = [j for j in D.filter_jobs(dict(TS.LIVE_BASE))
                        if j.get("posting_id") == args.get("posting_id")]
                city = ""   # looked up by id, not by city -- no subject to scope this row's evidence to
            elif tool == "list_clinics":
                rows, total = _clinic_rows(TS, args)
            elif tool == "match_cv_to_postings":
                ranked = _cv_ranked(TS, phone)
                rows, total = _shown(ranked, TS), len(ranked)
                city = ""   # ranked by CV fit, not filtered by city
            elif tool == "count_postings":
                rows, counts = [], _count_rows(TS, args)
            elif tool == "board_api_get":
                rows = _board_api_rows(TS, args)
                city = ""   # a path/query pair, not this module's city= filter shape
            else:
                continue
        except TS.ToolError:
            continue
        out.append({"tool": tool, "rows": rows, "total": total, "city": city, "counts": counts})
    return out


def clinics_returned(calls, phone=None):
    """The clinic names the logged calls could have returned (see ``replay``)."""
    return {name for entry in replay(calls, phone) for name in _names_of(entry["rows"])}


def _names_of(rows):
    for row in rows:
        name = clinic_name_of(row) or (row.get("name") or "").strip()
        if name:
            yield name


def _cv_ranked(TS, phone):
    """match_cv_to_postings, replayed: app/cv.py's ranking of the live board against THIS thread's
    stored CV, exactly as tools_server builds it. Without a number the live tool raises before it
    ranks anything (tools_server._turn_phone), so the honest replay of that call is no rows."""
    from ... import cv as CV

    if not phone:
        return []
    cv_text = TS._stored_cv_text(phone)
    if not cv_text:
        return []
    ranked = CV.match(CV.profile_from_text(cv_text), limit=len(D.jobs()) or 1)
    return [r for r in ranked if r.get("verify_status") == TS.LIVE_BASE["verify"]]


def _shown(rows, TS):
    """The rows a listing tool actually put in front of the model -- ``tools_server._listing``'s cut."""
    return rows[:TS.LISTING_LIMIT]


def _first_per_clinic(rows):
    """One row per clinic, in ``list_clinics_with_housing``'s own order: most postings with a flat
    first, then by name. Mirrored rather than approximated -- a different order would cut a
    different set of clinics out and reject a name the tool really did show."""
    from .board_vocabulary import clinic_key

    grouped = {}
    for row in rows:
        key = clinic_key(row)
        if not key:
            continue
        entry = grouped.setdefault(key, {"row": row, "n": 0})
        entry["n"] += 1
    ordered = sorted(grouped.values(), key=lambda e: (-e["n"], clinic_name_of(e["row"])))
    return [e["row"] for e in ordered]


def _clinic_rows(TS, args):
    """(the clinic rows ``list_clinics`` showed for these arguments, how many matched in all).

    The posting tools have ``tools_server._job_rows`` to replay through; list_clinics builds its rows
    inside the tool function, which also writes the call log -- calling it here would append to the
    log this replay is reading. So its steps are taken with its OWN helpers, in its own order:
    _resolve_city against the registry towns, D.town_match_keys for the town match (the board spells
    one town several ways), then the live-clinic filter and its own limit. The pinning test
    (tests/test_wa_luna_dialog_rules.py) runs the tool and this side over the same arguments, so a
    change over there fails loudly here instead of quietly rejecting a name the tool really showed."""
    filters, town = {}, None
    if args.get("city"):
        town = TS._resolve_city(args["city"], TS._registry_towns(), "clinics")
    if args.get("regierungsbezirk"):
        filters["regierungsbezirk"] = args["regierungsbezirk"]
    has_jobs = args.get("has_jobs", True)
    if has_jobs:
        filters["has_jobs"] = "1"
    matched = D.filter_clinics(filters)
    if town:
        keys = {k for spelling in town["spellings"] for k in D.town_match_keys(spelling)}
        matched = [c for c in matched if D.town_match_keys(c.get("town") or "") & keys]
    if has_jobs:
        matched = TS._live_clinics(matched)
    return matched[:TS._limit(args.get("limit"))], len(matched)


def _board_api_rows(TS, args):
    """The rows one logged board_api_get would return, through tools_server's own path handlers.

    /api/search is replayed too (audit C): it is the only door to a clinic BY NAME, the model is told
    to use it for a house the candidate names, and its hits were not replayed -- so looking a clinic
    up that way and then naming it killed the turn and the candidate got silence. /api/cities,
    /api/facets and /api/taxonomy carry no clinic name, so there is nothing in them to ground on."""
    from urllib.parse import parse_qsl

    path = str(args.get("path") or "").strip()
    params = dict(parse_qsl(str(args.get("query") or "").lstrip("?")))
    if path == "/api/jobs":
        return TS._api_jobs(params).get("rows") or []
    if path == "/api/clinics":
        return TS._api_clinics(params).get("rows") or []
    if path == "/api/search":
        found = TS.BOARD_API_PATHS[path](params)
        return [*(found.get("clinics") or []), *(found.get("jobs") or [])]
    if path.startswith(TS.BOARD_API_CLINIC_PREFIX):
        clinic_id = path[len(TS.BOARD_API_CLINIC_PREFIX):]
        if clinic_id and "/" not in clinic_id:
            return [TS._api_clinic(clinic_id, params)]
    return []


def board_clinic_names():
    """Every clinic name the live board has a posting for -- the vocabulary the detector reads a reply
    against, so a real clinic named without evidence is caught whatever shape its name has."""
    return OF.clinic_names(B.jobs_for({}))


def board_cities():
    """Every town the live board has an open posting in -- the vocabulary a COUNT claim's SUBJECT is
    read against (round-3 audit, 2026-09-22; see ``_false_counts``)."""
    return B.known_cities()


def _posting_of(rows):
    """{folded clinic name: posting_id} over rows that carry one -- which posting a named house was
    named FROM, for the test-thread evidence footnote (offer.py, TASK-150)."""
    out = {}
    for row in rows:
        name = clinic_name_of(row) or (row.get("name") or "").strip()
        if name and row.get("posting_id") and fold(name) not in out:
            out[fold(name)] = row["posting_id"]
    return out


def turn_evidence(snapshot, calls, known=(), phone=None, inbound="", known_postings=()):
    """Everything this turn's reply is checked against.

    -> {names, stale, stale_postings, remembered, deniable, postings, counts, counts_by_city, remaining}
      names           the clinic names the reply may name and offer
      stale           names this thread grounded before that the live board no longer has a posting for
      stale_postings  posting ids this thread grounded on that the live board no longer has
      remembered      the names this thread had already been shown before this turn
      deniable        folded names the reply may only DENY (stale ones, and the houses the candidate
                      themself just named)
      postings        {folded clinic name: posting_id} it was named from THIS turn (TASK-150)
      counts          every truthful market number for this turn: the offer's totals/remainders
                      (once there is an offer), this turn's own tool-call totals, and -- always,
                      offer or not -- the board-wide open-jobs total and clinic total the harness
                      itself just computed (round-2 audit, 2026-09-22)
      counts_by_city  {folded city: {int}} -- the same tool-call numbers, filed under the city= they
                      were filtered on, for a claim that names one (round-3 audit; see _false_counts).
      remaining       how many more matched beyond what was shown, 0 when nothing was left out

    STALENESS IS ABOUT THE POSTING, NOT THE HOUSE (TASK-151). Ivan's rule (a) is about the opening.
    The memory used to hold clinic names only, so a house that keeps ANY live posting was never
    stale -- and "die Stelle in Onkologie ist noch frei" went out after the verifier had removed
    every Onkologie posting the house had (reviewer F7). The posting ids a turn grounded on ride
    along now and are re-checked against the live board the same way the names are.
    """
    board = board_clinic_names()
    live = {fold(n) for n in board}
    live_postings = {r.get("posting_id") for r in B.jobs_for({})}
    names, postings, counts, remaining = set(), {}, set(), 0
    counts_by_city = {}

    offer = (snapshot or {}).get("offer") or {}
    for entry in [*(offer.get("positions") or []), *((snapshot or {}).get("shortlist") or [])]:
        if entry.get("clinic"):
            names.add(entry["clinic"])
            if entry.get("posting_id"):
                postings.setdefault(fold(entry["clinic"]), entry["posting_id"])
    if offer:
        counts.update({offer.get("clinics_total"), offer.get("remaining_clinics"),
                       offer.get("postings_total"), offer.get("remaining_postings"),
                       offer.get("shown")})
        remaining = max(remaining, int(offer.get("remaining_clinics") or 0))

    # THE BOARD-WIDE TOTALS, UNCONDITIONALLY (round-2 audit, 2026-09-22). market_snapshot's
    # open_jobs/matching_clinics_count and this module's own board_clinic_names() are arithmetic
    # over the live board the harness already ran this turn -- not a claim the model makes, so a
    # reply stating one of them is supported by definition. Before this they only entered ``counts``
    # inside ``if offer:``, i.e. only once every funnel gate was settled -- so the one question
    # market_snapshot's own docstring says the model may always answer ("the one aggregate number
    # always present -- safe for a first-turn greeting") had no number behind it in check_reply, and
    # a truthful Bavaria-wide answer was rejected as unsupported on a live Bavaria-wide question (2 of
    # 7 runs, Opus reviewer, 2026-09-22) while the harness had computed it one line above.
    counts.update({len(board), (snapshot or {}).get("open_jobs"),
                   (snapshot or {}).get("matching_clinics_count")})

    for entry in replay(calls, phone):
        names.update(_names_of(entry["rows"]))
        postings.update({k: v for k, v in _posting_of(entry["rows"]).items() if k not in postings})
        found = set()
        if entry["total"] is not None:
            found.update({entry["total"], entry["total"] - len(entry["rows"]), len(entry["rows"])})
            remaining = max(remaining, entry["total"] - len(entry["rows"]))
        if entry.get("counts"):
            # count_postings' own numbers (TASK-146 follow-up, round-3 audit): a scalar result, so
            # nothing above (rows/total) ever saw it -- see _count_rows.
            found.update(v for v in entry["counts"].values() if isinstance(v, int))
        counts.update(found)
        if entry.get("city") and found:
            # SAME-SUBJECT EVIDENCE (round-3 audit, 2026-09-22): filed under the city this call was
            # actually filtered on, in ADDITION to the flat set above -- never instead of it, so every
            # exact-match claim this module already checked keeps working unchanged. Only the
            # approximation-marker path (_false_counts) ever reads this map, because that is the one
            # rule an unrelated board-wide figure could silently satisfy (see there).
            counts_by_city.setdefault(entry["city"], set()).update(found)

    remembered = [str(n).strip() for n in known if str(n).strip()]
    if remembered and not live:
        # Without a board there is no way to tell a closed house from a loaded snapshot, and calling
        # every remembered clinic dead would reject every true sentence on the thread. Loud, not
        # guessed (CLAUDE.md).
        raise RuntimeError("the board snapshot holds no live posting, so this turn cannot tell whether "
                           f"the {len(remembered)} clinic(s) this thread already named are still open. "
                           "Refusing to check the reply against an empty board.")
    stale = sorted(n for n in remembered if fold(n) not in live)
    stale_postings = sorted(p for p in known_postings if p not in live_postings)
    names.update(n for n in remembered if fold(n) in live)

    deniable = {fold(n) for n in stale}
    deniable.update(fold(m) for m in mentions(inbound, board))
    return {"names": names, "stale": stale, "stale_postings": stale_postings,
            "remembered": {fold(n) for n in remembered}, "deniable": deniable, "postings": postings,
            "counts": {int(c) for c in counts if isinstance(c, int)},
            "counts_by_city": {key: {int(c) for c in vals if isinstance(c, int)}
                               for key, vals in counts_by_city.items()},
            "remaining": remaining}


def board_name_words():
    """Every word that appears in a live board clinic name or town, minus the clinic stems, folded.

    This is the corroborating vocabulary for ``_shaped`` -- the board's own data, not a lexicon we
    invented. "München"/"Munchen", "Tölz", "Barmherzige" are in it; "Nachtdienst", "Pflegefachkräfte"
    and "Dienst" are not, and those are the ordinary German nouns a head word used to swallow. Folded
    because the population writes towns without umlauts, and an umlaut-free fabrication
    ("Klinikum Munchen-Waldperlach") was corroborated by nothing and went out (audit B).
    """
    rows = B.jobs_for({})
    words = set()
    for row in rows:
        for text in (OF.clinic_name(row), city_of(row)):
            for word in _WORD_RE.findall(str(text or "")):
                low = fold(word)
                if len(low) > 2 and not any(stem in fold(stem_src) for stem_src, stem in
                                            ((low, stem) for stem in _CLINIC_STEMS)):
                    words.add(low)
    return words


def board_place_words():
    """Every town the clinic registry knows, plus every city the live board has a posting in, folded.

    Places are what a clinic name is built FROM, never what makes it a clinic, so they are subtracted
    from the head vocabulary below. Without that, "Augsburg" would be a head word and "Die Stadt
    Augsburg hat viele Häuser" would read as a claim about a house nobody looked up."""
    # Tokenised, not folded whole: "Bad Staffelstein", "Lohr a. Main" and "Neuburg an der Donau" are
    # towns whose PARTS a clinic name is built from, and folding the whole string left "staffelstein"
    # looking like a kind of house.
    words = set()
    for name in [*(city_of(row) for row in B.jobs_for({})),
                 *((c.get("town") or "") for c in D.clinics())]:
        words.update(fold(w) for w in _WORD_RE.findall(str(name or "")))
        words.add(fold(name))
    return {w for w in words if w}


def board_job_words():
    """Every word the live board's own JOB text uses -- titles, roles, departments, employment types
    -- minus the places, folded.

    This is the domain's ordinary German: "Pflegefachkräfte", "Nachtdienst", "Intensivstation",
    "Vollzeit". A span that is a head word plus one of these is prose, not a house, and reading it as
    a house failed the whole turn and left the candidate with silence (audit C, TASK-146). Places are
    subtracted because a title regularly names the town, and the town is exactly what makes
    "Universitätsmedizin Augsburg" a name."""
    words, place = set(), board_place_words()
    for row in B.jobs_for({}):
        values = (row.get("title"), row.get("role_label"), row.get("role_class"),
                  row.get("department_hint"), row.get("department_raw"),
                  row.get("qualification_hint"), *(row.get("employment_types") or []))
        for value in values:
            for word in _WORD_RE.findall(str(value or "")):
                low = fold(word)
                if low and low not in place:
                    words.add(low)
    return words


def _inflection_of(word, vocabulary):
    """The folded word is one of ``vocabulary``, allowing a German plural or case ending on it:
    "Pflegefachkräfte" is "pflegefachkraft" + "e", "Dienste" is "dienst" + "e"."""
    if word in vocabulary:
        return True
    return any(len(v) >= 4 and word.startswith(v) and len(word) - len(v) <= 3 for v in vocabulary)


def board_head_words():
    """Every word the live board's own clinic names are built from, minus places and company forms.

    THE DETECTOR GROWS WITH THE BOARD, NOT WITH AN EDIT (TASK-151). ``_CLINIC_STEMS`` is ten hand-
    written kinds of house; real German hospital names routinely carry none of them, and the live
    board itself has 26 stem-less entries ("Diakoneo KdöR", "Thoraxzentrum Bezirk Unterfranken",
    "Salus Gesundheitszentrum"). So the board's own naming vocabulary is read off the board the same
    way ``board_name_words`` reads the corroborating vocabulary: "zentrum", "medizin", "universität",
    "clinicum", "stiftung", "diakoneo" are in it because the board wrote them, and they will still be
    in it the day the board adds a kind nobody here thought of.

    Short words are dropped (< 5 folded characters): "anna", "josef", "nord", "west" are name PARTS,
    and matching them as a substring would read "Die Annahme" as a house."""
    words = set()
    place = board_place_words()
    for row in B.jobs_for({}):
        for word in _WORD_RE.findall(str(OF.clinic_name(row) or "")):
            low = fold(word)
            if len(low) >= 5 and not low.isdigit() and low not in place and low not in _LEGAL_FORMS:
                words.add(low)
    return words


def _head_of(word, heads):
    """The head word a folded token carries, or "".

    A seed stem matches anywhere in the token ("Rotkreuzklinikum" -> "klinik"). A board-derived head
    word matches as the whole token, or -- for a NOUN long enough not to be a name part -- as the
    END of a compound, which is where German puts the head: "Universitätsmedizin" -> "medizin",
    "Waldperlachklinikum" -> "klinikum". Not anywhere in the token: "Krankenhausleitung" ends in
    "leitung" and is a department, not a house.

    Only a noun, because the board's names carry adjectives too ("Orthopädische Klinik",
    "Urologische Klinik") and an inflected adjective is a substring of other words that have nothing
    to do with it -- "urologische" sits at the end of "neurologische", and every ad title with a
    neurological ward then read as a house. A German inflected adjective ends in -e/-en/-er/-es;
    a compound head does not."""
    for stem in _CLINIC_STEMS:
        if fold(stem) in word:
            return fold(stem)
    if word in heads:
        return word
    return next((h for h in heads
                 if len(h) >= 6 and not h.endswith(("e", "en", "er", "es"))
                 and word.endswith(h) and word != h), "")


def _is_stem(word):
    """A folded word carrying a SEED clinic stem ("klinikum", "krankenhaus", "uniklinik"). Used to
    keep the seed stems out of the corroborating vocabulary, where they would corroborate
    themselves."""
    return any(fold(stem) in word for stem in _CLINIC_STEMS)


def _seed_compound(word):
    """A folded word that is a NAME built on a seed kind of house, not the bare kind and not a word
    that merely starts with one.

    The seed has to sit at the end but for a grammatical ending: "rotkreuzklinikum" is
    "klinik" + "um", "waldperlachklinikum" and "sanktrochusklinikum" the same. "krankenhausleitung"
    carries "krankenhaus" and then seven more letters of a different noun -- a department, not a
    house, and reading it as one rejected a truthful sentence."""
    for stem in _CLINIC_STEMS:
        at = word.rfind(fold(stem))
        if at > 0 and len(word) - at - len(fold(stem)) <= 3:
            return True
    return False


def _ends_sentence(token):
    """A token whose trailing full stop ends the SENTENCE rather than abbreviating a name word.

    German capitalizes the first word of the next sentence, so without this a span ran straight
    through the stop and the house came out as "Klinikum Nürnberg. Es" -- a name nothing can be
    matched against, written into the thread's grounded memory, and (worse) no longer equal to the
    name the candidate wrote, so a DENIAL of it stopped being recognised as one and a truthful
    sentence was rejected. Every dotted token the live board uses inside a clinic name is two
    letters or fewer -- St., Dr., a.d., b. -- and a longer one is prose."""
    return str(token).endswith(".") and len(fold(token)) > 2


def _offers_a_position(sentence):
    """The sentence makes a POSITIVE availability claim -- this house is hiring, this post is open.

    Negation turns it off: "ist leider nicht mehr frei" carries "frei" and claims the opposite."""
    return bool(_OFFERS_RE.search(sentence)) and not _DENIAL_RE.search(sentence)


def _shaped(text, corroborating=None, heads=None, job_words=None):
    """Capitalized spans that name a house, as (start, end, text) in ``text``.

    A span is a candidate when one of its tokens carries a HEAD WORD -- a kind of house, read off
    the board's own clinic names (``board_head_words``) plus the seed stems. That alone is not
    enough: ordinary German says "Im Klinikum Nachtdienst zu arbeiten". So the span has to be
    confirmed one of two ways, and each closes a hole the other cannot:

    1. CORROBORATED BY THE BOARD'S VOCABULARY -- a DIFFERENT token in the span is a word the board
       uses in a clinic name or a town ("Klinikum Munchen-Waldperlach"). A different token on
       purpose: "Maria Schmidt" would otherwise corroborate itself, since "Maria" is both a head
       word and a board name word, and the bot greeting a candidate by name would fail the turn.
    2. THE SENTENCE OFFERS A POSITION -- "Das Klinikum Waldkraiburg sucht Pflegefachkräfte." A
       fabricated house in a town the board has no posting in is corroborated by nothing (audit's
       residual, and the reviewers' F3/F4), but a sentence that says it is hiring IS the claim rule
       2 exists to stop, whatever town it names. Prose about a house that claims nothing -- "Der
       Standort München ist gut angebunden" -- costs nothing and is not touched.

    A ONE-TOKEN SPAN is a house only when the whole token is a compound of one of the SEED kinds of
    house ("Waldperlachklinikum", "Rotkreuzklinikum", "Sankt-Rochus-Klinikum") and the sentence
    offers a position: there is no second word to corroborate against, so the compound itself has to
    carry the claim. The seed stems and not the whole head vocabulary, because a board-derived head
    word is regularly also a ward -- "Wir haben eine Stelle in der Altersmedizin frei" ends in
    "medizin" and names a department, not a house. "Im Klinikum ist eine Stelle frei" is the bare
    kind word and names no house either.

    THE RESIDUAL, STATED RATHER THAN GUARDED. A fabricated house whose name carries no head word the
    board has ever used is caught by neither detector: "Im Josefinum ist eine Intensivstelle frei"
    goes out, because "Josefinum" is not a compound of any word on this board (the board writes
    "Josephinum") and detector 1 cannot see a house the board does not have. Closing that needs a
    German institution lexicon we do not have; widening the head rule to any capitalized noun would
    reject truthful German instead, which is the cost the audit ruled out.

    THE COST, MEASURED. Every one of the 2462 live ad titles was put through this detector inside an
    offer sentence on 2026-09-21: 49 (2.0%) read as a house the turn had not grounded, and they are
    all ward or site phrases a reply would only carry by quoting a raw ad title verbatim
    ("Psychiatrische Institutsambulanz Roth", "Pädiatrische Intensivmedizin") -- plus a handful that
    really are houses under a name the board files differently ("Klinikum Penzberg", "München
    Klinik"). A reply like that costs the model its draft and one corrective retry, never the
    candidate their answer (check_reply). All 94 fixed German strings this harness itself sends, and
    the 20 funnel sentences in tests, come through untouched.
    """
    corroborating = board_name_words() if corroborating is None else corroborating
    heads = board_head_words() if heads is None else heads
    job_words = board_job_words() if job_words is None else job_words
    out = []
    for m in _SPAN_RE.finditer(str(text or "")):
        tokens, start = [], m.start()
        for token in m.group(0).split():
            if _inflection_of(fold(token), _NOT_A_NAME_FOLDS):
                break
            tokens.append(token)
            if _ends_sentence(token):
                break
        while tokens and fold(tokens[0]) in _LEAD_FOLDS:
            start += len(tokens[0]) + 1
            tokens.pop(0)
        if not tokens:
            continue
        words = [fold(w) for t in tokens for w in _WORD_RE.findall(t)]
        head_at = [i for i, w in enumerate(words) if _head_of(w, heads)]
        if not head_at:
            continue
        # A COUNT, NOT A NAME (round-3 audit, 2026-09-22): "278 Kliniken" / "5 Krankenhäuser" is a
        # bare plural head word directly preceded by a number and nothing else in the span -- a
        # quantity, never a clinic's own name. A board name that carries a digit puts it AFTER the
        # head ("Klinikum Augsburg 2"), never before it, so this reads only the NUMBER-THEN-HEAD shape
        # and leaves that one, and every real name, to the checks below. Has to run before them: an
        # unrelated "bieten" elsewhere in the SAME sentence ("Wir bieten Ihnen 278 Kliniken an")
        # satisfied "offers" regardless of what the two-token span itself said, and let a five-figure
        # board total read as an invented house -- killing the honest answer to "mit wie vielen
        # Kliniken arbeiten Sie" twice in one review (rounds 7/8, Opus reviewer, 2026-09-22).
        if len(tokens) == 2 and head_at == [1] and words[0].isdigit():
            continue
        offers = _offers_a_position(_sentence_of(text, start))
        if len(tokens) < 2:
            if not (_seed_compound(fold(tokens[0])) and offers):
                continue
        else:
            rest = [w for i, w in enumerate(words) if i not in head_at]
            # The span has to look like a NAME before either half decides: at least one token that
            # is neither a kind of house nor the board's own ordinary job German. Without that,
            # "Wir suchen im Klinikum Pflegefachkräfte für die Intensivstation" and "Pflegefachkraft
            # für unsere Psychosomatische Station" are a head word plus a ward and read as invented
            # houses -- the audit-C failure, where a truthful sentence killed the turn and the
            # candidate heard nothing. Measured over the 2462 live ad titles on 2026-09-21.
            if not any(not _inflection_of(w, job_words) for w in rest):
                continue
            if not (any(w in corroborating for w in rest) or offers):
                continue
        name = " ".join(tokens)
        out.append((start, start + len(name), name.strip(" .,;:!?")))
    return out


_NOT_A_NAME_FOLDS = {fold(w) for w in _NOT_A_NAME}
_LEAD_FOLDS = {fold(w) for w in _LEAD_WORDS}


def _board_hits(text, board):
    """Every live board clinic name the text writes, as (start, end, name) in ``text``.

    Folded on both sides, so the board's own scrape typo ("Bezirkskranken-haus Werneck") and the
    candidate's umlaut-free spelling ("Bezirkskrankenhaus Gunzburg") both find the house. The match
    has to begin and end on a word boundary of the original text: without that, "Klinik Mindelheim"
    would be found inside the word "Kreisklinik", which is a different house."""
    folded, index = _fold_chars(text)
    if not folded:
        return []
    raw = str(text or "")

    def _starts_word(k):
        i = index[k]
        return i == 0 or not raw[i - 1].isalnum()

    def _ends_word(k):
        i = index[k]
        return i + 1 >= len(raw) or not raw[i + 1].isalnum()

    out = []
    for name in board:
        needle = fold(name)
        if not needle:
            continue
        at = folded.find(needle)
        while at != -1:
            end = at + len(needle) - 1
            if _starts_word(at) and _ends_word(end):
                out.append((index[at], index[end] + 1, name))
            at = folded.find(needle, at + 1)
    return out


def mention_spans(text, board=None, corroborating=None, heads=None, job_words=None):
    """The clinics a bubble names, as [{start, end, text}], one entry per written house.

    Two detectors, union:
    1. every clinic name the live board has that appears in the text -- catches a real clinic named
       without evidence whatever its name looks like ("Rotkreuzklinikum", "Barmherzige Brüder");
    2. a capitalized span carrying a clinic stem and a word the board uses as a name -- catches a
       clinic the board has never heard of, which is what an invented name looks like.

    Overlapping hits are ONE house: the two detectors both fire on "Klinikum Augsburg Süd", and the
    board's shorter "Klinikum Augsburg" sits inside the written longer one. Hits that do NOT overlap
    are different houses even when one name contains the other -- that is the containment pair the
    five-position cap used to lose a house through (audit A2)."""
    board = board_clinic_names() if board is None else board
    hits = sorted([*_board_hits(text, board), *_shaped(text, corroborating, heads, job_words)],
                  key=lambda h: (h[0], -(h[1] - h[0])))
    merged = []
    for start, end, name in hits:
        if merged and start < merged[-1]["end"]:
            last = merged[-1]
            last["end"] = max(last["end"], end)
            if len(name) > len(last["text"]):
                last["text"] = name
            continue
        merged.append({"start": start, "end": end, "text": name})
    return merged


def mentions(text, board=None, corroborating=None, heads=None, job_words=None):
    """The clinic names a bubble writes, longest first -- ``mention_spans``'s text side."""
    return sorted({m["text"] for m in mention_spans(text, board, corroborating, heads, job_words)},
                  key=lambda s: (-len(s), s))


# --- how many positions one message carries ----------------------------------------------------

# A listed item is a position the candidate can act on, named house or not: "10 Stellen: 1) OP
# Vollzeit; 2) OP Teilzeit; ..." is ten positions, and the cap saw none of them because it counted
# detected clinic names (audit A1). TASK-151: a list is not a marker shape. The same ten jobs
# written as prose, one per line, or under letter markers were all counted as ZERO and sent, because
# only "1)" and "-" were read as a list. So four shapes are read, and the first that yields two or
# more items decides -- a message offers positions in one layout, not four at once.
_NUMBERED_RE = re.compile(r"(?<![\d.,])(\d{1,2})[.)](?=\s)")
_BULLET_RE = re.compile(r"(?:^|\n)[ \t]*[-–—•*][ \t]+", re.M)
_ALPHA_RE = re.compile(r"(?:(?<=^)|(?<=[\s(]))([a-z]|[ivx]{1,4})[.)](?=\s)", re.I)
# "Ich habe zehn Stellen für Sie: ..." -- a sentence that ANNOUNCES a set and then writes it out.
# The announcement is what makes the commas and semicolons after the colon item separators rather
# than ordinary prose punctuation; without it "Ich habe 414 Stellen in München: wollen Sie
# eingrenzen?" would be read as a list.
_ANNOUNCE_RE = re.compile(
    r"\b(\d{1,3}|zwei|drei|vier|f(ü|u)nf|sechs|sieben|acht|neun|zehn|elf|zw(ö|o)lf)\s+"
    r"(?:\w+\s+){0,2}?(stellen|stellenangebote|positionen|angebote|jobs|m(ö|o)glichkeiten)\b"
    r"[^:\n]{0,60}:", re.I)
_ITEM_SPLIT_RE = re.compile(r"[;,]|\bund\b", re.I)
_SENTENCE_END_RE = re.compile(r"[.!?]\s*$")


def board_position_words():
    """Every department and employment type the live board writes, folded -- what makes a fragment
    of a list a POSITION rather than a piece of prose. Read off the board for the same reason the
    name vocabulary is: a hand-written list of wards rots the first time the board adds one."""
    words = set()
    for row in B.jobs_for({}):
        values = [row.get("department_hint"), *(row.get("employment_types") or [])]
        for value in values:
            for word in _WORD_RE.findall(str(value or "")):
                if fold(word):
                    words.add(fold(word))
    return words


def _is_item(fragment, spans_inside, position_words):
    """A fragment of a list offers a position: it names a house, or it says a ward or an employment
    type the board itself uses."""
    if spans_inside:
        return True
    return any(fold(w) in position_words for w in _WORD_RE.findall(str(fragment or "")))


def _marks(text):
    """The offsets of the list markers in ``text``, for the marker layouts."""
    numbered = list(_NUMBERED_RE.finditer(text))
    values = [int(m.group(1)) for m in numbered]
    if len(numbered) < 2 or values[0] > 2 or any(b < a for a, b in zip(values, values[1:])):
        numbered = []
    if len(numbered) >= 2:
        return sorted(m.start() for m in numbered)
    bullets = list(_BULLET_RE.finditer(text))
    if len(bullets) >= 2:
        return sorted(m.start() for m in bullets)
    alpha = [m for m in _ALPHA_RE.finditer(text)]
    if len(alpha) >= 2 and fold(alpha[0].group(1)) in ("a", "b", "i"):
        return sorted(m.start() for m in alpha)
    return []


def _enumeration(text, spans=(), position_words=None):
    """The [(start, end)] of the items the list in ``text`` has, or [] when it is prose.

    Marker layouts first (numbered, bulleted, lettered), then the two unmarked ones: one item per
    LINE, and a sentence that announces a set and then writes it out after a colon."""
    text = str(text or "")
    position_words = board_position_words() if position_words is None else position_words

    def _inside(start, end):
        return [s for s in spans if start <= s["start"] < end]

    def _items(bounds):
        return [(a, b) for a, b in bounds if _is_item(text[a:b], _inside(a, b), position_words)]

    marks = _marks(text)
    if marks:
        return [(a, b) for a, b in zip(marks, [*marks[1:], len(text)])]

    # One item per line: at least two lines that each offer a position and are not whole sentences.
    at, lines = 0, []
    for line in text.split("\n"):
        lines.append((at, at + len(line)))
        at += len(line) + 1
    unmarked = _items([(a, b) for a, b in lines if text[a:b].strip()
                       and not _SENTENCE_END_RE.search(text[a:b])])
    if len(unmarked) >= 2:
        return unmarked

    announced = _ANNOUNCE_RE.search(text)
    if announced:
        at, bounds = announced.end(), []
        for piece in _ITEM_SPLIT_RE.split(text[announced.end():]):
            bounds.append((at, at + len(piece)))
            at += len(piece) + 1
        listed = _items(bounds)
        if len(listed) >= 2:
            return listed
    return []


def _position_spans(text, spans, position_words=None):
    """(how many positions the bubble carries, the item bounds) -- every listed item counts, plus
    every house named outside the list. An item that names two houses is two positions, an item that
    names none is still one: that is what "at most five positions in one message" means (Ivan,
    2026-09-21). A house named in prose outside any list is a position on its own."""
    items = _enumeration(text, spans, position_words)
    counted, out = set(), []
    for start, end in items:
        inside = [i for i, s in enumerate(spans) if start <= s["start"] < end]
        out.append(([spans[i] for i in inside], (start, end)))
        counted.update(inside)
    out += [([spans[i]], None) for i in range(len(spans)) if i not in counted]
    total = sum(max(1, len(named)) for named, _ in out)
    return total, out


def positions(text, spans, position_words=None):
    """How many positions one bubble carries -- see ``_position_spans``."""
    return _position_spans(text, spans, position_words)[0]


def new_positions(text, spans, shown_before=(), position_words=None):
    """How many of those positions are ones the candidate has NOT been shown before.

    Ivan's rule (b) -- five, how many more, both branches -- is about the OFFER turn. A follow-up
    that names two houses it already offered ("München Klinik Harlaching liegt im Süden, Privatklinik
    Dr. Gaertner ist eine Privatklinik") is not an offer, and requiring it to recite a total and both
    branch wordings rejected the honest answer and cost the candidate their reply (reviewer F11). A
    position with no house in it counts as new: nothing says the candidate saw it before."""
    shown_before = {fold(n) for n in shown_before}
    total = 0
    for named, _ in _position_spans(text, spans, position_words)[1]:
        fresh = [s for s in named if fold(s["text"]) not in shown_before]
        total += len(fresh) if named else 1
    return total


# --- the outgoing backstop ---------------------------------------------------------------------

# A denial: the sentence says we do NOT have this house / it is NOT open any more.
_DENIAL_RE = re.compile(r"\b(nicht|nichts|kein\w*|nirgend\w*|leider\s+nichts)\b", re.I)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?\n]?")

# A POSITIVE availability claim: this house is hiring, this post is open. What a name in a reply has
# to be grounded FOR (see DENIABLE NAMES above and ``_shaped``). Everything else a reply says about
# a house -- that it is in the south, that it is full, that the post is gone -- claims no vacancy and
# costs no position. Before TASK-151 the burden sat on the DENYING sentence instead, through a
# four-token list, so "Beim Sana Klinikum Coburg ist gerade alles besetzt" was rejected as an
# invention and the candidate got the holding message (reviewer F10).
_OFFERS_RE = re.compile(r"\b(sucht|suchen|stellt\s+\w+\s+ein|einstellen|bietet|bieten|frei|offen|"
                        r"verf(ü|u)gbar|ausgeschrieben|vakant|zu\s+haben|hat\s+(noch\s+)?(eine|"
                        r"zwei|drei|mehrere|freie)\b)", re.I)

# "These five are all there is" -- false whenever more matched. TASK-151 stopped enumerating the
# ways to say it: ordinary German ("Damit kennen Sie alle Kliniken", "unser komplettes Angebot",
# "sonst nichts") walked past the five literal alternatives, and any true number riding along
# satisfied the COUNT rule, so a false exhaustive claim went out next to a correct remainder
# (reviewer F6). The quantifier is matched per SENTENCE, so the pool branch -- "soll ich Sie allen
# Kliniken vorschlagen?" -- is not read as a claim that those are all of them.
#
# "nur diese/die" NEEDS A PLURAL OBJECT (round-3 audit, 2026-09-22, Opus reviewer, run 11). Unlike
# "alle", which is ungrammatical before a bare singular ("alle Klinik" is not German), "nur diese
# Klinik" is ordinary, TRUE, NARROW German -- "only this [one] clinic [in Straubing] answered" -- and
# the old alternative matched on "nur diese/die" ALONE, no object required at all, so that honest
# sentence tripped the same rule TASK-151 wrote to catch "nur diese 5 Kliniken" and killed the turn
# twice in a row (both the reply and its corrective rewrite, reviewer run 11). So this alternative
# now requires the same kind of PLURAL count noun the exhaustive claim is actually about, immediately
# the way "alle ... Kliniken" does -- singular "Klinik"/"Haus"/"Stelle" no longer qualifies, only the
# board's own plural forms do.
_EXHAUSTIVE_PLURAL_RE = r"(kliniken|krankenh(ä|a)user|h(ä|a)user|stellen|angebote|positionen)"
# "alle" BEFORE ITS OWN NOUN, A PREPOSITION OPTIONALLY BETWEEN THEM (ROUND 5, 2026-09-22: the
# preposition split round 4 introduced here -- a negative lookahead sending "alle bei/beim/im/in
# NOUN" to a separate, scoped-evidence-checked regex -- is gone). "alle Kliniken", "alle 5 Kliniken
# in Bayern" and "alle beim Klinikum St. Elisabeth" now match the SAME alternative: detecting the
# claim SHAPE no longer needs to tell them apart, because this rule no longer verifies the claim
# against evidence at all -- see the module docstring's ROUND 5 for why (it stops blocking; detection
# alone does not need to distinguish a claim about what exists from a claim about where it sits).
# Any preposition, not the round-4 enumeration of four: round 4's bei/beim/im/in list still missed
# "alle AM Klinikum" -- the live sentence that reopened the round-4 bug one preposition later -- and
# free German always has one more. Matching "alle" plus [0,40} chars plus the noun, with nothing
# excluded in between, closes that the only way that generalises: by not enumerating at all.
_EXHAUSTIVE_RE = re.compile(
    r"\b(alle|allen|alles|s(ä|a)mtliche\w*)\b[^.!?\n]{0,40}\b(klinik\w*|"
    r"h(ä|a)user|haus|stelle\w*|angebot\w*|position\w*)\b|\bkomplett\w*\b|"
    r"\bsonst\s+(nichts|keine|kein)\b|"
    r"\bnur\s+(diese|die|noch\s+diese)\b[^.!?\n]{0,40}\b" + _EXHAUSTIVE_PLURAL_RE + r"\b|"
    r"\bdas\s+sind\s+alle\b|\bmehr\s+(gibt|haben|ist)\b"
    r"[^.!?\n]{0,20}\bnicht\b|\bkeine\s+weiteren\b|\bweitere\w*\b[^.!?\n]{0,40}\bnicht\b|"
    r"\bnichts\s+(weiter|mehr)\b", re.I)
# The same sentence read as the POOL OFFER instead: "allen Kliniken vorschlagen" is a branch, not a
# claim about how many exist. "vormerk" added (F3, verification 2026-09-22): the offer turn's own
# required pool wording -- "...oder darf ich Sie gleich fuer alle dort passenden Stellen vormerken?"
# -- is this same branch said with a verb this list did not cover, so a correct, required sentence
# flagged on every offer turn that phrased it this way. A flag that fires on the happy path teaches a
# human to ignore flags; scoped to the sentence carrying the verb (_exhaustive_claim reads per
# sentence), so a genuine exhaustive claim elsewhere in the same reply still flags.
_OFFER_SENTENCE_RE = re.compile(r"vorschlag|vorstell|weiterleit|\bpool\b|bewerb|schicke|vormerk", re.I)

# "say how many more there are" -- a true number next to a more-word. Ivan's rule states a positive
# obligation, so that is what is checked, rather than a list of ways to break it.
_REMAINDER_RE = re.compile(
    r"\b(weitere\w*|mehr|insgesamt|noch|davon|andere|zus(ä|a)tzlich\w*)\b[^.!?\n]{0,30}?(\d{1,6})\b|"
    r"\b(\d{1,6})\b[^.!?\n]{0,30}?\b(weitere\w*|mehr|insgesamt|andere|zus(ä|a)tzlich\w*)\b", re.I)
# A number the message ASSERTS as a count of positions or houses. Every one of these has to be true,
# exactly or as an honest German approximation (see _approx_supported below); hours, years and grades
# are not counts of anything on the board and are not touched. Before TASK-151 the rule was satisfied
# by the PRESENCE of one true number anywhere, so "über 4000 offene Pflegestellen, davon 27 weitere
# hier" passed on the 27 (reviewer F7).
#
# GERMAN NUMBER LITERALS (defect found 2026-09-22, live board, TASK-144 follow-up). German groups
# thousands with a dot ("2.300") or, less often, a thin (U+2009) or non-breaking (U+00A0/U+202F)
# space; a decimal, on the rare occasion one sits next to a count, uses a comma. The old pattern read
# only bare ASCII digits, so "2.300" broke at the dot and the rule saw "300" -- a true "bayernweit
# über 2.300 Stellen" was rejected for stating a number nobody wrote. A plain space is deliberately
# NOT a thousands separator here: two unrelated numbers can sit a plain space apart ("5 200 Meter
# entfernt" is two facts, not one), and there is no way to tell those apart from a genuine grouping, so
# only the unambiguous German shapes are read. A dot only ever groups thousands between a 1-3 digit
# head and one or more 3-digit tails, so "1.500" is unambiguously 1500 -- there is no locale guess to
# make, only the one convention German print already uses.
_DE_NUM_RE = r"\d{1,3}(?:[.   ]\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
# The German words that turn an exact COUNT into an honest approximation, captured so
# _approx_supported knows which of the three shapes it is reading (see there). Immediately before the
# number, the way a German sentence actually puts them.
_APPROX_MARKER_RE = r"über|ueber|mehr\s+als|rund|etwa|ca\.?|knapp|fast|gut"
_COUNT_CLAIM_RE = re.compile(
    r"\b(?:(?P<marker>" + _APPROX_MARKER_RE + r")\s+)?(?P<num>" + _DE_NUM_RE + r")"
    r"\s+(?:\w+\s+){0,2}?(?:stellen|stelle|pflegestellen|kliniken|"
    r"klinik|h(?:ä|a)user|krankenh(?:ä|a)user|stellenangebote|positionen|angebote|jobs)\b", re.I)
# marker text (folded) -> which of the three shapes it admits. "über/mehr als/gut" all promise a
# FLOOR ("a good 2.000", "über 2.000" -- at least this many); "knapp/fast" promise a CEILING approached
# from below ("just short of"); "rund/etwa/ca." promise proximity on either side. Folded because fold()
# already collapses "über"/"ueber" to the same string and strips the space in "mehr als" and the dot
# in "ca." (see fold()).
_APPROX_KIND = {"uber": "at_least", "mehrals": "at_least", "gut": "at_least",
                "knapp": "just_under", "fast": "just_under",
                "rund": "near", "etwa": "near", "ca": "near"}


def _de_number(literal):
    """A German-written number literal -> int. The dot/thin-space/non-breaking-space groups above are
    thousands separators and are simply removed; a comma introduces a decimal and is never removed --
    a COUNT is a whole number of postings or houses, so the fractional part (not meaningful here, and
    never seen in practice next to "Stellen") is dropped rather than folded into a different whole
    number: "2,5" reads as 2, never as 25."""
    whole = str(literal).split(",", 1)[0]
    for sep in (".", " ", " ", " "):
        whole = whole.replace(sep, "")
    return int(whole)


def _round_tolerance(n):
    """Half the rounding unit ``n``'s own trailing zeros imply -- this is the mathematical definition
    of "rounded to the nearest unit", not a picked constant: a figure written as "2.500" carries two
    trailing zeros, so it was rounded to the nearest hundred, and the true value of a number rounded to
    the nearest hundred lies within 50 of it by definition. "18" carries no trailing zero, so its
    tolerance is 0 -- it reads as exact, because nothing about how it is written suggests otherwise.
    This is what stops an approximation marker from laundering an arbitrary figure: the number itself,
    not the marker, sets how much slack it gets."""
    n = abs(int(n))
    if n == 0:
        return 0
    unit = 1
    while n % (unit * 10) == 0:
        unit *= 10
    return unit // 2


def _approx_supported(marker, n, true):
    """Whether a COUNT of ``n``, written with the approximation ``marker`` (folded), holds against the
    turn's true evidence numbers ``true``. Ivan's rule (b) extended to honest German rounding (defect
    found live 2026-09-22): "über 2.300" against a true 2.462 is ordinary, truthful German, not
    invention -- but the marker only ever admits a number that is actually close to a real one; it
    cannot rescue a figure with no evidence behind it at all."""
    kind = _APPROX_KIND.get(marker, "")
    if kind == "at_least":
        return any(e >= n for e in true)
    if kind == "just_under":
        tol = _round_tolerance(n)
        return any(0 < n - e <= tol for e in true)
    if kind == "near":
        tol = _round_tolerance(n)
        return any(abs(e - n) <= tol for e in true)
    return n in true
# The offer's two branches, in the words a German reply actually uses for them (offer.BRANCHES).
_NARROW_RE = re.compile(r"eingrenz|einschr(ä|a)nk|genauer|konkreter|filter", re.I)
_POOL_RE = re.compile(r"\ball(e|en)\b[^.!?\n]{0,40}\b(klinik\w*|h(ä|a)user|stellen)\b|\bpool\b", re.I)
# Any link. The payload carries none (offer.py, tools_server), so one in a bubble came out of the
# model's own memory -- Ivan's standing rule is that a candidate never gets a board link.
#
# A GENERIC HOST SHAPE, NOT A TLD ALLOWLIST (TASK-151). The old seven-item list (de|com|org|net|eu|
# io|info) missed the live board's own ad URLs on .pro, .med and .bayern, and every German clinic
# career domain on .jobs or .health (reviewer F11/F20). So any lowercase dotted host with a 2-24
# letter last label counts. The trade-off is stated rather than guarded: a German sentence with a
# missing space after a full stop ("dank.ich") has the same shape and is rejected too. That costs the
# model its draft and a corrective retry, never the candidate their answer (see check_reply), while
# the other direction sends a candidate a board URL -- Ivan's rule, not a preference.
#
# File names are the one shape excluded, because "lebenslauf.pdf" is a thing this funnel talks about
# in almost every thread and is not a host.
_SCHEME_RE = re.compile(r"https?://|\bwww\.", re.I)
_HOST_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.([a-z]{2,24})\b")
_NOT_A_TLD = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "heic", "doc", "docx", "odt", "txt",
              "zip", "csv", "xls", "xlsx", "ppt", "pptx", "mp3", "mp4", "m4a", "ogg", "opus",
              "wav", "mov", "eml"}
# The obvious ways to write a host without writing a dot. Normalised before the link test, because a
# candidate can click "www(.)pflege-job-radar(.)de" exactly as well as the real thing.
_OBFUSCATED_DOT_RE = re.compile(r"\s*(?:\(\s*(?:\.|punkt|dot)\s*\)|\[\s*(?:\.|punkt|dot)\s*\]|"
                                r"\s(?:punkt|dot)\s)\s*", re.I)


def has_link(text):
    """The text carries something a candidate could open -- see the regexes above."""
    text = _OBFUSCATED_DOT_RE.sub(".", str(text or ""))
    if _SCHEME_RE.search(text):
        return True
    return any(m.group(1) not in _NOT_A_TLD for m in _HOST_RE.finditer(text))
# "It is still free" -- the claim that killed rule (a) when the posting had since been marked gone.
_STILL_OPEN_RE = re.compile(r"\b(noch|weiterhin|immer\s+noch)\b[^.!?\n]{0,30}\b(frei|offen|verf(ü|u)gbar|"
                            r"zu\s+haben|ausgeschrieben)\b|\bist\s+(frei|offen|verf(ü|u)gbar)\b", re.I)


class ReplyRejected(AssertionError):
    """A reply that must not go out as written. An AssertionError so the tests and the existing
    callers that match on the rule name keep working; its own type so app/wa/luna_brain.py can tell
    it from any other assertion and run the corrective retry (a rejected reply must never become
    silence)."""


def _sentence_of(text, at):
    for m in _SENTENCE_RE.finditer(str(text or "")):
        if m.start() <= at < m.end():
            return m.group(0)
    return str(text or "")


def _resolve(mention, allowed_by_fold, board_folds):
    """The EVIDENCE name a mention resolves to, or "".

    Equal after folding, or the written name is SHORTER than the evidence name and sits inside it --
    a reply may write the board's own name or a prefix of it ("Klinikum Nürnberg" for "Klinikum
    Nürnberg - Betriebsstätte Süd"), never a string longer than the evidence.

    CONTAINMENT IS ONE-DIRECTIONAL (TASK-151). It used to go both ways, so a name CONTAINING an
    evidence name was accepted: the model looked up Mindelheim, wrote "Die Kreisklinik Mindelheim
    Nord sucht Pflegefachkräfte", and an invented site of a real house went out AND was written into
    the thread's grounded memory, where it became permanently sayable (reviewer F5).

    Containment still stops at a DIFFERENT board record: "Klinikum Nürnberg" is not grounded by
    evidence for a house whose full name merely starts the same way when the board has both (audit
    A2, 16 such pairs live)."""
    low = fold(mention)
    if low in allowed_by_fold:
        return allowed_by_fold[low]
    if low in board_folds:
        return ""
    return next((name for a, name in allowed_by_fold.items() if low and low in a), "")


def _exhaustive_claim(text):
    """The sentence that says the named ones are all there are, or "".

    Read per sentence, and a sentence that OFFERS the pool ("soll ich Sie allen Kliniken
    vorschlagen?") is a branch rather than a claim about how many exist."""
    for m in _SENTENCE_RE.finditer(str(text or "")):
        sentence = m.group(0)
        if _EXHAUSTIVE_RE.search(sentence) and not _OFFER_SENTENCE_RE.search(sentence):
            return sentence.strip()
    return ""


def _claim_subject(sentence, cities):
    """The board city ``sentence`` names, folded, or "" when it names none -- which SUBJECT an
    approximation marker in this sentence is being asked to support (see ``_false_counts``). Reuses
    ``_board_hits``'s own fold/word-boundary match, so a multi-word town ("Bad Kissingen") and the
    population's umlaut-free spelling of one both count; the longest, earliest hit wins when a
    sentence happens to name more than one (mirrors ``mention_spans``'s own tie-break)."""
    hits = sorted(_board_hits(sentence, cities), key=lambda h: (h[0], -(h[1] - h[0])))
    return fold(hits[0][2]) if hits else ""


def _false_counts(text, evidence, structural, by_city=None):
    """Every COUNT claim in ``text`` this turn's evidence does not support (exactly, or as an honest
    approximation -- see _approx_supported), as (parsed value, the number literal as written, the
    whole matched phrase) -- not just the value, so a rejection can say how it was read off the text
    (2026-09-22 defect: "2.300" silently became 300 and nothing said why).

    EVIDENCE MAY ANCHOR A MARKER; STRUCTURAL MAY ONLY MATCH EXACTLY (round-2 audit, 2026-09-22).
    ``evidence`` is a fact about the MARKET -- computed by the harness from the live board, never
    chosen by us -- so an honest approximation of it is ordinary truthful German. ``structural`` is a
    fact about THIS MESSAGE (how many positions it actually lists) or a constant we picked
    (OF.OFFER_LIMIT, the display cap): true of the bubble, not of the market, so it may satisfy a
    BARE EXACT statement about what is being shown ("diese 5 Stellen") but must never anchor a
    marker. OF.OFFER_LIMIT is 5 whether the board holds 5 postings or 5000; letting it anchor "rund
    10" or "gut 5" laundered an invented figure past the check with zero market evidence behind it --
    exactly what an Opus reviewer caught live, 2 of 7 runs on the same question, 2026-09-22.

    A MARKER ANCHORS ONLY ON ITS OWN SUBJECT (round-3 audit, 2026-09-22). ``evidence`` is unscoped --
    true of the WHOLE board -- but a sentence that names a specific city is making a claim about that
    city, not about Bavaria, and "über N" is a floor with no ceiling: once a large board-wide number
    sits in ``evidence`` (which it always does now, see turn_evidence), it satisfies "über" applied to
    almost anything. Decision taken here: when the sentence containing a marker names one of the
    board's own cities (``_claim_subject``), the marker may anchor ONLY on ``by_city[that city]`` --
    this turn's own tool-call numbers for THAT city -- never on the unscoped board-wide figures and
    never on a different city's. A sentence naming no city keeps the old, unscoped ``evidence`` (a
    bayernweit approximation is still checked against the whole board, as it always was). This is
    what stopped "München hat über 40 Kliniken" from leaning on a true, unrelated "278" board-wide
    total with zero München-specific evidence behind it (live, Opus reviewer, 2026-09-22) -- and, by
    the same rule, now requires the model to have actually looked München up before approximating
    about it. EXACT statements are unaffected on purpose: they already have to equal a real number,
    which a different subject's total satisfies only by coincidence, and the reviewer's defect was
    specific to the unbounded-floor marker, not to exact matches."""
    true_evidence = {int(c) for c in evidence if isinstance(c, int)}
    true_exact = true_evidence | {int(c) for c in structural if isinstance(c, int)}
    cities = board_cities()
    wrong = []
    for m in _COUNT_CLAIM_RE.finditer(str(text or "")):
        n = _de_number(m.group("num"))
        marker = fold(m.group("marker") or "")
        if marker:
            subject = _claim_subject(_sentence_of(text, m.start()), cities)
            true_for_marker = (by_city or {}).get(subject, set()) if subject else true_evidence
            ok = _approx_supported(marker, n, true_for_marker)
        else:
            ok = n in true_exact
        if not ok:
            wrong.append((n, m.group("num"), m.group(0).strip()))
    return wrong


def _masked(bubble, spans):
    """The bubble with the given spans blanked out -- what is left over for the LINK rule.

    Three live board clinic names ARE hostnames ("jobs.sana.de", "karriereportal.kirinus.de",
    "vitrea-gesundheit.de", 7 live postings between them). Running the link test over the raw text
    rejected a truthful, fully grounded reply that named one of them and handed the candidate the
    holding message plus a colleague (reviewer F21). The name is evidence; everything around it is
    still checked."""
    out = list(str(bubble or ""))
    for span in spans:
        for i in range(span["start"], min(span["end"], len(out))):
            out[i] = " "
    return "".join(out)


def check_reply(bubbles, allowed, board=None, *, deniable=(), counts=(), counts_by_city=None,
                remaining=0, branches=False, stale=(), stale_postings=(), shown_before=(),
                postings=None, flagged=None):
    """Every rule that holds on the text about to be sent. -> the grounded clinic names it named,
    in the board's own spelling.

    ``counts_by_city`` (round-3 audit, 2026-09-22) is GR.turn_evidence's per-city split of ``counts``
    -- see ``_false_counts`` for what it is for. Optional and defaulting to no scoped evidence at all
    (every marker then falls back to the unscoped ``counts``, today's behaviour) so every caller and
    every existing test that predates this parameter keeps working unchanged.

    Raises ReplyRejected (an AssertionError) naming the rule for the four BLOCKING rules (NO
    INVENTION, COUNT's figure check, STALE, VOLUME with its remainder/BRANCHES obligation).
    app/wa/luna_brain.py:turn catches it, tells the model exactly what it broke and lets it write the
    turn once more; a second violation sends the harness's own short holding reply and flags the
    thread for a human. It never becomes silence -- that was the cost of this check before (four
    shapes of truthful German died and the candidate heard nothing, audit C).

    ``flagged`` is different (ROUND 5, 2026-09-22, module docstring): the exhaustive-claim check --
    "these are all there are" -- no longer raises. When the caller passes a list, the sentence it
    suspects is appended to it; the bubbles are still returned normally and the reply still goes out.
    The caller (app/wa/luna_brain.py) records what was appended the same way an escalation is
    recorded today, for a human to see -- see the module docstring for why this one rule flags while
    the other four still block."""
    board = board_clinic_names() if board is None else board
    corroborating = board_name_words()
    heads = board_head_words()
    job_words = board_job_words()
    position_words = board_position_words()
    board_folds = {fold(n) for n in board}
    allowed_by_fold = {fold(n): n for n in allowed}
    deniable = {fold(n) for n in deniable}
    postings = postings or {}

    named, claimed, total_positions, offered = [], [], 0, 0
    without_names = []
    for bubble in bubbles:
        spans = mention_spans(bubble, board, corroborating, heads, job_words)
        without_names.append(_masked(bubble, spans))
        grounded_spans = [s for s in spans if _resolve(s["text"], allowed_by_fold, board_folds)]
        if has_link(_masked(bubble, grounded_spans)):
            raise ReplyRejected(
                f"the reply carries a link -- LINK (TASK-144, Ivan's rule): a candidate never gets a "
                f"board URL or a job link. Say it in words instead. Offending bubble: {bubble[:120]!r}")
        kept = []
        for span in spans:
            sentence = _sentence_of(bubble, span["start"])
            if fold(span["text"]) in deniable and not _offers_a_position(sentence):
                # The candidate named this house, or this thread named it before the board lost it,
                # and the sentence claims no vacancy at it: it offers nothing and costs no position.
                continue
            kept.append(span)
            claimed.append(span["text"])
        total_positions += positions(bubble, kept, position_words)
        offered += new_positions(bubble, kept, shown_before, position_words)
        named.extend(s["text"] for s in kept)

    text = " ".join(bubbles)
    # The same text with every written clinic name blanked out. Board names carry digits ("Klinikum
    # Augsburg 2", "310klinik"), and reading those as a count of positions rejected a truthful reply.
    numbers_text = " ".join(without_names)
    resolved = {m: _resolve(m, allowed_by_fold, board_folds) for m in dict.fromkeys(named)}
    # STALE comes first, before NO INVENTION: a house this thread grounded before that the board has
    # since lost is not in the evidence set any more, so the invention rule would fire on it and tell
    # the model to look it up -- true, but not the correction that fits. This one says the thing the
    # model has to do instead, which is to search again before confirming.
    #
    # WHAT MAKES IT FRESH AGAIN is a tool call in THIS turn. Once the thread has lost a posting or a
    # house, a still-open claim has to rest on evidence from this turn -- ``postings`` is what the
    # turn's own tool calls and the harness offer returned. Without that the rule either never fired
    # (the old clinic-name test, reviewer F7) or could never be satisfied again on the thread.
    if (stale or stale_postings) and _STILL_OPEN_RE.search(text) and not _DENIAL_RE.search(text):
        unbacked = [m for m, name in resolved.items() if fold(name or m) not in postings]
        if unbacked or not resolved:
            raise ReplyRejected(
                f"the reply says a position is still open, but this thread already lost "
                f"{list(stale)!r} / posting(s) {list(stale_postings)!r} and nothing it names "
                f"({unbacked!r}) was looked up in THIS turn -- STALE (TASK-144, Ivan's rule (a)): "
                f"what was true last turn is not evidence now. Search again before confirming, or "
                f"say the position is no longer available")
    ungrounded = [m for m, name in resolved.items() if not name]
    if ungrounded:
        raise ReplyRejected(
            f"the reply names {ungrounded!r}, which no tool call on this thread returned and the "
            f"harness offer does not contain -- NO INVENTION (TASK-144). Call the tool that would "
            f"return it and write the turn again, or say plainly that we do not have it. "
            f"In evidence: {sorted(allowed)!r}")
    if total_positions > OF.OFFER_LIMIT:
        raise ReplyRejected(
            f"the reply carries {total_positions} positions, more than the {OF.OFFER_LIMIT} one message "
            f"may name -- VOLUME (TASK-144): a listed item counts as a position whether or not it "
            f"names a house. Name at most {OF.OFFER_LIMIT}, say how many more matched, and offer the "
            f"two branches")
    # STRUCTURAL, not evidence (round-2 audit, 2026-09-22): facts about THIS bubble (how many
    # positions it lists, how many names it grounded) or the display cap we chose -- true of the
    # message, not of the market. See _false_counts.
    structural_counts = {total_positions, len(named), OF.OFFER_LIMIT}
    wrong = _false_counts(numbers_text, counts, structural_counts, by_city=counts_by_city)
    if wrong:
        seen = "; ".join(f"{n} (read off {literal!r} in {phrase!r})" for n, literal, phrase in wrong)
        raise ReplyRejected(
            f"the reply states {seen} as a count of positions or clinics, and none of those is a "
            f"number this turn's evidence supports, exactly or as an honest approximation -- COUNT "
            f"(TASK-144, Ivan's rule (b)). Drop the figure or replace it with one of these: "
            f"{sorted(counts)!r}")
    # "These are all there are" is a FLAG, not a BLOCK (ROUND 5, 2026-09-22 -- module docstring has
    # the asymmetry argument). Read per sentence and gated on ``remaining > 0`` the same way it always
    # was -- that gate is now only a heuristic for whether flagging is worth doing at all, not a
    # correctness requirement, since nothing downstream of this trusts the flag as a verified
    # rejection. Covers both surface shapes since ROUND 5 merged them back into one regex: "alle
    # Kliniken" and "alle beim Klinikum X" alike.
    claim = _exhaustive_claim(text) if remaining > 0 else ""
    if claim and flagged is not None:
        flagged.append(claim)
    # The remainder and the two branches are the OFFER turn's rules, so they key on the positions
    # this message OFFERS -- not on how many clinic names it happens to carry (TASK-151). Keying on
    # names forced a factual follow-up about two houses it had already offered to recite a total and
    # both branch wordings or die, twice in a row, and the candidate got the holding message
    # (reviewer F11); and it missed a five-item list that named no house at all (reviewer F13).
    if remaining > 0 and offered >= 2:
        if not any(int(n) in counts for m in _REMAINDER_RE.finditer(numbers_text)
                   for n in m.groups() if n and str(n).isdigit()):
            raise ReplyRejected(
                f"the reply offers {offered} positions while {remaining} more matched, without "
                f"saying how many more -- COUNT (TASK-144, Ivan's rule (b)). Write the remainder next "
                f"to a word like 'weitere' or 'insgesamt', using one of these numbers: {sorted(counts)!r}")
        if branches and not (_NARROW_RE.search(text) and _POOL_RE.search(text)):
            missing = [w for w, ok in (("narrow the search", _NARROW_RE.search(text)),
                                       ("be put forward to all matching clinics (the pool)",
                                        _POOL_RE.search(text))) if not ok]
            raise ReplyRejected(
                f"the offer turn does not put both branches to the candidate -- BRANCHES (TASK-144, "
                f"Ivan's rule (b)): missing {missing!r}. Both go in the SAME message as the positions")
    # The EVIDENCE spelling, never the model's: what goes into the thread's grounded memory has to be
    # a name the board really has (reviewer F5).
    return list(dict.fromkeys(resolved[m] for m in resolved))
