#!/usr/bin/env python3
"""Print the WhatsApp status message for one candidate, exactly as it would be sent (TASK-439).

    python tools/wa_status_message.py <status.json> <status-page URL>

<status.json> is her status-page JSON (built by the email lane, outside this repo); <URL> is what
tools/status_docs_publish.py printed for her page. Prints the two bubbles, each under a "--- bubble N" line.
Dry run only: this tool has no send path. A bad input exits 1 with the reason (app/wa/luna/status_message.py).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wa.luna import status_message as SM                              # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("status_json")
    p.add_argument("url")
    args = p.parse_args(argv)
    status = json.loads(pathlib.Path(args.status_json).read_text(encoding="utf-8"))
    try:
        out = SM.bubbles(status, args.url)
    except ValueError as exc:
        print(f"wa_status_message: {exc}", file=sys.stderr)
        return 1
    for i, bubble in enumerate(out, 1):
        print(f"--- bubble {i}")
        print(bubble)
    return 0


if __name__ == "__main__":
    sys.exit(main())
