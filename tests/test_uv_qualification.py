from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


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
        process = subprocess.run(
            (sys.executable, "scripts/qualify_uv.py", "--list-cases"),
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=True,
        )

        assert set(json.loads(process.stdout)) == {
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

    def test_runner_qualifies_certified_and_ambiguous_local_cases(self) -> None:
        from pf.resolution import UV_SUPPORTED_VERSIONS

        uv_binary = shutil.which("uv")
        assert uv_binary is not None
        version_output = subprocess.run(
            (uv_binary, "--version"),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        uv_version = version_output.split()[1]
        assert uv_version in UV_SUPPORTED_VERSIONS
        process = subprocess.run(
            (
                sys.executable,
                "scripts/qualify_uv.py",
                "--case",
                "pure-version-contradiction",
                "--case",
                "package-version-unavailable",
                "--uv-bin",
                uv_binary,
                "--expected-version",
                uv_version,
            ),
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(process.stdout)

        assert result["uv_version"] == uv_version
        assert all(
            Path(item["command"][0]).is_absolute() for item in result["matrix"]
        )
        assert all(item["expected"] for item in result["matrix"])
