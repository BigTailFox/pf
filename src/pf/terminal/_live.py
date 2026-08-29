from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import copy
from datetime import timedelta
from math import ceil
from threading import RLock
from typing import Any

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.measure import Measurement
from rich.panel import Panel
from rich.progress import (
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
)
from rich.progress_bar import ProgressBar
from rich.segment import Segment
from rich.table import Column, Table
from rich.text import Text

from pf.schemas.evaluation import (
    ActivityEvent,
    BaselineDetailIdentity,
    CellCompletedEvent,
    CellContextEvent,
    CellDetailIdentity,
    CellMatrixEvent,
    CellSearchProgressEvent,
    CellStageEvent,
    DeclarationDetailIdentity,
    ProcessEvent,
    SearchFailureEvent,
    SearchProbeDetailIdentity,
    StageProgress,
    StatusEvent,
)
from pf.schemas.project import Cell, VersionPin
from pf.terminal._presentation import (
    CellPresentation,
    OutcomeKind,
    cell_identity_text,
    cell_title_text,
    escalate_outcome,
    live_cell_identity_text,
    marker_group,
    outcome_border_style,
    run_id_text,
    search_vector_text,
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

_MarkerRow = tuple[RenderableType | None, RenderableType]


class _ElasticSpace:
    """Absorb unused row width without adding a visible progress glyph."""

    def __rich_measure__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> Measurement:
        return Measurement(0, options.max_width)

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        yield Segment(" " * options.max_width)


class LiveVerificationView:
    """Own the Rich activity lifecycle and freeze completed cell presentations."""

    def __init__(
        self,
        *,
        stderr: Console,
        render_cell: Callable[[CellPresentation], tuple[RenderableType, ...] | None],
        run_id: str | None = None,
    ) -> None:
        self._stderr = stderr
        self._render_cell = render_cell
        self._lock = RLock()
        self._progress: _OrderedProgress | None = None
        self._overall_task: TaskID | None = None
        self._cell_tasks: dict[tuple[str, str, str, tuple[str, ...]], TaskID] = {}
        self._cell_context_tasks: dict[
            tuple[str, str, str, tuple[str, ...]], TaskID
        ] = {}
        self._cell_search_progress_tasks: dict[
            tuple[str, str, str, tuple[str, ...]], TaskID
        ] = {}
        self._cell_stage_tasks: dict[tuple[str, str, str, tuple[str, ...]], TaskID] = {}
        self._cell_identities: dict[
            tuple[str, str, str, tuple[str, ...]], CellDetailIdentity | None
        ] = {}
        self._cell_completed_packages: dict[
            tuple[str, str, str, tuple[str, ...]], tuple[VersionPin, ...]
        ] = {}
        self._cell_search_packages: dict[
            tuple[str, str, str, tuple[str, ...]], tuple[VersionPin, ...]
        ] = {}
        self._cell_active_search_dependencies: dict[
            tuple[str, str, str, tuple[str, ...]], str
        ] = {}
        self._render_tasks: tuple[Task, ...] = ()
        self._pending_status: StatusEvent | None = None
        self._pending_outcome: OutcomeKind | None = None
        self._search_diagnostics: list[SearchFailureEvent] = []
        self._setup_lines: list[_MarkerRow] = []
        self._setup_card_lines: tuple[_MarkerRow, ...] = ()
        self._completed_cards: list[RenderableType] = []
        self._command: str | None = None
        self._cell_matrix_active = False
        self._run_id = run_id
        self._run_id_rendered = False

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
                return
            elif isinstance(event, CellMatrixEvent):
                self._consume_matrix(event)
            elif isinstance(event, CellSearchProgressEvent):
                self._consume_search_progress(event)
            elif isinstance(event, CellContextEvent):
                self._consume_context(event)
            elif isinstance(event, CellStageEvent):
                self._consume_stage(event)
            elif isinstance(event, CellCompletedEvent):
                self._consume_completed(event)
            self._refresh_progress()

    def close(
        self,
        *,
        abandon_pending: bool = False,
        final_outcome: OutcomeKind | None = None,
    ) -> None:
        with self._lock:
            if not self._stderr.is_terminal:
                self._print_run_id()
            if abandon_pending:
                self._pending_status = None
            if final_outcome is not None:
                self._pending_outcome = final_outcome
            elif abandon_pending:
                self._pending_outcome = None
            self._finish_progress()

    def take_search_diagnostics(self) -> tuple[SearchFailureEvent, ...]:
        with self._lock:
            diagnostics = tuple(self._search_diagnostics)
            self._search_diagnostics.clear()
            return diagnostics

    def print_step(self, message: RenderableType) -> None:
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
            line = self._completed_status_row(self._pending_status, "success")
            if self._pending_status.message in _SETUP_MESSAGES:
                self._setup_lines.append(line)
            else:
                self.print_step(marker_group((line,), expand=False))
            self._pending_outcome = None
        self._pending_status = event
        self._ensure_overall(
            description=self._status_description(event),
            total=event.total,
            completed=event.completed,
        )

    def _consume_matrix(self, event: CellMatrixEvent) -> None:
        self._cell_matrix_active = True
        heading, *details = _matrix_summary_lines(
            event.cells,
            active_packages=event.active_packages,
            pinned_packages=event.pinned_packages,
        )
        heading_row: _MarkerRow = (
            Text(_ICONS["success"], style="success"),
            heading,
        )
        detail_rows: tuple[_MarkerRow, ...] = tuple((None, line) for line in details)
        if self._stderr.is_terminal:
            self._complete_pending_setup()
            self._setup_lines.append(heading_row)
            self._queue_run_id()
            self._setup_lines.extend(detail_rows)
            self._flush_setup_card()
        else:
            rows = [heading_row]
            run_id_row = self._run_id_row()
            if run_id_row is not None:
                rows.append(run_id_row)
                self._run_id_rendered = True
            rows.extend(detail_rows)
            self.print_step(marker_group(tuple(rows), expand=False))
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

    def _print_run_id(self) -> None:
        row = self._run_id_row()
        if row is None:
            return
        self._stderr.print(marker_group((row,), expand=False))
        self._run_id_rendered = True

    def _queue_run_id(self) -> None:
        row = self._run_id_row()
        if row is None:
            return
        self._setup_lines.append(row)
        self._run_id_rendered = True

    def _run_id_row(self) -> _MarkerRow | None:
        if (
            self._run_id is None
            or self._run_id_rendered
            or self._command not in {"check", "minimize", "search", "smoke"}
        ):
            return None
        return (None, run_id_text(self._run_id))

    def _consume_stage(self, event: CellStageEvent) -> None:
        if not self._stderr.is_terminal:
            return
        self._ensure_cell_task(event.cell, start=True)
        self._set_cell_stage(event.cell, event.stage, event.progress)

    def _consume_search_progress(self, event: CellSearchProgressEvent) -> None:
        key = _cell_key(event.cell)
        self._cell_search_packages[key] = event.packages
        self._cell_completed_packages[key] = event.completed_packages
        if not self._stderr.is_terminal:
            return
        self._ensure_cell_task(event.cell, start=True)
        task_id = self._cell_search_progress_tasks.get(key)
        if task_id is not None and self._progress is not None:
            active_dependency = self._cell_active_search_dependencies.get(key)
            completed_names = {pin.name for pin in event.completed_packages}
            if active_dependency in completed_names:
                self._cell_active_search_dependencies.pop(key, None)
                active_dependency = None
                context_id = self._cell_context_tasks.get(key)
                if context_id is not None:
                    self._progress.update(
                        context_id,
                        description="",
                        detail_identity=None,
                    )
                self._set_cell_stage(event.cell, "", None)
            self._progress.update(
                task_id,
                description=search_vector_text(
                    event.packages,
                    event.completed_packages,
                    active_dependency=active_dependency,
                ).plain,
                packages=event.packages,
                completed_packages=event.completed_packages,
                active_dependency=active_dependency,
            )

    def _consume_context(self, event: CellContextEvent) -> None:
        key = _cell_key(event.cell)
        self._cell_identities[key] = event.detail
        active_dependency = _active_search_dependency(event.detail)
        if active_dependency is None:
            self._cell_active_search_dependencies.pop(key, None)
        else:
            self._cell_active_search_dependencies[key] = active_dependency
        if not self._stderr.is_terminal:
            return
        self._ensure_cell_task(event.cell, start=True)
        cell_id = self._cell_tasks.get(key)
        context_id = self._cell_context_tasks.get(key)
        search_progress_id = self._cell_search_progress_tasks.get(key)
        stage_id = self._cell_stage_tasks.get(key)
        if cell_id is not None and self._progress is not None:
            self._progress.update(cell_id)
        if context_id is not None and self._progress is not None:
            self._progress.update(
                context_id,
                description=(
                    cell_identity_text(event.detail).plain
                    if event.detail is not None
                    else ""
                ),
                detail_identity=event.detail,
            )
        if search_progress_id is not None and self._progress is not None:
            packages = self._cell_search_packages.get(key)
            completed_packages = self._cell_completed_packages.get(key)
            if packages is not None and completed_packages is not None:
                self._progress.update(
                    search_progress_id,
                    description=search_vector_text(
                        packages,
                        completed_packages,
                        active_dependency=active_dependency,
                    ).plain,
                    active_dependency=active_dependency,
                )
        if stage_id is not None and self._progress is not None:
            self._set_cell_stage(event.cell, "", None)

    def _consume_completed(self, event: CellCompletedEvent) -> None:
        if self._stderr.is_terminal:
            self._flush_setup_card()
            self._ensure_cell_task(event.cell, start=True)
        presentation = CellPresentation.from_completed(
            event,
            elapsed=self._cell_elapsed(event.cell),
            identity=self._cell_identities.get(_cell_key(event.cell)),
            completed_packages=self._cell_completed_packages.get(_cell_key(event.cell)),
            search_events=self._take_cell_diagnostics(event.cell),
            command=self._command,
        )
        cell_report = self._render_cell(presentation)
        if cell_report is not None:
            if self._stderr.is_terminal and self._setup_card_lines:
                self._completed_cards.extend(cell_report)
            else:
                for renderable in cell_report:
                    self.print_step(renderable)
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
        context_id = self._cell_context_tasks.pop(key, None)
        if context_id is not None:
            self._progress.remove_task(context_id)
        search_progress_id = self._cell_search_progress_tasks.pop(key, None)
        if search_progress_id is not None:
            self._progress.remove_task(search_progress_id)
        self._cell_identities.pop(key, None)
        self._cell_completed_packages.pop(key, None)
        self._cell_search_packages.pop(key, None)
        self._cell_active_search_dependencies.pop(key, None)
        self._sync_overall_running()

    def _ensure_progress(self) -> None:
        if self._progress is not None:
            return
        self._progress = _OrderedProgress(
            _IconColumn(),
            _TaskDescriptionColumn(),
            _ProgressVisualColumn(),
            _OverallCountColumn(),
            _StageRemainingColumn(),
            _DimElapsedColumn(),
            order=self._ordered_tasks,
            header=self._pinned_renderable,
            console=self._stderr,
            transient=True,
            expand=True,
            auto_refresh=True,
            refresh_per_second=20,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        stream_isatty = getattr(self._stderr.file, "isatty", None)
        if callable(stream_isatty) and stream_isatty():
            self._progress.start()

    def _refresh_progress(self) -> None:
        if self._progress is not None:
            render_tasks = []
            for task in self._current_ordered_tasks():
                render_task = copy(task)
                render_task.fields = dict(task.fields)
                render_tasks.append(render_task)
            self._render_tasks = tuple(render_tasks)
            self._progress.refresh_after_event()

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
                running=len(self._cell_tasks),
                cell_activity=self._cell_matrix_active,
            )
            return
        if total is not None:
            self._progress.update(
                self._overall_task,
                description=description,
                total=total,
                completed=completed,
                running=len(self._cell_tasks),
                cell_activity=self._cell_matrix_active,
            )
        elif description is not None:
            self._progress.update(
                self._overall_task,
                description=description,
                running=len(self._cell_tasks),
                cell_activity=self._cell_matrix_active,
            )

    def _ensure_cell_task(self, cell: Cell, *, start: bool) -> TaskID:
        self._ensure_progress()
        assert self._progress is not None
        key = _cell_key(cell)
        task_id = self._cell_tasks.get(key)
        if task_id is None:
            task_id = self._progress.add_task(
                cell_title_text(cell).plain,
                total=None,
                role="cell",
                cell=cell,
                start=start,
            )
            self._cell_tasks[key] = task_id
            self._cell_search_progress_tasks[key] = self._progress.add_task(
                "",
                total=None,
                role="cell-search-progress",
                packages=None,
                completed_packages=None,
                active_dependency=None,
                start=False,
            )
            self._cell_context_tasks[key] = self._progress.add_task(
                "",
                total=None,
                role="cell-context",
                start=False,
            )
            self._cell_stage_tasks[key] = self._progress.add_task(
                "",
                total=None,
                role="cell-stage",
                stage_progress=None,
                start=False,
            )
            self._sync_overall_running()
            return task_id
        if start:
            self._progress.start_task(task_id)
        return task_id

    def _sync_overall_running(self) -> None:
        if self._progress is None or self._overall_task is None:
            return
        self._progress.update(
            self._overall_task,
            running=len(self._cell_tasks),
            cell_activity=self._cell_matrix_active,
        )

    def _ordered_tasks(self) -> list[Task]:
        return list(self._render_tasks)

    def _current_ordered_tasks(self) -> list[Task]:
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
        context_id = self._cell_context_tasks.get(key)
        search_progress_id = self._cell_search_progress_tasks.get(key)
        details = []
        if search_progress_id is not None and search_progress_id in by_id:
            search_progress = by_id[search_progress_id]
            if search_progress.description:
                details.append(search_progress)
        if context_id is not None and context_id in by_id:
            context = by_id[context_id]
            stage = by_id.get(stage_id) if stage_id is not None else None
            if context.description and (stage is None or not stage.description):
                details.append(context)
        if stage_id is not None and stage_id in by_id:
            stage = by_id[stage_id]
            if stage.description:
                details.append(stage)
        return details

    def _set_cell_stage(
        self,
        cell: Cell,
        stage: str,
        progress: StageProgress | None,
    ) -> None:
        if self._progress is None:
            return
        key = _cell_key(cell)
        stage_id = self._cell_stage_tasks.get(key)
        if stage_id is not None:
            stage_task = next(
                task for task in self._progress.tasks if task.id == stage_id
            )
            stage_changed = stage_task.description != stage
            stage_progress = progress
            detail_identity = self._cell_identities.get(key)
            if progress is None and stage_task.description == stage:
                previous = stage_task.fields.get("stage_progress")
                if isinstance(previous, StageProgress):
                    stage_progress = previous
            if stage_changed:
                self._progress.reset(
                    stage_id,
                    start=bool(stage),
                    total=(
                        stage_progress.total if stage_progress is not None else None
                    ),
                    completed=(
                        stage_progress.completed if stage_progress is not None else 0
                    ),
                    description=stage,
                    role="cell-stage",
                    stage_progress=stage_progress,
                    detail_identity=detail_identity,
                )
                return
            if stage_progress is not None:
                self._progress.update(
                    stage_id,
                    description=stage,
                    total=stage_progress.total,
                    completed=stage_progress.completed,
                    stage_progress=stage_progress,
                    detail_identity=detail_identity,
                )
                return
            self._progress.update(
                stage_id,
                description=stage,
                stage_progress=None,
                detail_identity=detail_identity,
            )

    @staticmethod
    def _status_description(event: StatusEvent) -> str:
        if event.package:
            return f"{event.package} {event.message}"
        return event.message

    @staticmethod
    def _completed_status_row(
        event: StatusEvent,
        kind: OutcomeKind,
    ) -> _MarkerRow:
        done = _COMPLETED_STATUS.get(event.message, event.message)
        text = f"{event.package} {done}" if event.package else done
        return (Text(_ICONS[kind], style=kind), Text(text))

    def _complete_pending_setup(self) -> None:
        if (
            self._pending_status is None
            or self._pending_status.message not in _SETUP_MESSAGES
        ):
            return
        self._setup_lines.append(
            self._completed_status_row(self._pending_status, "success")
        )
        self._pending_status = None
        self._pending_outcome = None

    def _flush_setup_card(self) -> None:
        if not self._setup_lines:
            return
        lines = tuple(self._setup_lines)
        self._setup_lines.clear()
        if self._stderr.is_terminal:
            self._setup_card_lines = lines
            return
        self.print_step(marker_group(lines, expand=False))

    def _pinned_renderable(self) -> RenderableType | None:
        if not self._setup_card_lines:
            return None
        return Group(
            self._setup_card(self._setup_card_lines, border_style="dim"),
            *self._completed_cards,
        )

    @staticmethod
    def _setup_card(
        lines: tuple[_MarkerRow, ...],
        *,
        border_style: str,
    ) -> Panel:
        return Panel(
            marker_group(lines, expand=True),
            box=box.ROUNDED,
            border_style=border_style,
            padding=(0, 1),
        )

    def _persist_setup_card(self, kind: OutcomeKind) -> None:
        if not self._setup_card_lines or self._progress is None:
            return
        lines = self._setup_card_lines
        completed_cards = tuple(self._completed_cards)
        self._setup_card_lines = ()
        self._completed_cards.clear()
        self._refresh_progress()
        self._progress.print(
            Group(
                self._setup_card(lines, border_style=outcome_border_style(kind)),
                *completed_cards,
            )
        )

    def _finish_progress(self) -> None:
        if self._progress is None:
            return
        outcome = self._pending_outcome or "success"
        if (
            self._pending_status is not None
            and self._pending_status.message not in _CELL_PHASE_MESSAGES
        ):
            line = self._completed_status_row(
                self._pending_status,
                outcome,
            )
            if self._pending_status.message in _SETUP_MESSAGES:
                self._setup_lines.append(line)
            else:
                self.print_step(marker_group((line,), expand=False))
        self._queue_run_id()
        self._flush_setup_card()
        self._persist_setup_card(outcome)
        self._pending_status = None
        self._pending_outcome = None
        self._progress.stop()
        self._progress = None
        self._overall_task = None
        self._cell_tasks.clear()
        self._cell_search_progress_tasks.clear()
        self._cell_context_tasks.clear()
        self._cell_stage_tasks.clear()
        self._cell_identities.clear()
        self._cell_completed_packages.clear()
        self._render_tasks = ()
        self._setup_card_lines = ()
        self._completed_cards.clear()
        self._cell_matrix_active = False


def _python_sort_key(minor: str) -> tuple[int, ...]:
    return tuple(int(part) for part in minor.split("."))


def _format_extra_surface(surface: tuple[str, ...]) -> str:
    return "no-extra" if not surface else "+".join(surface)


def _matrix_summary_lines(
    cells: tuple[Cell, ...],
    *,
    active_packages: int,
    pinned_packages: int,
) -> tuple[Text, ...]:
    pythons = tuple(sorted({cell.python_minor for cell in cells}, key=_python_sort_key))
    platforms = ", ".join(sorted({cell.target for cell in cells}))
    surfaces = ", ".join(
        _format_extra_surface(surface)
        for surface in sorted(
            {cell.extra_surface for cell in cells},
            key=lambda surface: (len(surface), surface),
        )
    )
    noun = "cell" if len(cells) == 1 else "cells"
    heading = f"selected {len(cells)} {noun}"
    package_noun = "package" if active_packages == 1 else "packages"
    heading += f", {active_packages} active {package_noun} ({pinned_packages} pinned)"
    python_line = Text("python: ", style="dim")
    if pythons:
        for index, python in enumerate(pythons):
            if index:
                python_line.append(", ", style="dim")
            python_line.append(python, style="dim bold")
    else:
        python_line.append("none", style="dim")
    return (
        Text(heading),
        python_line,
        Text(f"platform: {platforms or 'none'}", style="dim"),
        Text(f"extra surfaces: {surfaces or 'none'}", style="dim"),
    )


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _active_search_dependency(identity: CellDetailIdentity | None) -> str | None:
    if isinstance(identity, SearchProbeDetailIdentity):
        return identity.dependency
    return None


class _IconColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__()
        self._spinner = SpinnerColumn()

    def get_table_column(self) -> Column:
        return Column(width=1, no_wrap=True)

    def render(self, task: Task) -> RenderableType:
        role = task.fields.get("role")
        if role in {"cell-search-progress", "cell-context", "cell-stage"} or (
            role == "cell" and not task.started
        ):
            return Text()
        rendered = self._spinner.render(task)
        if not isinstance(rendered, Text):
            return Text()
        return rendered


class _TaskDescriptionColumn(TextColumn):
    def __init__(self) -> None:
        super().__init__(
            "{task.description}",
            markup=False,
            table_column=Column(overflow="fold", no_wrap=False),
        )

    def render(self, task: Task) -> Text:
        if task.fields.get("role") == "cell-search-progress":
            packages = task.fields.get("packages")
            completed_packages = task.fields.get("completed_packages")
            active_dependency = task.fields.get("active_dependency")
            if isinstance(packages, tuple) and isinstance(completed_packages, tuple):
                return search_vector_text(
                    packages,
                    completed_packages,
                    active_dependency=(
                        active_dependency
                        if isinstance(active_dependency, str)
                        else None
                    ),
                )
        if task.fields.get("role") == "cell-context":
            identity = task.fields.get("detail_identity")
            if isinstance(
                identity,
                BaselineDetailIdentity
                | DeclarationDetailIdentity
                | SearchProbeDetailIdentity,
            ):
                return live_cell_identity_text(identity)
        if task.fields.get("role") == "cell-stage" and task.description:
            identity = task.fields.get("detail_identity")
            if not isinstance(
                identity,
                BaselineDetailIdentity
                | DeclarationDetailIdentity
                | SearchProbeDetailIdentity,
            ):
                identity = None
            return live_cell_identity_text(identity, stage=task.description)
        rendered = super().render(task)
        role = task.fields.get("role")
        if role == "cell-stage":
            rendered.stylize("dim")
        elif (
            role == "overall"
            and task.total is not None
            and task.fields.get("cell_activity") is True
        ):
            running = task.fields.get("running")
            if isinstance(running, int):
                left = max(0, int(task.total - task.completed - running))
                rendered.append(" · ", style="dim")
                rendered.append(str(running), style="dim bold")
                rendered.append(" running · ", style="dim")
                rendered.append(str(int(task.completed)), style="dim bold")
                rendered.append(" finished · ", style="dim")
                rendered.append(str(left), style="dim bold")
                rendered.append(" left", style="dim")
        elif role == "cell":
            cell = task.fields.get("cell")
            if isinstance(cell, Cell):
                return cell_title_text(cell)
        return rendered


class _ProgressVisualColumn(ProgressColumn):
    def get_table_column(self) -> Column:
        return Column(ratio=1, overflow="fold")

    def render(self, task: Task) -> RenderableType:
        if task.fields.get("role") == "overall":
            return _ElasticSpace()
        stage_progress = task.fields.get("stage_progress")
        if task.fields.get("role") == "cell-stage" and isinstance(
            stage_progress, StageProgress
        ):
            if stage_progress.total == 0:
                return Text()
            return ProgressBar(
                total=stage_progress.total,
                completed=stage_progress.completed,
                style="bar.back",
                complete_style="bar.complete",
                finished_style="bar.finished",
            )
        if task.fields.get("role") == "cell":
            return _ElasticSpace()
        return Text()


class _OverallCountColumn(MofNCompleteColumn):
    def render(self, task: Task) -> Text:
        if (
            task.fields.get("role") == "overall"
            and task.total is not None
            and task.fields.get("cell_activity") is not True
        ):
            rendered = super().render(task)
            return rendered
        stage_progress = task.fields.get("stage_progress")
        if task.fields.get("role") == "cell-stage" and isinstance(
            stage_progress, StageProgress
        ):
            rendered = Text(
                f"{stage_progress.completed}/{stage_progress.total}",
                style="dim",
            )
            return rendered
        return Text()


class _StageRemainingColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__(table_column=Column(no_wrap=True))

    def render(self, task: Task) -> Text:
        progress = task.fields.get("stage_progress")
        if task.fields.get("role") != "cell-stage" or not isinstance(
            progress,
            StageProgress,
        ):
            return Text()
        if progress.completed == 0:
            return Text("ETA --:--:--", style="dim")
        remaining = max(0, progress.total - progress.completed)
        elapsed = task.elapsed or 0.0
        estimate = ceil(elapsed * remaining / progress.completed)
        hours, remainder = divmod(estimate, 3600)
        minutes, seconds = divmod(remainder, 60)
        return Text(
            f"ETA {hours:02d}:{minutes:02d}:{seconds:02d}",
            style="dim",
        )


class _DimElapsedColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__(table_column=Column(no_wrap=True))

    def render(self, task: Task) -> Text:
        if (
            task.fields.get("role")
            in {"cell-search-progress", "cell-context", "cell-stage"}
            or task.elapsed is None
        ):
            return Text()
        elapsed = str(timedelta(seconds=max(0, int(task.elapsed))))
        return Text(elapsed, style="dim cyan")


class _OrderedProgress(Progress):
    def __init__(
        self,
        *columns: str | ProgressColumn,
        order: Callable[[], list[Task]],
        header: Callable[[], RenderableType | None],
        **kwargs: Any,
    ) -> None:
        self._task_order = order
        self._header = header
        super().__init__(*columns, **kwargs)

    def refresh(self) -> None:
        """Defer implicit task refreshes until the activity event is complete."""

    def refresh_after_event(self) -> None:
        super().refresh()

    def get_renderables(self):
        header = self._header()
        tasks = self._task_order()
        if not tasks and header is None:
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
        renderables: list[RenderableType] = []
        if header is not None:
            renderables.append(header)
        renderables.extend(
            Panel(
                Group(*(self.make_tasks_table((task,)) for task in group)),
                box=box.ROUNDED,
                border_style="dim",
                padding=(0, 1),
            )
            for group in cell_groups
        )
        if overall:
            renderables.append(self.make_tasks_table(overall))
        if renderables:
            yield Group(*renderables)

    def make_tasks_table(self, tasks: Iterable[Task]) -> Table:
        task_values = tuple(tasks)
        if len(task_values) != 1:
            return super().make_tasks_table(task_values)
        task = task_values[0]
        columns = self._columns_for(task)
        table = Table.grid(
            *(
                Column(no_wrap=True)
                if isinstance(column, str)
                else column.get_table_column().copy()
                for column in columns
            ),
            padding=(0, 2),
            expand=self._expands_for(task),
        )
        table.add_row(
            *(
                column.format(task=task) if isinstance(column, str) else column(task)
                for column in columns
            )
        )
        return table

    def _columns_for(self, task: Task) -> tuple[str | ProgressColumn, ...]:
        role = task.fields.get("role")
        if role == "cell":
            return tuple(
                column
                for column in self.columns
                if not isinstance(column, _OverallCountColumn | _StageRemainingColumn)
                and (
                    task.elapsed is not None
                    or not isinstance(column, _DimElapsedColumn)
                )
            )
        if role in {"cell-search-progress", "cell-context"}:
            return tuple(
                column
                for column in self.columns
                if isinstance(column, _IconColumn | _TaskDescriptionColumn)
            )
        if role == "cell-stage":
            has_progress = isinstance(task.fields.get("stage_progress"), StageProgress)
            return tuple(
                column
                for column in self.columns
                if isinstance(column, _IconColumn | _TaskDescriptionColumn)
                or has_progress
                and isinstance(
                    column,
                    _ProgressVisualColumn | _OverallCountColumn | _StageRemainingColumn,
                )
            )
        if role == "overall":
            return tuple(
                column
                for column in self.columns
                if not isinstance(column, _StageRemainingColumn)
                and (
                    task.fields.get("cell_activity") is not True
                    or not isinstance(column, _OverallCountColumn)
                )
            )
        return tuple(self.columns)

    @staticmethod
    def _expands_for(task: Task) -> bool:
        role = task.fields.get("role")
        return role in {"cell", "overall"} or (
            role == "cell-stage"
            and isinstance(task.fields.get("stage_progress"), StageProgress)
        )
