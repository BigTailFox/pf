from __future__ import annotations

import hashlib
from importlib.metadata import version as distribution_version
import json

from pf.adapters.test_command import selected_test_outcome_policy_identity
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
    "boundary_rule": "runtime-evidence-only",
    "final_verification": "direct-test-command-pass",
}


def evaluation_policy_identity(config: EffectiveConfig) -> str:
    """Return the identity of every setting that changes evaluation evidence."""
    document = {
        "config": config.model_dump(mode="json", exclude={"jobs"}),
        "tool_versions": {"ty": distribution_version("ty")},
        "test_outcome_policy": selected_test_outcome_policy_identity(
            config.test_command,
            config.test_failure_exit_codes,
        ),
        "ty_diagnostic_policy": TY_DIAGNOSTIC_POLICY,
        "failure_policy": FailurePolicy.identity,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"pf:policy:v1\0{canonical}".encode()).hexdigest()
