from pflege_jobs.classify import canonical_job_url, fuzzy_key, employer_norm
from pflege_jobs.mechanics import get


def test_fuzzy_key_ignores_gender_marker_employment_and_legal_form():
    a = fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg gGmbH", "Nürnberg")
    b = fuzzy_key("Pflegefachkraft (w/m/d) Vollzeit", "Klinikum Nürnberg GmbH", "nürnberg")
    assert a == b


def test_fuzzy_key_separates_city_and_title():
    a = fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg", "Nürnberg")
    assert a != fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg", "Fürth")
    assert a != fuzzy_key("Pflegefachkraft Intensiv (m/w/d)", "Klinikum Nürnberg", "Nürnberg")


def test_employer_norm_is_conservative():
    assert employer_norm("Klinikum Nürnberg") != employer_norm("Klinikum Nürnberg Personalabteilung")


def test_canonical_ref_folds_url_variants():
    from pflege_jobs.cli import canonical_ref
    assert canonical_ref("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579-needle-nurse-m-w-d-in-teilzeit") == canonical_ref("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579")
    assert canonical_ref("https://x.de/job/1/?utm=a#top") == "https://x.de/job/1"


# TASK-83: canonical_job_url folds the vendor's own job id out of a URL, real pairs pulled live
# 2026-09-22 from production (posting_observations, clinics 46101 Bamberg/56301 Fuerth/37301
# Neumarkt for dvinci, 67206 Bezirk Unterfranken for helix, 56103 Diakoneo for b-ite). softgarden's
# vanity-vs-*.softgarden.io pairing is constructed (Bayreuth's current rows are all single-host,
# confirmed 39/39 -- the id itself, 43046213, is real, from clinic 46201).
def test_canonical_job_url_folds_dvinci_id_vs_id_slug():
    a = canonical_job_url("https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs/52664")
    b = canonical_job_url("https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs/52664/pflegefachkraft-mwd-dialyse")
    assert a == b
    # a different tenant's own id must never fold into this one
    assert a != canonical_job_url("https://jobs.klinikum-fuerth.de/de/jobs/52664")


def test_canonical_job_url_folds_dvinci_across_fuerth_and_neumarkt_real_pairs():
    assert canonical_job_url("https://jobs.klinikum-fuerth.de/de/jobs/11074") == \
        canonical_job_url("https://jobs.klinikum-fuerth.de/de/jobs/11074/pflegefachkraft-fur-die-neurologie-mwd")
    assert canonical_job_url("https://klinikum-neumarkt.dvinci-hr.com/de/jobs/90791") == \
        canonical_job_url("https://klinikum-neumarkt.dvinci-hr.com/de/jobs/90791/stv-stationsleitung-wmd-fur-eine-station-mit-den-fachgebieten-kardiologie-und-gastroenterologie-kommissarisch")


def test_canonical_job_url_folds_helix_prj_across_unit_paths():
    # bezirk-unterfranken.helixjobs.com posts the SAME prj under both /tzbu/ and /bkhwerneck/
    a = canonical_job_url("https://bezirk-unterfranken.helixjobs.com/tzbu/jobad?prj=2618P680")
    b = canonical_job_url("https://bezirk-unterfranken.helixjobs.com/bkhwerneck/jobad?prj=2618P680")
    assert a == b
    # a different prj on the same host must not fold
    assert a != canonical_job_url("https://bezirk-unterfranken.helixjobs.com/bkhwerneck/jobad?prj=2618P660")


def test_canonical_job_url_folds_bite_jobposting_hex_id_with_or_without_de_prefix():
    a = canonical_job_url("https://jobs.diakoneo.de/de/jobposting/e997ff05dd97bbd04a2baa8c907f1654035f5ef80/apply")
    b = canonical_job_url("https://jobs.diakoneo.de/jobposting/e997ff05dd97bbd04a2baa8c907f1654035f5ef80/apply")
    assert a == b


def test_canonical_job_url_folds_umantis_vacancy_id_on_the_same_host():
    # id scoped to its own host on purpose: two different umantis tenants reuse the same small
    # integer for unrelated jobs (recruitingapp-5511 vs recruitingapp-5610 both have a vacancy "1")
    a = canonical_job_url("https://recruitingapp-5511.de.umantis.com/Vacancies/573/Description/1?lang=ger")
    b = canonical_job_url("https://recruitingapp-5511.de.umantis.com/Vacancies/573/Description/2?lang=eng")
    assert a == b
    assert a != canonical_job_url("https://recruitingapp-5610.de.umantis.com/Vacancies/573/Description/1?lang=ger")


def test_canonical_job_url_folds_softgarden_id_across_vanity_and_io_host():
    # id itself real (Klinikum Bayreuth, clinic 46201); the vanity-vs-.io host pairing is
    # constructed per the vendor's own documented shape (pflege_jobs/sources/softgarden.py) since
    # Bayreuth's current rows are all on one host already (39/39, no live second host to sample).
    a = canonical_job_url("https://karriere.klinikum-bayreuth.de/jobs/43046213/Pflegefachkraefte-m-w-d/")
    b = canonical_job_url("https://klinikum-bayreuth.softgarden.io/job/43046213/Pflegefachkraefte-m-w-d")
    assert a == b


def test_canonical_job_url_passes_through_an_unrecognised_shape_unchanged():
    u = "https://example.de/karriere/stellenangebote/"
    assert canonical_job_url(u) == u
    assert canonical_job_url("") == ""
    assert canonical_job_url(None) is None


def test_mechanic_try_exposes_parts():
    r = get("dedupe_key").run({"title": "Pflegefachkraft (m/w/d)", "employer": "Klinikum Nürnberg gGmbH", "city": "Nürnberg"})
    assert r["result"]["employer_norm"] == "klinikum nürnberg" and len(r["result"]["fuzzy_key"]) == 40
