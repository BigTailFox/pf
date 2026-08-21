from __future__ import annotations

import hashlib
from importlib.metadata import version as distribution_version
import json

from pf.failure import FailurePolicy
from pf.schemas.config import EffectiveConfig


TY_DIAGNOSTIC_POLICY = {
    "comparison": "multiset-subtraction",
    "identity_rule": ("snapshot-path-line-column-code+external-namespace-path-code"),
    "output_format": "gitlab",
    "policy": "increment-v2",
}


def evaluation_policy_identity(config: EffectiveConfig) -> str:
    """Return the identity of every setting that changes evaluation evidence."""
    document = {
        "config": config.model_dump(mode="json", exclude={"jobs"}),
        "tool_versions": {"ty": distribution_version("ty")},
        "ty_diagnostic_policy": TY_DIAGNOSTIC_POLICY,
        "failure_policy": FailurePolicy.identity,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"pf:policy:v1\0{canonical}".encode()).hexdigest()
