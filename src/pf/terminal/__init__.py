from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Protocol

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
from rich.theme import Theme

from pf.errors import ConfigurationError, InvocationError, PfError
from pf.schemas.evaluation import (
    ActivityEvent,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CellMatrixEvent,
    CheckCellOutcome,
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
    TestFailEvaluation,
    ToolFailure,
    TyDiagnostic,
    VerificationRole,
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
    from pf.workflow import FailureDiagnosis


PF_THEME = Theme(
    {
        "success": "green",
        "failure": "bold red",
        "warning": "yellow",
        "indeterminate": "bold yellow",
        "dim": "dim",
        "path": "cyan",
        "cell": "bold cyan",
        "hint": "blue",
        "link": "underline cyan",
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
_SETUP_MESSAGES = frozenset({"loading project", "building snapshot"})
_ICONS = {
    "success": "✓",
    "failure": "✗",
    "warning": "⚠",
    "indeterminate": "!",
}
_BORDER_STYLES = {
    "success": "green",
    "failure": "bold red",
    "warning": "yellow",
    "indeterminate": "bold yellow",
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
_FAILED_AT = {
    "create-environment": "installing dependencies",
    "inspect-interpreter": "installing dependencies",
    "install": "installing dependencies",
    "install-project": "installing dependencies",
    "inspect": "installing dependencies",
    "install-harness": "installing harness",
    "ty": "static checking",
    "test": "testing",
}
_PROCESS_TAIL_LINES = 3
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


def _impact_for(
    failure: FailureRecord,
    *,
    role: VerificationRole | None = None,
    command: str | None = None,
) -> str:
    if not isinstance(failure.scope, AttemptFailureScope):
        return (
            "PF could not obtain the information needed to start or continue "
            "this cell."
        )
    resolved_role = role
    if resolved_role is None:
        resolution = failure.scope.attempt.identity.requested_resolution
        if resolution == "exact-vector":
            resolved_role = "probe"
        elif resolution == "lowest-direct":
            resolved_role = "declaration"
        else:
            resolved_role = "baseline"
    rejected = failure.disposition == "REJECTED"
    if resolved_role == "probe":
        return (
            "This candidate did not pass the required checks. "
            "PF will continue searching."
            if rejected
            else (
                "PF could not determine whether this candidate works, so it stopped "
                "this cell."
            )
        )
    if resolved_role == "declaration-capture":
        return (
            "PF could not capture a static baseline from the highest resolution of "
            "the current declarations, so it did not verify the declared lower "
            "bounds for this cell."
            if rejected
            else (
                "PF could not determine whether a static baseline can be captured, "
                "so it did not verify the declared lower bounds for this cell."
            )
        )
    if resolved_role == "declaration":
        return (
            "The declared lower bounds did not pass the required checks."
            if rejected
            else "PF could not determine whether the declared lower bounds work."
        )
    if command == "smoke":
        return (
            "The highest-version resolution did not pass the required checks."
            if rejected
            else "PF could not determine whether the highest-version resolution works."
        )
    return (
        "The highest-version baseline did not pass, so PF did not start "
        "the floor search for this cell."
        if rejected
        else (
            "PF could not determine whether the highest-version baseline "
            "works, so it stopped this cell."
        )
    )


class ProcessLogReferences(Protocol):
    def reference_for(self, result: ProcessResult) -> Path | None: ...


def _outcome_kind(status: str) -> OutcomeKind | None:
    if status in _IN_PROGRESS_STATUSES:
        return None
    if status in _SUCCESS_STATUSES:
        return "success"
    if status in _WARNING_STATUSES:
        return "warning"
    if status in {"CELL_INDETERMINATE", "BASELINE_INDETERMINATE", "INDETERMINATE"}:
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


def _failed_at_label(stage: str | None) -> str | None:
    if not stage:
        return None
    return _FAILED_AT.get(stage, stage.replace("-", " "))


def _process_output_tail(
    process: ProcessResult | None,
    *,
    detail: str = "",
    logs: ProcessLogReferences | None = None,
) -> tuple[str, ...]:
    text = ""
    if process is not None:
        text = process.stderr.strip() or process.stdout.strip()
        if not text:
            reader = getattr(logs, "read_output", None)
            if callable(reader):
                logged = reader(process)
                if logged:
                    text = (logged[1] or logged[0]).strip()
        if not text:
            text = process.diagnostic().strip()
    if not text:
        text = detail.strip()
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and line.strip() not in {"[]", "{}", "null"}
    ]
    return tuple(lines[-_PROCESS_TAIL_LINES:])


def _ty_diagnostic_summary(diagnostic: TyDiagnostic) -> str:
    location = diagnostic.path
    if diagnostic.line is not None:
        location += f":{diagnostic.line}"
    if diagnostic.column is not None:
        location += f":{diagnostic.column}"
    return f"{location} [{diagnostic.code}] {_single_line_summary(diagnostic.message)}"


def _counted(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _report_path(report: PackageFloorReportV1) -> str:
    parent = Path(report.package.pyproject_path).parent
    relative = Path("package-floor.json") if parent == Path(".") else parent / "package-floor.json"
    return relative.as_posix()


def _search_reasons(reports: tuple[PackageFloorReportV1, ...]) -> set[str]:
    return {
        reason
        for report in reports
        if report.result.status == "incomplete"
        for reason in report.result.reasons
    }


def _search_exit_code(reasons: set[str]) -> int:
    if "BASELINE_REJECTION" in reasons:
        return 1
    if "INDETERMINATE" in reasons:
        return 4
    if reasons:
        return 2
    return 0


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "0:00:00"
    return str(timedelta(seconds=max(0, int(seconds))))


def _cell_finished_line(
    *,
    title: str,
    kind: OutcomeKind,
    elapsed: float | None = None,
    failed_at: str | None = None,
) -> Text:
    parts: list[str | tuple[str, str]] = [
        (f"{_ICONS[kind]} ", kind),
        (title, "cell"),
    ]
    if failed_at:
        parts.extend([" failed at ", (failed_at, kind)])
    if elapsed is not None:
        parts.extend(["  " if failed_at else " ", (_format_elapsed(elapsed), "dim")])
    line = Text.assemble(*parts)
    return _fold_text(line)


def _hint_sentence(
    prefix: str,
    emphasis: str,
    suffix: str,
    *,
    emphasis_style: str = "",
) -> Text:
    line = Text("-> ", style="hint")
    line.append(prefix, style="hint")
    extra = f"hint {emphasis_style}".strip() if emphasis_style else "hint"
    line.append(emphasis, style=extra)
    line.append(suffix, style="hint")
    return _fold_text(line)


def _fold_text(text: Text) -> Text:
    text.overflow = "fold"
    text.no_wrap = False
    return text


def _unique_failures(
    search_events: tuple[SearchFailureEvent, ...],
    failure: FailureRecord | None,
    failures: tuple[FailureRecord, ...],
) -> tuple[FailureRecord, ...]:
    ordered: list[FailureRecord] = []
    seen: set[str] = set()
    for event in search_events:
        if event.failure.failure_id not in seen:
            ordered.append(event.failure)
            seen.add(event.failure.failure_id)
    for record in ((failure,) if failure is not None else ()) + failures:
        if record.failure_id not in seen:
            ordered.append(record)
            seen.add(record.failure_id)
    return tuple(ordered)


def _incremental_diagnostics(
    search_events: tuple[SearchFailureEvent, ...],
    diagnostics: tuple[TyDiagnostic, ...],
) -> tuple[TyDiagnostic, ...]:
    ordered: list[TyDiagnostic] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        if diagnostic.identity in seen:
            continue
        ordered.append(diagnostic)
        seen.add(diagnostic.identity)
    for event in search_events:
        evaluation = event.evaluation
        if not isinstance(evaluation, StaticFailEvaluation):
            continue
        for diagnostic in evaluation.incremental:
            if diagnostic.identity in seen:
                continue
            ordered.append(diagnostic)
            seen.add(diagnostic.identity)
    return tuple(ordered)


def command_usage_line(command: str | None) -> str:
    """Return the D006 Usage operands for a top-level command."""
    if command == "merge":
        return "pf merge REPORT [REPORT ...] --output PATH"
    if command:
        return f"pf {command} [OPTIONS] [PACKAGE]"
    return "pf COMMAND"


def command_usage(command: str | None) -> str:
    """Return the D006 Usage line for a top-level command."""
    return f"Usage: {command_usage_line(command)}"


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
        self._setup_lines: list[Text] = []
        self._command: str | None = None

    def bind_command(self, command: str) -> None:
        self._command = command

    def render_error(self, error: PfError) -> int:
        if isinstance(error, InvocationError) or (
            isinstance(error, ConfigurationError) and error.candidates
        ):
            return self._render_invocation(error)
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

    def _render_invocation(self, error: ConfigurationError) -> int:
        self.close(abandon_pending=True)
        self.stderr.print(f"Error: {error}")
        if error.candidates:
            shown = error.candidates[:10]
            remainder = len(error.candidates) - len(shown)
            suffix = f", ... and {remainder} more" if remainder else ""
            self.stderr.print(
                f"Known packages: {', '.join(shown)}{suffix}",
                soft_wrap=True,
            )
        command = self._command
        self.stderr.print(command_usage(command))
        help_target = f"pf {command}" if command else "pf"
        self.stderr.print(f"Try '{help_target} --help' for more information.")
        return int(error.exit_code)

    def render_check(self, result: CheckResult) -> int:
        self.close()
        if result.outcomes:
            for outcome in result.outcomes:
                self._print_check_cell_outcome(outcome)
        else:
            self._render_check_evaluations(result.evaluations)
        cell_count = len(result.outcomes) or len(result.evaluations)
        if result.status == "PASS":
            self._print_outcome(
                "success",
                f"Check passed · {_counted(cell_count, 'cell')}",
                console=self.stdout,
            )
            return 0
        if result.status == "COMPATIBILITY_FAILED":
            self._print_outcome(
                "failure",
                (
                    "Check failed · declared lower bounds are incompatible · "
                    f"{_counted(cell_count, 'cell')}"
                ),
            )
            return 1
        self._print_outcome(
            "indeterminate",
            (
                "Check indeterminate · "
                f"{_FAILURE_TITLES[result.failure.cause]} · "
                f"{_counted(cell_count, 'cell')}"
            ),
        )
        if result.failure.process is not None:
            self._print_process_detail(result.failure.process)
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
                    command="smoke",
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
                command="smoke",
            )
        if result.status == "PASS":
            self._print_outcome(
                "success",
                f"Smoke passed · {_counted(len(result.outcomes), 'cell')}",
                console=self.stdout,
            )
            return 0
        kind: OutcomeKind = (
            "failure" if result.status == "BASELINE_REJECTION" else "indeterminate"
        )
        self._print_outcome(
            kind,
            (
                "Smoke failed · highest-version resolution did not pass · "
                f"{_counted(len(result.outcomes), 'cell')}"
                if kind == "failure"
                else (
                    "Smoke indeterminate · compatibility is unknown · "
                    f"{_counted(len(result.outcomes), 'cell')}"
                )
            ),
        )
        return 1 if result.status == "BASELINE_REJECTION" else 4

    def _print_check_cell_outcome(self, outcome: CheckCellOutcome) -> None:
        evaluation = outcome.evaluation
        if outcome.status == "PASS":
            if not isinstance(evaluation, PassEvaluation):
                return
            diagnostics = evaluation.static.ty.diagnostics
            if diagnostics:
                self._print_cell_report(
                    outcome.attempt.identity.cell,
                    kind="warning",
                    diagnostics=diagnostics,
                    process=evaluation.static.ty.process,
                    role=outcome.role,
                    command="check",
                )
            return
        kind: OutcomeKind = (
            "failure" if outcome.status == "REJECTED" else "indeterminate"
        )
        diagnostics: tuple[TyDiagnostic, ...] = ()
        process = outcome.failure.process if outcome.failure is not None else None
        if isinstance(evaluation, StaticFailEvaluation):
            diagnostics = evaluation.incremental
            process = evaluation.ty.process
        elif isinstance(evaluation, TestFailEvaluation):
            diagnostics = evaluation.static.ty.diagnostics
            process = evaluation.test.process
        self._print_cell_report(
            outcome.attempt.identity.cell,
            kind=kind,
            diagnostics=diagnostics,
            process=process,
            failure=outcome.failure,
            stage=None if outcome.failure is None else outcome.failure.stage,
            role=outcome.role,
            command="check",
        )

    def _render_check_evaluations(self, evaluations: tuple[object, ...]) -> None:
        for evaluation in evaluations:
            if isinstance(evaluation, StaticFailEvaluation):
                self._print_cell_report(
                    evaluation.proposal.cell,
                    kind="failure",
                    diagnostics=evaluation.incremental,
                    process=evaluation.ty.process,
                    stage="ty",
                )
            elif isinstance(evaluation, TestFailEvaluation):
                self._print_cell_report(
                    evaluation.proposal.cell,
                    kind="failure",
                    diagnostics=evaluation.static.ty.diagnostics,
                    detail=evaluation.test.process.diagnostic(),
                    process=evaluation.test.process,
                    stage="test",
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
                    role=(
                        "baseline"
                        if isinstance(
                            result, (BaselineRejection, BaselineIndeterminate)
                        )
                        else "probe"
                    ),
                    command="search",
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
        return self._print_search_summary(reports)

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
        stage: str | None = None,
        role: VerificationRole | None = None,
        command: str | None = None,
        diagnose_available: bool = True,
    ) -> None:
        key = _cell_key(cell)
        if key in self._emitted_cell_keys:
            return
        if diagnostics and kind == "success":
            kind = "warning"
        records = _unique_failures(search_events, failure, failures)
        extra_diagnostics = _incremental_diagnostics(search_events, diagnostics)
        if kind in {"success", "warning"}:
            records = ()
            extra_diagnostics = diagnostics
        failed_at = None
        if kind in {"failure", "indeterminate"}:
            failed_at = _failed_at_label(
                records[0].stage if records else stage
            )
        lines = self._cell_result_lines(
            cell,
            kind=kind,
            elapsed=elapsed,
            failed_at=failed_at,
            records=records,
            diagnostics=extra_diagnostics,
            detail=detail,
            process=process,
            role=role,
            command=command if command is not None else self._command,
            diagnose_available=diagnose_available,
        )
        if self.stderr.is_terminal:
            self._print_step(
                Panel(
                    Group(*lines),
                    box=box.ROUNDED,
                    border_style=_BORDER_STYLES[kind],
                    padding=(0, 1),
                )
            )
        else:
            for line in lines:
                self._print_step(line)
        self._emitted_cell_keys.add(key)

    def _cell_result_lines(
        self,
        cell: Cell,
        *,
        kind: OutcomeKind,
        elapsed: float | None,
        failed_at: str | None,
        records: tuple[FailureRecord, ...],
        diagnostics: tuple[TyDiagnostic, ...],
        detail: str,
        process: ProcessResult | None,
        role: VerificationRole | None = None,
        command: str | None = None,
        diagnose_available: bool = True,
    ) -> list[Text]:
        body: list[Text] = [
            _cell_finished_line(
                title=_cell_title(cell),
                kind=kind,
                elapsed=elapsed,
                failed_at=failed_at,
            )
        ]
        if records:
            for record in records:
                presentation = self.failure_presentation(
                    record, role=role, command=command
                )
                body.append(_fold_text(Text(presentation.title)))
                body.append(_fold_text(Text(presentation.impact)))
                if diagnose_available:
                    body.append(
                        _hint_sentence(
                            "run ",
                            f"`pf diagnose {cell.package} --failure {record.failure_id}`",
                            " for more information.",
                            emphasis_style="bold",
                        )
                    )
                tail = _process_output_tail(record.process, logs=self._logs)
                if tail:
                    body.append(_fold_text(Text("\n".join(tail), style="dim")))
                if record.process is not None:
                    see = self._see_details_quote(record.process)
                    if see is not None:
                        body.append(see)
            for diagnostic in diagnostics:
                body.append(_fold_text(Text(_ty_diagnostic_summary(diagnostic))))
            return body
        for diagnostic in diagnostics:
            body.append(_fold_text(Text(_ty_diagnostic_summary(diagnostic))))
        tail = _process_output_tail(process, detail=detail, logs=self._logs)
        if tail:
            body.append(_fold_text(Text("\n".join(tail), style="dim")))
        if process is not None:
            see = self._see_details_quote(process)
            if see is not None:
                body.append(see)
        return body

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

    def _print_tool_failure(
        self,
        heading: str,
        failure: ToolFailure | FailureRecord,
    ) -> None:
        stage = _USER_STAGES.get(failure.stage, failure.stage)
        self._print_outcome("failure", f"{heading} ({stage})")
        if failure.process is not None:
            self._print_process_detail(failure.process)

    def _print_process_detail(self, process: ProcessResult) -> None:
        detail = _single_line_summary(process.diagnostic())
        if detail:
            self._print_step(Text(f"  {detail}", style="dim"))
        self._print_log_reference(process)

    def _print_log_reference(self, process: ProcessResult) -> None:
        link = self._log_link(process, indent="  ")
        if link is not None:
            self._print_step(link)

    def _log_link(self, process: ProcessResult, *, indent: str = "") -> Text | None:
        if self._logs is None:
            return None
        path = self._logs.reference_for(process)
        if path is None:
            return None
        resolved = path.resolve()
        try:
            displayed = resolved.relative_to(self._root).as_posix()
        except ValueError:
            displayed = resolved.as_posix()
        if indent:
            line = Text(f"{indent}details: ", style="dim")
            line.append(displayed, style=f"link {resolved.as_uri()}")
            return line
        line = Text("details: ")
        line.append(displayed, style=f"underline cyan link {resolved.as_uri()}")
        return line

    def _see_details_quote(self, process: ProcessResult) -> Text | None:
        if self._logs is None:
            return None
        path = self._logs.reference_for(process)
        if path is None:
            return None
        resolved = path.resolve()
        try:
            displayed = resolved.relative_to(self._root).as_posix()
        except ValueError:
            displayed = resolved.as_posix()
        return _hint_sentence(
            "see ",
            displayed,
            " for details.",
            emphasis_style=f"underline link {resolved.as_uri()}",
        )

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
    def failure_presentation(
        failure: FailureRecord,
        *,
        role: VerificationRole | None = None,
        command: str | None = None,
    ) -> FailurePresentation:
        impact = _impact_for(failure, role=role, command=command)
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
            line = self._completed_status_line(self._pending_status, "success")
            if self._pending_status.message in _SETUP_MESSAGES:
                self._setup_lines.append(line)
            else:
                self._print_step(line)
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
        heading_line = Text.assemble((f"{_ICONS['success']} ", "success"), heading)
        detail_lines = [Text(f"  {line}", style="dim") for line in details]
        if self.stderr.is_terminal:
            self._complete_pending_setup()
            self._setup_lines.append(heading_line)
            self._setup_lines.extend(detail_lines)
            self._flush_setup_card()
        else:
            self._print_step(heading_line)
            for line in detail_lines:
                self._print_step(line)
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
                self._flush_setup_card()
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
        stage = event.failure.stage if event.failure is not None else event.stage
        self._print_cell_report(
            event.cell,
            kind=display_kind,
            diagnostics=event.diagnostics,
            detail=event.detail,
            process=event.process,
            failure=event.failure,
            elapsed=elapsed,
            search_events=search_events,
            stage=stage,
            role=event.verification_role,
            command=self._command,
            diagnose_available=event.diagnose_available,
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

    def _print_search_summary(
        self,
        reports: tuple[PackageFloorReportV1, ...],
    ) -> int:
        reasons = _search_reasons(reports)
        exit_code = _search_exit_code(reasons)
        count = _counted(len(reports), "report")
        paths = tuple(_report_path(report) for report in reports)
        if exit_code == 0:
            artifact = f" · {paths[0]}" if len(paths) == 1 else ""
            if len(paths) > 1:
                for path in paths:
                    self.stdout.print(path)
            self._print_outcome(
                "success",
                f"Search complete · {count}{artifact}",
                console=self.stdout,
            )
            return 0
        if exit_code == 1:
            self._print_outcome(
                "failure",
                f"Search stopped · highest-version baseline did not pass · {count} written",
            )
            return 1
        if exit_code == 4:
            self._print_outcome(
                "indeterminate",
                f"Search stopped · compatibility is unknown · {count} written",
            )
            return 4
        self._print_outcome(
            "warning",
            f"Search incomplete · {count} written · no applicable floor",
        )
        return 2

    def render_minimize(
        self,
        reports: tuple[PackageFloorReportV1, ...],
        edits: tuple[ProjectEditResult, ...] | None,
    ) -> int:
        self.close()
        if edits is None:
            self._print_outcome(
                "warning",
                "Minimize stopped before apply · search report is incomplete",
            )
            return _search_exit_code(_search_reasons(reports)) or 2
        return self.render_apply(edits, command="minimize")

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
        if self.stderr.is_terminal:
            self._print_step(Panel(Group(*lines), box=box.ROUNDED, padding=(0, 1)))
            return
        for line in lines:
            self._print_step(line)

    def _print_step(self, message: str | Text | Panel | Group) -> None:
        printer = (
            self._progress.print if self._progress is not None else self.stderr.print
        )
        if self.stderr.is_terminal:
            printer(message, highlight=False, overflow="fold", crop=False)
            return
        printer(message, highlight=False, soft_wrap=True)

    def _finish_progress(self) -> None:
        if self._progress is None:
            return
        if (
            self._pending_status is not None
            and self._pending_status.message not in _CELL_PHASE_MESSAGES
        ):
            line = self._completed_status_line(
                self._pending_status, self._pending_outcome or "success"
            )
            if self._pending_status.message in _SETUP_MESSAGES:
                self._setup_lines.append(line)
            else:
                self._print_step(line)
        self._flush_setup_card()
        self._pending_status = None
        self._pending_outcome = None
        self._progress.stop()
        self._progress = None
        self._overall_task = None
        self._cell_tasks.clear()
        self._cell_stage_tasks.clear()

    def render_explain(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        from pf.terminal import _explain

        return _explain.render(self, reports)

    def render_diagnose(
        self,
        diagnoses: tuple[FailureDiagnosis, ...],
    ) -> int:
        from pf.terminal import _diagnose

        return _diagnose.render(self, diagnoses, root=self._root)

    def render_merge(self, report: PackageFloorReportV1, output: str) -> int:
        self.close()
        self._print_outcome(
            "success",
            f"Merged {_counted(1, 'report')} · {output}",
            console=self.stdout,
        )
        return 0

    def render_apply(
        self,
        edits: tuple[ProjectEditResult, ...],
        *,
        command: Literal["apply", "minimize"] = "apply",
    ) -> int:
        self.close()
        changed = tuple(edit for edit in edits if edit.changed)
        verb = "Minimized floors" if command == "minimize" else "Applied floors"
        if not changed:
            self._print_outcome(
                "success",
                f"{verb} · no metadata changes",
                console=self.stdout,
            )
            return 0
        path = changed[0].pyproject_path if len(changed) == 1 else ""
        artifact = f" · {path}" if path else ""
        self._print_outcome(
            "success",
            f"{verb} · {_counted(len(changed), 'project')} updated{artifact}",
            console=self.stdout,
        )
        return 0
