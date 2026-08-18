from __future__ import annotations

import sys

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.text import Text
from rich.theme import Theme

from pf.errors import PfError
from pf.schemas.evaluation import CheckResult, ProgressEvent
from pf.schemas.report import PackageFloorReportV1, ProjectEditResult


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
        self._progress: Progress | None = None
        self._progress_task: TaskID | None = None

    def render_error(self, error: PfError) -> int:
        message = Text.assemble(
            (f"{error.category}: ", "failure"),
            str(error),
        )
        self.stderr.print(message)
        return int(error.exit_code)

    def render_check(self, result: CheckResult) -> int:
        if result.status == "PASS":
            self.stdout.print(f"check passed ({len(result.evaluations)} cells)")
            return 0
        if result.status == "COMPATIBILITY_FAILED":
            self.stderr.print("check failed: current declarations are incompatible")
            return 1
        self.stderr.print(f"check indeterminate: {result.failure.status}")
        return 4

    def render_search(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
        self._finish_progress()
        self.stdout.print(f"search completed ({len(reports)} reports)")
        reasons = {
            reason
            for report in reports
            if report.result.status == "incomplete"
            for reason in report.result.reasons
        }
        if not reasons:
            return 0
        if "BASELINE_FAILED" in reasons:
            return 1
        if reasons & {
            "UNAVAILABLE",
            "BUILD_UNAVAILABLE",
            "UNRESOLVABLE",
            "HARNESS_ERROR",
            "SOURCE_ERROR",
            "TOOL_ERROR",
            "TIMEOUT",
        }:
            return 4
        return 2

    def consume(self, event: ProgressEvent) -> None:
        if self.stderr.is_terminal:
            if self._progress is None:
                self._progress = Progress(
                    TextColumn("{task.description}"),
                    BarColumn(),
                    TextColumn("{task.completed}/{task.total}"),
                    console=self.stderr,
                    transient=True,
                )
                self._progress.start()
                self._progress_task = self._progress.add_task(
                    event.package,
                    total=event.total,
                )
            assert self._progress_task is not None
            self._progress.update(
                self._progress_task,
                completed=event.completed,
                total=event.total,
                description=(
                    f"{event.package} {event.cell.python_minor} "
                    f"{event.cell.target} {event.message}"
                ),
                refresh=True,
            )
            if event.completed >= event.total:
                self._finish_progress()
            return
        self.stderr.print(
            f"[{event.completed}/{event.total}] "
            f"{event.package} {event.cell.python_minor} "
            f"{event.cell.target} {event.message}"
        )

    def _finish_progress(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._progress_task = None

    def render_explain(self, reports: tuple[PackageFloorReportV1, ...]) -> int:
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
        self.stdout.print(
            f"merged {report.package.name} report -> {output}",
            soft_wrap=True,
        )
        return 0

    def render_apply(self, edits: tuple[ProjectEditResult, ...]) -> int:
        changed = sum(edit.changed for edit in edits)
        self.stdout.print(f"apply completed ({changed} changed)")
        return 0
