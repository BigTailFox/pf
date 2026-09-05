from __future__ import annotations

from pathlib import Path
import platform
import subprocess
import sys
import json

import pytest

from pf.project import ProjectLoader
from pf.report import ReportStore


class TestInstalledCli:
    def test_missing_group_and_missing_verifier_record_actual_start_failure(
        self, tmp_path
    ):
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text("VALUE = 1\n")
        (tmp_path / "pyproject.toml").write_text("""
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-group = "missing"
test-command = ["pf-d035-unavailable-verifier"]
""")
        result = subprocess.run(
            [sys.executable, "-m", "pf", "search"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 4, (result.stdout, result.stderr)
        report = ReportStore().read(tmp_path / "package-floor.json")
        assert report.result.status == "incomplete"
        wire = json.loads((tmp_path / "package-floor.json").read_text())
        assert wire["evidence"]["proposals"][0]["environment_plan_digest"] is None
        failure = wire["evidence"]["failures"][0]
        assert failure["stage"] == "test"
        assert failure["authority"]["terminal"]["kind"] == "start-failed"

    @pytest.mark.parametrize("group", ["", "[dependency-groups]\ntest = []\n"])
    def test_active_platform_system_dependency_completes_smoke_check_search(
        self, tmp_path, group
    ):
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text("VALUE = 1\n")
        (tmp_path / "pyproject.toml").write_text(f"""
[project]
name = "demo"
version = "1"
dependencies = ["idna>=3.10,<=3.10; platform_system == '{platform.system()}'"]
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
{group}[tool.pf]
pythons = ["3.10"]
search-space = "minors[declaration]"
test-command = ["python", "-c", "import demo, idna; assert demo.VALUE == 1; assert idna.__version__ == '3.10'"]
""")
        package = ProjectLoader().load(root=tmp_path).target
        assert package.declarations[0].managed
        assert package.cells[0].active_declaration_ids == (
            package.declarations[0].declaration_id,
        )
        for command in ("smoke", "check", "search", "minimize"):
            before = set(tmp_path.glob(".pf/logs/*/process-*.log"))
            result = subprocess.run(
                [sys.executable, "-m", "pf", command],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert result.returncode == 0, (command, result.stdout, result.stderr)
            logs = set(tmp_path.glob(".pf/logs/*/process-*.log")) - before
            contents = [log.read_text() for log in logs]
            assert any("import demo, idna" in content for content in contents)
            # One project compile per prepare; no harness-augmented compile.
            compiles = [
                content for content in contents if '"pip", "compile"' in content
            ]
            installs = [content for content in contents if '"pip", "sync"' in content]
            assert len(compiles) == len(installs) > 0
            assert all("pf-environment" not in content for content in compiles)
        report = ReportStore().read(tmp_path / "package-floor.json")
        assert report.result.status == "complete"
        wire = json.loads((tmp_path / "package-floor.json").read_text())
        assert wire["evidence"]["proposals"]
        assert all(
            item["environment_plan_digest"] is None
            for item in wire["evidence"]["proposals"]
        )
        assert report.projection_evidence[0].floors[0].version == "3.10"
        assert (
            "platform_system" in report.projection_evidence[0].projected_requirements[0]
        )

    def test_installed_module_cli_completes_report_lifecycle(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text(
            "VALUE: str = 1\n",
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [build-system]
    requires = ["uv_build>=0.8.22,<0.9.0"]
    build-backend = "uv_build"

    [dependency-groups]
    test = []

    [tool.pf]
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        lockfile = tmp_path / "uv.lock"
        lockfile.write_text("search snapshot\n", encoding="utf-8")

        results = {}
        for command in ("search", "explain", "apply"):
            result = subprocess.run(
                [sys.executable, "-m", "pf", command],
                cwd=tmp_path,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, (
                command,
                result.stdout,
                result.stderr,
            )
            results[command] = result

        lockfile.write_text("bootstrap snapshot\n", encoding="utf-8")
        repeated_apply = subprocess.run(
            [sys.executable, "-m", "pf", "apply"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert (tmp_path / "package-floor.json").is_file()
        process_logs = tuple((tmp_path / ".pf/logs").glob("*/process-*.log"))
        assert process_logs
        assert any(
            'ty", "check' in path.read_text(encoding="utf-8") for path in process_logs
        )
        explained = results["explain"].stdout
        assert "complete · report evidence is eligible for apply" in explained
        assert "pf apply --package demo" in explained
        assert repeated_apply.returncode == 0, (
            repeated_apply.stdout,
            repeated_apply.stderr,
        )
        assert "Applied floors · no metadata changes" in repeated_apply.stdout
