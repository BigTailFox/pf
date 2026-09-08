"""Scoped diagnostic comparisons with replayable semantic admission."""

from __future__ import annotations

from collections import Counter
import hashlib
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator
from packaging.version import Version

from pf.schemas.evaluation import VerifierPass, VerifierRun
from pf.schemas.project import Proposal, SelectedCandidate, VersionPin

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.policy import GuidancePolicy
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.ty_fact import TyCheckFact, TyCheckUnavailable
from pf.static_admission import admit_static_consumer_context


class GlobalComparisonContext(FrozenSchema):
    kind: Literal["GLOBAL"] = "GLOBAL"
    highest_proposal_id: str | None


class SliceAnchorPass(FrozenSchema):
    proposal_id: str
    execution_policy_identity: str
    verifier: VerifierPass

    @classmethod
    def from_run(cls, *, proposal: Proposal, run: VerifierRun) -> SliceAnchorPass:
        if not isinstance(run.authoritative, VerifierPass):
            raise ValueError("slice anchor requires configured verifier PASS")
        return cls(proposal_id=proposal.proposal_id,
                   execution_policy_identity=proposal.policy_identity,
                   verifier=run.authoritative)


class SliceComparisonContext(FrozenSchema):
    kind: Literal["SLICE"] = "SLICE"
    dependency: str
    fixed_other_coordinates: tuple[VersionPin, ...]
    window: tuple[SelectedCandidate, ...] = Field(min_length=1)
    anchor_pass: SliceAnchorPass | None

    @model_validator(mode="after")
    def validate_window(self) -> SliceComparisonContext:
        names = tuple(item.name for item in self.fixed_other_coordinates)
        if names != tuple(sorted(set(names))) or self.dependency in names:
            raise ValueError("slice fixed coordinates must be unique and exclude the active dependency")
        if any(item.dependency != self.dependency for item in self.window):
            raise ValueError("slice candidate window must belong to its active dependency")
        versions = tuple(Version(item.version) for item in self.window)
        if versions != tuple(sorted(set(versions))) or any(
            str(version) != item.version for version, item in zip(versions, self.window, strict=True)
        ):
            raise ValueError("slice candidate window must be ordered and unique")
        return self


StaticComparisonContext = Annotated[
    Union[GlobalComparisonContext, SliceComparisonContext], Field(discriminator="kind"),
]


class StaticCompared(FrozenSchema):
    status: Literal["COMPARED"] = "COMPARED"
    state: Literal["STATIC_UNCHANGED", "STATIC_REGRESSION"]
    incremental_identities: tuple[str, ...]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class StaticUncompared(FrozenSchema):
    status: Literal["UNCOMPARED"] = "UNCOMPARED"
    reason: Literal["reference-missing", "reference-unavailable", "anchor-missing", "context-mismatch"]


class StaticComparisonUnavailable(FrozenSchema):
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: str


StaticComparisonResult = Annotated[
    Union[StaticCompared, StaticUncompared, StaticComparisonUnavailable],
    Field(discriminator="status"),
]


def derive_static_comparison(
    *,
    context: StaticComparisonContext,
    subject: StaticConsumerEvidence,
    reference: StaticConsumerEvidence | None,
    guidance: GuidancePolicy,
    uncollected_reference: StaticUncollectedBaseline | None = None,
) -> StaticComparisonResult:
    """Admit the semantic comparison before subtracting any diagnostics.

    Callers must additionally resolve all consumers, fixed highest or slice
    anchor, and PASS provenance in their open Run/Cell scope. This pure function
    never registers a cache ref.
    """
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
        return StaticUncompared(reason="reference-unavailable" if uncollected_reference is not None else "reference-missing")
    baseline = reference.observation.fact
    if isinstance(baseline, TyCheckUnavailable):
        return StaticUncompared(reason="reference-unavailable")
    if (
        subject.observation.observation_policy != guidance.observation
        or not admit_static_consumer_context(reference, subject)
    ):
        return StaticUncompared(reason="context-mismatch")
    if isinstance(context, GlobalComparisonContext):
        if (context.highest_proposal_id != reference.preparation.proposal.proposal_id
                or reference.preparation.attempt.identity.requested_resolution != "highest"):
            return StaticUncompared(reason="context-mismatch")
    else:
        if context.anchor_pass is None:
            return StaticUncompared(reason="anchor-missing")
        if not _admit_slice(context, reference, subject):
            return StaticUncompared(reason="context-mismatch")
    return _subtract(context=context, subject=subject, reference=reference,
                     current=current, baseline=baseline, guidance=guidance)


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
    if ({name: value for name, value in left_vector.items() if name != context.dependency} != fixed
            or {name: value for name, value in right_vector.items() if name != context.dependency} != fixed):
        return False
    if any(Version(item.version) > Version(left_vector[context.dependency]) for item in context.window):
        return False
    # The directly verified upper point is a read-only anchor, including a
    # highest preparation or a point outside the candidate snapshot. Its exact
    # self-comparison does not claim candidate membership or a new execution.
    if subject == reference:
        return True
    if right.attempt.identity.requested_resolution != "exact-vector":
        return False
    selected = next((item for item in right.selected_candidates or ()
                     if item.dependency == context.dependency), None)
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


class StaticComparisonDocument(FrozenSchema):
    """Offline semantic replay; scope membership remains an enclosing concern."""

    context: StaticComparisonContext
    subject: StaticConsumerEvidence
    reference: StaticConsumerEvidence | None
    guidance: GuidancePolicy
    result: StaticComparisonResult
    uncollected_reference: StaticUncollectedBaseline | None

    @classmethod
    def compare(
        cls, *, context: StaticComparisonContext, subject: StaticConsumerEvidence,
        reference: StaticConsumerEvidence | None, guidance: GuidancePolicy,
        uncollected_reference: StaticUncollectedBaseline | None = None,
    ) -> StaticComparisonDocument:
        return cls(
            context=context, subject=subject, reference=reference, guidance=guidance,
            uncollected_reference=uncollected_reference,
            result=derive_static_comparison(context=context, subject=subject,
                                            reference=reference, guidance=guidance,
                                            uncollected_reference=uncollected_reference),
        )

    @property
    def identity(self) -> str:
        """Intern semantic comparison evidence independently of local scope names."""
        def observed(consumer: StaticConsumerEvidence):
            return {
                "kind": "observed",
                "proposal": consumer.preparation.proposal.proposal_id,
                "subject": consumer.observation.subject.identity,
                "fact": consumer.observation.fact.identity,
            }

        if self.reference is not None:
            reference = observed(self.reference)
        elif self.uncollected_reference is not None:
            reference = {
                "kind": "uncollected",
                "proposal": self.uncollected_reference.proposal.proposal_id,
                "unavailable": self.uncollected_reference.unavailable.model_dump(mode="json"),
            }
        else:
            reference = {"kind": "missing"}
        payload = {
            "context": self.context.model_dump(mode="json"),
            "subject": observed(self.subject),
            "reference": reference,
            "guidance": self.guidance.identity,
            "result": self.result.model_dump(mode="json"),
        }
        return hashlib.sha256(
            b"pf:static-comparison-document:v1\0" + canonical_identity_json(payload)
        ).hexdigest()

    @model_validator(mode="after")
    def validate_replay(self) -> StaticComparisonDocument:
        expected = derive_static_comparison(
            context=self.context, subject=self.subject, reference=self.reference,
            guidance=self.guidance, uncollected_reference=self.uncollected_reference,
        )
        if self.result != expected:
            raise ValueError("saved static comparison does not match admitted evidence")
        return self
