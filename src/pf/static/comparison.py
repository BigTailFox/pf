"""Shared static comparison derive and consumer admission."""

from __future__ import annotations

from collections import Counter
import hashlib

from packaging.version import Version

from pf.schemas.base import canonical_identity_json
from pf.schemas.policy import GuidancePolicy
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_comparison import (
    GlobalComparisonContext,
    SliceComparisonContext,
    StaticCompared,
    StaticComparisonContext,
    StaticComparisonDocument,
    StaticComparisonResult,
    StaticComparisonUnavailable,
    StaticUncompared,
)
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.ty_fact import TyCheckFact, TyCheckUnavailable


def admit_harness_relation(
    reference: StaticPreparationEvidence,
    subject: StaticPreparationEvidence,
) -> bool:
    """Validate the directional D012 harness relation for either comparison kind."""
    if (
        reference.proposal.cell != subject.proposal.cell
        or reference.selected_test_group != subject.selected_test_group
        or reference.harness_requirements != subject.harness_requirements
        or reference.source_plan != subject.source_plan
        or reference.execution_policy.resolution != subject.execution_policy.resolution
    ):
        return False
    left = reference.attempt.identity
    right = subject.attempt.identity
    if left.harness_declaration_ids != right.harness_declaration_ids:
        return False
    if not left.harness_declaration_ids:
        return (
            reference.environment_plan is None and subject.environment_plan is None
            and not reference.harness_baseline.observations
            and not subject.harness_baseline.observations
        )
    if left.requested_resolution == right.requested_resolution == "highest":
        return True
    if right.requested_resolution == "highest":
        return False
    return reference.harness_baseline == subject.harness_baseline


def admit_common_static_context(
    reference: StaticPreparationEvidence,
    subject: StaticPreparationEvidence,
) -> bool:
    """Check fixed static inputs before GLOBAL/SLICE-specific admission."""
    left, right = reference.subject, subject.subject
    if (
        left.source_snapshot_digest != right.source_snapshot_digest
        or left.cell != right.cell
        or left.interpreter != right.interpreter
        or reference.source_plan.identity != subject.source_plan.identity
        or reference.declarations != subject.declarations
        or not admit_harness_relation(reference, subject)
    ):
        return False
    left_locators = {
        item.name: (item.source.kind, item.source.locator, item.source.commit)
        for item in left.resolution_projection
        if item.source.kind in {"path", "workspace", "git"}
    }
    right_locators = {
        item.name: (item.source.kind, item.source.locator, item.source.commit)
        for item in right.resolution_projection
        if item.source.kind in {"path", "workspace", "git"}
    }
    return left_locators == right_locators


def admit_static_consumer_context(
    reference: StaticConsumerEvidence,
    subject: StaticConsumerEvidence,
) -> bool:
    """Admit shared static context, including cache identity and snapshot config."""
    left = reference.observation.observation_policy
    right = subject.observation.observation_policy
    return (
        left.cache_identity == right.cache_identity
        and left.snapshot_ty_config == right.snapshot_ty_config
        and left.host_config == right.host_config
        and admit_common_static_context(reference.preparation, subject.preparation)
    )


def derive_static_comparison(
    *,
    context: StaticComparisonContext,
    subject: StaticConsumerEvidence,
    reference: StaticConsumerEvidence | None,
    guidance: GuidancePolicy,
    uncollected_reference: StaticUncollectedBaseline | None = None,
) -> StaticComparisonResult:
    """Admit the semantic comparison before subtracting any diagnostics."""
    if uncollected_reference is not None and (
        reference is not None
        or not isinstance(context, GlobalComparisonContext)
        or context.highest_proposal_id != uncollected_reference.proposal.proposal_id
        or subject.preparation.proposal.cell != uncollected_reference.proposal.cell
    ):
        return StaticUncompared(reason="context-mismatch")
    current = subject.observation.fact
    if isinstance(current, TyCheckUnavailable):
        return StaticComparisonUnavailable(reason=current.reason)
    if reference is None:
        return StaticUncompared(
            reason="reference-unavailable" if uncollected_reference is not None else "reference-missing"
        )
    baseline = reference.observation.fact
    if isinstance(baseline, TyCheckUnavailable):
        return StaticUncompared(reason="reference-unavailable")
    if (
        subject.observation.observation_policy != guidance.observation
        or not admit_static_consumer_context(reference, subject)
    ):
        return StaticUncompared(reason="context-mismatch")
    if isinstance(context, GlobalComparisonContext):
        if (
            context.highest_proposal_id != reference.preparation.proposal.proposal_id
            or reference.preparation.attempt.identity.requested_resolution != "highest"
        ):
            return StaticUncompared(reason="context-mismatch")
    else:
        if context.anchor_pass is None:
            return StaticUncompared(reason="anchor-missing")
        if not _admit_slice(context, reference, subject):
            return StaticUncompared(reason="context-mismatch")
    return _subtract(
        context=context, subject=subject, reference=reference,
        current=current, baseline=baseline, guidance=guidance,
    )


def _admit_slice(
    context: SliceComparisonContext, reference: StaticConsumerEvidence,
    subject: StaticConsumerEvidence,
) -> bool:
    anchor = context.anchor_pass
    left, right = reference.preparation, subject.preparation
    if anchor is None or (
        anchor.proposal_id != left.proposal.proposal_id
        or anchor.execution_policy_identity != left.execution_policy.identity
        or left.project_plan.context != right.project_plan.context
    ):
        return False
    left_vector = {pin.name: pin.version for pin in left.proposal.managed_vector}
    right_vector = {pin.name: pin.version for pin in right.proposal.managed_vector}
    fixed = {pin.name: pin.version for pin in context.fixed_other_coordinates}
    if context.dependency not in left_vector or context.dependency not in right_vector:
        return False
    if (
        {name: value for name, value in left_vector.items() if name != context.dependency} != fixed
        or {name: value for name, value in right_vector.items() if name != context.dependency} != fixed
    ):
        return False
    if any(Version(item.version) > Version(left_vector[context.dependency]) for item in context.window):
        return False
    if subject == reference:
        return True
    if right.attempt.identity.requested_resolution != "exact-vector":
        return False
    selected = next((
        item for item in right.selected_candidates or ()
        if item.dependency == context.dependency
    ), None)
    return selected is not None and selected in context.window


def _subtract(
    *, context: StaticComparisonContext, subject: StaticConsumerEvidence,
    reference: StaticConsumerEvidence, current: TyCheckFact, baseline: TyCheckFact,
    guidance: GuidancePolicy,
) -> StaticCompared:
    increment = Counter(item.identity for item in current.diagnostics)
    increment.subtract(item.identity for item in baseline.diagnostics)
    identities = tuple(sorted(increment.elements()))
    payload = {
        "context": context.model_dump(mode="json"),
        "subject": {
            "proposal": subject.preparation.proposal.proposal_id,
            "static_subject": subject.preparation.subject.identity,
            "fact": current.identity,
        },
        "reference": {
            "proposal": reference.preparation.proposal.proposal_id,
            "static_subject": reference.preparation.subject.identity,
            "fact": baseline.identity,
        },
        "guidance": guidance.identity,
        "incremental_identities": identities,
    }
    fingerprint = hashlib.sha256(
        f"pf:static-comparison:{context.kind}:v1\0".encode() + canonical_identity_json(payload)
    ).hexdigest()
    return StaticCompared(
        state="STATIC_REGRESSION" if identities else "STATIC_UNCHANGED",
        incremental_identities=identities, fingerprint=fingerprint,
    )


def compare_static_document(
    *, context: StaticComparisonContext, subject: StaticConsumerEvidence,
    reference: StaticConsumerEvidence | None, guidance: GuidancePolicy,
    uncollected_reference: StaticUncollectedBaseline | None = None,
) -> StaticComparisonDocument:
    return StaticComparisonDocument(
        context=context, subject=subject, reference=reference, guidance=guidance,
        uncollected_reference=uncollected_reference,
        result=derive_static_comparison(
            context=context, subject=subject, reference=reference,
            guidance=guidance, uncollected_reference=uncollected_reference,
        ),
    )
