from pflege_jobs.verify import _title_tokens, GONE_MARKERS, decide
from pflege_jobs.mechanics import get


def test_title_tokens_skip_generic():
    assert _title_tokens("Pflegefachkraft (m/w/d) Intensivstation Nürnberg") == ["intensivstation", "nürnberg"]


def test_gone_marker():
    assert GONE_MARKERS.search("Diese Stelle ist leider nicht mehr verfügbar.")


def test_status_codes():
    t = "Pflegefachkraft Intensivstation"
    assert decide(404, "", t)[0] == "gone" and decide(410, "", t)[0] == "gone"
    assert decide(403, "", t)[0] == "blocked" and decide(429, "", t)[0] == "blocked"
    assert decide(503, "", t)[0] == "error" and decide(302, "", t)[0] == "error"
    assert decide(None, None, t, exc_name="ConnectTimeout") == ("error", None, "ConnectTimeout")


def test_200_needs_title_in_body():
    t = "Pflegefachkraft (m/w/d) Intensivstation Nürnberg"
    assert decide(200, "Willkommen auf der Intensivstation", t)[0] == "live"
    assert decide(200, "Stellenangebote — Übersicht", t) == ("error", 200, "200 but title not found (JS-rendered or list page)")
    assert decide(200, "Diese Stelle ist nicht mehr verfügbar", t)[0] == "gone"


def test_mechanic_try():
    r = get("verify_title").run({"title": "Pflegefachkraft (m/w/d) OP Regensburg", "status_code": "200", "body": "OP-Team Regensburg sucht"})
    assert r["result"]["verify_status"] == "live" and r["result"]["title_tokens"] == ["regensburg"]
