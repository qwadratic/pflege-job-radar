"""tools/mailer_terms.py: what the terms letter takes from the clinic's mail, and the PDF printed with headless Chrome."""
import email
import email.policy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mailer_terms as T  # noqa: E402


def mail(**headers):
    m = email.message_from_string("\n".join(f"{k.replace('_', '-')}: {v}" for k, v in headers.items()) + "\n\ntext\n", policy=email.policy.default)
    return m


def test_the_subject_loses_its_reply_prefixes_once():
    assert T.bare_subject("AW: Pflegekraft für Ihre Intensivstation") == "Pflegekraft für Ihre Intensivstation"
    assert T.bare_subject("Re: AW: WG:  Pflegekraft\n  für uns") == "Pflegekraft für uns"
    assert T.bare_subject("Pflegekraft") == "Pflegekraft" and T.bare_subject(None) == ""


def test_reply_recipients_answer_the_sender_and_copy_everyone_else_but_us():
    m = mail(From="Anna <A@Klinik.example>", To="Daria <daria@us.example>, b@klinik.example", Cc="c@klinik.example, a@klinik.example")
    assert T.reply_recipients(m, "DARIA@us.example") == (["a@klinik.example"], ["b@klinik.example", "c@klinik.example"])
    assert T.reply_recipients(mail(From="a@k.example", Reply_To="Secretariat <s@k.example>", To="daria@us.example"), "daria@us.example") == (["s@k.example"], [])
    with pytest.raises(T.TermsError, match="no sender address"):
        T.reply_recipients(mail(To="daria@us.example"), "daria@us.example")


def test_the_thread_is_the_clinics_references_and_its_message_id_last():
    m = mail(Message_ID="<r@k>", In_Reply_To="<b@us>", References="<a@us> <b@us>")
    assert T.thread_of(m) == ("<r@k>", ["<a@us>", "<b@us>", "<r@k>"])
    with pytest.raises(T.TermsError, match="no Message-ID"):
        T.thread_of(mail(From="a@k.example"))


@pytest.mark.parametrize("sender,name", [('"Mustermann, Karin (Musterklinik)" <n@k.example>', "Karin Mustermann"), ("Karin Mustermann <n@k.example>", "Karin Mustermann"),
                                         ('"Dr. Müller" <n@k.example>', "Dr. Müller"), ("n@k.example", ""), ("<n@k.example>", ""),
                                         ("Pflegedirektion (Klinik) <n@k.example>", "Pflegedirektion")])
def test_the_addressee_is_the_name_on_the_from_line_as_first_and_last(sender, name):
    assert T.addressee(mail(From=sender)) == name


def test_a_clinics_name_becomes_a_file_name_part():
    assert T.file_slug("Klinikum Nürnberg (Nord und Süd)") == "Klinikum_Nuernberg_Nord_und_Sued"
    assert T.file_slug("Musterklinik Musterstadt") == "Musterklinik_Musterstadt" and T.file_slug("St. Josef-Krankenhaus ß") == "St_Josef_Krankenhaus_ss"


def test_the_pdf_html_is_filled_escaped_and_complete():
    tpl = "<h1>[KLINIK]</h1>[EMPFAENGER_ZEILE]<p>[STAND]</p>"
    assert T.fill_pdf_html(tpl, "A & B <Klinik>", "Karin Mustermann", "06.10.2026") == "<h1>A &amp; B &lt;Klinik&gt;</h1><div>z. Hd. Karin Mustermann</div><p>06.10.2026</p>"
    assert T.fill_pdf_html(tpl, "Klinik", "", "06.10.2026") == "<h1>Klinik</h1><p>06.10.2026</p>"
    with pytest.raises(T.TermsError, match=r"\[KONDITION\]"):
        T.fill_pdf_html("[KONDITION] [KLINIK]", "Klinik", "", "x")


def test_render_pdf_prints_a_pdf_with_headless_chrome(tmp_path):
    out = T.render_pdf("<html><body><h1>Klinik A</h1><p>Stand 06.10.2026</p></body></html>", tmp_path / "t.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type/Page") >= 1
    assert not (tmp_path / "t.html").exists()


def test_a_printer_that_fails_raises_with_its_message(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "CHROME", "/bin/false")
    with pytest.raises(T.TermsError, match="the PDF was not made"):
        T.render_pdf("<html></html>", tmp_path / "t.pdf")
    assert not (tmp_path / "t.html").exists()


def said(**fields):
    return {"clinic_name": "", "person_name": "", "greeting": "", **fields}


CONTEXT = "schicken Sie mir bitte Ihre Konditionen.\nKarin Mustermann\nStellvertretende Pflegedirektorin\nMusterklinik GmbH, Pflegedirektion\n\"Mustermann, Karin (Musterklinik)\" Klinik Musterstadt"


def test_names_the_thread_carries_are_kept_with_the_source_thread():
    got = T.resolve_names(said(clinic_name="Musterklinik  GmbH", person_name="Karin Mustermann", greeting="Sehr geehrte Frau Mustermann"), CONTEXT, "Musterklinik Musterstadt", "Karin Mustermann")
    assert got == {"clinic": ("Musterklinik GmbH", "thread"), "person": ("Karin Mustermann", "thread"), "greeting": ("Sehr geehrte Frau Mustermann", "thread")}
    got = T.resolve_names(said(person_name="Karin Mustermann", greeting="Sehr geehrter Herr Dr. Mustermann"), CONTEXT, "Board", "")
    assert got["greeting"] == ("Sehr geehrter Herr Dr. Mustermann", "thread")


@pytest.mark.parametrize("greeting", ["", "Hallo Karin", "Sehr geehrte Frau Meier", "Sehr geehrte Frau", "Sehr geehrte Frau Dr.", "Guten Tag Frau Mustermann", "sehr geehrte Frau Mustermann"])
def test_a_greeting_that_is_no_known_form_or_names_nobody_in_the_thread_becomes_the_general_one(greeting):
    assert T.resolve_names(said(greeting=greeting), CONTEXT, "Board", "")["greeting"] == (T.GENERAL, "general")


def test_a_clinic_or_person_the_thread_does_not_carry_falls_back_in_a_fixed_order():
    got = T.resolve_names(said(clinic_name="Hogwarts", person_name="Max Mustermann"), CONTEXT, "Musterklinik Musterstadt", "Karin Mustermann")
    assert got["clinic"] == ("Musterklinik Musterstadt", "board") and got["person"] == ("Karin Mustermann", "from-line")
    assert T.resolve_names(said(), CONTEXT, "Board", "")["person"] == ("", "none")
    assert T.resolve_names({}, CONTEXT, "Board", "")["clinic"] == ("Board", "board")
    assert T.resolve_names(said(clinic_name="Board"), CONTEXT, "Board", "")["clinic"] == ("Board", "board")   # the board's name is no thread evidence


def test_the_title_comes_from_the_greeting():
    assert (T.title_of("Sehr geehrte Frau Mustermann"), T.title_of("Sehr geehrter Herr Dr. X"), T.title_of(T.GENERAL)) == ("Frau", "Herr", "")
