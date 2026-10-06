"""One phone number, one identity: the two pure helpers every module keys a candidate on (TASK-348).

They lived in app/wa/meta.py, which is the Cloud API transport -- but a phone number is not a Meta
concept, and six non-transport callers already import them: api.py:149,418, luna/campaign.py:239,
luna/import_history.py:140, luna/migrate_candidates.py:46, luna/export_known_phones.py:42,
luna/test_threads.py:35. Three of those six -- migrate_candidates.py, export_known_phones.py,
test_threads.py -- import meta.py for nothing else at all. Moved here unchanged so a second
transport (TASK-349) does not drag meta.py in just to canonicalize a number.

meta.py re-exports both names, so every existing ``M.sender_e164`` / ``M.canonicalize_phone`` caller
keeps working -- and all six still reach the helpers that way. Repointing them at this module is out
of scope on purpose (TASK-348 AC#2: no call site is edited here); until someone does, those three
modules still import the Cloud API client to normalise a number.
"""
import re

from . import config as C


def sender_e164(sender):
    """Meta sends the wa_id as bare digits; the harness keys threads on +digits."""
    digits = re.sub(r"\D", "", str(sender or ""))
    return "+" + digits if digits else ""


#: ITU-T E.164 assigns exactly two 1-digit calling codes -- Zone 1 (NANP) and Zone 7 (Russia /
#: Kazakhstan) -- and nothing else under either leading digit, so matching the first digit alone is
#: exact, not a guess. Every other calling code is 2 or 3 digits; this repo carries no full E.164
#: length table to tell those two apart, so both mask to a 2-digit visible prefix. That never shows
#: MORE of the real subscriber number than a correct read would for a 2-digit-code country (Germany
#: +49, Austria +43 -- the two this harness's threads actually use), and for a 3-digit-code country
#: it shows one extra real digit as the "country code" rather than a bullet -- the wrong grouping,
#: never a wrong digit count for the last-4 promise this function exists to keep.
_ONE_DIGIT_CALLING_CODES = ("1", "7")


def phone_masked(phone):
    """Pro API masking (Ivan 2026-09-29): the calling code visible, every digit between it and the
    last 4 replaced by a bullet, grouped 3-then-4 for readability -- '+43 ••• •••• 1234'
    (docs/wa-dashboard.md). Never the autopilot demo's ``app/autopilot/seed.py:phone_masked`` (a
    different shape: fixed-position, last 2 digits only) -- this is the WA harness's own helper, for
    a contract that promises the last 4 and never less.

    None for an empty/unmasked-to-nothing input, so a caller reads "no phone" rather than a lone
    '+'. Short of 4 digits (should not happen for a real WhatsApp number), every digit is shown: there
    is nothing left to call "the last 4" without also exposing the whole thing, and this repo does not
    invent padding to hide a number that is not really there."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return None
    cc_len = min(1 if digits[0] in _ONE_DIGIT_CALLING_CODES else 2, max(len(digits) - 4, 0))
    cc, rest = digits[:cc_len], digits[cc_len:]
    tail = rest[-4:] if len(rest) > 4 else rest
    hidden = rest[:len(rest) - len(tail)]
    groups, i = [], 0
    for size in (3, 4, 4, 4, 4):   # the contract's own 3-then-4 cadence, repeating for a long number
        if i >= len(hidden):
            break
        groups.append("•" * min(size, len(hidden) - i))
        i += size
    parts = [f"+{cc}"] + groups + ([tail] if tail else [])
    return " ".join(p for p in parts if p)


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
