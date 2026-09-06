"""Render edge/pflege-ingest/index.ts from pflege_jobs/schema.py so column lists never drift.
Usage: python edge/build_ingest.py  (then deploy the function)"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pflege_jobs.schema import OBS_SPEC, EMP_SPEC, VERIFY_SPEC, CLINIC_SPEC, LINK_SPEC, IDENTITY, pg_record_def
root = pathlib.Path(__file__).resolve().parent
tpl = (root / "pflege-ingest" / "index.template.ts").read_text()
out = (tpl.replace("__OBS_COLS_DEF__", pg_record_def(OBS_SPEC)).replace("__EMP_COLS_DEF__", pg_record_def(EMP_SPEC))
          .replace("__VERIFY_COLS_DEF__", pg_record_def(VERIFY_SPEC)).replace("__CLINIC_COLS_DEF__", pg_record_def(CLINIC_SPEC)).replace("__LINK_COLS_DEF__", pg_record_def(LINK_SPEC)).replace("__IDENTITY__", ",".join(IDENTITY)))
(root / "pflege-ingest" / "index.ts").write_text(out)
print("rendered edge/pflege-ingest/index.ts", len(out), "bytes")
