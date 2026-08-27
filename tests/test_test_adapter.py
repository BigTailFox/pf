from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

import pytest

from pf.adapters.test_command import (
    TestAdapter,
    selected_test_outcome_policy_identity as outcome_policy_identity,
)
from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessResult,
    ProcessSpec,
    TestFail,
    TestPass,
    ToolFailure,
)


class FailingTestRunner:
    def __init__(self) -> None:
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.spec = spec
        return ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="1 failed\n",
            stderr="",
        )


class TestTestAdapter:
    @pytest.mark.parametrize(
        ("command", "failure_exit_codes", "expected"),
        (
            (("pytest",), (1,), "pytest-failure-witness-v1"),
            (("py.test", "tests"), (1,), "pytest-failure-witness-v1"),
            (("/venv/bin/pytest",), (1,), "pytest-failure-witness-v1"),
            ((r"C:\venv\Scripts\PyTest.EXE",), (1,), "pytest-failure-witness-v1"),
            (("python", "-m", "pytest"), (1,), "pytest-failure-witness-v1"),
            (("python3", "-m", "pytest", "-q"), (1,), "pytest-failure-witness-v1"),
            (("python3.12", "-m", "pytest"), (1,), "pytest-failure-witness-v1"),
            (
                ("/venv/bin/python3.10", "-m", "pytest"),
                (1,),
                "pytest-failure-witness-v1",
            ),
            (
                (r"C:\Python312\python.exe", "-m", "pytest"),
                (1,),
                "pytest-failure-witness-v1",
            ),
            (("pytest",), (1, 2), "configured-exit-code-v1"),
            (("coverage", "run", "-m", "pytest"), (1,), "configured-exit-code-v1"),
            (("tox",), (1,), "configured-exit-code-v1"),
            (("nox",), (1,), "configured-exit-code-v1"),
            (("env", "pytest"), (1,), "configured-exit-code-v1"),
            (("python", "tests/run.py"), (1,), "configured-exit-code-v1"),
        ),
    )
    def test_test_outcome_policy_has_one_command_profile_selector(
        self,
        command: tuple[str, ...],
        failure_exit_codes: tuple[int, ...],
        expected: str,
    ) -> None:
        assert outcome_policy_identity(command, failure_exit_codes) == expected

    def test_test_adapter_uses_configured_argv_and_failure_codes(
        self, tmp_path: Path
    ) -> None:
        runner = FailingTestRunner()
        adapter = TestAdapter(runner)

        result = adapter.run(
            command=("custom-test-runner", "tests"),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=1800,
        )

        assert result.status == "TEST_FAIL"
        assert runner.spec is not None
        assert runner.spec.argv == ("custom-test-runner", "tests")
        assert runner.spec.cwd == tmp_path.as_posix()
        assert runner.spec.timeout_seconds == 1800

    def test_direct_pytest_collection_witness_is_a_test_failure(
        self, tmp_path: Path
    ) -> None:
        class WitnessRunner:
            def __init__(self) -> None:
                self.spec: ProcessSpec | None = None
                self.plugin_was_present = False

            def run(self, spec: ProcessSpec) -> ProcessResult:
                self.spec = spec
                environment = {item.name: item.value for item in spec.environment}
                evidence = Path(environment["PF_PYTEST_WITNESS_DIR"])
                nonce = environment["PF_PYTEST_WITNESS_NONCE"]
                plugin_dir = Path(environment["PYTHONPATH"].split(os.pathsep)[0])
                module = spec.argv[2]
                self.plugin_was_present = (plugin_dir / f"{module}.py").is_file()
                summary = {
                    "execution_mode": "serial",
                    "facts": [{"kind": "COLLECTION_FAILED", "phase": "collect"}],
                    "finalized": True,
                    "protocol": "pf-pytest-failure-witness-v1",
                    "pytest_version": "9.1.1",
                    "python_implementation": "cpython",
                    "python_minor": "3.10",
                    "run_nonce": nonce,
                }
                (evidence / f"summary-{'a' * 32}.json").write_text(
                    json.dumps(
                        summary,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return ProcessResult(
                    exit_code=2,
                    signal=None,
                    duration_seconds=0.1,
                )

        runner = WitnessRunner()
        result = TestAdapter(runner).run(
            command=("pytest", "tests"),
            cwd=tmp_path,
            environment=(
                EnvironmentVariable(name="PYTHONPATH", value="/original/pythonpath"),
                EnvironmentVariable(name="KEEP", value="present"),
            ),
            failure_exit_codes=(1,),
            timeout_seconds=1800,
        )

        assert isinstance(result, TestFail)
        assert result.process.exit_code == 2
        assert runner.plugin_was_present
        assert runner.spec is not None
        assert runner.spec.argv[:2] == ("pytest", "-p")
        assert runner.spec.argv[3:] == ("tests",)
        assert runner.spec.cwd == tmp_path.as_posix()
        assert runner.spec.timeout_seconds == 1800
        observed_environment = {
            item.name: item.value for item in runner.spec.environment
        }
        assert observed_environment["PYTHONPATH"].endswith(
            os.pathsep + "/original/pythonpath"
        )
        assert observed_environment["KEEP"] == "present"

    @pytest.mark.parametrize(
        ("exit_code", "expected"),
        ((0, TestPass), (1, ToolFailure)),
    )
    def test_pytest_profile_preparation_failure_runs_original_command_fail_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        exit_code: int,
        expected: type[TestPass] | type[ToolFailure],
    ) -> None:
        class Runner:
            def __init__(self) -> None:
                self.specs: list[ProcessSpec] = []

            def run(self, spec: ProcessSpec) -> ProcessResult:
                self.specs.append(spec)
                return ProcessResult(
                    exit_code=exit_code,
                    signal=None,
                    duration_seconds=0.1,
                )

        def missing_resource(package: str) -> object:
            raise FileNotFoundError(package)

        monkeypatch.setattr(
            "pf.adapters.test_command.resources.files", missing_resource
        )
        runner = Runner()

        result = TestAdapter(runner).run(
            command=("pytest", "tests"),
            cwd=tmp_path,
            environment=(EnvironmentVariable(name="KEEP", value="present"),),
            failure_exit_codes=(1,),
            timeout_seconds=30,
        )

        assert isinstance(result, expected)
        assert len(runner.specs) == 1
        assert runner.specs[0].argv == ("pytest", "tests")
        assert runner.specs[0].environment == (
            EnvironmentVariable(name="KEEP", value="present"),
        )
        if isinstance(result, ToolFailure):
            assert result.summary_code == "pytest-failure-unwitnessed"

    def test_pytest_profile_cleanup_failure_stays_inside_test_outcome(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original_temporary_directory = tempfile.TemporaryDirectory

        class CleanupFails:
            def __init__(self, *, prefix: str) -> None:
                self._temporary = original_temporary_directory(prefix=prefix)
                self.name = self._temporary.name

            def cleanup(self) -> None:
                self._temporary.cleanup()
                raise OSError("cleanup failed")

        class PassingRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(
                    exit_code=0,
                    signal=None,
                    duration_seconds=0.1,
                )

        monkeypatch.setattr(
            "pf.adapters.test_command.tempfile.TemporaryDirectory",
            CleanupFails,
        )

        result = TestAdapter(PassingRunner()).run(
            command=("pytest",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=30,
        )

        assert isinstance(result, ToolFailure)
        assert result.process.exit_code == 0
        assert result.summary_code == "pytest-cleanup-failed"

    @pytest.mark.parametrize(
        ("exit_code", "facts", "expected", "summary_code"),
        (
            (0, (), "TEST_PASS", None),
            (
                0,
                (("COLLECTION_FAILED", "collect"),),
                "TOOL_FAILURE",
                "pytest-outcome-conflict",
            ),
            (
                1,
                (("TEST_FAILED", "call"),),
                "TEST_FAIL",
                None,
            ),
            (
                2,
                (("COLLECTION_FAILED", "collect"),),
                "TEST_FAIL",
                None,
            ),
            (1, (), "TOOL_FAILURE", "pytest-failure-unwitnessed"),
            (2, (), "TOOL_FAILURE", "pytest-failure-unwitnessed"),
            (
                1,
                (("INTERNAL_ERROR", "pytest"), ("TEST_FAILED", "call")),
                "TOOL_FAILURE",
                "pytest-internal-error",
            ),
            (
                3,
                (("TEST_FAILED", "call"),),
                "TOOL_FAILURE",
                "pytest-outcome-conflict",
            ),
        ),
    )
    def test_pytest_profile_joins_exit_status_with_finalized_witnesses(
        self,
        tmp_path: Path,
        exit_code: int,
        facts: tuple[tuple[str, str], ...],
        expected: str,
        summary_code: str | None,
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                summary = {
                    "execution_mode": "serial",
                    "facts": [{"kind": kind, "phase": phase} for kind, phase in facts],
                    "finalized": True,
                    "protocol": "pf-pytest-failure-witness-v1",
                    "pytest_version": "9.1.1",
                    "python_implementation": "cpython",
                    "python_minor": "3.10",
                    "run_nonce": environment["PF_PYTEST_WITNESS_NONCE"],
                }
                evidence = Path(environment["PF_PYTEST_WITNESS_DIR"])
                (evidence / f"summary-{'b' * 32}.json").write_text(
                    json.dumps(
                        summary,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return ProcessResult(
                    exit_code=exit_code,
                    signal=None,
                    duration_seconds=0.1,
                )

        result = TestAdapter(Runner()).run(
            command=("pytest",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=30,
        )

        observed = result.cause if isinstance(result, ToolFailure) else result.status
        assert observed == expected
        if isinstance(result, ToolFailure):
            assert result.summary_code == summary_code

    @pytest.mark.parametrize(
        ("exit_code", "timed_out", "expected"),
        (
            (0, False, "TEST_PASS"),
            (2, False, "TOOL_FAILURE"),
            (None, True, "TIMEOUT"),
        ),
    )
    def test_test_adapter_preserves_every_terminal_classification(
        self,
        tmp_path: Path,
        exit_code: int | None,
        timed_out: bool,
        expected: str,
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(
                    exit_code=exit_code,
                    signal=None if exit_code is not None else 9,
                    duration_seconds=0.1,
                    stdout="",
                    stderr="",
                    timed_out=timed_out,
                )

        result = TestAdapter(Runner()).run(
            command=("custom-test-runner",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=10,
        )

        observed = result.cause if isinstance(result, ToolFailure) else result.status
        assert observed == expected

    @pytest.mark.parametrize(
        ("exit_code", "stdout_complete", "stderr_complete"),
        (
            (0, False, True),
            (0, True, False),
            (1, False, True),
            (1, True, False),
        ),
    )
    def test_generic_profile_fails_closed_on_incomplete_process_evidence(
        self,
        tmp_path: Path,
        exit_code: int,
        stdout_complete: bool,
        stderr_complete: bool,
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(
                    exit_code=exit_code,
                    signal=None,
                    duration_seconds=0.1,
                    stdout_complete=stdout_complete,
                    stderr_complete=stderr_complete,
                )

        result = TestAdapter(Runner()).run(
            command=("custom-test-runner",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=10,
        )

        assert isinstance(result, ToolFailure)
        assert result.cause == "TOOL_FAILURE"
        assert result.stage == "test"

    @pytest.mark.parametrize(
        ("exit_code", "expected"),
        (
            (0, TestPass),
            (1, TestFail),
        ),
    )
    def test_test_adapter_classifies_exit_code_without_treating_cache_as_failure(
        self,
        tmp_path: Path,
        exit_code: int,
        expected: type[TestPass] | type[TestFail],
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(
                    exit_code=exit_code,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="bounded",
                    stderr="",
                )

        result = TestAdapter(Runner()).run(
            command=("custom-test-runner",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=10,
        )

        assert isinstance(result, expected)
