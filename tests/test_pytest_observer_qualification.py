from __future__ import annotations

import hashlib
import json
from pathlib import Path
from runpy import run_path
import sys
from typing import Callable, cast

import pytest

from pf.adapters.pytest_observer import PROTOCOL


MANIFEST = Path("tests/pytest_observer_qualification/matrix-manifest.json")
_qualification_main = cast(
    Callable[[], int],
    run_path("scripts/qualify_pytest_observer.py")["main"],
)


class TestPytestObserverQualificationManifest:
    def test_transparency_manifest_matches_observer_protocol(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        assert manifest["protocol"] == PROTOCOL
        assert manifest["execution_count"] == 24 * 12
        assert manifest["all_profiles_transparent"] is True
        core = [item for item in manifest["profiles"] if not item["current_plugins"]]
        assert {(item["python_minor"], item["pytest_version"]) for item in core} == {
            (minor, version)
            for minor in {"3.10", "3.11", "3.12"}
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

    def test_transparency_manifest_covers_every_python_minor(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        current = [item for item in manifest["profiles"] if item["current_plugins"]]

        assert {item["python_minor"] for item in current} == set(
            {"3.10", "3.11", "3.12"}
        )
        assert all(item["pytest_version"] == "9.1.1" for item in current)
        assert all(item["transparent"] and item["case_count"] == 12 for item in current)


class TestPytestObserverQualificationRunner:
    def test_transparency_runner_lists_the_committed_case_contracts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["qualify_pytest_observer.py", "--list-cases"],
        )

        assert _qualification_main() == 0

        listed_cases = json.loads(capsys.readouterr().out)
        committed_cases = {item["name"] for item in manifest["case_contracts"]}

        assert len(listed_cases) == len(set(listed_cases))
        assert set(listed_cases) == committed_cases

    def test_transparency_runner_replays_the_committed_current_profile(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "qualify_pytest_observer.py",
                "--inner",
                "--autoload",
                "--plugin-source",
                "src/pf/_pytest_observer.py",
            ],
        )

        assert _qualification_main() == 0

        result = json.loads(capsys.readouterr().out)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        profile = next(
            item
            for item in manifest["profiles"]
            if item["current_plugins"]
            and item["python_minor"] == result["python_minor"]
            and item["pytest_version"] == result["pytest_version"]
        )
        canonical = (
            json.dumps(
                result["cases"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")

        assert len(result["cases"]) == 12
        assert all(case["expected"] for case in result["cases"])
        assert hashlib.sha256(canonical).hexdigest() == profile["results_sha256"]
