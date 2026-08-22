from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Generic, Literal, Protocol, TypeVar

from pf.errors import InfrastructureError
from pf.policy import evaluation_policy_identity
from pf.schemas.evaluation import (
    ActivityEvent,
    AttemptFailureScope,
    CellFailureScope,
    FailureRecord,
    ProgressEvent,
    SearchFailureEvent,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
    VerificationRole,
)
from pf.schemas.project import Cell, PackagePlan, cell_identity
from pf.scheduling import ProgressConsumer, ScheduledCellTask, Scheduler
from pf.snapshot import SourceSnapshot


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
    run_id: str

    def write_journal(self, journal: VerificationJournal) -> Path: ...


class VerificationRunner:
    """Own scheduling and durable per-cell verification journal timing."""

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        events: ProgressConsumer,
        logs: JournalStore | None,
    ) -> None:
        self._scheduler = scheduler
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
                    run=gate.wrap(task),
                    deadline_scope=task.deadline_scope,
                )
                for task in request.tasks
            ),
            jobs=request.jobs,
            max_duration_seconds=request.max_duration_seconds,
            events=gate,
        )
        gate.finalize()
        return outcomes


class _VerificationEvents(Generic[T]):
    def __init__(
        self,
        *,
        inner: ProgressConsumer,
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
        self._outcomes: dict[tuple[str, str, str, tuple[str, ...]], T] = {}
        self._buffered: dict[
            tuple[str, str, str, tuple[str, ...]],
            list[VerificationJournalEntry],
        ] = {}
        self._entries: dict[str, VerificationJournalEntry] = {}
        self._lock = Lock()
        self._error: InfrastructureError | None = None

    def wrap(self, task: VerificationTask[T]) -> Callable[[], T]:
        def execute() -> T:
            outcome = task.execute()
            with self._lock:
                self._outcomes[cell_identity(task.cell)] = outcome
            return outcome

        return execute

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
        if not isinstance(event, ProgressEvent) or event.completed == 0:
            self._inner.consume(event)
            return

        key = cell_identity(event.cell)
        with self._lock:
            entries = list(self._buffered.pop(key, ()))
            outcome = self._outcomes.get(key)
            task = self._tasks[key]
            if outcome is not None:
                entries.extend(task.journal_entries(outcome))
            elif event.failure is not None:
                entries.append(
                    self._entry_for_failure(
                        package=event.cell.package,
                        cell=event.cell,
                        role=event.verification_role,
                        failure=event.failure,
                    )
                )
            self._merge(entries)
            failed = bool(entries) or event.failure is not None
            available = False
            if failed and self._logs is not None and self._error is None:
                available = self._persist()
            forwarded = (
                event.model_copy(update={"diagnose_available": available})
                if failed
                else event
            )
        self._inner.consume(forwarded)

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
            entries=tuple(
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
            ),
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
