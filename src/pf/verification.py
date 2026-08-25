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
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellFailed,
    CellFailureScope,
    CellSucceeded,
    CheckCellOutcome,
    FailureDetail,
    FailureRecord,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessResult,
    SearchFailureEvent,
    StaticRegressionEvaluation,
    TestFailEvaluation,
    ToolFailure,
    TyDiagnostic,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
    VerificationRole,
)
from pf.schemas.project import Cell, PackagePlan, cell_identity
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
    deadline_scope: CellFailureScope | None = None


@dataclass(frozen=True)
class VerificationRun(Generic[T]):
    command: Literal["smoke", "check", "search"]
    packages: tuple[PackagePlan, ...]
    snapshot: SourceSnapshot
    tasks: tuple[VerificationTask[T], ...]
    jobs: int | Literal["auto"]
    max_duration_seconds: float | None


class JournalStore(Protocol):
    @property
    def run_id(self) -> str: ...

    def write_journal(self, journal: VerificationJournal) -> Path: ...


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
        package_names = tuple(package.name for package in request.packages)
        if package_names != tuple(sorted(set(package_names))):
            raise ValueError("verification packages must be sorted and unique")
        task_keys = tuple(cell_identity(task.cell) for task in request.tasks)
        if len(set(task_keys)) != len(task_keys):
            raise ValueError("verification tasks must have unique cells")
        if any(task.cell.package not in package_names for task in request.tasks):
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
        self._buffered: dict[
            tuple[str, str, str, tuple[str, ...]],
            list[VerificationJournalEntry],
        ] = {}
        self._entries: dict[str, VerificationJournalEntry] = {}
        self._lock = Lock()
        self._error: InfrastructureError | None = None

    def consume(self, event: ActivityEvent) -> None:
        if isinstance(event, SearchFailureEvent):
            entry = self._entry_for_failure(
                package=event.cell.package,
                cell=event.cell,
                role=None,
                failure=event.failure,
            )
            with self._lock:
                self._buffered.setdefault(cell_identity(event.cell), []).append(entry)
            self._inner.consume(event)
            return
        self._inner.consume(event)

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
            entries = list(self._buffered.pop(key, ()))
            verification_task = self._tasks[key]
            entries.extend(verification_task.journal_entries(result))
            self._merge(entries)
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
            package_policies=tuple(
                VerificationPackagePolicy(
                    package=package.name,
                    evaluation_policy_identity=evaluation_policy_identity(
                        package.config
                    ),
                )
                for package in self._request.packages
            ),
            entries=entries,
        )

    def _merge(self, entries: list[VerificationJournalEntry]) -> None:
        for entry in entries:
            existing = self._entries.get(entry.failure.failure_id)
            if existing is not None and existing != entry:
                raise ValueError("journal failure ID maps to conflicting entries")
            self._entries.setdefault(entry.failure.failure_id, entry)

    @staticmethod
    def _entry_for_failure(
        *,
        package: str,
        cell: Cell,
        role: VerificationRole | None,
        failure: FailureRecord,
    ) -> VerificationJournalEntry:
        attempt = (
            failure.scope.attempt
            if isinstance(failure.scope, AttemptFailureScope)
            else None
        )
        resolved: VerificationRole
        if role is not None:
            resolved = role
        elif attempt is None:
            resolved = "probe"
        elif attempt.identity.requested_resolution == "highest":
            resolved = "baseline"
        elif attempt.identity.requested_resolution == "lowest-direct":
            resolved = "declaration"
        else:
            resolved = "probe"
        return VerificationJournalEntry(
            package=package,
            cell=cell,
            role=resolved,
            attempt=attempt,
            failure=failure,
        )


def completion_outcome(result: object) -> CellSucceeded | CellFailed:
    """Project one verification result into the terminal completion contract."""
    if isinstance(result, CheckCellOutcome):
        if isinstance(result.evaluation, PassEvaluation):
            diagnostics = result.evaluation.static.ty.diagnostics
            return CellSucceeded(
                status=result.status,
                phase="complete",
                diagnostics=diagnostics,
                process=result.evaluation.static.ty.process if diagnostics else None,
            )
        diagnostics, process = _failed_evaluation_payload(
            result.evaluation,
            result.failure,
        )
        assert result.failure is not None
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            diagnostics=diagnostics,
            process=process,
            failures=(result.failure,),
            verification_role=result.role,
        )

    if isinstance(result, HighestVersionPass):
        diagnostics = result.baseline.ty.diagnostics
        return CellSucceeded(
            status=result.status,
            phase="complete",
            diagnostics=diagnostics,
            process=result.baseline.ty.process if diagnostics else None,
        )

    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        diagnostics, process = _failed_evaluation_payload(
            result.evaluation,
            result.failure,
        )
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            diagnostics=diagnostics,
            process=process,
            failures=(result.failure,),
            verification_role="baseline",
        )

    if isinstance(result, CellSuccess):
        diagnostics = result.static_baseline.ty.diagnostics
        return CellSucceeded(
            status=result.status,
            phase="complete",
            diagnostics=diagnostics,
            process=result.static_baseline.ty.process if diagnostics else None,
        )

    if isinstance(result, CellSearchFailure):
        diagnostics = result.static_baseline.ty.diagnostics
        return CellFailed(
            status=result.status,
            phase=result.phase,
            diagnostics=diagnostics,
            process=result.static_baseline.ty.process if diagnostics else None,
            failures=result.failure_records,
            verification_role="probe",
        )

    if isinstance(result, CellIndeterminate):
        terminal = next(
            failure
            for failure in result.failure_records
            if failure.failure_id == result.failure_id
        )
        return CellFailed(
            status=result.status,
            phase=result.phase,
            process=terminal.process,
            failures=result.failure_records,
            verification_role="probe",
        )

    if isinstance(result, PassEvaluation):
        diagnostics = result.static.ty.diagnostics
        return CellSucceeded(
            status=result.status,
            phase="complete",
            diagnostics=diagnostics,
            process=result.static.ty.process if diagnostics else None,
        )

    if isinstance(result, RuntimeInterfaceMissingEvaluation):
        confirmed = next(
            attempt.outcome
            for attempt in result.witnesses
            if isinstance(attempt.outcome, RuntimeWitnessResult)
            and attempt.outcome.status == "CONFIRMED_MISSING"
        )
        return CellFailed(
            status=result.status,
            phase="witness",
            diagnostics=_static_diagnostics(result.static),
            process=confirmed.process,
        )

    if isinstance(result, TestFailEvaluation):
        return CellFailed(
            status=result.status,
            phase="test",
            diagnostics=_static_diagnostics(result.static),
            process=result.test.process,
        )

    if isinstance(result, IndeterminateEvaluation):
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


def _failed_evaluation_payload(
    evaluation: object,
    failure: FailureRecord | None,
) -> tuple[tuple[TyDiagnostic, ...], ProcessResult | None]:
    if isinstance(evaluation, TestFailEvaluation):
        return _static_diagnostics(evaluation.static), evaluation.test.process
    if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        confirmed = next(
            attempt.outcome
            for attempt in evaluation.witnesses
            if isinstance(attempt.outcome, RuntimeWitnessResult)
            and attempt.outcome.status == "CONFIRMED_MISSING"
        )
        return _static_diagnostics(evaluation.static), confirmed.process
    if isinstance(evaluation, IndeterminateEvaluation) and evaluation.static is not None:
        return _static_diagnostics(evaluation.static), evaluation.failure.process
    return (), None if failure is None else failure.process


def _static_diagnostics(
    static: object,
) -> tuple[TyDiagnostic, ...]:
    if isinstance(static, StaticRegressionEvaluation):
        return static.incremental
    diagnostics = getattr(getattr(static, "ty", None), "diagnostics", ())
    return tuple(diagnostics)
