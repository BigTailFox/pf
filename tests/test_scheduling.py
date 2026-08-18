from __future__ import annotations

from collections.abc import Callable
from threading import Lock
import time

from pf.scheduling import ScheduledCellTask, Scheduler
from pf.schemas.project import Cell
from pf.schemas.report import CellFailure


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


def test_scheduler_limits_concurrency_and_returns_canonical_cell_order() -> None:
    lock = Lock()
    active = 0
    maximum_active = 0

    def task(cell: Cell) -> Callable[[], CellFailure]:
        def run() -> CellFailure:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return CellFailure(status="TIMEOUT", cell=cell, phase="test")

        return run

    cells = tuple(
        Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor=minor,
            extra_surface=(),
        )
        for minor in ("3.12", "3.10", "3.11")
    )
    events = Events()
    results = Scheduler().run(
        tuple(ScheduledCellTask(cell=cell, run=task(cell)) for cell in cells),
        jobs=2,
        max_duration_seconds=None,
        events=events,
    )

    assert maximum_active == 2
    assert [result.cell.python_minor for result in results] == ["3.10", "3.11", "3.12"]
    assert len(events.items) == 3


def test_scheduler_stops_starting_cells_after_total_deadline() -> None:
    cells = tuple(
        Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor=minor,
            extra_surface=(),
        )
        for minor in ("3.10", "3.11")
    )

    def task(cell: Cell) -> Callable[[], CellFailure]:
        def run() -> CellFailure:
            time.sleep(0.02)
            return CellFailure(status="TIMEOUT", cell=cell, phase="test")

        return run

    events = Events()
    results = Scheduler().run(
        tuple(ScheduledCellTask(cell=cell, run=task(cell)) for cell in cells),
        jobs=1,
        max_duration_seconds=0.005,
        events=events,
    )

    assert [result.cell.python_minor for result in results] == ["3.10", "3.11"]
    assert results[1].status == "TIMEOUT"
    assert isinstance(results[1], CellFailure)
    assert results[1].phase == "scheduler-deadline"
    assert len(events.items) == 2
