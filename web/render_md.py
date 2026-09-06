"""Minimal Markdown -> HTML for the docs page.

Deliberately not a full CommonMark implementation: it covers exactly the constructs used in
docs/ARCHITECTURE.md (headings, fenced code, pipe tables, lists, blockquotes, hr, inline code /
bold / italic / links). Keeping it in-repo avoids a runtime dependency for one static page, and a
strict subset is easy to reason about -- anything it does not understand passes through escaped.
"""
import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITAL = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")


def _inline(text):
    """Escape, then re-introduce the inline markup. Code spans are protected from further parsing."""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return "\x00%d\x00" % (len(spans) - 1)

    text = _INLINE_CODE.sub(stash, text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2), quote=True), m.group(1)), text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITAL.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: "<code>%s</code>" % html.escape(spans[int(m.group(1))], quote=False), text)


def _table(rows):
    """rows: list of raw '| a | b |' lines, second of which is the --- separator."""
    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]
    head, body = cells(rows[0]), [cells(r) for r in rows[2:]]
    out = ["<table><thead><tr>"] + ["<th>%s</th>" % _inline(c) for c in head] + ["</tr></thead><tbody>"]
    for r in body:
        out.append("<tr>" + "".join("<td>%s</td>" % _inline(c) for c in r) + "</tr>")
    return "".join(out) + "</tbody></table>"


def render(md):
    out, i, lines = [], 0, md.split("\n")
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):                                   # fenced code
            lang, i = ln[3:].strip(), i + 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            cls = ' class="lang-%s"' % html.escape(lang, quote=True) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(buf), quote=False)))
            continue
        if re.match(r"^\|.*\|\s*$", ln) and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
            buf = []
            while i < len(lines) and re.match(r"^\|.*\|\s*$", lines[i]):
                buf.append(lines[i]); i += 1
            out.append(_table(buf)); continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, _inline(m.group(2)), lvl)); i += 1; continue
        if re.match(r"^---+\s*$", ln):
            out.append("<hr>"); i += 1; continue
        if ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip()); i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % _inline(" ".join(buf))); continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m:
            ordered = bool(re.match(r"\d+\.", m.group(2)))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not mm:
                    # a plain indented line continues the previous bullet
                    if items and lines[i].startswith("  ") and lines[i].strip():
                        items[-1] += " " + lines[i].strip(); i += 1; continue
                    break
                items.append(mm.group(3)); i += 1
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % _inline(x) for x in items), tag))
            continue
        if not ln.strip():
            i += 1; continue
        buf = []                                                   # paragraph
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|```|\||>|---+\s*$|\s*([-*]|\d+\.)\s)", lines[i]):
            buf.append(lines[i].strip()); i += 1
        out.append("<p>%s</p>" % _inline(" ".join(buf)))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    print(render(open(sys.argv[1], encoding="utf-8").read()))
