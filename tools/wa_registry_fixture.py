#!/usr/bin/env python3
"""Generates tests/fixtures/wa_registry/registry_snapshot.json from the REAL Supabase clinic
registry -- the three live PostgREST queries app/data.py's own loader makes (v_postings, clinics,
postings' housing-evidence columns), read through app.config.rest_get_all with real credentials --
served through tests/conftest.py's opt-in `registry_snapshot` fixture to any WA-lane test that
explicitly asks for real-shaped registry data (same spirit as tools/wa_pro_fixtures.py). A test that
reaches app/data.py._build() without stubbing app.data._snap/_refresh or requesting
`registry_snapshot` itself now hits conftest.py's loud tripwire instead of this fixture -- see that
fixture's own docstring for why silently serving it was replaced.

WHY THIS EXISTS (2026-10-05, plans/2026-10-05-merge-gate-fixtures.md, Ivan: "как чиним супабейс
тесты? полагаю надо фикстурами актуальными заменить"). 28 WA-lane offline tests
(tests/test_wa_luna_brain.py, tests/test_wa_harness.py) failed with HTTPException 503 "snapshot
unavailable: RuntimeError: PostgREST 401" -- app/data.py._build() reaching the LIVE Supabase
PostgREST endpoint because the test process's SUPABASE_ANON_KEY was empty (tests/conftest.py says
why that variable is deliberately not scrubbed). The actual bug behind every one of those 28 turned
out to be simpler than "needs a live-data fixture": each test had forgotten the `luna`/`wa` fixture
every sibling test in the same file already requests to stub app.data._snap -- adding the missing
fixture parameter fixed all 28 with no fixture file involved (see the report for exactly which
tests). This generator is the separate, deliberate ask from the same conversation: a real, minimal,
regenerable snapshot of the live registry so that a test that explicitly opts in (or the tripwire
names as the way out) gets real-shaped data instead of having to hand-build a fake one.

TRIM POLICY (deterministic, minimal, geographically diverse). Bavaria has 7 Regierungsbezirke; for
each one (sorted alphabetically), the single open clinic with the most open postings in it (ties
broken by clinic_id, so the choice never depends on API ordering) is kept whole, and up to
POSTINGS_PER_CLINIC of its open v_postings rows (sorted by posting_id) are kept. 7 clinics, at most
70 postings: small enough to read as a diff, spread across every Bavarian region and several
departments/cities, real enough to exercise filter_jobs/market_snapshot's own logic if some future
test ever falls through to this fixture instead of its own stub.

PII (the fork this ships in is public). clinics carries no contact-ish column at all (sql/001_schema
.sql's own columns -- name/town/operator/beds/.../ats_type -- are public register data; confirmed
against the live schema here, 2026-10-05), and clinics rows are left untouched below. postings is a
different story:
  - enr_contact_emails is almost always a scraped individual's own work address (app/data.py's own
    MEMBER_ONLY_JOB_FIELDS comment; confirmed live against the kept clinics' rows, 2026-10-05) --
    dropped outright below, not merely masked, since the field is never anything else. Every
    remaining string field is also run through app.data._scrub_emails, the app's own redaction, for
    the same reason that function exists: an address regularly rides in free text instead of the
    dedicated column.
  - Beyond e-mails, the ad text itself regularly names a real contact person together with their
    direct phone line (2026-10-05 Opus review, B1: three named individuals with a direct number, one
    a mobile, found in enr_requirements -- NOT caught by anything above, since none of it is an
    e-mail). Every string field of every v_postings/postings_housing_evidence row -- not only
    enr_requirements -- is therefore scrubbed twice more, after the e-mail pass, by _scrub_pii:
      1. PHONE_RE finds every German-formatted phone/fax substring (+49 (0)xxx, 0xxx/..., spaces,
         slashes, dashes, an attached extension) and _scrub_phones replaces each DISTINCT one with a
         deterministic, obviously-invalid synthetic number ("+49 (0)00 0000 0NN" -- "0000" cannot be
         a real German subscriber number). The same original phone always gets the same NN.
      2. A Haiku call (claude-haiku-4-5 -- the same `claude` CLI mechanism app/wa/luna/refusal.py
         already uses for its own narrow classifiers; app/wa/config.py's REFUSAL_MODEL names that
         tier) finds every person-name substring, honorifics/titles included, across one batched
         request covering every posting, and _scrub_names replaces each one with an entry from a
         small fixed Mustermann-style pool (SYNTHETIC_NAME_POOL), picked by a stable hash of the
         original substring -- the same person always gets the same fake name.
      3. _verify_scrubbed then re-checks the RESULT with a second, independent Haiku call plus a
         fresh PHONE_RE scan: any person name found that is not from the synthetic pool, or any
         phone-shaped substring that is not the synthetic placeholder, is a hard failure that names
         the posting and stops the run -- generate() never hands back partially-scrubbed data.
This generator's own live fetch (fetch_live(), below) does the e-mail pass only -- a fact the schema
drift test (tests/test_wa_registry_fixture_generated.py) relies on to run with no Haiku call at all.
generate() is the one that also runs _scrub_pii, and is what --write commits. --check compares schema
only (fetch_live() + diff_schema), so it runs no Haiku call either.

Usage (--env-file reads ONLY SUPABASE_URL/SUPABASE_ANON_KEY out of a .env file, in this process, and
sets them in os.environ before app.config (or anything that imports it) is ever imported -- it never
prints a value, and every other key in that file is read and discarded):
    python -m tools.wa_registry_fixture --write --env-file /home/claude/repo/pflege-board/.env
                                                     regenerate the committed fixture in place
    python -m tools.wa_registry_fixture --env-file /home/claude/repo/pflege-board/.env
                                                     print the row counts, write nothing
    python -m tools.wa_registry_fixture --check --env-file /home/claude/repo/pflege-board/.env
                                                     exit 1 if the live schema drifted from the fixture
Needs `claude` on PATH too (WA_LUNA_CLAUDE_BIN overrides it): --write and the plain run call
generate(), which runs the Haiku name classifier over every posting's free text as part of the PII
scrub above. If the live fetch or a Haiku call fails for any reason, this exits non-zero and says
why -- never a partial or unscrubbed fixture (Ivan, 2026-10-05: "stop and report, no fallback").

fetch_live() is also what tests/test_wa_registry_fixture_generated.py (the network-marked schema
drift guard) calls directly: it compares the SET OF FIELDS (and their JSON value types) of a fresh
live read against the committed fixture, per table -- never byte equality and never the Haiku-scrubbed
text -- so it goes red only when the registry's own shape changes, never after a plain crawl (new
rows, same columns) or after a regeneration's new scrub choices for the SAME underlying row.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "wa_registry"
FIXTURE_FILE = "registry_snapshot.json"
POSTINGS_PER_CLINIC = 10

# The Haiku tier, same model id app/wa/config.py's REFUSAL_MODEL defaults to -- a narrow, isolated
# classification (find the names in this text), not a conversation, so the cheap tier, never Sonnet.
HAIKU_MODEL = "claude-haiku-4-5"
# Same override convention as app/wa/config.py's LUNA_CLAUDE_BIN, kept local to this generator
# rather than importing app.wa.config: that module freezes a dozen unrelated WA-bot env vars into
# constants at import time, none of which this tool has any business triggering.
_CLAUDE_BIN = os.environ.get("WA_LUNA_CLAUDE_BIN", "claude").strip() or "claude"
# Generous on purpose, since Ivan's ask is "stop and report" on failure, never "time out and fall
# back to unscrubbed data" -- not the narrow one-shot calls refusal.py times at 90s.
_HAIKU_TIMEOUT_SEC = 180
# Rows per Haiku call, not fields: one item per STRING FIELD (3205 of them, this fixture's live data,
# 2026-10-05) pushed a single call's own JSON/id overhead past this host's 200k-token context ceiling
# (api_error_status 400, "prompt_too_long") well before the actual free text did. One item per ROW
# instead (its string fields concatenated into one blob, see _row_items) cuts that to ~90 non-empty
# items; chunking those into batches of this size keeps each call comfortably inside the ceiling
# regardless of a host's own baseline system-prompt overhead, while still being "batched ... to keep
# calls few" (Ivan) rather than one call per posting.
_NAMES_BATCH_SIZE = 20


def _anchor_clinics(open_rows):
    """{regierungsbezirk: clinic_id} -- the single clinic with the most open postings in each
    region, ties broken by clinic_id so the pick is stable run to run regardless of API ordering."""
    counts = {}
    for r in open_rows:
        bezirk, cid = r.get("regierungsbezirk"), r.get("clinic_id")
        if bezirk and cid:
            counts.setdefault(bezirk, {}).setdefault(cid, 0)
            counts[bezirk][cid] += 1
    return {bezirk: sorted(per_clinic.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            for bezirk, per_clinic in sorted(counts.items())}


def _scrub_posting(row):
    """A copy of a v_postings row with enr_contact_emails dropped outright (see the module
    docstring -- it is never anything but a scraped person's address) and every remaining string
    run through the app's own e-mail redaction, in case an address rides in free text instead."""
    from app import data as D

    row = dict(row)
    row["enr_contact_emails"] = []
    return D._scrub_emails(row)


def fetch_live():
    """-> (clinics, postings, housing_evidence), trimmed per the module docstring, read through
    app.config.rest_get_all exactly as app/data.py._build()/_housing_evidence() read them. Real
    credentials must already be in the environment (see Usage above) -- this never prints one. Does
    the e-mail PII pass only (_scrub_posting/D._scrub_emails) -- no Haiku call -- so the schema drift
    test can call this directly without ever running a model."""
    from app import config as A
    from app import data as D

    open_rows = A.rest_get_all("v_postings", {"select": D.JOB_COLS, "status": "eq.open", "order": "posting_id"})
    anchors = _anchor_clinics(open_rows)
    clinic_ids = sorted(set(anchors.values()))

    postings = []
    for cid in clinic_ids:
        rows = sorted((r for r in open_rows if r.get("clinic_id") == cid), key=lambda r: r["posting_id"])
        postings.extend(rows[:POSTINGS_PER_CLINIC])
    postings = [_scrub_posting(r) for r in sorted(postings, key=lambda r: r["posting_id"])]

    all_clinics = A.rest_get_all("clinics", {"select": "*", "order": "clinic_id"})
    by_id = {c["clinic_id"]: c for c in all_clinics}
    clinics = [by_id[cid] for cid in clinic_ids if cid in by_id]

    posting_ids = ",".join(str(r["posting_id"]) for r in postings)
    evidence_rows = []
    if posting_ids:
        evidence_rows = A.rest_get_all("postings", {"select": "posting_id,enr_housing_evidence",
                                                     "status": "eq.open", "posting_id": f"in.({posting_ids})",
                                                     "order": "posting_id"})
    evidence_rows = [D._scrub_emails(r) for r in evidence_rows]
    return clinics, postings, evidence_rows


# --- B1: names + phone numbers in free text (clinics are public register data and are not scanned) -

PHONE_RE = re.compile(
    # Starts with +49 / 0049, a bracketed area code "(0xxx)", or a trunk 0 followed by a digit; then any
    # run of digits, spaces, brackets, slashes, dots and dashes ending in a digit. Covers "+49 1xx ...",
    # "+49-xxxx-...", "+49 (xxxx) ...", "+49 (0)xxxx ...", "(0xxx) ...", "0xxx.xxx.xxx", "0xxxx/xx-0"
    # (tests/test_wa_registry_snapshot.py has one all-zero case per format).
    # (?<![\w.:]): the match may not START glued to a digit, letter, dot or colon (the seconds of an
    # ISO timestamp, "10:22:02.652769", are not a number to dial) -- without it the old
    # pattern matched "026-09-05" inside the ISO date "2026-09-05" (found live by
    # tests/test_wa_registry_snapshot.py). Dates that DO start a run ("01.10.2026", "04/2026") are
    # dropped by _is_phone below, not by the pattern.
    r"(?<![\w.:])(?:\+49|0049|\(0\d{1,5}\)|0\d)[\d ()/.\-]{4,}\d"
    r"(?:[ \-]?(?:Durchwahl|ext\.?)[ \-]?\d+)?",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\d{1,2}([./-])\d{1,2}\1\d{2,4}|\d{1,2}[./]\d{4}")


def _is_phone(candidate):
    """A PHONE_RE match is a phone number unless it is a date or too short to dial (< 6 digits)."""
    return len(re.sub(r"\D", "", candidate)) >= 6 and not _DATE_RE.fullmatch(candidate.strip())


def _phone_matches(text):
    return [m for m in PHONE_RE.finditer(text) if _is_phone(m.group(0))]


_SYNTHETIC_PHONE_RE = re.compile(r"^\+49 \(0\)00 0000 0\d+$")

# Every entry carries "Muster..." -- the standard German John-Doe surname -- on purpose: it is what
# lets _verify_scrubbed tell a synthetic name from a real one on sight, without needing to remember
# the pool's exact contents.
SYNTHETIC_NAME_POOL = (
    "Herr Max Mustermann", "Frau Erika Mustermann", "Herr Jan Mustermann", "Frau Lena Musterfrau",
    "Herr Dr. Thomas Mustermann", "Frau Dr. Sabine Musterfrau", "Herr Prof. Peter Mustermann",
    "Frau Anna Musterfrau",
)

NAME_SYSTEM_PROMPT = (
    "You receive one JSON object on stdin: {\"items\": [{\"id\": str, \"text\": str}, ...]}. For EACH "
    "item, find every exact substring in its `text` that names one specific human being -- a first "
    "and/or last name, together with any honorific or academic title directly attached to it in the "
    "text (Herr, Frau, Dr., Prof., Priv.-Doz., ...). Do NOT report a clinic, employer, department, "
    "ward, place name, job title/role label on its own, or a bare honorific with no name attached.\n"
    "Reply with ONLY this JSON object and nothing else: {\"<id>\": [\"<exact substring, copied "
    "character-for-character from that item's own text>\", ...], ...} -- one key per item id that has "
    "at least one person name in it; an item with none is simply omitted from the object."
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.S)


def _extract_json_object(text):
    """Same tolerance app/wa/luna/refusal.py._extract_verdict_json applies to the main brain's
    replies: a model may wrap its JSON object in a markdown fence or stray prose despite the system
    prompt saying not to. Raises ValueError if none of that yields a JSON object."""
    fenced = _FENCE_RE.match(text.strip())
    stripped = fenced.group(1).strip() if fenced else text.strip()
    for candidate in (text, stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(stripped[start:end + 1])
    raise ValueError(f"no JSON object found in {text[:300]!r}")


def _run_claude_once(payload_text, *, system_prompt, what):
    """One stateless `claude -p` call -- the same contract app/wa/luna/refusal.py._run_cli uses
    (stdin JSON, --restricted --tools "" so the classifier reaches nothing, --output-format json) --
    except this one is never allowed to resolve a failure to a safe default the way refusal.py's
    callers do: B1 is a PII scrub, and a classifier that silently answered "no names found" on a
    timeout or a bad binary would be the one bug this generator must not have. Raises RuntimeError on
    anything that is not a clean answer; the caller lets it propagate (Ivan: "stop and report")."""
    try:
        proc = subprocess.run(
            [_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
             "--model", HAIKU_MODEL, "--effort", "low", "--system-prompt", system_prompt],
            input=payload_text, capture_output=True, text=True, timeout=_HAIKU_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        raise RuntimeError(f"{_CLAUDE_BIN!r} is not on PATH -- cannot run the {what}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what} did not answer within {_HAIKU_TIMEOUT_SEC}s")
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p ({what}) exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude -p ({what}) did not return JSON on stdout: {exc}: {proc.stdout[:300]!r}")
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p ({what}) reported an error: {envelope.get('result')!r}")
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError(f"claude -p ({what}) returned no result text: {envelope!r}")
    return result


def _haiku_find_names(items):
    """items: [{"id": str, "text": str}, ...] -> {id: [name substring, ...]} for every item the
    classifier found at least one person's name in. Chunks `items` into batches of _NAMES_BATCH_SIZE
    and merges the results -- see that constant's comment for why one call for everything does not
    fit; a handful of calls is still "batched to keep calls few" (Ivan), not one call per posting."""
    if not items:
        return {}
    found = {}
    for start in range(0, len(items), _NAMES_BATCH_SIZE):
        chunk = items[start:start + _NAMES_BATCH_SIZE]
        payload = json.dumps({"items": chunk}, ensure_ascii=False)
        result = _run_claude_once(payload, system_prompt=NAME_SYSTEM_PROMPT,
                                  what=f"name classifier (batch {start // _NAMES_BATCH_SIZE + 1})")
        try:
            chunk_found = _extract_json_object(result)
        except ValueError as exc:
            raise RuntimeError(f"name classifier did not return a JSON object: {exc}")
        if not isinstance(chunk_found, dict):
            raise RuntimeError(f"name classifier returned a JSON {type(chunk_found).__name__}, not "
                               f"an object: {result[:300]!r}")
        found.update(chunk_found)
    return found


def _string_leaves(container):
    """Yields (container, key) for every string value reachable from `container` (a dict or list),
    recursing into nested dicts/lists -- e.g. a posting's `provenance` dict. Lets a caller read and
    overwrite each string leaf in place without rebuilding the row around it."""
    if isinstance(container, dict):
        items = container.items()
    elif isinstance(container, list):
        items = enumerate(container)
    else:
        return
    for key, value in items:
        if isinstance(value, str):
            yield container, key
        elif isinstance(value, (dict, list)):
            yield from _string_leaves(value)


def _collect_scrub_targets(postings, evidence_rows):
    """[(container, key, ref), ...] for every non-empty string value anywhere inside a v_postings or
    postings_housing_evidence row -- what the phone regex, the name classifier, and the verification
    pass below all run over. `ref` only ever names WHICH POSTING a problem is in; it never carries
    any of the row's own text."""
    targets = []
    for row in postings:
        ref = f"v_postings:{row['posting_id']}"
        for container, key in _string_leaves(row):
            if container[key]:
                targets.append((container, key, ref))
    for row in evidence_rows:
        ref = f"postings_housing_evidence:{row['posting_id']}"
        for container, key in _string_leaves(row):
            if container[key]:
                targets.append((container, key, ref))
    return targets


def _synthetic_phone(index):
    return f"+49 (0)00 0000 0{index:02d}"


def _scrub_phones(targets):
    """Replaces every PHONE_RE match across `targets` IN PLACE with a deterministic, obviously-
    invalid synthetic number. NN is the match's rank among every DISTINCT phone string found, in
    sorted order (so the mapping does not depend on scan order) -- the same original number always
    gets the same synthetic one, including when it repeats across postings. -> count of distinct
    originals replaced."""
    originals = sorted({m.group(0) for container, key, ref in targets for m in _phone_matches(container[key])})
    synthetic = {original: _synthetic_phone(i + 1) for i, original in enumerate(originals)}
    for container, key, ref in targets:
        if _phone_matches(container[key]):
            container[key] = PHONE_RE.sub(
                lambda m: synthetic[m.group(0)] if _is_phone(m.group(0)) else m.group(0), container[key])
    return len(originals)


def _synthetic_name(original):
    """The same fake name every time for the same original substring: a stable hash of the TEXT
    itself, not an index, so two independent runs that both see this person's name pick the same
    pool entry even when nothing else about scan order matches."""
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    return SYNTHETIC_NAME_POOL[int(digest, 16) % len(SYNTHETIC_NAME_POOL)]


_HONORIFIC_WORDS = {"herr", "frau", "dr", "prof", "priv", "doz", "priv-doz"}
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


def _name_tokens(name):
    """Word tokens of a detected (or synthetic) name, honorifics and very short tokens dropped --
    what _scrub_names also replaces as STANDALONE words elsewhere in the same row, on top of the
    exact-substring replace above. Found live, 2026-10-05: the classifier returned the full substring
    "Frau Erika Mustermann" for one posting and that got replaced correctly, but the SAME ad
    text separately truncated its "...per E-Mail an Erika" mention to the bare first name a few
    words later -- a different exact substring, so the first replace never touched it, and neither
    Haiku pass (scrub or the independent verification) flagged a lone first name with no honorific or
    surname attached as "naming one specific human being". A name's own word, once that name is
    confirmed real, is as much a fact as the name itself -- so finding every other bare occurrence of
    it in the SAME row is code's job, not a second guess for a model."""
    return [w for w in _WORD_RE.findall(name) if w.lower() not in _HONORIFIC_WORDS and len(w) >= 3]


def _group_by_ref(targets):
    """targets: [(container, key, ref), ...] -> {ref: [(container, key), ...]}, in first-seen order
    -- one group per POSTING/evidence row, the unit _row_items below batches at."""
    groups = {}
    for container, key, ref in targets:
        groups.setdefault(ref, []).append((container, key))
    return groups


def _row_items(groups):
    """groups: {ref: [(container, key), ...]} -> [{"id": ref, "text": blob}, ...], one item per ROW
    (every one of its string fields, labelled by field/key name, joined into a single blob) rather
    than one item per individual field -- see _NAMES_BATCH_SIZE's comment for why. A ref whose fields
    are all currently empty contributes no item (nothing to classify)."""
    items = []
    for ref, leaves in groups.items():
        blob = "\n".join(f"{key}: {container[key]}" for container, key in leaves if container[key])
        if blob:
            items.append({"id": ref, "text": blob})
    return items


def _scrub_names(targets):
    """Runs the Haiku name classifier once per batch (via _haiku_find_names) over every ROW's
    CURRENT text (after _scrub_phones, so it never has to reason about phone digits) and replaces
    each returned substring with its synthetic stand-in in EVERY field of that row that contains it,
    IN PLACE -- then also replaces any standalone occurrence of that name's own WORDS elsewhere in
    the same row (see _name_tokens: a confirmed name's own word is a fact, not a second model guess).
    -> count of distinct original substrings replaced."""
    groups = _group_by_ref(targets)
    found = _haiku_find_names(_row_items(groups))
    replaced = set()
    for ref, names in found.items():
        if ref not in groups:
            raise RuntimeError(f"name classifier returned an id {ref!r} that was not in the batch sent to it")
        if not isinstance(names, list):
            raise RuntimeError(f"name classifier returned a non-list value for {ref}: {names!r}")
        for name in names:
            if not isinstance(name, str) or not name:
                raise RuntimeError(f"name classifier returned an invalid name {name!r} for {ref}")
            synthetic = _synthetic_name(name)
            hit = False
            for container, key in groups[ref]:
                if name in container[key]:
                    container[key] = container[key].replace(name, synthetic)
                    hit = True
            if not hit:
                raise RuntimeError(f"name classifier returned {name!r} for {ref}, which is not an exact "
                                   f"substring of any field in that row")
            replaced.add(name)

            orig_tokens, synth_tokens = _name_tokens(name), _name_tokens(synthetic)
            for i, token in enumerate(orig_tokens):
                repl = synth_tokens[min(i, len(synth_tokens) - 1)]
                token_re = re.compile(rf"(?<![^\W\d_]){re.escape(token)}(?![^\W\d_])")
                for container, key in groups[ref]:
                    if token_re.search(container[key]):
                        container[key] = token_re.sub(repl, container[key])
    return len(replaced)


def _verify_scrubbed(targets):
    """Independent of _scrub_names/_scrub_phones above: a FRESH Haiku call per batch over the
    already-scrubbed rows, plus a fresh PHONE_RE scan over every field. Raises the moment either one
    finds something that is not a synthetic placeholder, naming the posting -- never a silent pass
    (Ivan: "stop and report, no fallback"). Returns nothing; a clean return means the scrub held up."""
    groups = _group_by_ref(targets)
    found = _haiku_find_names(_row_items(groups))
    for ref, names in found.items():
        for name in names:
            if not isinstance(name, str) or "Muster" not in name:
                raise RuntimeError(
                    f"PII scrub verification failed: the second, independent Haiku pass still finds a "
                    f"person name in {ref} -- regenerate failed, nothing written"
                )

    for container, key, ref in targets:
        for m in _phone_matches(container[key]):
            if not _SYNTHETIC_PHONE_RE.match(m.group(0)):
                raise RuntimeError(
                    f"PII scrub verification failed: {ref} still has a phone-shaped substring that is "
                    f"not the synthetic placeholder -- regenerate failed, nothing written"
                )


def _scrub_pii(postings, evidence_rows):
    """B1 (plans/2026-10-05-opus-review-79cbb64.md): replaces every person's name and every phone
    number anywhere in postings/evidence free text with deterministic synthetic values, then
    independently re-checks the result before handing it back -- this fixture ships in the PUBLIC
    fork. E-mail scrubbing already happened in fetch_live() (_scrub_posting/D._scrub_emails); this
    only adds names and phone numbers, both of which emails did not catch. -> (postings, evidence_rows),
    scrubbed in place (the same objects passed in). Raises loudly on any failure -- see
    _run_claude_once/_verify_scrubbed; the live fetch or the Haiku calls being unavailable is a reason
    to stop, never a reason to fall back to unscrubbed or partially-scrubbed data."""
    targets = _collect_scrub_targets(postings, evidence_rows)
    n_phones = _scrub_phones(targets)
    n_names = _scrub_names(targets)
    _verify_scrubbed(targets)
    print(f"PII scrub: replaced {n_names} distinct name(s) and {n_phones} distinct phone number(s); "
          f"independent verification found no residual PII", file=sys.stderr)
    return postings, evidence_rows


def generate():
    """-> the fixture dict, exactly what --write commits:
    {"clinics": [...], "v_postings": [...], "postings_housing_evidence": [...]}. Runs the
    full PII scrub (_scrub_pii) on top of fetch_live()'s e-mail-scrubbed rows -- unlike fetch_live()
    itself, this calls Haiku twice (see the module docstring)."""
    clinics, postings, evidence_rows = fetch_live()
    postings, evidence_rows = _scrub_pii(postings, evidence_rows)
    return {"clinics": clinics, "v_postings": postings, "postings_housing_evidence": evidence_rows}


def _dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(output_dir=FIXTURES_DIR):
    text = _dumps(generate())
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / FIXTURE_FILE).write_text(text, encoding="utf-8")
    return text


# --- schema-only comparison, used by tests/test_wa_registry_fixture_generated.py (no Haiku, no bytes)

def _json_type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def row_schema(rows):
    """{field name -> sorted tuple of JSON value-type names seen for it across `rows`}. Two schemas
    compare by key SET and, for a key that is a single stable type on BOTH sides, that type -- never
    by value. That is what lets diff_schema go red only when the registry's own shape changes, never
    after a plain crawl (new rows, same columns, a field that happens to be null in one batch)."""
    schema = {}
    for row in rows:
        for k, v in row.items():
            schema.setdefault(k, set()).add(_json_type(v))
    return {k: tuple(sorted(v)) for k, v in schema.items()}


def diff_schema(live_rows, fixture_rows, table):
    """-> a list of human-readable problems (empty = schemas match); see row_schema for what "match"
    means. `table` is only used to label a problem."""
    live, fixed = row_schema(live_rows), row_schema(fixture_rows)
    problems = []
    missing = sorted(set(fixed) - set(live))
    added = sorted(set(live) - set(fixed))
    if missing:
        problems.append(f"{table}: the fixture has field(s) {missing} that a fresh live read does not")
    if added:
        problems.append(f"{table}: a fresh live read has new field(s) {added} the fixture does not")
    for k in sorted(set(live) & set(fixed)):
        lt, ft = live[k], fixed[k]
        if len(lt) == 1 and len(ft) == 1 and lt != ft:
            problems.append(f"{table}.{k}: type changed from {ft[0]!r} (fixture) to {lt[0]!r} (live)")
    return problems


def _load_env_file(path):
    """Parses PATH as simple shell-style KEY=VALUE lines (an optional leading 'export ', '#'
    comments, blank lines skipped, an optionally quoted value) and returns {key: value} for
    SUPABASE_URL/SUPABASE_ANON_KEY ONLY -- every other key in the file is read and discarded without
    ever reaching os.environ, and no value from this file is ever printed by this module."""
    wanted = ("SUPABASE_URL", "SUPABASE_ANON_KEY")
    out = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or key not in wanted:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="overwrite the committed fixture")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the live registry schema drifted from the committed fixture")
    parser.add_argument("--env-file", metavar="PATH",
                        help="read SUPABASE_URL/SUPABASE_ANON_KEY from this .env file before "
                             "fetching live data (only those two keys; nothing from the file is "
                             "ever printed) -- see the module docstring's Usage section")
    args = parser.parse_args(argv)

    if args.env_file:
        for key, value in _load_env_file(args.env_file).items():
            os.environ[key] = value

    if args.check:
        # Schema only, like tests/test_wa_registry_fixture_generated.py: a byte compare went red after
        # every crawl and spent Haiku calls on a full generate() just to say so.
        clinics, postings, evidence_rows = fetch_live()
        live = {"clinics": clinics, "v_postings": postings, "postings_housing_evidence": evidence_rows}
        committed = json.loads((FIXTURES_DIR / FIXTURE_FILE).read_text(encoding="utf-8"))
        problems = [p for table in live for p in diff_schema(live[table], committed[table], table)]
        if problems:
            print("DRIFT: " + "\n".join(problems) + "\n-- regenerate with --write", file=sys.stderr)
            return 1
        print(f"{FIXTURE_FILE} has the same schema as a fresh live read")
        return 0

    if args.write:
        text = write()
        print(f"wrote {FIXTURES_DIR / FIXTURE_FILE} ({len(text)} bytes)")
        return 0

    body = generate()
    print(f"clinics: {len(body['clinics'])}  v_postings: {len(body['v_postings'])}  "
          f"postings_housing_evidence: {len(body['postings_housing_evidence'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
