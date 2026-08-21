from __future__ import annotations

from collections.abc import Callable
from threading import Lock
import time

from pf.failure import FailurePolicy
from pf.scheduling import ScheduledCellTask, Scheduler
from pf.schemas.evaluation import (
    CellFailureScope,
    FailureDetail,
    ProgressEvent,
    ProcessResult,
    ToolFailure,
)
from pf.schemas.project import Cell
from pf.schemas.report import CellIndeterminate


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


def cell_failure(cell: Cell, stage: str) -> CellIndeterminate:
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause="TIMEOUT",
        stage=stage,
        process=None,
        detail=FailureDetail(code="deadline", message="deadline expired"),
    )
    return CellIndeterminate(
        cell=cell,
        phase=stage,
        failure_id=failure.failure_id,
        failure_records=(failure,),
    )


def deadline_scope(cell: Cell) -> CellFailureScope:
    return CellFailureScope(
        package=cell.package,
        cell=cell,
        source_snapshot_digest="snapshot",
        evaluation_policy_identity="policy",
    )


def test_scheduler_limits_concurrency_and_returns_canonical_cell_order() -> None:
    lock = Lock()
    active = 0
    maximum_active = 0

    def task(cell: Cell) -> Callable[[], CellIndeterminate]:
        def run() -> CellIndeterminate:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return cell_failure(cell, "test")

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
    progress = [event for event in events.items if isinstance(event, ProgressEvent)]
    starts = [event for event in progress if event.phase == "start"]
    completions = [event for event in progress if event.phase != "start"]
    assert [event.cell.python_minor for event in starts] == ["3.12", "3.10", "3.11"]
    assert len(completions) == 3
    assert all(event.phase == "start" for event in progress[:3])


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

    def task(cell: Cell) -> Callable[[], CellIndeterminate]:
        def run() -> CellIndeterminate:
            time.sleep(0.02)
            return cell_failure(cell, "test")

        return run

    events = Events()
    results = Scheduler().run(
        tuple(
            ScheduledCellTask(
                cell=cell,
                run=task(cell),
                deadline_scope=deadline_scope(cell),
            )
            for cell in cells
        ),
        jobs=1,
        max_duration_seconds=0.005,
        events=events,
    )

    assert [result.cell.python_minor for result in results] == ["3.10", "3.11"]
    assert results[1].status == "CELL_INDETERMINATE"
    assert isinstance(results[1], CellIndeterminate)
    assert results[1].phase == "scheduler-deadline"
    assert results[1].failure_records[0].detail is not None
    assert (
        results[1].failure_records[0].detail.message
        == "scheduling stopped at the total deadline"
    )
    assert [
        event.phase for event in events.items if isinstance(event, ProgressEvent)
    ].count("scheduler-deadline") == 1
    deadline = next(
        event
        for event in events.items
        if isinstance(event, ProgressEvent) and event.phase == "scheduler-deadline"
    )
    assert deadline.detail == "scheduling stopped at the total deadline"


def test_scheduler_copies_process_diagnostic_onto_completion_progress() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )

    def run() -> ToolFailure:
        return ToolFailure(
            cause="BUILD_FAILURE",
            stage="install",
            process=ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="Because numpy==1.24.0 depends on wheel",
                stdout_tail="",
                stderr_tail="Because numpy==1.24.0 depends on wheel",
            ),
        )

    events = Events()
    Scheduler().run(
        (ScheduledCellTask(cell=cell, run=run),),
        jobs=1,
        max_duration_seconds=None,
        events=events,
    )
    completion = next(
        event
        for event in events.items
        if isinstance(event, ProgressEvent) and event.message != "running"
    )
    assert completion.message == "FAILURE"
    assert completion.detail == "Because numpy==1.24.0 depends on wheel"
