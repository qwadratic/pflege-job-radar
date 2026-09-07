"""Routing must never turn one shared board into N fetches, nor crash on unsupported vendors."""
from crawlers.routing import plan, ADAPTERS


def test_shared_board_is_fetched_once():
    # Seven Schön Klinik sites share one rexx board; the naive per-clinic loop produced 2.7x rows.
    clinics = [{"clinic_id": str(i), "name": "Schön Klinik %d" % i, "ats_type": "rexx",
                "careers_url": "https://jobs.schoen-klinik.de/stellenangebote.html"} for i in range(7)]
    boards, unroutable = plan(clinics)
    assert len(boards) == 1
    assert len(next(iter(boards.values()))["clinics"]) == 7
    assert unroutable == []


def test_same_host_different_tenant_stays_separate():
    # Two mein-check-in tenants share a host but list different jobs -- grouping by host would
    # silently discard one of them, so grouping is by exact URL.
    clinics = [
        {"clinic_id": "1", "name": "A", "ats_type": "mein-check-in",
         "careers_url": "https://www.mein-check-in.de/csl-kelheim/overview"},
        {"clinic_id": "2", "name": "B", "ats_type": "mein-check-in",
         "careers_url": "https://www.mein-check-in.de/geomed-klinik/overview"},
    ]
    boards, _ = plan(clinics)
    assert len(boards) == 2


def test_labelled_but_unsupported_vendor_is_reported_not_crashed():
    clinics = [{"clinic_id": "1", "name": "Some Clinic", "ats_type": "no-such-vendor",
                "careers_url": "https://example.invalid/jobs"}]
    boards, unroutable = plan(clinics)
    assert boards == {} and "no-such-vendor" in unroutable[0][1]


def test_missing_entry_point_is_reported():
    boards, unroutable = plan([{"clinic_id": "1", "name": "X", "ats_type": "softgarden", "careers_url": ""}])
    assert boards == {} and "entry point" in unroutable[0][1]


def test_every_advertised_adapter_is_importable():
    """A vendor label with a dead adapter reference is worse than no label: the scheduler would keep
    handing it work that silently returns nothing. This pins every entry to a real callable."""
    import importlib
    for vendor, (kind, ref) in ADAPTERS.items():
        assert kind in ("vendor", "seeded", "external"), vendor
        if kind == "external":
            continue          # module pulls in Playwright at import time; covered by its own runner
        mod, fn = ref.split(":")
        assert hasattr(importlib.import_module(mod), fn), "%s -> %s" % (vendor, ref)


def test_vendor_adapters_map_matches_routing():
    """VENDORS is what the crawler dispatches on; ADAPTERS is what routing advertises. If they drift,
    routing promises coverage the crawler will not deliver."""
    from crawlers.vendor_adapters import VENDORS
    advertised = {v for v, (kind, _) in ADAPTERS.items() if kind == "vendor"}
    assert advertised == set(VENDORS), advertised ^ set(VENDORS)
