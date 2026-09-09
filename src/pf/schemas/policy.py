"""Rule identities, kept separate from execution objects and observations."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Union

from packaging.version import Version
from pydantic import Field, field_validator, model_validator, model_serializer

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.config import ResolutionConfig, SearchConfig


def _identity(domain: bytes, policy: FrozenSchema) -> str:
    return hashlib.sha256(domain + canonical_identity_json(policy.model_dump(mode="json"))).hexdigest()


class ExecutionPolicy(FrozenSchema):
    rules: Literal["configured-execution-only-v1"] = "configured-execution-only-v1"
    failure: Literal["failure-execution-v4"] = "failure-execution-v4"
    verifier_outcome: Literal["configured-verifier-terminal-v1"] = "configured-verifier-terminal-v1"
    pass_authority: Literal["direct-test-command-pass"] = "direct-test-command-pass"
    failed_case_authority: Literal["qualified-rejection-only-original-command-for-pass"] = "qualified-rejection-only-original-command-for-pass"
    resolution: ResolutionConfig
    verifier_command: tuple[str, ...] = Field(min_length=1)
    verifier_cwd: Literal["package", "root"]
    verifier_timeout_seconds: int | None = Field(gt=0, strict=True, json_schema_extra={"x-pf-preserve-null": True})
    execution_outcome: Literal["execution-outcome-v1"] = "execution-outcome-v1"
    structured_facts: Literal["operation-structured-facts-v1"] = "operation-structured-facts-v1"
    uv_protocol: Literal["uv-pip-compile-pylock-v1"] = "uv-pip-compile-pylock-v1"
    uv_diagnostic_profile: Literal["uv-diagnostics-0.12.5-v1"] = "uv-diagnostics-0.12.5-v1"
    test_group_selection: Literal["explicit-or-dev-then-test-else-empty-v1"] = "explicit-or-dev-then-test-else-empty-v1"
    empty_harness_prepare: Literal["install-project-plan-without-environment-resolution-v1"] = "install-project-plan-without-environment-resolution-v1"
    project_marker_projection: Literal["portable-cell-platform-v1"] = "portable-cell-platform-v1"
    resolution_projection: Literal["actual-interpreter-target-active-pylock"] = "actual-interpreter-target-active-pylock"
    inspection: Literal["no-bytecode-write-v1"] = "no-bytecode-write-v1"
    self_reference: Literal["required-effective-cell-surface"] = "required-effective-cell-surface"
    extra_exploration: Literal["nonempty-declared-groups-only"] = "nonempty-declared-groups-only"
    baseline_harness: Literal["original-external-declarations"] = "original-external-declarations"
    probe_harness: Literal["remove-eligible-direct-lower-bounds"] = "remove-eligible-direct-lower-bounds"
    project_overlap: Literal["exact-project-node-without-harness-ceiling"] = "exact-project-node-without-harness-ceiling"
    external_ceiling: Literal["baseline-observed-version-for-current-harness-only-node"] = "baseline-observed-version-for-current-harness-only-node"

    @model_validator(mode="after")
    def validate_command(self) -> ExecutionPolicy:
        if not all(self.verifier_command) or self.verifier_command[:2] == ("uv", "run"):
            raise ValueError("execution policy requires a valid configured verifier command")
        return self

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        if self.verifier_timeout_seconds is None:
            result["verifier_timeout_seconds"] = None
        if self.resolution.timeout_seconds is None:
            result["resolution"]["timeout_seconds"] = None
        return result

    @property
    def identity(self) -> str:
        return _identity(b"pf:execution-policy:v1\0", self)


class TyToolVersionDistribution(FrozenSchema):
    kind: Literal["distribution"] = "distribution"
    name: Literal["ty"] = "ty"
    version: str = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def normalized_version(cls, value: str) -> str:
        if str(Version(value)) != value:
            raise ValueError("ty tool version must be a normalized PEP 440 version")
        return value


class TyToolVersionUnavailable(FrozenSchema):
    kind: Literal["unavailable"] = "unavailable"


TyToolVersion = Annotated[
    Union[TyToolVersionDistribution, TyToolVersionUnavailable],
    Field(discriminator="kind"),
]


class SnapshotTyConfigMaterialized(FrozenSchema):
    kind: Literal["materialized"] = "materialized"
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotTyConfigUnavailable(FrozenSchema):
    kind: Literal["unavailable"] = "unavailable"
    reason: Literal[
        "configuration-context-unavailable",
        "configuration-unreadable",
        "undeclared-analysis-root",
    ]


SnapshotTyConfig = Annotated[
    Union[SnapshotTyConfigMaterialized, SnapshotTyConfigUnavailable],
    Field(discriminator="kind"),
]


class TyObservationPolicy(FrozenSchema):
    rules: Literal["ty-observation-v2"] = "ty-observation-v2"
    tool_version: TyToolVersion
    args: tuple[str, ...] = ()
    timeout_seconds: int | None = Field(default=600, json_schema_extra={"x-pf-preserve-null": True})
    owned_options: Literal["adapter-cli-overrides"] = "adapter-cli-overrides"
    output_format: Literal["gitlab"] = "gitlab"
    diagnostic_identity: Literal["snapshot-path-line-column-code+external-namespace-path-code"] = "snapshot-path-line-column-code+external-namespace-path-code"
    analysis_scope: Literal["explicit-static-subject-v2"] = "explicit-static-subject-v2"
    process_environment: Literal["explicit-complete-v1"] = "explicit-complete-v1"
    unavailable: Literal["typed-ty-process-and-protocol-v1"] = "typed-ty-process-and-protocol-v1"
    observation: Literal["run-first-observation-v1"] = "run-first-observation-v1"
    snapshot_ty_config: SnapshotTyConfig
    host_config: Literal["reject-undeclared-v1"] = "reject-undeclared-v1"

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        if self.timeout_seconds is None:
            result["timeout_seconds"] = None
        return result

    @model_validator(mode="after")
    def validate_observation(self) -> TyObservationPolicy:
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0
        ):
            raise ValueError("ty timeout must be positive or None")
        return self

    def cache_preimage(self) -> dict:
        payload = self.model_dump(mode="json")
        payload.pop("timeout_seconds", None)
        return payload

    @property
    def identity(self) -> str:
        return _identity(b"pf:ty-observation-policy:v2\0", self)

    @property
    def cache_identity(self) -> str:
        return hashlib.sha256(
            b"pf:ty-observation-cache:v2\0" + canonical_identity_json(self.cache_preimage())
        ).hexdigest()


class GuidancePolicy(FrozenSchema):
    rules: Literal["static-guidance-v1"] = "static-guidance-v1"
    observation: TyObservationPolicy
    observation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison: Literal["multiset-subtraction"] = "multiset-subtraction"
    fingerprint: Literal["scoped-comparison-identity-multiset-v1"] = "scoped-comparison-identity-multiset-v1"
    anchors: Literal["global-diagnostic-and-slice-anchor-v1"] = "global-diagnostic-and-slice-anchor-v1"
    bisection: Literal["slice-local-static-bisection-v1"] = "slice-local-static-bisection-v1"
    authority: Literal["advisory-v1"] = "advisory-v1"
    unavailable: Literal["no-hint-fallback-v1"] = "no-hint-fallback-v1"

    @model_validator(mode="after")
    def validate_observation_identity(self) -> GuidancePolicy:
        if self.observation_identity != self.observation.identity:
            raise ValueError("ty observation policy identity does not match its rules")
        return self

    @property
    def identity(self) -> str:
        return _identity(b"pf:guidance-policy:v1\0", self)


class SearchDerivationPolicy(FrozenSchema):
    rules: Literal["direct-first-coordinate-guidance-v1"] = "direct-first-coordinate-guidance-v1"
    candidates: SearchConfig
    guidance_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    hint_consumption: Literal["suspect-then-clean-neighbor-v1"] = "suspect-then-clean-neighbor-v1"
    coordinate_order: Literal["canonical-dependency-order"] = "canonical-dependency-order"
    monotonicity: Literal["rejected-prefix-pass-suffix"] = "rejected-prefix-pass-suffix"
    predecessor: Literal["current-slice-direct-revalidation"] = "current-slice-direct-revalidation"
    mechanical: Literal["lowest-then-ascending-small-else-midpoint"] = "lowest-then-ascending-small-else-midpoint"
    small_threshold: int = Field(default=8, gt=0, strict=True)

    @property
    def identity(self) -> str:
        return _identity(b"pf:search-policy:v1\0", self)
