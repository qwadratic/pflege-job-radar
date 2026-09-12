# The Luna brain, adapted (not copied) from a private reference implementation

Source: a private WhatsApp recruiting-agent implementation the repo owner also operates,
outside this codebase and not linkable here (it is not open source). Read once, on request,
for the purpose of this port; nothing from it is imported, checked out, or otherwise present
in this repository beyond the adaptation described below.

Unlike `.claude/skills/PSTACK-VENDORED.md` and `BACKLOG-TPM-VENDORED.md` elsewhere in this
repo, this is **not a verbatim copy with a line-count of edits** — the source is a private,
company-branded system, and this repository is public. What follows is a rewrite that keeps
the same persona, the same hard rules, and the same conversation gates, with company-specific
and infrastructure-specific material removed or replaced:

| kept, same substance | genericized | dropped entirely |
|---|---|---|
| persona ("Valentina"), tone, Sie-Form, one-question-per-turn, bubble budget | company name → no company named; assistant self-describes generically | interview scheduling (a second, later conversation the source calls "Game 2") |
| qualification accept/reject gate, Urkunde/Defizit/Kenntnisprüfung logic | — (already generic regulatory knowledge, copied as-is: `qualification_knowledge.json`) | CV/document OCR ingestion and the rules that react to it |
| "not placeable → explain once, then stop" | — | clinic-submission email + human-approval token flow (kept only as a state flag, see `constitution.json:handoff_principle`) |
| primary-candidate-first (companion mentioned mid-chat) | — | manager WhatsApp call-permission form, WABA approved-template inventory |
| housing principle (ask people-count, never rooms, never guarantee) | — | proactive re-engagement (soft nudges, quiet hours, promise reminders) — this harness only replies to inbound messages |
| live market matching, anonymized-send offer, "never invent a clinic name" | source treats live-market matching as a Bavaria-only special case with a fixed-photo-pack fallback elsewhere; here it is the only mode, because this board only covers Bavaria and has no partner-clinic list | the fixed clinic photo packs themselves (named real partner clinics — a business relationship, not applicable here) |
| escalate-to-human flag for genuine unknowns / unreadable media | "manager" → "a human" (no manager CRM exists here; the flag is stored on the thread for `GET /api/wa/threads` to surface) | the manager-actions/manager-takeover machinery itself |

`constitution.json` and `prompts.py` (`GOAL`/`RULES`/`THINK_ORDER`) are the rewrite described
above — closely-paraphrased structure and gate logic, original wording, no source text
reproduced. `qualification_knowledge.json` is the one file kept effectively as-is: it is
factual German nursing-qualification/recognition domain knowledge (Anerkennung,
Defizitbescheid, Kenntnisprüfung, Urkunde definitions) with no company-specific content to
remove.

Model: the source calls OpenAI (`gpt-5.6-luna`, forced `response_format=json_object`). This
adaptation calls Claude (`app/wa/luna_brain.py`) through the `claude` CLI's non-interactive
print mode (`claude -p --restricted --output-format json`) rather than the Anthropic Python
SDK, so it rides whatever Claude Code auth already exists on the host instead of needing a
separate `ANTHROPIC_API_KEY` — the same "the model decides the action and writes the wording;
the harness only supplies state" design, different provider and a different call path.
