from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Annotated, Any, Literal, Union

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.evaluation import FailureCause, ProcessResult
from pf.schemas.project import (
    Cell,
    HarnessSelection,
    ResolvedNode,
    SourceIdentity,
)


UV_PROTOCOL_IDENTITY = "uv-pip-compile-pylock-v1"
UV_DIAGNOSTIC_PROFILES = {
    "0.12.5": "uv-diagnostics-0.12.5-v1",
}
UV_SUPPORTED_VERSIONS = frozenset(UV_DIAGNOSTIC_PROFILES)


def _digest(prefix: bytes, value: object) -> str:
    return hashlib.sha256(prefix + canonical_identity_json(value)).hexdigest()


class ResolutionRunContext(FrozenSchema):
    uv_version: str
    protocol_identity: Literal["uv-pip-compile-pylock-v1"] = UV_PROTOCOL_IDENTITY
    qualification_profile: str = ""
    release_cutoff: str
    cache_policy_identity: Literal["shared-run-no-refresh-v1"] = (
        "shared-run-no-refresh-v1"
    )

    @model_validator(mode="before")
    @classmethod
    def populate_qualification_profile(cls, value: Any, /) -> Any:
        if isinstance(value, dict) and "qualification_profile" not in value:
            version = value.get("uv_version")
            profile = UV_DIAGNOSTIC_PROFILES.get(version)
            if profile is not None:
                return {**value, "qualification_profile": profile}
        return value

    @model_validator(mode="after")
    def validate_run_context(self) -> "ResolutionRunContext":
        expected_profile = UV_DIAGNOSTIC_PROFILES.get(self.uv_version)
        if expected_profile is None:
            raise ValueError(f"unsupported uv version: {self.uv_version}")
        if self.qualification_profile != expected_profile:
            raise ValueError("uv qualification profile does not match its version")
        try:
            cutoff = datetime.fromisoformat(self.release_cutoff.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("resolution cutoff must be an ISO-8601 timestamp") from error
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("resolution cutoff must include a UTC offset")
        return self


def resolution_context_digest(
    *,
    run: ResolutionRunContext,
    cell: Cell,
    source_policy_identity: str,
    prerelease_policy: str,
) -> str:
    return _digest(
        b"pf:resolution-context:v1\0",
        {
            "run": run.model_dump(mode="json"),
            "cell": cell.model_dump(mode="json"),
            "source_policy_identity": source_policy_identity,
            "resolution_policy_identity": "uv-highest-normalized-input-v1",
            "prerelease_policy": prerelease_policy,
            "yanked_policy_identity": "uv-default-v1",
        },
    )


class ResolutionContext(FrozenSchema):
    run: ResolutionRunContext
    cell: Cell
    source_policy_identity: str
    resolution_policy_identity: Literal["uv-highest-normalized-input-v1"] = (
        "uv-highest-normalized-input-v1"
    )
    prerelease_policy: Literal["allow", "explicit"]
    yanked_policy_identity: Literal["uv-default-v1"] = "uv-default-v1"
    digest: str

    @classmethod
    def from_inputs(
        cls,
        *,
        run: ResolutionRunContext,
        cell: Cell,
        source_policy_identity: str,
        allow_prereleases: bool,
    ) -> "ResolutionContext":
        prerelease_policy = "allow" if allow_prereleases else "explicit"
        return cls(
            run=run,
            cell=cell,
            source_policy_identity=source_policy_identity,
            prerelease_policy=prerelease_policy,
            digest=resolution_context_digest(
                run=run,
                cell=cell,
                source_policy_identity=source_policy_identity,
                prerelease_policy=prerelease_policy,
            ),
        )

    @model_validator(mode="after")
    def validate_context(self) -> "ResolutionContext":
        if not self.source_policy_identity:
            raise ValueError("resolution source policy identity cannot be empty")
        expected = resolution_context_digest(
            run=self.run,
            cell=self.cell,
            source_policy_identity=self.source_policy_identity,
            prerelease_policy=self.prerelease_policy,
        )
        if self.digest != expected:
            raise ValueError("resolution context digest does not match its inputs")
        return self


class ResolutionArtifact(FrozenSchema):
    filename: str
    kind: Literal["wheel", "sdist", "archive"]
    locator: str
    content_hash: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ResolutionArtifact":
        if not self.filename or not self.locator:
            raise ValueError("resolution artifact requires filename and locator")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash) is None:
            raise ValueError("resolution artifact requires a lowercase SHA-256")
        return self


class ResolutionPackage(FrozenSchema):
    name: str
    version: str | None
    source: SourceIdentity
    dependencies: tuple[str, ...] = ()
    marker: str | None = None
    available_artifacts: tuple[ResolutionArtifact, ...] = ()
    selected_artifact: ResolutionArtifact | None = None

    @model_validator(mode="after")
    def validate_package(self) -> "ResolutionPackage":
        if canonicalize_name(self.name) != self.name:
            raise ValueError("resolution package name must be canonical")
        if self.version is not None:
            try:
                normalized = str(Version(self.version))
            except InvalidVersion as error:
                raise ValueError("resolution package version must be valid") from error
            if normalized != self.version:
                raise ValueError("resolution package version must be normalized")
        elif self.source.kind not in {"path", "workspace", "git"}:
            raise ValueError("versionless package requires a source tree")
        if self.dependencies != tuple(sorted(set(self.dependencies))):
            raise ValueError("resolution dependencies must be sorted and unique")
        artifacts = tuple(
            (item.kind, item.filename, item.locator, item.content_hash)
            for item in self.available_artifacts
        )
        if artifacts != tuple(sorted(set(artifacts))):
            raise ValueError("resolution artifacts must be sorted and unique")
        if (
            self.selected_artifact is not None
            and self.selected_artifact not in self.available_artifacts
        ):
            raise ValueError("selected artifact must belong to native alternatives")
        return self


class NativeResolutionPlan(FrozenSchema):
    format: Literal["pylock.toml"] = "pylock.toml"
    content: str = Field(exclude=True, repr=False)
    digest: str

    @classmethod
    def from_content(cls, content: str) -> "NativeResolutionPlan":
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
    direct_harness: tuple[HarnessSelection, ...],
    native_digest: str,
) -> str:
    return _digest(
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


def resolution_semantic_digest(
    *,
    kind: str,
    request_digest: str,
    context: ResolutionContext,
    packages: tuple[ResolutionPackage, ...],
    direct_harness: tuple[HarnessSelection, ...],
) -> str:
    return _digest(
        b"pf:resolution-semantic:v1\0",
        {
            "kind": kind,
            "request_digest": request_digest,
            "context": context.model_dump(mode="json"),
            "packages": [
                {
                    "name": item.name,
                    "version": item.version,
                    "source": item.source.model_dump(mode="json"),
                    "dependencies": item.dependencies,
                    "selected_artifact": (
                        item.selected_artifact.model_dump(mode="json")
                        if item.selected_artifact is not None
                        else None
                    ),
                }
                for item in packages
            ],
            "direct_harness": [
                item.model_dump(mode="json") for item in direct_harness
            ],
        },
    )


class ResolutionPlan(FrozenSchema):
    status: Literal["PLAN"] = "PLAN"
    kind: Literal["project", "environment"]
    request_digest: str
    context: ResolutionContext
    packages: tuple[ResolutionPackage, ...]
    direct_harness: tuple[HarnessSelection, ...] = ()
    native: NativeResolutionPlan
    process: ProcessResult = Field(exclude=True, repr=False)
    semantic_digest: str
    digest: str

    @classmethod
    def from_evidence(
        cls,
        *,
        kind: Literal["project", "environment"],
        request_digest: str,
        context: ResolutionContext,
        packages: tuple[ResolutionPackage, ...],
        direct_harness: tuple[HarnessSelection, ...],
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
        if not self.request_digest:
            raise ValueError("resolution request digest cannot be empty")
        names = tuple(item.name for item in self.packages)
        if names != tuple(sorted(set(names))):
            raise ValueError("single-cell resolution packages must be sorted and unique")
        harness_names = tuple(item.name for item in self.direct_harness)
        if harness_names != tuple(sorted(set(harness_names))):
            raise ValueError("direct harness selections must be sorted and unique")
        if self.kind == "project" and self.direct_harness:
            raise ValueError("project plan cannot contain direct harness selections")
        packages = {item.name: item for item in self.packages}
        for selection in self.direct_harness:
            package = packages.get(selection.name)
            if (
                package is None
                or package.version != selection.version
                or package.source != selection.source
            ):
                raise ValueError(
                    "direct harness selection must belong to the environment graph"
                )
            selected = package.selected_artifact
            if selection.selected_artifact is None:
                if selected is not None:
                    raise ValueError(
                        "direct harness selection omitted reliable artifact evidence"
                    )
            elif selected is None or (
                selection.selected_artifact.filename != selected.filename
                or selection.selected_artifact.kind != selected.kind
                or selection.selected_artifact.locator != selected.locator
                or selection.selected_artifact.content_hash
                != selected.content_hash
            ):
                raise ValueError(
                    "direct harness artifact must match the environment graph"
                )
        expected_semantic = resolution_semantic_digest(
            kind=self.kind,
            request_digest=self.request_digest,
            context=self.context,
            packages=self.packages,
            direct_harness=self.direct_harness,
        )
        if self.semantic_digest != expected_semantic:
            raise ValueError("resolution semantic digest does not match its evidence")
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


def environment_identity_digest(
    *,
    project_plan_digest: str,
    environment_plan_digest: str,
    graph: tuple[ResolvedNode, ...],
) -> str:
    return _digest(
        b"pf:environment:v1\0",
        {
            "project_plan_digest": project_plan_digest,
            "environment_plan_digest": environment_plan_digest,
            "graph": [item.model_dump(mode="json") for item in graph],
        },
    )


def resolution_graph_id(graph: tuple[ResolvedNode, ...]) -> str:
    """Return the Schema 2 identity for one canonical resolved graph."""
    names = tuple(node.name for node in graph)
    if names != tuple(sorted(set(names))):
        raise ValueError("resolution graph nodes must be sorted and unique")
    for node in graph:
        if canonicalize_name(node.name) != node.name:
            raise ValueError("resolution graph package names must be canonical")
        if node.dependencies != tuple(sorted(set(node.dependencies))):
            raise ValueError(
                "resolution graph dependencies must be sorted and unique"
            )
        if any(canonicalize_name(item) != item for item in node.dependencies):
            raise ValueError("resolution graph dependencies must be canonical")
    payload = [node.model_dump(mode="json") for node in graph]
    return "resolution-" + hashlib.sha256(
        b"pf:resolution-graph:v1\0" + canonical_identity_json(payload)
    ).hexdigest()


class EnvironmentIdentity(FrozenSchema):
    project_plan_digest: str
    environment_plan_digest: str
    graph: tuple[ResolvedNode, ...]
    digest: str

    @classmethod
    def from_plans(
        cls,
        *,
        project_plan: ResolutionPlan,
        environment_plan: ResolutionPlan,
        graph: tuple[ResolvedNode, ...],
    ) -> "EnvironmentIdentity":
        return cls(
            project_plan_digest=project_plan.semantic_digest,
            environment_plan_digest=environment_plan.semantic_digest,
            graph=graph,
            digest=environment_identity_digest(
                project_plan_digest=project_plan.semantic_digest,
                environment_plan_digest=environment_plan.semantic_digest,
                graph=graph,
            ),
        )

    @model_validator(mode="after")
    def validate_environment_identity(self) -> "EnvironmentIdentity":
        resolution_graph_id(self.graph)
        expected = environment_identity_digest(
            project_plan_digest=self.project_plan_digest,
            environment_plan_digest=self.environment_plan_digest,
            graph=self.graph,
        )
        if self.digest != expected:
            raise ValueError("environment identity digest does not match its evidence")
        return self


class ResolutionUnsat(FrozenSchema):
    status: Literal["UNSAT"] = "UNSAT"
    stage: Literal["resolve-project", "resolve-environment"]
    request_digest: str
    context: ResolutionContext
    proof_code: Literal[
        "direct-version-contradiction",
        "transitive-version-contradiction",
    ]
    diagnostic_digest: str
    process: ProcessResult

    @model_validator(mode="after")
    def validate_certified_unsat(self) -> "ResolutionUnsat":
        if not self.request_digest or not self.diagnostic_digest:
            raise ValueError("certified resolution conflict requires complete identity")
        if (
            self.process.exit_code != 1
            or self.process.signal is not None
            or self.process.start_error is not None
            or self.process.timed_out
            or not self.process.stdout_complete
            or not self.process.stderr_complete
        ):
            raise ValueError("certified resolution conflict requires a complete exit")
        return self


class ResolutionIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    stage: Literal["resolve-project", "resolve-environment"]
    request_digest: str
    context: ResolutionContext
    cause: FailureCause
    summary_code: str
    process: ProcessResult

    @model_validator(mode="after")
    def validate_indeterminate(self) -> "ResolutionIndeterminate":
        if not self.request_digest or not self.summary_code:
            raise ValueError("indeterminate resolution requires request evidence")
        return self


ResolutionOutcome = Annotated[
    Union[ResolutionPlan, ResolutionUnsat, ResolutionIndeterminate],
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
        if self.process.exit_code != 0:
            raise ValueError("installed resolution requires a successful process")
        return self


class InstallFailure(FrozenSchema):
    status: Literal["INSTALL_FAILURE"] = "INSTALL_FAILURE"
    plan_digest: str
    cause: FailureCause
    stage: Literal["install-environment"] = "install-environment"
    process: ProcessResult
    summary_code: str | None = None

    @model_validator(mode="after")
    def validate_install_failure(self) -> "InstallFailure":
        if not self.plan_digest:
            raise ValueError("install failure requires its plan identity")
        if self.cause in {"RESOLUTION_CONFLICT", "HARNESS_CONFLICT"}:
            raise ValueError("installation cannot prove a resolution conflict")
        return self


InstallOutcome = Annotated[
    Union[InstalledResolution, InstallFailure],
    Field(discriminator="status"),
]
