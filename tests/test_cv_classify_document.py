"""Offline tests for app/cv.py:classify_document (TASK-81) -- no real CLI, same LLMClient(call=...)
seam as tests/test_cv_intake.py."""
import json

import pytest

from app import cv as CV


def _client(**kw):
    return CV.LLMClient(call=lambda system_text, user_text: kw)


@pytest.mark.parametrize("doc_type,level", [
    ("urkunde", "fachkraft"), ("urkunde", "helfer"), ("lebenslauf", "unknown"),
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
