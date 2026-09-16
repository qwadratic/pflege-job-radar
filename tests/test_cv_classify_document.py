"""Offline tests for app/cv.py:classify_document (TASK-81) -- no real CLI, same LLMClient(call=...)
seam as tests/test_cv_intake.py."""
import json

import pytest

from app import cv as CV


def _client(**kw):
    return CV.LLMClient(call=lambda system_text, user_text: kw)


@pytest.mark.parametrize("doc_type,level", [
    ("urkunde", "fachkraft"), ("urkunde", "helfer"), ("auslaendisches_diplom", "unknown"), ("lebenslauf", "unknown"),
    ("defizitbescheid", "unknown"), ("aufenthaltstitel", "unknown"), ("dienstplan", "unknown"),
    ("other", "unknown"),
])
def test_classify_document_accepts_every_valid_combination(doc_type, level):
    out = CV.classify_document("irrelevant text",
                               client=_client(document_type=doc_type, certificate_level=level))
    assert out == {"document_type": doc_type, "certificate_level": level}


def test_classify_document_passes_the_text_to_the_model():
    seen = {}

    def call(system_text, user_text):
        seen["user"] = json.loads(user_text)
        return {"document_type": "urkunde", "certificate_level": "fachkraft"}

    CV.classify_document("Urkunde ueber die Erlaubnis zum Fuehren der Berufsbezeichnung",
                         client=CV.LLMClient(call=call))
    assert "Urkunde" in seen["user"]["document_text"]


def test_classify_document_truncates_very_long_text():
    seen = {}

    def call(system_text, user_text):
        seen["user"] = json.loads(user_text)
        return {"document_type": "lebenslauf", "certificate_level": "unknown"}

    CV.classify_document("x" * 20000, client=CV.LLMClient(call=call))
    assert len(seen["user"]["document_text"]) <= 8000


def test_classify_document_rejects_an_unknown_document_type():
    with pytest.raises(RuntimeError, match="not the expected shape"):
        CV.classify_document("text", client=_client(document_type="visa", certificate_level="unknown"))


def test_classify_document_rejects_an_unknown_certificate_level():
    with pytest.raises(RuntimeError, match="not the expected shape"):
        CV.classify_document("text", client=_client(document_type="urkunde", certificate_level="senior"))


def test_classify_document_rejects_a_non_dict_response():
    with pytest.raises(RuntimeError, match="not the expected shape"):
        CV.classify_document("text", client=CV.LLMClient(call=lambda s, u: "not a dict"))


def test_the_taxonomy_separates_the_german_urkunde_from_a_foreign_diploma():
    """TASK-96 review 2026-09-14: 'urkunde' used to read 'a foreign nursing qualification recognition
    certificate' and fachkraft included 'an equivalent foreign nursing degree', so a home-country diploma came
    back as urkunde/fachkraft and opened the documents gate as if it were the German Urkunde."""
    assert "auslaendisches_diplom" in CV.DOC_TYPES
    prompt = CV._CLASSIFY_SYSTEM_PROMPT
    assert "a GERMAN nursing licence issued by a German authority" in prompt
    assert "issued OUTSIDE Germany" in prompt and "even when it calls itself a certificate or Urkunde" in prompt
    assert "equivalent foreign nursing degree" not in prompt
    out = CV.classify_document("text", client=_client(document_type="auslaendisches_diplom",
                                                      certificate_level="unknown"))
    assert out == {"document_type": "auslaendisches_diplom", "certificate_level": "unknown"}


# --- the real classifier: synthetic document texts through the real `claude` CLI ------------------
# llm-marked (excluded from the offline run). Run explicitly:
#   pytest -q -m llm tests/test_cv_classify_document.py

_FOREIGN_DIPLOMAS = {
    "ukraine_junior_specialist": (
        "MINISTRY OF HEALTH OF UKRAINE. DIPLOMA of Junior Specialist. Series ZT No. 000000. This diploma certifies "
        "that Olena Testenko completed in 2014 the full course of study at a medical college, specialty "
        "Nursing, and was awarded the qualification: Nurse, Junior Specialist."),
    "philippines_bsn": (
        "Republic of the Philippines. Test University College of Nursing. This certifies that Maria Testos has "
        "satisfactorily completed the requirements for the degree of Bachelor of Science in Nursing. Given this "
        "5th day of June 2016."),
    "india_gnm_registration": (
        "Test State Nurses and Midwives Council. Certificate of Registration. This is to certify that Anu Testjose "
        "has been registered as a Registered Nurse and Registered Midwife (General Nursing and Midwifery) under "
        "the Nurses and Midwives Act. Registration No. 00000."),
}

_GERMAN_URKUNDEN = {
    "pflegefachfrau": (
        "Regierung von Oberbayern. URKUNDE über die Erlaubnis zum Führen der Berufsbezeichnung. Frau Olena "
        "Testenko, geboren am 01.02.1990 in Schytomyr, erhält aufgrund des Pflegeberufegesetzes (PflBG) mit "
        "Wirkung vom heutigen Tage die Erlaubnis, die Berufsbezeichnung Pflegefachfrau zu führen. München, den "
        "03.03.2025", "fachkraft"),
    "pflegefachhelferin": (
        "URKUNDE. Frau Maria Testos hat die staatliche Abschlussprüfung in der Pflegefachhilfe bestanden und ist "
        "berechtigt, die Berufsbezeichnung staatlich geprüfte Pflegefachhelferin zu führen. Berufsfachschule für "
        "Pflegefachhilfe, Augsburg, 20.07.2024", "helfer"),
}


@pytest.mark.llm
@pytest.mark.parametrize("name", sorted(_FOREIGN_DIPLOMAS))
def test_real_classifier_never_calls_a_foreign_diploma_the_urkunde(name):
    import shutil
    if not shutil.which(CV._LLM_CLAUDE_BIN):
        pytest.skip(f"{CV._LLM_CLAUDE_BIN!r} is not on PATH")
    out = CV.classify_document(_FOREIGN_DIPLOMAS[name])
    print(name, out)
    assert out["document_type"] == "auslaendisches_diplom", out


@pytest.mark.llm
@pytest.mark.parametrize("name", sorted(_GERMAN_URKUNDEN))
def test_real_classifier_still_reads_a_german_urkunde_and_its_level(name):
    import shutil
    if not shutil.which(CV._LLM_CLAUDE_BIN):
        pytest.skip(f"{CV._LLM_CLAUDE_BIN!r} is not on PATH")
    text, level = _GERMAN_URKUNDEN[name]
    out = CV.classify_document(text)
    print(name, out)
    assert out == {"document_type": "urkunde", "certificate_level": level}, out
