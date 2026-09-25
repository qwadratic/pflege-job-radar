"""EnvironmentFile parsing for the two files systemd loads for pflege-wa.service (Ivan/TASK-303,
2026-09-25): the repo's own .env, and ~/.local/state/pflege-wa-bridge/rail.env.

WHY THIS EXISTS. "set -a; . ./.env; set +a" is how every other tool in this repo loads the same file
(tools/wa_bridge_env.sh, docs/whatsapp.md) -- and it breaks on line 37, KNOWN_PHONES_SOURCE_QUERY, an
unquoted SQL string with spaces. Sourced as shell, "KNOWN_PHONES_SOURCE_QUERY=select distinct ..."
assigns only the word "select" and then hands bash the rest ("distinct", "phone", ...) as a command to
run -- exactly the "distinct: command not found" shape tools/wa_bridge_env.sh's own comment documents
working around with "set -uo pipefail" instead of "set -e" plus a swallowed stderr on the source line.
That workaround is fine for a fire-and-forget CLI wrapper that still runs under a real shell; it is not
something a periodic worker can rely on to fail loudly when something IS wrong, and it does not exist
at all for a caller with no shell in front of it (a subprocess.run() argv list has no ". ./.env" to run,
and neither does this process's own Python import of app.wa.config).

WHAT THIS PARSES: an EnvironmentFile the way systemd's own EnvironmentFile= directive does, scoped to
exactly what this repo's two files use --
  - one KEY=VALUE assignment per line, KEY a plain env-var name, never itself quoted;
  - blank lines ignored;
  - a line whose first non-whitespace character is '#' is a full-line comment, ignored;
  - VALUE may be surrounded by one layer of matching single or double quotes, stripped; whatever sits
    between them (including inner whitespace) is taken literally, no escape processing;
  - an unquoted VALUE runs to the end of the line (surrounding whitespace trimmed) -- so a value with
    spaces, or with its own '=' further in (a SQL WHERE clause), is never shell-word-split the way
    sourcing would split it;
  - no shell expansion of any kind: '$FOO', backticks, '~', globs are all literal characters in VALUE.
Deliberately narrower than systemd's full grammar (no backslash line continuation, no ';'-comments, no
'export ' prefix) -- this repo's two files use none of that, and CLAUDE.md's "no invented safety nets"
argues against silently supporting syntax nobody asked for and nobody can therefore have tested. A line
that is neither blank, a comment, nor a KEY=VALUE assignment is a real mistake in a hand-edited file,
not something to silently step over -- it raises (CLAUDE.md "no safety nets": failures fail loudly).

WHAT LOADS INTO os.environ, AND WHY NOT OVERRIDING: load_env_file/load_service_env_files use
os.environ.setdefault semantics -- a variable already set (by the real shell environment, by an
explicit export, or by an earlier file in the same load_service_env_files call) is left alone. That
matches what "set -a; . ./.env" would do to a variable the caller already exported on purpose, and it
is what lets a test override WA_ENV_FILE_PATH/WA_RAIL_ENV_FILE_PATH -- or set a variable directly --
without this module clobbering it back.

CALLERS MUST LOAD BEFORE app.wa.config IS FIRST IMPORTED IN THIS PROCESS. config.py reads every
WA_*/META_WHATSAPP_* variable into a plain module-level constant AT IMPORT TIME (os.environ.get(...)
executed once, when Python first imports the module); nothing in it re-reads os.environ later. Calling
load_service_env_files() from inside a function whose module already imported app.wa.config (directly,
or transitively via app.wa.api/app.wa.store) is too late -- the constants are already frozen from
whatever os.environ held before this module ever ran, and nothing this function does afterward changes
them. app/wa/luna/agent_notes.py and app/wa/luna/agent_note_worker.py both handle this the same way:
the load call sits behind ``if __name__ == "__main__":`` at the very top of the file, before either
module's own app.wa.* imports -- so it runs, and only runs, when the file is the actual entry point,
before app.wa.config is pulled in for the first time in that process. An ordinary import of either
module (by a test, or by the other of the two importing this one as a library) touches no file on disk
and changes no environment variable. See those two modules' own top-of-file comments for the mechanics.
"""
import os
import pathlib

# The two EnvironmentFiles of the live pflege-wa.service (deploy/pflege-wa.service, docs/whatsapp.md):
# the worktree's own .env, and the four bridge-rail variables that live outside the worktree on purpose
# (config.py's own BRIDGE_* comments: ".env is in a worktree"). Overridable per path for tests, never
# for anything else -- a real deploy loads both, always, in this order.
DEFAULT_ENV_FILE = "/home/claude/repo/pflege-board/.env"
DEFAULT_RAIL_ENV_FILE = "/home/claude/.local/state/pflege-wa-bridge/rail.env"


def _unquote(value):
    """Strip one layer of matching surrounding quotes, single or double; anything else comes back
    exactly as given. No escape processing inside the quotes -- the two files this module reads use
    none, and CLAUDE.md argues against silently supporting syntax nobody asked for."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_text(text, *, path=None):
    """-> [(key, value), ...] in file order. Blank lines and full-line '#' comments are skipped; every
    other line must contain '=' or this raises ValueError naming the line (see the module docstring).
    The key is the exact substring before the FIRST '=' (systemd's own rule -- KEY is never quoted,
    only VALUE can be, and VALUE may itself legitimately contain '=', e.g. a SQL WHERE clause), stripped
    of surrounding whitespace; VALUE is everything after that first '=', trimmed, then unquoted.

    ERROR MESSAGES NAME THE FILE, THE LINE NUMBER AND AT MOST THE KEY -- NEVER THE RAW LINE OR ANY
    VALUE (TASK-303, Ivan 2026-09-25, round-1 review finding: a malformed .env line can BE a secret
    value that continued onto its own line -- e.g. a Meta access token wrapped mid-string -- and the
    original ``{raw!r}`` in these messages put exactly that text into worker.log the moment such a line
    ever occurred; reproduced live against a real malformed line during that review). ``path`` is the
    file this text came from, for the message only (``load_env_file`` passes its own path; a direct
    caller with no file, e.g. a test, gets no path segment rather than a placeholder that could be
    mistaken for a real one)."""
    where = f"{path}: " if path else ""
    out = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{where}line {lineno}: no '=' -- not a KEY=VALUE assignment")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"{where}line {lineno}: empty key")
        out.append((key, _unquote(value.strip())))
    return out


def apply_env(pairs, environ=None):
    """setdefault every (key, value) pair into ``environ`` (os.environ by default). -> the keys this
    call actually set -- a key already present, from an earlier file in the same load or the real shell
    environment, is left untouched and does not appear here."""
    environ = os.environ if environ is None else environ
    newly_set = []
    for key, value in pairs:
        if key not in environ:
            environ[key] = value
            newly_set.append(key)
    return newly_set


def load_env_file(path, environ=None):
    """Parse and apply one file. A missing file sets nothing and is not an error -- an EnvironmentFile
    is optional configuration a given deployment may not have created (the same non-fatal treatment
    tools/wa_bridge_env.sh already gives a missing file with its own "2>/dev/null"); this module's job
    is parsing KEY=VALUE correctly, not deciding whether a particular deploy ought to have this file.
    -> the keys newly set (apply_env's own return), [] for a missing file."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return apply_env(parse_env_text(text, path=str(path)), environ)


def service_env_paths():
    """-> (env file path, rail env file path): WA_ENV_FILE_PATH/WA_RAIL_ENV_FILE_PATH when set (tests),
    else the live service's own two paths. Read fresh on every call, not cached at import -- so a test
    can set the override env var right before calling load_service_env_files() with no need to reload
    this module."""
    return (os.environ.get("WA_ENV_FILE_PATH", "").strip() or DEFAULT_ENV_FILE,
            os.environ.get("WA_RAIL_ENV_FILE_PATH", "").strip() or DEFAULT_RAIL_ENV_FILE)


def load_service_env_files(environ=None):
    """Load, in order, the two EnvironmentFiles of the live pflege-wa.service -- .env, then rail.env,
    matching tools/wa_bridge_env.sh's own order (the two never share a key on purpose, so order only
    matters for which file "wins" if that ever changes -- see config.py's BRIDGE_* comments). -> every
    key newly set, in load order. See this module's docstring for why the CALLER must run this before
    app.wa.config is first imported in the process, or it has no effect on that module's constants."""
    keys = []
    for path in service_env_paths():
        keys.extend(load_env_file(path, environ))
    return keys
