"""python -m app.firecrawl_gate <clinic_id> [max_credits]

Prints the Firecrawl credit cap the spend gate (app/crawl.spend_gate) + 24h kill switch
(app/crawl.kill_switch) allow for one clinic, so tools/firecrawl_agent.sh can pass the SAME
--max-credits the web app would use. Exits 0 and prints the cap to stdout on success; exits 1 and
prints the refusal reason to stderr when the run is refused; exits 2 on a usage/lookup error.

This treats an explicit CLI invocation the same as a manual mode='firecrawl' dashboard request: the
24h kill switch's throttle tier (>=20%) still allows it (with a log line), only its disable tier
(>=30%) blocks it -- see docs/firecrawl.md."""
import sys

from . import crawl as CR
from . import data as D
from . import settings as ST


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m app.firecrawl_gate <clinic_id> [max_credits]", file=sys.stderr)
        return 2
    clinic_id = argv[0]
    default_cap = ST.get_firecrawl()["default_max_credits"]
    try:
        max_credits = int(argv[1]) if len(argv) > 1 else default_cap
    except ValueError:
        print(f"max_credits must be an integer, got {argv[1]!r}", file=sys.stderr)
        return 2
    clinic = D.clinic(clinic_id)
    if not clinic:
        print(f"unknown clinic_id {clinic_id!r}", file=sys.stderr)
        return 2
    allowed, reason = CR.kill_switch(run_mode="firecrawl", trigger="cli", log=lambda *a: print(*a, file=sys.stderr))
    if not allowed:
        print(reason, file=sys.stderr)
        return 1
    gate = CR.spend_gate(clinic, max_credits, log=lambda *a: print(*a, file=sys.stderr))
    if not gate["allowed"]:
        print(gate["reason"], file=sys.stderr)
        return 1
    print(gate["cap"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
