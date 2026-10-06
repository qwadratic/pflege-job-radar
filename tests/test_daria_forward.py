"""tools/daria_forward.py: the hand forward of an inbound mail to the operators carries a text part and an HTML part."""
import base64
import email
import email.policy
import re
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import daria_forward as F  # noqa: E402


def test_a_forward_has_a_text_part_an_html_table_of_the_header_lines_and_the_original_attached():
    orig = EmailMessage()
    orig["From"], orig["To"], orig["Cc"] = "Frau Meier <meier@klinik.example>", F.BOX, "pd@klinik.example"
    orig["Subject"], orig["Message-ID"] = "AW: Pflegekraft", "<a1@klinik.example>"
    orig.set_content("Bitte senden Sie die Unterlagen & Zeugnisse.\n")
    raw = orig.as_bytes()
    n, subject = F.notification({"mime_b64": base64.b64encode(raw).decode(), "receivedDateTime": "2026-10-05T07:30:00+00:00",
                                 "folder": "inbox", "junk": False}, ["op1@example.org", "op2@example.net"])
    assert subject == "AW: Pflegekraft" and n["To"] == "op1@example.org, op2@example.net"
    n = email.message_from_bytes(n.as_bytes(), policy=email.policy.default)                  # as the operators receive it
    text, page = n.get_body(("plain",)).get_content(), n.get_body(("html",)).get_content()
    assert re.search(r"^ +От +Frau Meier <meier@klinik\.example>$", text, re.M) and re.search(r"^ +Получено +05\.10\.2026 09:30 \(Берлин\)$", text, re.M)
    assert "Bitte senden Sie die Unterlagen & Zeugnisse." in text
    assert re.findall(r"<td[^>]*>(.*?)</td>", page)[:4] == ["От", "Frau Meier &lt;meier@klinik.example&gt;", "Кому", re.findall(r"<td[^>]*>(.*?)</td>", page)[3]]
    assert "Unterlagen &amp; Zeugnisse" in page
    (att,) = list(n.iter_attachments())
    assert att.get_filename() == "original.eml" and att.get_content()["Message-ID"] == "<a1@klinik.example>"
