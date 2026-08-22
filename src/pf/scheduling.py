from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import os
import time
from typing import Generic, TypeVar

from pf.schemas.project import Cell


def cell_schedule_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (
        cell.package,
        cell.target,
        cell.python_minor,
        cell.extra_surface,
    )


T = TypeVar("T")


@dataclass(frozen=True)
class ScheduledCellTask(Generic[T]):
    cell: Cell
    run: Callable[[], T]
    deadline_result: Callable[[], T] | None = None


class Scheduler:
    """Bound independent cell work without learning domain outcomes."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic

    def run(
        self,
        tasks: tuple[ScheduledCellTask[T], ...],
        *,
        jobs: int | str,
        max_duration_seconds: float | None,
        on_started: Callable[[ScheduledCellTask[T]], None],
        on_completed: Callable[[ScheduledCellTask[T], T, int, int], None],
    ) -> tuple[T, ...]:
        worker_count = self._worker_count(jobs)
        deadline = (
            self._monotonic() + max_duration_seconds
            if max_duration_seconds is not None
            else None
        )
        pending = iter(tasks)
        running: dict[Future[T], ScheduledCellTask[T]] = {}
        completed_items: list[tuple[Cell, T]] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            self._fill(
                executor,
                running,
                pending,
                worker_count,
                deadline,
                on_started,
            )
            while running:
                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task = running.pop(future)
                    result = future.result()
                    completed_items.append((task.cell, result))
                    completed += 1
                    on_completed(task, result, completed, len(tasks))
                self._fill(
                    executor,
                    running,
                    pending,
                    worker_count,
                    deadline,
                    on_started,
                )

        for task in pending:
            if task.deadline_result is None:
                raise ValueError("deadline-limited cell task requires a deadline result")
            result = task.deadline_result()
            completed_items.append((task.cell, result))
            completed += 1
            on_completed(task, result, completed, len(tasks))

        return tuple(
            result
            for _, result in sorted(
                completed_items,
                key=lambda item: cell_schedule_key(item[0]),
            )
        )

    def _fill(
        self,
        executor: ThreadPoolExecutor,
        running: dict[Future[T], ScheduledCellTask[T]],
        pending: Iterator[ScheduledCellTask[T]],
        worker_count: int,
        deadline: float | None,
        on_started: Callable[[ScheduledCellTask[T]], None],
    ) -> None:
        while len(running) < worker_count:
            if deadline is not None and self._monotonic() >= deadline:
                return
            try:
                task = next(pending)
            except StopIteration:
                return
            running[executor.submit(task.run)] = task
            on_started(task)

    @staticmethod
    def _worker_count(jobs: int | str) -> int:
        if jobs == "auto":
            return max(1, os.cpu_count() or 1)
        if isinstance(jobs, int) and not isinstance(jobs, bool) and jobs > 0:
            return jobs
        raise ValueError("jobs must be 'auto' or a positive integer")
