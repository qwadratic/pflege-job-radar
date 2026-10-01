#!/usr/bin/env python3
"""Generates tests/fixtures/wa_pro_api/{threads,thread_detail,messages,health}.json from the REAL
board-scope Pro API routes (app/wa/pro_api.py), run against a synthetic, seeded, throwaway wa.sqlite
-- never data/wa.sqlite -- through the real ASGI app (app/wa/asgi.py) in a starlette TestClient.

WHY THIS EXISTS. The committed fixtures used to be hand-written JSON. Hand-written JSON drifts:
threads.json was missing synced_at/synced_source (app/wa/pro_api.py emits both on every envelope,
review item 13) and carried last_message/ball combinations the real functions (app/wa/store.py:
last_message, app/wa/luna/reporting.py:ball_for) cannot actually produce -- see the report this
generator shipped with for the specific rows. There were no fixtures at all for thread detail, the
messages route or health. This module seeds a real database and reads the real routes instead, so
every field it writes is a field the code actually sends, nothing invented by hand.

DETERMINISM. Three real, environment-dependent inputs would otherwise make two runs of this
generator produce different bytes for the same code -- all three are patched for the duration of
_generator_env's `with` block, never in production:
  - app.wa.store.now_iso -- real wall-clock "now". Replaced by a Clock this module drives by hand:
    every timestamp a seeded row carries is one this module chose, in a fixed script.
  - app.wa.store.secrets.token_hex -- the one source of real randomness in the harness, used only to
    mint a thread's opaque thread_id (app/wa/store.py:_ensure_thread_id). Replaced by a plain
    sequential counter: the seeding below mints ids in a fixed order (no concurrency, no other
    randomness anywhere in this module), so the same phone gets the same id every run.
  - app.wa.pro_api._source -- "harness@" + socket.gethostname(): whichever machine last regenerated
    the fixture would stamp its own hostname into the committed file. Replaced by the fixed string
    the hand-written fixture already used ("harness@fixture-host"), so this is not even a new value,
    just a real seam for one the hand-written version had baked in by accident.
`stuck_reply` (app/wa/api.py:_is_stuck) is the one field that is NOT frozen this way: it compares a
seeded past timestamp to the REAL wall clock. That is fine -- every seeded timestamp here is already
years in the past, so the boolean it produces stays true forever forward, never flips back.

The sales_brain-backed /api/wa/pro/leads route is Daria-scope (SCOPE_DARIA), not board-scope, and is
deliberately not touched here -- see docs/whatsapp.md's Pro API section for the board/Daria split.

Usage:
    python -m tools.wa_pro_fixtures --write     regenerate the committed fixtures in place
    python -m tools.wa_pro_fixtures            print what would be written, write nothing
    python -m tools.wa_pro_fixtures --check     exit 1 if the committed files differ from a fresh run

generate() is also the entry point tests/test_wa_pro_fixtures_generated.py (the drift guard) calls
directly: it runs this generator into a tmp directory and asserts the result is byte-identical to the
committed fixtures, so a change to pro_api.py/pro_models.py/store.py that changes what the API
actually sends fails that test until someone reruns this module with --write.
"""
import argparse
import contextlib
import itertools
import json
import pathlib
import sys
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "wa_pro_api"
FIXTURE_FILES = ("threads.json", "thread_detail.json", "messages.json", "health.json")

# Dummy, obviously-fake bearer tokens -- never read from .env, never anything a real deployment uses.
READ_TOKEN = "fixture-read-token-not-real"
WRITE_TOKEN = "fixture-write-token-not-real"
RH = {"Authorization": f"Bearer {READ_TOKEN}"}
WH = {"Authorization": f"Bearer {WRITE_TOKEN}"}

# Synthetic clinics -- the same placeholder-name convention tests/test_wa_pro_api.py already uses for
# its own D._snap fixture (``"Klinikum Test"``/``"Klinikum Zwei"``), never a real clinic's name.
CLINICS = [
    {"clinic_id": "c1", "name": "Klinikum Test", "town": "Testort"},
    {"clinic_id": "c2", "name": "Klinikum Zwei", "town": "Zweistadt"},
]

# A campaign template in the same shape tests/test_wa_pro_api.py already renders through the real
# app.wa.meta.render_template -- generic marketing copy, no client or clinic name in it.
CAMPAIGN_TEMPLATE = {"name": "test_campaign", "language": "de", "parameter_format": "POSITIONAL",
                     "components": [{"type": "BODY", "text": "Guten Tag {{1}}, neue Stellen fuer Sie.",
                                     "example": {"body_text": [["Frau X"]]}}]}
CAMPAIGN_PARAMS = {"body": ["Frau Test"]}

# Raw phones: all obviously fake, all "+49170000<last4>"-style repeats (one +43 number for the
# suppressed thread) in the exact pattern the hand-written fixture already used for its masked
# values -- phones.phone_masked(PHONE_ESCALATED) == "+49 ••• ••• 2222", etc.
PHONE_TEST = "+491700000001"
PHONE_ESCALATED = "+491700002222"
PHONE_STUCK = "+491700003333"
PHONE_SUPPRESSED = "+431700004444"
PHONE_CONSENTED = "+491700005555"
PHONE_TOMBSTONE = "+491700006666"
PHONE_DECLINED = "+491700007777"


class _Clock:
    """Deterministic stand-in for app.wa.store.now_iso: every call returns whatever this module last
    `.set()`, never the wall clock. Driven by hand, one `.set()` per seeded event, so the generator's
    output is a fixed script, not a function of when it happens to run."""

    def __init__(self, start):
        self.value = start

    def set(self, iso):
        self.value = iso
        return iso

    def now_iso(self):
        return self.value


def _deterministic_token_hex():
    """Replacement for secrets.token_hex inside app.wa.store._ensure_thread_id -- the only place this
    harness mints a Pro API thread_id (see that function's own docstring). A plain sequential
    counter, formatted the same length secrets.token_hex(nbytes) would produce, so the minted ids
    still look like the real ones (``t_<16 hex chars>``) without any real randomness."""
    counter = itertools.count(1)

    def fake(nbytes):
        return format(next(counter), "0%dx" % (nbytes * 2))

    return fake


@contextlib.contextmanager
def _generator_env(sqlite_path):
    """Everything that makes one run of this generator pure and repeatable, scoped to this `with`
    block only -- every patch here is undone when the block exits, and app/wa/pro_api.py's own code
    runs completely unmodified; see the module docstring for why each of these three is needed."""
    from app.wa import config as C
    from app.wa import pro_api as PA
    from app.wa import store as ST

    clock = _Clock("2026-08-01T00:00:00+00:00")
    with mock.patch.object(C, "SQLITE_PATH", sqlite_path), \
         mock.patch.dict("os.environ", {"WA_API_TOKEN": READ_TOKEN, "WA_API_WRITE_TOKEN": WRITE_TOKEN}), \
         mock.patch.object(ST, "now_iso", clock.now_iso), \
         mock.patch.object(ST.secrets, "token_hex", _deterministic_token_hex()), \
         mock.patch.object(PA, "_source", lambda: "harness@fixture-host"):
        yield clock


@contextlib.contextmanager
def _synthetic_board():
    """Points app.data's snapshot at the synthetic CLINICS above instead of letting
    app.wa.pro_api._matched_clinics's app.data.clinic() lookups fall through to a real Supabase read
    (app/data.py:refresh) -- same technique tests/test_wa_pro_api.py's own `env` fixture uses.
    Restores the previous snapshot dict and refresh() on exit, so this never leaks into whatever else
    is running in the same process (relevant only when generate() is called from a test, not for the
    standalone CLI)."""
    import time

    from app import data as D

    previous_snap = dict(D._snap)
    by_clinic = {c["clinic_id"]: c for c in CLINICS}
    D._snap.clear()
    D._snap.update({"at": time.time(), "jobs": [], "clinics": CLINICS, "by_clinic": by_clinic,
                    "facets": {}, "taxonomy": {}, "loading": False, "error": None})
    try:
        with mock.patch.object(D, "refresh", lambda: D._snap):
            yield
    finally:
        D._snap.clear()
        D._snap.update(previous_snap)


def _save(c, t):
    from app.wa import store as ST

    ST.save_thread(c, t)


def _seed_test_thread(c, client, clock):
    """Hidden-by-default test thread -- a campaign send, one exchange, marked is_test after the
    fact (the engine's own order: mark_test_thread is the only writer of is_test, see store.py)."""
    from app.wa import meta as META
    from app.wa import store as ST

    phone = PHONE_TEST
    rendered = META.render_template(CAMPAIGN_TEMPLATE, CAMPAIGN_PARAMS)
    clock.set("2026-09-20T08:00:00+00:00")
    ST.record_campaign_send(c, phone, "wamid.test-campaign", rendered, "camp-winter-2026", sent_at=clock.value)

    clock.set("2026-09-20T08:05:00+00:00")
    ST.record_inbound(c, phone, "wamid.test-in1", "Hallo, ich interessiere mich fuer eine Stelle in Bayern.",
                      kind="text")
    t = ST.thread(c, phone)
    t["slots"]["region"] = "Oberbayern"
    t["slots"]["stage"] = "qualification"
    t["slots"]["stage_at"] = clock.value
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-20T08:05:30+00:00")
    ST.record_outbound(c, phone, "wamid.test-out1",
                       "Willkommen bei Pflege Job Radar! Erzaehlen Sie mir etwas ueber sich.", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    ST.mark_test_thread(c, phone, True)
    return phone


def _seed_escalated_thread(c, client, clock):
    """Region/qualification/city/housing all answered, one CV already in (cv_document satisfied),
    the qualification document still missing (qualification_document open) -- stage "documents",
    truthfully: cv_document must be satisfied for funnel_stage to reach "documents" at all
    (app/wa/luna_brain.py:_STAGE_GATES puts cv_document BEFORE qualification_document), unlike the
    hand-written fixture this replaces, which paired stage "documents" with cv_document "open" -- a
    combination the real funnel_stage() cannot produce (see the report). Ends on an explicit human
    request, escalated exactly once."""
    from app.wa.luna import escalation as ESC
    from app.wa import store as ST

    phone = PHONE_ESCALATED
    clock.set("2026-09-18T09:00:00+00:00")
    ST.thread(c, phone)
    ST.record_inbound(c, phone, "wamid.esc-in1",
                      "Guten Tag, ich bin examinierte Pflegefachkraft und suche eine Stelle in Schwaben.",
                      kind="text")
    t = ST.thread(c, phone)
    t["slots"]["region"] = "Schwaben"
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-18T09:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.esc-out1", "Schoen, dass Sie sich melden! Haben Sie schon eine "
                       "Anerkennungs-Urkunde?", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-19T10:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.esc-in2",
                      "Meine Urkunde liegt schon vor, ich arbeite auf der Intensivstation.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["qualification_path"] = "urkunde"
    t["slots"]["department_pref"] = "Intensiv/IMC"
    t["last_inbound_at"] = clock.value
    t["turns"] = 2
    _save(c, t)

    clock.set("2026-09-19T10:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.esc-out2", "Verstanden. In welcher Stadt moechten Sie arbeiten, "
                       "und brauchen Sie eine Wohnung?", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-20T11:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.esc-in3",
                      "Ich moechte am liebsten in Augsburg arbeiten, und ja, ich brauche eine Wohnung "
                      "fuer eine Person.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["city"] = "Augsburg"
    t["slots"]["housing_needed"] = True
    t["slots"]["people_count"] = 1
    t["slots"]["match_branch"] = "narrow"
    t["last_inbound_at"] = clock.value
    t["turns"] = 3
    _save(c, t)

    clock.set("2026-09-20T11:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.esc-out3", "Danke! Bitte schicken Sie mir noch Ihren "
                       "Lebenslauf und Ihre Urkunde.", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    # The CV arrives and is classified -- cv_document becomes satisfied; no qualification document
    # ever arrives, so qualification_document stays open (funnel_stage -> "documents").
    clock.set("2026-09-20T11:08:00+00:00")
    ST.record_inbound(c, phone, "wamid.esc-doc1", "", kind="document", meta={"media_filename": "lebenslauf.pdf"})
    doc_id = ST.record_document(c, phone, "wamid.esc-doc1", "media-esc-cv", "document", "application/pdf",
                                "lebenslauf.pdf", "/documents/synthetic/esc-lebenslauf.pdf",
                                "synthetic-sha-esc-cv", 204800)
    ST.set_document_classification(c, doc_id, "lebenslauf", None, "cv_text")
    t = ST.thread(c, phone)
    t["slots"]["documents"] = [{"id": doc_id, "document_type": "lebenslauf", "certificate_level": None}]
    t["slots"]["stage"] = "documents"
    t["slots"]["stage_at"] = clock.value
    t["last_inbound_at"] = clock.value
    t["turns"] = 4
    _save(c, t)

    clock.set("2026-09-22T14:02:00+00:00")
    ST.record_outbound(c, phone, "wamid.esc-out4", "Danke fuer den Lebenslauf! Die Urkunde fehlt uns noch.",
                       kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-22T14:10:00+00:00")
    ST.record_inbound(c, phone, "wamid.esc-final", "Kann ich bitte mit einem Menschen sprechen?", kind="text")
    t = ST.thread(c, phone)
    t["last_inbound_at"] = clock.value
    t["turns"] = 5
    clock.set("2026-09-22T14:10:05+00:00")
    ESC.record_escalation(t["slots"], "explicit_human_request", "candidate explicitly asked for a human")
    _save(c, t)

    # The brain still drafts the next reply after the escalation (app/wa/api.py's AUTOSEND-off/
    # scope_refusal branch records it as kind="draft" and, truthfully, never touches
    # last_outbound_at -- that field is only set on an actual send). So this thread's last_message
    # becomes the unsent draft (ball "them", direction "out") while last_outbound_at stays pinned to
    # the last real send above: a human sorting by last_outbound_at would not see this thread as
    # freshly touched, even though there is a drafted reply sitting unsent (see the report).
    clock.set("2026-09-22T14:15:00+00:00")
    ST.record_outbound(c, phone, None, "Habe notiert -- eine Kollegin meldet sich persoenlich bei Ihnen, "
                       "sobald die Anerkennungs-Urkunde da ist.", kind="draft",
                       meta={"action": "qualification_document_request"})
    return phone


def _seed_stuck_thread(c, client, clock):
    """Bridge-rail thread stuck on an unanswered voice note: the real transcript lands in the
    message's own body and meta.transcript (app/wa/store.py:set_voice_transcript's own COALESCE
    rewrite), never a "[Sprachnachricht, N:NN]" placeholder -- the hand-written fixture's last_message
    preview for this thread never matches what the real pipeline stores (see the report)."""
    from app.wa import store as ST

    phone = PHONE_STUCK
    clock.set("2026-09-25T07:30:00+00:00")
    ST.pin_rail(c, phone, "bridge")

    clock.set("2026-09-25T07:35:00+00:00")
    ST.record_inbound(c, phone, "wamid.stuck-in1", "Hallo, ich suche eine Stelle in Niederbayern.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["region"] = "Niederbayern"
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-25T07:40:00+00:00")
    ST.record_outbound(c, phone, "wamid.stuck-out1", "Schoen! Erzaehlen Sie mir mehr.", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-27T06:00:00+00:00")
    ST.record_inbound_pending(c, phone, "wamid.stuck-audio1", "", kind="audio", meta={})
    doc_id = ST.record_document(c, phone, "wamid.stuck-audio1", "media-stuck-audio1", "audio", "audio/ogg",
                                None, "/documents/synthetic/stuck-voice.ogg", "synthetic-sha-stuck-audio", 51200)
    ST.set_voice_transcript(c, doc_id, "wamid.stuck-audio1",
                           "Ich habe noch eine Frage zu den Unterlagen, bitte rufen Sie mich zurueck.",
                           "whisper-test-model")
    ST.record_pending_attempt(c, "wamid.stuck-audio1", "claude CLI timeout after 552s")
    t = ST.thread(c, phone)
    t["last_inbound_at"] = clock.value
    t["turns"] = 2
    _save(c, t)
    return phone


def _seed_suppressed_thread(c, client, clock):
    """An inbound STOP token: wa_threads.stopped flips, the cross-rail wa_suppressions row is
    written, and the claim on the STOP message itself is finished "skipped_stopped"
    (app/wa/api.py's own handling) -- NOT one of the two states app/wa/luna/reporting.py:ball_for
    treats as settled, so ball is genuinely "us" here, not "them" (see the report)."""
    from app.wa import store as ST
    from app.wa import suppression as SUP

    phone = PHONE_SUPPRESSED
    clock.set("2026-09-10T11:00:00+00:00")
    ST.thread(c, phone)
    ST.record_inbound(c, phone, "wamid.supp-in1", "Hallo, wer sind Sie?", kind="text")
    t = ST.thread(c, phone)
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-10T11:05:00+00:00")
    ST.pin_rail(c, phone, "meta")
    ST.record_outbound(c, phone, "wamid.supp-out1", "Willkommen bei Pflege Job Radar!", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-12T09:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.supp-stop", "STOP", kind="text")
    t = ST.thread(c, phone)
    t["last_inbound_at"] = clock.value
    t["turns"] = 2
    t["stopped"], t["stopped_reason"] = True, ST.STOPPED
    _save(c, t)
    ST.claim_reply_turn(c, phone, "wamid.supp-stop")
    ST.finish_reply_turn_claim(c, phone, "wamid.supp-stop", "skipped_stopped")

    clock.set("2026-09-12T09:00:05+00:00")
    SUP.suppress(c, phone, SUP.REASON_STOP, "meta", trigger_text="STOP", at=clock.value)
    return phone


def _seed_consented_thread(c, client, clock):
    """A fully-qualified, consented lead: both documents in, a real button-tap consent, two matched
    clinics (wa_queue_matches) with handoffs POSTed through the real write route in two different
    statuses -- one of them opens "attention" -- so this thread's detail carries a non-empty
    handoff_matches and its list row carries a non-empty handoff.targets in more than one status."""
    from app.wa import store as ST

    phone = PHONE_CONSENTED
    clock.set("2026-09-01T08:00:00+00:00")
    ST.thread(c, phone)
    ST.record_inbound(c, phone, "wamid.cons-in1",
                      "Hallo, ich bin examinierte Pflegefachkraft und interessiere mich fuer eine "
                      "Stelle in Oberbayern.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["region"] = "Oberbayern"
    t["slots"]["qualification_path"] = "urkunde"
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-01T08:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.cons-out1", "Freut uns! In welcher Stadt und Abteilung moechten "
                       "Sie arbeiten?", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-05T09:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.cons-in2",
                      "Ich arbeite am liebsten in der Inneren Medizin in Muenchen, eine Wohnung "
                      "brauche ich nicht.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["city"] = "München"
    t["slots"]["department_pref"] = "Innere Medizin"
    t["slots"]["housing_needed"] = False
    t["slots"]["match_branch"] = "pool"
    t["last_inbound_at"] = clock.value
    t["turns"] = 2
    _save(c, t)

    clock.set("2026-09-05T09:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.cons-out2", "Super. Bitte schicken Sie uns Ihren Lebenslauf "
                       "und Ihre Urkunde.", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-08T10:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.cons-doc1", "", kind="document", meta={"media_filename": "lebenslauf.pdf"})
    cv_id = ST.record_document(c, phone, "wamid.cons-doc1", "media-cons-cv", "document", "application/pdf",
                               "lebenslauf.pdf", "/documents/synthetic/cons-lebenslauf.pdf",
                               "synthetic-sha-cons-cv", 198000)
    ST.set_document_classification(c, cv_id, "lebenslauf", None, "cv_text")
    t = ST.thread(c, phone)
    t["slots"]["documents"] = [{"id": cv_id, "document_type": "lebenslauf", "certificate_level": None}]
    t["last_inbound_at"] = clock.value
    t["turns"] = 3
    _save(c, t)

    clock.set("2026-09-08T10:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.cons-out3", "Danke fuer den Lebenslauf! Die Urkunde fehlt uns noch.",
                       kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-09T10:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.cons-doc2", "", kind="document", meta={"media_filename": "urkunde.pdf"})
    urkunde_id = ST.record_document(c, phone, "wamid.cons-doc2", "media-cons-urkunde", "document",
                                    "application/pdf", "urkunde.pdf", "/documents/synthetic/cons-urkunde.pdf",
                                    "synthetic-sha-cons-urkunde", 165000)
    ST.set_document_classification(c, urkunde_id, "urkunde", "fachkraft", "urkunde_text")
    t = ST.thread(c, phone)
    t["slots"]["documents"] = [*t["slots"]["documents"],
                              {"id": urkunde_id, "document_type": "urkunde", "certificate_level": "fachkraft"}]
    t["last_inbound_at"] = clock.value
    t["turns"] = 4
    _save(c, t)

    clock.set("2026-09-09T10:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.cons-out4", "Vollstaendig! Duerfen wir Ihr Profil anonymisiert an "
                       "passende Kliniken senden?", kind="buttons",
                       meta={"action": "consent_offer",
                             "buttons": [{"id": "consent:yes", "title": "Ja"},
                                        {"id": "consent:no", "title": "Nein"}]})
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-15T10:30:00+00:00")
    ST.record_inbound(c, phone, "wamid.cons-tap1", "Ja", kind="interactive", meta={"button_id": "consent:yes"})
    t = ST.thread(c, phone)
    t["slots"]["anonymous_send_consent"] = True
    t["slots"]["stage"] = "submitted"
    t["slots"]["stage_at"] = clock.value
    t["last_inbound_at"] = clock.value
    t["turns"] = 5
    _save(c, t)

    # A final thank-you the brain legitimately answers with silence (the funnel is already
    # submitted) -- ball_for reads this as "silent", not "them": no further reply is owed or sent.
    clock.set("2026-09-29T10:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.cons-thanks", "Danke, bis dann!", kind="text")
    t = ST.thread(c, phone)
    t["last_inbound_at"] = clock.value
    t["turns"] = 6
    _save(c, t)
    ST.claim_reply_turn(c, phone, "wamid.cons-thanks")
    ST.finish_reply_turn_claim(c, phone, "wamid.cons-thanks", ST.NO_SEND_STATE)

    consented_at = "2026-09-15T10:30:00+00:00"
    c.execute("insert into wa_queue_candidates (phone, consented_at, profile_json, status) values (?,?,?,?)",
             (phone, consented_at, json.dumps({"region": "Oberbayern"}), "queued"))
    c.execute("insert into wa_queue_matches (phone, clinic_id, posting_id, score, reasons_json) "
             "values (?,?,?,?,?)", (phone, "c1", 1, 85, "[]"))
    c.execute("insert into wa_queue_matches (phone, clinic_id, posting_id, score, reasons_json) "
             "values (?,?,?,?,?)", (phone, "c2", 2, 70, "[]"))
    c.commit()

    with ST.db() as c2:
        tid = ST.thread_id_for_phone(c2, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-20T09:00:00+00:00"})
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c2", "status": "interview_scheduled",
                     "ts": "2026-09-28T13:00:00+00:00"})
    return phone


def _seed_tombstone_thread(c, client, clock):
    """A failed outbound delivery (last_send_error / send_failures_for), then a forgotten inbound
    message -- the deleted-message tombstone shape (kind "deleted", preview null) the hand-written
    fixture already covered, now alongside the failed-delivery scenario the task also asked for."""
    from app.wa import store as ST

    phone = PHONE_TOMBSTONE
    clock.set("2026-09-23T15:00:00+00:00")
    ST.thread(c, phone)
    ST.record_inbound(c, phone, "wamid.tomb-in1", "Hallo, ich suche eine Stelle in Unterfranken.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["region"] = "Unterfranken"
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-23T15:10:00+00:00")
    ST.pin_rail(c, phone, "meta")
    ST.record_outbound(c, phone, "wamid.tomb-out1", "Willkommen! Erzaehlen Sie mir etwas ueber sich.",
                       kind="text")
    ST.record_message_status(c, phone, {"id": "wamid.tomb-out1", "status": "failed",
                                       "timestamp": "1758637800",
                                       "errors": [{"code": 131026, "title": "message undeliverable"}]})
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-24T09:00:00+00:00")
    ST.record_inbound(c, phone, "wamid.tomb-in2", "Hallo? Ist noch jemand da?", kind="text")
    t = ST.thread(c, phone)
    t["last_inbound_at"] = clock.value
    t["turns"] = 2
    _save(c, t)

    clock.set("2026-09-24T09:05:00+00:00")
    ST.forget_message(c, "wamid.tomb-in2", at=clock.value)
    return phone


def _seed_declined_thread(c, client, clock):
    """A plain decline ("already have a job"), answered once more -- outcome "declined"
    (app/wa/luna/reporting.py:stage_for reads card.declined before anything else), stage from the
    gates stays "qualification" (region only), both independently true at once (two different
    functions, see the report)."""
    from app.wa import store as ST

    phone = PHONE_DECLINED
    clock.set("2026-09-05T08:00:00+00:00")
    ST.thread(c, phone)
    ST.record_inbound(c, phone, "wamid.decl-in1",
                      "Hallo, ich interessiere mich fuer eine Stelle in der Oberpfalz.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["region"] = "Oberpfalz"
    t["last_inbound_at"] = clock.value
    t["turns"] = 1
    _save(c, t)

    clock.set("2026-09-05T08:05:00+00:00")
    ST.record_outbound(c, phone, "wamid.decl-out1", "Schoen, dass Sie sich melden! Erzaehlen Sie mir mehr.",
                       kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)

    clock.set("2026-09-06T08:30:00+00:00")
    ST.record_inbound(c, phone, "wamid.decl-in2",
                      "Nein danke, ich habe schon eine Stelle gefunden.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["declined"] = True
    t["last_inbound_at"] = clock.value
    t["turns"] = 2
    _save(c, t)

    clock.set("2026-09-06T08:31:00+00:00")
    ST.record_outbound(c, phone, "wamid.decl-out2", "Alles Gute fuer die neue Stelle!", kind="text")
    t = ST.thread(c, phone)
    t["last_outbound_at"] = clock.value
    _save(c, t)
    return phone


# Called in this order by generate(), each in its own ST.db() connection -- order only matters for
# the sequential thread_id-minting counter (_deterministic_token_hex), which this fixes.
SEEDERS = (_seed_test_thread, _seed_escalated_thread, _seed_stuck_thread, _seed_suppressed_thread,
          _seed_tombstone_thread, _seed_declined_thread, _seed_consented_thread)


def generate(sqlite_path):
    """Seeds ``sqlite_path`` (caller's responsibility: a throwaway file, never data/wa.sqlite) through
    the real store functions and the real ASGI app, then reads back every board-scope Pro API route.
    -> {"threads.json": <ThreadsEnvelope dict>, "thread_detail.json": {thread_id: <ThreadDetailResponse
    dict>}, "messages.json": {thread_id: <MessagesEnvelope dict>}, "health.json": <HealthResponse dict>}.

    Deterministic (see the module docstring): calling this twice on two different empty sqlite paths
    produces byte-identical JSON once ``json.dumps(..., sort_keys=True)`` is applied, which is what
    both ``--write`` and the drift-guard test do.

    Runs with the TestClient open for the whole seed-then-read pass (``with TestClient(app) as
    client``, per the task): the startup hook (app/wa/asgi.py's own ``_create_wa_schema``) creates
    every table -- store.py's, queue.py's and pro_api's own wa_handoffs/wa_handoff_events -- before
    any seeding touches the database, exactly as uvicorn would for a real deployment; seeding itself
    still goes through app.wa.store/queue's own functions (``ST.db()``), the same read-write
    connections app/wa/api.py's real turn-handling code uses, never a bypass of them."""
    from starlette.testclient import TestClient

    with _generator_env(sqlite_path) as clock, _synthetic_board():
        from app.wa import asgi
        from app.wa import store as ST

        with TestClient(asgi.app) as client:
            phones = []
            for seed in SEEDERS:
                with ST.db() as c:
                    phones.append(seed(c, client, clock))

            with ST.db() as c:
                clock.set("2026-09-30T11:55:00+00:00")
                ST.record_rail_sync_ok(c, ST.RAIL_SYNC_SOURCE)

            # include_test=1: the committed fixture shows the hidden test thread too (so pflege-fe
            # has a row to build the "show test threads" toggle against) -- the DEFAULT-filtered
            # behaviour itself (is_test excluded unless asked for) is covered by
            # tests/test_wa_pro_api.py:test_threads_excludes_test_threads_by_default, not by a
            # fixture file.
            threads_envelope = client.get("/api/wa/pro/threads?include_test=1", headers=RH).json()
            assert threads_envelope["total"] == len(phones), \
                f"seeded {len(phones)} threads, envelope reports {threads_envelope['total']}"
            assert threads_envelope["test_threads"] == 1

            with ST.db() as c:
                thread_ids = {phone: ST.thread_id_for_phone(c, phone) for phone in phones}

            thread_detail, messages = {}, {}
            for tid in thread_ids.values():
                detail = client.get(f"/api/wa/pro/threads/{tid}", headers=RH)
                detail.raise_for_status()
                thread_detail[tid] = detail.json()
                msgs = client.get(f"/api/wa/pro/threads/{tid}/messages", headers=RH)
                msgs.raise_for_status()
                messages[tid] = msgs.json()

            health = client.get("/api/wa/pro/health", headers=RH)
            health.raise_for_status()
            health_body = health.json()

    return {"threads.json": threads_envelope, "thread_detail.json": thread_detail,
           "messages.json": messages, "health.json": health_body}


def _dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(output_dir=FIXTURES_DIR, sqlite_dir=None):
    """Runs generate() into a fresh throwaway sqlite file and writes the four fixtures to
    ``output_dir`` (default: the committed tests/fixtures/wa_pro_api/). -> {filename: text}."""
    import tempfile

    with tempfile.TemporaryDirectory(dir=sqlite_dir) as tmp:
        bodies = generate(pathlib.Path(tmp) / "wa.sqlite")
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    texts = {}
    for name, body in bodies.items():
        text = _dumps(body)
        texts[name] = text
        (output_dir / name).write_text(text, encoding="utf-8")
    return texts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="overwrite the committed fixtures")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed fixtures differ from a fresh run")
    args = parser.parse_args(argv)

    if args.check:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bodies = generate(pathlib.Path(tmp) / "wa.sqlite")
        ok = True
        for name, body in bodies.items():
            committed = (FIXTURES_DIR / name)
            fresh = _dumps(body)
            if not committed.exists() or committed.read_text(encoding="utf-8") != fresh:
                print(f"DRIFT: {name} differs from a fresh generator run", file=sys.stderr)
                ok = False
        if ok:
            print("fixtures match a fresh generator run")
        return 0 if ok else 1

    if args.write:
        texts = write()
        for name in FIXTURE_FILES:
            print(f"wrote {FIXTURES_DIR / name} ({len(texts[name])} bytes)")
        return 0

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bodies = generate(pathlib.Path(tmp) / "wa.sqlite")
    for name, body in bodies.items():
        print(f"--- {name} ---")
        print(_dumps(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
