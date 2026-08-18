from __future__ import annotations

import os
from pathlib import Path


testmon_datafile = os.environ.get("TESTMON_DATAFILE")
if testmon_datafile:
    Path(testmon_datafile).parent.mkdir(parents=True, exist_ok=True)
