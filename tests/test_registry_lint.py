"""Registry lint: careers_url must not be a single job-detail page (TASK-86)."""
from pflege_jobs.registry_lint import check_careers_url, lint_csv, lint_rows


# --- the two real junk rows the lint exists to have caught --------------------------------------
def test_flags_job_uuid_shape_that_produced_47601():
    url = "https://www.helios-gesundheit.de/karriere/job/3bc89d91-7c4e-485d-ba7f-260fc7a5a378/"
    assert check_careers_url(url) == "job-uuid"


def test_flags_stellenangebote_slug_slug_shape_that_produced_77901():
    url = ("https://dongku.de/stellenangebote/gku-donau-ries-kliniken-und-seniorenheime/"
           "gesundheits-und-krankenpfleger-kinderkrankenpfleger-altenpfleger-m-w-d/")
    assert check_careers_url(url) == "stellenangebote-slug-slug"


# --- the corrected replacement for 77901 must NOT re-trip the same lint -----------------------
def test_corrected_77901_board_url_is_clean():
    assert check_careers_url("https://dongku.de/stellenangebote/") is None


# --- real board URLs already live in the registry must not false-positive ----------------------
def test_real_board_urls_are_not_flagged():
    boards = [
        "https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote",   # one slug only
        "https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote?selection3=3",
        "https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15",
        "https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobSite%3Afoo",
        "https://recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=22&Reset=G",
        "https://jobs.klinikum-ab-alz.de/Jobs",
    ]
    for url in boards:
        assert check_careers_url(url) is None, url


def test_empty_or_missing_careers_url_is_not_flagged():
    assert check_careers_url("") is None
    assert check_careers_url(None) is None


# --- row-level API -------------------------------------------------------------------------------
def test_lint_rows_reports_clinic_id_and_shape():
    rows = [
        {"clinic_id": "16201", "careers_url": "https://www.muenchen-klinik.de/stellenmarkt/"},
        {"clinic_id": "47601", "careers_url": "https://www.helios-gesundheit.de/karriere/job/"
                                               "3bc89d91-7c4e-485d-ba7f-260fc7a5a378/"},
    ]
    findings = lint_rows(rows)
    assert [f.clinic_id for f in findings] == ["47601"]
    assert findings[0].shape == "job-uuid"


def test_lint_csv_catches_the_still_uncorrected_47601_row():
    # This is a DRY-RUN-only correction (TASK-86 AC#1); the live CSV still carries the
    # bad value at the time this test is written, so the lint must still catch it here.
    findings = lint_csv("data/registry/clinics.csv")
    ids = {f.clinic_id for f in findings}
    assert "47601" in ids
