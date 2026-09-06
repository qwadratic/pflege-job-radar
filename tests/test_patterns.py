"""patterns.json is the single home of every classification regex: it must load, compile, and be hot-reloadable."""
import json, os, re, shutil
from pflege_jobs import config as C
from pflege_jobs import classify


def test_patterns_file_loads_and_every_regex_compiles():
    p = C.load()
    assert p["version"] >= 1
    n = 0
    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "re" or (isinstance(v, str) and k in ("pflege_gate", "nicht_pflege", "strong_pflege", "legal_forms", "experience_years", "languages")
                                 or (isinstance(v, str) and k in p["enrichment"] and o is p["enrichment"])):
                    re.compile(v, re.I); n += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o: walk(x)
    walk(p)
    assert n > 60
    assert [r["role_class"] for r in p["role"]["rules"]][:2] == ["werkstudent_praktikum", "ausbildung"]   # order = precedence
    assert {"nicht_pflege", "ausbildung", "werkstudent_praktikum"} == set(p["excluded_role_classes"])
    assert len(p["cv"]["skills"]) >= 20


def test_module_names_mirror_json():
    p = C.PATTERNS
    assert C.CLINIC_PATTERNS[0] == (p["employer"]["clinic"][0]["name"], p["employer"]["clinic"][0]["re"])
    assert C.ROLE_RULES == [(r["role_class"], r["re"]) for r in p["role"]["rules"]]
    assert C.EXCLUDED_ROLE_CLASSES == set(p["excluded_role_classes"])


def test_validate_rejects_bad_regex():
    p = json.loads(json.dumps(C.PATTERNS)); p["department"].append({"hint": "X", "re": "(unclosed"})
    try:
        C.validate(p); assert False, "expected ValueError"
    except ValueError as e:
        assert "department" in str(e)


def test_reload_picks_up_changed_file(tmp_path):
    src = C.PATTERNS_PATH; alt = tmp_path / "patterns.json"
    shutil.copy(src, alt)
    p = json.load(open(alt, encoding="utf-8"))
    p["department"].insert(0, {"hint": "Zebra-Station", "re": r"zebra"})
    p["employer"]["clinic"].append({"name": "testtoken", "re": r"\bxyzklinik\b"})
    json.dump(p, open(alt, "w", encoding="utf-8"), ensure_ascii=False)
    assert classify.department_hint("Pflegekraft Zebra") is None
    try:
        C.reload(str(alt))
        assert classify.department_hint("Pflegekraft Zebra") == "Zebra-Station"
        assert classify.classify_employer("xyzklinik GmbH") == ("clinic", "clinic:klinik")     # 'klinik' still wins first
        assert classify.classify_employer("Haus xyzklinik") == ("clinic", "clinic:klinik")
        assert ("testtoken", r"\bxyzklinik\b") in C.CLINIC_PATTERNS
    finally:
        C.reload(src)                                   # never leak test patterns into other tests
    assert classify.department_hint("Pflegekraft Zebra") is None


def test_save_is_validated_and_atomic(tmp_path):
    out = tmp_path / "p.json"
    C.save(C.PATTERNS, str(out))
    assert json.load(open(out, encoding="utf-8"))["version"] == C.PATTERNS["version"] and not os.path.exists(str(out) + ".tmp")
    bad = json.loads(json.dumps(C.PATTERNS)); bad["role"]["pflege_gate"] = "["
    try:
        C.save(bad, str(out)); assert False
    except ValueError:
        pass
