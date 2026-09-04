from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from rich import box
from rich.console import Console, ConsoleDimensions, Group, RenderableType
from rich.panel import Panel
from rich.table import Column, Table
from rich.text import Text
from rich.theme import Theme

from pf.errors import (
    ConfigurationError,
    DiagnoseNotFoundError,
    ExitCode,
    ExplainReportError,
    InvocationError,
    MergeCompatibilityError,
    MergeInputError,
    MergeOutputError,
    PfError,
)
from pf.schemas.evaluation import (
    ActivityEvent,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
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
from pf.schemas.project import ApplySelector, Cell
from pf.report import ValidatedReport
from pf.schemas.apply import ApplyCommandResult
from pf.schemas.report import CellIndeterminate, CellSearchFailure, CellSuccess
from pf.terminal._live import LiveVerificationView
from pf.terminal._presentation import (
    CellPresentation,
    OutcomeKind,
    cell_title_text,
    completed_packages_text,
    completion_action,
    marker_group,
    outcome_border_style,
    result_identity_text,
    result_stage_text,
)

if TYPE_CHECKING:
    from pf.workflow import (
        ExplainCommandResult,
        FailureDiagnosis,
        MergeCommandResult,
        SearchCommandResult,
    )


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
_RESULT_STYLES: dict[OutcomeKind, str] = {
    "success": "bold green",
    "failure": "bold red",
    "warning": "bold yellow",
    "indeterminate": "bold yellow",
}


def _outcome_card(
    lines: tuple[RenderableType, ...] | list[RenderableType],
    *,
    kind: OutcomeKind,
) -> Panel:
    return Panel(
        Group(*lines),
        box=box.ROUNDED,
        border_style=outcome_border_style(kind),
        padding=(0, 1),
    )


def _result_card(
    rows: tuple[tuple[RenderableType | None, RenderableType], ...],
    *,
    kind: OutcomeKind,
) -> Panel:
    """Render a command result with the shared icon/content card gutter."""
    return Panel(
        marker_group(rows, expand=True),
        box=box.ROUNDED,
        border_style=outcome_border_style(kind),
        padding=(0, 1),
    )


def _plain_result_card(
    rows: tuple[tuple[RenderableType | None, RenderableType], ...],
) -> Group:
    return marker_group(rows, expand=True)


def _fact_grid(
    rows: tuple[tuple[Text | str, RenderableType], ...],
) -> Table:
    """Lay out literal labels and values without hand-computed padding."""
    grid = Table.grid(
        Column(style="dim", no_wrap=True),
        Column(ratio=1, overflow="fold", no_wrap=False),
        padding=(0, 2),
        expand=True,
    )
    for label, value in rows:
        grid.add_row(Text(label) if isinstance(label, str) else label, value)
    return grid


def _path_text(display_path: str, *, base: Path, terminal: bool) -> Text:
    """Build a literal display path and an optional non-resolving OSC 8 target."""
    text = Text(display_path, style="path", overflow="fold", no_wrap=False)
    if not terminal:
        return text
    path = Path(display_path)
    target = path if path.is_absolute() else base / path
    text.stylize(f"underline cyan link {target.as_uri()}")
    return text


def _cell_outcome_card(lines: list[Text], *, kind: OutcomeKind) -> Panel:
    header, *details = lines
    content = marker_group(
        (
            (Text(_ICONS[kind], style=kind), header),
            *((None, detail) for detail in details),
        ),
        expand=True,
    )
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
            "PF could not obtain the information needed to start or continue this cell."
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
            "This candidate was excluded from the search."
            if rejected
            else "Compatibility for this candidate is unknown, so this cell stopped."
        )
    if resolved_role == "declaration-capture":
        return (
            "A static baseline could not be captured from the current declarations, "
            "so declared lower bounds were not verified for this cell."
            if rejected
            else (
                "Whether a static baseline can be captured is unknown, so declared "
                "lower bounds were not verified for this cell."
            )
        )
    if resolved_role == "declaration":
        return (
            "The declared lower bounds did not pass the required checks."
            if rejected
            else "Compatibility of the declared lower bounds is unknown."
        )
    if command == "smoke":
        return (
            "The highest-version resolution did not pass the required checks."
            if rejected
            else "Compatibility of the highest-version resolution is unknown."
        )
    return (
        "The highest-version baseline did not pass, so the floor search did not "
        "start for this cell."
        if rejected
        else (
            "Compatibility of the highest-version baseline is unknown, so this cell "
            "stopped."
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


def _plain_cell_result(
    lines: list[Text],
    *,
    kind: OutcomeKind,
) -> Group:
    if not lines:
        return Group()
    header, *details = lines
    return marker_group(
        (
            (Text(_ICONS[kind], style=kind), header),
            *((None, detail) for detail in details),
        ),
        expand=False,
    )


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


def _selector_label(selector: ApplySelector) -> str:
    platform = {
        "darwin": "macos",
        "win32": "windows",
    }.get(selector.sys_platform, selector.sys_platform)
    machine = {
        "AMD64": "x86_64",
        "ARM64": "arm64",
        "aarch64": "arm64",
    }.get(selector.platform_machine, selector.platform_machine)
    return f"{platform}/{machine}"


def _report_cell_counts(report: ValidatedReport) -> tuple[tuple[str, int], ...]:
    counts = {
        "passed": 0,
        "rejected": 0,
        "unknown": 0,
        "no floor": 0,
        "search failed": 0,
        "missing": 0,
    }
    for result in report.cell_results:
        if isinstance(result, CellSuccess):
            counts["passed"] += 1
        elif isinstance(result, BaselineRejection):
            counts["rejected"] += 1
        elif isinstance(result, (BaselineIndeterminate, CellIndeterminate)):
            counts["unknown"] += 1
        elif isinstance(result, CellSearchFailure):
            bucket = (
                "no floor"
                if result.reason == "NO_PASS_IN_SEARCH_SPACE"
                else "search failed"
            )
            counts[bucket] += 1
    target_count = len(report.target_cells) or len(report.cell_results)
    counts["missing"] = max(0, target_count - len(report.cell_results))
    return tuple((label, count) for label, count in counts.items() if count)


def _report_distribution_text(report: ValidatedReport) -> Text:
    target_count = len(report.target_cells) or len(report.cell_results)
    parts = [f"{count} {label}" for label, count in _report_cell_counts(report)]
    parts.append(f"{target_count} total")
    return Text(" · ".join(parts))


def _search_reasons(report: ValidatedReport) -> set[str]:
    if report.result.status != "incomplete":
        return set()
    return set(report.result.reasons)


def _missing_cells(report: ValidatedReport) -> tuple[Cell, ...]:
    observed = {_cell_key(result.cell) for result in report.cell_results}
    return tuple(
        cell for cell in report.target_cells if _cell_key(cell) not in observed
    )


def _other_host_missing_count(report: ValidatedReport) -> int:
    observed_targets = {result.cell.target for result in report.cell_results}
    return sum(
        1 for cell in _missing_cells(report) if cell.target not in observed_targets
    )


def _same_host_missing_count(report: ValidatedReport) -> int:
    observed_targets = {result.cell.target for result in report.cell_results}
    return sum(1 for cell in _missing_cells(report) if cell.target in observed_targets)


def _is_host_partial_success(report: ValidatedReport) -> bool:
    if _search_reasons(report) != {"MISSING_CELL"}:
        return False
    if not report.cell_results:
        return False
    if not all(isinstance(result, CellSuccess) for result in report.cell_results):
        return False
    missing = _missing_cells(report)
    if not missing:
        return False
    observed_targets = {result.cell.target for result in report.cell_results}
    return all(cell.target not in observed_targets for cell in missing)


def _host_partial_remainder(report: ValidatedReport) -> str | None:
    if not _is_host_partial_success(report):
        return None
    other = _other_host_missing_count(report)
    return (
        f"{_counted(other, 'cell')} "
        f"{'awaits another host' if other == 1 else 'await other hosts'} · "
        "next: collect reports and run pf merge"
    )


def _search_cli_outcome(report: ValidatedReport) -> tuple[int, OutcomeKind]:
    reasons = _search_reasons(report)
    if "BASELINE_REJECTION" in reasons:
        return 1, "failure"
    if "INDETERMINATE" in reasons:
        return 4, "indeterminate"
    if _is_host_partial_success(report):
        return 0, "warning"
    if reasons:
        return 2, "warning"
    return 0, "success"


def _missing_cell_conclusion(report: ValidatedReport) -> tuple[str, ...]:
    if not report.cell_results:
        return ("no configured cells match this host",)
    if _is_host_partial_success(report):
        passed_count = sum(
            isinstance(result, CellSuccess) for result in report.cell_results
        )
        remainder = _host_partial_remainder(report)
        passed = (
            (f"{_counted(passed_count, 'cell')} passed",) if passed_count else ()
        )
        return (*passed, *((remainder,) if remainder is not None else ()))
    parts: list[str] = []
    other = _other_host_missing_count(report)
    same = _same_host_missing_count(report)
    if other:
        parts.append(
            f"{_counted(other, 'cell')} "
            f"{'awaits another host' if other == 1 else 'await other hosts'}"
        )
    if same:
        parts.append(
            f"{_counted(same, 'cell')} "
            f"{'is missing' if same == 1 else 'are missing'}"
        )
    if not parts:
        parts.append("target cell results are missing")
    return tuple(parts)


def _search_incomplete_conclusion(report: ValidatedReport) -> str:
    reasons = _search_reasons(report)
    conclusions: list[str] = []
    no_floor_count = sum(
        isinstance(result, CellSearchFailure)
        and result.reason == "NO_PASS_IN_SEARCH_SPACE"
        for result in report.cell_results
    )
    if "NO_PASS_IN_SEARCH_SPACE" in reasons:
        conclusions.append(
            (
                f"{_counted(no_floor_count, 'cell')} "
                f"{'has' if no_floor_count == 1 else 'have'} no applicable floor"
            )
            if no_floor_count
            else "no applicable floor"
        )
    if "NON_MONOTONIC" in reasons:
        conclusions.append("search evidence is non-monotonic")
    if "NONDETERMINISTIC" in reasons:
        conclusions.append("repeated checks disagreed")
    if "UNREPRESENTABLE_PROJECTION" in reasons:
        conclusions.append("the full-matrix floor projection is not representable")
    if "MISSING_CELL" in reasons:
        conclusions.extend(_missing_cell_conclusion(report))
    return " · ".join(conclusions) or "report evidence is incomplete"


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
        line.append(_format_elapsed(elapsed), style="dim cyan")
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
    result_style = _RESULT_STYLES[presentation.kind]
    line = Text(overflow="fold", no_wrap=False)
    if action is not None:
        line.append(action, style=result_style)
        if presentation.identity is not None or failed_at is not None:
            line.append(" at ", style=result_style)
    elif failed_at is not None:
        line.append("failed at ", style=result_style)
    if presentation.identity is not None:
        line.append_text(
            result_identity_text(
                presentation.identity,
                content_style=result_style,
            )
        )
    if failed_at is not None:
        line.append_text(result_stage_text(failed_at, content_style=result_style))
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
    extra = f"{base_style} {emphasis_style}".strip() if emphasis_style else base_style
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
    if command == "diagnose":
        return "pf diagnose FAILURE_ID [OPTIONS]"
    if command:
        return f"pf {command} [OPTIONS]"
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
        self._root = root or Path.cwd()
        if not self._root.is_absolute():
            self._root = Path.cwd() / self._root
        self._emitted_cell_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()
        self._command: str | None = None
        self._final_emitted = False
        self._live = LiveVerificationView(
            stderr=self.stderr,
            render_cell=self._cell_report_renderables,
            run_id=getattr(logs, "run_id", None),
        )

    def bind_command(self, command: str) -> None:
        self._command = command
        self._live.bind_command(command)

    def render_error(self, error: PfError) -> int:
        self._final_emitted = True
        if isinstance(error, InvocationError):
            return self._render_invocation(error)
        self._live.close(abandon_pending=True, final_outcome="failure")
        if isinstance(error, ExplainReportError):
            return self._render_explain_error(error)
        if isinstance(error, DiagnoseNotFoundError):
            return self._render_diagnose_error(error)
        if isinstance(
            error,
            (MergeInputError, MergeCompatibilityError, MergeOutputError),
        ):
            return self._render_merge_error(error)
        rows: list[tuple[Text | None, Text]] = [
            (
                Text(_ICONS["failure"], style="failure"),
                Text.assemble(
                    (f"{error.category}: ", "failure"),
                    str(error),
                ),
            )
        ]
        if error.detail:
            rows.append((None, Text(_single_line_summary(error.detail))))
        self.stderr.print(marker_group(tuple(rows), expand=False), soft_wrap=True)
        if isinstance(error, ConfigurationError) and error.candidates:
            shown = error.candidates[:10]
            remainder = len(error.candidates) - len(shown)
            suffix = f", ... and {remainder} more" if remainder else ""
            self.stderr.print(
                f"Known packages: {', '.join(shown)}{suffix}",
                soft_wrap=True,
            )
        return int(error.exit_code)

    def _print_command_error_card(
        self,
        rows: tuple[tuple[RenderableType | None, RenderableType], ...],
    ) -> None:
        self.stderr.print(
            _result_card(rows, kind="failure")
            if self.stderr.is_terminal
            else _plain_result_card(rows)
        )

    def _render_explain_error(self, error: ExplainReportError) -> int:
        facts: list[tuple[Text | str, RenderableType]] = [
            (
                "report",
                _path_text(
                    error.report_path,
                    base=self._root,
                    terminal=self.stderr.is_terminal,
                ),
            ),
            ("reason", Text(error.reason)),
        ]
        rows: list[tuple[RenderableType | None, RenderableType]] = [
            (
                Text(_ICONS["failure"], style="failure"),
                Text("Explain failed", style="bold"),
            ),
            (None, _fact_grid(tuple(facts))),
        ]
        if error.recovery_command is not None:
            action = Text("-> run `", style="hint")
            action.append(error.recovery_command, style="hint not dim")
            action.append("` to create the report", style="hint")
            rows.append((None, action))
        self._print_command_error_card(tuple(rows))
        suffix = "unavailable" if error.reason == "report is unavailable" else "invalid"
        self._print_outcome(
            "failure",
            f"Explain failed · {error.report_path} {suffix}",
        )
        return int(error.exit_code)

    def _render_diagnose_error(self, error: DiagnoseNotFoundError) -> int:
        rows: tuple[tuple[RenderableType | None, RenderableType], ...] = (
            (
                Text(_ICONS["failure"], style="failure"),
                Text("Diagnosis failed", style="bold"),
            ),
            (
                None,
                _fact_grid(
                    (
                        ("failure", Text(error.failure_id)),
                        ("package", Text(error.package, style="bold cyan")),
                        ("reason", Text(error.reason)),
                    )
                ),
            ),
        )
        self._print_command_error_card(rows)
        self._print_outcome("failure", "Diagnosis failed · failure ID not found")
        return int(error.exit_code)

    def _render_merge_error(
        self,
        error: MergeInputError | MergeCompatibilityError | MergeOutputError,
    ) -> int:
        rows: list[tuple[RenderableType | None, RenderableType]] = [
            (
                Text(_ICONS["failure"], style="failure"),
                Text("Merge failed", style="bold"),
            ),
            (None, Text()),
            (None, Text("Inputs", style="bold")),
        ]
        rows.extend(
            (
                None,
                _path_text(
                    path,
                    base=Path.cwd(),
                    terminal=self.stderr.is_terminal,
                ),
            )
            for path in error.input_paths
        )
        facts: list[tuple[Text | str, RenderableType]] = []
        if isinstance(error, MergeInputError):
            facts.append(
                (
                    "Failed",
                    _path_text(
                        error.failed_input_path,
                        base=Path.cwd(),
                        terminal=self.stderr.is_terminal,
                    ),
                )
            )
            facts.append(("Reason", Text("input report is unavailable or invalid")))
            summary = "Merge failed · input report unavailable"
        elif isinstance(error, MergeCompatibilityError):
            facts.extend(
                (
                    ("Reason", Text("reports are incompatible and cannot be merged")),
                    ("Detail", Text(error.detail or "compatibility check failed")),
                )
            )
            summary = "Merge failed · reports are incompatible"
        else:
            facts.append(
                ("Reason", Text("merged report could not be written reliably"))
            )
            summary = "Merge failed · output was not written"
        output = _path_text(
            error.output_path,
            base=Path.cwd(),
            terminal=self.stderr.is_terminal,
        )
        output.append(" · not written", style="reason.failure")
        facts.append(("Output", output))
        rows.extend(((None, Text()), (None, _fact_grid(tuple(facts)))))
        self._print_command_error_card(tuple(rows))
        self._print_outcome("failure", summary)
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
        return 1

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
                "failure" if result.status == "BASELINE_REJECTION" else "indeterminate"
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
            presentation = CellPresentation.from_evaluation(
                evaluation,
                cell=evaluation.proposal.cell,
                identity=DeclarationDetailIdentity(),
                command="check",
            )
            if presentation.kind != "success":
                self._print_cell_report(presentation)

    def render_search(self, result: "SearchCommandResult") -> int:
        report = result.report
        _, search_kind = _search_cli_outcome(report)
        self.close(final_outcome=search_kind)
        leftover = self._take_search_diagnostics()
        events_by_cell: dict[
            tuple[str, str, str, tuple[str, ...]], list[SearchFailureEvent]
        ] = {}
        for event in leftover:
            events_by_cell.setdefault(_cell_key(event.cell), []).append(event)
        for cell_result in report.cell_results:
            key = _cell_key(cell_result.cell)
            events = tuple(events_by_cell.pop(key, ()))
            self._print_cell_report(
                CellPresentation.from_result(
                    cell_result,
                    cell=cell_result.cell,
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
                            failure.disposition == "REJECTED" for failure in failures
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
        return self._print_search_summary(result)

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
        return (_plain_cell_result(lines, kind=presentation.kind),)

    def _render_explain_cell(
        self,
        presentation: CellPresentation,
        *,
        show_diagnose: bool,
    ) -> None:
        """Render one report Cell with the shared final-card presentation."""
        lines = self._cell_result_lines(
            presentation,
            explain=True,
            show_diagnose=show_diagnose,
        )
        if self.stdout.is_terminal:
            self.stdout.print(_cell_outcome_card(lines, kind=presentation.kind))
            return
        self.stdout.print(_plain_cell_result(lines, kind=presentation.kind))

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
        *,
        explain: bool = False,
        show_diagnose: bool | None = None,
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
            diagnose_available = (
                presentation.diagnose_available
                if show_diagnose is None
                else show_diagnose
            )
            if diagnose_available:
                command = (
                    f"pf diagnose {record.failure_id} "
                    f"--package {presentation.cell.package}"
                )
                body.append(
                    _hint_sentence(
                        "" if explain else "run ",
                        command if explain else f"`{command}`",
                        "" if explain else " for more information.",
                        base_style="hint" if explain else "diagnose-hint",
                    )
                )
            elif not explain:
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

    def render_interrupt(self) -> int:
        if not self._final_emitted:
            self._live.close(abandon_pending=True, final_outcome="warning")
            self._print_outcome("warning", "Interrupted")
        return int(ExitCode.INTERRUPTED)

    def _print_outcome(
        self,
        kind: OutcomeKind,
        message: str,
        *,
        console: Console | None = None,
    ) -> None:
        self._final_emitted = True
        style = f"summary.{kind}"
        (console or self.stderr).print(
            marker_group(
                ((Text(_ICONS[kind], style=style), Text(message, style=style)),),
                expand=False,
            ),
            soft_wrap=True,
        )

    def _print_search_summary(
        self,
        result: "SearchCommandResult",
    ) -> int:
        report = result.report
        exit_code, kind = _search_cli_outcome(report)
        path = result.report_path
        if kind == "success":
            self._print_outcome(
                "success",
                f"Search complete · {path}",
                console=self.stdout,
            )
            return 0
        if exit_code == 1:
            self._print_outcome(
                "failure",
                f"Search stopped · highest-version baseline did not pass · {path} written",
            )
            return 1
        if exit_code == 4:
            self._print_outcome(
                "indeterminate",
                f"Search stopped · compatibility is unknown · {path} written",
            )
            return 4
        self._print_outcome(
            "warning",
            (
                f"Search incomplete · {path} written · "
                f"{_search_incomplete_conclusion(report)}"
            ),
        )
        return exit_code

    def render_minimize(
        self,
        report: ValidatedReport,
        result: ApplyCommandResult,
    ) -> int:
        self.close()
        return self.render_apply(
            result,
            command="minimize",
            host_partial_remainder=_host_partial_remainder(report),
        )

    def _print_step(self, message: str | Text | Panel | Group) -> None:
        self._live.print_step(message)

    def render_explain(self, result: "ExplainCommandResult") -> int:
        from pf.terminal import _explain

        return _explain.render(
            self,
            result.report,
            report_path=result.report_path,
            root=self._root,
        )

    def render_diagnose(
        self,
        diagnosis: FailureDiagnosis,
    ) -> int:
        from pf.terminal import _diagnose

        return _diagnose.render(self, diagnosis, root=self._root)

    def render_merge(self, result: "MergeCommandResult") -> int:
        self.close()
        report = result.report
        complete = report.result.status == "complete"
        rows: list[tuple[RenderableType | None, RenderableType]] = [
            (
                Text(_ICONS["success"], style="success"),
                Text("Merge completed", style="bold"),
            ),
            (None, Text()),
            (None, Text("Inputs", style="bold")),
        ]
        rows.extend(
            (
                None,
                _path_text(
                    path,
                    base=Path.cwd(),
                    terminal=self.stdout.is_terminal,
                ),
            )
            for path in result.input_paths
        )
        result_text = Text.assemble(
            (report.package.name, "bold cyan"),
            " · ",
            (
                report.result.status,
                "reason.success" if complete else "reason.warning",
            ),
        )
        if complete:
            target_count = len(report.target_cells) or len(report.cell_results)
            result_text.append(
                f" · {target_count}/{target_count} cells passed",
                style="reason.success",
            )
        result_facts: list[tuple[Text | str, RenderableType]] = [
            ("Result", result_text),
        ]
        if not complete:
            result_facts.extend(
                (
                    ("", _report_distribution_text(report)),
                    ("Apply", Text("blocked by report evidence", style="reason.warning")),
                )
            )
        result_facts.append(
            (
                "Output",
                _path_text(
                    result.output_path,
                    base=Path.cwd(),
                    terminal=self.stdout.is_terminal,
                ),
            )
        )
        rows.extend(((None, Text()), (None, _fact_grid(tuple(result_facts)))))
        self.stdout.print(
            _result_card(tuple(rows), kind="success")
            if self.stdout.is_terminal
            else _plain_result_card(tuple(rows))
        )
        self._print_outcome(
            "success",
            f"Merge complete · {result.output_path}",
            console=self.stdout,
        )
        return 0

    def render_apply(
        self,
        result: ApplyCommandResult,
        *,
        command: Literal["apply", "minimize"] = "apply",
        host_partial_remainder: str | None = None,
    ) -> int:
        self.close()
        edit = result.edit
        facts = result.presentation_facts
        verb = "Minimized floors" if command == "minimize" else "Applied floors"
        outcome = "project updated" if edit.changed else "no metadata changes"
        action = "minimized" if command == "minimize" else "applied"
        selected = " · ".join(_selector_label(item) for item in facts.selected_selectors)
        preserved = " · ".join(
            _selector_label(item) for item in facts.preserved_selectors
        )
        waiver = facts.source_drift_path_count > 0
        remainder = host_partial_remainder
        kind: OutcomeKind = "warning" if waiver or remainder else "success"
        console = self.stderr if kind == "warning" else self.stdout
        header_outcome = (
            f"{action} with source-drift override"
            if waiver
            else f"{action} verified floors"
        )
        header = Text.assemble(
            (result.package, "bold cyan"),
            " · ",
            (header_outcome, f"reason.{kind} bold"),
        )
        rows: list[tuple[Text | str, RenderableType]] = [
            ("Evidence", Text(f"{facts.observed_cells} observed cells passed")),
        ]
        if preserved:
            rows.extend(
                (
                    ("Scope", Text(f"{selected} verified")),
                    (
                        "Preserved",
                        Text(f"{preserved} · original constraints retained"),
                    ),
                )
            )
        else:
            rows.append(("Scope", Text("all declared platforms")))
        if waiver:
            rows.append(
                (
                    "Override",
                    Text(
                        "source drift accepted · "
                        f"{_counted(facts.source_drift_path_count, 'path')}"
                    ),
                )
            )
            if facts.source_drift_paths:
                hidden = facts.source_drift_path_count - len(facts.source_drift_paths)
                suffix = f" (+{hidden} more)" if hidden else ""
                rows.append(
                    (
                        "Paths",
                        Text(f"{', '.join(facts.source_drift_paths)}{suffix}"),
                    )
                )
        metadata = _path_text(
            edit.pyproject_path,
            base=self._root,
            terminal=console.is_terminal,
        )
        metadata.append(" updated" if edit.changed else " unchanged")
        rows.append(("Metadata", metadata))
        card_rows: tuple[tuple[RenderableType | None, RenderableType], ...] = (
            (Text(_ICONS[kind], style=kind), header),
            (None, _fact_grid(tuple(rows))),
        )
        console.print(
            _result_card(card_rows, kind=kind)
            if console.is_terminal
            else _plain_result_card(card_rows)
        )
        if waiver or remainder:
            summary = (
                f"{verb} with source-drift override · {outcome}"
                if waiver
                else f"{verb} · {outcome}"
            )
            if remainder:
                summary = f"{summary} · {remainder}"
            self._print_outcome("warning", summary)
            return 0
        self._print_outcome(
            "success",
            f"{verb} · {outcome}",
            console=self.stdout,
        )
        return 0
