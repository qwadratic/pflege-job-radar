"""Rail & jobs tab (web/pro.html#/leads?tab=rail, docs/wa-pro-activity.md): the bridge, the phone-op queue, the
automatic jobs and the ops.

The first and the last test run the built page against its own offline mock. The others route /api/* by hand, so
each state (a dead tunnel, a frozen mirror, an unknown value, a missing cursor) is one the test wrote down itself.
"""
import json
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_web_leads import _open, _serve_api, browser_and_base  # noqa: F401  (browser_and_base is a fixture)

T0 = "2026-09-30T12:00:00+00:00"
HEALTH = {"webhook_ready": True, "outbound_ready": True, "reply_scope": "all", "autosend": True}
NO_THREADS = {"total": 0, "next_offset": None, "rows": []}


def _job(job, **kw):
    return {"job": job, "enabled": True, "last_run_at": "2026-09-30T11:59:00+00:00", "last_ok_at": "2026-09-30T11:59:00+00:00",
            "last_error": None, "next_run_at": "2026-09-30T12:02:00+00:00", "ok_24h": 5, "failed_24h": 0, "overdue": False, **kw}


def _activity(**kw):
    counts = {"queued": 0, "running": 0, "done": 4, "failed": 0, "other": {}, "as_of": "2026-09-30T11:59:58+00:00"}
    base = {"generated_at": T0, "snapshot_at": "2026-09-30T11:59:30+00:00", "synced_at": "2026-09-30T11:59:58+00:00",
            "synced_source": "bridge_relay_pull", "source": "harness@test",
            "rail": {"tunnel": {"up": True, "since": "2026-09-30T09:00:00+00:00", "last_error": None},
                     "phone": {"state": "ready", "since": "2026-09-30T09:00:00+00:00"},
                     "watcher": {"alive": True, "heartbeat_at": "2026-09-30T11:59:30+00:00"},
                     "last_sync_at": "2026-09-30T11:59:58+00:00"},
            "queue": dict(counts), "human": {**counts, "done": 0}, "jobs": [_job("catchup")]}
    return {**base, **kw}


def _op(position, status="done", **kw):
    return {"id": f"op{position}", "position": position, "kind": "send", "origin": "luna", "status": status,
            "thread_id": None, "phone_masked": f"+49 ••• ••• {1000 + position}", "created_at": "2026-09-30T11:50:00+00:00",
            "started_at": "2026-09-30T11:50:02+00:00", "finished_at": "2026-09-30T11:50:09+00:00", "attempts": None, "error": None, **kw}


def _ops(rows, next_before_id=None):
    return {"generated_at": T0, "source": "harness@test", "synced_at": T0, "synced_source": "bridge_relay_pull",
            "mirrored_at": T0, "rows": rows, "next_before_id": next_before_id}


def _page(browser_and_base, activity, ops, tab="?tab=rail", wait=".rl-grid"):
    """Open the tab with /api/wa/activity answered by `activity` and /api/wa/ops by `ops(query)`.
    Either may be a (status, body) tuple. Every ops query is recorded in page.ops_queries."""
    browser, base = browser_and_base
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('lang','en')")
    page = ctx.new_page()
    page.errors, page.ops_queries = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)[:300]))

    def wa(route, path):
        if path == "wa/ops":
            q = {k: v[0] for k, v in parse_qs(urlparse(route.request.url).query).items()}
            page.ops_queries.append(q)
            body = ops(q)
        else:
            body = {"wa/threads": NO_THREADS, "wa/health": HEALTH, "wa/activity": activity}[path]
        status, body = body if isinstance(body, tuple) else (200, body)
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
    _serve_api(page, wa)
    page.goto(f"{base}/pro.html#/leads{tab}")
    page.wait_for_selector(wait)
    return page


def _done(page):
    assert not page.errors, page.errors[:2]
    page.context.close()


def test_the_rail_tab_shows_the_bridge_the_queue_the_jobs_and_every_op(browser_and_base):
    browser, base = browser_and_base
    page = _open(browser, f"{base}/pro.html?mock=1#/leads?tab=rail")
    page.wait_for_selector(".rl-grid")
    assert page.inner_text("h1") == "The phone rail is running, with warnings"
    assert [x.text_content() for x in page.locator(".rl-why li").all()] == ["1 job with an error"]
    assert [x.text_content() for x in page.locator(".rl-t .lbl").all()] == ["Tunnel", "Phone", "Watcher", "Last sync"]
    assert [x.text_content() for x in page.locator(".rl-t .v").all()][:3] == ["up", "ready", "alive"]
    jobs = page.locator("tr[data-job]")
    assert [r.get_attribute("data-job") for r in jobs.all()] == ["catchup", "followups", "tunnel_watch", "purge_test", "agent_notes",
                                                                 "relay_sync", "luna_reply", "broadcasts"]
    luna = page.locator('tr[data-job="luna_reply"]').text_content()
    assert "Luna replies" in luna and "send failed" in luna and "send_failed" in luna and "on demand" in luna
    assert "never ran" in page.locator('tr[data-job="broadcasts"]').text_content()
    # the first page is 50 ops; the list goes on to the very first op, with no cap
    assert page.locator("tr[data-op]").count() == 50
    page.click("#rl-older")
    page.wait_for_selector('tr[data-op="op_demo0001"]')
    assert page.locator("tr[data-op]").count() >= 72
    assert page.locator("#rl-older").count() == 0
    assert "This is the oldest operation." in page.inner_text(".rl-more")
    _done(page)


def test_a_dead_bridge_is_the_headline_and_shows_on_the_leads_tab_too(browser_and_base):
    down = _activity(rail={"tunnel": {"up": False, "since": "2026-09-30T11:58:00+00:00", "last_error": {"code": "RelayError"}},
                           "phone": {"state": "disconnected", "since": "2026-09-30T11:58:00+00:00"},
                           "watcher": {"alive": False, "heartbeat_at": "2026-09-30T11:40:00+00:00"},
                           "last_sync_at": "2026-09-30T11:57:00+00:00"})
    page = _page(browser_and_base, down, lambda q: _ops([]))
    assert page.inner_text("h1") == "The phone rail is down"
    assert [x.text_content() for x in page.locator(".rl-why li.bad").all()] == ["Tunnel down", "Phone disconnected", "Watcher not responding"]
    tunnel = page.locator(".rl-t").nth(0).text_content()
    assert "down" in tunnel and "RelayError" in tunnel              # a code the view has no label for is shown raw
    assert "The driver reports: not connected." in page.locator(".rl-t").nth(1).text_content()
    assert page.locator(".wa-tabs button", has_text="Rail & jobs").locator(".dot.bad").count() == 1
    page.click(".wa-tabs button:has-text('Leads')")
    page.wait_for_selector(".lh-none")
    assert page.locator(".wa-tabs button", has_text="Rail & jobs").locator(".dot.bad").count() == 1
    _done(page)


def test_a_frozen_queue_mirror_is_called_stale_and_job_states_are_named(browser_and_base):
    act = _activity(queue={"queued": 2, "running": 0, "done": 4, "failed": 1, "other": {"parked": 3}, "as_of": "2026-09-30T11:50:00+00:00"},
                    jobs=[_job("followups", overdue=True, next_run_at="2026-09-30T11:45:00+00:00"),
                          _job("tunnel_watch", last_error={"code": "tunnel_down"}, failed_24h=7),
                          _job("relay_sync", ok_24h=None, failed_24h=None),
                          _job("broadcasts", last_run_at=None, last_ok_at=None, next_run_at=None, overdue=None, ok_24h=None, failed_24h=None),
                          _job("brand_new_job")])
    page = _page(browser_and_base, act, lambda q: _ops([]))
    assert page.inner_text("h1") == "The phone rail is running, with warnings"
    assert [x.text_content() for x in page.locator(".rl-why li").all()] == ["1 job with an error", "1 job overdue", "queue mirror stale"]
    assert "The queue mirror stopped 10 min ago." in page.inner_text(".wa-note.bad")
    assert "queue mirror 10 min ago · stale" in page.inner_text(".rl-fresh")
    cells = lambda job: [c.text_content() for c in page.locator(f'tr[data-job="{job}"] td').all()]
    assert cells("followups")[1] == "overdue" and cells("followups")[4] == "due for 15 min"
    assert cells("tunnel_watch")[1] == "error tunnel downtunnel_down" and cells("tunnel_watch")[5] == "5 / 7"
    assert cells("relay_sync")[5] == "–"                              # no 24 h window exists for it: a dash, not a zero
    assert cells("broadcasts")[1:5] == ["never ran", "–", "–", "on demand"]
    assert cells("brand_new_job")[0] == "brand_new_jobbrand_new_job"  # an unknown job key is shown raw
    assert [x.text_content() for x in page.locator(".rl-q dd").nth(0).locator(".qc").all()] == ["queued 2", "running 0", "done 4", "failed 1", "parked 3"]
    _done(page)


def test_ops_follow_the_cursor_to_the_end_and_show_unknown_values_raw(browser_and_base):
    pages = {None: _ops([_op(5, "parked", kind="teleport", error={"code": "weird_code"}), _op(4, origin="pro_human", phone_masked=None)], 4),
             "4": _ops([_op(3, "failed", error={"code": "op_expired"}), _op(2)], 2), "2": _ops([_op(1)], None)}
    page = _page(browser_and_base, _activity(), lambda q: pages[q.get("before_id")])
    assert page.locator("tr[data-op]").count() == 2
    first = [c.text_content() for c in page.locator('tr[data-op="op5"] td').all()]
    assert first[1] == "teleport" and first[3] == "parked" and first[6] == "weird_code"
    second = [c.text_content() for c in page.locator('tr[data-op="op4"] td').all()]
    assert second[2] == "Human (Pro)" and second[4] == "no number" and second[5] == "7 s"
    page.click("#rl-older")
    page.wait_for_selector('tr[data-op="op2"]')
    assert [c.text_content() for c in page.locator('tr[data-op="op3"] td').all()][6] == "timed outop_expired"
    page.click("#rl-older")
    page.wait_for_selector('tr[data-op="op1"]')
    assert [r.get_attribute("data-op") for r in page.locator("tr[data-op]").all()] == ["op5", "op4", "op3", "op2", "op1"]
    assert page.locator("#rl-older").count() == 0
    assert [q["before_id"] for q in page.ops_queries if "before_id" in q][:2] == ["4", "2"]
    _done(page)


def test_an_op_on_screen_changes_state_on_the_next_pass(browser_and_base):
    calls = []

    def ops(q):
        calls.append(q)
        return _ops([_op(2, "queued" if len(calls) == 1 else "done"), _op(1)])
    page = _page(browser_and_base, _activity(), ops)
    assert page.locator('tr[data-op="op2"]').get_attribute("data-st") == "queued"
    page.wait_for_selector('tr[data-op="op2"][data-st="done"]', timeout=15000)      # the 5 s pass re-read the first page
    assert all("after_id" not in q for q in calls)
    _done(page)


def test_a_queue_count_filters_the_ops_by_state_and_origin(browser_and_base):
    page = _page(browser_and_base, _activity(), lambda q: _ops([_op(1, q.get("status", "done"))]))
    page.locator(".rl-q dd").nth(0).locator(".qc.failed").click()
    page.wait_for_selector('tr[data-op="op1"][data-st="failed"]')
    assert page.ops_queries[-1].get("status") == "failed" and "origin" not in page.ops_queries[-1]
    page.locator(".rl-q dd").nth(1).locator(".qc.queued").click()
    page.wait_for_selector('tr[data-op="op1"][data-st="queued"]')
    assert (page.ops_queries[-1].get("status"), page.ops_queries[-1].get("origin")) == ("queued", "pro")
    assert "ost=queued" in page.url and "oor=pro" in page.url
    _done(page)


def test_a_board_without_the_rail_routes_says_so_and_the_leads_tab_still_works(browser_and_base):
    page = _page(browser_and_base, (404, {"detail": "Not Found"}), lambda q: (404, {"detail": "Not Found"}), wait=".card[role=alert]")
    assert "does not know /api/wa/activity and /api/wa/ops yet" in page.inner_text(".card[role=alert]")
    assert page.locator(".rl-grid").count() == 0
    page.click(".wa-tabs button:has-text('Leads')")
    page.wait_for_selector(".lh-none")
    assert page.locator(".wa-tabs .dot").count() == 0                 # no rail state known: no dot, not a green one
    _done(page)


def test_an_ops_page_without_a_cursor_breaks_the_contract_loudly(browser_and_base):
    page = _page(browser_and_base, _activity(), lambda q: {"rows": [_op(1)]})
    assert "breaks the contract in docs/wa-pro-activity.md" in page.inner_text(".wa-note.bad")
    assert page.locator("tr[data-op]").count() == 0
    _done(page)


def test_the_rail_tab_fits_a_phone(browser_and_base):
    browser, base = browser_and_base
    page = _open(browser, f"{base}/pro.html?mock=1#/leads?tab=rail", width=390)
    page.wait_for_selector(".rl-grid")
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    _done(page)
