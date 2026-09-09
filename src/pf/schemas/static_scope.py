"""Portable Run/Cell reference closure for static observations and comparisons."""

from __future__ import annotations

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import ProcessObservation, execution_terminal
from pf.schemas.policy import GuidancePolicy
from pf.schemas.project import Cell
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_comparison import (
    GlobalComparisonContext, SliceComparisonContext, SliceAnchorPass,
    StaticComparisonContext, StaticComparisonDocument, StaticComparisonResult,
)
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.static_search import StaticSearchAudit, StaticPhaseOmission, StaticPhaseSkip, OracleSelectionAudit
from pf.schemas.ty_fact import TyFactDocument, validate_ty_fact_process


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
