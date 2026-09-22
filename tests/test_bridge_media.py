"""Offline proof for what bridge/media.py does (TASK-131 round 6): content-addressed ids and
reading the handset's own listing -- nothing about WHO a file is from. Identity matching itself
lives in bridge/identity.py (tests/test_bridge_identity.py); see this module's own docstring for
the four time-based rounds that failed before it.
"""
from bridge import media as MD


def test_the_id_is_a_prefix_over_a_slice_of_the_sha256():
    assert MD.content_media_id("abc123" + "0" * 60) == "wab.m." + ("abc123" + "0" * 60)[:20]


def test_identical_bytes_from_two_different_handset_paths_mint_one_id():
    same_sha = MD.sha256_bytes(b"same bytes, two files")
    a = MD.content_media_id(same_sha)
    b = MD.content_media_id(same_sha)
    assert a == b


# --- parsing the find+stat listing ----------------------------------------------------------------
def test_a_stat_listing_parses_size_mtime_and_a_path_with_spaces_in_it():
    out = ("1234 1758534000 WhatsApp Documents/Anna Musterfrau Lebenslauf.pdf\n"
          "222 1758534010 WhatsApp Images/IMG-20260922-WA0007.jpg\n")
    files = MD.parse_stat_listing(out)
    assert files == {"WhatsApp Documents/Anna Musterfrau Lebenslauf.pdf": (1234, 1758534000),
                     "WhatsApp Images/IMG-20260922-WA0007.jpg": (222, 1758534010)}


def test_an_unreadable_line_is_skipped_not_fatal():
    out = "stat: cannot access 'x': No such file or directory\n1234 1758534000 ok.pdf\n\n"
    assert MD.parse_stat_listing(out) == {"ok.pdf": (1234, 1758534000)}


def test_kind_is_read_off_the_extension_not_the_folder():
    assert MD.kind_for_path("WhatsApp Documents/photo_forwarded_as_document.jpg") == "image"
    assert MD.kind_for_path("WhatsApp Images/Urkunde.pdf") == "document"
    assert MD.kind_for_path("WhatsApp Voice Notes/PTT-20260922.opus") == "audio"
    assert MD.kind_for_path("WhatsApp Documents/unknown.xyz") == MD.DEFAULT_KIND


def test_source_dir_is_the_top_level_whatsapp_folder():
    assert MD.source_dir_for_path("WhatsApp Documents/Anna Lebenslauf.pdf") == "WhatsApp Documents"
    assert MD.source_dir_for_path("WhatsApp Voice Notes/PTT-1.opus") == "WhatsApp Voice Notes"


# --- structurally absent (TASK-131 round 5, requirement 1 and 6) -----------------------------------
def test_no_matching_machinery_survives_in_this_module():
    """The inference machinery -- the window as a decision rule, the catch-up gate, the round-3
    confirmation remnants -- is not dormant, it is deleted. A dead guard left importable would teach
    the next person something here is still guarded; nothing is."""
    for name in ("link_files", "catchup_gate_open", "CATCHUP_SETTLE_SEC", "DEFAULT_LINK_WINDOW_SEC",
                "PendingMessage", "MediaLink", "UnresolvedMedia", "OUTCOME_AWAITING_CATCHUP",
                "OUTCOME_NO_WINDOW_CANDIDATE", "OUTCOME_NO_MATCH", "OUTCOME_AMBIGUOUS"):
        assert not hasattr(MD, name), f"{name} should not exist any more"
