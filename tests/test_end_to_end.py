from __future__ import annotations

from pathlib import Path
import platform
import json

import pytest

from visible_text import run_pf_cli, visible_cli_text

from pf.project import ProjectLoader
from pf.report import ReportStore

pytestmark = pytest.mark.process


def _verify_project_only_cli(tmp_path, *, group, command):
    assert not (tmp_path / ".git").exists()
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
    before = set(tmp_path.glob(".pf/logs/*/process-*.log"))
    result = run_pf_cli(command, cwd=tmp_path, timeout=120)
    assert result.returncode == 0, (command, result.stdout, result.stderr)
    logs = set(tmp_path.glob(".pf/logs/*/process-*.log")) - before
    contents = [log.read_text() for log in logs]
    assert any("import demo, idna" in content for content in contents)
    if command == "check":
        assert not (tmp_path / "package-floor.json").exists()
    if command in {"search", "minimize"}:
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
            "platform_system"
            in report.projection_evidence[0].projected_requirements[0]
        )


class TestInstalledCli:
    @pytest.mark.e2e
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
        result = run_pf_cli("search", cwd=tmp_path, timeout=120)
        assert result.returncode == 4, (result.stdout, result.stderr)
        report = ReportStore().read(tmp_path / "package-floor.json")
        assert report.result.status == "incomplete"
        wire = json.loads((tmp_path / "package-floor.json").read_text())
        assert wire["evidence"]["proposals"][0]["environment_plan_digest"] is None
        failure = wire["evidence"]["failures"][0]
        assert failure["stage"] == "test"
        assert failure["authority"]["terminal"]["kind"] == "start-failed"

    @pytest.mark.e2e
    def test_cli_verifies_project_only_environment(self, tmp_path):
        _verify_project_only_cli(tmp_path, group="", command="search")

    @pytest.mark.e2e
    def test_cli_checks_handwritten_project_only_declaration(self, tmp_path):
        _verify_project_only_cli(tmp_path, group="", command="check")

    @pytest.mark.e2e
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
            result = run_pf_cli(command, cwd=tmp_path, timeout=60)
            assert result.returncode == 0, (
                command,
                result.stdout,
                result.stderr,
            )
            results[command] = result

        lockfile.write_text("bootstrap snapshot\n", encoding="utf-8")
        repeated_apply = run_pf_cli("apply", cwd=tmp_path, timeout=60)

        assert (tmp_path / "package-floor.json").is_file()
        process_logs = tuple((tmp_path / ".pf/logs").glob("*/process-*.log"))
        assert process_logs
        assert any(
            'ty", "check' in path.read_text(encoding="utf-8") for path in process_logs
        )
        explained = visible_cli_text(results["explain"].stdout)
        assert "complete · report evidence is eligible for apply" in explained
        assert "pf apply --package demo" in explained
        assert repeated_apply.returncode == 0, (
            repeated_apply.stdout,
            repeated_apply.stderr,
        )
        assert "Applied floors · no metadata changes" in visible_cli_text(
            repeated_apply.stdout
        )


def _process_logs(root: Path) -> set[Path]:
    return set(root.glob(".pf/logs/*/process-*.log"))


def _log_text(paths: set[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _write_steady_project(tmp_path: Path, *, value: int = 1) -> None:
    (tmp_path / "src" / "demo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "demo" / "__init__.py").write_text(
        f"VALUE: str = {value}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        f"""
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
test-command = ["python", "-c", "import demo; assert demo.VALUE == {value}"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _assert_no_git(tmp_path: Path) -> None:
    assert not (tmp_path / ".git").exists()
    assert not (tmp_path / ".git").is_file()


class TestCheckFirstLifecycle:
    @pytest.mark.e2e
    def test_no_git_onboarding_and_repeated_check(
        self,
        tmp_path: Path,
    ) -> None:
        _write_steady_project(tmp_path)
        _assert_no_git(tmp_path)
        report = tmp_path / "package-floor.json"
        assert not report.exists()

        before_first = _process_logs(tmp_path)
        first_check = run_pf_cli("check", cwd=tmp_path, timeout=120)
        assert first_check.returncode == 0, (first_check.stdout, first_check.stderr)
        first_logs = _process_logs(tmp_path) - before_first
        assert any("assert demo.VALUE == 1" in text for text in (
            path.read_text(encoding="utf-8") for path in first_logs
        ))
        assert 'ty", "check' not in _log_text(first_logs)
        assert not report.exists()
        _assert_no_git(tmp_path)

        before_second = _process_logs(tmp_path)
        second_check = run_pf_cli("check", cwd=tmp_path, timeout=120)
        assert second_check.returncode == 0, (second_check.stdout, second_check.stderr)
        second_logs = _process_logs(tmp_path) - before_second
        assert second_logs
        assert any("assert demo.VALUE == 1" in text for text in (
            path.read_text(encoding="utf-8") for path in second_logs
        ))
        assert 'ty", "check' not in _log_text(second_logs)
        assert not report.exists()

        before_smoke = _process_logs(tmp_path)
        first_smoke = run_pf_cli("smoke", cwd=tmp_path, timeout=120)
        second_smoke = run_pf_cli("smoke", cwd=tmp_path, timeout=120)
        assert first_smoke.returncode == 0, (first_smoke.stdout, first_smoke.stderr)
        assert second_smoke.returncode == 0, (second_smoke.stdout, second_smoke.stderr)
        smoke_logs = _process_logs(tmp_path) - before_smoke
        assert _log_text(smoke_logs).count("assert demo.VALUE == 1") >= 2
        assert 'ty", "check' not in _log_text(smoke_logs)
        assert not report.exists()

        search = run_pf_cli("search", cwd=tmp_path, timeout=120)
        assert search.returncode == 0, (search.stdout, search.stderr)
        assert report.is_file()
        report_after_search = report.read_bytes()
        explain = run_pf_cli("explain", cwd=tmp_path, timeout=60)
        apply = run_pf_cli("apply", cwd=tmp_path, timeout=60)
        repeated_apply = run_pf_cli("apply", cwd=tmp_path, timeout=60)
        assert explain.returncode == 0, (explain.stdout, explain.stderr)
        assert apply.returncode == 0, (apply.stdout, apply.stderr)
        assert repeated_apply.returncode == 0, (
            repeated_apply.stdout,
            repeated_apply.stderr,
        )
        assert "Applied floors · no metadata changes" in visible_cli_text(
            repeated_apply.stdout
        )
        assert report.read_bytes() == report_after_search

        before_third = _process_logs(tmp_path)
        third_check = run_pf_cli("check", cwd=tmp_path, timeout=120)
        assert third_check.returncode == 0, (third_check.stdout, third_check.stderr)
        third_logs = _process_logs(tmp_path) - before_third
        assert any("assert demo.VALUE == 1" in text for text in (
            path.read_text(encoding="utf-8") for path in third_logs
        ))
        assert report.read_bytes() == report_after_search

        _write_steady_project(tmp_path, value=2)
        _assert_no_git(tmp_path)
        before_edited = _process_logs(tmp_path)
        edited_check = run_pf_cli("check", cwd=tmp_path, timeout=120)
        assert edited_check.returncode == 0, (edited_check.stdout, edited_check.stderr)
        edited_logs = _process_logs(tmp_path) - before_edited
        assert any("assert demo.VALUE == 2" in text for text in (
            path.read_text(encoding="utf-8") for path in edited_logs
        ))
        assert report.read_bytes() == report_after_search
        _assert_no_git(tmp_path)

    @pytest.mark.e2e
    def test_minimize_keeps_search_and_apply_flow(self, tmp_path: Path) -> None:
        _write_steady_project(tmp_path)
        _assert_no_git(tmp_path)
        result = run_pf_cli("minimize", cwd=tmp_path, timeout=180)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert (tmp_path / "package-floor.json").is_file()
        output = visible_cli_text(result.stdout)
        assert "Applied floors" in output or "no metadata changes" in output
        report = (tmp_path / "package-floor.json").read_bytes()
        repeated = run_pf_cli("apply", cwd=tmp_path, timeout=60)
        assert repeated.returncode == 0, (repeated.stdout, repeated.stderr)
        assert "Applied floors · no metadata changes" in visible_cli_text(
            repeated.stdout
        )
        assert (tmp_path / "package-floor.json").read_bytes() == report
