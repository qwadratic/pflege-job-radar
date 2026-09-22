"""The board's own filter vocabulary, counted off the live board -- the text that goes into the MCP
tool descriptions the model reads on every turn (app/wa/luna/tools_server.py).

Its own module because it is computed on BOTH sides of the tools-server subprocess boundary
(TASK-110 review, 2026-09-16): the parent (app/wa/luna_brain.py) builds it from the snapshot it
already holds for market_snapshot and hands the lines to the spawned server as a file, so starting
the server costs milliseconds. Building it inside the server meant a cold, synchronous Supabase
build (measured on this host: 8.3s, 7.7s, 2.7s in three fresh processes, and 8.1/16.5s in the
review's) on the critical path of every turn, under the CLI's 30s MCP connect deadline
(``MCP_TIMEOUT``, default 30000ms in CLI 2.1.270) -- a deadline the parent cannot see being missed.
The server still counts its own board when it is started by hand without that file.

Nothing here imports ``mcp``: the parent must be able to build this text without pulling the MCP
server into the harness process.
"""
from ... import data as D
from .. import slots as SL

# The base every board query starts from: open postings the verifier re-fetched, newest first -- the
# same base app/wa/slots.py:filters() builds for the deterministic brain and market_snapshot, so a
# tool result and the harness's own shortlist count the same rows.
LIVE_BASE = {"verify": "live", "sort": "-first_published"}

# The GET /api/jobs filters no tool presets, i.e. the ones only board_api_get can reach. Several of
# them are columns the board fills for a minority of postings, and a filter on an unfilled column
# answers 0 -- indistinguishable from "nothing matches" unless the model is told the coverage
# (review 2026-09-16: `contract` is set on 16 of 2624 live-verified postings, all BEFRISTET, so
# `contract=UNBEFRISTET` returns total=0 and reads as "we have no permanent positions").
API_COLUMNS = ("contract", "enr_tariff", "qualification_hint", "clinic_size", "versorgungsstufe", "traegerart")
# Below this share of the rows, the values themselves go into the line: those are the traps.
API_COLUMN_VALUES_BELOW = 0.10


def city_of(row):
    """One definition of which town a posting is in, shared with the tools and the CV matcher
    (app/data.py:town_of): the posting's own city, the clinic's registry town only when it has none."""
    return D.town_of(row)


def clinic_name_of(row):
    return (row.get("clinic_name") or row.get("employer") or "").strip()


def clinic_key(row):
    """One clinic identity for every count and grouping in the tool surface: the board's clinic_id,
    else the employer's name.

    TASK-110 review: the generated housing line counted clinic_id (48 live) while the tools grouped by
    name (51), so "bei wie vielen Kliniken gibt es eine Wohnung" had two answers in one turn. skill/SKILL.md
    rule 5 ("count clinics by clinic_id, never by employer name") is about the dataset as a whole; here a
    posting whose employer the registry has not linked yet is still a real employer the tools return and
    Luna may name, so it counts as itself -- and a name that spans two clinic_ids stays two clinics.
    """
    return row.get("clinic_id") or clinic_name_of(row) or None


def _counted(rows, key):
    counts = {}
    for r in rows:
        v = r.get(key)
        for one in (v if isinstance(v, list) else [v]):
            if one:
                counts[one] = counts.get(one, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def board_vocabulary():
    """The live board's own filter vocabulary, counted over the rows the tools actually search (LIVE_BASE).

    Fails loudly on a board with no live-verified posting instead of generating an empty vocabulary: a
    board that did not load answers every tool call with "nothing found", which is exactly the silent
    wrong answer this task exists to remove. A snapshot that merely carries an ``error`` while still
    holding rows is NOT that case (app/data.py:refresh keeps serving the cached board and retries in a
    minute): those cached rows are the same ones market_snapshot is built from in this very turn.
    """
    snap = D.snapshot()
    rows = D.filter_jobs(dict(LIVE_BASE))
    if not rows:
        raise RuntimeError("board snapshot holds no live-verified open posting, refusing to serve tools with "
                           f"no vocabulary (snapshot error: {snap.get('error')})")

    # A department value the filter cannot apply (no label at all, or a label read_department_pref has no
    # word for) is not advertised as a value -- it is counted into what a department filter drops.
    departments = [(name, n) for name, n in _counted(rows, "department_hint")
                   if SL.read_department_pref(name)["status"] == "applied"]
    filterable = sum(n for _, n in departments)
    housing_rows = [r for r in rows if D.offers_housing(r)]
    housing_kinds = [D.housing_kind(r) for r in housing_rows]
    return {
        # What the housing mark actually promises, counted rather than assumed: 61 of the 422 marked
        # live rows on 2026-09-21 offer only help with the search or the move (app/data.py:housing_kind).
        "housing_accommodation": housing_kinds.count("accommodation"),
        "housing_relocation_support": housing_kinds.count("relocation_support"),
        "housing_unspecified": housing_kinds.count("unspecified"),
        "childcare_true": sum(1 for r in rows if r.get("enr_childcare")),
        "childcare_known": sum(1 for r in rows if r.get("enr_childcare") is not None),
        "postings": len(rows), "open_postings": len(D.jobs()),
        "clinics": len({clinic_key(r) for r in rows if clinic_key(r)}),
        "cities": len({city_of(r) for r in rows if city_of(r)}),
        "departments": departments, "no_department": len(rows) - filterable,
        "regierungsbezirke": _counted(rows, "regierungsbezirk"),
        "role_classes": _counted(rows, "role_class"), "employment_types": _counted(rows, "employment_types"),
        "housing_postings": len(housing_rows),
        "housing_clinics": len({clinic_key(r) for r in housing_rows if clinic_key(r)}),
        "housing_cities": len({city_of(r) for r in housing_rows if city_of(r)}),
        "api_columns": [(col, _counted(rows, col)) for col in API_COLUMNS],
    }


def _values(pairs):
    return ", ".join(f"{name} {n}" for name, n in pairs)


def _api_column_line(col, pairs, postings):
    carried = sum(n for _, n in pairs)
    if carried and carried < postings * API_COLUMN_VALUES_BELOW:
        return f"{col} {carried} ({_values(pairs)})"
    return f"{col} {carried}"


def vocabulary_lines():
    """One terse line per filter, ready to append to a tool description. Terse on purpose: every one of
    these is sent to the model on every single turn."""
    v = board_vocabulary()
    share = round(100 * v["housing_postings"] / v["postings"])
    return {
        "board": f"BOARD NOW: {v['postings']} live-verified of {v['open_postings']} open postings at "
                 f"{v['clinics']} clinics in {v['cities']} cities. Bavaria only.",
        "department": f"department: pass the candidate's own word, it is read into these board values "
                      f"({_values(v['departments'])}); several at once ('Innere oder Intensiv') filter on any of "
                      f"them, a flexible word (egal) filters nothing, a word the board has no department for is an "
                      f"error. {v['no_department']} of {v['postings']} postings carry no filterable department and "
                      f"drop out of every department filter.",
        "city": f"city: pass the candidate's own word -- it is resolved to the board's own spelling(s) of "
                f"that town over the {v['cities']} cities that carry postings, and the result says which "
                f"(town.board_spellings: Nuernberg -> Nürnberg, 'Lohr am Main' -> 'Lohr a. Main', "
                f"'Neuburg an der Donau' -> both 'Neuburg an der Donau' and 'Neuburg/Donau', a district "
                f"with no town of its own -> the towns its clinics are in). Name the town the way the board "
                f"does. A word the board has no town for is an error naming the spelling-nearest board "
                f"towns; those are NOT the place that was asked for, so never offer one as it. A word that "
                f"names several real towns (bare 'Neustadt') is an error too -- ask which one, never pick. "
                f"An empty result therefore never means an unrecognised city. A posting counts for the town "
                f"its OWN ad names, never for the town its clinic's head office is registered in.",
        "regierungsbezirk": f"regierungsbezirk: {_values(v['regierungsbezirke'])}.",
        "role_class": f"role_class: {_values(v['role_classes'])}.",
        "employment_type": f"employment_type: {_values(v['employment_types'])}.",
        "housing": f"housing = the board's own mark that the ad says SOMETHING about Wohnen (enr_housing), "
                   f"{v['housing_postings']} of {v['postings']} postings ({share}%) at {v['housing_clinics']} "
                   f"clinics in {v['housing_cities']} cities. The mark is not a flat: every row carries "
                   f"housing_kind -- {v['housing_accommodation']} accommodation (the clinic offers a "
                   f"room/flat/Wohnheim), {v['housing_relocation_support']} relocation_support (it only "
                   f"helps look for one or pays towards the move -- say that, never 'mit Wohnung'), "
                   f"{v['housing_unspecified']} unspecified (marked, wording says neither: get_posting and "
                   f"read enr_housing_evidence). No posting records rent, size or how long you may stay. A "
                   f"posting without the mark is not a flat.",
        # TASK-108 gave the board a housing mark and no way to answer the question that always follows it.
        # childcare is the same shape of datum and was exposed by no tool at all (audit 2026-09-21).
        "childcare": f"childcare on every posting row: true = the ad names a Kita/Betriebskindergarten/"
                     f"Kinderbetreuung ({v['childcare_true']} of {v['postings']} live postings), false = the "
                     f"ad was read and says nothing of the kind, null = not read ("
                     f"{v['postings'] - v['childcare_known']} postings). false and null are both 'the ad does "
                     f"not say', never 'there is no Kita' -- the clinic confirms that.",
        "api_columns": "filters only this tool reaches, and how many of the "
                       f"{v['postings']} postings carry any value for them: "
                       + ", ".join(_api_column_line(col, pairs, v["postings"]) for col, pairs in v["api_columns"])
                       + ". A filter on a column the board barely fills answers 0 rows because the column is "
                         "empty, not because nothing like it is open -- never turn such a 0 into 'we have none' "
                         "for the candidate; say the board does not record it and the clinic confirms it.",
    }
