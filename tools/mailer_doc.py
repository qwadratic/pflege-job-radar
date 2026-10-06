"""The body of a mail to the operators, written once as paragraphs and tables and rendered twice: plain text with aligned
rows, and HTML with real tables (Ivan, 2026-10-05: an operator reads a mail at a glance).

    Doc("Intro.", Table(["Время", "Клиника"], [["09:00", "Klinik A"]]), Quote("a letter"), "Daria")

A paragraph is a plain string. In the HTML part it may use a small subset of Markdown, which the desk's answerer writes:
"- " and "1. " lists, "| a | b |" tables, "# " headings, **bold** and `code`; the text part keeps the string as it is.
Letters to clinics do not use this: they stay plain German text."""
import html
import re

STYLE = {"body": "font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.45;color:#23201E",
         "table": "border-collapse:collapse;margin:4px 0 14px;font-size:14px",
         "th": "text-align:left;padding:5px 12px 5px 0;border-bottom:2px solid #999;vertical-align:bottom",
         "td": "padding:5px 12px 5px 0;border-bottom:1px solid #ddd;vertical-align:top",
         "quote": "margin:4px 0 14px;padding:6px 12px;border-left:3px solid #bbb;white-space:pre-wrap;color:#444",
         "p": "margin:0 0 14px"}


def esc(s):
    return html.escape(str(s), quote=False)


def inline(s):
    """Escaped text with **bold** and `code`."""
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


class Table:
    """Rows of strings under an optional header and title. Text: aligned columns, two spaces apart. HTML: a real table."""

    def __init__(self, header, rows, title=None):
        self.header, self.rows, self.title = header, [[str(c) for c in r] for r in rows], title
        if header and any(len(r) != len(header) for r in self.rows):
            raise ValueError(f"a table row does not have {len(header)} cells: {self.rows}")

    def text(self):
        grid = ([list(self.header)] if self.header else []) + self.rows
        width = [max(len(r[i]) for r in grid) for i in range(max(map(len, grid)))] if grid else []
        lines = ["  " + "  ".join(c.ljust(width[i]) for i, c in enumerate(r)).rstrip() for r in grid]
        return "\n".join(([self.title] if self.title else []) + lines)

    def html(self):
        th, td = f'style="{STYLE["th"]}"', f'style="{STYLE["td"]}"'
        head = "<tr>" + "".join(f"<th {th}>{esc(c)}</th>" for c in self.header) + "</tr>" if self.header else ""
        rows = "".join("<tr>" + "".join(f"<td {td}>{inline(c)}</td>" for c in r) + "</tr>" for r in self.rows)
        title = f'<p style="margin:0 0 4px"><b>{inline(self.title)}</b></p>' if self.title else ""
        return f'{title}<table style="{STYLE["table"]}">{head}{rows}</table>'


class Quote:
    """A text shown as it is, for example a letter: set off in HTML, unchanged in text."""

    def __init__(self, text):
        self.body = text.rstrip()

    def text(self):
        return self.body

    def html(self):
        return f'<div style="{STYLE["quote"]}">{esc(self.body)}</div>'


def markdown_blocks(s):
    """The HTML of a paragraph string: lists, pipe tables, headings, and lines kept as lines."""
    out, lines, i = [], s.split("\n"), 0
    para = []

    def flush():
        if para:
            out.append(f'<p style="{STYLE["p"]}">' + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*\|.*\|\s*$", line) and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            flush()
            cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
            header, rows, i = cells(line), [], i + 2
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                row = cells(lines[i])
                rows.append((row + [""] * len(header))[:len(header)])
                i += 1
            out.append(Table(header, rows).html())
            continue
        item = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", line)
        if item:
            flush()
            tag, items = ("ol" if item.group(1)[0].isdigit() else "ul"), []
            while i < len(lines) and (m := re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", lines[i])):
                items.append(f"<li>{inline(m.group(2))}</li>")
                i += 1
            out.append(f'<{tag} style="margin:0 0 14px;padding-left:22px">{"".join(items)}</{tag}>')
            continue
        head = re.match(r"^#{1,6}\s+(.*)$", line)
        if head:
            flush()
            out.append(f'<p style="{STYLE["p"]}"><b>{inline(head.group(1))}</b></p>')
        elif line.strip():
            para.append(line.rstrip())
        else:
            flush()
        i += 1
    flush()
    return "".join(out)


class Doc:
    """Blocks in order: a string (paragraph), a Table, a Quote."""

    def __init__(self, *blocks):
        self.blocks = [b for b in blocks if b is not None]

    def text(self):
        return "\n\n".join(b if isinstance(b, str) else b.text() for b in self.blocks).rstrip() + "\n"

    def html(self):
        body = "".join(markdown_blocks(b) if isinstance(b, str) else b.html() for b in self.blocks)
        return f'<!doctype html><html><head><meta charset="utf-8"></head><body style="{STYLE["body"]}">{body}</body></html>\n'


def doc_of(body):
    """A Doc from a Doc or a plain string."""
    return body if isinstance(body, Doc) else Doc(body)
