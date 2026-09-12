"""What Valentina says next.

The reply is decided here, deterministically, from the same job snapshot GET /api/jobs serves
(app/data.py). Two ideas carry it:

**The next question is the one the data says is worth asking.** Instead of a fixed script, every
unfilled slot is scored against the postings that still match this lead (``_gain``): how many of them
carry a value for it, and how evenly that value splits them. Asking "Intensiv oder OP?" when 90 % of
the remaining rows are Intensiv buys nothing; asking it when the split is 40/35/25 halves the list.

**One question per turn, two bubbles at most.** That is the production bot's style rule
(apps/connectors/candidate_luna_first.py ``STYLE`` on tasker-dispatcher-01) and here it is a checked
invariant, not advice: ``_check`` raises when a turn would send more, so a regression fails a test
instead of a candidate's chat.

The counts that come back from the filter are part of the answer -- "Intensiv in München: 12 offene
Stellen" is both terse and true, and it is the one thing a candidate cannot get from an ad.
"""
from .. import data as D
from . import slots as SL

MATCH_LIMIT = 3                 # postings named in one bubble; more than three is a wall of text
ENOUGH = 8                      # at or below this many hits, stop narrowing and show the list
MAX_QUESTIONS = 5               # ask at most this many slot questions before handing over
MIN_GAIN = 0.08                 # below this a question does not split the list enough to be worth a turn
MAX_BUBBLES = 2
MAX_BUBBLE_CHARS = 320

ROLE_LABEL = {"pflegefachkraft": "Pflegefachkraft", "fachpflege": "Fachpflege", "ota_ata": "OTA/ATA",
              "leitung": "Leitung", "praxisanleitung": "Praxisanleitung", "apn_experte": "Pflegeexperte/APN",
              "hebamme": "Hebamme", "pflegehelfer": "Pflegehelfer", "sonstige_pflege": "Pflege"}
HOURS_LABEL = {"vollzeit": "Vollzeit", "teilzeit": "Teilzeit", "minijob": "Minijob"}
URKUNDE_LABEL = {"urkunde": "Urkunde", "defizit": "Defizitbescheid", "kenntnispruefung": "Kenntnisprüfung bestanden",
                 "beantragt": "Anerkennung beantragt", "keine": "keine Anerkennung"}


# --- reading the board ---------------------------------------------------------------------------

def jobs_for(slots):
    """The postings that still match this lead, newest first (app/data.py:filter_jobs, same
    parameters GET /api/jobs takes)."""
    return D.filter_jobs(SL.filters(slots))


def known_cities():
    """Towns the board has open postings in -- the vocabulary read_city may answer with."""
    seen = {}
    for j in D.jobs():
        for key in ("city", "clinic_town"):
            v = (j.get(key) or "").strip()
            if v:
                seen[v] = seen.get(v, 0) + 1
    return sorted(seen, key=lambda c: (-seen[c], c))


def _values(rows, slot):
    """(value -> count) over rows for one slot, skipping rows that do not state it."""
    out = {}
    for r in rows:
        for v in _row_values(r, slot):
            out[v] = out.get(v, 0) + 1
    return out


def _row_values(r, slot):
    if slot == "role":
        return [r["role_class"]] if r.get("role_class") else []
    if slot == "where":
        v = r.get("city") or r.get("clinic_town")
        return [v] if v else []
    if slot == "department":
        return [r["department_hint"]] if r.get("department_hint") else []
    if slot == "hours":
        return [v for v in (r.get("employment_types") or []) if v]
    if slot == "housing":
        return ["ja"] if r.get("enr_housing") else []
    return []


def _gain(rows, slot):
    """How much asking this slot would narrow the list: coverage x (1 - the share of its top value).

    Zero when nothing states it (nothing to filter on) and zero when every row shares one value
    (the answer is already known). Housing is a yes/no, so its 'coverage' is the split itself.
    """
    if not rows:
        return 0.0
    counts = _values(rows, slot)
    if not counts:
        return 0.0
    n = len(rows)
    if slot == "housing":
        share = counts.get("ja", 0) / n
        return min(share, 1 - share) * 2          # 0 at 0/100, 1 at a 50/50 split
    coverage = min(1.0, sum(counts.values()) / n)
    return coverage * (1 - max(counts.values()) / sum(counts.values()))


# --- the questions -------------------------------------------------------------------------------

def _top(counts, k):
    return [v for v, _ in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:k]]


def _question(slot, rows):
    """-> {slot, text, buttons} for one slot, with the button titles taken from what is actually open.

    Buttons are Meta reply buttons (max 3, title max 20 chars); free text is always accepted too,
    so a lead who types 'Stroke Unit' is not stuck with the three offered options.
    """
    counts = _values(rows, slot)
    if slot == "role":
        opts = _top(counts, 3)
        return {"slot": "role", "text": "Was machen Sie beruflich?",
                "buttons": [{"id": f"role:{v}", "title": ROLE_LABEL.get(v, v)[:20]} for v in opts]}
    if slot == "where":
        opts = _top(counts, 3)
        return {"slot": "where", "text": f"Wo möchten Sie arbeiten? Am meisten offen ist gerade in {', '.join(opts[:3])}.",
                "buttons": [{"id": f"city:{v}", "title": v[:20]} for v in opts]}
    if slot == "department":
        opts = _top(counts, 3)
        return {"slot": "department", "text": "Welcher Bereich?",
                "buttons": [{"id": f"dept:{v}", "title": v[:20]} for v in opts]}
    if slot == "hours":
        opts = [v for v in ("vollzeit", "teilzeit") if v in counts] or ["vollzeit", "teilzeit"]
        return {"slot": "hours", "text": "Vollzeit oder Teilzeit?",
                "buttons": [{"id": f"hours:{v}", "title": HOURS_LABEL[v]} for v in opts]}
    if slot == "housing":
        return {"slot": "housing", "text": "Brauchen Sie eine Wohnung?",
                "buttons": [{"id": "housing:ja", "title": "Ja"}, {"id": "housing:nein", "title": "Nein"}]}
    raise ValueError(f"no question for slot {slot!r}")


URKUNDE_QUESTION = {"slot": "urkunde",
                    "text": "Haben Sie die deutsche Berufsurkunde, einen Defizitbescheid, oder die Kenntnisprüfung bestanden?",
                    "buttons": [{"id": "urk:urkunde", "title": "Urkunde"},
                                {"id": "urk:defizit", "title": "Defizitbescheid"},
                                {"id": "urk:kenntnispruefung", "title": "Prüfung bestanden"}]}
HANDOVER_QUESTION = {"slot": "handover",
                     "text": "Soll ich Ihr Profil anonym an die Pflegedirektion dieser Häuser schicken?",
                     "buttons": [{"id": "ho:ja", "title": "Ja, bitte"}, {"id": "ho:nein", "title": "Noch nicht"}]}

# Order the slots are considered in when two score the same, so a thread is reproducible.
LADDER = ("role", "where", "department", "hours", "housing")


ASK_AGAIN_PENALTY = 0.2         # a slot the lead already skipped once is worth less than a fresh one
MAX_ASKS_PER_SLOT = 2           # asked twice and still unanswered: the lead does not want to say


def next_question(rows, slots, asked):
    """The still-open slot whose answer would narrow this list most, or None when none is worth a turn.

    A question already asked is not struck off -- a lead who answers "Intensiv" to "which region?"
    leaves the region open, and the region is still the most useful thing to know. It is only
    discounted, and dropped for good after MAX_ASKS_PER_SLOT tries, so the harness cannot nag.
    """
    scored = []
    for s in LADDER:
        tries = asked.count(s)
        if _filled(slots, s) or tries >= MAX_ASKS_PER_SLOT:
            continue
        gain = _gain(rows, s) - ASK_AGAIN_PENALTY * tries
        if gain >= MIN_GAIN:
            scored.append((round(gain, 4), -LADDER.index(s), s))
    if not scored:
        return None
    return _question(max(scored)[2], rows)


def _filled(slots, slot):
    if slot == "where":
        return bool(slots.get("city") or slots.get("bezirk"))
    return slots.get(slot) is not None


# --- reading an answer ---------------------------------------------------------------------------

def read_answer(text, slots, asked, cities):
    """Everything the message says, not only the answer to the last question.

    A lead asked for a department who writes "Intensiv, am liebsten München, Teilzeit" fills three
    slots in one turn. The latest statement wins: naming another town later is a correction, not a
    second preference, and a chat where "eigentlich lieber Augsburg" is ignored is worse than useless.

    The yes/no slots (housing, Urkunde, handover) are read only once their question has been asked,
    because "ja" on its own means nothing before that.
    """
    found = {}
    for key, value in (("role", SL.read_role(text)), ("department", SL.read_department(text)),
                       ("hours", SL.read_hours(text)), ("city", SL.read_city(text, cities))):
        if value is not None and slots.get(key) != value:
            found[key] = value
    bezirk = SL.read_bezirk(text)
    if bezirk and not (found.get("city") or slots.get("city")) and slots.get("bezirk") != bezirk:
        found["bezirk"] = bezirk
    if found.get("city"):
        found["bezirk"] = None                  # a town is the sharper filter; drop the region
    last = asked[-1] if asked else None
    # A bare "ja" answers the question that was just asked, never one from four turns ago. The
    # keyword forms ("Wohnung", "Urkunde", "nicht bestanden") are read whenever they appear.
    housing = SL.read_housing(text) if last == "housing" else SL.read_housing_explicit(text)
    if housing is not None and slots.get("housing") != housing:
        found["housing"] = housing
    urkunde = SL.read_disqualifier(text) or (
        SL.read_urkunde(text) if last == "urkunde" else SL.read_urkunde_explicit(text))
    if urkunde and not slots.get("urkunde"):
        found["urkunde"] = urkunde
    if last == "handover" and slots.get("handover") is None:
        if SL.says_yes(text):
            found["handover"] = True
        elif SL.says_no(text):
            found["handover"] = False
    return found


def button_answer(button_id):
    """Reply-button ids are stable ('dept:Intensiv/IMC'), so a tap needs no language parsing."""
    prefix, _, value = str(button_id or "").partition(":")
    if not value:
        return {}
    return {"role": {"role": value}, "city": {"city": value}, "dept": {"department": value},
            "hours": {"hours": value}, "housing": {"housing": value == "ja"},
            "urk": {"urkunde": value}, "ho": {"handover": value == "ja"}}.get(prefix, {})


# --- what a turn looks like ----------------------------------------------------------------------

def describe(slots):
    """The lead in a few words, for the match bubble: 'Intensiv/IMC in München, Teilzeit'."""
    bits = [slots.get("department") or ROLE_LABEL.get(slots.get("role"), "Pflege")]
    where = slots.get("city") or slots.get("bezirk")
    if where:
        bits.append("in " + where)
    if slots.get("hours"):
        bits.append(HOURS_LABEL.get(slots["hours"], slots["hours"]))
    if slots.get("housing"):
        bits.append("mit Wohnung")
    return " ".join(bits[:2]) + ("" if len(bits) < 3 else ", " + ", ".join(bits[2:]))


def _clinic_name(name):
    """Long registry names do not fit a WhatsApp line; keep the distinguishing half."""
    raw = str(name or "").strip()
    if " - " in raw:
        left, right = raw.split(" - ", 1)
        if 8 < len(right) <= len(left) + 8:
            raw = right.strip()
    return raw[:46].rstrip() + "…" if len(raw) > 47 else raw


def match_bubble(rows, slots):
    """The list itself: how many, then at most three, then where to read them.

    One link per line, the posting's own ``source_url`` -- the board publishes no other outbound link
    and an unverifiable list is worth nothing to a nurse.
    """
    lines = [f"{len(rows)} offene Stellen passen: {describe(slots)}." if len(rows) != 1
             else f"Eine offene Stelle passt: {describe(slots)}."]
    for r in rows[:MATCH_LIMIT]:
        where = _clinic_name(r.get("clinic_name") or r.get("employer") or "")
        city = (r.get("city") or r.get("clinic_town") or "").strip()
        head = f"{where}, {city}" if city and city.casefold() not in where.casefold() else where
        title = (r.get("title") or "").strip()
        line = f"• {head} — {title[:60]}" if title else f"• {head}"
        url = (r.get("source_url") or r.get("external_url") or "").strip()
        lines.append(f"{line}\n{url}" if url else line)
    return "\n".join(lines)


# Order a wish is given up in when nothing matches: the loosest first, the job itself last.
DROP_ORDER = ("housing", "hours", "department", "city", "bezirk")
DROP_LABEL = {"housing": "Wohnungs-Wunsch", "hours": "Vollzeit/Teilzeit-Filter",
              "department": "Bereichs-Filter", "city": "Stadt-Filter", "bezirk": "Regions-Filter"}


def _und(labels):
    if len(labels) < 2:
        return labels[0] if labels else ""
    return ", ".join(labels[:-1]) + " und " + labels[-1]


def _widen(slots):
    """-> (slots, dropped[]) for the narrowest search that still finds something.

    Wishes are given up one at a time in DROP_ORDER until the filter returns rows, so a lead who
    asked for OP + Teilzeit + Wohnung in one town hears about the OP jobs there rather than nothing.
    Everything dropped is named in the message; this widens the search, it never widens the claim.
    """
    dropped = []
    wide = dict(slots)
    for slot in DROP_ORDER:
        if not wide.get(slot):
            continue
        wide = {k: v for k, v in wide.items() if k != slot}
        dropped.append(slot)
        if jobs_for(wide):
            return wide, dropped
    return wide, dropped if jobs_for(wide) else []


def _check(bubbles):
    """The style rule as an assertion: at most two bubbles, at most one question, nothing long."""
    if len(bubbles) > MAX_BUBBLES:
        raise AssertionError(f"{len(bubbles)} bubbles, the style rule allows {MAX_BUBBLES}")
    questions = sum(b.count("?") for b in bubbles)
    if questions > 1:
        raise AssertionError(f"{questions} questions in one turn, the style rule allows one")
    for b in bubbles:
        if not b.strip():
            raise AssertionError("empty bubble")
        if len(b) > MAX_BUBBLE_CHARS and "http" not in b:
            raise AssertionError(f"bubble over {MAX_BUBBLE_CHARS} chars: {b[:80]!r}")
    return bubbles


GREETING = ("Hallo, hier ist Valentina – ich bin die digitale Assistentin und helfe bei "
            "Pflegestellen in Bayern.")


def turn(text, thread, button_id=None):
    """One inbound message in, one decision out.

    -> {bubbles, buttons, slots, asked, stopped, matches, action}. Nothing is sent here and nothing is
    written here: app/wa/api.py owns the transport and the store, so this function stays testable
    against a stubbed snapshot and reads like the policy it is.
    """
    slots = dict(thread.get("slots") or {})
    asked = list(thread.get("asked") or [])
    last = asked[-1] if asked else None

    if SL.is_stop(text):
        return {"bubbles": [], "buttons": [], "slots": slots, "asked": asked, "stopped": True,
                "matches": [], "action": "stopped"}

    slots.update(button_answer(button_id) if button_id else
                 read_answer(text, slots, asked, known_cities()))
    slots = {k: v for k, v in slots.items() if v is not None or k == "housing"}

    # The Urkunde answer decides whether a placement is possible at all; the production bot refuses
    # to promise anything without one of the three accepted paths, and so does this.
    if slots.get("urkunde") and slots["urkunde"] not in SL.URKUNDE_OK:
        return {"bubbles": _check([
            "Ohne Urkunde, Defizitbescheid oder bestandene Kenntnisprüfung kann ich Sie bei einer Klinik "
            "noch nicht vorstellen.",
            "Sobald der Bescheid da ist, melden Sie sich – dann suche ich sofort passende Häuser."]),
            "buttons": [], "slots": slots, "asked": asked, "stopped": False, "matches": [],
            "action": "blocked_qualification"}

    if slots.get("handover") is True:
        return {"bubbles": _check(["Danke – ich gebe Ihr Profil anonym (ohne Name und Telefon) an die "
                                   "Pflegedirektion weiter und melde mich mit der Rückmeldung."]),
                "buttons": [], "slots": slots, "asked": asked, "stopped": False, "matches": [],
                "action": "handover_requested"}
    if slots.get("handover") is False:
        return {"bubbles": _check(["Alles gut – schreiben Sie mir, wenn Sie sich eines der Häuser genauer "
                                   "ansehen möchten."]),
                "buttons": [], "slots": slots, "asked": asked, "stopped": False, "matches": [],
                "action": "handover_declined"}

    rows = jobs_for(slots)
    first_turn = not asked and not thread.get("last_outbound_at")
    slot_questions = [a for a in asked if a in LADDER]

    q = None
    if len(rows) > ENOUGH and len(slot_questions) < MAX_QUESTIONS:
        q = next_question(rows, slots, asked)

    if q is not None:
        asked.append(q["slot"])
        lead = GREETING if first_turn else None
        if lead is None and slot_questions:
            lead = f"{len(rows)} Stellen passen bisher."
        bubbles = [b for b in (lead, q["text"]) if b]
        return {"bubbles": _check(bubbles), "buttons": q["buttons"], "slots": slots, "asked": asked,
                "stopped": False, "matches": rows[:MATCH_LIMIT], "action": "ask:" + q["slot"]}

    if not rows:
        # Nothing matches every answer. Say so, then drop the narrowest filter and show what is there
        # under the widened search -- named in the message, so the lead knows which wish was dropped.
        wider, dropped = _widen(slots)
        rows = jobs_for(wider) if dropped else []
        if not rows:
            asked.append("empty")
            return {"bubbles": _check([f"Für {describe(slots)} ist aktuell nichts offen.",
                                       "Welche andere Stadt oder welcher Bereich käme in Frage?"]),
                    "buttons": [], "slots": slots, "asked": asked, "stopped": False, "matches": [],
                    "action": "no_matches"}
        q3 = URKUNDE_QUESTION if not slots.get("urkunde") else HANDOVER_QUESTION
        asked.append(q3["slot"])
        head = f"Für {describe(slots)} ist nichts offen – ohne {_und([DROP_LABEL[d] for d in dropped])}:"
        return {"bubbles": _check([head + "\n" + match_bubble(rows, wider), q3["text"]]),
                "buttons": q3["buttons"], "slots": slots, "asked": asked, "stopped": False,
                "matches": rows[:MATCH_LIMIT], "action": "widened:" + ",".join(dropped)}

    q2 = URKUNDE_QUESTION if not slots.get("urkunde") else HANDOVER_QUESTION
    asked.append(q2["slot"])
    return {"bubbles": _check([match_bubble(rows, slots), q2["text"]]), "buttons": q2["buttons"],
            "slots": slots, "asked": asked, "stopped": False, "matches": rows[:MATCH_LIMIT],
            "action": "matches+" + q2["slot"]}
