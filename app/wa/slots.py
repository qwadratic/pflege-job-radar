"""What the harness knows about a lead, and how it reads that out of a WhatsApp line.

A slot is one board filter plus the German words candidates actually use for it. The department
aliases and the role words are the production bot's list (apps/connectors/pflege_jobs_match.py
``_DEPT_ALIASES`` on tasker-dispatcher-01), so a lead who answers "ITS" or "OP" is understood the
same way here. ``urkunde`` is the one slot that is not a filter: it decides whether we can place the
person at all, so it is asked before the handover is offered.
"""
import re
import unicodedata

# slot -> the GET /api/jobs parameter it fills. `urkunde` is absent on purpose (see module docstring).
FILTER_PARAM = {"role": "role_class", "city": "city", "bezirk": "regierungsbezirk",
                "department": "department_hint", "hours": "employment_types", "housing": "housing"}

ROLE_WORDS = (
    ("ota", "ota_ata"), ("ata", "ota_ata"), ("operationstechnisch", "ota_ata"), ("anästhesietechnisch", "ota_ata"),
    ("praxisanleit", "praxisanleitung"),
    ("stationsleit", "leitung"), ("pflegedienstleit", "leitung"), ("bereichsleit", "leitung"), ("leitung", "leitung"),
    ("hebamme", "hebamme"), ("entbindungspfleg", "hebamme"),
    ("fachweiterbildung", "fachpflege"), ("fachkrankenpfleg", "fachpflege"), ("fachpflege", "fachpflege"),
    ("pflegeexpert", "apn_experte"), ("apn", "apn_experte"), ("pflegepädagog", "apn_experte"),
    ("pflegehelfer", "pflegehelfer"), ("pflegeassistenz", "pflegehelfer"),
    ("pflegefachkraft", "pflegefachkraft"), ("pflegefachfrau", "pflegefachkraft"), ("pflegefachmann", "pflegefachkraft"),
    ("krankenpfleger", "pflegefachkraft"), ("krankenschwester", "pflegefachkraft"), ("gesundheits", "pflegefachkraft"),
    ("examiniert", "pflegefachkraft"), ("pflege", "pflegefachkraft"),
)

DEPT_WORDS = (
    ("intensiv", "Intensiv/IMC"), ("imc", "Intensiv/IMC"), ("its", "Intensiv/IMC"),
    ("anästhes", "Anästhesie"), ("anaesthes", "Anästhesie"), ("narkose", "Anästhesie"),
    ("notaufnahme", "Notaufnahme"), ("zna", "Notaufnahme"), ("notfall", "Notaufnahme"),
    ("psychiatr", "Psychiatrie"),
    ("pädiatr", "Pädiatrie/Neonatologie"), ("paediatr", "Pädiatrie/Neonatologie"),
    ("kinderkranken", "Pädiatrie/Neonatologie"), ("neonat", "Pädiatrie/Neonatologie"), ("kinder", "Pädiatrie/Neonatologie"),
    ("geburt", "Geburtshilfe"), ("kreissaal", "Geburtshilfe"),
    ("onkolog", "Onkologie"), ("kardiolog", "Kardiologie"), ("neurolog", "Neurologie"), ("geriatr", "Geriatrie"),
    ("dialyse", "Dialyse/Nephrologie"), ("nephrolog", "Dialyse/Nephrologie"),
    ("chirurg", "Chirurgie/Orthopädie"), ("orthopäd", "Chirurgie/Orthopädie"), ("unfall", "Chirurgie/Orthopädie"),
    ("innere", "Innere Medizin"), ("reha", "Reha"), ("springer", "Springerpool"),
    ("ambulanz", "Ambulanz/Tagesklinik"), ("tagesklinik", "Ambulanz/Tagesklinik"),
    ("op", "OP"), ("operationsdienst", "OP"), ("operationssaal", "OP"), ("kreissaal", "Geburtshilfe"),
)

BEZIRKE = ("Oberbayern", "Niederbayern", "Oberpfalz", "Oberfranken", "Mittelfranken", "Unterfranken", "Schwaben")

YES = ("ja", "jawohl", "jup", "yes", "ok", "okay", "passt", "gerne", "genau", "richtig", "klar", "doch")
NO = ("nein", "nee", "ne", "nicht", "kein", "keine", "no", "brauche nicht", "habe schon")
STOP_WORDS = ("stop", "stopp", "stoppen", "abmelden", "abmeldung", "löschen", "loeschen", "unsubscribe",
              "keine nachrichten", "nicht mehr schreiben", "dsgvo")

# Urkunde / Anerkennung: the three states the production bot accepts, plus the two it must not place.
URKUNDE_WORDS = (
    ("urkunde", "urkunde"), ("anerkennung liegt", "urkunde"), ("anerkannt", "urkunde"),
    ("berufsurkunde", "urkunde"), ("erlaubnis", "urkunde"),
    ("defizitbescheid", "defizit"), ("defizit", "defizit"), ("bescheid", "defizit"),
    ("kenntnisprüfung bestanden", "kenntnispruefung"), ("kenntnisprüfung", "kenntnispruefung"),
    ("prüfung bestanden", "kenntnispruefung"),
    ("beantragt", "beantragt"), ("in bearbeitung", "beantragt"), ("läuft noch", "beantragt"),
    ("keine urkunde", "keine"), ("nichts davon", "keine"), ("noch nichts", "keine"),
)
URKUNDE_OK = ("urkunde", "defizit", "kenntnispruefung")


_UMLAUT = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def _fold(text):
    """Casefold and expand umlauts, so 'WÜRZBURG' and 'Wuerzburg' both become 'wuerzburg'."""
    s = str(text or "").casefold()
    for ch, rep in _UMLAUT:
        s = s.replace(ch, rep)
    return s


def _fold_bare(text):
    """The same, but with the umlaut dropped instead of expanded: 'Wurzburg' -> 'wurzburg'.

    Candidates type a town three ways ('Würzburg', 'Wuerzburg', 'Wurzburg'); comparing both folds
    catches all three without a fuzzy matcher that would also accept a different town.
    """
    s = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(ch for ch in s if not unicodedata.combining(ch)).replace("ß", "ss")


def _contains(text, needle):
    """Whole-word for one-word needles ('stop' must not fire on 'stopfen'), substring for phrases."""
    low, n = _fold(text), _fold(needle)
    if " " in n:
        return n in low
    return re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low) is not None


def is_stop(text):
    return any(_contains(text, w) for w in STOP_WORDS)


def says_yes(text):
    return any(_contains(text, w) for w in YES)


def says_no(text):
    return any(_contains(text, w) for w in NO)


def _first_match(text, table):
    """First entry whose needle is in the message. Needles of three characters or less ('op', 'its',
    'apn') must match as whole words -- 'op' inside 'Operationsdienst' is fine, inside 'Top' is not.
    Longer needles match as substrings, which is what makes 'intensiv' catch 'Intensivpflege'."""
    low, bare = _fold(text), _fold_bare(text)
    for needle, value in table:
        n, nb = _fold(needle), _fold_bare(needle)
        if len(n.strip()) <= 3:
            if _contains(text, n.strip()):
                return value
        elif n in low or nb in bare:
            return value
    return None


def read_role(text):
    return _first_match(text, ROLE_WORDS)


def read_department(text):
    return _first_match(text, DEPT_WORDS)


FAILED_WORDS = ("nicht bestanden", "durchgefallen", "nicht geschafft")


def read_disqualifier(text):
    """A failed Kenntnisprüfung is a hard no in the production playbook, whether or not it was asked
    about -- so this one phrase is read at any point in the conversation."""
    low = _fold(text)
    return "keine" if any(_fold(w) in low for w in FAILED_WORDS) else None


def read_urkunde(text):
    """-> 'urkunde' | 'defizit' | 'kenntnispruefung' | 'beantragt' | 'keine' | None.

    A bare "nein" answers the Urkunde question with 'keine'; a bare "ja" is deliberately *not*
    accepted as proof, because "ja" to "haben Sie die Urkunde?" and "ja" to a Defizitbescheid are
    different placements. The caller asks which one.
    """
    found = _first_match(text, URKUNDE_WORDS)
    if found:
        return found
    if says_no(text):
        return "keine"
    return None


def read_bezirk(text):
    low = _fold(text)
    for b in BEZIRKE:
        if _fold(b) in low:
            return b
    if "franken" in low:            # "Franken" alone is three Bezirke: too coarse to fill the slot.
        return None
    return None


def read_city(text, known_cities):
    """A town the board actually has postings in. Whole-word, longest match wins, so a message
    naming 'Bad Tölz' does not come back as 'Bad' and 'Neuburg/Donau' beats 'Neuburg'."""
    forms = ((_fold(text), _fold), (_fold_bare(text), _fold_bare))
    hit = None
    for city in known_cities:
        for low, fold in forms:
            c = fold(city)
            if not c or not re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", low):
                continue
            if hit is None or len(c) > len(fold(hit)):
                hit = city
    return hit


def read_hours(text):
    low = _fold(text)
    if "vollzeit" in low or "100%" in low or "voll" in low:
        return "vollzeit"
    if "teilzeit" in low or "50%" in low or "75%" in low or "teil" in low:
        return "teilzeit"
    if "minijob" in low:
        return "minijob"
    return None


HOUSING_WORDS = ("wohnung", "unterkunft", "zimmer", "appartement", "apartment")


def read_housing_explicit(text):
    """Housing named by word, at any point in the chat: 'brauche eine Wohnung' / 'keine Wohnung'."""
    low = _fold(text)
    if any(w in low for w in HOUSING_WORDS):
        return not says_no(text)
    return None


def read_housing(text):
    """The answer to "Brauchen Sie eine Wohnung?" -- a bare ja/nein counts here, and only here."""
    explicit = read_housing_explicit(text)
    if explicit is not None:
        return explicit
    if says_yes(text):
        return True
    if says_no(text):
        return False
    return None


def read_urkunde_explicit(text):
    """Urkunde/Defizit/Kenntnisprüfung named by word, at any point in the chat. A bare ja/nein is
    deliberately not read here: it is only an answer right after the question."""
    return _first_match(text, URKUNDE_WORDS)


def filters(slots):
    """Slots -> the GET /api/jobs query app/data.py:filter_jobs takes. Only open, live-verified rows:
    a lead must not be sent to a posting the verifier already found gone."""
    p = {"verify": "live", "sort": "-first_published"}
    for slot, param in FILTER_PARAM.items():
        v = slots.get(slot)
        if v is None or v == "":
            continue
        if slot == "housing":
            if v:
                p["housing"] = "1"
            continue
        p[param] = v
    return p
