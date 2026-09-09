"""Portable association between an actual prepared Proposal and static inputs."""

from __future__ import annotations

from pydantic import model_validator

from pf.resolution import ResolutionPlanEvidence, environment_identity_digest, resolution_graph_id, resolution_request_digest
from pf.harness import active_harness_requirements, original_harness, relax_harness
from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import Attempt
from pf.schemas.project import (
    Proposal, HarnessBaseline, HarnessRequirement, SelectedCandidate, VersionPin,
    RequirementDeclaration, SourcePlan, selected_candidate_evidence_digest,
)
from pf.schemas.policy import ExecutionPolicy
from pf.schemas.static import StaticContentUnavailable, StaticSubject, StaticSubjectCell
from pf.static_projection import resolution_projection, subject_interpreter


class StaticPreparationEvidence(FrozenSchema):
    attempt: Attempt
    proposal: Proposal
    project_plan: ResolutionPlanEvidence
    environment_plan: ResolutionPlanEvidence | None
    subject: StaticSubject
    harness_requirements: tuple[HarnessRequirement, ...]
    harness_baseline: HarnessBaseline
    selected_candidates: tuple[SelectedCandidate, ...] | None
    execution_policy: ExecutionPolicy
    declarations: tuple[RequirementDeclaration, ...]
    selected_test_group: str | None
    source_plan: SourcePlan

    @model_validator(mode="after")
    def validate_preparation(self) -> StaticPreparationEvidence:
        attempt = self.attempt.identity
        proposal = self.proposal
        if self.execution_policy.identity != attempt.execution_policy_identity:
            raise ValueError("static preparation execution policy preimage mismatch")
        declarations = {item.declaration_id: item for item in self.declarations}
        if len(declarations) != len(self.declarations) or any(
            item.package != attempt.cell.package for item in self.declarations
        ) or not set(attempt.active_declaration_ids) <= declarations.keys():
            raise ValueError("static preparation declaration closure mismatch")
        active_declarations = tuple(declarations[key] for key in attempt.active_declaration_ids)
        if tuple(sorted(item.declaration_id for item in active_declarations if not item.managed)) != self.proposal.fixed_declaration_ids:
            raise ValueError("static preparation fixed declarations mismatch")
        if {item.name for item in active_declarations if item.managed} != {pin.name for pin in self.proposal.managed_vector}:
            raise ValueError("static preparation managed declarations mismatch")
        if self.selected_test_group is None and self.harness_requirements:
            raise ValueError("static preparation harness requires a selected test group")
        subject = self.subject
        if proposal.attempt_id != self.attempt.attempt_id:
            raise ValueError("static preparation Proposal must bind its Attempt")
        if not (
            proposal.snapshot_digest == attempt.source_snapshot_digest
            == subject.source_snapshot_digest
        ):
            raise ValueError("static preparation source snapshot mismatch")
        if not (
            StaticSubjectCell.from_cell(proposal.cell)
            == StaticSubjectCell.from_cell(attempt.cell)
            == subject.cell
        ):
            raise ValueError("static preparation Cell mismatch")
        if proposal.policy_identity != attempt.execution_policy_identity:
            raise ValueError("static preparation execution policy mismatch")
        if proposal.interpreter is None:
            raise ValueError("static preparation interpreter mismatch")
        if subject.interpreter != subject_interpreter(proposal.interpreter, proposal.cell):
            raise ValueError("static preparation interpreter mismatch")
        if self.source_plan.identity != attempt.source_plan_identity:
            raise ValueError("static preparation SourcePlan mismatch")
        if self.project_plan.kind != "project" or (
            self.environment_plan is not None and self.environment_plan.kind != "environment"
        ):
            raise ValueError("static preparation plan kind mismatch")
        for plan in (self.project_plan, self.environment_plan):
            if plan is None:
                continue
            context = plan.context
            if (
                context.digest != attempt.resolution_context_digest
                or context.cell != attempt.cell
                or context.source_plan_identity != attempt.source_plan_identity
                or context.interpreter != proposal.interpreter
            ):
                raise ValueError("static preparation resolution context mismatch")
        environment_digest = (
            self.environment_plan.semantic_digest if self.environment_plan else None
        )
        if (
            proposal.project_plan_digest != self.project_plan.semantic_digest
            or proposal.environment_plan_digest != environment_digest
        ):
            raise ValueError("static preparation plan identity mismatch")
        resolution_graph_id(proposal.resolved_graph)
        if proposal.proposal_id != environment_identity_digest(
            attempt_id=self.attempt.attempt_id,
            project_plan_digest=self.project_plan.semantic_digest,
            environment_plan_digest=environment_digest,
            graph=proposal.resolved_graph,
        ):
            raise ValueError("static preparation Proposal identity mismatch")
        selected_plan = self.environment_plan or self.project_plan
        if self.environment_plan is not None:
            resolved = {node.name: node for node in self.environment_plan.packages}
            if any(
                (actual := resolved.get(node.name)) is None
                or actual.version != node.version or actual.source != node.source
                or actual.selected_artifact != node.selected_artifact
                for node in self.project_plan.packages
            ):
                raise ValueError("static preparation environment changed project selections")
        projected = resolution_projection(selected_plan.packages)
        if isinstance(projected, StaticContentUnavailable) or projected != subject.resolution_projection:
            raise ValueError("static preparation resolution projection mismatch")
        names = tuple(pin.name for pin in proposal.managed_vector)
        if names != tuple(sorted(set(names))):
            raise ValueError("static preparation managed vector must be sorted and unique")
        vector = {pin.name: pin.version for pin in proposal.managed_vector}
        resolved = {node.name: node.version for node in proposal.resolved_graph}
        if len(vector) != len(proposal.managed_vector) or any(
            resolved.get(name) != version for name, version in vector.items()
        ):
            raise ValueError("static preparation managed vector mismatch")
        if attempt.requested_managed_vector is not None and (
            proposal.managed_vector != attempt.requested_managed_vector
        ):
            raise ValueError("static preparation exact vector mismatch")
        active = active_harness_requirements(self.harness_requirements, attempt.cell)
        declaration_ids = tuple(sorted(item.declaration_id for item in active))
        if declaration_ids != attempt.harness_declaration_ids or (
            self.harness_baseline.cell != attempt.cell
            or self.harness_baseline.declaration_ids != declaration_ids
        ):
            raise ValueError("static preparation harness declarations mismatch")
        if bool(active) != (self.environment_plan is not None):
            raise ValueError("static preparation project-only boundary mismatch")
        if not active and self.harness_baseline.observations:
            raise ValueError("static preparation empty harness baseline mismatch")
        if attempt.requested_resolution == "highest":
            if self.harness_baseline.observations != (
                self.environment_plan.direct_harness if self.environment_plan else ()
            ):
                raise ValueError("static preparation highest baseline mismatch")
            harness = original_harness(self.harness_requirements, attempt.cell)
        else:
            if self.harness_baseline.digest != attempt.harness_baseline_digest:
                raise ValueError("static preparation harness baseline identity mismatch")
            harness = relax_harness(
                self.harness_requirements, self.harness_baseline,
                project_plan=self.project_plan, source_plan=self.source_plan,
            ).requirements
        if attempt.requested_resolution == "exact-vector":
            if self.selected_candidates is None or (
                selected_candidate_evidence_digest(self.selected_candidates)
                != attempt.selected_candidate_evidence_digest
            ):
                raise ValueError("static preparation selected candidate evidence mismatch")
            if tuple(VersionPin(name=item.dependency, version=item.version)
                     for item in self.selected_candidates) != attempt.requested_managed_vector:
                raise ValueError("static preparation selected candidate vector mismatch")
            # pylock represents a direct URL wheel as an archive; bind its
            # actual filename, locator and hash as EnvironmentFactory does.
            project_packages = {node.name: node for node in self.project_plan.packages}
            for selected in self.selected_candidates:
                planned = project_packages.get(selected.dependency)
                if planned is None or planned.version != selected.version or (
                    planned.selected_artifact is None
                    or planned.selected_artifact.filename != selected.artifact.filename
                    or planned.selected_artifact.locator != selected.artifact.locator
                    or planned.selected_artifact.content_hash != selected.artifact.content_hash
                ):
                    raise ValueError("static preparation selected artifact mismatch")
        elif self.selected_candidates is not None:
            raise ValueError("static preparation unexpected selected candidates")
        for plan in (self.project_plan, self.environment_plan):
            if plan is None:
                continue
            expected = resolution_request_digest(
                kind=plan.kind, package_name=attempt.cell.package,
                snapshot_digest=attempt.source_snapshot_digest, cell=attempt.cell,
                resolution_kind=("exact-selection" if attempt.requested_resolution == "exact-vector"
                                 else attempt.requested_resolution),
                selection=self.selected_candidates,
                baseline_digest=attempt.harness_baseline_digest,
                context_digest=attempt.resolution_context_digest,
                project_plan_digest=(self.project_plan.semantic_digest if plan.kind == "environment" else None),
                harness=harness if plan.kind == "environment" else (),
                source_plan_identity=attempt.source_plan_identity,
            )
            if plan.request_digest != expected:
                raise ValueError("static preparation resolution request mismatch")
        return self
