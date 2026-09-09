"""Portable Run/Cell reference closure for static observations and comparisons."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_serializer, model_validator

from pf.resolution import ResolutionPlanEvidence
from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import Attempt, ProcessObservation, execution_terminal
from pf.schemas.policy import ExecutionPolicy, GuidancePolicy, TyObservationPolicy
from pf.schemas.project import (
    Cell, HarnessBaseline, HarnessRequirement, InterpreterIdentity, Proposal,
    RequirementDeclaration, SelectedCandidate, SourcePlan,
)
from pf.schemas.static import (
    StaticAnalysisLayout, StaticContentManifest, StaticContentPath,
    StaticInstalledNode, StaticPackageMapping, StaticProcessContext,
    StaticSubject,
)
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_comparison import (
    GlobalComparisonContext, SliceComparisonContext, SliceAnchorPass,
    StaticComparisonContext, StaticComparisonDocument, StaticComparisonResult,
)
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.static_search import StaticSearchAudit, StaticPhaseOmission, StaticPhaseSkip, OracleSelectionAudit
from pf.schemas.ty_fact import TyFact, TyFactDocument, validate_ty_fact_process


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StaticProcessRecord(FrozenSchema):
    ref: str = Field(min_length=1)
    process: ProcessObservation


class StaticFactMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    observation: TyFactDocument
    producer_ref: str = Field(min_length=1)
    process_ref: str = Field(min_length=1)


class StaticConsumerMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    preparation: StaticPreparationEvidence
    fact_ref: str = Field(min_length=1)


class StaticPassMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    evidence: SliceAnchorPass
    consumer_ref: str = Field(min_length=1)
    process_ref: str = Field(min_length=1)


class StaticComparisonMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_ref: str = Field(min_length=1)
    reference_ref: str | None
    anchor_pass_ref: str | None
    context: StaticComparisonContext
    guidance: GuidancePolicy
    result: StaticComparisonResult


class InternedStaticContent(FrozenSchema):
    identity: Digest
    manifest: StaticContentManifest

    @model_validator(mode="after")
    def validate_identity(self) -> InternedStaticContent:
        if self.identity != self.manifest.identity:
            raise ValueError("interned static content identity must match its manifest")
        return self


class InternedStaticSource(FrozenSchema):
    snapshot_identity: Digest
    content_identity: Digest
    packages: tuple[StaticPackageMapping, ...] = Field(min_length=1)
    source_plan: SourcePlan


class InternedStaticTarget(FrozenSchema):
    cell: Cell
    interpreter: InterpreterIdentity
    content_identity: Digest
    executable: StaticContentPath
    stdlib_roots: tuple[StaticContentPath, ...] = Field(min_length=1)


class InternedStaticInstalledWorld(FrozenSchema):
    content_identity: Digest
    nodes: tuple[StaticInstalledNode, ...] = Field(min_length=1)
    support_files: tuple[StaticContentPath, ...]


class InternedStaticConfiguration(FrozenSchema):
    content_identity: Digest
    effective_file: StaticContentPath
    files_in_precedence_order: tuple[StaticContentPath, ...]
    discovery_boundaries: tuple[StaticContentPath, ...]
    external_roots: tuple[StaticContentPath, ...]


class InternedStaticSubject(FrozenSchema):
    identity: Digest
    projection: Literal["static-subject-v1"]
    source: InternedStaticSource
    target: InternedStaticTarget
    installed_world: InternedStaticInstalledWorld
    analysis_layout: StaticAnalysisLayout
    configuration: InternedStaticConfiguration
    process_context: StaticProcessContext


class InternedStaticFact(FrozenSchema):
    """Document-level raw observation; local membership stays on the scope."""

    identity: Digest
    subject_identity: Digest
    observation_policy: TyObservationPolicy
    fact: TyFact

    @model_validator(mode="after")
    def validate_identity(self) -> InternedStaticFact:
        if self.identity != self.fact.identity:
            raise ValueError("interned static fact identity must match its observation")
        if self.fact.subject_identity != self.subject_identity:
            raise ValueError("interned static fact must reference its subject")
        if self.fact.observation_policy_identity != self.observation_policy.identity:
            raise ValueError("interned static fact must match its observation policy")
        return self


class InternedStaticComparison(FrozenSchema):
    """Semantic comparison payload; local subject/anchor refs stay on the scope."""

    identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    context: StaticComparisonContext
    guidance: GuidancePolicy
    result: StaticComparisonResult


class StaticPreparationIntern(FrozenSchema):
    """Consumer preparation without the duplicated StaticSubject preimage."""

    attempt: Attempt
    proposal: Proposal
    project_plan: ResolutionPlanEvidence
    environment_plan: ResolutionPlanEvidence | None
    harness_requirements: tuple[HarnessRequirement, ...]
    harness_baseline: HarnessBaseline
    selected_candidates: tuple[SelectedCandidate, ...] | None
    execution_policy: ExecutionPolicy
    declarations: tuple[RequirementDeclaration, ...]
    selected_test_group: str | None

    @model_serializer(mode="wrap")
    def preserve_required_null(self, handler):
        result = handler(self)
        result["environment_plan"] = (
            None if self.environment_plan is None
            else self.environment_plan.model_dump(mode="json")
        )
        result["selected_candidates"] = (
            None if self.selected_candidates is None
            else [item.model_dump(mode="json") for item in self.selected_candidates]
        )
        result["selected_test_group"] = self.selected_test_group
        return result


class StaticConsumerWire(FrozenSchema):
    ref: str = Field(min_length=1)
    fact_ref: str = Field(min_length=1)
    subject_identity: Digest
    preparation: StaticPreparationIntern


class StaticFactRef(FrozenSchema):
    ref: str = Field(min_length=1)
    observation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_ref: str = Field(min_length=1)
    process_ref: str = Field(min_length=1)


class StaticComparisonRef(FrozenSchema):
    ref: str = Field(min_length=1)
    identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_ref: str = Field(min_length=1)
    reference_ref: str | None
    anchor_pass_ref: str | None

    @model_serializer(mode="wrap")
    def preserve_required_null(self, handler):
        result = handler(self)
        result["reference_ref"] = self.reference_ref
        result["anchor_pass_ref"] = self.anchor_pass_ref
        return result


class StaticScopeWire(FrozenSchema):
    """Persisted scope membership; observations and comparisons are interned above it."""

    scope_ref: str = Field(min_length=1)
    cell: Cell
    processes: tuple[StaticProcessRecord, ...]
    facts: tuple[StaticFactRef, ...]
    consumers: tuple[StaticConsumerWire, ...]
    passes: tuple[StaticPassMembership, ...]
    comparisons: tuple[StaticComparisonRef, ...]
    highest_reference_ref: str | None
    highest_uncollected: StaticUncollectedBaseline | None
    searches: tuple[StaticSearchAudit, ...] = ()
    omissions: tuple[StaticPhaseOmission, ...] = ()
    skips: tuple[StaticPhaseSkip, ...] = ()
    selections: tuple[OracleSelectionAudit, ...] = ()

    @model_serializer(mode="wrap")
    def preserve_required_null(self, handler):
        result = handler(self)
        result["highest_reference_ref"] = self.highest_reference_ref
        result["highest_uncollected"] = (
            None if self.highest_uncollected is None
            else self.highest_uncollected.model_dump(mode="json")
        )
        return result


class InternedStaticAudit(FrozenSchema):
    contents: tuple[InternedStaticContent, ...]
    subjects: tuple[InternedStaticSubject, ...]
    facts: tuple[InternedStaticFact, ...]
    comparisons: tuple[InternedStaticComparison, ...]
    scopes: tuple[StaticScopeWire, ...]


def _index(records):
    result = {record.ref: record for record in records}
    if len(result) != len(records):
        raise ValueError("static scope references must be unique within their table")
    return result


class StaticScopeEvidence(FrozenSchema):
    scope_ref: str = Field(min_length=1)
    cell: Cell
    processes: tuple[StaticProcessRecord, ...]
    facts: tuple[StaticFactMembership, ...]
    consumers: tuple[StaticConsumerMembership, ...]
    passes: tuple[StaticPassMembership, ...]
    comparisons: tuple[StaticComparisonMembership, ...]
    highest_reference_ref: str | None
    highest_uncollected: StaticUncollectedBaseline | None
    searches: tuple[StaticSearchAudit, ...] = ()
    omissions: tuple[StaticPhaseOmission, ...] = ()
    skips: tuple[StaticPhaseSkip, ...] = ()
    selections: tuple[OracleSelectionAudit, ...] = ()

    @model_validator(mode="after")
    def validate_closure(self) -> StaticScopeEvidence:
        processes, facts = _index(self.processes), _index(self.facts)
        consumers = _index(self.consumers)
        _index(self.passes)
        _index(self.comparisons)
        keys = set()
        used_processes = set()
        for member in self.facts:
            key = (member.observation.subject.identity, member.observation.observation_policy.identity)
            if key in keys:
                raise ValueError("static scope must have one observation per raw request")
            keys.add(key)
            producer = consumers.get(member.producer_ref)
            process = processes.get(member.process_ref)
            if producer is None or producer.fact_ref != member.ref or process is None:
                raise ValueError("static scope raw producer references must be closed")
            if member.process_ref in used_processes:
                raise ValueError("static scope cannot reuse one process for distinct observations")
            used_processes.add(member.process_ref)
            validate_ty_fact_process(member.observation, process.process)
        for consumer in self.consumers:
            member = facts.get(consumer.fact_ref)
            if member is None or consumer.preparation.proposal.cell != self.cell:
                raise ValueError("static scope consumer must belong to its Cell and fact table")
            StaticConsumerEvidence(preparation=consumer.preparation, observation=member.observation)
        for passed in self.passes:
            consumer, process = consumers.get(passed.consumer_ref), processes.get(passed.process_ref)
            if consumer is None or process is None:
                raise ValueError("static scope PASS references must be closed")
            if (
                passed.evidence.proposal_id != consumer.preparation.proposal.proposal_id
                or passed.evidence.execution_policy_identity != consumer.preparation.execution_policy.identity
                or execution_terminal(process.process) != passed.evidence.verifier.terminal
                or passed.process_ref in used_processes
            ):
                raise ValueError("static scope PASS must bind its own execution and Proposal")
            used_processes.add(passed.process_ref)
        if self.highest_uncollected is not None and (
            self.highest_reference_ref is not None
            or self.highest_uncollected.proposal.cell != self.cell
        ):
            raise ValueError("static scope uncollected highest must be the sole baseline in its Cell")
        if self.highest_reference_ref is not None:
            highest = consumers.get(self.highest_reference_ref)
            if highest is None or highest.preparation.attempt.identity.requested_resolution != "highest":
                raise ValueError("static scope highest reference must bind original preparation")
        identities = set()
        for comparison in self.comparisons:
            replay = self.compare(
                scope_ref=self.scope_ref, subject_ref=comparison.subject_ref,
                reference_ref=comparison.reference_ref, context=comparison.context,
                guidance=comparison.guidance, anchor_pass_ref=comparison.anchor_pass_ref,
            )
            if replay.identity != comparison.identity or replay.result != comparison.result:
                raise ValueError("static scope comparison must match admitted semantic evidence")
            if comparison.identity in identities:
                raise ValueError("static scope comparisons must be interned by semantic identity")
            identities.add(comparison.identity)
        _index(self.searches)
        for search in self.searches:
            search.validate_in_scope(self)
        _index(self.omissions)
        for omission in self.omissions:
            omission.validate_in_scope(self)
        _index(self.skips)
        for skip in self.skips:
            skip.validate_in_scope(self)
        _index(self.selections)
        for selection in self.selections:
            selection.validate_in_scope(self)
        return self

    def consumer(self, ref: str) -> StaticConsumerEvidence:
        consumer = next((item for item in self.consumers if item.ref == ref), None)
        if consumer is None:
            raise ValueError("static scope consumer reference is missing")
        fact = next(item for item in self.facts if item.ref == consumer.fact_ref)
        return StaticConsumerEvidence(preparation=consumer.preparation, observation=fact.observation)

    def compare(
        self, *, scope_ref: str, subject_ref: str, reference_ref: str | None,
        context: StaticComparisonContext, guidance: GuidancePolicy,
        anchor_pass_ref: str | None = None,
    ) -> StaticComparisonDocument:
        """Resolve the complete local closure before shared semantic derivation.

        Runtime owners translate rejected foreign references to context-mismatch.
        Offline readers reject dangling/cross-scope associations as invalid data.
        """
        if scope_ref != self.scope_ref:
            raise ValueError("static comparison references belong to another scope")
        subject = self.consumer(subject_ref)
        reference = self.consumer(reference_ref) if reference_ref is not None else None
        uncollected = None
        if isinstance(context, GlobalComparisonContext):
            uncollected = self.highest_uncollected
            if uncollected is not None and context.highest_proposal_id != uncollected.proposal.proposal_id:
                raise ValueError("GLOBAL comparison must resolve the uncollected highest Proposal")
            if reference_ref != self.highest_reference_ref or anchor_pass_ref is not None:
                raise ValueError("GLOBAL comparison must resolve the fixed highest reference")
        elif isinstance(context, SliceComparisonContext):
            if context.anchor_pass is None:
                if anchor_pass_ref is not None:
                    raise ValueError("missing slice PASS cannot carry a PASS reference")
            else:
                passed = next((item for item in self.passes if item.ref == anchor_pass_ref), None)
                if (passed is None or passed.consumer_ref != reference_ref
                        or passed.evidence != context.anchor_pass):
                    raise ValueError("SLICE anchor PASS must resolve in the same scope")
        return StaticComparisonDocument.compare(context=context, subject=subject,
                                                 reference=reference, guidance=guidance,
                                                 uncollected_reference=uncollected)


def intern_static_scopes(scopes: tuple[StaticScopeEvidence, ...]) -> InternedStaticAudit:
    """Share identical raw facts, subjects, and content without joining membership."""
    contents: dict[str, InternedStaticContent] = {}
    subjects: dict[str, InternedStaticSubject] = {}
    facts: dict[str, InternedStaticFact] = {}
    comparisons: dict[str, InternedStaticComparison] = {}
    wires = []
    for scope in scopes:
        for member in scope.facts:
            _intern_subject(member.observation.subject, contents, subjects)
            interned = InternedStaticFact(
                identity=member.observation.fact_identity,
                subject_identity=member.observation.subject.identity,
                observation_policy=member.observation.observation_policy,
                fact=member.observation.fact,
            )
            existing = facts.get(interned.identity)
            if existing is not None and existing != interned:
                raise ValueError("interned static fact identity collision")
            facts[interned.identity] = interned
        for member in scope.comparisons:
            interned = InternedStaticComparison(
                identity=member.identity, context=member.context,
                guidance=member.guidance, result=member.result,
            )
            existing = comparisons.get(interned.identity)
            if existing is not None and existing != interned:
                raise ValueError("interned static comparison identity collision")
            comparisons[interned.identity] = interned
        wires.append(StaticScopeWire(
            scope_ref=scope.scope_ref, cell=scope.cell, processes=scope.processes,
            facts=tuple(
                StaticFactRef(
                    ref=member.ref, observation_identity=member.observation.fact_identity,
                    producer_ref=member.producer_ref, process_ref=member.process_ref,
                )
                for member in scope.facts
            ),
            consumers=tuple(_consumer_wire(item) for item in scope.consumers),
            passes=scope.passes,
            comparisons=tuple(
                StaticComparisonRef(
                    ref=member.ref, identity=member.identity, subject_ref=member.subject_ref,
                    reference_ref=member.reference_ref, anchor_pass_ref=member.anchor_pass_ref,
                )
                for member in scope.comparisons
            ),
            highest_reference_ref=scope.highest_reference_ref,
            highest_uncollected=scope.highest_uncollected,
            searches=scope.searches, omissions=scope.omissions,
            skips=scope.skips, selections=scope.selections,
        ))
    return InternedStaticAudit(
        contents=tuple(contents[key] for key in sorted(contents)),
        subjects=tuple(subjects[key] for key in sorted(subjects)),
        facts=tuple(facts[key] for key in sorted(facts)),
        comparisons=tuple(comparisons[key] for key in sorted(comparisons)),
        scopes=tuple(wires),
    )


def resolve_static_scopes(
    facts: tuple[InternedStaticFact, ...],
    comparisons: tuple[InternedStaticComparison, ...],
    scopes: tuple[StaticScopeWire, ...],
    *,
    contents: tuple[InternedStaticContent, ...] = (),
    subjects: tuple[InternedStaticSubject, ...] = (),
) -> tuple[StaticScopeEvidence, ...]:
    """Replay interned payloads into independent scope membership."""
    fact_ids = [item.identity for item in facts]
    if fact_ids != sorted(set(fact_ids)):
        raise ValueError("interned static facts must be unique and sorted by identity")
    comparison_ids = [item.identity for item in comparisons]
    if comparison_ids != sorted(set(comparison_ids)):
        raise ValueError("interned static comparisons must be unique and sorted by identity")
    content_ids = [item.identity for item in contents]
    if content_ids != sorted(set(content_ids)):
        raise ValueError("interned static contents must be unique and sorted by identity")
    subject_ids = [item.identity for item in subjects]
    if subject_ids != sorted(set(subject_ids)):
        raise ValueError("interned static subjects must be unique and sorted by identity")
    manifests = {item.identity: item.manifest for item in contents}
    inflated_subjects = {
        item.identity: _inflate_subject(item, manifests) for item in subjects
    }
    observations: dict[str, TyFactDocument] = {}
    referenced_subjects: set[str] = set()
    for item in facts:
        subject = inflated_subjects.get(item.subject_identity)
        if subject is None:
            raise ValueError("static fact references a missing interned subject")
        referenced_subjects.add(item.subject_identity)
        observations[item.identity] = TyFactDocument(
            subject=subject,
            observation_policy=item.observation_policy,
            fact=item.fact,
            fact_identity=item.identity,
        )
    payloads = {item.identity: item for item in comparisons}
    referenced_facts: set[str] = set()
    referenced_comparisons: set[str] = set()
    referenced_contents: set[str] = set()
    for interned in subjects:
        referenced_contents.update(_subject_content_identities(interned))
    resolved = []
    for scope in scopes:
        memberships = []
        for member in scope.facts:
            observation = observations.get(member.observation_identity)
            if observation is None:
                raise ValueError("static fact membership references a missing interned observation")
            referenced_facts.add(member.observation_identity)
            memberships.append(StaticFactMembership(
                ref=member.ref, observation=observation,
                producer_ref=member.producer_ref, process_ref=member.process_ref,
            ))
        facts_by_ref = {member.ref: member for member in memberships}
        consumers = []
        for member in scope.consumers:
            fact = facts_by_ref.get(member.fact_ref)
            if fact is None:
                raise ValueError("static consumer references a missing fact")
            if fact.observation.subject.identity != member.subject_identity:
                raise ValueError("static consumer subject must match its fact")
            consumers.append(StaticConsumerMembership(
                ref=member.ref,
                fact_ref=member.fact_ref,
                preparation=_inflate_preparation(member.preparation, fact.observation.subject),
            ))
        comparison_members = []
        for member in scope.comparisons:
            payload = payloads.get(member.identity)
            if payload is None:
                raise ValueError("static comparison membership references a missing interned comparison")
            referenced_comparisons.add(member.identity)
            comparison_members.append(StaticComparisonMembership(
                ref=member.ref, identity=member.identity, subject_ref=member.subject_ref,
                reference_ref=member.reference_ref, anchor_pass_ref=member.anchor_pass_ref,
                context=payload.context, guidance=payload.guidance, result=payload.result,
            ))
        resolved.append(StaticScopeEvidence(
            scope_ref=scope.scope_ref, cell=scope.cell, processes=scope.processes,
            facts=tuple(memberships), consumers=tuple(consumers), passes=scope.passes,
            comparisons=tuple(comparison_members),
            highest_reference_ref=scope.highest_reference_ref,
            highest_uncollected=scope.highest_uncollected,
            searches=scope.searches, omissions=scope.omissions,
            skips=scope.skips, selections=scope.selections,
        ))
    if referenced_facts != set(fact_ids):
        raise ValueError("interned static facts must match scope membership")
    if referenced_comparisons != set(comparison_ids):
        raise ValueError("interned static comparisons must match scope membership")
    if referenced_subjects != set(subject_ids):
        raise ValueError("interned static subjects must match fact membership")
    if referenced_contents != set(content_ids):
        raise ValueError("interned static contents must match subject membership")
    return tuple(resolved)


def _intern_manifest(
    manifest: StaticContentManifest, contents: dict[str, InternedStaticContent],
) -> str:
    interned = InternedStaticContent(identity=manifest.identity, manifest=manifest)
    existing = contents.get(interned.identity)
    if existing is not None and existing != interned:
        raise ValueError("interned static content identity collision")
    contents[interned.identity] = interned
    return interned.identity


def _intern_subject(
    subject: StaticSubject,
    contents: dict[str, InternedStaticContent],
    subjects: dict[str, InternedStaticSubject],
) -> InternedStaticSubject:
    del contents, subjects
    raise ValueError("static-subject-v2 is not interned")


def _consumer_wire(member: StaticConsumerMembership) -> StaticConsumerWire:
    preparation = member.preparation
    return StaticConsumerWire(
        ref=member.ref,
        fact_ref=member.fact_ref,
        subject_identity=preparation.subject.identity,
        preparation=StaticPreparationIntern(
            attempt=preparation.attempt,
            proposal=preparation.proposal,
            project_plan=preparation.project_plan,
            environment_plan=preparation.environment_plan,
            harness_requirements=preparation.harness_requirements,
            harness_baseline=preparation.harness_baseline,
            selected_candidates=preparation.selected_candidates,
            execution_policy=preparation.execution_policy,
            declarations=preparation.declarations,
            selected_test_group=preparation.selected_test_group,
        ),
    )


def _subject_content_identities(subject: InternedStaticSubject) -> tuple[str, ...]:
    return (
        subject.source.content_identity,
        subject.target.content_identity,
        subject.installed_world.content_identity,
        subject.configuration.content_identity,
    )


def _inflate_subject(
    interned: InternedStaticSubject, manifests: dict[str, StaticContentManifest],
) -> StaticSubject:
    del interned, manifests
    raise ValueError("static-subject-v2 is not interned")


def _inflate_preparation(
    interned: StaticPreparationIntern, subject: StaticSubject,
) -> StaticPreparationEvidence:
    del interned, subject
    raise ValueError("static-subject-v2 is not interned")
