import json, tempfile, os
from pflege_jobs.cli import main
from tests.test_sinks import RAW

def test_renormalize_keeps_enrichment():
    d = tempfile.mkdtemp(); p = os.path.join(d, "raw.json")
    from pflege_jobs.sources.arbeitsagentur import to_observation
    non_clinic = to_observation({**RAW, "referenznummer": "10000-2-S", "firma": "AWO Seniorenzentrum"})
    clinic = to_observation(RAW); clinic["description"] = "Wir bieten Personalwohnungen, TVöD-K. Kontakt: pd@klinik.de"; clinic["details_fetched_at"] = "2026-09-05T00:00:00Z"
    json.dump({"slice_counts": {}, "observations": [non_clinic, clinic]}, open(p, "w"))
    main(["renormalize", "--inp", p])
    obs = {o["source_ref"]: o for o in json.load(open(p))["observations"]}
    assert obs["10000-1-S"]["enr_housing"] is True and obs["10000-1-S"]["enr_tariff"] == "TVöD"
    assert obs["10000-1-S"]["enr_contact_emails"] == ["pd@klinik.de"] and obs["10000-1-S"]["details_fetched_at"]
    assert obs["10000-2-S"].get("enr_housing") is None
