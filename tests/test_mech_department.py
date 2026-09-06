from pflege_jobs.classify import department_hint
from pflege_jobs.mechanics import get


def test_common_departments():
    assert department_hint("Pflegefachkraft Intensivstation") == "Intensiv/IMC"
    assert department_hint("Pflegefachkraft OP (m/w/d)") == "OP"
    assert department_hint("Pflegefachkraft Notaufnahme (m/w/d)") == "Notaufnahme"
    assert department_hint("Pflegefachkraft (m/w/d) für die Anästhesie") == "Anästhesie"


def test_order_decides_on_multiple_hits():
    assert department_hint("Pflegefachkraft Intensivstation Innere Medizin") == "Intensiv/IMC"


def test_null_when_not_stated():
    assert department_hint("Pflegefachkraft (m/w/d)") is None
    assert get("department").run({"title": "Pflegefachkraft (m/w/d)"}) == {"result": {"department_hint": None}, "rule": None}
