from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Protocol

from rich.console import Console
from rich.padding import Padding
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
from rich.theme import Theme

from pf.errors import PfError
from pf.schemas.evaluation import (
    ActivityEvent,
    CellMatrixEvent,
    CheckResult,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessEvent,
    ProcessResult,
    ProgressEvent,
    SmokeResult,
    StaticFailEvaluation,
    StatusEvent,
    ToolFailure,
    TestFailEvaluation,
    TyCheck,
    TyDiagnostic,
)
from pf.schemas.project import Cell
from pf.schemas.report import (
    CellFailure,
    CellSuccess,
    PackageFloorReportV1,
    ProjectEditResult,
)

if TYPE_CHECKING:
    from rich.console import RenderableType


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
    "smoke testing": "smoke tested",
    "applying floors": "applied floors",
}

_ICONS = {
    "success": "✓",
    "failure": "✗",
    "warning": "⚠",
}
_ICON_WIDTH = 2
_SUMMARY_WIDTH = 240

_USER_STAGES = {
    "create-environment": "install",
    "inspect-interpreter": "install",
    "install": "install",
    "inspect": "install",
    "install-harness": "harness",
    "ty": "static",
    "test": "dynamic",
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


class ProcessLogReferences(Protocol):
    def reference_for(self, result: ProcessResult) -> Path | None: ...


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


def _two_char_icon(text: Text) -> Text:
    if text.cell_len >= _ICON_WIDTH:
        return text
    padded = text.copy()
    padded.pad_right(_ICON_WIDTH - text.cell_len)
    return padded


class _IconColumn(ProgressColumn):
    """Spinner while running; outcome icon when a cell is finished."""

    def __init__(self) -> None:
        super().__init__()
        self._spinner = SpinnerColumn()

    def render(self, task: Task) -> RenderableType:
        role = task.fields.get("role")
        if role == "cell-stage" or (role == "cell" and not task.started):
            return Text(" " * _ICON_WIDTH)
        if role == "cell" and task.finished:
            kind = task.fields.get("kind")
            if kind in _ICONS:
                return _two_char_icon(Text(_ICONS[kind], style=kind))
            return Text(" " * _ICON_WIDTH)
        rendered = self._spinner.render(task)
        if isinstance(rendered, Text):
            return _two_char_icon(rendered)
        return Text(" " * _ICON_WIDTH)


class _TaskDescriptionColumn(TextColumn):
    def __init__(self) -> None:
        super().__init__("{task.description}", markup=False)

    def render(self, task: Task) -> Text:
        rendered = super().render(task)
        if task.fields.get("role") == "cell-stage":
            rendered.stylize("dim")
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
        if tasks:
            yield self.make_tasks_table(tasks)

    def make_tasks_table(self, tasks: Iterable[Task]) -> Table:
        table = super().make_tasks_table(tasks)
        table.padding = (0, 0)
        return table


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _cell_title(cell: Cell) -> str:
    return (
        f"[{cell.python_minor}][{cell.target}]"
        f"[{_format_extra_surface(cell.extra_surface)}]"
    )


def _single_line_summary(value: str) -> str:
    summary = " ".join(value.split())
    if len(summary) <= _SUMMARY_WIDTH:
        return summary
    return f"{summary[: _SUMMARY_WIDTH - 3]}..."


def _ty_diagnostic_summary(diagnostic: TyDiagnostic) -> str:
    location = diagnostic.path
    if diagnostic.line is not None:
        location += f":{diagnostic.line}"
    if diagnostic.column is not None:
        location += f":{diagnostic.column}"
    return (
        f"{location} [{diagnostic.code}] "
        f"{_single_line_summary(diagnostic.message)}"
    )


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "0:00:00"
    return str(timedelta(seconds=max(0, int(seconds))))


def _styled_reason_lines(
    status: str, diagnostic: str, kind: OutcomeKind
) -> tuple[Text, ...]:
    text = _single_line_summary(diagnostic)
    if not text:
        return (Text.assemble("  ", (status, kind)),)
    return (Text.assemble("  ", (status, kind), (f": {text}", "dim")),)


def _cell_finished_block(
    *,
    title: str,
    status: str,
    kind: OutcomeKind,
    elapsed: float | None = None,
    detail: str = "",
) -> tuple[Text, ...]:
    heading = _cell_finished_line(title=title, kind=kind, elapsed=elapsed)
    if kind == "success":
        return (heading,)
    return (heading, *_styled_reason_lines(status, detail, kind))


def _cell_finished_line(
    *,
    title: str,
    kind: OutcomeKind,
    elapsed: float | None = None,
) -> Text:
    parts: list[str | tuple[str, str]] = [(f"{_ICONS[kind]} ", kind), title]
    if elapsed is not None:
        parts.extend([" ", (_format_elapsed(elapsed), "dim")])
    return Text.assemble(*parts)


class TerminalPresenter:
    """Own all user-facing Rich rendering and stdout/stderr routing."""

    def __init__(
        self,
        *,
        stdout: Console | None = None,
        stderr: Console | None = None,
        logs: ProcessLogReferences | None = None,
        root: Path | None = None,
    ) -> None:
        self.stdout = stdout or Console(file=sys.stdout, theme=PF_THEME)
        self.stderr = stderr or Console(file=sys.stderr, theme=PF_THEME)
        self._logs = logs
        self._root = (root or Path.cwd()).resolve()
        self._lock = Lock()
        self._progress: Progress | None = None
        self._overall_task: TaskID | None = None
        self._cell_tasks: dict[tuple[str, str, str, tuple[str, ...]], TaskID] = {}
        self._cell_stage_tasks: dict[tuple[str, str, str, tuple[str, ...]], TaskID] = {}
        self._completed_cell_keys: list[tuple[str, str, str, tuple[str, ...]]] = []
        self._frozen_cell_blocks: list[tuple[Text, ...]] = []
        self._pending_status: StatusEvent | None = None
        self._pending_outcome: OutcomeKind | None = None

    def render_error(self, error: PfError) -> int:
        self.close(abandon_pending=True)
        self.stderr.print(
            Text.assemble(
                (f"{_ICONS['failure']} ", "failure"),
                (f"{error.category}: ", "failure"),
                str(error),
            ),
            soft_wrap=True,
        )
        if error.detail:
            self.stderr.print(
                Text(_single_line_summary(error.detail)),
                soft_wrap=True,
            )
        return int(error.exit_code)

    def render_check(self, result: CheckResult) -> int:
        self.close()
        self._render_evaluation_ty_summaries(result.evaluations)
        self._render_evaluation_process_failures(result.evaluations)
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

    def render_smoke(self, result: SmokeResult) -> int:
        self.close()
        self._render_evaluation_ty_summaries(result.evaluations)
        self._render_evaluation_process_failures(result.evaluations)
        if result.status == "PASS":
            self._print_outcome(
                "success",
                f"smoke passed ({len(result.evaluations)} cells)",
                console=self.stdout,
            )
            return 0
        if result.status == "TEST_FAILED":
            self._print_outcome("failure", "smoke failed: tests failed")
            return 1
        self._print_tool_failure(
            f"smoke indeterminate: {result.failure.status}",
            result.failure,
        )
        return 4

    def _render_evaluation_ty_summaries(
        self,
        evaluations: tuple[object, ...],
    ) -> None:
        for evaluation in evaluations:
            if isinstance(evaluation, StaticFailEvaluation):
                self._print_ty_diagnostics(
                    evaluation.proposal.cell,
                    evaluation.incremental,
                    kind="failure",
                    qualifier="new ",
                )
                self._print_log_reference(evaluation.ty.process)
                continue
            if not isinstance(evaluation, (PassEvaluation, TestFailEvaluation)):
                continue
            self._print_ty_warning(
                evaluation.proposal.cell,
                evaluation.static.ty,
            )

    def _render_evaluation_process_failures(
        self,
        evaluations: tuple[object, ...],
    ) -> None:
        for evaluation in evaluations:
            if not isinstance(evaluation, TestFailEvaluation):
                continue
            cell = evaluation.proposal.cell
            self._print_outcome(
                "failure",
                f"{cell.package} {_cell_title(cell)} tests failed (dynamic)",
            )
            self._print_process_detail(evaluation.test.process)

    def _print_ty_warning(self, cell: Cell, check: TyCheck) -> None:
        self._print_ty_diagnostics(
            cell,
            check.diagnostics,
            kind="warning",
        )
        if check.diagnostics:
            self._print_log_reference(check.process)

    def _print_ty_diagnostics(
        self,
        cell: Cell,
        diagnostics: tuple[TyDiagnostic, ...],
        *,
        kind: OutcomeKind,
        qualifier: str = "",
    ) -> None:
        if not diagnostics:
            return
        count = len(diagnostics)
        noun = "diagnostic" if count == 1 else "diagnostics"
        self.stderr.print(
            Text.assemble(
                (f"{_ICONS[kind]} ", kind),
                (
                    f"{cell.package} {_cell_title(cell)} ty: "
                    f"{count} {qualifier}{noun}"
                ),
            )
        )
        for diagnostic in diagnostics:
            self.stderr.print(Text(f"  {_ty_diagnostic_summary(diagnostic)}"))

    def render_search(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self.close()
        self.stdout.print(f"search completed ({len(reports)} reports)")
        for report in reports:
            for result in report.cell_results:
                if result.static_baseline is not None:
                    self._print_ty_warning(
                        result.cell,
                        result.static_baseline.ty,
                    )
                if isinstance(result, CellFailure) and isinstance(
                    result.baseline, TestFailEvaluation
                ):
                    self._render_evaluation_process_failures((result.baseline,))
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

    def close(self, *, abandon_pending: bool = False) -> None:
        with self._lock:
            if abandon_pending:
                self._pending_status = None
                self._pending_outcome = None
            self._finish_progress()

    def _print_tool_failure(self, heading: str, failure: ToolFailure) -> None:
        stage = _USER_STAGES.get(failure.stage, failure.stage)
        self._print_outcome("failure", f"{heading} ({stage})")
        self._print_process_detail(failure.process)

    def _print_process_detail(self, process: ProcessResult) -> None:
        detail = _single_line_summary(process.diagnostic())
        if detail:
            self.stderr.print(Text(f"  {detail}"), soft_wrap=True)
        self._print_log_reference(process)

    def _print_log_reference(self, process: ProcessResult) -> None:
        if self._logs is None:
            return
        path = self._logs.reference_for(process)
        if path is None:
            return
        resolved = path.resolve()
        try:
            displayed = resolved.relative_to(self._root).as_posix()
        except ValueError:
            displayed = resolved.as_posix()
        link = Text(displayed, style=f"link {resolved.as_uri()}")
        self.stderr.print(Text.assemble("  details: ", link))

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
                    self.stderr.print(
                        Text(f"  {_single_line_summary(result.detail)}"),
                        soft_wrap=True,
                    )
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
        heading, *details = _matrix_summary_lines(event.cells)
        self._print_step(
            Text.assemble((f"{_ICONS['success']} ", "success"), heading)
        )
        for line in details:
            self._print_step(Text(f"  {line}", style="dim"))
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
        return

    def _consume_progress(self, event: ProgressEvent) -> None:
        title = _cell_title(event.cell)
        kind = _outcome_kind(event.message)
        if kind is None and event.phase != "start":
            if not self.stderr.is_terminal:
                return
            self._ensure_cell_task(event.cell, start=True)
            self._set_cell_stage(event.cell, event.phase)
            return
        if not self.stderr.is_terminal:
            if kind is not None:
                for line in _cell_finished_block(
                    title=title,
                    status=event.message,
                    kind=kind,
                    detail=event.detail,
                ):
                    self.stderr.print(line)
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
            self._set_cell_stage(event.cell, "")
            self._progress.update(
                task_id,
                description=title,
                total=1,
                completed=1,
                kind=kind,
            )
            self._progress.stop_task(task_id)
            elapsed = next(
                (task.elapsed for task in self._progress.tasks if task.id == task_id),
                None,
            )
            block = _cell_finished_block(
                title=title,
                status=event.message,
                kind=kind,
                elapsed=elapsed,
                detail=event.detail,
            )
            if key in self._completed_cell_keys:
                index = self._completed_cell_keys.index(key)
                self._frozen_cell_blocks[index] = block
            else:
                self._completed_cell_keys.append(key)
                self._frozen_cell_blocks.append(block)
        self._pending_outcome = _escalate_outcome(self._pending_outcome, kind)
        if event.completed >= event.total and kind is not None:
            self._finish_progress()

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
            console=self.stderr,
            transient=True,
            expand=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        stream_isatty = getattr(self.stderr.file, "isatty", None)
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
        if self._overall_task is not None and self._overall_task in by_id:
            ordered.append(by_id[self._overall_task])
        seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
        for key in self._completed_cell_keys:
            task_id = self._cell_tasks.get(key)
            if task_id is not None and task_id in by_id:
                ordered.append(by_id[task_id])
                ordered.extend(self._stage_tasks_for(key, by_id))
                seen.add(key)
        for key, task_id in self._cell_tasks.items():
            if key not in seen and task_id in by_id:
                ordered.append(by_id[task_id])
                ordered.extend(self._stage_tasks_for(key, by_id))
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
        if not stage.description:
            return []
        return [stage]

    def _set_cell_stage(self, cell: Cell, stage: str) -> None:
        if self._progress is None:
            return
        stage_id = self._cell_stage_tasks.get(_cell_key(cell))
        if stage_id is not None:
            self._progress.update(stage_id, description=stage)

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
        for block in self._frozen_cell_blocks:
            for line in block:
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
        self._cell_stage_tasks.clear()
        self._completed_cell_keys.clear()
        self._frozen_cell_blocks.clear()

    def render_explain(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self.close()
        if not reports:
            self.stdout.print("explained 0 reports")
            return 0
        for report in reports:
            self.stdout.print(f"{report.package.name}: {report.result.status}")
            if report.result.status == "incomplete":
                self.stdout.print(f"  reasons: {', '.join(report.result.reasons)}")
            self._render_static_diagnostics(report)
            for projection in report.projection_evidence:
                requirements = ", ".join(projection.projected_requirements) or "none"
                self.stdout.print(
                    f"  {projection.declaration_id}: {requirements}"
                )
        return 0

    def _render_static_diagnostics(self, report: PackageFloorReportV1) -> None:
        for result in report.cell_results:
            baseline = result.static_baseline
            if baseline is None:
                continue
            count = len(baseline.diagnostics)
            noun = "diagnostic" if count == 1 else "diagnostics"
            self.stdout.print(
                Text(f"  {_cell_title(result.cell)} ty baseline: {count} {noun}")
            )
            if isinstance(result, CellSuccess):
                searches = (
                    result.static_search,
                    *((result.dynamic_search,) if result.dynamic_search is not None else ()),
                )
            elif result.coordinate_failure is not None:
                searches = (result.coordinate_failure,)
            else:
                searches = ()
            seen_proposals: set[str] = set()
            for search in searches:
                for observation in search.observations:
                    evidence = observation.evidence
                    static = evidence.static
                    if not isinstance(static, StaticFailEvaluation):
                        continue
                    if evidence.proposal_id in seen_proposals:
                        continue
                    seen_proposals.add(evidence.proposal_id)
                    for diagnostic in static.incremental:
                        self.stdout.print(
                            Text(f"    + {_ty_diagnostic_summary(diagnostic)}")
                        )

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
