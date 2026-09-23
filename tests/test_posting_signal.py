from pflege_jobs.posting_signal import GENDER_MARKER


def test_matches_every_german_gendering_form_both_prior_copies_accepted():
    # union of crawlers.vendor_adapters.GENDER's and career_crawl.JOB_TEXT's forms (TASK-123)
    good = [
        "Pflegefachkraft (m/w/d)",       # parenthetical, vendor_adapters' original form
        "Pflegefachkraft (m/w/d/a)",     # career_crawl-only: the extra "a" gender letter
        "Pflegefachkraft m/w/d",         # career_crawl-only: bare, unparenthesized
        "Pfleger:in",                    # colon suffix, both copies
        "Pfleger*in",                    # asterisk suffix, both copies
        "Pfleger/in",                    # vendor_adapters-only: bare-slash suffix (TASK-90)
        "Pfleger/innen",                 # vendor_adapters-only: bare-slash suffix
        "Angestellte/r",                 # vendor_adapters-only: bare-slash suffix
    ]
    for title in good:
        assert GENDER_MARKER.search(title), title


def test_does_not_match_plain_prose_with_no_gender_marker():
    bad = ["Stellenangebote", "Impressum", "Wir suchen Verstärkung", "Pflegedienst"]
    for title in bad:
        assert not GENDER_MARKER.search(title), title


def test_vendor_adapters_and_career_crawl_import_the_same_compiled_pattern():
    from crawlers import vendor_adapters as va
    from pflege_jobs.sources import career_crawl as cc

    assert va.GENDER is GENDER_MARKER
    assert cc.JOB_TEXT is GENDER_MARKER
