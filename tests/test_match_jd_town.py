"""R_jd_text: a clinic named in the description is not the posting's clinic when the posting's own place is another town (TASK-132, TASK-431.9).

_match_jd was the one content rung with no town awareness: a unique name/operator token hit anywhere in the registry won, whatever place the
posting states. On the mirror, with the registry of 2026-10-06 and the rows each board's adapter gives today:
  kbo.de boards   "kbo-Ambulanter Psychiatrischer Pflegedienst", Muenchen   -> 17501 (Taufkirchen), the clinic the description mentions
  Starnberger Kliniken   "Starnberger Kliniken GmbH", Herrsching am Ammersee -> 18804, a clinic in another town
The posting names its place, the registry has no clinic of that employer there, and a mention in the text is weaker than the place: the rung
now refuses and the posting stays unlinked, with its own city and employer (Ivan 2026-10-06, TASK-431.9: no registry clinic at that place -> do not
link). A row without a place of its own, or with the seed clinic's copied town, still passes through unchanged.
"""
import pytest

from pflege_jobs.registry import _town_match, city_key
from tests import mirror_rows as MR

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

BOARDS = ["typo3_jobs__kbo.de-1", "typo3_jobs__kbo-iak.de", "softgarden__starnberger-kliniken.de", "typo3_jobs__barmherzige-bieten-zukunft.de"]


@pytest.mark.completeness
@pytest.mark.parametrize("board_id", BOARDS)
def test_r_jd_text_never_files_a_posting_under_a_clinic_in_another_town(board_id):
    m = MR.registry()
    wrong = []
    for row in MR.board_rows(board_id):
        got = MR.match(m, row)
        if not got or got[1] != "R_jd_text" or row["city_inherited"] or not city_key(row["city"]):
            continue
        town = m.by_id[got[0]].get("town")
        if not _town_match(city_key(town), city_key(row["city"])):
            wrong.append((row["employer"][:40], row["city"], got[0], town))
    assert not wrong, f"{board_id} :: place :: {len(wrong)} R_jd_text link(s) to a clinic in another town than the posting's own, e.g. {wrong[:3]}"
