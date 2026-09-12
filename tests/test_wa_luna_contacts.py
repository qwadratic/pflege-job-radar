"""Clinic contact discovery (TASK-64): the extraction heuristic against fixture HTML/text (no
network in the default run), the source ordering (enr_contact_emails short-circuits the HTTP
fetch), the obfuscated-description re-scan, and the clinic_contacts storage round trip. One
pytest.mark.network test does a real fetch against a real clinic careers page as a sanity check --
deselected by default, same convention as every other network-marked test in this repo.
"""
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa.luna import contacts as CT
from app.wa.luna import discover_contacts as DC


def _clinic(**over):
    base = {"clinic_id": "90001", "name": "Klinikum Beispielstadt", "website": "", "careers_url": ""}
    base.update(over)
    return base


def _posting(**over):
    # "description" defaults present-but-empty so tests that don't care about it never trip the
    # lazy app.data.job_detail() fetch (a real network call) -- see the dedicated lazy-fetch test
    # below, which builds its own posting dict without this key on purpose.
    base = {"posting_id": 1, "clinic_id": "90001", "enr_contact_emails": None, "description": None}
    base.update(over)
    return base


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Records what it was asked to fetch; never touches the network."""

    def __init__(self, text=None, status_code=200, error=None):
        self.text = text
        self.status_code = status_code
        self.error = error
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if self.error:
            raise self.error
        return FakeResponse(self.text, self.status_code)


class ExplodingSession:
    """A session that fails the test if it is ever called -- proves a source short-circuited."""

    def get(self, *a, **k):
        raise AssertionError("HTTP fetch attempted when it should have been short-circuited")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """POLITE_SLEEP is real (1.0s) in app.crawl; skip the actual wait in every test here."""
    monkeypatch.setattr(CT.time, "sleep", lambda s: None)


# --- source 0: optional external contact CRM (TASK-69) ------------------------------------------

def test_external_crm_wins_and_skips_every_other_source(monkeypatch):
    """A hit from the external CRM must short-circuit enr_contact_emails, the website fetch and
    the description rescan -- ExplodingSession/enr_contact_emails prove none of them ever ran."""
    import app.wa.luna.external_contacts as EC
    monkeypatch.setattr(EC, "contact_for_clinic",
                        lambda name, run=None: {"email": "pd@klinikum-beispielstadt.example",
                                                 "source": "external_crm", "confidence": "high"})
    postings = [_posting(enr_contact_emails=["someone-else@example.de"])]
    clinic = _clinic(careers_url="https://klinikum-x.de/karriere")
    hit = CT.discover_contact(clinic, postings, session=ExplodingSession())
    assert hit == {"email": "pd@klinikum-beispielstadt.example", "source": "external_crm", "confidence": "high"}


def test_external_crm_miss_falls_through_to_enr_contact_emails(monkeypatch):
    import app.wa.luna.external_contacts as EC
    monkeypatch.setattr(EC, "contact_for_clinic", lambda name, run=None: None)
    postings = [_posting(enr_contact_emails=["pflegedirektion@klinikum-x.de"])]
    hit = CT.discover_contact(_clinic(), postings, session=ExplodingSession())
    assert hit["source"] == "enr_contact_emails"


def test_external_crm_error_falls_through_rather_than_propagating(monkeypatch):
    import app.wa.luna.external_contacts as EC

    def boom(name, run=None):
        raise RuntimeError("the external CRM is unreachable in this environment")

    monkeypatch.setattr(EC, "contact_for_clinic", boom)
    postings = [_posting(enr_contact_emails=["pflegedirektion@klinikum-x.de"])]
    hit = CT.discover_contact(_clinic(), postings, session=ExplodingSession())
    assert hit["source"] == "enr_contact_emails"


# --- source 1: enr_contact_emails ---------------------------------------------------------------

def test_enr_contact_emails_wins_and_skips_the_http_fetch():
    postings = [_posting(enr_contact_emails=None), _posting(enr_contact_emails=["  Pflegedirektion@Klinikum-X.de "])]
    clinic = _clinic(careers_url="https://klinikum-x.de/karriere")
    hit = CT.discover_contact(clinic, postings, session=ExplodingSession())
    assert hit == {"email": "pflegedirektion@klinikum-x.de", "source": "enr_contact_emails", "confidence": "high"}


def test_enr_contact_emails_empty_list_falls_through():
    postings = [_posting(enr_contact_emails=[])]
    clinic = _clinic()   # no website/careers_url either -> nothing to fetch
    assert CT.discover_contact(clinic, postings) is None


# --- source 2: one polite fetch of the clinic's own site ----------------------------------------

def test_website_email_near_context_word_is_found():
    html_body = ("<html><body><h2>Personalabteilung</h2><p>Bewerbungen bitte an "
                 '<a href="mailto:hr@klinikum-x.de">hr@klinikum-x.de</a> senden.</p></body></html>')
    clinic = _clinic(careers_url="https://klinikum-x.de/karriere")
    session = FakeSession(text=html_body)
    hit = CT.discover_contact(clinic, [_posting()], session=session)
    assert hit == {"email": "hr@klinikum-x.de", "source": "website", "confidence": "medium"}
    assert session.calls == ["https://klinikum-x.de/karriere"]   # careers_url preferred, one request


def test_website_prefers_careers_url_falls_back_to_website():
    clinic = _clinic(website="https://klinikum-x.de", careers_url="")
    session = FakeSession(text="<p>Personalabteilung: hr@klinikum-x.de</p>")
    hit = CT.discover_contact(clinic, [_posting()], session=session)
    assert hit["email"] == "hr@klinikum-x.de"
    assert session.calls == ["https://klinikum-x.de"]


def test_website_email_far_from_any_context_word_is_ignored():
    filler = "x" * 400
    html_body = f"<html><body><p>Personalabteilung</p><p>{filler}</p><p>info@klinikum-x.de</p></body></html>"
    clinic = _clinic(careers_url="https://klinikum-x.de/karriere")
    session = FakeSession(text=html_body)
    # no enr_contact_emails, no description -> overall result is None (step 3 also finds nothing)
    hit = CT.discover_contact(clinic, [_posting(description="kein Kontakt hier.")], session=session)
    assert hit is None


def test_no_website_configured_skips_the_fetch_entirely():
    clinic = _clinic()   # website/careers_url both ""
    session = ExplodingSession()
    hit = CT.discover_contact(clinic, [_posting(description="kein Kontakt hier.")], session=session)
    assert hit is None


def test_website_fetch_error_falls_through_to_description_rescan():
    clinic = _clinic(careers_url="https://klinikum-x.de/karriere")
    session = FakeSession(text=None, error=ConnectionError("boom"))
    postings = [_posting(description="Bewerbung an bewerbung(at)klinikum-x(punkt)de.")]
    hit = CT.discover_contact(clinic, postings, session=session)
    assert hit["source"] == "description_rescan"


def test_website_non_200_falls_through():
    clinic = _clinic(careers_url="https://klinikum-x.de/karriere")
    session = FakeSession(text="<p>Personalabteilung hr@klinikum-x.de</p>", status_code=404)
    hit = CT.discover_contact(clinic, [_posting()], session=session)
    assert hit is None


# --- source 3: second regex pass over the job ad's own description ------------------------------

@pytest.mark.parametrize("text", [
    "Bitte bewerben Sie sich unter bewerbung(at)klinikum-x(punkt)de.",
    "Kontakt: bewerbung[at]klinikum-x[punkt]de",
    "Schreiben Sie an bewerbung at klinikum-x dot de für Rückfragen.",
])
def test_description_rescan_finds_obfuscated_addresses(text):
    clinic = _clinic()
    hit = CT.discover_contact(clinic, [_posting(description=text)])
    assert hit == {"email": "bewerbung@klinikum-x.de", "source": "description_rescan", "confidence": "low"}


def test_description_rescan_lazily_fetches_full_row_when_not_already_present(monkeypatch):
    """The in-memory snapshot's job rows have no `description` key (app/data.py:JOB_COLS) -- a
    posting missing the key entirely triggers a lookup via app.data.job_detail()."""
    monkeypatch.setattr(D, "job_detail", lambda pid: {"description": "bewerbung(at)klinikum-x(punkt)de"})
    posting = {"posting_id": 7, "clinic_id": "90001", "enr_contact_emails": None}   # no "description" key at all
    assert "description" not in posting
    hit = CT.discover_contact(_clinic(), [posting])
    assert hit == {"email": "bewerbung@klinikum-x.de", "source": "description_rescan", "confidence": "low"}


def test_description_rescan_none_found_returns_none():
    clinic = _clinic()
    hit = CT.discover_contact(clinic, [_posting(description="Wir freuen uns auf Ihre Bewerbung.")])
    assert hit is None


# --- storage --------------------------------------------------------------------------------------

@pytest.fixture()
def sqlite_path(tmp_path, monkeypatch):
    path = tmp_path / "wa.sqlite"
    monkeypatch.setattr(C, "SQLITE_PATH", path)
    return path


def test_save_and_get_contact_roundtrip(sqlite_path):
    c = CT.db()
    try:
        assert CT.get_contact(c, "90001") is None
        CT.save_contact(c, "90001", "hr@klinikum-x.de", "website", "medium")
        got = CT.get_contact(c, "90001")
        assert got["email"] == "hr@klinikum-x.de"
        assert got["source"] == "website"
        assert got["confidence"] == "medium"
        assert got["discovered_at"]
    finally:
        c.close()


def test_save_contact_upserts_one_row_per_clinic(sqlite_path):
    c = CT.db()
    try:
        CT.save_contact(c, "90001", "first@klinikum-x.de", "website", "medium")
        CT.save_contact(c, "90001", "second@klinikum-x.de", "enr_contact_emails", "high")
        n = c.execute("select count(*) as n from clinic_contacts where clinic_id=?", ("90001",)).fetchone()["n"]
        assert n == 1
        assert CT.get_contact(c, "90001")["email"] == "second@klinikum-x.de"
    finally:
        c.close()


def test_clinic_contacts_table_is_separate_from_wa_threads(sqlite_path):
    c = CT.db()
    try:
        tables = {r["name"] for r in c.execute("select name from sqlite_master where type='table'").fetchall()}
        assert {"clinic_contacts", "wa_threads", "wa_messages"} <= tables
    finally:
        c.close()


# --- batch entry point -----------------------------------------------------------------------------

@pytest.fixture()
def board(tmp_path, monkeypatch):
    jobs = [
        {"posting_id": 1, "clinic_id": "90001", "status": "open",
         "enr_contact_emails": ["hr@klinikum-x.de"]},
        {"posting_id": 2, "clinic_id": "90002", "status": "open", "enr_contact_emails": None},
    ]
    clinics = [{"clinic_id": "90001", "name": "Klinikum X", "website": "", "careers_url": ""},
               {"clinic_id": "90002", "name": "Klinikum Y", "website": "", "careers_url": ""}]
    D._snap.update({"at": time.time(), "jobs": jobs, "clinics": clinics,
                    "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    # These job dicts have no "description" key at all (same as the real in-memory snapshot,
    # app/data.py:JOB_COLS) -- clinic 90002 has no enr_contact_emails and no website either, so
    # discover_contact() reaches the lazy job_detail() fetch; stub it instead of hitting Supabase.
    monkeypatch.setattr(D, "job_detail", lambda pid: {})
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    return clinics


def test_batch_all_resolves_what_it_can(board):
    found, total = DC.run(D.clinics())
    assert total == 2
    assert found == 1   # only 90001 has enr_contact_emails; 90002 has no site and no description
    c = CT.db()
    try:
        assert CT.get_contact(c, "90001")["email"] == "hr@klinikum-x.de"
        assert CT.get_contact(c, "90002") is None
    finally:
        c.close()


def test_batch_clinic_id_targets_one_clinic(board, capsys):
    DC.main(["--clinic-id", "90001"])
    c = CT.db()
    try:
        assert CT.get_contact(c, "90001")["email"] == "hr@klinikum-x.de"
        assert CT.get_contact(c, "90002") is None   # untouched -- --clinic-id scoped to one
    finally:
        c.close()


def test_batch_unknown_clinic_id_exits_loudly(board):
    with pytest.raises(SystemExit):
        DC.main(["--clinic-id", "does-not-exist"])


def test_batch_requires_one_of_clinic_id_or_all(board):
    with pytest.raises(SystemExit):
        DC.main([])


# --- real-network sanity check (deselected by default) -------------------------------------------

@pytest.mark.network
def test_real_fetch_against_a_real_clinic_careers_page():
    """No assertion on *finding* a contact (real page content changes) -- just that a real fetch
    through discover_contact() completes without raising, against a real registry clinic."""
    clinic = {"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum Ingolstadt",
              "website": "https://kbo-heckscher-klinikum.de",
              "careers_url": "https://kbo-heckscher-klinikum.de/arbeiten-bei-uns"}
    hit = CT.discover_contact(clinic, [_posting(clinic_id="16104", description=None)])
    assert hit is None or {"email", "source", "confidence"} <= set(hit)
