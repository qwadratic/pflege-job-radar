"""Status documents: candidate-anonymous HTML/PDF one-pagers (public clinic names, vacancy titles,
travel minutes, housing quotes, her anonymised profile fields as the clinics see them -- never a
candidate name or phone), reachable at a public,
unguessable token link (Ivan, 2026-10-06): https://pflege-board.exe.xyz/s/<token>/.

WHY THIS PREFIX, WHY NO NGINX CHANGE. Nobody has sudo on this host, so there is no new nginx
location block to add. The only off-loopback path into this harness is the one nginx rule that
already exists -- `location ^~ /api/wa/pro/` on ki-workflow.agency -> 127.0.0.1:8502 (this process,
app/wa/asgi.py), allow-listed to the board VM's egress IP only (see app/wa/pro_api.py's own module
docstring, "topology B"). Mounting this module's router under that same "/api" prefix, right next
to pro_api.router (app/wa/asgi.py), means a status-document request rides the exact nginx rule and
the exact bearer check that already exists -- no new network surface, no new secret to provision.

WHO CALLS THIS. Not a browser directly: the board (pflege-board.exe.xyz, the pflege-fe process)
adds a public, UNAUTHENTICATED route `/s/{token}/` that proxies server-side to
`WA_API_BASE + /api/wa/pro/status/{token}/` with the board's existing `WA_API_TOKEN` bearer -- the
same topology-B proxy pattern the rest of the Pro API already uses. See docs/wa-dashboard.md's
"Status documents" section for that board-side contract. A candidate, a clinic, or anyone holding
the link never sees this host's name or this token's bearer.

WHO PRODUCES THE FILES. The email lane (Daria), running on this same host as this same user, writes
each candidate's index.html/detail.html/PDFs into a plain directory and hands it to
tools/status_docs_publish.py. Publishing is therefore a local filesystem tool, not an upload API --
see that tool's own module docstring for the publish/atomic-swap side of this design.

TOKEN NEVER IN GIT. This repo is public (TASK-162, CLAUDE.md's own "no safety nets" background).
The slug->token map (status_docs_home()/tokens.tsv) and every served file
(status_docs_home()/www/<token>/) live under WA_STATUS_DOCS_HOME -- entirely outside this
repository. Nothing this module reads, and nothing tools/status_docs_publish.py writes, is ever a
path git tracks.

AUTH FIRST, ALWAYS. Every route below calls `pro_api._authorize(request, pro_api.SCOPE_BOARD)`
before it ever looks at the token or the filesystem -- the identical board-or-Daria-write-token
check every other board-scope Pro API route already makes (app/wa/pro_api.py's own AUTH section,
reused here, not reimplemented): an unconfigured token env is still that function's own 503, a
missing or wrong bearer is still its own 401.

TOKEN_RE AND THE NAME ALLOW-LIST ARE THE WHOLE ACCESS DESIGN (CLAUDE.md "no safety nets" -- no
guard beyond what is specified here). TOKEN_RE (22 URL-safe base64 characters --
secrets.token_urlsafe(16)'s own output length, app/wa/status_docs.py:TOKEN_RE) and `allowed_name`
(exactly "index.html"/"detail.html", or a lowercase, bounded .pdf name) are shared verbatim with
tools/status_docs_publish.py, which must refuse anything this would refuse before it ever copies a
file into place -- one definition, never two that could drift apart. Anything that does not match
either one is 404, with the identical body every other failure in this module returns (_NOT_FOUND):
a wrong token and a wrong file name must look the same from outside, so a prober cannot tell which
half it got wrong. A symlinked file, or a symlinked www/<token> directory itself, is also 404
(lstat/is_symlink, never followed) -- the one check beyond the two regexes, because
tools/status_docs_publish.py's own atomic swap never produces a symlink there, so one appearing at
all means something else touched this directory.

GET .../{token} AND GET .../{token}/ ARE BOTH DECLARED, ON PURPOSE (same idiom as app/main.py's
/pro, /pro/ and /deck, /deck/). FastAPI's default behaviour for an undeclared trailing-slash variant
is a 307 redirect built from THIS request's own Location -- which would name this harness's own
host, not the board's public /s/<token>/ URL the link actually promised. Declaring both explicitly
means every request this route accepts is answered directly, never redirected. The catch-all route
at the bottom of this file closes the remaining gap: a shape neither this pair nor status_file
matches (an extra trailing slash after a name, a double trailing slash, a decoded path with more
segments than any of these) would otherwise still hit that same undeclared-variant redirect, or
Starlette's own generic 404, before auth ever ran -- see that route's own docstring.

NO DATABASE. This module never opens wa.sqlite or any other database -- it only ever stat()s and
reads plain files under status_docs_home()/www/. The documents it serves are themselves the
record; there is nothing here to look up.
"""
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import config as C
from . import pro_api as PA

router = APIRouter()

#: secrets.token_urlsafe(16) -> exactly 22 URL-safe base64 characters (tools/status_docs_publish.py
#: mints these). Anything else is 404, same as a wrong name (see module docstring).
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")

#: The two fixed page names a status-document directory may ever serve.
FIXED_NAMES = ("index.html", "detail.html")

#: The one variable shape: a lowercase, bounded .pdf name.
PDF_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}\.pdf$")

#: Identical body for every failure (bad token, bad name, missing file, a symlink anywhere) --
#: never reveal which check failed (module docstring).
_NOT_FOUND = "status document not found"

#: Served alongside every 200 (module docstring's design): never indexed, never referred onward,
#: and never cached as if a later republish under the same token couldn't change the content.
_RESPONSE_HEADERS = {"X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer",
                     "Cache-Control": "no-cache"}


def allowed_name(name):
    """True for exactly the file shapes a status-document directory may ever serve (module
    docstring) -- the file-name half of the access design, shared verbatim with
    tools/status_docs_publish.py so the route and the publisher can never drift apart."""
    return name in FIXED_NAMES or bool(PDF_NAME_RE.fullmatch(name))


def _content_type(name):
    return "application/pdf" if name.endswith(".pdf") else "text/html; charset=utf-8"


def _serve(token, name):
    """-> a FileResponse for status_docs_home()/www/<token>/<name>, or 404 (module docstring: the
    identical body for every one of these checks, in order -- token shape, name shape, the token
    directory itself existing and not being a symlink, the file itself existing, being a regular
    file, and not being a symlink). lstat()/is_symlink() throughout: a symlink is never followed,
    only ever rejected -- this never cares where a symlink would point, only that one is there."""
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(404, _NOT_FOUND)
    if not allowed_name(name):
        raise HTTPException(404, _NOT_FOUND)
    token_dir = C.status_docs_home() / "www" / token
    if not token_dir.is_symlink() and token_dir.is_dir():
        path = token_dir / name
        if not path.is_symlink() and path.is_file():
            return FileResponse(str(path), media_type=_content_type(name), headers=_RESPONSE_HEADERS)
    raise HTTPException(404, _NOT_FOUND)


@router.get("/wa/pro/status/{token}")
@router.get("/wa/pro/status/{token}/")
def status_index(token: str, request: Request):
    """Both declared explicitly (module docstring) -- no redirect, either way serves index.html."""
    PA._authorize(request, PA.SCOPE_BOARD)
    return _serve(token, "index.html")


@router.get("/wa/pro/status/{token}/{name}")
def status_file(token: str, name: str, request: Request):
    PA._authorize(request, PA.SCOPE_BOARD)
    return _serve(token, name)


@router.get("/wa/pro/status/{rest:path}")
def status_catch_all(rest: str, request: Request):
    """LAST route in this router, on purpose -- status_index/status_file above match every path
    shape this module actually serves, so FastAPI only ever reaches this one for a shape neither of
    them matches. Without it, two framework behaviours leak around this module's own auth-first,
    identical-404 design: (1) Starlette's own `redirect_slashes` answers a 307 (Location naming THIS
    host, not the board's public URL) for a path like .../{token}/index.html/ or .../{token}// --
    neither one matches status_index/status_file (both declared with no extra trailing slash), so
    without this route Starlette falls back to its slash-toggling redirect before `_authorize` ever
    runs; (2) a request whose path, once percent-decoded, has MORE segments than any declared route
    (an encoded '/' decodes to a real separator before routing, same as a literal one) matches
    nothing at all and falls through to Starlette's own unauthenticated `{"detail": "Not Found"}`.
    Catching the rest here means the only two answers this prefix ever gives, whatever shape of
    nonsense a client sends, are 401 and this module's own `_NOT_FOUND` -- never a redirect, never
    the framework's generic body. Route order matters: FastAPI matches in registration order, so
    this broad `{rest:path}` must stay the LAST route added, after every narrower one above it."""
    PA._authorize(request, PA.SCOPE_BOARD)
    raise HTTPException(404, _NOT_FOUND)
