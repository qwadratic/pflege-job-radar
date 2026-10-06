"""The board fallback never files an employer that is not a clinic (TASK-431.9, Ivan 2026-10-06: care homes, rescue services and schools are outside
the clinic registry; the pipeline already says so in employers.employer_class = non_clinic, patterns.json -> employer.non_clinic).

R0_board, R0_board_town and their tie-breaks attach a posting to a clinic of the board's pool from the BOARD alone: nothing in the posting names the
clinic. For an employer the pipeline itself classifies non_clinic that is not provenance, it is a wrong filing, and ingest then relabels the
employer 'clinic' (registry_match) so it counts in every clinic statistic. On the mirror, with the registry of 2026-10-06:
  wp_jobs__waldkrankenhaus.de          "Malteser Hilfsdienst e.V." Erlangen          12 postings under a clinic of the pool (R0_board_town_bestsite)
  wp_jobs__allgaeuer-jobs.de           "Bayerisches Rotes Kreuz - Kreisverband Ostallgaeu" Fuessen   5 (R0_board)
  pi_asp__pflegejobs.brk-muenchen.de   "BRK Muenchen"                                 2 (R0_board)
  dvinci__romed-jobs.de                "Berufsfachschulen Wasserburg"                 1 (R0_board_town)
Content rules are untouched: an employer that names a registry clinic or its operator is still linked by name (a hospital operator whose legal form
the keyword list reads as non_clinic, "Diakoniewerk Martha-Maria e. V.", keeps its R3/R6 links). The posting stays unlinked, keeps its own city and
the employer named on the page, and employer_class stays non_clinic.
"""
import pytest

from tests import mirror_rows as MR

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

# the two fast boards; allgaeuer-jobs (3 986 rows, 3 minutes) and the BRK Muenchen P&I board (100 s) show the same on the mirror and are left out for their run time
BOARDS = ["wp_jobs__waldkrankenhaus.de", "dvinci__romed-jobs.de"]


@pytest.mark.completeness
@pytest.mark.parametrize("board_id", BOARDS)
def test_board_fallback_never_files_an_employer_the_pipeline_classifies_non_clinic(board_id):
    m = MR.registry()
    filed = []
    seen = 0
    for row in MR.board_rows(board_id):
        if row["employer_inherited"] or row["employer_class"] != "non_clinic":
            continue
        seen += 1
        got = MR.match(m, row)
        if got and got[1].startswith("R0_board"):
            filed.append((row["employer"][:40], row["city"], got[0], got[1]))
    assert seen, f"{board_id} :: no posting of a non_clinic employer on the board: the case this test stands for is gone from the mirror"
    assert not filed, f"{board_id} :: employer_class :: {len(filed)}/{seen} postings of a non_clinic employer filed under a clinic by the board alone, e.g. {filed[:2]}"
