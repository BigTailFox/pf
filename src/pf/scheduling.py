from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import os
import time
from typing import Protocol

from pf.schemas.evaluation import ProgressEvent
from pf.schemas.project import Cell
from pf.schemas.report import CellFailure, CellResult


class ProgressConsumer(Protocol):
    def consume(self, event: ProgressEvent) -> None: ...


@dataclass(frozen=True)
class ScheduledCellTask:
    cell: Cell
    run: Callable[[], CellResult]


class Scheduler:
    """Bound independent cell work while preserving canonical result order."""

    def run(
        self,
        tasks: tuple[ScheduledCellTask, ...],
        *,
        jobs: int | str,
        max_duration_seconds: float | None,
        events: ProgressConsumer,
    ) -> tuple[CellResult, ...]:
        worker_count = self._worker_count(jobs)
        deadline = (
            time.monotonic() + max_duration_seconds
            if max_duration_seconds is not None
            else None
        )
        pending = iter(tasks)
        running: dict[Future[CellResult], ScheduledCellTask] = {}
        results: list[CellResult] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            self._fill(executor, running, pending, worker_count, deadline)
            while running:
                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task = running.pop(future)
                    result = future.result()
                    results.append(result)
                    completed += 1
                    events.consume(
                        ProgressEvent(
                            package=task.cell.package,
                            cell=task.cell,
                            phase=(
                                result.phase
                                if isinstance(result, CellFailure)
                                else "complete"
                            ),
                            completed=completed,
                            total=len(tasks),
                            message=result.status,
                        )
                    )
                self._fill(executor, running, pending, worker_count, deadline)

        for task in pending:
            result = CellFailure(
                status="TIMEOUT",
                cell=task.cell,
                phase="scheduler-deadline",
            )
            results.append(result)
            completed += 1
            events.consume(
                ProgressEvent(
                    package=task.cell.package,
                    cell=task.cell,
                    phase="scheduler-deadline",
                    completed=completed,
                    total=len(tasks),
                    message="TIMEOUT",
                )
            )

        return tuple(sorted(results, key=lambda result: self._cell_key(result.cell)))

    @staticmethod
    def _fill(
        executor: ThreadPoolExecutor,
        running: dict[Future[CellResult], ScheduledCellTask],
        pending: Iterator[ScheduledCellTask],
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
