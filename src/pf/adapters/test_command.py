from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from typing import Literal

from pydantic import ValidationError

from pf.adapters.process import ProcessRunner
from pf.adapters.pytest_progress import (
    PROGRESS_DIRECTORY_VARIABLE,
    PROGRESS_NONCE_VARIABLE,
    PytestProgressMonitor,
)
from pf.adapters.pytest_observer import (
    CASES_DIRECTORY_VARIABLE,
    CASES_PROJECTION_VARIABLE,
    DETAILS_DIRECTORY_VARIABLE,
    OBSERVATION_DIRECTORY_VARIABLE,
    PRUNE_NONCE_VARIABLE,
    PRUNE_REQUEST_VARIABLE,
    RUN_NONCE_VARIABLE,
    PytestCasesObservation,
    read_pytest_observer_cases,
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
    CASES_DIRECTORY_VARIABLE,
    CASES_PROJECTION_VARIABLE,
    PROGRESS_DIRECTORY_VARIABLE,
    PROGRESS_NONCE_VARIABLE,
    RUN_NONCE_VARIABLE,
    PRUNE_REQUEST_VARIABLE,
    PRUNE_NONCE_VARIABLE,
)

CasesProjection = Literal["failed", "collected"]
SelectionReason = Literal[
    "empty",
    "collection-failed",
    "unexpected-item",
    "duplicate",
    "missing",
    "invalid",
]


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


@dataclass(frozen=True)
class _PytestOutcome:
    run: VerifierRun
    cases: PytestCasesObservation


@dataclass(frozen=True)
class _Selection:
    applied: bool
    collected: tuple[str, ...] = ()
    reason: SelectionReason | None = None


class ConfiguredVerifier:
    """Run one configured verifier and decide only from process terminal facts."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def run(
        self,
        request: VerifierRequest,
        progress: Callable[[StageProgress | None], None] | None = None,
    ) -> VerifierRun:
        if not _is_direct_pytest(request.command):
            if request.failed_case_nodeids:
                raise InfrastructureError(
                    "failed-case nodeids require a direct pytest command",
                )
            result = self._run_process(
                command=request.command,
                cwd=request.cwd,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
            )
            return self._project_observation(result)
        if not request.failed_case_nodeids:
            return self._run_pytest_profile(
                command=request.command,
                cwd=request.cwd,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                progress=progress,
                projection="failed",
            ).run
        failed_set = self._run_pytest_profile(
            command=request.command,
            cwd=request.cwd,
            environment=request.environment,
            timeout_seconds=request.timeout_seconds,
            progress=progress,
            projection="collected",
            prune_nodeids=request.failed_case_nodeids,
        )
        if isinstance(failed_set.run.authoritative, VerifierIndeterminate):
            return failed_set.run
        if not isinstance(failed_set.run.authoritative.terminal, NormalExit):
            return failed_set.run
        selection = _selection_decision(
            requested=request.failed_case_nodeids,
            cases=failed_set.cases,
        )
        if not selection.applied:
            return self._run_pytest_profile(
                command=request.command,
                cwd=request.cwd,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                progress=progress,
                projection="failed",
            ).run
        if isinstance(failed_set.run.authoritative, VerifierRejected):
            return failed_set.run
        return self._run_pytest_profile(
            command=request.command,
            cwd=request.cwd,
            environment=request.environment,
            timeout_seconds=request.timeout_seconds,
            progress=progress,
            projection="failed",
        ).run

    def _run_pytest_profile(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        timeout_seconds: int | None,
        progress: Callable[[StageProgress | None], None] | None,
        projection: CasesProjection,
        prune_nodeids: tuple[str, ...] = (),
    ) -> _PytestOutcome:
        temporary = None
        progress_temporary = None
        failure_details_temporary = None
        cache_temporary = None
        cases_temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="pf-pytest-observer-")
            cache_temporary = tempfile.TemporaryDirectory(prefix="pf-pytest-cache-")
            cases_temporary = tempfile.TemporaryDirectory(
                prefix="pf-pytest-observer-cases-"
            )
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
            modules = [f"_pf_pytest_observer_{nonce}"]
            observer_source = (
                resources.files("pf").joinpath("_pytest_observer.py").read_bytes()
            )
            (plugin_directory / f"{modules[0]}.py").write_bytes(observer_source)
            extra_environment: dict[str, str] = {
                CASES_DIRECTORY_VARIABLE: Path(cases_temporary.name).as_posix(),
                CASES_PROJECTION_VARIABLE: projection,
            }
            if prune_nodeids:
                prune_module = f"_pf_pytest_pruning_{nonce}"
                prune_source = (
                    resources.files("pf").joinpath("_pytest_pruning.py").read_bytes()
                )
                (plugin_directory / f"{prune_module}.py").write_bytes(prune_source)
                modules.append(prune_module)
                request_path = root / "prune-request.json"
                request_path.write_bytes(
                    (
                        json.dumps(
                            list(prune_nodeids),
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                extra_environment[PRUNE_REQUEST_VARIABLE] = request_path.as_posix()
                extra_environment[PRUNE_NONCE_VARIABLE] = nonce
            injected_command = _insert_before_double_dash(
                self._inject_plugins(command, tuple(modules)),
                (
                    "--maxfail=1",
                    "-o",
                    f"cache_dir={Path(cache_temporary.name).as_posix()}",
                ),
            )
            injected_environment = self._inject_environment(
                environment,
                plugin_directory=plugin_directory,
                evidence_directory=evidence_directory,
                progress_directory=active_progress_directory,
                failure_details_directory=active_failure_details_directory,
                nonce=nonce,
                extra=extra_environment,
            )
        except Exception as error:
            _cleanup(failure_details_temporary)
            _cleanup(progress_temporary)
            _cleanup(cases_temporary)
            _cleanup(cache_temporary)
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
            cases = read_pytest_observer_cases(
                Path(cases_temporary.name),
                nonce=nonce,
                projection=projection,
            )
            if isinstance(result, ProcessTerminalUnavailable) or (
                result.timed_out
                or result.signal is not None
                or result.start_error is not None
            ):
                outcome = _PytestOutcome(
                    run=self._project_observation(result),
                    cases=cases,
                )
            else:
                observer = read_pytest_observer(evidence_directory, nonce=nonce)
                detail = (
                    None
                    if active_failure_details_directory is None
                    else read_pytest_observer_detail(
                        active_failure_details_directory,
                        nonce=nonce,
                    )
                )
                facts = () if observer is None else tuple(sorted(observer.facts))
                conflict = (
                    "pytest-terminal-metadata-conflict"
                    if observer is not None and (result.exit_code == 0) == observer.has_failure
                    else None
                )
                additions = (
                    _failed_additions(cases) if projection == "failed" else ()
                )
                outcome = _PytestOutcome(
                    run=self._project_observation(
                        result,
                        diagnostics=VerifierDiagnostics(
                            process=result,
                            detail=detail,
                            summary_code=conflict,
                            pytest_execution_mode=None if observer is None else observer.execution_mode,
                            pytest_facts=facts,
                            pytest_version=None if observer is None else observer.pytest_version,
                            python_minor=None if observer is None else observer.python_minor,
                        ),
                        failed_case_additions=additions,
                    ),
                    cases=cases,
                )
        except BaseException:
            _cleanup(failure_details_temporary)
            _cleanup(progress_temporary)
            _cleanup(cases_temporary)
            _cleanup(cache_temporary)
            _cleanup(temporary)
            raise
        _cleanup(failure_details_temporary)
        _cleanup(progress_temporary)
        cases_cleaned = _cleanup(cases_temporary)
        cache_cleaned = _cleanup(cache_temporary)
        if not _cleanup(temporary) or not cache_cleaned or not cases_cleaned:
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
        failed_case_additions: tuple[str, ...] = (),
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
        additions = (
            failed_case_additions
            if isinstance(authoritative, VerifierRejected)
            else ()
        )
        return VerifierRun(
            authoritative=authoritative,
            diagnostics=diagnostics,
            failed_case_additions=additions,
        )

    @staticmethod
    def _inject_plugins(
        command: tuple[str, ...], modules: tuple[str, ...]
    ) -> tuple[str, ...]:
        executable = _executable_basename(command[0])
        prefix_length = 1 if executable in {"pytest", "py.test"} else 3
        extra = tuple(token for module in modules for token in ("-p", module))
        return (*command[:prefix_length], *extra, *command[prefix_length:])

    @staticmethod
    def _inject_plugin(command: tuple[str, ...], module: str) -> tuple[str, ...]:
        return ConfiguredVerifier._inject_plugins(command, (module,))

    @staticmethod
    def _inject_environment(
        environment: tuple[EnvironmentVariable, ...],
        *,
        plugin_directory: Path,
        evidence_directory: Path,
        progress_directory: Path | None,
        failure_details_directory: Path | None,
        nonce: str,
        extra: dict[str, str] | None = None,
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
        values.pop(CASES_DIRECTORY_VARIABLE, None)
        values.pop(CASES_PROJECTION_VARIABLE, None)
        values.pop(PRUNE_REQUEST_VARIABLE, None)
        values.pop(PRUNE_NONCE_VARIABLE, None)
        if progress_directory is not None:
            values[PROGRESS_DIRECTORY_VARIABLE] = progress_directory.as_posix()
            values[PROGRESS_NONCE_VARIABLE] = nonce
        if failure_details_directory is not None:
            values[DETAILS_DIRECTORY_VARIABLE] = failure_details_directory.as_posix()
        if extra:
            values.update(extra)
        return tuple(
            EnvironmentVariable(name=name, value=value)
            for name, value in values.items()
        )


def _insert_before_double_dash(
    command: tuple[str, ...], extra: tuple[str, ...]
) -> tuple[str, ...]:
    try:
        index = command.index("--")
    except ValueError:
        return (*command, *extra)
    return (*command[:index], *extra, *command[index:])


def _failed_additions(cases: PytestCasesObservation) -> tuple[str, ...]:
    if cases.status != "present":
        return ()
    nodeids = sorted(
        {
            nodeid
            for record in cases.records
            if record.projection == "failed"
            for nodeid in record.nodeids
        }
    )
    return tuple(nodeids)


def _selection_decision(
    *,
    requested: tuple[str, ...],
    cases: PytestCasesObservation,
) -> _Selection:
    if cases.status == "missing":
        return _Selection(applied=False, reason="missing")
    if cases.status != "present":
        return _Selection(applied=False, reason="invalid")
    requested_set = set(requested)
    authoritative: tuple[str, ...] | None = None
    workers: list[tuple[str, ...]] = []
    collection_failed = False
    collection_completed = False
    for record in cases.records:
        if record.collection_failed:
            collection_failed = True
        if record.role in {"serial", "controller"}:
            collection_completed = record.collection_completed
            if authoritative is not None:
                return _Selection(applied=False, reason="invalid")
            authoritative = record.nodeids
        elif record.role == "worker":
            workers.append(record.nodeids)
    if collection_failed:
        return _Selection(applied=False, reason="collection-failed")
    if authoritative is None:
        return _Selection(applied=False, reason="missing")
    if not collection_completed:
        return _Selection(applied=False, reason="collection-failed")
    if not authoritative:
        return _Selection(applied=False, reason="empty")
    if len(authoritative) != len(set(authoritative)):
        return _Selection(applied=False, reason="duplicate")
    if any(nodeid not in requested_set for nodeid in authoritative):
        return _Selection(applied=False, reason="unexpected-item")
    for worker_nodeids in workers:
        if len(worker_nodeids) != len(set(worker_nodeids)):
            return _Selection(applied=False, reason="duplicate")
        if any(nodeid not in requested_set for nodeid in worker_nodeids):
            return _Selection(applied=False, reason="unexpected-item")
    collected = tuple(nodeid for nodeid in requested if nodeid in set(authoritative))
    return _Selection(applied=True, collected=collected)


def _cleanup(temporary: tempfile.TemporaryDirectory[str] | None) -> bool:
    if temporary is None:
        return True
    try:
        temporary.cleanup()
    except Exception:
        return False
    return True
