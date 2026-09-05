from __future__ import annotations

from collections.abc import Callable
from threading import Barrier, Lock

import pytest

import pf.scheduling as scheduling
from pf.scheduling import ScheduledCellTask, Scheduler, cell_schedule_key
from pf.schemas.project import Cell


def make_cell(python_minor: str) -> Cell:
    return Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor=python_minor,
        extra_surface=(),
    )


class TestScheduler:
    @pytest.mark.parametrize("callback_error", [False, True])
    def test_error_stops_pending_dispatch(self, callback_error: bool) -> None:
        started = []
        completed = []
        error = RuntimeError("cell space cannot be evaluated")

        def run():
            if not callback_error:
                raise error
            return "done"

        def finish(task, result, count, total):
            completed.append(result)
            raise error

        with pytest.raises(RuntimeError) as caught:
            Scheduler().run(
                tuple(ScheduledCellTask(cell=make_cell(minor), run=run) for minor in ("3.10", "3.11", "3.12")),
                jobs=1, max_duration_seconds=None,
                on_started=lambda task: started.append(task.cell.python_minor), on_completed=finish,
            )
        assert caught.value is error
        assert started == ["3.10"]
        assert completed == (["done"] if callback_error else [])

    def test_error_drains_every_started_task_without_dispatching_pending(self) -> None:
        pair = Barrier(2)
        cleaned = []
        started = []

        def run(minor):
            def operation():
                try:
                    pair.wait(timeout=5)
                    raise RuntimeError(minor)
                finally:
                    cleaned.append(minor)
            return operation

        with pytest.raises(RuntimeError):
            Scheduler().run(
                tuple(ScheduledCellTask(cell=make_cell(minor), run=run(minor)) for minor in ("3.10", "3.11", "3.12")),
                jobs=2, max_duration_seconds=None,
                on_started=lambda task: started.append(task.cell.python_minor), on_completed=lambda *_: None,
            )
        assert started == ["3.10", "3.11"]
        assert sorted(cleaned) == ["3.10", "3.11"]

    def test_scheduler_finishes_started_callback_before_operation(self) -> None:
        cell = make_cell("3.10")
        timeline: list[str] = []

        def run() -> str:
            assert timeline == ["started"]
            timeline.append("operation")
            return "done"

        results = Scheduler().run(
            (ScheduledCellTask(cell=cell, run=run),),
            jobs=1,
            max_duration_seconds=None,
            on_started=lambda _task: timeline.append("started"),
            on_completed=lambda *_: timeline.append("completed"),
        )

        assert results == ("done",)
        assert timeline == ["started", "operation", "completed"]

    def test_scheduling_order_does_not_delegate_to_cell_identity(
        self,
        monkeypatch,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=("cli",),
        )
        monkeypatch.setattr(
            scheduling,
            "cell_identity",
            lambda candidate: ("changed",),
            raising=False,
        )

        assert cell_schedule_key(cell) == (
            "demo",
            "x86_64-unknown-linux-gnu",
            "3.10",
            ("cli",),
        )

    def test_scheduler_limits_concurrency_and_returns_canonical_cell_order(
        self,
    ) -> None:
        lock = Lock()
        first_pair = Barrier(2)
        entered = 0
        active = 0
        maximum_active = 0

        def task(cell: Cell) -> Callable[[], Cell]:
            def run() -> Cell:
                nonlocal active, entered, maximum_active
                with lock:
                    entered += 1
                    ordinal = entered
                    active += 1
                    maximum_active = max(maximum_active, active)
                if ordinal <= 2:
                    first_pair.wait()
                with lock:
                    active -= 1
                return cell

            return run

        cells = tuple(make_cell(minor) for minor in ("3.12", "3.10", "3.11"))
        started: list[Cell] = []
        completed: list[tuple[Cell, int, int]] = []
        results = Scheduler().run(
            tuple(ScheduledCellTask(cell=cell, run=task(cell)) for cell in cells),
            jobs=2,
            max_duration_seconds=None,
            on_started=lambda item: started.append(item.cell),
            on_completed=lambda item, _result, count, total: completed.append(
                (item.cell, count, total)
            ),
        )

        assert maximum_active == 2
        assert [result.python_minor for result in results] == ["3.10", "3.11", "3.12"]
        assert [cell.python_minor for cell in started] == ["3.12", "3.10", "3.11"]
        assert sorted(count for _, count, _ in completed) == [1, 2, 3]
        assert {total for _, _, total in completed} == {3}

    def test_scheduler_uses_clock_and_task_fallback_after_deadline(self) -> None:
        cells = tuple(make_cell(minor) for minor in ("3.10", "3.11"))
        times = iter((0.0, 0.0, 1.0))
        started: list[str] = []
        completed: list[tuple[str, str, int, int]] = []

        results = Scheduler(monotonic=lambda: next(times)).run(
            (
                ScheduledCellTask(cell=cells[0], run=lambda: "ran"),
                ScheduledCellTask(
                    cell=cells[1],
                    run=lambda: "must-not-run",
                    deadline_result=lambda: "deadline",
                ),
            ),
            jobs=1,
            max_duration_seconds=0.5,
            on_started=lambda item: started.append(item.cell.python_minor),
            on_completed=lambda item, result, count, total: completed.append(
                (item.cell.python_minor, result, count, total)
            ),
        )

        assert results == ("ran", "deadline")
        assert started == ["3.10"]
        assert completed == [
            ("3.10", "ran", 1, 2),
            ("3.11", "deadline", 2, 2),
        ]
