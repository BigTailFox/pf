from __future__ import annotations

import hashlib
from importlib.metadata import version as distribution_version
import json

from pf.failure import FailurePolicy
from pf.schemas.config import EffectiveConfig
from pf.static_transition import STATIC_POLICY_VERSION


TY_DIAGNOSTIC_POLICY = {
    "comparison": "multiset-subtraction",
    "fingerprint": "ordered-incremental-identity-multiset",
    "identity_rule": ("snapshot-path-line-column-code+external-namespace-path-code"),
    "output_format": "gitlab",
    "policy": STATIC_POLICY_VERSION,
    "region_scope": "fixed-slice-contiguous",
    "strong_classifier": "strong-classifier-v1",
    "witness_planner": "witness-planner-v1",
    "witness_harness": "witness-harness-v1",
    "witness_stderr": "diagnostic-only",
    "project_terminal": "adapter-cli-overrides",
    "boundary_rule": "runtime-evidence-only",
    "final_verification": "direct-test-command-pass",
}

CONFIGURED_VERIFIER_OUTCOME_POLICY = "configured-verifier-terminal-v1"

VALIDATION_CONTRACT_POLICY = {
    "resolution_projection": "actual-interpreter-target-active-pylock",
    "self_reference": "required-effective-cell-surface",
    "extra_exploration": "nonempty-declared-groups-only",
    "baseline_harness": "original-external-declarations",
    "probe_harness": "remove-eligible-direct-lower-bounds",
    "project_overlap": "exact-project-node-without-harness-ceiling",
    "external_ceiling": "baseline-observed-version-for-current-harness-only-node",
}


def evaluation_policy_identity(config: EffectiveConfig) -> str:
    """Return the identity of every setting that changes evaluation evidence."""
    document = {
        "config": {
            "resolution": config.resolution.model_dump(mode="json"),
            "ty": config.ty.model_dump(mode="json"),
            "test": {
                "command": config.test.command,
                "cwd": config.test.cwd,
                "timeout_seconds": config.test.timeout_seconds,
            },
        },
        "tool_versions": {"ty": distribution_version("ty")},
        "verifier_outcome_policy": CONFIGURED_VERIFIER_OUTCOME_POLICY,
        "ty_diagnostic_policy": TY_DIAGNOSTIC_POLICY,
        "failure_policy": FailurePolicy.identity,
        "validation_contract_policy": VALIDATION_CONTRACT_POLICY,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"pf:policy:v1\0{canonical}".encode()).hexdigest()
