"""Highest preparation retained when no complete static request can be formed."""

from __future__ import annotations

from pydantic import model_validator

from pf.resolution import environment_identity_digest, resolution_graph_id
from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import Attempt
from pf.schemas.project import Proposal
from pf.schemas.static import StaticContentUnavailable


class StaticUncollectedBaseline(FrozenSchema):
    attempt: Attempt
    proposal: Proposal
    unavailable: StaticContentUnavailable

    @model_validator(mode="after")
    def validate_capture(self) -> StaticUncollectedBaseline:
        identity = self.attempt.identity
        if (
            identity.requested_resolution != "highest"
            or self.proposal.attempt_id != self.attempt.attempt_id
            or self.proposal.cell != identity.cell
            or self.proposal.snapshot_digest != identity.source_snapshot_digest
            or self.proposal.policy_identity != identity.execution_policy_identity
            or self.proposal.interpreter is None
            or self.proposal.project_plan_digest is None
        ):
            raise ValueError("uncollected static baseline requires its actual highest preparation")
        resolution_graph_id(self.proposal.resolved_graph)
        if self.proposal.proposal_id != environment_identity_digest(
            attempt_id=self.attempt.attempt_id,
            project_plan_digest=self.proposal.project_plan_digest,
            environment_plan_digest=self.proposal.environment_plan_digest,
            graph=self.proposal.resolved_graph,
        ):
            raise ValueError("uncollected static baseline requires its actual highest preparation identity")
        return self
