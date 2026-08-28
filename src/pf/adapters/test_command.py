from __future__ import annotations

from collections.abc import Callable
from importlib import resources
import os
from pathlib import Path
import re
import secrets
import tempfile

from pydantic import ValidationError

from pf.adapters.process import ProcessRunner
from pf.adapters.pytest_progress import (
    PROGRESS_DIRECTORY_VARIABLE,
    PROGRESS_NONCE_VARIABLE,
    PytestProgressMonitor,
)
from pf.adapters.pytest_observer import (
    DETAILS_DIRECTORY_VARIABLE,
    OBSERVATION_DIRECTORY_VARIABLE,
    RUN_NONCE_VARIABLE,
    InvalidPytestObservation,
    read_pytest_observer_detail,
    read_pytest_observer,
)
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    Signaled,
    StartFailed,
    StageProgress,
    NormalExit,
    TimedOut,
    Unavailable,
    VerifierDiagnostics,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRequest,
    VerifierRun,
)

_PYTEST_PRIVATE_ENVIRONMENT = (
    OBSERVATION_DIRECTORY_VARIABLE,
    DETAILS_DIRECTORY_VARIABLE,
    PROGRESS_DIRECTORY_VARIABLE,
    PROGRESS_NONCE_VARIABLE,
    RUN_NONCE_VARIABLE,
)


def _executable_basename(command: str) -> str:
    basename = command.replace("\\", "/").rsplit("/", 1)[-1]
    return basename[:-4].casefold() if basename.casefold().endswith(".exe") else basename


def _is_direct_pytest(command: tuple[str, ...]) -> bool:
    if not command:
        return False
    executable = _executable_basename(command[0])
    if executable in {"pytest", "py.test"}:
        return True
    if (
        re.fullmatch(r"python(?:3|\d+\.\d+)?", executable) is not None
        and command[1:3] == ("-m", "pytest")
    ):
        return True
    return False


class ConfiguredVerifier:
    """Run one configured verifier and decide only from process terminal facts."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def run(
        self,
        request: VerifierRequest,
        progress: Callable[[StageProgress | None], None] | None = None,
    ) -> VerifierRun:
        if _is_direct_pytest(request.command):
            return self._run_pytest_profile(
                command=request.command,
                cwd=request.cwd,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                progress=progress,
            )
        result = self._run_process(
            command=request.command,
            cwd=request.cwd,
            environment=request.environment,
            timeout_seconds=request.timeout_seconds,
        )
        return self._project_observation(result)

    def _run_pytest_profile(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        timeout_seconds: int | None,
        progress: Callable[[StageProgress | None], None] | None,
    ) -> VerifierRun:
        temporary = None
        progress_temporary = None
        failure_details_temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="pf-pytest-observer-")
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
                    prefix="pf-pytest-observer-details-"
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
            module = f"_pf_pytest_observer_{nonce}"
            source = resources.files("pf").joinpath("_pytest_observer.py").read_bytes()
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
        except Exception as error:
            _cleanup(failure_details_temporary)
            _cleanup(progress_temporary)
            _cleanup(temporary)
            raise InfrastructureError(
                "pytest observer preparation failed",
                detail=str(error),
            ) from error
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
                    environment_removals=_PYTEST_PRIVATE_ENVIRONMENT,
                    timeout_seconds=timeout_seconds,
                )
            finally:
                if monitor is not None:
                    try:
                        monitor.stop()
                    except Exception:
                        pass
            if isinstance(result, ProcessTerminalUnavailable) or (
                result.timed_out
                or result.signal is not None
                or result.start_error is not None
            ):
                outcome = self._project_observation(result)
            else:
                try:
                    observer = read_pytest_observer(evidence_directory, nonce=nonce)
                except InvalidPytestObservation as error:
                    raise InfrastructureError(
                        "pytest observer protocol failed",
                        detail=str(error),
                    ) from error
                detail = (
                    None
                    if active_failure_details_directory is None
                    else read_pytest_observer_detail(
                        active_failure_details_directory,
                        nonce=nonce,
                    )
                )
                facts = tuple(sorted(observer.facts))
                conflict = (
                    "pytest-terminal-metadata-conflict"
                    if (result.exit_code == 0) == observer.has_failure
                    else None
                )
                outcome = self._project_observation(
                    result,
                    diagnostics=VerifierDiagnostics(
                        process=result,
                        detail=detail,
                        summary_code=conflict,
                        pytest_execution_mode=observer.execution_mode,
                        pytest_facts=facts,
                        pytest_version=observer.pytest_version,
                        python_minor=observer.python_minor,
                    ),
                )
        except BaseException:
            _cleanup(failure_details_temporary)
            _cleanup(progress_temporary)
            _cleanup(temporary)
            raise
        _cleanup(failure_details_temporary)
        _cleanup(progress_temporary)
        if not _cleanup(temporary):
            raise InfrastructureError(
                "pytest observer cleanup failed",
            )
        return outcome

    def _run_process(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        environment_removals: tuple[str, ...] = (),
        timeout_seconds: int | None,
    ) -> ProcessResult | ProcessTerminalUnavailable:
        try:
            return self._runner.run(
                ProcessSpec(
                    argv=command,
                    cwd=cwd.as_posix(),
                    environment=environment,
                    environment_removals=environment_removals,
                    timeout_seconds=timeout_seconds,
                )
            )
        except InfrastructureError:
            raise
        except Exception as error:
            raise InfrastructureError(
                "configured verifier process failed",
                detail=str(error),
            ) from error

    @staticmethod
    def _project_observation(
        result: ProcessResult | ProcessTerminalUnavailable,
        *,
        diagnostics: VerifierDiagnostics | None = None,
    ) -> VerifierRun:
        if isinstance(result, ProcessTerminalUnavailable):
            authoritative = VerifierIndeterminate(
                terminal=Unavailable(),
                reason="terminal-unavailable",
            )
        else:
            try:
                ProcessResult.model_validate(result.model_dump(mode="python"))
            except ValidationError as error:
                raise InfrastructureError(
                    "invalid verifier process terminal",
                    detail=str(error),
                ) from error
            if result.timed_out:
                authoritative = VerifierIndeterminate(
                    terminal=TimedOut(),
                    reason="process-timed-out",
                )
            elif result.signal is not None:
                authoritative = VerifierIndeterminate(
                    terminal=Signaled(signal=result.signal),
                    reason="process-signaled",
                )
            elif result.start_error is not None:
                authoritative = VerifierIndeterminate(
                    terminal=StartFailed(),
                    reason="process-start-failed",
                )
            else:
                assert result.exit_code is not None
                terminal = NormalExit(exit_code=result.exit_code)
                authoritative = (
                    VerifierPass(terminal=terminal)
                    if result.exit_code == 0
                    else VerifierRejected(terminal=terminal)
                )
        diagnostics = diagnostics or VerifierDiagnostics(process=result)
        return VerifierRun(authoritative=authoritative, diagnostics=diagnostics)

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
        values[OBSERVATION_DIRECTORY_VARIABLE] = evidence_directory.as_posix()
        values[RUN_NONCE_VARIABLE] = nonce
        values.pop(PROGRESS_DIRECTORY_VARIABLE, None)
        values.pop(PROGRESS_NONCE_VARIABLE, None)
        values.pop(DETAILS_DIRECTORY_VARIABLE, None)
        if progress_directory is not None:
            values[PROGRESS_DIRECTORY_VARIABLE] = progress_directory.as_posix()
            values[PROGRESS_NONCE_VARIABLE] = nonce
        if failure_details_directory is not None:
            values[DETAILS_DIRECTORY_VARIABLE] = failure_details_directory.as_posix()
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
