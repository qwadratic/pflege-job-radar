"""TASK-107 AC#1: app/cv.py's match() reads j.get('department_hint') off the live app.data snapshot,
where TASK-97 made every job's department_hint a real list (even a single match becomes a 1-element
list, app/data.py:249) -- never a bare string on a live row. The pre-TASK-97 check here was
`d = j.get('department_hint'); if d and d in depts:` -- `list in set-of-strings` raises
`TypeError: unhashable type: 'list'` for every open posting carrying any department_hint at all
(1657/3638 live, 2026-09-24), not just multi-label ones. This regression-tests match()'s current
set-intersection fix, which mirrors app/data.py's own filter_jobs() dept block."""
from app import cv as CV
from app import data as D


def _prof(departments):
    return {"roles": [], "departments": departments, "cities": [], "regierungsbezirke": [], "skills": []}


def test_single_label_list_department_hint_scores_without_crashing(monkeypatch):
    # The real live shape post-TASK-97: even one matched department is a 1-element list.
    jobs = [{"posting_id": 1, "title": "Pflegefachkraft", "department_hint": ["Intensiv/IMC"], "department_raw": None}]
    monkeypatch.setattr(D, "snapshot", lambda: {"jobs": jobs})
    out = CV.match(_prof(["Intensiv/IMC"]))
    assert len(out) == 1
    assert out[0]["score"] >= 30 and "Intensiv/IMC" in out[0]["why"]


def test_multi_label_list_department_hint_intersects_correctly(monkeypatch):
    jobs = [{"posting_id": 2, "title": "Pflegefachkraft", "department_hint": ["Intensiv/IMC", "Anästhesie"], "department_raw": None}]
    monkeypatch.setattr(D, "snapshot", lambda: {"jobs": jobs})
    out = CV.match(_prof(["Anästhesie"]))
    assert len(out) == 1 and "Anästhesie" in out[0]["why"]


def test_bare_string_department_hint_still_tolerated(monkeypatch):
    # Older cached rows / hand-built fixtures may still carry a bare string -- must not crash either.
    jobs = [{"posting_id": 3, "title": "Pflegefachkraft", "department_hint": "Psychiatrie", "department_raw": None}]
    monkeypatch.setattr(D, "snapshot", lambda: {"jobs": jobs})
    out = CV.match(_prof(["Psychiatrie"]))
    assert len(out) == 1 and "Psychiatrie" in out[0]["why"]


def test_no_department_hint_does_not_crash_and_gets_no_overlap_bonus(monkeypatch):
    # score stays below the >=25 cutoff (generic-ward +6, no role/city match) -- proves no crash AND
    # no false +30 overlap bonus, without depending on the cutoff's exact value.
    jobs = [{"posting_id": 4, "title": "Pflegefachkraft", "department_hint": None, "department_raw": None}]
    monkeypatch.setattr(D, "snapshot", lambda: {"jobs": jobs})
    out = CV.match(_prof(["Intensiv/IMC"]))
    assert out == []
