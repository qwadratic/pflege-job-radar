"""Build: inject public config into the SPA and agent docs.
Usage: SUPABASE_URL=... python web/build.py
Outputs: web/index.html (default at /), web/pro.html (/pro), web/login.html (/login), web/deck.html (/deck),
web/autopilot.html (/autopilot), web/skill/* (+ single-file bundle pflege-jobs.skill.md).
The app server (app/) serves web/ directly; there is no hosted copy, no netlify/vercel, no edge dashboard."""
import os, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
# Single place the public repo URL is configured; every page and doc interpolates it.
REPO_URL = os.environ.get("REPO_URL", "https://github.com/qwadratic/pflege-job-radar")


# The geodata attribution is an HTML fragment of its own (web/references.html) and is substituted into
# the page here: a licence notice must ship inside the page, never behind a fetch that can fail.
_references = pathlib.Path(__file__).resolve().parent / "references.html"
if not _references.exists():
    raise SystemExit(f"web/build.py: {_references} is missing — every page that shows the geometry has to carry "
                     f"the BKG attribution (__REFERENCES_GEO__); building without it would ship an unlicensed map.")
REFERENCES_GEO = _references.read_text(encoding="utf-8")


def fill(text):
    # No __SUPABASE_ANON_KEY__ substitution: no template, skill file or doc carries that placeholder any
    # more (the pages read the app API, and skill/scripts/query.py ships the anon key as its own default),
    # so requiring the env var only made the build fail for nothing.
    return (text.replace("__SUPABASE_URL__", os.environ["SUPABASE_URL"])
                .replace("__REFERENCES_GEO__", REFERENCES_GEO)
                .replace("__REPO_URL__", REPO_URL))


web = root / "web"
# (template, output). index.* is whatever is served at "/" -- today the light minimal page; pro.* is the dashboard at /pro.
PAGES = [("index.template.html", "index.html"), ("pro.template.html", "pro.html"),
         ("login.template.html", "login.html"), ("deck.template.html", "deck.html")]
sizes = {}
for _tpl, _out in PAGES:
    _txt = fill((web / _tpl).read_text(encoding="utf-8"))
    (web / _out).write_text(_txt, encoding="utf-8")
    sizes[_out] = len(_txt)
if (web / "autopilot.template.html").exists():                      # operator console, /autopilot (docs/autopilot.md)
    (web / "autopilot.html").write_text(fill((web / "autopilot.template.html").read_text(encoding="utf-8")), encoding="utf-8")

# Publish the agent skill: skill/ is the source of truth, web/skill/ is what gets served.
skill_src, skill_out = root / "skill", web / "skill"
skill_out.mkdir(parents=True, exist_ok=True)
for src in list(skill_src.glob("*.md")) + list(skill_src.glob("references/*.md")) + list(skill_src.glob("scripts/*.py")):
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
print("built " + ", ".join(f"web/{k} ({v} bytes)" for k, v in sizes.items()) + ", web/skill/")
