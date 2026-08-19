from __future__ import annotations

import sys
from datetime import timedelta
from threading import Lock
from typing import Callable, Literal

from rich.console import Console
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
from rich.text import Text
from rich.theme import Theme

from pf.errors import PfError
from pf.schemas.evaluation import (
    ActivityEvent,
    CellMatrixEvent,
    CheckResult,
    IndeterminateEvaluation,
    ProcessEvent,
    ProgressEvent,
    StatusEvent,
    ToolFailure,
)
from pf.schemas.project import Cell
from pf.schemas.report import CellFailure, PackageFloorReportV1, ProjectEditResult


PF_THEME = Theme(
    {
        "success": "green",
        "failure": "bold red",
        "warning": "yellow",
        "dim": "dim",
        "path": "cyan",
        "version": "magenta",
    }
)

_INFRA_REASONS = frozenset(
    {
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
    }
)

_COMPLETED_STATUS = {
    "loading project": "loaded project",
    "building snapshot": "built snapshot",
    "searching cells": "searched cells",
    "checking declarations": "checked declarations",
    "applying floors": "applied floors",
}

_ICONS = {
    "success": "✓",
    "failure": "✗",
    "warning": "⚠",
}

_SUCCESS_STATUSES = frozenset({"SUCCESS", "PASS"})
_WARNING_STATUSES = frozenset(
    {
        "NO_PASS_IN_SEARCH_SPACE",
        "NON_MONOTONIC",
        "NONDETERMINISTIC",
        "MISSING_CELL",
        "UNREPRESENTABLE_PROJECTION",
    }
)
_IN_PROGRESS_STATUSES = frozenset({"running"})
_OUTCOME_RANK = {"success": 0, "warning": 1, "failure": 2}

OutcomeKind = Literal["success", "failure", "warning"]


def _outcome_kind(status: str) -> OutcomeKind | None:
    if status in _IN_PROGRESS_STATUSES:
        return None
    if status in _SUCCESS_STATUSES:
        return "success"
    if status in _WARNING_STATUSES:
        return "warning"
    return "failure"


def _escalate_outcome(
    current: OutcomeKind | None, new: OutcomeKind | None
) -> OutcomeKind | None:
    if new is None:
        return current
    if current is None or _OUTCOME_RANK[new] > _OUTCOME_RANK[current]:
        return new
    return current


def _python_sort_key(minor: str) -> tuple[int, ...]:
    return tuple(int(part) for part in minor.split("."))


def _format_extra_surface(surface: tuple[str, ...]) -> str:
    if not surface:
        return "none"
    return "+".join(surface)


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


class _IconColumn(ProgressColumn):
    """Spinner while running; outcome icon when a cell is finished."""

    def __init__(self) -> None:
        super().__init__()
        self._spinner = SpinnerColumn()

    def render(self, task: Task) -> object:
        role = task.fields.get("role")
        if role == "overall":
            if task.total is None:
                return self._spinner.render(task)
            return Text()
        if role == "cell" and task.finished:
            kind = task.fields.get("kind")
            if kind in _ICONS:
                return Text(f"{_ICONS[kind]} ", style=kind)
            return Text()
        if role == "cell" and not task.started:
            return Text("  ")
        return self._spinner.render(task)


class _OverallBarColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__()
        self._bar = BarColumn(bar_width=20)

    def render(self, task: Task) -> object:
        if task.fields.get("role") == "overall" and task.total is not None:
            return self._bar.render(task)
        return Text()


class _OverallCountColumn(MofNCompleteColumn):
    def render(self, task: Task) -> Text:
        if task.fields.get("role") == "overall" and task.total is not None:
            return super().render(task)
        return Text()


class _DimElapsedColumn(TimeElapsedColumn):
    def render(self, task: Task) -> Text:
        if task.elapsed is None:
            return Text()
        rendered = super().render(task)
        rendered.stylize("dim")
        return rendered


class _CellStatusColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        status = task.fields.get("status")
        if task.fields.get("role") != "cell" or not status:
            return Text()
        kind = task.fields.get("kind")
        return Text(str(status), style=kind or "")


class _OrderedProgress(Progress):
    def __init__(self, *columns: object, order: Callable[[], list[Task]], **kwargs: object) -> None:
        self._task_order = order
        super().__init__(*columns, **kwargs)  # type: ignore[arg-type]

    def get_renderables(self):
        tasks = self._task_order()
        if tasks:
            yield self.make_tasks_table(tasks)


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _cell_title(cell: Cell) -> str:
    return (
        f"[{cell.python_minor}][{cell.target}]"
        f"[{_format_extra_surface(cell.extra_surface)}]"
    )


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "0:00:00"
    return str(timedelta(seconds=max(0, int(seconds))))


def _cell_finished_line(
    *,
    title: str,
    status: str,
    kind: OutcomeKind,
    elapsed: float | None = None,
) -> Text:
    parts: list[str | tuple[str, str]] = [(f"{_ICONS[kind]} ", kind), title]
    if elapsed is not None:
        parts.extend([" ", (_format_elapsed(elapsed), "dim")])
    parts.extend([" ", (status, kind)])
    return Text.assemble(*parts)


class TerminalPresenter:
    """Own all user-facing Rich rendering and stdout/stderr routing."""

    def __init__(
        self,
        *,
        stdout: Console | None = None,
        stderr: Console | None = None,
    ) -> None:
        self.stdout = stdout or Console(file=sys.stdout, theme=PF_THEME)
        self.stderr = stderr or Console(file=sys.stderr, theme=PF_THEME)
        self._lock = Lock()
        self._progress: Progress | None = None
        self._overall_task: TaskID | None = None
        self._cell_tasks: dict[tuple[str, str, str, tuple[str, ...]], TaskID] = {}
        self._completed_cell_keys: list[tuple[str, str, str, tuple[str, ...]]] = []
        self._frozen_cell_lines: list[Text] = []
        self._pending_status: StatusEvent | None = None
        self._pending_outcome: OutcomeKind | None = None

    def render_error(self, error: PfError) -> int:
        self.close()
        self.stderr.print(
            Text.assemble(
                (f"{_ICONS['failure']} ", "failure"),
                (f"{error.category}: ", "failure"),
                str(error),
            ),
            soft_wrap=True,
        )
        if error.detail:
            self.stderr.print(error.detail, soft_wrap=True)
        return int(error.exit_code)

    def render_check(self, result: CheckResult) -> int:
        self.close()
        if result.status == "PASS":
            self._print_outcome(
                "success",
                f"check passed ({len(result.evaluations)} cells)",
                console=self.stdout,
            )
            return 0
        if result.status == "COMPATIBILITY_FAILED":
            self._print_outcome(
                "failure",
                "check failed: current declarations are incompatible",
            )
            return 1
        self._print_tool_failure(
            f"check indeterminate: {result.failure.status}",
            result.failure,
        )
        return 4

    def render_search(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self.close()
        self.stdout.print(f"search completed ({len(reports)} reports)")
        reasons = {
            reason
            for report in reports
            if report.result.status == "incomplete"
            for reason in report.result.reasons
        }
        for report in reports:
            self._print_search_infra(report)
        self._print_search_outcome(reasons)
        if not reasons:
            return 0
        if "BASELINE_FAILED" in reasons:
            return 1
        if reasons & _INFRA_REASONS:
            return 4
        return 2

    def consume(self, event: ActivityEvent) -> None:
        with self._lock:
            if isinstance(event, StatusEvent):
                self._consume_status(event)
            elif isinstance(event, ProcessEvent):
                self._consume_process(event)
            elif isinstance(event, CellMatrixEvent):
                self._consume_matrix(event)
            else:
                self._consume_progress(event)

    def close(self) -> None:
        with self._lock:
            self._finish_progress()

    def _print_tool_failure(self, heading: str, failure: ToolFailure) -> None:
        self._print_outcome("failure", f"{heading} ({failure.stage})")
        detail = failure.process.diagnostic()
        if detail:
            self.stderr.print(detail, soft_wrap=True)

    def _print_search_infra(self, report: PackageFloorReportV1) -> None:
        if report.result.status != "incomplete":
            return
        printed = False
        for result in report.cell_results:
            if (
                not isinstance(result, CellFailure)
                or result.status not in _INFRA_REASONS
            ):
                continue
            heading = (
                f"{result.status} ({result.phase}): {result.cell.package} "
                f"{result.cell.python_minor} {result.cell.target}"
            )
            failure = result.failure
            if failure is None and isinstance(
                result.baseline, IndeterminateEvaluation
            ):
                failure = result.baseline.failure
            if failure is not None:
                self._print_tool_failure(heading, failure)
            else:
                self._print_outcome("failure", heading)
                if result.detail:
                    self.stderr.print(result.detail, soft_wrap=True)
            printed = True
        if printed:
            return
        infra_reasons = tuple(
            reason for reason in report.result.reasons if reason in _INFRA_REASONS
        )
        if infra_reasons:
            self._print_outcome("failure", ", ".join(infra_reasons))

    def _consume_status(self, event: StatusEvent) -> None:
        if not self.stderr.is_terminal:
            self.stderr.print(event.message)
            return
        self._ensure_progress()
        assert self._progress is not None
        if (
            self._pending_status is not None
            and self._pending_status.message != event.message
        ):
            self._print_step(self._completed_status_line(self._pending_status, "success"))
            self._pending_outcome = None
        self._pending_status = event
        description = self._status_description(event)
        self._ensure_overall(
            description=description,
            total=event.total,
            completed=event.completed,
        )

    def _consume_matrix(self, event: CellMatrixEvent) -> None:
        for line in _matrix_summary_lines(event.cells):
            self._print_step(line)
        if not self.stderr.is_terminal:
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

    def _consume_process(self, event: ProcessEvent) -> None:
        command = " ".join(event.argv)
        if not self.stderr.is_terminal:
            if event.state == "started":
                self.stderr.print(f"running: {command}")
                return
            duration = (
                f"{event.duration_seconds:.1f}s"
                if event.duration_seconds is not None
                else "?"
            )
            self.stderr.print(f"done ({duration}): {command}")

    def _consume_progress(self, event: ProgressEvent) -> None:
        title = _cell_title(event.cell)
        kind = _outcome_kind(event.message)
        if not self.stderr.is_terminal:
            if kind is not None:
                self.stderr.print(
                    _cell_finished_line(title=title, status=event.message, kind=kind)
                )
            return
        self._ensure_overall(
            description=(
                self._status_description(self._pending_status)
                if self._pending_status is not None
                else None
            ),
            total=event.total,
            completed=event.completed,
        )
        key = _cell_key(event.cell)
        task_id = self._ensure_cell_task(event.cell, start=True)
        assert self._progress is not None
        if kind is None:
            self._progress.update(task_id, description=title)
        else:
            self._progress.update(
                task_id,
                description=title,
                total=1,
                completed=1,
                status=event.message,
                kind=kind,
            )
            self._progress.stop_task(task_id)
            elapsed = next(
                (task.elapsed for task in self._progress.tasks if task.id == task_id),
                None,
            )
            line = _cell_finished_line(
                title=title,
                status=event.message,
                kind=kind,
                elapsed=elapsed,
            )
            if key in self._completed_cell_keys:
                index = self._completed_cell_keys.index(key)
                self._frozen_cell_lines[index] = line
            else:
                self._completed_cell_keys.append(key)
                self._frozen_cell_lines.append(line)
        self._pending_outcome = _escalate_outcome(self._pending_outcome, kind)
        if event.completed >= event.total and kind is not None:
            self._finish_progress()

    def _ensure_progress(self) -> None:
        if self._progress is not None:
            return
        self._progress = _OrderedProgress(
            _IconColumn(),
            TextColumn("{task.description}", markup=False),
            _OverallBarColumn(),
            _OverallCountColumn(),
            _DimElapsedColumn(),
            _CellStatusColumn(),
            order=self._ordered_tasks,
            console=self.stderr,
            transient=True,
            expand=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
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
        updates: dict[str, object] = {}
        if description is not None:
            updates["description"] = description
        if total is not None:
            updates["total"] = total
            updates["completed"] = completed
        if updates:
            self._progress.update(self._overall_task, **updates)

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
            return task_id
        if start:
            self._progress.start_task(task_id)
        return task_id

    def _ordered_tasks(self) -> list[Task]:
        if self._progress is None:
            return []
        by_id = {task.id: task for task in self._progress.tasks}
        ordered: list[Task] = []
        if self._overall_task is not None and self._overall_task in by_id:
            ordered.append(by_id[self._overall_task])
        seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
        for key in self._completed_cell_keys:
            task_id = self._cell_tasks.get(key)
            if task_id is not None and task_id in by_id:
                ordered.append(by_id[task_id])
                seen.add(key)
        for key, task_id in self._cell_tasks.items():
            if key not in seen and task_id in by_id:
                ordered.append(by_id[task_id])
        return ordered

    def _status_description(self, event: StatusEvent) -> str:
        if event.package:
            return f"{event.package} {event.message}"
        return event.message

    def _completed_status_line(self, event: StatusEvent, kind: OutcomeKind) -> Text:
        done = _COMPLETED_STATUS.get(event.message, event.message)
        text = f"{event.package} {done}" if event.package else done
        return Text.assemble((f"{_ICONS[kind]} ", kind), text)

    def _print_outcome(
        self,
        kind: OutcomeKind,
        message: str,
        *,
        console: Console | None = None,
    ) -> None:
        (console or self.stderr).print(
            Text.assemble((f"{_ICONS[kind]} ", kind), message),
            soft_wrap=True,
        )

    def _print_search_outcome(self, reasons: set[str]) -> None:
        remaining = tuple(
            reason for reason in sorted(reasons) if reason not in _INFRA_REASONS
        )
        if not remaining:
            return
        kind: OutcomeKind = (
            "failure"
            if any(reason not in _WARNING_STATUSES for reason in remaining)
            else "warning"
        )
        self._print_outcome(kind, ", ".join(remaining))

    def _print_step(self, message: str | Text) -> None:
        if self._progress is not None:
            self._progress.print(message, highlight=False)
        else:
            self.stderr.print(message)

    def _finish_progress(self) -> None:
        if self._progress is None:
            return
        for line in self._frozen_cell_lines:
            self._print_step(line)
        if self._pending_status is not None:
            self._print_step(
                self._completed_status_line(
                    self._pending_status, self._pending_outcome or "success"
                )
            )
            self._pending_status = None
        self._pending_outcome = None
        self._progress.stop()
        self._progress = None
        self._overall_task = None
        self._cell_tasks.clear()
        self._completed_cell_keys.clear()
        self._frozen_cell_lines.clear()

    def render_explain(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self.close()
        if not reports:
            self.stdout.print("explained 0 reports")
            return 0
        for report in reports:
            self.stdout.print(f"{report.package.name}: {report.result.status}")
            if report.result.status == "incomplete":
                self.stdout.print(f"  reasons: {', '.join(report.result.reasons)}")
            for projection in report.projection_evidence:
                requirements = ", ".join(projection.projected_requirements) or "none"
                self.stdout.print(
                    f"  {projection.declaration_id}: {requirements}"
                )
        return 0

    def render_merge(self, report: PackageFloorReportV1, output: str) -> int:
        self.close()
        self.stdout.print(
            f"merged {report.package.name} report -> {output}",
            soft_wrap=True,
        )
        return 0

    def render_apply(self, edits: tuple[ProjectEditResult, ...]) -> int:
        self.close()
        changed = sum(edit.changed for edit in edits)
        self.stdout.print(f"apply completed ({changed} changed)")
        return 0
