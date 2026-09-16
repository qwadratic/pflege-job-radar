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
    return (row.get("city") or row.get("clinic_town") or "").strip()


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
    return {
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
        "regierungsbezirk": f"regierungsbezirk: {_values(v['regierungsbezirke'])}.",
        "role_class": f"role_class: {_values(v['role_classes'])}.",
        "employment_type": f"employment_type: {_values(v['employment_types'])}.",
        "housing": f"housing = the board's own mark on the ad (enr_housing: a flat/Unterkunft comes with the job), "
                   f"{v['housing_postings']} of {v['postings']} postings ({share}%) at {v['housing_clinics']} "
                   f"clinics in {v['housing_cities']} cities. A posting without the mark is not a flat.",
        "api_columns": "filters only this tool reaches, and how many of the "
                       f"{v['postings']} postings carry any value for them: "
                       + ", ".join(_api_column_line(col, pairs, v["postings"]) for col, pairs in v["api_columns"])
                       + ". A filter on a column the board barely fills answers 0 rows because the column is "
                         "empty, not because nothing like it is open -- never turn such a 0 into 'we have none' "
                         "for the candidate; say the board does not record it and the clinic confirms it.",
    }
