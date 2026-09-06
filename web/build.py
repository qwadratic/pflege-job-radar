"""Build: inject public config into the SPA and agent docs.
Usage: SUPABASE_URL=... SUPABASE_ANON_KEY=... python web/build.py
Outputs: web/index.html, web/collect.html, web/skill/* (+ single-file bundle pflege-jobs.skill.md).
The app server (app/) serves web/ directly; there is no hosted copy, no netlify/vercel, no edge dashboard."""
import os, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
# Single place the public repo URL is configured; every page and doc interpolates it.
REPO_URL = os.environ.get("REPO_URL", "https://github.com/qwadratic/pflege-job-radar")


def fill(text):
    return (text.replace("__SUPABASE_URL__", os.environ["SUPABASE_URL"])
                .replace("__SUPABASE_ANON_KEY__", os.environ["SUPABASE_ANON_KEY"])
                .replace("__REPO_URL__", REPO_URL))


web = root / "web"
html = fill((web / "index.template.html").read_text(encoding="utf-8"))
(web / "index.html").write_text(html, encoding="utf-8")
(web / "collect.html").write_text(fill((web / "collect.template.html").read_text(encoding="utf-8")), encoding="utf-8")

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
print("built web/index.html, web/collect.html, web/skill/", len(html), "bytes")
