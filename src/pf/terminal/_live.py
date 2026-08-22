from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import RLock
from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from pf.schemas.evaluation import (
    ActivityEvent,
    CellCompletedEvent,
    CellMatrixEvent,
    CellStageEvent,
    ProcessEvent,
    SearchFailureEvent,
    StatusEvent,
)
from pf.schemas.project import Cell
from pf.terminal._presentation import (
    CellPresentation,
    OutcomeKind,
    escalate_outcome,
)


_COMPLETED_STATUS = {
    "loading project": "loaded project",
    "building snapshot": "built snapshot",
    "searching cells": "searched cells",
    "checking declarations": "checked declarations",
    "smoke testing": "smoke tested",
    "applying floors": "applied floors",
}
_CELL_PHASE_MESSAGES = frozenset(
    {"checking declarations", "searching cells", "smoke testing"}
)
_SETUP_MESSAGES = frozenset({"loading project", "building snapshot"})
_ICONS = {
    "success": "✓",
    "failure": "✗",
    "warning": "⚠",
    "indeterminate": "!",
}
_ICON_WIDTH = 2


class LiveVerificationView:
    """Own the Rich activity lifecycle and freeze completed cell presentations."""

    def __init__(
        self,
        *,
        stderr: Console,
        emit_cell: Callable[[CellPresentation], None],
    ) -> None:
        self._stderr = stderr
        self._emit_cell = emit_cell
        self._lock = RLock()
        self._progress: Progress | None = None
        self._overall_task: TaskID | None = None
        self._cell_tasks: dict[tuple[str, str, str, tuple[str, ...]], TaskID] = {}
        self._cell_stage_tasks: dict[
            tuple[str, str, str, tuple[str, ...]], TaskID
        ] = {}
        self._pending_status: StatusEvent | None = None
        self._pending_outcome: OutcomeKind | None = None
        self._search_diagnostics: list[SearchFailureEvent] = []
        self._setup_lines: list[Text] = []
        self._command: str | None = None

    def bind_command(self, command: str) -> None:
        self._command = command

    def consume(self, event: ActivityEvent) -> None:
        with self._lock:
            if isinstance(event, StatusEvent):
                self._consume_status(event)
            elif isinstance(event, ProcessEvent):
                return
            elif isinstance(event, SearchFailureEvent):
                self._search_diagnostics.append(event)
            elif isinstance(event, CellMatrixEvent):
                self._consume_matrix(event)
            elif isinstance(event, CellStageEvent):
                self._consume_stage(event)
            elif isinstance(event, CellCompletedEvent):
                self._consume_completed(event)

    def close(self, *, abandon_pending: bool = False) -> None:
        with self._lock:
            if abandon_pending:
                self._pending_status = None
                self._pending_outcome = None
            self._finish_progress()

    def take_search_diagnostics(self) -> tuple[SearchFailureEvent, ...]:
        with self._lock:
            diagnostics = tuple(
                sorted(
                    self._search_diagnostics,
                    key=lambda event: (
                        *_cell_key(event.cell),
                        event.failure.failure_id,
                    ),
                )
            )
            self._search_diagnostics.clear()
            return diagnostics

    def print_step(self, message: str | Text | Panel | Group) -> None:
        with self._lock:
            printer = (
                self._progress.print
                if self._progress is not None
                else self._stderr.print
            )
            if self._stderr.is_terminal:
                printer(message, highlight=False, overflow="fold", crop=False)
                return
            printer(message, highlight=False, soft_wrap=True)

    def _consume_status(self, event: StatusEvent) -> None:
        if not self._stderr.is_terminal:
            self._stderr.print(event.message)
            return
        self._ensure_progress()
        if (
            self._pending_status is not None
            and self._pending_status.message != event.message
            and self._pending_status.message not in _CELL_PHASE_MESSAGES
        ):
            line = self._completed_status_line(self._pending_status, "success")
            if self._pending_status.message in _SETUP_MESSAGES:
                self._setup_lines.append(line)
            else:
                self.print_step(line)
            self._pending_outcome = None
        self._pending_status = event
        self._ensure_overall(
            description=self._status_description(event),
            total=event.total,
            completed=event.completed,
        )

    def _consume_matrix(self, event: CellMatrixEvent) -> None:
        heading, *details = _matrix_summary_lines(event.cells)
        heading_line = Text.assemble((f"{_ICONS['success']} ", "success"), heading)
        detail_lines = [Text(f"  {line}", style="dim") for line in details]
        if self._stderr.is_terminal:
            self._complete_pending_setup()
            self._setup_lines.append(heading_line)
            self._setup_lines.extend(detail_lines)
            self._flush_setup_card()
        else:
            self.print_step(heading_line)
            for line in detail_lines:
                self.print_step(line)
            return
        description = (
            self._status_description(self._pending_status)
            if self._pending_status is not None
            else ""
        )
        self._ensure_overall(
            description=description,
            total=len(event.cells) or None,
            completed=0,
        )
        for cell in event.cells:
            self._ensure_cell_task(cell, start=False)

    def _consume_stage(self, event: CellStageEvent) -> None:
        if not self._stderr.is_terminal:
            return
        self._ensure_cell_task(event.cell, start=True)
        self._set_cell_stage(event.cell, event.stage)

    def _consume_completed(self, event: CellCompletedEvent) -> None:
        if self._stderr.is_terminal:
            self._flush_setup_card()
            self._ensure_cell_task(event.cell, start=True)
        presentation = CellPresentation.from_completed(
            event,
            elapsed=self._cell_elapsed(event.cell),
            search_events=self._take_cell_diagnostics(event.cell),
            command=self._command,
        )
        self._emit_cell(presentation)
        self._pending_outcome = escalate_outcome(
            self._pending_outcome,
            presentation.kind,
        )
        if self._stderr.is_terminal:
            self._remove_live_cell(event.cell)
            self._ensure_overall(
                description=(
                    self._status_description(self._pending_status)
                    if self._pending_status is not None
                    else None
                ),
                total=event.total,
                completed=event.completed,
            )
            if event.completed >= event.total:
                self._finish_progress()

    def _take_cell_diagnostics(
        self,
        cell: Cell,
    ) -> tuple[SearchFailureEvent, ...]:
        key = _cell_key(cell)
        matched = tuple(
            item for item in self._search_diagnostics if _cell_key(item.cell) == key
        )
        self._search_diagnostics = [
            item for item in self._search_diagnostics if _cell_key(item.cell) != key
        ]
        return matched

    def _cell_elapsed(self, cell: Cell) -> float | None:
        if self._progress is None:
            return None
        task_id = self._cell_tasks.get(_cell_key(cell))
        if task_id is None:
            return None
        return next(
            (task.elapsed for task in self._progress.tasks if task.id == task_id),
            None,
        )

    def _remove_live_cell(self, cell: Cell) -> None:
        if self._progress is None:
            return
        key = _cell_key(cell)
        task_id = self._cell_tasks.pop(key, None)
        if task_id is not None:
            self._progress.remove_task(task_id)
        stage_id = self._cell_stage_tasks.pop(key, None)
        if stage_id is not None:
            self._progress.remove_task(stage_id)

    def _ensure_progress(self) -> None:
        if self._progress is not None:
            return
        self._progress = _OrderedProgress(
            _IconColumn(),
            _TaskDescriptionColumn(),
            _OverallBarColumn(),
            _OverallCountColumn(),
            _DimElapsedColumn(),
            order=self._ordered_tasks,
            console=self._stderr,
            transient=True,
            expand=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        stream_isatty = getattr(self._stderr.file, "isatty", None)
        if callable(stream_isatty) and stream_isatty():
            self._progress.start()

    def _ensure_overall(
        self,
        *,
        description: str | None,
        total: int | None,
        completed: int,
    ) -> None:
        self._ensure_progress()
        assert self._progress is not None
        if self._overall_task is None:
            self._overall_task = self._progress.add_task(
                description or "",
                total=total,
                completed=completed,
                role="overall",
            )
            return
        if total is not None:
            self._progress.update(
                self._overall_task,
                description=description,
                total=total,
                completed=completed,
            )
        elif description is not None:
            self._progress.update(self._overall_task, description=description)

    def _ensure_cell_task(self, cell: Cell, *, start: bool) -> TaskID:
        self._ensure_progress()
        assert self._progress is not None
        key = _cell_key(cell)
        task_id = self._cell_tasks.get(key)
        if task_id is None:
            task_id = self._progress.add_task(
                _cell_title(cell),
                total=None,
                role="cell",
                start=start,
            )
            self._cell_tasks[key] = task_id
            self._cell_stage_tasks[key] = self._progress.add_task(
                "",
                total=None,
                role="cell-stage",
                start=False,
            )
            return task_id
        if start:
            self._progress.start_task(task_id)
        return task_id

    def _ordered_tasks(self) -> list[Task]:
        if self._progress is None:
            return []
        by_id = {task.id: task for task in self._progress.tasks}
        ordered: list[Task] = []
        for key, task_id in self._cell_tasks.items():
            if task_id in by_id:
                ordered.append(by_id[task_id])
                ordered.extend(self._stage_tasks_for(key, by_id))
        if self._overall_task is not None and self._overall_task in by_id:
            ordered.append(by_id[self._overall_task])
        return ordered

    def _stage_tasks_for(
        self,
        key: tuple[str, str, str, tuple[str, ...]],
        by_id: dict[TaskID, Task],
    ) -> list[Task]:
        stage_id = self._cell_stage_tasks.get(key)
        if stage_id is None or stage_id not in by_id:
            return []
        stage = by_id[stage_id]
        return [stage] if stage.description else []

    def _set_cell_stage(self, cell: Cell, stage: str) -> None:
        if self._progress is None:
            return
        stage_id = self._cell_stage_tasks.get(_cell_key(cell))
        if stage_id is not None:
            self._progress.update(stage_id, description=stage)

    @staticmethod
    def _status_description(event: StatusEvent) -> str:
        if event.package:
            return f"{event.package} {event.message}"
        return event.message

    @staticmethod
    def _completed_status_line(event: StatusEvent, kind: OutcomeKind) -> Text:
        done = _COMPLETED_STATUS.get(event.message, event.message)
        text = f"{event.package} {done}" if event.package else done
        return Text.assemble((f"{_ICONS[kind]} ", kind), text)

    def _complete_pending_setup(self) -> None:
        if (
            self._pending_status is None
            or self._pending_status.message not in _SETUP_MESSAGES
        ):
            return
        self._setup_lines.append(
            self._completed_status_line(self._pending_status, "success")
        )
        self._pending_status = None
        self._pending_outcome = None

    def _flush_setup_card(self) -> None:
        if not self._setup_lines:
            return
        lines = tuple(self._setup_lines)
        self._setup_lines.clear()
        if self._stderr.is_terminal:
            self.print_step(Panel(Group(*lines), box=box.ROUNDED, padding=(0, 1)))
            return
        for line in lines:
            self.print_step(line)

    def _finish_progress(self) -> None:
        if self._progress is None:
            return
        if (
            self._pending_status is not None
            and self._pending_status.message not in _CELL_PHASE_MESSAGES
        ):
            line = self._completed_status_line(
                self._pending_status,
                self._pending_outcome or "success",
            )
            if self._pending_status.message in _SETUP_MESSAGES:
                self._setup_lines.append(line)
            else:
                self.print_step(line)
        self._flush_setup_card()
        self._pending_status = None
        self._pending_outcome = None
        self._progress.stop()
        self._progress = None
        self._overall_task = None
        self._cell_tasks.clear()
        self._cell_stage_tasks.clear()


def _python_sort_key(minor: str) -> tuple[int, ...]:
    return tuple(int(part) for part in minor.split("."))


def _format_extra_surface(surface: tuple[str, ...]) -> str:
    return "no-extra" if not surface else "+".join(surface)


def _matrix_summary_lines(cells: tuple[Cell, ...]) -> tuple[str, ...]:
    pythons = ", ".join(
        sorted({cell.python_minor for cell in cells}, key=_python_sort_key)
    )
    platforms = ", ".join(sorted({cell.target for cell in cells}))
    surfaces = ", ".join(
        _format_extra_surface(surface)
        for surface in sorted(
            {cell.extra_surface for cell in cells},
            key=lambda surface: (len(surface), surface),
        )
    )
    noun = "cell" if len(cells) == 1 else "cells"
    return (
        f"selected {len(cells)} {noun}",
        f"python: {pythons or 'none'}",
        f"platform: {platforms or 'none'}",
        f"extra surfaces: {surfaces or 'none'}",
    )


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _cell_title(cell: Cell) -> str:
    return (
        f"[py{cell.python_minor}][{cell.target}]"
        f"[{_format_extra_surface(cell.extra_surface)}]"
    )


def _two_char_icon(text: Text) -> Text:
    if text.cell_len >= _ICON_WIDTH:
        return text
    padded = text.copy()
    padded.pad_right(_ICON_WIDTH - text.cell_len)
    return padded


class _IconColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__()
        self._spinner = SpinnerColumn()

    def render(self, task: Task) -> RenderableType:
        role = task.fields.get("role")
        if role == "cell-stage" or (role == "cell" and not task.started):
            return Text(" " * _ICON_WIDTH)
        rendered = self._spinner.render(task)
        return _two_char_icon(rendered) if isinstance(rendered, Text) else Text()


class _TaskDescriptionColumn(TextColumn):
    def __init__(self) -> None:
        super().__init__("{task.description}", markup=False)

    def render(self, task: Task) -> Text:
        rendered = super().render(task)
        role = task.fields.get("role")
        if role == "cell-stage":
            rendered.stylize("dim")
        elif role == "cell":
            rendered.stylize("cell")
        return rendered


class _OverallBarColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__()
        self._bar = BarColumn(bar_width=20)

    def render(self, task: Task) -> RenderableType:
        if task.fields.get("role") == "overall" and task.total is not None:
            return Padding(self._bar.render(task), (0, 0, 0, 1))
        return Text()


class _OverallCountColumn(MofNCompleteColumn):
    def render(self, task: Task) -> Text:
        if task.fields.get("role") == "overall" and task.total is not None:
            rendered = super().render(task)
            rendered.pad_left(1)
            return rendered
        return Text()


class _DimElapsedColumn(TimeElapsedColumn):
    def render(self, task: Task) -> Text:
        if task.fields.get("role") == "cell-stage" or task.elapsed is None:
            return Text()
        rendered = super().render(task)
        rendered.stylize("dim")
        rendered.pad_left(1)
        return rendered


class _OrderedProgress(Progress):
    def __init__(
        self,
        *columns: str | ProgressColumn,
        order: Callable[[], list[Task]],
        **kwargs: Any,
    ) -> None:
        self._task_order = order
        super().__init__(*columns, **kwargs)

    def get_renderables(self):
        tasks = self._task_order()
        if not tasks:
            return
        cell_groups: list[list[Task]] = []
        current: list[Task] = []
        overall: list[Task] = []
        for task in tasks:
            role = task.fields.get("role")
            if role == "overall":
                if current:
                    cell_groups.append(current)
                    current = []
                overall.append(task)
            elif role == "cell":
                if current:
                    cell_groups.append(current)
                current = [task]
            else:
                current.append(task)
        if current:
            cell_groups.append(current)
        renderables: list[RenderableType] = [
            Panel(
                self.make_tasks_table(group),
                box=box.ROUNDED,
                padding=(0, 1),
            )
            for group in cell_groups
        ]
        if overall:
            renderables.append(self.make_tasks_table(overall))
        if renderables:
            yield Group(*renderables)

    def make_tasks_table(self, tasks: Iterable[Task]) -> Table:
        table = super().make_tasks_table(tasks)
        table.padding = (0, 0)
        return table
