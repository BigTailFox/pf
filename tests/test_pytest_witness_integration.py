from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.schemas.evaluation import (
    EnvironmentVariable,
    PytestFailureDetail,
    StageProgress,
    TestFail,
    TestPass,
    ToolFailure,
)


def _run_pytest(
    root: Path,
    *args: str,
    autoload: bool = False,
    progress: Callable[[StageProgress | None], None] | None = None,
):
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
        progress=progress,
    )


def _write_test(root: Path, source: str) -> None:
    (root / "test_example.py").write_text(source, encoding="utf-8")


class TestPytestWitnessIntegration:
    @pytest.mark.parametrize(
        ("source", "expected_nodeid", "expected_phase"),
        (
            (
                "raise ImportError('collection')\n",
                "test_example.py",
                "collect",
            ),
            (
                "import pytest\n"
                "@pytest.fixture\n"
                "def broken():\n    raise RuntimeError('setup')\n"
                "def test_bad(broken):\n    pass\n",
                "test_example.py::test_bad",
                "setup",
            ),
            (
                "def test_bad():\n    assert False\n",
                "test_example.py::test_bad",
                "call",
            ),
            (
                "import pytest\n"
                "@pytest.fixture\n"
                "def broken():\n    yield\n    raise RuntimeError('teardown')\n"
                "def test_bad(broken):\n    pass\n",
                "test_example.py::test_bad",
                "teardown",
            ),
        ),
        ids=("collection", "setup", "call", "teardown"),
    )
    def test_pytest_failure_detail_identifies_the_first_failed_case(
        self,
        tmp_path: Path,
        source: str,
        expected_nodeid: str,
        expected_phase: str,
    ) -> None:
        _write_test(tmp_path, source)

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestFail)
        assert isinstance(result.detail, PytestFailureDetail)
        assert result.detail.first.nodeid == expected_nodeid
        assert result.detail.first.phase == expected_phase
        assert result.detail.total == 1

    def test_pytest_failure_detail_counts_distinct_nodeids_once(
        self,
        tmp_path: Path,
    ) -> None:
        _write_test(
            tmp_path,
            "import pytest\n"
            "@pytest.fixture\n"
            "def broken():\n    yield\n    raise RuntimeError('teardown')\n"
            "def test_first(broken):\n    assert False\n"
            "def test_second():\n    assert False\n",
        )

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestFail)
        assert isinstance(result.detail, PytestFailureDetail)
        assert result.detail.first.nodeid == "test_example.py::test_first"
        assert result.detail.first.phase == "call"
        assert result.detail.total == 2

    @pytest.mark.parametrize(
        ("nodeid_escape", "expected"),
        (("\\u009b31m", TestFail), ("\\ud800bad", ToolFailure)),
        ids=("c1-control", "surrogate"),
    )
    def test_pytest_failure_detail_omits_an_unsafe_display_nodeid(
        self,
        tmp_path: Path,
        nodeid_escape: str,
        expected: type[TestFail] | type[ToolFailure],
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_collection_modifyitems(items):\n"
            f"    items[0]._nodeid = 'test_example.py::test_bad{nodeid_escape}'\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, expected)
        assert getattr(result, "detail", None) is None

    def test_serial_pytest_reports_determinate_collection_and_completion(
        self,
        tmp_path: Path,
    ) -> None:
        ready = tmp_path / "progress-visible"
        _write_test(
            tmp_path,
            "import time\n"
            "from pathlib import Path\n"
            "def test_one():\n"
            "    deadline = time.monotonic() + 5\n"
            "    while not Path('progress-visible').exists():\n"
            "        if time.monotonic() >= deadline:\n"
            "            raise AssertionError('initial progress was not observed')\n"
            "        time.sleep(0.005)\n",
        )
        initial = StageProgress(completed=0, total=1, unit="tests")
        observed: list[StageProgress | None] = []

        def observe(progress: StageProgress | None) -> None:
            observed.append(progress)
            if progress == initial:
                ready.touch()

        result = _run_pytest(tmp_path, progress=observe)

        assert isinstance(result, TestPass)
        assert None not in observed
        assert observed[0] == initial
        assert observed[-1] == StageProgress(completed=1, total=1, unit="tests")

    def test_test_adapter_progress_reaches_completion_across_nested_pytest(
        self,
        tmp_path: Path,
    ) -> None:
        _write_test(
            tmp_path,
            "import sys\n"
            "import time\n"
            "from pf.adapters.process import SubprocessRunner\n"
            "from pf.adapters.test_command import TestAdapter\n"
            "from pf.schemas.evaluation import EnvironmentVariable, TestPass\n"
            "def test_outer_first():\n"
            "    time.sleep(0.2)\n"
            "def test_outer_runs_inner_pytest(tmp_path):\n"
            "    inner = tmp_path / 'inner'\n"
            "    inner.mkdir()\n"
            "    (inner / 'test_inner.py').write_text(\n"
            "        'import time\\ndef test_inner():\\n    time.sleep(0.2)\\n',\n"
            "        encoding='utf-8',\n"
            "    )\n"
            "    result = TestAdapter(SubprocessRunner()).run(\n"
            "        command=(sys.executable, '-m', 'pytest', '-q'),\n"
            "        cwd=inner,\n"
            "        environment=(EnvironmentVariable(\n"
            "            name='PYTEST_DISABLE_PLUGIN_AUTOLOAD', value='1'\n"
            "        ),),\n"
            "        failure_exit_codes=(1,),\n"
            "        timeout_seconds=10,\n"
            "    )\n"
            "    assert isinstance(result, TestPass)\n",
        )
        observed: list[StageProgress | None] = []

        result = _run_pytest(tmp_path, progress=observed.append)

        assert isinstance(result, TestPass)
        assert None not in observed
        assert observed[-1] == StageProgress(completed=2, total=2, unit="tests")

    def test_test_adapter_progress_reaches_completion_across_qualification_pytest(
        self,
    ) -> None:
        qualification = (
            "tests/test_pytest_witness_qualification.py::"
            "TestPytestWitnessQualificationRunner::"
        )
        observed: list[StageProgress | None] = []

        result = _run_pytest(
            Path(__file__).resolve().parents[1],
            qualification
            + "test_qualification_runner_lists_the_committed_case_contracts",
            qualification + "test_qualification_runner_executes_current_profile_case",
            autoload=True,
            progress=observed.append,
        )

        assert isinstance(result, TestPass)
        assert None not in observed
        assert observed[-1] == StageProgress(completed=2, total=2, unit="tests")

    def test_collect_only_keeps_indeterminate_progress(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "def test_ok():\n    pass\n")
        observed: list[StageProgress | None] = []

        result = _run_pytest(
            tmp_path,
            "--collect-only",
            progress=observed.append,
        )

        assert isinstance(result, TestPass)
        assert observed == []

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

    def test_pytest_failure_detail_commit_failure_only_omits_detail(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import shutil\n"
            "def pytest_sessionfinish(session):\n"
            "    shutil.rmtree("
            "Path(os.environ['PF_PYTEST_FAILURE_DETAILS_DIR']))\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result, TestFail)
        assert result.detail is None

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
    def test_pytest_witness_accepts_xdist_pass_from_config(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts = ['-n1']\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")
        observed: list[StageProgress | None] = []

        result = _run_pytest(
            tmp_path,
            autoload=True,
            progress=observed.append,
        )

        assert isinstance(result, TestPass)
        assert observed == []

    def test_pytest_witness_rejects_xdist_test_failure(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path, "-n1", autoload=True)

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

        result = _run_pytest(tmp_path, "-n1", autoload=True)

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 3
        assert result.summary_code == "pytest-internal-error"

    def test_pytest_witness_rejects_xdist_worker_crash(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "import os\ndef test_crash():\n    os._exit(7)\n")

        result = _run_pytest(
            tmp_path,
            "-n1",
            "--max-worker-restart=0",
            autoload=True,
        )

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 1
        assert result.summary_code == "pytest-outcome-conflict"
