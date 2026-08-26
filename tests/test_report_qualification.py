from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INLINE_FIXTURE = (
    ROOT / "tests" / "fixtures" / "report-schema" / "pf-self-search-inline.json"
)


@pytest.mark.skipif(
    not INLINE_FIXTURE.is_file(),
    reason="D014 fixed Schema 1 qualification fixture is not available",
)
def test_pf_self_search_report_meets_schema_2_qualification() -> None:
    subprocess.run(
        [sys.executable, "scripts/qualify_report_schema.py", "--check"],
        cwd=ROOT,
        check=True,
    )
