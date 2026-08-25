from __future__ import annotations

from pathlib import Path
import sys

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.schemas.evaluation import EnvironmentVariable, TestFail, TestPass, ToolFailure


def _run_pytest(root: Path, *args: str, autoload: bool = False):
    return TestAdapter(SubprocessRunner()).run(
        command=(sys.executable, "-m", "pytest", "--no-header", "-q", *args),
        cwd=root,
        environment=(
            ()
            if autoload
            else (
                EnvironmentVariable(
                    name="PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                    value="1",
                ),
            )
        ),
        failure_exit_codes=(1,),
        timeout_seconds=30,
    )


def _write_test(root: Path, source: str) -> None:
    (root / "test_example.py").write_text(source, encoding="utf-8")


class TestPytestWitnessIntegration:
    def test_pytest_witness_accepts_complete_pass(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestPass)

    def test_pytest_witness_classifies_interrupt_as_indeterminate(
        self, tmp_path: Path
    ) -> None:
        _write_test(tmp_path, "raise KeyboardInterrupt\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 2
        assert result.summary_code == "pytest-failure-unwitnessed"

    def test_pytest_witness_classifies_usage_error_as_indeterminate(
        self, tmp_path: Path
    ) -> None:
        result = _run_pytest(tmp_path, "--definitely-invalid-option")

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 4
        assert result.summary_code == "pytest-failure-unwitnessed"

    def test_pytest_witness_classifies_plugin_bootstrap_failure_as_indeterminate(
        self, tmp_path: Path
    ) -> None:
        result = _run_pytest(tmp_path, "-p", "plugin_that_does_not_exist")

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code == "pytest-failure-unwitnessed"

    def test_pytest_witness_classifies_initial_conftest_failure_as_indeterminate(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "raise ImportError('initial conftest')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 4
        assert result.summary_code == "pytest-failure-unwitnessed"

    def test_pytest_witness_classifies_late_conftest_failure_as_test_failure(
        self,
        tmp_path: Path,
    ) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "conftest.py").write_text(
            "raise ImportError('nested conftest')\n",
            encoding="utf-8",
        )
        (nested / "test_nested.py").write_text(
            "def test_nested():\n    pass\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_root():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestFail)
        assert result.process.exit_code == 2

    def test_pytest_witness_rejects_pass_rewritten_to_failure(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_sessionfinish(session):\n    session.exitstatus = 1\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code == "pytest-failure-unwitnessed"

    def test_pytest_witness_rejects_failure_rewritten_to_pass(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_sessionfinish(session):\n    session.exitstatus = 0\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 0
        assert result.summary_code == "pytest-outcome-conflict"

    def test_pytest_witness_retains_test_failure_before_interrupt(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_runtest_logreport(report):\n"
            "    if report.failed and report.when == 'call':\n"
            "        raise KeyboardInterrupt\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestFail)
        assert result.process.exit_code == 2

    def test_pytest_witness_retains_collection_failure_before_interrupt(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_collectreport(report):\n"
            "    if report.failed:\n"
            "        raise KeyboardInterrupt\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "raise ImportError('collection')\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestFail)
        assert result.process.exit_code == 2

    def test_pytest_witness_prioritizes_internal_error_over_failure(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_runtest_logreport(report):\n"
            "    if report.failed and report.when == 'call':\n"
            "        raise RuntimeError('after failure')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 3
        assert result.summary_code == "pytest-internal-error"

    @pytest.mark.parametrize("rewritten_exit", (1, 2))
    def test_pytest_witness_prioritizes_internal_error_after_exit_rewrite(
        self,
        tmp_path: Path,
        rewritten_exit: int,
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_collection_modifyitems(items):\n"
            "    raise RuntimeError('internal')\n"
            "def pytest_sessionfinish(session):\n"
            f"    session.exitstatus = {rewritten_exit}\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == rewritten_exit
        assert result.summary_code == "pytest-internal-error"

    def test_pytest_witness_rejects_uncommitted_summary(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import shutil\n"
            "def pytest_sessionstart(session):\n"
            "    shutil.rmtree(Path(os.environ['PF_PYTEST_WITNESS_DIR']))\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code in {
            "pytest-evidence-invalid",
            "pytest-failure-unwitnessed",
        }

    def test_pytest_witness_rejects_residual_temporary_artifact(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "def pytest_sessionfinish(session):\n"
            "    directory = Path(os.environ['PF_PYTEST_WITNESS_DIR'])\n"
            "    (directory / 'leftover.tmp').write_text('partial')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code == "pytest-evidence-invalid"

    @pytest.mark.parametrize(
        "conftest_source",
        (
            "def pytest_sessionfinish(session):\n"
            "    raise RuntimeError('sessionfinish')\n",
            "def pytest_unconfigure(config):\n    raise RuntimeError('unconfigure')\n",
            "def _cleanup():\n"
            "    raise RuntimeError('cleanup')\n"
            "def pytest_configure(config):\n"
            "    config.add_cleanup(_cleanup)\n",
        ),
        ids=("sessionfinish", "unconfigure", "config-cleanup"),
    )
    def test_pytest_witness_records_finalization_exception_as_internal_error(
        self,
        tmp_path: Path,
        conftest_source: str,
    ) -> None:
        (tmp_path / "conftest.py").write_text(conftest_source, encoding="utf-8")
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, ToolFailure)
        assert result.summary_code == "pytest-internal-error"


class TestPytestWitnessXdistIntegration:
    def test_pytest_witness_accepts_xdist_pass_from_argv(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path, "-n2", autoload=True)

        assert isinstance(result, TestPass)

    def test_pytest_witness_accepts_xdist_pass_from_config(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts = ['-n2']\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path, autoload=True)

        assert isinstance(result, TestPass)

    def test_pytest_witness_rejects_xdist_test_failure(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path, "-n2", autoload=True)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code == "pytest-outcome-conflict"

    def test_pytest_witness_rejects_xdist_internal_error(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_runtest_logreport(report):\n"
            "    if report.failed and report.when == 'call':\n"
            "        raise RuntimeError('worker internal error')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path, "-n2", autoload=True)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 3
        assert result.summary_code == "pytest-internal-error"

    def test_pytest_witness_rejects_xdist_worker_crash(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "import os\ndef test_crash():\n    os._exit(7)\n")

        result = _run_pytest(
            tmp_path,
            "-n2",
            "--max-worker-restart=0",
            autoload=True,
        )

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code == "pytest-outcome-conflict"
