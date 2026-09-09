"""Saved static audit admission. Semantic replay lives here, not in schemas."""

from __future__ import annotations

from uuid import UUID, uuid5

from pf.harness import original_harness, relax_harness
from pf.schemas.resolution import resolution_request_digest
from pf.schemas.static_comparison import (
    GlobalComparisonContext,
    SliceComparisonContext,
    StaticComparisonDocument,
)
from pf.schemas.static_scope import StaticScopeEvidence
from pf.schemas.static_search import StaticHintEvidence
from pf.static.comparison import derive_static_comparison
from pf.static.guidance import StaticPoint, locate_static_hint


StaticAuditDocument = StaticScopeEvidence


def expected_scope_ref(*, run_identity: str, cell) -> str:
    return uuid5(UUID(run_identity), cell.model_dump_json()).hex


def compare_in_document(
    document: StaticAuditDocument, *, scope_ref: str, subject_ref: str,
    reference_ref: str | None, context, guidance, anchor_pass_ref: str | None = None,
) -> StaticComparisonDocument:
    """Resolve local membership, then derive through the shared comparison function."""
    if scope_ref != document.scope_ref:
        raise ValueError("static comparison references belong to another scope")
    subject = document.consumer(subject_ref)
    reference = document.consumer(reference_ref) if reference_ref is not None else None
    uncollected = None
    if isinstance(context, GlobalComparisonContext):
        uncollected = document.highest_uncollected
        if uncollected is not None and context.highest_proposal_id != uncollected.proposal.proposal_id:
            raise ValueError("GLOBAL comparison must resolve the uncollected highest Proposal")
        if reference_ref != document.highest_reference_ref or anchor_pass_ref is not None:
            raise ValueError("GLOBAL comparison must resolve the fixed highest reference")
    elif isinstance(context, SliceComparisonContext):
        if context.anchor_pass is None:
            if anchor_pass_ref is not None:
                raise ValueError("missing slice PASS cannot carry a PASS reference")
        else:
            passed = next((item for item in document.passes if item.ref == anchor_pass_ref), None)
            if (
                passed is None
                or passed.consumer_ref != reference_ref
                or passed.evidence != context.anchor_pass
            ):
                raise ValueError("SLICE anchor PASS must resolve in the same scope")
    result = derive_static_comparison(
        context=context, subject=subject, reference=reference,
        guidance=guidance, uncollected_reference=uncollected,
    )
    return StaticComparisonDocument(
        context=context, subject=subject, reference=reference, guidance=guidance,
        uncollected_reference=uncollected, result=result,
    )


def _admit_search(search, document: StaticAuditDocument) -> None:
    if search.policy.guidance_identity != search.guidance.identity:
        raise ValueError("static search policy must bind its guidance")
    if search.candidates.cell != document.cell or search.candidates.dependency != search.context.dependency:
        raise ValueError("static search candidate snapshot must belong to its Slice")
    window = search.context.window
    expected = tuple(search.candidates.select(item.version) for item in window)
    if expected != window:
        raise ValueError("static search window must use its frozen candidate artifacts")
    all_versions = [item.version for item in search.candidates.candidates]
    indices = [all_versions.index(item.version) for item in window]
    if indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError("static search window must be contiguous in its frozen snapshot")
    comparisons = {item.identity: item for item in document.comparisons}

    def resolve(identity, *, prior=False):
        item = comparisons.get(identity)
        if item is None or not isinstance(item.context, SliceComparisonContext):
            raise ValueError("static search comparison is missing from this scope")
        if (
            item.guidance != search.guidance
            or item.context.anchor_pass != search.context.anchor_pass
            or item.context.dependency != search.context.dependency
            or item.context.fixed_other_coordinates != search.context.fixed_other_coordinates
            or (not prior and item.context != search.context)
        ):
            raise ValueError("static search comparison belongs to another local context")
        consumer = document.consumer(item.subject_ref)
        version = next(
            pin.version for pin in consumer.preparation.proposal.managed_vector
            if pin.name == search.context.dependency
        )
        return item, StaticPoint(version, item.result, identity)

    if not search.points or search.points[0].comparison_identity is None:
        raise ValueError("static search anchor requires an actual self-comparison")
    anchor_record, anchor = resolve(search.points[0].comparison_identity)
    if anchor_record.subject_ref != anchor_record.reference_ref:
        raise ValueError("static search anchor requires an actual self-comparison")
    anchor_preparation = document.consumer(anchor_record.reference_ref).preparation
    if search.candidates.source_plan_identity != anchor_preparation.attempt.identity.source_plan_identity:
        raise ValueError("static search candidate snapshot must bind its anchor source plan")
    if search.points[0].version != anchor.version:
        raise ValueError("static search anchor version differs from its Proposal")
    prior = tuple(resolve(identity, prior=True)[1] for identity in search.prior_comparison_identities)
    restored = [anchor]
    for point in search.points[1:]:
        if point.comparison_identity is not None:
            _, actual = resolve(point.comparison_identity)
        else:
            failure = point.unavailable
            assert failure is not None
            identity = failure.attempt.identity
            anchor_identity = anchor_preparation.attempt.identity
            if (
                identity.cell != document.cell or identity.requested_resolution != "exact-vector"
                or identity.source_snapshot_digest != anchor_identity.source_snapshot_digest
                or identity.source_plan_identity != anchor_identity.source_plan_identity
                or identity.execution_policy_identity != anchor_identity.execution_policy_identity
            ):
                raise ValueError("static uncollected probe must belong to its exact Slice request")
            expected_vector = {pin.name: pin.version for pin in search.context.fixed_other_coordinates}
            expected_vector[search.context.dependency] = point.version
            vector = (
                failure.proposal.managed_vector if failure.proposal is not None
                else identity.requested_managed_vector or ()
            )
            if {pin.name: pin.version for pin in vector} != expected_vector:
                raise ValueError("static uncollected probe vector must match its selected point")
            reason = failure.unavailable.detail if failure.unavailable is not None else "prepare-unavailable"
            from pf.schemas.static_comparison import StaticComparisonUnavailable
            actual = StaticPoint(point.version, StaticComparisonUnavailable(reason=reason), None)
        if actual.version != point.version:
            raise ValueError("static search point version differs from its comparison")
        restored.append(actual)
    cursor = 1
    captured = None

    class Replay:
        known_points = prior

        def __init__(self):
            self.anchor = anchor

        def inspect(self, version):
            nonlocal cursor
            if cursor >= len(restored) or restored[cursor].version != version:
                raise ValueError("static search query sequence cannot be replayed")
            result = restored[cursor]
            cursor += 1
            return result

        def finish(self, result):
            nonlocal captured
            captured = result

    locate_static_hint(Replay(), tuple(item.version for item in window))
    if cursor != len(restored) or captured is None or captured.reason != search.reason:
        raise ValueError("static search result does not match its actual query sequence")
    expected_hint = None
    if captured.hint is not None:
        expected_hint = StaticHintEvidence(
            suspect_index=restored.index(captured.hint.suspect),
            clean_index=restored.index(captured.hint.clean_neighbor),
            clean_is_anchor=captured.hint.clean_is_anchor,
        )
    if expected_hint != search.hint:
        raise ValueError("static hint endpoints do not match admitted local comparisons")


def _admit_preparation_request(preparation) -> None:
    """Replay environment request identity; project identity is schema-closed."""
    if preparation.environment_plan is None:
        return
    attempt = preparation.attempt.identity
    if attempt.requested_resolution == "highest":
        harness = original_harness(preparation.harness_requirements, attempt.cell)
    else:
        harness = relax_harness(
            preparation.harness_requirements,
            preparation.harness_baseline,
            project_plan=preparation.project_plan,
            source_plan=preparation.source_plan,
        ).requirements
    expected = resolution_request_digest(
        kind="environment",
        package_name=attempt.cell.package,
        snapshot_digest=attempt.source_snapshot_digest,
        cell=attempt.cell,
        resolution_kind=(
            "exact-selection"
            if attempt.requested_resolution == "exact-vector"
            else attempt.requested_resolution
        ),
        selection=preparation.selected_candidates,
        baseline_digest=attempt.harness_baseline_digest,
        context_digest=attempt.resolution_context_digest,
        project_plan_digest=preparation.project_plan.semantic_digest,
        harness=harness,
        source_plan_identity=attempt.source_plan_identity,
    )
    if preparation.environment_plan.request_digest != expected:
        raise ValueError("static preparation resolution request mismatch")


def _admit_pass(document: StaticAuditDocument, passed) -> None:
    if not passed.preparation_ref:
        raise ValueError("static PASS requires its runtime-owner preparation_ref")
    preparation = document.preparation(passed.preparation_ref)
    if preparation.proposal.cell != document.cell:
        raise ValueError("static PASS preparation does not belong to this Cell")
    if (
        passed.evidence.proposal_id != preparation.proposal.proposal_id
        or passed.evidence.execution_policy_identity != preparation.execution_policy.identity
    ):
        raise ValueError("static PASS preparation does not match its Proposal")
    process = next((item.process for item in document.processes if item.ref == passed.process_ref), None)
    if process is None:
        raise ValueError("static PASS process is missing")
    from pf.schemas.evaluation import execution_terminal
    if execution_terminal(process) != passed.evidence.verifier.terminal:
        raise ValueError("static PASS must bind its own execution and Proposal")
    if passed.consumer_ref is not None:
        consumer = next(item for item in document.consumers if item.ref == passed.consumer_ref)
        consumer_prep = document.preparation(consumer.preparation_ref)
        if consumer_prep.proposal.proposal_id != preparation.proposal.proposal_id:
            raise ValueError("static PASS consumer preparation is not the same Proposal")


def _admit_saved_static_audit(document: StaticAuditDocument) -> StaticAuditDocument:
    """Replay comparison and hint semantics for one immutable saved audit."""
    if document.scope_ref != expected_scope_ref(
        run_identity=document.run_identity, cell=document.cell,
    ):
        raise ValueError("static audit scope_ref does not match run_identity and cell")
    for member in document.preparations:
        _admit_preparation_request(member.preparation)
    for passed in document.passes:
        _admit_pass(document, passed)
    for comparison in document.comparisons:
        replay = compare_in_document(
            document, scope_ref=document.scope_ref, subject_ref=comparison.subject_ref,
            reference_ref=comparison.reference_ref, context=comparison.context,
            guidance=comparison.guidance, anchor_pass_ref=comparison.anchor_pass_ref,
        )
        if replay.identity != comparison.identity or replay.result != comparison.result:
            raise ValueError("static scope comparison must match admitted semantic evidence")
    for search in document.searches:
        _admit_search(search, document)
    for omission in document.omissions:
        omission.validate_in_scope(document)
    for skip in document.skips:
        skip.validate_in_scope(document)
    for selection in document.selections:
        selection.validate_in_scope(document)
    return document
