#!/usr/bin/env python3
"""Render project markdown docs to a static HTML site and (optionally) deploy it to nginx.

This is the repeatable way to publish docs on tasker-dispatcher-01. See the
"How to host docs" rule in Claude's memory for the whole procedure.

Layout it produces:
    <build>/index.html          grouped link list
    <build>/<slug>.html         one page per markdown file, with a TOC

Usage:
    python3 tools/docs_publish.py                 # render into data/docs-site/
    python3 tools/docs_publish.py --deploy        # render + copy to DOCROOT (needs sudo)
    python3 tools/docs_publish.py --list          # show what would be published

Adding a doc: append it to MANIFEST_RU (published) / MANIFEST_EN (source). Nothing else to change.
    --lang=en renders the English originals instead of the Russian caveman versions.
Requires the venv at /home/claude/.venvs/docs (python3 -m venv + pip install markdown pygments);
the system python is PEP 668-managed, do not pip into it.
"""
import os, re, sys, html, shutil, subprocess, datetime

REPO    = "/home/claude/repo/pflege-board"
BUILD   = os.path.join(REPO, "data/docs-site")
DOCROOT = "/var/www/pflege-docs"          # served by nginx as /pflege-docs/ (basic auth)
RU      = "data/email-analysis/out/ru"     # Russian caveman versions (published)
SITE_EN = "Pflege — internal docs"
SITE_RU = "Pflege — внутренние доки"

# Russian caveman versions are what gets published; the English originals below stay
# in the repo/backlog as the source of record (Ivan, 2026-09-18).
MANIFEST_RU = [
    (RU + "/flow-catalog.ru.md",    "Каталог флоу — переписка с клиниками",            "Результаты"),
    (RU + "/proposal.ru.md",        "Email-модуль — предложение по архитектуре",       "Результаты"),
    (RU + "/runbook.ru.md",         "Ранбук email-канала",                            "Результаты"),
    (RU + "/report-stage1.ru.md",   "Этап 1 — корпус, таксономия, флоу",               "Анализ"),
    (RU + "/report-stage2.ru.md",   "Этап 2 — книга сделок, теория против практики",   "Анализ"),
    (RU + "/report-stage2b.ru.md",  "Этап 2b — улики закрытия сделок",                 "Анализ"),
    (RU + "/ledgers.ru.md",         "Реестры — отказы, комплаенс, скорость ответа",    "Анализ"),
    (RU + "/decode-outreach.ru.md", "Разбор машины аутрича",                           "Анализ"),
    (RU + "/decode-pushback.ru.md", "Таксономия отказов клиник",                       "Анализ"),
    (RU + "/attachments.ru.md",     "Типология вложений и документов",                 "Анализ"),
    (RU + "/research-de.ru.md",     "Ресёрч — Direktvermittlung, гонорары, счета",     "Справка"),
]

# (path relative to REPO, title, group). Order inside a group is preserved.
MANIFEST_EN = [
    ("backlog/docs/email/doc-2 - Clinic-email-communication-—-flow-catalog-6-month-analysis.md",
     "Flow catalog — clinic email communication", "Deliverables"),
    ("backlog/docs/email/doc-3 - Email-module-—-architecture-proposal-Direktvermittlung-Rechnung.md",
     "Email module — architecture proposal", "Deliverables"),
    ("backlog/docs/email/doc-1 - Email-channel-runbook.md",
     "Email channel runbook", "Deliverables"),
    ("data/email-analysis/out/report_stage1.md",  "Stage 1 — corpus, taxonomy, first flow pass", "Analysis"),
    ("data/email-analysis/out/report_stage2.md",  "Stage 2 — deal book, theory vs practice",     "Analysis"),
    ("data/email-analysis/out/report_stage2b.md", "Stage 2b — closing evidence",                 "Analysis"),
    ("data/email-analysis/out/ledgers.md",        "Ledgers — bounce, compliance, response",      "Analysis"),
    ("data/email-analysis/out/decode_outreach.md","Decoded outreach machine",                    "Analysis"),
    ("data/email-analysis/out/decode_pushback.md","Clinic pushback taxonomy",                    "Analysis"),
    ("data/email-analysis/out/attachments.md",    "Attachments / document typology",             "Analysis"),
    ("data/email-analysis/research_de_placement.md",
     "Research — Direktvermittlung, fees, invoicing (DE law)", "Reference"),
]

CSS = """
*{box-sizing:border-box}
:root{--bg:#11151a;--panel:#161b22;--line:#273039;--fg:#d7dee6;--dim:#8b97a4;--acc:#6ea8fe;--warn:#e5a13a;--ok:#5fb87a;--code:#1c2129}
@media(prefers-color-scheme:light){:root{--bg:#f7f8fa;--panel:#fff;--line:#e2e6ea;--fg:#1d2733;--dim:#5b6673;--acc:#1c5fd0;--code:#f1f3f6}}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{position:sticky;top:0;z-index:10;background:var(--panel);border-bottom:1px solid var(--line);padding:.7rem 1.2rem;display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap}
header a.home{color:var(--acc);text-decoration:none;font-weight:600}
header .meta{color:var(--dim);font-size:.85rem}
.wrap{max-width:1180px;margin:0 auto;padding:0 1.2rem;display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:2.2rem}
@media(max-width:900px){.wrap{grid-template-columns:1fr}nav.toc{position:static!important;max-height:none!important;order:-1}}
main{min-width:0;padding:1.6rem 0 5rem}
nav.toc{position:sticky;top:64px;align-self:start;max-height:calc(100vh - 88px);overflow:auto;padding:1.4rem 0 2rem;font-size:.87rem}
nav.toc .t{color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-size:.72rem;margin-bottom:.5rem}
nav.toc ul{list-style:none;margin:0;padding-left:.8rem;border-left:1px solid var(--line)}
nav.toc li{margin:.28rem 0}
nav.toc a{color:var(--dim);text-decoration:none}
nav.toc a:hover{color:var(--acc)}
h1,h2,h3,h4{line-height:1.25;margin:1.8em 0 .6em}
h1{font-size:1.85rem;margin-top:.4em}
h2{font-size:1.35rem;border-bottom:1px solid var(--line);padding-bottom:.3em}
h3{font-size:1.1rem}
a{color:var(--acc)}
code{background:var(--code);padding:.12em .38em;border-radius:4px;font-size:.88em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:.9rem 1rem;overflow:auto}
pre code{background:none;padding:0}
blockquote{margin:1em 0;padding:.2em 1em;border-left:3px solid var(--warn);color:var(--dim)}
hr{border:0;border-top:1px solid var(--line);margin:2.2em 0}
.tablewrap{overflow-x:auto;margin:1.1em 0;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border-bottom:1px solid var(--line);padding:.5rem .7rem;text-align:left;vertical-align:top}
th{background:var(--panel);font-weight:600;position:sticky;top:0}
tbody tr:hover{background:rgba(110,168,254,.06)}
ul,ol{padding-left:1.4rem}
li{margin:.2em 0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:.9rem;margin:.8rem 0 2rem}
.card{display:block;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem;text-decoration:none;color:var(--fg)}
.card:hover{border-color:var(--acc)}
.card .n{font-weight:600;margin-bottom:.25rem}
.card .d{color:var(--dim);font-size:.85rem}
.group{color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-size:.74rem;margin:1.8rem 0 .2rem}
footer{color:var(--dim);font-size:.82rem;border-top:1px solid var(--line);padding:1.2rem 0;margin-top:2rem}
@media print{header,nav.toc{display:none}.wrap{display:block;max-width:none}body{background:#fff;color:#000}}
"""

PAGE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{title} — {site}</title><style>{css}</style></head><body>
<header><a class="home" href="./index.html">&larr; {site}</a><span class="meta">{meta}</span></header>
<div class="wrap"><main>{body}</main>{toc}</div>
<div class="wrap"><footer>{foot}</footer></div>
</body></html>"""


def slug(p):
    base = os.path.basename(p)
    base = re.sub(r"\.md$", "", base)
    base = re.sub(r"^doc-(\d+)\s*-\s*", r"doc\1-", base)
    base = re.sub(r"[^\w\-]+", "-", base, flags=re.UNICODE).strip("-").lower()
    return re.sub(r"-{2,}", "-", base)[:80]


def strip_frontmatter(text):
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


def render(md_text):
    import markdown
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list", "toc", "nl2br"],
                           extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(md_text)
    body = body.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")
    return body, md.toc


def build(lang="ru"):
    manifest = MANIFEST_RU if lang == "ru" else MANIFEST_EN
    site = SITE_RU if lang == "ru" else SITE_EN
    os.makedirs(BUILD, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    foot = (f"Внутренний документ. Собрано {stamp} через tools/docs_publish.py. Английский оригинал — в репозитории. Наружу не выносить."
            if lang == "ru" else
            f"Internal document. Generated {stamp} by tools/docs_publish.py — do not share outside the team.")
    pages, missing = [], []
    for rel, title, group in manifest:
        src = os.path.join(REPO, rel)
        if not os.path.exists(src):
            missing.append(rel)
            continue
        text = strip_frontmatter(open(src, encoding="utf-8").read())
        body, toc = render(text)
        first = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), "")
        out = slug(rel) + ".html"
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(src)).strftime("%Y-%m-%d")
        tochtml = f'<nav class="toc"><div class="t">On this page</div>{toc}</nav>' if toc.strip() else ""
        open(os.path.join(BUILD, out), "w", encoding="utf-8").write(PAGE.format(
            title=html.escape(title), site=html.escape(site), css=CSS, body=body, toc=tochtml,
            meta=f"{html.escape(rel)} · updated {mtime}", stamp=stamp, lang=lang, foot=foot))
        pages.append((group, title, out, first[:170], mtime))

    groups = []
    for g, t, o, d, m in pages:
        if g not in [x[0] for x in groups]:
            groups.append((g, []))
        dict(groups)[g].append((t, o, d, m))
    cards = []
    for g, items in groups:
        cards.append(f'<div class="group">{html.escape(g)}</div><div class="cards">')
        for t, o, d, m in items:
            cards.append(f'<a class="card" href="{o}"><div class="n">{html.escape(t)}</div>'
                         f'<div class="d">{html.escape(d)}</div><div class="d">updated {m}</div></a>')
        cards.append("</div>")
    idx_body = f"<h1>{html.escape(site)}</h1>" + "".join(cards)
    if missing:
        idx_body += "<p><em>Missing sources: " + html.escape(", ".join(missing)) + "</em></p>"
    open(os.path.join(BUILD, "index.html"), "w", encoding="utf-8").write(PAGE.format(
        title="Index", site=html.escape(site), css=CSS, body=idx_body, toc="",
        meta=(f"{len(pages)} документов" if lang == "ru" else f"{len(pages)} documents"),
        stamp=stamp, lang=lang, foot=foot))
    return pages, missing


def deploy():
    cmds = [["sudo", "-n", "mkdir", "-p", DOCROOT],
            ["sudo", "-n", "rsync", "-a", "--delete", BUILD + "/", DOCROOT + "/"],
            ["sudo", "-n", "chown", "-R", "www-data:www-data", DOCROOT],
            ["sudo", "-n", "chmod", "-R", "u=rwX,g=rX,o=", DOCROOT]]
    if not shutil.which("rsync"):
        cmds[1] = ["sudo", "-n", "cp", "-rT", BUILD, DOCROOT]
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        print(" ".join(c), "->", "ok" if r.returncode == 0 else "FAIL " + (r.stderr or "").strip()[:200])
        if r.returncode != 0:
            return False
    return True


if __name__ == "__main__":
    lang = "en" if "--lang=en" in sys.argv else "ru"
    if "--list" in sys.argv:
        for rel, title, group in (MANIFEST_RU if lang == "ru" else MANIFEST_EN):
            print(f"[{group}] {title}  <-  {rel}  {'' if os.path.exists(os.path.join(REPO, rel)) else '(MISSING)'}")
        sys.exit(0)
    pages, missing = build(lang)
    print(f"built {len(pages)} {lang.upper()} pages into {BUILD}" + (f"; MISSING {missing}" if missing else ""))
    if "--deploy" in sys.argv:
        print("deployed" if deploy() else "deploy failed — run the sudo commands manually")
