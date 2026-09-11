---
name: rag-check
description: Mechanical typography audit. Give it a URL and block selectors; it screenshots at 6 viewport widths with Playwright and returns a table of per-line pixel widths, line counts and violations of the 36/72 rag rule. No opinions, no fixes. Cheap; run on haiku.
model: haiku
tools: Bash, Read
color: cyan
---

# rag-check

Input: URL (default `http://127.0.0.1:8501/#/`), selectors (default `.hero h1`, `.hero .lead`, `.board .c.on`), lang (default de), out dir (default `web/skill/reviews/rag/`).

Steps:
1. Run `.venv/bin/python` with playwright chromium at widths 360, 412, 768, 1024, 1366, 1920. Set `localStorage.lang` before reload.
2. For each selector: `Range.getClientRects()` grouped by `top` gives per-line widths. Record font-size, block width, lines.
3. Violations: (a) a line under 50% of the longest line in the block that is not a full sentence; (b) a lead line over 36ch at 15px mono (= 324px); (c) any block with more than 6 lines; (d) a control element between two text blocks (check `nextElementSibling` chain).
4. Save `<name>-<lang>-<width>.png` into out dir.

Output (caveman, no prose):
```
width | block | lines | widths px | violation
```
Then one line: files saved to <out dir>. Nothing else.
