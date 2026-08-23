from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal

from pf.resolution import UV_DIAGNOSTIC_PROFILES
from pf.schemas.evaluation import FailureCause, ProcessResult


UV_DIAGNOSTIC_SHAPE_SET = "uv-resolution-stderr-shapes-v1"


@dataclass(frozen=True)
class UvResolutionClassification:
    kind: Literal["unsat", "indeterminate"]
    signature: str
    proof_code: Literal[
        "direct-version-contradiction",
        "transitive-version-contradiction",
    ] | None = None
    cause: FailureCause | None = None
    summary_code: str | None = None


_UNSAT_HEADER = "× No solution found when resolving dependencies:"
_UNSAT_CONCLUSION = "we can conclude that your requirements are unsatisfiable."
_AMBIGUOUS_AVAILABILITY = (
    " was not found ",
    " no versions of ",
    " no version of ",
    " only the following versions of ",
    " has no wheels ",
    " no matching distribution ",
    " requires python ",
    " is not available ",
    " package locations",
    " index lookups were disabled",
    " offline mode",
    " cache miss",
)
_SOURCE_FAILURE = (
    "failed to fetch",
    "failed to download",
    "request failed",
    "dns error",
    "name or service not known",
    "temporary failure in name resolution",
    "connection refused",
    "connection timed out",
    "gateway timeout",
    "401 unauthorized",
    "403 forbidden",
    "invalid metadata",
    "invalid package format",
    "metadata is invalid",
    "failed to read metadata",
    "hash mismatch",
    "does not match the expected hash",
    "archive hash",
)
_BUILD_FAILURE = (
    "failed to build",
    "build backend",
    "failed to build wheel",
    "failed to prepare distributions",
)


def _normalized(text: str) -> str:
    without_ansi = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return " ".join(without_ansi.split()).lower()


def diagnostic_digest(stdout: str, stderr: str) -> str:
    return hashlib.sha256(
        b"pf:uv-diagnostic:v1\0" + stdout.encode() + b"\0" + stderr.encode()
    ).hexdigest()


def classify_resolution_diagnostic(
    *,
    uv_version: str,
    process: ProcessResult,
    stdout: str,
    stderr: str,
) -> UvResolutionClassification:
    if uv_version not in UV_DIAGNOSTIC_PROFILES:
        return UvResolutionClassification(
            kind="indeterminate",
            signature="unsupported-uv-version",
            cause="TOOL_FAILURE",
            summary_code="unsupported-uv-version",
        )
    if process.timed_out:
        return UvResolutionClassification(
            kind="indeterminate",
            signature="process-timeout",
            cause="TIMEOUT",
            summary_code="resolution-timeout",
        )
    if process.signal is not None or process.start_error is not None:
        return UvResolutionClassification(
            kind="indeterminate",
            signature="abnormal-process-exit",
            cause="TOOL_FAILURE",
            summary_code="resolution-process-failed",
        )
    if not process.stdout_complete or not process.stderr_complete:
        return UvResolutionClassification(
            kind="indeterminate",
            signature="incomplete-diagnostic",
            cause="TOOL_FAILURE",
            summary_code="resolution-output-incomplete",
        )
    combined = _normalized(f"{stdout}\n{stderr}")
    if any(phrase in combined for phrase in _SOURCE_FAILURE):
        return UvResolutionClassification(
            kind="indeterminate",
            signature="source-failure",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        )
    if any(phrase in combined for phrase in _BUILD_FAILURE):
        return UvResolutionClassification(
            kind="indeterminate",
            signature="build-failure",
            cause="BUILD_FAILURE",
            summary_code="resolution-build-failure",
        )
    complete_unsat = (
        _UNSAT_HEADER.lower() in combined
        and _UNSAT_CONCLUSION in combined
        and process.exit_code == 1
    )
    if complete_unsat and any(
        phrase in combined for phrase in _AMBIGUOUS_AVAILABILITY
    ):
        return UvResolutionClassification(
            kind="indeterminate",
            signature="candidate-availability",
            cause="TOOL_FAILURE",
            summary_code="resolution-candidate-unavailable",
        )
    if complete_unsat and (
        " depends on " in combined or " depend on " in combined
    ):
        return UvResolutionClassification(
            kind="unsat",
            signature="complete-transitive-contradiction",
            proof_code="transitive-version-contradiction",
        )
    direct = complete_unsat and re.search(
        r"because you require .+ and .+, we can conclude that your requirements are unsatisfiable\.",
        combined,
    )
    if direct:
        return UvResolutionClassification(
            kind="unsat",
            signature="complete-direct-contradiction",
            proof_code="direct-version-contradiction",
        )
    return UvResolutionClassification(
        kind="indeterminate",
        signature="unknown-diagnostic",
        cause="TOOL_FAILURE",
        summary_code="resolution-diagnostic-unknown",
    )
