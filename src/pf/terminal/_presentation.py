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
from pf.schemas.project import Cell
from pf.verification import completion_outcome


OutcomeKind = Literal["success", "failure", "warning", "indeterminate"]

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


def cell_identity_text(
    identity: CellDetailIdentity,
    *,
    style: str = "",
) -> Text:
    if isinstance(identity, BaselineDetailIdentity):
        value = "[baseline][highest]"
    elif isinstance(identity, DeclarationDetailIdentity):
        value = "[declaration][lowest-direct]"
    elif isinstance(identity, SearchProbeDetailIdentity):
        value = (
            f"[{identity.dependency}={identity.version}]"
            f"[{identity.lower_version}..{identity.upper_version}"
            f"#{identity.candidate_count}]"
        )
    else:
        raise AssertionError(
            f"unsupported cell identity: {type(identity).__name__}"
        )
    return Text(value, style=style, overflow="fold", no_wrap=False)


def cell_identity_title(identity: CellDetailIdentity) -> str:
    return cell_identity_text(identity).plain


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
        search_events: tuple[SearchFailureEvent, ...] = (),
        command: str | None = None,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=event.cell,
            identity=identity,
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
        search_events: tuple[SearchFailureEvent, ...] = (),
        command: str | None = None,
        diagnose_available: bool = True,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=cell,
            identity=identity,
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
            kind=kind,
            status=outcome.status,
            elapsed=elapsed,
            failures=failures,
            detail=detail,
            primary_failure_id=primary_failure_id,
            process=outcome.process if isinstance(outcome, CellFailed) else None,
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
