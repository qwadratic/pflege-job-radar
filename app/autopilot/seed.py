"""Deterministic synthetic dataset for the autopilot PoC (docs/autopilot.md).

    python -m app.autopilot.seed --reset      # rebuild data/autopilot.sqlite

Anchored on the virtual clock db.DEFAULT_POLICY['sim_now'] and random.Random(42): two machines produce the same demo.
Candidate names, phones and e-mails are invented (nationalities that are actually recruited into German nursing);
clinic names, towns, KeZ ids and open postings come from the real registry (app.data snapshot, fallback: registry CSV)
and are cached in two extra tables (registry_clinics, registry_postings) so the console never depends on the
Supabase load being finished.
"""
import argparse
import random
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config as A
from .. import data as D
from . import db
from . import matching as M

TZ = ZoneInfo("Europe/Berlin")
EXTRA_SCHEMA = """
create table if not exists registry_clinics (
  clinic_id text primary key, name text, town text, regierungsbezirk text, landkreis text, fachrichtungen text default '[]',
  beds integer, jobs_open integer default 0, careers_url text, versorgungsstufe text, traegerart text);
create table if not exists registry_postings (
  posting_id integer primary key, clinic_id text, title text, role_class text, department_hint text, qualification_hint text,
  city text, url text, first_published text);
"""

STAGES = ["new", "contacted", "qualifying", "docs_pending", "qualified", "matching", "profile_sent", "interview_scheduling",
          "interview_scheduled", "interviewed", "offer", "placed"]
STAGE_WEIGHTS = [16, 18, 16, 15, 12, 8, 9, 5, 4, 3, 3, 4]          # early stages weigh more
STATES = ["greeting", "consent", "collect_basics", "collect_location", "collect_docs", "docs_review", "qualified", "matching",
          "profile_sent", "interview_prep", "awaiting_feedback", "placed"]
STAGE_TO_STATES = {"new": ["greeting", "consent"], "contacted": ["consent", "collect_basics"], "qualifying": ["collect_basics", "collect_location"],
                   "docs_pending": ["collect_docs", "collect_docs", "docs_review"], "qualified": ["qualified"], "matching": ["matching"],
                   "profile_sent": ["profile_sent"], "interview_scheduling": ["interview_prep"], "interview_scheduled": ["interview_prep"],
                   "interviewed": ["awaiting_feedback"], "offer": ["awaiting_feedback"], "placed": ["placed"]}
LOST_REASONS = ["anderes Angebot angenommen", "kein Deutsch B2", "Anerkennung abgelehnt", "Opt-out (STOP)", "nicht erreichbar",
                "Gehaltsvorstellung zu hoch", "kein Umzug nach Bayern", "Visum abgelehnt"]
THREAD_STATES = ["draft", "intro_sent", "awaiting_reply", "interested", "scheduling", "scheduled", "feedback_pending", "closed_won", "closed_lost", "no_response"]
THREAD_WEIGHTS = [2, 5, 9, 6, 6, 5, 3, 3, 3, 4]
DOC_KINDS = ["cv", "education_cert", "anerkennung", "language_cert", "work_permit", "references"]
DEPARTMENTS = ["Intensiv/IMC", "OP", "Chirurgie/Orthopädie", "Notaufnahme", "Psychiatrie", "Anästhesie", "Kardiologie", "Neurologie",
               "Geriatrie", "Innere Medizin", "Onkologie", "Pädiatrie/Neonatologie", "Geburtshilfe", "Dialyse/Nephrologie"]
ROLE_POOL = ["pflegefachkraft"] * 10 + ["fachpflege"] * 3 + ["pflegehelfer"] * 2 + ["ota_ata", "hebamme", "praxisanleitung", "leitung"]
QUALI = {"pflegefachkraft": ["Pflegefachkraft (generalistisch)", "Gesundheits- und Krankenpflegerin", "Gesundheits- und Krankenpfleger", "Altenpflegerin"],
         "fachpflege": ["Fachkrankenpflege Intensiv/Anästhesie", "Fachkrankenpflege OP", "Gesundheits- und Krankenpflegerin"],
         "pflegehelfer": ["Pflegehelferin (1-jährig)", "Krankenpflegehelfer"], "ota_ata": ["Operationstechnische Assistentin", "Anästhesietechnischer Assistent"],
         "hebamme": ["Hebamme (B.Sc.)"], "praxisanleitung": ["Gesundheits- und Krankenpflegerin, Praxisanleitung"],
         "leitung": ["Gesundheits- und Krankenpfleger, Stationsleitung"]}
PLZ_PREFIX = {"Oberbayern": ["80", "81", "82", "83", "84", "85"], "Niederbayern": ["84", "94"], "Oberpfalz": ["92", "93"], "Oberfranken": ["95", "96"],
              "Mittelfranken": ["90", "91"], "Unterfranken": ["97"], "Schwaben": ["86", "87", "89"]}
NAMES = {
    "Rumänien": (["Andreea", "Ioana", "Mihaela", "Elena", "Cristina", "Alexandru", "Bogdan", "Raluca", "Gabriela", "Florin", "Adriana", "Cătălin"],
                 ["Popescu", "Ionescu", "Dumitru", "Stan", "Munteanu", "Radu", "Constantin", "Marin", "Dobre", "Neagu"]),
    "Indien": (["Priya", "Anjali", "Neha", "Deepak", "Rahul", "Sneha", "Aparna", "Jeslin", "Anu", "Vishnu", "Merin", "Jithin"],
               ["Nair", "Thomas", "Joseph", "Sharma", "Varghese", "Kumar", "Pillai", "George", "Mathew", "Kurian"]),
    "Philippinen": (["Maria Cristina", "Jenalyn", "Rowena", "Marjorie", "John Paul", "Kristine", "Aileen", "Mark Anthony", "Rhea", "Joanna"],
                    ["Santos", "Reyes", "dela Cruz", "Bautista", "Villanueva", "Garcia", "Mendoza", "Ramos", "Aquino"]),
    "Türkei": (["Ayşe", "Elif", "Zeynep", "Merve", "Emre", "Büşra", "Fatma", "Murat", "Selin", "Hakan"],
               ["Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Yıldız", "Aydın", "Öztürk"]),
    "Bosnien": (["Amina", "Selma", "Emir", "Lejla", "Adnan", "Ajla", "Mirza", "Edina", "Kenan", "Nejra"],
                ["Hodžić", "Begić", "Mujić", "Halilović", "Kovačević", "Delić", "Softić"]),
    "Tunesien": (["Amira", "Mariem", "Yasmine", "Mohamed", "Ahmed", "Nour", "Sami", "Rania", "Khalil", "Ines"],
                 ["Ben Ali", "Trabelsi", "Gharbi", "Jelassi", "Ben Salah", "Mansouri", "Haddad"]),
    "Vietnam": (["Hoa", "Linh", "Mai", "Thu", "Hương", "Minh", "Đức", "Anh", "Ngọc", "Trang"],
                ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng"]),
    "Deutschland": (["Katharina", "Julia", "Sabine", "Lena", "Markus", "Stefan", "Anna", "Melanie", "Tobias", "Christina"],
                    ["Müller", "Schmidt", "Huber", "Bauer", "Wagner", "Hofmann", "Maier", "Fischer", "Weber", "Schneider"]),
}
ORIGIN_POOL = ["Rumänien"] * 5 + ["Indien"] * 4 + ["Philippinen"] * 4 + ["Türkei"] * 3 + ["Bosnien"] * 3 + ["Tunesien"] * 2 + ["Vietnam"] * 2 + ["Deutschland"] * 3
ABROAD_CITY = {"Rumänien": ("Cluj-Napoca", "Iași", "Timișoara"), "Indien": ("Kochi", "Kottayam", "Bengaluru"), "Philippinen": ("Manila", "Cebu City", "Davao"),
               "Türkei": ("Istanbul", "Izmir", "Ankara"), "Bosnien": ("Sarajevo", "Tuzla", "Zenica"), "Tunesien": ("Tunis", "Sfax", "Sousse"),
               "Vietnam": ("Hanoi", "Đà Nẵng", "Hồ-Chí-Minh-Stadt")}
CONTACTS = [("Frau Huber", "Pflegedirektion"), ("Herr Brandl", "Pflegedienstleitung"), ("Frau Dr. Seidl", "Personalabteilung"), ("Frau Öztürk", "Recruiting"),
            ("Herr Wimmer", "Pflegedirektion"), ("Frau Kraus", "Personalentwicklung"), ("Herr Reiter", "Stationsleitung Intensiv"), ("Frau Lang", "Personalabteilung"),
            ("Herr Fuchs", "Pflegedienstleitung"), ("Frau Berger", "Recruiting"), ("Frau Schuster", "Pflegedirektion"), ("Herr Maier", "Personalabteilung")]
AVATAR_COLORS = ["#9DE146", "#C8F1FF", "#F2C94C", "#D82434", "#7B61FF", "#F28C28", "#3DDC97", "#E056A0"]
CAMPAIGN_SLUGS = ["pflege-muenchen", "pflege-nuernberg", "intensiv-oberpfalz", "nurses-augsburg-en", "anerkennung-bayern", "asistente-wuerzburg-ro"]


# --- time helpers (shared with engine/api) --------------------------------------------------------------
def parse(s):
    if isinstance(s, datetime):
        return s
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def iso(d):
    return d.astimezone(TZ).isoformat(timespec="seconds")


def sim_now(c=None):
    return parse(db.policy(c)["sim_now"])


def init():
    db.init()
    with db._lock, db.db() as c:
        c.executescript(EXTRA_SCHEMA)


# --- registry ------------------------------------------------------------------------------------------
def load_registry_source(wait=60):
    """(clinics, jobs) from the live snapshot; falls back to the registry CSV (no postings) when the snapshot is empty."""
    clinics, jobs = [], []
    try:
        snap = D.snapshot(wait=wait)
        clinics, jobs = list(snap.get("clinics") or []), list(snap.get("jobs") or [])
    except Exception as e:                                                   # network down: keep the seed working
        print("snapshot unavailable:", e, file=sys.stderr)
    if not clinics:
        for r in D.registry_csv_rows():
            if not (r.get("clinic_id") or "").strip().isdigit():
                continue
            clinics.append({**r, "fachrichtungen": [x for x in (r.get("fachrichtungen") or "").replace(",", "|").split("|") if x],
                            "beds": int(r.get("beds") or 0), "jobs_open": 0})
    clinics.sort(key=lambda c: str(c["clinic_id"]))
    jobs.sort(key=lambda j: j.get("posting_id") or 0)
    return clinics, jobs


def cache_registry(c, clinics, jobs):
    c.execute("delete from registry_clinics")
    c.execute("delete from registry_postings")
    for cl in clinics:
        c.execute("insert or replace into registry_clinics values(?,?,?,?,?,?,?,?,?,?,?)",
                  (str(cl["clinic_id"]), cl.get("name"), cl.get("town"), cl.get("regierungsbezirk"), cl.get("landkreis"), db.j(cl.get("fachrichtungen") or []),
                   cl.get("beds") or 0, cl.get("jobs_open") or 0, cl.get("careers_url"), cl.get("versorgungsstufe"), cl.get("traegerart")))
    for jb in jobs:
        if not jb.get("clinic_id"):
            continue
        c.execute("insert or replace into registry_postings values(?,?,?,?,?,?,?,?,?)",
                  (jb["posting_id"], str(jb["clinic_id"]), jb.get("title"), jb.get("role_class"), jb.get("department_hint"), jb.get("qualification_hint"),
                   jb.get("city"), jb.get("external_url"), jb.get("first_published")))


def registry(c):
    """Cached (clinics, jobs) in the snapshot shape used by matching.py."""
    clinics = [dict(r) for r in c.execute("select * from registry_clinics order by clinic_id")]
    for cl in clinics:
        cl["fachrichtungen"] = db.uj(cl["fachrichtungen"], [])
    jobs = [dict(r) for r in c.execute("select * from registry_postings order by posting_id")]
    return clinics, jobs


# --- text corpus --------------------------------------------------------------------------------------------
TEMPLATES = [
    ("whatsapp", "de", "greeting", "Begrüßung + Einwilligung", None,
     "Hallo {{first_name}}, ich bin Luna vom Recruiting-Team. Schön, dass Sie sich melden! Damit ich Ihnen passende Stellen in Bayern zeigen kann, "
     "stelle ich Ihnen ein paar kurze Fragen. Ihre Daten nutzen wir nur für die Vermittlung (DSGVO). Sind Sie einverstanden? Antworten Sie einfach mit JA.",
     ["first_name"]),
    ("whatsapp", "de", "consent", "Einwilligung nachfassen", None,
     "Kurze Nachfrage, {{first_name}}: Darf ich Ihre Angaben für die Stellensuche speichern? Ein JA genügt – Sie können jederzeit STOP schreiben.", ["first_name"]),
    ("whatsapp", "de", "collect_basics", "Ausbildung & Erfahrung", None,
     "Super, danke! Welche Ausbildung haben Sie (z. B. Pflegefachkraft, GuK, Altenpflege) und wie viele Jahre Berufserfahrung? In welchem Bereich haben Sie zuletzt gearbeitet?",
     []),
    ("whatsapp", "de", "collect_location", "Wohnort & Radius", None,
     "Danke! Wo wohnen Sie aktuell (PLZ oder Ort) und wie weit würden Sie zur Arbeit fahren? Ab wann könnten Sie anfangen?", []),
    ("whatsapp", "de", "collect_docs", "Unterlagen anfordern", None,
     "Fast geschafft, {{first_name}}. Bitte schicken Sie mir Ihren Lebenslauf und Ihr Examenszeugnis als PDF oder Foto. Falls vorhanden auch Anerkennungsbescheid und Sprachzertifikat.",
     ["first_name"]),
    ("whatsapp", "de", "doc_reminder", "Erinnerung Unterlagen", None,
     "Hallo {{first_name}}, mir fehlt noch: {{missing_docs}}. Können Sie es mir heute schicken? Dann kann ich Ihr Profil den Kliniken vorstellen.", ["first_name", "missing_docs"]),
    ("whatsapp", "de", "docs_review", "Unterlagen erhalten", None,
     "Vielen Dank, alles angekommen. Ich prüfe die Unterlagen und melde mich bis morgen mit einer Rückmeldung.", []),
    ("whatsapp", "de", "qualified", "Profil vollständig", None,
     "Gute Nachrichten, {{first_name}}: Ihr Profil ist vollständig. Ich suche jetzt passende Kliniken in {{region}} und melde mich, sobald ich Vorschläge habe.",
     ["first_name", "region"]),
    ("whatsapp", "de", "profile_sent", "Profil an Klinik gesendet", None,
     "Ich habe Ihr anonymisiertes Profil (ohne Name und Telefonnummer) an {{clinic_name}} in {{clinic_town}} geschickt. Sobald die Klinik antwortet, melde ich mich.",
     ["clinic_name", "clinic_town"]),
    ("whatsapp", "de", "share_posting", "Stelle teilen", None,
     "{{first_name}}, diese Stelle könnte passen: {{posting_title}} bei {{clinic_name}} in {{clinic_town}}. Details: {{url}} – Soll ich Ihr Profil dort vorstellen?",
     ["first_name", "posting_title", "clinic_name", "clinic_town", "url"]),
    ("whatsapp", "de", "interview_prep", "Terminvorschlag", None,
     "{{clinic_name}} möchte Sie kennenlernen! Vorgeschlagen ist {{slot}} ({{format}}). Passt das für Sie?", ["clinic_name", "slot", "format"]),
    ("whatsapp", "de", "interview_reminder", "Erinnerung Gespräch", None,
     "Erinnerung: Ihr Gespräch mit {{clinic_name}} ist {{slot}}. Halten Sie bitte Ihre Unterlagen bereit. Viel Erfolg!", ["clinic_name", "slot"]),
    ("whatsapp", "de", "awaiting_feedback", "Feedback nach Gespräch", None,
     "Wie ist das Gespräch mit {{clinic_name}} gelaufen? Ich frage parallel bei der Klinik nach.", ["clinic_name"]),
    ("whatsapp", "de", "follow_up", "Nachfassen (keine Antwort)", None,
     "Hallo {{first_name}}, ich wollte kurz nachhören – sind Sie noch an einer Stelle in Bayern interessiert? Eine kurze Antwort genügt.", ["first_name"]),
    ("whatsapp", "de", "nurture", "Nurture (dormant)", None,
     "Hallo {{first_name}}, es gibt neue Stellen in {{region}}, die zu Ihrem Profil passen könnten. Wenn Sie wieder suchen, schreiben Sie mir einfach.",
     ["first_name", "region"]),
    ("whatsapp", "en", "greeting", "Greeting + consent (EN)", None,
     "Hi {{first_name}}, I'm Luna from the recruiting team. To show you matching positions in Bavaria I need to ask a few short questions. "
     "We only use your data for placement (GDPR). Is that okay? Just reply YES.", ["first_name"]),
    ("whatsapp", "en", "collect_docs", "Request documents (EN)", None,
     "Almost done, {{first_name}}. Please send me your CV and your nursing diploma as PDF or photo. If you have them: recognition notice and German certificate.",
     ["first_name"]),
    ("whatsapp", "en", "follow_up", "Follow-up (EN)", None,
     "Hi {{first_name}}, just checking in – are you still interested in a nursing job in Bavaria? A short reply is enough.", ["first_name"]),
    ("whatsapp", "ro", "greeting", "Salut + consimțământ (RO)", None,
     "Bună {{first_name}}, sunt Luna din echipa de recrutare. Ca să vă arăt posturi potrivite în Bavaria, vă pun câteva întrebări scurte. "
     "Datele sunt folosite doar pentru plasare (GDPR). Sunteți de acord? Răspundeți cu DA.", ["first_name"]),
    ("whatsapp", "ro", "collect_docs", "Documente (RO)", None,
     "Aproape gata, {{first_name}}. Vă rog să-mi trimiteți CV-ul și diploma de asistent medical ca PDF sau poză.", ["first_name"]),
    ("email", "de", "clinic_intro", "Klinik: Profil vorstellen", "Pflegefachkraft für {{clinic_name}} – anonymisiertes Profil {{initials}}",
     "Sehr geehrte/r {{contact_name}},\n\nfür Ihre ausgeschriebene Stelle „{{posting_title}}“ möchte ich Ihnen eine Kandidatin/einen Kandidaten vorstellen:\n\n"
     "{{profile}}\n\nBei Interesse schlage ich gern drei Gesprächstermine vor. Die vollständigen Unterlagen sende ich nach Ihrer Rückmeldung.\n\n"
     "Mit freundlichen Grüßen\nLuna – Recruiting-Team", ["contact_name", "clinic_name", "posting_title", "initials", "profile"]),
    ("email", "de", "clinic_follow_up", "Klinik: Nachfassen", "Re: Pflegefachkraft für {{clinic_name}} – kurze Nachfrage",
     "Sehr geehrte/r {{contact_name}},\n\nich wollte kurz nachfragen, ob das Profil {{initials}} für Sie interessant ist. Es ist weiterhin verfügbar "
     "(Start ab {{start_from}}).\n\nMit freundlichen Grüßen\nLuna – Recruiting-Team", ["contact_name", "clinic_name", "initials", "start_from"]),
    ("email", "de", "propose_slots", "Klinik: Terminvorschläge", "Gesprächstermine für Profil {{initials}}",
     "Sehr geehrte/r {{contact_name}},\n\nvielen Dank für Ihr Interesse. Für ein erstes Gespräch ({{format}}) schlage ich vor:\n\n{{slots}}\n\n"
     "Welcher Termin passt Ihnen? Gern auch ein Gegenvorschlag.\n\nMit freundlichen Grüßen\nLuna – Recruiting-Team", ["contact_name", "initials", "format", "slots"]),
    ("email", "de", "confirm_slot", "Klinik: Termin bestätigen", "Bestätigung: Gespräch {{slot}} – Profil {{initials}}",
     "Sehr geehrte/r {{contact_name}},\n\nhiermit bestätige ich das Gespräch am {{slot}} ({{format}}). Profil {{initials}} erhält die Einladung und eine Erinnerung.\n\n"
     "Mit freundlichen Grüßen\nLuna – Recruiting-Team", ["contact_name", "slot", "format", "initials"]),
    ("email", "de", "feedback_request", "Klinik: Feedback erbitten", "Ihr Feedback zum Gespräch mit {{initials}}",
     "Sehr geehrte/r {{contact_name}},\n\nwie war Ihr Eindruck vom Gespräch mit {{initials}} am {{slot}}? Möchten Sie ein Angebot machen oder ein zweites Gespräch führen?\n\n"
     "Mit freundlichen Grüßen\nLuna – Recruiting-Team", ["contact_name", "initials", "slot"]),
    ("email", "de", "cohort_bundle", "Klinik: Kandidatenpaket", "{{n}} Pflegekräfte für {{clinic_name}} – anonymisierte Profile",
     "Sehr geehrte/r {{contact_name}},\n\nfür Ihre offenen Stellen im Bereich {{criteria}} stelle ich Ihnen {{n}} anonymisierte Profile vor:\n\n{{profiles}}\n\n"
     "Sagen Sie mir einfach, welche Profile Sie kennenlernen möchten – ich organisiere die Gespräche.\n\nMit freundlichen Grüßen\nLuna – Recruiting-Team",
     ["contact_name", "clinic_name", "criteria", "n", "profiles"]),
]

OPENERS = {
    "de": ["Hallo, ich habe Ihre Anzeige gesehen. Ich bin {quali} und suche eine Stelle in Bayern.",
           "Guten Tag, ich interessiere mich für die Pflege-Stellen aus der Anzeige. Können Sie mir mehr sagen?",
           "Hallo Luna, ich bin {quali} mit {years_dat} Erfahrung und suche ab {start} eine neue Stelle."],
    "en": ["Hello, I saw your ad on Facebook. I am a registered nurse from {origin} and I am looking for a job in Germany.",
           "Hi, I am interested in the nursing jobs in Bavaria. I have {years} of experience in {dept}."],
    "ro": ["Bună ziua, am văzut anunțul dvs. Sunt asistentă medicală cu {years} experiență și caut un post în Bavaria.",
           "Salut, mă interesează posturile de asistent medical din anunț."],
}
CAND_LINES = {
    "consent_yes": {"de": ["Ja, einverstanden.", "Ja gerne!", "JA", "Ja, kein Problem."], "en": ["Yes, that's fine.", "YES"], "ro": ["Da, sunt de acord.", "DA"]},
    "basics": {"de": ["Ich bin {quali}, {years} Erfahrung, zuletzt {dept}.", "{quali}, seit {years_dat}, Schwerpunkt {dept}."],
               "en": ["I am a {quali}, {years} of experience, mostly {dept}."], "ro": ["Sunt {quali}, {years} experiență, ultima dată pe {dept}."]},
    "location": {"de": ["{plz} {city}, bis {radius} km wäre ok. Anfangen könnte ich ab {start}.", "Ich wohne in {city} ({plz}). Radius {radius} km. Start {start}."],
                 "en": ["I live in {city} at the moment, I could start from {start}."], "ro": ["Locuiesc în {city}, pot începe din {start}."]},
    "cv": {"de": ["Hier mein Lebenslauf.", "Anbei der CV.", "Lebenslauf im Anhang."], "en": ["Here is my CV."], "ro": ["CV-ul meu atașat."]},
    "cert": {"de": ["Zeugnis anbei.", "Hier das Examenszeugnis.", "Und hier die Urkunde."], "en": ["My diploma attached."], "ro": ["Diploma atașată."]},
    "thanks": {"de": ["Super, danke!", "Vielen Dank, ich warte auf Rückmeldung.", "Okay, danke Luna."], "en": ["Great, thank you!"], "ro": ["Mulțumesc mult!"]},
    "slot_ok": {"de": ["Ja, {slot} passt mir.", "{slot} geht, danke."], "en": ["Yes, {slot} works for me."], "ro": ["Da, {slot} e ok."]},
    "feedback": {"de": ["Es lief gut, ich hatte einen guten Eindruck.", "Ganz okay, sie wollen sich melden."], "en": ["It went well I think."], "ro": ["A mers bine."]},
}
ESCALATION_LINES = {
    "salary": ["Wie viel Gehalt zahlt die Klinik? Ich brauche mindestens 3.800 Euro brutto.", "What is the salary? I need to know before the interview.",
               "Wie ist die Bezahlung bei {clinic}? Mit oder ohne Zulagen?"],
    "visa_legal": ["Ich habe Fragen zum Visum. Mein Anwalt sagt, die Blue Card geht nicht ohne Anerkennung.", "Brauche ich für den Aufenthalt erst den Bescheid?"],
    "asks_for_human": ["Kann ich bitte mit einem Menschen sprechen? Ich möchte mit einem Mitarbeiter sprechen.", "Can I talk to a human please?"],
    "complaint": ["Ich bin unzufrieden, die Klinik hat sich nie gemeldet. Das ist schlecht organisiert.", "Ich möchte eine Beschwerde einreichen."],
}
CLINIC_LINES = {
    "interested": ["Vielen Dank für das Profil. Das klingt interessant – bitte schicken Sie uns die vollständigen Unterlagen.",
                   "Das Profil passt gut zu unserer Station. Wann könnte ein Gespräch stattfinden?"],
    "accept": ["Termin {slot} passt uns. Bitte per Video.", "Wir nehmen den zweiten Termin ({slot})."],
    "counter": ["Die Termine passen leider nicht. Ginge {slot}?"],
    "decline": ["Vielen Dank, aktuell ist die Stelle bereits besetzt.", "Das Profil passt leider nicht zu den Anforderungen (Deutsch B2 zwingend)."],
    "feedback_pos": ["Das Gespräch war sehr positiv. Wir möchten ein Angebot machen.", "Guter Eindruck, wir laden zum Probearbeiten ein."],
}


def render(body, vars_):
    return re.sub(r"{{\s*(\w+)\s*}}", lambda m: str(vars_.get(m.group(1), "")), body or "")


def first_name(name):
    return name.split()[0]


def initials(name):
    parts = [p for p in name.replace("-", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() + "."


def phone_masked(phone):
    if not phone:
        return None
    return phone[:7] + " ••• ••" + phone[-2:]


def years_label(n, lang="de", dative=False):
    """Singular/plural-correct 'N years of experience' label, e.g. '1 Jahr' vs '3 Jahre' (dative 'Jahren' after mit/seit)."""
    if lang == "en":
        return f"{n} year" if n == 1 else f"{n} years"
    if lang == "ro":
        return f"{n} an" if n == 1 else f"{n} ani"
    if n == 1:
        return f"{n} Jahr"
    return f"{n} Jahren" if dative else f"{n} Jahre"


def anonymised_profile(cand, docs=None):
    """Initials + qualifications + experience + availability only (feature 11)."""
    return (f"{cand.get('initials')} – {cand.get('qualification')}, {years_label(cand.get('experience_years'))} Erfahrung "
            f"({', '.join(cand.get('departments') or []) or 'Allgemeinstation'}), Deutsch {cand.get('german_level')}, "
            f"{M.ANERK_LABEL.get(cand.get('anerkennung_status'), '')}, verfügbar ab {cand.get('start_from')}, Radius {cand.get('radius_km')} km um {cand.get('city')}.")


def missing_docs_label(docs):
    lab = {"cv": "Lebenslauf", "education_cert": "Examenszeugnis", "anerkennung": "Anerkennungsbescheid", "language_cert": "Sprachzertifikat",
           "work_permit": "Aufenthaltstitel", "references": "Arbeitszeugnisse"}
    return ", ".join(lab[d["kind"]] for d in docs if d["kind"] in ("cv", "education_cert", "language_cert", "anerkennung") and d["status"] in ("missing", "requested")) or "nichts"


def escalation_draft(kind, cand):
    """Luna's suggested reply for an escalated thread (goes to the manager as a high-risk approval)."""
    return {"salary": f"Hallo {first_name(cand['name'])}, das Gehalt richtet sich nach Tarif (TVöD-P / AVR) und Ihrer Erfahrung – als {cand['qualification']} mit {years_label(cand['experience_years'], dative=True)} "
                          "liegen Sie in der Regel zwischen 3.600 € und 4.300 € brutto plus Zulagen. Konkrete Zahlen nennt die Klinik im Gespräch.",
                "visa_legal": f"Hallo {first_name(cand['name'])}, zu Visum und Aufenthalt darf ich keine Rechtsauskunft geben. Eine Kollegin meldet sich dazu persönlich bei Ihnen – "
                              "die üblichen Wege sind das Visum nach §16d (Anerkennung in Deutschland) oder die Blue Card nach erteilter Anerkennung.",
                "asks_for_human": f"Hallo {first_name(cand['name'])}, natürlich – meine Kollegin Sarah übernimmt ab jetzt persönlich und meldet sich heute noch bei Ihnen.",
                "complaint": f"Hallo {first_name(cand['name'])}, das tut mir leid. Ich habe Ihre Rückmeldung an das Team weitergegeben; jemand meldet sich innerhalb von 24 Stunden persönlich."}[kind]


# --- the seed ---------------------------------------------------------------------------------------------
class Seeder:
    def __init__(self, c, now, rng, clinics, jobs, n_candidates=140):
        self.c, self.now, self.rng, self.n_candidates = c, now, rng, n_candidates
        self.clinics, self.jobs = clinics, jobs
        self.by_clinic = {str(cl["clinic_id"]): cl for cl in clinics}
        self.jobs_by = M.jobs_by_clinic(jobs)
        self.clinics_with_jobs = [cl for cl in clinics if self.jobs_by.get(str(cl["clinic_id"]))] or clinics
        self.tpl = {}
        self.events = []
        self.wa_ids, self.mail_ids = [], []
        self.campaign_ids, self.lp_ids = [], []
        self.stats = {"messages_out": 0}

    # -- helpers
    def ev(self, at, actor, kind, target=None, detail=None):
        self.events.append((iso(at), actor, kind, target or {}, detail))

    def tid(self, channel, lang, stage):
        return self.tpl.get((channel, lang, stage)) or self.tpl.get((channel, "de", stage))

    def pick(self, seq):
        return self.rng.choice(seq)

    def dt(self, days=0, hours=0, minutes=0):
        return self.now + timedelta(days=days, hours=hours, minutes=minutes)

    def business_dt(self, d):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.replace(hour=self.rng.choice([9, 10, 11, 14, 15, 16]), minute=self.rng.choice([0, 30]), second=0)

    # -- accounts, templates, campaigns, landing pages
    def seed_templates(self):
        for i, (ch, lang, stage, name, subject, body, vars_) in enumerate(TEMPLATES):
            uses = self.rng.randint(20, 400) if ch == "whatsapp" else self.rng.randint(5, 90)
            tid = db.insert(self.c, "templates", {"channel": ch, "lang": lang, "stage": stage, "name": name, "subject": subject, "body": body,
                                                  "variables": vars_, "version": self.rng.choice([1, 1, 2, 3]), "uses": uses,
                                                  "reply_rate": round(self.rng.uniform(0.35, 0.82), 2), "active": 1})
            self.tpl[(ch, lang, stage)] = tid

    def seed_accounts(self):
        wa = [("Luna DE 1", "+49 151 2384 7719", "connected", "green", 1000, 412, "done", None, {"campaign_ids": [1, 2], "languages": ["de"]}),
              ("Luna DE 2", "+49 160 9147 3306", "rate_limited", "yellow", 250, 250, "tier_1 (250/Tag)", "Tageslimit erreicht (250/250) – Meta Tier 1", {"campaign_ids": [3, 5], "languages": ["de"]}),
              ("Luna EN/RO", "+49 176 4471 0952", "connected", "green", 1000, 138, "done", None, {"campaign_ids": [4, 6], "languages": ["en", "ro"]}),
              ("Luna neu (Warm-up)", "+49 157 5520 6681", "connected", "yellow", 50, 12, "Tag 3/14", None, {"campaign_ids": [], "languages": ["de"], "note": "Warm-up: 50 → 250 → 1000"})]
        for name, ident, st, q, cap, used, warm, err, routing in wa:
            self.wa_ids.append(db.insert(self.c, "accounts", {"kind": "whatsapp", "name": name, "identifier": ident, "provider": "WhatsApp Cloud API", "status": st, "quality": q,
                                                              "daily_cap": cap, "used_today": used, "warmup_stage": warm, "last_error": err,
                                                              "last_ok_at": iso(self.dt(minutes=-self.rng.randint(3, 90))), "routing": routing}))
        mb = [("Outreach Kliniken", "kliniken@pflegematch-bayern.example", "connected", "green", 300, 84, None, {"purpose": "clinic_intro", "dmarc": "pass", "bounce_rate": 0.8}),
              ("Partner / Cohorts", "partner@pflegematch-bayern.example", "degraded", "yellow", 300, 61, "Bounce-Rate 6,2 % (>5 %): 4 Hard Bounces (pflegedirektion@… unbekannt)", {"purpose": "cohort_send", "dmarc": "pass", "bounce_rate": 6.2}),
              ("Luna Kandidaten", "luna@pflegematch-bayern.example", "connected", "green", 500, 22, None, {"purpose": "candidate_email", "dmarc": "pass", "bounce_rate": 0.3})]
        for name, ident, st, q, cap, used, err, routing in mb:
            self.mail_ids.append(db.insert(self.c, "accounts", {"kind": "mailbox", "name": name, "identifier": ident, "provider": "SMTP/IMAP (Hetzner)", "status": st, "quality": q,
                                                                "daily_cap": cap, "used_today": used, "last_error": err, "last_ok_at": iso(self.dt(minutes=-self.rng.randint(5, 120))), "routing": routing}))
        apis = [("Meta Marketing API", "act_2093847561", "Meta", "connected", "green", 200, 143, None, {"quota": "200 Calls/h", "scopes": ["ads_read", "ads_management"]}),
                ("WhatsApp Cloud API", "WABA 1104…772", "Meta", "connected", "green", 4, 4, None, {"quota": "4 Nummern", "webhook": "ok"}),
                ("E-Mail-Provider", "smtp.hetzner.example", "Hetzner", "degraded", "yellow", 1000, 167, "Bounce-Rate Mailbox 'Partner' über Schwelle", {"quota": "1000 Mails/Tag"}),
                ("Kalender", "calendar@pflegematch-bayern.example", "Google Calendar", "connected", "green", None, None, None, {"quota": "unbegrenzt"}),
                ("LLM Gateway", A.LLM_MODEL, "exe.dev gateway", "disconnected", "red", 0, 0, "disconnected: no credits", {"quota": "0 Credits – Engine läuft regelbasiert"}),
                ("Exa", "exa-search", "Exa", "connected", "green", 1000, 212, None, {"quota": "788 Suchen übrig"}),
                ("Firecrawl", "fc-agent", "Firecrawl", "connected", "green", 379, 0, None, {"quota": "379 Credits"})]
        for name, ident, prov, st, q, cap, used, err, routing in apis:
            db.insert(self.c, "accounts", {"kind": "api", "name": name, "identifier": ident, "provider": prov, "status": st, "quality": q, "daily_cap": cap, "used_today": used,
                                           "last_error": err, "last_ok_at": None if st == "disconnected" else iso(self.dt(minutes=-self.rng.randint(1, 60))), "routing": routing})

    def seed_campaigns(self):
        specs = [("Pflege München – Pflegefachkraft (DE)", "active", 60.0, [("München 25 km · DE", "München", 25, ["de"]), ("München 40 km · RO/BS", "München", 40, ["ro", "bs"])], 1.0),
                 ("Nürnberg/Erlangen – Pflegefachkraft (DE)", "active", 45.0, [("Nürnberg 30 km", "Nürnberg", 30, ["de"]), ("Erlangen/Fürth 20 km", "Erlangen", 20, ["de"]), ("Retargeting Website", "Nürnberg", 50, ["de"])], 1.0),
                 ("Regensburg/Oberpfalz – Intensiv & Anästhesie", "active", 40.0, [("Regensburg 35 km", "Regensburg", 35, ["de"]), ("Weiden/Amberg 40 km", "Weiden", 40, ["de"])], 2.4),
                 ("Augsburg/Schwaben – Nurses (EN)", "paused", 35.0, [("Augsburg 30 km · EN", "Augsburg", 30, ["en"]), ("Kempten/Allgäu · EN", "Kempten", 45, ["en"])], 1.3),
                 ("Bayern – Anerkennung Retargeting", "learning", 25.0, [("Bayern · Lookalike 1 %", "München", 200, ["de", "en"]), ("Bayern · Interessen Pflege", "Nürnberg", 200, ["de"])], 1.0),
                 ("Würzburg/Unterfranken – Asistente medicale (RO)", "active", 30.0, [("Würzburg 40 km · RO", "Würzburg", 40, ["ro"]), ("Schweinfurt 30 km · RO", "Schweinfurt", 30, ["ro"])], 1.0)]
        for i, (name, status, budget, adsets, spike) in enumerate(specs, 1):
            days = self.rng.randint(18, 60)
            adset_rows, ad_rows = [], []
            tot = {"spend": 0.0, "impressions": 0, "clicks": 0, "leads": 0}
            for k, (an, city, radius, langs) in enumerate(adsets, 1):
                spend = round(budget * days * self.rng.uniform(0.25, 0.55), 2)
                imps = int(spend * self.rng.randint(90, 160))
                clicks = int(imps * self.rng.uniform(0.012, 0.03))
                leads = max(1, int(clicks * self.rng.uniform(0.06, 0.14) / spike))
                adset_rows.append({"id": i * 10 + k, "name": an, "city": city, "radius_km": radius, "languages": langs, "status": "active" if status != "paused" else "paused",
                                   "spend": spend, "impressions": imps, "clicks": clicks, "leads": leads, "cpl": round(spend / leads, 2)})
                for a in range(1, 3):
                    share = 0.6 if a == 1 else 0.4
                    ad_rows.append({"id": i * 100 + k * 10 + a, "adset_id": i * 10 + k, "name": f"{an} · Anzeige {a}", "creative": self.pick(["Video 15s Station", "Carousel Benefits", "Static Testimonial", "Reel Nachtdienst"]),
                                    "spend": round(spend * share, 2), "impressions": int(imps * share), "clicks": int(clicks * share), "leads": int(leads * share) or 1})
                for key in tot:
                    tot[key] += adset_rows[-1][key]
            leads = tot["leads"]
            db.insert(self.c, "campaigns", {"name": name, "platform": "meta", "objective": "messages", "status": status, "daily_budget": budget,
                                            "spend_total": round(tot["spend"], 2), "spend_7d": round(budget * 7 * (0 if status == "paused" else self.rng.uniform(0.8, 1.0)), 2),
                                            "impressions": tot["impressions"], "clicks": tot["clicks"], "leads": leads,
                                            "qualified": 0, "interviews": 0, "placed": 0, "adsets": adset_rows, "ads": ad_rows, "created_at": iso(self.dt(days=-days))})
            self.campaign_ids.append(i)
        lps = [("pflege-muenchen", "Pflegejobs in München – wir finden Ihre Station", "de", [1, 5]),
               ("asistente-bavaria-ro", "Posturi de asistent medical în Bavaria", "ro", [6, 1]),
               ("nurse-jobs-bavaria-en", "Nursing jobs in Bavaria – recognition support included", "en", [4, 5]),
               ("intensivpflege-oberpfalz", "Intensivpflege in der Oberpfalz – Zulagen & Dienstplan-Garantie", "de", [3]),
               ("anerkennung-bayern", "Anerkennung in Bayern – wir begleiten Sie", "de", [5, 2])]
        for slug, title, lang, cids in lps:
            variants = []
            for key in ("A", "B"):
                visits = self.rng.randint(400, 2600)
                leads = int(visits * self.rng.uniform(0.05, 0.14))
                variants.append({"key": key, "headline": title if key == "A" else title.split(" – ")[0] + " – jetzt in 2 Minuten bewerben",
                                 "cta": "Auf WhatsApp schreiben" if lang == "de" else ("Chat on WhatsApp" if lang == "en" else "Scrie pe WhatsApp"),
                                 "form_fields": ["name", "phone"] if key == "A" else ["phone"], "visits": visits, "leads": leads, "winner": False})
            best = max(variants, key=lambda v: v["leads"] / v["visits"])
            if self.rng.random() < 0.6:
                best["winner"] = True
            self.lp_ids.append(db.insert(self.c, "landing_pages", {"slug": slug, "url": f"https://pflegematch-bayern.example/{slug}", "title": title, "language": lang,
                                                                  "campaign_ids": cids, "variants": variants,
                                                                  "wa_deeplink": f"https://wa.me/4915123847719?text=Hallo%20Luna&ref={CAMPAIGN_SLUGS[cids[0] - 1]}",
                                                                  "status": "live"}))

    # -- candidates
    def make_candidate(self, i):
        rng = self.rng
        origin = self.pick(ORIGIN_POOL)
        fn, ln = self.pick(NAMES[origin][0]), self.pick(NAMES[origin][1])
        name = f"{fn} {ln}"
        stage = rng.choices(STAGES, STAGE_WEIGHTS)[0]
        r = rng.random()
        if r < 0.08:
            stage = "lost"
        elif r < 0.11:
            stage = "dormant"
        si = STAGES.index(stage) if stage in STAGES else (3 if stage == "dormant" else rng.randint(1, 8))
        role = self.pick(ROLE_POOL)
        lang = "de"
        if origin == "Rumänien" and rng.random() < 0.4:
            lang = "ro"
        elif origin in ("Indien", "Philippinen", "Vietnam") and rng.random() < 0.45:
            lang = "en"
        if origin == "Deutschland":
            german, anerk, permit = "C2", "not_needed", "EU"
        elif origin in ("Rumänien", "Bosnien"):
            german = self.pick(["B1", "B2", "B2", "C1"])
            anerk = self.pick(["granted", "applied", "applied", "none", "deficit_notice"]) if origin == "Bosnien" else self.pick(["granted", "granted", "applied", "not_needed", "none"])
            permit = "EU" if origin == "Rumänien" else self.pick(["Visum §16d", "Blue Card", "beantragt", "keine"])
        else:
            german = self.pick(["A2", "B1", "B1", "B2", "B2", "C1"])
            anerk = self.pick(["none", "none", "applied", "applied", "deficit_notice", "granted"])
            permit = self.pick(["Visum §16d", "Blue Card", "beantragt", "keine", "Aufenthaltstitel §18a"])
        campaign = rng.choices(self.campaign_ids, [3, 3, 2, 2, 1, 2])[0]
        lp_row = [lp for lp in self.landing_pages if campaign in lp["campaign_ids"]]
        lp = self.pick(lp_row)["id"] if lp_row else None
        abroad = origin != "Deutschland" and si <= 5 and rng.random() < 0.2
        if abroad:
            city, region, plz = self.pick(ABROAD_CITY[origin]), "Ausland", None
        else:
            cl = rng.choices(self.clinics_with_jobs, [(self.by_clinic.get(str(x["clinic_id"]), {}).get("jobs_open") or 0) + 1 for x in self.clinics_with_jobs])[0]
            city, region = cl["town"], cl.get("regierungsbezirk") or "Oberbayern"
            plz = self.pick(PLZ_PREFIX.get(region, ["8"])) + f"{rng.randint(0, 999):03d}"
        depts = rng.sample(DEPARTMENTS, rng.choice([1, 1, 2, 2, 3]))
        days_in_stage = rng.randint(0, 6) if si <= 3 else rng.randint(1, 25)
        if stage == "dormant":
            days_in_stage = rng.randint(12, 40)
        stage_changed = self.dt(days=-days_in_stage, hours=-rng.randint(0, 23), minutes=-rng.randint(0, 59))
        created = stage_changed - timedelta(days=int(si * rng.uniform(2, 6)), hours=rng.randint(0, 12))
        start_from = (self.now + timedelta(days=rng.choice([14, 30, 30, 60, 90]))).strftime("%d.%m.%Y")
        cand = {"name": name, "initials": initials(name), "phone": f"+49 {self.pick(['151', '152', '157', '160', '162', '170', '171', '176', '177', '179'])} {rng.randint(1000000, 9999999)}",
                "email": f"{fn.split()[0].lower()}.{ln.split()[-1].lower()}{rng.randint(1, 99)}@example.org", "language": lang, "german_level": german, "origin_country": origin,
                "city": city, "plz": plz, "region": region, "radius_km": self.pick([15, 25, 30, 30, 50, 80]), "role_class": role, "departments": depts,
                "qualification": self.pick(QUALI[role]), "experience_years": rng.randint(0, 18), "anerkennung_status": anerk, "work_permit": permit,
                "employment_type": self.pick(["Vollzeit", "Vollzeit", "Teilzeit"]), "shifts": rng.sample(["Früh", "Spät", "Nacht", "Wechsel"], rng.choice([1, 2, 3])),
                "start_from": start_from, "salary_expectation": rng.choice([None, None, 3400, 3800, 4200]),
                "source_campaign_id": campaign, "landing_page_id": lp, "consent_at": iso(created + timedelta(minutes=rng.randint(2, 40))) if si >= 1 or stage in ("lost", "dormant") else None,
                "stage": stage, "stage_changed_at": iso(stage_changed), "lost_reason": self.pick(LOST_REASONS) if stage == "lost" else None,
                "owner": rng.choices(["Luna", "Ivan", "Sarah", "Mert"], [6, 2, 2, 1])[0], "tags": [], "created_at": iso(created)}
        cand["email"] = cand["email"].replace("ß", "ss")
        cand["email"] = re.sub(r"[^a-z0-9.@]", "", cand["email"])
        if lang != "de":
            cand["tags"].append(lang.upper())
        if abroad:
            cand["tags"].append("Ausland")
        return cand, si

    def stage_index_for_state(self, stage, state):
        return STATES.index(state) if state in STATES else 0

    def seed_candidates(self, n):
        self.landing_pages = db.rows("landing_pages", self.c.execute("select * from landing_pages"))
        for i in range(n):
            cand, si = self.make_candidate(i)
            cid = db.insert(self.c, "candidates", cand)
            cand["id"] = cid
            state = self.pick(STAGE_TO_STATES.get(cand["stage"], ["collect_docs"])) if cand["stage"] not in ("lost", "dormant") else \
                (self.pick(["collect_basics", "collect_location", "collect_docs", "docs_review", "profile_sent"]))
            self.seed_documents(cand, state)
            self.seed_conversation(cand, state)

    def seed_documents(self, cand, state):
        sidx = STATES.index(state)
        rng = self.rng
        recv = lambda d: iso(parse(cand["stage_changed_at"]) - timedelta(days=d))
        for kind in DOC_KINDS:
            status, at, note = "missing", None, None
            if kind == "cv":
                status = "verified" if sidx >= 6 else ("received" if sidx >= 5 or (sidx == 4 and rng.random() < 0.6) else ("requested" if sidx == 4 else "missing"))
            elif kind == "education_cert":
                status = "verified" if sidx >= 6 else ("received" if sidx >= 5 else ("requested" if sidx == 4 else "missing"))
            elif kind == "anerkennung":
                a = cand["anerkennung_status"]
                status = {"granted": "verified" if sidx >= 6 else "received", "applied": "received" if sidx >= 5 else "requested", "deficit_notice": "rejected" if sidx >= 5 else "requested",
                          "not_needed": "verified", "none": "missing"}[a]
                note = {"deficit_notice": "Defizitbescheid: Kenntnisprüfung erforderlich", "applied": "Antrag bei Regierung von Oberbayern, Eingang bestätigt",
                        "none": "noch nicht beantragt", "granted": "Urkunde liegt vor"}.get(a)
            elif kind == "language_cert":
                status = "verified" if sidx >= 6 and cand["german_level"] in ("B2", "C1", "C2") else ("received" if sidx >= 5 else ("requested" if sidx >= 4 else "missing"))
                if cand["german_level"] in ("A2", "B1") and sidx >= 5:
                    status, note = "rejected", f"Zertifikat {cand['german_level']} – B2 gefordert"
            elif kind == "work_permit":
                status = "not_needed" if cand["work_permit"] == "EU" else ("received" if sidx >= 6 and cand["work_permit"] not in ("keine", "beantragt") else ("requested" if sidx >= 5 else "missing"))
                if status == "not_needed":
                    status, note = "verified", "EU-Bürger/in – kein Aufenthaltstitel nötig"
            elif kind == "references":
                status = "received" if sidx >= 7 and rng.random() < 0.5 else "missing"
            if status in ("received", "verified", "rejected"):
                at = recv(rng.randint(0, 6))
            if status == "requested" and not note:
                note = "angefordert, Erinnerung geplant"
            db.insert(self.c, "documents", {"candidate_id": cand["id"], "kind": kind, "status": status, "received_at": at, "note": note})
        cand["docs"] = db.rows("documents", self.c.execute("select * from documents where candidate_id=?", (cand["id"],)))

    def script(self, cand, state):
        """Message script up to `state`: list of (author, text, attachments, state_after, template_stage)."""
        lang = cand["language"]
        L = lambda key: self.pick(CAND_LINES[key].get(lang) or CAND_LINES[key]["de"])
        v = {"quali": cand["qualification"], "years": years_label(cand["experience_years"], lang=lang),
             "years_dat": years_label(cand["experience_years"], lang=lang, dative=True), "dept": cand["departments"][0], "plz": cand["plz"] or "", "city": cand["city"],
             "radius": cand["radius_km"], "start": cand["start_from"], "origin": cand["origin_country"], "first_name": first_name(cand["name"]), "region": cand["region"]}
        m = self.top_match(cand)
        v.update({"clinic_name": m["name"] if m else "der Klinik", "clinic_town": m["town"] if m else "", "slot": self.slot_label(self.business_dt(self.dt(days=self.rng.randint(1, 6)))), "format": "Video"})
        T = lambda stage: (render(self.tpl_body(lang, stage), v), stage)
        steps = [("candidate", self.pick(OPENERS[lang]).format(**v), [], "greeting", None), ("luna", *T("greeting"), "consent"),
                 ("candidate", L("consent_yes"), [], "consent", None), ("luna", *T("collect_basics"), "collect_basics"),
                 ("candidate", L("basics").format(**v), [], "collect_basics", None), ("luna", *T("collect_location"), "collect_location"),
                 ("candidate", L("location").format(**v), [], "collect_location", None), ("luna", *T("collect_docs"), "collect_docs"),
                 ("candidate", L("cv"), [{"kind": "cv", "name": f"Lebenslauf_{cand['initials'][:-1]}.pdf", "size_kb": self.rng.randint(80, 900)}], "collect_docs", None),
                 ("luna", "Danke, der Lebenslauf ist angekommen. Fehlt noch das Examenszeugnis.", "collect_docs", "collect_docs"),
                 ("candidate", L("cert"), [{"kind": "education_cert", "name": "Zeugnis.jpg", "size_kb": self.rng.randint(300, 2500)}], "docs_review", None),
                 ("luna", *T("docs_review"), "docs_review"), ("luna", *T("qualified"), "qualified"), ("candidate", L("thanks"), [], "matching", None),
                 ("luna", *T("profile_sent"), "profile_sent"), ("candidate", L("thanks"), [], "profile_sent", None),
                 ("luna", *T("interview_prep"), "interview_prep"), ("candidate", L("slot_ok").format(**v), [], "interview_prep", None),
                 ("luna", f"Bestätigt: {v['slot']} per Video. Ich schicke Ihnen 24 Stunden vorher eine Erinnerung.", "interview_prep", "interview_prep"),
                 ("luna", *T("awaiting_feedback"), "awaiting_feedback"), ("candidate", L("feedback"), [], "awaiting_feedback", None),
                 ("luna", f"Herzlichen Glückwunsch, {v['first_name']}! {v['clinic_name']} hat Ihnen ein Angebot gemacht. Ich schicke Ihnen die Details per E-Mail.", "placed", "placed")]
        # normalise shape: luna steps from T() are (author, text, stage, state_after)
        out = []
        for s in steps:
            if s[0] == "luna":
                author, text, stage, state_after = s
                out.append((author, text, [], state_after, stage))
            else:
                out.append(s)
        target = STATES.index(state)
        cut = [s for s in out if STATES.index(s[3]) <= target]
        # drop trailing candidate replies sometimes so that Luna wrote last
        if len(cut) > 1 and cut[-1][0] == "candidate" and self.rng.random() < 0.45:
            cut = cut[:-1]
        return cut, v

    def tpl_body(self, lang, stage):
        tid = self.tid("whatsapp", lang, stage)
        r = self.c.execute("select body from templates where id=?", (tid,)).fetchone()
        return r["body"] if r else ""

    def top_match(self, cand):
        if "_top" not in cand:
            r = M.rank(cand, self.clinics_with_jobs[:120], self.jobs, n=1)
            cand["_top"] = r[0] if r else None
        return cand["_top"]

    def slot_label(self, d):
        return d.strftime("%a %d.%m. %H:%M").replace("Mon", "Mo").replace("Tue", "Di").replace("Wed", "Mi").replace("Thu", "Do").replace("Fri", "Fr")

    def seed_conversation(self, cand, state):
        rng = self.rng
        stage = cand["stage"]
        steps, v = self.script(cand, state)
        r = rng.random()
        mode = "luna" if r < 0.62 else "paused" if r < 0.74 else "human" if r < 0.84 else "manager" if r < 0.955 else "stopped"
        tags, pause_reason = list(cand["tags"]), None
        if stage == "lost" and cand["lost_reason"] == "Opt-out (STOP)":
            mode = "stopped"
        if stage == "placed":
            mode = "luna"
        escalation = None
        if mode == "stopped":
            if stage != "lost":
                cand["stage"], cand["lost_reason"] = "lost", "Opt-out (STOP)"
                db.update(self.c, "candidates", cand["id"], {"stage": "lost", "lost_reason": "Opt-out (STOP)"})
                stage = "lost"
            steps.append(("candidate", "STOP", [], steps[-1][3], None))
            tags.append("STOP")
        elif mode == "manager":
            escalation = self.pick(list(ESCALATION_LINES))
            steps.append(("candidate", self.pick(ESCALATION_LINES[escalation]).format(clinic=v["clinic_name"]), [], steps[-1][3], None))
            tags.append(f"escalation:{escalation}")
        elif mode == "paused":
            pause_reason = self.pick(["Kandidat/in im Urlaub bis 15.09.", "wartet auf Anerkennungsbescheid", "Kampagne pausiert (CPL-Spike)", "Doppelter Lead – prüfen",
                                      "WhatsApp-Nummer in Warm-up", "auf Wunsch der Kandidatin/des Kandidaten bis Oktober"])
            tags.append("PAUSIERT")
        elif mode == "human":
            steps.append(("operator", self.pick(["Hallo, hier ist Sarah aus dem Team – ich übernehme ab jetzt persönlich. Wann kann ich Sie kurz anrufen?",
                                                 "Ivan hier. Ich habe mit der Klinik telefoniert, die Rückmeldung kommt bis Freitag.",
                                                 "Hallo! Ich melde mich wegen der Visumsfrage – ich schicke Ihnen gleich die Checkliste."]), [], steps[-1][3], None))
        if stage == "lost" and mode != "stopped":
            steps.append(("candidate", self.pick(["Ich habe schon eine Stelle gefunden, danke.", "Danke, aber ich bleibe doch in meiner Heimat.",
                                                  "Kein Interesse mehr, bitte nicht mehr schreiben."]), [], steps[-1][3], None))
            steps.append(("luna", "Schade, aber alles Gute für Sie! Wenn Sie später wieder suchen, schreiben Sie mir gern.", [], steps[-1][3], None))
        if stage == "dormant":
            for _ in range(2):
                steps.append(("luna", render(self.tpl_body(cand["language"], "follow_up"), v), [], steps[-1][3], "follow_up"))
        # timestamps: walk backwards from last message
        last_author = steps[-1][0]
        if last_author == "candidate":
            last_at = self.dt(hours=-rng.choice([0.2, 0.5, 1, 2, 3, 5, 8, 14, 26, 40]))
        elif stage == "dormant":
            last_at = self.dt(days=-rng.randint(8, 30))
        else:
            last_at = self.dt(hours=-rng.choice([0.5, 1, 3, 6, 12, 20, 30, 48, 72]))
        if stage == "placed":
            last_at = parse(cand["stage_changed_at"])
        ats = []
        t = last_at
        for _ in steps:
            ats.append(t)
            t = t - timedelta(minutes=rng.choice([3, 5, 12, 25, 45, 90, 180, 600, 1440]))
        ats.reverse()
        account = self.wa_ids[2] if cand["language"] != "de" else rng.choices(self.wa_ids[:2] + [self.wa_ids[3]], [6, 3, 1])[0]
        conv_id = db.insert(self.c, "conversations", {"kind": "candidate", "candidate_id": cand["id"], "channel": "whatsapp", "account_id": account, "mode": mode,
                                                      "state": "lost" if stage == "lost" else "dormant" if stage == "dormant" else state, "language": cand["language"],
                                                      "tags": tags, "pause_reason": pause_reason, "opened_at": iso(ats[0]),
                                                      "closed_at": iso(last_at) if stage in ("lost", "placed") else None})
        for (author, text, att, st_after, tstage), at in zip(steps, ats):
            d = "in" if author == "candidate" else "out"
            meta = {}
            if att:
                meta["documents"] = [a["kind"] for a in att]
            if author == "candidate" and cand["plz"] and cand["plz"] in text:
                meta["plz"], meta["city"] = cand["plz"], cand["city"]
            db.insert(self.c, "messages", {"conversation_id": conv_id, "dir": d, "author": author, "text": text, "at": iso(at),
                                           "status": "received" if d == "in" else self.pick(["read", "read", "delivered", "sent"]),
                                           "template_id": self.tid("whatsapp", cand["language"], tstage) if tstage else None, "attachments": att, "meta": meta})
            if author == "candidate" and att:
                self.ev(at, "candidate", "document_received", {"conversation_id": conv_id, "candidate_id": cand["id"]}, f"{cand['initials']}: {', '.join(a['kind'] for a in att)} erhalten")
            elif author == "luna" and self.rng.random() < 0.12:
                self.ev(at, "luna", "message_sent", {"conversation_id": conv_id, "candidate_id": cand["id"]}, f"an {cand['initials']}: {text[:60]}…")
        # next_actor / sla / unread
        if mode == "stopped":
            nxt, sla, unread = "none", None, 0
        elif last_author == "candidate":
            nxt, sla, unread = "us", iso(last_at + timedelta(hours=4)), 1 if mode != "human" else rng.choice([0, 1])
            if mode == "manager":
                unread = 1
        elif stage in ("placed", "lost"):
            nxt, sla, unread = "none", None, 0
        else:
            nxt, sla, unread = "candidate", iso(last_at + timedelta(hours=24 if stage != "dormant" else 24 * 14)), 0
        db.update(self.c, "conversations", conv_id, {"next_actor": nxt, "sla_due_at": sla, "unread": unread, "last_message_at": iso(last_at), "last_preview": steps[-1][1][:120]})
        cand["conversation_id"] = conv_id
        # events
        self.ev(parse(cand["created_at"]), "meta", "lead_created", {"candidate_id": cand["id"], "conversation_id": conv_id, "campaign_id": cand["source_campaign_id"]},
                f"Neuer Lead {cand['initials']} über Kampagne #{cand['source_campaign_id']}")
        if cand["stage"] not in ("new",):
            self.ev(parse(cand["stage_changed_at"]), "luna", "stage_changed", {"candidate_id": cand["id"], "conversation_id": conv_id},
                    f"{cand['initials']}: → {cand['stage']}" + (f" ({cand['lost_reason']})" if cand.get("lost_reason") else ""))
        if mode != "luna":
            self.ev(last_at + timedelta(minutes=1), "system" if mode in ("stopped", "manager") else "operator", "mode_changed", {"conversation_id": conv_id, "candidate_id": cand["id"]},
                    f"{cand['initials']}: luna → {mode}" + (f" ({pause_reason})" if pause_reason else "") + (f" (Eskalation: {escalation})" if escalation else ""))
        if escalation:
            self.pending_escalations.append((conv_id, cand, escalation, steps[-1][1], last_at))
        if mode == "stopped":
            self.ev(last_at, "system", "opt_out", {"conversation_id": conv_id, "candidate_id": cand["id"]}, f"{cand['initials']}: STOP empfangen – nie wieder anschreiben")

    # -- matches, cohorts, clinic threads
    def seed_matches(self):
        cands = db.rows("candidates", self.c.execute("select * from candidates order by id"))
        convmap = {r["candidate_id"]: r["id"] for r in self.c.execute("select id, candidate_id from conversations where kind='candidate'")}
        for cand in cands:
            cand["conversation_id"] = convmap.get(cand["id"])
        self.cands = cands
        self.by_cand = {c["id"]: c for c in cands}
        match_status = {"qualified": "proposed", "matching": "proposed", "profile_sent": "sent", "interview_scheduling": "clinic_interested", "interview_scheduled": "interview",
                        "interviewed": "interview", "offer": "interview", "placed": "placed"}
        for cand in cands:
            if cand["stage"] not in match_status and not (cand["stage"] == "docs_pending" and self.rng.random() < 0.3):
                continue
            top = M.rank(cand, self.clinics_with_jobs, self.jobs, n=self.rng.choice([3, 4, 5, 5]))
            for k, m in enumerate(top):
                st = match_status.get(cand["stage"], "proposed")
                if k > 0:
                    st = {"placed": self.pick(["declined_by_clinic", "declined_by_candidate", "sent"]), "interview": self.pick(["sent", "declined_by_clinic", "proposed"]),
                          "clinic_interested": self.pick(["sent", "proposed"]), "sent": self.pick(["sent", "proposed", "approved", "declined_by_clinic"])}.get(st, "proposed")
                db.insert(self.c, "matches", {"candidate_id": cand["id"], "clinic_id": m["clinic_id"], "posting_id": m["posting_id"], "score": m["score"], "reasons": m["reasons"],
                                              "status": st, "created_at": iso(parse(cand["stage_changed_at"]) - timedelta(days=self.rng.randint(0, 3)))})
                if k == 0 and st != "proposed":
                    self.ev(parse(cand["stage_changed_at"]), "luna", "profile_sent", {"candidate_id": cand["id"], "conversation_id": cand.get("conversation_id"), "clinic_id": m["clinic_id"]},
                            f"Profil {cand['initials']} an {m['name']} ({m['town']}) gesendet – Score {m['score']}")

    def seed_cohorts(self):
        specs = [("Pflegefachkräfte Oberbayern B2+", {"role_class": "pflegefachkraft", "region": "Oberbayern", "german_level_min": "B2"}, "sent", 9),
                 ("Intensiv/Anästhesie Oberpfalz", {"role_class": "fachpflege", "region": "Oberpfalz", "departments": ["Intensiv/IMC", "Anästhesie"]}, "in_progress", 5),
                 ("Pflegefachkräfte Mittelfranken (Anerkennung erteilt)", {"role_class": "pflegefachkraft", "region": "Mittelfranken", "anerkennung": "granted"}, "sent", 12),
                 ("Pflegehelfer Schwaben", {"role_class": "pflegehelfer", "region": "Schwaben"}, "done", 20),
                 ("OTA/ATA Bayern", {"role_class": "ota_ata"}, "sent", 6),
                 ("Pflegefachkräfte Unterfranken B1 (mit Anerkennung beantragt)", {"role_class": "pflegefachkraft", "region": "Unterfranken", "german_level_min": "B1", "anerkennung": "applied"}, "draft", 1),
                 ("Notaufnahme/Intensiv Niederbayern", {"role_class": "pflegefachkraft", "region": "Niederbayern", "departments": ["Notaufnahme", "Intensiv/IMC"]}, "pending_approval", 0),
                 ("Pflegefachkräfte Oberfranken", {"role_class": "pflegefachkraft", "region": "Oberfranken", "german_level_min": "B2"}, "draft", 0)]
        policy = db.policy(self.c)
        matches = db.rows("matches", self.c.execute("select * from matches"))
        self.cohorts = []
        for name, crit, status, age in specs:
            prev = M.cohort_preview(crit, self.cands, self.clinics, self.jobs, {**policy, "max_profiles_per_clinic_per_week": 99, "max_concurrent_profiles_per_candidate": 99},
                                    matches, iso(self.now), max_clinics=6)
            cand_ids = [c["id"] for c in prev["candidates"] if c["stage"] not in ("lost", "dormant", "new", "contacted")][:8]
            if not cand_ids:                                               # keep the cohort demo-able even on a sparse registry
                cand_ids = [c["id"] for c in self.cands if c["role_class"] == crit.get("role_class", "pflegefachkraft") and c["stage"] not in ("lost", "new")][:5]
            clinic_ids = [k["clinic_id"] for k in prev["clinics"]][:self.rng.choice([3, 4, 5, 6])]
            if not clinic_ids:
                clinic_ids = [str(cl["clinic_id"]) for cl in self.clinics_with_jobs[:4]]
            created = self.dt(days=-(age + 2))
            coh = {"name": name, "criteria": crit, "candidate_ids": cand_ids, "clinic_ids": clinic_ids, "status": status, "created_at": iso(created),
                   "sent_at": iso(self.dt(days=-age)) if status in ("sent", "in_progress", "done") else None}
            coh["id"] = db.insert(self.c, "cohorts", coh)
            self.cohorts.append(coh)
            self.ev(created, "luna", "cohort_created", {"cohort_id": coh["id"]}, f"Cohort „{name}“: {len(cand_ids)} Profile, {len(clinic_ids)} Kliniken")
            if coh["sent_at"]:
                self.ev(parse(coh["sent_at"]), "operator", "cohort_sent", {"cohort_id": coh["id"]}, f"Cohort „{name}“ an {len(clinic_ids)} Kliniken gesendet")
                self.c.execute("update matches set cohort_id=? where candidate_id in (%s) and clinic_id in (%s)" % (",".join("?" * len(cand_ids)), ",".join("?" * len(clinic_ids))),
                               (coh["id"], *cand_ids, *clinic_ids))
                for cid in clinic_ids:
                    self.seed_thread(cid, coh, parse(coh["sent_at"]))

    def seed_thread(self, clinic_id, cohort, sent_at, cand_ids=None, state=None):
        rng = self.rng
        cl = self.by_clinic.get(str(clinic_id)) or {"name": "Klinik", "town": ""}
        contact, dept = self.pick(CONTACTS)
        slug = re.sub(r"[^a-z]", "", cl["name"].lower())[:12] or "klinik"
        cands = cand_ids if cand_ids is not None else cohort["candidate_ids"]
        if state is None:
            state = rng.choices(THREAD_STATES, THREAD_WEIGHTS)[0]
            if cohort and cohort["status"] == "done":
                state = self.pick(["closed_won", "closed_lost", "no_response", "feedback_pending"])
        cadence = db.policy(self.c)["cadence_clinic_business_days"]
        attempt = {"draft": 0, "intro_sent": 0, "awaiting_reply": rng.choice([0, 1, 2]), "no_response": 3}.get(state, rng.choice([0, 1]))
        next_fu = None
        if state in ("intro_sent", "awaiting_reply") and attempt < 3:
            next_fu = iso(self.business_dt(sent_at + timedelta(days=cadence[attempt])))
        posting = next((j for j in self.jobs_by.get(str(clinic_id), []) if j.get("role_class") == (cohort or {}).get("criteria", {}).get("role_class", "pflegefachkraft")), None) \
            or (self.jobs_by.get(str(clinic_id)) or [None])[0]
        slots = []
        if state in ("scheduling", "scheduled", "feedback_pending", "closed_won", "closed_lost"):
            base = self.now + timedelta(days=rng.randint(1, 4)) if state in ("scheduling", "scheduled") else self.now - timedelta(days=rng.randint(1, 6))
            slots = [{"at": iso(self.business_dt(base + timedelta(days=i))), "format": "video"} for i in range(3)]
        agreed = slots[1]["at"] if slots and state != "scheduling" else None
        th = {"clinic_id": str(clinic_id), "clinic_name": cl["name"], "contact_name": f"{contact} ({dept})", "contact_email": f"{dept.lower().replace(' ', '.')}.{slug}@example.org",
              "mailbox_id": self.mail_ids[1] if cohort else self.mail_ids[0], "cohort_id": cohort["id"] if cohort else None, "state": state, "followup_attempt": attempt, "followup_max": 3,
              "next_followup_at": next_fu, "candidate_ids": cands, "proposed_slots": slots, "agreed_slot": agreed, "round": 2 if state == "feedback_pending" and rng.random() < 0.3 else 1,
              "last_at": None, "created_at": iso(sent_at)}
        tid = db.insert(self.c, "clinic_threads", th)
        th["id"] = tid
        # e-mail conversation
        conv_id = db.insert(self.c, "conversations", {"kind": "clinic", "clinic_thread_id": tid, "channel": "email", "account_id": th["mailbox_id"], "mode": "luna",
                                                      "state": state, "language": "de", "tags": ["COHORT"] if cohort else [], "opened_at": iso(sent_at)})
        first = self.by_cand.get(cands[0]) if cands else None
        v = {"contact_name": contact, "clinic_name": cl["name"], "posting_title": posting["title"] if posting else "Pflegefachkraft (m/w/d)", "initials": first["initials"] if first else "–",
             "profile": anonymised_profile(first) if first else "", "start_from": first["start_from"] if first else "", "format": "Video",
             "slots": "\n".join(f"{i + 1}. {self.slot_label(parse(s['at']))}" for i, s in enumerate(slots)), "slot": self.slot_label(parse(agreed)) if agreed else "",
             "n": len(cands), "criteria": (cohort or {}).get("name", ""), "profiles": "\n\n".join(anonymised_profile(self.by_cand[i]) for i in cands if i in self.by_cand)}
        msgs = []
        t = sent_at
        if state != "draft":
            stage = "cohort_bundle" if cohort and len(cands) > 1 else "clinic_intro"
            msgs.append(("luna", "out", stage, t))
            for a in range(attempt):
                t = self.business_dt(t + timedelta(days=cadence[min(a, 2)]))
                msgs.append(("luna", "out", "clinic_follow_up", t))
        if state in ("interested", "scheduling", "scheduled", "feedback_pending", "closed_won", "closed_lost"):
            t = self.business_dt(t + timedelta(days=rng.randint(1, 4)))
            msgs.append(("clinic", "in", "interested" if state != "closed_lost" or rng.random() < 0.5 else "decline", t))
        if state in ("scheduling", "scheduled", "feedback_pending", "closed_won"):
            t = t + timedelta(hours=rng.randint(1, 5))
            msgs.append(("luna", "out", "propose_slots", t))
        if state in ("scheduled", "feedback_pending", "closed_won"):
            t = self.business_dt(t + timedelta(days=1))
            msgs.append(("clinic", "in", "accept", t))
            t = t + timedelta(minutes=rng.randint(10, 120))
            msgs.append(("luna", "out", "confirm_slot", t))
        if state == "scheduling" and rng.random() < 0.5:
            t = self.business_dt(t + timedelta(days=1))
            msgs.append(("clinic", "in", "counter", t))
        if state in ("feedback_pending", "closed_won"):
            t = parse(agreed) + timedelta(hours=3)
            msgs.append(("luna", "out", "feedback_request", t))
        if state == "closed_won":
            t = self.business_dt(t + timedelta(days=1))
            msgs.append(("clinic", "in", "feedback_pos", t))
        last_at, last_text, last_author = sent_at, "", "luna"
        for author, d, key, at in msgs:
            if d == "out":
                tpl_id = self.tid("email", "de", key)
                r = self.c.execute("select subject, body from templates where id=?", (tpl_id,)).fetchone()
                text = f"Betreff: {render(r['subject'], v)}\n\n{render(r['body'], v)}"
                st = "delivered"
            else:
                tpl_id, text, st = None, self.pick(CLINIC_LINES[key]).format(slot=v["slot"] or self.slot_label(self.business_dt(self.dt(days=3)))), "received"
            db.insert(self.c, "messages", {"conversation_id": conv_id, "dir": d, "author": author, "text": text, "at": iso(at), "status": st, "template_id": tpl_id, "attachments": [], "meta": {}})
            if d == "out":
                self.ev(at, "luna", "email_sent", {"conversation_id": conv_id, "clinic_thread_id": tid, "clinic_id": str(clinic_id)}, f"E-Mail an {cl['name']}: {key}")
            else:
                self.ev(at, "clinic", "email_received", {"conversation_id": conv_id, "clinic_thread_id": tid, "clinic_id": str(clinic_id)}, f"{cl['name']} antwortet: {key}")
            last_at, last_text, last_author = at, text, author
        if state in ("draft",):
            nxt, sla = "us", iso(self.dt(hours=4))
        elif state in ("intro_sent", "awaiting_reply"):
            nxt, sla = "clinic", next_fu
        elif last_author == "clinic":
            nxt, sla = "us", iso(last_at + timedelta(hours=8))
        elif state in ("closed_won", "closed_lost", "no_response"):
            nxt, sla = "none", None
        else:
            nxt, sla = "clinic", iso(self.business_dt(last_at + timedelta(days=3)))
        db.update(self.c, "conversations", conv_id, {"next_actor": nxt, "sla_due_at": sla, "unread": 1 if last_author == "clinic" and state in ("interested", "scheduling") else 0,
                                                     "last_message_at": iso(last_at) if msgs else None, "last_preview": (last_text.split("\n\n", 1)[-1] if last_text else "Entwurf")[:120],
                                                     "closed_at": iso(last_at) if state.startswith("closed") or state == "no_response" else None})
        db.update(self.c, "clinic_threads", tid, {"last_at": iso(last_at) if msgs else None})
        th["conversation_id"] = conv_id
        # interviews
        if agreed and cands:
            cid = cands[0]
            ist = {"scheduled": "confirmed" if parse(agreed) - self.now > timedelta(hours=24) else "reminded", "feedback_pending": "done", "closed_won": "done", "closed_lost": self.pick(["done", "no_show", "cancelled"])}[state]
            fb = None
            if state == "closed_won":
                fb = "Sehr positiv – Angebot ausgesprochen"
            elif state == "closed_lost" and ist == "done":
                fb = "Fachlich gut, Deutsch noch nicht ausreichend"
            db.insert(self.c, "interviews", {"candidate_id": cid, "clinic_id": str(clinic_id), "clinic_thread_id": tid, "at": agreed, "format": "video", "round": th["round"], "status": ist, "feedback": fb})
            self.ev(parse(agreed) - timedelta(days=1), "luna", "interview_scheduled", {"clinic_thread_id": tid, "candidate_id": cid, "clinic_id": str(clinic_id)},
                    f"Gespräch {self.by_cand[cid]['initials'] if cid in self.by_cand else cid} × {cl['name']} am {self.slot_label(parse(agreed))}")
        self.threads.append(th)
        return th

    def seed_standalone_threads(self):
        """One-candidate clinic threads for candidates in profile_sent+ stages (not part of a cohort)."""
        stage_state = {"profile_sent": ["intro_sent", "awaiting_reply", "awaiting_reply", "interested", "no_response"], "interview_scheduling": ["scheduling", "interested"],
                       "interview_scheduled": ["scheduled"], "interviewed": ["feedback_pending"], "offer": ["feedback_pending", "closed_won"], "placed": ["closed_won"]}
        n = 0
        for cand in self.cands:
            if cand["stage"] not in stage_state or n >= 22:
                continue
            m = self.c.execute("select * from matches where candidate_id=? order by score desc limit 1", (cand["id"],)).fetchone()
            if not m:
                continue
            self.seed_thread(m["clinic_id"], None, parse(cand["stage_changed_at"]) - timedelta(days=self.rng.randint(0, 2)), [cand["id"]], self.pick(stage_state[cand["stage"]]))
            n += 1

    # -- approvals, queue
    def seed_approvals(self):
        rng = self.rng
        n = 0
        for conv_id, cand, kind, text, at in self.pending_escalations:
            db.insert(self.c, "approvals", {"kind": "escalation", "risk": "high", "reason": {"salary": "Gehaltsfrage", "visa_legal": "Visum/Rechtsfrage", "asks_for_human": "Kandidat möchte Menschen sprechen", "complaint": "Beschwerde"}[kind],
                                            "context": {"conversation_id": conv_id, "candidate_id": cand["id"]}, "draft": self.escalation_draft(kind, cand),
                                            "suggested": {"action": "reply_and_release", "mode_after": "luna"}, "status": "pending", "created_at": iso(at + timedelta(minutes=1))})
            self.ev(at + timedelta(minutes=1), "luna", "approval_created", {"conversation_id": conv_id, "candidate_id": cand["id"]}, f"Eskalation ({kind}) bei {cand['initials']} – Manager-Entscheidung nötig")
            n += 1
        # match sends (medium)
        for m in self.c.execute("select m.*, c.initials, c.stage as cstage from matches m join candidates c on c.id=m.candidate_id where m.status='approved' order by m.id limit 4").fetchall():
            cl = self.by_clinic.get(m["clinic_id"], {})
            db.insert(self.c, "approvals", {"kind": "match_send", "risk": "medium", "reason": f"Profil {m['initials']} an {cl.get('name')} senden (Score {int(m['score'])})",
                                            "context": {"candidate_id": m["candidate_id"], "match_id": m["id"], "clinic_id": m["clinic_id"], "conversation_id": self.by_cand[m["candidate_id"]].get("conversation_id")},
                                            "draft": render(self.c.execute("select body from templates where id=?", (self.tid("email", "de", "clinic_intro"),)).fetchone()["body"],
                                                            {"contact_name": "Pflegedirektion", "posting_title": "Pflegefachkraft (m/w/d)", "profile": anonymised_profile(self.by_cand[m["candidate_id"]])}),
                                            "suggested": {"action": "send_profile"}, "status": "pending", "created_at": iso(self.dt(hours=-rng.randint(1, 30)))})
        # cohort send
        coh = next(c for c in self.cohorts if c["status"] == "pending_approval")
        db.insert(self.c, "approvals", {"kind": "cohort_send", "risk": "medium", "reason": f"Cohort „{coh['name']}“ an {len(coh['clinic_ids'])} Kliniken senden ({len(coh['candidate_ids'])} Profile)",
                                        "context": {"cohort_id": coh["id"]}, "draft": "Betreff: Pflegekräfte für Ihre offenen Stellen – anonymisierte Profile\n\n" +
                                        "\n\n".join(anonymised_profile(self.by_cand[i]) for i in coh["candidate_ids"] if i in self.by_cand),
                                        "suggested": {"action": "send_cohort"}, "status": "pending", "created_at": iso(self.dt(hours=-5))})
        # message drafts (medium: proposing slots / profile info) and low ones
        convs = self.c.execute("select cv.*, c.initials, c.name, c.language from conversations cv join candidates c on c.id=cv.candidate_id where cv.mode='luna' and cv.next_actor='us' and cv.state in ('profile_sent','interview_prep','qualified','matching') order by cv.id limit 5").fetchall()
        for cv in convs:
            cand = self.by_cand[cv["candidate_id"]]
            m = self.top_match(cand)
            db.insert(self.c, "approvals", {"kind": "message", "risk": "medium", "reason": "Terminvorschlag an Kandidat/in" if cv["state"] == "interview_prep" else "Profil-Versand ankündigen",
                                            "context": {"conversation_id": cv["id"], "candidate_id": cand["id"]},
                                            "draft": render(self.tpl_body("de", "interview_prep" if cv["state"] == "interview_prep" else "profile_sent"),
                                                            {"clinic_name": m["name"] if m else "Klinik", "clinic_town": m["town"] if m else "", "slot": self.slot_label(self.business_dt(self.dt(days=2))), "format": "Video"}),
                                            "suggested": {"action": "send", "template_stage": cv["state"]}, "status": "pending", "created_at": iso(self.dt(hours=-rng.randint(0, 20)))})
        # stage change (low) + campaign change (medium) + a few decided ones
        for cand in [c for c in self.cands if c["stage"] == "docs_pending"][:2]:
            db.insert(self.c, "approvals", {"kind": "stage_change", "risk": "low", "reason": f"{cand['initials']}: docs_pending → qualified (alle Unterlagen erhalten)",
                                            "context": {"candidate_id": cand["id"], "conversation_id": cand.get("conversation_id"), "stage": "qualified"}, "draft": None,
                                            "suggested": {"stage": "qualified"}, "status": "pending", "created_at": iso(self.dt(hours=-rng.randint(1, 10)))})
        db.insert(self.c, "approvals", {"kind": "campaign_change", "risk": "medium", "reason": "Budget Kampagne #2 von 45 € auf 80 € (+78 %) – CPL 9,40 € unter Ziel",
                                        "context": {"campaign_id": 2}, "draft": None, "suggested": {"action": "budget", "daily_budget": 80.0}, "status": "pending", "created_at": iso(self.dt(hours=-2))})
        db.insert(self.c, "approvals", {"kind": "campaign_change", "risk": "high", "reason": "Kampagne #3 pausieren: CPL 31,20 € (+140 % ggü. 7-Tage-Schnitt)",
                                        "context": {"campaign_id": 3}, "draft": None, "suggested": {"action": "pause"}, "status": "pending", "created_at": iso(self.dt(minutes=-40))})
        for i in range(4):
            cand = self.cands[rng.randint(0, len(self.cands) - 1)]
            db.insert(self.c, "approvals", {"kind": self.pick(["message", "match_send"]), "risk": self.pick(["low", "medium"]), "reason": f"{cand['initials']}: Nachricht freigeben",
                                            "context": {"candidate_id": cand["id"], "conversation_id": cand.get("conversation_id")}, "draft": "Hallo, kurze Rückfrage zu Ihren Unterlagen …",
                                            "status": self.pick(["approved", "approved", "rejected", "edited"]), "created_at": iso(self.dt(days=-rng.randint(1, 6))),
                                            "decided_at": iso(self.dt(days=-rng.randint(0, 1))), "decided_by": self.pick(["Ivan", "Sarah"]), "remember": rng.choice([0, 0, 1])})

    def escalation_draft(self, kind, cand):
        return escalation_draft(kind, cand)

    def seed_queue(self):
        rng = self.rng
        cadence = db.policy(self.c)["cadence_candidate_hours"]
        n = 0
        # candidate follow-ups: Luna wrote last, waiting
        for cv in self.c.execute("select cv.*, c.initials, c.stage from conversations cv join candidates c on c.id=cv.candidate_id where cv.kind='candidate' and cv.next_actor='candidate' and cv.mode in ('luna','human') order by cv.id").fetchall():
            if n >= 18:
                break
            due = parse(cv["last_message_at"]) + timedelta(hours=cadence[0])
            kind = "doc_reminder" if cv["state"] in ("collect_docs", "docs_review") else ("nurture" if cv["stage"] == "dormant" else "follow_up_candidate")
            db.insert(self.c, "queue", {"due_at": iso(due), "kind": kind, "target": {"conversation_id": cv["id"], "candidate_id": cv["candidate_id"]},
                                        "reason": {"doc_reminder": f"{cv['initials']}: Unterlagen fehlen seit {cadence[0]} h", "nurture": f"{cv['initials']}: inaktiv, Nurture-Nachricht",
                                                   "follow_up_candidate": f"{cv['initials']}: keine Antwort seit {cadence[0]} h"}[kind],
                                        "status": "scheduled", "attempt": 0, "created_by": "luna"})
            n += 1
        # clinic follow-ups
        for th in self.threads:
            if th["next_followup_at"]:
                db.insert(self.c, "queue", {"due_at": th["next_followup_at"], "kind": "follow_up_clinic", "target": {"clinic_thread_id": th["id"], "conversation_id": th["conversation_id"]},
                                            "reason": f"{th['clinic_name']}: Nachfassen #{th['followup_attempt'] + 1} (Kadenz 3/7/14 Werktage)", "status": "scheduled", "attempt": th["followup_attempt"], "created_by": "luna"})
        # interview reminders / feedback requests
        for iv in self.c.execute("select i.*, t.clinic_name from interviews i join clinic_threads t on t.id=i.clinic_thread_id").fetchall():
            at = parse(iv["at"])
            if iv["status"] in ("confirmed", "reminded"):
                for h in (24, 2):
                    if at - timedelta(hours=h) > self.now - timedelta(hours=2):
                        db.insert(self.c, "queue", {"due_at": iso(at - timedelta(hours=h)), "kind": "interview_reminder", "target": {"interview_id": iv["id"], "candidate_id": iv["candidate_id"], "clinic_thread_id": iv["clinic_thread_id"]},
                                                    "reason": f"Erinnerung {h} h vor Gespräch bei {iv['clinic_name']}", "status": "scheduled", "created_by": "luna"})
            elif iv["status"] == "done" and not iv["feedback"]:
                db.insert(self.c, "queue", {"due_at": iso(at + timedelta(days=1)), "kind": "feedback_request", "target": {"interview_id": iv["id"], "clinic_thread_id": iv["clinic_thread_id"], "candidate_id": iv["candidate_id"]},
                                            "reason": f"Feedback von {iv['clinic_name']} erbitten", "status": "scheduled", "created_by": "luna"})
        # cohort send (draft scheduled), campaign checks
        draft = next(c for c in self.cohorts if c["status"] == "draft")
        db.insert(self.c, "queue", {"due_at": iso(self.business_dt(self.dt(days=1))), "kind": "cohort_send", "target": {"cohort_id": draft["id"]}, "reason": f"Cohort „{draft['name']}“ versenden (nach Freigabe)", "status": "scheduled", "created_by": "operator"})
        for cid in (1, 3, 6):
            db.insert(self.c, "queue", {"due_at": iso(self.dt(hours=rng.randint(1, 30))), "kind": "campaign_check", "target": {"campaign_id": cid}, "reason": f"Kampagne #{cid}: CPL und Lead-Qualität prüfen", "status": "scheduled", "created_by": "luna"})
        # history
        for i in range(6):
            cv = self.pick(self.cands)
            db.insert(self.c, "queue", {"due_at": iso(self.dt(days=-rng.randint(1, 5))), "kind": self.pick(["follow_up_candidate", "doc_reminder"]), "target": {"candidate_id": cv["id"], "conversation_id": cv.get("conversation_id")},
                                        "reason": f"{cv['initials']}: Nachfassen", "status": self.pick(["done", "done", "cancelled", "failed"]), "attempt": 1, "created_by": "luna",
                                        "result": self.pick(["gesendet", "Kandidat/in hatte bereits geantwortet", "Nummer rate-limited"])})

    def seed_campaign_rollup(self):
        for cid in self.campaign_ids:
            q = self.c.execute("select stage, count(*) n from candidates where source_campaign_id=? group by stage", (cid,)).fetchall()
            by = {r["stage"]: r["n"] for r in q}
            qual = sum(n for s, n in by.items() if s in STAGES and STAGES.index(s) >= STAGES.index("qualified"))
            inter = sum(n for s, n in by.items() if s in STAGES and STAGES.index(s) >= STAGES.index("interview_scheduled"))
            db.update(self.c, "campaigns", cid, {"qualified": qual, "interviews": inter, "placed": by.get("placed", 0)})
        # alerts as events
        self.ev(self.dt(minutes=-35), "system", "alert", {"campaign_id": 3}, "CPL-Spike Kampagne #3 (Regensburg/Oberpfalz): 31,20 € vs. 13,00 € Ø – Pause vorgeschlagen")
        self.ev(self.dt(hours=-3), "system", "alert", {"account_id": self.wa_ids[1]}, "WhatsApp „Luna DE 2“: Tageslimit erreicht (250/250), Qualität gelb")
        self.ev(self.dt(hours=-6), "system", "alert", {"account_id": self.mail_ids[1]}, "Mailbox „Partner / Cohorts“: Bounce-Rate 6,2 % über Schwelle")
        self.ev(self.dt(days=-1, hours=-2), "operator", "campaign_paused", {"campaign_id": 4}, "Kampagne #4 (Augsburg EN) pausiert – Lead-Qualität (A2) zu niedrig")
        self.ev(self.dt(days=-2), "operator", "policy_changed", {}, "Autopilot-Modus: assist → auto (low-risk Nachrichten gehen ohne Freigabe)")

    def flush_events(self):
        self.events.sort(key=lambda e: e[0])
        for at, actor, kind, target, detail in self.events:
            db.event(self.c, at, actor, kind, target, detail)

    def run(self):
        self.pending_escalations, self.threads = [], []
        self.seed_templates()
        self.seed_accounts()
        self.seed_campaigns()
        self.seed_candidates(self.n_candidates)
        self.seed_matches()
        self.seed_cohorts()
        self.seed_standalone_threads()
        self.seed_approvals()
        self.seed_queue()
        self.seed_campaign_rollup()
        self.flush_events()


def seed(reset=True, n_candidates=140):
    """Rebuild the dataset. Returns db.counts()."""
    if reset and db.SQLITE_PATH.exists():
        db.SQLITE_PATH.unlink()
    init()
    clinics, jobs = load_registry_source()
    rng = random.Random(42)
    now = parse(db.DEFAULT_POLICY["sim_now"])
    with db._lock, db.db() as c:
        cache_registry(c, clinics, jobs)
        for k, v in db.DEFAULT_POLICY.items():
            db.set_policy(c, k, v)
        Seeder(c, now, rng, clinics, jobs, n_candidates).run()
        c.commit()
        return db.counts(c)


def main(argv=None):
    ap = argparse.ArgumentParser(description="rebuild data/autopilot.sqlite (synthetic, deterministic)")
    ap.add_argument("--reset", action="store_true", help="delete the file first")
    ap.add_argument("--n", type=int, default=140)
    a = ap.parse_args(argv)
    counts = seed(reset=True if a.reset else not db.SQLITE_PATH.exists(), n_candidates=a.n)
    for k, v in counts.items():
        print(f"{k:16s} {v}")


if __name__ == "__main__":
    main()
