# Grading criteria

Pass/fail, not partial credit — any single miss below fails the case.

1. **Every number survives exactly.** 407, 0, 28, 8, 18, 121, 71, 47, 3, 118, 69, 49 — all present,
   none rounded, none dropped, none invented. This is the sharpest failure mode: compression that
   quietly drops or fuzzes a count is worse than no compression.
2. **Clinic_id 66303 and its closure reason are both named**, not summarized away as "one clinic had
   issues." The specific fact (insolvency, closed after the plan's cutoff date) is the whole reason
   it's an exception worth reporting.
3. **No invented facts.** The 49 vendor names in the source text must not appear inflated,
   collapsed, or replaced with a vendor not in the input list.
4. **No stock openers or hedging** — fail on "Sure!", "I'd be happy to", "Let me summarize", "It's
   worth noting that", or any sentence whose removal loses no information.
5. **Structure may compress** (fragments, dropped articles, short synonyms) but must stay
   readable by someone who wasn't in the room — no shorthand invented mid-answer without
   definition.
6. **Length**: a compressed report of this input should land under ~180 words. Above that, the
   compression isn't doing its job.
