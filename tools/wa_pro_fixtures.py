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

TASK-283.7 (frontend team already caught the hand-written activity.json/ops.json drifting from the
real routes once -- see the report this extension shipped with): activity.json, activity_tunnel_down
.json, ops.json and ops_page2.json are generated the same way, seeded through the real engine-side
writers (ST.job_run, ST.record_luna_call/record_send_failure, ST.upsert_mirrored_op,
ST.write_rail_snapshot, ST.record_rail_sync_ok/record_rail_sync_error -- never a raw INSERT) with
fake bridge /v1/ops and /v1/health payloads shaped like bridge/ledger.py::list_ops and
bridge/executor.py::health actually produce. A FOURTH determinism seam is needed for these two
routes specifically -- see _FrozenNow's own docstring below: JobRow.overdue and the ok_24h/
failed_24h windows read ``datetime.now(timezone.utc)`` directly, never through ``store.now_iso()``,
which the three patches in _generator_env never touch.
"""
import argparse
import contextlib
import datetime
import itertools
import json
import pathlib
import sys
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "wa_pro_api"
FIXTURE_FILES = ("threads.json", "thread_detail.json", "messages.json", "health.json",
                 "activity.json", "activity_tunnel_down.json", "ops.json", "ops_page2.json")

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

# TASK-283.7: a fresh block of fake phones for the ops-mirror/activity fixtures, same
# "+4917000000X"-style convention as PHONE_* above -- never one of the 7 thread phones, so this
# seeding (which runs strictly after threads.json/thread_detail.json/messages.json/health.json are
# already captured, see generate() below) can never change a byte of those four files.
OPS_PHONES = [f"+4917000080{n:02d}" for n in range(1, 13)]
PHONE_LUNA_REPLY = "+491700008999"

# TASK-283.7: the two moments activity.json / activity_tunnel_down.json are captured at -- both fed
# to _frozen_wall_clock (see that seam's own docstring) so ST.now_iso() (generated_at) and the
# datetime.now(timezone.utc) reads JobRow.overdue/ok_24h/failed_24h use agree on "now". 2 minutes
# apart: long enough for the bridge to have gone unreachable in between (_seed_rail_snapshot_down,
# called between the two captures), short enough that nothing seeded as "not overdue" at _NOW1
# flips to overdue by _NOW2 purely from the gap itself.
_NOW1 = "2026-09-30T12:00:00+00:00"
_NOW2 = "2026-09-30T12:02:00+00:00"


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


class _FrozenNow:
    """Stand-in for the bare ``datetime`` class inside app.wa.pro_api's and app.wa.store's own
    module namespace (where ``from datetime import datetime`` bound the name) -- installed ONLY
    around the two GET /api/wa/pro/activity calls in generate() below (_frozen_wall_clock), never
    for the whole generator run the way _generator_env's three patches are.

    WHY A FOURTH SEAM. JobRow.overdue (app/wa/pro_api.py:_job_row) and the ok_24h/failed_24h
    windows (app/wa/store.py:job_run_summary, luna_reply_job_summary) all read
    ``datetime.now(timezone.utc)`` directly, never through ``store.now_iso()`` -- the one real
    wall-clock read _generator_env's existing three patches miss. Left unpatched, these three
    fields would be a function of WHEN the generator happens to run (today's real date) rather
    than of the fixed clock script below, and -- unlike stuck_reply (see this module's own
    DETERMINISM section) -- there is no "already years in the past, stays true forever" escape:
    a job deliberately NOT overdue needs its last_run_at within a couple of cadences of "now", which
    drifts stale (flips to overdue) within minutes of the generator ever running again. ``.now(tz)``
    returns the SAME moment ``clock.value`` holds; every other attribute (``fromisoformat``,
    ``min``, ...) delegates to the real ``datetime.datetime`` class unchanged.

    WHY NOT WIDER. app/wa/api.py's ``_is_stuck`` (a different module, never patched here) and
    several OTHER app/wa/store.py functions (reply-turn claim staleness, pending-inbound age,
    STALE_CLAIM_SECONDS) also read ``datetime.now()`` directly and must keep reading the REAL wall
    clock during thread seeding -- freezing ST.datetime for the whole run would silently change
    stuck_reply and friends, which this module's own docstring already settled. Scoping the patch
    to just the activity calls (never active during thread seeding, ops-mirror seeding, or the
    /api/wa/pro/ops calls, none of which read datetime.now() at all) keeps that settled behaviour
    untouched."""

    def __init__(self, clock):
        self._clock = clock

    def now(self, tz=None):
        value = datetime.datetime.fromisoformat(self._clock.value)
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    def __getattr__(self, name):
        return getattr(datetime.datetime, name)


@contextlib.contextmanager
def _frozen_wall_clock(clock):
    """Patches app.wa.pro_api.datetime and app.wa.store.datetime to _FrozenNow(clock) -- see that
    class's own docstring for why this exists and why it is scoped this narrowly."""
    from app.wa import pro_api as PA
    from app.wa import store as ST

    frozen = _FrozenNow(clock)
    with mock.patch.object(PA, "datetime", frozen), mock.patch.object(ST, "datetime", frozen):
        yield


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
    combination the real funnel_stage() cannot produce (see the report). Also the one card with
    housing_flexible true (docs/wa-dashboard.md's board-scope contract, 2026-10-01). Ends on an
    explicit human request, escalated exactly once."""
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
                      "fuer eine Person, aber eine Klinik ohne Wohnung waere auch in Ordnung.", kind="text")
    t = ST.thread(c, phone)
    t["slots"]["city"] = "Augsburg"
    t["slots"]["housing_needed"] = True
    t["slots"]["people_count"] = 1
    # docs/wa-dashboard.md's board-scope card contract (Ivan, 2026-10-01): housing_flexible is "wanted
    # a flat, a clinic without one is also an option" (luna_brain.housing_flexible's own docstring) --
    # it never unsets housing_needed (TASK-211), both are true on this card at once.
    t["slots"]["housing_flexible"] = True
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
    handoff_matches and its list row carries a non-empty handoff.targets in more than one status.
    Also the one card with anonymous_send_offered true (docs/wa-dashboard.md's board-scope contract,
    2026-10-01)."""
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
    # The model's own card_patch flips this the turn it actually asks (app/wa/luna_brain.py line
    # ~1811: only a real button tap may then set anonymous_send_consent -- that is still a separate,
    # code-owned field, set below on the tap). docs/wa-dashboard.md's board-scope contract (2026-10-01).
    t["slots"]["anonymous_send_offered"] = True
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


# --- TASK-283.7: wa_job_runs (the 5 heartbeat jobs, store.HEARTBEAT_JOBS) --------------------------

def _seed_heartbeat_jobs(c, clock):
    """Seeds all 5 of ST.HEARTBEAT_JOBS through real writers: a couple of earlier successful runs
    per job via ST.record_job_run (itself a named writer, never a raw INSERT -- just not wrapped in
    the job_run() context manager, which only exists to time a live call this generator never
    makes) plus one current run each. catchup/purge_test/agent_notes are all comfortably inside
    their own cadence (app/wa/pro_api.py:JOB_CADENCE_SEC) and so read as NOT overdue; followups'
    last real run is hours stale and reads as overdue -- the honest state of a 15-minute job nobody
    has driven since 09:30, not a second manufactured incident.

    tunnel_watch is the one job seeded through ST.job_run() itself rather than by replaying
    app/wa/tunnel_watch.py::main(): review finding 1 (BLOCKER) now makes job_run() itself raise if
    a caller leaves ``rec.ok`` False with no ``rec.error_code`` set, and main() was fixed to set
    ``jr.error_code = "tunnel_down"`` on a plain ``check_once()`` False -- so that specific gap
    (an ErrorInfo with code=None, which app/wa/pro_models.py's required, non-Optional
    ``ErrorInfo.code: str`` rejects -- a real 500 on GET /api/wa/pro/activity, see the report this
    extension originally shipped with) can no longer happen either way. This fixture keeps its own
    seeding call instead of replaying main() purely so the committed fixture shows a SECOND, equally
    real tunnel_watch failure mode (a genuinely raised and, immediately outside job_run's own
    re-raise, suppressed PermissionError from the job's state-file write, ``_save_state``, at its
    real default path) rather than always "tunnel_down" -- not an invented exception on an invented
    path, just a different one of tunnel_watch's own two ways to fail. last_run_at lands 120s before
    this seeding's own "now" (JOB_CADENCE_SEC[tunnel_watch]=30s, so 2*cadence=60s) -- overdue AND
    errored at once, in both activity.json and activity_tunnel_down.json alike (the down variant is
    2 minutes later still, see _NOW2)."""
    from app.wa import store as ST
    from app.wa import tunnel_watch as TW

    ST.record_job_run(c, ST.JOB_CATCHUP, "2026-09-30T11:51:00+00:00", "2026-09-30T11:51:02+00:00", True)
    ST.record_job_run(c, ST.JOB_CATCHUP, "2026-09-30T11:54:00+00:00", "2026-09-30T11:54:02+00:00", True)
    ST.record_job_run(c, ST.JOB_CATCHUP, "2026-09-30T11:57:00+00:00", "2026-09-30T11:57:02+00:00", True)

    ST.record_job_run(c, ST.JOB_FOLLOWUPS, "2026-09-30T09:00:00+00:00", "2026-09-30T09:00:03+00:00", True)
    ST.record_job_run(c, ST.JOB_FOLLOWUPS, "2026-09-30T09:30:00+00:00", "2026-09-30T09:30:03+00:00", True)

    ST.record_job_run(c, ST.JOB_PURGE_TEST, "2026-09-30T03:00:04+00:00", "2026-09-30T03:00:09+00:00", True)

    ST.record_job_run(c, ST.JOB_AGENT_NOTES, "2026-09-30T11:45:00+00:00", "2026-09-30T11:45:01+00:00", True)
    ST.record_job_run(c, ST.JOB_AGENT_NOTES, "2026-09-30T11:50:00+00:00", "2026-09-30T11:50:01+00:00", True)
    ST.record_job_run(c, ST.JOB_AGENT_NOTES, "2026-09-30T11:55:00+00:00", "2026-09-30T11:55:01+00:00", True)

    ST.record_job_run(c, ST.JOB_TUNNEL_WATCH, "2026-09-30T11:50:00+00:00", "2026-09-30T11:50:00+00:00", True)
    ST.record_job_run(c, ST.JOB_TUNNEL_WATCH, "2026-09-30T11:55:00+00:00", "2026-09-30T11:55:00+00:00", True)
    clock.set("2026-09-30T11:58:00+00:00")
    with contextlib.suppress(PermissionError):
        with ST.job_run(ST.JOB_TUNNEL_WATCH):
            raise PermissionError(13, "Permission denied", str(TW.STATE_PATH))


# --- TASK-283.7: the derived "luna_reply" job (wa_luna_calls / wa_send_failures) -------------------

def _seed_luna_reply_signal(c, clock, phone=PHONE_LUNA_REPLY):
    """Seeds store.luna_reply_job_summary's own two tables through the real writers: one logged
    reply-turn attempt (ST.record_luna_call) that then failed to send (ST.record_send_failure).
    The failure text is copied VERBATIM from app/wa/api.py::_send_reopen_template's own RuntimeError
    -- the real free-form-window-closed message a genuine send_and_record failure records under
    this phone, using the real C.FREEFORM_WINDOW_HOURS value -- never invented wording like the
    hand-written fixture's old "send_and_record: 24h window closed, no reopen template matched",
    which this harness's actual code has never produced (see the report)."""
    from app.wa import config as C
    from app.wa import store as ST

    clock.set("2026-09-30T11:54:02+00:00")
    ST.record_luna_call(c, phone)
    error = (f"the WhatsApp free-form window closed for {phone} (last inbound message is over "
            f"{C.FREEFORM_WINDOW_HOURS}h old) and no reopen template is configured -- register a "
            f"template with Meta and set WA_REOPEN_TEMPLATE_NAME before this thread can be reached again")
    clock.set("2026-09-30T11:54:03+00:00")
    ST.record_send_failure(c, phone, error)


# --- TASK-283.7: wa_ops_mirror (GET /v1/ops, bridge/ledger.py's own row shape) ----------------------
# 14 rows: positions 3..14 (the newest 12 -- ops.json's own page) cover every status in
# bridge/ledger.py's OP_QUEUED/OP_RUNNING/OP_DONE/OP_FAILED. They do NOT cover all 12
# store.ORIGIN_VALUES once each any more (review finding 6's own repro: "ops.json:39 and :58 show
# broadcast and nudges, which no code path sends") -- bridge/ledger.py's own origin comment says
# why: ``followups`` already covers the tiered nudge sweep ("there is no separate 'nudges' code
# path to split out of it"), and a broadcast run never touches phone_ops/enqueue_op at all -- it is
# queued through the wholly separate ``/v1/broadcasts`` table (``bridge/broadcast.py``,
# ``app/wa/bridge.py::Client.broadcast``), so no phone_ops row can ever carry origin=broadcast
# either. Positions 6 and 9 (ex-"nudges"/"broadcast") are ``followups``/``campaign`` instead -- the
# real origins those two code paths actually stamp -- so the 12 slots now cover 10 distinct real
# origins (followups and campaign each twice, pro_human still once: review flagged only the two
# fixture origins "no code path sends", not pro_human, which TASK-283.3's own write half will mint
# once it ships -- not a regression this fix pass owns). Positions 1-2 (the 2 oldest) exist only so
# ops_page2.json has a real ``before_id`` page to show. The three failed rows' error code/text are
# copied VERBATIM from bridge/ledger.py's own three real phone_ops failure sites (_recover_stuck_ops'
# "restarted_while_running", _expire_op's "op_expired", cancel_op's "op_cancelled") -- never
# invented wording, unlike the hand-written fixture's old "executor restarted while this op was
# running" (the real message says "in flight", see the report). Position 3's phone is
# PHONE_ESCALATED, not an OPS_PHONES entry (NIT-6, 10-05 review) -- every OPS_PHONES value is a
# synthetic number no wa_threads row is ever seeded for, so every op row used to show
# thread_id=null; using an already-seeded candidate here gives ops.json one real linked example,
# through the same real lookup (ST.upsert_mirrored_op -> thread_id_for_phone_if_known), not a
# hand-set field.
_OPS_SPEC = (
    {"position": 1, "op_id": "op_fixt0001", "kind": "send", "origin": "campaign", "state": "done",
     "phone": OPS_PHONES[11], "created_at": "2026-09-30T11:35:00+00:00",
     "started_at": "2026-09-30T11:35:01+00:00", "finished_at": "2026-09-30T11:35:05+00:00"},
    {"position": 2, "op_id": "op_fixt0002", "kind": "send", "origin": "followups", "state": "failed",
     "phone": OPS_PHONES[9], "created_at": "2026-09-30T11:36:00+00:00",
     "finished_at": "2026-09-30T11:36:30+00:00", "error_code": "op_cancelled",
     "error_text": "the caller gave up waiting for this op and cancelled it before it was claimed"},
    {"position": 3, "op_id": "op_fixt0003", "kind": "send", "origin": "luna", "state": "done",
     # NIT-6, 10-05 review: PHONE_ESCALATED, not a fresh OPS_PHONES entry -- it is already a
     # seeded candidate by the time this runs (SEEDERS, strictly before this function), so
     # ST.upsert_mirrored_op's own real thread_id_for_phone_if_known lookup finds its real,
     # already-minted thread_id. ops.json used to show thread_id=null on every single row -- no
     # OPS_PHONES entry is ever a real candidate -- leaving pflege-fe with no linked-row example;
     # generated, never hand-written, same as every other value here.
     "phone": PHONE_ESCALATED, "created_at": "2026-09-30T11:39:00+00:00",
     "started_at": "2026-09-30T11:39:01+00:00", "finished_at": "2026-09-30T11:39:03+00:00"},
    {"position": 4, "op_id": "op_fixt0004", "kind": "send_document", "origin": "luna_tool", "state": "done",
     "phone": OPS_PHONES[1], "created_at": "2026-09-30T11:40:00+00:00",
     "started_at": "2026-09-30T11:40:01+00:00", "finished_at": "2026-09-30T11:40:05+00:00"},
    {"position": 5, "op_id": "op_fixt0005", "kind": "send", "origin": "followups", "state": "queued",
     "phone": OPS_PHONES[2], "created_at": "2026-09-30T11:48:00+00:00"},
    {"position": 6, "op_id": "op_fixt0006", "kind": "send", "origin": "followups", "state": "queued",
     "phone": OPS_PHONES[3], "created_at": "2026-09-30T11:49:00+00:00"},
    {"position": 7, "op_id": "op_fixt0007", "kind": "send", "origin": "catchup", "state": "failed",
     "phone": OPS_PHONES[4], "created_at": "2026-09-30T11:50:00+00:00",
     "finished_at": "2026-09-30T11:52:05+00:00", "budget_sec": 120, "error_code": "op_expired",
     "error_text": "queued 125s, past the 120s budget the caller queued it under -- nobody is "
                  "still waiting on it"},
    {"position": 8, "op_id": "op_fixt0008", "kind": "send", "origin": "campaign", "state": "running",
     "phone": OPS_PHONES[5], "created_at": "2026-09-30T11:56:00+00:00",
     "started_at": "2026-09-30T11:59:50+00:00"},
    {"position": 9, "op_id": "op_fixt0009", "kind": "send", "origin": "campaign", "state": "running",
     "phone": OPS_PHONES[6], "created_at": "2026-09-30T11:56:30+00:00",
     "started_at": "2026-09-30T11:59:55+00:00"},
    {"position": 10, "op_id": "op_fixt0010", "kind": "read_thread", "origin": "operator", "state": "done",
     "phone": OPS_PHONES[7], "created_at": "2026-09-30T11:57:00+00:00",
     "started_at": "2026-09-30T11:57:01+00:00", "finished_at": "2026-09-30T11:57:02+00:00"},
    {"position": 11, "op_id": "op_fixt0011", "kind": "send", "origin": "agent_notes", "state": "done",
     "phone": OPS_PHONES[8], "created_at": "2026-09-30T11:57:30+00:00",
     "started_at": "2026-09-30T11:57:31+00:00", "finished_at": "2026-09-30T11:57:33+00:00"},
    {"position": 12, "op_id": "op_fixt0012", "kind": "reconcile", "origin": "bridge", "state": "done",
     "phone": None, "created_at": "2026-09-30T11:58:00+00:00",
     "started_at": "2026-09-30T11:58:01+00:00", "finished_at": "2026-09-30T11:58:03+00:00"},
    {"position": 13, "op_id": "op_fixt0013", "kind": "send", "origin": "pro_human", "state": "queued",
     "phone": OPS_PHONES[10], "created_at": "2026-09-30T11:59:40+00:00"},
    {"position": 14, "op_id": "op_fixt0014", "kind": "list_chats", "origin": "unknown", "state": "failed",
     "phone": None, "created_at": "2026-09-30T11:59:50+00:00",
     "started_at": "2026-09-30T11:59:51+00:00", "finished_at": "2026-09-30T11:59:52+00:00",
     "error_code": "restarted_while_running",
     "error_text": "the executor restarted while this op was in flight"},
)


def _seed_ops_mirror(c, clock):
    """Seeds wa_ops_mirror (_OPS_SPEC above) through the one real writer, ST.upsert_mirrored_op --
    never a raw INSERT -- with dicts shaped exactly like bridge/ledger.py::_ops_list_row's own GET
    /v1/ops row (op_id/position/kind/origin/state/priority/created_at/started_at/finished_at/
    resolved_at/budget_sec/phone/error_code/error_text), the same shape Relay.mirror_ops feeds this
    function in production."""
    from app.wa import store as ST

    clock.set("2026-09-30T11:59:58+00:00")
    for row in _OPS_SPEC:
        op = {"op_id": row["op_id"], "position": row["position"], "kind": row["kind"],
             "origin": row["origin"], "state": row["state"], "priority": 1,
             "created_at": row["created_at"], "started_at": row.get("started_at"),
             "finished_at": row.get("finished_at"), "resolved_at": None,
             "budget_sec": row.get("budget_sec"), "phone": row["phone"],
             "error_code": row.get("error_code"), "error_text": row.get("error_text")}
        ST.upsert_mirrored_op(c, op)
    # Review finding 4: queue.as_of / OpsEnvelope.mirrored_at both read wa_rail_sync's own
    # OPS_MIRROR_SYNC_SOURCE row (the mirror's own heartbeat, separate from the tunnel/bridge
    # health snapshot below) -- write it through the real writer so those fields are not left
    # null in every fixture, the one state this generator would otherwise never exercise.
    ST.record_rail_sync_ok(c, ST.OPS_MIRROR_SYNC_SOURCE)


# --- TASK-283.7: wa_rail_snapshot (GET /v1/health, bridge/relay_pull.py's own transform) -----------

def _fake_bridge_health_ok(snapshot_time):
    """A fake bridge GET /v1/health body, shaped like bridge/executor.py::health()'s real return --
    only the keys bridge/relay_pull.py::_trim_health actually keeps (_HEALTH_PASSTHROUGH_KEYS) plus
    rail.driver (which _trim_health narrows to {connected, kind} itself), so this can be fed
    straight through the real _trim_health/_derive_phone_state pair exactly as Relay.health() would.
    driver.connected=True with one recent journal_recent.dirty_state_recovered lands
    _derive_phone_state's own priority order (disconnected > blocked > recovering > ready) on
    "recovering" -- the one non-"ready" phone.state the task asks every fixture to cover, without a
    second scenario: write_rail_snapshot(ok=False) never touches phone_state, so
    activity_tunnel_down.json reports this SAME "recovering" rather than inventing another one."""
    return {
        "rail": {"driver": {"connected": True, "kind": "adb"}},
        "watcher": {"interval_sec": 5.0, "started_at": "2026-09-01T00:00:00+00:00", "cycles": 50000,
                   "errors": 0, "last_ok_at": snapshot_time, "last_error": None, "last_error_at": None,
                   "idle_dirty_recovered": 3, "idle_dirty_held": 0, "alive": True},
        "media_watcher": None, "identity_watcher": None, "reconcile_watcher": None,
        "unresolved_send_watcher": None, "ops_dispatcher": None,
        "doctor": {"blocked_by_stuck_op": False},
        "phone_ops": {"queued": 3, "running": 2, "done": 6, "failed": 3},
        "quota": {},
        "inbound": {"seen": 0, "unresolved": 0, "last_poll_at": snapshot_time, "dirty_recovered": 1},
        "oldest_unresolved_sec": None,
        "reconcile": {"escalated": 0},
        "broadcast": {"runs_open": 0, "runner": {
            "interval_sec": 30.0, "started_at": "2026-09-01T00:00:00+00:00", "cycles": 10000,
            "attempted": 0, "errors": 0, "last_item_at": None, "last_error": None,
            "last_error_at": None, "alive": True}},
        "retention": {"last_ok_at": None, "errors": 0, "last_error": None, "last_error_at": None,
                     "result": None},
        "journal_recent": {"window_sec": 3600, "idle_dirty_recovered": 0,
                           "dirty_state_recovered": 1, "watcher_error": 0},
    }


def _seed_rail_snapshot_good(c, clock):
    """The "normal state" wa_rail_snapshot row activity.json captures: two sequential fake bridge
    health bodies fed through the REAL bridge/relay_pull.py::_trim_health/_derive_phone_state pair
    (never re-derived by hand here) into ST.write_rail_snapshot -- the first establishes
    tunnel_since/phone_state_since (write_rail_snapshot's own "only moves when the value itself
    changes" rule), the second refreshes snapshot_at ~19 minutes later with the SAME tunnel_up/
    phone_state, exactly the shape a healthy, repeatedly-polling relay produces."""
    from bridge import relay_pull as RP
    from app.wa import store as ST

    clock.set("2026-09-30T11:40:00+00:00")
    trimmed = RP._trim_health(_fake_bridge_health_ok(clock.value))
    ST.write_rail_snapshot(c, ok=True, health=trimmed, tunnel_up=True,
                           phone_state=RP._derive_phone_state(trimmed))

    clock.set("2026-09-30T11:58:42+00:00")
    trimmed = RP._trim_health(_fake_bridge_health_ok(clock.value))
    ST.write_rail_snapshot(c, ok=True, health=trimmed, tunnel_up=True,
                           phone_state=RP._derive_phone_state(trimmed))


def _seed_rail_snapshot_down(c, clock):
    """The bridge-unreachable variant activity_tunnel_down.json captures: ST.write_rail_snapshot's
    own ok=False branch (that function's own docstring: health_json/phone_state are left exactly as
    they were -- never blanked -- so a dead engine shows as an OLD snapshot_at, never a missing one)
    plus the matching wa_rail_sync failure (ST.record_rail_sync_error) -- a second, later
    relay_pull drain attempt that never reached the bridge at all, on top of the already-good
    snapshot _seed_rail_snapshot_good wrote. Deliberately leaves wa_rail_sync's last_ok_at (and so
    _relay_sync_job_summary's last_run_at = ``synced_at or last_error_at``) exactly as the earlier
    successful sync set it -- see the report for why that `or` means a fresh failure here never
    moves relay_sync's own last_run_at forward, which this fixture surfaces rather than hides."""
    from app.wa import store as ST

    clock.set("2026-09-30T12:01:30+00:00")
    ST.write_rail_snapshot(c, ok=False, tunnel_up=False, error_code="RelayError",
                           error="ssh -L to mini01 did not come up in 20s")
    ST.record_rail_sync_error(c, ST.RAIL_SYNC_SOURCE, "relay: ssh -L to mini01 did not come up in 20s")


def generate(sqlite_path):
    """Seeds ``sqlite_path`` (caller's responsibility: a throwaway file, never data/wa.sqlite) through
    the real store functions and the real ASGI app, then reads back every board-scope Pro API route.
    -> {"threads.json": <ThreadsEnvelope dict>, "thread_detail.json": {thread_id: <ThreadDetailResponse
    dict>}, "messages.json": {thread_id: <MessagesEnvelope dict>}, "health.json": <HealthResponse dict>,
    "activity.json": <ActivityResponse dict>, "activity_tunnel_down.json": <ActivityResponse dict>,
    "ops.json": <OpsEnvelope dict>, "ops_page2.json": <OpsEnvelope dict>}.

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

            # TASK-283.7: activity.json / activity_tunnel_down.json / ops.json / ops_page2.json --
            # seeded through the real engine-side writers (never a raw INSERT, see each seed
            # function's own docstring), strictly AFTER every read above, so none of it can change
            # a byte of threads.json/thread_detail.json/messages.json/health.json.
            with ST.db() as c:
                _seed_heartbeat_jobs(c, clock)
            with ST.db() as c:
                _seed_luna_reply_signal(c, clock)
            with ST.db() as c:
                _seed_ops_mirror(c, clock)
            with ST.db() as c:
                _seed_rail_snapshot_good(c, clock)

            clock.set(_NOW1)
            with _frozen_wall_clock(clock):
                activity = client.get("/api/wa/pro/activity", headers=RH)
                activity.raise_for_status()
                activity_body = activity.json()

            ops = client.get("/api/wa/pro/ops?limit=12", headers=RH)
            ops.raise_for_status()
            ops_body = ops.json()
            next_before_id = ops_body["next_before_id"]
            assert next_before_id is not None, \
                "_OPS_SPEC must have more than 12 rows -- ops_page2.json needs a real before_id page"
            ops_page2 = client.get(f"/api/wa/pro/ops?before_id={next_before_id}", headers=RH)
            ops_page2.raise_for_status()
            ops_page2_body = ops_page2.json()
            assert ops_page2_body["next_before_id"] is None, \
                "ops_page2.json should exhaust _OPS_SPEC -- adjust the 14-row table or this assertion"

            with ST.db() as c:
                _seed_rail_snapshot_down(c, clock)

            clock.set(_NOW2)
            with _frozen_wall_clock(clock):
                activity_down = client.get("/api/wa/pro/activity", headers=RH)
                activity_down.raise_for_status()
                activity_tunnel_down_body = activity_down.json()

    return {"threads.json": threads_envelope, "thread_detail.json": thread_detail,
           "messages.json": messages, "health.json": health_body,
           "activity.json": activity_body, "activity_tunnel_down.json": activity_tunnel_down_body,
           "ops.json": ops_body, "ops_page2.json": ops_page2_body}


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
