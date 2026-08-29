from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import time
from typing import Generic, Literal, Protocol, TypeVar, cast

from pf.errors import InfrastructureError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.evaluation import (
    ActivityEvent,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellFailed,
    CellFailureScope,
    CellResultDetail,
    CellSucceeded,
    CheckCellOutcome,
    FailureDetail,
    FailureEvaluationRuntimeRun,
    FailureRecord,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessObservation,
    RuntimeInterfaceMissingEvaluation,
    RuntimeEvaluationRun,
    RuntimeWitnessResult,
    StaticIssueDetail,
    VerifierRejectedEvaluation,
    ToolFailure,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
)
from pf.schemas.project import (
    Cell,
    PackagePlan,
    ResolutionSourceMode,
    cell_identity,
)
from pf.schemas.report import CellIndeterminate, CellSearchFailure, CellSuccess
from pf.scheduling import ScheduledCellTask, Scheduler
from pf.snapshot import SourceSnapshot


class ActivityConsumer(Protocol):
    def consume(self, event: ActivityEvent) -> None: ...


class VerificationOutcome(Protocol):
    @property
    def status(self) -> str: ...


T = TypeVar("T", bound=VerificationOutcome)


@dataclass(frozen=True)
class VerificationTask(Generic[T]):
    cell: Cell
    execute: Callable[[], T]
    journal_entries: Callable[[T], tuple[VerificationJournalEntry, ...]]
    runtime_associations: (
        Callable[[T], tuple[tuple[str, ProcessObservation], ...]] | None
    ) = None
    deadline_scope: CellFailureScope | None = None


@dataclass(frozen=True)
class VerificationRun(Generic[T]):
    command: Literal["smoke", "check", "search"]
    package: PackagePlan
    source_mode: ResolutionSourceMode
    snapshot: SourceSnapshot
    tasks: tuple[VerificationTask[T], ...]
    jobs: int | Literal["auto"]
    max_duration_seconds: float | None


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
    """Own scheduling and durable per-cell verification journal timing."""

    def __init__(
        self,
        *,
        events: ActivityConsumer,
        logs: JournalStore | None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._scheduler = Scheduler(monotonic=monotonic)
        self._failures = FailurePolicy()
        self._events = events
        self._logs = logs

    def run(self, request: VerificationRun[T]) -> tuple[T, ...]:
        expected_mode: ResolutionSourceMode = (
            "DEVELOPMENT" if request.command == "smoke" else "SEARCH"
        )
        if request.source_mode != expected_mode:
            raise ValueError("verification source mode does not match the command")
        task_keys = tuple(cell_identity(task.cell) for task in request.tasks)
        if len(set(task_keys)) != len(task_keys):
            raise ValueError("verification tasks must have unique cells")
        if any(
            task.cell.package != request.package.name for task in request.tasks
        ):
            raise ValueError("verification task package is outside the run")

        gate = _VerificationEvents(
            inner=self._events,
            logs=self._logs,
            request=request,
        )
        outcomes = self._scheduler.run(
            tuple(
                ScheduledCellTask(
                    cell=task.cell,
                    run=task.execute,
                    deadline_result=self._deadline_result(task),
                )
                for task in request.tasks
            ),
            jobs=request.jobs,
            max_duration_seconds=request.max_duration_seconds,
            on_started=lambda task: None,
            on_completed=gate.completed,
        )
        gate.finalize()
        return outcomes

    def _deadline_result(self, task: VerificationTask[T]) -> Callable[[], T] | None:
        scope = task.deadline_scope
        if scope is None:
            return None

        def deadline_result() -> T:
            failure = self._failures.classify(
                scope=scope,
                cause="TIMEOUT",
                stage="scheduler-deadline",
                process=None,
                detail=FailureDetail(
                    code="scheduler-deadline",
                    message="scheduling stopped at the total deadline",
                ),
            )
            return cast(
                T,
                CellIndeterminate(
                    cell=task.cell,
                    phase="scheduler-deadline",
                    failure_id=failure.failure_id,
                    failure_records=(failure,),
                ),
            )

        return deadline_result


class _VerificationEvents(Generic[T]):
    def __init__(
        self,
        *,
        inner: ActivityConsumer,
        logs: JournalStore | None,
        request: VerificationRun[T],
    ) -> None:
        self._inner = inner
        self._logs = logs
        self._request = request
        self._tasks = {
            cell_identity(task.cell): task
            for task in request.tasks
        }
        self._entries: dict[str, VerificationJournalEntry] = {}
        self._runtime_processes: dict[str, ProcessObservation] = {}
        self._lock = Lock()
        self._error: InfrastructureError | None = None

    def completed(
        self,
        task: ScheduledCellTask[T],
        result: T,
        completed: int,
        total: int,
    ) -> None:
        key = cell_identity(task.cell)
        outcome = completion_outcome(result)
        with self._lock:
            verification_task = self._tasks[key]
            entries = list(verification_task.journal_entries(result))
            self._merge(entries)
            if verification_task.runtime_associations is not None:
                for failure_id, process in verification_task.runtime_associations(
                    result
                ):
                    self._runtime_processes.setdefault(failure_id, process)
            if isinstance(outcome, CellFailed) and outcome.process is not None:
                assert outcome.process_failure_id is not None
                self._runtime_processes.setdefault(
                    outcome.process_failure_id,
                    outcome.process,
                )
            available = False
            if entries and self._logs is not None and self._error is None:
                available = self._persist()
            event = CellCompletedEvent(
                cell=task.cell,
                completed=completed,
                total=total,
                outcome=outcome,
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
        entries: tuple[VerificationJournalEntry, ...] = tuple(
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

    def _merge(self, entries: list[VerificationJournalEntry]) -> None:
        for entry in entries:
            existing = self._entries.get(entry.failure.failure_id)
            if existing is not None and existing != entry:
                raise ValueError("journal failure ID maps to conflicting entries")
            self._entries.setdefault(entry.failure.failure_id, entry)


def completion_outcome(result: object) -> CellSucceeded | CellFailed:
    """Project one verification result into the terminal completion contract."""
    if isinstance(result, CheckCellOutcome):
        if isinstance(result.evaluation, PassEvaluation):
            return CellSucceeded(
                status=result.status,
                phase="complete",
            )
        assert result.failure is not None
        detail = _evaluation_detail(result.evaluation, runtime=result.runtime)
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            detail=detail,
            detail_failure_id=(result.failure.failure_id if detail is not None else None),
            process=(
                process := _failed_evaluation_process(
                    result.evaluation,
                    result.failure,
                    runtime=result.runtime,
                )
                or result.failure_process
            ),
            process_failure_id=(
                result.failure.failure_id if process is not None else None
            ),
            failures=(result.failure,),
            verification_role=result.role,
        )

    if isinstance(result, HighestVersionPass):
        return CellSucceeded(
            status=result.status,
            phase="complete",
        )

    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        detail = _evaluation_detail(result.evaluation, runtime=result.runtime)
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            detail=detail,
            detail_failure_id=(result.failure.failure_id if detail is not None else None),
            process=(
                process := _failed_evaluation_process(
                    result.evaluation,
                    result.failure,
                    runtime=result.runtime,
                )
                or (
                    result.failure_process
                    if isinstance(result, BaselineIndeterminate)
                    else None
                )
            ),
            process_failure_id=(
                result.failure.failure_id if process is not None else None
            ),
            failures=(result.failure,),
            verification_role="baseline",
        )

    if isinstance(result, CellSuccess):
        return CellSucceeded(
            status=result.status,
            phase="complete",
        )

    if isinstance(result, CellSearchFailure):
        return CellFailed(
            status=result.reason,
            phase=result.phase,
            failures=result.failure_records,
            verification_role="probe",
        )

    if isinstance(result, CellIndeterminate):
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
        return CellFailed(
            status=result.status,
            phase=result.phase,
            detail=detail,
            detail_failure_id=(terminal.failure_id if detail is not None else None),
            process=process,
            process_failure_id=(
                terminal.failure_id if process is not None else None
            ),
            failures=result.failure_records,
            verification_role="probe",
        )

    if isinstance(result, PassEvaluation):
        return CellSucceeded(
            status=result.status,
            phase="complete",
        )

    if isinstance(result, RuntimeInterfaceMissingEvaluation):
        confirmed = result.witnesses[-1].outcome
        assert isinstance(confirmed, RuntimeWitnessResult)
        return CellFailed(
            status=result.status,
            phase="witness",
            detail=_evaluation_detail(result),
            process=confirmed.process,
        )

    if isinstance(result, VerifierRejectedEvaluation):
        return CellFailed(
            status=result.status,
            phase="test",
        )

    if isinstance(result, IndeterminateEvaluation):
        if result.verifier is not None:
            return CellFailed(status=result.status, phase="test")
        assert result.failure is not None
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            process=result.failure.process,
        )

    if isinstance(result, ToolFailure):
        return CellFailed(
            status=result.status,
            phase=result.stage,
            process=result.process,
        )

    raise TypeError(f"unsupported verification result: {type(result).__name__}")


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
