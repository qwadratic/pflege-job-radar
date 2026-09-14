"""TASK-68: end-to-end synthetic funnel test. Unlike tests/test_wa_luna_personas.py (a fixed,
hand-written candidate script -- deterministic on purpose, for stable regression assertions), BOTH
sides of every conversation here are live model calls: a small `_CandidateAgent` plays the
candidate from a short persona brief and improvises its own replies, while `app.wa.luna_brain.turn`
plays Valentina exactly as in production. This is the harness's answer to "prove it is not just a
fixed script" for the funnel as a whole, run end-to-end through to a real queue entry.

The candidate agent only types. TASK-96 review: once Valentina asks for the documents with every other
pre-close gate settled, the persona's next message is the upload of its CV and qualification document
(PERSONA_DOCUMENTS, written onto the card like app/wa/api.py:_ingest_media) instead of an agent reply.

Marked ``llm``: every turn on both sides spawns the real `claude` CLI. Run explicitly:
``pytest -q -m llm tests/test_wa_luna_e2e_funnel.py -s`` (the ``-s`` shows the report as it runs).
Skipped automatically if the `claude` CLI is not on PATH.
"""
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import queue as Q
from app.wa.luna import contacts as CT
from tests.luna_fixture_tools_server import use_fixture_board
from tests.test_wa_luna_personas import _send_document

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not shutil.which(C.LUNA_CLAUDE_BIN),
                        reason=f"{C.LUNA_CLAUDE_BIN!r} is not on PATH -- needs the Claude Code CLI installed"),
]

MAX_TURNS = 12
CANDIDATE_MODEL = "claude-haiku-4-5"          # cheaper side: a candidate reply is a small, low-stakes task
ARTIFACT_DIR = Path(__file__).parent / ".artifacts"


class _CandidateAgent:
    """Plays the candidate side: a persistent Claude Code session (own session dir, own model)
    given a short persona brief, replying to whatever Valentina's last bubbles were. Free-text
    reply, not the structured JSON contract luna_brain.Client uses -- a candidate does not owe the
    harness a schema."""

    def __init__(self, persona_prompt, session_dir):
        self.persona_prompt = persona_prompt
        self.session_dir = session_dir
        self.session_id = None
        session_dir.mkdir(parents=True, exist_ok=True)

    def reply_to(self, valentina_bubbles):
        fresh = self.session_id is None
        this_session_id = self.session_id or str(uuid.uuid4())
        session_flags = ["--session-id", this_session_id] if fresh else ["--resume", this_session_id]
        # TASK-82 follow-up: this used to key the opening-message prompt off `valentina_bubbles`
        # being empty, which also fires on a legitimate mid-conversation no_send turn (Valentina
        # has nothing new to say, e.g. while "waiting" on a promised upload) -- the candidate then
        # got told it was writing to Valentina "for the first time" again and looped its own
        # opening line verbatim (observed live: a persona that had already been chatting for 5
        # turns re-introduced itself four times in a row instead of naturally continuing). `fresh`
        # (an actual new session, never used before) is the correct signal, not bubble emptiness.
        if fresh:
            user_text = "(Sie schreiben Valentina zum ersten Mal auf WhatsApp. Schreiben Sie Ihre Eröffnungsnachricht.)"
        elif valentina_bubbles:
            user_text = "\n".join(valentina_bubbles)
        else:
            user_text = ("(Valentina hat gerade nichts Neues geschrieben, vielleicht wartet sie auf etwas von "
                        "Ihnen. Schreiben Sie kurz weiter -- z.B. eine kurze Nachfrage oder ein kurzes Update "
                        "-- schreiben Sie NICHT erneut Ihre Eröffnungsnachricht, das Gespräch läuft bereits.)")
        proc = subprocess.run(
            [C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
             "--model", CANDIDATE_MODEL, "--effort", "low",
             "--system-prompt", self.persona_prompt, *session_flags],
            input=user_text, capture_output=True, text=True, timeout=60, cwd=self.session_dir,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"candidate persona CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            raise RuntimeError(f"candidate persona CLI reported an error: {envelope.get('result')!r}")
        text = str(envelope.get("result") or "").strip()
        if not text:
            raise RuntimeError(f"candidate persona CLI returned no text: {envelope!r}")
        self.session_id = str(envelope.get("session_id") or this_session_id)
        return text


# TASK-74: rewritten after comparing these live-improvised personas against aggregate, non-
# identifying stats from 701 real WhatsApp messages -- the original style here (a "Hallo
# Valentina!" self-introduction, full sentences, warm sign-offs) read as an organized written
# email, not a rushed WhatsApp text: real candidates were overwhelmingly short (median ~3 words,
# ~90% under 10), answered with statements more than questions, and about a third of their inbound
# messages were bare one-word acknowledgements. This shared style block is prepended to every
# persona's own facts below instead of duplicating near-identical style boilerplate three times.
_STYLE_GUIDE = (
    "Schreiben Sie wie eine echte Person, die schnell auf dem Handy tippt -- nie wie eine "
    "Assistentin oder ein Chatbot, und erwähnen Sie unter keinen Umständen, dass dies ein Test, "
    "eine Simulation oder eine Übung ist, selbst wenn Valentina sich wiederholt oder das Gespräch "
    "seltsam wirkt. Halten Sie jede Nachricht sehr kurz: meistens 3-10 Wörter, selten mehr als ein "
    "kurzer Satz. Ein einziges Wort oder ein kurzer Halbsatz ('Ok', 'Ja klar', 'Passt', 'München "
    "wäre gut') ist der Normalfall, keine Ausnahme -- nicht bei jeder Antwort ausführlich werden. "
    "Antworten Sie meistens mit einer kurzen Aussage statt einer Frage; stellen Sie nur gelegentlich "
    "selbst eine kurze Rückfrage. Keine Begrüßung oder Verabschiedung mitten im Gespräch (kein "
    "erneutes 'Hallo' oder 'Viele Grüße', außer ganz in der ersten Nachricht), keine "
    "Selbstvorstellung mit vollständigem Lebenslauf in einer einzigen Nachricht -- Fakten kommen "
    "nach und nach heraus, wie Valentina sie erfragt, nicht alle auf einmal. Bleiben Sie beim "
    "'Sie' -- wechseln Sie nie von sich aus zu 'du', auch wenn Valentina das täte. Gelegentlich "
    "klein geschrieben oder ganz ohne Satzzeichen ist in Ordnung, wie beim schnellen Tippen."
)

PERSONAS = {
    "anna_urkunde": _STYLE_GUIDE + (
        " Sie sind Anna, 29, Gesundheits- und Krankenpflegerin aus Polen. Ihre deutsche "
        "Berufsanerkennung (Urkunde) ist bereits erteilt. Sie wollen nach Bayern ziehen, am "
        "liebsten nach München, Fachbereich Intensivstation, und leben allein. Sie schreiben "
        "Valentina, einer digitalen Recruiting-Assistentin, auf WhatsApp -- immer als Anna selbst, "
        "nie als Assistentin. Seien Sie kooperativ: beantworten Sie jede Frage ehrlich anhand "
        "dieser Angaben, und stimmen Sie zu, wenn Valentina nach einer anonymisierten Weiterleitung "
        "Ihres Profils an passende Kliniken fragt."
    ),
    "carlos_defizit": _STYLE_GUIDE + (
        " Sie sind Carlos, 35, examinierter Krankenpfleger aus Kolumbien. Sie haben noch keine "
        "volle Anerkennung, aber bereits einen Defizitbescheid erhalten. Sie suchen eine Stelle in "
        "Augsburg, Fachbereich Innere Medizin, und ziehen allein. Sie schreiben Valentina, einer "
        "digitalen Recruiting-Assistentin, auf WhatsApp -- immer als Carlos selbst, nie als "
        "Assistentin. Seien Sie kooperativ: beantworten Sie jede Frage ehrlich anhand dieser "
        "Angaben, und stimmen Sie zu, wenn Valentina nach einer anonymisierten Weiterleitung Ihres "
        "Profils an passende Kliniken fragt."
    ),
    "mai_kenntnispruefung": _STYLE_GUIDE + (
        " Sie sind Mai, 27, Pflegefachkraft aus Vietnam. Sie haben die Kenntnisprüfung bereits "
        "bestanden, die offizielle Urkunde ist noch beim Amt in Bearbeitung. Sie sind bei der "
        "Region flexibel, Hauptsache eine Klinik in Bayern, Fachbereich ist Ihnen nicht so wichtig. "
        "Sie ziehen allein. Sie schreiben Valentina, einer digitalen Recruiting-Assistentin, auf "
        "WhatsApp -- immer als Mai selbst, nie als Assistentin. Seien Sie kooperativ: beantworten "
        "Sie jede Frage ehrlich anhand dieser Angaben, und stimmen Sie zu, wenn Valentina nach "
        "einer anonymisierten Weiterleitung Ihres Profils an passende Kliniken fragt."
    ),
}


# TASK-96 review: the close needs a CV and the qualification document for the path in card.documents, and the
# candidate agent can only type. Each persona's files as (document_type, text, certificate_level): the
# kenntnispruefung path asks for the Defizitbescheid too (prompts.py DOCUMENT ASK).
PERSONA_DOCUMENTS = {
    "anna_urkunde": [("lebenslauf", "Lebenslauf Anna, Gesundheits- und Krankenpflegerin, 6 Jahre Intensivstation",
                      "unknown"),
                     ("urkunde", "Urkunde über die Erlaubnis zum Führen der Berufsbezeichnung Pflegefachfrau",
                      "fachkraft")],
    "carlos_defizit": [("lebenslauf", "Lebenslauf Carlos, Krankenpfleger, Innere Medizin", "unknown"),
                       ("defizitbescheid", "Bescheid über die Gleichwertigkeitsprüfung: wesentliche Unterschiede",
                        "unknown")],
    "mai_kenntnispruefung": [("lebenslauf", "Lebenslauf Mai, Pflegefachkraft", "unknown"),
                             ("defizitbescheid", "Bescheid über die Gleichwertigkeitsprüfung: Kenntnisprüfung",
                              "unknown")],
}
_DOCUMENT_NAME_RE = re.compile(r"lebenslauf|urkunde|defizitbescheid", re.I)


def _documents_asked(slots, valentina_bubbles):
    """Valentina's last turn was the document ask: documents is the one gate before the close still open,
    and her bubbles name a document."""
    board = LB.requirement_scoreboard(slots)
    settled = all(board[k] == "satisfied" for k in ("qualification", "city_or_department", "housing"))
    return settled and board["documents"] == "open" and bool(_DOCUMENT_NAME_RE.search(" ".join(valentina_bubbles)))


def _fixture_board():
    rows = []
    plan = [("München", "Oberbayern", "Intensiv/IMC", "c1", "Klinikum München"),
            ("Augsburg", "Schwaben", "Innere Medizin", "c2", "Klinikum Augsburg"),
            ("Bayreuth", "Oberfranken", "Chirurgie/Orthopädie", "c3", "Klinikum Bayreuth")]
    for i, (city, bezirk, dept, clinic_id, clinic_name) in enumerate(plan):
        rows.append({"posting_id": i + 1, "title": f"Pflegefachkraft {dept}", "role_class": "pflegefachkraft",
                     "department_hint": dept, "city": city, "clinic_town": city, "regierungsbezirk": bezirk,
                     "clinic_id": clinic_id, "clinic_name": clinic_name, "employer": clinic_name,
                     "employment_types": ["vollzeit"], "enr_housing": True, "verify_status": "live",
                     "status": "open", "first_published": "2026-09-01", "fresh": True,
                     "source_url": f"https://example.org/job/{i + 1}"})
    clinics = [{"clinic_id": "c1", "name": "Klinikum München", "town": "München", "regierungsbezirk": "Oberbayern",
               "beds": 800, "jobs_open": 1, "fachrichtungen": []},
              {"clinic_id": "c2", "name": "Klinikum Augsburg", "town": "Augsburg", "regierungsbezirk": "Schwaben",
               "beds": 500, "jobs_open": 1, "fachrichtungen": []},
              {"clinic_id": "c3", "name": "Klinikum Bayreuth", "town": "Bayreuth", "regierungsbezirk": "Oberfranken",
               "beds": 400, "jobs_open": 1, "fachrichtungen": []}]
    return rows, clinics


@pytest.fixture()
def board(tmp_path, monkeypatch):
    rows, clinics = _fixture_board()
    D._snap.update({"at": time.time(), "jobs": rows, "clinics": clinics,
                    "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    use_fixture_board(monkeypatch, tmp_path)
    # Seed one known contact -- deliberately only one of the three clinics, so the report also
    # shows the honest "contact unknown" case rather than a suspiciously perfect 3/3.
    conn = CT.db()
    CT.save_contact(conn, "c1", "pflegedirektion@klinikum-muenchen.example", "board", "high")
    conn.close()
    return tmp_path


def _run_persona(name, persona_prompt, session_root):
    agent = _CandidateAgent(persona_prompt, session_root / f"candidate_{name}")
    thread = {"slots": {}, "asked": []}
    valentina_bubbles = []
    valentina_buttons = []
    transcript = []
    uploaded = False
    for turn in range(1, MAX_TURNS + 1):
        if valentina_buttons:
            # TASK-80: Valentina just offered the anonymized send and attached real Ja/Nein
            # buttons -- a real WhatsApp UI renders those as taps, not free text, so simulate the
            # tap a cooperative persona (every persona here is written to consent) would make,
            # rather than asking the live candidate LLM to type something a button UI wouldn't.
            yes = next(b for b in valentina_buttons if b["id"] == LB.CONSENT_YES_ID)
            candidate_text, button_id, logged = yes["title"], yes["id"], yes["title"]
        elif not uploaded and _documents_asked(thread["slots"], valentina_bubbles):
            # TASK-96 review: the reply to the document ask is the upload -- both files land on the card the way
            # app/wa/api.py:_ingest_media writes them, then the turn runs with empty text, as a media message does.
            for document_type, text, certificate_level in PERSONA_DOCUMENTS[name]:
                thread = _send_document(thread, document_type, text, certificate_level)
            uploaded = True
            candidate_text, button_id = "", None
            logged = " ".join(f"[{t}]" for t, _, _ in PERSONA_DOCUMENTS[name])
        else:
            candidate_text = logged = agent.reply_to(valentina_bubbles)
            button_id = None
        d = LB.turn(candidate_text, thread, button_id=button_id)
        thread = {"slots": d["slots"], "asked": d["asked"]}
        valentina_bubbles = d["bubbles"]
        valentina_buttons = d["buttons"]
        transcript.append({"turn": turn, "candidate": logged, "valentina": valentina_bubbles})
        if thread["slots"].get("anonymous_send_consent"):
            return {"name": name, "converged": True, "turns": turn, "card": thread["slots"], "transcript": transcript}
    return {"name": name, "converged": False, "turns": MAX_TURNS, "card": thread["slots"], "transcript": transcript}


def _write_report(outcomes, mailing_list):
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# E2E synthetic funnel report (TASK-68)", ""]
    for o in outcomes:
        status = "CONSENTED" if o["converged"] else "DID NOT CONVERGE"
        lines.append(f"## {o['name']} -- {status} in {o['turns']} turns")
        for t in o["transcript"]:
            lines.append(f"- turn {t['turn']}: candidate: {t['candidate']!r}")
            lines.append(f"  valentina: {t['valentina']!r}")
        lines.append("")
    lines.append("## Mailing-list view")
    lines.append(f"total rows: {len(mailing_list)}")
    for row in mailing_list:
        lines.append(f"- {row['phone']} -> clinic {row['clinic_id']} (score {row['score']}, "
                     f"contact: {row.get('contact_email') or 'UNKNOWN'})")
    text = "\n".join(lines)
    (ARTIFACT_DIR / "e2e_funnel_report.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    return text


def test_the_funnel_runs_end_to_end_for_three_synthetic_personas_and_produces_a_mailing_list(board):
    session_root = board / "candidate_sessions"
    outcomes = [_run_persona(name, prompt, session_root) for name, prompt in PERSONAS.items()]

    consented = [o for o in outcomes if o["converged"]]
    for o in outcomes:
        if not o["converged"]:
            print(f"WARNING: persona {o['name']!r} did not converge within {MAX_TURNS} turns "
                  f"-- not silently counted as success, just not included in the queue below.")

    phones = {}
    for i, o in enumerate(consented):
        phone = f"+4915000000{i:02d}"
        phones[o["name"]] = phone
        Q.build_queue_entry(phone, o["card"])

    conn = Q.db()
    try:
        mailing_list = Q.mailing_list_rows(conn)
    finally:
        conn.close()

    report = _write_report(outcomes, mailing_list)

    assert len(consented) >= 3, (
        f"expected all 3 personas to reach consent (cooperative scripts, generous turn budget), "
        f"got {len(consented)}/3 -- see the printed report above for the transcripts")
    assert mailing_list, "the mailing-list view must have at least one row once candidates consented"
    resolved = [r for r in mailing_list if r.get("contact_email")]
    assert resolved, (
        f"expected at least one clinic with a resolved contact email (c1 was seeded with one), "
        f"got: {mailing_list!r}")
    assert report   # the report text itself is the human-facing deliverable, not just these asserts
