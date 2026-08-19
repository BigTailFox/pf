from __future__ import annotations

import sys
from threading import Lock

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
    CheckResult,
    IndeterminateEvaluation,
    ProcessEvent,
    ProgressEvent,
    StatusEvent,
    ToolFailure,
)
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


class _ActivityColumn(ProgressColumn):
    """Show a spinner until total is known, then a determinate bar."""

    def __init__(self) -> None:
        super().__init__()
        self._spinner = SpinnerColumn()
        self._bar = BarColumn(bar_width=None)

    def render(self, task: Task) -> object:
        if task.total is None:
            return self._spinner.render(task)
        return self._bar.render(task)


class _CountIfKnownColumn(MofNCompleteColumn):
    def render(self, task: Task) -> Text:
        if task.total is None:
            return Text()
        return super().render(task)

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
        self._process_tasks: dict[int, TaskID] = {}

    def render_error(self, error: PfError) -> int:
        self.close()
        message = Text.assemble(
            (f"{error.category}: ", "failure"),
            str(error),
        )
        self.stderr.print(message, soft_wrap=True)
        if error.detail:
            self.stderr.print(error.detail, soft_wrap=True)
        return int(error.exit_code)

    def render_check(self, result: CheckResult) -> int:
        self.close()
        if result.status == "PASS":
            self.stdout.print(f"check passed ({len(result.evaluations)} cells)")
            return 0
        if result.status == "COMPATIBILITY_FAILED":
            self.stderr.print("check failed: current declarations are incompatible")
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
            else:
                self._consume_progress(event)

    def close(self) -> None:
        with self._lock:
            self._finish_progress()

    def _print_tool_failure(self, heading: str, failure: ToolFailure) -> None:
        self.stderr.print(f"{heading} ({failure.stage})", soft_wrap=True)
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
                self.stderr.print(heading, soft_wrap=True)
                if result.detail:
                    self.stderr.print(result.detail, soft_wrap=True)
            printed = True
        if printed:
            return
        infra_reasons = tuple(
            reason for reason in report.result.reasons if reason in _INFRA_REASONS
        )
        if infra_reasons:
            self.stderr.print(", ".join(infra_reasons), soft_wrap=True)

    def _consume_status(self, event: StatusEvent) -> None:
        if not self.stderr.is_terminal:
            self.stderr.print(event.message)
            return
        self._ensure_progress()
        assert self._progress is not None
        description = (
            f"{event.package} {event.message}" if event.package else event.message
        )
        if self._overall_task is None:
            self._overall_task = self._progress.add_task(
                description,
                total=event.total,
                completed=event.completed,
            )
            return
        updates: dict[str, object] = {"description": description}
        if event.total is not None:
            updates["total"] = event.total
            updates["completed"] = event.completed
        self._progress.update(self._overall_task, **updates)

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
            return
        self._ensure_progress()
        assert self._progress is not None
        if event.state == "started":
            self._process_tasks[event.process_id] = self._progress.add_task(
                command,
                total=None,
            )
            return
        task = self._process_tasks.pop(event.process_id, None)
        if task is not None:
            self._progress.remove_task(task)

    def _consume_progress(self, event: ProgressEvent) -> None:
        description = (
            f"{event.package} {event.cell.python_minor} "
            f"{event.cell.target} {event.message}"
        )
        if not self.stderr.is_terminal:
            self.stderr.print(f"[{event.completed}/{event.total}] {description}")
            return
        self._ensure_progress()
        assert self._progress is not None
        if self._overall_task is None:
            self._overall_task = self._progress.add_task(
                description,
                total=event.total,
                completed=event.completed,
            )
        else:
            self._progress.update(
                self._overall_task,
                completed=event.completed,
                total=event.total,
                description=description,
            )
        if event.completed >= event.total and event.phase != "start":
            self._finish_progress()

    def _ensure_progress(self) -> None:
        if self._progress is not None:
            return
        self._progress = Progress(
            TextColumn("{task.description}"),
            _ActivityColumn(),
            _CountIfKnownColumn(),
            TimeElapsedColumn(),
            console=self.stderr,
            transient=True,
            expand=True,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self._progress.start()

    def _finish_progress(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._overall_task = None
            self._process_tasks.clear()

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
