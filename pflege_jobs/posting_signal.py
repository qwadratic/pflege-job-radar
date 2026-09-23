"""Shared "does this text carry a German job-posting gender marker" signal.

Used by the crawl layer (crawlers/vendor_adapters.py, pflege_jobs/sources/career_crawl.py) as one
discovery-time signal for "is this a real single posting's title/anchor text, not an index/listing/FAQ
page" -- never the final Pflege/role decision, which stays classify.classify_role()'s alone (see
pflege_jobs/section.py's docstring for the equivalent two-phase contract on nursing-section detection).

Previously duplicated independently in both call sites and drifted apart (confirmed live 2026-09-23,
TASK-123): vendor_adapters.py's copy gained a bare-slash suffix form ("Pfleger/in", "Pfleger/innen",
"Angestellte/r", added TASK-90) that career_crawl.py's copy never received, and career_crawl.py's copy
had its own bare-unparenthesized "m/w/d" form that vendor_adapters.py's copy never received. GENDER_MARKER
below is the union of every form either copy accepted, so neither board class regresses.
"""
import re

GENDER_MARKER = re.compile(
    r"\((?:m|w|d|x|i|gn|a)\s?[/|\\*]\s?(?:m|w|d|x|i|gn|a)(?:\s?[/|\\*]\s?(?:m|w|d|x|i|gn|a))?\)"  # (m/w/d) and friends
    r"|\b[mwd]/[mwd]/[mwdx]\b"     # bare m/w/d, no parens
    r"|[:*]in\b"                   # Pfleger:in, Pfleger*in
    r"|/(?:innen|in|r)\b",         # Pfleger/in, Pfleger/innen, Angestellte/r
    re.I,
)
