"""Bake the two map literals the public board inlines.

  GEO_BY     -- the real administrative outline of Bayern, from data/geo/bayern_vg2500.json
                (BKG VG2500; rebuild it with tools/build_geo_outline.py). Ring 0 is the border,
                any further ring is a hole -- Bayern has one, the Austrian exclave Jungholz --
                so the page fills the path with fill-rule:evenodd.
  GEO_TOWNS   -- every Bavarian town's coordinates, from data/geo/gemeinden_de.csv, the same
                municipality table pflege_jobs/geo.py resolves against, so the dots cannot
                disagree with what the pipeline geocoded. Keyed by normalised name and by
                unambiguous bare stem; a name two municipalities share is left out.

Usage: .venv/bin/python web/geo_shapes.py     # prints the two JS literals for web/index.template.html
"""
import csv
import json
import re
import unicodedata
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "geo" / "gemeinden_de.csv"
OUTLINE = ROOT / "data" / "geo" / "bayern_vg2500.json"
REGISTRY = ROOT / "data" / "registry" / "clinics.csv"
RB_DIGIT = {"Oberbayern": "1", "Niederbayern": "2", "Oberpfalz": "3", "Oberfranken": "4",
            "Mittelfranken": "5", "Unterfranken": "6", "Schwaben": "7"}
PARENS = re.compile(r"\(.*?\)")
_QUAL = r"a\.?\s?d\.?|i\.?\s?d\.?|an der|in der|am|im|in|bei|b\.|ob der|i\.|a\."
CONNECTOR = re.compile(r"\b(?:%s)\b" % _QUAL)          # keeps the river: "Neuburg a.d.Donau" -> "Neuburg Donau"
QUALIFIER = re.compile(r"(?:\s*/|\s+\b(?:%s)\b).*$" % _QUAL)   # drops it: -> "Neuburg"


def main():
    doc = json.loads(OUTLINE.read_text(encoding="utf-8"))
    rings = ";".join(" ".join("%g %g" % (x, y) for x, y in ring) for ring in doc["bayern"])

    # The lookup is keyed exactly the way the page normalises a town name -- lowercase, eszett to ss,
    # accents decomposed away, everything but a-z0-9 dropped -- so the two can never drift. The CSV's own
    # normalized_name column is NOT reused for this: it folds "ü" to "ue" where the browser's NFD folds it
    # to "u", and the two would never meet.
    #
    # Four spellings per municipality, because upstream writes all of them: the plain name, the name
    # without its "(Allgäu)" parenthetical, the name with the river/region connector dropped but the
    # qualifier kept ("Neuburg a.d.Donau" -> "neuburgdonau", which is how an ATS writes "Neuburg/Donau"),
    # and the bare stem. A key that two Bavarian municipalities would share is dropped, not guessed: a dot
    # in the wrong town is worse than a town openly listed as unplaceable.
    def norm(x):
        x = unicodedata.normalize("NFD", x.lower().replace("ß", "ss"))
        return re.sub(r"[^a-z0-9]", "", x)

    seen = defaultdict(set)
    with open(CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["source"] != "destatis" or not r["lat"] or r["land"] != "BY":
                continue
            xy = (round(float(r["lat"]), 3), round(float(r["lon"]), 3))
            base = r["gemeindename"].split(",")[0].strip()
            plain = PARENS.sub(" ", base)
            for k in (norm(base), norm(plain), norm(CONNECTOR.sub(" ", plain)), norm(QUALIFIER.sub(" ", plain))):
                if k:
                    seen[k].add(xy)
    keys = {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}
    dropped = len(seen) - len(keys)

    # Two Bavarian municipalities are called Amberg, and the one with the hospital is the Oberpfalz city.
    # The register knows which -- it carries the Regierungsbezirk per clinic -- and the municipality table
    # encodes the Regierungsbezirk in digit 3 of the ARS. So for every town in the hospital register the
    # ambiguity is resolved by a join, not by picking the bigger one: same join the pipeline itself makes.
    rb_rows = defaultdict(list)
    with open(CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["source"] == "destatis" and r["lat"] and r["land"] == "BY" and r["ars"]:
                base = r["gemeindename"].split(",")[0].strip()
                plain = PARENS.sub(" ", base)
                for k in {norm(base), norm(plain), norm(CONNECTOR.sub(" ", plain)), norm(QUALIFIER.sub(" ", plain))}:
                    rb_rows[(k, r["ars"][2])].append((round(float(r["lat"]), 3), round(float(r["lon"]), 3)))
    pinned, unresolved = 0, []
    with open(REGISTRY, newline="", encoding="utf-8") as f:
        for c in csv.DictReader(f):
            town, d = (c["town"] or "").strip(), RB_DIGIT.get(c["regierungsbezirk"])
            if not town or not d:
                continue
            # Most specific spelling first, and stop at the first one that identifies exactly one
            # municipality: "Aschau im Chiemgau" is unambiguous, its bare stem "Aschau" is not.
            cand = [norm(town), norm(PARENS.sub(" ", town)), norm(CONNECTOR.sub(" ", town)), norm(QUALIFIER.sub(" ", town))]
            hit = next((set(rb_rows[(k, d)]) for k in cand if k and len(set(rb_rows.get((k, d), []))) == 1), set())
            if len(hit) == 1:
                xy = next(iter(hit))
                for k in {norm(town), norm(PARENS.sub(" ", town)), norm(CONNECTOR.sub(" ", town))}:
                    if k and keys.get(k) != xy:
                        keys[k] = xy
                        pinned += 1
            elif norm(town) not in keys:
                unresolved.append(f"{town} ({c['regierungsbezirk']})")
    js_towns = "|".join("%s:%.3f,%.3f" % (k, la, lo) for k, (la, lo) in sorted(keys.items()))
    js_towns = "|".join("%s:%.3f,%.3f" % (k, la, lo) for k, (la, lo) in sorted(keys.items()))

    sys.stdout.write('const GEO_BY="%s";\n\n' % rings)
    sys.stdout.write("const GEO_ATTR=%s;\n\n" % json.dumps(doc["attribution"], ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write('const GEO_TOWNS="%s";\n' % js_towns)
    sys.stderr.write("Bayern %d ring(s)/%d points, %d town keys (%d ambiguous spellings dropped, "
                     "%d pinned by the hospital register), %.1f kB\n"
                     % (len(doc["bayern"]), sum(len(r) for r in doc["bayern"]), len(keys), dropped, pinned,
                        (len(rings) + len(js_towns)) / 1024))
    if unresolved:
        sys.stderr.write("register towns with no municipality match: " + ", ".join(sorted(set(unresolved))) + "\n")


if __name__ == "__main__":
    main()
