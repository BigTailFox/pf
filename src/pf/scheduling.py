from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import os
import time
from typing import Generic, Protocol, TypeVar, cast

from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    ActivityEvent,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    FailureDetail,
    FailureRecord,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    ProgressEvent,
    StaticFailEvaluation,
    TestFailEvaluation,
    ToolFailure,
    TyDiagnostic,
)
from pf.schemas.project import Cell
from pf.schemas.report import CellIndeterminate, CellSearchFailure, CellSuccess


class ProgressConsumer(Protocol):
    def consume(self, event: ActivityEvent) -> None: ...


class HasStatus(Protocol):
    status: str


T = TypeVar("T", bound=HasStatus)


@dataclass(frozen=True)
class ScheduledCellTask(Generic[T]):
    cell: Cell
    run: Callable[[], T]
    deadline_scope: CellFailureScope | None = None


class Scheduler:
    """Bound independent cell work while preserving canonical result order."""

    def __init__(self, *, failures: FailurePolicy | None = None) -> None:
        self._failures = failures or FailurePolicy()

    def run(
        self,
        tasks: tuple[ScheduledCellTask[T], ...],
        *,
        jobs: int | str,
        max_duration_seconds: float | None,
        events: ProgressConsumer,
    ) -> tuple[T, ...]:
        worker_count = self._worker_count(jobs)
        deadline = (
            time.monotonic() + max_duration_seconds
            if max_duration_seconds is not None
            else None
        )
        pending = iter(tasks)
        running: dict[Future[T], ScheduledCellTask[T]] = {}
        completed_items: list[tuple[Cell, T]] = []
        completed = 0
        for task in tasks:
            events.consume(
                ProgressEvent(
                    package=task.cell.package,
                    cell=task.cell,
                    phase="start",
                    completed=0,
                    total=len(tasks),
                    message="running",
                )
            )

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            self._fill(
                executor,
                running,
                pending,
                worker_count,
                deadline,
            )
            while running:
                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task = running.pop(future)
                    result = future.result()
                    completed_items.append((task.cell, result))
                    completed += 1
                    events.consume(
                        _completion_progress(
                            cell=task.cell,
                            result=result,
                            completed=completed,
                            total=len(tasks),
                        )
                    )
                self._fill(
                    executor,
                    running,
                    pending,
                    worker_count,
                    deadline,
                )

        for task in pending:
            if task.deadline_scope is None:
                raise ValueError("deadline-limited cell task requires failure scope")
            failure = self._failures.classify(
                scope=task.deadline_scope,
                cause="TIMEOUT",
                stage="scheduler-deadline",
                process=None,
                detail=FailureDetail(
                    code="scheduler-deadline",
                    message="scheduling stopped at the total deadline",
                ),
            )
            timeout = CellIndeterminate(
                cell=task.cell,
                phase="scheduler-deadline",
                failure_id=failure.failure_id,
                failure_records=(failure,),
            )
            completed_items.append((task.cell, cast(T, timeout)))
            completed += 1
            events.consume(
                _completion_progress(
                    cell=task.cell,
                    result=timeout,
                    completed=completed,
                    total=len(tasks),
                    phase="scheduler-deadline",
                )
            )

        return tuple(
            result
            for _, result in sorted(
                completed_items,
                key=lambda item: self._cell_key(item[0]),
            )
        )

    @staticmethod
    def _fill(
        executor: ThreadPoolExecutor,
        running: dict[Future[T], ScheduledCellTask[T]],
        pending: Iterator[ScheduledCellTask[T]],
        worker_count: int,
        deadline: float | None,
    ) -> None:
        while len(running) < worker_count:
            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                task = next(pending)
            except StopIteration:
                return
            running[executor.submit(task.run)] = task

    @staticmethod
    def _worker_count(jobs: int | str) -> int:
        if jobs == "auto":
            return max(1, os.cpu_count() or 1)
        if isinstance(jobs, int) and not isinstance(jobs, bool) and jobs > 0:
            return jobs
        raise ValueError("jobs must be 'auto' or a positive integer")

    @staticmethod
    def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
        return (cell.package, cell.target, cell.python_minor, cell.extra_surface)


def _completion_progress(
    *,
    cell: Cell,
    result: object,
    completed: int,
    total: int,
    phase: str | None = None,
) -> ProgressEvent:
    diagnostics, process, failure, detail = _completion_payload(result)
    return ProgressEvent(
        package=cell.package,
        cell=cell,
        phase=phase or getattr(result, "phase", "complete"),
        completed=completed,
        total=total,
        message=getattr(result, "status", "FAILURE"),
        detail=detail,
        diagnostics=diagnostics,
        process=process,
        failure=failure,
    )


def _completion_payload(
    result: object,
) -> tuple[
    tuple[TyDiagnostic, ...],
    ProcessResult | None,
    FailureRecord | None,
    str,
]:
    if isinstance(result, StaticFailEvaluation):
        return result.incremental, result.ty.process, None, ""
    if isinstance(result, TestFailEvaluation):
        return (
            result.static.ty.diagnostics,
            result.test.process,
            None,
            result.test.process.diagnostic(),
        )
    if isinstance(result, PassEvaluation):
        diagnostics = result.static.ty.diagnostics
        return (
            diagnostics,
            result.static.ty.process if diagnostics else None,
            None,
            "",
        )
    if isinstance(result, ToolFailure):
        return (), result.process, None, result.process.diagnostic()
    if isinstance(result, IndeterminateEvaluation):
        return (), result.failure.process, None, result.failure.process.diagnostic()
    if isinstance(result, HighestVersionPass):
        diagnostics = result.baseline.ty.diagnostics
        return (
            diagnostics,
            result.baseline.ty.process if diagnostics else None,
            None,
            "",
        )
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        evaluation = result.evaluation
        if isinstance(evaluation, StaticFailEvaluation):
            return (
                evaluation.incremental,
                evaluation.ty.process,
                result.failure,
                "",
            )
        if isinstance(evaluation, TestFailEvaluation):
            return (
                evaluation.static.ty.diagnostics,
                evaluation.test.process,
                result.failure,
                evaluation.test.process.diagnostic(),
            )
        return (), result.failure.process, result.failure, _outcome_diagnostic(result)
    if isinstance(result, CellSuccess):
        baseline = result.static_baseline
        diagnostics = baseline.ty.diagnostics if baseline is not None else ()
        return (
            diagnostics,
            baseline.ty.process if baseline is not None and diagnostics else None,
            None,
            "",
        )
    if isinstance(result, CellSearchFailure):
        baseline = result.static_baseline
        diagnostics = baseline.ty.diagnostics
        return diagnostics, baseline.ty.process if diagnostics else None, None, ""
    if isinstance(result, CellIndeterminate):
        terminal = _terminal_failure(result)
        if terminal.process is not None:
            return (), terminal.process, terminal, terminal.process.diagnostic()
        return (
            (),
            None,
            terminal,
            terminal.detail.message if terminal.detail is not None else "",
        )
    return (), None, None, _outcome_diagnostic(result)


def _terminal_failure(result: CellIndeterminate) -> FailureRecord:
    return next(
        failure
        for failure in result.failure_records
        if failure.failure_id == result.failure_id
    )


def _outcome_diagnostic(result: object) -> str:
    if isinstance(result, ToolFailure):
        return result.process.diagnostic()
    if isinstance(result, StaticFailEvaluation):
        return result.ty.process.diagnostic()
    if isinstance(result, TestFailEvaluation):
        return result.test.process.diagnostic()
    if isinstance(result, IndeterminateEvaluation):
        return result.failure.process.diagnostic()
    if isinstance(result, CellIndeterminate):
        terminal = _terminal_failure(result)
        if terminal.process is not None:
            return terminal.process.diagnostic()
        return terminal.detail.message if terminal.detail is not None else ""
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        if result.failure.process is not None:
            return result.failure.process.diagnostic()
        if result.failure.detail is not None:
            return result.failure.detail.message
    return ""
