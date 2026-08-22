from __future__ import annotations

from pathlib import Path
import subprocess
import sys


class TestInstalledCli:
    def test_installed_module_cli_completes_smoke_check_search_explain_apply(
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
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        results = {}
        for command in ("smoke", "check", "search", "explain", "diagnose", "apply"):
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

        assert "[py3.10]" in results["smoke"].stderr
        assert ".pf/logs/" in results["smoke"].stderr
        assert "details." in results["smoke"].stderr
        assert "diagnosed 0 failures" in results["diagnose"].stdout
        assert (tmp_path / "package-floor.json").is_file()
        process_logs = tuple((tmp_path / ".pf/logs").glob("*/process-*.log"))
        assert process_logs
        assert any(
            'ty", "check' in path.read_text(encoding="utf-8") for path in process_logs
        )
        explained = subprocess.run(
            [sys.executable, "-m", "pf", "explain"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "Status: complete" in explained
        assert "Apply: authorized by this report" in explained

    def test_smoke_returns_compatibility_failure_when_full_tests_fail(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text(
            "VALUE = 1\n",
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
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "raise SystemExit('smoke test failed')"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "pf", "smoke"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 1, (result.stdout, result.stderr)
        assert (
            "The full test command failed for this version combination."
            in result.stderr
        )
        assert "details." in result.stderr
        assert ".pf/logs/" in result.stderr
        assert "pf diagnose demo --failure" in result.stderr
        assert "failed at testing" in result.stderr
