from __future__ import annotations

import os
import pytest
from pathlib import Path

from pf.schemas.project import Cell, HarnessBaseline
from static_fixtures import scripted_static_request as scripted_static_request


testmon_datafile = os.environ.get("TESTMON_DATAFILE")
if testmon_datafile:
    Path(testmon_datafile).parent.mkdir(parents=True, exist_ok=True)


def empty_harness_baseline(cell: Cell) -> HarnessBaseline:
    return HarnessBaseline.from_evidence(
        cell=cell,
        declaration_ids=(),
        observations=(),
    )


@pytest.fixture
def run_cache():
    from pf.static_cache import TyCheckCache
    with TyCheckCache() as cache:
        yield cache
