"""Central config: sources, precedence, slices, classification rules.
Everything a human or agent might tune lives here."""
import re

# --- Sources & precedence (lower = more authoritative). Mirrors pflege_jobs.sources in DB.
SOURCES = {
    "krankenhausplan": {"source_id": 10, "precedence": 1, "kind": "registry"},
    "employer_ats":    {"source_id": 20, "precedence": 2, "kind": "employer_ats"},
    "arbeitsagentur":  {"source_id": 30, "precedence": 3, "kind": "public_api"},
    "aggregator":      {"source_id": 40, "precedence": 4, "kind": "aggregator"},
}

# --- Arbeitsagentur Jobsuche API (search = v6, details = v4; verified 2026-09-05)
AA_SEARCH_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6"
AA_DETAILS_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
AA_API_KEY = "jobboerse-jobsuche"
AA_PAGE_SIZE = 100
AA_REGION = "Bayern"          # BUNDESLANDSUCHE
AA_ANGEBOTSART = {"ARBEIT": 1, "SELBSTAENDIG": 2, "AUSBILDUNG": 4, "PRAKTIKUM_TRAINEE": 34}

# Slices: union of these, deduped on referenznummer. berufsfeld = whole occupational field
# (complete), `was` = keyword slices for roles that sit outside the two fields.
AA_SLICES = [
    {"name": "kp_arbeit",     "berufsfeld": "Krankenpflege, Rettungsdienst und Geburtshilfe", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "kp_ausbildung", "berufsfeld": "Krankenpflege, Rettungsdienst und Geburtshilfe", "angebotsart": 4},
    {"name": "kp_praktikum",  "berufsfeld": "Krankenpflege, Rettungsdienst und Geburtshilfe", "angebotsart": 34},
    {"name": "ap_arbeit",     "berufsfeld": "Altenpflege", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "ap_ausbildung", "berufsfeld": "Altenpflege", "angebotsart": 4},
    {"name": "was_hebamme",   "was": "Hebamme", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_ota",       "was": "Operationstechnischer Assistent", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_ata",       "was": "Anästhesietechnischer Assistent", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_pdl",       "was": "Pflegedienstleitung", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_stl",       "was": "Stationsleitung", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_praxisanl", "was": "Praxisanleiter Pflege", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_apn",       "was": "Pflegeexperte", "angebotsart": 1, "zeitarbeit": "false"},
    {"name": "was_werkstud",  "was": "Werkstudent Pflege", "angebotsart": 1},
]

# --- Employer classification (clinic vs non-clinic). Both lists checked.
# Conflict matrix: clinic token + WEAK non-clinic group (verband, sonstige) -> clinic
#                  clinic token + STRONG non-clinic group (altenhilfe, ambulant, wohnen, agentur) -> unknown
WEAK_NON_CLINIC_GROUPS = {"verband", "sonstige"}
CLINIC_PATTERNS = [
    ("klinik",      r"klinik"),
    ("krankenhaus", r"krankenhaus|krankenhäuser|hospital\b|spital"),
    ("uniklinik",   r"universitätsklinikum|uniklinik|universitätsmedizin"),
    ("herz",        r"herzzentrum|herzchirurg"),
    ("bezirk",      r"bezirkskrankenhaus|bezirksklinik|\bkbo\b|\bmedbo\b|\bgebo\b"),
    ("chain",       r"\bsana\b|\bhelios\b|\basklepios\b|schön[- ]klinik|schoen[- ]klinik|\bameos\b|\bmedian\b|mediclin|\bvamed\b|\brhön\b|regiomed|\bromed\b|innklinikum|isar-amper|danuvius|\bbg klinikum\b|\bbgu\b"),
    ("kurklinik",   r"kurklinik|sanatorium|reha-?zentrum|rehazentrum|rehabilitationszentrum|reha-?fachklinik"),
    ("operator",    r"sozialstiftung bamberg|schwesternschaft münchen|schwesternschaft nürnberg|rotkreuz|medical park|passauer wolf|kirinus|johannesbad|m&i-fachklinik|dr\. becker|kinderzentrum|klinikverbund|kreiskrankenh|deutsches herzzentrum|thoraxzentrum|lungenzentrum|\boberberg\b|bg unfallklinik|schlossklinik|fachklinik"),
]
NON_CLINIC_PATTERNS = [
    ("altenhilfe",  r"senioren|altenheim|altenpflegeheim|pflegeheim|pflegezentrum|wohnstift|residenz|altenzentrum|altenhilfe|seniorenwohnen|pflegewohn|wohnpark|pflegestift|kursana|pro seniore|azurit|korian|alloheim|schönes leben|domicil"),
    ("ambulant",    r"ambulant|pflegedienst|sozialstation|tagespflege|hauskrankenpflege|häusliche|intensivpflege\b|heimbeatmung|betreuungsdienst|24[- ]stunden|deutschefachpflege|\bmvz\b|wundmanagement|wundzentrum|wundex"),
    ("wohnen",      r"wohngruppe|betreutes wohnen|wohngemeinschaft|hospiz"),
    ("verband",     r"\bawo\b|arbeiterwohlfahrt|caritasverband|caritas-verband|diakonisches werk|diakoniewerk|diakoniestation|johanniter|malteser|\basb\b|arbeiter-samariter|\bdrk\b|\bbrk\b|rotes kreuz|volkssolidarität|paritätisch|lebenshilfe|behindertenhilfe|heilpädagog|sozialwerk|sozialdienst"),
    ("brand_nc",    r"vitolus|vitanas|anthojo|münchenstift|rummelsberger|\bcurata\b|cosmea|linimed|aiutanda|floni\.care|pflegius|\bghd\b|\bbipg\b|renafan|burchard führer|dr\. krantz|pelikids|körperbehinderte|advita|promedica|pflege & hilfe daheim|hilfe im alter"),
    ("agentur",     r"personaldienst|personalservice|personalvermittlung|zeitarbeit|leasing|personalmanagement gmbh|staffing|recruit"),
    ("sonstige",    r"praxis|apotheke|bundeswehr|krankenkasse|pflegekasse|medizinischer dienst|\bmdk\b|jugendhilfe|kinderheim|\bkita\b|kindergarten|schule|akademie|hochschule|bildungs|labor|sanitätshaus|homecare|versicherung|ministerium|landratsamt|gesundheitsamt"),
]
LEGAL_FORMS = r"\b(gmbh|ggmbh|mbh|ag|kg|ohg|e\.?\s?v\.?|gbr|se|stiftung|gemeinnützige?|gemeinnuetzige?|& co\.?|und co\.?|kgaa|ek|e\.k\.)\b"

# --- Role classification: order matters (first match wins), evaluated on title + hauptberuf.
PFLEGE_TOKEN = r"pfleg|betreuungskraft|alltagsbegleit|\bstation(en)?\b|op-bereich|op-fachkr|op-kraft|funktionsdienst|intensivstation|notaufnahme|kreißsaal|krankenschwester|hebamme|entbindungs|\bota\b|\bata\b|operationstechn|anästhesietechn|anaesthesietechn|\bapn\b|\bnurse\b|stationsleit|bereichsleit|praxisanleit"
STRONG_PFLEGE_TITLE = r"pfleg|op-fachkr|krankenschwester|hebamme|entbindungs|\bota\b|\bata\b|operationstechn|anästhesietechn|anaesthesietechn|stationsleit|praxisanleit|\bnurse\b|\bapn\b"
NICHT_PFLEGE = r"facharzt|fachärzt|oberarzt|oberärzt|assistenzarzt|assistenzärzt|chefarzt|chefärzt|\barzt\b|ärztin\b|\bärzte\b|psycholog|psychotherapeut|bewegungstherapeut|sporttherapeut|rettungs|notfallsanit|sanitäter|arzthelfer|medizinische/?r? fachangestellte|\bmfa\b|\bmta\b|mtra|mtla|physiotherap|ergotherap|logopäd|heilerziehung|\berzieher|sozialpädag|hauswirtschaft|reinigung|\bkoch\b|köchin|medizincontroll|kodier|schulleit|niederlassungsleit|bildungsbegleit|restaurant|küche|gastronom|hol-? ?u(nd)?\.? ?bringe?dienst|bringdienst|patientenbegleit|patiententransport|\baemp\b|\bzsva\b|sterilgut|physician assistant|arztassistent|empfang|sekretariat|\bit-\b|haustechnik"
ROLE_RULES = [
    ("werkstudent_praktikum", r"werkstudent|praktik|\bfsj\b|bufdi|bundesfreiwillig|freiwilliges soziales|studentische|ferienjob|hospitation"),
    ("ausbildung",            r"\bausbildung\b|\bazubi|auszubildende|\(ausbildung\)|duales studium|dualstudium"),
    ("hebamme",               r"hebamme|entbindungspfleg"),
    ("ota_ata",               r"\bota\b|\bata\b|operationstechnische|anästhesietechnische|anaesthesietechnische"),
    ("praxisanleitung",       r"praxisanleit"),
    ("leitung",               r"pflegedienstleit|pflegedirekt|stationsleit|bereichsleit|wohnbereichsleit|teamleit|gruppenleit|einrichtungsleit|heimleit|abteilungsleit|funktionsleit|ambulanzleit|zentrumsleit|schichtleit|pflegeleit|pflegerische leitung|(?<![a-zäöüß])leitung\b|(?<![a-zäöüß])leiter(/in|\*in|in)?\b|\bpdl\b"),
    ("apn_experte",           r"\bapn\b|advanced practice|pflegeexpert|pflegewissenschaft|pädagog|paedagog|pflegemanage|qualitätsmanage|hygienefachkraft|hygienebeauftragte"),
    ("fachpflege",            r"op-fachkr[aä]ft|fachkrankenpfleg|fachaltenpfleg|fachpfleg|fachweiterbildung|fachkraft für intensiv|fachkraft für anästhesie|intensivpflegekraft|anästhesiepflegekraft|\bcritical care\b|kinderintensiv"),
    ("pflegehelfer",          r"pflegehelfer|pflegefachhelfer|pflege\(fach\)helfer|pflegeassist|krankenpflegehelfer|altenpflegehelfer|pflegehilfskraft|hilfskraft|pflegehilfe|betreuungskraft|alltagsbegleit|servicekraft|stationshilfe|pflegeassistenz|versorgungsassist|pflegefachassist|pflegeunterstützung|stationsassist|servicehelfer"),
    ("pflegefachkraft",       r"pflegefachkraft|pflegefachfrau|pflegefachmann|pflegefachperson|krankenpfleger|krankenschwester|kinderkrankenpfleg|altenpfleger|\bnurse\b|gesundheits- und|examinierte|pflegekraft|pflegefachkräfte|pfleger\b|pflegerin\b|dauernachtwache|nachtwache"),
]
# --- Intake policy: which role_classes are allowed into the database at all.
# The board serves *qualified, experienced* nursing staff only. Trainees (Ausbildung/Azubi), interns,
# working students and volunteers (FSJ/BFD) are classified so the rule is auditable, then dropped at the
# sink — they are never stored. `nicht_pflege` was already excluded; this widens the same gate.
# Enforced in pflege_jobs.sinks.only_pflege() (all sinks) and pflege_jobs.cli.cmd_inbox().
EXCLUDED_ROLE_CLASSES = {
    "nicht_pflege",            # not a nursing role at all (Arzt, MFA, Rettungsdienst, ...)
    "ausbildung",              # Ausbildung / Azubi / duales Studium -> no professional experience yet
    "werkstudent_praktikum",   # Werkstudent / Praktikum / FSJ / BFD / Hospitation
}

QUALIFICATION_HINT = [
    ("GKiK",           r"kinderkrankenpfleg"),
    ("GuK",            r"gesundheits- und krankenpfleg|krankenschwester|krankenpfleger"),
    ("Altenpflege",    r"altenpfleg"),
    ("generalistisch", r"pflegefachmann/-frau|pflegefachkraft|pflegefachfrau|pflegefachmann|pflegefachperson"),
]
DEPARTMENT_HINT = [
    ("Intensiv/IMC",           r"intensiv|\bits\b|\bimc\b|intermediate|beatmung|weaning"),
    ("Anästhesie",             r"anästhesie|anaesthesie|aufwachraum"),
    ("OP",                     r"\bop\b|operations|zentral-?op|opsaal|op-?bereich|op-?pflege"),
    ("Notaufnahme",            r"notaufnahme|\bzna\b|notfallzentrum|notfallpflege|schockraum"),
    ("Psychiatrie",            r"psychiatr|psychosomat|forensi|sucht|gerontopsych"),
    ("Pädiatrie/Neonatologie", r"pädiatr|paediatr|neonat|kinderklinik|kinderstation|kinder- und jugend"),
    ("Geburtshilfe",           r"geburtshilfe|kreißsaal|kreisssaal|wochenbett|entbindung"),
    ("Onkologie",              r"onkolog|hämatolog|haematolog|strahlen|palliativ"),
    ("Kardiologie",            r"kardiolog|herzkath|chest pain|herzchirurg"),
    ("Neurologie",             r"neurolog|stroke|schlaganfall|neurochirurg|frühreha"),
    ("Geriatrie",              r"geriatr|altersmedizin"),
    ("Dialyse/Nephrologie",    r"dialyse|nephrolog"),
    ("Chirurgie/Orthopädie",   r"chirurg|unfall|orthop|traumatolog|wirbelsäule"),
    ("Innere Medizin",         r"innere|internist|gastroenterolog|pneumolog|diabetolog"),
    ("Reha",                   r"\breha\b|rehabilitation"),
    ("Springerpool",           r"springer|\bpool\b|flexpool|flexteam"),
    ("Ambulanz/Tagesklinik",   r"ambulanz|tagesklinik|funktionsdienst|endoskopie|herzkatheter"),
]

# --- Description enrichment (phase 2, needs jobdetails)
HOUSING = r"personalwohn|personalunterkunft|personalappartement|personalapartment|wohnraum|wohnheim|dienstwohnung|mitarbeiterwohn|mitarbeiterapartment|betriebswohnung|unterstützung bei der wohnungssuche|hilfe bei der wohnungssuche|wohnungssuche|bezugsrecht|möblierte?s? (apartment|appartement|zimmer)|umzugskosten"
TARIFF = [
    ("TVöD", r"tvöd|tv-?öd|tvoed"), ("TV-L", r"\btv-?l\b"), ("AVR Caritas", r"avr[- ]caritas|avr-c\b|caritas.{0,30}\bavr\b|\bavr\b.{0,30}caritas"),
    ("AVR Diakonie", r"avr[- ]diakonie|avr-?bayern|avr\.?dd"), ("Haustarif", r"haustarif|hausvertrag|firmentarif"), ("AVR (unspecified)", r"\bavr\b"),
]
PAY_GRADE = r"(?:entgeltgruppe|eingruppierung|vergütung|tarif|tvöd|tv-l|avr|gehalt|bezahlung|nach)[^.\n]{0,60}?\b((?:p|kr|eg|e|s)\s?0?(\d{1,2})(?:\s?[/–-]\s?(?:p|kr|eg|e|s)?\s?0?(\d{1,2}))?[a-c]?)\b|\b((?:p|kr)\s?0?(\d{1,2})[a-c]?)\b[^.\n]{0,40}(?:tvöd|tv-l|avr|entgelt|tarif)"
PAY_TEXT = r"[^.\n]{0,80}(?:vergütung|entgelt|gehalt|tarif|tvöd|tv-l|avr)[^.\n]{0,120}"
REQ_HEAD = r"(?:ihr profil|das bringen sie mit|das bringst du mit|dein profil|anforderungen|wir erwarten|voraussetzungen|ihre qualifikation|deine qualifikation|was sie mitbringen|was du mitbringst|sie bringen mit|du bringst mit)"
REQ_STOP = r"(?:wir bieten|unser angebot|was wir bieten|das bieten wir|ihre aufgaben|deine aufgaben|aufgaben|benefits|kontakt|bewerbung|ansprechpartner|wir freuen uns)"
EXPERIENCE = r"((?:\d+|mehrjährig\w*|langjährig\w*|erste|einschlägig\w*|fundiert\w*)\s?(?:-?\s?jahr\w*)?\s?(?:berufs|praxis)?erfahrung[^.\n]{0,60})"
EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
LANGUAGE_REQ = r"\b(a2|b1|b2|c1)\b.{0,40}(deutsch|sprach)|deutsch.{0,40}\b(a2|b1|b2|c1)\b"
BONUS = r"willkommensprämie|wechselprämie|startprämie|starterprämie|antrittsprämie"
CHILDCARE = r"betriebskita|betriebskindergarten|kinderbetreuung|kita-?platz|kinderkrippe"
ANERKENNUNG = r"anerkennung|berufsanerkennung|internationale? pflegekr|ausländische|defizitbescheid|kenntnisprüfung"

def rx(p): return re.compile(p, re.IGNORECASE)
