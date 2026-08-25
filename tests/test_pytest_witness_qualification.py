from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from pf.adapters.pytest_witness import (
    PROTOCOL,
    PYTEST_QUALIFIED_MINIMUMS,
    PYTEST_QUALIFIED_PYTHON_MINORS,
)


MANIFEST = Path("tests/pytest_witness_qualification/matrix-manifest.json")
PACKAGING_19_MANIFEST = Path(
    "tests/pytest_witness_qualification/packaging-19-manifest.json"
)


def test_committed_matrix_matches_production_qualification_authority() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["protocol"] == PROTOCOL
    assert manifest["execution_count"] == 24 * 12
    assert manifest["all_profiles_qualified"] is True
    core = [item for item in manifest["profiles"] if not item["current_plugins"]]
    assert {
        (item["python_minor"], item["pytest_version"]) for item in core
    } == {
        (minor, version)
        for minor in PYTEST_QUALIFIED_PYTHON_MINORS
        for version in ("6.2.5", "7.0.1", "7.4.4", "8.0.2", "8.4.2", "9.0.2", "9.1.1")
    }
    observed_minimums = {
        int(item["pytest_version"].split(".", 1)[0]): item["pytest_version"]
        for item in sorted(core, key=lambda item: item["pytest_version"], reverse=True)
    }
    assert observed_minimums == PYTEST_QUALIFIED_MINIMUMS


def test_current_plugin_matrix_covers_every_python_minor() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current = [item for item in manifest["profiles"] if item["current_plugins"]]

    assert {item["python_minor"] for item in current} == set(
        PYTEST_QUALIFIED_PYTHON_MINORS
    )
    assert all(item["pytest_version"] == "9.1.1" for item in current)
    assert all(item["qualified"] and item["case_count"] == 12 for item in current)


def test_qualification_runner_lists_the_committed_case_contracts() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    process = subprocess.run(
        (sys.executable, "scripts/qualify_pytest_witness.py", "--list-cases"),
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(process.stdout) == [
        item["name"] for item in manifest["case_contracts"]
    ]


def test_qualification_runner_replays_current_profile() -> None:
    process = subprocess.run(
        (
            sys.executable,
            "scripts/qualify_pytest_witness.py",
            "--inner",
            "--plugin-source",
            "src/pf/_pytest_failure_witness.py",
        ),
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(process.stdout)

    assert result["pytest_version"] == "9.1.1"
    assert len(result["cases"]) == 12
    assert all(item["expected"] for item in result["cases"])


def test_packaging_19_dogfood_reaches_probe_rejection_on_every_minor() -> None:
    manifest = json.loads(PACKAGING_19_MANIFEST.read_text(encoding="utf-8"))

    assert manifest == {
        "schema": "pf-packaging-19-pytest-witness-v1",
        "packaging_version": "19.2",
        "pytest_version": "6.2.5",
        "host_scope": "x86_64-unknown-linux-gnu",
        "all_profiles_qualified": True,
        "profiles": [
            {
                "python_minor": minor,
                "process_exit": 2,
                "test_outcome": "TEST_FAIL",
                "evaluation": "TEST_FAIL",
                "cause": "TEST_FAILURE",
                "disposition": "REJECTED",
                "probe_status": "REJECTED",
                "qualified": True,
            }
            for minor in ("3.10", "3.11", "3.12")
        ],
    }
