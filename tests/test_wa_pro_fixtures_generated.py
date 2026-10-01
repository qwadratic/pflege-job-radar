"""The committed tests/fixtures/wa_pro_api/*.json files must be exactly what
tools/wa_pro_fixtures.py produces -- never hand-edited afterwards. This is the drift guard: it runs
the real generator (seeding a throwaway sqlite through app/wa/store.py and reading the real
app/wa/pro_api.py routes via a starlette TestClient, see that module's docstring) into a tmp
directory and asserts the output is byte-for-byte identical to what is checked in. A failure here
means either the fixtures were edited by hand (regenerate instead: `python -m tools.wa_pro_fixtures
--write`) or app/wa/pro_api.py / app/wa/pro_models.py changed in a way that moved the goalposts
(regenerate, review the diff, commit both)."""
import pathlib

from tools import wa_pro_fixtures as GEN


def test_generated_fixtures_match_committed_files(tmp_path):
    fresh = GEN.write(output_dir=tmp_path, sqlite_dir=tmp_path)
    assert set(fresh) == set(GEN.FIXTURE_FILES)
    for name in GEN.FIXTURE_FILES:
        committed = (GEN.FIXTURES_DIR / name).read_text(encoding="utf-8")
        assert fresh[name] == committed, (
            f"{name} differs from a fresh `python -m tools.wa_pro_fixtures` run -- "
            f"regenerate with --write instead of hand-editing the fixture"
        )


def test_generator_is_deterministic_across_independent_runs(tmp_path):
    """Two independent generator runs (two throwaway sqlite files, two TestClient lifespans) must
    produce the same bytes -- this is what makes the fixture reviewable as a diff instead of noise."""
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    out_a.mkdir()
    out_b.mkdir()
    first = GEN.write(output_dir=out_a, sqlite_dir=out_a)
    second = GEN.write(output_dir=out_b, sqlite_dir=out_b)
    assert first == second
