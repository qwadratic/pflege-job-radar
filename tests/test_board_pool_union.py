"""TASK-166: one real board reached through several registry careers_urls.

crawlers.routing groups boards by exact careers_url, so Bezirksklinikum Mainkofen's mein-check-in
tenant (3 URLs: 27105, RH2143, 26205), karriere.passauerwolf.de (3 URLs) and karriere.medicalpark.de
(9 URLs) were each fetched once per URL. Every copy of a posting carried a one-clinic board pool, was
matched on its own, and the pflege-ingest clinic_links `update ... from` applied an arbitrary one of
the conflicting links (run 217, 2026-09-29: Mainkofen's 3 Deggendorf nursing postings sat on RH2143,
its 13-bed early-rehab unit, instead of the 562-bed 27105). Registry rows below are the live ones.
No network: EdgeSink, the posting_id lookup and the manual check are stubbed; the local queue, the
drain, jobposting_to_obs and the Matcher are real."""
import argparse

import pytest

from crawlers import vendor_adapters as VA
from pflege_jobs import cli, inbox_db as IB

REGISTRY = [
    ("27105", "Bezirksklinikum Mainkofen", "Deggendorf", "Bezirk Niederbayern", 562),
    ("RH2143", "Bezirksklinikum Mainkofen neurologische Frührehabilitation", "Deggendorf", "Bezirk Niederbayern", 13),
    ("26205", "Bezirkskrankenhaus Passau - Fachklinik für Erwachsenenpsychiat rie und Psychotherapie", "Passau", "Bezirk Niederbayern", 60),
    ("27307", "Passauer Wolf Bad Gögging - Klinik für Neurologie", "Neustadt an der Donau", "Passauer Wolf Bad Gögging GmbH & Co. KG", 40),
    ("RH2733", "Passauer Wolf Bad Gögging GmbH & Co. KG - Rehabilitation", "Neustadt a. d. Donau", "Passauer Wolf Bad Gögging GmbH & Co. KG", 477),
    ("RH1892", "Reha-Zentrum Ingolstadt GmbH", "Ingolstadt", "Reha-Zentrum Ingolstadt GmbH", 38),
]
NAME = {r[0]: r[1] for r in REGISTRY}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(IB, "PATH", str(tmp_path / "inbox.sqlite"))
    monkeypatch.setattr(cli, "_live_clinics", lambda url, H: [
        {"clinic_id": i, "name": n, "town": t, "operator": o, "beds": b} for i, n, t, o, b in REGISTRY])


class _Sink:
    links = []
    def __init__(self, *a, **kw): pass
    def write(self, obs, **kw): return {"observations": len(obs)}
    def write_clinics(self, rows, log=print): return len(rows)
    def _post(self, body):
        _Sink.links.extend(body.get("clinic_links") or [])
        return {}


def _drain(monkeypatch):
    """Real cmd_inbox over the local queue; returns {source_ref: [clinic_id of every pushed link]}."""
    _Sink.links = []
    refs = {}
    monkeypatch.setattr(cli, "EdgeSink", _Sink)
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)
    monkeypatch.setattr(cli, "lookup_posting_ids", lambda get, url, H, obs: {
        (o["source_id"], o["source_ref"]): refs.setdefault(o["source_ref"], len(refs) + 1) for o in obs})
    monkeypatch.setattr(cli, "manual_posting_ids", lambda get, url, H, ids: set())
    monkeypatch.setattr(cli, "linked_posting_ids", lambda get, url, H, ids: set())   # nothing stored yet: nothing to clear
    cli.cmd_inbox(argparse.Namespace(no_ack=False, max_batches=10, inbox_db=None,
                                     reprocess_run=None, reprocess_all=False))
    by_ref = {pid: ref for ref, pid in refs.items()}
    out = {}
    for l in _Sink.links:
        out.setdefault(by_ref[l["posting_id"]], []).append(l["clinic_id"])
    return out


def _mci_copy(seed, url, city, plz):
    """One copy of a mein-check-in posting as app/crawl.py._vendor_rows queues it for board `seed`:
    no employer on the page (org is the seed clinic's own name, org_source='seed')."""
    return {"kind": "jobposting", "collector": "vendor-mein-check-in-v1", "source_host": "www.mein-check-in.de",
            "source_url": url,
            "payload": {"url": url, "title": "Pflegefachpersonen (m/w/d) für das Neurologische Zentrum",
                        "org": NAME[seed], "org_source": "seed", "loc": [{"city": city, "plz": plz, "region": "bavaria"}],
                        "description": "", "board_clinic_ids": [seed]}}


def test_every_copy_of_a_posting_served_by_several_boards_links_to_the_same_site(monkeypatch):
    deg = "https://www.mein-check-in.de/mainkofen/position-430614"
    pas = "https://www.mein-check-in.de/mainkofen/position-505464"
    # run 217's order: 26205's board first, then 27105's, then RH2143's (the last one used to win)
    IB.enqueue([_mci_copy(s, deg, "Deggendorf", "94469") for s in ("26205", "27105", "RH2143")]
               + [_mci_copy(s, pas, "Passau", "94032") for s in ("26205", "27105", "RH2143")], run_id=1)

    links = _drain(monkeypatch)

    # union pool {26205, 27105, RH2143}: Deggendorf ties 27105/RH2143 (one operator) -> the real
    # 562-bed site, on every copy; one link per posting reaches the push
    assert links == {deg: ["27105"], pas: ["26205"]}


def _sg_copy(seed_kez, board, city, description=""):
    """A seeded softgarden observation as app/crawl.py queues it (_obs_row): the seed's own _kez
    preset, the board pool in _board, the page-stated employer and city."""
    ref = "softgarden:64935176"
    return {"kind": "observation", "collector": "seed-20", "source_host": "karriere.passauerwolf.de",
            "source_url": "https://karriere.passauerwolf.de/jobs/64935176/Pflegefachkraft-m-w-d-im-Dauernachtdienst/",
            "payload": {"source_id": 20, "source_ref": ref, "source_url": "https://karriere.passauerwolf.de/jobs/64935176/x/",
                        "title": "Pflegefachkraft (m/w/d) im Dauernachtdienst", "employer_name": "PASSAUER WOLF Medizin fürs Leben",
                        "employer_class_rule": "x", "role_class": "pflegefachkraft", "in_bavaria": True,
                        "city": city, "description": description, "_kez": seed_kez, "_board": board}}


def test_a_refused_match_is_not_filled_in_with_the_seed_clinic(monkeypatch):
    # Only Reha-Zentrum Ingolstadt's board served it (e.g. a clinic-scoped crawl of RH1892): the
    # posting's own city is Neustadt a.d. Donau, RH1892 is in Ingolstadt -> the Matcher refuses, and
    # the seed's preset _kez must not stand in for it (it did: 3 open postings on RH1892, run 217).
    IB.enqueue([_sg_copy("RH1892", ["RH1892"], "Neustadt an der Donau")], run_id=1)

    assert _drain(monkeypatch) == {}


def test_observation_copies_are_pooled_too(monkeypatch):
    # 27307 (Klinik für Neurologie) and RH2733 (Rehabilitation) are two sites of one operator in one town:
    # the text has to name the one it is (R6/_bestsite no longer take the bigger on bed count).
    text = "Pflegefachkraft in der Rehabilitation"
    IB.enqueue([_sg_copy("27307", ["27307", "RH2733"], "Neustadt an der Donau", text),
                _sg_copy("RH1892", ["RH1892"], "Neustadt an der Donau", text)], run_id=1)

    assert _drain(monkeypatch) == {"softgarden:64935176": ["RH2733"]}


def test_copies_that_still_disagree_push_no_link_and_say_so(monkeypatch, capsys):
    # each copy names a different registry site as its own page-stated employer (org_source unset)
    url = "https://www.mein-check-in.de/mainkofen/position-1"
    a, b = _mci_copy("27105", url, "Deggendorf", "94469"), _mci_copy("RH2143", url, "Deggendorf", "94469")
    for r in (a, b):
        r["payload"].pop("org_source")
    IB.enqueue([a, b], run_id=1)

    assert _drain(monkeypatch) == {}
    assert "CONFLICT: 1 posting(s) matched to different clinics by different copies, not linked: 1->27105/RH2143" in capsys.readouterr().out


def test_medical_park_site_heading_is_the_posting_city():
    # karriere.medicalpark.de/stellenangebot/stationsleitung-m-w-d-in-vollzeit-de-j4983/, fetched
    # 2026-09-29, trimmed: no JSON-LD, the site's town only in the <h4> above the title.
    html = ('<html><head><title>Stationsleitung (m/w/d) in Vollzeit | Karriere bei Medical Park</title></head><body>'
            '<div class="et_pb_text_inner"><div class="db-sh-divider-top"><div></div></div><h4>Stellenangebot in Bad Rodach</h4></div>'
            '<div class="et_pb_text_inner"><h1>Stationsleitung (m/w/d) in Vollzeit</h1></div>'
            '<p>In unserer Fachklinik Medical Park Bad Rodach im Landkreis Coburg ...</p></body></html>')
    j = VA.parse_job_page(html, "https://karriere.medicalpark.de/stellenangebot/stationsleitung-m-w-d-in-vollzeit-de-j4983/", "Seed")
    assert j["title"] == "Stationsleitung (m/w/d) in Vollzeit"
    assert j["loc"] == [{"city": "Bad Rodach", "plz": None, "region": None}]
