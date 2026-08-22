from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pf.schemas.evaluation import (
    CellCompletedEvent,
    CellFailed,
    CellSucceeded,
    FailureRecord,
    ProcessResult,
    SearchFailureEvent,
    StaticRegressionEvaluation,
    TyDiagnostic,
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
    kind: OutcomeKind
    elapsed: float | None
    failures: tuple[FailureRecord, ...]
    diagnostics: tuple[TyDiagnostic, ...]
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
        search_events: tuple[SearchFailureEvent, ...] = (),
        command: str | None = None,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=event.cell,
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
        search_events: tuple[SearchFailureEvent, ...] = (),
        command: str | None = None,
        diagnose_available: bool = True,
    ) -> "CellPresentation":
        return cls._from_outcome(
            cell=cell,
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
        outcome: CellSucceeded | CellFailed,
        elapsed: float | None,
        search_events: tuple[SearchFailureEvent, ...],
        command: str | None,
        diagnose_available: bool,
    ) -> "CellPresentation":
        kind = outcome_kind(outcome.status)
        if outcome.diagnostics and kind == "success":
            kind = "warning"
        failures = (
            _unique_failures(search_events, outcome.failures)
            if isinstance(outcome, CellFailed)
            else ()
        )
        diagnostics = (
            _incremental_diagnostics(search_events, outcome.diagnostics)
            if isinstance(outcome, CellFailed)
            else outcome.diagnostics
        )
        return cls(
            cell=cell,
            kind=kind,
            elapsed=elapsed,
            failures=failures,
            diagnostics=diagnostics,
            process=outcome.process,
            stage=failures[0].stage if failures else outcome.phase,
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


def _incremental_diagnostics(
    search_events: tuple[SearchFailureEvent, ...],
    diagnostics: tuple[TyDiagnostic, ...],
) -> tuple[TyDiagnostic, ...]:
    ordered = list(diagnostics)
    seen = {diagnostic.identity for diagnostic in diagnostics}
    for event in search_events:
        evaluation = event.evaluation
        static = getattr(evaluation, "static", None)
        if not isinstance(static, StaticRegressionEvaluation):
            continue
        for diagnostic in static.incremental:
            if diagnostic.identity in seen:
                continue
            ordered.append(diagnostic)
            seen.add(diagnostic.identity)
    return tuple(ordered)
