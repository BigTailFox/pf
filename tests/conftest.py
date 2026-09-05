from __future__ import annotations

import os
from pathlib import Path

from pf.schemas.project import Cell, HarnessBaseline


testmon_datafile = os.environ.get("TESTMON_DATAFILE")
if testmon_datafile:
    Path(testmon_datafile).parent.mkdir(parents=True, exist_ok=True)


def empty_harness_baseline(cell: Cell) -> HarnessBaseline:
    return HarnessBaseline.from_evidence(
        cell=cell,
        declaration_ids=(),
        observations=(),
    )
