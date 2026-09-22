"""One phone number, one identity: the two pure helpers every module keys a candidate on (TASK-115).

They lived in app/wa/meta.py, which is the Cloud API transport -- but a phone number is not a Meta
concept, and six non-transport callers already import them: api.py:149,418, luna/campaign.py:239,
luna/import_history.py:140, luna/migrate_candidates.py:46, luna/export_known_phones.py:42,
luna/test_threads.py:35. Three of those six -- migrate_candidates.py, export_known_phones.py,
test_threads.py -- import meta.py for nothing else at all. Moved here unchanged so a second
transport (TASK-116) does not drag meta.py in just to canonicalize a number.

meta.py re-exports both names, so every existing ``M.sender_e164`` / ``M.canonicalize_phone`` caller
keeps working -- and all six still reach the helpers that way. Repointing them at this module is out
of scope on purpose (TASK-115 AC#2: no call site is edited here); until someone does, those three
modules still import the Cloud API client to normalise a number.
"""
import re

from . import config as C


def sender_e164(sender):
    """Meta sends the wa_id as bare digits; the harness keys threads on +digits."""
    digits = re.sub(r"\D", "", str(sender or ""))
    return "+" + digits if digits else ""


def canonicalize_phone(raw, default_country_code=None):
    """One identity per human: '0170…', '0049170…', '+49170…' and '49170…' all become '+49170…'."""
    text = str(raw or "").strip()
    if not text:
        return ""
    cc = re.sub(r"\D", "", default_country_code or C.DEFAULT_COUNTRY_CODE) or "49"
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
    elif text.startswith("00"):
        digits = re.sub(r"\D", "", text[2:])
    else:
        digits = re.sub(r"\D", "", text)
        if digits.startswith("0") and len(digits) >= 10:
            digits = cc + digits[1:]
        elif len(digits) <= 11 and not digits.startswith(cc):
            digits = cc + digits
    return "+" + digits if digits else ""
