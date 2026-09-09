from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import (
    ProcessObservation, ProcessResult, OperationFailure, ExecutionFailure,
    UvUnsatAttribution, NormalExit, classify_operation_failure, execution_terminal,
)
from pf.schemas.project import (
    HarnessSatisfaction,
    ResolvedNode,
)
from pf.schemas.resolution import (
    UV_DIAGNOSTIC_PROFILES,
    UV_PROTOCOL_IDENTITY,
    UV_SUPPORTED_VERSIONS,
    ResolutionArtifact,
    ResolutionContext,
    ResolutionPackage,
    ResolutionPlanEvidence,
    ResolutionRunContext,
    environment_identity_digest,
    identity_digest,
    resolution_graph_id,
    resolution_request_digest,
    resolution_semantic_digest,
)


class NativeResolutionPlan(FrozenSchema):
    format: Literal["pylock.toml"] = "pylock.toml"
    content: str = Field(exclude=True, repr=False)
    digest: str

    @classmethod
    def from_content(cls, content: str) -> NativeResolutionPlan:
        return cls(
            content=content,
            digest=hashlib.sha256(b"pf:native-pylock:v1\0" + content.encode()).hexdigest(),
        )

    @model_validator(mode="after")
    def validate_native_plan(self) -> "NativeResolutionPlan":
        expected = hashlib.sha256(
            b"pf:native-pylock:v1\0" + self.content.encode()
        ).hexdigest()
        if self.digest != expected:
            raise ValueError("native resolution plan digest does not match content")
        return self


def resolution_plan_digest(
    *,
    kind: str,
    request_digest: str,
    context: ResolutionContext,
    packages: tuple[ResolutionPackage, ...],
    direct_harness: tuple[HarnessSatisfaction, ...],
    native_digest: str,
) -> str:
    return identity_digest(
        b"pf:resolution-plan:v1\0",
        {
            "kind": kind,
            "request_digest": request_digest,
            "context": context.model_dump(mode="json"),
            "packages": [item.model_dump(mode="json") for item in packages],
            "direct_harness": [
                item.model_dump(mode="json") for item in direct_harness
            ],
            "native_digest": native_digest,
        },
    )


class ResolutionPlan(ResolutionPlanEvidence):
    status: Literal["PLAN"] = "PLAN"
    native: NativeResolutionPlan
    process: ProcessResult = Field(exclude=True, repr=False)
    digest: str

    @classmethod
    def from_evidence(
        cls,
        *,
        kind: Literal["project", "environment"],
        request_digest: str,
        context: ResolutionContext,
        packages: tuple[ResolutionPackage, ...],
        direct_harness: tuple[HarnessSatisfaction, ...],
        native: NativeResolutionPlan,
        process: ProcessResult,
    ) -> "ResolutionPlan":
        return cls(
            kind=kind,
            request_digest=request_digest,
            context=context,
            packages=packages,
            direct_harness=direct_harness,
            native=native,
            process=process,
            semantic_digest=resolution_semantic_digest(
                kind=kind,
                request_digest=request_digest,
                context=context,
                packages=packages,
                direct_harness=direct_harness,
            ),
            digest=resolution_plan_digest(
                kind=kind,
                request_digest=request_digest,
                context=context,
                packages=packages,
                direct_harness=direct_harness,
                native_digest=native.digest,
            ),
        )

    @model_validator(mode="after")
    def validate_plan(self) -> "ResolutionPlan":
        if execution_terminal(self.process) != NormalExit(exit_code=0):
            raise ValueError("resolution plan requires a successful process")
        expected = resolution_plan_digest(
            kind=self.kind,
            request_digest=self.request_digest,
            context=self.context,
            packages=self.packages,
            direct_harness=self.direct_harness,
            native_digest=self.native.digest,
        )
        if self.digest != expected:
            raise ValueError("resolution plan digest does not match its evidence")
        return self


class EnvironmentIdentity(FrozenSchema):
    attempt_id: str
    project_plan_digest: str
    environment_plan_digest: str | None
    graph: tuple[ResolvedNode, ...]
    digest: str

    @classmethod
    def from_plans(
        cls,
        *,
        attempt_id: str,
        project_plan: ResolutionPlan,
        environment_plan: ResolutionPlan | None,
        graph: tuple[ResolvedNode, ...],
    ) -> "EnvironmentIdentity":
        environment_digest = (
            environment_plan.semantic_digest if environment_plan is not None else None
        )
        return cls(
            attempt_id=attempt_id,
            project_plan_digest=project_plan.semantic_digest,
            environment_plan_digest=environment_digest,
            graph=graph,
            digest=environment_identity_digest(
                attempt_id=attempt_id,
                project_plan_digest=project_plan.semantic_digest,
                environment_plan_digest=environment_digest,
                graph=graph,
            ),
        )

    @model_validator(mode="after")
    def validate_environment_identity(self) -> "EnvironmentIdentity":
        resolution_graph_id(self.graph)
        expected = environment_identity_digest(
            attempt_id=self.attempt_id,
            project_plan_digest=self.project_plan_digest,
            environment_plan_digest=self.environment_plan_digest,
            graph=self.graph,
        )
        if self.digest != expected:
            raise ValueError("environment identity digest does not match its evidence")
        return self


class ResolutionFailure(FrozenSchema):
    status: Literal["RESOLUTION_FAILURE"] = "RESOLUTION_FAILURE"
    stage: Literal["resolve-project", "resolve-environment"]
    request_digest: str
    context: ResolutionContext
    failure: OperationFailure
    process: ProcessObservation | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_resolution_failure(self) -> "ResolutionFailure":
        if not self.request_digest:
            raise ValueError("resolution failure requires request evidence")
        classify_operation_failure(self.stage, self.failure)
        if isinstance(self.failure, ExecutionFailure) and isinstance(self.failure.attribution, UvUnsatAttribution):
            attribution = self.failure.attribution
            if (
                attribution.tool_version != self.context.run.uv_version
                or attribution.protocol != self.context.run.protocol_identity
                or attribution.profile != self.context.run.qualification_profile
            ):
                raise ValueError("UNSAT attribution does not match runtime context")
        return self


ResolutionOutcome = Annotated[
    Union[ResolutionPlan, ResolutionFailure],
    Field(discriminator="status"),
]


class InstalledResolution(FrozenSchema):
    status: Literal["INSTALLED"] = "INSTALLED"
    plan_digest: str
    process: ProcessResult

    @model_validator(mode="after")
    def validate_installed_resolution(self) -> "InstalledResolution":
        if not self.plan_digest:
            raise ValueError("installed resolution requires its plan identity")
        if execution_terminal(self.process) != NormalExit(exit_code=0):
            raise ValueError("installed resolution requires a successful process")
        return self


class InstallFailure(FrozenSchema):
    status: Literal["INSTALL_FAILURE"] = "INSTALL_FAILURE"
    plan_digest: str
    failure: OperationFailure
    stage: Literal["install-project", "install-environment"]
    process: ProcessObservation | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_install_failure(self) -> "InstallFailure":
        if not self.plan_digest:
            raise ValueError("install failure requires its plan identity")
        classify_operation_failure(self.stage, self.failure)
        return self


InstallOutcome = Annotated[
    Union[InstalledResolution, InstallFailure],
    Field(discriminator="status"),
]


__all__ = [
    "UV_DIAGNOSTIC_PROFILES",
    "UV_PROTOCOL_IDENTITY",
    "UV_SUPPORTED_VERSIONS",
    "EnvironmentIdentity",
    "InstallFailure",
    "InstallOutcome",
    "InstalledResolution",
    "NativeResolutionPlan",
    "ResolutionArtifact",
    "ResolutionContext",
    "ResolutionFailure",
    "ResolutionOutcome",
    "ResolutionPackage",
    "ResolutionPlan",
    "ResolutionPlanEvidence",
    "ResolutionRunContext",
    "environment_identity_digest",
    "resolution_graph_id",
    "resolution_plan_digest",
    "resolution_request_digest",
    "resolution_semantic_digest",
]
