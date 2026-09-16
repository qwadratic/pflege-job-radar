"""CV/Urkunde intake extraction primitives added for TASK-67: app/cv.py's vision path
(extract_text_vision/VisionClient) for images and scanned (text-layer-less) PDFs, and
analyse_candidate() for the WhatsApp harness (folds app.wa.store history in alongside
cv_text/urkunde_text, then reasons over it with analyse_llm -- the extraction path TASK-65
measured as the winner, see evals/cv/README.md). No network, no real `claude` CLI subprocess in
the default run: VisionClient/LLMClient are both fakes here, same seam convention as
tests/test_cv_eval_cases_llm.py. pdf_bytes_with_text() is reused by tests/test_wa_media_intake.py.
"""
import io
import json
import shutil
import time

import pytest

from app import cv as CV
from app import data as D
from app.wa import config as WC
from app.wa import store as ST

PHONE = "+491701234567"


# --- a tiny, hand-built, valid PDF (no external PDF-writer library on this host) -------------------

def pdf_bytes_with_text(text):
    """A minimal, syntactically valid one-page PDF with ``text`` as its only content-stream
    operation -- real enough for pdfplumber to extract it back out, small enough to inline in a
    test. ``text=""`` produces a PDF with an empty content stream: pdfplumber returns "" for it,
    the same "no text layer" signal a scanned Urkunde saved as PDF gives (see app/cv.py's own
    extract_text() docstring / TASK-67 notes)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 300 200] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = (f"BT /F1 12 Tf 10 150 Td ({text}) Tj ET".encode("latin-1") if text
              else b"")
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode("latin-1"))
        out.write(body)
        out.write(b"\nendobj\n")
    xref_offset = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.write(b"trailer\n" + f"<< /Size {n} /Root 1 0 R >>\n".encode("latin-1"))
    out.write(b"startxref\n" + f"{xref_offset}\n".encode("latin-1") + b"%%EOF")
    return out.getvalue()


def test_pdf_fixture_round_trips_through_extract_text():
    """Sanity on the fixture itself, not app/cv.py: a real PDF/1.4 file pdfplumber can read."""
    assert "Hallo Fixture" in CV.extract_text("x.pdf", pdf_bytes_with_text("Hallo Fixture"))
    assert CV.extract_text("x.pdf", pdf_bytes_with_text("")).strip() == ""


# --- vision extraction (images, and the scanned-PDF fallback) --------------------------------------

def test_extract_text_vision_happy_path_writes_and_cleans_up_a_temp_file():
    seen = {}

    def fake_call(file_path):
        import pathlib
        seen["path"] = file_path
        seen["existed_during_call"] = pathlib.Path(file_path).exists()
        seen["bytes_during_call"] = pathlib.Path(file_path).read_bytes()
        return "Urkunde: Pflegefachfrau, anerkannt"

    client = CV.VisionClient(call=fake_call)
    text = CV.extract_text_vision(b"fake-image-bytes", suffix=".png", client=client)
    assert text == "Urkunde: Pflegefachfrau, anerkannt"
    assert seen["existed_during_call"] is True
    assert seen["bytes_during_call"] == b"fake-image-bytes"
    assert seen["path"].endswith(".png")
    import pathlib
    assert not pathlib.Path(seen["path"]).parent.exists(), "the throwaway temp dir must not survive the call"


def test_extract_text_vision_raises_loudly_on_no_text_found():
    client = CV.VisionClient(call=lambda path: "NO_TEXT_FOUND")
    with pytest.raises(CV.NoReadableText, match="no readable text"):
        CV.extract_text_vision(b"blank-image", client=client)


def test_extract_text_vision_raises_loudly_on_an_empty_reply():
    """An empty reply is no answer, not the model's final NO_TEXT_FOUND: an ordinary (retryable) failure."""
    client = CV.VisionClient(call=lambda path: "   ")
    with pytest.raises(RuntimeError, match="no readable text") as raised:
        CV.extract_text_vision(b"blank-image", client=client)
    assert not isinstance(raised.value, CV.NoReadableText)


def test_vision_client_uses_restricted_not_tools_empty_and_grants_add_dir(monkeypatch):
    """Regression guard for the TASK-67 CLI-image-input spike: `--tools ""` was confirmed to also
    disable the Read tool the model needs to open the file at all (it answered "I don't have a
    tool available to read local files"), so this path must use `--restricted` alone, plus
    `--add-dir` on the temp file's own directory (Read is confined to cwd + added dirs, confirmed
    live: a `--restricted` call without `--add-dir` for a path outside cwd was refused with a
    permission_denials entry). If a future edit reintroduces `--tools ""` here, this must fail."""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw

        class P:
            returncode = 0
            stdout = json.dumps({"is_error": False, "result": "HELLO"})
            stderr = ""
        return P()

    # CV.VisionClient._live_call imports subprocess locally; patching the real module object
    # (the same one that local import binds, since modules are singletons in sys.modules) reaches it.
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CV.VisionClient()
    text = client.transcribe("/tmp/some/dir/upload.png")
    assert text == "HELLO"
    cmd = captured["cmd"]
    assert "--restricted" in cmd
    assert "--tools" not in cmd, "--tools disables the Read tool this path depends on (see spike note)"
    assert "--add-dir" in cmd
    add_dir = cmd[cmd.index("--add-dir") + 1]
    assert add_dir == "/tmp/some/dir"
    # TASK-95 review: Read/Glob/Grep also reach the cwd -- the service's cwd is the repo root, which holds
    # data/wa_documents/ and data/wa.sqlite. The cwd is the single-file temp dir, and no transcript is kept.
    assert captured["kw"]["cwd"] == add_dir
    assert "--no-session-persistence" in cmd


# --- analyse_candidate: chat history + cv_text/urkunde_text, TASK-65's winning extraction path ------

@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(WC, "SQLITE_PATH", tmp_path / "wa.sqlite")
    c = ST.db()
    yield c
    c.close()


@pytest.fixture()
def fixture_snapshot(monkeypatch):
    """match()/`_regierungsbezirke_for_cities` both call D.snapshot() -- an empty-but-valid board
    is enough here, this file is about extraction wiring, not job-matching scoring."""
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)


def _fake_llm_client(capture=None, **profile_kw):
    prof = {"roles": ["pflegefachkraft"], "departments": [], "qualifications": ["GuK"],
            "cities": [], "languages": [], "skills": [], "experience_years": 5}
    prof.update(profile_kw)

    def call(system_text, user_text):
        if capture is not None:
            capture["system"] = system_text
            capture["user"] = json.loads(user_text)
        return dict(prof)

    return CV.LLMClient(call=call)


def test_analyse_candidate_folds_cv_urkunde_text_and_chat_history(conn, fixture_snapshot):
    ST.record_inbound(conn, PHONE, "wamid.in.1", "Hallo, ich suche eine Stelle in München", kind="text")
    ST.record_outbound(conn, PHONE, "wamid.out.1", "Welche Qualifikation haben Sie?", kind="text")
    capture = {}
    client = _fake_llm_client(capture)

    result = CV.analyse_candidate(PHONE, conn, cv_text="Lebenslauf: 5 Jahre Intensivstation",
                                   urkunde_text="Urkunde: Pflegefachfrau", client=client)

    assert result["used_llm"] is True
    assert result["profile"]["roles"] == ["pflegefachkraft"]
    sent_text = capture["user"]["cv_text"]
    assert "--- CV ---" in sent_text and "Lebenslauf: 5 Jahre Intensivstation" in sent_text
    assert "--- Urkunde ---" in sent_text and "Urkunde: Pflegefachfrau" in sent_text
    hist = capture["user"]["chat_history"]
    assert {"direction": "in", "text": "Hallo, ich suche eine Stelle in München"} in hist
    assert {"direction": "out", "text": "Welche Qualifikation haben Sie?"} in hist


def test_analyse_candidate_works_with_only_one_of_cv_or_urkunde_text(conn, fixture_snapshot):
    capture = {}
    client = _fake_llm_client(capture)
    CV.analyse_candidate(PHONE, conn, cv_text="Lebenslauf: examinierte Pflegefachkraft", client=client)
    assert "--- CV ---" in capture["user"]["cv_text"]
    assert "--- Urkunde ---" not in capture["user"]["cv_text"]


def test_analyse_candidate_raises_without_any_document_text(conn, fixture_snapshot):
    with pytest.raises(ValueError, match="no readable text"):
        CV.analyse_candidate(PHONE, conn, client=_fake_llm_client())


def test_analyse_candidate_with_no_history_yet_still_works(conn, fixture_snapshot):
    """A candidate's very first message can already be a CV upload -- no prior chat at all."""
    capture = {}
    result = CV.analyse_candidate(PHONE, conn, cv_text="Lebenslauf: Altenpflegerin, 3 Jahre",
                                  client=_fake_llm_client(capture))
    assert capture["user"]["chat_history"] == []
    assert result["profile"]["roles"] == ["pflegefachkraft"]


def test_analyse_candidate_does_not_touch_the_public_api_cv_path():
    """analyse()/analyse_llm() (the public /api/cv upload, app/main.py) take no phone/conn/history
    at all -- this is purely additive, asserted by signature rather than by re-running app/main.py's
    own route tests here."""
    import inspect
    assert "phone" not in inspect.signature(CV.analyse).parameters
    assert "phone" not in inspect.signature(CV.analyse_llm).parameters
    assert {"phone", "conn"} <= set(inspect.signature(CV.analyse_candidate).parameters)


# --- the real thing: an actual `claude` CLI subprocess reading an actual image ----------------------
# llm-marked: excluded from the default offline run (-m "not llm"), same convention as
# tests/test_cv_eval_cases_llm.py -- costs real money/time, run explicitly:
#   pytest -q -m llm tests/test_cv_intake.py -k vision

@pytest.mark.llm
@pytest.mark.skipif(not shutil.which("claude"),
                    reason="'claude' is not on PATH -- CV vision extraction needs the Claude Code CLI")
def test_real_cli_vision_extraction_reads_a_real_image(tmp_path):
    """The TASK-67 spike question, asserted as a real regression test rather than a one-off manual
    check: can `claude -p` actually read image content and transcribe it? Renders a short line of
    text to a real PNG with PIL and asks extract_text_vision (the real VisionClient, real
    subprocess) to read it back."""
    PIL_Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw

    img = PIL_Image.new("RGB", (400, 100), color="white")
    ImageDraw.Draw(img).text((10, 40), "PFLEGE URKUNDE TEST 7788", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    text = CV.extract_text_vision(buf.getvalue(), suffix=".png")
    assert "7788" in text and "PFLEGE" in text.upper()
