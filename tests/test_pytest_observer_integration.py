from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import ConfiguredVerifier
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    EnvironmentVariable,
    PytestFailureDetail,
    StageProgress,
    VerifierPass,
    VerifierRejected,
    VerifierRequest,
)


def _run_pytest(
    root: Path,
    *args: str,
    autoload: bool = False,
    progress: Callable[[StageProgress | None], None] | None = None,
):
    return ConfiguredVerifier(SubprocessRunner()).run(
        VerifierRequest(
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
            timeout_seconds=30,
        ),
        progress=progress,
    )


def _write_test(root: Path, source: str) -> None:
    (root / "test_example.py").write_text(source, encoding="utf-8")


class TestPytestObserverIntegration:
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert isinstance(result.diagnostics.detail, PytestFailureDetail)
        assert result.diagnostics.detail.first.nodeid == expected_nodeid
        assert result.diagnostics.detail.first.phase == expected_phase
        assert result.diagnostics.detail.total == 1

    def test_pytest_failure_detail_counts_call_and_teardown_as_one_nodeid(
        self,
        tmp_path: Path,
    ) -> None:
        _write_test(
            tmp_path,
            "import pytest\n"
            "@pytest.fixture\n"
            "def broken():\n    yield\n    raise RuntimeError('teardown')\n"
            "def test_first(broken):\n    assert False\n",
        )

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert isinstance(result.diagnostics.detail, PytestFailureDetail)
        assert result.diagnostics.detail.first.nodeid == "test_example.py::test_first"
        assert result.diagnostics.detail.first.phase == "call"
        assert result.diagnostics.detail.total == 1

    @pytest.mark.parametrize(
        "nodeid_escape",
        ("\\u009b31m", "\\ud800bad"),
        ids=("c1-control", "surrogate"),
    )
    def test_pytest_failure_detail_omits_an_unsafe_display_nodeid(
        self,
        tmp_path: Path,
        nodeid_escape: str,
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_collection_modifyitems(items):\n"
            f"    items[0]._nodeid = 'test_example.py::test_bad{nodeid_escape}'\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail is None

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

        assert isinstance(result.authoritative, VerifierPass)
        assert None not in observed
        assert observed[0] == initial
        assert observed[-1] == StageProgress(completed=1, total=1, unit="tests")

    def test_configured_verifier_progress_reaches_completion_across_nested_pytest(
        self,
        tmp_path: Path,
    ) -> None:
        _write_test(
            tmp_path,
            "import sys\n"
            "import time\n"
            "from pf.adapters.process import SubprocessRunner\n"
            "from pf.adapters.test_command import ConfiguredVerifier\n"
            "from pf.schemas.evaluation import (EnvironmentVariable, VerifierPass, VerifierRequest)\n"
            "def test_outer_first():\n"
            "    time.sleep(0.2)\n"
            "def test_outer_runs_inner_pytest(tmp_path):\n"
            "    inner = tmp_path / 'inner'\n"
            "    inner.mkdir()\n"
            "    (inner / 'test_inner.py').write_text(\n"
            "        'import time\\ndef test_inner():\\n    time.sleep(0.2)\\n',\n"
            "        encoding='utf-8',\n"
            "    )\n"
            "    result = ConfiguredVerifier(SubprocessRunner()).run(\n"
            "        VerifierRequest(\n"
            "            command=(sys.executable, '-m', 'pytest', '-q'),\n"
            "            cwd=inner,\n"
            "            environment=(EnvironmentVariable(\n"
            "                name='PYTEST_DISABLE_PLUGIN_AUTOLOAD', value='1'\n"
            "            ),),\n"
            "            timeout_seconds=10,\n"
            "        ),\n"
            "    )\n"
            "    assert isinstance(result.authoritative, VerifierPass)\n",
        )
        observed: list[StageProgress | None] = []

        result = _run_pytest(tmp_path, progress=observed.append)

        assert isinstance(result.authoritative, VerifierPass)
        assert None not in observed
        assert observed[-1] == StageProgress(completed=2, total=2, unit="tests")

    def test_configured_verifier_progress_reaches_completion_across_qualification_pytest(
        self,
    ) -> None:
        qualification = (
            "tests/test_pytest_observer_qualification.py::"
            "TestPytestObserverQualificationRunner::"
        )
        observed: list[StageProgress | None] = []

        result = _run_pytest(
            Path(__file__).resolve().parents[1],
            qualification
            + "test_transparency_runner_lists_the_committed_case_contracts",
            qualification
            + "test_transparency_runner_replays_the_committed_current_profile",
            autoload=True,
            progress=observed.append,
        )

        assert isinstance(result.authoritative, VerifierPass)
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

        assert isinstance(result.authoritative, VerifierPass)
        assert observed == []

    def test_pytest_observer_accepts_complete_pass(self, tmp_path: Path) -> None:
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierPass)

    def test_importing_packaged_observer_does_not_compete_with_injected_plugin(
        self,
        tmp_path: Path,
    ) -> None:
        _write_test(
            tmp_path,
            "from pf import _pytest_observer\n"
            "def test_ok():\n    assert _pytest_observer is not None\n",
        )

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierPass)

    def test_pytest_observer_does_not_override_interrupt_exit(
        self, tmp_path: Path
    ) -> None:
        _write_test(tmp_path, "raise KeyboardInterrupt\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 2

    def test_pytest_observer_does_not_override_usage_error_exit(
        self, tmp_path: Path
    ) -> None:
        result = _run_pytest(tmp_path, "--definitely-invalid-option")

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 4

    def test_pytest_observer_does_not_override_plugin_bootstrap_exit(
        self, tmp_path: Path
    ) -> None:
        result = _run_pytest(tmp_path, "-p", "plugin_that_does_not_exist")

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 1

    def test_pytest_observer_does_not_override_initial_conftest_exit(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "raise ImportError('initial conftest')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 4

    def test_pytest_observer_records_late_conftest_failure(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.pytest_facts == (("COLLECTION_FAILED", "collect"),)

    def test_pytest_observer_cannot_override_pass_rewritten_to_nonzero(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_sessionfinish(session):\n    session.exitstatus = 1\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 1
        assert result.diagnostics is not None
        assert result.diagnostics.summary_code == "pytest-terminal-metadata-conflict"

    def test_pytest_observer_cannot_override_failure_rewritten_to_pass(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_sessionfinish(session):\n    session.exitstatus = 0\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierPass)
        assert result.diagnostics is not None
        assert result.diagnostics.summary_code == "pytest-terminal-metadata-conflict"

    def test_pytest_observer_retains_diagnostics_before_interrupt(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 2

    def test_pytest_observer_retains_collection_diagnostics_before_interrupt(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 2

    def test_pytest_observer_internal_error_cannot_override_exit(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 3

    @pytest.mark.parametrize("rewritten_exit", (1, 2))
    def test_pytest_observer_internal_error_after_exit_rewrite_is_diagnostic_only(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == rewritten_exit

    def test_pytest_observer_rejects_uncommitted_summary(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import shutil\n"
            "def pytest_sessionstart(session):\n"
            "    shutil.rmtree(Path(os.environ['PF_PYTEST_OBSERVER_DIR']))\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        with pytest.raises(InfrastructureError, match="observer protocol"):
            _run_pytest(tmp_path)

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
            "Path(os.environ['PF_PYTEST_OBSERVER_DETAILS_DIR']))\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail is None

    def test_pytest_observer_rejects_residual_temporary_artifact(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "def pytest_sessionfinish(session):\n"
            "    directory = Path(os.environ['PF_PYTEST_OBSERVER_DIR'])\n"
            "    (directory / 'leftover.tmp').write_text('partial')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        with pytest.raises(InfrastructureError, match="observer protocol"):
            _run_pytest(tmp_path)

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
    def test_pytest_observer_records_finalization_exception_as_diagnostics(
        self,
        tmp_path: Path,
        conftest_source: str,
    ) -> None:
        (tmp_path / "conftest.py").write_text(conftest_source, encoding="utf-8")
        _write_test(tmp_path, "def test_ok():\n    pass\n")

        result = _run_pytest(tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)


class TestPytestObserverXdistIntegration:
    def test_pytest_observer_accepts_xdist_pass_from_config(
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

        assert isinstance(result.authoritative, VerifierPass)
        assert observed == []

    def test_pytest_observer_does_not_override_xdist_test_failure(
        self, tmp_path: Path
    ) -> None:
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path, "-n1", autoload=True)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert ("TEST_FAILED", "call") in result.diagnostics.pytest_facts

    def test_pytest_observer_does_not_override_xdist_internal_error(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "def pytest_runtest_logreport(report):\n"
            "    if report.failed and report.when == 'call':\n"
            "        raise RuntimeError('worker internal error')\n",
            encoding="utf-8",
        )
        _write_test(tmp_path, "def test_bad():\n    assert False\n")

        result = _run_pytest(tmp_path, "-n1", autoload=True)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 3

    def test_pytest_observer_does_not_override_xdist_worker_crash(
        self, tmp_path: Path
    ) -> None:
        _write_test(tmp_path, "import os\ndef test_crash():\n    os._exit(7)\n")

        result = _run_pytest(
            tmp_path,
            "-n1",
            "--max-worker-restart=0",
            autoload=True,
        )

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.authoritative.terminal.exit_code == 1
