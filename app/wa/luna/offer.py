"""TASK-144: what a candidate is shown about open positions, and the cap on how much of it.

Ivan, 2026-09-21, after the first conversation on the phone rail: a candidate never gets a wall of
vacancies. At most ``OFFER_LIMIT`` positions in one message, each a short description built from
board fields only, then how many more matched, then -- in the same turn -- a choice between two
branches: narrow the search, with the criteria that actually discriminate in THIS result set, or go
into the general pool and be put forward to every matching clinic.

The cap lives here, where the result set is assembled, not in the prompt: app/wa/luna/prompts.py can
only describe what to do with what this module already cut to size, so no prompt edit can raise it.
app/wa/luna_brain.py:market_snapshot calls ``build_offer`` and hands the result to the model;
app/wa/luna/grounding.py checks afterwards that the reply stayed inside it.

No position carries a URL. The board's ``external_url`` never leaves this module -- prompts.py has
banned sending a board URL or job link since TASK-91, and the way to make that a guarantee rather
than a rule is to keep the link out of the payload the model writes from. That stays true with
TASK-150: the original-ad link a TEST thread may ask for is looked up by posting_id in
app/wa/luna/source_link.py, after the model has written its reply, so no link ever passes through
the model on any thread.

THAT CLAIM NEEDED TWO MORE DOORS CLOSED (TASK-151). It was false in production on every thread:
``board_api_get`` -- a tool prompts.py tells the model to use -- returned the raw board rows with
``external_url`` on /api/jobs and ``website``/``careers_url``/``board`` on /api/clinics
(tools_server._without_urls closes that), and the remembered test-thread links rode into the next
payload on the card itself (luna_brain._user_payload hides them).
"""
from ... import data as D
from .board_vocabulary import city_of, clinic_name_of

# Ivan's cap, 2026-09-21: at most five positions in one message. app/wa/luna_brain.py:CLOSE_LIMIT is
# this same number under its older name (the close-sequence shortlist was the first place it applied).
OFFER_LIMIT = 5

# The two branches the offer must put to the candidate in the same turn, and the values
# card_patch.match_branch takes (app/wa/luna_brain.py records them on the card).
BRANCH_NARROW = "narrow"
BRANCH_POOL = "pool"
BRANCHES = (
    {"id": BRANCH_NARROW, "means": "narrow the search with one of narrow_by's criteria, then show the "
                                   "positions that are left"},
    {"id": BRANCH_POOL, "means": "put the candidate forward to every matching clinic -- the general pool, "
                                 "no narrowing"},
)

# The dimensions a result set can be narrowed along, most discriminating kind first on a tie. Each is
# read straight off the board row, so a suggestion is never a criterion the data cannot actually apply.
_DIMENSIONS = ("city", "department", "employment_type", "regierungsbezirk", "housing")


def clinic_name(row):
    return clinic_name_of(row)


def clinic_names(rows):
    return {clinic_name(r) for r in rows} - {""}


def _values(row, dimension):
    """The value(s) this row has on one narrowing dimension, or () when the board does not record it.

    A missing value is never a value: a posting with no department_hint must not turn into a
    "Department: unbekannt" criterion the candidate could pick and match nothing with."""
    if dimension == "city":
        return (city_of(row),) if city_of(row) else ()
    if dimension == "department":
        return (row["department_hint"],) if row.get("department_hint") else ()
    if dimension == "employment_type":
        return tuple(t for t in (row.get("employment_types") or []) if t)
    if dimension == "regierungsbezirk":
        return (row["regierungsbezirk"],) if row.get("regierungsbezirk") else ()
    return ("mit Wohnung",) if D.offers_housing(row) else ("ohne Wohnung",)


def narrowing_criteria(rows):
    """The criteria that would actually narrow THIS result set -- [{criterion, values: [{value, clinics,
    postings}], more_values}] -- most distinct values first.

    Ivan's rule: when more matched than we may name, suggest the concrete criteria that narrow it for
    this candidate, "taken from the actual result set". So every value here is one the rows in front of
    us carry, with the clinic and posting count it would leave; a dimension the whole set agrees on
    (one distinct value) narrows nothing and is left out entirely.

    NO CUT ON THE VALUES (TASK-146). They used to be trimmed to OFFER_LIMIT as well. Ivan's five is a
    cap on POSITIONS IN ONE MESSAGE; applying the same number to how many values a criterion may list
    was a second ceiling nobody asked for (CLAUDE.md), and it left the model able to suggest only 5 of
    the 20 departments that would actually narrow the set -- chosen by a sort order rather than by the
    candidate's need. This is machine-readable context, not message text: the model still has to pick
    what to say, and the VOLUME rule still governs what goes out."""
    out = []
    for dimension in _DIMENSIONS:
        counts = {}
        for row in rows:
            for value in _values(row, dimension):
                entry = counts.setdefault(value, {"value": value, "clinics": set(), "postings": 0})
                entry["postings"] += 1
                if clinic_name(row):
                    entry["clinics"].add(clinic_name(row))
        if len(counts) < 2:
            continue
        values = sorted(({"value": e["value"], "clinics": len(e["clinics"]), "postings": e["postings"]}
                         for e in counts.values()),
                        key=lambda e: (-e["clinics"], -e["postings"], e["value"]))
        out.append({"criterion": dimension, "values": values})
    out.sort(key=lambda c: (-len(c["values"]), _DIMENSIONS.index(c["criterion"])))
    return out


def position(row):
    """One position as the candidate may hear it: clinic, city, department and the board facts that
    matter. Board fields only, and no link -- see the module docstring."""
    return {"posting_id": row.get("posting_id"), "clinic": clinic_name(row), "city": city_of(row),
            "department": row.get("department_hint"), "title": row.get("title"),
            "regierungsbezirk": row.get("regierungsbezirk"),
            "employment_types": list(row.get("employment_types") or []),
            "housing": D.offers_housing(row)}


def build_offer(rows):
    """-> {positions, shown, clinics_total, postings_total, remaining_clinics, remaining_postings,
    narrow_by, branches} for a matching result set of any size.

    ``positions`` is one entry per distinct clinic, in the order the rows came (newest posting first,
    app/data.py:filter_jobs), cut to OFFER_LIMIT. One per clinic rather than one per posting because
    that is what the candidate is choosing between -- three wards in the same house read as one
    option to them, and naming the house three times spends the cap on nothing.

    ``remaining_clinics``/``remaining_postings`` are what the cap left out, so the message can say how
    many more matched instead of implying the five are all there is. Both are counts of the SAME set
    the positions came from, so "und 95 weitere Kliniken" is arithmetic, not an estimate."""
    positions, seen = [], set()
    for row in rows:
        name = clinic_name(row)
        if not name or name in seen:
            continue
        seen.add(name)
        if len(positions) < OFFER_LIMIT:
            positions.append(position(row))
    clinics_total, postings_total = len(clinic_names(rows)), len(rows)
    return {"positions": positions, "shown": len(positions),
            "clinics_total": clinics_total, "postings_total": postings_total,
            "remaining_clinics": clinics_total - len(positions),
            "remaining_postings": postings_total - len(positions),
            "narrow_by": narrowing_criteria(rows) if clinics_total > len(positions) else [],
            "branches": [dict(b) for b in BRANCHES]}
