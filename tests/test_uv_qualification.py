from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
import sys
from typing import Callable, Protocol, cast

import pytest


class NamedCase(Protocol):
    name: str


_script = run_path("scripts/qualify_uv.py")
CASES = cast(
    tuple[NamedCase, ...],
    _script["CASES"],
)
_qualification_main = cast(
    Callable[[], None],
    _script["main"],
)


class TestUvQualificationRunner:
    def test_committed_matrix_manifest_matches_supported_profiles(self) -> None:
        from pf.resolution import UV_DIAGNOSTIC_PROFILES, UV_PROTOCOL_IDENTITY

        manifest = json.loads(
            Path("tests/uv_qualification/matrix-manifest.json").read_text()
        )

        assert manifest["protocol_identity"] == UV_PROTOCOL_IDENTITY
        assert manifest["execution_count"] == 13
        assert manifest["all_outputs_complete"] is True
        assert {
            item["version"]: item["profile"] for item in manifest["versions"]
        } == UV_DIAGNOSTIC_PROFILES
        assert [item["outcome"] for item in manifest["cases"]].count("UNSAT") == 2

    def test_matrix_contains_every_required_case(self) -> None:
        assert {case.name for case in CASES} == {
            "pure-version-contradiction",
            "transitive-version-contradiction",
            "package-unavailable",
            "package-version-unavailable",
            "platform-wheel-unavailable",
            "requires-python-mismatch",
            "index-401",
            "index-403",
            "index-timeout",
            "metadata-failure",
            "hash-mismatch",
            "sdist-build-failure",
            "offline-cache-miss",
        }

    def test_runner_qualifies_a_certified_local_case(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "qualify_uv.py",
                "--case",
                "pure-version-contradiction",
            ],
        )

        _qualification_main()

        result = json.loads(capsys.readouterr().out)
        assert len(result["matrix"]) == 1
        assert result["matrix"][0]["case"] == "pure-version-contradiction"
        assert result["matrix"][0]["expected"] is True
