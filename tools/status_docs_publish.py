#!/usr/bin/env python3
"""Publish a candidate status document under a public, unguessable token link (Ivan, 2026-10-06).

    python tools/status_docs_publish.py <slug> <src_dir>
    python tools/status_docs_publish.py --list

WHY A LOCAL TOOL, NOT AN UPLOAD API. The documents (an HTML one-pager, a detail page, any number of
PDFs -- candidate-anonymous: clinic names, vacancy titles, travel minutes, housing quotes, never a
candidate name or phone) are produced on THIS host, by the email lane (Daria), which runs as the
SAME user this tool runs as. Publishing is therefore a local filesystem operation, not a network
call -- this script makes no network call at all. See app/wa/status_docs.py's own module docstring
for the route side of this design (the board's public /s/{token}/ proxies to it).

WHAT IT DOES. `<slug> <src_dir>` copies src_dir's own files into
`status_docs_home()/www/<token>/`, where `<token>` is this slug's token: reused from
`status_docs_home()/tokens.tsv` if the slug has published before, else freshly minted
(`secrets.token_urlsafe(16)`, 22 characters -- app/wa/status_docs.py:TOKEN_RE). src_dir must
contain `index.html`; it may also contain `detail.html` and any number of PDFs, every name checked
against app/wa/status_docs.py's own `allowed_name` -- the SAME function the route enforces, never a
second copy of that allow-list. Prints exactly one line on success:

    URL: <public_base><token>/

`--list` prints `slug<TAB>URL` for every row in the map, nothing else.

TOKEN NEVER IN GIT (this repo is public, TASK-162). tokens.tsv and every published file live under
`status_docs_home()` (default `~/.local/state/pflege-status`, env `WA_STATUS_DOCS_HOME`) --
entirely outside this repository. Nothing this tool writes is ever a path git tracks.

ANY OTHER ENTRY IN src_dir IS A REFUSAL, LOUD, NOTHING PUBLISHED (CLAUDE.md "no safety nets": the
allow-list above IS the access design, not a guard layered on top of it). A subdirectory, a
dotfile, a symlink, or a file whose name `allowed_name` rejects each stop the whole publish and are
named in the error -- never silently skipped, never partially published.

ATOMIC REPUBLISH, WITH A SUB-MILLISECOND GAP -- NEVER HALF-WRITTEN, BUT BRIEFLY ABSENT. A second
publish of the same slug swaps in new content: it is copied into a fresh sibling directory under
www/ whose name starts with "." (so it can never match TOKEN_RE itself -- app/wa/status_docs.py's
route can never serve it mid-copy), then, if a live www/<token> already exists, it is renamed aside
to its own dot-name and the new directory is renamed into the live one's place. Each of those two
renames is atomic on its own (os.rename, same filesystem -- this tool never splits
status_docs_home() across one), but the two together are not: between them there is a real window,
sub-millisecond on this host but not zero, with no www/<token> at all. A request that lands in that
window gets this module's own 404, never a half-written file and never the wrong content -- the
property this design actually guarantees is "complete or absent", not "never absent". A crash
inside that window (the process killed between the two renames) leaves exactly that: no live
www/<token> for this slug until the NEXT publish of the same slug runs. That next publish is what
resolves it -- see the two cleanup lines in publish() below, which clear any leftover
www/.new-<token> or www/.old-<token> from a previous, possibly-crashed run before building and
swapping in their own, so a crash's leftover dot-dir does not accumulate.

NO SYMLINK FOLLOWING FROM src_dir. A symlink in src_dir is refused outright (see above) before
anything is copied -- every actual copy is of a file src_dir's own entry already proved is a
regular file, matching the allow-list.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import secrets
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wa import config as C                                            # noqa: E402
from app.wa import status_docs as SD                                     # noqa: E402

#: YYYY-MM-DD-<slug words>, lowercase, hyphen-separated -- Daria's own per-candidate naming.
SLUG_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+$")


def _token_for_slug(home, slug):
    """-> the token for this slug: the existing one from home/tokens.tsv (tab-separated
    slug\\ttoken, one row per line) when the slug has published before, else a freshly minted
    secrets.token_urlsafe(16) (22 chars, app/wa/status_docs.py:TOKEN_RE) appended as a new row.
    tokens.tsv is created mode 600 if it did not exist yet (module docstring: never in git, and
    never world-readable either -- it is the one file that maps a public URL back to a slug).

    A reused row's own token is validated against the route's own TOKEN_RE before it is handed
    back: a hand-edited or otherwise corrupted row (truncated, extra whitespace caught in the
    column, a token from some other minting scheme) raises loudly, naming the slug and the exact
    line, rather than silently minting a fresh token to paper over it -- CLAUDE.md "no safety
    nets". A silent re-mint here would be worse than raising: the old URL would quietly stop
    working with no record of why, and nothing downstream would ever notice."""
    tokens_path = home / "tokens.tsv"
    if tokens_path.exists():
        for line in tokens_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row_slug, _, row_token = line.partition("\t")
            if row_slug == slug:
                if not SD.TOKEN_RE.fullmatch(row_token):
                    raise ValueError(
                        f"tokens.tsv has a malformed token for slug {slug!r}: {line!r} does not "
                        f"match {SD.TOKEN_RE.pattern} -- fix or remove that line by hand, it is "
                        f"never re-minted automatically")
                return row_token
    token = secrets.token_urlsafe(16)
    with open(tokens_path, "a", encoding="utf-8") as f:
        f.write(f"{slug}\t{token}\n")
    tokens_path.chmod(0o600)
    return token


def _check_src(src):
    """-> (problems, entries). problems is a list of problem strings, each naming one offending
    entry (module docstring: a subdirectory, a dotfile, a symlink, or a name allowed_name rejects),
    plus a final entry if index.html is missing -- empty list means src is clean and publishable
    as-is. entries is the exact, sorted list of directory entries this call already walked to reach
    that verdict; publish() copies exactly these rather than calling src.iterdir() a second time --
    one walk, one set of entries, never a second listing that could in principle see a different
    src_dir than the one just checked."""
    problems = []
    names = set()
    entries = sorted(src.iterdir(), key=lambda p: p.name)
    for entry in entries:
        name = entry.name
        names.add(name)
        if entry.is_symlink():
            problems.append(f"{name}: a symlink is never published")
        elif name.startswith("."):
            problems.append(f"{name}: a dotfile is never published")
        elif entry.is_dir():
            problems.append(f"{name}: a subdirectory is never published")
        elif not SD.allowed_name(name):
            problems.append(f"{name}: not index.html, detail.html, or a valid .pdf name")
    if "index.html" not in names:
        problems.append("index.html is required and missing")
    return problems, entries


def publish(slug, src_dir):
    """-> the public URL string on success. Raises ValueError, naming every problem at once, on a
    bad slug or a bad src_dir -- nothing is published when this raises (module docstring)."""
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"slug {slug!r} does not match {SLUG_RE.pattern}")
    src = pathlib.Path(src_dir)
    if not src.is_dir():
        raise ValueError(f"src_dir {src_dir!r} is not a directory")
    problems, entries = _check_src(src)
    if problems:
        raise ValueError("; ".join(problems))

    home = C.status_docs_home()
    www = home / "www"
    for d in (home, www):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    token = _token_for_slug(home, slug)

    # Atomic swap (module docstring): build the new content in a dot-prefixed sibling (never a
    # valid token itself -- TOKEN_RE has no "."), then rename it into place.
    tmp_new = www / f".new-{token}"
    if tmp_new.exists():
        shutil.rmtree(tmp_new)
    tmp_new.mkdir(mode=0o700)
    for entry in entries:   # the exact entries _check_src already validated, never a re-listing
        shutil.copy2(entry, tmp_new / entry.name)

    live = www / token
    tmp_old = www / f".old-{token}"
    if tmp_old.exists():
        shutil.rmtree(tmp_old)
    if live.exists():
        live.rename(tmp_old)
    tmp_new.rename(live)
    if tmp_old.exists():
        shutil.rmtree(tmp_old)

    return f"{C.status_docs_public_base()}{token}/"


def list_published():
    """-> ["slug<TAB>URL", ...] for every row in tokens.tsv, in file order."""
    home = C.status_docs_home()
    tokens_path = home / "tokens.tsv"
    if not tokens_path.exists():
        return []
    base = C.status_docs_public_base()
    rows = []
    for line in tokens_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        slug, _, token = line.partition("\t")
        rows.append(f"{slug}\t{base}{token}/")
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("slug", nargs="?")
    p.add_argument("src_dir", nargs="?")
    p.add_argument("--list", action="store_true", help="print slug<TAB>URL for every published slug")
    args = p.parse_args(argv)

    if args.list:
        for row in list_published():
            print(row)
        return 0

    if not args.slug or not args.src_dir:
        p.error("slug and src_dir are required unless --list is given")

    try:
        url = publish(args.slug, args.src_dir)
    except ValueError as exc:
        print(f"status_docs_publish: {exc}", file=sys.stderr)
        return 1
    print(f"URL: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
