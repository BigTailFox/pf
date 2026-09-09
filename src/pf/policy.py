from __future__ import annotations

import hashlib
import json

from pf.failure import FailurePolicy
from pf.schemas.config import EffectiveConfig
from pf.schemas.policy import (
    ExecutionPolicy, GuidancePolicy, SearchDerivationPolicy, TyObservationPolicy,
    SnapshotTyConfig, SnapshotTyConfigUnavailable, TyToolVersion,
)
from pf.ty_options import validate_ty_args
from pf.ty_version import read_ty_tool_version


TY_DIAGNOSTIC_POLICY = {
    "comparison": "multiset-subtraction",
    "fingerprint": "scoped-comparison-identity-multiset-v1",
    "identity_rule": ("snapshot-path-line-column-code+external-namespace-path-code"),
    "output_format": "gitlab",
    "policy": "static-guidance-v1",
    "project_terminal": "adapter-cli-overrides",
    "boundary_rule": "runtime-evidence-only",
    "final_verification": "direct-test-command-pass",
}

CONFIGURED_VERIFIER_OUTCOME_POLICY = "configured-verifier-terminal-v1"

EXECUTION_OUTCOME_POLICY = {
    "rules": "execution-outcome-v1",
    "structured_facts": "operation-structured-facts-v1",
    "attribution_profiles": [{
        "tool": "uv", "tool_version": "0.12.5",
        "protocol": "uv-pip-compile-pylock-v1",
        "profile": "uv-diagnostics-0.12.5-v1",
        "codes": ["direct-version-contradiction", "transitive-version-contradiction"],
    }],
}

VALIDATION_CONTRACT_POLICY = {
    "test_group_selection": "explicit-or-dev-then-test-else-empty-v1",
    "empty_harness_prepare": "install-project-plan-without-environment-resolution-v1",
    "project_marker_projection": "portable-cell-platform-v1",
    "resolution_projection": "actual-interpreter-target-active-pylock",
    "self_reference": "required-effective-cell-surface",
    "extra_exploration": "nonempty-declared-groups-only",
    "baseline_harness": "original-external-declarations",
    "probe_harness": "remove-eligible-direct-lower-bounds",
    "project_overlap": "exact-project-node-without-harness-ceiling",
    "external_ceiling": "baseline-observed-version-for-current-harness-only-node",
}


def execution_policy(config: EffectiveConfig) -> ExecutionPolicy:
    """Project only the settings that govern configured execution authority."""
    return ExecutionPolicy(
        resolution=config.resolution,
        verifier_command=config.test.command,
        verifier_cwd=config.test.cwd,
        verifier_timeout_seconds=config.test.timeout_seconds,
    )


def guidance_policy(
    config: EffectiveConfig,
    *,
    tool_version: TyToolVersion,
    snapshot_ty_config: SnapshotTyConfig,
) -> GuidancePolicy:
    """Bind the complete generation observation without a verifier dependency."""
    validate_ty_args(config.ty.args)
    observation = TyObservationPolicy(
        tool_version=tool_version,
        args=config.ty.args,
        timeout_seconds=config.ty.timeout_seconds,
        snapshot_ty_config=snapshot_ty_config,
    )
    return GuidancePolicy(observation=observation, observation_identity=observation.identity)


def search_derivation_policy(
    config: EffectiveConfig, *, guidance: GuidancePolicy, small_threshold: int = 8,
) -> SearchDerivationPolicy:
    return SearchDerivationPolicy(
        candidates=config.search, guidance_identity=guidance.identity,
        small_threshold=small_threshold,
    )


def execution_policy_identity(config: EffectiveConfig) -> str:
    """Identify dynamic execution independently of guidance and search."""
    return execution_policy(config).identity


def _digest(domain: str, document: object) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{domain}\0{canonical}".encode()).hexdigest()


def guidance_policy_identity(
    config: EffectiveConfig,
    *,
    tool_version: TyToolVersion | None = None,
    snapshot_ty_config: SnapshotTyConfig | None = None,
) -> str:
    """Identify guidance as GuidancePolicy.identity."""
    return guidance_policy(
        config,
        tool_version=tool_version if tool_version is not None else read_ty_tool_version(),
        snapshot_ty_config=(
            snapshot_ty_config if snapshot_ty_config is not None
            else SnapshotTyConfigUnavailable(reason="configuration-context-unavailable")
        ),
    ).identity


def search_derivation_identity(config: EffectiveConfig, *, small_threshold: int = 8) -> str:
    """Identify search derivation rules independently of a specific run snapshot."""
    return _digest("pf:search-derivation-identity:v1", {
        "candidates": config.search.model_dump(mode="json"),
        "guidance_identity": guidance_policy_identity(config),
        "rules": "direct-first-coordinate-guidance-v1",
        "hint_consumption": "suspect-then-clean-neighbor-v1",
        "coordinate_order": "canonical-dependency-order",
        "monotonicity": "rejected-prefix-pass-suffix",
        "predecessor": "current-slice-direct-revalidation",
        "mechanical": "lowest-then-ascending-small-else-midpoint",
        "small_threshold": small_threshold,
    })


def report_provenance_identity(config: EffectiveConfig) -> str:
    """Identify search/guidance provenance independently of ExecutionPolicy.

    Apply and merge compare this digest separately from execution_policy.identity
    so a ty/heuristic change is search-provenance-mismatch, not execution failure.
    """
    return _digest("pf:policy:v1", {
        "guidance_policy_identity": guidance_policy_identity(config),
        "search_derivation_identity": search_derivation_identity(config),
        "config": {
            "resolution": config.resolution.model_dump(mode="json"),
            "test": {
                "command": config.test.command,
                "cwd": config.test.cwd,
                "timeout_seconds": config.test.timeout_seconds,
            },
        },
        "verifier_outcome_policy": CONFIGURED_VERIFIER_OUTCOME_POLICY,
        "failure_policy": FailurePolicy.identity,
        "execution_outcome_policy": EXECUTION_OUTCOME_POLICY,
        "validation_contract_policy": VALIDATION_CONTRACT_POLICY,
    })
