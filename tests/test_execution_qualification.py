from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from runpy import run_path
import subprocess
import sys
from typing import Any, Callable, cast

import pytest


SCRIPT = run_path("scripts/qualify_execution_failures.py")
FIXTURES = cast(Callable[..., dict[str, bytes]], SCRIPT["fixtures"])
MANIFEST = Path("tests/execution_qualification/2026-09-07-d038-uv-0.12.5-v1.json")


def assert_case(record: dict[str, Any]) -> None:
    operation = record["operation"]
    failure = record["failure"]
    assert failure["stage"] == f"{operation}-project"
    assert failure["disposition"] == "REJECTED"
    assert failure["cause"] == ("RESOLUTION_FAILED" if operation == "resolve" else "INSTALLATION_FAILED")
    assert failure["authority"] == {
        "kind": "execution", "terminal": {"kind": "normal-exit", "exit_code": 1},
        "attribution": {"kind": "unattributed"},
    }
    assert ("project_plan_digest" in failure) == (operation == "install")
    assert "environment_plan_digest" not in failure
    assert [pin["version"] for pin in record["baseline_vector"]] == ["3"]
    assert [pin["version"] for pin in record["final_vector"]] == ["2"]
    assert record["final_evaluation"]["status"] == "PASS"
    assert record["final_evaluation"]["verifier"]["terminal"] == {"kind": "normal-exit", "exit_code": 0}
    boundary = record["search"]["boundaries"][0]
    assert (boundary["floor"], boundary["predecessor"]) == ("2", "1")
    assert boundary["predecessor_failure_id"] == failure["failure_id"]
    if record["global_comparison"] is None:
        assert record["static_unavailable_detail"]
    elif record["write_bytecode"]:
        assert record["global_comparison"] == {"status": "UNCOMPARED", "reason": "context-mismatch"}
    else:
        assert record["global_comparison"]["status"] == "COMPARED"
        assert record["global_comparison"]["state"] == "STATIC_UNCHANGED"
        assert record["global_comparison"]["incremental_identities"] == []
    assert record["report_roundtrip"] is True
    assert record["full_verifier_count"] == 2
    assert record["new_full_pass_after_rejection"] is True






@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    output = tmp_path_factory.mktemp("execution-replay") / "result.json"
    environment = dict(os.environ)
    environment["PATH"] = str(Path(".venv/bin").resolve()) + os.pathsep + environment["PATH"]
    subprocess.run([
        sys.executable, "scripts/qualify_execution_failures.py", "--output", str(output),
    ], env=environment, check=True, timeout=60)
    return json.loads(output.read_text())


class TestExecutionFailureQualification:
    def test_manifest_covers_resolution_and_installation_failures(
        self,
    ) -> None:
        manifest = json.loads(MANIFEST.read_text())
        assert manifest["schema"] == "pf-execution-failure-qualification-v1"
        assert manifest["uv_version"] == "0.12.5"
        assert manifest["protocol"] == "uv-pip-compile-pylock-v1"
        assert manifest["profile"] == "uv-diagnostics-0.12.5-v1"
        assert manifest["failure_policy"] == "failure-execution-v4"
        assert {case["operation"] for case in manifest["cases"]} == {
            "resolve",
            "install",
        }
        for case in manifest["cases"]:
            assert_case(case)
            artifacts = FIXTURES(
                f"pf-execution-{case['operation']}",
                static_metadata=case["sdist_static_metadata"],
            )
            assert case["artifact_sha256"] == {
                filename: hashlib.sha256(content).hexdigest()
                for filename, content in artifacts.items()
            }

    @pytest.mark.qualification
    @pytest.mark.parametrize("operation", ["resolve", "install"], ids=("resolve", "install"))
    @pytest.mark.parametrize("write_bytecode", [True, False], ids=("bytecode", "no-bytecode"))
    def test_replay_searches_to_full_pass_after_execution_rejection(
        self, replay, operation, write_bytecode
    ):
        assert_case(
            next(case for case in replay["cases"]
                 if case["operation"] == operation and case["write_bytecode"] == write_bytecode)
        )
