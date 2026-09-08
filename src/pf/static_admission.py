"""Pure admission rules shared by online comparisons and saved-evidence readers."""

from __future__ import annotations

from pathlib import PurePosixPath

from pf.schemas.static import StaticContentPath
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence


def admit_harness_relation(
    reference: StaticPreparationEvidence,
    subject: StaticPreparationEvidence,
) -> bool:
    """Validate the directional D012 harness relation for either comparison kind.

    Preparation evidence has already recomputed each endpoint's requirements and
    resolution requests. This rule admits their relationship; it does not admit
    the remaining GLOBAL/SLICE context or authorize diagnostic subtraction.
    """
    if (
        reference.proposal.cell != subject.proposal.cell
        or reference.selected_test_group != subject.selected_test_group
        or reference.harness_requirements != subject.harness_requirements
        or reference.subject.source.source_plan != subject.subject.source.source_plan
        or reference.execution_policy.resolution != subject.execution_policy.resolution
    ):
        return False
    # Normalization rules are fixed typed literals in the full ExecutionPolicy
    # preimages validated by each endpoint. Verifier configuration is unrelated
    # to harness preparation and does not enter this comparison.
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
    # For highest, the saved baseline is validated against that actual original
    # environment plan. For relaxed requests, it is bound to the Attempt and the
    # regenerated request; equal baselines therefore have the same preimage.
    return reference.harness_baseline == subject.harness_baseline


def admit_common_static_context(
    reference: StaticPreparationEvidence,
    subject: StaticPreparationEvidence,
) -> bool:
    """Check fixed static inputs before GLOBAL/SLICE-specific admission.

    Observation policy and Run reference membership are checked at the consumer
    seam. No whole installed-world equality or attribution claim is made here.
    """
    left, right = reference.subject, subject.subject
    if (
        left.source.snapshot_identity != right.source.snapshot_identity
        or left.source.packages != right.source.packages
        or left.source.source_plan != right.source.source_plan
        or reference.declarations != subject.declarations
        or left.target != right.target
        or left.analysis_layout != right.analysis_layout
        or left.process_context != right.process_context
        or not admit_harness_relation(reference, subject)
    ):
        return False
    # Preparation is allowed to rewrite dependency declarations and uv source
    # bindings in governed pyprojects. All remaining source bytes stay fixed.
    governed = {
        StaticContentPath(root=item.source.root,
                          path=str(PurePosixPath(item.source.path) / "pyproject.toml"))
        for item in left.source.packages
    } | {
        StaticContentPath(root="snapshot", path=item.pyproject_path)
        for item in reference.declarations
    }
    if tuple(item for item in left.source.content.entries if item.location not in governed) != tuple(
        item for item in right.source.content.entries if item.location not in governed
    ):
        return False
    lc, rc = left.configuration, right.configuration
    if (
        lc.effective_file != rc.effective_file
        or lc.files_in_precedence_order != rc.files_in_precedence_order
        or lc.discovery_boundaries != rc.discovery_boundaries
        or lc.external_roots != rc.external_roots
    ):
        return False
    # Original documents remain in raw identity. Comparison uses the actual
    # materialized effective configuration plus frozen external/ignore inputs;
    # a managed dependency edit in an original pyproject is not a ty setting.
    originals = {path for path in lc.files_in_precedence_order
                 if path.root == lc.effective_file.root and path != lc.effective_file}
    return tuple(item for item in lc.content.entries if item.location not in originals) == tuple(
        item for item in rc.content.entries if item.location not in originals
    )


def admit_static_consumer_context(
    reference: StaticConsumerEvidence,
    subject: StaticConsumerEvidence,
) -> bool:
    """Admit shared static context, including the actual observation policy."""
    return (
        reference.observation.observation_policy == subject.observation.observation_policy
        and admit_common_static_context(reference.preparation, subject.preparation)
    )
