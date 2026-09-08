"""Derive Failure-scoped static audit associations without changing dynamic identity."""
from __future__ import annotations

from typing import Literal

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import AttemptFailureScope, FailureRecord, ProcessObservation
from pf.schemas.static_comparison import SliceComparisonContext
from pf.schemas.static_scope import StaticScopeEvidence
from pf.schemas.ty_fact import TyCheckFact


class AssociatedComparison(FrozenSchema):
    identity: str
    kind: Literal["GLOBAL", "SLICE"]
    status: Literal["COMPARED", "UNCOMPARED", "UNAVAILABLE"]
    state: Literal["STATIC_UNCHANGED", "STATIC_REGRESSION"] | None = None


class DiagnoseStaticAssociation(FrozenSchema):
    """Read-time audit link; it is not Failure authority and not a CLI selector."""

    path: Literal["execution", "selection"]
    scope_ref: str
    fact_kind: Literal["ty-check", "ty-check-unavailable"] | None = None
    diagnostic_count: int = 0
    comparisons: tuple[AssociatedComparison, ...] = ()
    selection_reason: Literal["mechanical", "history", "static-suspect", "static-clean-neighbor"] | None = None
    static_search_ref: str | None = None
    producer_ref: str | None = None
    log_path: str | None = None


def diagnose_static_associations(
    failure: FailureRecord,
    scopes: tuple[StaticScopeEvidence, ...],
    *,
    proposal_id: str | None = None,
) -> tuple[DiagnoseStaticAssociation, ...]:
    """Return explicit execution then selection links in one hit source."""
    scope = _scope_for(failure, scopes)
    if scope is None:
        return ()
    found: list[DiagnoseStaticAssociation] = []
    seen: set[tuple[object, ...]] = set()

    def add(item: DiagnoseStaticAssociation) -> None:
        key = (
            item.path, item.fact_kind, item.static_search_ref, item.selection_reason,
            tuple(comparison.identity for comparison in item.comparisons),
        )
        if key not in seen:
            seen.add(key)
            found.append(item)

    if (association := _execution_association(failure, scope, proposal_id=proposal_id)) is not None:
        add(association)
    if (association := _selection_association(failure, scope)) is not None:
        add(association)
    return tuple(found)


def static_producer_log_associations(
    scope: StaticScopeEvidence,
) -> tuple[tuple[str, ProcessObservation], ...]:
    """Typed producer fact keys for Diagnosis Index static log locators."""
    processes = {item.ref: item.process for item in scope.processes}
    found: list[tuple[str, ProcessObservation]] = []
    seen: set[str] = set()

    def add(key: str, process: ProcessObservation) -> None:
        if key not in seen:
            seen.add(key)
            found.append((key, process))

    for fact in scope.facts:
        process = processes.get(fact.process_ref)
        if process is None:
            continue
        add(f"{fact.observation.fact.kind}:{fact.observation.fact_identity}", process)
    for search in scope.searches:
        for point in search.points:
            unavailable = point.unavailable
            if (
                unavailable is None
                or unavailable.failure is None
                or unavailable.process is None
            ):
                continue
            add(f"static-prepare:{unavailable.attempt.attempt_id}", unavailable.process)
    return tuple(found)


def static_producer_ref(fact_kind: str | None, fact_identity: str | None) -> str | None:
    if fact_kind is None or fact_identity is None:
        return None
    return f"{fact_kind}:{fact_identity}"


def _scope_for(
    failure: FailureRecord, scopes: tuple[StaticScopeEvidence, ...],
) -> StaticScopeEvidence | None:
    scope = failure.scope
    cell = scope.attempt.identity.cell if isinstance(scope, AttemptFailureScope) else scope.cell
    matches = [item for item in scopes if item.cell == cell]
    return matches[0] if len(matches) == 1 else None


def _execution_association(
    failure: FailureRecord, scope: StaticScopeEvidence, *, proposal_id: str | None,
) -> DiagnoseStaticAssociation | None:
    if not isinstance(failure.scope, AttemptFailureScope):
        return None
    attempt_id = failure.scope.attempt.attempt_id
    consumers = [
        item for item in scope.consumers
        if item.preparation.attempt.attempt_id == attempt_id
        or (proposal_id is not None and item.preparation.proposal.proposal_id == proposal_id)
    ]
    if not consumers:
        return None
    consumer = next(
        (item for item in consumers if item.preparation.attempt.attempt_id == attempt_id),
        consumers[0],
    )
    if consumer.preparation.attempt.attempt_id != attempt_id:
        return None
    fact = next(item for item in scope.facts if item.ref == consumer.fact_ref)
    return DiagnoseStaticAssociation(
        path="execution",
        scope_ref=scope.scope_ref,
        fact_kind=fact.observation.fact.kind,
        diagnostic_count=(
            len(fact.observation.fact.diagnostics)
            if isinstance(fact.observation.fact, TyCheckFact) else 0
        ),
        comparisons=tuple(
            _comparison(item) for item in scope.comparisons if item.subject_ref == consumer.ref
        ),
        producer_ref=static_producer_ref(
            fact.observation.fact.kind, fact.observation.fact_identity,
        ),
    )


def _selection_association(
    failure: FailureRecord, scope: StaticScopeEvidence,
) -> DiagnoseStaticAssociation | None:
    matches = [item for item in scope.selections if item.failure_id == failure.failure_id]
    if not matches:
        return None
    selection = next((item for item in matches if not item.reused), matches[0])
    ref = selection.request.static_search_ref
    if ref is None:
        return None
    search = next(item for item in scope.searches if item.ref == ref)
    hint = search.hint
    if hint is None:
        return None
    point = (
        search.points[hint.suspect_index]
        if selection.request.selection_reason == "static-suspect"
        else search.points[hint.clean_index]
    )
    comparisons = ()
    if point.comparison_identity is not None:
        comparisons = tuple(
            _comparison(item) for item in scope.comparisons if item.identity == point.comparison_identity
        )
    fact = None
    if comparisons:
        subject_ref = next(item.subject_ref for item in scope.comparisons
                           if item.identity == point.comparison_identity)
        fact = next(item for item in scope.facts
                    if item.ref == next(c.fact_ref for c in scope.consumers if c.ref == subject_ref))
    return DiagnoseStaticAssociation(
        path="selection",
        scope_ref=scope.scope_ref,
        fact_kind=fact.observation.fact.kind if fact is not None else None,
        diagnostic_count=(
            len(fact.observation.fact.diagnostics)
            if fact is not None and isinstance(fact.observation.fact, TyCheckFact) else 0
        ),
        comparisons=comparisons,
        selection_reason=selection.request.selection_reason,
        static_search_ref=ref,
        producer_ref=static_producer_ref(
            fact.observation.fact.kind if fact is not None else None,
            fact.observation.fact_identity if fact is not None else None,
        ),
    )


def _comparison(item) -> AssociatedComparison:
    context_kind = "SLICE" if isinstance(item.context, SliceComparisonContext) else "GLOBAL"
    return AssociatedComparison(
        identity=item.identity, kind=context_kind, status=item.result.status,
        state=getattr(item.result, "state", None),
    )
