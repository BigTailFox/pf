from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from rich import box
from rich.console import Console, ConsoleDimensions, Group
from rich.panel import Panel
from rich.table import Column, Table
from rich.text import Text
from rich.theme import Theme

from pf.errors import ConfigurationError, InvocationError, PfError
from pf.schemas.evaluation import (
    ActivityEvent,
    AttemptFailureScope,
    CellCompletedEvent,
    CellFailed,
    CheckCellOutcome,
    CheckResult,
    DeclarationDetailIdentity,
    FailureCause,
    FailureRecord,
    PassEvaluation,
    ProcessResult,
    PytestFailureDetail,
    RuntimeInterfaceMissingEvaluation,
    SearchFailureEvent,
    SmokeResult,
    StaticIssueDetail,
    VerifierRejectedEvaluation,
    TyDiagnostic,
    VerificationRole,
)
from pf.schemas.project import Cell
from pf.report import ValidatedReport
from pf.schemas.report import ProjectEditResult
from pf.terminal._live import LiveVerificationView
from pf.terminal._presentation import (
    CellPresentation,
    OutcomeKind,
    cell_identity_text,
    cell_title_text,
    completed_packages_text,
    completion_action,
    outcome_border_style,
)

if TYPE_CHECKING:
    from pf.workflow import FailureDiagnosis


PF_THEME = Theme(
    {
        "success": "green",
        "failure": "bold red",
        "warning": "yellow",
        "indeterminate": "bold yellow",
        "reason.success": "green not bold",
        "reason.failure": "red not bold",
        "reason.warning": "yellow not bold",
        "reason.indeterminate": "yellow not bold",
        "summary.success": "bold green",
        "summary.failure": "bold red",
        "summary.warning": "bold yellow",
        "summary.indeterminate": "bold yellow",
        "dim": "dim",
        "path": "cyan",
        "cell": "bold cyan",
        "cell-title": "bold",
        "hint": "italic cyan not dim not bold",
        "diagnose-hint": "italic dim not bold",
        "link": "underline cyan",
        "version": "magenta",
    }
)

_MAX_CLI_WIDTH = 120


class _CliConsole(Console):
    """Keep the outer CLI canvas readable while preserving Rich measurement."""

    @property
    def size(self) -> ConsoleDimensions:
        measured = super().size
        return ConsoleDimensions(min(measured.width, _MAX_CLI_WIDTH), measured.height)


_INFRA_REASONS = frozenset({"INDETERMINATE", "BASELINE_REJECTION"})

_ICONS = {
    "success": "✓",
    "failure": "✗",
    "warning": "⚠",
    "indeterminate": "!",
}
def _outcome_card(
    lines: tuple[Text, ...] | list[Text],
    *,
    kind: OutcomeKind,
) -> Panel:
    return Panel(
        Group(*lines),
        box=box.ROUNDED,
        border_style=outcome_border_style(kind),
        padding=(0, 1),
    )


def _cell_outcome_card(lines: list[Text], *, kind: OutcomeKind) -> Panel:
    header, *details = lines
    content = Table.grid(
        Column(no_wrap=True),
        Column(overflow="fold", no_wrap=False),
        padding=(0, 0),
        expand=True,
    )
    content.add_row(Text(_ICONS[kind], style=kind), header)
    for detail in details:
        content.add_row(Text(), detail)
    return Panel(
        content,
        box=box.ROUNDED,
        border_style=outcome_border_style(kind),
        padding=(0, 1),
    )

_FAILED_AT = {
    "resolve-project": "resolving project dependencies",
    "resolve-environment": "resolving the test environment",
    "create-environment": "installing dependencies",
    "inspect-interpreter": "installing dependencies",
    "install": "installing dependencies",
    "install-project": "installing dependencies",
    "inspect": "installing dependencies",
    "install-harness": "installing harness",
    "install-environment": "installing the environment plan",
    "inspect-environment-plan": "verifying the environment plan",
    "ty": "static checking",
    "test": "testing",
}
_FAILURE_TITLES: dict[FailureCause, str] = {
    "RESOLUTION_CONFLICT": "This version combination has conflicting dependency requirements and cannot be installed.",
    "BUILD_FAILURE": "This version combination could not be built.",
    "HARNESS_CONFLICT": "The test dependencies cannot be installed without changing the versions being checked.",
    "RUNTIME_INTERFACE_MISSING": "A required runtime interface is missing from this version combination.",
    "VERIFIER_EXITED_NONZERO": "The configured verifier rejected this version combination.",
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
    "RUNTIME_INTERFACE_MISSING": "Review the confirmed missing module or member before changing dependency constraints.",
    "VERIFIER_EXITED_NONZERO": "Review the verifier diagnostics and log before changing code or dependency constraints.",
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
    @property
    def run_id(self) -> str: ...

    def reference_for(self, result: ProcessResult) -> Path | None: ...


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _single_line_summary(value: str) -> str:
    return " ".join(value.split())


def _failed_at_label(stage: str | None) -> str | None:
    if not stage:
        return None
    return _FAILED_AT.get(stage, stage.replace("-", " "))


def _ty_diagnostic_summary(diagnostic: TyDiagnostic) -> str:
    location = diagnostic.path
    if diagnostic.line is not None:
        location += f":{diagnostic.line}"
    if diagnostic.column is not None:
        location += f":{diagnostic.column}"
    return f"{location} [{diagnostic.code}] {_single_line_summary(diagnostic.message)}"


def _cell_detail_lines(
    detail: PytestFailureDetail | StaticIssueDetail | None,
) -> tuple[Text, ...]:
    if detail is None:
        return ()
    if isinstance(detail, PytestFailureDetail):
        phase = "" if detail.first.phase == "call" else f" ({detail.first.phase})"
        first = Text(f"FAILED {detail.first.nodeid}{phase}", style="dim")
    else:
        first = Text(_ty_diagnostic_summary(detail.first), style="dim")
    if detail.total == 1:
        return (_fold_text(first),)
    return (
        _fold_text(first),
        Text(f"... and {detail.total - 1} more", style="dim"),
    )


def _plain_cell_result_lines(
    lines: list[Text],
    *,
    kind: OutcomeKind,
) -> list[Text]:
    if not lines:
        return []
    return [
        Text.assemble((f"{_ICONS[kind]} ", kind), lines[0]),
        *(
            _fold_text(Text.assemble("  ", line))
            for line in lines[1:]
        ),
    ]


def _primary_failure(presentation: CellPresentation) -> FailureRecord:
    if presentation.primary_failure_id is not None:
        matching_id = next(
            (
                failure
                for failure in presentation.failures
                if failure.failure_id == presentation.primary_failure_id
            ),
            None,
        )
        if matching_id is not None:
            return matching_id
    return presentation.failures[0]


_SEARCH_COMPLETION_REASONS = {
    "NO_PASS_IN_SEARCH_SPACE": (
        "The configured search space was fully evaluated, but no compatible "
        "version combination was found."
    ),
    "NON_MONOTONIC": (
        "Search evidence was non-monotonic, so PF could not derive a reliable floor."
    ),
    "NONDETERMINISTIC": (
        "Repeated checks disagreed, so PF could not derive a reliable floor."
    ),
    "MISSING_CELL": "This target cell has no result in this report.",
}


def _cell_reason(
    presentation: CellPresentation,
    failure_title: str | None,
) -> str | None:
    if presentation.command != "search":
        return failure_title
    conclusion = _SEARCH_COMPLETION_REASONS.get(presentation.status)
    if conclusion is not None:
        return conclusion
    if presentation.kind == "indeterminate" and failure_title is not None:
        return (
            "Search stopped before the configured search space was fully evaluated. "
            f"{failure_title}"
        )
    return failure_title


def _counted(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _report_path(report: ValidatedReport) -> str:
    parent = Path(report.package.pyproject_path).parent
    relative = Path("package-floor.json") if parent == Path(".") else parent / "package-floor.json"
    return relative.as_posix()


def _search_reasons(reports: tuple[ValidatedReport, ...]) -> set[str]:
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


def _cell_title_line(
    *,
    cell: Cell,
    elapsed: float | None = None,
) -> Text:
    line = cell_title_text(cell)
    if elapsed is not None:
        line.append(" ")
        line.append(_format_elapsed(elapsed), style="dim magenta")
    return _fold_text(line)


def _cell_completion_detail_line(
    presentation: CellPresentation,
) -> Text | None:
    failed_at = (
        _failed_at_label(presentation.stage)
        if presentation.kind in {"failure", "warning", "indeterminate"}
        else None
    )
    action = completion_action(presentation.command, presentation.kind)
    if action is None and presentation.identity is None and failed_at is None:
        return None
    base_style = f"reason.{presentation.kind}"
    line = Text(style=base_style, overflow="fold", no_wrap=False)
    if action is not None:
        line.append(action)
        if presentation.identity is not None or failed_at is not None:
            line.append(" at ")
    elif failed_at is not None:
        line.append("failed at ")
    if presentation.identity is not None:
        line.append_text(
            cell_identity_text(
                presentation.identity,
                style=base_style,
                dim_secondary=False,
            )
        )
    if failed_at is not None:
        line.append(f"[{failed_at}]")
    return line


def _hint_sentence(
    prefix: str,
    emphasis: str,
    suffix: str,
    *,
    base_style: str = "hint",
    emphasis_style: str = "",
) -> Text:
    line = Text("-> ", style=base_style)
    line.append(prefix, style=base_style)
    extra = (
        f"{base_style} {emphasis_style}".strip()
        if emphasis_style
        else base_style
    )
    line.append(emphasis, style=extra)
    line.append(suffix, style=base_style)
    return _fold_text(line)


def _fold_text(text: Text) -> Text:
    text.overflow = "fold"
    text.no_wrap = False
    return text



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
        self.stdout = stdout or _CliConsole(file=sys.stdout, theme=PF_THEME)
        self.stderr = stderr or _CliConsole(file=sys.stderr, theme=PF_THEME)
        self._logs = logs
        self._root = (root or Path.cwd()).resolve()
        self._emitted_cell_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()
        self._command: str | None = None
        self._live = LiveVerificationView(
            stderr=self.stderr,
            render_cell=self._cell_report_renderables,
            run_id=getattr(logs, "run_id", None),
        )

    def bind_command(self, command: str) -> None:
        self._command = command
        self._live.bind_command(command)

    def render_error(self, error: PfError) -> int:
        if isinstance(error, InvocationError) or (
            isinstance(error, ConfigurationError) and error.candidates
        ):
            return self._render_invocation(error)
        self._live.close(abandon_pending=True, final_outcome="failure")
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
        self._live.close(abandon_pending=True, final_outcome="failure")
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
        check_kind: OutcomeKind = (
            "success"
            if result.status == "PASS"
            else (
                "failure"
                if result.status == "COMPATIBILITY_FAILED"
                else "indeterminate"
            )
        )
        self.close(final_outcome=check_kind)
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
        return 4

    def render_smoke(self, result: SmokeResult) -> int:
        smoke_kind: OutcomeKind = (
            "success"
            if result.status == "PASS"
            else (
                "failure"
                if result.status == "BASELINE_REJECTION"
                else "indeterminate"
            )
        )
        self.close(final_outcome=smoke_kind)
        for outcome in result.outcomes:
            presentation = CellPresentation.from_result(
                outcome,
                cell=outcome.attempt.identity.cell,
                command="smoke",
            )
            if presentation.kind != "success":
                self._print_cell_report(presentation)
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
        presentation = CellPresentation.from_result(
            outcome,
            cell=outcome.attempt.identity.cell,
            command="check",
        )
        if presentation.kind != "success":
            self._print_cell_report(presentation)

    def _render_check_evaluations(self, evaluations: tuple[object, ...]) -> None:
        for evaluation in evaluations:
            if not isinstance(
                evaluation,
                (
                    RuntimeInterfaceMissingEvaluation,
                    VerifierRejectedEvaluation,
                    PassEvaluation,
                ),
            ):
                continue
            presentation = CellPresentation.from_result(
                evaluation,
                cell=evaluation.proposal.cell,
                identity=DeclarationDetailIdentity(),
                command="check",
            )
            if presentation.kind != "success":
                self._print_cell_report(presentation)

    def render_search(self, reports: tuple[ValidatedReport, ...]) -> int:
        search_exit_code = _search_exit_code(_search_reasons(reports))
        search_kind: OutcomeKind
        if search_exit_code == 0:
            search_kind = "success"
        elif search_exit_code == 1:
            search_kind = "failure"
        elif search_exit_code == 4:
            search_kind = "indeterminate"
        else:
            search_kind = "warning"
        self.close(final_outcome=search_kind)
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
                self._print_cell_report(
                    CellPresentation.from_result(
                        result,
                        cell=result.cell,
                        search_events=events,
                        command="search",
                    )
                )
        for events in events_by_cell.values():
            first = events[0]
            failures = tuple(event.failure for event in events)
            event = CellCompletedEvent(
                cell=first.cell,
                completed=1,
                total=1,
                outcome=CellFailed(
                    status=(
                        "SEARCH_FAILED"
                        if any(
                            failure.disposition == "REJECTED"
                            for failure in failures
                        )
                        else "INDETERMINATE"
                    ),
                    phase=failures[0].stage,
                    failures=failures,
                    verification_role="probe",
                ),
                diagnose_available=True,
            )
            self._print_cell_report(
                CellPresentation.from_completed(
                    event,
                    search_events=tuple(events),
                    command="search",
                )
            )
        return self._print_search_summary(reports)

    def _print_cell_report(
        self,
        presentation: CellPresentation,
    ) -> None:
        renderables = self._cell_report_renderables(presentation)
        if renderables is not None:
            for renderable in renderables:
                self._print_step(renderable)

    def _cell_report_renderables(
        self,
        presentation: CellPresentation,
    ) -> tuple[Text | Panel | Group, ...] | None:
        key = _cell_key(presentation.cell)
        if key in self._emitted_cell_keys:
            return None
        lines = self._cell_result_lines(presentation)
        self._emitted_cell_keys.add(key)
        if self.stderr.is_terminal:
            return (_cell_outcome_card(lines, kind=presentation.kind),)
        return tuple(_plain_cell_result_lines(lines, kind=presentation.kind))

    def _render_explain_cell(self, presentation: CellPresentation) -> None:
        """Render one report Cell with the shared final-card presentation."""
        lines = self._cell_result_lines(presentation)
        if self.stdout.is_terminal:
            self.stdout.print(_cell_outcome_card(lines, kind=presentation.kind))
            return
        for line in _plain_cell_result_lines(lines, kind=presentation.kind):
            self.stdout.print(line)

    def _render_explain_overview(
        self,
        lines: tuple[Text, ...],
        *,
        kind: OutcomeKind,
    ) -> None:
        """Render the report overview with the shared outcome-card theme."""
        if self.stdout.is_terminal:
            self.stdout.print(_outcome_card(lines, kind=kind))
            return
        for line in lines:
            self.stdout.print(line)

    def _cell_result_lines(
        self,
        presentation: CellPresentation,
    ) -> list[Text]:
        body: list[Text] = [
            _cell_title_line(
                cell=presentation.cell,
                elapsed=presentation.elapsed,
            )
        ]
        if presentation.completed_packages is not None:
            body.append(completed_packages_text(presentation.completed_packages))
        completion_detail = _cell_completion_detail_line(presentation)
        if completion_detail is not None:
            body.append(completion_detail)
        if presentation.failures:
            record = _primary_failure(presentation)
            failure_presentation = self.failure_presentation(
                record,
                role=presentation.role,
                command=presentation.command,
            )
            body.append(
                _fold_text(
                    Text(
                        _cell_reason(
                            presentation,
                            failure_presentation.title,
                        )
                        or failure_presentation.title,
                    )
                )
            )
            body.extend(_cell_detail_lines(presentation.detail))
            if presentation.diagnose_available:
                body.append(
                    _hint_sentence(
                        "run ",
                        f"`pf diagnose {presentation.cell.package} --failure {record.failure_id}`",
                        " for more information.",
                        base_style="diagnose-hint",
                    )
                )
            else:
                see = (
                    self._see_details_quote(record.process)
                    if record.process is not None
                    else None
                )
                body.append(
                    see
                    if see is not None
                    else Text("Detailed diagnosis unavailable.", style="dim")
                )
            return body
        reason = _cell_reason(presentation, None)
        if reason is not None:
            body.append(_fold_text(Text(reason)))
        body.extend(_cell_detail_lines(presentation.detail))
        return body

    def consume(self, event: ActivityEvent) -> None:
        self._live.consume(event)

    def close(
        self,
        *,
        abandon_pending: bool = False,
        final_outcome: OutcomeKind | None = None,
    ) -> None:
        self._live.close(
            abandon_pending=abandon_pending,
            final_outcome=final_outcome,
        )

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
        return self._live.take_search_diagnostics()

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


    def _print_outcome(
        self,
        kind: OutcomeKind,
        message: str,
        *,
        console: Console | None = None,
    ) -> None:
        (console or self.stderr).print(
            Text(f"{_ICONS[kind]} {message}", style=f"summary.{kind}"),
            soft_wrap=True,
        )

    def _print_search_summary(
        self,
        reports: tuple[ValidatedReport, ...],
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
        reports: tuple[ValidatedReport, ...],
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


    def _print_step(self, message: str | Text | Panel | Group) -> None:
        self._live.print_step(message)


    def render_explain(self, reports: tuple[ValidatedReport, ...]) -> int:
        from pf.terminal import _explain

        return _explain.render(self, reports)

    def render_diagnose(
        self,
        diagnoses: tuple[FailureDiagnosis, ...],
    ) -> int:
        from pf.terminal import _diagnose

        return _diagnose.render(self, diagnoses, root=self._root)

    def render_merge(self, report: ValidatedReport, output: str) -> int:
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
