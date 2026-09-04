from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import time
from typing import ClassVar, Literal, Protocol, overload

from pf.errors import ConfigurationError, InfrastructureError
from pf.evaluation import StagePermitPools, require_full_evaluation_contract
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.evaluation import (
    ActivityEvent,
    AttemptFailureScope,
    BaselineDetailIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellContextEvent,
    CellFailed,
    CellFailureScope,
    CellMatrixEvent,
    CellResultDetail,
    CellSucceeded,
    CheckCellOutcome,
    FailureDetail,
    FailureEvaluationRuntimeRun,
    FailureRecord,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessObservation,
    RuntimeEvaluationRun,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessResult,
    StaticIssueDetail,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
    VerificationRole,
)
from pf.schemas.project import Cell, PackagePlan, SourcePlan, cell_identity
from pf.schemas.config import RunLimits
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
    failure_records_for_result,
    failure_runtime_runs_for_result,
)
from pf.scheduling import ScheduledCellTask, Scheduler
from pf.snapshot import SourceSnapshot


class ActivityConsumer(Protocol):
    def consume(self, event: ActivityEvent) -> None: ...


class CheckCellOperations(Protocol):
    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CheckCellOutcome: ...


class SmokeCellOperations(Protocol):
    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> HighestVersionOutcome: ...


class CellSearchOperations(Protocol):
    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CellResult: ...


@dataclass(frozen=True)
class CheckVerificationRun:
    command: ClassVar[Literal["check"]] = "check"
    package: PackagePlan
    source_plan: SourcePlan
    snapshot: SourceSnapshot
    operation: CheckCellOperations
    limits: RunLimits


@dataclass(frozen=True)
class SmokeVerificationRun:
    command: ClassVar[Literal["smoke"]] = "smoke"
    package: PackagePlan
    source_plan: SourcePlan
    snapshot: SourceSnapshot
    operation: SmokeCellOperations
    limits: RunLimits


@dataclass(frozen=True)
class SearchVerificationRun:
    command: ClassVar[Literal["search"]] = "search"
    package: PackagePlan
    source_plan: SourcePlan
    snapshot: SourceSnapshot
    operation: CellSearchOperations
    limits: RunLimits


VerificationRun = CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun
VerificationResult = CheckCellOutcome | HighestVersionOutcome | CellResult


class JournalStore(Protocol):
    @property
    def run_id(self) -> str: ...

    def write_journal(self, journal: VerificationJournal) -> Path: ...

    def associate(
        self,
        report_generation_id: str,
        failure_id: str,
        result: ProcessObservation,
    ) -> None: ...


class VerificationRunner:
    """Own one command's cross-cell verification lifecycle."""

    def __init__(
        self,
        *,
        events: ActivityConsumer,
        logs: JournalStore | None,
        host_target: str,
        monotonic: Callable[[], float] = time.monotonic,
        permits: StagePermitPools | None = None,
    ) -> None:
        self._scheduler = Scheduler(monotonic=monotonic)
        self._failures = FailurePolicy()
        self._events = events
        self._logs = logs
        self._host_target = host_target
        self._permits = permits

    @overload
    def run(self, request: CheckVerificationRun) -> tuple[CheckCellOutcome, ...]: ...

    @overload
    def run(
        self,
        request: SmokeVerificationRun,
    ) -> tuple[HighestVersionOutcome, ...]: ...

    @overload
    def run(self, request: SearchVerificationRun) -> tuple[CellResult, ...]: ...

    def run(self, request: VerificationRun) -> tuple[VerificationResult, ...]:
        if not isinstance(
            request,
            (CheckVerificationRun, SmokeVerificationRun, SearchVerificationRun),
        ):
            raise TypeError("unsupported verification request")
        self._validate_source_plan(request)
        cells = self._host_cells(request.package)
        self._events.consume(_cell_matrix_event(request.package, cells))
        self._admit_evaluation(request, cells)
        if self._permits is not None:
            self._permits.configure(
                ty_jobs=request.limits.ty_jobs,
                test_jobs=request.limits.test_jobs,
            )

        gate = _VerificationEvents(
            inner=self._events,
            logs=self._logs,
            request=request,
        )
        outcomes = self._scheduler.run(
            tuple(self._task(request, cell) for cell in cells),
            jobs=request.limits.max_cells,
            max_duration_seconds=request.limits.max_duration_seconds,
            on_started=lambda task: self._events.consume(
                CellContextEvent(
                    cell=task.cell,
                    detail=BaselineDetailIdentity(),
                )
            ),
            on_completed=gate.completed,
        )
        gate.finalize()
        return outcomes

    @staticmethod
    def _validate_source_plan(request: VerificationRun) -> None:
        expected_mode = (
            "DEVELOPMENT" if isinstance(request, SmokeVerificationRun) else "SEARCH"
        )
        if request.source_plan.source_mode != expected_mode:
            raise ValueError("verification source mode does not match the command")
        if request.source_plan.routes != request.package.source_routes:
            raise ValueError("verification source plan does not match the package")

    def _host_cells(self, package: PackagePlan) -> tuple[Cell, ...]:
        cells = tuple(
            cell for cell in package.cells if cell.target == self._host_target
        )
        identities = tuple(cell_identity(cell) for cell in cells)
        if len(set(identities)) != len(identities):
            raise ValueError("verification host cells must have unique identities")
        return cells

    def _admit_evaluation(
        self,
        request: VerificationRun,
        cells: tuple[Cell, ...],
    ) -> None:
        if isinstance(request, SearchVerificationRun):
            require_full_evaluation_contract(request.package, request.command)
            return
        require_full_evaluation_contract(request.package, request.command)
        if not cells:
            raise ConfigurationError(
                f"no configured cell matches host target: {self._host_target}"
            )

    def _task(
        self,
        request: VerificationRun,
        cell: Cell,
    ) -> ScheduledCellTask[VerificationResult]:
        return ScheduledCellTask(
            cell=cell,
            run=lambda: self._run_cell(request, cell),
            deadline_result=self._deadline_result(request, cell),
        )

    @staticmethod
    def _run_cell(request: VerificationRun, cell: Cell) -> VerificationResult:
        if isinstance(request, CheckVerificationRun):
            return request.operation.check(
                package=request.package,
                cell=cell,
                snapshot=request.snapshot,
                source_plan=request.source_plan,
            )
        if isinstance(request, SmokeVerificationRun):
            return request.operation.verify(
                package=request.package,
                cell=cell,
                snapshot=request.snapshot,
                source_plan=request.source_plan,
            )
        return request.operation.search(
            package=request.package,
            cell=cell,
            snapshot=request.snapshot,
            source_plan=request.source_plan,
        )

    def _deadline_result(
        self,
        request: VerificationRun,
        cell: Cell,
    ) -> Callable[[], VerificationResult] | None:
        if not isinstance(request, SearchVerificationRun):
            return None
        if request.limits.max_duration_seconds is None:
            return None

        def deadline_result() -> VerificationResult:
            failure = self._failures.classify(
                scope=CellFailureScope(
                    package=request.package.name,
                    cell=cell,
                    source_snapshot_digest=request.snapshot.identity.digest,
                    evaluation_policy_identity=evaluation_policy_identity(
                        request.package.config
                    ),
                ),
                cause="TIMEOUT",
                stage="scheduler-deadline",
                process=None,
                detail=FailureDetail(
                    code="scheduler-deadline",
                    message="scheduling stopped at the total deadline",
                ),
            )
            return CellIndeterminate(
                cell=cell,
                phase="scheduler-deadline",
                failure_id=failure.failure_id,
                failure_records=(failure,),
            )

        return deadline_result


@dataclass(frozen=True)
class _CellProjection:
    completion: CellSucceeded | CellFailed
    entries: tuple[VerificationJournalEntry, ...]
    processes: tuple[tuple[str, ProcessObservation], ...]


class _VerificationEvents:
    def __init__(
        self,
        *,
        inner: ActivityConsumer,
        logs: JournalStore | None,
        request: VerificationRun,
    ) -> None:
        self._inner = inner
        self._logs = logs
        self._request = request
        self._entries: dict[str, VerificationJournalEntry] = {}
        self._runtime_processes: dict[str, ProcessObservation] = {}
        self._lock = Lock()
        self._error: InfrastructureError | None = None

    def completed(
        self,
        task: ScheduledCellTask[VerificationResult],
        result: VerificationResult,
        completed: int,
        total: int,
    ) -> None:
        projection = _project_result(self._request, task.cell, result)
        with self._lock:
            self._merge(projection.entries)
            for failure_id, process in projection.processes:
                self._runtime_processes.setdefault(failure_id, process)
            available = False
            if projection.entries and self._logs is not None and self._error is None:
                available = self._persist()
            event = CellCompletedEvent(
                cell=task.cell,
                completed=completed,
                total=total,
                outcome=projection.completion,
                diagnose_available=available,
            )
        self._inner.consume(event)

    def finalize(self) -> None:
        with self._lock:
            if self._logs is not None and self._error is None:
                self._persist()
            error = self._error
        if error is not None:
            raise error

    def _persist(self) -> bool:
        assert self._logs is not None
        try:
            self._logs.write_journal(self._journal())
            for failure_id, process in self._runtime_processes.items():
                self._logs.associate(
                    f"journal:{self._logs.run_id}",
                    failure_id,
                    process,
                )
        except InfrastructureError as error:
            self._error = error
            return False
        return True

    def _journal(self) -> VerificationJournal:
        assert self._logs is not None
        entries = tuple(
            sorted(
                self._entries.values(),
                key=lambda entry: (
                    entry.package,
                    entry.cell.target,
                    entry.cell.python_minor,
                    entry.cell.extra_surface,
                    entry.failure.failure_id,
                ),
            )
        )
        return VerificationJournal(
            run_id=self._logs.run_id,
            command=self._request.command,
            source_snapshot_digest=self._request.snapshot.identity.digest,
            package_policies=(
                VerificationPackagePolicy(
                    package=self._request.package.name,
                    evaluation_policy_identity=evaluation_policy_identity(
                        self._request.package.config
                    ),
                ),
            ),
            entries=entries,
        )

    def _merge(self, entries: tuple[VerificationJournalEntry, ...]) -> None:
        for entry in entries:
            existing = self._entries.get(entry.failure.failure_id)
            if existing is not None and existing != entry:
                raise ValueError("journal failure ID maps to conflicting entries")
            self._entries.setdefault(entry.failure.failure_id, entry)


def _project_result(
    request: VerificationRun,
    cell: Cell,
    result: VerificationResult,
) -> _CellProjection:
    if isinstance(request, CheckVerificationRun):
        if not isinstance(result, CheckCellOutcome):
            raise TypeError("check operation returned an invalid outcome")
        if result.attempt.identity.cell != cell:
            raise ValueError("check outcome cell does not match its scheduled cell")
        return _project_check(result)
    if isinstance(request, SmokeVerificationRun):
        if not isinstance(
            result,
            (HighestVersionPass, BaselineRejection, BaselineIndeterminate),
        ):
            raise TypeError("smoke operation returned an invalid outcome")
        if result.attempt.identity.cell != cell:
            raise ValueError("smoke outcome cell does not match its scheduled cell")
        return _project_smoke(result)
    if not isinstance(
        result,
        (
            CellSuccess,
            CellSearchFailure,
            CellIndeterminate,
            BaselineRejection,
            BaselineIndeterminate,
        ),
    ):
        raise TypeError("search operation returned an invalid outcome")
    if result.cell != cell:
        raise ValueError("search outcome cell does not match its scheduled cell")
    return _project_search(result)


def _project_check(result: CheckCellOutcome) -> _CellProjection:
    if isinstance(result.evaluation, PassEvaluation):
        completion: CellSucceeded | CellFailed = CellSucceeded(
            status=result.status,
            phase="complete",
        )
    else:
        assert result.failure is not None
        detail = _evaluation_detail(result.evaluation, runtime=result.runtime)
        process = (
            _failed_evaluation_process(
                result.evaluation,
                result.failure,
                runtime=result.runtime,
            )
            or result.failure_process
        )
        completion = CellFailed(
            status=result.status,
            phase=result.failure.stage,
            detail=detail,
            detail_failure_id=(
                result.failure.failure_id if detail is not None else None
            ),
            process=process,
            process_failure_id=(
                result.failure.failure_id if process is not None else None
            ),
            failures=(result.failure,),
            verification_role=result.role,
        )
    entries = (
        ()
        if result.failure is None
        else (
            VerificationJournalEntry(
                package=result.attempt.identity.cell.package,
                cell=result.attempt.identity.cell,
                role=result.role,
                attempt=result.attempt,
                failure=result.failure,
            ),
        )
    )
    return _CellProjection(
        completion=completion,
        entries=entries,
        processes=_completion_processes(completion),
    )


def _project_smoke(result: HighestVersionOutcome) -> _CellProjection:
    if isinstance(result, HighestVersionPass):
        completion: CellSucceeded | CellFailed = CellSucceeded(
            status=result.status,
            phase="complete",
        )
        entries: tuple[VerificationJournalEntry, ...] = ()
    else:
        detail = _evaluation_detail(result.evaluation, runtime=result.runtime)
        process = _failed_evaluation_process(
            result.evaluation,
            result.failure,
            runtime=result.runtime,
        ) or (
            result.failure_process
            if isinstance(result, BaselineIndeterminate)
            else None
        )
        completion = CellFailed(
            status=result.status,
            phase=result.failure.stage,
            detail=detail,
            detail_failure_id=(
                result.failure.failure_id if detail is not None else None
            ),
            process=process,
            process_failure_id=(
                result.failure.failure_id if process is not None else None
            ),
            failures=(result.failure,),
            verification_role="baseline",
        )
        entries = (
            VerificationJournalEntry(
                package=result.cell.package,
                cell=result.cell,
                role="baseline",
                attempt=result.attempt,
                failure=result.failure,
            ),
        )
    return _CellProjection(
        completion=completion,
        entries=entries,
        processes=_completion_processes(completion),
    )


def _project_search(result: CellResult) -> _CellProjection:
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        return _project_smoke(result)
    if isinstance(result, CellSuccess):
        completion: CellSucceeded | CellFailed = CellSucceeded(
            status=result.status,
            phase="complete",
        )
    elif isinstance(result, CellSearchFailure):
        completion = CellFailed(
            status=result.reason,
            phase=result.phase,
            failures=result.failure_records,
            verification_role="probe",
        )
    else:
        terminal = next(
            failure
            for failure in result.failure_records
            if failure.failure_id == result.failure_id
        )
        runtime_run = next(
            (
                item
                for item in result.failure_runtime_runs
                if item.failure_id == terminal.failure_id
            ),
            None,
        )
        runtime = (
            runtime_run.runtime
            if isinstance(runtime_run, FailureEvaluationRuntimeRun)
            else None
        )
        detail = _evaluation_detail(
            None if runtime is None else runtime.evaluation,
            runtime=runtime,
        )
        process = (
            None if runtime_run is None else runtime_run.process_observation
        ) or _failed_evaluation_process(
            None if runtime is None else runtime.evaluation,
            terminal,
            runtime=runtime,
        )
        completion = CellFailed(
            status=result.status,
            phase=result.phase,
            detail=detail,
            detail_failure_id=(
                terminal.failure_id if detail is not None else None
            ),
            process=process,
            process_failure_id=(
                terminal.failure_id if process is not None else None
            ),
            failures=result.failure_records,
            verification_role="probe",
        )

    entries: list[VerificationJournalEntry] = []
    for failure in failure_records_for_result(result):
        if isinstance(failure.scope, AttemptFailureScope):
            attempt = failure.scope.attempt
            requested = attempt.identity.requested_resolution
            if requested == "lowest-direct":
                raise ValueError("search result cannot contain a lowest-direct Attempt")
            role: VerificationRole = (
                "baseline" if requested == "highest" else "probe"
            )
            result_cell = attempt.identity.cell
        else:
            role = "probe"
            attempt = None
            result_cell = failure.scope.cell
        entries.append(
            VerificationJournalEntry(
                package=result_cell.package,
                cell=result_cell,
                role=role,
                attempt=attempt,
                failure=failure,
            )
        )
    processes = [
        (item.failure_id, process)
        for item in failure_runtime_runs_for_result(result)
        if (process := item.process_observation) is not None
    ]
    process_failure_ids = {failure_id for failure_id, _ in processes}
    for association in _completion_processes(completion):
        if association[0] not in process_failure_ids:
            processes.append(association)
    return _CellProjection(
        completion=completion,
        entries=tuple(entries),
        processes=tuple(processes),
    )


def _completion_processes(
    completion: CellSucceeded | CellFailed,
) -> tuple[tuple[str, ProcessObservation], ...]:
    if not isinstance(completion, CellFailed) or completion.process is None:
        return ()
    assert completion.process_failure_id is not None
    return ((completion.process_failure_id, completion.process),)


def _failed_evaluation_process(
    evaluation: object,
    failure: FailureRecord | None,
    *,
    runtime: RuntimeEvaluationRun | None = None,
) -> ProcessObservation | None:
    process = _runtime_process(runtime)
    if process is not None:
        return process
    if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        confirmed = evaluation.witnesses[-1].outcome
        assert isinstance(confirmed, RuntimeWitnessResult)
        return confirmed.process
    if isinstance(evaluation, IndeterminateEvaluation):
        if evaluation.failure is None:
            return None
        return evaluation.failure.process
    return None if failure is None else failure.process


def _evaluation_detail(
    evaluation: object | None,
    *,
    runtime: RuntimeEvaluationRun | None = None,
) -> CellResultDetail | None:
    if runtime is not None and runtime.diagnostics is not None:
        detail = runtime.diagnostics.detail
        if detail is not None:
            return detail
    if not isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        return None
    confirmed = evaluation.witnesses[-1].outcome
    assert isinstance(confirmed, RuntimeWitnessResult)
    identities = set(confirmed.plan.diagnostic_identities)
    relevant = tuple(
        diagnostic
        for diagnostic in evaluation.static.incremental
        if diagnostic.identity in identities
    )
    if not relevant:
        return None
    return StaticIssueDetail(first=relevant[0], total=len(relevant))


def _runtime_process(runtime: RuntimeEvaluationRun | None) -> ProcessObservation | None:
    if runtime is None or runtime.diagnostics is None:
        return None
    return runtime.diagnostics.process


def _cell_matrix_event(
    package: PackagePlan,
    cells: tuple[Cell, ...],
) -> CellMatrixEvent:
    active_names: set[str] = set()
    pinned_names: set[str] = set()
    for cell in cells:
        active_ids = set(cell.active_declaration_ids)
        for declaration in package.declarations:
            if declaration.declaration_id not in active_ids:
                continue
            active_names.add(declaration.name)
            if declaration.kind == "fixed":
                pinned_names.add(declaration.name)
    return CellMatrixEvent(
        cells=cells,
        active_packages=len(active_names),
        pinned_packages=len(pinned_names),
    )
