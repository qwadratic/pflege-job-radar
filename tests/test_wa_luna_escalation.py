"""app/wa/luna/escalation.py, directly: the closed list itself, not just its wiring into turn()
(that side is covered in tests/test_wa_luna_brain.py, tests/test_wa_luna_dialog_rules.py,
tests/test_wa_luna_soft_answers.py, app/wa/api.py's own use is in tests/test_wa_luna_campaign.py).

Ivan, 2026-09-22, right after the multi-city UAT round: "чтобы у меня был предсказуемый список
ситуаций, когда идет эскалация на человека... не было ситуаций, в которых человек вмешивается в
чат, а там реально какая-то херня, которую мог бы решить искусственный интеллект".
"""
import pytest

from app.wa.luna import escalation as ESC


def test_the_two_tiers_never_share_a_code():
    assert set(ESC.ESCALATE_CODES) & set(ESC.FLAG_CODES) == set(), (
        "a code must mean exactly one tier -- escalate, or flag, never both")


def test_every_code_has_a_label():
    for code in (*ESC.ESCALATE_CODES, *ESC.FLAG_CODES):
        assert code in ESC.LABELS and ESC.LABELS[code].strip(), f"{code!r} has no human-readable label"


def test_model_codes_are_a_subset_of_escalate_codes():
    assert set(ESC.MODEL_CODES) <= set(ESC.ESCALATE_CODES)
    # the two code-decided (never model-decided) triggers stay outside what the model may pick
    assert ESC.UNREAD_MEDIA not in ESC.MODEL_CODES
    assert ESC.GROUNDING_RULE_VIOLATED_TWICE not in ESC.MODEL_CODES


def test_record_escalation_sets_escalated_and_appends_the_code():
    card = {}
    ESC.record_escalation(card, ESC.PET_POLICY_QUESTION, "candidate has a dog")
    assert card["_escalated"] is True
    assert card["_escalation_codes"] == [ESC.PET_POLICY_QUESTION]
    assert card["_escalate_reason"] == f"{ESC.PET_POLICY_QUESTION}: candidate has a dog"
    ESC.record_escalation(card, ESC.UNREADABLE_ATTACHMENT, "corrupt PDF")
    assert card["_escalation_codes"] == [ESC.PET_POLICY_QUESTION, ESC.UNREADABLE_ATTACHMENT], (
        "a second real fact about the same turn is appended, never silently overwritten (TASK-156 F1)")
    assert card["_escalate_reason"] == (
        f"{ESC.PET_POLICY_QUESTION}: candidate has a dog; {ESC.UNREADABLE_ATTACHMENT}: corrupt PDF")


def test_record_escalation_rejects_a_code_outside_the_closed_set():
    with pytest.raises(RuntimeError, match="not one of the closed escalation codes"):
        ESC.record_escalation({}, "something_i_made_up")


def test_record_flag_never_sets_escalated():
    card = {}
    ESC.record_flag(card, ESC.DECLINE_CLASSIFIER_DISAGREEMENT, "classifier disagreed")
    assert "_escalated" not in card, "a flag must never read as an escalation"
    assert card["_flag_codes"] == [ESC.DECLINE_CLASSIFIER_DISAGREEMENT]
    assert "classifier disagreed" in card["_flags"]


def test_record_flag_rejects_an_escalate_code():
    """The two tiers are not interchangeable at the call site either -- an escalate code passed to
    record_flag (or vice versa) is a programming error, not a judgement call, and fails loudly."""
    with pytest.raises(RuntimeError, match="not one of the closed flag codes"):
        ESC.record_flag({}, ESC.PET_POLICY_QUESTION)


@pytest.mark.parametrize("code", ESC.MODEL_CODES)
def test_record_model_escalation_honours_every_model_code(code):
    card = {}
    honoured = ESC.record_model_escalation(card, code, "detail")
    assert honoured is True
    assert card["_escalated"] is True
    assert card["_escalation_codes"] == [code]


def test_record_model_escalation_refuses_an_unrecognized_code_without_raising():
    """The model tried to escalate for a reason outside the closed set -- refused, not fatal: the
    caller (luna_brain.py) still sends the turn's own reply, only the escalation itself is a no-op."""
    card = {}
    honoured = ESC.record_model_escalation(card, "a_reason_nobody_approved", "some detail")
    assert honoured is False
    assert "_escalated" not in card
    assert card["_flag_codes"] == [ESC.UNRECOGNIZED_ESCALATION_ATTEMPT]
    assert "a_reason_nobody_approved" in card["_flags"]


def test_record_model_escalation_with_no_code_at_all_is_also_refused():
    """A model that sets escalate_to_manager=true but forgets escalate_reason_code entirely must not
    silently succeed -- the code is what makes an escalation real, not the boolean."""
    card = {}
    honoured = ESC.record_model_escalation(card, None, "please escalate this")
    assert honoured is False
    assert "_escalated" not in card
    assert card["_flag_codes"] == [ESC.UNRECOGNIZED_ESCALATION_ATTEMPT]
