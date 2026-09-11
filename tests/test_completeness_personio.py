"""Adapter-specific completeness regressions for personio (TASK-30) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live run found that the generic checks can't exercise offline:

  TLD          personio_domain must keep whichever TLD (.de or .com) the tenant actually links --
               munich-airport-clinic.com only ever links the .com form; forcing .de still returns
               200 (personio serves both), so the generic checks pass by accident while the client's
               own .com read path stays uncovered. Verified missing live 2026-09-10.
  WP plugin    sites running the "Personio Integration Light" WordPress plugin (ProSomno) have no
               <slug>.jobs.personio.* tenant at all; before this fix crawl_personio silently fell
               through to crawl_wp_jobs and returned 0 rows for prosomno.de even though 5 real
               postings are public at wp-json/wp/v2/personioposition -- the shared harness's checks
               are all gated on `if not rows: return True`, so a silent 0 never went red on its own.
               Verified missing live 2026-09-10 (0/5 rows before the fix).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402


def test_personio_domain_keeps_the_com_tld_when_thats_all_the_page_links():
    monkeypatch_get = _router({
        "https://munich-airport-clinic.com/karriere/":
            _R("<a href='https://medicare-flughafen-muenchen.jobs.personio.com/'>Jobs</a>", ok=True),
    })
    domain = va.personio_domain("https://munich-airport-clinic.com/karriere/", session=_FakeSession(monkeypatch_get))
    assert domain == "medicare-flughafen-muenchen.jobs.personio.com"


class _FakeSession:
    """va.get() takes `session=...` and calls session.get(...) -- wrap a bare fake_get(url) function
    (the shape _router returns) so personio_domain's own HTTP fallback path can be driven directly,
    without a full crawl_personio() run."""
    def __init__(self, fake_get):
        self._fake_get = fake_get

    def get(self, u, headers=None, timeout=None, allow_redirects=None):
        return self._fake_get(u)


def test_personio_never_silently_returns_zero_rows_for_a_wp_plugin_tenant(monkeypatch):
    """A board genuinely served by the Personio Integration Light WP plugin, with real published
    posts, must come back with rows -- zero is never "ok" (project rule), and the shared harness's
    field/url/round-trip checks all no-op on an empty row list, so this can only be caught here."""
    careers_url = "https://prosomno.de/ueber-uns/jobs/"
    posts = [
        {"title": {"rendered": "Pflegefachkraft (m/w/d)"},
         "excerpt": {"rendered": "<h3>Festangestellte • Vollzeit • München</h3>"},
         "content": {"rendered": "<p>Aufgaben...</p>"},
         "date": "2026-03-01T00:00:00", "link": "https://prosomno.de/stelle/a/"},
        {"title": {"rendered": "MFA (m/w/d)"},
         "excerpt": {"rendered": "<h3>Teilzeit • München</h3>"},
         "content": {"rendered": "<p>Aufgaben...</p>"},
         "date": "2026-02-01T00:00:00", "link": "https://prosomno.de/stelle/b/"},
    ]
    monkeypatch.setattr(va, "get", _router({
        careers_url: _R(ok=False),
        "https://prosomno.de/wp-json/wp/v2/personioposition?per_page=100": _R(ok=True, json_data=posts),
    }))
    rows = va.crawl_personio({"name": "ProSomno", "careers_url": careers_url})
    assert len(rows) == 2  # not 0 -- every listed posting comes back, no narrowing
    assert all(r["payload"]["description"] for r in rows)
    assert all(r["payload"]["datePosted"] for r in rows)
