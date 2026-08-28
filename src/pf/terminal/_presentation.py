from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.text import Text

from pf.schemas.evaluation import (
    BaselineDetailIdentity,
    CellCompletedEvent,
    CellDetailIdentity,
    CellFailed,
    CellResultDetail,
    CellSucceeded,
    DeclarationDetailIdentity,
    FailureRecord,
    ProcessResult,
    SearchFailureEvent,
    SearchProbeDetailIdentity,
    VerificationRole,
)
from pf.schemas.project import Cell, VersionPin
from pf.verification import completion_outcome


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
        raise AssertionError(
            f"unsupported cell identity: {type(identity).__name__}"
        )
    if first is not None and second is not None:
        _append_bracket_token(value, first, style="bold cyan")
        _append_bracket_token(value, second, style="cyan")
    if stage is not None:
        _append_bracket_token(
            value,
            "testing" if stage == "dynamic tests" else stage,
            style="default",
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
        raise AssertionError(
            f"unsupported cell identity: {type(identity).__name__}"
        )


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
        result: object,
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
            outcome=completion_outcome(result),
            elapsed=elapsed,
            search_events=search_events,
            command=command,
            diagnose_available=diagnose_available,
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
            search_projection[0]
            if search_projection is not None
            else outcome_primary
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
            search_projection[1]
            if search_projection is not None
            else outcome_detail
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
            stage=primary_failure.stage if primary_failure is not None else outcome.phase,
            role=(
                outcome.verification_role
                if isinstance(outcome, CellFailed)
                else None
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


def _search_projection(
    search_events: tuple[SearchFailureEvent, ...],
) -> tuple[str, CellResultDetail | None] | None:
    if not search_events:
        return None
    terminal = search_events[-1]
    if terminal.evaluation is None:
        return terminal.failure.failure_id, None
    outcome = completion_outcome(terminal.evaluation)
    detail = outcome.detail if isinstance(outcome, CellFailed) else None
    return terminal.failure.failure_id, detail
