"""Replay the colleague's sales_brain WhatsApp history through the Luna brain, cut at turn N, nothing
sent (TASK-313; Ivan, 2026-10-06). Design, no-send chain and known limits: ``app/wa/luna/replay.py``'s
docstring. This file is the CLI wrapper only, same split as tools/wa_rehearse.py and
app/wa/luna/shadow_run.py.

    python tools/wa_replay.py --list
    python tools/wa_replay.py --candidate 9001 --out /dev/shm/wa-replay-9001 --max-turns 2
    python tools/wa_replay.py --candidate 9001 --out /dev/shm/wa-replay-9001 --at-turns 3,7
    python tools/wa_replay.py --candidate 9001 --out /dev/shm/wa-replay-9001 --board-db PATH

``--at-turns 3,7`` runs the brain on turns 3 and 7 only; every other turn is recorded as plain history with
no model call (replay.py's docstring, AT TURNS, has the card limitation that follows).

Exit code 0 on a clean run, 1 when any turn errored ("K of N turns errored"); the run itself goes on past
an erroring turn.

NOTHING IS SENT TO A CANDIDATE, and the sales_brain CRM is never written: every turn runs ``no_send=True``
(replay.py's docstring has the chain, down to the two MCP tools that can send), and sales_brain.sqlite is
opened ``mode=ro``. What does leave ``--out``: the candidate's text goes to the model through ``claude -p``
like any live turn, and the CLI keeps its turn transcripts under ``~/.claude/projects/``.

ENV, BEFORE ``app.wa.config`` IS IMPORTED. config.py freezes every ``WA_*``/``META_WHATSAPP_*`` variable
into a module constant at import (app/wa/envfile.py explains why that matters), so the setup sits behind
``if __name__ == "__main__"`` at the top of this file, above the app imports, same idiom as
app/wa/luna/agent_notes.py (an ordinary import, e.g. by a test, reads no file and changes no variable):
  * the live-credential set tests/conftest.py pops is popped here too, so a shell that still has the
    phone rail's rail.env loaded can never hand a replay a real transport, bridge token or Meta token;
  * ``WA_LUNA_NO_SEND=1`` is set on this one-shot process, like tools/wa_rehearse.py;
  * ``--candidate`` loads ONLY ``WA_LUNA_MODEL``, ``WA_LUNA_EFFORT`` and ``SUPABASE_ANON_KEY`` (config.py's
    ``LUNA_MODEL``/``LUNA_EFFORT`` and app/config.py's ``ANON_KEY``, which app/data.py's board snapshot
    reads Supabase with) from the MAIN checkout's ``.env`` (a worktree has none) through
    ``app.wa.envfile.parse_env_text``, never printing a value, so the replay runs with the model and effort
    production runs. A key that is missing or empty is a loud error: a silent fallback to the library
    defaults would not be comparable to production.

BOARD. ``--board-db`` (default: the main checkout's data/app.sqlite) is copied with SQLite's online-backup
API from a ``mode=ro`` source (same idiom as shadow_run.db_copy) into ``--out``, and
``app.config.SQLITE_PATH``, which app/runs.py's board-registry reads (last run, career profiles, photos,
blurbs; all folded into app/data.py's snapshot) go through, is pointed at the copy. The live file is never
opened writable.

ISOLATION. ``--out`` is refused when it resolves inside this checkout or the main checkout: a scratch run
never writes into a tracked tree. Use a directory under /dev/shm.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

#: Same names as tests/conftest.py's ``_LIVE_CREDENTIALS`` (a test keeps the two equal).
_LIVE_CREDENTIALS = (
    "WA_TRANSPORT", "WA_BRIDGE_URL", "WA_BRIDGE_TOKEN", "WA_BRIDGE_INBOUND_TOKEN", "WA_BRIDGE_PHONE_NUMBER_ID",
    "WA_AUTOSEND", "WA_REAL_SYSTEM_WEBHOOK_URL",
    "META_WHATSAPP_ACCESS_TOKEN", "META_WHATSAPP_APP_SECRET", "META_WHATSAPP_PHONE_NUMBER_ID",
    "META_WHATSAPP_VERIFY_TOKEN", "OPENAI_API_KEY",
)
#: The only keys read from .env (ENV, module docstring).
_PROD_BRAIN_ENV_KEYS = ("WA_LUNA_MODEL", "WA_LUNA_EFFORT", "SUPABASE_ANON_KEY")


def _main_checkout_root(this_root=None):
    """The MAIN checkout's root, where the live .env lives: git's common dir is shared by every worktree
    and sits inside the main checkout (same as tools/test_gate.py's main_checkout_root). ``this_root``:
    any directory of the checkout to resolve from, default this script's own."""
    this_root = pathlib.Path(this_root) if this_root else pathlib.Path(__file__).resolve().parents[1]
    out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         cwd=this_root, capture_output=True, text=True, check=True).stdout.strip()
    return pathlib.Path(out).parent


def _repo_roots():
    """Roots an --out must not resolve inside: this script's own checkout and the MAIN checkout (live
    production, never touched)."""
    return {pathlib.Path(__file__).resolve().parents[1], _main_checkout_root()}


def check_out_dir(out_dir):
    """Refuse an --out that resolves inside a repo checkout (ISOLATION), naming /dev/shm as the place."""
    resolved = pathlib.Path(out_dir).resolve()
    for root in _repo_roots():
        if resolved == root or root in resolved.parents:
            raise SystemExit(f"--out {resolved} resolves inside a repo checkout ({root}) -- this tool never "
                             f"writes scratch state into a checkout. Use a directory outside any checkout, "
                             f"e.g. under /dev/shm.")


def _turn_list(text):
    """argparse type for --at-turns: ``"3,7"`` -> [3, 7], positive integers only."""
    try:
        turns = [int(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a comma-separated list of turn numbers")
    if not turns or any(n < 1 for n in turns):
        raise argparse.ArgumentTypeError(f"{text!r}: turn numbers start at 1")
    return turns


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="list every candidate_id sales_brain holds, with counts")
    p.add_argument("--candidate", type=int, help="candidate_id to replay")
    p.add_argument("--out", help="directory for the scratch sqlite + JSONL report (required with --candidate); "
                                "refused inside any repo checkout, use e.g. /dev/shm")
    p.add_argument("--max-turns", type=int, default=None, help="stop after this many turns (default: all)")
    p.add_argument("--at-turns", dest="at_turns", type=_turn_list, default=None,
                   help="comma-separated turn numbers to run the brain on, e.g. 3,7; the others are plain "
                        "history, no model call (exclusive with --max-turns)")
    p.add_argument("--sales-brain-path", dest="sales_brain_path", default=None,
                   help="override the sales_brain.sqlite path (default: app.wa.config.sales_brain_path())")
    p.add_argument("--board-db", dest="board_db", default=None,
                   help="board registry sqlite to copy read-only (default: the main checkout's data/app.sqlite)")
    return p


def parse_args(argv):
    """Parse and validate before anything else happens, so a bad invocation (no --out, an --out inside a
    checkout) fails before any .env is read."""
    p = build_parser()
    args = p.parse_args(argv)
    if args.list:
        return args
    if args.candidate is None:
        p.error("give --list or --candidate ID --out DIR")
    if not args.out:
        p.error("--candidate requires --out DIR")
    if args.at_turns is not None and args.max_turns is not None:
        p.error("--at-turns and --max-turns are exclusive")
    check_out_dir(args.out)
    return args


def scrub_live_credentials(environ):
    """Drop every live credential from ``environ`` and set the no-send flag."""
    for name in _LIVE_CREDENTIALS:
        environ.pop(name, None)
    environ["WA_LUNA_NO_SEND"] = "1"


def load_prod_brain_env(env_path, environ):
    """Load ONLY ``_PROD_BRAIN_ENV_KEYS`` from ``env_path`` into ``environ``, through app.wa.envfile's own
    parser. They override whatever the shell had (the file is the single source of truth); no value is
    ever printed or put in an error. A key missing or empty is a loud error naming the key."""
    from app.wa import envfile as EF   # imports only os/pathlib: safe before app.wa.config
    pairs = dict(EF.parse_env_text(pathlib.Path(env_path).read_text(encoding="utf-8"), path=str(env_path)))
    bad = [k for k in _PROD_BRAIN_ENV_KEYS if not pairs.get(k, "").strip()]
    if bad:
        raise RuntimeError(f"{env_path}: missing or empty {', '.join(bad)} -- cannot run with production's "
                           f"brain settings")
    for key in _PROD_BRAIN_ENV_KEYS:
        environ[key] = pairs[key]


def bootstrap_env(argv, environ=None, env_path=None):
    """Everything that has to happen before ``app.wa.config`` is imported (ENV, module docstring). The
    .env is read only for ``--candidate``; ``--list`` needs no production settings."""
    environ = os.environ if environ is None else environ
    args = parse_args(argv)
    scrub_live_credentials(environ)
    if args.candidate is not None:
        load_prod_brain_env(env_path or _main_checkout_root() / ".env", environ)


if __name__ == "__main__":
    bootstrap_env(sys.argv[1:])

import app.config as APPC                                                 # noqa: E402
from app.wa import config as C                                            # noqa: E402
from app.wa.luna import replay as RP                                      # noqa: E402


def copy_board_db(board_db_path, out_dir):
    """Back up ``board_db_path`` into ``out_dir/board.sqlite`` via SQLite's online-backup API from a
    ``mode=ro`` source. -> the copy's path. A missing source is a loud error: a replay that silently ran
    on an empty board must never look like one that found no match."""
    board_db_path = pathlib.Path(board_db_path)
    if not board_db_path.exists():
        raise RuntimeError(f"--board-db {board_db_path} does not exist")
    dest = pathlib.Path(out_dir) / "board.sqlite"
    src = sqlite3.connect(f"file:{board_db_path}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return dest


def cmd_list(args):
    rows = RP.list_candidates(args.sales_brain_path or C.sales_brain_path())
    if not rows:
        print("no candidates with a non-null candidate_id")
        return 0
    print(f"{'candidate_id':>12}  {'in':>4}  {'out':>4}  {'first':<25} {'last':<25}")
    for r in rows:
        print(f"{r['candidate_id']:>12}  {r['n_in']:>4}  {r['n_out']:>4}  {r['first_at']:<25} {r['last_at']:<25}")
    return 0


def cmd_candidate(args):
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    board_db_path = args.board_db or (_main_checkout_root() / "data" / "app.sqlite")
    board_copy = copy_board_db(board_db_path, out_dir)
    APPC.SQLITE_PATH = board_copy                              # BOARD, module docstring
    try:
        result = RP.replay_candidate(args.candidate, out_dir, max_turns=args.max_turns,
                                     at_turns=args.at_turns,
                                     sales_brain_path=args.sales_brain_path or C.sales_brain_path())
    finally:
        # The board db also holds sessions, magic links and customers: the copy lives only for the
        # run and never stays next to the replayed conversations.
        for suffix in ("", "-wal", "-shm", "-journal"):
            pathlib.Path(str(board_copy) + suffix).unlink(missing_ok=True)
    # --at-turns: only the chosen turns ran through the brain, the others are plain history.
    ran = len(set(args.at_turns)) if args.at_turns is not None else result["turns_run"]
    print(f"candidate {result['candidate_id']}: {result['turns_run']} turn(s) walked, {ran} run through the brain"
          + (" (truncated)" if result["truncated"] else " (end of history)"))
    print(f"  jsonl:  {result['jsonl_path']}")
    print(f"  sqlite: {result['sqlite_path']}")
    # LOUD: an erroring turn never stops the run, but the run must not read as clean.
    print(f"  {result['errors']} of {ran} turns errored")
    return 1 if result["errors"] > 0 else 0


def main(argv=None):
    args = parse_args(argv)
    return cmd_list(args) if args.list else cmd_candidate(args)


if __name__ == "__main__":
    raise SystemExit(main())
