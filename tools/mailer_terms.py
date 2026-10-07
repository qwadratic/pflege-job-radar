"""The parts of the terms letter that need nothing from the mailer (TASK-345.12.16): the subject and recipients of the answer,
the greeting-free facts taken from the clinic's mail, the clinic's name as a file name, the PDF made for the clinic.

Terms are the standard conditions every clinic gets when it asks for them (Ivan, 2026-10-07): a short letter in the clinic's own
thread and one PDF made for that clinic. The letter, the PDF's HTML and the date they are valid from are the campaign config's
"terms" block; this module only fills and prints them."""
import html
import re
import subprocess
from email.utils import getaddresses, parseaddr
from pathlib import Path

REPLY_PREFIX = re.compile(r"^\s*(?:(?:re|aw|antw|wg|fw|fwd)\s*:\s*)+", re.I)
PLACEHOLDER = re.compile(r"\[[A-ZÄÖÜ_]{3,}\]")
TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
CHROME = "google-chrome"                     # prints the PDF, as tools/mailer_announce.py does; the mailer runs under the system python


GENERAL = "Sehr geehrte Damen und Herren"
GREETING = re.compile(r"Sehr geehrte Frau (.+)|Sehr geehrter Herr (.+)")
TITLES = {"dr.", "prof.", "dr", "prof"}


class TermsError(Exception):
    """The PDF could not be made, or the clinic's mail has nothing to answer."""


def bare_subject(subject):
    """The clinic's subject without the reply prefixes ("AW: ", "Re: ", "WG: " ...), once."""
    return REPLY_PREFIX.sub("", " ".join(str(subject or "").split())).strip()


def reply_recipients(mail, own):
    """(To, Cc) of a reply to everyone: To the address the clinic's mail came from (Reply-To when it has one), Cc every other
    address on its To and Cc lines except ours. All lower case."""
    first = parseaddr(str(mail.get("Reply-To") or mail.get("From") or ""))[1].lower()
    if not first:
        raise TermsError("the clinic's mail has no sender address to answer")
    others = [a.lower() for _, a in getaddresses([str(v) for v in mail.get_all("To", []) + mail.get_all("Cc", [])]) if a]
    return [first], [a for a in dict.fromkeys(others) if a != own.lower() and a != first]


def thread_of(mail):
    """(Message-ID of the clinic's mail, References for the answer): the clinic's own References and its Message-ID last."""
    mid = str(mail.get("Message-ID") or "").strip()
    if not mid:
        raise TermsError("the clinic's mail has no Message-ID to answer in its thread")
    refs = re.findall(r"<[^>]+>", str(mail.get("References") or "") + " " + str(mail.get("In-Reply-To") or ""))
    return mid, list(dict.fromkeys(refs + [mid]))


def addressee(mail):
    """The name on the clinic's From line as "First Last" ("Mustermann, Karin (Musterklinik)" gives "Karin Mustermann"), "" when it has none."""
    name = parseaddr(str(mail.get("From") or ""))[0]
    name = re.sub(r"\s*\([^)]*\)", "", name).strip().strip('"')
    if not name or "@" in name:
        return ""
    last, comma, first = name.partition(",")
    return f"{first.strip()} {last.strip()}" if comma and first.strip() else name


def squash(v):
    return " ".join(str(v).split()).casefold()


def resolve_names(said, context, board_clinic, from_person):
    """What the letter and the PDF name, from what the classifier read in the whole thread (Ivan, 2026-10-07: the model picks
    them, they must not be garbage, and it must never fail). `said` is the classifier's JSON, `context` every text it saw (the
    whole thread, the From line, our own letter's greeting). Each value is kept only when the thread itself carries
    it, else it is replaced by a fixed fallback; the source says which: returns {"clinic": (name, "thread"|"board"),
    "person": (name, "thread"|"from-line"|"none"), "greeting": (line, "thread"|"general")}.
    clinic: the clinic's name as the thread writes it, else the board's name. person: the writer's name as the thread writes it
    (every word in it), else the name on the From line, else none. greeting: "Sehr geehrte Frau X", "Sehr geehrter Herr X" with
    X in the thread, else the general greeting; a gender is never guessed from a first name, the model takes it from words in the
    thread (Frau, Herr, a title, a feminine function) or answers with the general greeting."""
    seen = squash(context)
    words_in = lambda v: bool(v) and all(squash(w) in seen for w in v.split())
    clean = lambda k: " ".join(str(said.get(k) or "").split())
    clinic = clean("clinic_name")
    clinic = (clinic, "thread") if clinic and squash(clinic) in seen else (board_clinic, "board")
    person = clean("person_name")
    person = (person, "thread") if words_in(person) else ((from_person, "from-line") if from_person else ("", "none"))
    greeting = clean("greeting")
    m = GREETING.fullmatch(greeting)
    name = [w for w in ((m.group(1) or m.group(2)).split() if m else []) if w.casefold() not in TITLES]
    greeting = (greeting, "thread") if m and name and words_in(" ".join(name)) else (GENERAL, "general")
    return {"clinic": clinic, "person": person, "greeting": greeting}


def title_of(greeting):
    """"Frau" or "Herr" when the greeting names a person, else ""."""
    m = GREETING.fullmatch(greeting)
    return "" if not m else ("Frau" if m.group(1) else "Herr")


def file_slug(name):
    """The clinic's name as a file name part: umlauts spelled out, everything else but letters and digits an underscore."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name.translate(TRANSLIT)).strip("_")


def fill_pdf_html(template, clinic, who, stand):
    """The PDF's HTML for one clinic: [KLINIK], [EMPFAENGER_ZEILE] (a "z. Hd." line, nothing when the mail names nobody), [STAND]."""
    out = (template.replace("[KLINIK]", html.escape(clinic)).replace("[STAND]", html.escape(stand))
           .replace("[EMPFAENGER_ZEILE]", f"<div>z. Hd. {html.escape(who)}</div>" if who else ""))
    left = sorted(set(PLACEHOLDER.findall(out)))
    if left:
        raise TermsError(f"the PDF template has placeholders without a value: {left}")
    return out


def render_pdf(html_text, out):
    """Print `html_text` to the PDF file `out` with headless Chrome (the page's own @page rule sets A4 and no margin)."""
    out = Path(out).resolve()
    src = out.with_suffix(".html")
    src.write_text(html_text, encoding="utf-8")
    try:
        r = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                            "--virtual-time-budget=8000", f"--print-to-pdf={out}", src.as_uri()], capture_output=True, text=True)
    finally:
        src.unlink(missing_ok=True)
    if r.returncode != 0 or not out.exists():
        raise TermsError(f"the PDF was not made (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-400:]}")
    return out
