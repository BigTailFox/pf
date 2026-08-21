from __future__ import annotations

import sys
from dataclasses import dataclass
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

from pf.errors import ConfigurationError, PfError
from pf.schemas.evaluation import (
    ActivityEvent,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CellMatrixEvent,
    CheckResult,
    FailureCause,
    FailureRecord,
    HighestVersionPass,
    PassEvaluation,
    ProcessEvent,
    ProcessResult,
    ProgressEvent,
    SearchFailureEvent,
    SmokeResult,
    StaticFailEvaluation,
    StatusEvent,
    ToolFailure,
    TestFailEvaluation,
    TyDiagnostic,
)
from pf.schemas.project import Cell
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    PackageFloorReportV1,
    ProjectEditResult,
)

if TYPE_CHECKING:
    from rich.console import RenderableType
    from pf.workflow import FailureDiagnosis


PF_THEME = Theme(
    {
        "success": "green",
        "failure": "bold red",
        "warning": "yellow",
        "indeterminate": "bold yellow",
        "dim": "dim",
        "path": "cyan",
        "version": "magenta",
    }
)

_INFRA_REASONS = frozenset({"INDETERMINATE", "BASELINE_REJECTION"})

_COMPLETED_STATUS = {
    "loading project": "loaded project",
    "building snapshot": "built snapshot",
    "searching cells": "searched cells",
    "checking declarations": "checked declarations",
    "smoke testing": "smoke tested",
    "applying floors": "applied floors",
}
_CELL_PHASE_MESSAGES = frozenset(
    {
        "checking declarations",
        "searching cells",
        "smoke testing",
    }
)
_ICONS = {
    "success": "✓",
    "failure": "✗",
    "warning": "⚠",
    "indeterminate": "!",
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
_OUTCOME_RANK = {"success": 0, "warning": 1, "failure": 2, "indeterminate": 3}

OutcomeKind = Literal["success", "failure", "warning", "indeterminate"]


_FAILURE_TITLES: dict[FailureCause, str] = {
    "RESOLUTION_CONFLICT": "This version combination has conflicting dependency requirements and cannot be installed.",
    "BUILD_FAILURE": "This version combination could not be built.",
    "HARNESS_CONFLICT": "The test dependencies cannot be installed without changing the versions being checked.",
    "STATIC_REGRESSION": "This version combination introduces new type-checking diagnostics.",
    "TEST_FAILURE": "The full test command failed for this version combination.",
    "SOURCE_FAILURE": "PF could not reach or read a configured package source.",
    "ENVIRONMENT_FAILURE": "The current Python or system environment cannot run this check.",
    "TOOL_FAILURE": "PF could not complete a verification tool operation reliably.",
    "TIMEOUT": "The operation timed out, so compatibility is unknown.",
    "INTERNAL_INVARIANT": "PF detected an inconsistent verification result.",
    "NONDETERMINISTIC": "The same version combination produced conflicting results.",
}

_FAILURE_NEXT_STEPS: dict[FailureCause, str] = {
    "RESOLUTION_CONFLICT": "Review the conflicting requirements, adjust project constraints if needed, then rerun PF.",
    "BUILD_FAILURE": "Inspect the build details and log; check build requirements, Python support, and available artifacts.",
    "HARNESS_CONFLICT": "Adjust the configured test dependencies so they preserve the dependency graph under test.",
    "STATIC_REGRESSION": "Review the new diagnostics and decide whether to fix the code or keep a higher dependency floor.",
    "TEST_FAILURE": "Review the failing test summary and log before changing code or dependency constraints.",
    "SOURCE_FAILURE": "Check the index URL, network, credentials, and source availability, then rerun PF.",
    "ENVIRONMENT_FAILURE": "Verify the interpreter, platform support, permissions, and required system tools.",
    "TOOL_FAILURE": "Inspect the technical details and log; verify that the named tool can run in this environment.",
    "TIMEOUT": "Inspect the log and increase the relevant timeout only if the operation is expected to finish.",
    "INTERNAL_INVARIANT": "Keep the failure ID and technical details when reporting the problem; do not trust this cell result.",
    "NONDETERMINISTIC": "Stabilize flaky tests or external inputs, then rerun the full search.",
}


@dataclass(frozen=True)
class FailurePresentation:
    title: str
    impact: str
    next_step: str
    failure_id: str
    technical_code: str


class ProcessLogReferences(Protocol):
    def reference_for(self, result: ProcessResult) -> Path | None: ...


def _outcome_kind(status: str) -> OutcomeKind | None:
    if status in _IN_PROGRESS_STATUSES:
        return None
    if status in _SUCCESS_STATUSES:
        return "success"
    if status in _WARNING_STATUSES:
        return "warning"
    if status in {"CELL_INDETERMINATE", "BASELINE_INDETERMINATE"}:
        return "indeterminate"
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
        return "no-extra"
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
        f"[py{cell.python_minor}][{cell.target}]"
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
    return f"{location} [{diagnostic.code}] {_single_line_summary(diagnostic.message)}"


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "0:00:00"
    return str(timedelta(seconds=max(0, int(seconds))))


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
        self._emitted_cell_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()
        self._pending_status: StatusEvent | None = None
        self._pending_outcome: OutcomeKind | None = None
        self._search_diagnostics: list[SearchFailureEvent] = []

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
        if isinstance(error, ConfigurationError) and error.candidates:
            shown = error.candidates[:10]
            remainder = len(error.candidates) - len(shown)
            suffix = f", ... and {remainder} more" if remainder else ""
            self.stderr.print(
                f"Known packages: {', '.join(shown)}{suffix}",
                soft_wrap=True,
            )
        return int(error.exit_code)

    def render_check(self, result: CheckResult) -> int:
        self.close()
        self._render_check_evaluations(result.evaluations)
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
            f"check indeterminate: {_FAILURE_TITLES[result.failure.cause]}",
            result.failure,
        )
        return 4

    def render_smoke(self, result: SmokeResult) -> int:
        self.close()
        for outcome in result.outcomes:
            if isinstance(outcome, HighestVersionPass):
                diagnostics = outcome.baseline.ty.diagnostics
                if diagnostics:
                    self._print_cell_report(
                        outcome.attempt.identity.cell,
                        kind="warning",
                        diagnostics=diagnostics,
                        process=outcome.baseline.ty.process,
                    )
                continue
            kind: OutcomeKind = (
                "failure"
                if isinstance(outcome, BaselineRejection)
                else "indeterminate"
            )
            diagnostics: tuple[TyDiagnostic, ...] = ()
            process = outcome.failure.process
            detail = ""
            if isinstance(outcome.evaluation, StaticFailEvaluation):
                diagnostics = outcome.evaluation.incremental
                process = outcome.evaluation.ty.process
            elif isinstance(outcome.evaluation, TestFailEvaluation):
                diagnostics = outcome.evaluation.static.ty.diagnostics
                process = outcome.evaluation.test.process
            self._print_cell_report(
                outcome.cell,
                kind=kind,
                diagnostics=diagnostics,
                detail=detail,
                process=process,
                failure=outcome.failure,
            )
        if result.status == "PASS":
            self._print_outcome(
                "success",
                f"smoke passed ({len(result.outcomes)} cells)",
                console=self.stdout,
            )
            return 0
        return 1 if result.status == "BASELINE_REJECTION" else 4

    def _render_check_evaluations(self, evaluations: tuple[object, ...]) -> None:
        for evaluation in evaluations:
            if isinstance(evaluation, StaticFailEvaluation):
                self._print_cell_report(
                    evaluation.proposal.cell,
                    kind="failure",
                    diagnostics=evaluation.incremental,
                    process=evaluation.ty.process,
                )
            elif isinstance(evaluation, TestFailEvaluation):
                self._print_cell_report(
                    evaluation.proposal.cell,
                    kind="failure",
                    diagnostics=evaluation.static.ty.diagnostics,
                    detail=evaluation.test.process.diagnostic(),
                    process=evaluation.test.process,
                )
            elif isinstance(evaluation, PassEvaluation):
                diagnostics = evaluation.static.ty.diagnostics
                if not diagnostics:
                    continue
                self._print_cell_report(
                    evaluation.proposal.cell,
                    kind="warning",
                    diagnostics=diagnostics,
                    process=evaluation.static.ty.process,
                )

    def render_search(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self.close()
        self.stdout.print(f"search completed ({len(reports)} reports)")
        leftover = self._take_search_diagnostics()
        events_by_cell: dict[
            tuple[str, str, str, tuple[str, ...]], list[SearchFailureEvent]
        ] = {}
        for event in leftover:
            events_by_cell.setdefault(_cell_key(event.cell), []).append(event)
        for report in reports:
            for result in report.cell_results:
                key = _cell_key(result.cell)
                events = tuple(events_by_cell.pop(key, ()))
                baseline = result.static_baseline
                diagnostics = baseline.ty.diagnostics if baseline is not None else ()
                process = (
                    baseline.ty.process
                    if baseline is not None and diagnostics
                    else None
                )
                failures = (
                    (result.failure,)
                    if isinstance(result, (BaselineRejection, BaselineIndeterminate))
                    else result.failure_records
                    if isinstance(
                        result, (CellSuccess, CellIndeterminate, CellSearchFailure)
                    )
                    else ()
                )
                kind = _outcome_kind(result.status) or "failure"
                if diagnostics and kind == "success":
                    kind = "warning"
                self._print_cell_report(
                    result.cell,
                    kind=kind,
                    diagnostics=diagnostics,
                    process=process,
                    failures=failures,
                    search_events=events,
                )
        for events in events_by_cell.values():
            first = events[0]
            kind = (
                "failure"
                if any(event.failure.disposition == "REJECTED" for event in events)
                else "indeterminate"
            )
            self._print_cell_report(
                first.cell,
                kind=kind,
                search_events=tuple(events),
            )
        reasons = {
            reason
            for report in reports
            if report.result.status == "incomplete"
            for reason in report.result.reasons
        }
        self._print_search_outcome(reasons)
        if not reasons:
            return 0
        if "BASELINE_REJECTION" in reasons:
            return 1
        if "INDETERMINATE" in reasons:
            return 4
        return 2

    def _print_cell_report(
        self,
        cell: Cell,
        *,
        kind: OutcomeKind,
        diagnostics: tuple[TyDiagnostic, ...] = (),
        detail: str = "",
        process: ProcessResult | None = None,
        failure: FailureRecord | None = None,
        failures: tuple[FailureRecord, ...] = (),
        elapsed: float | None = None,
        search_events: tuple[SearchFailureEvent, ...] = (),
    ) -> None:
        key = _cell_key(cell)
        if key in self._emitted_cell_keys:
            return
        if diagnostics and kind == "success":
            kind = "warning"
        self._print_step(
            _cell_finished_line(
                title=_cell_title(cell),
                kind=kind,
                elapsed=elapsed,
            )
        )
        seen_identities: set[str] = set()
        for diagnostic in diagnostics:
            seen_identities.add(diagnostic.identity)
            self._print_step(
                Text(f"  {_ty_diagnostic_summary(diagnostic)}", style="dim")
            )
        diagnosed = {event.failure.failure_id for event in search_events}
        for event in search_events:
            evaluation = event.evaluation
            if isinstance(evaluation, StaticFailEvaluation):
                for diagnostic in evaluation.incremental:
                    if diagnostic.identity in seen_identities:
                        continue
                    seen_identities.add(diagnostic.identity)
                    self._print_step(
                        Text(f"  {_ty_diagnostic_summary(diagnostic)}", style="dim")
                    )
            self._print_failure_details(cell, event.failure)
            if event.failure.process is not None:
                self._print_log_reference(event.failure.process)
        records = ((failure,) if failure is not None else ()) + failures
        for record in records:
            if record.failure_id in diagnosed:
                continue
            self._print_failure_details(cell, record)
            if record.process is not None:
                self._print_log_reference(record.process)
            diagnosed.add(record.failure_id)
        if detail and failure is None and not failures and not search_events:
            self._print_step(Text(f"  {_single_line_summary(detail)}", style="dim"))
        if process is not None and not diagnosed:
            self._print_log_reference(process)
        self._emitted_cell_keys.add(key)

    def _print_failure_details(self, cell: Cell, failure: FailureRecord) -> None:
        presentation = self.failure_presentation(failure)
        self._print_step(Text(f"  {presentation.title}", style="dim"))
        self._print_step(Text(f"  {presentation.impact}", style="dim"))
        self._print_step(
            Text(
                f"  Diagnose: pf diagnose {cell.package} --failure {failure.failure_id}",
                style="dim",
            )
        )

    def consume(self, event: ActivityEvent) -> None:
        with self._lock:
            if isinstance(event, StatusEvent):
                self._consume_status(event)
            elif isinstance(event, ProcessEvent):
                self._consume_process(event)
            elif isinstance(event, SearchFailureEvent):
                self._search_diagnostics.append(event)
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
            self._print_step(Text(f"  {detail}", style="dim"))
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
        line = Text("  details: ", style="dim")
        line.append(displayed, style=f"link {resolved.as_uri()}")
        self._print_step(line)

    def _take_search_diagnostics(self) -> tuple[SearchFailureEvent, ...]:
        diagnostics = tuple(
            sorted(self._search_diagnostics, key=self._search_diagnostic_key)
        )
        self._search_diagnostics.clear()
        return diagnostics

    @staticmethod
    def _search_diagnostic_key(
        event: SearchFailureEvent,
    ) -> tuple[object, ...]:
        return (*_cell_key(event.cell), event.failure.failure_id)

    @staticmethod
    def failure_presentation(failure: FailureRecord) -> FailurePresentation:
        if not isinstance(failure.scope, AttemptFailureScope):
            impact = (
                "PF could not obtain the information needed to start or continue "
                "this cell."
            )
        elif failure.disposition == "REJECTED" and (
            failure.scope.attempt.identity.requested_resolution == "exact-vector"
        ):
            impact = (
                "This candidate did not pass the required checks. "
                "PF will continue searching."
            )
        elif failure.disposition == "REJECTED":
            impact = (
                "The highest-version baseline did not pass, so PF did not start "
                "the floor search for this cell."
            )
        elif failure.scope.attempt.identity.requested_resolution == "highest":
            impact = (
                "PF could not determine whether the highest-version baseline "
                "works, so it stopped this cell."
            )
        else:
            impact = (
                "PF could not determine whether this candidate works, so it stopped "
                "this cell."
            )
        return FailurePresentation(
            title=_FAILURE_TITLES[failure.cause],
            impact=impact,
            next_step=_FAILURE_NEXT_STEPS[failure.cause],
            failure_id=failure.failure_id,
            technical_code=f"{failure.disposition}/{failure.cause}",
        )

    def _consume_status(self, event: StatusEvent) -> None:
        if not self.stderr.is_terminal:
            self.stderr.print(event.message)
            return
        self._ensure_progress()
        assert self._progress is not None
        if (
            self._pending_status is not None
            and self._pending_status.message != event.message
            and self._pending_status.message not in _CELL_PHASE_MESSAGES
        ):
            self._print_step(
                self._completed_status_line(self._pending_status, "success")
            )
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
        self._print_step(Text.assemble((f"{_ICONS['success']} ", "success"), heading))
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
        kind = _outcome_kind(event.message)
        if kind is None and event.phase != "start":
            if not self.stderr.is_terminal:
                return
            self._ensure_cell_task(event.cell, start=True)
            self._set_cell_stage(event.cell, event.phase)
            return
        if kind is not None:
            if self.stderr.is_terminal:
                self._ensure_cell_task(event.cell, start=True)
            elapsed = self._cell_elapsed(event.cell)
            self._freeze_completed_cell(event, kind=kind, elapsed=elapsed)
            if self.stderr.is_terminal:
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
            return
        if not self.stderr.is_terminal:
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
        task_id = self._ensure_cell_task(event.cell, start=True)
        assert self._progress is not None
        self._progress.update(task_id, description=_cell_title(event.cell))

    def _freeze_completed_cell(
        self,
        event: ProgressEvent,
        *,
        kind: OutcomeKind,
        elapsed: float | None,
    ) -> None:
        key = _cell_key(event.cell)
        search_events = tuple(
            item for item in self._search_diagnostics if _cell_key(item.cell) == key
        )
        self._search_diagnostics = [
            item for item in self._search_diagnostics if _cell_key(item.cell) != key
        ]
        display_kind = "warning" if event.diagnostics and kind == "success" else kind
        self._print_cell_report(
            event.cell,
            kind=display_kind,
            diagnostics=event.diagnostics,
            detail=event.detail,
            process=event.process,
            failure=event.failure,
            elapsed=elapsed,
            search_events=search_events,
        )
        self._pending_outcome = _escalate_outcome(self._pending_outcome, display_kind)

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
            self._progress.print(message, highlight=False, soft_wrap=True)
        else:
            self.stderr.print(message, soft_wrap=True)

    def _finish_progress(self) -> None:
        if self._progress is None:
            return
        if (
            self._pending_status is not None
            and self._pending_status.message not in _CELL_PHASE_MESSAGES
        ):
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

    def render_explain(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self.close()
        if not reports:
            self.stdout.print("explained 0 reports")
            return 0
        for report in reports:
            self.stdout.print(f"{report.package.name}: {report.result.status}")
            if report.result.status == "incomplete":
                self.stdout.print(f"  reasons: {', '.join(report.result.reasons)}")
            for failure in report.failure_records:
                presentation = self.failure_presentation(failure)
                self.stdout.print(f"  {presentation.title}")
                self.stdout.print(
                    f"    Diagnose: pf diagnose {report.package.name} "
                    f"--failure {failure.failure_id}"
                )
            self._render_static_diagnostics(report)
            for projection in report.projection_evidence:
                requirements = ", ".join(projection.projected_requirements) or "none"
                self.stdout.print(f"  {projection.declaration_id}: {requirements}")
        return 0

    def render_diagnose(
        self,
        diagnoses: tuple[FailureDiagnosis, ...],
    ) -> int:
        self.close()
        if not diagnoses:
            self.stdout.print("diagnosed 0 failures")
            return 0
        for index, diagnosis in enumerate(diagnoses):
            if index:
                self.stdout.print()
            failure = diagnosis.failure
            presentation = self.failure_presentation(failure)
            scope = failure.scope
            cell = (
                scope.attempt.identity.cell
                if isinstance(scope, AttemptFailureScope)
                else scope.cell
            )
            outcome = (
                "The verification attempt was rejected"
                if failure.disposition == "REJECTED"
                else "Compatibility is unknown"
            )
            self.stdout.print(f"Failure: {failure.failure_id}")
            self.stdout.print(f"Outcome: {outcome}")
            self.stdout.print(f"What happened: {presentation.title}")
            self.stdout.print(f"Impact: {presentation.impact}")
            self.stdout.print(f"Next step: {presentation.next_step}")
            self.stdout.print()
            self.stdout.print("Context:")
            self.stdout.print(f"  package: {diagnosis.package}")
            self.stdout.print(
                "  cell: "
                f"py{cell.python_minor} / {cell.target} / "
                f"{_format_extra_surface(cell.extra_surface)}"
            )
            self.stdout.print(f"  stage: {failure.stage}")
            self.stdout.print()
            self.stdout.print("Technical details:")
            self.stdout.print(f"  disposition: {failure.disposition}")
            self.stdout.print(f"  cause: {failure.cause}")
            if isinstance(scope, AttemptFailureScope):
                identity = scope.attempt.identity
                self.stdout.print(f"  attempt: {scope.attempt.attempt_id}")
                self.stdout.print(
                    f"  requested resolution: {identity.requested_resolution}"
                )
                vector = identity.requested_managed_vector
                self.stdout.print(
                    "  requested vector: "
                    + (
                        ", ".join(f"{pin.name}=={pin.version}" for pin in vector)
                        if vector is not None
                        else "not applicable"
                    )
                )
            else:
                self.stdout.print("  attempt: not available")
                self.stdout.print("  requested vector: not applicable")
            self.stdout.print(f"  proposal: {diagnosis.proposal_id or 'not available'}")
            self.stdout.print(f"  boundary role: {diagnosis.boundary_role or 'none'}")
            if failure.detail is not None:
                self.stdout.print(f"  detail code: {failure.detail.code}")
                self.stdout.print(
                    f"  detail: {_single_line_summary(failure.detail.message)}"
                )
            if failure.process is not None:
                self.stdout.print(
                    f"  process: {self._process_terminal(failure.process)}"
                )
                summary = _single_line_summary(failure.process.diagnostic())
                if summary:
                    self.stdout.print(f"  summary: {summary}")
            if diagnosis.log_path is not None:
                resolved = (self._root / diagnosis.log_path).resolve()
                self.stdout.print(
                    Text.assemble(
                        "  log: ",
                        (
                            diagnosis.log_path.as_posix(),
                            f"link {resolved.as_uri()}",
                        ),
                    )
                )
            else:
                self.stdout.print("  Detailed local log is unavailable.")
        return 0

    @staticmethod
    def _process_terminal(process: ProcessResult) -> str:
        if process.timed_out:
            return "timed out"
        if process.start_error is not None:
            return "could not start"
        if process.signal is not None:
            return f"terminated by signal {process.signal}"
        assert process.exit_code is not None
        return f"exited {process.exit_code}"

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
                    *(
                        (result.dynamic_search,)
                        if result.dynamic_search is not None
                        else ()
                    ),
                )
            elif isinstance(result, (CellIndeterminate, CellSearchFailure)) and (
                result.coordinate_failure is not None
            ):
                searches = (result.coordinate_failure,)
            else:
                searches = ()
            seen_proposals: set[str] = set()
            for search in searches:
                for observation in search.observations:
                    evidence = observation.evidence
                    static = evidence.static_evaluation
                    if not isinstance(static, StaticFailEvaluation):
                        continue
                    if evidence.proposal_id is None:
                        raise ValueError("static evidence requires a Proposal")
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
