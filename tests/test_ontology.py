"""docs/ontology.json is served verbatim at GET /api/ontology, so every claim in it is an API
response. These tests keep the published claims tied to the code they describe: vocabularies to
taxonomy.json, published columns to the table and the producers that fill them, file:line
references to files that still have that line.

Four of them were written on 2026-09-10 against a *rewritten* ontology -- one with `dead`,
`dead_source`, `enum`, `populated` and a `career_profile` node. That rewrite is not in the tree and
never was (deck slide 7 retracts it): `docs/ontology.json` is the 22-node / 40-edge bilingual graph
of HEAD, the only thing that reads it is the SPA's graph view (web/pro.template.html:1246), and it
publishes exactly `nodes[].{id,label,kind,desc,fields[]}` plus `edges` and `process`. So on
2026-09-11 the tests were reconciled the other way: the file stayed as it is and the four
assertions were rewritten against it. None of the four became vacuous -- each still fails on a real
drift, named in its own docstring -- and none was deleted. Growing the published document new keys
that nothing consumes is a separate decision, not a test fix.
"""
import ast
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ONTOLOGY = json.loads((ROOT / "docs" / "ontology.json").read_text(encoding="utf-8"))
TAXONOMY = json.loads((ROOT / "data" / "registry" / "taxonomy.json").read_text(encoding="utf-8"))
NODES = {n["id"]: n for n in ONTOLOGY["nodes"]}


def _strings(o, path="$"):
    """Every string in the document, with the path it sits at (for readable failures)."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


def test_vocab_ids_resolve_against_taxonomy():
    """`vocab` is the promise "look this up in /api/taxonomy". Anything not a top-level key there
    is a broken promise -- code-only enumerations belong in a field's `enum`, not here."""
    keys = set(TAXONOMY)
    for nid, n in NODES.items():
        for v in n.get("vocab", []):
            assert v in keys, f"{nid}: vocab id {v!r} is not a key of data/registry/taxonomy.json"


def test_field_enums_that_claim_a_taxonomy_vocab_match_it():
    """Where a node claims a vocab AND a field spells the values out, the two must agree."""
    for nid, n in NODES.items():
        for name in n.get("vocab", []):
            entry = TAXONOMY[name]
            published = set(entry) if isinstance(entry, dict) else set(entry) if isinstance(entry, list) and entry and isinstance(entry[0], str) else None
            if published is None:
                continue
            for fld in n.get("fields", []):
                if fld["name"].split(" ")[0].rstrip("[]") == name and "enum" in fld:
                    assert set(fld["enum"]) <= published, f"{nid}.{name}: enum drifted from taxonomy.{name}"


def _observation_producers():
    """{file:line: columns hard-coded to a literal None} for every observation row literal under
    pflege_jobs/sources/ -- any dict that carries both `source_id` and `source_ref` keys.

    Not `return {...}` as the first version of this helper had it: bite.py:182, board_csv.py:70 and
    pi_asp.py:142 build the row into a list instead of returning it, so a return-only scan saw 3 of
    the 6 producers and called 15 columns dead that two of the other three do fill."""
    out = {}
    for path in sorted((ROOT / "pflege_jobs" / "sources").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if not {"source_id", "source_ref"} <= set(keys):
                continue
            out[f"{path.relative_to(ROOT)}:{node.lineno}"] = {
                k.value for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant) and v.value is None}
    return out


def _published_column_names(nid):
    """The column names a node's `fields` actually name. A field name is prose as well as a name --
    "source_id / source_ref" is two columns, "…" and "enr_*" are neither."""
    out = set()
    for fld in NODES[nid].get("fields", []):
        for token in re.split(r"[\s/]+", fld["name"]):
            if token.isidentifier():
                out.add(token)
    return out


def test_columns_no_producer_fills_are_not_published_as_observation_fields():
    """The rewrite would have published these as `dead: true`. The file has no such flag, so what is
    checkable is the weaker half that still matters to a caller: the graph must not advertise a
    column that nothing writes, which is the same lie with the flag left off.

    Measured, not listed here: 11 of the 57 posting_observations columns are hard-coded None in all
    six producers (aa_kundennummer_hash, details_error, fixed_term_months, hauptberuf, homeoffice,
    quereinstieg, salary_min/max/unit, shift_night_weekend, start_date), and nothing else in
    pflege_jobs/ or app/ writes them either -- there is no salary parser and no producer that fills
    details_error."""
    producers = _observation_producers()
    assert len(producers) >= 6, f"the producer scan found only {sorted(producers)}"
    never_filled = set.intersection(*producers.values())
    assert never_filled, "no column is None in every producer -- the scan stopped working"
    published = _published_column_names("posting_observations") & never_filled
    assert not published, (f"posting_observations publishes {sorted(published)} as a field, but no producer "
                           f"ever fills them: {sorted(producers)}")


def test_every_published_observation_field_is_a_real_column():
    """The other half: a name in the graph that is not a column of the table is an API answer a
    caller cannot use. The table is the union of the ingest spec (pflege_jobs/schema.py, what a sink
    writes) and the DDL (sql/001_schema.sql:84, which also has the keys the DB generates --
    observation_id, employer_id, posting_id)."""
    from pflege_jobs.schema import OBS_COLUMNS
    ddl = (ROOT / "sql" / "001_schema.sql").read_text(encoding="utf-8")
    body = ddl.split("create table if not exists pflege_jobs.posting_observations (", 1)[1].split("\n);", 1)[0]
    columns = set(OBS_COLUMNS)
    for frag in body.replace("\n", " ").split(","):
        tok = frag.split()
        if len(tok) >= 2 and tok[0].isidentifier() and tok[0] not in ("unique", "primary", "references"):
            columns.add(tok[0])
    unknown = _published_column_names("posting_observations") - columns
    assert not unknown, f"posting_observations publishes field names that are not columns: {sorted(unknown)}"


REF = re.compile(r"([\w./-]+\.(?:py|json|sql|md|ts|html|csv)):(\d+)(?:-(\d+))?")


def test_every_file_line_reference_still_exists():
    """A file:line that has drifted off the end of its file is worse than no reference at all."""
    bad = []
    for path, s in _strings(ONTOLOGY):
        for relpath, start, end in REF.findall(s):
            p = ROOT / relpath
            if not p.is_file():
                bad.append(f"{path}: {relpath} does not exist")
                continue
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            last = int(end or start)
            if not 1 <= int(start) <= last <= n:
                bad.append(f"{path}: {relpath}:{start}-{last} but the file has {n} lines")
    assert not bad, "\n".join(bad)


def test_store_paths_exist():
    """`store` names where an entity lives. Repo-relative paths in it must be real."""
    for nid, n in NODES.items():
        for s in n.get("store", []):
            for token in re.findall(r"(?:^|\s)((?:data|sql|web|app|skill|docs|pflege_jobs|crawlers)/[\w./-]+)", s):
                assert (ROOT / token).exists(), f"{nid}: store path {token} does not exist"


def test_every_edge_points_at_a_published_node():
    """The SPA silently drops edges whose endpoints are missing (web/pro.template.html), so a typo
    here is invisible in the graph."""
    for e in ONTOLOGY["edges"]:
        assert e["from"] in NODES, f"edge from unknown node {e['from']}"
        assert e["to"] in NODES, f"edge to unknown node {e['to']}"


def test_excluded_role_classes_published_value_matches_the_live_one():
    """The design brief expected a 4-vs-3 drift here between pflege_jobs/patterns.json and the
    literal in pflege_jobs/config.py:94. There is none: that literal is the fallback for a MISSING
    key, and the key is present with four classes, so the loaded set is the file's four.

    The graph names the field but does not spell the values out (there is no `enum` in this
    document), so what this pins is the intake policy itself -- the drift the brief was afraid of --
    plus the node that claims to describe it. Both halves are load-bearing: on 2026-09-11 an empty
    PUT /api/settings/patterns overwrote patterns.json with `{}`, which took the key with it and
    would have silently dropped the set back to the 3-class fallback."""
    from pflege_jobs import config as C
    patterns = json.loads((ROOT / "pflege_jobs" / "patterns.json").read_text(encoding="utf-8"))
    assert set(C.EXCLUDED_ROLE_CLASSES) == set(patterns["excluded_role_classes"]), \
        "config.EXCLUDED_ROLE_CLASSES is not what patterns.json says -- the fallback is being used"
    published = next(f for f in NODES["patterns_json"]["fields"] if f["name"] == "excluded_role_classes")
    if "enum" in published:                    # the day it does spell them out, it must spell them right
        assert set(published["enum"]) == set(patterns["excluded_role_classes"])


def test_career_profiles_is_empty_while_the_graph_leaves_it_out():
    """The rewrite would have published a `career_profile` node carrying `populated: false`. The
    graph has no such node -- and omitting a table is only honest while it holds nothing. Rows in it
    with no node for them is a hole in the published graph, which is what this catches."""
    import sqlite3
    db = ROOT / "data" / "app.sqlite"
    if not db.exists():
        pytest.skip("no local data/app.sqlite")
    if "career_profile" in NODES:                       # published: then it is the node's own flag to keep
        assert NODES["career_profile"].get("populated") is not None
        return
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        n = c.execute("select count(*) from career_profiles").fetchone()[0]
    assert n == 0, f"career_profiles holds {n} rows and no node in docs/ontology.json publishes it"


def test_new_names_and_rename_collisions_are_declared_both_ways():
    for nid, n in NODES.items():
        rc = n.get("rename_collision")
        if not rc:
            continue
        other = NODES.get(rc["with"])
        assert other, f"{nid}: rename_collision points at unknown node {rc['with']}"
        assert other.get("rename_collision", {}).get("with") == nid, \
            f"{rc['with']} does not declare the collision with {nid} back"
