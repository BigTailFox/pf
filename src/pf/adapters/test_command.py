from __future__ import annotations

from collections.abc import Callable
from importlib import resources
import os
from pathlib import Path
import re
import secrets
import tempfile
from typing import Literal

from pf.adapters.process import ProcessRunner
from pf.adapters.pytest_progress import (
    PROGRESS_DIRECTORY_VARIABLE,
    PytestProgressMonitor,
)
from pf.adapters.pytest_witness import (
    EVIDENCE_DIRECTORY_VARIABLE,
    FAILURE_DETAILS_DIRECTORY_VARIABLE,
    RUN_NONCE_VARIABLE,
    classify_pytest_result,
    read_pytest_failure_detail,
)
from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessResult,
    ProcessSpec,
    StageProgress,
    TestFail,
    TestOutcome,
    TestPass,
    ToolFailure,
)

TestOutcomePolicyIdentity = Literal[
    "configured-exit-code-v1",
    "pytest-failure-witness-v1",
]


def _executable_basename(command: str) -> str:
    basename = command.replace("\\", "/").rsplit("/", 1)[-1]
    return basename[:-4].casefold() if basename.casefold().endswith(".exe") else basename


def _select_test_profile(
    command: tuple[str, ...],
    failure_exit_codes: tuple[int, ...],
) -> TestOutcomePolicyIdentity:
    if failure_exit_codes != (1,) or not command:
        return "configured-exit-code-v1"
    executable = _executable_basename(command[0])
    if executable in {"pytest", "py.test"}:
        return "pytest-failure-witness-v1"
    if (
        re.fullmatch(r"python(?:3|\d+\.\d+)?", executable) is not None
        and command[1:3] == ("-m", "pytest")
    ):
        return "pytest-failure-witness-v1"
    return "configured-exit-code-v1"


def selected_test_outcome_policy_identity(
    command: tuple[str, ...],
    failure_exit_codes: tuple[int, ...],
) -> TestOutcomePolicyIdentity:
    """Return the stable identity selected for one configured test command."""
    return _select_test_profile(command, failure_exit_codes)


class TestAdapter:
    """Run the complete configured test command without interpreting test output."""

    __test__ = False

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        failure_exit_codes: tuple[int, ...],
        timeout_seconds: int | None,
        progress: Callable[[StageProgress | None], None] | None = None,
    ) -> TestOutcome:
        profile = _select_test_profile(command, failure_exit_codes)
        if profile == "pytest-failure-witness-v1":
            return self._run_pytest_profile(
                command=command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                progress=progress,
            )
        result = self._run_process(
            command=command,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        failure = self._incomplete_process(result)
        if failure is not None:
            return failure
        if result.exit_code == 0:
            return TestPass(process=result)
        if result.exit_code in failure_exit_codes:
            return TestFail(process=result)
        return ToolFailure(cause="TOOL_FAILURE", stage="test", process=result)

    def _run_pytest_profile(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        timeout_seconds: int | None,
        progress: Callable[[StageProgress | None], None] | None,
    ) -> TestOutcome:
        temporary = None
        progress_temporary = None
        failure_details_temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="pf-pytest-witness-")
            root = Path(temporary.name)
            plugin_directory = root / "plugin"
            evidence_directory = root / "evidence"
            plugin_directory.mkdir()
            evidence_directory.mkdir()
            nonce = secrets.token_hex(16)
            monitor = None
            active_progress_directory = None
            active_failure_details_directory = None
            try:
                failure_details_temporary = tempfile.TemporaryDirectory(
                    prefix="pf-pytest-failure-details-"
                )
                active_failure_details_directory = Path(
                    failure_details_temporary.name
                )
            except Exception:
                _cleanup(failure_details_temporary)
                failure_details_temporary = None
            if progress is not None:
                try:
                    progress_temporary = tempfile.TemporaryDirectory(
                        prefix="pf-pytest-progress-"
                    )
                    progress_directory = Path(progress_temporary.name)
                    monitor = PytestProgressMonitor(
                        progress_directory,
                        nonce=nonce,
                        consume=progress,
                    )
                    active_progress_directory = progress_directory
                except Exception:
                    monitor = None
                    _cleanup(progress_temporary)
                    progress_temporary = None
            module = f"_pf_pytest_witness_{nonce}"
            source = resources.files("pf").joinpath(
                "_pytest_failure_witness.py"
            ).read_bytes()
            (plugin_directory / f"{module}.py").write_bytes(source)
            injected_command = self._inject_plugin(command, module)
            injected_environment = self._inject_environment(
                environment,
                plugin_directory=plugin_directory,
                evidence_directory=evidence_directory,
                progress_directory=active_progress_directory,
                failure_details_directory=active_failure_details_directory,
                nonce=nonce,
            )
        except Exception:
            _cleanup(failure_details_temporary)
            _cleanup(progress_temporary)
            _cleanup(temporary)
            result = self._run_process(
                command=command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
            )
            failure = self._incomplete_process(result)
            if failure is not None:
                return failure
            if result.exit_code == 0:
                return TestPass(process=result)
            return ToolFailure(
                cause="TOOL_FAILURE",
                stage="test",
                process=result,
                summary_code="pytest-failure-unwitnessed",
            )
        try:
            if monitor is not None:
                try:
                    monitor.start()
                except Exception:
                    monitor = None
            try:
                result = self._run_process(
                    command=injected_command,
                    cwd=cwd,
                    environment=injected_environment,
                    timeout_seconds=timeout_seconds,
                )
            finally:
                if monitor is not None:
                    try:
                        monitor.stop()
                    except Exception:
                        pass
            failure = self._incomplete_process(result)
            if failure is not None:
                outcome: TestOutcome = failure
            else:
                outcome = classify_pytest_result(
                    result,
                    evidence_directory=evidence_directory,
                    nonce=nonce,
                )
                if (
                    isinstance(outcome, TestFail)
                    and active_failure_details_directory is not None
                ):
                    detail = read_pytest_failure_detail(
                        active_failure_details_directory,
                        nonce=nonce,
                    )
                    if detail is not None:
                        outcome = outcome.model_copy(update={"detail": detail})
        except BaseException:
            _cleanup(failure_details_temporary)
            _cleanup(progress_temporary)
            _cleanup(temporary)
            raise
        if not _cleanup(failure_details_temporary) and isinstance(outcome, TestFail):
            outcome = outcome.model_copy(update={"detail": None})
        _cleanup(progress_temporary)
        if not _cleanup(temporary):
            if isinstance(outcome, ToolFailure):
                return outcome
            return ToolFailure(
                cause="TOOL_FAILURE",
                stage="test",
                process=result,
                summary_code="pytest-cleanup-failed",
            )
        return outcome

    def _run_process(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        timeout_seconds: int | None,
    ) -> ProcessResult:
        return self._runner.run(
            ProcessSpec(
                argv=command,
                cwd=cwd.as_posix(),
                environment=environment,
                timeout_seconds=timeout_seconds,
            )
        )

    @staticmethod
    def _incomplete_process(result: ProcessResult) -> ToolFailure | None:
        if result.timed_out:
            return ToolFailure(cause="TIMEOUT", stage="test", process=result)
        if (
            result.signal is not None
            or result.start_error is not None
            or not result.stdout_complete
            or not result.stderr_complete
        ):
            return ToolFailure(cause="TOOL_FAILURE", stage="test", process=result)
        return None

    @staticmethod
    def _inject_plugin(command: tuple[str, ...], module: str) -> tuple[str, ...]:
        executable = _executable_basename(command[0])
        prefix_length = 1 if executable in {"pytest", "py.test"} else 3
        return (*command[:prefix_length], "-p", module, *command[prefix_length:])

    @staticmethod
    def _inject_environment(
        environment: tuple[EnvironmentVariable, ...],
        *,
        plugin_directory: Path,
        evidence_directory: Path,
        progress_directory: Path | None,
        failure_details_directory: Path | None,
        nonce: str,
    ) -> tuple[EnvironmentVariable, ...]:
        values = {item.name: item.value for item in environment}
        original_pythonpath = values.get("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
        values["PYTHONPATH"] = (
            plugin_directory.as_posix()
            if not original_pythonpath
            else plugin_directory.as_posix() + os.pathsep + original_pythonpath
        )
        values[EVIDENCE_DIRECTORY_VARIABLE] = evidence_directory.as_posix()
        values[RUN_NONCE_VARIABLE] = nonce
        values.pop(PROGRESS_DIRECTORY_VARIABLE, None)
        values.pop(FAILURE_DETAILS_DIRECTORY_VARIABLE, None)
        if progress_directory is not None:
            values[PROGRESS_DIRECTORY_VARIABLE] = progress_directory.as_posix()
        if failure_details_directory is not None:
            values[FAILURE_DETAILS_DIRECTORY_VARIABLE] = (
                failure_details_directory.as_posix()
            )
        return tuple(
            EnvironmentVariable(name=name, value=value)
            for name, value in values.items()
        )


def _cleanup(temporary: tempfile.TemporaryDirectory[str] | None) -> bool:
    if temporary is None:
        return True
    try:
        temporary.cleanup()
    except Exception:
        return False
    return True
