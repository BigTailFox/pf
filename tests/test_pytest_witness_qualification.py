from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
import sys
from typing import Callable, cast

import pytest

from pf.adapters.pytest_witness import (
    PROTOCOL,
    PYTEST_QUALIFIED_MINIMUMS,
    PYTEST_QUALIFIED_PYTHON_MINORS,
)


MANIFEST = Path("tests/pytest_witness_qualification/matrix-manifest.json")
PACKAGING_19_MANIFEST = Path(
    "tests/pytest_witness_qualification/packaging-19-manifest.json"
)
_qualification_main = cast(
    Callable[[], int],
    run_path("scripts/qualify_pytest_witness.py")["main"],
)


class TestPytestWitnessQualificationManifest:
    def test_qualification_manifest_matches_production_authority(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        assert manifest["protocol"] == PROTOCOL
        assert manifest["execution_count"] == 24 * 12
        assert manifest["all_profiles_qualified"] is True
        core = [item for item in manifest["profiles"] if not item["current_plugins"]]
        assert {(item["python_minor"], item["pytest_version"]) for item in core} == {
            (minor, version)
            for minor in PYTEST_QUALIFIED_PYTHON_MINORS
            for version in (
                "6.2.5",
                "7.0.1",
                "7.4.4",
                "8.0.2",
                "8.4.2",
                "9.0.2",
                "9.1.1",
            )
        }
        observed_minimums = {
            int(item["pytest_version"].split(".", 1)[0]): item["pytest_version"]
            for item in sorted(
                core, key=lambda item: item["pytest_version"], reverse=True
            )
        }
        assert observed_minimums == PYTEST_QUALIFIED_MINIMUMS

    def test_qualification_manifest_covers_every_python_minor(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        current = [item for item in manifest["profiles"] if item["current_plugins"]]

        assert {item["python_minor"] for item in current} == set(
            PYTEST_QUALIFIED_PYTHON_MINORS
        )
        assert all(item["pytest_version"] == "9.1.1" for item in current)
        assert all(item["qualified"] and item["case_count"] == 12 for item in current)


class TestPytestWitnessQualificationRunner:
    def test_qualification_runner_lists_the_committed_case_contracts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["qualify_pytest_witness.py", "--list-cases"],
        )

        assert _qualification_main() == 0

        listed_cases = json.loads(capsys.readouterr().out)
        committed_cases = {item["name"] for item in manifest["case_contracts"]}

        assert len(listed_cases) == len(set(listed_cases))
        assert set(listed_cases) == committed_cases

    def test_qualification_runner_executes_current_profile_case(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "qualify_pytest_witness.py",
                "--inner",
                "--case",
                "no-tests-collected",
                "--plugin-source",
                "src/pf/_pytest_failure_witness.py",
            ],
        )

        assert _qualification_main() == 0

        result = json.loads(capsys.readouterr().out)
        assert result["pytest_version"] == "9.1.1"
        assert [item["case"] for item in result["cases"]] == ["no-tests-collected"]
        assert result["cases"][0]["expected"] is True


class TestPackaging19QualificationManifest:
    def test_packaging_19_manifest_records_probe_rejection_for_every_minor(
        self,
    ) -> None:
        manifest = json.loads(PACKAGING_19_MANIFEST.read_text(encoding="utf-8"))
        metadata = {key: value for key, value in manifest.items() if key != "profiles"}
        profiles = {
            item["python_minor"]: {
                key: value for key, value in item.items() if key != "python_minor"
            }
            for item in manifest["profiles"]
        }

        assert metadata == {
            "schema": "pf-packaging-19-pytest-witness-v1",
            "packaging_version": "19.2",
            "pytest_version": "6.2.5",
            "host_scope": "x86_64-unknown-linux-gnu",
            "all_profiles_qualified": True,
        }
        expected_profile = {
            "process_exit": 2,
            "test_outcome": "TEST_FAIL",
            "evaluation": "TEST_FAIL",
            "cause": "TEST_FAILURE",
            "disposition": "REJECTED",
            "probe_status": "REJECTED",
            "qualified": True,
        }
        assert len(manifest["profiles"]) == len(profiles)
        assert profiles == {
            minor: expected_profile for minor in ("3.10", "3.11", "3.12")
        }
