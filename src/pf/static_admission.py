"""Pure admission rules shared by online comparisons and saved-evidence readers."""

from __future__ import annotations

from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence


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
