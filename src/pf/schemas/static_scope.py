"""Portable Run/Cell reference closure for static observations and comparisons."""

from __future__ import annotations

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import ProcessObservation
from pf.schemas.policy import GuidancePolicy
from pf.schemas.project import Cell
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_comparison import (
    SliceAnchorPass,
    StaticComparisonContext, StaticComparisonResult,
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


class StaticPreparationMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    preparation: StaticPreparationEvidence


class StaticConsumerMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    preparation_ref: str = Field(min_length=1)
    fact_ref: str = Field(min_length=1)


class StaticPassMembership(FrozenSchema):
    ref: str = Field(min_length=1)
    evidence: SliceAnchorPass
    preparation_ref: str | None = None
    consumer_ref: str | None = None
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
    run_identity: str = Field(min_length=1)
    cell: Cell
    preparations: tuple[StaticPreparationMembership, ...] = ()
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
        preparations = _index(self.preparations)
        _index(self.passes)
        _index(self.comparisons)
        _index(self.searches)
        _index(self.omissions)
        _index(self.skips)
        _index(self.selections)
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
            preparation = preparations.get(consumer.preparation_ref)
            if member is None or preparation is None or preparation.preparation.proposal.cell != self.cell:
                raise ValueError("static scope consumer must belong to its Cell and fact table")
            StaticConsumerEvidence(
                preparation=preparation.preparation, observation=member.observation,
            )
        for passed in self.passes:
            process = processes.get(passed.process_ref)
            if process is None:
                raise ValueError("static scope PASS references must be closed")
            if passed.consumer_ref is not None and passed.consumer_ref not in consumers:
                raise ValueError("static scope PASS references must be closed")
            if passed.preparation_ref is not None and passed.preparation_ref not in preparations:
                raise ValueError("static scope PASS references must be closed")
            if passed.process_ref in used_processes:
                raise ValueError("static scope PASS must bind its own execution and Proposal")
            used_processes.add(passed.process_ref)
        if self.highest_uncollected is not None and (
            self.highest_reference_ref is not None
            or self.highest_uncollected.proposal.cell != self.cell
        ):
            raise ValueError("static scope uncollected highest must be the sole baseline in its Cell")
        if self.highest_reference_ref is not None:
            highest = consumers.get(self.highest_reference_ref)
            if highest is None:
                raise ValueError("static scope highest reference must bind original preparation")
            preparation = preparations.get(highest.preparation_ref)
            if (
                preparation is None
                or preparation.preparation.attempt.identity.requested_resolution != "highest"
            ):
                raise ValueError("static scope highest reference must bind original preparation")
        identities = set()
        for comparison in self.comparisons:
            if comparison.identity in identities:
                raise ValueError("static scope comparisons must be interned by semantic identity")
            identities.add(comparison.identity)
            if comparison.subject_ref not in consumers:
                raise ValueError("static scope comparison subject is missing")
            if comparison.reference_ref is not None and comparison.reference_ref not in consumers:
                raise ValueError("static scope comparison reference is missing")
            if comparison.anchor_pass_ref is not None and comparison.anchor_pass_ref not in {
                item.ref for item in self.passes
            }:
                raise ValueError("static scope comparison PASS is missing")
        return self

    def preparation(self, ref: str) -> StaticPreparationEvidence:
        member = next((item for item in self.preparations if item.ref == ref), None)
        if member is None:
            raise ValueError("static scope preparation reference is missing")
        return member.preparation

    def consumer(self, ref: str) -> StaticConsumerEvidence:
        consumer = next((item for item in self.consumers if item.ref == ref), None)
        if consumer is None:
            raise ValueError("static scope consumer reference is missing")
        fact = next(item for item in self.facts if item.ref == consumer.fact_ref)
        return StaticConsumerEvidence(
            preparation=self.preparation(consumer.preparation_ref),
            observation=fact.observation,
        )
