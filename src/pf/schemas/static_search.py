"""Portable static search traces, independently replayed within one scope."""
from typing import Literal

from pydantic import Field, model_validator

from pf.resolution import environment_identity_digest, resolution_graph_id
from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import Attempt, PrepareFailure, ProcessObservation, execution_terminal, SearchProbeRequest
from pf.schemas.project import CandidateSnapshot, Proposal
from pf.schemas.policy import GuidancePolicy, SearchDerivationPolicy
from pf.schemas.static import StaticContentUnavailable
from pf.schemas.static_comparison import SliceComparisonContext, StaticComparisonUnavailable
from pf.static_guidance import StaticPoint, locate_static_hint


class StaticProbeUnavailableEvidence(FrozenSchema):
    attempt: Attempt
    proposal: Proposal | None
    unavailable: StaticContentUnavailable | None
    failure: PrepareFailure | None
    process: ProcessObservation | None

    @model_validator(mode="after")
    def validate_operation(self):
        if (self.unavailable is None) == (self.failure is None):
            raise ValueError("static unavailable probe requires exactly one actual failure kind")
        if self.failure is not None:
            if (self.attempt != self.failure.attempt or self.proposal is not None
                    or self.failure.process is not None):
                raise ValueError("static prepare failure must retain its actual Attempt without a Proposal")
            if self.process is not None and execution_terminal(self.process) != self.failure.failure.terminal:
                raise ValueError("static prepare process must match its recorded operation terminal")
        elif self.proposal is None or self.process is not None or (
            self.proposal.attempt_id != self.attempt.attempt_id
            or self.proposal.cell != self.attempt.identity.cell
            or self.proposal.snapshot_digest != self.attempt.identity.source_snapshot_digest
            or self.proposal.policy_identity != self.attempt.identity.execution_policy_identity
        ):
            raise ValueError("static capture failure must bind its actual prepared Proposal")
        if self.proposal is not None:
            proposal = self.proposal
            if proposal.interpreter is None or proposal.project_plan_digest is None:
                raise ValueError("static capture failure requires completed preparation facts")
            resolution_graph_id(proposal.resolved_graph)
            if proposal.proposal_id != environment_identity_digest(
                attempt_id=self.attempt.attempt_id, project_plan_digest=proposal.project_plan_digest,
                environment_plan_digest=proposal.environment_plan_digest, graph=proposal.resolved_graph,
            ):
                raise ValueError("static capture failure requires its actual preparation identity")
        return self


class StaticSearchPointEvidence(FrozenSchema):
    version: str
    comparison_identity: str | None
    unavailable: StaticProbeUnavailableEvidence | None

    @model_validator(mode="after")
    def validate_point(self):
        if (self.comparison_identity is None) == (self.unavailable is None):
            raise ValueError("static search point requires one comparison or uncollected operation")
        return self


class StaticHintEvidence(FrozenSchema):
    suspect_index: int = Field(ge=1)
    clean_index: int = Field(ge=0)
    clean_is_anchor: bool


class StaticSearchAudit(FrozenSchema):
    ref: str = Field(min_length=1)
    context: SliceComparisonContext
    guidance: GuidancePolicy
    policy: SearchDerivationPolicy
    candidates: CandidateSnapshot
    prior_comparison_identities: tuple[str, ...]
    points: tuple[StaticSearchPointEvidence, ...] = Field(min_length=1)
    hint: StaticHintEvidence | None
    reason: Literal["lower-unchanged", "context-mismatch", "static-unavailable", "anchor-unavailable", "static-inconsistent"] | None

    def validate_in_scope(self, scope) -> None:
        if self.policy.guidance_identity != self.guidance.identity:
            raise ValueError("static search policy must bind its guidance")
        if self.candidates.cell != scope.cell or self.candidates.dependency != self.context.dependency:
            raise ValueError("static search candidate snapshot must belong to its Slice")
        window = self.context.window
        expected = tuple(self.candidates.select(item.version) for item in window)
        if expected != window:
            raise ValueError("static search window must use its frozen candidate artifacts")
        all_versions = [item.version for item in self.candidates.candidates]
        indices = [all_versions.index(item.version) for item in window]
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("static search window must be contiguous in its frozen snapshot")
        comparisons = {item.identity: item for item in scope.comparisons}

        def resolve(identity, *, prior=False):
            item = comparisons.get(identity)
            if item is None or not isinstance(item.context, SliceComparisonContext):
                raise ValueError("static search comparison is missing from this scope")
            if (item.guidance != self.guidance
                    or item.context.anchor_pass != self.context.anchor_pass
                    or item.context.dependency != self.context.dependency
                    or item.context.fixed_other_coordinates != self.context.fixed_other_coordinates
                    or (not prior and item.context != self.context)):
                raise ValueError("static search comparison belongs to another local context")
            consumer = scope.consumer(item.subject_ref)
            version = next(pin.version for pin in consumer.preparation.proposal.managed_vector
                           if pin.name == self.context.dependency)
            return item, StaticPoint(version, item.result, identity)

        anchor_record, anchor = resolve(self.points[0].comparison_identity)
        if anchor_record.subject_ref != anchor_record.reference_ref:
            raise ValueError("static search anchor requires an actual self-comparison")
        anchor_preparation = scope.consumer(anchor_record.reference_ref).preparation
        if self.candidates.source_plan_identity != anchor_preparation.attempt.identity.source_plan_identity:
            raise ValueError("static search candidate snapshot must bind its anchor source plan")
        if self.points[0].version != anchor.version:
            raise ValueError("static search anchor version differs from its Proposal")
        prior = tuple(resolve(identity, prior=True)[1] for identity in self.prior_comparison_identities)
        restored = [anchor]
        for point in self.points[1:]:
            if point.comparison_identity is not None:
                _, actual = resolve(point.comparison_identity)
            else:
                failure = point.unavailable
                assert failure is not None
                identity = failure.attempt.identity
                anchor_identity = anchor_preparation.attempt.identity
                if (identity.cell != scope.cell or identity.requested_resolution != "exact-vector"
                        or identity.source_snapshot_digest != anchor_identity.source_snapshot_digest
                        or identity.source_plan_identity != anchor_identity.source_plan_identity
                        or identity.execution_policy_identity != anchor_identity.execution_policy_identity):
                    raise ValueError("static uncollected probe must belong to its exact Slice request")
                expected_vector = {pin.name: pin.version for pin in self.context.fixed_other_coordinates}
                expected_vector[self.context.dependency] = point.version
                vector = (failure.proposal.managed_vector if failure.proposal is not None
                          else identity.requested_managed_vector or ())
                if {pin.name: pin.version for pin in vector} != expected_vector:
                    raise ValueError("static uncollected probe vector must match its selected point")
                reason = failure.unavailable.detail if failure.unavailable is not None else "prepare-unavailable"
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
        if cursor != len(restored) or captured is None or captured.reason != self.reason:
            raise ValueError("static search result does not match its actual query sequence")
        expected_hint = None
        if captured.hint is not None:
            expected_hint = StaticHintEvidence(
                suspect_index=restored.index(captured.hint.suspect),
                clean_index=restored.index(captured.hint.clean_neighbor),
                clean_is_anchor=captured.hint.clean_is_anchor,
            )
        if expected_hint != self.hint:
            raise ValueError("static hint endpoints do not match admitted local comparisons")


class StaticPhaseOmission(FrozenSchema):
    """Requested local guidance with no admitted raw/PASS anchor; no fake observation."""
    ref: str = Field(min_length=1)
    reason: Literal["anchor-unavailable"] = "anchor-unavailable"
    observed_pass_refs: tuple[str, ...] = ()
    attempt: Attempt
    proposal: Proposal
    candidates: CandidateSnapshot
    window: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_request(self):
        proposal, identity = self.proposal, self.attempt.identity
        if (proposal.attempt_id != self.attempt.attempt_id or proposal.cell != identity.cell
                or proposal.snapshot_digest != identity.source_snapshot_digest
                or proposal.policy_identity != identity.execution_policy_identity
                or proposal.project_plan_digest is None or proposal.interpreter is None):
            raise ValueError("omitted static phase must bind its actual prepared upper point")
        if proposal.proposal_id != environment_identity_digest(
            attempt_id=self.attempt.attempt_id, project_plan_digest=proposal.project_plan_digest,
            environment_plan_digest=proposal.environment_plan_digest, graph=proposal.resolved_graph,
        ):
            raise ValueError("omitted static phase upper Proposal identity is inconsistent")
        if (self.candidates.cell != proposal.cell
                or self.candidates.source_plan_identity != identity.source_plan_identity):
            raise ValueError("omitted static phase candidate context is inconsistent")
        versions = [item.version for item in self.candidates.candidates]
        indices = [versions.index(version) for version in self.window]
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("omitted static phase window must be contiguous")
        from packaging.version import Version
        upper = next((pin.version for pin in proposal.managed_vector
                      if pin.name == self.candidates.dependency), None)
        if upper is None or any(Version(version) > Version(upper) for version in self.window):
            raise ValueError("omitted static phase window exceeds its upper point")
        if not any(Version(version) < Version(upper) for version in self.window):
            raise ValueError("omitted static phase must have unresolved lower candidates")
        return self

    def validate_in_scope(self, scope):
        from pf.schemas.ty_fact import TyCheckFact
        if scope.cell != self.proposal.cell:
            raise ValueError("omitted static phase belongs to another Cell")
        highest = (scope.consumer(scope.highest_reference_ref).preparation.attempt
                   if scope.highest_reference_ref is not None else
                   scope.highest_uncollected.attempt if scope.highest_uncollected is not None else None)
        if highest is None or any(
            getattr(self.attempt.identity, field) != getattr(highest.identity, field)
            for field in ("source_snapshot_digest", "source_plan_identity", "execution_policy_identity")
        ):
            raise ValueError("omitted static phase must belong to its Run input context")
        known = tuple(item.ref for item in scope.passes)[:len(self.observed_pass_refs)]
        if known != self.observed_pass_refs:
            raise ValueError("omitted static phase must retain its observed PASS prefix")
        for passed in scope.passes[:len(self.observed_pass_refs)]:
            consumer = scope.consumer(passed.consumer_ref)
            if consumer.preparation.proposal == self.proposal and isinstance(consumer.observation.fact, TyCheckFact):
                raise ValueError("omitted static phase has an admitted local anchor")


class StaticPhaseSkip(FrozenSchema):
    """Already-delimited coordinate; no static phase and no fabricated hint."""
    ref: str = Field(min_length=1)
    reason: Literal["direct-bound"] = "direct-bound"
    attempt: Attempt
    proposal: Proposal
    candidates: CandidateSnapshot
    window: tuple[str, ...] = Field(min_length=1)
    predecessor: str | None = None
    predecessor_failure_id: str | None = None
    observed_pass_refs: tuple[str, ...] = ()
    observed_search_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_request(self):
        proposal, identity = self.proposal, self.attempt.identity
        if (proposal.attempt_id != self.attempt.attempt_id or proposal.cell != identity.cell
                or proposal.snapshot_digest != identity.source_snapshot_digest
                or proposal.policy_identity != identity.execution_policy_identity
                or proposal.project_plan_digest is None or proposal.interpreter is None):
            raise ValueError("direct-bound skip must bind its actual prepared floor")
        if proposal.proposal_id != environment_identity_digest(
            attempt_id=self.attempt.attempt_id, project_plan_digest=proposal.project_plan_digest,
            environment_plan_digest=proposal.environment_plan_digest, graph=proposal.resolved_graph,
        ):
            raise ValueError("direct-bound skip floor Proposal identity is inconsistent")
        if (self.candidates.cell != proposal.cell
                or self.candidates.source_plan_identity != identity.source_plan_identity):
            raise ValueError("direct-bound skip candidate context is inconsistent")
        versions = [item.version for item in self.candidates.candidates]
        indices = [versions.index(version) for version in self.window]
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("direct-bound skip window must be contiguous")
        from packaging.version import Version
        floor = next((pin.version for pin in proposal.managed_vector
                      if pin.name == self.candidates.dependency), None)
        if floor is None or floor not in self.window or any(
            Version(version) > Version(floor) for version in self.window
        ):
            raise ValueError("direct-bound skip window must include its floor and no higher candidate")
        if (self.predecessor is None) != (self.predecessor_failure_id is None):
            raise ValueError("direct-bound skip predecessor requires its failure")
        if self.predecessor is not None and (
            self.predecessor not in self.window or Version(self.predecessor) >= Version(floor)
        ):
            raise ValueError("direct-bound skip predecessor must lie below its floor")
        return self

    def validate_in_scope(self, scope):
        if scope.cell != self.proposal.cell:
            raise ValueError("direct-bound skip belongs to another Cell")
        highest = (scope.consumer(scope.highest_reference_ref).preparation.attempt
                   if scope.highest_reference_ref is not None else
                   scope.highest_uncollected.attempt if scope.highest_uncollected is not None else None)
        if highest is None or any(
            getattr(self.attempt.identity, field) != getattr(highest.identity, field)
            for field in ("source_snapshot_digest", "source_plan_identity", "execution_policy_identity")
        ):
            raise ValueError("direct-bound skip must belong to its Run input context")
        if tuple(item.ref for item in scope.passes)[:len(self.observed_pass_refs)] != self.observed_pass_refs:
            raise ValueError("direct-bound skip must retain its observed PASS prefix")
        if tuple(item.ref for item in scope.searches)[:len(self.observed_search_refs)] != self.observed_search_refs:
            raise ValueError("direct-bound skip must retain its completed static search prefix")


class OracleSelectionAudit(FrozenSchema):
    """Scheduling evidence; the selected request is not a compatibility result."""
    ref: str = Field(min_length=1)
    request: SearchProbeRequest
    candidates: CandidateSnapshot
    reused: bool
    observed_search_refs: tuple[str, ...]
    attempt: Attempt
    proposal_id: str | None = None
    status: Literal["PASS", "REJECTED", "INDETERMINATE"]
    failure_id: str | None = None

    @model_validator(mode="after")
    def validate_binding(self):
        identity = self.attempt.identity
        if identity.cell != self.candidates.cell:
            raise ValueError("oracle selection must bind an Attempt in its candidate Cell")
        if identity.requested_resolution == "exact-vector":
            if identity.requested_managed_vector != self.request.vector:
                raise ValueError("oracle selection must bind the selected exact Attempt")
        elif identity.requested_resolution != "highest":
            raise ValueError("oracle selection must bind an exact or highest Attempt")
        if self.status == "PASS":
            if self.failure_id is not None or self.proposal_id is None:
                raise ValueError("oracle selection PASS must bind its Proposal without a failure")
        elif self.failure_id is None:
            raise ValueError("oracle selection failure must bind its FailureRecord")
        return self

    def validate_in_scope(self, scope):
        request = self.request
        if self.candidates.cell != scope.cell or self.candidates.dependency != request.active_dependency:
            raise ValueError("oracle selection must belong to its candidate Slice")
        versions = [item.version for item in self.candidates.candidates]
        low, high = versions.index(request.lower_version), versions.index(request.upper_version)
        if high - low + 1 != request.candidate_count or request.candidate_version not in versions[low:high + 1]:
            raise ValueError("oracle selection window must match its frozen candidate domain")
        highest = (scope.consumer(scope.highest_reference_ref).preparation.attempt
                   if scope.highest_reference_ref is not None else
                   scope.highest_uncollected.attempt if scope.highest_uncollected is not None else None)
        if highest is None or any(
            getattr(self.attempt.identity, field) != getattr(highest.identity, field)
            for field in ("source_snapshot_digest", "source_plan_identity", "execution_policy_identity")
        ):
            raise ValueError("oracle selection must belong to its Run input context")
        if tuple(item.ref for item in scope.searches)[:len(self.observed_search_refs)] != self.observed_search_refs:
            raise ValueError("oracle selection must retain its completed static search prefix")
        ref = request.static_search_ref
        if ref is None:
            return
        if ref not in self.observed_search_refs:
            raise ValueError("oracle selection cannot reference a future static search")
        search = next(item for item in scope.searches if item.ref == ref)
        if (search.hint is None or search.candidates != self.candidates
                or search.context.fixed_other_coordinates != tuple(pin for pin in request.vector
                                                                  if pin.name != request.active_dependency)):
            raise ValueError("oracle selection static hint belongs to another Slice")
        hint = search.hint
        if request.selection_reason == "static-suspect":
            version = search.points[hint.suspect_index].version
        else:
            if hint.clean_is_anchor:
                raise ValueError("oracle selection cannot execute a read-only static anchor")
            version = search.points[hint.clean_index].version
        if version != request.candidate_version:
            raise ValueError("oracle selection must use the referenced static endpoint")

