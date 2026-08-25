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


def test_real_pytest_complete_pass_remains_a_pass(tmp_path: Path) -> None:
    _write_test(tmp_path, "def test_ok():\n    pass\n")

    result = _run_pytest(tmp_path)

    assert isinstance(result, TestPass)


@pytest.mark.parametrize(
    ("source", "args", "expected_exit"),
    (
        ("raise KeyboardInterrupt\n", (), 2),
        (None, ("--definitely-invalid-option",), 4),
        (None, ("-p", "plugin_that_does_not_exist"), 1),
    ),
)
def test_unwitnessed_interrupt_usage_and_plugin_bootstrap_are_indeterminate(
    tmp_path: Path,
    source: str | None,
    args: tuple[str, ...],
    expected_exit: int,
) -> None:
    if source is not None:
        _write_test(tmp_path, source)

    result = _run_pytest(tmp_path, *args)

    assert isinstance(result, ToolFailure)
    assert result.process.exit_code == expected_exit
    assert result.summary_code in {
        "pytest-failure-unwitnessed",
        "pytest-outcome-conflict",
    }


def test_initial_conftest_import_failure_is_indeterminate(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(
        "raise ImportError('initial conftest')\n",
        encoding="utf-8",
    )
    _write_test(tmp_path, "def test_ok():\n    pass\n")

    result = _run_pytest(tmp_path)

    assert isinstance(result, ToolFailure)
    assert result.process.exit_code == 4
    assert result.summary_code == "pytest-failure-unwitnessed"


def test_late_nested_conftest_import_failure_is_a_test_failure(
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


@pytest.mark.parametrize(
    ("test_source", "rewritten_exit"),
    (
        ("def test_ok():\n    pass\n", 1),
        ("def test_bad():\n    assert False\n", 0),
    ),
)
def test_exit_rewrite_cannot_create_a_false_pass_or_rejection(
    tmp_path: Path,
    test_source: str,
    rewritten_exit: int,
) -> None:
    (tmp_path / "conftest.py").write_text(
        "def pytest_sessionfinish(session):\n"
        f"    session.exitstatus = {rewritten_exit}\n",
        encoding="utf-8",
    )
    _write_test(tmp_path, test_source)

    result = _run_pytest(tmp_path)

    assert isinstance(result, ToolFailure)
    assert result.process.exit_code == rewritten_exit
    assert result.summary_code in {
        "pytest-failure-unwitnessed",
        "pytest-outcome-conflict",
    }


def test_failure_witness_survives_a_later_interrupt(tmp_path: Path) -> None:
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


def test_collection_failure_witness_survives_a_later_interrupt(
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


def test_internal_error_after_failure_witness_has_priority(tmp_path: Path) -> None:
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
def test_internal_error_priority_survives_exit_rewrite(
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


def test_summary_commit_failure_cannot_authorize_rejection(tmp_path: Path) -> None:
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


def test_residual_temporary_artifact_invalidates_evidence(tmp_path: Path) -> None:
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
        "def pytest_unconfigure(config):\n"
        "    raise RuntimeError('unconfigure')\n",
        "def _cleanup():\n"
        "    raise RuntimeError('cleanup')\n"
        "def pytest_configure(config):\n"
        "    config.add_cleanup(_cleanup)\n",
    ),
    ids=("sessionfinish", "unconfigure", "config-cleanup"),
)
def test_finalization_exception_is_committed_as_internal_error(
    tmp_path: Path,
    conftest_source: str,
) -> None:
    (tmp_path / "conftest.py").write_text(conftest_source, encoding="utf-8")
    _write_test(tmp_path, "def test_ok():\n    pass\n")

    result = _run_pytest(tmp_path)

    assert isinstance(result, ToolFailure)
    assert result.summary_code == "pytest-internal-error"


def test_xdist_argv_pass_retains_only_positive_authority(tmp_path: Path) -> None:
    _write_test(tmp_path, "def test_ok():\n    pass\n")

    result = _run_pytest(tmp_path, "-n2", autoload=True)

    assert isinstance(result, TestPass)


def test_xdist_configured_pass_retains_only_positive_authority(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = ['-n2']\n",
        encoding="utf-8",
    )
    _write_test(tmp_path, "def test_ok():\n    pass\n")

    result = _run_pytest(tmp_path, autoload=True)

    assert isinstance(result, TestPass)


@pytest.mark.parametrize(
    ("source", "conftest_source", "args"),
    (
        ("def test_bad():\n    assert False\n", None, ("-n2",)),
        (
            "def test_bad():\n    assert False\n",
            "def pytest_runtest_logreport(report):\n"
            "    if report.failed and report.when == 'call':\n"
            "        raise RuntimeError('worker internal error')\n",
            ("-n2",),
        ),
        (
            "import os\ndef test_crash():\n    os._exit(7)\n",
            None,
            ("-n2", "--max-worker-restart=0"),
        ),
    ),
    ids=("worker-failure", "worker-internal-error", "worker-crash"),
)
def test_xdist_nonzero_outcome_never_authorizes_rejection(
    tmp_path: Path,
    source: str,
    conftest_source: str | None,
    args: tuple[str, ...],
) -> None:
    if conftest_source is not None:
        (tmp_path / "conftest.py").write_text(
            conftest_source,
            encoding="utf-8",
        )
    _write_test(tmp_path, source)

    result = _run_pytest(tmp_path, *args, autoload=True)

    assert isinstance(result, ToolFailure)
    assert result.summary_code in {
        "pytest-evidence-invalid",
        "pytest-internal-error",
        "pytest-outcome-conflict",
    }
