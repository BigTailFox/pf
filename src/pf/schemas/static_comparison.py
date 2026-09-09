"""Scoped diagnostic comparison records. Semantic derive lives in pf.static."""

from __future__ import annotations

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


class StaticComparisonDocument(FrozenSchema):
    """Saved comparison record. Admission and subtraction are not replayed here."""

    context: StaticComparisonContext
    subject: StaticConsumerEvidence
    reference: StaticConsumerEvidence | None
    guidance: GuidancePolicy
    result: StaticComparisonResult
    uncollected_reference: StaticUncollectedBaseline | None

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
