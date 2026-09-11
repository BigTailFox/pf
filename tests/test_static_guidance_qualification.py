from __future__ import annotations

import json
import os
from pathlib import Path
from runpy import run_path
import subprocess
import sys
from typing import Any, Callable, cast

import pytest


pytestmark = pytest.mark.qualification

SCRIPT = run_path("scripts/qualify_static_guidance.py")
CONTROLLED = cast(Callable[..., dict[str, Any]], SCRIPT["qualify_controlled"])


def assert_controlled(record: dict[str, Any]) -> None:
    assert record["schema"] == "pf-static-guidance-qualification-v1"
    assert record["mode"] == "controlled"
    assert record["profile"] == "controlled-prepare-ty-verifier-v1"
    assert record["status"] == "PASS"
    assert record["cells"]
    assert all(item.get("final_pass") is True for item in record["cells"])
    assert record["static_journal"]
    assert any(
        item["highest"]["kind"] == "collected"
        and item["highest"]["subject_identity"]
        and item["highest"]["cache_identity"]
        and item["highest"]["fact_identity"]
        for item in record["static_journal"]
    )
    for cell in record["cells"]:
        assert cell["final_verifier_terminal"] == {
            "kind": "normal-exit",
            "exit_code": 0,
        }


@pytest.fixture(scope="module")
def controlled(tmp_path_factory):
    output = tmp_path_factory.mktemp("static-guidance") / "controlled.json"
    environment = dict(os.environ)
    environment["PATH"] = str(Path(".venv/bin").resolve()) + os.pathsep + environment["PATH"]
    subprocess.run(
        [sys.executable, "scripts/qualify_static_guidance.py", "--mode", "controlled", "--output", str(output)],
        env=environment,
        check=True,
        timeout=120,
    )
    return json.loads(output.read_text())


class TestStaticGuidanceQualification:
    def test_controlled_prepare_ty_and_verifier_persist_static_audit(self, controlled) -> None:
        assert_controlled(controlled)
