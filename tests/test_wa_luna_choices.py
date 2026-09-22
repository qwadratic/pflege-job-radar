"""Offline tests for app/wa/luna/choices.py (TASK-121: server-side recovery of a typed reply into a
button id; TASK-122: gating WA_BRIDGE_SYNTHETIC_CONSENT). No network, no claude CLI: the matcher is
pure, and the only I/O is wa_messages rows in a throwaway sqlite file.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import choices as CH

PHONE = "+491701234567"

# The real brain.py qualification ladder (TASK-121 AC#2 names it explicitly).
URK_BUTTONS = [{"id": "urk:urkunde", "title": "Urkunde"},
               {"id": "urk:defizit", "title": "Defizitbescheid"},
               {"id": "urk:kenntnispruefung", "title": "Prüfung bestanden"}]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "BRAIN", "deterministic")
    monkeypatch.setattr(C, "SYNTHETIC_CONSENT", True)
    conn = ST.db()
    yield conn
    conn.close()


def _iso(hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(microsecond=0).isoformat()


def _offer(c, buttons, wamid="wab.o.1", phone=PHONE, hours_ago=0, kind="buttons"):
    ST.record_outbound(c, phone, wamid, "Frage?", kind=kind, meta={"action": "ask", "buttons": buttons})
    c.execute("update wa_messages set at=? where wamid=?", (_iso(hours_ago), wamid))
    c.commit()
    return wamid


def _reply(c, text, wamid="wab.i.1", phone=PHONE, hours_ago=0):
    assert ST.record_inbound(c, phone, wamid, text) is True
    c.execute("update wa_messages set at=? where wamid=?", (_iso(hours_ago), wamid))
    c.commit()
    return wamid


# --- pure matcher: table-driven over the consent pair and the question ladder (AC#2/#3) -----------

@pytest.mark.parametrize("typed, expected_id", [
    ("1", LB.CONSENT_YES_ID), ("1.", LB.CONSENT_YES_ID), ("1)", LB.CONSENT_YES_ID),
    ("ja", LB.CONSENT_YES_ID), ("Ja gerne", LB.CONSENT_YES_ID), ("JA GERNE", LB.CONSENT_YES_ID),
    ("2", LB.CONSENT_NO_ID), ("nein", LB.CONSENT_NO_ID), ("Nein danke", LB.CONSENT_NO_ID),
])
def test_the_consent_pair_resolves(typed, expected_id):
    button_id, _tier = CH.match(typed, LB.CONSENT_BUTTONS)
    assert button_id == expected_id


@pytest.mark.parametrize("typed", ["Prüfung", "Pruefung", "PRUEFUNG", "prüfung"])
def test_the_question_ladder_folds_umlauts(typed):
    button_id, tier = CH.match(typed, URK_BUTTONS)
    assert button_id == "urk:kenntnispruefung"
    assert tier == CH.TIER_PREFIX


def test_vielleicht_does_not_match_and_reaches_the_brain_unchanged():
    assert CH.match("vielleicht", LB.CONSENT_BUTTONS) == (None, None)


def test_an_ambiguous_prefix_does_not_match():
    buttons = [{"id": "a", "title": "Intensivstation"}, {"id": "b", "title": "Intern"},
               {"id": "c", "title": "Nachtdienst"}]
    assert CH.match("Inte", buttons) == (None, None)


def test_two_identical_folded_titles_never_match_even_at_the_exact_tier():
    buttons = [{"id": "a", "title": "Ja bitte"}, {"id": "b", "title": "JA BITTE"}]
    assert CH.match("Ja bitte", buttons) == (None, None)


def test_the_keyword_map_fires_only_on_two_button_offers():
    # "gerne" is a yes-word, but URK_BUTTONS has three buttons and none of their titles match it
    # at any other tier -- the keyword map must not step in just because a word is in its set.
    assert CH.match("gerne", URK_BUTTONS) == (None, None)


def test_the_keyword_map_does_fire_on_a_two_button_offer():
    buttons = [{"id": "housing:ja", "title": "Ja"}, {"id": "housing:nein", "title": "Nein"}]
    assert CH.match("gerne", buttons) == ("housing:ja", CH.TIER_KEYWORD)
    assert CH.match("nee", buttons) == ("housing:nein", CH.TIER_KEYWORD)


# --- recover_button_id: the offer has to be live (AC#5) --------------------------------------------

def test_a_bare_ordinal_resolves_against_the_offer_just_made(db):
    _offer(db, URK_BUTTONS)
    wamid = _reply(db, "1")
    assert CH.recover_button_id(db, PHONE, wamid, "1") == "urk:urkunde"


def test_an_offer_older_than_the_ttl_yields_no_match(db):
    _offer(db, URK_BUTTONS, hours_ago=49)
    wamid = _reply(db, "1")
    assert CH.recover_button_id(db, PHONE, wamid, "1") is None


def test_an_offer_inside_the_ttl_still_matches(db):
    _offer(db, URK_BUTTONS, hours_ago=47)
    wamid = _reply(db, "1")
    assert CH.recover_button_id(db, PHONE, wamid, "1") == "urk:urkunde"


def test_a_reply_to_an_older_offer_does_not_match(db):
    """A second inbound message sits between the offer and the reply being tested -- the offer is no
    longer the newest thing said, so it is retired exactly as a real button tap would be."""
    _offer(db, URK_BUTTONS, wamid="wab.o.1")
    _reply(db, "irgendwas anderes", wamid="wab.i.1")
    wamid = _reply(db, "1", wamid="wab.i.2")
    assert CH.recover_button_id(db, PHONE, wamid, "1") is None


def test_a_text_bubble_after_the_offer_also_retires_it(db):
    """The newest message must BE the buttons offer, not merely exist somewhere in history."""
    _offer(db, URK_BUTTONS, wamid="wab.o.1")
    ST.record_outbound(db, PHONE, "wab.o.2", "Einen Moment noch.", kind="text", meta={"action": "media_ack"})
    wamid = _reply(db, "1")
    assert CH.recover_button_id(db, PHONE, wamid, "1") is None


def test_no_prior_offer_at_all_yields_no_match(db):
    wamid = _reply(db, "1")
    assert CH.recover_button_id(db, PHONE, wamid, "1") is None


def test_an_unmatched_reply_yields_none_not_an_error(db):
    _offer(db, URK_BUTTONS)
    wamid = _reply(db, "vielleicht")
    assert CH.recover_button_id(db, PHONE, wamid, "vielleicht") is None


def test_a_match_records_the_tier_and_the_verbatim_token_in_message_meta(db):
    _offer(db, URK_BUTTONS)
    wamid = _reply(db, "Pruefung")
    assert CH.recover_button_id(db, PHONE, wamid, "Pruefung") == "urk:kenntnispruefung"
    stored = ST.message_by_wamid(db, wamid)
    rec = stored["meta"]["button_recovery"]
    assert rec == {"tier": CH.TIER_PREFIX, "button_id": "urk:kenntnispruefung",
                   "offer_wamid": "wab.o.1", "token": "Pruefung"}


# --- consent: tier gating and the flag (TASK-122) --------------------------------------------------

@pytest.mark.parametrize("typed", ["1", "1.", "ja", "Ja gerne", "JA GERNE"])
def test_consent_is_granted_only_by_tier_1_or_2(db, typed, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _offer(db, LB.CONSENT_BUTTONS)
    wamid = _reply(db, typed)
    _button_id, tier = CH.match(typed, LB.CONSENT_BUTTONS)
    got = CH.recover_button_id(db, PHONE, wamid, typed)
    if tier in CH.CONSENT_GRADE_TIERS:
        assert got == LB.CONSENT_YES_ID
    else:
        assert got is None


def test_a_prefix_match_never_grants_consent(db, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _offer(db, LB.CONSENT_BUTTONS)
    wamid = _reply(db, "Ja ger")   # a real, unique prefix of "Ja, gerne" -- tier 3, not tier 1/2
    button_id, tier = CH.match("Ja ger", LB.CONSENT_BUTTONS)
    assert button_id == LB.CONSENT_YES_ID and tier == CH.TIER_PREFIX   # sanity: the plain matcher would resolve it
    assert CH.recover_button_id(db, PHONE, wamid, "Ja ger") is None


def test_a_keyword_match_never_grants_consent(db, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _offer(db, LB.CONSENT_BUTTONS)
    wamid = _reply(db, "ok")
    assert CH.recover_button_id(db, PHONE, wamid, "ok") is None


def test_the_granted_consent_token_is_stored_verbatim(db, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _offer(db, LB.CONSENT_BUTTONS)
    wamid = _reply(db, "Ja gerne")
    assert CH.recover_button_id(db, PHONE, wamid, "Ja gerne") == LB.CONSENT_YES_ID
    rec = ST.message_by_wamid(db, wamid)["meta"]["button_recovery"]
    assert rec["token"] == "Ja gerne" and rec["tier"] == CH.TIER_EXACT_TITLE


@pytest.mark.parametrize("typed", ["1", "Ja gerne", "ja"])
def test_with_the_flag_off_no_typed_reply_ever_sets_consent(db, monkeypatch, typed):
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "SYNTHETIC_CONSENT", False)
    _offer(db, LB.CONSENT_BUTTONS)
    wamid = _reply(db, typed)
    assert CH.recover_button_id(db, PHONE, wamid, typed) is None


def test_a_non_consent_offer_is_unaffected_by_the_flag_or_the_tier_gate(db, monkeypatch):
    """The tier restriction is a consent-only rule -- an ordinary deterministic-brain button ladder
    resolves at any tier, luna brain or not."""
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "SYNTHETIC_CONSENT", False)
    _offer(db, URK_BUTTONS)
    wamid = _reply(db, "Pruefung")
    assert CH.recover_button_id(db, PHONE, wamid, "Pruefung") == "urk:kenntnispruefung"


def test_recovery_works_under_the_deterministic_brain_with_no_consent_ids_in_play(db):
    """C.BRAIN stays 'deterministic' (the fixture default): the consent-tier gate in
    recover_button_id is skipped entirely, and brain.py's own button ladder still resolves."""
    _offer(db, URK_BUTTONS)
    wamid = _reply(db, "Urkunde")
    assert CH.recover_button_id(db, PHONE, wamid, "Urkunde") == "urk:urkunde"


def test_recover_button_id_raises_on_an_unknown_wamid(db):
    with pytest.raises(RuntimeError):
        CH.recover_button_id(db, PHONE, "wab.i.never-stored", "1")
