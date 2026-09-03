from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from rich.console import Group, RenderableType
from rich.table import Column, Table
from rich.text import Text

from pf.schemas.evaluation import (
    BaselineDetailIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellDetailIdentity,
    CellFailed,
    CellResultDetail,
    CellSucceeded,
    CheckCellOutcome,
    DeclarationDetailIdentity,
    FailureRecord,
    FailureEvaluationRuntimeRun,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessObservation,
    ProcessResult,
    RuntimeEvaluationRun,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessResult,
    SearchFailureEvent,
    SearchProbeDetailIdentity,
    StaticIssueDetail,
    VerifierRejectedEvaluation,
    VerificationRole,
)
from pf.schemas.project import Cell, VersionPin
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
)


OutcomeKind = Literal["success", "failure", "warning", "indeterminate"]

_OUTCOME_BORDER_STYLES: dict[OutcomeKind, str] = {
    "success": "dim green",
    "failure": "dim red",
    "warning": "dim yellow",
    "indeterminate": "dim yellow",
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
_INDETERMINATE_STATUSES = frozenset(
    {"CELL_INDETERMINATE", "BASELINE_INDETERMINATE", "INDETERMINATE"}
)
_OUTCOME_RANK = {"success": 0, "warning": 1, "failure": 2, "indeterminate": 3}
_COMMAND_COMPLETION_ACTIONS: dict[str, tuple[str, str]] = {
    "smoke": ("smoke passed", "smoke failed"),
    "check": ("check passed", "check failed"),
    "search": ("search completed", "search stopped"),
}
_RUN_ID_PATTERN = re.compile(
    r"^(?P<date>\d{8})T(?P<time>\d{6})\."
    r"(?P<first>[^-]+)-(?P<second>[^-]+)-(?P<third>[^-]+)$"
)


def marker_group(
    rows: tuple[tuple[RenderableType | None, RenderableType], ...],
    *,
    expand: bool,
) -> Group:
    """Lay out marker/content rows with a stable two-column Rich gutter."""
    renderables: list[Table] = []
    for marker, content in rows:
        row = Table.grid(
            Column(width=1, no_wrap=True),
            Column(ratio=1 if expand else None, overflow="fold", no_wrap=False),
            padding=(0, 2),
            expand=expand,
        )
        row.add_row(marker or Text(), content)
        renderables.append(row)
    return Group(*renderables)


def run_id_text(run_id: str) -> Text:
    value = Text("run-id: ", style="dim", overflow="fold", no_wrap=False)
    matched = _RUN_ID_PATTERN.fullmatch(run_id)
    if matched is None:
        value.append(run_id, style="dim")
        return value
    value.append(matched.group("date"), style="dim bold green")
    value.append("T", style="dim")
    value.append(matched.group("time"), style="dim bold green")
    value.append(".", style="dim")
    for index, name in enumerate(("first", "second", "third")):
        if index:
            value.append("-", style="dim")
        value.append(matched.group(name), style="dim bold magenta")
    return value


def _append_bracket_token(value: Text, content: str, *, style: str) -> None:
    value.append("[", style="dim")
    value.append(content, style=style)
    value.append("]", style="dim")


def cell_title_text(cell: Cell) -> Text:
    extra = "no-extra" if not cell.extra_surface else "+".join(cell.extra_surface)
    value = Text(overflow="fold", no_wrap=False)
    for content in (f"py{cell.python_minor}", cell.target, extra):
        _append_bracket_token(value, content, style="bold")
    return value


def live_cell_identity_text(
    identity: CellDetailIdentity | None,
    *,
    stage: str | None = None,
) -> Text:
    value = Text(overflow="fold", no_wrap=False)
    if isinstance(identity, BaselineDetailIdentity):
        first, second = "baseline", "highest"
    elif isinstance(identity, DeclarationDetailIdentity):
        first, second = "declaration", "lowest-direct"
    elif isinstance(identity, SearchProbeDetailIdentity):
        first = f"{identity.dependency}={identity.version}"
        second = (
            f"{identity.lower_version}~{identity.upper_version}"
            f"#{identity.candidate_count}"
        )
    elif identity is None:
        first = second = None
    else:
        raise AssertionError(f"unsupported cell identity: {type(identity).__name__}")
    if first is not None and second is not None:
        _append_bracket_token(value, first, style="bold cyan")
        _append_bracket_token(value, second, style="cyan")
    if stage is not None:
        dynamic = stage == "dynamic tests"
        _append_bracket_token(
            value,
            "testing" if dynamic else stage,
            style="cyan" if dynamic else "default",
        )
    return value


def cell_identity_text(
    identity: CellDetailIdentity,
    *,
    style: str = "",
    dim_secondary: bool = True,
) -> Text:
    secondary_style = "dim" if dim_secondary else ""
    bold_secondary_style = "bold dim" if dim_secondary else "bold"
    if isinstance(identity, BaselineDetailIdentity):
        value = Text("[baseline]", style=style, overflow="fold", no_wrap=False)
        value.append("[highest]", style=secondary_style)
        return value
    elif isinstance(identity, DeclarationDetailIdentity):
        value = Text("[declaration]", style=style, overflow="fold", no_wrap=False)
        value.append("[lowest-direct]", style=secondary_style)
        return value
    elif isinstance(identity, SearchProbeDetailIdentity):
        value = Text(style=style, overflow="fold", no_wrap=False)
        value.append(f"[{identity.dependency}=")
        value.append(identity.version, style="bold")
        value.append("][", style=secondary_style)
        value.append(identity.lower_version, style=bold_secondary_style)
        value.append("~", style=secondary_style)
        value.append(identity.upper_version, style=bold_secondary_style)
        value.append("#", style=secondary_style)
        value.append(str(identity.candidate_count), style=bold_secondary_style)
        value.append("]", style=secondary_style)
        return value
    else:
        raise AssertionError(f"unsupported cell identity: {type(identity).__name__}")


def result_identity_text(
    identity: CellDetailIdentity,
    *,
    content_style: str,
) -> Text:
    if isinstance(identity, BaselineDetailIdentity):
        tokens = ("baseline", "highest")
    elif isinstance(identity, DeclarationDetailIdentity):
        tokens = ("declaration", "lowest-direct")
    elif isinstance(identity, SearchProbeDetailIdentity):
        tokens = (
            f"{identity.dependency}={identity.version}",
            (
                f"{identity.lower_version}~{identity.upper_version}"
                f"#{identity.candidate_count}"
            ),
        )
    else:
        raise AssertionError(f"unsupported cell identity: {type(identity).__name__}")
    value = Text(overflow="fold", no_wrap=False)
    for token in tokens:
        value.append("[", style="dim default not bold")
        value.append(token, style=content_style)
        value.append("]", style="dim default not bold")
    return value


def result_stage_text(stage: str, *, content_style: str) -> Text:
    value = Text(overflow="fold", no_wrap=False)
    value.append("[", style="dim default not bold")
    value.append(stage, style=content_style)
    value.append("]", style="dim default not bold")
    return value


def cell_identity_title(identity: CellDetailIdentity) -> str:
    return cell_identity_text(identity).plain


def completed_packages_text(
    completed_packages: tuple[VersionPin, ...],
    *,
    style: str = "success",
) -> Text:
    value = Text("[baseline]", style=style, overflow="fold", no_wrap=False)
    for pin in completed_packages:
        value.append(f"[{pin.name}=")
        value.append(pin.version, style="bold")
        value.append("]")
    return value


def search_vector_text(
    packages: tuple[VersionPin, ...],
    completed_packages: tuple[VersionPin, ...],
    *,
    active_dependency: str | None = None,
) -> Text:
    value = Text(overflow="fold", no_wrap=False)
    completed_names = {pin.name for pin in completed_packages}
    for pin in packages:
        completed = pin.name in completed_names
        if pin.name == active_dependency and not completed:
            continue
        content_style = "green" if completed else "dim"
        package_style = f"bold {content_style}" if completed else content_style
        value.append("[", style="dim")
        value.append(pin.name, style=package_style)
        value.append(f"={pin.version}", style=content_style)
        value.append("]", style="dim")
    return value


def outcome_border_style(kind: OutcomeKind) -> str:
    """Return the shared dim border style for a completed outcome."""
    return _OUTCOME_BORDER_STYLES[kind]


def completion_action(command: str | None, kind: OutcomeKind) -> str | None:
    if command not in _COMMAND_COMPLETION_ACTIONS:
        return None
    success, stopped = _COMMAND_COMPLETION_ACTIONS[command]
    return success if kind == "success" else stopped


def _completion_identity(
    command: str | None,
    outcome: CellSucceeded | CellFailed,
) -> CellDetailIdentity | None:
    if isinstance(outcome, CellSucceeded):
        if command == "smoke":
            return BaselineDetailIdentity()
        if command == "check":
            return DeclarationDetailIdentity()
        return None
    if command == "smoke" and outcome.verification_role == "baseline":
        return BaselineDetailIdentity()
    if command == "check":
        if outcome.verification_role == "declaration-capture":
            return BaselineDetailIdentity()
        if outcome.verification_role == "declaration":
            return DeclarationDetailIdentity()
        return None
    if command == "search" and outcome.verification_role == "baseline":
        return BaselineDetailIdentity()
    return None


def outcome_kind(status: str) -> OutcomeKind:
    if status in _SUCCESS_STATUSES:
        return "success"
    if status in _WARNING_STATUSES:
        return "warning"
    if status in _INDETERMINATE_STATUSES:
        return "indeterminate"
    return "failure"


def escalate_outcome(
    current: OutcomeKind | None,
    new: OutcomeKind,
) -> OutcomeKind:
    if current is None or _OUTCOME_RANK[new] > _OUTCOME_RANK[current]:
        return new
    return current


@dataclass(frozen=True)
class CellPresentation:
    cell: Cell
    identity: CellDetailIdentity | None
    completed_packages: tuple[VersionPin, ...] | None
    kind: OutcomeKind
    status: str
    elapsed: float | None
    failures: tuple[FailureRecord, ...]
    detail: CellResultDetail | None
    primary_failure_id: str | None
    process: ProcessResult | None
    stage: str
    role: VerificationRole | None
    command: str | None
    diagnose_available: bool

    @classmethod
    def from_completed(
        cls,
        event: CellCompletedEvent,
        *,
        elapsed: float | None = None,
        identity: CellDetailIdentity | None = None,
        completed_packages: tuple[VersionPin, ...] | None = None,
        search_events: tuple[SearchFailureEvent, ...] = (),
        command: str | None = None,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=event.cell,
            identity=identity,
            completed_packages=completed_packages,
            outcome=event.outcome,
            elapsed=elapsed,
            search_events=search_events,
            command=command,
            diagnose_available=event.diagnose_available,
        )

    @classmethod
    def from_result(
        cls,
        result: CheckCellOutcome | HighestVersionOutcome | CellResult,
        *,
        cell: Cell,
        elapsed: float | None = None,
        identity: CellDetailIdentity | None = None,
        completed_packages: tuple[VersionPin, ...] | None = None,
        search_events: tuple[SearchFailureEvent, ...] = (),
        command: str | None = None,
        diagnose_available: bool = True,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=cell,
            identity=identity,
            completed_packages=completed_packages,
            outcome=_run_result_outcome(result),
            elapsed=elapsed,
            search_events=search_events,
            command=command,
            diagnose_available=diagnose_available,
        )

    @classmethod
    def from_evaluation(
        cls,
        evaluation: (
            PassEvaluation
            | RuntimeInterfaceMissingEvaluation
            | VerifierRejectedEvaluation
            | IndeterminateEvaluation
        ),
        *,
        cell: Cell,
        identity: CellDetailIdentity | None = None,
        command: str | None = None,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=cell,
            identity=identity,
            completed_packages=None,
            outcome=_evaluation_outcome(evaluation),
            elapsed=None,
            search_events=(),
            command=command,
            diagnose_available=True,
        )

    @classmethod
    def _from_outcome(
        cls,
        *,
        cell: Cell,
        identity: CellDetailIdentity | None,
        completed_packages: tuple[VersionPin, ...] | None,
        outcome: CellSucceeded | CellFailed,
        elapsed: float | None,
        search_events: tuple[SearchFailureEvent, ...],
        command: str | None,
        diagnose_available: bool,
    ) -> "CellPresentation":
        kind = outcome_kind(outcome.status)
        resolved_identity = identity or _completion_identity(command, outcome)
        failures = (
            _unique_failures(search_events, outcome.failures)
            if isinstance(outcome, CellFailed)
            else ()
        )
        search_projection = _search_projection(search_events)
        outcome_primary = failures[0].failure_id if failures else None
        primary_failure_id = (
            search_projection[0] if search_projection is not None else outcome_primary
        )
        outcome_detail_failure_id = (
            outcome.detail_failure_id if isinstance(outcome, CellFailed) else None
        )
        outcome_detail = (
            outcome.detail
            if isinstance(outcome, CellFailed)
            and outcome_detail_failure_id in {None, outcome_primary}
            else None
        )
        detail = (
            search_projection[1] if search_projection is not None else outcome_detail
        )
        primary_failure = next(
            (
                failure
                for failure in failures
                if failure.failure_id == primary_failure_id
            ),
            None,
        )
        return cls(
            cell=cell,
            identity=resolved_identity,
            completed_packages=completed_packages,
            kind=kind,
            status=outcome.status,
            elapsed=elapsed,
            failures=failures,
            detail=detail,
            primary_failure_id=primary_failure_id,
            process=(
                outcome.process
                if isinstance(outcome, CellFailed)
                and isinstance(outcome.process, ProcessResult)
                else None
            ),
            stage=primary_failure.stage
            if primary_failure is not None
            else outcome.phase,
            role=(
                outcome.verification_role if isinstance(outcome, CellFailed) else None
            ),
            command=command,
            diagnose_available=diagnose_available,
        )


def _unique_failures(
    search_events: tuple[SearchFailureEvent, ...],
    failures: tuple[FailureRecord, ...],
) -> tuple[FailureRecord, ...]:
    ordered: list[FailureRecord] = []
    seen: set[str] = set()
    for record in (
        *(event.failure for event in search_events),
        *failures,
    ):
        if record.failure_id in seen:
            continue
        ordered.append(record)
        seen.add(record.failure_id)
    return tuple(ordered)


def _run_result_outcome(
    result: CheckCellOutcome | HighestVersionOutcome | CellResult,
) -> CellSucceeded | CellFailed:
    if isinstance(result, CheckCellOutcome):
        if isinstance(result.evaluation, PassEvaluation):
            return CellSucceeded(status=result.status, phase="complete")
        assert result.failure is not None
        detail = _evaluation_detail(result.evaluation, runtime=result.runtime)
        process = (
            _failed_evaluation_process(
                result.evaluation,
                result.failure,
                runtime=result.runtime,
            )
            or result.failure_process
        )
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            detail=detail,
            detail_failure_id=(
                result.failure.failure_id if detail is not None else None
            ),
            process=process,
            process_failure_id=(
                result.failure.failure_id if process is not None else None
            ),
            failures=(result.failure,),
            verification_role=result.role,
        )
    if isinstance(result, HighestVersionPass):
        return CellSucceeded(status=result.status, phase="complete")
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        detail = _evaluation_detail(result.evaluation, runtime=result.runtime)
        process = _failed_evaluation_process(
            result.evaluation,
            result.failure,
            runtime=result.runtime,
        ) or (
            result.failure_process
            if isinstance(result, BaselineIndeterminate)
            else None
        )
        return CellFailed(
            status=result.status,
            phase=result.failure.stage,
            detail=detail,
            detail_failure_id=(
                result.failure.failure_id if detail is not None else None
            ),
            process=process,
            process_failure_id=(
                result.failure.failure_id if process is not None else None
            ),
            failures=(result.failure,),
            verification_role="baseline",
        )
    if isinstance(result, CellSuccess):
        return CellSucceeded(status=result.status, phase="complete")
    if isinstance(result, CellSearchFailure):
        return CellFailed(
            status=result.reason,
            phase=result.phase,
            failures=result.failure_records,
            verification_role="probe",
        )
    if isinstance(result, CellIndeterminate):
        terminal = next(
            failure
            for failure in result.failure_records
            if failure.failure_id == result.failure_id
        )
        runtime_run = next(
            (
                item
                for item in result.failure_runtime_runs
                if item.failure_id == terminal.failure_id
            ),
            None,
        )
        runtime = (
            runtime_run.runtime
            if isinstance(runtime_run, FailureEvaluationRuntimeRun)
            else None
        )
        detail = _evaluation_detail(
            None if runtime is None else runtime.evaluation,
            runtime=runtime,
        )
        process = (
            None if runtime_run is None else runtime_run.process_observation
        ) or _failed_evaluation_process(
            None if runtime is None else runtime.evaluation,
            terminal,
            runtime=runtime,
        )
        return CellFailed(
            status=result.status,
            phase=result.phase,
            detail=detail,
            detail_failure_id=(
                terminal.failure_id if detail is not None else None
            ),
            process=process,
            process_failure_id=(
                terminal.failure_id if process is not None else None
            ),
            failures=result.failure_records,
            verification_role="probe",
        )
    raise TypeError(f"unsupported Run result: {type(result).__name__}")


def _evaluation_outcome(
    evaluation: (
        PassEvaluation
        | RuntimeInterfaceMissingEvaluation
        | VerifierRejectedEvaluation
        | IndeterminateEvaluation
    ),
    *,
    runtime: RuntimeEvaluationRun | None = None,
) -> CellSucceeded | CellFailed:
    if isinstance(evaluation, PassEvaluation):
        return CellSucceeded(status=evaluation.status, phase="complete")
    if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        confirmed = evaluation.witnesses[-1].outcome
        assert isinstance(confirmed, RuntimeWitnessResult)
        return CellFailed(
            status=evaluation.status,
            phase="witness",
            detail=_evaluation_detail(evaluation, runtime=runtime),
            process=confirmed.process,
        )
    if isinstance(evaluation, VerifierRejectedEvaluation):
        return CellFailed(status=evaluation.status, phase="test")
    if evaluation.verifier is not None:
        return CellFailed(status=evaluation.status, phase="test")
    assert evaluation.failure is not None
    return CellFailed(
        status=evaluation.status,
        phase=evaluation.failure.stage,
        process=evaluation.failure.process,
    )


def _failed_evaluation_process(
    evaluation: object,
    failure: FailureRecord | None,
    *,
    runtime: RuntimeEvaluationRun | None = None,
) -> ProcessObservation | None:
    process = _runtime_process(runtime)
    if process is not None:
        return process
    if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        confirmed = evaluation.witnesses[-1].outcome
        assert isinstance(confirmed, RuntimeWitnessResult)
        return confirmed.process
    if isinstance(evaluation, IndeterminateEvaluation):
        if evaluation.failure is None:
            return None
        return evaluation.failure.process
    return None if failure is None else failure.process


def _evaluation_detail(
    evaluation: object | None,
    *,
    runtime: RuntimeEvaluationRun | None = None,
) -> CellResultDetail | None:
    if runtime is not None and runtime.diagnostics is not None:
        detail = runtime.diagnostics.detail
        if detail is not None:
            return detail
    if not isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        return None
    confirmed = evaluation.witnesses[-1].outcome
    assert isinstance(confirmed, RuntimeWitnessResult)
    identities = set(confirmed.plan.diagnostic_identities)
    relevant = tuple(
        diagnostic
        for diagnostic in evaluation.static.incremental
        if diagnostic.identity in identities
    )
    if not relevant:
        return None
    return StaticIssueDetail(first=relevant[0], total=len(relevant))


def _runtime_process(runtime: RuntimeEvaluationRun | None) -> ProcessObservation | None:
    if runtime is None or runtime.diagnostics is None:
        return None
    return runtime.diagnostics.process


def _search_projection(
    search_events: tuple[SearchFailureEvent, ...],
) -> tuple[str, CellResultDetail | None] | None:
    if not search_events:
        return None
    terminal = search_events[-1]
    if terminal.evaluation is None:
        return terminal.failure.failure_id, None
    outcome = _evaluation_outcome(
        terminal.evaluation,
        runtime=terminal.runtime,
    )
    detail = outcome.detail if isinstance(outcome, CellFailed) else None
    return terminal.failure.failure_id, detail
