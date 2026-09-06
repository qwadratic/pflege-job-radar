"""Build: inject public config into the dashboard -> web/index.html + edge/pflege-dashboard/index.ts.
Usage: SUPABASE_URL=... SUPABASE_ANON_KEY=... python web/build.py"""
import json, os, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
# Single place the public repo URL is configured; every page and doc interpolates it.
REPO_URL = os.environ.get("REPO_URL", "https://github.com/ivan-kotelnikov/pflege-jobs")


def fill(text):
    return (text.replace("__SUPABASE_URL__", os.environ["SUPABASE_URL"])
                .replace("__SUPABASE_ANON_KEY__", os.environ["SUPABASE_ANON_KEY"])
                .replace("__REPO_URL__", REPO_URL))


tpl = (root / "web" / "index.template.html").read_text(encoding="utf-8")
html = fill(tpl)
(root / "web" / "index.html").write_text(html, encoding="utf-8")
ct = (root / "web" / "collect.template.html").read_text(encoding="utf-8")
(root / "web" / "collect.html").write_text(fill(ct), encoding="utf-8")
(root / "web" / "llms.txt").write_text(fill((root / "web" / "llms.template.txt").read_text(encoding="utf-8")), encoding="utf-8")
(root / "web" / "vercel.json").write_text(json.dumps({"cleanUrls": True, "headers": [{"source": "/(.*)", "headers": [{"key": "Cache-Control", "value": "public, max-age=300"}]}]}, indent=1), encoding="utf-8")
# Docs page: rendered from docs/ARCHITECTURE.md so prose and page can never drift apart.
sys.path.insert(0, str(root))
from web.render_md import render as _render_md
doc_html = _render_md(fill((root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")))
docs_tpl = (root / "web" / "docs.template.html").read_text(encoding="utf-8")
(root / "web" / "docs.html").write_text(fill(docs_tpl).replace("__DOC__", doc_html), encoding="utf-8")

# Publish the agent skill: skill/ is the source of truth, web/skill/ is what gets served.
skill_src, skill_out = root / "skill", root / "web" / "skill"
skill_out.mkdir(parents=True, exist_ok=True)
for src in list(skill_src.glob("*.md")) + list(skill_src.glob("references/*.md")):
    (skill_out / src.name).write_text(fill(src.read_text(encoding="utf-8")), encoding="utf-8")
for f in skill_out.glob("*.md"):                      # fill any file already living only in web/skill
    txt = f.read_text(encoding="utf-8")
    if "__REPO_URL__" in txt:
        f.write_text(fill(txt), encoding="utf-8")
# Single-file bundle: SKILL.md + every reference concatenated, so an agent can load one URL. Generated
# here rather than maintained by hand -- it drifted out of date the moment the sources changed.
_ref_order = ["api.md", "data-model.md", "pipeline.md"]
_bundle = [fill((skill_src / "SKILL.md").read_text(encoding="utf-8")).rstrip()]
for _name in _ref_order:
    _p = skill_src / "references" / _name
    if _p.exists():
        _bundle.append("\n\n---\n\n" + fill(_p.read_text(encoding="utf-8")).rstrip())
(skill_out / "pflege-jobs.skill.md").write_text("\n".join(_bundle) + "\n", encoding="utf-8")

edge = root / "edge" / "pflege-dashboard"; edge.mkdir(parents=True, exist_ok=True)
(edge / "index.ts").write_text(
    '// pflege-dashboard: serves the static dashboard (public). Built by web/build.py — do not edit by hand.\n'
    'const HTML = ' + json.dumps(html, ensure_ascii=False) + ';\n'
    'Deno.serve(() => new Response(HTML, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=300" } }));\n',
    encoding="utf-8")
print("built web/index.html, web/docs.html, web/vercel.json, edge/pflege-dashboard/index.ts", len(html), "bytes")
