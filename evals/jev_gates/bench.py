"""TASK-457 before/after bench: the closing + refusal gates on Jev (OpenRouter HTTP) vs the
previous haiku ``claude -p`` subprocess.

A SCRIPT, not a test: it spends real money on Jev and spawns real haiku processes, so it lives
in evals/ like evals/cv/run.py. Needs WA_OPENROUTER_API_KEY in the environment (psst) and an
authenticated `claude` CLI on the host. The cases are labeled by the reviewed policy -- the
labeled unit tests' examples plus the TASK-386 distinction (short negative after a FACT
question vs after an INTEREST question) and the shapes both gates' docstrings call out.

Usage (repo root):  WA_OPENROUTER_API_KEY="$(psst get WA_OPENROUTER_API_KEY)" \
                    .venv/bin/python evals/jev_gates/bench.py [--repeats N]
Exit 1 on any verdict disagreement between an arm and its label.
"""
import argparse
import json
import statistics
import sys
import time

sys.path.insert(0, ".")  # ponytail: run from the repo root

from app.wa import config as C  # noqa: E402
from app.wa.luna import closing_gate as CG  # noqa: E402
from app.wa.luna import refusal as R  # noqa: E402
from app.wa.luna.refusal import _run_cli  # noqa: E402  (the old arm)

# (bubbles, expected closes) -- labeled by the gate's reviewed SYSTEM_PROMPT / unit tests.
CLOSING_CASES = [
    (["In welcher Region suchen Sie?"], True, "direct question (labeled test)"),
    (["Das ist leider unterschiedlich."], False, "state-only statement (labeled test)"),
    (["Erst das.", "Dann die Frage?"], True, "two bubbles, last is a question (labeled test)"),
    (["Koennen Sie mir ein Foto vom Attest schicken?"], True, "document request"),
    (["Wir koennen Sie leider nicht gut platzieren. Ich wuensche Ihnen fuer die Zukunft alles Gute."],
     True, "deliberate end"),
    (["Vielen Dank fuer Ihre Rueckmeldung. Wir melden uns, sobald etwas passt."],
     False, "promise, nothing to answer"),
    (["Ein Kollege von mir meldet sich heute Nachmittag. Bis dahin: Haben Sie Fragen?"],
     True, "handoff plus trailing question"),
]

# (candidate_reply, our_last_message, expected refusal) -- labeled by the TASK-386 distinction.
REFUSAL_CASES = [
    ("nein, danke", "Suchen Sie noch eine Stelle?", True, "short negative / interest question"),
    ("nein", "Haben Sie die Urkunde schon?", False, "short negative / fact question"),
    ("nein", None, False, "short negative / no anchor"),
    ("vielleicht spaeter", None, False, "deferral"),
    ("Ich habe schon eine Stelle angenommen.", None, True, "hard stop"),
    ("Kein Interesse.", None, True, "plain refusal"),
    ("Wie viel wird denn bezahlt?", "Ist das fuer Sie interessant?", False, "question back"),
]


def _old_closing(payload_text):
    return _run_cli(payload_text, model=C.CLOSING_GATE_MODEL, timeout_sec=C.CLOSING_GATE_TIMEOUT_SEC,
                    system_prompt=CG.SYSTEM_PROMPT, what="closing bench/old")


def _old_refusal(payload_text):
    return _run_cli(payload_text, model=C.REFUSAL_MODEL, timeout_sec=C.REFUSAL_TIMEOUT_SEC,
                    system_prompt=R.SYSTEM_PROMPT, what="refusal bench/old")


def _timed(fn, *args):
    t0 = time.monotonic()
    out = fn(*args)
    return out, (time.monotonic() - t0) * 1000.0


def _verdict_from_old(raw_text, key):
    obj = R._extract_verdict_json(raw_text)
    return bool(obj[key])


def bench(label, cases, old_fn, old_key, new_fn, new_key, repeats):
    """cases: (payload_text, expected, note) -- payload_text is the exact JSON envelope the
    gate's own transport builds, so both arms see byte-identical input."""
    print(f"\n=== {label} ===")
    old_ms, new_ms, disagreements = [], [], 0
    for i, (payload_text, expected, note) in enumerate(cases, 1):
        results = []
        for _ in range(repeats):
            old_raw, old_t = _timed(old_fn, payload_text)
            old_v = _verdict_from_old(old_raw, old_key)
            new_raw, new_t = _timed(new_fn, payload_text)
            new_v = json.loads(new_raw)[new_key]
            old_ms.append(old_t)
            new_ms.append(new_t)
            results.append((old_v, new_v, old_t, new_t))
        ok = all(old_v == expected and new_v == expected for old_v, new_v, _, _ in results)
        if not ok:
            disagreements += 1
        ex, ne = results[-1]
        mark = "ok " if ok else "MIS"
        print(f"  [{mark}] {i}. {note}\n"
              f"       expected={expected}  old(haiku)={ex[0]}  new(jev)={ne[1]}  "
              f"over {repeats} repeats: "
              + ", ".join(f"o={a}/{b:.0f}ms j={c}/{d:.0f}ms" for a, c, b, d in results))
    def _pct(vals, p):
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(len(vals) * p))]
    print(f"  latency old(haiku CLI): p50={_pct(old_ms, .5):.0f}ms max={max(old_ms):.0f}ms")
    print(f"  latency new(jev HTTP):  p50={_pct(new_ms, .5):.0f}ms max={max(new_ms):.0f}ms")
    return disagreements


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()

    if not C.OPENROUTER_API_KEY:
        sys.exit("WA_OPENROUTER_API_KEY is not set -- the Jev arm cannot run")
    if not C.JEV_MODEL:
        sys.exit("WA_JEV_MODEL is empty")

    d = 0
    d += bench("closing gate",
               [(json.dumps({"bubbles": b}), e, n) for b, e, n in CLOSING_CASES],
               _old_closing, "closes",
               CG._live_transport, "closes", args.repeats)
    d += bench("refusal gate",
               [(json.dumps({"candidate_reply": c, "our_last_message": o}), e, n)
                for c, o, e, n in REFUSAL_CASES],
               _old_refusal, "unambiguous_refusal",
               R._live_transport, "unambiguous_refusal", args.repeats)

    print(f"\nverdict disagreements vs labels: {d}")
    sys.exit(1 if d else 0)


if __name__ == "__main__":
    main()
