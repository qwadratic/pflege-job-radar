"""Structural "nursing section" signal -- narrower and cheaper than classify.py's role classifier.

Two-phase contract every adapter should follow:
  1. Look for a nursing section/category/department signal on the vendor's own board: a taxonomy
     field returned per job by an API/feed (SmartRecruiters' department.label, Personio's
     <recruitingCategory>/<department>, dvinci's jobOpening.categories[].name, ...), a query
     parameter/distinct listing URL that filters server-side to that category, or a navigable
     link/menu item on the listing page whose visible text names the nursing department.
  2. If found, fetch/filter USING it -- either request the narrower URL/param directly, or (when the
     field comes for free on a listing/feed the adapter already fetches, e.g. Personio's XML,
     SmartRecruiters' JSON, dvinci's list.json) use it to prioritise/restrict which jobs get a full
     detail fetch. If nothing confident is found, fall back to the adapter's EXISTING full-board
     behaviour unchanged and let classify.classify_role() sort it out as it does today.

This module does NOT duplicate classify.py's role classification (nicht_pflege / pflegehelfer /
ausbildung / ...). It only answers a narrower question -- "does this category/department LABEL, or
this nav LINK TEXT, look like the nursing section of the board?" -- so an adapter can decide what to
fetch before any page body or title has even been read. The final admit/exclude decision for any job
that does get fetched is still classify_role()'s alone.
"""
import re

# Word-boundary-aware synonyms for "the nursing department" as vendors actually label it (English and
# German), collected from real career-site category taxonomies and nav labels surveyed 2026-09:
#   Personio recruitingCategory/department: "Pflege"
#   SmartRecruiters department.label:        "Pflegedienst"
#   dvinci jobOpening.categories[].name:      "Pflege- und Funktionsdienst"
#   rexx Berufsgruppe/Fachbereich option:     "Pflegedienst", "Pflege, Patientenmanagement & Dokumentation"
#   concludis (native widget) Berufsgruppe:   "Pflege"
#   concludis (TYPO3+Solr "nxmamajobs")       jobgroup facet: "Pflege"
#   mein-check-in position_group heading:     "Pflegedienst"
#   talention campaignProperties "bereich":   "Pflegepersonal"
#   generic WP nav ("münchen-klinik.de"):     "Pflegedienst" (menu text), URL slug "/jobs/pflege/"
#   English boards:                           "Nursing"
# Anchored on a nursing stem with a left word boundary (German compounds freely append a suffix --
# "Pflegefachkraft", "Pflegehelfer*in" -- so the right side stays open) so a category label that
# merely *ends in* "pflege" as part of an unrelated compound (grounds/vehicle/laundry maintenance --
# "Grünflächenpflege", "Fahrzeugpflege", "Wäschepflege" -- plausible facilities-department names in a
# hospital's Wirtschafts-/Versorgungsdienst group, though none were observed live in the 2026-09
# survey) does not match.
NURSING_SECTION_RX = re.compile(
    r"(?<![a-zA-ZäöüÄÖÜß])"
    r"(?:"
    r"pflege[a-zäöüß]*"                                 # Pflege, Pflegedienst, Pflegepersonal,
                                                         # Pflegefachkraft, Pflegehelfer(*in), ...
    r"|gesundheits?-?\s*(?:&|und)\s*krankenpflege"       # Gesundheits- und Krankenpflege
    r"|krankenpflege"
    r"|nursing"
    r")",
    re.IGNORECASE,
)

# Category/link text that contains the nursing stem but names something else entirely -- grounds,
# vehicle or textile "care", not patient care. None of these were seen on a real board in the 2026-09
# survey (every live "*pflege*" category/label found meant nursing); kept as an explicit reject list
# for the word-boundary discipline and in case a facilities-department board ever does surface one.
_FALSE_POSITIVE_RX = re.compile(
    r"grün(?:flächen|anlagen)?pflege|fahrzeugpflege|wäschepflege|textilpflege|gartenpflege|anlagenpflege",
    re.IGNORECASE,
)


def _is_nursing_label(label: str) -> bool:
    if not label:
        return False
    if _FALSE_POSITIVE_RX.search(label):
        return False
    return bool(NURSING_SECTION_RX.search(label))


def pick_nursing_category(categories):
    """categories: list[str] of category/department labels as returned by a vendor API/feed (e.g.
    SmartRecruiters' [p["department"]["label"] for p in postings], Personio's <recruitingCategory>
    values, dvinci's jobOpening.categories[].name). -> the first label matching NURSING_SECTION_RX
    (and not a false-positive compound), or None if none of them look like the nursing section."""
    for label in categories or []:
        if _is_nursing_label(label):
            return label
    return None


def job_confirmed_nursing(labels):
    """STRICT signal (for relaxing classify.classify_role's pflege_gate -- NOT the same question as
    _keep-style fetch gates elsewhere, which are lenient): does this ONE job's OWN category/department
    label(s) positively name the nursing section? Unlike a fetch gate, an untagged/missing label
    returns False here -- absence of a label must never be read as confirmation, only a confident
    match is. `labels`: a single label string, a list of label strings, or None/empty when the vendor
    didn't tag this particular job."""
    if isinstance(labels, str):
        labels = [labels] if labels else []
    return any(_is_nursing_label(l) for l in (labels or []) if l)


def pick_nursing_link(links):
    """links: list[(text, href)] pairs scraped from a listing page's nav/filter UI (menu items,
    <option>/<label> filter facets, footer "Berufsgruppe" dropdowns, ...). -> the href of the first
    pair whose visible text looks like the nursing section, or None.

    Rejects a link whose text is a compound false-positive for the domain (see _FALSE_POSITIVE_RX)
    even if it contains the "pflege" stem -- e.g. a facilities-department "Grünflächenpflege" link
    should not be mistaken for the nursing department.
    """
    for text, href in links or []:
        if href and _is_nursing_label(text):
            return href
    return None
